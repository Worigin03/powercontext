"""Ingest specs, code symbols and tests into seekdb.

Re-running the ingest replaces rows by natural key and prunes records whose
source disappeared, so an edited spec is visible to the next ``prepare`` call
without any cache flush.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sdd_context.config import Settings
from sdd_context.db import Database, QueryLog, content_revision
from sdd_context.parser import index_code, index_tests, parse_specs

DEFAULT_MODULE_MAP = {"ledgerflow": "order-query", "tests": "order-query"}


def ingest(
    settings: Settings,
    repo_root: Path,
    *,
    specs_dir: Path | None = None,
    src_dir: Path | None = None,
    tests_dir: Path | None = None,
    note: str = "",
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    specs_dir = (specs_dir or repo_root / "specs").resolve()
    src_dir = (src_dir or repo_root / "src").resolve()
    tests_dir = (tests_dir or repo_root / "tests").resolve()
    for path in (specs_dir, src_dir, tests_dir):
        if not path.exists():
            raise FileNotFoundError(f"missing ingest input: {path}")

    specs = parse_specs(specs_dir, repo_root)
    if not specs:
        raise ValueError(f"no spec items found under {specs_dir}")
    symbols = index_code(src_dir, repo_root, DEFAULT_MODULE_MAP)
    tests = index_tests(tests_dir, repo_root, DEFAULT_MODULE_MAP)

    db = Database(settings.database, QueryLog(settings.query_log))
    try:
        db.init_schema()
        db.upsert_specs(specs)
        db.upsert_symbols(symbols, content_revision(*[s.revision for s in specs]))
        db.upsert_tests(tests)
        db.prune("spec_item", "item_id", [s.item_id for s in specs])
        db.prune("code_symbol", "symbol_id", [s.symbol_id for s in symbols])
        db.prune("test_case", "test_id", [t.test_id for t in tests])
        run_id = content_revision(
            str(repo_root), *[s.revision for s in specs], str(len(symbols)), str(len(tests))
        )[:24]
        db.record_run(
            run_id,
            repo_root.as_posix(),
            len(specs),
            len(symbols),
            len(tests),
            note,
        )
        db.commit()
        summary = {
            "run_id": run_id,
            "specs": len(specs),
            "active_specs": sum(1 for item in specs if item.status == "active"),
            "superseded_specs": sum(1 for item in specs if item.status == "superseded"),
            "symbols": len(symbols),
            "tests": len(tests),
            "target_tests": sum(1 for test in tests if test.kind == "target"),
            "regression_tests": sum(1 for test in tests if test.kind == "regression"),
            "database": settings.database.dsn(),
            "counts": db.counts(),
            "queries": db.log.entries,
        }
        return summary
    finally:
        db.close()
