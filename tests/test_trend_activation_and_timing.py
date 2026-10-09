"""10-09 (dd): 4H trend lane on the demo venue (core/trend_executor.py), micro lane muted, bracket backtest,
execution-timing replay helpers.  MockVenue only; temp files; no network, no real ledger."""
import json
import math
import random
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

from core import engine as eng  # noqa: E402
from core import micro_signals as msig  # noqa: E402
from core import trend_executor as tx  # noqa: E402
from core import trend_lane as tl  # noqa: E402
import trend_execution_replay as ter  # noqa: E402

H4 = 4 * 3600


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def book(feed, bid, ask, sym='SOLUSDT'):
    feed.on_message({'stream': f'{sym.lower()}@depth10@100ms', 'data': {'b': [[str(bid), '50']], 'a': [[str(ask), '50']]}})
    feed.on_message({'stream': f'{sym.lower()}@aggTrade', 'data': {'p': str((bid + ask) / 2), 'q': '1', 'm': False}})


def bars_trend(n=520, drift=0.004, t0=1.7e9, seed=1, sym_px=100.0):
    rng = random.Random(seed)
    c, px = [], sym_px
    for _ in range(n):
        px *= math.exp(drift + rng.gauss(0, 0.004))
        c.append(px)
    return {'t': [t0 + i * H4 for i in range(n)], 'o': c[:], 'h': [x * 1.003 for x in c],
            'l': [x * 0.997 for x in c], 'c': c}


def setup(tmp_path, policy='taker', mode='demo'):
    p = replace(eng.PROFILES['demo'], symbol='SOLUSDT', qty=eng.SYMBOL_QTY.get('SOLUSDT', 0.0), trend_lane='active',
                micro_standalone=False, trend_exec=policy, trend_exec_window_s=20.0, mode=mode,
                # the (dd) lane: no regime gate / lock, legacy clipped sizing (10-09 ee tested separately)
                trend_regime='off', trend_confirm=False, trend_chop_exit=False, trend_lock_pct=None,
                capital_allocation_pct=None, trend_notional_min=150.0, trend_notional_max=300.0,
                trend_be_pct=None, trend_conv_alloc_pct=None, trend_btc_align='off')
    feed = msig.MicroFeed('SOLUSDT', msig.config_for('SOLUSDT'))
    venue = eng.MockVenue(p, quote=lambda s: eng._touch(feed))
    e = eng.Engine(p, venue, feed, None, ledger=tmp_path / 'micro.jsonl')
    e.spec, e.started, e.account = dict(eng.FALLBACK_SPECS['SOLUSDT']), True, {'wallet': 5000.0}
    events = []
    e.event = events.append
    book(feed, 100.0, 100.01)
    clk = Clock(1.7e9 + 520 * H4 + 60)
    hooks = []

    async def fake_sleep(dt):
        clk.t += dt
        for h in list(hooks):
            h(clk.t)
    x = tx.TrendExecutor({'SOLUSDT': e}, tmp_path / 'state.json', tmp_path / 'trend_trades.jsonl', clock=clk,
                         sleep=fake_sleep)
    return x, e, feed, venue, events, clk, hooks


def data_for(bars):
    return {'bars': {'SOLUSDT': bars}, 'funding': {'SOLUSDT': []}}


# ============================================================================ profile / guards
@pytest.mark.production_profile
def test_demo_profile_mutes_micro_and_activates_trend_live_untouched():
    d, l = eng.PROFILES['demo'], eng.PROFILES['live']
    assert d.trend_lane == 'active' and d.micro_standalone is False
    assert l.trend_lane == 'off' and l.micro_standalone is True


def test_micro_standalone_false_makes_engine_observer(tmp_path):
    x, e, *_ = setup(tmp_path)
    assert e.observer
    e.p = replace(e.p, micro_standalone=True)
    assert not e.observer


def test_executor_refuses_live_profile_and_mainnet_venue(tmp_path):
    x, e, *_ = setup(tmp_path, mode='live')
    assert 'demo-only' in tx.TrendExecutor.allowed(e)
    x2, e2, *_ = setup(tmp_path / 'b')
    e2.venue.label = 'BINANCE MAINNET'
    assert 'mainnet' in tx.TrendExecutor.allowed(e2)


def test_size_is_vol_targeted_and_clipped(tmp_path):
    x, e, *_ = setup(tmp_path)
    assert x.size(e, 0.01, 100.0) * 100 == pytest.approx(150, abs=1)      # floor $150
    assert x.size(e, 0.5, 100.0) * 100 == pytest.approx(300, abs=1)       # cap $300
    assert 150 < x.size(e, 0.05, 100.0) * 100 < 300


# ============================================================================ life-cycle
async def test_open_with_disaster_stop_then_flip_and_trade_row(tmp_path):
    x, e, feed, venue, events, clk, hooks = setup(tmp_path)
    up = bars_trend()
    r = await x.step(data_for(up))
    st = x.state['SOLUSDT']
    assert st['side'] == 'BUY' and e.trend_owned and 150 <= st['qty'] * st['entry'] <= 301
    assert any(o['kind'] == 'SL' and o['status'] == 'NEW' for o in venue.orders.values())
    assert any('OPEN BUY' in m for m in events) and r['positions']['SOLUSDT']['side'] == 'BUY'
    atr = tl.atr_series(up['h'], up['l'], up['c'])[-2]
    assert st['stop'] == pytest.approx(st['entry'] - 8.0 * atr, rel=0.05)
    # same bar again -> nothing
    n = len(venue.fills)
    await x.step(data_for(up))
    assert len(venue.fills) == n
    # a new closed bar with a down trend -> FLIP: close the long (trade row), open a short
    dn = bars_trend(n=521, drift=-0.006, seed=2)
    clk.t = dn['t'][-1] + H4 + 60
    await x.step(data_for(dn))
    rows = [json.loads(l) for l in (tmp_path / 'trend_trades.jsonl').read_text().splitlines()]
    assert rows[-1]['reason'] == 'FLIP' and rows[-1]['lane'] == 'TREND_4H' and rows[-1]['pnl'] is not None
    assert x.state['SOLUSDT']['side'] == 'SELL'
    assert json.loads((tmp_path / 'state.json').read_text())['SOLUSDT']['side'] == 'SELL'


async def test_disaster_stop_fill_settles_and_blocks_same_side(tmp_path):
    x, e, feed, venue, events, clk, hooks = setup(tmp_path)
    up = bars_trend()
    await x.step(data_for(up))
    stop = x.state['SOLUSDT']['stop']
    book(feed, stop * 0.99, stop * 0.99 + 0.01)                 # crash through the exchange stop
    venue._match('SOLUSDT')
    assert not (await venue.position_view('SOLUSDT'))['amt']
    up2 = bars_trend(n=521)
    clk.t = up2['t'][-1] + H4 + 60
    book(feed, 100.0, 100.01)
    await x.step(data_for(up2))
    rows = [json.loads(l) for l in (tmp_path / 'trend_trades.jsonl').read_text().splitlines()]
    assert rows[-1]['reason'] == 'DISASTER-STOP'
    assert 'SOLUSDT' not in x.state and x.state.get('_blocked_SOLUSDT') == 'BUY'   # no re-entry into the stop


async def test_maker_policy_fills_at_touch_and_saves_taker_fee(tmp_path):
    x, e, feed, venue, events, clk, hooks = setup(tmp_path, policy='maker')
    moved = {'done': False}

    def dip(t):
        if not moved['done']:
            moved['done'] = True
            book(feed, 99.98, 99.99)                          # sellers trade through our 100.00 bid
    hooks.append(dip)
    await x.step(data_for(bars_trend()))
    st = x.state['SOLUSDT']
    assert st['entry'] == pytest.approx(100.0) and st['how'] == 'maker'
    assert all(f['maker'] for f in venue.fills if f['side'] == 'BUY')


async def test_maker_policy_falls_back_to_ioc_when_unfilled(tmp_path):
    x, e, feed, venue, events, clk, hooks = setup(tmp_path, policy='maker')
    await x.step(data_for(bars_trend()))
    st = x.state['SOLUSDT']
    assert st['how'] == 'maker+taker' and st['entry'] == pytest.approx(100.01, abs=0.02)


async def test_swarm_policy_takes_when_flow_leaves_with_us(tmp_path):
    x, e, feed, venue, events, clk, hooks = setup(tmp_path, policy='swarm')
    e.features = {'cvd_60s': 1_000_000.0, 'cvd_15s': 50_000.0}
    await x.step(data_for(bars_trend()))
    assert 'flow with us' in x.state['SOLUSDT']['how']


async def test_swarm_policy_posts_on_pullback(tmp_path):
    x, e, feed, venue, events, clk, hooks = setup(tmp_path, policy='swarm')
    e.features = {'cvd_60s': 0.0, 'cvd_15s': -40_000.0, 'vpin': 0.3}
    hooks.append(lambda t: book(feed, 99.98, 99.99))
    await x.step(data_for(bars_trend()))
    assert x.state['SOLUSDT']['how'] == 'swarm' and x.state['SOLUSDT']['entry'] == pytest.approx(100.0)


async def test_engine_does_not_adopt_trend_position(tmp_path):
    x, e, feed, venue, events, clk, hooks = setup(tmp_path)
    e.p = replace(e.p, adopt_external=True)
    await x.step(data_for(bars_trend()))
    await e.sync()
    assert e.pos is None and e.trend_owned


async def test_flat_signal_closes_and_venue_position_not_ours_is_left(tmp_path):
    x, e, feed, venue, events, clk, hooks = setup(tmp_path)
    await venue.ioc('SOLUSDT', 'BUY', '1.00', '101', 'manual')                # someone else's position
    await x.step(data_for(bars_trend()))
    assert 'SOLUSDT' not in x.state and any('not ours' in m for m in events)


# ============================================================================ bracket backtest
def test_backtest_brackets_signal_only_matches_weight_backtest_shape():
    b = bars_trend(n=900, drift=0.002, seed=3)
    cfg = tl.TrendConfig(bar='4h')
    r = tl.backtest_brackets({'A': b}, cfg, tl.Brackets())
    assert r['trades'] and set(r['bar_returns']) == set(b['t'])
    assert all(t['why'] in ('SIGNAL', 'OPEN') for t in r['trades'])


def test_backtest_brackets_stop_before_tp_and_block():
    n = 520
    rng = random.Random(9)
    c = [100.0 * (1.003 ** i) * (1 + rng.gauss(0, 0.002)) for i in range(n)]
    b = {'t': [i * H4 for i in range(n)], 'o': c[:], 'h': [x * 1.002 for x in c], 'l': [x * 0.998 for x in c], 'c': c[:]}
    # one crash bar whose range covers both the stop and the TP1 -> the stop is assumed first
    k = 480
    b['l'][k], b['h'][k] = c[k - 1] * 0.50, c[k - 1] * 1.05      # far below the fixed entry stop
    r = tl.backtest_brackets({'A': b}, tl.TrendConfig(bar='4h'), tl.Brackets(tp1_pct=1.0, sl_atr=1.5))
    whys = [t['why'] for t in r['trades']]
    assert 'SL' in whys
    after = [t for t in r['trades'] if t['entry_t'] > b['t'][k]]
    assert all(t['side'] != 'LONG' for t in after)          # long blocked while the signal stays long


def test_tp1_half_closes_at_maker_fee():
    n = 520
    rng = random.Random(8)
    c = [100.0 * (1.003 ** i) * (1 + rng.gauss(0, 0.002)) for i in range(n)]
    b = {'t': [i * H4 for i in range(n)], 'o': c[:], 'h': [x * 1.02 for x in c], 'l': [x * 0.999 for x in c], 'c': c}
    r = tl.backtest_brackets({'A': b}, tl.TrendConfig(bar='4h'), tl.Brackets(tp1_pct=1.0))
    assert r['trades'][0]['why'] in ('OPEN', 'SIGNAL')
    assert sum(r['turnover'].values()) == pytest.approx(r['trades'][0]['w'] * 1.5, rel=1e-6)


# ============================================================================ execution replay helpers
def _prints(side_seq, t0=1000.0, px=100.0):
    rows, t = [], t0 - 30
    for bm, p in side_seq:
        rows.append((t, p, 1.0, 1.0 if bm else 0.0))
        t += 2.0
    return np.array(rows)


def test_simulate_taker_pays_spread_and_fee():
    tr = _prints([(True, 99.99), (False, 100.00)] * 5 + [(True, 99.99)] * 5)
    r = ter.simulate(tr, 1000.0, 'BUY', 100.0, 'SOLUSDT', 60, 'TAKER')
    assert r['cost'] == pytest.approx(0.0 + ter.TAKER_BPS, abs=0.01)


def test_simulate_maker_fills_only_through_and_times_out_to_taker():
    flat = _prints([(True, 99.99), (False, 100.00)] * 30)
    r = ter.simulate(flat, 1000.0, 'BUY', 100.0, 'SOLUSDT', 60, 'MAKER', 'through')
    assert not r['filled_maker'] and r['cost'] == pytest.approx(ter.TAKER_BPS, abs=0.02)
    touch = ter.simulate(flat, 1000.0, 'BUY', 100.0, 'SOLUSDT', 60, 'MAKER', 'touch')
    assert touch['filled_maker'] and touch['cost'] == pytest.approx(-1.0 + ter.MAKER_BPS, abs=0.02)
    dip = _prints([(True, 99.99), (False, 100.00)] * 16 + [(True, 99.97)] * 3)
    r2 = ter.simulate(dip, 1000.0, 'BUY', 100.0, 'SOLUSDT', 60, 'MAKER', 'through')
    assert r2['filled_maker'] and r2['px'] == pytest.approx(99.99)


def test_simulate_swarm_takes_with_strong_flow():
    tr = _prints([(True, 99.99), (False, 100.00)] * 30)
    r = ter.simulate(tr, 1000.0, 'SELL', 100.0, 'SOLUSDT', 60, 'SWARM', 'through',
                     lambda sec: {'cvd_60s': -1e9, 'cvd_15s': -1e6, '_scale': 0.1})
    assert not r['filled_maker'] and r['why'].startswith('flow-with-us')


def test_feature_fn_uses_engine_microfeed():
    rng = random.Random(1)
    rows, t, px = [], 0.0, 100.0
    for _ in range(600):
        t += 1.0
        px += rng.choice([-0.01, 0.01])
        rows.append((t, px, 2.0, 1.0 if rng.random() < 0.5 else 0.0))
    f = ter.feature_fn(np.array(rows), 300.0, 'SOLUSDT')
    x = f(400.0)
    assert x['cvd_60s'] is not None and x['price'] is not None and not x['stale']
