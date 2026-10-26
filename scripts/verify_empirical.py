"""Bounded synthetic checks of historical-case semantics; no downloads."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.dont_write_bytecode = True
from quantlab.empirical import simulate
from quantlab.market_data.candle import Candle


def main():
    protocol = json.loads((ROOT/'research/eth_case/protocol.json').read_text())
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    segment = {'name': 'synthetic-check', 'start': start.isoformat(), 'end_exclusive': (start+timedelta(hours=48)).isoformat()}
    bars = tuple(Candle('ETHUSDT-SPOT', 3600, start+timedelta(hours=i), D(100), D(102), D(98), D(101), D(1000), None, None, True) for i in range(-4,48))
    zero, base = protocol['costs'][:2]
    with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
        # Constant close equals mean: teaching rule remains flat; buy-hold is independently hand-checkable.
        flat, _, fills = simulate(bars, segment, base, protocol)
        assert flat['metrics']['fill_count'] == 0 and D(flat['metrics']['net_pnl']) == 0
        hold, _, fills = simulate(bars, segment, zero, protocol, benchmark=True)
        assert hold['metrics']['fill_count'] == 1
        assert D(hold['metrics']['ending_marked_equity']) == D('10010')  # 10 ETH bought at 100, marked at 101
        assert fills[0]['time'] == start.isoformat()
        costly, _, fills = simulate(bars, segment, base, protocol, benchmark=True)
        qty = D(fills[0]['base_quantity']); paid = D(fills[0]['fill_price'])
        fee = qty*paid*D('0.001')
        expected = D(10000)+qty*(D(101)-paid)-fee
        assert D(costly['metrics']['ending_marked_equity']) == expected
        assert D(costly['metrics']['fees_paid']) == fee
        assert D(costly['metrics']['exit_cost_adjusted_equity']) < expected
        gap, _, _ = simulate(bars[:10]+bars[11:], segment, zero, protocol)
        assert gap == {'status': 'INVALID_DECLARED_GAP', 'metrics': None}
        # Alternating closes generate multiple signals, but fill prices use next opens only.
        varying = tuple(replace(b, close=D(101 if i%2 else 99)) for i,b in enumerate(bars))
        report, daily, fills = simulate(varying, segment, base, protocol)
        assert fills and all(D(f['reference_price']) == 100 for f in fills)
        assert all(datetime.fromisoformat(f['time']) < start+timedelta(hours=48) for f in fills)
        short = {**segment, 'end_exclusive': (start+timedelta(hours=24)).isoformat()}
        _, short_daily, short_fills = simulate(varying[:28], short, base, protocol)
        assert short_daily == daily[:1]
        assert short_fills == [f for f in fills if datetime.fromisoformat(f['time']) < start+timedelta(hours=24)]
        assert report == simulate(varying, segment, base, protocol)[0]
    print('PASS: flat strategy, hand-calculated buy/hold PnL and fees, terminal costs, missing-bar null, next-open pricing, prefix causality, repeatability')


if __name__ == '__main__':
    main()
