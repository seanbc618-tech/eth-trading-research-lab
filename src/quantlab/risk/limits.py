"""Fail-closed central risk gate evaluated before any future Broker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum

from quantlab.domain.decimal_utils import DecimalInput, ZERO, as_decimal, positive_decimal
from quantlab.domain.order import OrderIntent, require_aware_timestamp


class RiskReason(str, Enum):
    ORDER_SUBMISSION_DISABLED = "order_submission_disabled"
    KILL_SWITCH = "kill_switch"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    STALE_MARKET_DATA = "stale_market_data"
    MARKET_TIME_INVALID = "market_time_invalid"
    ORDER_NOTIONAL_LIMIT = "order_notional_limit"
    GROSS_NOTIONAL_LIMIT = "gross_notional_limit"
    INSTRUMENT_NOTIONAL_LIMIT = "instrument_notional_limit"
    ORDER_RATE_LIMIT = "order_rate_limit"
    DAILY_LOSS_LIMIT = "daily_loss_limit"
    DRAWDOWN_LIMIT = "drawdown_limit"


@dataclass(frozen=True, slots=True)
class RiskLimits:
    max_order_notional: Decimal
    max_gross_notional: Decimal
    max_daily_loss: Decimal
    max_drawdown: Decimal
    max_market_age: timedelta
    max_instrument_notional: Decimal | None = None
    max_orders_per_window: int = 10
    order_rate_window: timedelta = timedelta(minutes=1)

    def __post_init__(self) -> None:
        for field_name in (
            "max_order_notional",
            "max_gross_notional",
            "max_daily_loss",
            "max_drawdown",
        ):
            object.__setattr__(
                self,
                field_name,
                positive_decimal(getattr(self, field_name), field=field_name),
            )
        if not isinstance(self.max_market_age, timedelta) or self.max_market_age <= timedelta(0):
            raise ValueError("max_market_age must be a positive timedelta")
        instrument_limit = (
            self.max_gross_notional
            if self.max_instrument_notional is None
            else positive_decimal(
                self.max_instrument_notional,
                field="max_instrument_notional",
            )
        )
        object.__setattr__(self, "max_instrument_notional", instrument_limit)
        if (
            not isinstance(self.max_orders_per_window, int)
            or isinstance(self.max_orders_per_window, bool)
            or self.max_orders_per_window <= 0
        ):
            raise ValueError("max_orders_per_window must be a positive integer")
        if not isinstance(self.order_rate_window, timedelta) or self.order_rate_window <= timedelta(0):
            raise ValueError("order_rate_window must be a positive timedelta")


@dataclass(frozen=True, slots=True)
class RiskContext:
    now: datetime
    market_timestamp: datetime
    order_submission_enabled: bool
    kill_switch_engaged: bool
    reconciled: bool
    daily_pnl: Decimal
    equity: Decimal
    peak_equity: Decimal
    projected_gross_notional: Decimal
    projected_instrument_notional: Decimal = ZERO
    recent_order_count: int = 0

    def __post_init__(self) -> None:
        require_aware_timestamp(self.now, field="now")
        require_aware_timestamp(self.market_timestamp, field="market_timestamp")
        for field_name in (
            "daily_pnl",
            "equity",
            "peak_equity",
            "projected_gross_notional",
            "projected_instrument_notional",
        ):
            object.__setattr__(self, field_name, as_decimal(getattr(self, field_name), field=field_name))
        if self.projected_gross_notional < ZERO or self.projected_instrument_notional < ZERO:
            raise ValueError("projected notionals cannot be negative")
        if (
            not isinstance(self.recent_order_count, int)
            or isinstance(self.recent_order_count, bool)
            or self.recent_order_count < 0
        ):
            raise ValueError("recent_order_count must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    reasons: tuple[RiskReason, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "approved": self.approved,
            "reasons": [reason.value for reason in self.reasons],
        }


class RiskEngine:
    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits

    def evaluate(self, intent: OrderIntent, context: RiskContext) -> RiskDecision:
        reasons: list[RiskReason] = []
        if not context.order_submission_enabled:
            reasons.append(RiskReason.ORDER_SUBMISSION_DISABLED)
        if context.kill_switch_engaged and not intent.reduce_only:
            reasons.append(RiskReason.KILL_SWITCH)
        if not context.reconciled:
            reasons.append(RiskReason.RECONCILIATION_REQUIRED)

        market_age = context.now - context.market_timestamp
        if market_age < timedelta(0):
            reasons.append(RiskReason.MARKET_TIME_INVALID)
        elif market_age > self.limits.max_market_age:
            reasons.append(RiskReason.STALE_MARKET_DATA)

        if not intent.reduce_only:
            if intent.notional > self.limits.max_order_notional:
                reasons.append(RiskReason.ORDER_NOTIONAL_LIMIT)
            if context.projected_gross_notional > self.limits.max_gross_notional:
                reasons.append(RiskReason.GROSS_NOTIONAL_LIMIT)
            assert self.limits.max_instrument_notional is not None
            if context.projected_instrument_notional > self.limits.max_instrument_notional:
                reasons.append(RiskReason.INSTRUMENT_NOTIONAL_LIMIT)
            if context.recent_order_count >= self.limits.max_orders_per_window:
                reasons.append(RiskReason.ORDER_RATE_LIMIT)
            if context.daily_pnl <= -self.limits.max_daily_loss:
                reasons.append(RiskReason.DAILY_LOSS_LIMIT)
            drawdown = max(context.peak_equity - context.equity, ZERO)
            if drawdown >= self.limits.max_drawdown:
                reasons.append(RiskReason.DRAWDOWN_LIMIT)

        unique_reasons = tuple(dict.fromkeys(reasons))
        return RiskDecision(approved=not unique_reasons, reasons=unique_reasons)
