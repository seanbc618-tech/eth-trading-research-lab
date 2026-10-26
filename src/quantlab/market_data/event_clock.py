"""Injectable event-time clock used by replay and future shadow processing."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    @property
    def now(self) -> datetime: ...


class EventClock:
    """A monotonic clock advanced only by canonical market events."""

    def __init__(self) -> None:
        self._now: datetime | None = None

    @property
    def now(self) -> datetime:
        if self._now is None:
            raise RuntimeError("event clock has not started")
        return self._now

    def advance_to(self, value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock values must be timezone-aware")
        normalized = value.astimezone(timezone.utc)
        if self._now is not None and normalized < self._now:
            raise ValueError(
                f"event clock cannot move backwards: {normalized.isoformat()} < {self._now.isoformat()}"
            )
        self._now = normalized
