"""FLS-1 evaluator (research/flash_liquidation_study.py), pre-registered in
docs/research_upgrade/plan_flash_liquidation_sniping_2026_11.md. Hand-built tapes; no outcome on real data exists yet."""
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
import record_market as rm  # noqa: E402


def win(prints, touches):
    pr = np.array([t for t, _ in prints], float)
    pp = np.array([p for _, p in prints], float)
    t = np.array(touches, float).reshape(-1, 5)
    return fs.Window((pr, pp), tuple(t[:, i] for i in range(5)))


def ev(r, side, usd, ap=0.07, sym='DOGEUSDT'):
    return {'r': r, 'symbol': sym, 'side': side, 'notional': usd, 'ap': ap}


# ============================================================================= cascades
def test_trigger_fires_once_per_cluster_on_the_5_second_same_side_sum():
    events = [ev(0, 'SELL', 40e3), ev(1, 'SELL', 40e3), ev(2, 'SELL', 30e3, ap=0.069), ev(2.5, 'BUY', 200e3),
              ev(3, 'SELL', 50e3), ev(100, 'SELL', 120e3)]
    got = fs.cascades(events)
    assert [(c['r'], c['side'], c['sign']) for c in got] == [(2, 'SELL', 1), (2.5, 'BUY', -1), (100, 'SELL', 1)]
    assert got[0]['p_liq'] == 0.069 and got[0]['cascade_usd'] == pytest.approx(110e3) and got[0]['start'] == 0


def test_window_is_half_open_and_threshold_is_inclusive():
    assert fs.cascades([ev(0, 'SELL', 60e3), ev(5.0, 'SELL', 50e3)]) == []          # (t - 5, t]: 0 has left
    assert len(fs.cascades([ev(0, 'SELL', 60e3), ev(4.9, 'SELL', 40e3)])) == 1       # exactly 100 k
    assert fs.cascades([ev(0, 'SELL', 99_999.0)]) == []


# ============================================================================= features
def test_atr_uses_only_minutes_before_the_trigger_minute():
    prints = []
    for m in range(20):
        prints += [(m * 60 + 1, 100.0), (m * 60 + 30, 101.0)]
    prints.append((20 * 60 + 5, 150.0))                                  # spike inside the trigger minute
    w = win(prints, [(0, 99, 1, 100, 1)])
    assert w.atr(20 * 60 + 30) == pytest.approx(1.0)                      # 14 TRs of exactly 1.0
    assert win(prints[:28], [(0, 99, 1, 100, 1)]).atr(14 * 60 + 1) is None   # 14 bars: not enough


def test_level_one_ofi_matches_the_hand_computation():
    touches = [(0, 100, 10, 101, 10), (1, 100, 15, 101, 10), (2, 100.5, 5, 101, 8), (3, 100.5, 5, 100.9, 20)]
    w = win([(0, 100.5)], touches)
    assert w.ofi(2.0) == pytest.approx(1.0)                               # e = +5, +7
    assert w.ofi(3.0) == pytest.approx(-8 / 32)                           # e = +5, +7, -20
    assert win([(0, 1.0)], touches[:1]).ofi(3.0) is None


# ============================================================================= one attempt
BASE_T = [(0, 99.9, 1, 100.0, 1), (10.5, 99.6, 1, 99.7, 1)]          # mid 99.65 >= L after the fill


def test_post_only_refuses_a_limit_that_would_cross():
    w = win([(10, 99.0)], [(0, 99.9, 1, 100.0, 1)])
    assert fs.simulate(w, 0, 1, 100.6, 1.0, 0.5, 2.0, 'G0')['outcome'] == 'REJECT_CROSS'   # L 100.1 >= ask 100.0


def test_a_touch_is_not_a_fill_and_the_order_expires():
    at_limit = win([(t, 99.5) for t in range(1, 60)], BASE_T)
    assert fs.simulate(at_limit, 0, 1, 100.0, 1.0, 0.5, 2.0, 'G0')['outcome'] == 'MISSED'
    late = win([(61, 99.0)], BASE_T)
    assert fs.simulate(late, 0, 1, 100.0, 1.0, 0.5, 2.0, 'G0')['outcome'] == 'MISSED'


def test_take_profit_is_a_maker_exit_at_the_target():
    w = win([(10, 99.4), (12, 99.8), (20, 101.6)], BASE_T)
    r = fs.simulate(w, 0, 1, 100.0, 1.0, 0.5, 2.0, 'G0')
    assert r['outcome'] == 'TP' and r['entry'] == 99.5 and r['exit'] == 101.5 and r['t_fill'] == 10
    assert r['net_bps'] == pytest.approx((101.5 / 99.5 - 1) * 1e4 - 4.0)
    assert r['risk_bps'] == pytest.approx(1.0 / 99.5 * 1e4)


def test_stop_exits_at_the_gapped_print_with_taker_fee_and_slip():
    w = win([(10, 99.4), (20, 98.0)], BASE_T)
    r = fs.simulate(w, 0, 1, 100.0, 1.0, 0.5, 2.0, 'G0')
    assert r['outcome'] == 'SL' and r['exit'] == pytest.approx(98.0 * (1 - 1e-4))
    assert r['net_bps'] == pytest.approx((98.0 * (1 - 1e-4) / 99.5 - 1) * 1e4 - 7.0)
    through_both = win([(10, 98.0)], BASE_T)                              # the fill print is already past the stop
    assert fs.simulate(through_both, 0, 1, 100.0, 1.0, 0.5, 2.0, 'G0')['outcome'] == 'SL'


def test_markout_rule_exits_at_the_touch_and_the_trade_is_kept():
    touches = [(0, 99.9, 1, 100.0, 1), (10.5, 99.3, 1, 99.4, 1)]         # mid 99.35 < L 99.5 at fill + 1 s
    w = win([(10, 99.4), (20, 101.6)], touches)
    r = fs.simulate(w, 0, 1, 100.0, 1.0, 0.5, 2.0, 'G0')
    assert r['outcome'] == 'E1' and r['t_exit'] == 11 and r['exit'] == pytest.approx(99.3 * (1 - 1e-4))
    assert r['net_bps'] == pytest.approx((99.3 * (1 - 1e-4) / 99.5 - 1) * 1e4 - 7.0)
    assert fs.simulate(w, 0, 1, 100.0, 1.0, 0.5, 2.0, 'G0', e1=False)['outcome'] == 'TP'   # E0, info only


def test_time_stop_exits_at_the_touch_as_a_taker():
    w = win([(10, 99.4), (100, 99.9)], BASE_T + [(900, 99.8, 1, 99.9, 1)])
    r = fs.simulate(w, 0, 1, 100.0, 1.0, 0.5, 2.0, 'G0')
    assert r['outcome'] == 'TIME' and r['t_exit'] == 910 and r['exit'] == pytest.approx(99.8 * (1 - 1e-4))


def test_short_side_is_the_mirror():
    w = win([(10, 100.6), (20, 98.4)], [(0, 100.0, 1, 100.1, 1), (10.5, 100.3, 1, 100.4, 1)])
    r = fs.simulate(w, 0, -1, 100.0, 1.0, 0.5, 2.0, 'G0')
    assert r['outcome'] == 'TP' and r['entry'] == 100.5 and r['exit'] == 98.5
    assert r['net_bps'] == pytest.approx(-(98.5 / 100.5 - 1) * 1e4 - 4.0)


def test_g1_waits_for_the_owner_ofi_floor_or_gives_up():
    flip = [(-2, 100, 10, 100.1, 10), (-1, 100, 5, 100.1, 10), (15, 100, 5, 100.1, 10), (16, 100, 30, 100.1, 10),
            (17, 100, 30, 100.1, 4), (30, 99.6, 1, 99.7, 1)]
    w = win([(40, 99.4), (50, 101.6)], flip)
    r = fs.simulate(w, 0, 1, 100.0, 1.0, 0.5, 2.0, 'G1')
    assert r['t_place'] == 16 and r['outcome'] == 'TP'                    # OFI(16) = +1.0 >= 0.65; OFI(0) = -1
    never = win([(40, 99.4)], flip[:2])
    assert fs.simulate(never, 0, 1, 100.0, 1.0, 0.5, 2.0, 'G1')['outcome'] == 'NO_FLIP'


# ============================================================================= files, driver, holdout lock
def write_tape(tmp_path, t0, minutes=60, liq=()):
    sink = rm.ThreadedSink(rm.HourlyWriter(tmp_path), rm.DailyWriter(tmp_path))
    router = rm.Router(sink)
    frame = lambda s, d: json.dumps({'stream': s, 'data': d}, separators=(',', ':'))
    for i in range(minutes * 6):
        t = t0 + i * 10
        p = 99.8 if i % 2 == 0 else 100.3
        router(frame('dogeusdt@bookTicker', {'e': 'bookTicker', 's': 'DOGEUSDT', 'b': f'{p - 0.05:.2f}', 'B': '10',
                                             'a': f'{p + 0.05:.2f}', 'A': '10', 'u': i, 'T': 0}), int(t * 1e9))
        router(frame('dogeusdt@aggTrade', {'e': 'aggTrade', 's': 'DOGEUSDT', 'p': f'{p:.2f}', 'q': '1', 'T': 0}),
               int((t + 1) * 1e9))
    for t, side, z, ap in liq:
        router(frame('!forceOrder@arr', {'e': 'forceOrder', 'E': 0, 'o': {'s': 'DOGEUSDT', 'S': side, 'z': str(z),
                                                                          'ap': str(ap), 'q': str(z), 'p': str(ap)}}),
               int(t * 1e9))
    sink.close()


def test_loader_reads_what_the_recorder_writes(tmp_path):
    t0 = 1_794_297_600.0
    write_tape(tmp_path, t0, minutes=30, liq=[(t0 + 1500, 'SELL', 1_000_000, 0.1)])
    with open(next(tmp_path.glob('liquidations_*.jsonl')), 'a', encoding='utf-8') as f:
        f.write('{"r":1,"s":"forceOrder","d":{"e":"forc')                 # the line a killed recorder was writing
    rows = fs.load_liquidations(tmp_path)
    assert rows == [{'r': t0 + 1500, 'symbol': 'DOGEUSDT', 'side': 'SELL', 'notional': pytest.approx(100_000.0),
                     'ap': 0.1}]
    w = fs.HourTape(tmp_path, 'DOGEUSDT').window(t0, t0 + 1800)
    assert len(w.pr) == 180 and len(w.tr) == 180 and w.atr(t0 + 1500) == pytest.approx(0.5)


def test_one_position_per_symbol_and_placebos_avoid_cascades(tmp_path):
    t0 = 1_794_297_600.0
    write_tape(tmp_path, t0, minutes=60)
    # at t0 + 1810 the touch is 100.25 / 100.35 (at t0 + 1800 it was 99.75 / 99.85: L 99.85 would cross the ask)
    casc = [{'r': t0 + 1810, 'symbol': 'DOGEUSDT', 'side': 'SELL', 'sign': 1, 'p_liq': 100.1, 'cascade_usd': 2e5,
             'start': t0 + 1805},
            {'r': t0 + 1830, 'symbol': 'DOGEUSDT', 'side': 'SELL', 'sign': 1, 'p_liq': 100.1, 'cascade_usd': 2e5,
             'start': t0 + 1825}]
    out = fs.run_variant(tmp_path, casc, 0.5, 1.5, 'G0', e1=False, rng=random.Random(1))
    assert out['counts'] == {'TIME': 1, 'BUSY': 1} and len(out['trades']) == 1
    tr = out['trades'][0]
    assert tr['why'] == 'TIME' and tr['net_bps'] == pytest.approx(tr['gross_bps'] - 7.0)
    assert len(tr['placebo_net_bps']) <= fs.N_PLACEBO


def test_no_outcome_before_21_full_days(tmp_path):
    t0 = 1_794_297_600.0
    write_tape(tmp_path, t0, minutes=30, liq=[(t0 + 1500, 'SELL', 2_000_000, 0.1)])
    res = fs.evaluate(tmp_path, symbols=('DOGEUSDT',))
    assert res['verdict'] == 'COLLECTING' and set(res) == {'verdict', 'status'}
    assert res['status']['days_recorded'] == 0 and res['status']['cascades_by_symbol'] == {'DOGEUSDT': 1}


def test_acceptance_needs_every_pre_registered_check():
    good = {'stats': {'n': 150, 'mean_net_bps': 3.0, 'lcb95': 1.0}, 'vs_placebo': {'lcb95': 0.5},
            'symbols_eligible': 4, 'symbols_positive': 2}
    train = {'a': {'stats': {'n': 40, 'mean_net_bps': 2.0}}, 'b': {'stats': {'n': 20, 'mean_net_bps': 9.0}}}
    assert fs.acceptance(train, lambda k: good) == {'verdict': 'PASS', 'chosen': 'a', 'checks': {
        'lcb95>0': True, 'beats_placebo_lcb95>0': True, 'half_symbols_positive': True}}   # b: n < 30 on TRAIN
    assert fs.acceptance(train, lambda k: {**good, 'vs_placebo': {'lcb95': -0.1}})['verdict'] == 'FAIL'
    assert fs.acceptance(train, lambda k: {**good, 'stats': {**good['stats'], 'n': 99}})['verdict'] == 'INCONCLUSIVE'
    assert fs.acceptance({'b': train['b']}, lambda k: good)['verdict'] == 'INCONCLUSIVE'
