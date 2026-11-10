"""11-09f profit-acceleration directive (docs/harness/plan_micro_profit_scale.md): 3 concurrent micro positions (one per coin) inside
the 0.15 U daily-kill budget, a PnL-linked lot, a bankroll knob that honours values above 15 U, the soft 5 m macro in the chop
harvester lane, and the simulated funding-carry yield in the telemetry.  Mock venue, synthetic books: wiring and arithmetic only,
nothing here shows that more positions or larger lots make money."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from config.settings import micro_capital_env
from core import engine as eng
from core import funding_executor as fx
from core import invariants as inv
from core.position_sizer import MicroCapitalSizer
from research import carry_lane as cl
from tests.test_micro_rotation import ADA, BUY, DOGE, PEPE, PX, SELL, XRP, burst_engine, doge_micro, rows, scanner

FEES = 0.08


def peer(symbol, risk=0.0116, margin=1.02):
    """An engine that holds a position, as slot_block / portfolio_check see it."""
    return SimpleNamespace(pos={'x': 1}, busy=False, view={}, risk_at_stop=lambda: risk, margin_used=lambda: margin,
                           p=SimpleNamespace(symbol=symbol))


def held(e, qty=72.0, entry=0.0711, sl=0.0709):
    e.pos = {'side': 'BUY', 'qty': qty, 'entry': entry, 'sl': sl}


# ============================================================================= pillar 1: three slots, one per coin
def test_the_demo_profile_has_three_slots_and_live_stays_at_one_with_no_acceleration():
    d, live = eng.PROFILES['demo'], eng.PROFILES['live']
    assert (d.micro_max_positions, d.micro_accel_k) == (3, 5.0) and (live.micro_max_positions, live.micro_accel_k) == (1, 0.0)
    assert inv.check_profile(d) == [] and inv.check_profile(live) == []
    for kw, word in (({'micro_max_positions': 2}, 'micro_max_positions'), ({'micro_accel_k': 1.0}, 'micro_accel_k')):
        errs = inv.check_profile(replace(live, **kw))
        assert len(errs) == 1 and word in errs[0]


def test_three_coins_hold_at_once_the_fourth_waits_and_each_coin_has_one(tmp_path):
    es, _ = scanner(tmp_path)
    assert es[DOGE].max_positions_eff() == 3 and es[DOGE].slot_block() is None
    held(es[DOGE])
    assert es[XRP].slot_block() is None and es[DOGE].open_peers() == []        # a coin does not count itself
    held(es[XRP], 4.6, 1.1113, 1.109)
    assert es[ADA].slot_block() is None and es[PEPE].slot_block() is None
    held(es[ADA], 12.0, 0.42, 0.419)
    assert es[PEPE].slot_block() == 'PORTFOLIO FULL 3/3'
    assert es[DOGE].slot_block() is None and es[PEPE].open_peers() != []
    # one position per symbol: a coin that already holds refuses a second entry
    assert es[DOGE].pos is not None


def held_side(e, side, qty=72.0, entry=0.0711, sl=0.0709):
    e.pos = {'side': side, 'qty': qty, 'entry': entry, 'sl': sl}


def test_at_most_two_positions_on_one_side_across_the_three_slots(tmp_path):
    es, _ = scanner(tmp_path)
    assert es[DOGE].p.micro_max_same_side == 2 and eng.PROFILES['live'].micro_max_same_side == 2
    held_side(es[DOGE], 'BUY')
    assert es[XRP].slot_block('BUY') is None and es[XRP].slot_block('SELL') is None        # one BUY open: a second BUY fits
    held_side(es[XRP], 'BUY', 4.6, 1.1113, 1.109)
    assert es[ADA].slot_block('BUY') == 'PORTFOLIO SIDE 2/2 BUY already open (correlated coins)'
    assert es[ADA].slot_block('SELL') is None                                              # the other side is free
    assert es[ADA].slot_block() is None                                                    # no side asked (display): count rule only
    held_side(es[ADA], 'SELL', 12.0, 0.42, 0.421)
    assert es[PEPE].slot_block('SELL') == 'PORTFOLIO FULL 3/3'                             # the slot count still comes first
    # a SELL book is symmetric
    held_side(es[DOGE], 'SELL'), held_side(es[XRP], 'SELL', 4.6, 1.1113, 1.1135), held_side(es[ADA], 'BUY', 12.0, 0.42, 0.419)
    es[ADA].pos = None
    assert es[ADA].slot_block('SELL') == 'PORTFOLIO SIDE 2/2 SELL already open (correlated coins)' and es[ADA].slot_block('BUY') is None


def test_an_unknown_or_in_flight_side_counts_against_the_cap(tmp_path):
    es, _ = scanner(tmp_path)
    es[DOGE].busy, es[DOGE]._xm = True, {'side': 'BUY'}                                    # resting entry, side from the order
    es[XRP].busy, es[XRP]._xm = True, None                                                 # in flight, side not recorded yet
    assert es[DOGE].open_side() == 'BUY' and es[XRP].open_side() is None
    assert es[ADA].slot_block('BUY') == 'PORTFOLIO SIDE 2/2 BUY already open (correlated coins)'
    assert es[ADA].slot_block('SELL') is None                                              # 1 unknown < 2 on the SELL side: fits
    es[DOGE].busy, es[DOGE]._xm, es[XRP].busy = False, None, False
    es[PEPE].view = {'amt': -3000.0}                                                       # a venue position seen only by the sync
    assert es[PEPE].open_side() == 'SELL' and es[ADA].slot_block('BUY') is None


def test_the_same_side_cap_is_off_when_asked_or_not_a_micro_account_or_single_slot(tmp_path):
    off, _ = scanner(tmp_path / 'off', micro_max_same_side=0)
    held_side(off[DOGE], 'BUY'), held_side(off[XRP], 'BUY', 4.6, 1.1113, 1.109)
    assert off[ADA].slot_block('BUY') is None                                              # cap 0 = off: 3 BUYs may open
    one, _ = scanner(tmp_path / 'one', micro_max_positions=1)
    held_side(one[DOGE], 'BUY')
    assert one[ADA].slot_block('BUY') == 'PORTFOLIO FULL 1/1'                              # single slot: the count rule answers
    big = burst_engine(tmp_path / 'big', wallet=5000.0)                                    # not a micro account
    big.risk.engines = [big, SimpleNamespace(pos={'side': 'BUY', 'qty': 1, 'entry': 1, 'sl': 1}, busy=False, view={},
                                             open_side=lambda: 'BUY', risk_at_stop=lambda: 0.0, margin_used=lambda: 0.0,
                                             p=SimpleNamespace(symbol=XRP)),
                        SimpleNamespace(pos={'side': 'BUY', 'qty': 1, 'entry': 1, 'sl': 1}, busy=False, view={},
                                        open_side=lambda: 'BUY', risk_at_stop=lambda: 0.0, margin_used=lambda: 0.0,
                                        p=SimpleNamespace(symbol=ADA))]
    assert big.slot_block('BUY') is None


async def test_the_entry_is_refused_before_any_order_when_the_side_is_full(tmp_path):
    e, venue, feed = await doge_micro(tmp_path, micro_max_positions=3)
    e.risk.engines = [e, SimpleNamespace(pos={'side': 'BUY'}, busy=False, view={}, open_side=lambda: 'BUY', risk_at_stop=lambda: 0.01,
                                         margin_used=lambda: 1.0, p=SimpleNamespace(symbol=XRP)),
                      SimpleNamespace(pos={'side': 'BUY'}, busy=False, view={}, open_side=lambda: 'BUY', risk_at_stop=lambda: 0.01,
                                      margin_used=lambda: 1.0, p=SimpleNamespace(symbol=ADA))]
    res = await e.execute('BUY', BUY, 'SWARM')
    assert not res['ok'] and res['error'] == 'SCANNER: PORTFOLIO SIDE 2/2 BUY already open (correlated coins)'
    assert not venue.orders and not [r for r in rows(tmp_path) if r.get('qty_req')]


async def test_a_coin_that_already_holds_refuses_a_second_entry(tmp_path):
    e, venue, feed = await doge_micro(tmp_path)
    held(e)
    res = await e.execute('BUY', BUY, 'SWARM')
    assert not res['ok'] and 'position' in res['error']


def test_the_money_at_all_stops_stays_inside_the_daily_kill(tmp_path):
    e = burst_engine(tmp_path, micro_max_positions=3)
    assert e.risk_cap() == pytest.approx(0.15)                                  # micro_daily_kill_usdt, not portfolio_risk_usdt 5.0
    e.risk.engines = [e, peer(XRP, 0.05), peer(ADA, 0.05)]
    assert e.portfolio_check({'loss_at_sl': 0.05, 'margin': 1.02}) is None       # 3 x the 0.05 U cap = exactly the kill
    assert e.portfolio_check({'loss_at_sl': 0.0501, 'margin': 1.02}).startswith('PORTFOLIO RISK')
    e.day_pnl = -0.05                                                           # the day already lost a third of the budget
    assert e.risk_cap() == pytest.approx(0.10)
    assert e.portfolio_check({'loss_at_sl': 0.05, 'margin': 1.02}).startswith('PORTFOLIO RISK 0.150 U > 0.1 U')
    e.day_pnl = -0.15
    assert e.risk_cap() == 0.0
    off = burst_engine(tmp_path / 'b', wallet=5000.0, micro_capital='off')
    assert off.risk_cap() == off.p.portfolio_risk_usdt                          # not a micro account: unchanged


@pytest.mark.parametrize('wallet', [5.0, 5000.0])
def test_the_margin_of_three_lots_fits_the_rehearsed_bankroll_not_the_demo_wallet(tmp_path, wallet):
    e = burst_engine(tmp_path, wallet=wallet, micro_capital='on', micro_max_positions=3)
    assert e.micro_equity() == 5.0                                              # 5 U bankroll, whatever the wallet shows
    e.risk.engines = [e, peer(XRP), peer(ADA)]
    assert e.portfolio_check({'loss_at_sl': 0.012, 'margin': 1.02}) is None     # 3 x 1.02 = 3.06 U <= 0.85 x 5 = 4.25 U
    e.risk.engines = [e, peer(XRP, margin=1.2), peer(ADA, margin=1.2)]
    assert e.portfolio_check({'loss_at_sl': 0.012, 'margin': 1.21}).startswith('PORTFOLIO MARGIN 3.61 U > 3.60 U')


def test_the_best_candidate_takes_a_slot_only_when_the_better_ones_fill_every_free_slot(tmp_path):
    es, t = scanner(tmp_path)
    for s, q in ((DOGE, 0.50), (XRP, 0.90), (ADA, 0.80), (PEPE, 0.70)):
        es[s].candidate = (t[0], q)
    assert es[DOGE].better_peer(0.50)[0].p.symbol == XRP                        # 3 better candidates fill the 3 free slots
    assert es[PEPE].better_peer(0.70) is None and es[ADA].better_peer(0.80) is None     # top 3 all trade
    held(es[ADA])
    es[ADA].candidate = None                                                    # ADA holds: 2 free slots
    assert es[DOGE].better_peer(0.50)[0].p.symbol == XRP                        # XRP and PEPE are ahead = 2 free slots
    assert es[PEPE].better_peer(0.70) is None
    one, t1 = scanner(tmp_path / 'b', micro_max_positions=1)                    # the old single-slot rule is unchanged
    for s, q in ((DOGE, 0.50), (XRP, 0.90), (ADA, 0.80), (PEPE, 0.70)):
        one[s].candidate = (t1[0], q)
    assert one[ADA].better_peer(0.80)[0].p.symbol == XRP and one[XRP].better_peer(0.90) is None


def test_env_flags_for_slots_acceleration_and_bankroll_alias():
    assert micro_capital_env({'MICRO_MAX_POSITIONS': '3', 'MICRO_ACCEL_K': '2.5'}) == {'micro_max_positions': 3, 'micro_accel_k': 2.5}
    assert micro_capital_env({'MICRO_MAX_SAME_SIDE': '0'}) == {'micro_max_same_side': 0}
    with pytest.raises(ValueError, match='MICRO_MAX_SAME_SIDE'):
        micro_capital_env({'MICRO_MAX_SAME_SIDE': '4'})
    assert micro_capital_env({'MICRO_BANKROLL_DEMO': '25'}) == {'micro_bankroll': 25.0}
    assert micro_capital_env({'MICRO_CAPITAL_BANKROLL': '25', 'MICRO_BANKROLL_DEMO': '25'}) == {'micro_bankroll': 25.0}
    for env, word in (({'MICRO_MAX_POSITIONS': '4'}, 'MICRO_MAX_POSITIONS'), ({'MICRO_MAX_POSITIONS': '0'}, 'MICRO_MAX_POSITIONS'),
                      ({'MICRO_ACCEL_K': '-1'}, 'MICRO_ACCEL_K'), ({'MICRO_ACCEL_K': 'x'}, 'MICRO_ACCEL_K'),
                      ({'MICRO_BANKROLL_DEMO': '0'}, 'MICRO_BANKROLL_DEMO'),
                      ({'MICRO_CAPITAL_BANKROLL': '5', 'MICRO_BANKROLL_DEMO': '25'}, 'disagree')):
        with pytest.raises(ValueError, match=word):
            micro_capital_env(env)


# ============================================================================= pillar 2: PnL-linked lot
def test_the_accelerated_target_curve():
    s = MicroCapitalSizer(max_positions=3, accel_k=5.0)
    t = lambda e, pnl: s.compound_target(e, 5.0, pnl)
    assert t(5.0, 0.0) == pytest.approx(5.0) and t(5.0, None) == pytest.approx(5.0)          # no gain, no acceleration
    assert t(5.1, 0.1) == pytest.approx(7.225)                                  # 5.1 x 1.5 = 7.65 -> capped at 0.85 x 5.1 x 5 / 3
    assert t(5.1, 0.1) > s.compound_target(5.1, 5.0) * 1.4                      # +2 % equity -> +42 % lot
    assert t(6.0, 1.0) == pytest.approx(0.85 * 6.0 * 5.0 / 3)                   # the slot's margin share is the ceiling
    assert t(4.9, -0.1) == pytest.approx(4.9 * 0.5) and t(4.8, -0.2) == 0.0     # a realized loss shrinks the lot, to 0 at -1/k
    assert t(10.0, 0.0) == pytest.approx(10.0)
    assert MicroCapitalSizer(max_positions=3).compound_target(5.1, 5.0, 0.1) == pytest.approx(5.1)   # k = 0: off
    assert MicroCapitalSizer(max_positions=1, accel_k=5.0).compound_target(5.1, 5.0, 0.1) == pytest.approx(7.65)
    with pytest.raises(ValueError):
        MicroCapitalSizer(accel_k=-1.0)


def test_the_lot_never_leaves_the_0_05_u_loss_ceiling_whatever_the_bankroll():
    s = MicroCapitalSizer(max_positions=3, accel_k=5.0)
    for sl in (0.15, 0.22):
        ceiling = s.risk_notional(sl, FEES)                                     # 21.7 U / 16.7 U
        for equity, pnl in ((5.0, 0.0), (5.5, 0.5), (25.0, 0.0), (25.0, 2.0), (50.0, 5.0)):
            q, why = s.qty(0.0711, 1.0, 1.0, 5.0, equity=equity, leverage=5.0, risk_notional=ceiling, pnl=pnl)
            assert q * 0.0711 <= ceiling + 1e-9 and s.loss_at_stop(q * 0.0711, sl, FEES) <= 0.05 + 1e-9, (sl, equity, pnl)
    q25, _ = s.qty(0.0711, 1.0, 1.0, 5.0, equity=25.0, leverage=5.0, risk_notional=s.risk_notional(0.15, FEES), pnl=0.0)
    assert q25 * 0.0711 == pytest.approx(21.7, abs=0.1)                         # a 25 U bankroll is loss-capped at 21.7 U, not 25
    assert 21.7 * 0.0011 == pytest.approx(0.0239, abs=1e-4)                     # net of one harvester TP at that lot (< the +0.03-0.08 asked)


def test_a_bankroll_above_15_u_is_honoured_and_pnl_compounds_on_top_of_it(tmp_path):
    e = burst_engine(tmp_path, wallet=5000.0, micro_capital='on', micro_bankroll=25.0)
    assert e.micro_equity() == 25.0                                             # it was clipped to micro_equity_max (15) by 11-09e
    e.all_rows = [{'pnl': 1.0, 'micro': True}, {'pnl': 0.5, 'micro': True}, {'pnl': 7.0, 'micro': False}, {'pnl': None, 'micro': True}]
    assert e.micro_pnl() == pytest.approx(1.5) and e.micro_equity() == 25.0     # 26.5 capped at max(15, bankroll)
    small = burst_engine(tmp_path / 'b', wallet=5000.0, micro_capital='on')
    small.all_rows = [{'pnl': 40.0, 'micro': True}]
    assert small.micro_equity() == 15.0                                         # the default bankroll still ends at 15 U


def test_the_engine_passes_the_realized_pnl_to_the_window(tmp_path):
    e = burst_engine(tmp_path, wallet=5000.0, micro_capital='on')
    assert e.micro_window_hi() == pytest.approx(6.0)                            # E = 5, no pnl: the static window
    e.all_rows = [{'pnl': 0.1, 'micro': True}]
    assert e.micro_equity() == pytest.approx(5.1) and e.micro_window_hi() == pytest.approx(7.225)
    off = burst_engine(tmp_path / 'b', wallet=5000.0, micro_capital='on', micro_accel_k=0.0)
    off.all_rows = [{'pnl': 0.1, 'micro': True}]
    assert off.micro_window_hi() == pytest.approx(6.0)
    e.all_rows = [{'pnl': -0.1, 'micro': True}]
    assert e.micro_window_hi() == pytest.approx(6.0)                            # a loss never opens the window


async def test_a_won_tenth_of_a_usdt_raises_the_order_size_inside_the_window(tmp_path):
    async def lot(accel_k, root):
        e, venue, feed = await doge_micro(tmp_path / root, wallet=5.0, micro_ttl=False, gtx_ttl_s=0.2, micro_accel_k=accel_k)
        e.all_rows.append({'pnl': 0.1, 'micro': True, 'tag': 'AUTO', 't': 1.0})
        await e.execute('BUY', BUY, 'SWARM')
        [r] = [x for x in rows(tmp_path / root) if x.get('qty_req')]
        return r['qty_req']
    plain, fast = await lot(0.0, 'a'), await lot(5.0, 'b')
    assert plain * PX == pytest.approx(5.1, abs=0.1)                            # the smallest legal lot
    assert fast > plain * 1.3 and fast * PX <= 0.85 * 5.0 * 5 / 3 + 1e-6        # +42 % at most, inside one slot's margin share


# ============================================================================= pillar 3: soft 5 m macro in the chop lane
CHOP = {'ker': 0.20, 'spread_bps': 0.5, 'atr_pct': 0.10, 'cvd_3m': 0.0, 'px_vel_60s': 0.0}
LONG_ONLY = {'name': 'LONG ONLY', 'allow': ('BUY',), 'why': 'giá > VWAP5m · EMA20 dốc'}


LOCK = {'scalp_macro_lock': True}        # tests/conftest.py switches the shipped 11-08 locks off for the older tests


def test_a_chop_maker_with_strong_ofi_gets_through_a_5m_macro_that_forbids_its_side(tmp_path):
    e = burst_engine(tmp_path, **LOCK)
    e.macro = LONG_ONLY
    side, why = e.scalp_candidate({**CHOP, 'ofi_10s': -0.60})
    assert side == 'SELL' and 'macro LONG ONLY softened (OFI -0.60)' in why
    assert e.scalp_candidate({**CHOP, 'ofi_10s': -0.55})[0] == 'SELL'                          # exactly the 0.55 floor passes
    side, why = e.scalp_candidate({**CHOP, 'ofi_10s': -0.54})
    assert side is None and why == 'MACRO LONG ONLY forbids SELL scalp'                       # under it: the old block
    assert e.scalp_candidate({**CHOP, 'ofi_10s': -0.60, 'ker': 0.35})[0] is None               # TREND lane (KER >= 0.28): hard
    off = burst_engine(tmp_path / 'c', micro_harvest_macro_soft=False, **LOCK)
    off.macro = LONG_ONLY
    assert off.scalp_candidate({**CHOP, 'ofi_10s': -0.60})[0] is None                          # switch off = the old hard block
    old = burst_engine(tmp_path / 'd', micro_harvester='off', **LOCK)
    old.macro = LONG_ONLY
    assert old.scalp_candidate({**CHOP, 'ofi_10s': -0.60})[0] is None                          # harvester off: no chop lane, no softening


def test_the_lagging_cvd3m_lock_of_the_scalper_is_a_size_penalty_when_ofi_and_the_1m_move_agree(tmp_path):
    e = burst_engine(tmp_path, scalp_cvd3m_usd=50_000.0)
    f = {**CHOP, 'ofi_10s': 0.60, 'cvd_3m': -80_000.0, 'px_vel_60s': 0.10}
    side, why = e.scalp_candidate(f)
    assert side == 'BUY' and 'CVD 3m' not in why
    assert 'size x0.75' in e.cvd3m_soft('BUY', f)
    for kw in ({'px_vel_60s': 0.0}, {'px_vel_60s': None}, {'ofi_10s': 0.49}):
        got = e.scalp_candidate({**f, **kw})
        assert got[0] is None or kw == {'ofi_10s': 0.49}, kw                    # 0.49 is under the 0.50 soft floor ...
    assert e.scalp_candidate({**f, 'px_vel_60s': 0.0}) == (None, 'CVD_3M conflict (-80000) with BUY')
    assert burst_engine(tmp_path / 'b', scalp_cvd3m_usd=50_000.0, micro_cvd_soft=False).scalp_candidate(f)[0] is None


def test_the_cvd_flow_regime_stays_a_hard_block(tmp_path):
    """The 23:05-23:13 XRP / PEPE locks were 'STRONG DOWNTREND (SHORT ONLY)' = the CVD flow regime, not the 5 m macro."""
    e = burst_engine(tmp_path, **LOCK)
    e.reg = {'name': 'DOWN', 'allow': ('SELL',), 'label': 'STRONG DOWNTREND (SHORT ONLY)'}
    side, why = e.scalp_candidate({**CHOP, 'ofi_10s': +0.90})
    assert side is None and why == 'MACRO STRONG DOWNTREND (SHORT ONLY) forbids BUY scalp'


def test_the_softening_applies_in_the_chop_lane_of_a_micro_account_only(tmp_path):
    big = burst_engine(tmp_path, wallet=5000.0, **LOCK)                          # not a micro account
    big.macro = LONG_ONLY
    assert big.scalp_candidate({**CHOP, 'ofi_10s': -0.90})[0] is None


async def test_shadow_and_order_rows_record_that_the_macro_was_softened(tmp_path):
    e, venue, feed = await doge_micro(tmp_path, **LOCK)
    e.macro = LONG_ONLY
    await e.chop_scalp(SELL, {**feed.features(), **CHOP, 'ofi_10s': -0.60})
    [r] = [x for x in rows(tmp_path) if x['outcome'] == 'SHADOW_SCALP']
    assert r['harvest'] is True and r['macro_soft'] is True and r['side'] == 'SELL' and 'softened' in r['why']
    e2, venue2, feed2 = await doge_micro(tmp_path / 'b', micro_harvester='on', micro_harvest_ttl_s=0.3, scalp_min_edge_bps=0.0,
                                         scalp_align3=False, **LOCK)
    e2.macro, e2.diag = LONG_ONLY, {'consensus': 0.9, 'edge_bps': 40.0}
    await e2.chop_scalp(SELL, {**feed2.features(), **CHOP, 'ofi_10s': -0.60})
    [o] = [x for x in rows(tmp_path / 'b') if x.get('qty_req')]
    assert o['harvest'] is True and o['macro_soft'] is True and o['side'] == 'SELL' and o['route'] == 'gtx'
    (tmp_path / 'c').mkdir()                                                    # _shadow_row does not create its directory
    e3, _, feed3 = await doge_micro(tmp_path / 'c', **LOCK)
    await e3.chop_scalp(SELL, {**feed3.features(), **CHOP, 'ofi_10s': -0.60})     # no macro lock at all: nothing was softened
    assert [x['macro_soft'] for x in rows(tmp_path / 'c') if x['outcome'] == 'SHADOW_SCALP'] == [False]


# ============================================================================= pillar 4: funding carry yield (simulated)
LINK = fx.FundingQuote('LINK', 0.0001, 0.00005, 20.0, 20.01)
LINK_FLIP = fx.FundingQuote('LINK', 0.0006, 0.00005, 20.0, 20.01)


async def test_the_carry_book_reports_its_accumulated_simulated_funding(tmp_path):
    t = [1_000_000.0]
    sim = cl.CarrySim(1000.0, clock=lambda: t[0])
    v = await sim.step([LINK])
    assert v['funding_total_usdt'] == 0.0 and v['funding_apr_pct'] is None and v['run_days'] == 0.0     # no run time: UNKNOWN
    t[0] += 8 * 3600.0
    v = await sim.step([LINK])
    assert v['funding_open_usdt'] > 0 and v['funding_total_usdt'] == v['funding_open_usdt'] and v['funding_closed_usdt'] == 0.0
    assert v['positions'][0]['funding_usdt'] == pytest.approx(v['funding_open_usdt'], abs=1e-4)
    assert v['run_days'] == pytest.approx(8 / 24, abs=1e-3) and v['funding_apr_pct'] == pytest.approx(
        v['funding_total_usdt'] / 1000.0 / v['run_days'] * 365 * 100, abs=0.01)
    banked = v['funding_total_usdt']
    t[0] += 3600.0
    v = await sim.step([LINK_FLIP])                                               # the spread flips: the pair closes
    assert sim.closed and sim.closed[-1]['funding_usdt'] > 0 and v['positions'] == []
    assert v['funding_open_usdt'] == 0.0 and v['funding_closed_usdt'] == pytest.approx(sim.closed[-1]['funding_usdt'], abs=1e-4)
    assert v['funding_total_usdt'] == v['funding_closed_usdt'] and banked > 0     # the closing hour accrued at the flipped (adverse) rates
    assert v['mode'].startswith('SIMULATED')


async def test_the_runner_publishes_the_carry_sim_state_in_the_snapshot():
    class Tel:
        state = {}
        def __init__(self): self.updates = []
        def update(self, **kw): self.updates.append(kw)
        def persist(self): pass
    e = SimpleNamespace(snapshot=lambda: {}, p=eng.PROFILES['demo'])
    tel = Tel()
    r = SimpleNamespace(refocus=lambda: e, tel=tel, scanner_view=lambda x: {}, live_ready=lambda: (True, ''), ws={}, env={},
                        carry_state={'held': []}, carry_sim_state={'mode': 'SIMULATED · paper venues · no real order',
                                                                    'funding_total_usdt': 0.0123}, autonomy=None, stat_arb=None)
    task = asyncio.ensure_future(eng.Runner.publish_loop(r))
    await asyncio.sleep(0.8)
    task.cancel()
    assert tel.updates and tel.updates[0]['carry_sim']['funding_total_usdt'] == 0.0123 and tel.updates[0]['funding_carry'] == {'held': []}
