from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from unittest import TestCase

from quantlab.domain.order import OrderIntent, OrderSide, OrderType
from quantlab.domain.order_state import (
    InvalidOrderTransition,
    OrderAggregate,
    OrderEvent,
    OrderEventConflict,
    OrderEventType,
    OrderStatus,
)

from tests.support import eth_swap


NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


def intent(*, order_type: OrderType = OrderType.MARKET) -> OrderIntent:
    return OrderIntent(
        intent_id="signal-1",
        instrument=eth_swap(),
        side=OrderSide.BUY,
        contract_quantity=Decimal("10"),
        reference_price=Decimal("2500"),
        reduce_only=False,
        created_at=NOW,
        order_type=order_type,
        limit_price=Decimal("2490") if order_type is OrderType.LIMIT else None,
    )


def event(
    event_id: str,
    event_type: OrderEventType,
    *,
    quantity: str | None = None,
    price: str | None = None,
    exchange_order_id: str | None = "replay-order-1",
) -> OrderEvent:
    order = intent()
    return OrderEvent(
        event_id=event_id,
        client_order_id=order.client_order_id or "",
        event_type=event_type,
        occurred_at=NOW,
        exchange_order_id=exchange_order_id,
        fill_contract_quantity=None if quantity is None else Decimal(quantity),
        fill_price=None if price is None else Decimal(price),
    )


class OrderIntentTests(TestCase):
    def test_client_order_id_is_deterministic(self) -> None:
        first = intent()
        second = intent()
        self.assertEqual(first.client_order_id, second.client_order_id)
        self.assertTrue((first.client_order_id or "").startswith("ethv2-"))

    def test_market_and_limit_price_contract_is_strict(self) -> None:
        self.assertEqual(intent(order_type=OrderType.LIMIT).limit_price, Decimal("2490"))
        with self.assertRaisesRegex(ValueError, "cannot carry"):
            replace(intent(), limit_price=Decimal("2490"))
        with self.assertRaisesRegex(ValueError, "require"):
            replace(intent(order_type=OrderType.LIMIT), limit_price=None)


class OrderStateMachineTests(TestCase):
    def test_partial_fills_reach_filled_with_weighted_average(self) -> None:
        aggregate = OrderAggregate(intent())
        aggregate.apply(event("submit", OrderEventType.SUBMIT, exchange_order_id=None))
        aggregate.apply(event("ack", OrderEventType.ACKNOWLEDGE))
        aggregate.apply(event("fill-1", OrderEventType.FILL, quantity="4", price="2500"))
        partial = aggregate.snapshot()
        self.assertEqual(partial.status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(partial.remaining_contract_quantity, Decimal("6"))

        aggregate.apply(event("fill-2", OrderEventType.FILL, quantity="6", price="2510"))
        filled = aggregate.snapshot()
        self.assertEqual(filled.status, OrderStatus.FILLED)
        self.assertEqual(filled.filled_contract_quantity, Decimal("10"))
        self.assertEqual(filled.average_fill_price, Decimal("2506"))
        with self.assertRaises(InvalidOrderTransition):
            aggregate.apply(event("cancel", OrderEventType.CANCEL))

    def test_overfill_is_rejected_without_mutating_position(self) -> None:
        aggregate = OrderAggregate(intent())
        aggregate.apply(event("submit", OrderEventType.SUBMIT, exchange_order_id=None))
        aggregate.apply(event("fill-1", OrderEventType.FILL, quantity="4", price="2500"))
        before = aggregate.snapshot()
        with self.assertRaisesRegex(InvalidOrderTransition, "exceed"):
            aggregate.apply(event("fill-2", OrderEventType.FILL, quantity="7", price="2500"))
        self.assertEqual(aggregate.snapshot(), before)

    def test_duplicate_event_is_idempotent_but_conflict_fails(self) -> None:
        aggregate = OrderAggregate(intent())
        submitted = event("submit", OrderEventType.SUBMIT, exchange_order_id=None)
        self.assertTrue(aggregate.apply(submitted))
        self.assertFalse(aggregate.apply(submitted))
        with self.assertRaises(OrderEventConflict):
            aggregate.apply(replace(submitted, reason="different"))

    def test_rejected_order_is_not_reported_as_filled(self) -> None:
        aggregate = OrderAggregate(intent())
        aggregate.apply(event("submit", OrderEventType.SUBMIT, exchange_order_id=None))
        aggregate.apply(event("reject", OrderEventType.REJECT, exchange_order_id=None))
        snapshot = aggregate.snapshot()
        self.assertEqual(snapshot.status, OrderStatus.REJECTED)
        self.assertEqual(snapshot.filled_contract_quantity, Decimal("0"))
        self.assertIsNone(snapshot.average_fill_price)

    def test_partial_order_can_be_cancelled_without_losing_fill(self) -> None:
        aggregate = OrderAggregate(intent())
        aggregate.apply(event("submit", OrderEventType.SUBMIT, exchange_order_id=None))
        aggregate.apply(event("ack", OrderEventType.ACKNOWLEDGE))
        aggregate.apply(event("fill", OrderEventType.FILL, quantity="4", price="2500"))
        aggregate.apply(event("cancel-request", OrderEventType.REQUEST_CANCEL))
        aggregate.apply(event("cancel", OrderEventType.CANCEL))
        snapshot = aggregate.snapshot()
        self.assertEqual(snapshot.status, OrderStatus.CANCELED)
        self.assertEqual(snapshot.filled_contract_quantity, Decimal("4"))
        self.assertEqual(snapshot.remaining_contract_quantity, Decimal("6"))
