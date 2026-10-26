"""Confirmed-candle deterministic decision engine with next-bar pricing.

The strategy receives only the confirmed history through the current candle.
Signals become eligible after that candle closes and can receive a replay price
only from the next confirmed candle's open. No Broker or fill is constructed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Callable, Iterable, Protocol

from .candle import Candle, CandleKey
from .event_clock import EventClock


def _canonical_hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _event_id(prefix: str, payload: object) -> str:
    return f"{prefix}-{_canonical_hash(payload)[:20]}"


class SignalAction(str, Enum):
    LONG = "long"
    SHORT = "short"
    EXIT = "exit"


@dataclass(frozen=True, slots=True)
class SignalProposal:
    action: SignalAction
    reason: str
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.action, SignalAction):
            raise TypeError("action must be SignalAction")
        if not self.reason:
            raise ValueError("reason is required")
        normalized = tuple(sorted(self.metadata))
        if len({key for key, _ in normalized}) != len(normalized):
            raise ValueError("signal metadata keys must be unique")
        object.__setattr__(self, "metadata", normalized)

    def as_dict(self) -> dict[str, object]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "metadata": {key: value for key, value in self.metadata},
        }


@dataclass(frozen=True, slots=True)
class StrategyContext:
    now: datetime
    current: Candle
    history: tuple[Candle, ...]


class ConfirmedCandleStrategy(Protocol):
    def on_confirmed_candle(self, context: StrategyContext) -> SignalProposal | None: ...


@dataclass(frozen=True, slots=True)
class ReplayConfig:
    dataset_manifest_id: str
    strategy_id: str
    strategy_parameters: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.dataset_manifest_id or not self.strategy_id:
            raise ValueError("dataset_manifest_id and strategy_id are required")
        normalized = tuple(sorted(self.strategy_parameters))
        if len({key for key, _ in normalized}) != len(normalized):
            raise ValueError("strategy parameter keys must be unique")
        object.__setattr__(self, "strategy_parameters", normalized)

    def as_dict(self) -> dict[str, object]:
        return {
            "dataset_manifest_id": self.dataset_manifest_id,
            "strategy_id": self.strategy_id,
            "strategy_parameters": {key: value for key, value in self.strategy_parameters},
        }


@dataclass(frozen=True, slots=True)
class DecisionEvent:
    decision_id: str
    candle_key: CandleKey
    candle_content_hash: str
    decided_at: datetime
    history_length: int
    action: str
    reason: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "candle_key": self.candle_key.as_text(),
            "candle_content_hash": self.candle_content_hash,
            "decided_at": self.decided_at.isoformat(),
            "history_length": self.history_length,
            "action": self.action,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class SignalEvent:
    signal_id: str
    decision_id: str
    source_candle_key: CandleKey
    decision_time: datetime
    proposal: SignalProposal

    def as_dict(self) -> dict[str, object]:
        return {
            "signal_id": self.signal_id,
            "decision_id": self.decision_id,
            "source_candle_key": self.source_candle_key.as_text(),
            "decision_time": self.decision_time.isoformat(),
            "proposal": self.proposal.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class ExecutionOpportunity:
    opportunity_id: str
    signal_id: str
    source_candle_key: CandleKey
    execution_candle_key: CandleKey
    eligible_at: datetime
    priced_at: datetime
    reference_price: Decimal

    def as_dict(self) -> dict[str, object]:
        return {
            "opportunity_id": self.opportunity_id,
            "signal_id": self.signal_id,
            "source_candle_key": self.source_candle_key.as_text(),
            "execution_candle_key": self.execution_candle_key.as_text(),
            "eligible_at": self.eligible_at.isoformat(),
            "priced_at": self.priced_at.isoformat(),
            "reference_price": format(self.reference_price, "f"),
        }


@dataclass(frozen=True, slots=True)
class ReplayResult:
    config: ReplayConfig
    decisions: tuple[DecisionEvent, ...]
    signals: tuple[SignalEvent, ...]
    execution_opportunities: tuple[ExecutionOpportunity, ...]
    pending_signal_ids: tuple[str, ...]
    confirmed_candles_processed: int
    unconfirmed_candles_ignored: int
    exact_duplicates_ignored: int
    result_hash: str

    def hash_payload(self) -> dict[str, object]:
        return {
            "config": self.config.as_dict(),
            "decisions": [event.as_dict() for event in self.decisions],
            "signals": [event.as_dict() for event in self.signals],
            "execution_opportunities": [
                event.as_dict() for event in self.execution_opportunities
            ],
            "pending_signal_ids": list(self.pending_signal_ids),
            "confirmed_candles_processed": self.confirmed_candles_processed,
            "unconfirmed_candles_ignored": self.unconfirmed_candles_ignored,
            "exact_duplicates_ignored": self.exact_duplicates_ignored,
        }


class ConflictingCandleEvent(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    accepted: bool
    ignored_reason: str | None
    decision: DecisionEvent | None
    signal: SignalEvent | None
    execution_opportunities: tuple[ExecutionOpportunity, ...]


class ConfirmedCandleProcessor:
    """Stateful processor shared by finite replay and future shadow streams."""

    def __init__(
        self,
        *,
        config: ReplayConfig,
        strategy: ConfirmedCandleStrategy,
        clock: EventClock,
    ) -> None:
        self.config = config
        self.strategy = strategy
        self.clock = clock
        self.seen: dict[CandleKey, str] = {}
        self.history: list[Candle] = []
        self.decisions: list[DecisionEvent] = []
        self.signals: list[SignalEvent] = []
        self.opportunities: list[ExecutionOpportunity] = []
        self.pending: list[SignalEvent] = []
        self.unconfirmed_ignored = 0
        self.exact_duplicates_ignored = 0
        self.last_confirmed_open_time: datetime | None = None
        self._finished = False

    def process(self, candle: Candle) -> ProcessOutcome:
        if self._finished:
            raise RuntimeError("processor is already finalized")
        if not candle.confirmed:
            self.unconfirmed_ignored += 1
            return ProcessOutcome(False, "unconfirmed", None, None, ())

        content_hash = candle.content_hash()
        existing_hash = self.seen.get(candle.key)
        if existing_hash is not None:
            if existing_hash != content_hash:
                raise ConflictingCandleEvent(
                    f"confirmed candle changed after first decision: {candle.key.as_text()}"
                )
            self.exact_duplicates_ignored += 1
            return ProcessOutcome(False, "exact_duplicate", None, None, ())
        if (
            self.last_confirmed_open_time is not None
            and candle.open_time <= self.last_confirmed_open_time
        ):
            raise ValueError("confirmed candles must arrive in strictly ascending event-time order")
        self.seen[candle.key] = content_hash
        self.last_confirmed_open_time = candle.open_time

        self.clock.advance_to(candle.open_time)
        released: list[ExecutionOpportunity] = []
        for pending_signal in self.pending:
            opportunity_payload = {
                "signal_id": pending_signal.signal_id,
                "execution_candle_key": candle.key.as_text(),
                "reference_price": format(candle.open, "f"),
            }
            opportunity = ExecutionOpportunity(
                opportunity_id=_event_id("opportunity", opportunity_payload),
                signal_id=pending_signal.signal_id,
                source_candle_key=pending_signal.source_candle_key,
                execution_candle_key=candle.key,
                eligible_at=pending_signal.decision_time,
                priced_at=candle.open_time,
                reference_price=candle.open,
            )
            released.append(opportunity)
            self.opportunities.append(opportunity)
        self.pending = []

        self.clock.advance_to(candle.close_time)
        self.history.append(candle)
        context = StrategyContext(
            now=self.clock.now,
            current=candle,
            history=tuple(self.history),
        )
        proposal = self.strategy.on_confirmed_candle(context)
        decision_payload = {
            "dataset_manifest_id": self.config.dataset_manifest_id,
            "strategy_id": self.config.strategy_id,
            "candle_key": candle.key.as_text(),
            "candle_content_hash": content_hash,
            "proposal": None if proposal is None else proposal.as_dict(),
        }
        decision_id = _event_id("decision", decision_payload)
        decision = DecisionEvent(
            decision_id=decision_id,
            candle_key=candle.key,
            candle_content_hash=content_hash,
            decided_at=self.clock.now,
            history_length=len(self.history),
            action="hold" if proposal is None else proposal.action.value,
            reason=None if proposal is None else proposal.reason,
        )
        self.decisions.append(decision)

        signal: SignalEvent | None = None
        if proposal is not None:
            signal_payload = {
                "decision_id": decision_id,
                "source_candle_key": candle.key.as_text(),
                "proposal": proposal.as_dict(),
            }
            signal = SignalEvent(
                signal_id=_event_id("signal", signal_payload),
                decision_id=decision_id,
                source_candle_key=candle.key,
                decision_time=self.clock.now,
                proposal=proposal,
            )
            self.signals.append(signal)
            self.pending.append(signal)
        return ProcessOutcome(True, None, decision, signal, tuple(released))

    def finish(self) -> ReplayResult:
        if self._finished:
            raise RuntimeError("processor is already finalized")
        self._finished = True
        provisional = ReplayResult(
            config=self.config,
            decisions=tuple(self.decisions),
            signals=tuple(self.signals),
            execution_opportunities=tuple(self.opportunities),
            pending_signal_ids=tuple(signal.signal_id for signal in self.pending),
            confirmed_candles_processed=len(self.history),
            unconfirmed_candles_ignored=self.unconfirmed_ignored,
            exact_duplicates_ignored=self.exact_duplicates_ignored,
            result_hash="",
        )
        return ReplayResult(
            config=provisional.config,
            decisions=provisional.decisions,
            signals=provisional.signals,
            execution_opportunities=provisional.execution_opportunities,
            pending_signal_ids=provisional.pending_signal_ids,
            confirmed_candles_processed=provisional.confirmed_candles_processed,
            unconfirmed_candles_ignored=provisional.unconfirmed_candles_ignored,
            exact_duplicates_ignored=provisional.exact_duplicates_ignored,
            result_hash=_canonical_hash(provisional.hash_payload()),
        )


class DeterministicReplayEngine:
    def __init__(
        self,
        *,
        config: ReplayConfig,
        strategy_factory: Callable[[], ConfirmedCandleStrategy],
        clock_factory: Callable[[], EventClock] = EventClock,
    ) -> None:
        self.config = config
        self.strategy_factory = strategy_factory
        self.clock_factory = clock_factory

    def run(self, candles: Iterable[Candle]) -> ReplayResult:
        processor = ConfirmedCandleProcessor(
            config=self.config,
            strategy=self.strategy_factory(),
            clock=self.clock_factory(),
        )
        for candle in candles:
            processor.process(candle)
        return processor.finish()
