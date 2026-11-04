"""Request level errors raised by the order query service."""

from __future__ import annotations


class FilterError(ValueError):
    """A caller supplied filter that the service cannot honour."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class OrderNotFoundError(LookupError):
    """Raised when an order id is unknown."""

    def __init__(self, order_id: str) -> None:
        super().__init__(f"unknown order: {order_id}")
        self.order_id = order_id
