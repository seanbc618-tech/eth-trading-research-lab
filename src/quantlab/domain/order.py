"""Broker-independent order intent types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
import hashlib
import json

from .decimal_utils import positive_decimal
from .instrument import InstrumentSpec


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> Decimal:
        return Decimal("1") if self is OrderSide.BUY else Decimal("-1")


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


def require_aware_timestamp(value: datetime, *, field: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class OrderIntent:
    intent_id: str
    instrument: InstrumentSpec
    side: OrderSide
    contract_quantity: Decimal
    reference_price: Decimal
    reduce_only: bool
    created_at: datetime
    order_type: OrderType = OrderType.MARKET
    limit_price: Decimal | None = None
    client_order_id: str | None = None

    def __post_init__(self) -> None:
        if not self.intent_id:
            raise ValueError("intent_id is required")
        if not isinstance(self.side, OrderSide):
            raise TypeError("side must be OrderSide")
        contracts = positive_decimal(self.contract_quantity, field="contract_quantity")
        price = positive_decimal(self.reference_price, field="reference_price")
        self.instrument.validate_trade(price=price, contract_quantity=contracts)
        require_aware_timestamp(self.created_at, field="created_at")
        if not isinstance(self.order_type, OrderType):
            raise TypeError("order_type must be OrderType")
        limit_price: Decimal | None = None
        if self.limit_price is not None:
            limit_price = positive_decimal(self.limit_price, field="limit_price")
            if self.instrument.normalize_price(limit_price) != limit_price:
                raise ValueError("limit_price is not aligned to price_tick")
        if self.order_type is OrderType.LIMIT and limit_price is None:
            raise ValueError("limit orders require limit_price")
        if self.order_type is OrderType.MARKET and limit_price is not None:
            raise ValueError("market orders cannot carry limit_price")
        object.__setattr__(self, "contract_quantity", contracts)
        object.__setattr__(self, "reference_price", price)
        object.__setattr__(self, "limit_price", limit_price)
        if self.client_order_id is None:
            identity_payload = {
                "intent_id": self.intent_id,
                "instrument_id": self.instrument.instrument_id,
                "side": self.side.value,
                "contract_quantity": format(contracts, "f"),
                "reference_price": format(price, "f"),
                "reduce_only": self.reduce_only,
                "created_at": self.created_at.isoformat(),
                "order_type": self.order_type.value,
                "limit_price": None if limit_price is None else format(limit_price, "f"),
            }
            digest = hashlib.sha256(
                json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            object.__setattr__(self, "client_order_id", f"ethv2-{digest[:24]}")
        elif not self.client_order_id.strip() or len(self.client_order_id) > 64:
            raise ValueError("client_order_id must be 1-64 characters")

    @property
    def notional(self) -> Decimal:
        return self.instrument.quote_notional(self.reference_price, self.contract_quantity)

    def as_dict(self) -> dict[str, object]:
        return {
            "intent_id": self.intent_id,
            "client_order_id": self.client_order_id,
            "instrument": {
                "instrument_id": self.instrument.instrument_id,
                "base_currency": self.instrument.base_currency,
                "quote_currency": self.instrument.quote_currency,
                "settlement_currency": self.instrument.settlement_currency,
                "contract_size_base": format(self.instrument.contract_size_base, "f"),
                "price_tick": format(self.instrument.price_tick, "f"),
                "contract_step": format(self.instrument.contract_step, "f"),
                "min_contracts": format(self.instrument.min_contracts, "f"),
                "min_notional": format(self.instrument.min_notional, "f"),
            },
            "side": self.side.value,
            "contract_quantity": format(self.contract_quantity, "f"),
            "reference_price": format(self.reference_price, "f"),
            "reduce_only": self.reduce_only,
            "created_at": self.created_at.isoformat(),
            "order_type": self.order_type.value,
            "limit_price": None if self.limit_price is None else format(self.limit_price, "f"),
        }
