"""Offline single-instrument host, separate from the teaching plugin."""
from __future__ import annotations
import argparse
import csv
import hashlib
import html
import json
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Callable

from quantlab.domain.instrument import InstrumentSpec
from quantlab.domain.order import OrderIntent, OrderSide
from quantlab.domain.sizing import SizePlan
from quantlab.execution.broker import SimulationExecutionModel
from quantlab.execution.replay_broker import ReplayBroker
from quantlab.execution.workflow import TradingWorkflow, WorkflowRiskInputs
from quantlab.market_data.candle import Candle
from quantlab.market_data.replay import ConfirmedCandleStrategy, DeterministicReplayEngine, ReplayConfig, SignalAction
from quantlab.persistence.serde import fill_as_dict
from quantlab.persistence.sqlite_store import SQLiteStore, ReconciliationSnapshot
from quantlab.risk.limits import RiskEngine, RiskLimits
from quantlab.tutorial import TeachingMeanStrategy

D = Decimal
SYMBOL = "DEMO-USD-LINEAR"
STARTING_CASH = D("10000")


def canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def load_candles(path: Path) -> tuple[Candle, ...]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("empty dataset")
    bars = []
    for row in rows:
        if row["synthetic"] != "true":
            raise ValueError("this teaching entrypoint accepts explicitly synthetic data only")
        bars.append(Candle(SYMBOL, 3600, datetime.fromisoformat(row["open_time"]),
                           D(row["open"]), D(row["high"]), D(row["low"]), D(row["close"]),
                           D(row["base_volume"]), None, None, True, synthetic=True))
    return tuple(bars)


def run(candles: tuple[Candle, ...], *, strategy_factory: Callable[[], ConfirmedCandleStrategy] = TeachingMeanStrategy,
        strategy_id: str = "teaching-mean-4", max_order_notional: Decimal = D("1500"),
        fee_rate: Decimal = D("0.001"), slippage_bps: Decimal = D("5")) -> dict:
    # Pin arithmetic regardless of the caller's global Decimal context.
    with localcontext() as context:
        context.prec = 28
        return _run(candles, strategy_factory, strategy_id, max_order_notional, fee_rate, slippage_bps)


def _run(candles, strategy_factory, strategy_id, max_order_notional, fee_rate, slippage_bps):
    if not candles:
        raise ValueError("empty dataset")
    for index, candle in enumerate(candles):
        if candle.instrument_id != SYMBOL or candle.interval_seconds != 3600 or not candle.confirmed or not candle.synthetic:
            raise ValueError("demo requires one synthetic, confirmed, hourly instrument")
        if index and candle.open_time != candles[index - 1].close_time:
            raise ValueError("INVALID_DECLARED_GAP: missing, overlapping or unordered candle; no fill-forward")
    dataset_hash = canonical_hash([bar.economic_record() for bar in candles])
    replay = DeterministicReplayEngine(config=ReplayConfig(dataset_hash, strategy_id), strategy_factory=strategy_factory).run(candles)
    instrument = InstrumentSpec(SYMBOL, "DEMO", "USD", "USD", D("1"), D("0.01"), D("0.001"), D("0.001"), D("1"))
    model = SimulationExecutionModel(fee_rate, slippage_bps)
    limits = RiskLimits(max_order_notional, D("1500"), D("500"), D("1000"), timedelta(seconds=1),
                        max_instrument_notional=D("1500"), max_orders_per_window=4, order_rate_window=timedelta(days=1))
    signals = {signal.signal_id: signal for signal in replay.signals}
    opportunities = {item.priced_at: item for item in replay.execution_opportunities}
    curve, decisions, submitted_times = [], [], []
    peak, day_start, current_day = STARTING_CASH, STARTING_CASH, None
    with SQLiteStore(":memory:") as store:
        store.set_kill_switch(engaged=False, reason="synthetic offline demo only", updated_at=candles[0].open_time)
        workflow = TradingWorkflow(store=store, risk_engine=RiskEngine(limits), broker=ReplayBroker(store, model))
        for candle in candles:
            ledger = store.recover_ledger(settlement_currency="USD", opening_cash=str(STARTING_CASH))
            marked = ledger.snapshot({SYMBOL: candle.open})
            if current_day != candle.open_time.date():
                current_day, day_start = candle.open_time.date(), marked.equity
            peak = max(peak, marked.equity)
            opportunity = opportunities.get(candle.open_time)
            if opportunity is not None:
                signal = signals[opportunity.signal_id]
                held = sum((p.signed_base_quantity for p in marked.positions), D(0))
                action = signal.proposal.action
                if action not in (SignalAction.LONG, SignalAction.EXIT):
                    raise ValueError("demo host supports long/flat only")
                if (action is SignalAction.LONG and held == 0) or (action is SignalAction.EXIT and held > 0):
                    reduce = action is SignalAction.EXIT
                    side = OrderSide.SELL if reduce else OrderSide.BUY
                    price = model.slipped_price(price=candle.open, side=side, tick=instrument.price_tick)
                    if reduce:
                        quantity, projected = instrument.contract_quantity(held), D(0)
                    else:
                        if marked.equity <= 0:
                            raise ValueError("non-positive equity")
                        size = SizePlan.from_margin(instrument=instrument, margin=marked.equity * D("0.1"), leverage="1", price=price)
                        quantity, projected = size.contract_quantity, size.notional
                    intent = OrderIntent(intent_id=signal.signal_id, instrument=instrument, side=side,
                                         contract_quantity=quantity, reference_price=price,
                                         reduce_only=reduce, created_at=opportunity.priced_at)
                    # Local store is the only execution state; this is not exchange reconciliation.
                    store.record_reconciliation(ReconciliationSnapshot(signal.signal_id, candle.open_time, True, True, True))
                    outcome = workflow.execute_replay(signal=signal, opportunity=opportunity, intent=intent,
                        risk_inputs=WorkflowRiskInputs(now=candle.open_time, market_timestamp=candle.open_time,
                            order_submission_enabled=True, daily_pnl=marked.equity-day_start, equity=marked.equity,
                            peak_equity=peak, projected_gross_notional=projected, projected_instrument_notional=projected,
                            recent_order_count=sum(candle.open_time - t < limits.order_rate_window for t in submitted_times),
                            reconciliation_max_age=timedelta(seconds=1)))
                    if outcome.risk_approved:
                        submitted_times.append(candle.open_time)
                    decisions.append({"signal_id": signal.signal_id, "decision_time": signal.decision_time.isoformat(),
                                      "execution_time": candle.open_time.isoformat(), "action": action.value,
                                      **outcome.risk_decision.as_dict(), "filled": outcome.filled,
                                      "audit": list(store.audit_chain(signal.signal_id))})
            ledger = store.recover_ledger(settlement_currency="USD", opening_cash=str(STARTING_CASH))
            snapshot = ledger.snapshot({SYMBOL: candle.close})
            peak = max(peak, snapshot.equity)
            curve.append({"time": candle.close_time.isoformat(), "equity": str(snapshot.equity),
                          "drawdown": str((peak-snapshot.equity)/peak)})
        fills = [fill_as_dict(fill) for fill in store.load_fills()]
        payload = {"dataset": "SYNTHETIC_TEACHING_ONLY", "dataset_sha256": dataset_hash,
                   "strategy": strategy_id, "replay_sha256": replay.result_hash,
                   "assumptions": {"fee_rate": str(fee_rate), "slippage_bps": str(slippage_bps),
                                   "max_order_notional": str(max_order_notional), "exposure_fraction": "0.1",
                                   "execution": "full fill at next bar open plus adverse slippage", "funding": "not simulated"},
                   "metrics": {"starting_equity": str(STARTING_CASH), "ending_equity": str(snapshot.equity),
                               "net_pnl": str(snapshot.net_pnl), "realized_pnl_gross": str(snapshot.realized_pnl_gross),
                               "unrealized_pnl_gross": str(snapshot.unrealized_pnl_gross), "fees": str(snapshot.fees),
                               "return_fraction": str(snapshot.net_pnl / STARTING_CASH),
                               "max_drawdown_fraction": str(max(D(p["drawdown"]) for p in curve)),
                               "fill_count": len(fills), "risk_rejections": sum(not d["approved"] for d in decisions),
                               "open_base_quantity": str(sum((p.signed_base_quantity for p in snapshot.positions), D(0)))},
                   "pending_signal_ids": list(replay.pending_signal_ids), "equity_curve": curve,
                   "fills": fills, "risk_decisions": decisions}
    return {**payload, "result_sha256": canonical_hash(payload)}


def write_report(result: dict, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(result, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    with (output / "equity.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["time", "equity", "drawdown"])
        writer.writeheader(); writer.writerows(result["equity_curve"])
    values = [float(row["equity"]) for row in result["equity_curve"]]
    low, high = min(values+[10000]), max(values+[10000])
    span = max(high-low, 1)
    points = " ".join(f"{70+i*800/max(len(values)-1,1):.2f},{260-(v-low)*200/span:.2f}" for i,v in enumerate(values))
    rows = "".join(f"<tr><td>{html.escape(k)}</td><td>{html.escape(str(v))}</td></tr>" for k,v in result["metrics"].items())
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quant Replay Lab · 合成回放报告</title><style>body{{max-width:960px;margin:48px auto;padding:0 24px;font:16px/1.65 system-ui;color:#183041;background:#f5f8fa}}h1{{font-size:38px}}.card{{background:white;border:1px solid #dce5eb;border-radius:16px;padding:24px;margin:20px 0}}table{{width:100%;border-collapse:collapse}}td{{padding:8px;border-bottom:1px solid #eee}}code{{overflow-wrap:anywhere}}svg{{width:100%}}</style>
<p>FINANCIAL ENGINEERING · 可复现研究</p><h1>Quant Replay Lab</h1><p>Synthetic data. Auditable execution. / 合成数据，可审计执行。</p>
<div class="card"><strong>Teaching demonstration / 教学演示</strong><p>These numbers describe an artificial price path, not market performance or alpha. 以下数值仅用于验证工程流程，不代表市场收益或策略优势。</p></div>
<div class="card"><h2>Equity / 权益（USD）</h2><svg viewBox="0 0 920 320" role="img" aria-label="Synthetic hourly equity curve"><path d="M70 50 V260 H870" fill="none" stroke="#abbac4"/><text x="0" y="65">{high:.2f}</text><text x="0" y="260">{low:.2f}</text><polyline points="{points}" fill="none" stroke="#007f78" stroke-width="3"/><text x="70" y="300">First bar / 首根</text><text x="710" y="300">Last bar / 末根</text></svg></div>
<div class="card"><h2>Accounting / 核算</h2><table>{rows}</table></div>
<div class="card"><h2>Receipt / 复现凭据</h2><code>{result['result_sha256']}</code><p>Signals use confirmed closes and execute at the next bar open. Fees and adverse slippage apply. Open positions are marked, not forcibly liquidated. Funding, market impact and liquidation are not simulated.</p><p>信号在收盘确认后生成，下一根开盘模拟成交；计入手续费及不利滑点。期末持仓按收盘估值，不强制平仓；不模拟资金费、市场冲击或强平。</p></div></html>'''
    (output / "report.html").write_text(page, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Offline synthetic teaching replay")
    parser.add_argument("--data", type=Path, default=Path("examples/synthetic_hourly.csv"))
    parser.add_argument("--output", type=Path, default=Path("outputs/demo"))
    parser.add_argument("--risk-limit", type=Decimal, default=D("1500"))
    args = parser.parse_args()
    result = run(load_candles(args.data), max_order_notional=args.risk_limit)
    write_report(result, args.output)
    print(json.dumps({"metrics": result["metrics"], "result_sha256": result["result_sha256"]}, indent=2))


if __name__ == "__main__":
    main()
