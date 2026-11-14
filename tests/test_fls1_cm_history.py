"""FLS-1-CM (docs/research_upgrade/plan_fls1_cm_history_2026_11.md): COIN-M archive converters, the trade-derived
touch / trade-flow imbalance, the history evaluator and the TRAIN-only search. Synthetic data; no network."""
import gzip
import json
import math
import random
import sys
from pathlib import Path

import numpy as np
import pytest

from research import flash_liquidation_study as fs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import fetch_cm_liquidation_tape as cm  # noqa: E402


# ============================================================================= converters
def test_liquidation_rows_become_recorder_lines_with_usd_notional_and_no_duplicates():
    row = ['1728875626061', 'BUY', 'LIMIT', 'IOC', '20', '152.664', '148.877', 'FILLED', '19', '20']
    lines = cm.liquidation_lines([row, list(row), ['1728875693048', 'BUY', 'LIMIT', 'IOC', '488', '152.734',
                                                   '148.930', 'FILLED', '59', '488']], 'SOLUSD_PERP', 250)
    assert len(lines) == 2                                               # the repeated snapshot row is dropped
    ns, text = lines[0]
    rec = json.loads(text)
    assert ns == (1728875626061 + 250) * 1_000_000 and rec['r'] == ns and rec['book'] is None
    ev = fs.parse_force_order(rec['d'])
    assert ev['symbol'] == 'SOLUSD_PERP' and ev['side'] == 'BUY'
    assert ev['notional'] == pytest.approx(20 * 10.0, rel=1e-6)          # 20 contracts x 10 USD
    assert fs.parse_force_order(json.loads(lines[1][1])['d'])['notional'] == pytest.approx(4880.0, rel=1e-6)


def test_trade_and_book_lines_match_the_recorder_layout():
    t = cm.trade_lines([['1', '147.61', '51.0', '2', '2', '1728864000740', 'false'],
                        ['2', '147.60', '3.0', '3', '3', '1728864000741', 'True'], ['bad']])
    assert [json.loads(x)['d']['m'] for _, x in t] == [False, True] and t[0][0] == 1728864000740 * 1_000_000
    b = cm.book_lines([['7', '147.60', '10', '147.62', '12', '1728864000700', '1728864000701'], ['8', '0', '1', '0', '1', '1', '1']])
    rec = json.loads(b[0][1])
    assert len(b) == 1 and rec['s'] == 'bookTicker' and rec['d']['b'] == '147.60' and rec['d']['A'] == '12'


def test_written_archive_is_read_by_the_study(tmp_path):
    rows = [[str(1728864000740 + i * 1000), str(147.6 + (i % 2) * 0.1), '1', '1', '1', str(1728864000740 + i * 1000),
             'true' if i % 2 else 'false'] for i in range(100)]
    cm.write_hourly(tmp_path, 'SOLUSD_PERP', cm.trade_lines(rows))
    cm.write_liquidations(tmp_path, cm.liquidation_lines(
        [['1728864050000', 'SELL', 'LIMIT', 'IOC', '9000', '147', '147.5', 'FILLED', '9000', '9000']], 'SOLUSD_PERP', 250))
    ev = fs.load_liquidations(tmp_path)
    assert len(ev) == 1 and ev[0]['notional'] == pytest.approx(90_000.0, rel=1e-6)
    w = fs.HourTape(tmp_path, 'SOLUSD_PERP').window(1728864000.0, 1728864200.0)
    assert len(w.pr) == 100 and w.synthetic and len(w.tr) > 0            # no bookTicker: touch from the prints


# ============================================================================= trade-derived touch, flow
def test_trade_touch_uses_the_last_print_of_each_aggressor_and_fixes_crossed_pairs():
    pr = np.array([0.0, 1.0, 2.0, 3.0])
    pp = np.array([100.0, 100.2, 100.3, 100.1])
    pm = np.array([True, False, True, False])                            # sell@bid, buy@ask, sell@bid, buy@ask
    r, b, bq, a, aq = fs.trade_touch(pr, pp, np.ones(4), pm)
    assert list(r) == [1.0, 2.0, 3.0]                                    # both sides known from the 2nd print
    # t=2: bid 100.3 > ask 100.2 (stale) -> both 100.3; t=3: ask 100.1 < bid 100.3 (stale) -> both the latest, 100.1
    assert list(b) == [100.0, 100.3, 100.1] and list(a) == [100.2, 100.3, 100.1]
    assert np.isnan(bq).all() and np.isnan(aq).all()


def test_trade_flow_imbalance():
    w = fs.Window((np.array([1.0, 2.0, 3.0, 9.0]), np.ones(4) * 100, np.array([3.0, 1.0, 1.0, 50.0]),
                   np.array([False, True, False, True])), tuple(np.zeros(0) for _ in range(5)), synthetic=True)
    assert w.tfi(4.0) == pytest.approx((3 - 1 + 1) / 5)                  # buys 4, sells 1 over (-1, 4]
    assert w.tfi(9.0, 4.0) == pytest.approx(-1.0) and w.tfi(20.0) is None


def test_g1_uses_trade_flow_on_a_trade_derived_touch():
    pr = np.array([-3.0, -2.0, 5.0, 6.0, 7.0, 40.0, 50.0])
    pp = np.array([100.0, 100.1, 100.0, 100.1, 100.1, 99.4, 101.6])
    pq = np.array([1.0, 1.0, 1.0, 5.0, 5.0, 1.0, 1.0])
    pm = np.array([True, False, True, False, False, True, False])
    prints = (pr, pp, pq, pm)
    w = fs.Window(prints, fs.trade_touch(*prints), synthetic=True)
    r = fs.simulate(w, 0.0, 1, 100.0, 1.0, 0.5, 2.0, 'G1', e1=False)
    assert r['t_place'] == 6.0 and r['outcome'] == 'TP'                  # TFI(6) = (5 - 1) / 6 ... >= 0.65 first at 6


# ============================================================================= params, search
def test_default_params_are_the_frozen_fls1_values():
    P = fs.DEFAULT
    assert (P.ttl_s, P.flip_wait_s, P.flow_win_s, P.flow_min, P.time_stop_s, P.sl_atr) == (60.0, 60.0, 5.0, 0.65, 900.0, 1.0)
    assert P.look_ahead_s == fs.LOOK_AHEAD_S


def test_mutation_changes_one_parameter_inside_its_bounds():
    rng = random.Random(3)
    cfg = {'threshold': 100e3, 'window_s': 5.0, 'k': 0.5, 'r_mult': 2.0, 'time_stop_s': 900.0, 'ttl_s': 60.0,
           'guard': 'G0', 'e1': True}
    for _ in range(300):
        new = fs.mutate(cfg, rng)
        diff = [k for k in cfg if new[k] != cfg[k]]
        assert len(diff) <= 1
        for k, (lo, hi, *_) in fs.SEARCH_SPACE.items():
            if k not in ('guard', 'e1'):
                assert lo <= new[k] <= hi


def synth_archive(tmp_path, t0, days=2, cascades_per_day=6):
    """Alternating 99.8 / 100.3 prints every 10 s (ATR 0.5) and same-side liquidation clusters."""
    lines, liq = [], []
    for d in range(days):
        base = t0 + d * 86_400
        for i in range(0, 86_400 // 10):
            t = base + i * 10
            p = 99.8 if i % 2 == 0 else 100.3
            lines.append((int(t * 1e9), json.dumps({'r': int(t * 1e9), 's': 'aggTrade',
                                                     'd': {'p': f'{p}', 'q': '1', 'm': i % 2 == 0}})))
        for c in range(cascades_per_day):
            tc = base + 3600 * (2 + 3 * c) + 5
            for j in range(3):
                liq.append(['%d' % ((tc + j) * 1000), 'SELL', 'LIMIT', 'IOC', '4000', '100', '100.1', 'FILLED', '4000', '4000'])
    cm.write_hourly(tmp_path, 'SOLUSD_PERP', lines)
    cm.write_liquidations(tmp_path, cm.liquidation_lines(liq, 'SOLUSD_PERP', 0))


def test_history_evaluator_reports_every_variant_on_both_partitions(tmp_path):
    t0 = 1_700_006_400.0                                                 # 2023-11-15 00:00 UTC
    synth_archive(tmp_path, t0)
    res = fs.evaluate_history(tmp_path, ('SOLUSD_PERP',), split_ts=t0 + 86_400, threshold=100e3, placebo=False)
    assert res['cascades'] == 12 and res['cascades_train'] == 6 and len(res['variants']) == 8
    for v in res['variants'].values():
        assert set(v) == {'train', 'holdout', 'counts'}
    assert res['verdict'] in ('INCONCLUSIVE', 'FAIL', 'PASS')            # n < 30 on TRAIN here: INCONCLUSIVE
    assert res['verdict'] == 'INCONCLUSIVE'


def test_search_scores_train_only_and_reads_holdout_once(tmp_path):
    t0 = 1_700_006_400.0
    synth_archive(tmp_path, t0)
    calls = []
    real = fs.score_cfg

    def spy(root, events, symbols, cfg, lo, hi, tapes, placebo_rng=None):
        calls.append((lo, hi, placebo_rng is not None))
        return real(root, events, symbols, cfg, lo, hi, tapes, placebo_rng)

    fs.score_cfg = spy
    try:
        start = {'threshold': 100e3, 'window_s': 5.0, 'k': 0.5, 'r_mult': 2.0, 'time_stop_s': 900.0, 'ttl_s': 60.0,
                 'guard': 'G0', 'e1': True}
        out = fs.autoresearch(tmp_path, ('SOLUSD_PERP',), t0 + 86_400, start, rounds=5, log=lambda *a: None)
    finally:
        fs.score_cfg = real
    split = t0 + 86_400
    assert [hi for _, hi, _ in calls[:-1]] == [split] * 6                 # round 0 + 5 mutations: TRAIN only
    assert calls[-1][0] == split and calls[-1][1] == math.inf and calls[-1][2]   # one HOLDOUT read, with placebos
    assert out['trials_scored'] == 6 + 8 and len(out['history']) == 6
    assert out['final'] == start                                          # TRAIN n < 30: nothing can be kept


def test_one_sweep_for_many_variants_gives_each_variant_its_own_result(tmp_path):
    t0 = 1_700_006_400.0
    synth_archive(tmp_path, t0)
    casc = fs.history_cascades(fs.load_liquidations(tmp_path), ('SOLUSD_PERP',), 100e3, 5.0)
    specs = [('a', 0.5, 1.5, 'G0', False, fs.DEFAULT), ('b', 1.0, 2.0, 'G0', True, fs.DEFAULT)]
    both = fs.run_variants(tmp_path, casc, specs, seed=7, placebo=False)
    for key, k, r, g, e1, P in specs:
        alone = fs.run_variant(tmp_path, casc, k, r, g, e1, placebo=False, P=P)
        assert both[key]['counts'] == alone['counts']
        assert [t['net_bps'] for t in both[key]['trades']] == [t['net_bps'] for t in alone['trades']]
