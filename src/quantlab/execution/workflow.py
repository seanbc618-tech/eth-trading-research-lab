"""Audited signal → risk → intent → Broker → fill workflow."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from quantlab.domain.order import OrderIntent
from quantlab.market_data.replay import ExecutionOpportunity, SignalEvent
from quantlab.persistence.serde import fill_as_dict
from quantlab.persistence.sqlite_store import AuditEvent, AuditStage, SQLiteStore
from quantlab.risk.limits import RiskContext, RiskDecision, RiskEngine

from .broker import BrokerExecution
from .replay_broker import ReplayBroker


def _audit_id(stage: str, correlation_id: str, payload: object) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"audit-{stage}-{correlation_id[:12]}-{digest[:16]}"


@dataclass(frozen=True, slots=True)
class WorkflowRiskInputs:
    now: datetime
    market_timestamp: datetime
    order_submission_enabled: bool
    daily_pnl: Decimal
    equity: Decimal
    peak_equity: Decimal
    projected_gross_notional: Decimal
    reconciliation_max_age: timedelta
    projected_instrument_notional: Decimal | None = None
    recent_order_count: int = 0


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    risk_approved: bool
    risk_decision: RiskDecision
    broker_execution: BrokerExecution | None

    @property
    def filled(self) -> bool:
        return self.broker_execution is not None and self.broker_execution.filled


class TradingWorkflow:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        risk_engine: RiskEngine,
        broker: ReplayBroker,
    ) -> None:
        self.store = store
        self.risk_engine = risk_engine
        self.broker = broker

    def _append_audit(
        self,
        *,
        correlation_id: str,
        stage: AuditStage,
        occurred_at: datetime,
        payload: dict[str, object],
    ) -> None:
        self.store.append_audit_event(
            AuditEvent(
                audit_id=_audit_id(stage.value, correlation_id, payload),
                correlation_id=correlation_id,
                stage=stage,
                occurred_at=occurred_at,
                payload=payload,
            )
        )

    def execute_replay(
        self,
        *,
        signal: SignalEvent,
        opportunity: ExecutionOpportunity,
        intent: OrderIntent,
        risk_inputs: WorkflowRiskInputs,
    ) -> WorkflowResult:
        if opportunity.signal_id != signal.signal_id:
            raise ValueError("execution opportunity does not belong to signal")
        if intent.intent_id != signal.signal_id:
            raise ValueError("order intent must use signal_id as intent_id")
        if intent.created_at != opportunity.priced_at:
            raise ValueError("order intent must be created at next-bar pricing time")
        correlation_id = signal.signal_id
        self._append_audit(
            correlation_id=correlation_id,
            stage=AuditStage.SIGNAL,
            occurred_at=signal.decision_time,
            payload=signal.as_dict(),
        )
        kill_switch = self.store.kill_switch_state()
        reconciled = self.store.reconciliation_is_fresh(
            now=risk_inputs.now,
            max_age=risk_inputs.reconciliation_max_age,
        )
        risk_context = RiskContext(
            now=risk_inputs.now,
            market_timestamp=risk_inputs.market_timestamp,
            order_submission_enabled=risk_inputs.order_submission_enabled,
            kill_switch_engaged=bool(kill_switch["engaged"]),
            reconciled=reconciled,
            daily_pnl=risk_inputs.daily_pnl,
            equity=risk_inputs.equity,
            peak_equity=risk_inputs.peak_equity,
            projected_gross_notional=risk_inputs.projected_gross_notional,
            projected_instrument_notional=(
                risk_inputs.projected_gross_notional
                if risk_inputs.projected_instrument_notional is None
                else risk_inputs.projected_instrument_notional
            ),
            recent_order_count=risk_inputs.recent_order_count,
        )
        decision = self.risk_engine.evaluate(intent, risk_context)
        risk_payload = {
            **decision.as_dict(),
            "kill_switch_reason": kill_switch["reason"],
            "reconciliation_fresh": reconciled,
        }
        self._append_audit(
            correlation_id=correlation_id,
            stage=AuditStage.RISK_DECISION,
            occurred_at=risk_inputs.now,
            payload=risk_payload,
        )
        if not decision.approved:
            return WorkflowResult(False, decision, None)

        self._append_audit(
            correlation_id=correlation_id,
            stage=AuditStage.ORDER_INTENT,
            occurred_at=intent.created_at,
            payload=intent.as_dict(),
        )
        execution = self.broker.execute(intent, opportunity)
        for order_event in execution.events:
            self._append_audit(
                correlation_id=correlation_id,
                stage=AuditStage.ORDER_EVENT,
                occurred_at=order_event.occurred_at,
                payload=order_event.as_dict(),
            )
        for fill in execution.fills:
            self._append_audit(
                correlation_id=correlation_id,
                stage=AuditStage.FILL,
                occurred_at=fill.occurred_at,
                payload=fill_as_dict(fill),
            )
        return WorkflowResult(True, decision, execution)
