"""Assemble the development context for one task.

Selection is deliberately explainable: SQL recall narrows the corpus, BM25
orders it, and spec links (``applies_to`` / ``verified_by`` / ``supersedes``)
pull in the exact code symbols and tests the task has to touch.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sdd_context.config import Settings
from sdd_context.db import Database, QueryLog
from sdd_context.models import (
    CodeSymbol,
    ContextPack,
    SpecItem,
    TaskState,
    TestCase,
)
from sdd_context.powercontext import PowerContextClient, PowerContextError
from sdd_context.rank import rank, sql_terms
from sdd_context.state import load_state


@dataclass
class PrepareOptions:
    top_specs: int = 5
    top_symbols: int = 6
    top_tests: int = 8
    allow_empty_state: bool = False
    repo_root: Path | None = None


def load_task(path: Path) -> dict[str, Any]:
    task = json.loads(path.read_text(encoding="utf-8"))
    for key in ("task_id", "title", "description"):
        if not task.get(key):
            raise ValueError(f"task file {path} is missing '{key}'")
    return task


def build_pack(
    settings: Settings,
    task: dict[str, Any],
    options: PrepareOptions | None = None,
) -> ContextPack:
    options = options or PrepareOptions()
    repo_root = (options.repo_root or Path.cwd()).resolve()
    query = _query_text(task)
    terms = sql_terms(query, 14)

    db = Database(settings.database, QueryLog(settings.query_log))
    try:
        specs = db.search_specs(terms, ("active", "superseded"))
        if len(specs) < 4:
            specs = db.fetch_specs(("active", "superseded"))
        selected = _select_specs(query, specs, options.top_specs)
        code: list[CodeSymbol] = []
        tests: list[TestCase] = []
        # Tests carry their own spec coverage; pull in any spec they cite even
        # when BM25 ranked it below the cut-off, then re-resolve code and tests.
        for _ in range(2):
            code = _code_for(db, selected, query, terms, options)
            tests = _tests_for(db, selected, query, terms, options)
            cover_ids = {cited for test in tests for cited in test.covers}
            extra = [
                item
                for item in specs
                if item.status == "active" and item.item_id in cover_ids and item not in selected
            ]
            if not extra:
                break
            selected = [*selected, *extra]

        decisions = [item for item in specs if item.kind == "decision"]
        selected_ids = {item.item_id for item in selected}
        active_decisions = [
            item
            for item in decisions
            if item.status == "active" and set(item.affects) & selected_ids
        ]
        invalidated = _invalidated(selected, active_decisions, specs)

        target_tests = [test for test in tests if test.kind == "target"]
        regression_tests = [test for test in tests if test.kind == "regression"]

        state, state_warnings = _load_state(settings, task, options)
        warnings = state_warnings + _coverage_warnings(
            selected, code, target_tests, regression_tests
        )
        warnings += _citation_warnings(repo_root, selected, code, target_tests, regression_tests)

        return ContextPack(
            task=task,
            specs=selected,
            active_decisions=active_decisions,
            invalidated=invalidated,
            code=code,
            target_tests=target_tests,
            regression_tests=regression_tests,
            state=state,
            database={
                "dsn": settings.database.dsn(),
                "counts": db.counts(),
                "queries": len(db.log.entries),
                "query_log": settings.query_log,
                "recall_terms": terms,
            },
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            warnings=warnings,
        )
    finally:
        db.close()


def _query_text(task: dict[str, Any]) -> str:
    modules = " ".join(task.get("modules") or [])
    keywords = " ".join(task.get("keywords") or [])
    return " ".join([task.get("title", ""), task.get("description", ""), modules, keywords])


def _spec_text(item: SpecItem) -> str:
    return " ".join([item.item_id, item.title, item.module, " ".join(item.tags), item.body])


def _symbol_text(symbol: CodeSymbol) -> str:
    return " ".join([symbol.qualname, symbol.signature, symbol.docstring, symbol.path])


def _code_for(
    db: Database,
    selected: list[SpecItem],
    query: str,
    terms: list[str],
    options: PrepareOptions,
) -> list[CodeSymbol]:
    linked_names = [
        target
        for item in selected
        for target in item.applies_to
        if "." in target and not target.endswith(".py")
    ]
    linked = db.symbols_for(linked_names)
    extra = [
        symbol
        for symbol, score in rank(
            query, db.search_symbols(terms), _symbol_text, limit=options.top_symbols
        )
        if score > 0
    ]
    return _dedupe_symbols(linked + extra)


def _tests_for(
    db: Database,
    selected: list[SpecItem],
    query: str,
    terms: list[str],
    options: PrepareOptions,
) -> list[TestCase]:
    paths = [path for item in selected for path in item.verified_by if path.endswith(".py")]
    linked = db.fetch_tests(paths)
    extra = [
        test
        for test, score in rank(
            query, db.search_tests(terms), _test_text, limit=options.top_tests
        )
        if score > 0
    ]
    return _dedupe_tests(linked + extra)


def _test_text(test: TestCase) -> str:
    return " ".join([test.name, " ".join(test.covers), test.docstring, test.path])


def _select_specs(query: str, specs: list[SpecItem], limit: int) -> list[SpecItem]:
    ranked = rank(query, specs, _spec_text)
    scored: list[tuple[SpecItem, float]] = []
    for item, score in ranked:
        # An id spelled out in the task text is an explicit pointer, not a guess.
        if item.item_id in query:
            score += 30.0
        scored.append((item, score))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    selected: list[SpecItem] = []
    for item, score in scored:
        if item.status != "active":
            continue
        if score <= 0 and selected:
            break
        selected.append(item)
        if len(selected) >= limit:
            break
    if selected:
        return selected
    return [item for item in specs if item.status == "active"][:limit]


def _invalidated(
    selected: list[SpecItem], active_decisions: list[SpecItem], specs: list[SpecItem]
) -> list[dict[str, Any]]:
    index = {item.item_id: item for item in specs}
    result: dict[str, dict[str, Any]] = {}
    for item in [*selected, *active_decisions]:
        for old_id in item.supersedes:
            old = index.get(old_id)
            if old is None:
                continue
            result[old_id] = {
                "id": old.item_id,
                "title": old.title,
                "kind": old.kind,
                "status": old.status,
                "superseded_by": item.item_id,
                "reason": f"{item.item_id} 取代了该条目，不得作为实现依据",
                "source": old.citation,
            }
    for item in selected:
        if item.status == "superseded" and item.item_id not in result:
            result[item.item_id] = {
                "id": item.item_id,
                "title": item.title,
                "kind": item.kind,
                "status": item.status,
                "superseded_by": item.superseded_by,
                "reason": "该条目已失效",
                "source": item.citation,
            }
    return sorted(result.values(), key=lambda entry: entry["id"])


def _dedupe_symbols(symbols: list[CodeSymbol]) -> list[CodeSymbol]:
    unique: dict[str, CodeSymbol] = {}
    for symbol in symbols:
        unique.setdefault(symbol.symbol_id, symbol)
    # Prefer the most specific symbol: a method wins over its enclosing class.
    ordered = sorted(
        unique.values(),
        key=lambda symbol: (symbol.path, symbol.end_lineno - symbol.lineno, symbol.lineno),
    )
    kept: list[CodeSymbol] = []
    for symbol in ordered:
        contains_kept = any(
            other.path == symbol.path
            and other.lineno >= symbol.lineno
            and other.end_lineno <= symbol.end_lineno
            for other in kept
        )
        if contains_kept:
            continue
        kept.append(symbol)
    return sorted(kept, key=lambda symbol: (symbol.path, symbol.lineno))


def _dedupe_tests(tests: list[TestCase]) -> list[TestCase]:
    seen: dict[str, TestCase] = {}
    for test in tests:
        seen.setdefault(test.test_id, test)
    return sorted(seen.values(), key=lambda test: (test.path, test.lineno))


def _load_state(
    settings: Settings, task: dict[str, Any], options: PrepareOptions
) -> tuple[TaskState, list[str]]:
    client = PowerContextClient(settings.powercontext)
    try:
        return load_state(client, task["task_id"]), []
    except PowerContextError as exc:
        if not options.allow_empty_state:
            raise PowerContextError(
                f"{exc}\nStart PowerContext first (see README) or pass --allow-empty-state."
            ) from exc
        return (
            TaskState(task_id=task["task_id"], source="unavailable"),
            [f"PowerContext 不可用，任务状态为空：{exc}"],
        )
    finally:
        client.close()


def _coverage_warnings(
    specs: list[SpecItem],
    code: list[CodeSymbol],
    target_tests: list[TestCase],
    regression_tests: list[TestCase],
) -> list[str]:
    warnings: list[str] = []
    if not target_tests:
        warnings.append("未检索到目标功能测试，验证样例不完整")
    if not regression_tests:
        warnings.append("未检索到回归测试，兼容性验证不完整")
    symbol_names = {symbol.qualname for symbol in code}
    for item in specs:
        if item.kind != "requirement":
            continue
        targets = [
            name for name in item.applies_to if "." in name and not name.endswith(".py")
        ]
        if targets and not set(targets) & symbol_names:
            warnings.append(f"{item.item_id} 声明的 applies_to 未定位到代码符号")
    return warnings


def _citation_warnings(
    repo_root: Path,
    specs: list[SpecItem],
    code: list[CodeSymbol],
    target_tests: list[TestCase],
    regression_tests: list[TestCase],
) -> list[str]:
    warnings: list[str] = []
    for item in specs:
        path = repo_root / item.source_path
        if not path.exists():
            warnings.append(f"规格来源不存在: {item.source_path}")
            continue
        total = len(path.read_text(encoding="utf-8").splitlines())
        if item.source_line > total:
            warnings.append(f"规格来源行号越界: {item.citation}")
    for symbol in code:
        path = repo_root / symbol.path
        if not path.exists():
            warnings.append(f"代码来源不存在: {symbol.path}")
            continue
        total = len(path.read_text(encoding="utf-8").splitlines())
        if symbol.end_lineno > total:
            warnings.append(f"代码来源行号越界: {symbol.citation}")
    for test in [*target_tests, *regression_tests]:
        path = repo_root / test.path
        if not path.exists():
            warnings.append(f"测试来源不存在: {test.path}")
            continue
        total = len(path.read_text(encoding="utf-8").splitlines())
        if test.lineno > total:
            warnings.append(f"测试来源行号越界: {test.citation}")
    return warnings
