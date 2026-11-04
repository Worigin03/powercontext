"""Read-only order repository backed by a JSON seed file."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from ledgerflow.models import Order

DEFAULT_SEED = Path(__file__).resolve().parents[2] / "data" / "orders.json"


class OrderRepository:
    """Loads the demo ledger once and serves it in memory."""

    def __init__(self, seed_path: Path | str = DEFAULT_SEED) -> None:
        self._seed_path = Path(seed_path)
        self._orders = self._load()

    def _load(self) -> tuple[Order, ...]:
        raw = json.loads(self._seed_path.read_text(encoding="utf-8"))
        orders: list[Order] = []
        for row in raw:
            orders.append(
                Order(
                    order_id=row["order_id"],
                    customer_id=row["customer_id"],
                    status=row["status"],
                    currency=row["currency"],
                    amount=Decimal(row["amount"]),
                    created_at=datetime.strptime(
                        row["created_at"], "%Y-%m-%dT%H:%M:%SZ"
                    ).replace(tzinfo=timezone.utc),
                    channel=row["channel"],
                )
            )
        return tuple(orders)

    def all(self) -> tuple[Order, ...]:
        return self._orders

    def get(self, order_id: str) -> Order:
        for order in self._orders:
            if order.order_id == order_id:
                return order
        from ledgerflow.errors import OrderNotFoundError

        raise OrderNotFoundError(order_id)
