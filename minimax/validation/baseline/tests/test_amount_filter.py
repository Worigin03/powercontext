"""金额区间筛选的目标行为。

covers: REQ-ORD-014, ADR-007
"""

from __future__ import annotations

import pytest

from ledgerflow.service import handle_search

pytestmark = pytest.mark.target


def test_amount_range_is_inclusive_on_both_bounds() -> None:
    response = handle_search(
        {"amount_min": "1200.00", "amount_max": "3600.00", "currency": "CNY"}
    )

    assert response["status"] == 200
    body = response["body"]
    assert [item["order_id"] for item in body["items"]] == [
        "ORD-1012",
        "ORD-1013",
        "ORD-1007",
        "ORD-1004",
        "ORD-1003",
        "ORD-1002",
    ]
    assert body["total"] == 6


def test_amount_filter_keeps_contract_ordering() -> None:
    response = handle_search(
        {"amount_min": "1200.00", "amount_max": "3600.00", "currency": "CNY"}
    )

    ids = [item["order_id"] for item in response["body"]["items"]]
    assert ids == [
        "ORD-1012",
        "ORD-1013",
        "ORD-1007",
        "ORD-1004",
        "ORD-1003",
        "ORD-1002",
    ]
    assert ids.index("ORD-1012") < ids.index("ORD-1013")


def test_amount_min_only() -> None:
    response = handle_search({"amount_min": "15000", "currency": "CNY"})

    assert response["status"] == 200
    assert [item["order_id"] for item in response["body"]["items"]] == ["ORD-1008"]


def test_amount_max_includes_zero_valued_order() -> None:
    response = handle_search({"amount_max": "0.00", "currency": "CNY"})

    assert response["status"] == 200
    assert [item["order_id"] for item in response["body"]["items"]] == ["ORD-1011"]


def test_bounds_with_extra_precision_compare_numerically() -> None:
    response = handle_search(
        {"amount_min": "1199.999", "amount_max": "3600.000", "currency": "CNY"}
    )

    assert response["status"] == 200
    assert response["body"]["total"] == 6


def test_amount_filter_is_scoped_to_the_requested_currency() -> None:
    response = handle_search(
        {"amount_min": "500.00", "amount_max": "1000.00", "currency": "USD"}
    )

    assert response["status"] == 200
    assert [item["order_id"] for item in response["body"]["items"]] == [
        "ORD-1010",
        "ORD-1005",
    ]


def test_currency_alone_filters_without_amount_bounds() -> None:
    response = handle_search({"currency": "USD"})

    assert response["status"] == 200
    assert [item["order_id"] for item in response["body"]["items"]] == [
        "ORD-1010",
        "ORD-1005",
    ]


def test_amount_filter_combines_with_status() -> None:
    response = handle_search(
        {
            "status": "paid",
            "amount_min": "1200.00",
            "amount_max": "3600.00",
            "currency": "CNY",
        }
    )

    assert response["status"] == 200
    assert [item["order_id"] for item in response["body"]["items"]] == [
        "ORD-1012",
        "ORD-1013",
        "ORD-1007",
        "ORD-1002",
    ]
