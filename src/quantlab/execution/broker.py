"""Broker result contracts shared by replay and paper implementations."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from quantlab.domain.decimal_utils import DecimalInput, ZERO, as_decimal
from quantlab.domain.fill import Fill
from quantlab.domain.order import OrderSide
from quantlab.domain.order_state import OrderEvent, OrderSnapshot


@dataclass(frozen=True, slots=True)
class SimulationExecutionModel:
    fee_rate: Decimal
    slippage_bps: Decimal
    max_volume_participation: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        fee_rate = as_decimal(self.fee_rate, field="fee_rate")
        slippage_bps = as_decimal(self.slippage_bps, field="slippage_bps")
        participation = as_decimal(
            self.max_volume_participation,
            field="max_volume_participation",
        )
        if fee_rate < ZERO:
            raise ValueError("fee_rate cannot be negative in the conservative simulator")
        if slippage_bps < ZERO:
            raise ValueError("slippage_bps cannot be negative")
        if not ZERO < participation <= Decimal("1"):
            raise ValueError("max_volume_participation must be in (0, 1]")
        object.__setattr__(self, "fee_rate", fee_rate)
        object.__setattr__(self, "slippage_bps", slippage_bps)
        object.__setattr__(self, "max_volume_participation", participation)

    def slipped_price(self, *, price: DecimalInput, side: OrderSide, tick: Decimal) -> Decimal:
        parsed = as_decimal(price, field="price")
        multiplier = Decimal("1") + (
            self.slippage_bps / Decimal("10000") * side.sign
        )
        raw = parsed * multiplier
        units = raw / tick
        if side is OrderSide.BUY:
            return units.to_integral_value(rounding=ROUND_CEILING) * tick
        return units.to_integral_value(rounding=ROUND_FLOOR) * tick


@dataclass(frozen=True, slots=True)
class BrokerExecution:
    order: OrderSnapshot
    events: tuple[OrderEvent, ...]
    fills: tuple[Fill, ...]

    @property
    def filled(self) -> bool:
        return bool(self.fills) and self.order.remaining_contract_quantity == ZERO
