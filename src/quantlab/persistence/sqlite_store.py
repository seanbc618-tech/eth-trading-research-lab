"""Transactional SQLite event store with fail-closed recovery semantics."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Iterator, Mapping

from quantlab.domain.fill import Fill, FundingPayment
from quantlab.domain.ledger import TradingLedger
from quantlab.domain.order import OrderIntent
from quantlab.domain.order_state import OrderAggregate, OrderEvent, OrderEventType, OrderStatus

from .serde import (
    canonical_json,
    fill_as_dict,
    fill_from_dict,
    funding_as_dict,
    funding_from_dict,
    order_event_from_dict,
    order_intent_from_dict,
    payload_hash,
)


SCHEMA_VERSION = 2


class PersistenceConflict(ValueError):
    pass


class PersistenceCorruption(RuntimeError):
    pass


class AuditStage(str, Enum):
    SIGNAL = "signal"
    RISK_DECISION = "risk_decision"
    ORDER_INTENT = "order_intent"
    ORDER_EVENT = "order_event"
    FILL = "fill"
    FUNDING = "funding"
    RECONCILIATION = "reconciliation"
    KILL_SWITCH = "kill_switch"


def _require_aware(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    audit_id: str
    correlation_id: str
    stage: AuditStage
    occurred_at: datetime
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.audit_id or not self.correlation_id:
            raise ValueError("audit_id and correlation_id are required")
        if not isinstance(self.stage, AuditStage):
            raise TypeError("stage must be AuditStage")
        object.__setattr__(self, "occurred_at", _require_aware(self.occurred_at, field="occurred_at"))
        canonical_json(dict(self.payload))


@dataclass(frozen=True, slots=True)
class ReconciliationSnapshot:
    snapshot_id: str
    checked_at: datetime
    orders_match: bool
    positions_match: bool
    balances_match: bool
    details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.snapshot_id:
            raise ValueError("snapshot_id is required")
        object.__setattr__(self, "checked_at", _require_aware(self.checked_at, field="checked_at"))
        for value in (self.orders_match, self.positions_match, self.balances_match):
            if not isinstance(value, bool):
                raise TypeError("reconciliation match flags must be boolean")

    @property
    def reconciled(self) -> bool:
        return self.orders_match and self.positions_match and self.balances_match

    def as_dict(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "checked_at": self.checked_at.isoformat(),
            "orders_match": self.orders_match,
            "positions_match": self.positions_match,
            "balances_match": self.balances_match,
            "reconciled": self.reconciled,
            "details": list(self.details),
        }


class SQLiteStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if self.path != Path(":memory:"):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path), isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self._initialize_schema()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "SQLiteStore":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")

    def _initialize_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS order_intents (
                client_order_id TEXT PRIMARY KEY,
                intent_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS order_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                client_order_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                FOREIGN KEY(client_order_id) REFERENCES order_intents(client_order_id)
            );
            CREATE INDEX IF NOT EXISTS idx_order_events_client_sequence
                ON order_events(client_order_id, sequence);
            CREATE TABLE IF NOT EXISTS fills (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                fill_id TEXT NOT NULL UNIQUE,
                client_order_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                FOREIGN KEY(client_order_id) REFERENCES order_intents(client_order_id)
            );
            CREATE TABLE IF NOT EXISTS funding_payments (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                instrument_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_id TEXT NOT NULL UNIQUE,
                correlation_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audit_correlation_sequence
                ON audit_events(correlation_id, sequence);
            CREATE TABLE IF NOT EXISTS reconciliation_snapshots (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id TEXT NOT NULL UNIQUE,
                checked_at TEXT NOT NULL,
                reconciled INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runtime_flags (
                flag_key TEXT PRIMARY KEY,
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL
            );
            """
        )
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            self.connection.execute(
                "INSERT INTO metadata(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        elif int(row["value"]) == 1:
            # v2 adds standalone funding facts. The table was created above;
            # updating metadata is the complete, additive migration.
            self.connection.execute(
                "UPDATE metadata SET value=? WHERE key='schema_version'",
                (str(SCHEMA_VERSION),),
            )
        elif int(row["value"]) != SCHEMA_VERSION:
            raise PersistenceCorruption(
                f"unsupported schema version {row['value']}; expected {SCHEMA_VERSION}"
            )

    @staticmethod
    def _checked_payload(row: sqlite3.Row) -> dict[str, object]:
        payload_json = row["payload_json"]
        if payload_hash(payload_json) != row["payload_hash"]:
            raise PersistenceCorruption("stored payload hash mismatch")
        value = json.loads(payload_json)
        if not isinstance(value, dict):
            raise PersistenceCorruption("stored payload is not an object")
        return value

    def create_order(self, intent: OrderIntent) -> bool:
        assert intent.client_order_id is not None
        payload_json = canonical_json(intent.as_dict())
        digest = payload_hash(payload_json)
        with self._transaction():
            existing = self.connection.execute(
                "SELECT payload_json, payload_hash FROM order_intents WHERE client_order_id = ?",
                (intent.client_order_id,),
            ).fetchone()
            if existing is not None:
                if existing["payload_hash"] != digest or existing["payload_json"] != payload_json:
                    raise PersistenceConflict("client_order_id was reused with different intent")
                return False
            self.connection.execute(
                """
                INSERT INTO order_intents(
                    client_order_id, intent_id, payload_json, payload_hash, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    intent.client_order_id,
                    intent.intent_id,
                    payload_json,
                    digest,
                    intent.created_at.isoformat(),
                ),
            )
        return True

    def _load_order_locked(self, client_order_id: str) -> OrderAggregate:
        intent_row = self.connection.execute(
            "SELECT payload_json, payload_hash FROM order_intents WHERE client_order_id = ?",
            (client_order_id,),
        ).fetchone()
        if intent_row is None:
            raise KeyError(client_order_id)
        intent_payload = self._checked_payload(intent_row)
        aggregate = OrderAggregate(order_intent_from_dict(intent_payload))
        event_rows = self.connection.execute(
            """
            SELECT payload_json, payload_hash FROM order_events
            WHERE client_order_id = ? ORDER BY sequence
            """,
            (client_order_id,),
        ).fetchall()
        for row in event_rows:
            aggregate.apply(order_event_from_dict(self._checked_payload(row)))
        return aggregate

    def load_order(self, client_order_id: str) -> OrderAggregate:
        return self._load_order_locked(client_order_id)

    def has_order_event(self, event_id: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM order_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        return row is not None

    def append_order_event(self, event: OrderEvent, *, fill: Fill | None = None) -> bool:
        event_json = canonical_json(event.as_dict())
        event_digest = payload_hash(event_json)
        fill_json: str | None = None
        fill_digest: str | None = None
        if event.event_type is OrderEventType.FILL and fill is None:
            raise ValueError("order FILL events must atomically persist a Fill")
        if fill is not None:
            if event.event_type is not OrderEventType.FILL:
                raise ValueError("a Fill can only accompany an order FILL event")
            if fill.order_id != event.client_order_id:
                raise ValueError("fill.order_id must equal client_order_id")
            if (
                fill.contract_quantity != event.fill_contract_quantity
                or fill.price != event.fill_price
            ):
                raise ValueError("Fill facts do not match the order event")
            fill_json = canonical_json(fill_as_dict(fill))
            fill_digest = payload_hash(fill_json)

        with self._transaction():
            existing = self.connection.execute(
                "SELECT payload_json, payload_hash FROM order_events WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()
            if existing is not None:
                if existing["payload_hash"] != event_digest or existing["payload_json"] != event_json:
                    raise PersistenceConflict("order event ID was reused with different content")
                if fill is not None:
                    stored_fill = self.connection.execute(
                        "SELECT payload_json, payload_hash FROM fills WHERE fill_id = ?",
                        (fill.fill_id,),
                    ).fetchone()
                    if (
                        stored_fill is None
                        or stored_fill["payload_hash"] != fill_digest
                        or stored_fill["payload_json"] != fill_json
                    ):
                        raise PersistenceConflict("idempotent fill does not match stored fill")
                return False

            aggregate = self._load_order_locked(event.client_order_id)
            aggregate.apply(event)
            if fill is not None:
                existing_fill = self.connection.execute(
                    "SELECT payload_json, payload_hash FROM fills WHERE fill_id = ?",
                    (fill.fill_id,),
                ).fetchone()
                if existing_fill is not None:
                    raise PersistenceConflict("fill ID already exists without matching order event")
            self.connection.execute(
                """
                INSERT INTO order_events(
                    event_id, client_order_id, event_type, payload_json, payload_hash, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.client_order_id,
                    event.event_type.value,
                    event_json,
                    event_digest,
                    event.occurred_at.isoformat(),
                ),
            )
            if fill is not None:
                assert fill_json is not None and fill_digest is not None
                self.connection.execute(
                    """
                    INSERT INTO fills(
                        fill_id, client_order_id, payload_json, payload_hash, occurred_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        fill.fill_id,
                        event.client_order_id,
                        fill_json,
                        fill_digest,
                        fill.occurred_at.isoformat(),
                    ),
                )
        return True

    def list_orders(self, *, non_terminal_only: bool = False) -> tuple[OrderAggregate, ...]:
        rows = self.connection.execute(
            "SELECT client_order_id FROM order_intents ORDER BY created_at, client_order_id"
        ).fetchall()
        aggregates = tuple(self.load_order(row["client_order_id"]) for row in rows)
        if not non_terminal_only:
            return aggregates
        return tuple(order for order in aggregates if not order.status.terminal)

    def load_fills(self) -> tuple[Fill, ...]:
        rows = self.connection.execute(
            "SELECT payload_json, payload_hash FROM fills ORDER BY sequence"
        ).fetchall()
        return tuple(fill_from_dict(self._checked_payload(row)) for row in rows)

    def append_funding(self, payment: FundingPayment) -> bool:
        payload_json = canonical_json(funding_as_dict(payment))
        digest = payload_hash(payload_json)
        with self._transaction():
            known_instrument = any(
                fill.instrument.instrument_id == payment.instrument_id
                for fill in self.load_fills()
            )
            if not known_instrument:
                raise ValueError("funding references an instrument with no persisted fill")
            existing = self.connection.execute(
                "SELECT payload_json, payload_hash FROM funding_payments WHERE event_id=?",
                (payment.event_id,),
            ).fetchone()
            if existing is not None:
                if existing["payload_hash"] != digest or existing["payload_json"] != payload_json:
                    raise PersistenceConflict("funding event ID was reused with different content")
                return False
            self.connection.execute(
                """
                INSERT INTO funding_payments(
                    event_id, instrument_id, payload_json, payload_hash, occurred_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    payment.event_id,
                    payment.instrument_id,
                    payload_json,
                    digest,
                    payment.occurred_at.isoformat(),
                ),
            )
        return True

    def load_funding(self) -> tuple[FundingPayment, ...]:
        rows = self.connection.execute(
            "SELECT payload_json, payload_hash FROM funding_payments ORDER BY sequence"
        ).fetchall()
        return tuple(funding_from_dict(self._checked_payload(row)) for row in rows)

    def recover_ledger(self, *, settlement_currency: str, opening_cash: str) -> TradingLedger:
        ledger = TradingLedger(
            settlement_currency=settlement_currency,
            opening_cash=opening_cash,
        )
        for fill in self.load_fills():
            ledger.apply_fill(fill)
        for payment in self.load_funding():
            ledger.apply_funding(payment)
        return ledger

    def append_audit_event(self, event: AuditEvent) -> bool:
        payload_json = canonical_json(dict(event.payload))
        digest = payload_hash(payload_json)
        with self._transaction():
            existing = self.connection.execute(
                "SELECT payload_json, payload_hash FROM audit_events WHERE audit_id = ?",
                (event.audit_id,),
            ).fetchone()
            if existing is not None:
                if existing["payload_hash"] != digest or existing["payload_json"] != payload_json:
                    raise PersistenceConflict("audit ID was reused with different content")
                return False
            self.connection.execute(
                """
                INSERT INTO audit_events(
                    audit_id, correlation_id, stage, occurred_at, payload_json, payload_hash
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.audit_id,
                    event.correlation_id,
                    event.stage.value,
                    event.occurred_at.isoformat(),
                    payload_json,
                    digest,
                ),
            )
        return True

    def audit_chain(self, correlation_id: str) -> tuple[dict[str, object], ...]:
        rows = self.connection.execute(
            """
            SELECT audit_id, stage, occurred_at, payload_json, payload_hash
            FROM audit_events WHERE correlation_id = ? ORDER BY sequence
            """,
            (correlation_id,),
        ).fetchall()
        return tuple(
            {
                "audit_id": row["audit_id"],
                "stage": row["stage"],
                "occurred_at": row["occurred_at"],
                "payload": self._checked_payload(row),
            }
            for row in rows
        )

    def record_reconciliation(self, snapshot: ReconciliationSnapshot) -> bool:
        payload_json = canonical_json(snapshot.as_dict())
        digest = payload_hash(payload_json)
        with self._transaction():
            existing = self.connection.execute(
                "SELECT payload_json, payload_hash FROM reconciliation_snapshots WHERE snapshot_id = ?",
                (snapshot.snapshot_id,),
            ).fetchone()
            if existing is not None:
                if existing["payload_hash"] != digest or existing["payload_json"] != payload_json:
                    raise PersistenceConflict("reconciliation ID was reused with different content")
                return False
            self.connection.execute(
                """
                INSERT INTO reconciliation_snapshots(
                    snapshot_id, checked_at, reconciled, payload_json, payload_hash
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.checked_at.isoformat(),
                    int(snapshot.reconciled),
                    payload_json,
                    digest,
                ),
            )
        return True

    def latest_reconciliation(self) -> ReconciliationSnapshot | None:
        row = self.connection.execute(
            """
            SELECT payload_json, payload_hash FROM reconciliation_snapshots
            ORDER BY sequence DESC LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        payload = self._checked_payload(row)
        return ReconciliationSnapshot(
            snapshot_id=str(payload["snapshot_id"]),
            checked_at=datetime.fromisoformat(str(payload["checked_at"])),
            orders_match=bool(payload["orders_match"]),
            positions_match=bool(payload["positions_match"]),
            balances_match=bool(payload["balances_match"]),
            details=tuple(str(value) for value in payload["details"]),
        )

    def reconciliation_is_fresh(self, *, now: datetime, max_age: timedelta) -> bool:
        checked_now = _require_aware(now, field="now")
        if max_age <= timedelta(0):
            raise ValueError("max_age must be positive")
        latest = self.latest_reconciliation()
        if latest is None or not latest.reconciled:
            return False
        age = checked_now - latest.checked_at
        return timedelta(0) <= age <= max_age

    def set_kill_switch(self, *, engaged: bool, reason: str, updated_at: datetime) -> None:
        if not isinstance(engaged, bool):
            raise TypeError("engaged must be boolean")
        if not reason:
            raise ValueError("kill switch changes require a reason")
        checked_at = _require_aware(updated_at, field="updated_at")
        payload = {
            "engaged": bool(engaged),
            "reason": reason,
            "updated_at": checked_at.isoformat(),
        }
        payload_json = canonical_json(payload)
        digest = payload_hash(payload_json)
        with self._transaction():
            self.connection.execute(
                """
                INSERT INTO runtime_flags(flag_key, updated_at, payload_json, payload_hash)
                VALUES ('kill_switch', ?, ?, ?)
                ON CONFLICT(flag_key) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    payload_json = excluded.payload_json,
                    payload_hash = excluded.payload_hash
                """,
                (checked_at.isoformat(), payload_json, digest),
            )

    def kill_switch_state(self) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT payload_json, payload_hash FROM runtime_flags WHERE flag_key = 'kill_switch'"
        ).fetchone()
        if row is None:
            return {"engaged": True, "reason": "not_initialized", "updated_at": None}
        return self._checked_payload(row)
