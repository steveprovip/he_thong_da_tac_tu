"""10-09 (ee): regime gate (KER/ADX + volume/CVD confirmation), 10 % capital sizing with a 1 % risk cap, soft profit
lock - core/trend_lane.py + core/trend_executor.py.  MockVenue only; temp files; no network, no real ledger."""
import json
import math
import random
from dataclasses import replace

import pytest

from core import engine as eng
from core import micro_signals as msig
from core import trend_executor as tx
from core import trend_lane as tl

H4 = 4 * 3600


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def trend_bars(n=520, drift=0.004, seed=1, vol_last=2.0, buy_share=0.6, t0=1.7e9):
    rng = random.Random(seed)
    c, px = [], 100.0
    for _ in range(n):
        px *= math.exp(drift + rng.gauss(0, 0.004))
        c.append(px)
    c = [x * 100.0 / c[-1] for x in c]                  # last close = the venue book (~100): ATR on the same scale
    v = [1000.0] * n
    v[-1] = 1000.0 * vol_last
    return {'t': [t0 + i * H4 for i in range(n)], 'o': c[:], 'h': [x * 1.003 for x in c], 'l': [x * 0.997 for x in c],
            'c': c, 'v': v, 'tb': [x * buy_share for x in v]}


def chop_bars(n=200):
    c = [100.0 * (1.01 if i % 2 else 0.99) for i in range(n)]
    return {'t': [i * H4 for i in range(n)], 'o': c[:], 'h': [x * 1.002 for x in c], 'l': [x * 0.998 for x in c],
            'c': c, 'v': [1000.0] * n, 'tb': [500.0] * n}


# ============================================================================ indicators
def test_ker_is_one_on_a_straight_line_and_near_zero_on_a_zigzag():
    assert tl.ker_series([float(i) for i in range(30)], 10)[-1] == pytest.approx(1.0)
    assert tl.ker_series(chop_bars()['c'], 10)[-1] < 0.15
    assert tl.ker_series([1.0] * 5, 10) == [None] * 5


def test_adx_high_in_trend_low_in_chop_and_warms_up():
    b = trend_bars()
    adx = tl.adx_series(b['h'], b['l'], b['c'], 14)
    assert adx[26] is None and adx[27] is not None
    assert adx[-1] > 25
    cb = chop_bars()
    assert tl.adx_series(cb['h'], cb['l'], cb['c'], 14)[-1] < 20


def test_regime_info_classifies_and_reports_unknown_volume():
    g = tl.RegimeGate()
    assert tl.regime_info(trend_bars(), g)['regime'][-1] == 'TREND'
    assert tl.regime_info(chop_bars(), g)['regime'][-1] == 'CHOP'
    b = trend_bars()
    del b['v'], b['tb']
    info = tl.regime_info(b, g)
    assert info['vol_ratio'][-1] is None and info['cvd'][-1] is None
    ok, why = tl.gate_entry(info, len(b['c']) - 1, 1.0, g)
    assert not ok and 'UNKNOWN' in why                       # missing volume never passes silently


def test_gate_entry_needs_trend_volume_and_cvd_with_the_side():
    g = tl.RegimeGate()
    b = trend_bars()
    i = len(b['c']) - 1
    assert tl.gate_entry(tl.regime_info(b, g), i, 1.0, g)[0]
    assert 'CVD' in tl.gate_entry(tl.regime_info(b, g), i, -1.0, g)[1]           # short against buy flow
    quiet = trend_bars(vol_last=1.1)
    assert 'volume' in tl.gate_entry(tl.regime_info(quiet, g), i, 1.0, g)[1]
    cb = chop_bars()
    ci = len(cb['c']) - 1
    assert 'needs TREND' in tl.gate_entry(tl.regime_info(cb, g), ci, 1.0, g)[1]
    assert not tl.gate_entry(tl.regime_info(cb, g), ci, 1.0, tl.RegimeGate(mode='no_chop', confirm=False))[0]
    assert tl.gate_entry({}, 0, 1.0, None) == (True, 'no gate')


def test_sizing_ten_pct_ceiling_low_atr_and_one_pct_risk_high_atr():
    s = tl.Sizing(0.10, 0.01, 8.0)
    assert s.fraction(0.5, 100.0) == pytest.approx(0.10)              # 8 ATR = 4 % -> risk 0.4 % -> ceiling
    assert s.fraction(2.5, 100.0) == pytest.approx(0.05)              # 8 ATR = 20 % -> 1 % / 20 %
    assert 0.05 * 8 * 2.5 / 100 == pytest.approx(0.01)                # loss at the stop = 1 % of equity
    assert s.fraction(None, 100.0) == 0.0


# ============================================================================ backtest
def test_backtest_gate_blocks_chop_entries_and_chop_exit_flattens():
    cfg = tl.TrendConfig(bar='4h')
    up = trend_bars(n=600)
    base = tl.backtest_brackets({'X': up}, cfg, tl.Brackets(sl_atr=8.0))
    assert base['trades']
    off = tl.backtest_brackets({'X': up}, cfg, tl.Brackets(sl_atr=8.0), gate=tl.RegimeGate(confirm=True))
    # only the last bar carries the volume spike: no confirmed entry before it
    assert all(t['entry_t'] == up['t'][-1] for t in off['trades'])
    cb = chop_bars(800)
    gated = tl.backtest_brackets({'X': cb}, cfg, tl.Brackets(sl_atr=8.0), gate=tl.RegimeGate(confirm=False))
    assert not gated['trades']


def test_backtest_soft_lock_arms_at_two_pct_and_exits_on_the_close():
    cfg = tl.TrendConfig(bar='4h')
    up = trend_bars(n=600, drift=0.004)
    n = len(up['c'])
    # crash the last 10 bars by 2 % each (closes only, highs/lows follow)
    for k in range(n - 10, n):
        up['c'][k] = up['c'][k - 1] * 0.98
        up['h'][k], up['l'][k], up['o'][k] = up['c'][k] * 1.003, up['c'][k] * 0.997, up['c'][k - 1]
    br = tl.Brackets(sl_atr=8.0, trail='chandelier', trail_atr=3.5, lock_pct=2.0, soft=True)
    res = tl.backtest_brackets({'X': up}, cfg, br)
    locks = [t for t in res['trades'] if t.get('why') == 'LOCK']
    assert locks and locks[-1]['pnl'] > 0
    never = tl.backtest_brackets({'X': up}, cfg, replace(br, lock_pct=500.0))
    assert not [t for t in never['trades'] if t.get('why') == 'LOCK']


def test_backtest_sizing_fixes_the_weight_at_entry():
    cfg = tl.TrendConfig(bar='4h')
    up = trend_bars(n=600)
    res = tl.backtest_brackets({'X': up}, cfg, tl.Brackets(sl_atr=8.0), sizing=tl.Sizing(0.10, 0.01, 8.0))
    assert res['trades'] and all(0 < t['w'] <= 0.10 + 1e-12 for t in res['trades'])


# ============================================================================ executor
def setup(tmp_path, **kw):
    p = replace(eng.PROFILES['demo'], symbol='SOLUSDT', qty=eng.SYMBOL_QTY.get('SOLUSDT', 0.0), trend_lane='active',
                micro_standalone=False, trend_exec='taker', trend_exec_window_s=20.0, mode='demo',
                trend_regime='trend_only', trend_confirm=True, trend_chop_exit=True, trend_lock_pct=2.0,
                capital_allocation_pct=0.10, trend_risk_pct=0.01, trend_notional_max=600.0,
                trend_be_pct=None, trend_conv_alloc_pct=None, trend_btc_align='off')   # 10-09 ff tested apart
    p = replace(p, **kw)
    feed = msig.MicroFeed('SOLUSDT', msig.config_for('SOLUSDT'))
    feed.on_message({'stream': 'solusdt@depth10@100ms', 'data': {'b': [['100.0', '50']], 'a': [['100.01', '50']]}})
    feed.on_message({'stream': 'solusdt@aggTrade', 'data': {'p': '100.005', 'q': '1', 'm': False}})
    venue = eng.MockVenue(p, quote=lambda s: eng._touch(feed))
    e = eng.Engine(p, venue, feed, None, ledger=tmp_path / 'micro.jsonl')
    e.spec, e.started, e.account = dict(eng.FALLBACK_SPECS['SOLUSDT']), True, {'wallet': 5000.0}
    events = []
    e.event = events.append
    clk = Clock(1.7e9 + 520 * H4 + 60)

    async def fake_sleep(dt):
        clk.t += dt
    x = tx.TrendExecutor({'SOLUSDT': e}, tmp_path / 'state.json', tmp_path / 'trend_trades.jsonl', clock=clk,
                         sleep=fake_sleep)
    return x, e, venue, events, clk


def data_for(b):
    return {'bars': {'SOLUSDT': b}, 'funding': {'SOLUSDT': []}}


def test_executor_size_ten_pct_of_wallet_and_risk_capped(tmp_path):
    x, e, *_ = setup(tmp_path)
    assert x.size(e, 0.01, 100.0, atr=0.5) * 100 == pytest.approx(500, abs=1)        # $5 000 x 10 %
    assert x.size(e, 0.01, 100.0, atr=2.5) * 100 == pytest.approx(250, abs=1)        # 1 % / (8 x 2.5 %)
    assert x.size(e, 0.01, 100.0, atr=None) == 0.0                                    # ATR unknown -> no size
    e.p = replace(e.p, trend_notional_max=300.0)
    assert x.size(e, 0.01, 100.0, atr=0.5) * 100 == pytest.approx(300, abs=1)        # hard USD cap still binds


async def test_executor_holds_back_unconfirmed_entry_then_opens_on_confirmation(tmp_path):
    x, e, venue, events, clk = setup(tmp_path)
    quiet = trend_bars(vol_last=1.0)
    clk.t = quiet['t'][-1] + H4 + 60
    await x.step(data_for(quiet))
    assert 'SOLUSDT' not in x.state and any('held back' in m and 'volume' in m for m in events)
    assert x.last['SOLUSDT']['regime'] == 'TREND' and x.last['SOLUSDT']['gate_ok'] is False
    loud = trend_bars(n=521)
    clk.t = loud['t'][-1] + H4 + 60
    await x.step(data_for(loud))
    st = x.state['SOLUSDT']
    assert st['side'] == 'BUY' and st['regime'] == 'TREND'
    assert st['qty'] * st['entry'] <= 600 and st['qty'] * st['entry'] > 150


async def test_executor_unknown_volume_never_enters(tmp_path):
    x, e, venue, events, clk = setup(tmp_path)
    b = trend_bars()
    del b['v'], b['tb']
    clk.t = b['t'][-1] + H4 + 60
    await x.step(data_for(b))
    assert 'SOLUSDT' not in x.state and any('UNKNOWN' in m for m in events)


def test_exit_check_chop_and_profit_lock():
    p = replace(eng.PROFILES['demo'], trend_chop_exit=True, trend_lock_pct=2.0, trend_lock_trail='chandelier',
                trend_lock_atr=3.5)
    st = {'side': 'BUY', 'entry': 100.0, 'best': 100.0}
    assert tx.TrendExecutor.exit_check(p, st, {'close': 101.0, 'atr': 0.5, 'regime': 'CHOP'}) == 'CHOP'
    st = {'side': 'BUY', 'entry': 100.0, 'best': 100.0}
    assert tx.TrendExecutor.exit_check(p, st, {'close': 101.5, 'atr': 0.1, 'regime': 'TREND'}) is None   # +1.5 %: unarmed
    assert tx.TrendExecutor.exit_check(p, st, {'close': 106.0, 'atr': 1.0, 'regime': 'TREND'}) is None   # armed, best 106
    assert st['best'] == 106.0
    assert tx.TrendExecutor.exit_check(p, st, {'close': 102.6, 'atr': 1.0, 'regime': 'MIXED'}) is None  # 106 - 3.5 = 102.5
    assert tx.TrendExecutor.exit_check(p, st, {'close': 102.4, 'atr': 1.0, 'regime': 'MIXED'}) == 'PROFIT-LOCK'
    sh = {'side': 'SELL', 'entry': 100.0, 'best': 100.0}
    tx.TrendExecutor.exit_check(p, sh, {'close': 95.0, 'atr': 1.0, 'regime': 'TREND'})
    assert tx.TrendExecutor.exit_check(p, sh, {'close': 98.6, 'atr': 1.0, 'regime': 'TREND'}) == 'PROFIT-LOCK'
    dm = replace(p, trend_lock_trail='donchian_mid', trend_chop_exit=False)
    st = {'side': 'BUY', 'entry': 100.0, 'best': 105.0}
    assert tx.TrendExecutor.exit_check(dm, st, {'close': 103.0, 'atr': 1.0, 'dmid': 103.5}) == 'PROFIT-LOCK'


async def test_executor_chop_close_flattens_and_writes_the_trade_row(tmp_path):
    x, e, venue, events, clk = setup(tmp_path, trend_lock_pct=None)
    up = trend_bars()
    clk.t = up['t'][-1] + H4 + 60
    await x.step(data_for(up))
    assert x.state['SOLUSDT']['side'] == 'BUY'
    nxt = trend_bars(n=521)
    clk.t = nxt['t'][-1] + H4 + 60
    tgt = x.targets(data_for(nxt), clk.t)['SOLUSDT']
    orig = x.targets
    x.targets = lambda data, now: {'SOLUSDT': {**tgt, 'regime': 'CHOP'}}
    await x.step(data_for(nxt))
    x.targets = orig
    rows = [json.loads(l) for l in (tmp_path / 'trend_trades.jsonl').read_text().splitlines()]
    assert rows[-1]['reason'] == 'CHOP' and rows[-1]['regime_entry'] == 'TREND'
    assert 'SOLUSDT' not in x.state and not x.state.get('_blocked_SOLUSDT')


def test_trend_fetch_and_klines_to_bars_carry_volume():
    rows = [(1.0 + i, 1.0, 2.0, 0.5, 1.5, 2.0 + i, 100.0, 60.0) for i in range(3)]
    d = eng.trend_fetch(['X'], '4h', klines=lambda s, b, n: rows, funding=lambda s: [])
    assert d['bars']['X']['v'] == [100.0] * 3 and d['bars']['X']['tb'] == [60.0] * 3
    raw = [[1000, '1', '2', '0.5', '1.5', '10', 2000, '15', 5, '6', '9', '0']]
    b = tl.klines_to_bars(raw)
    assert b['v'] == [15.0] and b['tb'] == [9.0]


@pytest.mark.production_profile
def test_demo_profile_runs_the_gate_and_ten_pct_sizing_live_untouched():
    d, l = eng.PROFILES['demo'], eng.PROFILES['live']
    assert d.trend_lane == 'active' and d.trend_regime == 'trend_only' and d.trend_confirm and d.trend_chop_exit
    assert d.capital_allocation_pct == pytest.approx(0.10) and d.trend_risk_pct == pytest.approx(0.01)
    assert d.trend_disaster_atr == 8.0 and d.trend_notional_max <= 1500          # 10-09 ff: conviction size
    assert d.trend_lock_pct == 2.0 and d.trend_lock_trail == 'chandelier' and d.trend_lock_atr == 3.5
    assert l.trend_lane == 'off' and l.capital_allocation_pct is None
