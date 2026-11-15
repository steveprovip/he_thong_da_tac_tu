"""AF-1 (2026-11-15): maker conversion of the HWK-1 entries, CEX-DEX arithmetic, Kalshi longshot takers. Synthetic data only."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pytest

from research import asymmetric_frontier_study as g
from research import hawkes_momentum_study as hwk
from research.alternative_alphas import cex_dex
from tests.test_hawkes_momentum import bars, make_mx, shock

ROOT = Path(__file__).resolve().parents[1]
FLAT = (100, 100, 100, 100)


# ------------------------------------------------------------------------------------------------- plan and constants
def test_the_plan_is_the_file_that_was_frozen_and_the_code_runs_its_numbers():
    plan = (ROOT / g.PLAN).read_bytes().replace(b'\r\n', b'\n')
    rows = [json.loads(x) for x in (ROOT / 'docs/research_upgrade/trial_log.jsonl').read_text(encoding='utf-8').splitlines()
            if x.strip()]
    frozen = [r for r in rows if r.get('kind') == 'af1-prereg']
    assert len(frozen) == 1 and frozen[0]['freeze']['sha256_lf'] == hashlib.sha256(plan).hexdigest()
    a, b, c, gr = (frozen[0]['cfg'][k] for k in ('a', 'b', 'c', 'growth'))
    for name, cfg in (('S1', g.S1), ('S2', g.S2)):
        assert {k: getattr(cfg, k) for k in a['signals'][name]} == a['signals'][name]
    assert (list(g.K_BPS), list(g.TTLS), g.SLIP_A, g.VERDICT_SCHEDULE, g.PRIOR_TRIALS) == (
        a['k_bps'], a['ttl'], a['slip'], a['verdict_schedule'], a['prior_trials'])
    assert {k: list(v) for k, v in g.SCHEDULES.items()} == a['schedules']
    assert (g.A_MIN_TRAIN, g.A_MIN_HOLDOUT, g.A_MIN_FRESH) == (a['min_n']['train'], a['min_n']['holdout'], a['min_n']['fresh'])
    assert (g.DEX_Q, list(g.DEX_PHI), list(g.DEX_FC), g.DEX_SLIP, list(g.DEX_GAS), list(g.DEX_TAU), list(g.DEX_PWIN)) == (
        b['q'], b['phi_bps'], b['f_c_bps'], b['slip_bps'], b['gas_u'], b['tau'], b['p_win'])
    assert (g.C_DAY0, {k: list(v) for k, v in g.C_PARTS.items()}) == (c['day0'], c['parts'])
    assert (g.WINDOW_S, g.MINUTE_MULT, g.MAX_PAGES, g.CUTOFF, g.MAX_PRICE, g.STAKE, g.FEE_RATE, g.LONG_H, g.COMBO_PREFIX) == (
        c['window_s'], c['minute_mult'], c['max_pages'], c['cutoff'], c['max_price'], c['stake'], c['fee_rate'], c['long_h'],
        c['combo_prefix'])
    assert {k: list(v) for k, v in g.BUCKETS.items()} == c['buckets'] and list(g.HORIZONS) == c['horizons']
    assert (g.C_MIN_TRAIN, g.C_MIN_TRAIN_WINDOWS, g.C_MIN_HOLDOUT, g.C_MIN_FRESH) == (
        c['min_n']['train'], c['min_n']['train_windows'], c['min_n']['holdout'], c['min_n']['fresh'])
    assert (g.PATHS, g.DAYS, g.SEED, g.EQUITY0, g.HL_MIN_ORDER, g.HL_MAX_LEV, g.FLAT_RISK, list(g.C_STAKES), g.C_BETS_PER_DAY) == (
        gr['paths'], gr['days'], gr['seed'], gr['equity0'], gr['hl_min_order'], gr['hl_max_lev'], gr['flat_risk'],
        gr['c_stakes'], gr['c_bets_per_day'])
    assert frozen[0]['n_trials'] == len(g.K_BPS) * len(g.TTLS) + len(g.BUCKETS) * len(g.HORIZONS) == 18
    assert g.S1.name == 'BAR|hl5|th3|k3|W15|BOTH|ALL|600/240|H240|cd60'      # the HWK-1 T1 cell, unchanged
    assert g.S2.name == 'BAR|hl5|th2|k3|W30|BOTH|ALL|600/240|H120|cd60'      # the HWK-1 T2 final, unchanged


def test_the_venue_schedule_is_a_maker_fee_and_the_directive_rebates_are_labelled_hypothetical():
    assert g.SCHEDULES['HL0'] == (1.5, 4.5)                    # Hyperliquid tier 0: the maker PAYS 1.5 bps
    assert g.SCHEDULES['HL_TOP'][0] == -0.3                    # the largest rebate the venue lists
    assert g.SCHEDULES['DIR15'][0] == -1.5 and g.SCHEDULES['DIR30'][0] == -3.0
    assert g.SCHEDULES['DIR30'][0] / g.SCHEDULES['HL_TOP'][0] == pytest.approx(10.0)


# ------------------------------------------------------------------------------------------------- A: one maker trade
def test_a_maker_order_needs_a_trade_through_and_dies_at_its_ttl():
    o, h, l, c = bars([FLAT, (100, 100.5, 100.0, 100.3), (100.3, 100.4, 99.9, 100.1), (100.1, 100.2, 100.0, 100.1),
                       (100.1, 100.3, 100.05, 100.2), FLAT])
    j, je, gross, why = g.sim_maker(o, h, l, c, 0, 1, 0, 2, 600, 240, 3)
    assert (j, je, why) == (2, 4, hwk.TIME) and gross == pytest.approx(20.0)        # bar 1 only touched 100.0
    j, je, gross, why = g.sim_maker(o, h, l, c, 0, 1, 0, 1, 600, 240, 3)
    assert j == g.UNFILLED and je == 1 and math.isnan(gross)
    assert g.sim_maker(o, h, l, c, 0, 1, 25, 5, 600, 240, 3)[0] == g.UNFILLED       # 99.75 never traded through
    j, _, gross, _ = g.sim_maker(o, h, l, c, 0, 1, 5, 5, 600, 240, 3)               # L = 99.95, filled in bar 2
    assert j == 2 and gross == pytest.approx((100.2 - 99.95) / 99.95 * 1e4)
    assert g.sim_maker(o, h, l, c, 5, 1, 0, 5, 600, 240, 3)[0] == g.UNFILLED        # no bar after the decision
    j, je, _, why = g.sim_maker(o, h, l, c, 0, 1, 0, 2, 600, 240, 240, n_bars=4)
    assert (j, je, why) == (2, 3, hwk.EOD)


def test_maker_fill_bar_counts_for_the_stop_but_never_for_the_target():
    o, h, l, c = bars([FLAT, (100, 107, 99.5, 106), (106, 106.5, 105, 106.2), FLAT])
    assert g.sim_maker(o, h, l, c, 0, 1, 0, 5, 600, 240, 10) == (1, 2, 600.0, hwk.TP)   # +700 in the fill bar: no credit
    o, h, l, c = bars([FLAT, (100, 100.2, 97.0, 97.5), FLAT])
    assert g.sim_maker(o, h, l, c, 0, 1, 0, 5, 600, 240, 10) == (1, 1, -240.0, hwk.STOP)
    o, h, l, c = bars([FLAT, (97.0, 97.2, 96.5, 97.0), FLAT])                       # opens through L and the stop
    j, je, gross, why = g.sim_maker(o, h, l, c, 0, 1, 0, 5, 600, 240, 10)
    assert (j, je, why) == (1, 1, hwk.STOP) and gross == pytest.approx(-300.0)
    o, h, l, c = bars([FLAT, (100, 100.1, 99.8, 100.0), (97, 97.5, 96.8, 97.2), FLAT])
    assert g.sim_maker(o, h, l, c, 0, 1, 0, 5, 600, 240, 10)[2:] == (pytest.approx(-300.0), hwk.STOP)   # later gap
    o, h, l, c = bars([FLAT, (100, 106.5, 97.0, 100), FLAT])
    assert g.sim_maker(o, h, l, c, 0, 1, 0, 5, 600, 240, 10)[2:] == (-240.0, hwk.STOP)                  # both: the stop


def test_maker_short_is_the_mirror_image():
    o, h, l, c = bars([FLAT, (100, 100.1, 99.5, 99.8), (99.8, 100.2, 99.7, 100.0), (100.0, 100.05, 93.9, 94.2), FLAT])
    j, je, gross, why = g.sim_maker(o, h, l, c, 0, -1, 10, 5, 600, 240, 10)         # L = 100.10: bar 2 trades 100.2
    assert (j, je, why) == (2, 3, hwk.TP) and gross == 600.0
    assert g.sim_maker(o, h, l, c, 0, -1, 25, 5, 600, 240, 10)[0] == g.UNFILLED     # 100.25 never traded through
    o, h, l, c = bars([FLAT, (100, 102.6, 99.9, 102.5), FLAT])
    assert g.sim_maker(o, h, l, c, 0, -1, 0, 5, 600, 240, 10)[2:] == (-240.0, hwk.STOP)


# ------------------------------------------------------------------------------------------------- A: triggers -> fills
def test_a_runaway_move_never_fills_the_maker_and_a_pullback_does():
    mx = make_mx(['2025-03-20'], ['AUSDT'], ['G'])
    shock(mx, 0, hwk.L_DAY + 600)                                   # +10 bps a minute: lows never undercut the close
    f = hwk.Features(mx, [0])
    tr = g.run_maker(f, g.S1, 0, 5)
    assert len(tr['s']) >= 2 and not tr['filled'].any() and (np.diff(tr['s']) >= 6).all()   # free again at s + ttl + 1
    assert tr['taker_gross'][0] > 100.0 and math.isnan(tr['gross'][0])     # the taker entry rode what the maker missed
    s = int(tr['s'][0])
    mx['l'][0, s + 1] = mx['c'][0, s] * 0.999                       # a one-bar dip through the limit
    tr2 = g.run_maker(hwk.Features(mx, [0]), g.S1, 0, 5)
    assert tr2['filled'][0] and tr2['j_fill'][0] == s + 1 and tr2['reason'][0] == hwk.TIME
    lim = float(mx['c'][0, s])
    assert tr2['gross'][0] == pytest.approx((float(mx['c'][0, s + 240]) - lim) / lim * 1e4, abs=1e-2)
    assert tr2['j_exit'][0] == s + 240 and len(tr2['s']) == 1       # the cooldown outlasts the shock
    assert tr2['t_entry'][0] == hwk.ms('2025-03-19') + (s + 1) * 60000
    assert not g.run_maker(hwk.Features(mx, [0]), g.S1, 25, 1)['filled'].any()      # 10 bps dip < 25 bps offset


def test_costs_by_schedule_and_the_break_even_maker_fee():
    r = np.array([hwk.STOP, hwk.TP, hwk.TIME, hwk.EOD])
    assert g.maker_cost(r, 'HL0').tolist() == [9.0, 3.0, 9.0, 9.0]
    assert g.maker_cost(r, 'BN0').tolist() == [10.0, 4.0, 10.0, 10.0]
    assert g.maker_cost(r, 'DIR30').tolist() == [4.5, -6.0, 4.5, 4.5]
    assert g.maker_cost(r, 'HL_TOP').tolist() == pytest.approx([5.1, -0.6, 5.1, 5.1])
    assert g.maker_cost(r, 'ZERO').tolist() == [0.0] * 4
    assert g.taker_cost(r, 'HL0').tolist() == [15.0, 9.0, 15.0, 15.0]
    gross, why = np.array([600.0, -240.0, -240.0, -240.0]), np.array([hwk.TP, hwk.STOP, hwk.STOP, hwk.STOP])
    f = g.breakeven_maker_fee(gross, why)
    assert f == pytest.approx(-28.5)                                # a 28.5 bps REBATE would be needed
    net = gross - (f + np.where(why == hwk.TP, f, 4.5 + 3.0))
    assert net.mean() == pytest.approx(0.0, abs=1e-9)
    assert g.breakeven_maker_fee([], []) is None


def test_maker_summary_reports_fills_selection_and_every_schedule():
    day = np.array(['a', 'a', 'b', 'b'])
    tr = {'filled': np.array([True, True, True, False]), 'gross': np.array([600.0, -240.0, -240.0, np.nan]),
          'reason': np.array([hwk.TP, hwk.STOP, hwk.STOP, hwk.EOD]), 'day': day,
          'taker_gross': np.array([550.0, -240.0, -240.0, 600.0]), 'taker_reason': np.array([hwk.TP, hwk.STOP, hwk.STOP, hwk.TP])}
    s = g.maker_summary(tr, 240.0, n_trials=47)
    assert (s['triggers'], s['n'], s['fill_rate']) == (4, 3, 0.75)
    assert s['mean'] == pytest.approx((597.0 - 249.0 - 249.0) / 3) and s['gross'] == pytest.approx(40.0)
    assert s['mean_r'] == pytest.approx(s['mean'] / 240.0) and 'dsr' in s
    assert s['taker_gross_unfilled'] == 600.0 and s['taker_gross_filled'] == pytest.approx(70.0 / 3)
    assert set(s['schedules']) == set(g.SCHEDULES) and s['schedules']['DIR30']['mean'] > s['schedules']['HL0']['mean']
    assert s['net_per_trigger'] == pytest.approx(s['mean'] * 3 / 4)
    assert s['taker_net_hl0']['n'] == 4
    none = g.maker_summary({k: v[3:] for k, v in tr.items()}, 240.0)
    assert none == {'triggers': 1, 'n': 0, 'fill_rate': 0.0}
    assert g.maker_summary({'filled': np.zeros(0, dtype=bool)}, 240.0) == {'triggers': 0, 'n': 0}


def test_cell_choice_and_the_candidate_label_on_reused_partitions():
    table = {'a': {'n': 199, 'mean_r': 0.9}, 'b': {'n': 200, 'mean_r': -0.01}, 'c': {'n': 500, 'mean_r': -0.2}, 'd': {'n': 0}}
    assert g.choose_cell(table) == 'b' and g.choose_cell({'a': {'n': 5, 'mean_r': 1.0}}) is None
    good = {'n': 150, 'lcb95': 1.0, 'dsr': 0.97}
    assert g.candidate(good, {'n': 30, 'mean': 2.0})['verdict'] == 'CANDIDATE'      # T-A: reused HOLDOUT, never EDGE
    assert g.candidate(good, {'n': 30, 'mean': 2.0}, pass_label='EDGE')['verdict'] == 'EDGE'
    assert g.candidate(good, {'n': 5, 'mean': 2.0})['verdict'] == 'UNCONFIRMED'
    assert g.candidate(good, {'n': 30, 'mean': -2.0})['verdict'] == 'NO EDGE'
    assert g.candidate({**good, 'lcb95': -0.1}, {'n': 30, 'mean': 2.0})['verdict'] == 'NO EDGE'
    assert g.candidate({'n': 0}, {'n': 0})['verdict'] == 'NO EDGE'
    assert g.cell_name(0, 5) == 'k0|ttl5' and g.cell_name(25, 15) == 'k25|ttl15'


def test_hyperliquid_coin_names():
    assert g.hl_coin('DOGEUSDT') == 'DOGE' and g.hl_coin('1000PEPEUSDT') == 'kPEPE' and g.hl_coin('1000BONKUSDT') == 'kBONK'
    assert g.hl_coin('1000000MOGUSDT') == '1000000MOG' and g.hl_coin('BTC') == 'BTC'


# ------------------------------------------------------------------------------------------------- B: CEX-DEX
def test_dex_break_even_gap_and_round_arithmetic():
    assert g.dex_breakeven_gap(5, 25, 5, 3, 0.001) == pytest.approx(35.0)          # 33 bps of fees + 2 bps of gas
    assert g.dex_breakeven_gap(5, 25, 5, 3, 2.0) == pytest.approx(4033.0)          # an L1 transaction on a 5 U round
    assert g.dex_breakeven_gap(5, 100, 10, 3, 0.02) == pytest.approx(153.0)
    win = g.dex_round(5, 100, 25, 5, 3, 0.001)
    assert win == pytest.approx(5 * 67e-4 - 0.001) and 20.0 / win == pytest.approx(615.4, abs=0.1)
    assert g.dex_round(5, 100, 25, 5, 3, 0.001, tau=0.5) == pytest.approx(win / 2)
    assert g.dex_round(5, 100, 25, 5, 3, 0.001, p_win=0.1) == pytest.approx(0.1 * win - 0.9 * 0.001)
    assert g.dex_round(5, 100, 25, 5, 3, 0.001, p_win=0.1, gas_fail_u=0.0) == pytest.approx(0.1 * win)
    lose = g.dex_round(5, 20, 25, 5, 3, 0.001, tau=0.9)
    assert lose == pytest.approx(5 * -13e-4 - 0.001)                               # no inclusion share on a loss
    ref = cex_dex.gap_trade(100.0, 101.0, 5.0, lp_fee=.0025, cex_taker=.0005, gas_usd=.001)
    assert g.dex_round(5, 100, 25, 5, 0, 0.001) / 5 * 1e4 == pytest.approx(ref['net_bps'])
    t = g.dex_table()
    assert len(t['breakeven_gap_bps']) == 4 * 2 * 3 and len(t['rounds']) == 3 * 3 * 3 * 3 and t['q'] == 5.0
    assert all((r['rounds_per_hour_for_target'] is None) == (r['net_u'] <= 0) for r in t['rounds'])
    assert min(r['rounds_per_hour_for_target'] for r in t['rounds'] if r['net_u'] > 0) > 200   # even at a 2 % gap


# ------------------------------------------------------------------------------------------------- C: sample and fetch
def test_the_frozen_sample_windows():
    ws = g.windows()
    by = {p: [w for w in ws if w['part'] == p] for p in g.C_PARTS}
    assert (len(by['TRAIN']), len(by['HOLDOUT']), len(by['FRESH'])) == (122, 91, 22)
    assert len({w['day'] for w in ws}) == len(ws) == 235 and max(w['day'] for w in ws) == '2026-11-12'
    for w in ws:
        assert w['end'] - w['start'] == 300 and (w['start'] - g.day_ts(w['day'])) == ((397 * w['n']) % 1440) * 60
        assert w['endpoint'] == ('/historical/trades' if w['end'] < g.day_ts('2026-09-15') else '/markets/trades')
    assert by['TRAIN'][0]['n'] == 0 and by['TRAIN'][1]['n'] == 3 and by['FRESH'][1]['n'] - by['FRESH'][0]['n'] == 2
    hours = {(w['start'] - g.day_ts(w['day'])) // 3600 for w in ws}
    assert len(hours) == 24                                         # the start minute rotates through the day
    assert g.iso_ts('2025-06-10T15:59:58.821341Z') == pytest.approx(g.day_ts('2025-06-10') + 15 * 3600 + 59 * 60 + 58.821341)
    assert g.iso_ts('2025-06-10T00:00:00Z') == g.day_ts('2025-06-10')


def api_trade(ticker, side, yes, n=10, t='2025-06-10T15:00:01Z'):
    return {'ticker': ticker, 'taker_side': side, 'yes_price_dollars': f'{yes:.4f}', 'no_price_dollars': f'{1 - yes:.4f}',
            'count_fp': f'{n:.2f}', 'created_time': t}


def test_taker_price_and_window_fetch_keep_only_cheap_taker_buys():
    assert g.taker_price(api_trade('A', 'yes', 0.05)) == ('yes', 0.05)
    assert g.taker_price(api_trade('A', 'no', 0.95)) == ('no', pytest.approx(0.05))
    assert g.taker_price({'taker_outcome_side': 'no', 'yes_price_dollars': '0.9900', 'no_price_dollars': '0.0100'}) == ('no', 0.01)
    pages = {'': {'trades': [api_trade('A', 'yes', 0.02), api_trade('B', 'yes', 0.40), api_trade('C', 'no', 0.99)], 'cursor': 'x'},
             'x': {'trades': [api_trade('D', 'no', 0.50), api_trade('E', 'yes', 0.10), api_trade('F', 'yes', 0.11)], 'cursor': ''}}
    calls = []

    def get(url):
        calls.append(url)
        return pages[url.split('cursor=')[1] if 'cursor=' in url else '']

    w = {'part': 'TRAIN', 'day': '2025-06-10', 'n': 160, 'start': 100, 'end': 400, 'endpoint': '/historical/trades'}
    res = g.fetch_window(w, get)
    assert (res['n_all'], res['pages'], res['truncated']) == (6, 2, False)
    assert [(r[0], r[1], round(r[2], 4), r[3]) for r in res['rows']] == [('A', 'yes', 0.02, 10.0), ('C', 'no', 0.01, 10.0),
                                                                           ('E', 'yes', 0.10, 10.0)]
    assert '/historical/trades?limit=1000&min_ts=100&max_ts=400' in calls[0] and calls[1].endswith('&cursor=x')
    cut = g.fetch_window(w, get, max_pages=1)
    assert cut['truncated'] and cut['pages'] == 1 and len(cut['rows']) == 2
    assert g.fetch_window(w, lambda url: None)['n_all'] == 0


def test_fetch_trades_resumes_and_markets_fall_back_to_the_live_endpoint(tmp_path, monkeypatch):
    ws = [{'part': 'TRAIN', 'day': '2025-01-01', 'n': 0, 'start': 0, 'end': 300, 'endpoint': '/historical/trades'},
          {'part': 'FRESH', 'day': '2026-10-01', 'n': 638, 'start': 900, 'end': 1200, 'endpoint': '/markets/trades'}]
    monkeypatch.setattr(g, 'windows', lambda: ws)
    seen = []

    def get(url):
        seen.append(url)
        return {'trades': [api_trade('A', 'yes', 0.01)], 'cursor': ''}

    out = tmp_path / 'trades'
    assert g.fetch_trades(out, workers=1, get=get, log=lambda *_: None)['done'] == 2
    assert sorted(p.name for p in out.glob('*.json')) == ['2025-01-01.json', '2026-10-01.json']
    assert any('/markets/trades' in u for u in seen) and any('/historical/trades' in u for u in seen)
    assert g.fetch_trades(out, workers=1, get=get, log=lambda *_: None)['done'] == 0           # nothing fetched twice
    assert len(g.load_windows(out)) == 2

    def mget(url):
        if '/historical/markets' in url:
            return {'markets': [{'ticker': 'A', 'result': 'no', 'event_ticker': 'EA', 'status': 'finalized',
                                 'close_time': '2025-01-02T00:00:00Z'}]}
        assert 'tickers=B,C' in url                                 # only what the historical endpoint did not have
        return {'markets': [{'ticker': 'B', 'result': '', 'event_ticker': 'EB', 'status': 'active', 'close_time': None}]}

    mk = g.fetch_markets({'A', 'B', 'C'}, tmp_path / 'm.json', get=mget, log=lambda *_: None)
    assert mk['A'] == {'result': 'no', 'event': 'EA', 'status': 'finalized', 'close_ts': g.day_ts('2025-01-02')}
    assert mk['B']['result'] == '' and mk['C']['status'] == 'not_found'
    assert g.fetch_markets({'A', 'B', 'C'}, tmp_path / 'm.json', get=lambda url: 1 / 0, log=lambda *_: None) == mk


# ------------------------------------------------------------------------------------------------- C: returns
def test_fee_rounds_up_to_the_cent_and_the_stake_buys_whole_contracts():
    assert g.fee_cents_up(100, 0.01) == 0.07 and g.fee_cents_up(50, 0.02) == 0.07       # 7 % of a 1 U stake
    assert g.fee_cents_up(5, 0.01) == 0.01                                             # 20 % of a 0.05 U stake
    assert g.fee_cents_up(1, 0.50) == 0.02
    pnl, outlay = g.stake_pnl(0.01, True)
    assert (pnl, outlay) == (pytest.approx(100 - 1 - 0.07), pytest.approx(1.07))
    assert g.stake_pnl(0.01, False) == (pytest.approx(-1.07), pytest.approx(1.07))
    assert g.stake_pnl(0.03, True) == (pytest.approx(33 - 0.99 - 0.07), pytest.approx(1.06))   # 33 contracts
    assert g.stake_pnl(0.01, False, stake=0.05) == (pytest.approx(-0.06), pytest.approx(0.06))
    assert g.stake_pnl(0.10, True, stake=0.05) is None                                 # the stake buys no contract
    assert g.stake_pnl(0.02, True, rate=0.0) == (pytest.approx(49.0), pytest.approx(1.0))
    # break-even win probability at 1 cent with the fee: 1.07 / 100
    assert 1.07 / 100 > 0.01


def kalshi_fixture():
    """Three windows; market M* resolve, U is open, X is unknown, KXMVE* is a combo."""
    def win(day, part, rows):
        return {'part': part, 'day': day, 'n': 0, 'start': 0, 'end': 300, 'endpoint': '', 'n_all': len(rows) + 5, 'pages': 1,
                'truncated': day == '2026-10-01', 'rows': rows}
    ts = float(g.day_ts('2025-01-01'))
    wins = [win('2025-01-01', 'TRAIN', [['M1', 'yes', 0.01, 10.0, ts], ['M2', 'no', 0.02, 4.0, ts], ['M3', 'yes', 0.05, 1.0, ts],
                                         ['U', 'yes', 0.01, 1.0, ts], ['X', 'yes', 0.01, 1.0, ts], ['KXMVE-1', 'yes', 0.01, 2.0, ts]]),
            win('2026-01-01', 'HOLDOUT', [['M1', 'yes', 0.02, 5.0, ts], ['M2', 'no', 0.01, 5.0, ts]]),
            win('2026-10-01', 'FRESH', [['M3', 'no', 0.01, 5.0, ts]])]
    markets = {'M1': {'result': 'yes', 'event': 'E1', 'status': 'finalized', 'close_ts': ts + 2 * 86400},
               'M2': {'result': 'yes', 'event': 'E2', 'status': 'finalized', 'close_ts': ts + 3600},
               'M3': {'result': 'no', 'event': 'E3', 'status': 'finalized', 'close_ts': ts + 30 * 86400},
               'U': {'result': '', 'event': 'EU', 'status': 'active', 'close_ts': None},
               'X': {'result': '', 'event': '', 'status': 'not_found', 'close_ts': None},
               'KXMVE-1': {'result': 'no', 'event': 'EM', 'status': 'finalized', 'close_ts': ts + 600}}
    return wins, markets


def test_trade_table_joins_results_and_counts_what_is_left_out():
    wins, markets = kalshi_fixture()
    t = g.trade_table(wins, markets)
    assert t['left'] == {'unresolved': 1, 'not_found': 1, 'kept': 9, 'all_trades': 24, 'windows': 3, 'truncated_windows': 1}
    assert len(t['p']) == 7 and t['combo'].sum() == 1
    assert t['win'].tolist() == [True, False, False, False, True, False, True]      # M1 yes won; M2 / M3 no-takers: M3 won
    assert t['r'][0] == pytest.approx((100 - 1 - 0.07) / 1.07) and t['r'][1] == -1.0 and t['r0'][0] == pytest.approx(99.0)
    assert t['hours'][0] == pytest.approx(48.0) and t['hours'][1] == pytest.approx(1.0)
    m = g.cell_mask(t, 'P1', 'ALL')
    assert m.tolist() == [True, False, False, False, False, True, True]             # 1 cent, single-event markets
    assert g.cell_mask(t, 'P2', 'ALL').sum() == 2 and g.cell_mask(t, 'P12', 'ALL').sum() == 5
    assert g.cell_mask(t, 'P12', 'LONG').tolist() == [True, False, False, False, True, False, True]
    assert g.cell_mask(t, 'P12', 'SHORT').sum() == 2 and g.cell_mask(t, 'P1', 'ALL', combo=True).sum() == 1
    with pytest.raises(ValueError):
        g.cell_mask(t, 'P1', 'NEXT')


def test_cell_statistics_calibration_and_the_sellers_side():
    wins, markets = kalshi_fixture()
    t = g.trade_table(wins, markets)
    s = g.c_stats(t, g.cell_mask(t, 'P12', 'ALL'), n_trials=9)
    assert s['n'] == 5 and s['windows'] == 3 and s['win_rate'] == 0.6 and s['mean_price'] == pytest.approx(0.014)
    assert s['win_over_price'] == pytest.approx(0.6 / 0.014) and s['mean_r_no_fee'] > s['mean'] and 'dsr' in s
    assert s['yes_share'] == 0.4
    # the seller of a 1 cent contract that lost keeps 1 cent on 99 cents of collateral
    lose = g.c_stats(t, g.cell_mask(t, 'P1', 'ALL', combo=True))
    assert lose['seller_r'] == pytest.approx(0.01 / 0.99) and lose['mean'] == -1.0 and lose['contract_weighted_r'] == -1.0
    assert g.c_stats(t, np.zeros(len(t['p']), dtype=bool)) == {'n': 0}
    cells = g.c_cells(t, 'TRAIN')
    assert set(cells) == {f'{b}|{h}' for b in g.BUCKETS for h in g.HORIZONS} and cells['P12|ALL']['n'] == 2
    cal = g.calibration(t, 'TRAIN')
    assert list(cal) == ['0-0.015', '0.015-0.025', '0.025-0.05', '0.05-0.1'] and cal['0.025-0.05']['n'] == 1
    assert g.calibration(t, 'TRAIN', combo=True)['0-0.015']['n'] == 1
    assert g.choose_c({'a': {'n': 200, 'windows': 19, 'mean': 5.0}, 'b': {'n': 200, 'windows': 20, 'mean': -0.4},
                       'c': {'n': 900, 'windows': 60, 'mean': -0.6}}) == 'b'
    assert g.choose_c({'a': {'n': 199, 'windows': 50, 'mean': 5.0}}) is None


# ------------------------------------------------------------------------------------------------- growth
def test_growth_of_the_maker_book_sizes_by_the_minimum_order_or_one_u_at_the_stop():
    lose = [[-249.0]]                                               # one stop-out a day under HL0
    mn = g.growth_a(lose, 'MIN', 240.0, paths=3, days=4)
    assert mn['median'] == pytest.approx(10 - 4 * 10 * 0.0249) and mn['p_ruin'] == 0.0 and mn['mean_trades'] == 4
    fl = g.growth_a(lose, 'FLAT', 240.0, paths=3, days=2)
    assert fl['median'] == pytest.approx(10 - 2 * (1 / 0.024) * 0.0249)
    long_run = g.growth_a(lose, 'FLAT', 240.0, paths=3, days=30)
    assert long_run['p_ruin'] == 1.0 and long_run['median'] < g.HL_MIN_ORDER / g.HL_MAX_LEV   # cannot margin 10 U at 5x
    win = g.growth_a([[591.0], []], 'MIN', 240.0, paths=200, days=30)
    assert win['p_profit'] > 0.99 and win['p_pace_20u_per_hour'] == 0.0 and 10.0 < win['median'] < 30.0
    assert g.growth_a([], 'MIN', 240.0) == {'paths': 0}
    assert g.HL_MIN_ORDER * 240.0 * 1e-4 == pytest.approx(0.24)     # the minimum order's loss at this stop: 4.8x the cap


def test_growth_of_the_longshot_book_stops_when_a_stake_cannot_be_funded():
    lose = [[(0.01, False)] * 5]
    r = g.growth_c(lose, 1.0, paths=5, days=30)
    assert r['p_ruin'] == 1.0 and r['mean_trades'] == 9 and r['median'] == pytest.approx(10 - 9 * 1.07)
    small = g.growth_c(lose, 0.05, paths=5, days=30)
    assert small['p_ruin'] == 0.0 and small['median'] == pytest.approx(10 - 150 * 0.06) and small['mean_trades'] == 150
    hit = g.growth_c([[(0.01, True)]], 1.0, paths=5, days=1)
    assert hit['median'] == pytest.approx(10 + 100 - 1.07) and hit['p_double'] == 1.0
    mixed = g.growth_c([[(0.01, False)] * 3, []], 1.0, paths=50, days=10, seed=3)
    assert mixed == g.growth_c([[(0.01, False)] * 3, []], 1.0, paths=50, days=10, seed=3) and 0 < mixed['mean_trades'] < 10
    assert g.growth_c([], 1.0) == {'paths': 0}
    assert g.growth_c([[(0.10, True)]], 0.05, paths=2, days=3)['mean_trades'] == 0     # 0.05 U buys no 10 cent contract


# ------------------------------------------------------------------------------------------------- end to end
def test_run_end_to_end_on_synthetic_inputs(tmp_path, monkeypatch):
    days = ['2025-03-20', '2025-03-21', '2025-04-02', '2025-05-07', '2026-02-03', '2026-02-04', '2026-03-09', '2026-10-05']
    mx = make_mx(days, [f'S{k}USDT' for k in range(len(days))], ['G', 'L'] * 4)
    for p in range(len(days)):
        a = hwk.L_DAY + 300 + 100 * p
        shock(mx, p, a, side=1 if p % 2 == 0 else -1)
        mx['l'][p, a:a + 30] *= 0.9995                              # pullbacks, so that makers fill
        mx['h'][p, a:a + 30] *= 1.0005
    np.savez(tmp_path / 'matrix.npz', **mx)
    wins, markets = kalshi_fixture()
    (tmp_path / 'trades').mkdir()
    for w in wins:
        (tmp_path / 'trades' / f"{w['day']}.json").write_text(json.dumps(w))
    (tmp_path / 'm.json').write_text(json.dumps(markets))
    for name in ('A_MIN_TRAIN', 'C_MIN_TRAIN', 'C_MIN_TRAIN_WINDOWS'):
        monkeypatch.setattr(g, name, 1)
    monkeypatch.setattr(g, 'PATHS', 20)
    res = g.run(log=lambda *_: None, matrix=tmp_path / 'matrix.npz', kalshi_src=tmp_path / 'trades',
                kalshi_markets=tmp_path / 'm.json', hl_universe={'S4', 'S6'})
    a, c = res['a'], res['c']
    assert len(a['train']) == 9 and a['cell'] in a['train'] and a['n_trials'] == 47 and a['verdict'] == 'NO EDGE'
    assert a['checks']['holdout_n>=100'] is False and set(a['holdout']['schedules']) == set(g.SCHEDULES)
    assert a['holdout_hl_listed']['share_of_triggers'] is not None and a['growth']['min_loss_at_stop_u'] == pytest.approx(0.24)
    assert len(res['b']['rounds']) == 81 and res['fee_schedules_bps']['HL0'] == {'maker': 1.5, 'taker': 4.5}
    assert c['cell'] in c['train'] and c['n_trials'] == 9 and c['verdict'] == 'NO EDGE' and c['sample']['unresolved'] == 1
    assert set(c['growth']) >= {'STAKE_1U', 'STAKE_0.05U'} and set(c['calibration']) == set(g.C_PARTS)
    json.dumps(res, default=float)
    empty = g.run_c(lambda *_: None, tmp_path / 'none', tmp_path / 'none.json')
    assert empty['verdict'] == 'NOT RUN'
