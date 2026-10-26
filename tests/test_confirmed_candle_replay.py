from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest import TestCase

from quantlab.market_data.event_clock import EventClock
from quantlab.market_data.replay import (
    ConfirmedCandleProcessor,
    ConflictingCandleEvent,
    DeterministicReplayEngine,
    ReplayConfig,
    SignalAction,
    SignalProposal,
    StrategyContext,
)

from tests.support import minute_candle


class RecordingStrategy:
    def __init__(self, *, signal_every_bar: bool = False) -> None:
        self.signal_every_bar = signal_every_bar
        self.contexts: list[StrategyContext] = []

    def on_confirmed_candle(self, context: StrategyContext) -> SignalProposal | None:
        self.contexts.append(context)
        if self.signal_every_bar or context.current.close >= Decimal("102"):
            return SignalProposal(
                action=SignalAction.LONG,
                reason="fixture-threshold",
                metadata=(("close", format(context.current.close, "f")),),
            )
        return None


def config() -> ReplayConfig:
    return ReplayConfig(
        dataset_manifest_id="DATASET-fixture",
        strategy_id="fixture-strategy-v1",
        strategy_parameters=(("threshold", "102"),),
    )


class ConfirmedCandleReplayTests(TestCase):
    def test_unconfirmed_candles_never_reach_strategy_or_emit_signal(self) -> None:
        strategy = RecordingStrategy(signal_every_bar=True)
        result = DeterministicReplayEngine(
            config=config(),
            strategy_factory=lambda: strategy,
        ).run([minute_candle(0, confirmed=False), minute_candle(1, confirmed=True)])

        self.assertEqual(result.unconfirmed_candles_ignored, 1)
        self.assertEqual(result.confirmed_candles_processed, 1)
        self.assertEqual(len(result.decisions), 1)
        self.assertEqual(len(result.signals), 1)
        self.assertEqual(len(strategy.contexts), 1)
        self.assertEqual(strategy.contexts[0].current.open_time.minute, 1)

    def test_exact_duplicate_gets_one_decision_and_conflict_fails_closed(self) -> None:
        original = minute_candle(0, source_row=1)
        duplicate = replace(original, source_row=2)
        engine = DeterministicReplayEngine(
            config=config(),
            strategy_factory=lambda: RecordingStrategy(signal_every_bar=True),
        )
        result = engine.run([original, duplicate])
        self.assertEqual(len(result.decisions), 1)
        self.assertEqual(result.exact_duplicates_ignored, 1)

        conflict = minute_candle(0, close_price="102", source_row=3)
        with self.assertRaises(ConflictingCandleEvent):
            engine.run([original, conflict])

    def test_strategy_history_contains_no_future_candle(self) -> None:
        strategy = RecordingStrategy()
        candles = [
            minute_candle(0, close_price="100"),
            minute_candle(1, close_price="101"),
            minute_candle(2, close_price="102"),
        ]
        DeterministicReplayEngine(
            config=config(),
            strategy_factory=lambda: strategy,
        ).run(candles)

        self.assertEqual([len(context.history) for context in strategy.contexts], [1, 2, 3])
        self.assertEqual(
            [[candle.open_time.minute for candle in context.history] for context in strategy.contexts],
            [[0], [0, 1], [0, 1, 2]],
        )
        self.assertEqual(
            [context.now for context in strategy.contexts],
            [candle.close_time for candle in candles],
        )

    def test_signal_is_priced_only_at_next_bar_open(self) -> None:
        first = minute_candle(0, open_price="100", close_price="102")
        second = minute_candle(1, open_price="110", close_price="111")
        result = DeterministicReplayEngine(
            config=config(),
            strategy_factory=lambda: RecordingStrategy(),
        ).run([first, second])

        self.assertEqual(len(result.signals), 2)
        self.assertEqual(len(result.execution_opportunities), 1)
        opportunity = result.execution_opportunities[0]
        self.assertEqual(opportunity.source_candle_key, first.key)
        self.assertEqual(opportunity.execution_candle_key, second.key)
        self.assertEqual(opportunity.eligible_at, first.close_time)
        self.assertEqual(opportunity.priced_at, second.open_time)
        self.assertEqual(opportunity.reference_price, Decimal("110"))
        self.assertEqual(result.pending_signal_ids, (result.signals[-1].signal_id,))

    def test_same_input_and_config_produce_identical_events_and_hash(self) -> None:
        candles = [minute_candle(0), minute_candle(1, close_price="102")]
        engine = DeterministicReplayEngine(
            config=config(),
            strategy_factory=RecordingStrategy,
        )
        first = engine.run(candles)
        second = engine.run(candles)

        self.assertEqual(first.decisions, second.decisions)
        self.assertEqual(first.signals, second.signals)
        self.assertEqual(first.execution_opportunities, second.execution_opportunities)
        self.assertEqual(first.result_hash, second.result_hash)

    def test_incremental_shadow_style_processing_matches_batch_replay(self) -> None:
        candles = [
            minute_candle(0, close_price="100"),
            minute_candle(1, close_price="102"),
            minute_candle(2, close_price="103"),
        ]
        batch = DeterministicReplayEngine(
            config=config(),
            strategy_factory=RecordingStrategy,
        ).run(candles)
        processor = ConfirmedCandleProcessor(
            config=config(),
            strategy=RecordingStrategy(),
            clock=EventClock(),
        )
        outcomes = [processor.process(candle) for candle in candles]
        incremental = processor.finish()

        self.assertTrue(all(outcome.accepted for outcome in outcomes))
        self.assertEqual(incremental.decisions, batch.decisions)
        self.assertEqual(incremental.signals, batch.signals)
        self.assertEqual(
            incremental.execution_opportunities,
            batch.execution_opportunities,
        )
        self.assertEqual(incremental.result_hash, batch.result_hash)

    def test_confirmed_event_time_cannot_move_backwards(self) -> None:
        engine = DeterministicReplayEngine(
            config=config(),
            strategy_factory=RecordingStrategy,
        )
        with self.assertRaisesRegex(ValueError, "strictly ascending"):
            engine.run([minute_candle(1), minute_candle(0)])

    def test_injected_clock_is_monotonic(self) -> None:
        clock = EventClock()
        first = minute_candle(0).open_time
        clock.advance_to(first)
        clock.advance_to(first)
        with self.assertRaisesRegex(ValueError, "backwards"):
            clock.advance_to(first - timedelta(seconds=1))
