"""Fixed public ETH case; no private inputs, credentials or live execution."""
from __future__ import annotations
import argparse
import calendar
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import hashlib
import io
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
import zipfile

from quantlab.demo import canonical_hash
from quantlab.domain.instrument import InstrumentSpec
from quantlab.domain.ledger import TradingLedger
from quantlab.domain.order import OrderIntent, OrderSide
from quantlab.domain.sizing import SizePlan
from quantlab.execution.broker import SimulationExecutionModel
from quantlab.execution.replay_broker import ReplayBroker
from quantlab.execution.workflow import TradingWorkflow, WorkflowRiskInputs
from quantlab.market_data.candle import Candle
from quantlab.market_data.event_clock import EventClock
from quantlab.market_data.replay import ConfirmedCandleProcessor, ReplayConfig, SignalAction, SignalEvent, SignalProposal, ExecutionOpportunity
from quantlab.persistence.sqlite_store import SQLiteStore, ReconciliationSnapshot
from quantlab.risk.limits import RiskEngine, RiskLimits
from quantlab.tutorial import TeachingMeanStrategy

D = Decimal
UTC = timezone.utc
SYMBOL = 'ETHUSDT-SPOT'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_month(month: str, base: str, cache: Path) -> dict:
    name = f'ETHUSDT-1h-{month}.zip'
    url = base + name
    path = cache / name
    checksum_path = cache / (name + '.CHECKSUM')
    try:
        if not path.exists() or not checksum_path.exists():
            with urlopen(url + '.CHECKSUM', timeout=60) as response:
                checksum = response.read()
            with urlopen(url, timeout=60) as response:
                payload = response.read()
            expected = checksum.decode().split()[0]
            if sha(payload) != expected:
                raise ValueError('provider checksum mismatch: ' + month)
            path.write_bytes(payload); checksum_path.write_bytes(checksum)
        payload = path.read_bytes()
        expected = checksum_path.read_text().split()[0]
        if sha(payload) != expected:
            raise ValueError('cached archive checksum mismatch: ' + month)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            member = name.replace('.zip', '.csv')
            if archive.namelist() != [member] or archive.testzip() is not None:
                raise ValueError('unexpected or corrupt archive: ' + month)
            contents = archive.read(member)
        return {'month': month, 'url': url, 'checksum_url': url+'.CHECKSUM', 'status': 'AVAILABLE',
                'zip_sha256': sha(payload), 'csv_sha256': sha(contents), 'csv_bytes': len(contents)}
    except HTTPError as error:
        if error.code != 404:
            raise
        return {'month': month, 'url': url, 'status': 'INVALID_DECLARED_GAP', 'reason': 'provider HTTP 404'}


def fetch(protocol: dict, cache: Path, pinned: dict | None = None) -> dict:
    cache.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(lambda month: fetch_month(month, protocol['source_base'], cache), protocol['download_months']))
    manifest = {'provider': 'Binance Vision', 'dataset_terms': protocol['dataset_terms'], 'files': records}
    if pinned is not None and manifest != pinned:
        raise ValueError('source archives differ from pinned manifest; do not replace the reference')
    (cache/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    return manifest


def load_market(protocol: dict, cache: Path, manifest: dict) -> tuple[tuple[Candle, ...], list[str]]:
    bars, gaps = [], []
    records = manifest['files']
    if [r['month'] for r in records] != protocol['download_months']:
        raise ValueError('manifest does not match declared months')
    for record in records:
        month = record['month']
        if record['status'] != 'AVAILABLE':
            gaps.append(month); continue
        payload = (cache/f'ETHUSDT-1h-{month}.zip').read_bytes()
        if sha(payload) != record['zip_sha256']:
            raise ValueError('archive changed: '+month)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            contents = archive.read(f'ETHUSDT-1h-{month}.csv')
        if sha(contents) != record['csv_sha256']:
            raise ValueError('CSV changed: '+month)
        year, number = map(int, month.split('-'))
        expected_start = datetime(year, number, 1, tzinfo=UTC)
        rows = list(csv.reader(io.StringIO(contents.decode())))
        expected_count = calendar.monthrange(year, number)[1]*24
        month_bars = []
        for row in rows:
            if len(row) != 12:
                raise ValueError('malformed row: '+month)
            # Provider documents millisecond timestamps for these pre-2025 spot archives.
            opened_ms, closed_ms = int(row[0]), int(row[6])
            if opened_ms % 3600000 or not opened_ms <= closed_ms <= opened_ms + 3599999:
                raise ValueError('unexpected timestamp convention: '+month)
            if closed_ms != opened_ms + 3599999:
                # A provider-truncated hour is unavailable as a complete hourly observation.
                gaps.append(month)
                continue
            opened = datetime.fromtimestamp(opened_ms//1000, UTC)
            if opened.year != year or opened.month != number:
                raise ValueError('row outside declared archive month')
            month_bars.append(Candle(SYMBOL, 3600, opened, *map(D, row[1:6]), D(row[7]), int(row[8]), True, synthetic=False))
        if len({b.open_time for b in month_bars}) != len(month_bars):
            raise ValueError('duplicate source bars: '+month)
        if len(month_bars) != expected_count or any(b.open_time != expected_start+timedelta(hours=i) for i,b in enumerate(month_bars)):
            gaps.append(month)
        bars.extend(month_bars)
    return tuple(bars), sorted(set(gaps))


def simulate(bars: tuple[Candle, ...], segment: dict, cost: dict, protocol: dict, *, benchmark: bool = False) -> tuple[dict, list[dict], list[dict]]:
    with localcontext() as context:
        context.prec = 28
        return _simulate(bars, segment, cost, protocol, benchmark)


def _simulate(bars, segment, cost, protocol, benchmark):
    start, end = map(datetime.fromisoformat, (segment['start'], segment['end_exclusive']))
    warmup = protocol['warmup_bars']
    selected = tuple(b for b in bars if start-timedelta(hours=warmup) <= b.open_time < end)
    expected = int((end-start).total_seconds()/3600)+warmup
    if len(selected) != expected or any(b.open_time != start-timedelta(hours=warmup)+timedelta(hours=i) for i,b in enumerate(selected)):
        return {'status': 'INVALID_DECLARED_GAP', 'metrics': None}, [], []
    initial = D(protocol['initial_equity_usdt'])
    instrument = InstrumentSpec(SYMBOL, 'ETH', 'USDT', 'USDT', D(1), D(protocol['price_tick']), D(protocol['base_step']), D(protocol['base_step']), D(protocol['min_notional']))
    model = SimulationExecutionModel(D(cost['fee_rate_per_side']), D(cost['slippage_bps_per_side']))
    rl = protocol['risk_limits']
    limits = RiskLimits(D(rl['order_notional']), D(rl['gross_notional']), D(rl['daily_loss']), D(rl['drawdown']), timedelta(seconds=1),
                        max_orders_per_window=rl['orders_per_24h'], order_rate_window=timedelta(days=1))
    ledger = TradingLedger(settlement_currency='USDT', opening_cash=initial)
    processor = ConfirmedCandleProcessor(config=ReplayConfig('eth-case', 'teaching-mean-4'), strategy=TeachingMeanStrategy(4), clock=EventClock())
    signal_map = {}
    for bar in selected[:warmup]:
        outcome = processor.process(bar)
        if outcome.signal: signal_map[outcome.signal.signal_id] = outcome.signal
    fills, daily, quarter_ends = [], [], {}
    rejections = Counter()
    order_times = []
    peak = initial; max_drawdown = D(0); day_start = initial; current_day = None
    slippage_cost = D(0); turnover = D(0); exposure_hours = 0
    with SQLiteStore(':memory:') as store:
        store.set_kill_switch(engaged=False, reason='offline public historical case', updated_at=start)
        workflow = TradingWorkflow(store=store, risk_engine=RiskEngine(limits), broker=ReplayBroker(store, model))
        for index, bar in enumerate(selected[warmup:]):
            # Mark at open before consuming this bar's close in the strategy callback.
            marked = ledger.snapshot({SYMBOL: bar.open})
            if current_day != bar.open_time.date():
                current_day, day_start = bar.open_time.date(), marked.equity
            if benchmark:
                if index == 0:
                    key = selected[warmup-1].key
                    signal = SignalEvent('buy-hold', 'buy-hold-decision', key, start, SignalProposal(SignalAction.LONG, '10-percent-buy-hold'))
                    opportunities = (ExecutionOpportunity('buy-hold-open', signal.signal_id, key, bar.key, start, start, bar.open),)
                    signal_map[signal.signal_id] = signal
                else:
                    opportunities = ()
            else:
                outcome = processor.process(bar)
                if outcome.signal: signal_map[outcome.signal.signal_id] = outcome.signal
                opportunities = outcome.execution_opportunities
            for opportunity in opportunities:
                signal = signal_map[opportunity.signal_id]
                held = sum((p.signed_base_quantity for p in marked.positions), D(0))
                action = signal.proposal.action
                if not ((action is SignalAction.LONG and held == 0) or (action is SignalAction.EXIT and held > 0)):
                    continue
                reduce = action is SignalAction.EXIT
                side = OrderSide.SELL if reduce else OrderSide.BUY
                price = model.slipped_price(price=bar.open, side=side, tick=instrument.price_tick)
                if reduce:
                    quantity, projected = held, D(0)
                else:
                    size = SizePlan.from_margin(instrument=instrument, margin=marked.equity*D(protocol['entry_equity_fraction']), leverage=protocol['leverage'], price=price)
                    quantity, projected = size.contract_quantity, size.notional
                intent = OrderIntent(intent_id=signal.signal_id, instrument=instrument, side=side, contract_quantity=quantity,
                                     reference_price=price, reduce_only=reduce, created_at=bar.open_time)
                store.record_reconciliation(ReconciliationSnapshot(signal.signal_id, bar.open_time, True, True, True))
                result = workflow.execute_replay(signal=signal, opportunity=opportunity, intent=intent,
                    risk_inputs=WorkflowRiskInputs(now=bar.open_time, market_timestamp=bar.open_time, order_submission_enabled=True,
                        daily_pnl=marked.equity-day_start, equity=marked.equity, peak_equity=peak, projected_gross_notional=projected,
                        projected_instrument_notional=projected, recent_order_count=sum(bar.open_time-t < timedelta(days=1) for t in order_times),
                        reconciliation_max_age=timedelta(seconds=1)))
                if not result.risk_approved:
                    rejections.update(r.value for r in result.risk_decision.reasons)
                    continue
                order_times.append(bar.open_time)
                for fill in result.broker_execution.fills:
                    ledger.apply_fill(fill)
                    slip = abs(fill.price-bar.open)*fill.base_quantity
                    slippage_cost += slip; turnover += fill.notional
                    fills.append({'time': fill.occurred_at.isoformat(), 'decision_time': signal.decision_time.isoformat(),
                                  'side': side.value, 'base_quantity': str(fill.base_quantity), 'reference_price': str(bar.open),
                                  'fill_price': str(fill.price), 'fee': str(fill.fee), 'slippage_cost': str(slip)})
            final = ledger.snapshot({SYMBOL: bar.close})
            held = sum((p.signed_base_quantity for p in final.positions), D(0))
            exposure_hours += held > 0
            # Drawdown is defined on hourly CLOSE equity plus the initial cash point.
            peak = max(peak, final.equity)
            max_drawdown = max(max_drawdown, (peak-final.equity)/peak)
            if bar.close_time.hour == 0:
                daily.append({'time': bar.close_time.isoformat(), 'equity': str(final.equity)})
            quarter_ends[f'{bar.open_time.year}-Q{(bar.open_time.month-1)//3+1}'] = final.equity
        recovered = store.recover_ledger(settlement_currency='USDT', opening_cash=str(initial)).snapshot({SYMBOL: bar.close})
        if recovered != final:
            raise AssertionError('persisted and incremental ledgers disagree')
    exit_price = model.slipped_price(price=bar.close, side=OrderSide.SELL, tick=instrument.price_tick)
    terminal_cost = held*(bar.close-exit_price) + held*exit_price*model.fee_rate
    quarterly = {}; previous = initial
    for name, equity in quarter_ends.items():
        quarterly[name] = str(equity/previous-1); previous = equity
    metrics = {'starting_equity': str(initial), 'ending_marked_equity': str(final.equity), 'return_fraction': str(final.equity/initial-1),
               'exit_cost_adjusted_equity': str(final.equity-terminal_cost), 'exit_cost_adjusted_return_fraction': str((final.equity-terminal_cost)/initial-1),
               'estimated_terminal_exit_cost': str(terminal_cost), 'max_hourly_close_drawdown_fraction': str(max_drawdown),
               'net_pnl': str(final.net_pnl), 'realized_gross': str(final.realized_pnl_gross), 'unrealized_gross': str(final.unrealized_pnl_gross),
               'fees_paid': str(final.fees), 'slippage_paid': str(slippage_cost), 'turnover': str(turnover), 'fill_count': len(fills),
               'risk_rejection_reasons': dict(sorted(rejections.items())), 'open_base_quantity': str(held), 'hours_with_position': exposure_hours,
               'quarterly_returns': quarterly, 'bar_count': len(selected)-warmup}
    if final.net_pnl != final.realized_pnl_gross+final.unrealized_pnl_gross-final.fees:
        raise AssertionError('accounting identity failed')
    return {'status': 'VALID', 'metrics': metrics, 'fills_sha256': canonical_hash(fills), 'daily_equity_sha256': canonical_hash(daily)}, daily, fills


def execute(protocol: dict, bars: tuple[Candle, ...], manifest: dict) -> tuple[dict, list[dict], list[dict]]:
    results = {}; daily_rows = []; fill_rows = []
    for segment in protocol['segments']:
        for cost in protocol['costs']:
            for benchmark in (False, True):
                kind = 'buy_hold_10pct' if benchmark else 'teaching_mean_4'
                key = '/'.join((segment['name'], cost['name'], kind))
                result, daily, fills = simulate(bars, segment, cost, protocol, benchmark=benchmark)
                results[key] = result
                daily_rows += [dict(run=key, **row) for row in daily]
                fill_rows += [dict(run=key, **row) for row in fills]
    payload = {'case_id': protocol['case_id'], 'protocol_sha256': canonical_hash(protocol), 'source_manifest_sha256': canonical_hash(manifest),
               'results': results, 'cash_return_fraction': '0', 'oos_interpretation': 'retrospective chronological split, no parameter tuning or prospective claim'}
    return {**payload, 'result_sha256': canonical_hash(payload)}, daily_rows, fill_rows


def write_csv(path: Path, rows: list[dict]):
    if not rows:
        return
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader(); writer.writerows(rows)



def summary_markdown(report: dict) -> str:
    lines = ["# ETH historical case — results / 历史案例结果", "",
             "Source: Binance Vision. Data-derived analysis: CC BY-NC-SA 4.0; see [LICENSE.md](LICENSE.md).", "",
             "Fixed teaching rule; 2024 is a retrospective chronological evaluation, not prospective evidence. Development 2023 is invalid because of a source gap. No parameter optimization was performed.", "",
             "固定教学规则；2024 是回顾性的时间后置评估，不是前瞻证据。2023 开发区间因源缺口无效，未做参数寻优。", "",
             "Returns below are percentages of the full 10,000 USDT account, not returns on the 10% allocation. Fees/slippage are per executed side. Cost scenarios rerun the risk gates and can take different trade paths.", "",
             "下列收益以全部 10,000 USDT 账户为分母；不是 10% 投入部分的收益。费用按每次成交单边计入，各成本场景独立运行风控，路径可能不同。", "",
             "| Segment / 区间 | Cost | Strategy | Marked return | Exit-adjusted return | Max close DD | Fees (USDT) | Slippage (USDT) | Fills |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|"]
    for key, result in report['results'].items():
        segment, cost, strategy = key.split('/')
        m = result['metrics']
        if m is None:
            lines.append(f"| {segment} | {cost} | {strategy} | INVALID_DECLARED_GAP | null | null | null | null | null |")
        else:
            pct = lambda name: f"{D(m[name])*100:.4f}%"
            lines.append(f"| {segment} | {cost} | {strategy} | {pct('return_fraction')} | {pct('exit_cost_adjusted_return_fraction')} | {pct('max_hourly_close_drawdown_fraction')} | {D(m['fees_paid']):.4f} | {D(m['slippage_paid']):.4f} | {m['fill_count']} |")
    lines += ["", "## Quarterly returns / 季度收益", "", "Quarters are slices of each continuous 2024 run, not separately reset portfolios or independent validation folds. / 各季度来自同一次连续运行，不重置资金，不属于独立验证折。", "",
              "| Cost / Strategy | Q1 | Q2 | Q3 | Q4 |", "|---|---:|---:|---:|---:|"]
    for key, result in report['results'].items():
        m = result['metrics']
        if m is not None:
            values = [f"{D(v)*100:.4f}%" for v in m['quarterly_returns'].values()]
            lines.append('| '+key+' | '+' | '.join(values)+' |')
    lines += ["", "## Interpretation / 解读", "",
              "The teaching strategy loses money even in the zero-cost scenario and underperforms the 10% buy-and-hold benchmark in every modeled cost scenario. Cash has a zero return under the stated no-interest assumption. This case provides no support for an investment edge.", "",
              "教学策略在零成本下仍亏损，所有成本场景均落后于 10% 买入持有。按现金不计息假设，持有现金收益为零。本案例不支持策略具有投资优势。", "",
              "The 1,000 USDT drawdown gate restricts new entries; it does not guarantee a maximum realized loss or liquidate existing holdings. Flat late-year returns in costed strategy runs reflect stopped entries, not sustained strategy stability. Read the rejection counts in results.json.", "",
              "1,000 USDT 回撤限额限制新增入场，不保证最大实际亏损，也不主动强平已有持仓。含成本策略后期曲线走平源于入场被阻止，不代表策略稳定。拒单原因计数见 results.json。", "",
              "Fee and slippage columns include executed fills only. Exit-adjusted return additionally estimates fees and adverse slippage to sell any final holding at the final close; that hypothetical exit is not recorded as a fill. Slippage is already embedded in fill prices and is not subtracted twice.", "",
              "费用列仅统计已模拟成交；期末退出成本调整收益另估算剩余持仓在最后收盘退出的费用及滑点，假设退出不记为成交。滑点已包含在成交价格中，不重复扣减。", "",
              "No funding/borrowing is charged because this is an unlevered spot long/cash simulation. Full-fill assumptions omit order-book impact, latency and historical exchange-filter changes. No statistical significance or real execution claim is made.", "",
              "这是无杠杆现货多头/现金模拟，不计资金费或借贷。全量成交假设未建模订单簿冲击、延迟及历史交易规则变化，不宣称统计显著性或真实成交。", "",
              "Result SHA-256: `"+report['result_sha256']+"`", ""]
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', type=Path, default=Path('research/eth_case'))
    parser.add_argument('--cache', type=Path, default=Path('outputs/eth-case-cache'))
    parser.add_argument('--output', type=Path, default=Path('outputs/eth-case'))
    parser.add_argument('--fetch', action='store_true', help='download public archives and verify provider SHA-256')
    parser.add_argument('--check-reference', action='store_true')
    args = parser.parse_args()
    protocol = json.loads((args.case/'protocol.json').read_text())
    pinned_path = args.case/'source_manifest.json'
    pinned = json.loads(pinned_path.read_text()) if pinned_path.exists() else None
    if args.fetch:
        manifest = fetch(protocol, args.cache, pinned)
    else:
        manifest = json.loads((args.cache/'manifest.json').read_text())
        if pinned is not None and manifest != pinned:
            raise ValueError('local manifest differs from published pin')
    bars, gaps = load_market(protocol, args.cache, manifest)
    print(json.dumps({'loaded_rows': len(bars), 'source_gap_months': gaps}), flush=True)
    report, daily, fills = execute(protocol, bars, manifest)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'results.json').write_text(json.dumps(report, indent=2)+'\n')
    (args.output/'source_manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    write_csv(args.output/'daily_equity.csv', daily)
    write_csv(args.output/'fills.csv', fills)
    (args.output/'RESULTS.md').write_text(summary_markdown(report))
    if args.check_reference:
        if report != json.loads((args.case/'results.json').read_text()):
            raise AssertionError('historical reference mismatch')
        if (args.output/'RESULTS.md').read_bytes() != (args.case/'RESULTS.md').read_bytes():
            raise AssertionError('written report reference mismatch')
        if (args.output/'daily_equity.csv').read_bytes() != (args.case/'daily_equity.csv').read_bytes():
            raise AssertionError('daily equity reference mismatch')
    print(json.dumps({'result_sha256': report['result_sha256'], 'runs': len(report['results'])}))


if __name__ == '__main__':
    main()
