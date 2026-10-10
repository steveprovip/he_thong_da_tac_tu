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
    bk = lane.books['S1_ROLL100']
    assert bk['order'] and bk['order']['side'] == 1
    m['l'][i5 + 1] = c * 0.995                                     # not through 0.99 c
    lane.on_bar(m, f, i5 + 1)
    assert bk['order'] is None and bk['pos'] is None


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
    lane2 = il.IntradayLane(fake_engine(tmp_path / 'b', intraday_variants=('S1_ROLL100',))[0], tmp_path / 'b', clock=lambda: 0.0)
    run_bars(lane2, m2, f2, lo=i6)
    row2 = json.loads((tmp_path / 'b' / 'intraday_trades_shadow.jsonl').read_text().splitlines()[0])
    assert row2['exit_how'] == 'TIME' and row2['hold_min'] == pytest.approx(45, abs=1)


def test_would_block_reasons_busy_4h_window_and_breaker(tmp_path):
    e, _ = fake_engine(tmp_path, intraday_trend_quiet_s=600.0, intraday_daily_kill_usdt=25.0)
    lane = il.IntradayLane(e, tmp_path, clock=lambda: 0.0)
    assert 'trend lane' in lane.would_block(14400 * 100 + 30)
    assert lane.would_block(14400 * 100 + 3600) is None
    lane.day_pnl = -26.0
    assert 'breaker' in lane.would_block(14400 * 100 + 3600)
    lane.day_pnl = 0.0
    e.trend_owned = True
    assert 'another lane' in lane.would_block(14400 * 100 + 3600)


# ============================================================================ the shadow lane IS the replay
def test_shadow_lane_reproduces_the_replay_trade_by_trade(tmp_path):
    """Production defaults (both DEV-best variants in ONE lane, real quiet window / kill values): every variant's
    virtual book must equal the replay's independent run of that variant."""
    import intraday_replay as ir
    m = walk(n=6000, seed=11, vol=0.0018)
    f = il.features(m)
    p = eng.PROFILES['demo']
    e = SimpleNamespace(p=replace(p, symbol='SOLUSDT'), trend_owned=False, pos=None, busy=False,
                        account={'wallet': 5000.0}, spec=dict(eng.FALLBACK_SPECS['SOLUSDT']), event=lambda x: None)
    names = ('S1_ROLL100', 'S1_SESSION', 'S2_slope3_tp60', 'S2_slope3_tp100')
    e.p = replace(e.p, intraday_variants=names)
    lane = il.IntradayLane(e, tmp_path, clock=lambda: 0.0)
    run_bars(lane, m, f, lo=int(f['b5']['i_last'][30]))
    rows = [json.loads(x) for x in (tmp_path / 'intraday_trades_shadow.jsonl').read_text().splitlines()]
    for name in names:
        rep = ir.run_symbol(m, f, il.VARIANTS[name])
        got = [(round(r['entry'], 8), round(r['exit'], 8), r['exit_how']) for r in rows if r['variant'] == name]
        want = [(round(r['entry'], 8), round(r['exit_px'], 8), r['exit']) for r in rep]
        assert len(want) >= 5 and got == want, name


# ============================================================================ shared account, active refusal
def test_would_block_is_recorded_not_applied(tmp_path, monkeypatch):
    m = walk()
    f = il.features(m)
    k = 300
    i5 = int(f['b5']['i_last'][k])
    c = m['c'][i5]
    monkeypatch.setattr(il, 's1_signal', lambda f_, k_, vw: (1, c, c * 1.004, c * 0.997) if k_ == k else None)
    m['l'][i5 + 1], m['h'][i5 + 2] = c * 0.9995, c * 1.0045
    e, _ = fake_engine(tmp_path, intraday_variants=('S1_ROLL100',))
    e.trend_owned = True                                           # a real entry would have been refused
    lane = il.IntradayLane(e, tmp_path, clock=lambda: 0.0)
    run_bars(lane, m, f, lo=i5)
    row = json.loads((tmp_path / 'intraday_trades_shadow.jsonl').read_text().splitlines()[0])
    assert row['exit_how'] == 'TP' and row['would_block'] == 'symbol held by another lane'


def test_variant_books_are_independent(tmp_path, monkeypatch):
    m = walk()
    f = il.features(m)
    k = 300
    i5 = int(f['b5']['i_last'][k])
    c = m['c'][i5]
    monkeypatch.setattr(il, 's1_signal', lambda f_, k_, vw: (1, c, c * 1.004, c * 0.997) if k_ == k else None)
    monkeypatch.setattr(il, 's2_trend', lambda f_, k_, sl: 1 if k_ == k else 0)
    e, _ = fake_engine(tmp_path, intraday_variants=('S1_ROLL100', 'S2_slope3_tp60'))
    lane = il.IntradayLane(e, tmp_path, clock=lambda: 0.0)
    lane.on_bar(m, f, i5)
    assert lane.books['S1_ROLL100']['order'] and lane.books['S2_slope3_tp60']['order']   # one slot each


def test_active_is_refused_and_runs_shadow(tmp_path):
    e, ev = fake_engine(tmp_path, intraday_lane='active')
    v = tmp_path / 'v.json'
    v.write_text(json.dumps({'SOLUSDT': {'S1': {'variant': 'S1_SESSION', 'verdict': 'ACTIVE-ELIGIBLE'}}}))
    lane = il.IntradayLane(e, tmp_path, clock=lambda: 0.0, verdict_path=v)
    assert lane.mode == 'shadow' and lane.ledger.name == 'intraday_trades_shadow.jsonl'
    assert any('active refused' in x and 'no active executor' in x for x in ev)
    lane2 = il.IntradayLane(e, tmp_path, clock=lambda: 0.0, verdict_path=tmp_path / 'missing.json')
    assert lane2.mode == 'shadow' and any('no ACTIVE-ELIGIBLE verdict' in x for x in ev)


def kl_rows(m):
    return [(float(m['t'][j]), m['o'][j], m['h'][j], m['l'][j], m['c'][j], float(m['t'][j]) + 60, m['qv'][j],
             m['tbq'][j], m['v'][j]) for j in range(len(m['t']))]


async def test_step_admits_bars_on_the_server_clock_with_margin(tmp_path):
    m = walk(n=900)
    rows = kl_rows(m)
    local = float(m['t'][-1]) + 60 + 30.0                          # local clock 30 s after the last close ...
    e, _ = fake_engine(tmp_path)
    lane = il.IntradayLane(e, tmp_path, fetch=lambda s, b, n: rows, clock=lambda: local,
                           server_offset=lambda: -28.0)            # ... but the server says only 2 s
    await lane.step()
    assert lane.last_t == m['t'][-2] and lane.last['server_offset_s'] == -28.0   # too-fresh bar not admitted


async def test_cold_start_backfills_one_page_and_short_pages_after(tmp_path):
    m = walk(n=3000)
    rows = kl_rows(m)
    calls = []

    def fetch(sym, iv, n, end_ms=None):
        calls.append((n, end_ms))
        if end_ms:
            return [r for r in rows if r[0] * 1000 <= end_ms][-n:]
        return rows[-1500:][-n:]
    clk = {'t': float(m['t'][-1]) + 60 + 10.0}
    e, _ = fake_engine(tmp_path)
    lane = il.IntradayLane(e, tmp_path, fetch=fetch, clock=lambda: clk['t'], server_offset=lambda: 0.0)
    await lane.step()
    assert calls[0] == (1500, None) and calls[1][0] == 1500 and calls[1][1] == int(rows[-1500][0] * 1000) - 1
    assert lane.last['store_bars'] == 3000                         # 2 pages: ADX(15m) / SESSION VWAP warmed up
    rows.append((rows[-1][0] + 60, *rows[-1][1:5], rows[-1][5] + 60, *rows[-1][6:]))
    clk['t'] += 60
    await lane.step()
    assert calls[-1] == (120, None) and lane.last['new_bars'] == 1


async def test_hole_ends_open_virtual_trades_unknown(tmp_path, monkeypatch):
    m = walk(n=6000)
    rows = kl_rows(m)
    feed = {'rows': rows[:3000]}
    clk = {'t': rows[2999][5] + 10.0}
    e, ev = fake_engine(tmp_path, intraday_variants=('S1_ROLL100',))
    lane = il.IntradayLane(e, tmp_path, fetch=lambda s, b, n, end_ms=None: [] if end_ms else feed['rows'][-n:],
                           clock=lambda: clk['t'], server_offset=lambda: 0.0)
    await lane.step()
    lane.books['S1_ROLL100']['pos'] = {'var': 'S1_ROLL100', 'side': 1, 'entry': 100.0, 'sl': 90.0, 'sl0': 90.0,
                                       'tp': 110.0, 'fill_t': lane.last_t, 'qty': 1.5, 'would_block': None}
    feed['rows'] = rows[:5800]                                     # 2800 bars later: more than one REST page
    clk['t'] = rows[5799][5] + 10.0
    await lane.step()
    rows_out = [json.loads(x) for x in (tmp_path / 'intraday_trades_shadow.jsonl').read_text().splitlines()]
    assert [r['exit_how'] for r in rows_out] == ['UNKNOWN-DATA-GAP']          # no replay of the missed history
    assert rows_out[0]['exit'] is None and rows_out[0]['net_bps'] is None and lane.last['new_bars'] == 1
    assert lane.gaps == 1 and lane.books['S1_ROLL100']['pos'] is None and any('data gap' in x for x in ev)


async def test_processed_bar_that_changes_is_flagged_as_clock_skew(tmp_path):
    m = walk(n=900)
    rows = kl_rows(m)
    clk = {'t': rows[-1][5] + 10.0}
    e, ev = fake_engine(tmp_path)
    lane = il.IntradayLane(e, tmp_path, fetch=lambda s, b, n: list(rows), clock=lambda: clk['t'],
                           server_offset=lambda: 0.0)
    await lane.step()
    rows[-1] = (rows[-1][0], rows[-1][1], rows[-1][2] * 1.01, *rows[-1][3:])   # the "closed" bar was still open
    rows.append((rows[-1][0] + 60, *rows[-1][1:5], rows[-1][5] + 60, *rows[-1][6:]))
    clk['t'] += 60
    await lane.step()
    assert lane.skew == 1 and any('clock skew' in x for x in ev)


def test_btc_lot_below_min_notional_is_recorded_not_dropped(tmp_path, monkeypatch):
    m = walk()
    f = il.features(m)
    k = 300
    i5 = int(f['b5']['i_last'][k])
    c = m['c'][i5]
    monkeypatch.setattr(il, 's1_signal', lambda f_, k_, vw: (1, c, c * 1.004, c * 0.997) if k_ == k else None)
    m['l'][i5 + 1], m['h'][i5 + 2] = c * 0.9995, c * 1.0045
    e, ev = fake_engine(tmp_path, intraday_variants=('S1_ROLL100',))
    e.spec = {'step': 1.0, 'tick': 0.01, 'min_notional': 200.0}    # $150 / 100 -> 1 lot = 100 U < 200 U
    lane = il.IntradayLane(e, tmp_path, clock=lambda: 0.0)
    run_bars(lane, m, f, lo=i5)
    row = json.loads((tmp_path / 'intraday_trades_shadow.jsonl').read_text().splitlines()[0])
    assert row['exit_how'] == 'TP' and row['would_block'].startswith('below min notional')
    assert row['qty'] * row['entry'] == pytest.approx(150.0)        # the replay's fixed notional


# ============================================================================ 10-10 c regime-aligned variants
def test_regime5_up_down_chop_mixed():
    f = {'ker5': np.array([0.5, 0.5, 0.1, 0.1, 0.3]), 'dir5': np.array([1, -1, 1, 1, 1]),
         'slope1': np.array([0.0, 0.0, 0.0, 0.12, 0.0]), 'adx15': np.array([30, 30, 15, 15, 15])}
    assert [il.regime5(f, k) for k in range(5)] == ['UP', 'DOWN', 'CHOP', 'UP', 'MIXED']


def test_r1_blocks_a_band_fade_outside_chop(tmp_path, monkeypatch):
    m = walk()
    f = il.features(m)
    k = 300
    i5 = int(f['b5']['i_last'][k])
    c = m['c'][i5]
    monkeypatch.setattr(il, 's1_signal', lambda f_, k_, vw: (-1, c, c * 0.996, c * 1.003) if k_ == k else None)
    for reg, want in (('UP', False), ('CHOP', True)):
        monkeypatch.setattr(il, 'regime5', lambda f_, k_, reg=reg: reg)
        e, _ = fake_engine(tmp_path / reg, intraday_variants=('R1_SESSION', 'S1_SESSION'))
        lane = il.IntradayLane(e, tmp_path / reg, clock=lambda: 0.0)
        lane.on_bar(m, f, i5)
        assert bool(lane.books['R1_SESSION']['order']) is want and lane.books['S1_SESSION']['order']


def test_p15_tp1_half_then_break_even_books_the_whole_trade(tmp_path, monkeypatch):
    m = walk(n=1500)
    f = il.features(m)
    j = 60
    i15 = int(f['b15']['i_last'][j])
    c = m['c'][i15]
    monkeypatch.setattr(il, 'p15_signal', lambda f_, j_: (1, c, c * 0.01) if j_ == j else None)
    flat = c * 1.001
    for k in range(i15 + 1, i15 + 200):                          # quiet tape around the entry unless set below
        m['o'][k] = m['h'][k] = m['l'][k] = m['c'][k] = flat
    m['l'][i15 + 1] = c * 0.9995                                  # filled through the bid
    m['h'][i15 + 3] = c * 1.004                                   # TP1 +35 bps reached (half, maker)
    m['l'][i15 + 3] = c * 1.001
    m['l'][i15 + 5] = c * 0.9999                                  # back to entry: the break-even stop takes the rest
    e, _ = fake_engine(tmp_path, intraday_variants=('P15_tp35',))
    lane = il.IntradayLane(e, tmp_path, clock=lambda: 0.0)
    run_bars(lane, m, f, lo=i15)
    row = json.loads((tmp_path / 'intraday_trades_shadow.jsonl').read_text().splitlines()[0])
    p = e.p
    want_net = 0.5 * 35.0 - p.maker_bps - 0.5 * p.maker_bps - 0.5 * (p.taker_bps + p.intraday_slip_bps)
    assert row['exit_how'] == 'TP1+SL' and row['net_bps'] == pytest.approx(want_net, abs=1e-6) and row['net_bps'] > 0
    assert row['tp1'] == pytest.approx(c * 1.0035) and row['qty'] * row['entry'] == pytest.approx(150.0)


def test_p15_trailing_chandelier_after_tp1(tmp_path, monkeypatch):
    m = walk(n=1500)
    f = il.features(m)
    j = 60
    i15 = int(f['b15']['i_last'][j])
    c = m['c'][i15]
    monkeypatch.setattr(il, 'p15_signal', lambda f_, j_: (1, c, c * 0.01) if j_ == j else None)
    e, _ = fake_engine(tmp_path, intraday_variants=('P15_tp35',))
    lane = il.IntradayLane(e, tmp_path, clock=lambda: 0.0)
    m['l'][i15 + 1] = c * 0.9995
    m['h'][i15 + 2], m['l'][i15 + 2] = c * 1.004, c * 1.001
    run_bars(lane, m, f, lo=i15)
    bk = lane.books['P15_tp35']
    rows = [json.loads(x) for x in (tmp_path / 'intraday_trades_shadow.jsonl').read_text().splitlines()]
    assert rows[0]['exit_how'].startswith('TP1+')                 # TP1 taken, then the rest left by stop / trail / time
    assert bk['pos'] is None or bk['pos'].get('tp1_done')


def test_replay_runs_the_lane_engine_for_the_new_variants():
    import intraday_replay as ir
    m = walk(n=6000, seed=11, vol=0.0018)
    f = il.features(m)
    out = ir.run_lane(m, f, ['R1_SESSION', 'P5_tp35', 'P15_tp50'])
    assert set(out) == {'R1_SESSION', 'P5_tp35', 'P15_tp50'} and sum(len(v) for v in out.values()) > 0
    assert all({'t', 't_entry', 'net_bps', 'gross_bps', 'exit', 'hold_min'} <= set(r) for v in out.values() for r in v)


@pytest.mark.production_profile
def test_profiles_intraday_shadow_on_demo_off_on_live():
    assert eng.PROFILES['demo'].intraday_lane == 'shadow' and eng.PROFILES['live'].intraday_lane == 'off'
    d = eng.PROFILES['demo']
    assert d.intraday_notional_usdt == 150.0 and d.intraday_daily_kill_usdt == 25.0 and d.intraday_hold_min == 45.0
    assert d.intraday_close_margin_s == 10.0 and d.intraday_variants == ('R1_SESSION', 'P15_tp50')
