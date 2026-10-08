"""Fast research loop: core/fast_eval statistics, tools/edge_check, tools/factor_scan, tools/record_market.

Everything here is offline and uses temp files; the real ledger is never touched.
"""
import csv
import gzip
import io
import json
import math
import random
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

from core import fast_eval as fe  # noqa: E402
import edge_check  # noqa: E402
import factor_scan as fs  # noqa: E402
import record_market as rm  # noqa: E402


# ------------------------------------------------------------------ sample size
def test_required_n_matches_textbook():
    # (1.645 + 0.8416)^2 * (20/5)^2 = 98.9 -> 99
    assert fe.required_n(5, 20) == 99


def test_required_n_two_sided_is_larger():
    assert fe.required_n(5, 20, two_sided=True) > fe.required_n(5, 20)


def test_required_pairs_shrinks_with_correlation():
    assert fe.required_pairs(5, 20, 0.9) < fe.required_pairs(5, 20, 0.5) < fe.required_unpaired(5, 20)


def test_required_pairs_value_rho_09():
    # sigma_d = 20 * sqrt(0.2) = 8.94 -> (2.486 * 8.94 / 5)^2 = 19.8 -> 20
    assert fe.required_pairs(5, 20, 0.9) == 20


def test_required_n_rejects_bad_input():
    with pytest.raises(ValueError):
        fe.required_n(0, 20)
    with pytest.raises(ValueError):
        fe.required_pairs(5, 20, 1.0)


# ------------------------------------------------------------------ SPRT
def _draws(mu, sd, n, seed):
    rng = random.Random(seed)
    return [rng.gauss(mu, sd) for _ in range(n)]


def test_sprt_detects_good():
    assert fe.sprt(_draws(4, 20, 2000, 1), -3, 2, 20)['decision'] == 'H1'


def test_sprt_detects_bad():
    assert fe.sprt(_draws(-5, 20, 2000, 2), -3, 2, 20)['decision'] == 'H0'


def test_sprt_stops_early_and_reports_index():
    r = fe.sprt(_draws(-5, 20, 2000, 3), -3, 2, 20)
    assert r['at'] is not None and r['at'] < 2000


def test_sprt_undecided_on_few_trades():
    assert fe.sprt([1.0, -2.0, 3.0], -3, 2, 20)['decision'] == 'continue'


def test_sprt_requires_ordered_hypotheses():
    with pytest.raises(ValueError):
        fe.sprt([1, 2, 3], 2, -3, 20)


def test_sprt_boundaries_wald():
    r = fe.sprt([0.0] * 3, -3, 2, 20)
    assert r['upper'] == pytest.approx(math.log(19)) and r['lower'] == pytest.approx(-math.log(19))


def test_sprt_error_rate_under_h0_is_bounded():
    wrong = sum(fe.sprt(_draws(-3, 20, 3000, 100 + k), -3, 2, 20)['decision'] == 'H1' for k in range(200))
    assert wrong / 200 <= 0.08


def test_sprt_expected_n_less_than_fixed():
    assert fe.sprt_expected_n(-3, 2, 20) < fe.required_n(5, 20, 0.05, 0.95)


# ------------------------------------------------------------------ mSPRT
def test_msprt_flags_strong_negative():
    r = fe.msprt(_draws(-8, 20, 800, 4), tau=5, sigma=20)
    assert r['decision'] == 'negative' and r['p_always_valid'] < 0.05


def test_msprt_null_rarely_rejects():
    rej = sum(fe.msprt(_draws(0, 20, 400, 200 + k), tau=5, sigma=20)['p_always_valid'] < 0.05 for k in range(100))
    assert rej <= 10


def test_msprt_empty_is_continue():
    assert fe.msprt([], tau=5)['decision'] == 'continue'


# ------------------------------------------------------------------ PSR / DSR
def test_psr_half_at_benchmark():
    assert fe.psr(0.1, 500, 0, 3, sr0=0.1) == pytest.approx(0.5)


def test_psr_increases_with_n():
    assert fe.psr(0.1, 1000) > fe.psr(0.1, 100)


def test_expected_max_sr_grows_with_trials():
    assert fe.expected_max_sr(0.001, 100) > fe.expected_max_sr(0.001, 10) > 0


def test_dsr_below_psr_when_many_trials():
    assert fe.dsr(0.08, 1000, 0, 3, 0.001, 96) < fe.psr(0.08, 1000)


def test_moments_normal_kurtosis_near_three():
    m, sd, sk, ku = fe.moments(_draws(0, 1, 20000, 5))
    assert abs(sk) < 0.1 and abs(ku - 3) < 0.2


# ------------------------------------------------------------------ paired
def test_paired_diff_efficiency_and_sign():
    rng = random.Random(6)
    a = [rng.gauss(0, 20) for _ in range(300)]
    b = [x + 2 + rng.gauss(0, 3) for x in a]
    r = fe.paired_diff(a, b)
    assert r['mean'] == pytest.approx(2, abs=0.6)
    assert r['rho'] > 0.95 and r['efficiency_vs_unpaired'] > 10
    assert r['ci95'][0] > 0


def test_paired_diff_length_mismatch():
    with pytest.raises(ValueError):
        fe.paired_diff([1, 2], [1])


# ------------------------------------------------------------------ ledger + edge_check
def _ledger(tmp_path, rows):
    p = tmp_path / 'trades.jsonl'
    p.write_text('\n'.join(json.dumps(r) for r in rows) + '\n\nnot json\n')
    return p


def test_ledger_bps_uses_entry_notional():
    rows = [{'qty': 2, 'entry': 100, 'pnl': 0.1, 'symbol': 'SOLUSDT'},
            {'qty': 1, 'entry': 100, 'symbol': 'SOLUSDT'}]
    assert fe.ledger_bps(rows) == [pytest.approx(5.0)]


def test_ledger_bps_filters_symbol_and_mode():
    rows = [{'qty': 1, 'entry': 100, 'pnl': 0.01, 'symbol': 'A', 'mode': 'demo'},
            {'qty': 1, 'entry': 100, 'pnl': 0.02, 'symbol': 'B', 'mode': 'live'}]
    assert fe.ledger_bps(rows, symbol='B') == [pytest.approx(2.0)]
    assert fe.ledger_bps(rows, mode='demo') == [pytest.approx(1.0)]


def test_verdict_bad_stream():
    v = fe.verdict(_draws(-8, 15, 400, 7))
    assert v['decision'].startswith('BAD')


def test_edge_check_cli_groups_by_code(tmp_path, capsys):
    rng = random.Random(8)
    rows = [{'qty': 1, 'entry': 100, 'pnl': rng.gauss(-0.05, 0.2), 'symbol': 'SOLUSDT', 'code': c, 'mode': 'demo'}
            for c in ('aaa', 'bbb') for _ in range(30)]
    led = _ledger(tmp_path, rows)
    before = led.read_bytes()
    out = tmp_path / 'v.json'
    assert edge_check.main(['--ledger', str(led), '--by', 'code', '--json', str(out)]) == 0
    res = json.loads(out.read_text())
    assert set(res) == {'aaa', 'bbb'} and res['aaa']['n'] == 30
    assert led.read_bytes() == before            # read-only
    assert 'SPRT' in capsys.readouterr().out


def test_edge_check_paired(tmp_path, capsys):
    a = [float(x) for x in range(20)]
    b = [x + 1.0 for x in a]
    pa, pb = tmp_path / 'a.json', tmp_path / 'b.json'
    pa.write_text(json.dumps(a))
    pb.write_text(json.dumps(b))
    assert edge_check.main(['--paired', str(pa), str(pb)]) == 0
    assert 'paired B-A' in capsys.readouterr().out


def test_edge_check_handles_tiny_group(tmp_path, capsys):
    led = _ledger(tmp_path, [{'qty': 1, 'entry': 100, 'pnl': 0.01, 'symbol': 'X'}])
    assert edge_check.main(['--ledger', str(led)]) == 0
    assert 'too few' in capsys.readouterr().out


# ------------------------------------------------------------------ factor_scan
def _write_klines(d: Path, sym: str, closes, start_ms=1640995200000):
    """Hourly klines split into monthly-looking zips (one file is enough for the loader)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_volume', 'count',
                'taker_buy_volume', 'taker_buy_quote_volume', 'ignore'])
    prev = closes[0]
    for i, c in enumerate(closes):
        t = start_ms + i * fs.H
        w.writerow([t, prev, max(prev, c) * 1.001, min(prev, c) * 0.999, c, 1, t + fs.H - 1, 1000, 1, 0.5, 500, 0])
        prev = c
    with zipfile.ZipFile(d / f'{sym}-1h-2022-01.zip', 'w') as z:
        z.writestr(f'{sym}-1h-2022-01.csv', buf.getvalue())


def _write_funding(d: Path, sym: str, n_hours, rate, start_ms=1640995200000):
    buf = io.StringIO()
    buf.write('calc_time,funding_interval_hours,last_funding_rate\n')
    for h in range(0, n_hours, 8):
        buf.write(f'{start_ms + h * fs.H + 5},8,{rate}\n')
    with zipfile.ZipFile(d / f'{sym}-fundingRate-2022-01.zip', 'w') as z:
        z.writestr('f.csv', buf.getvalue())


def test_load_klines_and_resample(tmp_path):
    _write_klines(tmp_path, 'TST', [100 + i for i in range(48)])
    ts, arr = fs.load_klines(tmp_path, 'TST')
    assert len(ts) == 48 and arr[-1, 3] == 147
    bts, bars = fs.resample(ts, arr, 4)
    assert len(bts) == 12 and bars[0, 3] == 103 and bars[0, 0] == 100
    bts, bars = fs.resample(ts, arr, 24)
    assert len(bts) == 2 and bars[1, 3] == 147


def test_resample_drops_incomplete_group(tmp_path):
    _write_klines(tmp_path, 'TST', [100.0] * 30)
    ts, arr = fs.load_klines(tmp_path, 'TST')
    assert len(fs.resample(ts, arr, 24)[0]) == 1


def test_load_klines_missing_raises(tmp_path):
    with pytest.raises(SystemExit):
        fs.load_klines(tmp_path, 'NOPE')


def test_signals_have_no_lookahead():
    rng = np.random.default_rng(9)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 400)))
    bars = np.column_stack([c, c * 1.002, c * 0.998, c, np.ones(400), np.ones(400)])
    for fam, L in (('TSMOM', 20), ('DONCH', 20), ('EMAX', 40), ('ENS', 0), ('ENS_VT', 0)):
        s1 = fs.signal(fam, '1d', L, bars)
        b2 = bars.copy()
        b2[300:] *= 1.5                      # change the future only
        s2 = fs.signal(fam, '1d', L, b2)
        assert np.allclose(s1[:300], s2[:300]), fam


def test_tsmom_sign():
    c = np.array([1, 2, 3, 2, 1], dtype=float)
    assert list(fs.sig_tsmom(c, 2)) == [0, 0, 1, 0, -1]


def test_donchian_is_stateful():
    c = np.array([10, 10, 12, 11, 11, 8, 9], dtype=float)
    s = fs.sig_donch(c, c, c, 2)
    assert list(s) == [0, 0, 1, 1, 1, -1, -1]


def test_bar_pnl_costs_and_lag():
    c = np.array([100, 101, 102, 101], dtype=float)
    pos = np.array([1, 1, 0, 0], dtype=float)
    pnl, turn = fs.bar_pnl(pos, c, np.zeros(4), 6.0)
    # bar1: held 1 from close0, +1 %, entry cost 6 bps; bar2: +0.99 %; exit cost 6 bps in bar3
    assert pnl[1] == pytest.approx(0.01 - 0.0006)
    assert pnl[2] == pytest.approx(102 / 101 - 1)
    assert pnl[3] == pytest.approx(-0.0006)
    assert turn.sum() == pytest.approx(2)


def test_bar_pnl_long_pays_positive_funding():
    c = np.full(3, 100.0)
    pnl, _ = fs.bar_pnl(np.array([1.0, 1.0, 1.0]), c, np.array([0, 0.0001, 0.0001]), 0.0)
    assert pnl[1] == pytest.approx(-0.0001)
    pnl, _ = fs.bar_pnl(np.array([-1.0, -1.0, -1.0]), c, np.array([0, 0.0001, 0.0001]), 0.0)
    assert pnl[1] == pytest.approx(0.0001)


def test_funding_per_bar_and_trailing(tmp_path):
    _write_funding(tmp_path, 'TST', 48, 0.0005)
    f = fs.load_funding(tmp_path, 'TST')
    assert len(f) == 6
    bts = np.arange(0, 48, 4, dtype=np.int64) * fs.H + 1640995200000
    per = fs.funding_per_bar(bts, 4, f)
    assert per.sum() == pytest.approx(6 * 0.0005)
    tr = fs.trailing_funding(bts + 4 * fs.H, f)
    assert tr[-1] == pytest.approx(0.0005)


def test_ens_ff_blocks_longs_when_funding_hot():
    c = np.linspace(100, 200, 200)
    bars = np.column_stack([c, c, c, c, np.ones(200), np.ones(200)])
    hot = np.full(200, 0.001)
    assert (fs.signal('ENS_FF', '1d', 0, bars, hot)[100:] == 0).all()
    assert (fs.signal('ENS', '1d', 0, bars)[100:] > 0).all()


def test_run_end_to_end_on_synthetic_trend(tmp_path):
    n = 24 * 400
    c = list(100 * np.exp(np.linspace(0, 1.0, n)))
    for s in ('AAA', 'BBB'):
        _write_klines(tmp_path, s, c)
        _write_funding(tmp_path, s, n, 0.0)
    configs, results = fs.run(tmp_path, ['AAA', 'BBB'], cost_names=('TAKER',))
    daily, turn = results[(('TSMOM', '1d', 20, 'LS'), 'TAKER')]
    st = fs.stats(daily, turn, min(daily), max(daily))
    assert st['ann_ret_pct'] > 0 and st['net_bps_per_rt'] > 0
    assert fs.BASELINE in configs


def test_walk_forward_picks_and_oos(tmp_path):
    n = 24 * 900
    rng = np.random.default_rng(10)
    c = list(100 * np.exp(np.cumsum(rng.normal(0.0002, 0.004, n))))
    _write_klines(tmp_path, 'AAA', c)
    configs, results = fs.run(tmp_path, ['AAA'], cost_names=('TAKER',))
    configs = [x for x in configs if x != fs.BASELINE]
    oos, _, picks = fs.walk_forward(configs, results, start='2023-06-01', end='2024-05-01')
    assert picks and oos
    assert min(oos) >= fs.day_of('2023-06-01')


def test_splits_are_disjoint_and_ordered():
    days = [(fs.day_of(a), fs.day_of(b)) for a, b in fs.SPLITS.values()]
    for (a0, a1), (b0, b1) in zip(days, days[1:]):
        assert a1 < b0


# ------------------------------------------------------------------ recorder
def test_stream_url_has_all_streams():
    pub, mkt = rm.stream_url(['SOLUSDT'], 'public'), rm.stream_url(['SOLUSDT'], 'market')
    assert pub.startswith('wss://fstream.binance.com/public/') and 'solusdt@depth@100ms' in pub
    assert mkt.startswith('wss://fstream.binance.com/market/') and 'solusdt@aggTrade' in mkt
    assert 'solusdt@forceOrder' in mkt and 'aggTrade' not in pub


def test_recorder_never_uses_order_endpoints():
    src = (ROOT / 'tools' / 'record_market.py').read_text()
    for bad in ('/fapi/v1/order', 'signature', 'X-MBX-APIKEY', '.env', 'listenKey'):
        assert bad not in src


def test_record_line_parses_combined_frame():
    raw = json.dumps({'stream': 'dogeusdt@aggTrade', 'data': {'p': '0.1'}})
    sym, rec = rm.record_line(raw, 123)
    assert sym == 'DOGEUSDT' and rec == {'r': 123, 's': 'aggTrade', 'd': {'p': '0.1'}}
    assert rm.record_line('{bad', 1) is None
    assert rm.record_line(json.dumps({'result': None}), 1) is None


def test_hourly_writer_rotates(tmp_path):
    w = rm.HourlyWriter(tmp_path)
    t0 = 1_791_000_000 * 10 ** 9
    w.write('SOLUSDT', t0, {'a': 1})
    w.write('SOLUSDT', t0 + 3600 * 10 ** 9, {'a': 2})
    w.close()
    files = sorted((tmp_path / 'SOLUSDT').glob('*.jsonl.gz'))
    assert len(files) == 2
    assert json.loads(gzip.open(files[1], 'rt').read().strip()) == {'a': 2}
    assert w.counts['SOLUSDT'] == 2


def test_recorded_data_is_gitignored():
    gi = (ROOT / '.gitignore').read_text()
    assert 'data/recordings/' in gi
