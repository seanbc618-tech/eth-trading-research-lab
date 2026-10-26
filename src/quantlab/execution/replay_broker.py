"""Deterministic, full-fill ReplayBroker. It cannot access any network."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from quantlab.domain.fill import Fill
from quantlab.domain.order import OrderIntent, OrderType
from quantlab.domain.order_state import OrderEvent, OrderEventType
from quantlab.market_data.replay import ExecutionOpportunity
from quantlab.persistence.sqlite_store import SQLiteStore

from .broker import BrokerExecution, SimulationExecutionModel


def _id(prefix: str, payload: object) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"{prefix}-{digest[:24]}"


@dataclass(slots=True)
class ReplayBroker:
    store: SQLiteStore
    model: SimulationExecutionModel
    reject_reason: str | None = None

    def execute(
        self,
        intent: OrderIntent,
        opportunity: ExecutionOpportunity,
    ) -> BrokerExecution:
        if intent.order_type is not OrderType.MARKET:
            raise ValueError("ReplayBroker.execute currently accepts market intents only")
        if intent.intent_id != opportunity.signal_id:
            raise ValueError("intent must be correlated to the execution opportunity signal")
        if intent.created_at != opportunity.priced_at:
            raise ValueError("replay intent must be created at the next-bar pricing time")
        assert intent.client_order_id is not None
        exchange_order_id = _id("replay-order", {"client_order_id": intent.client_order_id})
        submit = OrderEvent(
            event_id=_id("submit", {"client_order_id": intent.client_order_id}),
            client_order_id=intent.client_order_id,
            event_type=OrderEventType.SUBMIT,
            occurred_at=intent.created_at,
        )
        terminal_events: list[OrderEvent] = [submit]
        self.store.create_order(intent)
        self.store.append_order_event(submit)

        if self.reject_reason is not None:
            reject = OrderEvent(
                event_id=_id(
                    "reject",
                    {"client_order_id": intent.client_order_id, "reason": self.reject_reason},
                ),
                client_order_id=intent.client_order_id,
                event_type=OrderEventType.REJECT,
                occurred_at=opportunity.priced_at,
                reason=self.reject_reason,
            )
            self.store.append_order_event(reject)
            terminal_events.append(reject)
            return BrokerExecution(
                order=self.store.load_order(intent.client_order_id).snapshot(),
                events=tuple(terminal_events),
                fills=(),
            )

        acknowledge = OrderEvent(
            event_id=_id("ack", {"client_order_id": intent.client_order_id}),
            client_order_id=intent.client_order_id,
            event_type=OrderEventType.ACKNOWLEDGE,
            occurred_at=opportunity.priced_at,
            exchange_order_id=exchange_order_id,
        )
        fill_price = self.model.slipped_price(
            price=opportunity.reference_price,
            side=intent.side,
            tick=intent.instrument.price_tick,
        )
        fill_id = _id(
            "replay-fill",
            {
                "client_order_id": intent.client_order_id,
                "execution_candle_key": opportunity.execution_candle_key.as_text(),
            },
        )
        execution_fill = Fill.from_fee_rate(
            fill_id=fill_id,
            order_id=intent.client_order_id,
            instrument=intent.instrument,
            side=intent.side,
            price=fill_price,
            contract_quantity=intent.contract_quantity,
            fee_rate=self.model.fee_rate,
            occurred_at=opportunity.priced_at,
        )
        fill_event = OrderEvent(
            event_id=_id("fill-event", {"fill_id": fill_id}),
            client_order_id=intent.client_order_id,
            event_type=OrderEventType.FILL,
            occurred_at=opportunity.priced_at,
            exchange_order_id=exchange_order_id,
            fill_contract_quantity=execution_fill.contract_quantity,
            fill_price=execution_fill.price,
        )
        self.store.append_order_event(acknowledge)
        self.store.append_order_event(fill_event, fill=execution_fill)
        terminal_events.extend((acknowledge, fill_event))
        return BrokerExecution(
            order=self.store.load_order(intent.client_order_id).snapshot(),
            events=tuple(terminal_events),
            fills=(execution_fill,),
        )
