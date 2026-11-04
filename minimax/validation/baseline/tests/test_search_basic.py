"""既有筛选行为与排序契约。

covers: REQ-ORD-001, REQ-ORD-002, REQ-ORD-004
"""

from __future__ import annotations

import pytest

from ledgerflow.service import handle_search

pytestmark = pytest.mark.regression

DEFAULT_ORDER = [
    "ORD-1012",
    "ORD-1013",
    "ORD-1011",
    "ORD-1010",
    "ORD-1009",
    "ORD-1008",
    "ORD-1007",
    "ORD-1006",
    "ORD-1005",
    "ORD-1004",
    "ORD-1003",
    "ORD-1002",
    "ORD-1001",
]


def test_default_listing_returns_every_order_in_contract_order() -> None:
    response = handle_search({})

    assert response["status"] == 200
    body = response["body"]
    assert [item["order_id"] for item in body["items"]] == DEFAULT_ORDER
    assert body["total"] == 13
    assert body["page"] == 1
    assert body["page_size"] == 20


def test_same_created_at_is_broken_by_order_id_ascending() -> None:
    response = handle_search({})

    ids = [item["order_id"] for item in response["body"]["items"]]
    assert ids.index("ORD-1012") < ids.index("ORD-1013")


def test_status_filter() -> None:
    response = handle_search({"status": "paid"})

    assert response["status"] == 200
    body = response["body"]
    assert [item["order_id"] for item in body["items"]] == [
        "ORD-1012",
        "ORD-1013",
        "ORD-1011",
        "ORD-1008",
        "ORD-1007",
        "ORD-1005",
        "ORD-1002",
        "ORD-1001",
    ]
    assert body["total"] == 8


def test_customer_filter() -> None:
    response = handle_search({"customer_id": "C-002"})

    assert response["status"] == 200
    assert [item["order_id"] for item in response["body"]["items"]] == [
        "ORD-1012",
        "ORD-1006",
        "ORD-1002",
    ]


def test_status_and_customer_filter_combine() -> None:
    response = handle_search({"status": "paid", "customer_id": "C-002"})

    assert response["status"] == 200
    assert [item["order_id"] for item in response["body"]["items"]] == [
        "ORD-1012",
        "ORD-1002",
    ]


def test_unknown_fields_are_ignored_for_backward_compatibility() -> None:
    response = handle_search({"unknown_field": "anything", "status": "paid"})

    assert response["status"] == 200
    assert response["body"]["total"] == 8


def test_blank_string_is_treated_as_absent() -> None:
    response = handle_search({"status": "", "customer_id": "   "})

    assert response["status"] == 200
    assert response["body"]["total"] == 13


def test_unknown_status_is_rejected() -> None:
    response = handle_search({"status": "lost"})

    assert response["status"] == 400
    assert response["body"]["error"]["code"] == "INVALID_STATUS"


def test_item_shape_stays_stable() -> None:
    item = handle_search({"customer_id": "C-001"})["body"]["items"][0]

    assert set(item) == {
        "order_id",
        "customer_id",
        "status",
        "currency",
        "amount",
        "created_at",
        "channel",
    }
    assert item["amount"] == "3600.00"
    assert item["created_at"] == "2026-09-07T14:00:00Z"
