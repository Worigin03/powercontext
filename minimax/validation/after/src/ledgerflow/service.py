"""Order query service: in-process implementation of ``POST /orders/search``.

The HTTP layer is intentionally thin; everything worth testing lives here so the
same code path is exercised by tests and by the local demo runner.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from ledgerflow.errors import FilterError
from ledgerflow.models import Order
from ledgerflow.repository import OrderRepository

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
ORDER_STATUSES = frozenset({"pending", "paid", "shipped", "cancelled", "refunded"})
# REQ-ORD-014: decimal strings only, at most 12 integer digits and 6 decimals.
AMOUNT_PATTERN = re.compile(r"^[0-9]{1,12}(?:\.[0-9]{1,6})?$")


class OrderQueryService:
    """Filters, orders and paginates ledger orders."""

    def __init__(self, repository: OrderRepository | None = None) -> None:
        self._repository = repository if repository is not None else OrderRepository()

    def search(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        status = _optional_text(payload, "status")
        customer_id = _optional_text(payload, "customer_id")
        if status is not None and status not in ORDER_STATUSES:
            raise FilterError("INVALID_STATUS", f"unsupported status: {status}")
        page, page_size = _paging(payload)

        amount_min = _amount(payload, "amount_min")
        amount_max = _amount(payload, "amount_max")
        currency = _optional_text(payload, "currency")
        if (amount_min is not None or amount_max is not None) and currency is None:
            raise FilterError(
                "CURRENCY_REQUIRED", "currency is required when filtering by amount"
            )
        if amount_min is not None and amount_max is not None and amount_min > amount_max:
            raise FilterError("INVALID_RANGE", "amount_min must not be greater than amount_max")

        rows = [
            order
            for order in self._repository.all()
            if _matches(order, status=status, customer_id=customer_id)
            and _matches_amount(
                order,
                amount_min=amount_min,
                amount_max=amount_max,
                currency=currency,
            )
        ]
        rows.sort(key=lambda order: order.order_id)
        rows.sort(key=lambda order: order.created_at, reverse=True)

        total = len(rows)
        start = (page - 1) * page_size
        window = rows[start : start + page_size]
        return {
            "items": [order.to_dict() for order in window],
            "page": page,
            "page_size": page_size,
            "total": total,
        }


def _matches(order: Order, *, status: str | None, customer_id: str | None) -> bool:
    if status is not None and order.status != status:
        return False
    if customer_id is not None and order.customer_id != customer_id:
        return False
    return True


def _matches_amount(
    order: Order,
    *,
    amount_min: Decimal | None,
    amount_max: Decimal | None,
    currency: str | None,
) -> bool:
    """Closed-interval amount filter; both bounds are inclusive (REQ-ORD-014)."""

    if currency is not None and order.currency != currency:
        return False
    if amount_min is not None and order.amount < amount_min:
        return False
    if amount_max is not None and order.amount > amount_max:
        return False
    return True


def _amount(payload: Mapping[str, Any], key: str) -> Decimal | None:
    """Parse a decimal amount bound; blank values mean "not provided"."""

    raw = _optional_text(payload, key)
    if raw is None:
        return None
    if not AMOUNT_PATTERN.fullmatch(raw):
        raise FilterError("INVALID_AMOUNT", f"{key} must be a decimal amount string")
    return Decimal(raw)


def _optional_text(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise FilterError("INVALID_REQUEST", f"{key} must be a string")
    value = value.strip()
    return value or None


def _paging(payload: Mapping[str, Any]) -> tuple[int, int]:
    page = _positive_int(payload.get("page", 1), "page")
    page_size = _positive_int(payload.get("page_size", DEFAULT_PAGE_SIZE), "page_size")
    if page < 1:
        raise FilterError("INVALID_PAGE", "page must be >= 1")
    if page_size < 1:
        raise FilterError("INVALID_PAGE_SIZE", "page_size must be >= 1")
    return page, min(page_size, MAX_PAGE_SIZE)


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise FilterError("INVALID_REQUEST", f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise FilterError("INVALID_REQUEST", f"{field} must be an integer")


_SERVICE: OrderQueryService | None = None


def default_service() -> OrderQueryService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = OrderQueryService()
    return _SERVICE


def handle_search(
    payload: Mapping[str, Any], *, service: OrderQueryService | None = None
) -> dict[str, Any]:
    """Handle one ``POST /orders/search`` call and return status + JSON body."""

    if not isinstance(payload, Mapping):
        return {
            "status": 400,
            "body": {
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "payload must be a JSON object",
                }
            },
        }
    active = service if service is not None else default_service()
    try:
        body = active.search(payload)
    except FilterError as exc:
        return {"status": 400, "body": {"error": {"code": exc.code, "message": exc.message}}}
    return {"status": 200, "body": body}
