"""Persistent candle-driven PaperBroker with conservative partial fills."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_DOWN, Decimal

from quantlab.domain.fill import Fill
from quantlab.domain.order import OrderIntent, OrderSide, OrderType
from quantlab.domain.order_state import OrderEvent, OrderEventType, OrderStatus
from quantlab.market_data.candle import Candle
from quantlab.persistence.sqlite_store import SQLiteStore

from .broker import BrokerExecution, SimulationExecutionModel
from .replay_broker import _id


@dataclass(slots=True)
class PaperBroker:
    store: SQLiteStore
    model: SimulationExecutionModel

    def submit(self, intent: OrderIntent) -> BrokerExecution:
        assert intent.client_order_id is not None
        exchange_order_id = _id("paper-order", {"client_order_id": intent.client_order_id})
        submit = OrderEvent(
            event_id=_id("paper-submit", {"client_order_id": intent.client_order_id}),
            client_order_id=intent.client_order_id,
            event_type=OrderEventType.SUBMIT,
            occurred_at=intent.created_at,
        )
        acknowledge = OrderEvent(
            event_id=_id("paper-ack", {"client_order_id": intent.client_order_id}),
            client_order_id=intent.client_order_id,
            event_type=OrderEventType.ACKNOWLEDGE,
            occurred_at=intent.created_at,
            exchange_order_id=exchange_order_id,
        )
        self.store.create_order(intent)
        self.store.append_order_event(submit)
        self.store.append_order_event(acknowledge)
        return BrokerExecution(
            order=self.store.load_order(intent.client_order_id).snapshot(),
            events=(submit, acknowledge),
            fills=(),
        )

    @staticmethod
    def _limit_is_touched(intent: OrderIntent, candle: Candle) -> bool:
        assert intent.limit_price is not None
        if intent.side is OrderSide.BUY:
            return candle.low <= intent.limit_price
        return candle.high >= intent.limit_price

    def on_confirmed_candle(self, candle: Candle) -> tuple[BrokerExecution, ...]:
        if not candle.confirmed:
            return ()
        remaining_base_capacity = candle.base_volume * self.model.max_volume_participation
        executions: list[BrokerExecution] = []
        orders = self.store.list_orders(non_terminal_only=True)
        for aggregate in orders:
            intent = aggregate.intent
            if intent.instrument.instrument_id != candle.instrument_id:
                continue
            if aggregate.status not in {OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED}:
                continue
            if candle.open_time < intent.created_at:
                continue
            candle_event_id = _id(
                "paper-fill-event",
                {
                    "client_order_id": intent.client_order_id,
                    "candle_key": candle.key.as_text(),
                },
            )
            if self.store.has_order_event(candle_event_id):
                continue
            if intent.order_type is OrderType.LIMIT and not self._limit_is_touched(intent, candle):
                continue
            raw_available_contracts = (
                remaining_base_capacity / intent.instrument.contract_size_base
            )
            available_contracts = (
                raw_available_contracts / intent.instrument.contract_step
            ).to_integral_value(rounding=ROUND_DOWN) * intent.instrument.contract_step
            remaining = aggregate.snapshot().remaining_contract_quantity
            fill_contracts = min(remaining, available_contracts)
            if fill_contracts <= Decimal("0"):
                break
            if intent.order_type is OrderType.LIMIT:
                assert intent.limit_price is not None
                fill_price = intent.limit_price
            else:
                fill_price = self.model.slipped_price(
                    price=candle.open,
                    side=intent.side,
                    tick=intent.instrument.price_tick,
                )
            fill_id = _id(
                "paper-fill",
                {
                    "client_order_id": intent.client_order_id,
                    "candle_key": candle.key.as_text(),
                },
            )
            execution_fill = Fill.from_fee_rate(
                fill_id=fill_id,
                order_id=intent.client_order_id or "",
                instrument=intent.instrument,
                side=intent.side,
                price=fill_price,
                contract_quantity=fill_contracts,
                fee_rate=self.model.fee_rate,
                occurred_at=candle.open_time,
            )
            fill_event = OrderEvent(
                event_id=candle_event_id,
                client_order_id=intent.client_order_id or "",
                event_type=OrderEventType.FILL,
                occurred_at=candle.open_time,
                exchange_order_id=aggregate.exchange_order_id,
                fill_contract_quantity=fill_contracts,
                fill_price=fill_price,
            )
            self.store.append_order_event(fill_event, fill=execution_fill)
            remaining_base_capacity -= execution_fill.base_quantity
            executions.append(
                BrokerExecution(
                    order=self.store.load_order(intent.client_order_id or "").snapshot(),
                    events=(fill_event,),
                    fills=(execution_fill,),
                )
            )
        return tuple(executions)

    def cancel(
        self,
        client_order_id: str,
        *,
        occurred_at: datetime,
        reason: str,
    ) -> BrokerExecution:
        aggregate = self.store.load_order(client_order_id)
        request = OrderEvent(
            event_id=_id("paper-cancel-request", {"client_order_id": client_order_id}),
            client_order_id=client_order_id,
            event_type=OrderEventType.REQUEST_CANCEL,
            occurred_at=occurred_at,
            exchange_order_id=aggregate.exchange_order_id,
            reason=reason,
        )
        canceled = OrderEvent(
            event_id=_id("paper-cancel", {"client_order_id": client_order_id}),
            client_order_id=client_order_id,
            event_type=OrderEventType.CANCEL,
            occurred_at=occurred_at,
            exchange_order_id=aggregate.exchange_order_id,
            reason=reason,
        )
        self.store.append_order_event(request)
        self.store.append_order_event(canceled)
        return BrokerExecution(
            order=self.store.load_order(client_order_id).snapshot(),
            events=(request, canceled),
            fills=(),
        )
