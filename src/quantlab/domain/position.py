"""Average-cost position accounting for quote-settled linear perpetuals."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .decimal_utils import DecimalInput, ZERO, positive_decimal
from .fill import Fill
from .instrument import InstrumentSpec


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    instrument_id: str
    signed_base_quantity: Decimal
    average_entry_price: Decimal | None
    realized_pnl_gross: Decimal
    unrealized_pnl_gross: Decimal


class LinearPosition:
    def __init__(self, instrument: InstrumentSpec) -> None:
        self.instrument = instrument
        self.signed_base_quantity = ZERO
        self.average_entry_price: Decimal | None = None
        self.realized_pnl_gross = ZERO

    def apply_fill(self, fill: Fill) -> Decimal:
        if fill.instrument != self.instrument:
            raise ValueError("fill instrument specification does not match position")
        incoming = fill.signed_base_quantity
        current = self.signed_base_quantity

        if current == ZERO:
            self.signed_base_quantity = incoming
            self.average_entry_price = fill.price
            return ZERO

        assert self.average_entry_price is not None
        if current * incoming > ZERO:
            combined = abs(current) + abs(incoming)
            self.average_entry_price = (
                abs(current) * self.average_entry_price + abs(incoming) * fill.price
            ) / combined
            self.signed_base_quantity = current + incoming
            return ZERO

        closing_quantity = min(abs(current), abs(incoming))
        direction = Decimal("1") if current > ZERO else Decimal("-1")
        realized = closing_quantity * (fill.price - self.average_entry_price) * direction
        remaining = current + incoming
        self.signed_base_quantity = remaining
        self.realized_pnl_gross += realized

        if remaining == ZERO:
            self.average_entry_price = None
        elif current * remaining < ZERO:
            self.average_entry_price = fill.price
        return realized

    def unrealized_pnl(self, mark_price: DecimalInput) -> Decimal:
        if self.signed_base_quantity == ZERO:
            return ZERO
        assert self.average_entry_price is not None
        mark = positive_decimal(mark_price, field="mark_price")
        return self.signed_base_quantity * (mark - self.average_entry_price)

    def snapshot(self, mark_price: DecimalInput | None = None) -> PositionSnapshot:
        if self.signed_base_quantity == ZERO:
            unrealized = ZERO
        elif mark_price is None:
            raise ValueError("mark_price is required for an open position")
        else:
            unrealized = self.unrealized_pnl(mark_price)
        return PositionSnapshot(
            instrument_id=self.instrument.instrument_id,
            signed_base_quantity=self.signed_base_quantity,
            average_entry_price=self.average_entry_price,
            realized_pnl_gross=self.realized_pnl_gross,
            unrealized_pnl_gross=unrealized,
        )
