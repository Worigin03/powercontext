"""Unit tests for the context module itself (no database required).

    cd minimax && python -m pytest tests -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sdd_context.models import CodeSymbol, ContextPack, SpecItem, TaskState, TestCase
from sdd_context.parser import index_code, index_tests, parse_specs
from sdd_context.rank import bm25, sql_terms, tokenize
from sdd_context.render import render_markdown

PROJECT = ROOT / "demo_project"


@pytest.fixture(scope="module")
def specs() -> list[SpecItem]:
    return parse_specs(PROJECT / "specs", PROJECT)


def test_spec_parser_reads_ids_status_and_links(specs: list[SpecItem]) -> None:
    ids = {item.item_id for item in specs}
    assert {"REQ-ORD-014", "REQ-ORD-009", "CTR-ORD-001", "ADR-003", "ADR-007"} <= ids

    requirement = next(item for item in specs if item.item_id == "REQ-ORD-014")
    assert requirement.status == "active"
    assert requirement.kind == "requirement"
    assert requirement.supersedes == ["REQ-ORD-009"]
    assert requirement.applies_to == ["ledgerflow.service.OrderQueryService.search"]
    assert requirement.verified_by == [
        "tests/test_amount_filter.py",
        "tests/test_amount_validation.py",
    ]
    assert requirement.source_path == "specs/order-query.md"
    assert requirement.source_line > 0
    assert len(requirement.revision) == 64

    legacy = next(item for item in specs if item.item_id == "ADR-003")
    assert legacy.status == "superseded"
    assert legacy.superseded_by == "ADR-007"

    decision = next(item for item in specs if item.item_id == "ADR-007")
    assert decision.affects == ["REQ-ORD-014"]


def test_code_index_reports_qualified_symbols_and_lines() -> None:
    symbols = index_code(PROJECT / "src", PROJECT, {"ledgerflow": "order-query"})
    by_name = {symbol.qualname: symbol for symbol in symbols}
    search = by_name["ledgerflow.service.OrderQueryService.search"]

    assert search.path == "src/ledgerflow/service.py"
    assert search.lineno < search.end_lineno
    assert "def search" in search.signature
    assert search.module == "order-query"


def test_test_index_separates_target_and_regression() -> None:
    cases = index_tests(PROJECT / "tests", PROJECT, {"tests": "order-query"})
    by_name = {case.name: case for case in cases}

    assert by_name["test_amount_range_is_inclusive_on_both_bounds"].kind == "target"
    assert by_name["test_amount_range_is_inclusive_on_both_bounds"].covers == ["REQ-ORD-014"]
    assert by_name["test_status_filter"].kind == "regression"
    assert "REQ-ORD-002" in by_name["test_status_filter"].covers
    assert by_name["test_amount_filter_requires_explicit_currency"].kind == "target"


def test_tokenizer_handles_chinese_and_identifiers() -> None:
    tokens = tokenize("OrderQueryService 金额区间 order_query")
    assert "order" in tokens
    assert "query" in tokens
    assert "service" in tokens
    assert "金额" in tokens


def test_sql_terms_drop_noise() -> None:
    terms = sql_terms("REQ-ORD-014 金额区间筛选")
    assert "req" not in terms
    assert "ord" not in terms
    assert any("金额" in term for term in terms)


def test_bm25_ranks_the_matching_spec_first(specs: list[SpecItem]) -> None:
    query = "按金额区间筛选订单"
    documents = [
        f"{item.item_id} {item.title} {' '.join(item.tags)} {item.body}" for item in specs
    ]
    scores = bm25(query, documents)
    ranked = sorted(zip(specs, scores), key=lambda pair: pair[1], reverse=True)
    assert ranked[0][0].item_id == "REQ-ORD-014"
    assert scores[specs.index(next(item for item in specs if item.item_id == "REQ-ORD-009"))] > 0


def test_render_contains_citations_and_state() -> None:
    spec = SpecItem(
        item_id="REQ-ORD-014",
        title="订单金额区间筛选",
        kind="requirement",
        status="active",
        module="order-query",
        tags=["amount"],
        applies_to=["ledgerflow.service.OrderQueryService.search"],
        verified_by=["tests/test_amount_filter.py"],
        supersedes=["REQ-ORD-009"],
        superseded_by="",
        affects=[],
        body="金额必须使用 Decimal 比较。",
        source_path="specs/order-query.md",
        source_line=63,
        revision="a" * 64,
    )
    symbol = CodeSymbol(
        symbol_id="ledgerflow.service.OrderQueryService.search",
        path="src/ledgerflow/service.py",
        qualname="ledgerflow.service.OrderQueryService.search",
        symbol_kind="function",
        lineno=27,
        end_lineno=50,
        signature="def search(self, payload) -> dict",
        docstring="",
        module="order-query",
        source="    def search(self, payload):\n        pass",
    )
    test = TestCase(
        test_id="tests/test_amount_filter.py::test_x",
        path="tests/test_amount_filter.py",
        name="test_x",
        kind="target",
        covers=["REQ-ORD-014"],
        docstring="",
        lineno=15,
        module="order-query",
    )
    pack = ContextPack(
        task={"task_id": "TASK-1", "title": "金额筛选", "description": "实现金额区间筛选"},
        specs=[spec],
        active_decisions=[],
        invalidated=[
            {
                "id": "REQ-ORD-009",
                "title": "旧需求",
                "kind": "requirement",
                "status": "superseded",
                "superseded_by": "REQ-ORD-014",
                "reason": "被取代",
                "source": "specs/order-query.md#L52",
            }
        ],
        code=[symbol],
        target_tests=[test],
        regression_tests=[],
        state=TaskState(task_id="TASK-1", done=["已导入"], todo=["实现"], verified=[]),
        database={
            "dsn": "mysql://root@127.0.0.1:2881/sdd_context",
            "counts": {"spec_item": 12, "code_symbol": 20, "test_case": 30, "ingest_run": 1},
            "queries": 3,
            "query_log": None,
            "recall_terms": ["金额"],
        },
        generated_at="2026-11-04T00:00:00Z",
    )
    markdown = render_markdown(pack)

    assert "REQ-ORD-014" in markdown
    assert "specs/order-query.md#L63" in markdown
    assert "ledgerflow.service.OrderQueryService.search" in markdown
    assert "tests/test_amount_filter.py#L15" in markdown
    assert "REQ-ORD-009" in markdown
    assert "已导入" in markdown
