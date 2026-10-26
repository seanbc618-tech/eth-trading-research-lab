"""Position sizing with margin, notional, base units, and contracts separated."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .decimal_utils import DecimalInput, ZERO, positive_decimal
from .instrument import InstrumentSpec


@dataclass(frozen=True, slots=True)
class SizePlan:
    instrument: InstrumentSpec
    requested_margin: Decimal
    leverage: Decimal
    reference_price: Decimal
    contract_quantity: Decimal
    base_quantity: Decimal
    notional: Decimal
    required_margin: Decimal
    unused_margin: Decimal

    @classmethod
    def from_margin(
        cls,
        *,
        instrument: InstrumentSpec,
        margin: DecimalInput,
        leverage: DecimalInput,
        price: DecimalInput,
    ) -> "SizePlan":
        requested_margin = positive_decimal(margin, field="margin")
        parsed_leverage = positive_decimal(leverage, field="leverage")
        reference_price = positive_decimal(price, field="price")
        raw_base_quantity = requested_margin * parsed_leverage / reference_price
        raw_contract_quantity = instrument.contract_quantity(raw_base_quantity)
        contract_quantity = instrument.normalize_contracts(raw_contract_quantity)
        if contract_quantity < instrument.min_contracts or contract_quantity == ZERO:
            raise ValueError("requested margin cannot satisfy min_contracts after quantization")
        base_quantity = instrument.base_quantity(contract_quantity)
        notional = base_quantity * reference_price
        if notional < instrument.min_notional:
            raise ValueError("requested margin cannot satisfy min_notional after quantization")
        required_margin = notional / parsed_leverage
        unused_margin = requested_margin - required_margin
        if unused_margin < ZERO:
            raise AssertionError("quantization must never increase requested exposure")
        return cls(
            instrument=instrument,
            requested_margin=requested_margin,
            leverage=parsed_leverage,
            reference_price=reference_price,
            contract_quantity=contract_quantity,
            base_quantity=base_quantity,
            notional=notional,
            required_margin=required_margin,
            unused_margin=unused_margin,
        )
