"""Idempotent in-memory event ledger for deterministic replay.

Leverage is intentionally absent from PnL. It affects sizing/margin, while
price PnL is always signed base quantity multiplied by the price move.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from .decimal_utils import DecimalInput, ZERO, as_decimal
from .fill import Fill, FundingPayment
from .instrument import InstrumentSpec
from .position import LinearPosition, PositionSnapshot


class DuplicateEventConflict(ValueError):
    """Raised when an existing event ID is reused with different content."""


@dataclass(frozen=True, slots=True)
class ApplyResult:
    applied: bool
    realized_pnl_gross: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    settlement_currency: str
    opening_cash: Decimal
    realized_pnl_gross: Decimal
    unrealized_pnl_gross: Decimal
    fees: Decimal
    funding: Decimal
    net_pnl: Decimal
    cash_balance: Decimal
    equity: Decimal
    positions: tuple[PositionSnapshot, ...]
    event_count: int


class TradingLedger:
    def __init__(self, *, settlement_currency: str, opening_cash: DecimalInput) -> None:
        if not settlement_currency:
            raise ValueError("settlement_currency is required")
        self.settlement_currency = settlement_currency
        self.opening_cash = as_decimal(opening_cash, field="opening_cash")
        self._instruments: dict[str, InstrumentSpec] = {}
        self._positions: dict[str, LinearPosition] = {}
        self._events: dict[tuple[str, str], object] = {}
        self._realized_pnl_gross = ZERO
        self._fees = ZERO
        self._funding = ZERO

    @property
    def event_count(self) -> int:
        return len(self._events)

    def _is_duplicate(self, kind: str, event_id: str, event: object) -> bool:
        key = (kind, event_id)
        existing = self._events.get(key)
        if existing is None:
            return False
        if existing != event:
            raise DuplicateEventConflict(f"{kind} event ID {event_id!r} was reused")
        return True

    def _register_instrument(self, instrument: InstrumentSpec) -> None:
        if instrument.settlement_currency != self.settlement_currency:
            raise ValueError("instrument settlement currency does not match ledger")
        existing = self._instruments.get(instrument.instrument_id)
        if existing is not None and existing != instrument:
            raise ValueError("instrument ID was reused with a different specification")
        self._instruments[instrument.instrument_id] = instrument

    def apply_fill(self, fill: Fill) -> ApplyResult:
        if self._is_duplicate("fill", fill.fill_id, fill):
            return ApplyResult(applied=False)
        self._register_instrument(fill.instrument)
        position = self._positions.setdefault(
            fill.instrument.instrument_id,
            LinearPosition(fill.instrument),
        )
        realized = position.apply_fill(fill)
        self._realized_pnl_gross += realized
        self._fees += fill.fee
        self._events[("fill", fill.fill_id)] = fill
        return ApplyResult(applied=True, realized_pnl_gross=realized)

    def apply_funding(self, payment: FundingPayment) -> ApplyResult:
        if self._is_duplicate("funding", payment.event_id, payment):
            return ApplyResult(applied=False)
        instrument = self._instruments.get(payment.instrument_id)
        if instrument is None:
            raise ValueError("funding references an unknown instrument")
        if payment.currency != self.settlement_currency:
            raise ValueError("funding currency does not match ledger")
        self._funding += payment.amount
        self._events[("funding", payment.event_id)] = payment
        return ApplyResult(applied=True)

    def snapshot(self, mark_prices: Mapping[str, DecimalInput] | None = None) -> LedgerSnapshot:
        marks = mark_prices or {}
        position_snapshots: list[PositionSnapshot] = []
        unrealized = ZERO
        for instrument_id, position in sorted(self._positions.items()):
            mark = marks.get(instrument_id)
            position_snapshot = position.snapshot(mark)
            position_snapshots.append(position_snapshot)
            unrealized += position_snapshot.unrealized_pnl_gross

        net_pnl = self._realized_pnl_gross + unrealized - self._fees + self._funding
        cash_balance = self.opening_cash + self._realized_pnl_gross - self._fees + self._funding
        equity = cash_balance + unrealized
        return LedgerSnapshot(
            settlement_currency=self.settlement_currency,
            opening_cash=self.opening_cash,
            realized_pnl_gross=self._realized_pnl_gross,
            unrealized_pnl_gross=unrealized,
            fees=self._fees,
            funding=self._funding,
            net_pnl=net_pnl,
            cash_balance=cash_balance,
            equity=equity,
            positions=tuple(position_snapshots),
            event_count=self.event_count,
        )
