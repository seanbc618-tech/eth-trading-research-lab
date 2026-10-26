"""Canonical serialization for event-sourced domain objects."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from quantlab.domain.fill import Fill, FundingPayment
from quantlab.domain.instrument import InstrumentSpec
from quantlab.domain.order import OrderIntent, OrderSide, OrderType
from quantlab.domain.order_state import OrderEvent, OrderEventType


def canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def instrument_from_dict(payload: dict[str, Any]) -> InstrumentSpec:
    return InstrumentSpec(
        instrument_id=payload["instrument_id"],
        base_currency=payload["base_currency"],
        quote_currency=payload["quote_currency"],
        settlement_currency=payload["settlement_currency"],
        contract_size_base=Decimal(payload["contract_size_base"]),
        price_tick=Decimal(payload["price_tick"]),
        contract_step=Decimal(payload["contract_step"]),
        min_contracts=Decimal(payload["min_contracts"]),
        min_notional=Decimal(payload["min_notional"]),
    )


def order_intent_from_dict(payload: dict[str, Any]) -> OrderIntent:
    return OrderIntent(
        intent_id=payload["intent_id"],
        client_order_id=payload["client_order_id"],
        instrument=instrument_from_dict(payload["instrument"]),
        side=OrderSide(payload["side"]),
        contract_quantity=Decimal(payload["contract_quantity"]),
        reference_price=Decimal(payload["reference_price"]),
        reduce_only=payload["reduce_only"],
        created_at=datetime.fromisoformat(payload["created_at"]),
        order_type=OrderType(payload["order_type"]),
        limit_price=(
            None if payload["limit_price"] is None else Decimal(payload["limit_price"])
        ),
    )


def order_event_from_dict(payload: dict[str, Any]) -> OrderEvent:
    return OrderEvent(
        event_id=payload["event_id"],
        client_order_id=payload["client_order_id"],
        event_type=OrderEventType(payload["event_type"]),
        occurred_at=datetime.fromisoformat(payload["occurred_at"]),
        exchange_order_id=payload["exchange_order_id"],
        fill_contract_quantity=(
            None
            if payload["fill_contract_quantity"] is None
            else Decimal(payload["fill_contract_quantity"])
        ),
        fill_price=None if payload["fill_price"] is None else Decimal(payload["fill_price"]),
        reason=payload["reason"],
    )


def fill_as_dict(fill: Fill) -> dict[str, object]:
    return {
        "fill_id": fill.fill_id,
        "order_id": fill.order_id,
        "instrument": {
            "instrument_id": fill.instrument.instrument_id,
            "base_currency": fill.instrument.base_currency,
            "quote_currency": fill.instrument.quote_currency,
            "settlement_currency": fill.instrument.settlement_currency,
            "contract_size_base": format(fill.instrument.contract_size_base, "f"),
            "price_tick": format(fill.instrument.price_tick, "f"),
            "contract_step": format(fill.instrument.contract_step, "f"),
            "min_contracts": format(fill.instrument.min_contracts, "f"),
            "min_notional": format(fill.instrument.min_notional, "f"),
        },
        "side": fill.side.value,
        "price": format(fill.price, "f"),
        "contract_quantity": format(fill.contract_quantity, "f"),
        "fee": format(fill.fee, "f"),
        "fee_currency": fill.fee_currency,
        "occurred_at": fill.occurred_at.isoformat(),
    }


def fill_from_dict(payload: dict[str, Any]) -> Fill:
    return Fill(
        fill_id=payload["fill_id"],
        order_id=payload["order_id"],
        instrument=instrument_from_dict(payload["instrument"]),
        side=OrderSide(payload["side"]),
        price=Decimal(payload["price"]),
        contract_quantity=Decimal(payload["contract_quantity"]),
        fee=Decimal(payload["fee"]),
        fee_currency=payload["fee_currency"],
        occurred_at=datetime.fromisoformat(payload["occurred_at"]),
    )


def funding_as_dict(payment: FundingPayment) -> dict[str, object]:
    return {
        "event_id": payment.event_id,
        "instrument_id": payment.instrument_id,
        "amount": format(payment.amount, "f"),
        "currency": payment.currency,
        "occurred_at": payment.occurred_at.isoformat(),
    }


def funding_from_dict(payload: dict[str, Any]) -> FundingPayment:
    return FundingPayment(
        event_id=payload["event_id"],
        instrument_id=payload["instrument_id"],
        amount=Decimal(payload["amount"]),
        currency=payload["currency"],
        occurred_at=datetime.fromisoformat(payload["occurred_at"]),
    )
