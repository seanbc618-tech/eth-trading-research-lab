import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from quantlab.domain.fill import Fill, FundingPayment
from quantlab.domain.order import OrderIntent, OrderSide
from quantlab.domain.order_state import OrderEvent, OrderEventType, OrderStatus
from quantlab.persistence.sqlite_store import (
    AuditEvent,
    AuditStage,
    PersistenceConflict,
    PersistenceCorruption,
    ReconciliationSnapshot,
    SQLiteStore,
)

from tests.support import eth_swap


NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


def intent(*, client_order_id: str | None = None, contracts: str = "10") -> OrderIntent:
    return OrderIntent(
        intent_id="signal-1",
        client_order_id=client_order_id,
        instrument=eth_swap(),
        side=OrderSide.BUY,
        contract_quantity=Decimal(contracts),
        reference_price=Decimal("2500"),
        reduce_only=False,
        created_at=NOW,
    )


def event(
    order: OrderIntent,
    event_id: str,
    event_type: OrderEventType,
    *,
    contracts: str | None = None,
    price: str | None = None,
) -> OrderEvent:
    return OrderEvent(
        event_id=event_id,
        client_order_id=order.client_order_id or "",
        event_type=event_type,
        occurred_at=NOW,
        exchange_order_id=(
            None if event_type is OrderEventType.SUBMIT else "paper-exchange-order-1"
        ),
        fill_contract_quantity=None if contracts is None else Decimal(contracts),
        fill_price=None if price is None else Decimal(price),
    )


def fill(order: OrderIntent, fill_id: str, contracts: str, price: str) -> Fill:
    return Fill.from_fee_rate(
        fill_id=fill_id,
        order_id=order.client_order_id or "",
        instrument=order.instrument,
        side=order.side,
        price=price,
        contract_quantity=contracts,
        fee_rate="0.0004",
        occurred_at=NOW,
    )


class SQLiteOrderStoreTests(TestCase):
    def test_order_events_and_fill_recover_after_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.db"
            order = intent()
            with SQLiteStore(path) as store:
                self.assertTrue(store.create_order(order))
                self.assertFalse(store.create_order(order))
                store.append_order_event(event(order, "submit", OrderEventType.SUBMIT))
                store.append_order_event(event(order, "ack", OrderEventType.ACKNOWLEDGE))
                fill_event = event(
                    order,
                    "fill-event",
                    OrderEventType.FILL,
                    contracts="10",
                    price="2500",
                )
                execution_fill = fill(order, "fill-1", "10", "2500")
                self.assertTrue(store.append_order_event(fill_event, fill=execution_fill))
                self.assertFalse(store.append_order_event(fill_event, fill=execution_fill))
                self.assertEqual(store.load_order(order.client_order_id or "").status, OrderStatus.FILLED)

            with SQLiteStore(path) as recovered:
                aggregate = recovered.load_order(order.client_order_id or "")
                self.assertEqual(aggregate.status, OrderStatus.FILLED)
                self.assertEqual(aggregate.snapshot().event_count, 3)
                self.assertEqual(len(recovered.load_fills()), 1)
                ledger = recovered.recover_ledger(
                    settlement_currency="USDT",
                    opening_cash="1000",
                )
                snapshot = ledger.snapshot({"ETH-USDT-SWAP": Decimal("2510")})
                self.assertEqual(snapshot.unrealized_pnl_gross, Decimal("1.00"))
                self.assertEqual(snapshot.fees, Decimal("0.100000"))

    def test_funding_facts_are_idempotent_persistent_and_replayed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.db"
            order = intent()
            payment = FundingPayment(
                event_id="funding-1",
                instrument_id=order.instrument.instrument_id,
                amount=Decimal("-1.25"),
                currency="USDT",
                occurred_at=NOW + timedelta(hours=8),
            )
            with SQLiteStore(path) as store:
                store.create_order(order)
                store.append_order_event(event(order, "submit", OrderEventType.SUBMIT))
                store.append_order_event(event(order, "ack", OrderEventType.ACKNOWLEDGE))
                execution_fill = fill(order, "fill-1", "10", "2500")
                store.append_order_event(
                    event(
                        order,
                        "fill-event",
                        OrderEventType.FILL,
                        contracts="10",
                        price="2500",
                    ),
                    fill=execution_fill,
                )
                self.assertTrue(store.append_funding(payment))
                self.assertFalse(store.append_funding(payment))
                with self.assertRaises(PersistenceConflict):
                    store.append_funding(
                        FundingPayment(
                            event_id="funding-1",
                            instrument_id=order.instrument.instrument_id,
                            amount=Decimal("-2"),
                            currency="USDT",
                            occurred_at=payment.occurred_at,
                        )
                    )
            with SQLiteStore(path) as recovered:
                self.assertEqual(recovered.load_funding(), (payment,))
                snapshot = recovered.recover_ledger(
                    settlement_currency="USDT", opening_cash="1000"
                ).snapshot({"ETH-USDT-SWAP": Decimal("2500")})
                self.assertEqual(snapshot.funding, Decimal("-1.25"))
                self.assertEqual(snapshot.net_pnl, Decimal("-1.350000"))

    def test_funding_without_a_known_instrument_fails_before_persistence(self) -> None:
        with SQLiteStore(":memory:") as store:
            with self.assertRaisesRegex(ValueError, "no persisted fill"):
                store.append_funding(
                    FundingPayment(
                        event_id="orphan-funding",
                        instrument_id="ETH-USDT-SWAP",
                        amount=Decimal("-1"),
                        currency="USDT",
                        occurred_at=NOW,
                    )
                )
            self.assertEqual(store.load_funding(), ())

    def test_schema_one_is_additively_migrated_to_funding_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.db"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("INSERT INTO metadata(key, value) VALUES ('schema_version', '1')")
            connection.commit()
            connection.close()
            with SQLiteStore(path) as store:
                version = store.connection.execute(
                    "SELECT value FROM metadata WHERE key='schema_version'"
                ).fetchone()[0]
                self.assertEqual(version, "2")
                table = store.connection.execute(
                    "SELECT name FROM sqlite_master WHERE name='funding_payments'"
                ).fetchone()
                self.assertIsNotNone(table)

    def test_fill_event_and_fill_fact_are_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            order = intent()
            with SQLiteStore(Path(directory) / "state.db") as store:
                store.create_order(order)
                store.append_order_event(event(order, "submit", OrderEventType.SUBMIT))
                fill_event = event(
                    order,
                    "fill-event",
                    OrderEventType.FILL,
                    contracts="4",
                    price="2500",
                )
                with self.assertRaisesRegex(ValueError, "atomically"):
                    store.append_order_event(fill_event)
                with self.assertRaisesRegex(ValueError, "do not match"):
                    store.append_order_event(
                        fill_event,
                        fill=fill(order, "fill-1", "5", "2500"),
                    )
                self.assertEqual(store.load_order(order.client_order_id or "").status, OrderStatus.SUBMITTED)
                self.assertEqual(store.load_fills(), ())

    def test_client_order_id_conflict_and_payload_corruption_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            order = intent(client_order_id="fixed-id")
            with SQLiteStore(Path(directory) / "state.db") as store:
                store.create_order(order)
                conflicting = intent(client_order_id="fixed-id", contracts="11")
                with self.assertRaises(PersistenceConflict):
                    store.create_order(conflicting)
                store.connection.execute(
                    "UPDATE order_intents SET payload_json = '{}' WHERE client_order_id = ?",
                    (order.client_order_id,),
                )
                with self.assertRaises(PersistenceCorruption):
                    store.load_order(order.client_order_id or "")

    def test_non_terminal_query_only_returns_recoverable_orders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with SQLiteStore(Path(directory) / "state.db") as store:
                open_order = intent(client_order_id="open")
                rejected_order = intent(client_order_id="rejected")
                store.create_order(open_order)
                store.create_order(rejected_order)
                store.append_order_event(event(open_order, "submit-open", OrderEventType.SUBMIT))
                store.append_order_event(event(rejected_order, "submit-rejected", OrderEventType.SUBMIT))
                store.append_order_event(event(rejected_order, "reject", OrderEventType.REJECT))
                non_terminal = store.list_orders(non_terminal_only=True)
                self.assertEqual([order.intent.client_order_id for order in non_terminal], ["open"])


class SQLiteSafetyStateTests(TestCase):
    def test_reconciliation_is_fail_closed_and_age_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with SQLiteStore(Path(directory) / "state.db") as store:
                self.assertFalse(
                    store.reconciliation_is_fresh(now=NOW, max_age=timedelta(minutes=5))
                )
                mismatch = ReconciliationSnapshot(
                    snapshot_id="recon-1",
                    checked_at=NOW,
                    orders_match=True,
                    positions_match=False,
                    balances_match=True,
                    details=("position drift",),
                )
                store.record_reconciliation(mismatch)
                self.assertFalse(
                    store.reconciliation_is_fresh(now=NOW, max_age=timedelta(minutes=5))
                )
                matched = ReconciliationSnapshot(
                    snapshot_id="recon-2",
                    checked_at=NOW + timedelta(minutes=1),
                    orders_match=True,
                    positions_match=True,
                    balances_match=True,
                )
                store.record_reconciliation(matched)
                self.assertTrue(
                    store.reconciliation_is_fresh(
                        now=NOW + timedelta(minutes=2),
                        max_age=timedelta(minutes=5),
                    )
                )
                self.assertFalse(
                    store.reconciliation_is_fresh(
                        now=NOW + timedelta(minutes=7),
                        max_age=timedelta(minutes=5),
                    )
                )

    def test_kill_switch_defaults_engaged_and_requires_explicit_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with SQLiteStore(Path(directory) / "state.db") as store:
                self.assertEqual(store.kill_switch_state()["engaged"], True)
                with self.assertRaisesRegex(ValueError, "reason"):
                    store.set_kill_switch(engaged=False, reason="", updated_at=NOW)
                store.set_kill_switch(engaged=False, reason="paper test", updated_at=NOW)
                self.assertEqual(store.kill_switch_state()["engaged"], False)

    def test_audit_chain_is_ordered_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with SQLiteStore(Path(directory) / "state.db") as store:
                signal = AuditEvent(
                    audit_id="audit-signal",
                    correlation_id="signal-1",
                    stage=AuditStage.SIGNAL,
                    occurred_at=NOW,
                    payload={"action": "long"},
                )
                risk = AuditEvent(
                    audit_id="audit-risk",
                    correlation_id="signal-1",
                    stage=AuditStage.RISK_DECISION,
                    occurred_at=NOW,
                    payload={"approved": False, "reasons": ["kill_switch"]},
                )
                self.assertTrue(store.append_audit_event(signal))
                self.assertFalse(store.append_audit_event(signal))
                store.append_audit_event(risk)
                chain = store.audit_chain("signal-1")
                self.assertEqual([item["stage"] for item in chain], ["signal", "risk_decision"])
