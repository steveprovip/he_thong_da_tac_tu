"""10-24 DEMO ACTIVE (owner decision): the shipped demo trades the micro lane again, for data collection.

Micro lane SOL/DOGE on (BTC stays observe_only), chop scalper 'on' - also while the capital router is CHOP -, churn
and MR score floors at the 0.768 quorum (the VESKA token and the RUNE veto, core/invariants I1/I3, are unchanged),
KER 0.42 scalp ceiling, fixed MR bracket, Kelly on the last 20 trades.  Behaviour tests only: the replay result is in
docs/harness/progress.md 10-24 (d), and it is not evidence of a positive expectancy."""
from dataclasses import replace

import pytest

from core import autonomy as au
from core import engine as eng
from core import invariants as inv
from core import micro_signals as msig
from tests.test_autonomy_self_learning import Clock
from tests.test_continuous_active_trading import BUY, SCALP_F, SELL, orders
from tests.test_two_mode_engine import fill_through, mock_engine

CHOP = {'regime': 'CHOP', 'scalp_alloc': 0.0, 'trend_trail': False, 'symbols': [], 'why': 'test'}
NORMAL = {'regime': 'NORMAL', 'scalp_alloc': 1.0, 'trend_trail': False, 'symbols': [], 'why': ''}


def entries(venue):
    return [o for o in venue.orders.values() if o['kind'] == 'ENTRY']


def hub(tmp_path, base):
    feeds = {s: msig.MicroFeed(s, msig.config_for(s)) for s in ('SOLUSDT', 'DOGEUSDT')}
    profiles = {s: replace(base, symbol=s, qty=eng.SYMBOL_QTY.get(s, 0.0)) for s in feeds}
    venue = eng.MockVenue(profiles['SOLUSDT'], quote=lambda s: (100.0, 100.01))
    es = eng.build_scanner(profiles, venue, feeds, ledger=tmp_path / 'trades.jsonl')
    a = au.Autonomy(tmp_path / 'data', tmp_path / 'config', tmp_path / 'trades.jsonl', clock=Clock(), seed=1)
    a.attach(es)
    return es, a


# ============================================================================ shipped profiles
@pytest.mark.production_profile
def test_shipped_demo_is_active_and_keeps_the_invariants():
    d, live = eng.PROFILES['demo'], eng.PROFILES['live']
    assert d.micro_standalone and d.observe_only == ('BTCUSDT',)
    assert (d.chop_scalper, d.chop_router_scalp, d.scalp_only, d.scalp_ker_max) == ('on', True, False, 0.42)
    assert (d.min_score, d.mr_min_score, d.scalp_min_score, d.mr_bracket, d.kelly_window) == \
        (0.768, 0.768, 0.768, 'fixed', 20)
    assert min(d.min_score, d.mr_min_score, d.scalp_min_score) >= inv.SUPERMAJORITY - 1e-12
    assert inv.check_profile(d) == [] and d.entry == 'gtx' and not d.super_edge and d.super_cross == 'shadow'
    assert (d.burst_lane, d.intraday_lane, d.stat_arb_lane, d.trend_lane) == ('shadow', 'shadow', 'shadow', 'active')
    assert (live.chop_scalper, live.chop_router_scalp, live.kelly_window, live.min_score, live.mr_bracket) == \
        ('off', False, 0, 0.82, 'vwap')


@pytest.mark.production_profile
def test_shipped_demo_engine_starts_clean_and_btc_stays_observer(tmp_path):
    def build(sym):
        p = replace(eng.PROFILES['demo'], symbol=sym, qty=eng.SYMBOL_QTY.get(sym, 0.0))
        return eng.Engine(p, eng.MockVenue(p, quote=lambda s: (100.0, 100.01)), msig.MicroFeed(sym, msig.config_for(sym)),
                          ledger=tmp_path / f'{sym}.jsonl')
    sol, btc = build('SOLUSDT'), build('BTCUSDT')
    assert sol.invariant_errors == [] and not any('INVARIANT' in ev['message'] for ev in sol.events)
    assert not sol.observer and btc.observer
    m = sol.lane_feasibility()
    assert m['SCALP']['state'] == 'ENABLED' and m['MR']['feasible'] is True and m['MR']['rr_min'] >= 1.30


# ============================================================================ chop scalper 'on'
async def test_scalp_on_needs_the_swarm_vote_on_its_side_and_the_quorum(tmp_path):
    e, venue, feed = mock_engine(tmp_path, chop_scalper='on', qty=0.004)
    await e.sync()
    f = {**feed.features(), **SCALP_F}                           # OFI +0.6 -> a BUY scalp setup
    await e.chop_scalp(SELL, f)                                   # swarm votes SELL 80 %: no BUY token on it
    await e.chop_scalp(replace(BUY, score=0.72, allowed=False, reason='WEAK 72.0% < 76.8%'), f)
    assert orders(tmp_path) == [] and not entries(venue) and e.pos is None and e.authorized == 0
    await fill_through(e, feed, e.chop_scalp(BUY, f))             # BUY 80 % >= 0.768: the scalp opens
    assert e.pos and e.pos['path'] == 'SCALP' and e.authorized == 1


async def test_scalp_only_demotes_a_swarm_entry(tmp_path):
    e, venue, feed = mock_engine(tmp_path, scalp_only=True)
    await e.sync()
    d = await e.tick()                                            # the same tape authorises a GTX entry otherwise
    assert not d.allowed and d.reason.startswith('ROUTER CHOP: scalp lane only')
    assert e.pos is None and not entries(venue)
    assert any(stage == 'SCALP_ONLY' for _, stage in e.funnel.stages)
    assert e.radar()['label'] == 'SCALP ONLY (router CHOP)'


# ============================================================================ capital router CHOP
@pytest.mark.production_profile
def test_router_chop_mutes_swarm_entries_but_keeps_the_chop_scalper(tmp_path):
    es, a = hub(tmp_path, eng.PROFILES['demo'])
    sol = es['SOLUSDT']
    a.router = dict(CHOP)
    a.refresh(sol, {'ker': 0.1})
    assert sol.p.scalp_only and not sol.observer and sol.p.chop_scalper == 'on'
    a.router = dict(NORMAL)
    a.refresh(sol, {'ker': 0.5})
    assert not sol.p.scalp_only and not sol.observer


def test_router_chop_without_the_flag_still_mutes_the_whole_lane(tmp_path):
    es, a = hub(tmp_path, replace(eng.PROFILES['demo'], chop_scalper='on', chop_router_scalp=False))
    sol = es['SOLUSDT']
    a.router = dict(CHOP)
    a.refresh(sol, {'ker': 0.1})
    assert sol.observer and not sol.p.scalp_only


# ============================================================================ Kelly window
def test_kelly_window_lets_an_old_losing_streak_age_out(tmp_path):
    e, _, _ = mock_engine(tmp_path, kelly_window=20)
    sym = e.p.symbol
    old = [{'symbol': sym, 'pnl': -0.118} for _ in range(56)]
    new = [{'symbol': sym, 'pnl': 0.091 if i % 5 < 3 else -0.118} for i in range(20)]     # 12 W / 8 L
    e.all_rows = old + new
    k = e.kelly()
    assert k['n'] == 20 and k['window'] == 20 and k['p'] == pytest.approx(0.60) and k['state'] == 'EDGE'
    assert k['f_star'] == pytest.approx(0.60 - 0.40 / (0.091 / 0.118))
    e.p = replace(e.p, kelly_window=0)                            # whole ledger: the 56 old losses dominate
    k0 = e.kelly()
    assert k0['n'] == 76 and k0['f_star'] < 0 and k0['state'] == 'NO EDGE' and k0['window'] is None


def test_negative_kelly_falls_back_to_the_base_size_instead_of_blocking(tmp_path):
    e, _, _ = mock_engine(tmp_path, kelly_window=20)
    e.all_rows = [{'symbol': e.p.symbol, 'pnl': -0.1 if i % 4 else 0.05} for i in range(20)]
    assert e.kelly()['f'] < 0 and e.kelly_qty(100_000.0, e.p) is None


# ============================================================================ 10-24 (e) after the 11:29-12:41 DEMO run
@pytest.mark.production_profile
def test_shipped_demo_holds_the_burst_wake_and_waits_longer_for_intraday_bars():
    d, live = eng.PROFILES['demo'], eng.PROFILES['live']
    assert (d.burst_wake_hold_s, d.intraday_close_margin_s, d.scalp_hard_s) == (180.0, 20.0, 180.0)
    assert (live.burst_wake_hold_s, live.intraday_close_margin_s) == (0.0, 10.0)


async def test_burst_wake_needs_the_condition_held_not_one_flicker(tmp_path):
    from tests.test_break_doom_loop_2026_10_16 import _lane, _obs
    lane, e, events = _lane(tmp_path, burst_wake_hold_s=180.0)
    e.features = {'ker': 0.1}
    await lane.step()
    for k in (0.41, 0.20, 0.45, 0.10, 0.42):                      # the 10-24 flicker: KER 0.40 <-> < 0.25 in seconds
        e.features = {'ker': k}
        lane.clock.t += 3
        await lane.step()
    assert lane.hibernating and not _obs(events, 'WAKE') and len(_obs(events)) == 1
    e.features = {'ker': 0.45}
    lane.clock.t += 179
    await lane.step()
    assert lane.hibernating                                       # 179 s since the run started at 0.42
    lane.clock.t += 2
    await lane.step()
    assert not lane.hibernating and len(_obs(events, 'WAKE')) == 1 and len(_obs(events)) == 1


async def test_scalp_keeps_its_half_size_and_escalates_at_180s(tmp_path):
    e, venue, feed = mock_engine(tmp_path, chop_scalper='on', qty=0.004, scalp_hard_s=180.0)
    e.super_conviction = lambda *a, **k: True                     # a 95 % vote would size SUPER/boost a trend entry
    e.alt_boosted = lambda *a, **k: True
    await e.sync()
    await fill_through(e, feed, e.chop_scalp(BUY, {**feed.features(), **SCALP_F}))
    assert e.pos['qty'] == pytest.approx(0.002) and e.pos['eject_hard_s'] == 180.0 and e.pos['eject_s'] == 45.0   # 11-08 (i)
