import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from quantlab.domain.instrument import InstrumentSpec
from quantlab.domain.order import OrderIntent, OrderSide, OrderType
from quantlab.domain.order_state import OrderStatus
from quantlab.execution.broker import SimulationExecutionModel
from quantlab.execution.paper_broker import PaperBroker
from quantlab.execution.replay_broker import ReplayBroker
from quantlab.execution.workflow import TradingWorkflow, WorkflowRiskInputs
from quantlab.market_data.candle import Candle
from quantlab.market_data.replay import (
    DeterministicReplayEngine,
    ReplayConfig,
    SignalAction,
    SignalProposal,
    StrategyContext,
)
from quantlab.persistence.sqlite_store import ReconciliationSnapshot, SQLiteStore
from quantlab.risk.limits import RiskEngine, RiskLimits, RiskReason


BASE_TIME = datetime(2025, 1, 1, tzinfo=timezone.utc)


def research_instrument() -> InstrumentSpec:
    return InstrumentSpec(
        instrument_id="BINANCE:ETHUSDT:SPOT",
        base_currency="ETH",
        quote_currency="USDT",
        settlement_currency="USDT",
        contract_size_base=Decimal("0.01"),
        price_tick=Decimal("0.01"),
        contract_step=Decimal("1"),
        min_contracts=Decimal("1"),
        min_notional=Decimal("5"),
    )


def candle(
    minute: int,
    *,
    open_price: str,
    high: str,
    low: str,
    close: str,
    base_volume: str = "10",
    confirmed: bool = True,
) -> Candle:
    return Candle(
        instrument_id="BINANCE:ETHUSDT:SPOT",
        interval_seconds=60,
        open_time=BASE_TIME + timedelta(minutes=minute),
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        base_volume=Decimal(base_volume),
        quote_volume=Decimal(base_volume) * Decimal(close),
        trade_count=10,
        confirmed=confirmed,
    )


class AlwaysLong:
    def on_confirmed_candle(self, context: StrategyContext) -> SignalProposal:
        return SignalProposal(SignalAction.LONG, "fixture")


def signal_and_opportunity():
    candles = [
        candle(0, open_price="100", high="103", low="99", close="102"),
        candle(1, open_price="110", high="112", low="109", close="111"),
    ]
    result = DeterministicReplayEngine(
        config=ReplayConfig("DATASET-fixture", "always-long"),
        strategy_factory=AlwaysLong,
    ).run(candles)
    return result.signals[0], result.execution_opportunities[0]


def market_intent(signal_id: str, created_at: datetime, price: Decimal, *, client_id=None):
    return OrderIntent(
        intent_id=signal_id,
        client_order_id=client_id,
        instrument=research_instrument(),
        side=OrderSide.BUY,
        contract_quantity=Decimal("10"),
        reference_price=price,
        reduce_only=False,
        created_at=created_at,
    )


def risk_engine() -> RiskEngine:
    return RiskEngine(
        RiskLimits(
            max_order_notional=Decimal("100"),
            max_gross_notional=Decimal("200"),
            max_daily_loss=Decimal("10"),
            max_drawdown=Decimal("20"),
            max_market_age=timedelta(seconds=10),
        )
    )


def risk_inputs(now: datetime) -> WorkflowRiskInputs:
    return WorkflowRiskInputs(
        now=now,
        market_timestamp=now,
        order_submission_enabled=True,
        daily_pnl=Decimal("0"),
        equity=Decimal("1000"),
        peak_equity=Decimal("1000"),
        projected_gross_notional=Decimal("11"),
        reconciliation_max_age=timedelta(minutes=5),
    )


def open_safety_gates(store: SQLiteStore, now: datetime) -> None:
    store.set_kill_switch(engaged=False, reason="simulation fixture", updated_at=now)
    store.record_reconciliation(
        ReconciliationSnapshot(
            snapshot_id="recon-ok",
            checked_at=now,
            orders_match=True,
            positions_match=True,
            balances_match=True,
        )
    )


class ReplayWorkflowTests(TestCase):
    def test_full_signal_to_fill_chain_is_persisted(self) -> None:
        signal, opportunity = signal_and_opportunity()
        with tempfile.TemporaryDirectory() as directory:
            with SQLiteStore(Path(directory) / "state.db") as store:
                open_safety_gates(store, opportunity.priced_at)
                broker = ReplayBroker(
                    store,
                    SimulationExecutionModel(
                        fee_rate=Decimal("0.001"),
                        slippage_bps=Decimal("10"),
                    ),
                )
                workflow = TradingWorkflow(store=store, risk_engine=risk_engine(), broker=broker)
                order_intent = market_intent(
                    signal.signal_id,
                    opportunity.priced_at,
                    opportunity.reference_price,
                )
                result = workflow.execute_replay(
                    signal=signal,
                    opportunity=opportunity,
                    intent=order_intent,
                    risk_inputs=risk_inputs(opportunity.priced_at),
                )

                self.assertTrue(result.risk_approved)
                self.assertTrue(result.filled)
                assert result.broker_execution is not None
                self.assertEqual(result.broker_execution.order.status, OrderStatus.FILLED)
                self.assertEqual(result.broker_execution.fills[0].price, Decimal("110.11"))
                stages = [item["stage"] for item in store.audit_chain(signal.signal_id)]
                self.assertEqual(
                    stages,
                    [
                        "signal",
                        "risk_decision",
                        "order_intent",
                        "order_event",
                        "order_event",
                        "order_event",
                        "fill",
                    ],
                )
                ledger = store.recover_ledger(settlement_currency="USDT", opening_cash="1000")
                self.assertEqual(len(ledger.snapshot({research_instrument().instrument_id: Decimal("111")}).positions), 1)

                # A retry is completely idempotent: no duplicate order, fill, or audit facts.
                again = workflow.execute_replay(
                    signal=signal,
                    opportunity=opportunity,
                    intent=order_intent,
                    risk_inputs=risk_inputs(opportunity.priced_at),
                )
                self.assertTrue(again.filled)
                self.assertEqual(len(store.load_fills()), 1)
                self.assertEqual(len(store.audit_chain(signal.signal_id)), 7)

    def test_default_safety_state_blocks_before_order_creation(self) -> None:
        signal, opportunity = signal_and_opportunity()
        with tempfile.TemporaryDirectory() as directory:
            with SQLiteStore(Path(directory) / "state.db") as store:
                broker = ReplayBroker(
                    store,
                    SimulationExecutionModel(Decimal("0.001"), Decimal("10")),
                )
                workflow = TradingWorkflow(store=store, risk_engine=risk_engine(), broker=broker)
                result = workflow.execute_replay(
                    signal=signal,
                    opportunity=opportunity,
                    intent=market_intent(
                        signal.signal_id,
                        opportunity.priced_at,
                        opportunity.reference_price,
                    ),
                    risk_inputs=risk_inputs(opportunity.priced_at),
                )
                self.assertFalse(result.risk_approved)
                self.assertFalse(result.filled)
                self.assertIn(RiskReason.KILL_SWITCH, result.risk_decision.reasons)
                self.assertIn(RiskReason.RECONCILIATION_REQUIRED, result.risk_decision.reasons)
                self.assertEqual(store.list_orders(), ())
                self.assertEqual(
                    [item["stage"] for item in store.audit_chain(signal.signal_id)],
                    ["signal", "risk_decision"],
                )

    def test_broker_rejection_is_never_reported_as_fill_success(self) -> None:
        signal, opportunity = signal_and_opportunity()
        with tempfile.TemporaryDirectory() as directory:
            with SQLiteStore(Path(directory) / "state.db") as store:
                open_safety_gates(store, opportunity.priced_at)
                broker = ReplayBroker(
                    store,
                    SimulationExecutionModel(Decimal("0.001"), Decimal("10")),
                    reject_reason="fault injection",
                )
                workflow = TradingWorkflow(store=store, risk_engine=risk_engine(), broker=broker)
                result = workflow.execute_replay(
                    signal=signal,
                    opportunity=opportunity,
                    intent=market_intent(
                        signal.signal_id,
                        opportunity.priced_at,
                        opportunity.reference_price,
                    ),
                    risk_inputs=risk_inputs(opportunity.priced_at),
                )
                self.assertTrue(result.risk_approved)
                self.assertFalse(result.filled)
                assert result.broker_execution is not None
                self.assertEqual(result.broker_execution.order.status, OrderStatus.REJECTED)
                self.assertEqual(store.load_fills(), ())
                self.assertNotIn(
                    "fill",
                    [item["stage"] for item in store.audit_chain(signal.signal_id)],
                )


class PaperBrokerTests(TestCase):
    def test_volume_limited_partial_fill_survives_restart_and_duplicate_candle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.db"
            order = market_intent("paper-signal", BASE_TIME, Decimal("100"), client_id="paper-1")
            model = SimulationExecutionModel(
                fee_rate=Decimal("0.001"),
                slippage_bps=Decimal("0"),
                max_volume_participation=Decimal("1"),
            )
            first_candle = candle(
                0,
                open_price="100",
                high="101",
                low="99",
                close="100",
                base_volume="0.05",
            )
            second_candle = candle(
                1,
                open_price="101",
                high="102",
                low="100",
                close="101",
                base_volume="0.05",
            )
            with SQLiteStore(path) as store:
                broker = PaperBroker(store, model)
                broker.submit(order)
                first = broker.on_confirmed_candle(first_candle)
                self.assertEqual(first[0].order.status, OrderStatus.PARTIALLY_FILLED)
                self.assertEqual(first[0].order.filled_contract_quantity, Decimal("5"))
                self.assertEqual(broker.on_confirmed_candle(first_candle), ())

            with SQLiteStore(path) as recovered_store:
                recovered_broker = PaperBroker(recovered_store, model)
                second = recovered_broker.on_confirmed_candle(second_candle)
                self.assertEqual(second[0].order.status, OrderStatus.FILLED)
                self.assertEqual(len(recovered_store.load_fills()), 2)

    def test_limit_order_waits_for_touch_and_uses_limit_price(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with SQLiteStore(Path(directory) / "paper.db") as store:
                broker = PaperBroker(
                    store,
                    SimulationExecutionModel(Decimal("0.001"), Decimal("0")),
                )
                order = OrderIntent(
                    intent_id="limit-signal",
                    client_order_id="limit-1",
                    instrument=research_instrument(),
                    side=OrderSide.BUY,
                    contract_quantity=Decimal("10"),
                    reference_price=Decimal("100"),
                    reduce_only=False,
                    created_at=BASE_TIME,
                    order_type=OrderType.LIMIT,
                    limit_price=Decimal("95"),
                )
                broker.submit(order)
                untouched = candle(0, open_price="100", high="102", low="96", close="101")
                touched = candle(1, open_price="97", high="98", low="94", close="96")
                self.assertEqual(broker.on_confirmed_candle(untouched), ())
                execution = broker.on_confirmed_candle(touched)[0]
                self.assertEqual(execution.fills[0].price, Decimal("95"))
                self.assertEqual(execution.order.status, OrderStatus.FILLED)

    def test_unconfirmed_candle_never_fills_paper_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with SQLiteStore(Path(directory) / "paper.db") as store:
                broker = PaperBroker(
                    store,
                    SimulationExecutionModel(Decimal("0.001"), Decimal("0")),
                )
                order = market_intent("paper-signal", BASE_TIME, Decimal("100"))
                broker.submit(order)
                partial = candle(
                    0,
                    open_price="100",
                    high="101",
                    low="99",
                    close="100",
                    confirmed=False,
                )
                self.assertEqual(broker.on_confirmed_candle(partial), ())
                self.assertEqual(store.load_fills(), ())
