"""金额筛选的入参校验目标行为。

covers: REQ-ORD-014, CTR-ORD-001
"""

from __future__ import annotations

import pytest

from ledgerflow.service import handle_search

pytestmark = pytest.mark.target


def test_amount_filter_requires_explicit_currency() -> None:
    response = handle_search({"amount_min": "1200.00"})

    assert response["status"] == 400
    assert response["body"]["error"]["code"] == "CURRENCY_REQUIRED"


def test_malformed_amount_is_rejected() -> None:
    response = handle_search(
        {"amount_min": "1,200.00", "currency": "CNY"}
    )

    assert response["status"] == 400
    assert response["body"]["error"]["code"] == "INVALID_AMOUNT"


@pytest.mark.parametrize("value", ["-1", "abc", "12.3.4", "1e5"])
def test_other_malformed_amounts_are_rejected(value: str) -> None:
    response = handle_search({"amount_max": value, "currency": "CNY"})

    assert response["status"] == 400
    assert response["body"]["error"]["code"] == "INVALID_AMOUNT"


def test_blank_amount_is_treated_as_absent() -> None:
    response = handle_search({"amount_min": "   ", "currency": "CNY"})

    assert response["status"] == 200
    assert response["body"]["total"] == 11


def test_lower_bound_above_upper_bound_is_rejected() -> None:
    response = handle_search(
        {"amount_min": "3600.00", "amount_max": "1200.00", "currency": "CNY"}
    )

    assert response["status"] == 400
    assert response["body"]["error"]["code"] == "INVALID_RANGE"


def test_equal_bounds_select_exact_amount() -> None:
    response = handle_search(
        {"amount_min": "1200.00", "amount_max": "1200.00", "currency": "CNY"}
    )

    assert response["status"] == 200
    assert [item["order_id"] for item in response["body"]["items"]] == [
        "ORD-1007",
        "ORD-1002",
    ]
