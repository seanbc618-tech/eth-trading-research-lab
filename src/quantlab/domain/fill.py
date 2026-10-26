"""Immutable exchange-fact events used by the ledger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .decimal_utils import DecimalInput, as_decimal, positive_decimal
from .instrument import InstrumentSpec
from .order import OrderSide, require_aware_timestamp


@dataclass(frozen=True, slots=True)
class Fill:
    fill_id: str
    order_id: str
    instrument: InstrumentSpec
    side: OrderSide
    price: Decimal
    contract_quantity: Decimal
    fee: Decimal
    fee_currency: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        if not self.fill_id or not self.order_id:
            raise ValueError("fill_id and order_id are required")
        if not isinstance(self.side, OrderSide):
            raise TypeError("side must be OrderSide")
        price = positive_decimal(self.price, field="price")
        contracts = positive_decimal(self.contract_quantity, field="contract_quantity")
        fee = as_decimal(self.fee, field="fee")
        self.instrument.validate_trade(price=price, contract_quantity=contracts)
        if self.fee_currency != self.instrument.settlement_currency:
            raise ValueError("fee currency must match settlement currency")
        require_aware_timestamp(self.occurred_at, field="occurred_at")
        object.__setattr__(self, "price", price)
        object.__setattr__(self, "contract_quantity", contracts)
        object.__setattr__(self, "fee", fee)

    @classmethod
    def from_fee_rate(
        cls,
        *,
        fill_id: str,
        order_id: str,
        instrument: InstrumentSpec,
        side: OrderSide,
        price: DecimalInput,
        contract_quantity: DecimalInput,
        fee_rate: DecimalInput,
        occurred_at: datetime,
    ) -> "Fill":
        parsed_price = positive_decimal(price, field="price")
        contracts = positive_decimal(contract_quantity, field="contract_quantity")
        rate = as_decimal(fee_rate, field="fee_rate")
        fee = instrument.quote_notional(parsed_price, contracts) * rate
        return cls(
            fill_id=fill_id,
            order_id=order_id,
            instrument=instrument,
            side=side,
            price=parsed_price,
            contract_quantity=contracts,
            fee=fee,
            fee_currency=instrument.settlement_currency,
            occurred_at=occurred_at,
        )

    @property
    def base_quantity(self) -> Decimal:
        return self.instrument.base_quantity(self.contract_quantity)

    @property
    def signed_base_quantity(self) -> Decimal:
        return self.base_quantity * self.side.sign

    @property
    def notional(self) -> Decimal:
        return self.instrument.quote_notional(self.price, self.contract_quantity)


@dataclass(frozen=True, slots=True)
class FundingPayment:
    event_id: str
    instrument_id: str
    amount: Decimal
    currency: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        if not self.event_id or not self.instrument_id:
            raise ValueError("event_id and instrument_id are required")
        object.__setattr__(self, "amount", as_decimal(self.amount, field="amount"))
        if not self.currency:
            raise ValueError("currency is required")
        require_aware_timestamp(self.occurred_at, field="occurred_at")
