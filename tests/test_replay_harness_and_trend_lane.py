"""10-09: event-driven replay of the real engine (virtual time), paired A/B bench, PBO, 1D/4H trend lane (paper),
daily-loss meter roll-over.  Offline; temp files only; the real ledger is never touched."""
import asyncio
import csv
import io
import json
import math
import random
import sys
import time
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

import ab_bench  # noqa: E402
import engine_replay as er  # noqa: E402
from core import engine as eng  # noqa: E402
from core import fast_eval as fe  # noqa: E402
from core import micro_signals as msig  # noqa: E402
from core import trend_lane as tl  # noqa: E402

T0 = 1790812800.0          # 2026-10-01 00:00 UTC


# ============================================================================ virtual time
def test_virtual_loop_sleeps_without_wall_time():
    er.install_clock(T0)
    loop = er.VirtualLoop()
    try:
        async def go():
            a = time.time()
            await asyncio.sleep(3600)
            await asyncio.sleep(0.2)
            return time.time() - a, time.monotonic()
        w0 = time.perf_counter()
        dt, mono = loop.run_until_complete(go())
        assert dt == pytest.approx(3600.2) and mono == pytest.approx(T0 + 3600.2)
        assert time.perf_counter() - w0 < 2.0
    finally:
        loop.close()
        er.restore_clock()
    assert abs(time.time() - er._REAL['time']()) < 5


def test_virtual_loop_skips_cancelled_head_timer():
    er.install_clock(T0)
    loop = er.VirtualLoop()
    try:
        async def go():
            h = loop.call_later(1.0, lambda: None)
            h.cancel()
            w0 = time.perf_counter()
            await asyncio.sleep(5.0)
            return time.perf_counter() - w0
        assert loop.run_until_complete(go()) < 1.0
    finally:
        loop.close()
        er.restore_clock()


def test_vdatetime_follows_clock_in_local_time():
    er.install_clock(T0)
    try:
        assert er.VDatetime.now().strftime('%Y-%m-%d %H') == '2026-10-01 07'
    finally:
        er.restore_clock()


# ============================================================================ books
def test_synthetic_book_from_trade_sides():
    b = er.SyntheticBook(0.01)
    b.on_trade(100.0, True)              # sell aggressor prints on the bid
    assert b.bid == 100.0 and b.ask == pytest.approx(100.01)
    b.on_trade(100.05, False)            # buy aggressor prints on the ask
    assert b.ask == 100.05 and b.bid == 100.0
    d = b.depth(3)
    assert len(d['b']) == 3 and float(d['a'][0][0]) == pytest.approx(100.05)


def test_diff_book_ticker_overrides_touch():
    b = er.DiffBook()
    b.diff({'b': [['99.9', '5'], ['99.8', '7']], 'a': [['100.1', '4'], ['100.3', '1']]})
    b.ticker({'b': '100.0', 'B': '2', 'a': '100.1', 'A': '3'})
    d = b.depth(3)
    assert d['b'][0] == ['100.00000000', '2.0'] and float(d['b'][1][0]) == 99.9
    b.diff({'b': [['99.9', '0']]})
    assert float(b.depth(3)['b'][1][0]) == 99.8


# ============================================================================ replay end to end
def _agg_zip(d: Path, symbol: str, day: str, t_start: float, minutes: int, seed: int = 1, drift: float = 0.0):
    rng = random.Random(seed)
    px, rows, t, i = 100.0, [], t_start, 0
    while t < t_start + minutes * 60:
        t += rng.expovariate(4.0)
        px *= math.exp(rng.gauss(drift, 0.0002))
        px = round(px, 2)
        rows.append([i, px, round(rng.uniform(0.5, 30), 2), i, i, int(t * 1000), rng.random() < 0.5])
        i += 1
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['agg_trade_id', 'price', 'quantity', 'first_trade_id', 'last_trade_id', 'transact_time',
                'is_buyer_maker'])
    for r in rows:
        w.writerow([r[0], r[1], r[2], r[3], r[4], r[5], 'true' if r[6] else 'false'])
    with zipfile.ZipFile(d / f'{symbol}-aggTrades-{day}.zip', 'w') as z:
        z.writestr(f'{symbol}-aggTrades-{day}.csv', buf.getvalue())
    return len(rows)


@pytest.fixture(scope='module')
def replay_run(tmp_path_factory):
    d = tmp_path_factory.mktemp('agg')
    _agg_zip(d, 'SOLUSDT', '2026-10-01', T0, 40, seed=3, drift=0.00004)
    led = eng.LEDGER
    before = led.read_bytes() if led.exists() else None
    res = er.replay('agg', d, ['SOLUSDT'], T0, T0 + 40 * 60, {}, warmup_s=600.0)
    after = led.read_bytes() if led.exists() else None
    return res, before, after, d


def test_replay_runs_real_engine_fast(replay_run):
    res, *_ = replay_run
    assert res['messages'] > 5000 and res['approx_book'] is True
    assert res['wall_s'] < 120 and res['sim_hours_per_wall_s'] > 0
    assert len(res['hourly_pnl_u']) == 1
    assert set(res['summary']) >= {'n'}


def test_replay_never_touches_real_ledger(replay_run):
    _, before, after, _ = replay_run
    assert before == after


def test_replay_restores_clock(replay_run):
    assert abs(time.time() - er._REAL['time']()) < 5 and time.monotonic is er._REAL['monotonic']
    assert eng.datetime is not er.VDatetime


def test_replay_positions_have_net_bps(replay_run):
    res, *_ = replay_run
    for p in res['positions']:
        assert p['bps'] == pytest.approx(p['pnl'] / (p['qty'] * p['entry']) * 1e4)


def test_replay_is_deterministic(replay_run):
    res, _, _, d = replay_run
    again = er.replay('agg', d, ['SOLUSDT'], T0, T0 + 40 * 60, {}, warmup_s=600.0)
    assert again['messages'] == res['messages']
    assert [round(p['pnl'], 9) for p in again['positions']] == [round(p['pnl'], 9) for p in res['positions']]


def test_replay_rejects_unknown_profile_field(tmp_path):
    _agg_zip(tmp_path, 'SOLUSDT', '2026-10-01', T0, 2)
    with pytest.raises(SystemExit):
        er.replay('agg', tmp_path, ['SOLUSDT'], T0, T0 + 60, {'no_such_field': 1}, warmup_s=0.0)
    er.restore_clock()


def test_parse_sets_json_or_string():
    assert er.parse_sets(['a=1', 'b="on"', 'c=off', 'd=[1,2]']) == {'a': 1, 'b': 'on', 'c': 'off', 'd': [1, 2]}


def test_hourly_buckets_and_summary():
    rows = [{'t': T0 + 10, 'pnl': 1.0, 'gross': 1.2, 'fee': 0.2, 'bps': 5, 'reason': 'TP'},
            {'t': T0 + 3700, 'pnl': -0.5, 'gross': -0.4, 'fee': 0.1, 'bps': -3, 'reason': 'SL'}]
    assert er.hourly(rows, T0, T0 + 7200) == [1.0, -0.5]
    s = er.summarize(rows)
    assert s['n'] == 2 and s['win_rate'] == 0.5 and s['pf'] == 2.0 and s['by_reason'] == {'SL': 1, 'TP': 1}


# ============================================================================ micro_signals fast path
def test_ker_and_sigma_newest_first_identical():
    rng = random.Random(5)
    pts, t, px = [], 1000.0, 100.0
    for _ in range(2000):
        t += rng.choice([0.0, 0.0, 0.05, 0.3, 1.0, 2.5])
        px = round(px + rng.gauss(0, 0.02), 2)
        pts.append((t, px))
    for now in (t, t + 3.7, t - 50.0, t + 130.0):
        assert msig.ker(pts, now, 120.0, 5.0, 60.0) == msig.ker(list(reversed(pts)), now, 120.0, 5.0, 60.0,
                                                               newest_first=True)
        assert msig.realized_sigma_bps(pts, now, 60.0) == msig.realized_sigma_bps(list(reversed(pts)), now, 60.0,
                                                                                   newest_first=True)


# ============================================================================ A/B bench
def test_build_chunks():
    ch = ab_bench.build_chunks('2026-10-01..2026-10-02', 12)
    assert ch == [('2026-10-01T00:00', '2026-10-01T12:00'), ('2026-10-01T12:00', '2026-10-02T00:00'),
                  ('2026-10-02T00:00', '2026-10-02T12:00'), ('2026-10-02T12:00', '2026-10-03T00:00')]
    assert ab_bench._iso_minus('2026-10-01T00:00', 60) == '2026-09-30T23:00'


def test_verdict_detects_paired_improvement():
    rng = random.Random(7)
    a = [rng.gauss(0, 1) for _ in range(400)]
    b = [x + 0.3 + rng.gauss(0, 0.2) for x in a]
    v = ab_bench.verdict({'a': a, 'b': b}, n_trials=5)
    assert v['decision'].startswith('B BETTER') and v['rho'] > 0.9 and v['ci95'][0] > 0


def test_verdict_no_difference_and_deflation():
    rng = random.Random(8)
    a = [rng.gauss(0, 1) for _ in range(200)]
    b = [x + rng.gauss(0, 1) for x in a]
    assert ab_bench.verdict({'a': a, 'b': b}, 3)['decision'] in ('NO DIFFERENCE DETECTED',
                                                                 'B better, NOT significant after deflation',
                                                                 'B worse, NOT significant after deflation')
    small = [x + 0.12 for x in a]
    assert ab_bench.verdict({'a': a, 'b': small}, 10_000)['dsr_diff_gt0'] <= \
        ab_bench.verdict({'a': a, 'b': small}, 2)['dsr_diff_gt0']


def test_verdict_too_few_units():
    assert ab_bench.verdict({'a': [1.0], 'b': [2.0]}, 1)['decision'] == 'TOO FEW UNITS'


def test_register_appends_trial_log_and_row(tmp_path):
    reg = tmp_path / 'reg.md'
    reg.write_text('| ID | x |\n|---|---|\n| G1 | a |\n')
    log = tmp_path / 'trial_log.jsonl'
    res = {'kind': 'trend', 'unit': 'day', 'a_label': '{}', 'b_label': '{"bar":"4h"}'}
    v = {'n': 10, 'mean': 0.1, 'ci95': (0.0, 0.2), 'p_two_sided': 0.04, 'dsr_diff_gt0': 0.5, 'decision': 'x'}
    ab_bench.register(res, v, 'demo', log, reg)
    assert ab_bench.logged_trials(log) == 1 and '| AB-1 | trend |' in reg.read_text()
    assert ab_bench.register_rows(reg) == 2


# ============================================================================ PBO
def test_pbo_noise_vs_skill():
    import numpy as np
    rng = np.random.default_rng(2)
    noise = fe.pbo_cscv(rng.normal(0, 1, (600, 30)), n_blocks=8)
    assert 0.2 < noise['pbo'] < 0.8
    m = rng.normal(0, 1, (600, 30))
    m[:, 3] += 0.4
    assert fe.pbo_cscv(m, n_blocks=8)['pbo'] == 0.0


def test_pbo_input_checks():
    with pytest.raises(ValueError):
        fe.pbo_cscv([[1.0]] * 40)
    with pytest.raises(ValueError):
        fe.pbo_cscv([[1.0, 2.0]] * 40, n_blocks=5)


# ============================================================================ trend lane
def _bars(closes, t0=1.6e9, hours=24):
    return {'t': [t0 + i * hours * 3600 for i in range(len(closes))], 'o': list(closes),
            'h': [c * 1.01 for c in closes], 'l': [c * 0.99 for c in closes], 'c': list(closes)}


def test_tsmom_and_donchian_components():
    c = [100 + i for i in range(70)]
    assert tl.tsmom(c, 69, (5, 20, 60)) == 1.0 and tl.tsmom(c, 10, (5, 20, 60)) is None
    g = [100 * 1.03 ** i for i in range(70)]                 # +3 % a bar clears the prior +1 % highs
    st = tl.donchian_states([x * 1.01 for x in g], [x * 0.99 for x in g], g, 20)
    assert st[19] == 0.0 and st[-1] == 1.0
    dn = list(reversed(g))
    assert tl.donchian_states([x * 1.01 for x in dn], [x * 0.99 for x in dn], dn, 20)[-1] == -1.0


def test_weights_long_in_uptrend_short_in_downtrend():
    rng = random.Random(1)
    up = [100 * math.exp(0.004 * i + rng.gauss(0, 0.01)) for i in range(120)]
    dn = [100 * math.exp(-0.004 * i + rng.gauss(0, 0.01)) for i in range(120)]
    w, comp = tl.weights_series({'A': _bars(up), 'B': _bars(dn)}, tl.TrendConfig())
    assert w['A'][-1] > 0 > w['B'][-1]
    assert abs(w['A'][-1]) <= 1.5 / 2 + 1e-9                       # max_lev / n_sym
    w_lo, _ = tl.weights_series({'B': _bars(dn)}, tl.TrendConfig(long_only=True))
    assert w_lo['B'][-1] == 0.0


def test_vol_target_scales_size():
    rng = random.Random(2)
    calm = [100 * math.exp(0.003 * i + rng.gauss(0, 0.005)) for i in range(120)]
    wild = [100 * math.exp(0.003 * i + rng.gauss(0, 0.04)) for i in range(120)]
    wc, cc = tl.weights_series({'A': _bars(calm)}, tl.TrendConfig(max_lev=10))
    ww, cw = tl.weights_series({'A': _bars(wild)}, tl.TrendConfig(max_lev=10))
    assert cc['A'][-1]['rv'] < cw['A'][-1]['rv']
    assert abs(wc['A'][-1]) / abs(cc['A'][-1]['score']) > abs(ww['A'][-1]) / max(1e-9, abs(cw['A'][-1]['score']))


def test_weights_have_no_lookahead():
    rng = random.Random(3)
    c = [100 * math.exp(sum(rng.gauss(0, 0.02) for _ in range(1)) + 0.001 * i) for i in range(150)]
    w1, _ = tl.weights_series({'A': _bars(c)}, tl.TrendConfig())
    c2 = c[:100] + [x * 1.5 for x in c[100:]]
    w2, _ = tl.weights_series({'A': _bars(c2)}, tl.TrendConfig())
    assert w1['A'][:100] == w2['A'][:100]


def test_funding_overlay_halves_crowded_longs():
    c = [100 * math.exp(0.004 * i) * (1 + 0.01 * ((-1) ** i)) for i in range(120)]
    b = _bars(c)
    hot = {'A': [(t + 3600, 0.001) for t in b['t']]}
    w0, _ = tl.weights_series({'A': b}, tl.TrendConfig())
    w1, comp = tl.weights_series({'A': b}, tl.TrendConfig(), hot)
    assert w0['A'][-1] > 0 and w1['A'][-1] == pytest.approx(w0['A'][-1] * 0.5)
    assert comp['A'][-1]['funding'] == pytest.approx(0.001)


def test_apply_band_reduces_trades():
    assert tl.apply_band([0.10, 0.12, 0.20, 0.0, -0.1], 0.05) == [0.10, 0.10, 0.20, 0.0, -0.1]


def test_backtest_cost_and_lag():
    c = [100.0] * 70 + [101.0, 102.0, 103.0]
    b = _bars(c)
    cfg = replace(tl.TrendConfig(), lookbacks=(1,), donchian_n=1, w_tsmom=1.0, vol_window=2, max_lev=1.0,
                  vol_target=1e9, band=0.0)
    res = tl.backtest({'A': b}, cfg, None, cost_bps=10.0)
    r = res['bar_returns']
    ts = b['t']
    assert r[ts[70]] == pytest.approx(-10e-4)                     # first up-close: enter, pay cost, no return yet
    assert r[ts[71]] == pytest.approx(102 / 101 - 1)              # held through the next bar
    assert res['trades'] and res['trades'][0]['side'] == 'LONG'


def test_backtest_funding_sign():
    c = [100 * math.exp(0.01 * i) for i in range(80)]
    b = _bars(c)
    f = {'A': [(t + 3600, 0.001) for t in b['t']]}
    cfg = tl.TrendConfig(band=0.0, funding_hot=1.0)
    with_f = sum(tl.backtest({'A': b}, cfg, f, cost_bps=0.0)['bar_returns'].values())
    without = sum(tl.backtest({'A': b}, cfg, None, cost_bps=0.0)['bar_returns'].values())
    assert with_f < without


def test_4h_config_scales_horizons():
    c = tl.TrendConfig(bar='4h').scaled()
    assert c.lookbacks == (30, 120, 360) and c.donchian_n == 120 and c.vol_window == 180


def test_paper_book_rebalance_and_pnl():
    pb = tl.PaperBook(equity0=1000.0)
    f = pb.rebalance('A', 0.5, 100.0, 1000.0, 10.0, 0.05, now=1.0)
    assert f['side'] == 'BUY' and pb.qty['A'] == pytest.approx(5.0) and f['fee'] == pytest.approx(0.5)
    assert pb.rebalance('A', 0.52, 100.0, 999.5, 10.0, 0.05, now=2.0) is None          # inside the band
    f = pb.rebalance('A', 0.0, 110.0, 1049.5, 10.0, 0.05, now=3.0)
    assert f['side'] == 'SELL' and f['realized'] == pytest.approx(50.0)
    assert pb.equity({'A': 110.0}) == pytest.approx(1000 + 50 - 0.5 - 0.55)


def test_paper_step_uses_closed_bars_once(tmp_path):
    rng = random.Random(4)
    c = [100 * math.exp(0.004 * i + rng.gauss(0, 0.01)) for i in range(120)]
    b = _bars(c, t0=1.6e9)
    now = b['t'][-1] + 3600                 # the last bar is still forming
    pb = tl.PaperBook(equity0=1000.0)
    r1 = tl.paper_step(pb, {'A': b}, {}, tl.TrendConfig(), now=now)
    assert r1['targets']['A']['bar_t'] == b['t'][-2] and len(r1['fills']) == 1
    r2 = tl.paper_step(pb, {'A': b}, {}, tl.TrendConfig(), now=now + 60)
    assert r2['fills'] == []
    p = tmp_path / 'b.json'
    p.write_text(pb.to_json())
    assert tl.PaperBook.load(p).qty == pb.qty


@pytest.mark.production_profile
def test_profiles_trend_lane_active_demo_off_live():
    assert eng.PROFILES['demo'].trend_lane == 'active' and eng.PROFILES['live'].trend_lane == 'off'   # 10-09 dd


async def test_runner_trend_loop_paper_only(tmp_path):
    rng = random.Random(6)
    now = time.time()

    def fake_fetch(symbols, bar):
        h = tl.BAR_HOURS[bar]
        n = 400
        t0 = (now // (h * 3600)) * h * 3600 - n * h * 3600
        out = {}
        for s in symbols:
            c = [100 * math.exp(0.002 * i + rng.gauss(0, 0.01)) for i in range(n)]
            out[s] = _bars(c, t0=t0, hours=h)
        return {'bars': out, 'funding': {s: [] for s in symbols}}

    events = []
    p = eng.PROFILES['demo']
    venue_calls = []
    venue = SimpleNamespace(__getattr__=lambda *a: venue_calls.append(a))
    engines = {s: SimpleNamespace(p=replace(p, symbol=s), event=events.append, venue=venue)
               for s in eng.SCAN_SYMBOLS}
    r = SimpleNamespace(engines=engines)
    st = await eng.Runner.trend_loop(r, fetch=fake_fetch, once=True, root=tmp_path)
    assert set(st) == {'1d', '4h'} and all(v['mode'] == 'PAPER' and not v['error'] for v in st.values())
    assert (tmp_path / 'trend_paper_1d.json').exists() and (tmp_path / 'trend_paper_1d.jsonl').exists()
    rows = [json.loads(x) for x in (tmp_path / 'trend_paper_1d.jsonl').read_text().splitlines()]
    assert rows and all(x['paper'] for x in rows)
    assert all('no order sent' in e for e in events if e.startswith('TREND'))
    assert venue_calls == []


# ============================================================================ daily loss meter
def test_riskbook_roll_resets_day_pnl():
    rb = eng.RiskBook()
    assert rb.roll('2026-10-08') is False
    rb.day_pnl = -4.9
    assert rb.roll('2026-10-08') is False and rb.day_pnl == -4.9
    assert rb.roll('2026-10-09') is True and rb.day_pnl == 0.0 and rb.day_key == '2026-10-09'


def test_gates_reset_daily_kill_after_midnight(tmp_path, monkeypatch):
    feed = msig.MicroFeed('SOLUSDT', msig.config_for('SOLUSDT'))
    p = replace(eng.PROFILES['demo'], symbol='SOLUSDT', qty=eng.SYMBOL_QTY.get('SOLUSDT', 0.0))
    venue = eng.MockVenue(p, quote=lambda s: (100.0, 100.01))
    e = eng.Engine(p, venue, feed, None, ledger=tmp_path / 't.jsonl')
    e.spec = dict(eng.FALLBACK_SPECS['SOLUSDT'])
    e.day_pnl = -6.0
    g = e.gates({'price': 100.0, 'spread_bps': 1.0})
    assert 'daily loss' in g.rune[1]

    class Tomorrow(eng.datetime):
        @classmethod
        def now(cls, tz=None):
            return eng.datetime(2099, 1, 2, 0, 5)
    monkeypatch.setattr(eng, 'datetime', Tomorrow)
    g = e.gates({'price': 100.0, 'spread_bps': 1.0})
    assert 'daily loss' not in g.rune[1] and e.day_pnl == 0.0
