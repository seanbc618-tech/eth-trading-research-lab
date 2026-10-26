"""Strict, idempotent order lifecycle state machine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from .decimal_utils import ZERO, positive_decimal
from .order import OrderIntent, require_aware_timestamp


class OrderStatus(str, Enum):
    INTENT = "intent"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_FILLED = "partially_filled"
    CANCEL_PENDING = "cancel_pending"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"

    @property
    def terminal(self) -> bool:
        return self in {
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        }


class OrderEventType(str, Enum):
    SUBMIT = "submit"
    ACKNOWLEDGE = "acknowledge"
    FILL = "fill"
    REQUEST_CANCEL = "request_cancel"
    CANCEL = "cancel"
    REJECT = "reject"
    EXPIRE = "expire"


@dataclass(frozen=True, slots=True)
class OrderEvent:
    event_id: str
    client_order_id: str
    event_type: OrderEventType
    occurred_at: datetime
    exchange_order_id: str | None = None
    fill_contract_quantity: Decimal | None = None
    fill_price: Decimal | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.event_id or not self.client_order_id:
            raise ValueError("event_id and client_order_id are required")
        if not isinstance(self.event_type, OrderEventType):
            raise TypeError("event_type must be OrderEventType")
        require_aware_timestamp(self.occurred_at, field="occurred_at")
        fill_quantity = self.fill_contract_quantity
        fill_price = self.fill_price
        if self.event_type is OrderEventType.FILL:
            if fill_quantity is None or fill_price is None:
                raise ValueError("fill events require quantity and price")
            fill_quantity = positive_decimal(fill_quantity, field="fill_contract_quantity")
            fill_price = positive_decimal(fill_price, field="fill_price")
        elif fill_quantity is not None or fill_price is not None:
            raise ValueError("only fill events may carry fill quantity or price")
        object.__setattr__(self, "fill_contract_quantity", fill_quantity)
        object.__setattr__(self, "fill_price", fill_price)

    def as_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "client_order_id": self.client_order_id,
            "event_type": self.event_type.value,
            "occurred_at": self.occurred_at.isoformat(),
            "exchange_order_id": self.exchange_order_id,
            "fill_contract_quantity": (
                None
                if self.fill_contract_quantity is None
                else format(self.fill_contract_quantity, "f")
            ),
            "fill_price": None if self.fill_price is None else format(self.fill_price, "f"),
            "reason": self.reason,
        }


class InvalidOrderTransition(ValueError):
    pass


class OrderEventConflict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class OrderSnapshot:
    client_order_id: str
    status: OrderStatus
    exchange_order_id: str | None
    requested_contract_quantity: Decimal
    filled_contract_quantity: Decimal
    remaining_contract_quantity: Decimal
    average_fill_price: Decimal | None
    event_count: int


class OrderAggregate:
    def __init__(self, intent: OrderIntent) -> None:
        assert intent.client_order_id is not None
        self.intent = intent
        self.status = OrderStatus.INTENT
        self.exchange_order_id: str | None = None
        self.filled_contract_quantity = ZERO
        self.average_fill_price: Decimal | None = None
        self._events: dict[str, OrderEvent] = {}

    def apply(self, event: OrderEvent) -> bool:
        if event.client_order_id != self.intent.client_order_id:
            raise ValueError("order event targets a different client_order_id")
        existing = self._events.get(event.event_id)
        if existing is not None:
            if existing != event:
                raise OrderEventConflict(f"order event ID {event.event_id!r} was reused")
            return False
        if self.status.terminal:
            raise InvalidOrderTransition(f"cannot apply {event.event_type.value} after {self.status.value}")
        if (
            event.exchange_order_id is not None
            and self.exchange_order_id is not None
            and self.exchange_order_id != event.exchange_order_id
        ):
            raise OrderEventConflict("exchange_order_id changed for the same order")

        if event.event_type is OrderEventType.SUBMIT:
            self._require_status(OrderStatus.INTENT)
            self.status = OrderStatus.SUBMITTED
        elif event.event_type is OrderEventType.ACKNOWLEDGE:
            self._require_status(OrderStatus.SUBMITTED)
            self.exchange_order_id = event.exchange_order_id or self.exchange_order_id
            self.status = OrderStatus.ACKNOWLEDGED
        elif event.event_type is OrderEventType.FILL:
            if self.status not in {
                OrderStatus.SUBMITTED,
                OrderStatus.ACKNOWLEDGED,
                OrderStatus.PARTIALLY_FILLED,
                OrderStatus.CANCEL_PENDING,
            }:
                raise InvalidOrderTransition(f"fill is invalid from {self.status.value}")
            assert event.fill_contract_quantity is not None
            assert event.fill_price is not None
            new_total = self.filled_contract_quantity + event.fill_contract_quantity
            if new_total > self.intent.contract_quantity:
                raise InvalidOrderTransition("cumulative fills exceed requested contract quantity")
            if self.average_fill_price is None:
                self.average_fill_price = event.fill_price
            else:
                self.average_fill_price = (
                    self.average_fill_price * self.filled_contract_quantity
                    + event.fill_price * event.fill_contract_quantity
                ) / new_total
            self.filled_contract_quantity = new_total
            self.status = (
                OrderStatus.FILLED
                if new_total == self.intent.contract_quantity
                else OrderStatus.PARTIALLY_FILLED
            )
        elif event.event_type is OrderEventType.REQUEST_CANCEL:
            if self.status not in {
                OrderStatus.SUBMITTED,
                OrderStatus.ACKNOWLEDGED,
                OrderStatus.PARTIALLY_FILLED,
            }:
                raise InvalidOrderTransition(f"cancel request is invalid from {self.status.value}")
            self.status = OrderStatus.CANCEL_PENDING
        elif event.event_type is OrderEventType.CANCEL:
            if self.status not in {
                OrderStatus.SUBMITTED,
                OrderStatus.ACKNOWLEDGED,
                OrderStatus.PARTIALLY_FILLED,
                OrderStatus.CANCEL_PENDING,
            }:
                raise InvalidOrderTransition(f"cancel is invalid from {self.status.value}")
            self.status = OrderStatus.CANCELED
        elif event.event_type is OrderEventType.REJECT:
            if self.status not in {OrderStatus.SUBMITTED, OrderStatus.ACKNOWLEDGED}:
                raise InvalidOrderTransition(f"reject is invalid from {self.status.value}")
            if self.filled_contract_quantity != ZERO:
                raise InvalidOrderTransition("an order with fills cannot be rejected")
            self.status = OrderStatus.REJECTED
        elif event.event_type is OrderEventType.EXPIRE:
            if self.status not in {
                OrderStatus.SUBMITTED,
                OrderStatus.ACKNOWLEDGED,
                OrderStatus.PARTIALLY_FILLED,
            }:
                raise InvalidOrderTransition(f"expire is invalid from {self.status.value}")
            self.status = OrderStatus.EXPIRED
        else:  # pragma: no cover - Enum exhaustiveness guard
            raise InvalidOrderTransition(f"unknown order event: {event.event_type}")

        if event.exchange_order_id is not None:
            self.exchange_order_id = event.exchange_order_id
        self._events[event.event_id] = event
        return True

    def _require_status(self, expected: OrderStatus) -> None:
        if self.status is not expected:
            raise InvalidOrderTransition(f"expected {expected.value}, found {self.status.value}")

    @property
    def events(self) -> tuple[OrderEvent, ...]:
        return tuple(self._events.values())

    def snapshot(self) -> OrderSnapshot:
        remaining = self.intent.contract_quantity - self.filled_contract_quantity
        return OrderSnapshot(
            client_order_id=self.intent.client_order_id or "",
            status=self.status,
            exchange_order_id=self.exchange_order_id,
            requested_contract_quantity=self.intent.contract_quantity,
            filled_contract_quantity=self.filled_contract_quantity,
            remaining_contract_quantity=remaining,
            average_fill_price=self.average_fill_price,
            event_count=len(self._events),
        )
