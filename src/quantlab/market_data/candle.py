"""Canonical UTC candle event with strict OHLCV invariants."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from quantlab.domain.decimal_utils import DecimalInput, ZERO, as_decimal, positive_decimal


def _utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


@dataclass(frozen=True, order=True, slots=True)
class CandleKey:
    instrument_id: str
    interval_seconds: int
    open_time: datetime

    def __post_init__(self) -> None:
        if not self.instrument_id:
            raise ValueError("instrument_id is required")
        if self.interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        object.__setattr__(self, "open_time", _utc(self.open_time, field="open_time"))

    def as_text(self) -> str:
        return f"{self.instrument_id}|{self.interval_seconds}|{self.open_time.isoformat()}"


@dataclass(frozen=True, slots=True)
class Candle:
    instrument_id: str
    interval_seconds: int
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    base_volume: Decimal
    quote_volume: Decimal | None
    trade_count: int | None
    confirmed: bool
    synthetic: bool = False
    source_row: int | None = None

    def __post_init__(self) -> None:
        if not self.instrument_id:
            raise ValueError("instrument_id is required")
        if not isinstance(self.interval_seconds, int) or self.interval_seconds <= 0:
            raise ValueError("interval_seconds must be a positive integer")
        object.__setattr__(self, "open_time", _utc(self.open_time, field="open_time"))
        for field_name in ("open", "high", "low", "close"):
            object.__setattr__(
                self,
                field_name,
                positive_decimal(getattr(self, field_name), field=field_name),
            )
        base_volume = as_decimal(self.base_volume, field="base_volume")
        if base_volume < ZERO:
            raise ValueError("base_volume cannot be negative")
        object.__setattr__(self, "base_volume", base_volume)
        if self.quote_volume is not None:
            quote_volume = as_decimal(self.quote_volume, field="quote_volume")
            if quote_volume < ZERO:
                raise ValueError("quote_volume cannot be negative")
            object.__setattr__(self, "quote_volume", quote_volume)
        if self.trade_count is not None:
            if not isinstance(self.trade_count, int) or isinstance(self.trade_count, bool):
                raise TypeError("trade_count must be an integer")
            if self.trade_count < 0:
                raise ValueError("trade_count cannot be negative")
        if not isinstance(self.confirmed, bool) or not isinstance(self.synthetic, bool):
            raise TypeError("confirmed and synthetic must be boolean")
        if self.synthetic and not self.confirmed:
            raise ValueError("synthetic historical candles must be confirmed")
        if self.source_row is not None and self.source_row <= 0:
            raise ValueError("source_row must be positive")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high must be at least open, close, and low")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low must be at most open, close, and high")

    @property
    def key(self) -> CandleKey:
        return CandleKey(self.instrument_id, self.interval_seconds, self.open_time)

    @property
    def close_time(self) -> datetime:
        return self.open_time + timedelta(seconds=self.interval_seconds)

    def economic_record(self) -> dict[str, object]:
        """Canonical content excluding the location-specific source row."""
        return {
            "instrument_id": self.instrument_id,
            "interval_seconds": self.interval_seconds,
            "open_time": self.open_time.isoformat(),
            "open": _decimal_text(self.open),
            "high": _decimal_text(self.high),
            "low": _decimal_text(self.low),
            "close": _decimal_text(self.close),
            "base_volume": _decimal_text(self.base_volume),
            "quote_volume": None if self.quote_volume is None else _decimal_text(self.quote_volume),
            "trade_count": self.trade_count,
            "confirmed": self.confirmed,
            "synthetic": self.synthetic,
        }

    def content_hash(self) -> str:
        payload = json.dumps(
            self.economic_record(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @classmethod
    def synthetic_from_previous(cls, previous: "Candle", open_time: datetime) -> "Candle":
        return cls(
            instrument_id=previous.instrument_id,
            interval_seconds=previous.interval_seconds,
            open_time=open_time,
            open=previous.close,
            high=previous.close,
            low=previous.close,
            close=previous.close,
            base_volume=ZERO,
            quote_volume=ZERO if previous.quote_volume is not None else None,
            trade_count=0 if previous.trade_count is not None else None,
            confirmed=True,
            synthetic=True,
            source_row=None,
        )
