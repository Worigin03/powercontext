"""Domain models for the ledger order query service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class Order:
    """One immutable ledger order row."""

    order_id: str
    customer_id: str
    status: str
    currency: str
    amount: Decimal
    created_at: datetime
    channel: str

    def to_dict(self) -> dict[str, str]:
        return {
            "order_id": self.order_id,
            "customer_id": self.customer_id,
            "status": self.status,
            "currency": self.currency,
            "amount": format(self.amount, "f"),
            "created_at": self.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "channel": self.channel,
        }
