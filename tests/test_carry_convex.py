"""CCX-1 (research/carry_convex_study.py): frozen plan, venue floors at 10 U, the two-venue carry account against the
replay it is built on, the Binance-only variant, the funding-snipe simulator on hand-made bars, statistics, the
win-streak sizer and the growth simulation."""
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pytest

from core.position_sizer import streak_scaled_notional
from research import carry_convex_study as g
from tools import fetch_funding_universe as fu
from tools import funding_spread_replay as fsr

ROOT = Path(__file__).resolve().parents[1]
MIN, HOUR, DAY = 60_000, 3_600_000, 86_400_000
H8 = 8 * HOUR


# ------------------------------------------------------------------------------------------------- plan and constants
def test_the_plan_is_the_file_that_was_frozen_and_the_code_runs_its_numbers():
    plan = (ROOT / g.PLAN).read_bytes().replace(b'\r\n', b'\n')
    rows = [json.loads(x) for x in (ROOT / 'docs/research_upgrade/trial_log.jsonl').read_text(encoding='utf-8').splitlines()
            if x.strip()]
    frozen = [r for r in rows if r.get('kind') == 'ccx1-prereg']
    assert len(frozen) == 1 and frozen[0]['freeze']['sha256_lf'] == hashlib.sha256(plan).hexdigest()
    cfg = frozen[0]['cfg']
    assert (g.THETA_BPS, list(g.ENTRIES), list(g.EXITS), g.MIN_N) == (cfg['theta_bps'], cfg['entries'], cfg['exits'], cfg['min_n'])
    assert g.N_TRIALS == frozen[0]['n_trials'] == len(g.ENTRIES) * len(g.EXITS)
    assert {k: list(v) for k, v in g.TRAILS.items()} == cfg['trails'] and g.MAX_HOLD_MIN == cfg['max_hold_min']
    assert {'maker': g.MAKER, 'taker': g.TAKER, 'slip': g.SLIP} == cfg['cost_bps']
    assert (g.XS_H_DAYS, g.XS_K, g.XS_COST_BPS) == (cfg['xs']['h_days'], cfg['xs']['k'], cfg['xs']['cost_bps'])
    assert (list(g.CARRY_LEVERAGES), list(g.TRANSFER_COSTS), g.HL_MIN_ORDER_USD, g.MMR) == \
        (cfg['carry']['leverages'], cfg['carry']['transfer_costs'], cfg['carry']['hl_min'], cfg['carry']['mmr'])
    assert {k: list(v) for k, v in g.PARTS.items()} == cfg['parts']
    assert (g.PATHS, g.DAYS, g.EQUITY0, g.RUIN_U, g.MAX_LEV) == (cfg['growth']['paths'], cfg['growth']['days'],
                                                                 cfg['growth']['equity0'], cfg['growth']['ruin'],
                                                                 cfg['growth']['max_lev'])


def test_costs_are_the_repo_constants_and_the_lane_guard():
    from core.early_stop import MAKER_FEE_BPS, SPREAD_SLIPPAGE_BPS, TAKER_FEE_BPS
    from research.carry_lane import GuardPolicy, HEDGE_ROUND_TRIP_BPS
    assert (g.MAKER, g.TAKER, g.SLIP) == (MAKER_FEE_BPS, TAKER_FEE_BPS, SPREAD_SLIPPAGE_BPS)
    assert g.SCHEDULES[g.PRIMARY] == (g.MAKER, g.TAKER, g.SLIP)
    assert g.BN_FEE_RT + g.HL_FEE_RT == HEDGE_ROUND_TRIP_BPS == fsr.round_trip_cost('bn_perp', 'hl_perp', 'HEDGE')
    assert g.BN_FEE_RT == 2 * fsr.FEES['bn_perp'][0] and g.HL_FEE_RT == 2 * (fsr.FEES['hl_perp'][1] + fsr.SLIP)
    assert g.XS_COST_BPS == 2 * (fsr.FEES['bn_perp'][0] + fsr.FEES['bn_perp'][1] + fsr.SLIP)
    p = GuardPolicy()
    assert (g.GUARD_CLOSE_DIST, g.LANE_MAX_LEVERAGE) == (p.close_liq_dist_pct / 100, p.max_leverage)


def test_partitions_do_not_overlap():
    assert g.part_of(g.ms('2025-01-01')) == 'TRAIN' and g.part_of(g.ms('2025-12-31') + DAY - 1) == 'TRAIN'
    assert g.part_of(g.ms('2026-01-01')) == 'HOLDOUT' and g.part_of(g.ms('2026-10-01')) == 'FRESH'
    assert g.part_of(g.ms('2024-12-31')) is None and g.part_of(g.ms('2026-11-13')) is None


# ------------------------------------------------------------------------------------------------- A: venue floors
def test_ten_usdt_reaches_the_two_venue_floor_only_at_two_times_leverage_with_no_buffer():
    assert g.two_venue_plan(10.0, 1.0)['feasible'] is False            # 5 U per leg < Hyperliquid 10 USD
    p = g.two_venue_plan(10.0, 2.0)
    assert p['feasible'] and p['notional_per_leg'] == 10.0 and p['free_margin'] == 0.0 and p['min_leverage'] == 2.0
    assert g.two_venue_plan(9.99, 2.0)['feasible'] is False
    assert g.two_venue_plan(10.0, 2.0, transfer_cost=1.0)['feasible'] is False   # one transfer fee breaks it
    p3 = g.two_venue_plan(10.0, 3.0, transfer_cost=1.0)
    assert p3['feasible'] and p3['notional_per_leg'] == pytest.approx(13.5)
    assert p3['free_margin'] == pytest.approx(9.0 - 2 * 10.0 / 3.0)
    assert g.two_venue_plan(10.0, 3.0, bn_min=20.0)['feasible'] is False         # a 20 U symbol needs more
    with pytest.raises(ValueError):
        g.two_venue_plan(0.0, 2.0)


def test_one_venue_pair_fits_at_one_times_leverage():
    assert g.one_venue_plan(10.0, 1.0) == {'notional_per_leg': 5.0, 'floor': 5.0, 'feasible': True}
    assert g.one_venue_plan(9.9, 1.0)['feasible'] is False
    assert g.one_venue_plan(10.0, 1.0, min_b=20.0)['feasible'] is False and g.one_venue_plan(10.0, 4.0, min_b=20.0)['feasible']


def test_liquidation_and_guard_distances():
    assert g.liq_move(2.0, -1) == pytest.approx(0.475 / 1.025) and g.liq_move(2.0, 1) == pytest.approx(0.475 / 0.975)
    assert g.guard_move(2.0, -1) == pytest.approx((1 + 0.475 / 1.025) / 1.06 - 1)
    assert g.guard_move(2.0, 1) == pytest.approx(1 - (1 - 0.475 / 0.975) / 0.94)
    for side in (1, -1):
        assert 0 < g.guard_move(3.0, side) < g.liq_move(3.0, side) < g.liq_move(2.0, side)
        assert g.liq_move(50.0, side) == 0.0 and g.guard_move(50.0, side) == 0.0     # 1 / L <= maintenance margin
    with pytest.raises(ValueError):
        g.liq_move(0.0, 1)


def test_min_notional_reads_usdt_perpetuals_only():
    info = {'symbols': [
        {'symbol': 'AUSDT', 'contractType': 'PERPETUAL', 'quoteAsset': 'USDT', 'filters': [{'filterType': 'MIN_NOTIONAL', 'notional': '5'}]},
        {'symbol': 'BUSDT', 'contractType': 'PERPETUAL', 'quoteAsset': 'USDT', 'filters': [{'filterType': 'MIN_NOTIONAL', 'notional': '20'}]},
        {'symbol': 'AUSDC', 'contractType': 'PERPETUAL', 'quoteAsset': 'USDC', 'filters': [{'filterType': 'MIN_NOTIONAL', 'notional': '5'}]},
        {'symbol': 'AUSDT_260327', 'contractType': 'CURRENT_QUARTER', 'quoteAsset': 'USDT', 'filters': []}]}
    assert g.min_notional(info) == {'AUSDT': 5.0, 'BUSDT': 20.0}
    assert fu.perp_symbols(info) == ['AUSDT', 'BUSDT']
    assert fu.on_minute(5 * MIN + 29_999) == 5 * MIN and fu.on_minute(5 * MIN + 30_001) == 6 * MIN   # venue jitter


# ------------------------------------------------------------------------------------------------- B: XV account
def series(n_days=130, bn_rate=3.0, hl_rate=0.0, px_bn=None, px_hl=None):
    a = fsr.ms(fsr.START)
    bt = np.arange(a, a + n_days * DAY + 1, H8, dtype=np.int64)
    ht = np.arange(a, a + n_days * DAY + 1, HOUR, dtype=np.int64)
    kt = np.arange(a, a + n_days * DAY + 1, 4 * HOUR, dtype=np.int64)
    flat = np.full(kt.size, 100.0)
    return {'bn': (bt, np.full(bt.size, bn_rate)), 'hl': (ht, np.full(ht.size, hl_rate)),
            'px_bn': (kt, flat if px_bn is None else px_bn(kt)), 'px_other': (kt, flat if px_hl is None else px_hl(kt))}


def wiggle(seed, drift=0.0):
    def f(kt):
        rng = np.random.default_rng(seed)
        return 100.0 * np.exp(np.cumsum(rng.normal(drift, 0.004, kt.size)))
    return f


def test_per_venue_legs_add_up_to_the_replay_trade():
    ser = series(px_bn=wiggle(1), px_hl=wiggle(2))
    trades = fsr.simulate(ser, 'XV', 'AAA', 28, 2.0, 'HEDGE')
    assert len(trades) >= 3
    for tr in trades:
        legs = g.xv_legs(ser, tr, 2.0, 2.0)
        assert legs['guard_close'] is False and legs['t_exit'] == int(tr['t'] * 1000) + 28 * DAY
        assert legs['net_bps'] == pytest.approx(tr['net_bps'], abs=1e-9)
    flat = series()
    tr = fsr.simulate(flat, 'XV', 'AAA', 28, 2.0, 'HEDGE')[0]
    legs = g.xv_legs(flat, tr, 2.0, 2.0)
    assert legs['bn_bps'] == pytest.approx(3.0 * 84 - 4.0) and legs['hl_bps'] == pytest.approx(-11.0)   # short Binance


def test_guard_closes_both_legs_before_a_liquidation():
    def rally(kt):                                         # the Binance (short) leg gains 60 % from day 13
        return np.where(kt >= fsr.ms(fsr.START) + 13 * DAY, 160.0, 100.0)
    ser = series(px_bn=rally, px_hl=rally)
    tr = fsr.simulate(ser, 'XV', 'AAA', 28, 2.0, 'HEDGE')[0]
    t0 = int(tr['t'] * 1000)
    legs = g.xv_legs(ser, tr, 2.0, 2.0)
    assert legs['guard_close'] and legs['t_exit'] == fsr.ms(fsr.START) + 13 * DAY < t0 + 28 * DAY
    prints = (legs['t_exit'] - t0) // H8
    assert legs['bn_bps'] == pytest.approx(3.0 * prints - 6000.0 - 4.0) and legs['hl_bps'] == pytest.approx(6000.0 - 11.0)
    assert g.xv_legs(ser, tr, 1.2, 1.2)['guard_close'] is False        # lower leverage: the guard is further away


def test_account_at_two_times_leverage_dies_on_one_transfer_fee():
    ser = {'AAA': series()}
    trades = fsr.simulate(ser['AAA'], 'XV', 'AAA', 28, 2.0, 'HEDGE')
    free = g.carry_account(trades, ser, 10.0, 2.0, 0.0)
    assert free['rounds'] == len(trades) and free['path'][0]['notional'] == 10.0
    assert free['final_equity'] == pytest.approx(10.0 + free['trading_net_u']) and free['transfers_paid_u'] == 0.0
    paid = g.carry_account(trades, ser, 10.0, 2.0, 1.0)
    assert paid['rounds'] == 0 and paid['skipped_floor'] == len(trades) and paid['final_equity'] == 9.0 and paid['net_u'] == -1.0
    lev3 = g.carry_account(trades, ser, 10.0, 3.0, 1.0)
    assert lev3['rounds'] == len(trades) and lev3['path'][0]['notional'] == pytest.approx(13.5)
    assert lev3['final_equity'] == pytest.approx(10.0 - lev3['transfers_paid_u'] + lev3['trading_net_u'])


def test_account_takes_one_slot_and_rebalances_only_when_that_makes_the_round_possible():
    up = lambda kt: 100.0 * np.exp((kt - kt[0]) / DAY * 0.002)          # +0.2 % a day: the short venue bleeds margin
    a, b = series(bn_rate=3.0, px_bn=up, px_hl=up), series(bn_rate=6.0, px_bn=up, px_hl=up)
    ser = {'AAA': a, 'BBB': b}
    trades = fsr.simulate(a, 'XV', 'AAA', 28, 2.0, 'HEDGE') + fsr.simulate(b, 'XV', 'BBB', 28, 2.0, 'HEDGE')
    acc = g.carry_account(trades, ser, 10.0, 2.0, 0.0)
    assert {r['coin'] for r in acc['path']} == {'BBB'}                   # same entry time: the larger signal
    assert all(y['t'] >= x['t'] + 28 * DAY for x, y in zip(acc['path'], acc['path'][1:]))
    assert acc['rebalances'] >= 1 and acc['rounds'] == len(trades) // 2
    stuck = g.carry_account(trades, ser, 10.0, 2.0, 0.0, rebalance=False)
    assert stuck['rounds'] == 1 and stuck['skipped_floor'] >= 1          # the smaller balance can no longer post 10 U
    dear = g.carry_account(trades, ser, 10.0, 3.0, 1.0)
    assert dear['transfers_paid_u'] == 1.0 + dear['rebalances'] and dear['final_equity'] == pytest.approx(
        10.0 - dear['transfers_paid_u'] + dear['trading_net_u'])
    big = g.carry_account(trades, ser, 10.0, 3.0, 0.0, bn_min={'BBBUSDT': 20.0})
    assert {r['coin'] for r in big['path']} == {'AAA'}                   # 15 U per leg cannot open a 20 U symbol


# ------------------------------------------------------------------------------------------------- C: XS
def test_xs_shorts_the_highest_and_longs_the_lowest_binance_funding():
    ser = {'AAA': series(bn_rate=3.0), 'BBB': series(bn_rate=-1.0), 'CCC': series(bn_rate=1.0)}
    rounds = g.xs_rounds(ser)
    assert rounds and {r['coin'] for r in rounds} == {'AAA/BBB'}
    assert all(r['net_bps'] == pytest.approx(4.0 * 84 - g.XS_COST_BPS) and r['basis_bps'] == 0.0 for r in rounds)
    slot = g.one_slot(rounds)
    assert len(slot) < len(rounds) and all(y['t'] >= x['t'] + 28 * 86400 for x, y in zip(slot, slot[1:]))
    assert g.xs_rounds({'AAA': series(bn_rate=0.1), 'BBB': series(bn_rate=0.0)}) == []      # spread below 2 x cost
    px = {'AAA': series(bn_rate=3.0, px_bn=lambda kt: np.linspace(100, 110, kt.size)), 'BBB': series(bn_rate=-1.0)}
    r = g.xs_rounds(px)[0]
    assert r['basis_bps'] < 0 and r['net_bps'] == pytest.approx(r['funding_bps'] + r['basis_bps'] - g.XS_COST_BPS)


def test_xs_verdict_needs_the_month_bootstrap_bound_and_a_positive_fresh_mean():
    ho = {'n': 800, 'mean_net_bps': 50.0, 'ci95_month': [5.0, 90.0]}
    assert g.xs_verdict(ho, {'n': 20, 'mean_net_bps': 1.0})['verdict'] == 'EDGE'
    assert g.xs_verdict({**ho, 'ci95_month': [-5.0, 90.0]}, {'n': 20, 'mean_net_bps': 1.0})['verdict'] == 'NO EDGE'
    assert g.xs_verdict({**ho, 'n': 99}, {'n': 20, 'mean_net_bps': 1.0})['verdict'] == 'NO EDGE'
    assert g.xs_verdict(ho, {'n': 20, 'mean_net_bps': -1.0})['verdict'] == 'NO EDGE'
    assert g.xs_verdict(ho, {'n': 0})['verdict'] == 'NO EDGE' and g.xs_verdict({'n': 0}, {'n': 0})['verdict'] == 'NO EDGE'


def test_stop_fraction_of_a_cell():
    assert g.stop_frac_of('M|TR15') == 0.005 and g.stop_frac_of('TK|TR30') == 0.010
    assert g.stop_frac_of('M|X0') == 0.005 and g.stop_frac_of(None) == 0.005


# ------------------------------------------------------------------------------------------------- D: events
def test_events_previous_print_is_causal_and_the_print_itself_is_the_bound():
    t = np.array([0, H8, 2 * H8, 5 * H8, 6 * H8], dtype=np.int64) + g.ms('2025-03-01')
    r = np.array([20.0, 1.0, -16.0, -30.0, 2.0])
    ev = g.find_events(t, r)
    assert ev == [(int(t[0]), None, 20.0),          # O only: no previous print
                  (int(t[1]), 20.0, 1.0),           # P: the previous print was 20 bps
                  (int(t[2]), 1.0, -16.0),          # O only
                  (int(t[3]), None, -30.0),         # a 24 h hole: the previous print is not a signal
                  (int(t[4]), -30.0, 2.0)]
    assert g.find_events(t, r * 0.5) == [(int(t[4]), -15.0, 1.0), (int(t[3]), None, -15.0)][::-1]
    c = g.count_events({'AUSDT': ev})
    assert c['P|TRAIN']['events'] == 2 and c['O|TRAIN']['events'] == 3 and c['P|HOLDOUT']['events'] == 0
    assert c['O|TRAIN']['settlement_times'] == 3 and c['O|TRAIN']['symbols'] == 1


# ------------------------------------------------------------------------------------------------- D: simulator
def bars(n=g.PRE + g.MAX_HOLD_MIN + 1, px=100.0):
    o = np.full(n, px)
    return o.copy(), o.copy(), o.copy(), o.copy()          # o, h, l, c


I0 = g.PRE


def test_maker_entry_needs_a_trade_through_the_quote():
    o, h, l, c = bars()
    assert g.snipe_trade(o, h, l, c, 1, -20.0, 'M', 'X0') is None                # nothing trades below the bid
    l[I0 - 3] = 100.0
    assert g.snipe_trade(o, h, l, c, 1, -20.0, 'M', 'X0') is None                # a touch is not a fill
    l[I0 - 3] = 99.99
    assert g.snipe_trade(o, h, l, c, 1, -20.0, 'M', 'X0') is not None
    assert g.snipe_trade(o, h, l, c, -1, 20.0, 'M', 'X0') is None                # the short needs a print above
    h[I0 - 1] = 100.01
    assert g.snipe_trade(o, h, l, c, -1, 20.0, 'M', 'X0') is not None
    o2, h2, l2, c2 = bars()
    l2[I0 - 6] = 99.0                                                            # before T - 5 min: too early
    l2[I0] = 99.0                                                                # after T: too late
    assert g.snipe_trade(o2, h2, l2, c2, 1, -20.0, 'M', 'X0') is None
    assert g.snipe_trade(o2, h2, l2, c2, 1, -20.0, 'M', 'X0', fill='opt') is not None


def test_x0_arithmetic_and_the_funding_sign():
    o, h, l, c = bars()
    l[I0 - 2] = 99.9
    o[I0] = 100.10
    r = g.snipe_trade(o, h, l, c, 1, -20.0, 'M', 'X0')
    price = (100.10 * (1 - 1.5e-4) / 100.0 - 1) * 1e4
    assert r['fund'] == 20.0 and r['fee'] == 7.0 and r['price'] == pytest.approx(price) and r['why'] == 'T0'
    assert r['net'] == pytest.approx(price + 20.0 - 7.0)
    assert g.snipe_trade(o, h, l, c, 1, +20.0, 'M', 'X0')['fund'] == -20.0       # a long PAYS a positive print
    h[I0 - 2] = 100.2
    s = g.snipe_trade(o, h, l, c, -1, 20.0, 'M', 'X0')
    assert s['fund'] == 20.0 and s['price'] == pytest.approx(-(100.10 * (1 + 1.5e-4) / 100.0 - 1) * 1e4)
    z = g.snipe_trade(o, h, l, c, 1, -20.0, 'M', 'X0', fees=(0.0, 0.0, 0.0))
    assert z['net'] == pytest.approx(10.0 + 20.0)


def test_taker_entry_pays_the_taker_fee_and_the_slip():
    o, h, l, c = bars()
    o[I0 - 1] = 100.0
    r = g.snipe_trade(o, h, l, c, 1, -20.0, 'TK', 'X0')
    assert r['fee'] == 10.0 and r['price'] == pytest.approx(((1 - 1.5e-4) / (1 + 1.5e-4) - 1) * 1e4)
    assert r['net'] == pytest.approx(r['price'] + 20.0 - 10.0)
    with pytest.raises(ValueError):
        g.snipe_trade(o, h, l, c, 1, -20.0, 'ZZ', 'X0')
    with pytest.raises(ValueError):
        g.snipe_trade(o, h, l, c, 1, -20.0, 'TK', 'ZZ')


def test_d1_is_outside_the_frozen_grid_and_exits_one_minute_later():
    assert g.EXITS == ('X0', 'X1', 'TR15', 'TR30') and g.N_TRIALS == 8      # the addendum did not widen the grid
    o, h, l, c = bars()
    o[I0] = 100.30
    o[I0 + 1] = 99.90
    x0, d1 = g.snipe_trade(o, h, l, c, 1, -20.0, 'TK', 'X0'), g.snipe_trade(o, h, l, c, 1, -20.0, 'TK', 'D1')
    pe = 100.0 * (1 + 1.5e-4)
    assert d1['why'] == 'D1' and d1['hold_min'] == 1 and d1['fee'] == 10.0
    assert d1['price'] == pytest.approx((99.90 * (1 - 1.5e-4) / pe - 1) * 1e4)
    assert x0['net'] - d1['net'] == pytest.approx((100.30 - 99.90) / pe * (1 - 1.5e-4) * 1e4)


def test_x1_maker_exit_or_taker_after_ten_minutes():
    o, h, l, c = bars()
    h[I0 + 4] = 100.01
    r = g.snipe_trade(o, h, l, c, 1, -20.0, 'TK', 'X1')
    assert r['why'] == 'MK' and r['fee'] == 5.0 + 2.0
    o, h, l, c = bars()
    o[I0 + 10] = 99.5
    h[I0 + 10] = 100.5                                                           # the 11th minute is too late
    r = g.snipe_trade(o, h, l, c, 1, -20.0, 'TK', 'X1')
    assert r['why'] == 'TO' and r['fee'] == 10.0 and r['hold_min'] == 10
    assert r['price'] == pytest.approx((99.5 * (1 - 1.5e-4) / (100 * (1 + 1.5e-4)) - 1) * 1e4)


def test_trailing_exit_hard_stop_gap_and_time():
    o, h, l, c = bars()
    l[I0 + 3] = 99.4                                                             # 60 bps under a 50 bps stop
    r = g.snipe_trade(o, h, l, c, 1, -20.0, 'TK', 'TR15')
    pe = 100.0 * (1 + 1.5e-4)
    assert r['why'] == 'SL' and r['hold_min'] == 3
    assert r['price'] == pytest.approx((pe * (1 - 50e-4) * (1 - 1.5e-4) / pe - 1) * 1e4)
    o[I0 + 3] = l[I0 + 3] = 99.0                                                 # the bar OPENS through the stop
    r = g.snipe_trade(o, h, l, c, 1, -20.0, 'TK', 'TR15')
    assert r['price'] == pytest.approx((99.0 * (1 - 1.5e-4) / pe - 1) * 1e4)
    assert g.snipe_trade(o, h, l, c, 1, -20.0, 'TK', 'TR30')['why'] == 'SL' and \
        g.snipe_trade(*bars(), 1, -20.0, 'TK', 'TR30')['why'] == 'TM'
    o, h, l, c = bars()
    o[I0 + g.MAX_HOLD_MIN] = 100.2
    r = g.snipe_trade(o, h, l, c, 1, -20.0, 'TK', 'TR15')
    assert r['why'] == 'TM' and r['hold_min'] == g.MAX_HOLD_MIN
    assert r['price'] == pytest.approx((100.2 * (1 - 1.5e-4) / pe - 1) * 1e4)


def test_trail_arms_only_after_the_fees_and_reads_the_adverse_side_first():
    pe = 100.0 * (1 + 1.5e-4)
    o, h, l, c = bars()
    h[I0 + 1] = pe * (1 + 5e-4)                                                  # +5 bps: below the 8.5 bps shield
    l[I0 + 2] = pe * (1 - 20e-4)                                                 # -20 bps: no trail yet, no stop
    assert g.snipe_trade(o, h, l, c, 1, -20.0, 'TK', 'TR15')['why'] == 'TM'
    o, h, l, c = bars()
    best = pe * (1 + 30e-4)
    h[I0 + 1] = best                                                             # armed, best = +30 bps
    o[I0 + 2] = h[I0 + 2] = best
    l[I0 + 2] = pe * (1 + 10e-4)                                                 # 20 bps off the best
    r = g.snipe_trade(o, h, l, c, 1, -20.0, 'TK', 'TR15')
    assert r['why'] == 'TR' and r['hold_min'] == 2
    assert r['price'] == pytest.approx((best * (1 - 15e-4) * (1 - 1.5e-4) / pe - 1) * 1e4)
    o, h, l, c = bars()
    h[I0 + 1] = best
    l[I0 + 1] = pe * (1 + 2e-4)                                                  # same bar: its low is read FIRST
    r = g.snipe_trade(o, h, l, c, 1, -20.0, 'TK', 'TR15')
    assert r['why'] == 'TR' and r['hold_min'] == 2                               # the NEXT bar opens under the trail
    assert r['price'] == pytest.approx((100.0 * (1 - 1.5e-4) / pe - 1) * 1e4)    # and it leaves at that open
    o, h, l, c = bars()
    ps = 100.0 * (1 - 1.5e-4)                                                    # short: mirror image
    low = ps * (1 - 30e-4)
    l[I0 + 1] = low
    o[I0 + 2] = l[I0 + 2] = low
    h[I0 + 2] = ps * (1 - 10e-4)
    r = g.snipe_trade(o, h, l, c, -1, 20.0, 'TK', 'TR15')
    assert r['why'] == 'TR' and r['price'] == pytest.approx(-(low * (1 + 15e-4) * (1 + 1.5e-4) / ps - 1) * 1e4)


def test_bars_need_every_minute():
    t = g.ms('2025-03-01') + 8 * HOUR
    rows = [[t + (i - g.PRE) * MIN, 100 + i, 101 + i, 99 + i, 100.5 + i, 7.0] for i in range(g.KL_LIMIT)]
    o, h, l, c = g.bars_of(rows, t)
    assert (o[g.PRE], h[g.PRE], l[g.PRE], c[g.PRE]) == (110.0, 111.0, 109.0, 110.5) and o.size == g.PRE + g.MAX_HOLD_MIN + 1
    assert g.bars_of(rows[:g.PRE + g.MAX_HOLD_MIN], t) is None                   # one bar short
    assert g.bars_of(rows[:20] + rows[21:], t) is None                           # a missing minute is UNKNOWN
    assert g.bars_of(rows[1:], t) is None and g.bars_of([], t) is None and g.bars_of(None, t) is None
    d = g.drift(o, c, 1)
    assert d['post1'] == pytest.approx((o[g.PRE + 1] / o[g.PRE] - 1) * 1e4) and g.drift(o, c, -1)['post1'] == -d['post1']


def test_simulate_events_reads_the_cache_and_marks_missing_bars(tmp_path):
    t = g.ms('2026-02-01') + 8 * HOUR
    rows = [[t + (i - g.PRE) * MIN, 100.0, 100.0, 100.0, 100.0, 1.0] for i in range(g.KL_LIMIT)]
    rows[g.PRE - 2][3] = 99.9                                                    # the long's maker entry fills
    (tmp_path / 'kl_AUSDT.json').write_text(json.dumps({str(t): rows}))
    ev = {'AUSDT': [(t, -20.0, -18.0), (t + H8, -18.0, -1.0)], 'BUSDT': [(t, None, 40.0)]}
    out, missing = g.simulate_events(ev, tmp_path)
    assert missing == 2 and [(r['sig'], r['side'], r['part']) for r in out] == [('P', 1, 'HOLDOUT'), ('O', 1, 'HOLDOUT')]
    assert set(out[0]['cells']) == {f'{a}|{b}' for a in g.ENTRIES for b in g.EXITS}
    assert out[0]['cells']['M|X0']['net'] == pytest.approx(18.0 - 7.0 - 1.5, abs=1e-6)
    table = g.cell_table(out, 'P', 'HOLDOUT')
    assert table['M|X0']['n'] == 1 and table['M|X0']['fill_rate'] == 1.0 and table['M|X0']['fund'] == 18.0
    assert g.cell_table(out, 'P', 'TRAIN')['M|X0'] == {'n': 0, 'signals': 0, 'fill_rate': None}


# ------------------------------------------------------------------------------------------------- statistics
def test_cluster_stats_is_never_tighter_than_iid_and_matches_a_hand_case():
    x, d = np.array([1.0, 1.0, 3.0, 3.0]), np.array([0, 0, 1, 1])
    s = g.cluster_stats(x, d, 0)
    sums = np.array([0.5, 1.5])                                                  # day sums of x / n
    assert s['mean'] == 2.0 and s['se'] == pytest.approx(math.sqrt(2 * ((sums - 1.0) ** 2).sum())) and s['days'] == 2
    assert s['se'] > x.std(ddof=1) / 2 and s['lcb95'] == pytest.approx(2.0 - 1.645 * s['se'])
    iid = g.cluster_stats(x, np.arange(4), 0)
    assert iid['se'] >= x.std(ddof=1) / 2 - 1e-12
    assert g.cluster_stats([], [], 0) == {'n': 0}
    rng = np.random.default_rng(0)
    y = rng.normal(5.0, 1.0, 400)
    assert g.cluster_stats(y, np.arange(400), 8)['dsr'] > 0.95 and g.cluster_stats(y - 5.0, np.arange(400), 8)['dsr'] < 0.95


def test_verdict_needs_every_check():
    good = {'n': 150, 'mean': 4.0, 'lcb95': 1.0, 'dsr': 0.99}
    assert g.verdict(good, good, {'n': 20, 'mean': 0.5})['verdict'] == 'EDGE'
    for bad in ({**good, 'n': 99}, {**good, 'lcb95': 0.0}, {**good, 'dsr': 0.9}, {**good, 'dsr': None}):
        assert g.verdict(good, bad, {'n': 20, 'mean': 0.5})['verdict'] == 'NO EDGE'
    assert g.verdict(good, good, {'n': 20, 'mean': -0.1})['verdict'] == 'NO EDGE'
    assert g.verdict(good, good, {'n': 0})['verdict'] == 'NO EDGE'


def test_choose_cell_and_top1():
    train = {'M|X0': {'n': 99, 'mean': 50.0}, 'TK|X0': {'n': 100, 'mean': -3.0}, 'TK|X1': {'n': 500, 'mean': -1.0}}
    assert g.choose_cell(train) == 'TK|X1' and g.choose_cell({'M|X0': {'n': 5, 'mean': 1.0}}) is None
    t = g.ms('2026-03-01')
    rows = [{'sym': 'A', 't': t, 'part': 'HOLDOUT', 'day': t // DAY, 'sig': 'P', 'strength': 20.0, 'cells': {'M|X0': {'net': 5.0}}},
            {'sym': 'B', 't': t, 'part': 'HOLDOUT', 'day': t // DAY, 'sig': 'P', 'strength': 90.0, 'cells': {}},
            {'sym': 'C', 't': t + H8, 'part': 'HOLDOUT', 'day': t // DAY, 'sig': 'P', 'strength': 16.0, 'cells': {'M|X0': {'net': -2.0}}},
            {'sym': 'D', 't': t + H8, 'part': 'HOLDOUT', 'day': t // DAY, 'sig': 'O', 'strength': 99.0, 'cells': {'M|X0': {'net': 9.0}}}]
    net, day, span = g.top1_series(rows, 'P', 'HOLDOUT', 'M|X0')
    assert list(net) == [-2.0] and span == 273.0             # the strongest symbol at t did not fill: no trade there


# ------------------------------------------------------------------------------------------------- sizer
def test_streak_scaling_needs_profit_a_streak_and_full_confluence():
    base = 10.0
    assert streak_scaled_notional(base, 0.2, 2, 3, 0.005) == pytest.approx(min(base * (1 + 5 * 0.2), base + 0.5 * 0.2 / 0.005))
    assert streak_scaled_notional(base, 0.2, 1, 3, 0.005) == base              # one win is not a streak
    assert streak_scaled_notional(base, 0.2, 5, 2, 0.005) == base              # confluence 2 / 3
    assert streak_scaled_notional(base, 0.0, 5, 3, 0.005) == base              # no profit to spend
    assert streak_scaled_notional(base, -0.3, 5, 3, 0.005) == base             # in drawdown
    for bad in (float('nan'), float('inf'), None, 'x', True):
        assert streak_scaled_notional(base, bad, 5, 3, 0.005) == base
    assert streak_scaled_notional(0.0, 1.0, 5, 3, 0.005) == 0.0 and streak_scaled_notional(float('nan'), 1.0, 5, 3, 0.005) == 0.0
    assert streak_scaled_notional(base, 0.2, 5, 3, 0.0) == base


def test_streak_scaling_never_risks_more_than_the_base_stop_plus_a_share_of_profit():
    rng = np.random.default_rng(3)
    for _ in range(500):
        base, pnl = rng.uniform(5, 50), rng.uniform(0, 30)
        stop, k, hf = rng.uniform(0.001, 0.05), rng.uniform(0.1, 50), rng.uniform(0.05, 1.0)
        n = streak_scaled_notional(base, pnl, 3, 3, stop, k=k, house_frac=hf)
        assert base <= n <= base * (1 + k * pnl) + 1e-9
        assert (n - base) * stop <= hf * pnl + 1e-9          # the step-up is paid by accumulated profit
    assert streak_scaled_notional(10.0, 1.0, 3, 3, 0.005, k=5.0) == 60.0        # (1 + 5 x 1) x 10 = 60 < shield 110
    assert streak_scaled_notional(10.0, 1.0, 3, 3, 0.05, k=5.0) == 20.0         # shield: 10 + 0.5 x 1 / 0.05


def test_sizer_function_has_no_engine_call_site():
    hits = [p for p in (ROOT / 'core').glob('*.py') if p.name != 'position_sizer.py'
            and 'streak_scaled_notional' in p.read_text(encoding='utf-8', errors='ignore')]
    assert hits == []


# ------------------------------------------------------------------------------------------------- growth
class FixedRng:
    def __init__(self, n):
        self.n = n

    def poisson(self, _):
        return self.n


def test_growth_path_flat_streak_fraction_and_the_absorbing_floor():
    flat = g.growth_path(lambda: 10.0, 2.0, FixedRng(2), days=30, mode='flat', base_notional=10.0)
    assert flat['trades'] == 60 and flat['equity'] == pytest.approx(10.0 + 60 * 0.01) and not flat['ruined']
    st = g.growth_path(lambda: 10.0, 2.0, FixedRng(2), days=30, mode='streak', base_notional=10.0, stop_frac=0.005, k=5.0)
    assert st['equity'] > flat['equity']                                          # only winners: the step-up adds
    lose = g.growth_path(lambda: -10.0, 2.0, FixedRng(2), days=30, mode='streak', base_notional=10.0, stop_frac=0.005)
    assert lose['equity'] == pytest.approx(10.0 - 60 * 0.01)                      # never scaled without profit
    dead = g.growth_path(lambda: -50.0, 5.0, FixedRng(5), days=30, mode='frac', risk_frac=0.10, stop_frac=0.005)
    assert dead['ruined'] and 0 < dead['equity'] < g.RUIN_U and dead['trades'] < 150
    capped = g.growth_path(lambda: 0.0, 1.0, FixedRng(1), days=1, mode='frac', risk_frac=0.5, stop_frac=0.0001, max_lev=20.0)
    assert capped['equity'] == 10.0
    one = g.growth_path(lambda: 100.0, 1.0, FixedRng(1), days=1, mode='frac', risk_frac=0.5, stop_frac=0.0001, max_lev=20.0)
    assert one['equity'] == pytest.approx(10.0 + 200.0 * 0.01)                    # notional capped at 20 x equity
    with pytest.raises(ValueError):
        g.growth_path(lambda: 0.0, 1.0, FixedRng(1), mode='zz')
    wiped = g.growth_path(lambda: -900.0, 1.0, FixedRng(1), days=3, mode='flat', base_notional=200.0)
    assert wiped['equity'] == 0.0 and wiped['ruined'] and wiped['trades'] == 1     # -18 U on 10 U: liquidated, not -8 U
    cap = g.growth_path(lambda: 100.0, 1.0, FixedRng(1), days=1, mode='frac', risk_frac=0.5, stop_frac=0.0001,
                        cap_notional=50.0)
    assert cap['equity'] == pytest.approx(10.0 + 50.0 * 0.01)                      # the book takes 50 U, not 200


def test_growth_mc_and_the_target_arithmetic():
    rng = np.random.default_rng(1)
    x = rng.normal(-5.0, 30.0, 500)
    a = g.growth_mc(x, 3.0, paths=200, mode='flat', base_notional=10.0)
    b = g.growth_mc(x, 3.0, paths=200, mode='flat', base_notional=10.0)
    assert a == b and a['paths'] == 200 and a['p_target_20u_per_hour'] == 0.0 and a['median'] < 10.0
    assert a['mean_u_per_hour'] == pytest.approx((a['mean'] - 10.0) / 720.0)
    assert g.growth_mc([], 3.0) == {'paths': 0}
    t = g.target_arithmetic(10.0, 20.0, 20.0, best_bps=2.0, per_hour=0.5)
    assert t['max_notional'] == 200.0 and t['net_bps_per_hour_needed'] == pytest.approx(1000.0)
    assert t['pct_of_equity_per_hour'] == 200.0 and t['shortfall_x'] == pytest.approx(1000.0)
    assert g.target_arithmetic(best_bps=-3.0, per_hour=1.0)['shortfall_x'] == math.inf


def test_ledger_view_counts_margin_rows_and_net_bps(tmp_path):
    tr, od = tmp_path / 't.jsonl', tmp_path / 'o.jsonl'
    tr.write_text('\n'.join(json.dumps(x) for x in [
        {'tag': 'AUTO', 'qty': 100.0, 'entry': 0.1, 'pnl': -0.005, 'day': '2026-11-10', 'micro': True},
        {'tag': 'AUTO', 'qty': 50.0, 'entry': 2.0, 'pnl': 0.02, 'day': '2026-11-11'},
        {'tag': 'TEST', 'qty': 1.0, 'entry': 1.0, 'pnl': 9.0, 'day': '2026-11-11'}]))
    od.write_text('\n'.join(json.dumps(x) for x in [{'outcome': 'REFUSED', 'why': 'KER'},
                                                     {'outcome': 'REFUSED', 'error': 'Margin is insufficient'}]))
    v = g.ledger_view(tr, od)
    assert v['auto_trades'] == 2 and v['net_bps'] == pytest.approx([-5.0, 2.0]) and v['micro_net_bps'] == pytest.approx([-5.0])
    assert v['order_rows'] == 2 and v['margin_or_liquidation_rows'] == 1 and v['trades_per_active_day'] == 1.0
    assert v['max_notional'] == 100.0


def test_run_end_to_end_on_a_synthetic_universe(tmp_path, monkeypatch):
    uni, kl = tmp_path / 'u', tmp_path / 'k'
    uni.mkdir()
    kl.mkdir()
    (uni / 'exchangeInfo.json').write_text(json.dumps({'symbols': [
        {'symbol': 'AUSDT', 'contractType': 'PERPETUAL', 'quoteAsset': 'USDT',
         'filters': [{'filterType': 'MIN_NOTIONAL', 'notional': '5'}]}]}))
    t = np.arange(g.ms('2024-12-20'), g.ms('2026-11-13'), H8, dtype=np.int64)
    rate = np.where(np.arange(t.size) % 10 == 0, -20.0, 1.0)             # every tenth print pays the longs 20 bps
    (uni / 'fr_AUSDT.json').write_text(json.dumps([[int(a), float(b)] for a, b in zip(t, rate)]))
    cache = {}
    for T, _, _ in g.load_events(uni)['AUSDT']:
        rows = [[T + (i - g.PRE) * MIN, 100.0, 100.0, 100.0, 100.0, 1.0] for i in range(g.KL_LIMIT)]
        rows[g.PRE - 2][3] = 99.9                                        # the long's post-only entry fills
        cache[str(T)] = rows
    (kl / 'kl_AUSDT.json').write_text(json.dumps(cache))
    ser = {'AAA': series(n_days=400), 'BBB': series(n_days=400, bn_rate=-1.0)}
    trades = fsr.simulate(ser['AAA'], 'XV', 'AAA', 28, 2.0, 'HEDGE')
    led = {'auto_trades': 2, 'net_bps': [-5.0, 2.0], 'mean_net_bps': -1.5, 'active_days': 1, 'trades_per_active_day': 2.0,
           'order_rows': 0, 'margin_or_liquidation_rows': 0, 'micro_trades': 0, 'micro_net_bps': [],
           'micro_mean_net_bps': None, 'max_notional': 5.0}
    monkeypatch.setattr(g, 'UNIVERSE', uni)
    monkeypatch.setattr(g, 'KLINES', kl)
    monkeypatch.setattr(g, 'MIN_N', 5)
    monkeypatch.setattr(g, 'load_xv', lambda: (ser, trades))
    monkeypatch.setattr(g, 'ledger_view', lambda: led)
    monkeypatch.setattr(g, 'load_events', lambda universe=uni, theta=g.THETA_BPS, _f=g.load_events: _f(uni, theta))
    monkeypatch.setattr(g, 'simulate_events', lambda ev, klines=kl, fill='cons', fees=(g.MAKER, g.TAKER, g.SLIP),
                        exits=g.EXITS, _f=g.simulate_events: _f(ev, kl, fill, fees, exits))
    out = g.run(log=lambda *_: None, paths=20)
    json.dumps(out, default=float)                                        # the result file can be written
    assert out['A_floors']['two_venue']['L2|c0']['feasible'] and not out['A_floors']['two_venue']['L2|c1']['feasible']
    assert out['B_carry']['accounts']['L2|c1']['rounds'] == 0 and out['B_carry']['accounts']['L2|c0']['rounds'] > 0
    assert out['C_xs']['t2']['verdict'] in ('EDGE', 'NO EDGE') and out['C_xs']['TRAIN']['n'] > 0
    d = out['D_snipe']
    assert d['t1_cell'] in {f'{a}|{b}' for a in g.ENTRIES for b in g.EXITS} and d['t1']['verdict'] in ('EDGE', 'NO EDGE')
    assert out['D_missing_bars'] == 0 and out['D_events']['O|TRAIN']['events'] > 0
    # signal O is long into a -20 bps print: +20 funding, -7 fees, -1.5 slip on a flat price
    assert d['tables']['O']['HOLDOUT']['M|X0']['mean'] == pytest.approx(11.5, abs=1e-6)
    # signal P trades the NEXT print (+1 bps, which a long pays): -1 funding
    assert d['tables']['P']['HOLDOUT']['M|X0']['mean'] == pytest.approx(-9.5, abs=1e-6)
    assert d['schedules']['zero']['O']['M|X0']['mean'] == pytest.approx(20.0, abs=1e-6)
    assert d['opt_fill_holdout']['P']['M|X0']['fill_rate'] == 1.0
    assert set(out['E_growth']) == {'snipe_P_top1', 'ledger_auto', 'snipe_O_top1_bound'}
    assert out['E_growth']['snipe_P_top1']['flat_10u']['p_target_20u_per_hour'] == 0.0
    assert out['E_target']['net_bps_per_hour_needed'] == pytest.approx(1000.0)
    add = g.addendum(paths=20)                                            # A2: the exit one minute later
    json.dumps(add, default=float)
    d1 = add['delayed_exit']['P']['HOLDOUT']
    assert set(d1) == {'M|X0', 'M|D1', 'TK|X0', 'TK|D1'} and d1['M|D1']['mean'] == pytest.approx(d1['M|X0']['mean'])
    assert set(add['growth']) == {'M|X0', 'M|D1'} and add['cap_notional'] == g.CAP_NOTIONAL
    assert add['growth']['M|D1']['frac_10pct_cap']['p_target_20u_per_hour'] == 0.0


def test_tick_ladder_measures_from_the_last_trade_before_the_settlement():
    from tools import snipe_tick_check as tc
    t = 1_000_000
    trades = [(t - 900, 100.0), (t - 20, 100.10), (t + 8, 100.09), (t + 120, 100.00), (t + 400, 99.80), (t + 5000, 99.70)]
    lad = tc.ladder(trades, t, 1)
    assert lad['first_delay_ms'] == 8 and lad['n_100ms'] == 1 and lad['n_1s'] == 3
    f = lambda px: (px / 100.10 - 1) * 1e4
    assert lad['moves']['0'] == pytest.approx(f(100.09)) and lad['moves']['50'] == pytest.approx(f(100.09))
    assert lad['moves']['150'] == pytest.approx(f(100.00)) and lad['moves']['500'] == pytest.approx(f(99.80))
    assert lad['moves']['60000'] == pytest.approx(f(99.70))
    assert tc.ladder(trades, t, -1)['moves']['500'] == pytest.approx(-f(99.80))    # the short receiver gains on a fall
    quiet = tc.ladder([(t - 20, 100.0), (t + 700, 99.0)], t, 1)
    assert quiet['moves']['500'] == 0.0 and quiet['moves']['1000'] == pytest.approx(-100.0)   # no print yet: unchanged
    assert tc.ladder([(t + 5, 100.0)], t, 1) is None and tc.ladder([(t - 5, 100.0)], t, 1) is None
    rows = [{'sym': 'A', 'funding': 40.0, **lad}, {'sym': 'B', 'funding': 20.0, **quiet}]
    s = tc.summarize(rows)
    assert s['n'] == 2 and s['symbols'] == 2 and s['funding_mean'] == 30.0
    assert s['moves']['500']['mean'] == pytest.approx((f(99.80) + 0.0) / 2)
    assert s['moves']['500']['share_of_funding'] == pytest.approx(s['moves']['500']['mean'] / 30.0)
    assert tc.summarize([]) == {'n': 0}


def test_basis_dislocation_needs_a_gap_larger_than_the_cost():
    flat = {'AAA': series()}
    assert g.basis_dislocation(flat)['signals'] == 0 and g.basis_dislocation(flat)['bars'] > 0

    def spike(kt):
        p = np.full(kt.size, 100.0)
        p[100] = 100.5                                    # one 50 bps print that is gone at the next bar
        return p
    d = g.basis_dislocation({'AAA': series(px_hl=spike)})
    assert d['signals'] == 1 and d['mean_net_bps'] == pytest.approx(math.log(1.005) * 1e4 - 21.0)
