"""分页契约。

covers: REQ-ORD-003
"""

from __future__ import annotations

import pytest

from ledgerflow.service import handle_search

pytestmark = pytest.mark.regression


def test_second_page_window() -> None:
    response = handle_search({"page": 2, "page_size": 5})

    assert response["status"] == 200
    body = response["body"]
    assert [item["order_id"] for item in body["items"]] == [
        "ORD-1008",
        "ORD-1007",
        "ORD-1006",
        "ORD-1005",
        "ORD-1004",
    ]
    assert body["total"] == 13
    assert body["page"] == 2
    assert body["page_size"] == 5


def test_page_beyond_last_page_is_empty() -> None:
    response = handle_search({"page": 9, "page_size": 5})

    assert response["status"] == 200
    assert response["body"]["items"] == []
    assert response["body"]["total"] == 13


def test_page_size_is_capped() -> None:
    response = handle_search({"page_size": 1000})

    assert response["status"] == 200
    assert response["body"]["page_size"] == 100
    assert len(response["body"]["items"]) == 13


def test_numeric_strings_are_accepted() -> None:
    response = handle_search({"page": "2", "page_size": "3"})

    assert response["status"] == 200
    assert response["body"]["page"] == 2
    assert response["body"]["page_size"] == 3


@pytest.mark.parametrize("page", [0, -1])
def test_invalid_page_is_rejected(page: int) -> None:
    response = handle_search({"page": page})

    assert response["status"] == 400
    assert response["body"]["error"]["code"] == "INVALID_PAGE"


def test_invalid_page_size_is_rejected() -> None:
    response = handle_search({"page_size": 0})

    assert response["status"] == 400
    assert response["body"]["error"]["code"] == "INVALID_PAGE_SIZE"


def test_non_numeric_page_is_rejected() -> None:
    response = handle_search({"page": "two"})

    assert response["status"] == 400
    assert response["body"]["error"]["code"] == "INVALID_REQUEST"
