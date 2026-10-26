"""Explicit specification for a quote-settled linear perpetual contract."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from .decimal_utils import DecimalInput, ZERO, as_decimal, positive_decimal


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    instrument_id: str
    base_currency: str
    quote_currency: str
    settlement_currency: str
    contract_size_base: Decimal
    price_tick: Decimal
    contract_step: Decimal
    min_contracts: Decimal
    min_notional: Decimal

    def __post_init__(self) -> None:
        for field_name in ("instrument_id", "base_currency", "quote_currency", "settlement_currency"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if self.quote_currency != self.settlement_currency:
            raise ValueError("v2 currently supports quote-settled linear contracts only")
        for field_name in (
            "contract_size_base",
            "price_tick",
            "contract_step",
            "min_contracts",
            "min_notional",
        ):
            object.__setattr__(
                self,
                field_name,
                positive_decimal(getattr(self, field_name), field=field_name),
            )

    @staticmethod
    def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
        return (value / step).to_integral_value(rounding=ROUND_DOWN) * step

    def normalize_price(self, value: DecimalInput) -> Decimal:
        price = positive_decimal(value, field="price")
        return self._floor_to_step(price, self.price_tick)

    def normalize_contracts(self, value: DecimalInput) -> Decimal:
        contracts = positive_decimal(value, field="contract_quantity")
        return self._floor_to_step(contracts, self.contract_step)

    def base_quantity(self, contract_quantity: DecimalInput) -> Decimal:
        contracts = as_decimal(contract_quantity, field="contract_quantity")
        return contracts * self.contract_size_base

    def contract_quantity(self, base_quantity: DecimalInput) -> Decimal:
        base = as_decimal(base_quantity, field="base_quantity")
        return base / self.contract_size_base

    def quote_notional(self, price: DecimalInput, contract_quantity: DecimalInput) -> Decimal:
        parsed_price = positive_decimal(price, field="price")
        contracts = as_decimal(contract_quantity, field="contract_quantity")
        return abs(self.base_quantity(contracts) * parsed_price)

    def validate_trade(self, *, price: DecimalInput, contract_quantity: DecimalInput) -> None:
        parsed_price = positive_decimal(price, field="price")
        contracts = positive_decimal(contract_quantity, field="contract_quantity")
        if self.normalize_price(parsed_price) != parsed_price:
            raise ValueError("price is not aligned to price_tick")
        if self.normalize_contracts(contracts) != contracts:
            raise ValueError("contract_quantity is not aligned to contract_step")
        if contracts < self.min_contracts:
            raise ValueError("contract_quantity is below min_contracts")
        if self.quote_notional(parsed_price, contracts) < self.min_notional:
            raise ValueError("filled notional is below min_notional")

    def is_flat_quantity(self, contract_quantity: DecimalInput) -> bool:
        return as_decimal(contract_quantity, field="contract_quantity") == ZERO
