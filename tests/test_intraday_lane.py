"""10-10 b lane 3: intraday 5m / 15m micro-swing (core/intraday_lane.py) and its kline replay
(tools/intraday_replay.py).  Synthetic bars, MockVenue, temp files; no network, no real ledger."""
import asyncio
import json
import math
import random
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

from core import engine as eng  # noqa: E402
from core import intraday_lane as il  # noqa: E402
from core import micro_signals as msig  # noqa: E402

T0 = 1_790_006_400.0                       # 00:00 UTC + 2 h of a day (outside the 4H quiet window after warm-up)


def walk(n=2400, seed=3, vol=0.0012, t0=T0 - 1200 * 60, px=100.0):
    rng = random.Random(seed)
    o, h, l, c, v, qv, tb = [], [], [], [], [], [], []
    for _ in range(n):
        op = px
        px *= math.exp(rng.gauss(0, vol))
        hi, lo = max(op, px) * (1 + abs(rng.gauss(0, vol / 3))), min(op, px) * (1 - abs(rng.gauss(0, vol / 3)))
        vv = 100 + rng.random() * 50
        o.append(op), h.append(hi), l.append(lo), c.append(px), v.append(vv), qv.append(vv * px)
        tb.append(vv * px * (0.5 + rng.gauss(0, 0.1)))
    a = lambda x: np.array(x, float)  # noqa: E731
    return {'t': t0 + 60.0 * np.arange(n), 'o': a(o), 'h': a(h), 'l': a(l), 'c': a(c), 'v': a(v), 'qv': a(qv), 'tbq': a(tb)}


def fake_engine(tmp_path, **kw):
    kw = {'intraday_lane': 'shadow', 'intraday_trend_quiet_s': 0.0, 'intraday_daily_kill_usdt': 1e9, **kw}
    p = replace(eng.PROFILES['demo'], symbol='SOLUSDT', **kw)
    ev = []
    e = SimpleNamespace(p=p, trend_owned=False, pos=None, busy=False, account={'wallet': 5000.0},
                        spec=dict(eng.FALLBACK_SPECS['SOLUSDT']), event=ev.append, venue=None)
    return e, ev


# ============================================================================ indicators
def test_resample_keeps_only_complete_clock_aligned_groups():
    m = walk(n=23, t0=1_790_000_000.0 - 1_790_000_000.0 % 300 + 60)      # starts 1 min into a 5m bucket
    b5 = il.resample(m, 300)
    assert all(t % 300 == 0 for t in b5['t']) and len(b5['t']) == 3
    assert b5['c'][0] == m['c'][b5['i_last'][0]] and b5['h'][0] == m['h'][4:9].max()


def test_vwap_roll100_and_session_warmup():
    m = walk(n=300)
    m['h'][:], m['l'][:], m['c'][:] = 100.0, 100.0, 100.0
    vw, sd = il.vwap_bands(m, 'ROLL100')
    assert np.isnan(vw[98]) and vw[99] == pytest.approx(100.0) and sd[150] == pytest.approx(0.0, abs=1e-6)
    day0 = 1_790_000_000.0 - 1_790_000_000.0 % 86400
    m2 = walk(n=200, t0=day0)
    vs, _ = il.vwap_bands(m2, 'SESSION')
    assert np.isnan(vs[58]) and np.isfinite(vs[59])                       # >= 60 min into the UTC session


def test_s1_signal_needs_band_cvd_flat_slope_and_rr():
    b5 = {'c': np.array([99.0]), 'l': np.array([98.9]), 'h': np.array([99.2])}
    f = {'b5': b5, 'atr5': np.array([0.2]), 'vwap_ROLL100': np.array([100.0]), 'sd_ROLL100': np.array([0.5]),
         'slope1': np.array([0.01]), 'cvd60': np.array([1000.0])}
    side, entry, tp, sl = il.s1_signal(f, 0, 'ROLL100')
    assert side == 1 and entry == 99.0 and tp == 100.0 and sl == pytest.approx(98.8)
    assert il.s1_signal({**f, 'cvd60': np.array([-1.0])}, 0, 'ROLL100') is None          # no seller exhaustion
    assert il.s1_signal({**f, 'slope1': np.array([0.1])}, 0, 'ROLL100') is None          # EMA not flat
    assert il.s1_signal({**f, 'atr5': np.array([0.8])}, 0, 'ROLL100') is None           # R:R 1.25 < 1.5


def test_s2_trend_and_stop():
    b5 = {'c': np.array([101.0, 101.5]), 'l': np.array([100.2, 100.9]), 'h': np.array([101.2, 101.6])}
    f = {'b5': b5, 'slope3': np.array([0.2, 0.2]), 'slope1': np.array([0.05, 0.05]), 'adx15': np.array([30.0, 30.0]),
         'ema20': np.array([100.5, 100.8]), 'atr5': np.array([0.3, 0.3])}
    assert il.s2_trend(f, 1, 3) == 1 and il.s2_trend(f, 1, 1) == 0
    assert il.s2_trend({**f, 'adx15': np.array([20.0, 20.0])}, 1, 3) == 0
    assert il.s2_stop(f, 1, 1, 100.8) == pytest.approx(100.2)
    assert il.s2_stop(f, 1, 1, 100.21) == pytest.approx(100.21 - 0.3)                 # stop within 5 bps -> 1 ATR


def test_rows_to_m_requires_volume_fields():
    assert il.rows_to_m([(0, 1, 2, 0.5, 1.5, 60)]) is None                            # spot proxy: UNKNOWN
    m = il.rows_to_m([(0, 1, 2, 0.5, 1.5, 60, 150.0, 90.0, 100.0)])
    assert m['v'][0] == 100.0 and m['qv'][0] == 150.0 and m['tbq'][0] == 90.0


# ============================================================================ shadow lane, bar by bar
def run_bars(lane, m, f, lo=0):
    for i in range(lo, len(m['t'])):
        lane.on_bar(m, f, i)


def test_shadow_s1_order_fill_and_take_profit(tmp_path, monkeypatch):
    m = walk()
    f = il.features(m)
    k = 300
    i5 = int(f['b5']['i_last'][k])
    c = m['c'][i5]
    calls = {'n': 0}

    def fake(f_, k_, vw):
        if k_ == k:
            calls['n'] += 1
            return 1, c, c * 1.004, c * 0.997
        return None
    monkeypatch.setattr(il, 's1_signal', fake)
    m['l'][i5 + 1] = c * 0.9995                                    # trades through the bid the next minute
    m['h'][i5 + 1] = c * 1.01                                      # a high in the fill minute does NOT count
    m['l'][i5 + 2], m['h'][i5 + 2] = c * 0.999, c * 1.0045         # TP next minute
    e, ev = fake_engine(tmp_path, intraday_variants=('S1_ROLL100',))
    lane = il.IntradayLane(e, tmp_path, clock=lambda: 0.0)
    run_bars(lane, m, f, lo=i5)
    rows = [json.loads(x) for x in (tmp_path / 'intraday_trades_shadow.jsonl').read_text().splitlines()]
    assert calls['n'] == 1 and rows[0]['exit_how'] == 'TP' and rows[0]['variant'] == 'S1_ROLL100'
    assert rows[0]['gross_bps'] == pytest.approx(40, abs=0.5) and rows[0]['net_bps'] == pytest.approx(36, abs=0.5)
    assert rows[0]['qty'] * rows[0]['entry'] <= 150 + 1e-6


def test_shadow_s1_unfilled_order_expires_after_one_minute(tmp_path, monkeypatch):
    m = walk()
    f = il.features(m)
    k = 300
    i5 = int(f['b5']['i_last'][k])
    c = m['c'][i5]
    monkeypatch.setattr(il, 's1_signal', lambda f_, k_, vw: (1, c * 0.99, c * 1.01, c * 0.98) if k_ == k else None)
    e, _ = fake_engine(tmp_path, intraday_variants=('S1_ROLL100',))
    lane = il.IntradayLane(e, tmp_path, clock=lambda: 0.0)
    lane.on_bar(m, f, i5)
    assert lane.order and lane.order['side'] == 1
    m['l'][i5 + 1] = c * 0.995                                     # not through 0.99 c
    lane.on_bar(m, f, i5 + 1)
    assert lane.order is None and lane.pos is None


def test_shadow_stop_in_fill_minute_and_time_barrier(tmp_path, monkeypatch):
    m = walk()
    f = il.features(m)
    k = 300
    i5 = int(f['b5']['i_last'][k])
    c = m['c'][i5]
    monkeypatch.setattr(il, 's1_signal', lambda f_, k_, vw: (1, c, c * 1.05, c * 0.99) if k_ == k else None)
    e, _ = fake_engine(tmp_path, intraday_variants=('S1_ROLL100',))
    lane = il.IntradayLane(e, tmp_path, clock=lambda: 0.0)
    m['l'][i5 + 1] = c * 0.985                                     # fills and stops in the same minute
    run_bars(lane, m, f, lo=i5)
    row = json.loads((tmp_path / 'intraday_trades_shadow.jsonl').read_text().splitlines()[0])
    assert row['exit_how'] == 'SL' and row['exit'] == pytest.approx(c * 0.99) and row['net_bps'] < -100
    # time barrier: a fill that never reaches TP / SL exits after 45 min at the close
    m2 = walk(seed=5)
    f2 = il.features(m2)
    i6 = int(f2['b5']['i_last'][k])
    c2 = m2['c'][i6]
    monkeypatch.setattr(il, 's1_signal', lambda f_, k_, vw: (1, c2, c2 * 1.5, c2 * 0.5) if k_ == k else None)
    m2['l'][i6 + 1] = c2 * 0.999
    lane2 = il.IntradayLane(fake_engine(tmp_path / 'b')[0], tmp_path / 'b', clock=lambda: 0.0)
    run_bars(lane2, m2, f2, lo=i6)
    row2 = json.loads((tmp_path / 'b' / 'intraday_trades_shadow.jsonl').read_text().splitlines()[0])
    assert row2['exit_how'] == 'TIME' and row2['hold_min'] == pytest.approx(45, abs=1)


def test_no_entry_when_symbol_busy_or_in_4h_window(tmp_path):
    e, _ = fake_engine(tmp_path, intraday_trend_quiet_s=600.0)
    lane = il.IntradayLane(e, tmp_path, clock=lambda: 0.0)
    assert lane._free(14400 * 100 + 30) and 'trend lane' in lane._free(14400 * 100 + 30)
    assert lane._free(14400 * 100 + 3600) is None
    e.trend_owned = True
    assert 'another lane' in lane._free(14400 * 100 + 3600)


# ============================================================================ the shadow lane IS the replay
def test_shadow_lane_reproduces_the_replay_trade_by_trade(tmp_path):
    import intraday_replay as ir
    m = walk(n=6000, seed=11, vol=0.0018)
    f = il.features(m)
    for name in ('S1_ROLL100', 'S2_slope3_tp60'):
        rep = ir.run_symbol(m, f, il.VARIANTS[name])
        e, _ = fake_engine(tmp_path / name, intraday_variants=(name,))
        lane = il.IntradayLane(e, tmp_path / name, clock=lambda: 0.0)
        lo = int(f['b5']['i_last'][30])
        run_bars(lane, m, f, lo=lo)
        p = tmp_path / name / 'intraday_trades_shadow.jsonl'
        rows = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
        assert len(rep) >= 5, name
        got = [(round(r['entry'], 8), round(r['exit'], 8), r['exit_how']) for r in rows]
        want = [(round(r['entry'], 8), round(r['exit_px'], 8), r['exit']) for r in rep]
        assert got == want, name


# ============================================================================ active gate
def test_active_refused_without_verdict_and_on_live(tmp_path):
    e, ev = fake_engine(tmp_path, intraday_lane='active')
    e.venue = SimpleNamespace(label='MOCK')
    lane = il.IntradayLane(e, tmp_path, clock=lambda: 0.0, verdict_path=tmp_path / 'none.json')
    assert lane.mode == 'shadow' and any('refused' in m for m in ev)
    v = tmp_path / 'v.json'
    v.write_text(json.dumps({'SOLUSDT': {'S1': {'variant': 'S1_ROLL100', 'verdict': 'ACTIVE-ELIGIBLE'},
                                         'S2': {'variant': 'S2_slope3_tp60', 'verdict': 'SHADOW'}}}))
    lane2 = il.IntradayLane(e, tmp_path, clock=lambda: 0.0, verdict_path=v)
    assert lane2.mode == 'active' and lane2.variants == ['S1_ROLL100'] and lane2.ledger.name == 'intraday_trades.jsonl'
    e.p = replace(e.p, mode='live')
    assert il.IntradayLane(e, tmp_path, clock=lambda: 0.0, verdict_path=v).mode == 'shadow'


async def test_active_entry_fill_places_exchange_stop_and_tp(tmp_path):
    p = replace(eng.PROFILES['demo'], symbol='SOLUSDT', intraday_lane='active', intraday_variants=('S1_ROLL100',))
    feed = msig.MicroFeed('SOLUSDT', msig.config_for('SOLUSDT'))
    feed.on_message({'stream': 'solusdt@depth10@100ms', 'data': {'b': [['100.0', '50']], 'a': [['100.01', '50']]}})
    venue = eng.MockVenue(p, quote=lambda s: eng._touch(feed))
    e = eng.Engine(p, venue, feed, None, ledger=tmp_path / 'micro.jsonl')
    e.spec, e.started, e.account = dict(eng.FALLBACK_SPECS['SOLUSDT']), True, {'wallet': 5000.0}
    e.event = lambda m: None
    v = tmp_path / 'v.json'
    v.write_text(json.dumps({'SOLUSDT': {'S1': {'variant': 'S1_ROLL100', 'verdict': 'ACTIVE-ELIGIBLE'}}}))
    lane = il.IntradayLane(e, tmp_path, clock=lambda: 1000.0, verdict_path=v)
    await lane._post_entry({'var': 'S1_ROLL100', 'side': 1, 'px': 100.0, 'qty': 1.5, 'tp': 100.4, 'sl': 99.75,
                            'expire_t': 1060.0})
    assert e.busy and lane.order['cid'] in venue.orders
    feed.on_message({'stream': 'solusdt@depth10@100ms', 'data': {'b': [['99.98', '50']], 'a': [['99.99', '50']]}})
    venue._match('SOLUSDT')
    await lane._venue_sync()
    assert lane.pos and lane.pos['entry'] == pytest.approx(100.0)
    assert venue.orders[lane.pos['sl_cid']]['kind'] == 'SL' and venue.orders[lane.pos['sl_cid']]['status'] == 'NEW'
    tp = venue.orders[lane.pos['tp_cid']]
    assert tp['status'] == 'NEW' and tp['side'] == 'SELL' and tp['price'] == pytest.approx(100.4)


@pytest.mark.production_profile
def test_profiles_intraday_shadow_on_demo_off_on_live():
    assert eng.PROFILES['demo'].intraday_lane == 'shadow' and eng.PROFILES['live'].intraday_lane == 'off'
    d = eng.PROFILES['demo']
    assert d.intraday_notional_usdt == 150.0 and d.intraday_daily_kill_usdt == 25.0 and d.intraday_hold_min == 45.0
