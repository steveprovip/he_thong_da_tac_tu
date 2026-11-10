"""2026-11-10 (g): runner guards (armed early invalidation, stale eject) and the carry book's basis freeze + cost telemetry.

Ledger of the 13:00-14:40 Demo run (data/engine_trades.jsonl, both rows `runner: true`, path SWARM_OVERRIDE):
  XRP  SELL  REGIME  +0.01378 U  hold 443 s   MFE 0.132 %  shadow trigger 'OFI flipped +0.32' at 11.8 s
  DOGE SELL  SL      -0.03535 U  hold 2215 s  MFE 0.093 %  shadow trigger 'OFI flipped +0.93' at 14.7 s
The 0.70 OFI limit separates these two rows after the fact; it is not a validated rule (see Profile.micro_runner_inval_ofi).
"""
from pathlib import Path

import pytest

from core import engine as eng
from core import funding_executor as fx
from research import carry_lane as cl
from tests.test_micro_rotation import burst_engine
from tests.test_sniper_runner_carry_2026_11_10 import LINK, LINK_FLIP, T0, discrete, swarm_fill

ROOT = Path(__file__).resolve().parents[1]
NOW = 10_000.0
DOGE = {'runner': True, 'side': 'SELL', 'opened': NOW - 14.7, 'tag': 'AUTO', 'path': 'SWARM_OVERRIDE'}


def said(e, text):
    return any(text in ev['message'] for ev in e.events)


async def synced_fill(root, score, **prof):
    e, venue, feed = await swarm_fill(root, score, **prof)
    await e.sync()                                           # manage() reads the venue's position view
    return e, venue, feed


# ============================================================================= pillar 1: early invalidation armed for runners
def test_the_shipped_guards_and_the_profile_wide_mode():
    d, live = eng.PROFILES['demo'], eng.PROFILES['live']
    assert (d.micro_runner_inval_ofi, d.micro_runner_stale_s, d.micro_runner_stale_mfe_pct) == (0.70, 420.0, 0.20)
    assert (d.early_invalidation, live.early_invalidation, d.early_inval_ofi, d.early_inval_s) == ('shadow', 'off', 0.30, 45.0)
    assert live.micro_sniper_runner == 'off'                 # the guards act on runner positions only, and live refuses the runner


def test_the_armed_check_fires_on_the_doge_flip_and_not_on_the_xrp_one(tmp_path):
    e = burst_engine(tmp_path)                               # the mock profile has the profile-wide rule 'off'
    due = lambda p, f: e.early_invalid_due(p, NOW, f, runner=True)     # noqa: E731
    assert due(DOGE, {'ofi_10s': 0.93}) == 'OFI flipped +0.93'
    assert due(DOGE, {'ofi_10s': 0.32}) is None              # the XRP winner's first trigger: under the runner limit
    assert due(DOGE, {'ofi_10s': 0.70}) and due(DOGE, {'ofi_10s': 0.69}) is None
    assert due(DOGE, {'ofi_10s': -0.93}) is None             # flow with the short
    assert due(DOGE, {'ofi_10s': None}) is None and due(DOGE, {}) is None      # UNKNOWN is never adverse
    assert due(DOGE, {'cvd_vel_10s': 60_000.0}) == 'CVD -60k/s against' and due(DOGE, {'cvd_vel_10s': 49_000.0}) is None
    assert due({**DOGE, 'opened': NOW - 45.1}, {'ofi_10s': 0.93}) is None      # the 45 s window
    assert due({**DOGE, 'runner': False}, {'ofi_10s': 0.93}) is None
    long = {**DOGE, 'side': 'BUY'}
    assert due(long, {'ofi_10s': -0.8}) == 'OFI flipped -0.80' and due(long, {'ofi_10s': 0.8}) is None
    off = burst_engine(tmp_path / 'b', micro_runner_inval_ofi=0.0)
    assert off.early_invalid_due(DOGE, NOW, {'ofi_10s': 0.99, 'cvd_vel_10s': 1e9}, runner=True) is None      # 0 = off
    # the profile-wide rule keeps its own switch and its own 0.30 limit
    assert e.early_invalid_due(DOGE, NOW, {'ofi_10s': 0.93}) is None
    shadow = burst_engine(tmp_path / 'c', early_invalidation='shadow')
    assert shadow.early_invalid_due(DOGE, NOW, {'ofi_10s': 0.32}) == 'OFI flipped +0.32'


async def test_a_runner_whose_flow_flips_inside_45_s_leaves_at_the_touch(tmp_path):
    e, venue, feed = await synced_fill(tmp_path, 0.85, micro_sniper_runner='on', early_invalidation='shadow')
    p = e.pos
    e.features = {**(e.features or {}), 'ofi_10s': -0.32, 'cvd_vel_10s': 0.0}       # a LONG: the XRP-sized flip
    await e.manage()
    assert not p.get('exiting') and p['early_invalid']['mode'] == 'shadow' and 'armed' not in p['early_invalid']
    e.features = {**e.features, 'ofi_10s': -0.93}                                   # the DOGE-sized one
    await e.manage()
    assert p['exiting']['reason'] == 'EARLY-INVALID' and p['exiting']['floor'] is None      # maker at the touch, no BE floor
    assert p['early_invalid']['why'] == 'OFI flipped -0.32'                         # the shadow record keeps its first trigger
    assert p['early_invalid']['armed']['why'] == 'OFI flipped -0.93' and said(e, 'RUNNER EARLY INVALIDATION')
    await e.close('EARLY-INVALID')
    await e.sync()
    assert e.history[0]['runner'] is True and e.history[0]['early_invalid']['armed']['why'] == 'OFI flipped -0.93'


async def test_the_armed_check_does_not_need_the_profile_wide_rule(tmp_path):
    e, venue, feed = await synced_fill(tmp_path, 0.85, micro_sniper_runner='on')      # mock profile: early_invalidation 'off'
    e.features = {**(e.features or {}), 'ofi_10s': -0.93, 'cvd_vel_10s': 0.0}
    await e.manage()
    assert e.pos['exiting']['reason'] == 'EARLY-INVALID' and set(e.pos['early_invalid']) == {'armed'}


async def test_a_plain_swarm_fill_keeps_the_shadow_record_and_stays(tmp_path):
    e, venue, feed = await synced_fill(tmp_path, 0.85, early_invalidation='shadow')   # runner flag off
    e.features = {**(e.features or {}), 'ofi_10s': -0.93, 'cvd_vel_10s': 0.0}
    await e.manage()
    assert not e.pos.get('exiting') and e.pos['early_invalid']['mode'] == 'shadow' and 'armed' not in e.pos['early_invalid']
    late, _, _ = await synced_fill(tmp_path / 'b', 0.85, micro_sniper_runner='on')    # a runner past the window
    late.pos['opened'] -= 46.0
    late.features = {**(late.features or {}), 'ofi_10s': -0.93, 'cvd_vel_10s': 0.0}
    await late.manage()
    assert not late.pos.get('exiting') and not late.pos.get('early_invalid')


# ============================================================================= pillar 2: the stale-runner scratch
async def test_a_runner_fill_carries_its_own_stale_schedule(tmp_path):
    e, _, _ = await swarm_fill(tmp_path, 0.85, micro_sniper_runner='on')
    p = e.pos
    assert (p['eject_s'], p['eject_mfe']) == (420.0, 0.20)
    assert p['eject_hard_s'] == 420.0 + (e.p.eject_hard_s - e.p.eject_s) == 600.0    # the profile's floor -> touch gap
    assert said(e, 'stale eject 420s under +0.2% MFE')
    off, _, _ = await swarm_fill(tmp_path / 'b', 0.85, micro_sniper_runner='on', micro_runner_stale_s=0.0)
    assert off.pos['runner'] and 'eject_s' not in off.pos and said(off, 'no time eject')
    plain, _, _ = await swarm_fill(tmp_path / 'c', 0.85)
    assert 'eject_s' not in plain.pos and 'eject_mfe' not in plain.pos               # only runners get the schedule


def test_the_stale_guard_binds_at_420_s_under_020_mfe_and_the_chop_ejects_never_apply(tmp_path):
    e = burst_engine(tmp_path, eject=True, eject_fast=True)
    run = {'runner': True, 'eject_s': 420.0, 'eject_mfe': 0.20, 'eject_hard_s': 600.0, 'tp1_filled': False}
    for age, mfe, due in ((419.0, 0.0, False), (420.0, 0.093, True),
                          (2215.0, 0.093, True),             # the DOGE stop-out: due 30 minutes before its stop
                          (420.0, 0.132, True),              # the XRP winner as well: it left by REGIME at 443 s
                          (420.0, 0.20, False), (3000.0, 0.393, False)):       # a runner that ran is left to ratchet / trail
        p = {**run, 'opened': NOW - age, 'mfe_pct': mfe}
        assert bool(e.eject_due(p, NOW)) is due, (age, mfe)
        assert not e.eject_fast_due(p, NOW, {'px_vel_15s': 0.0})
    assert not e.eject_due({'runner': True, 'opened': NOW - 5000.0, 'mfe_pct': 0.0, 'tp1_filled': False}, NOW)   # stale_s 0
    assert not e.eject_due({**run, 'opened': NOW - 900.0, 'mfe_pct': 0.0, 'tp1_filled': True}, NOW)
    quiet = burst_engine(tmp_path / 'b')                     # Profile.eject off: the position's own schedule still runs
    assert quiet.eject_due({**run, 'opened': NOW - 421.0, 'mfe_pct': 0.0}, NOW)


async def test_a_stale_runner_gets_the_be_scratch_then_the_touch(tmp_path):
    e, venue, feed = await synced_fill(tmp_path, 0.85, micro_sniper_runner='on')
    p = e.pos
    p['opened'] -= 418.0
    await e.manage()
    assert not p.get('exiting')                              # 7 minutes are not over
    p['opened'] -= 3.0
    await e.manage()
    x = p['exiting']
    assert x['reason'] == 'EJECT-BE' and x['floor'] == e.eject_floor(p) and x['floor'] > p['entry']
    assert said(e, 'RUNNER STALE EJECT') and not said(e, 'TIME-DECAY EJECT')
    p['opened'] -= 100.0                                     # 521 s: the scratch order keeps its floor
    await e.manage()
    assert p['exiting']['floor'] is not None and p['exiting']['reason'] == 'EJECT-BE'
    p['opened'] -= 200.0 + e.p.eject_defer_max_s             # past 600 s + the fee-hurdle deferral: out at the touch
    await e.manage()
    assert p['exiting']['floor'] is None and p['exiting']['reason'] == 'EJECT'


# ============================================================================= pillar 3: carry basis freeze + cost telemetry
AAVE = fx.FundingQuote('AAVE', 0.0001, 0.00005, 100.0, 100.05)        # eligible: 3 bps / 8 h, basis +5 bps


async def test_a_basis_jump_of_15_bps_freezes_new_pairs_for_one_rotation_interval():
    t = [T0]
    sim = discrete(t)
    await sim.step([LINK])                                             # short HL / long BN
    t[0] += 300.0
    jump = fx.FundingQuote('LINK', 0.0001, 0.00005, 20.0, 20.05)       # the short leg +20 bps against the long leg
    v = await sim.step([jump, AAVE])
    assert 'LINK' not in sim.pairs and sim.closed[-1]['why'].startswith('EJECT-BASIS')      # the -3 bps stop closes it
    frz = v['basis_freeze']
    assert frz['coin'] == 'LINK' and frz['drag_bps'] == pytest.approx(-19.99, abs=0.02) and frz['until'] == t[0] + 3600.0
    assert 'AAVE' not in sim.pairs and any('BASIS DIVERGENCE LINK' in m for m in v['new_events'])
    assert v['rule']['basis_freeze_bps'] == 15.0
    t[0] += 3599.0
    v = await sim.step([AAVE])                                         # a rotation scan inside the freeze opens nothing
    assert 'AAVE' not in sim.pairs and v['basis_freeze'] is not None
    t[0] += 2.0
    v = await sim.step([AAVE])
    assert 'AAVE' in sim.pairs and v['basis_freeze'] is None


async def test_a_slow_drift_past_the_stop_closes_the_pair_without_a_freeze():
    t = [T0]
    sim = discrete(t)
    await sim.step([LINK])
    t[0] += 300.0
    drift = fx.FundingQuote('LINK', 0.0001, 0.00005, 20.0, 20.02)      # -5 bps: past the -3 stop, short of 15
    v = await sim.step([drift, AAVE])
    assert 'LINK' not in sim.pairs and v['basis_freeze'] is None and 'AAVE' in sim.pairs
    for bad in (0.0, -15.0, float('nan')):
        with pytest.raises(ValueError):
            cl.CarrySim(1000.0, basis_freeze_bps=bad)


async def test_the_book_reports_its_fees_its_realized_yield_and_the_break_even_hours():
    t = [T0]
    sim = discrete(t)
    v = await sim.step([LINK])
    pos = v['positions'][0]
    assert pos['breakeven_h'] == v['top'][0]['breakeven_h'] == 40.0    # 3 bps / 8 h repays the 15 bps round trip in 40 h
    assert v['rule']['round_trip_bps'] == 15.0
    assert v['fees_total_usdt'] == pytest.approx(pos['fees_usdt'], abs=1e-4) and v['fees_total_usdt'] > 0
    assert v['net_apr_realized_pct'] is None                           # under an hour of run time: UNKNOWN, not zero
    t[0] += 3700.0
    v = await sim.step([LINK])                                         # one HL print banked, entry costs still unpaid
    assert v['funding_total_usdt'] > 0 > v['pnl']
    assert v['net_apr_realized_pct'] == pytest.approx(v['pnl'] / 1000.0 / (3700.0 / 86400.0) * 365 * 100, rel=1e-2)
    assert v['net_apr_realized_pct'] < 0 < v['top'][0]['net_apr_pct']   # the per-coin figure assumes a 28-day hold
    paid = v['fees_total_usdt']
    t[0] += 300.0
    v = await sim.step([LINK_FLIP])                                    # closed: its fees stay in the total
    assert not sim.pairs and v['fees_total_usdt'] == pytest.approx(sim.fees_closed, abs=1e-4) and sim.fees_closed > paid
    assert cl.breakeven_h(0.0) is None and cl.breakeven_h(-1.0) is None and cl.breakeven_h(None) is None
    assert cl.breakeven_h(3.28) == pytest.approx(36.6, abs=0.05)       # the AVAX print of the directive: 36.6 h, at a 1 h rotation


def test_the_cockpit_shows_the_fees_and_the_freeze():
    js = (ROOT / 'web_ui' / 'app.js').read_text(encoding='utf-8')
    if 'ck-carry-book' not in js:
        pytest.skip('web_ui/app.js is not the single-screen cockpit')
    assert 'sim.fees_total_usdt' in js and 'sim.basis_freeze' in js


# ============================================================================= 11-10 (h): no rotation before the break-even hours
HOT = fx.FundingQuote('AAVE', 0.0001, 0.0001, 100.0, 100.05)          # 7 bps / 8 h: outranks LINK's 3


async def test_a_pair_is_not_rotated_out_before_its_break_even_hours():
    t = [T0]
    sim = cl.CarrySim(1000.0, slots=1, clock=lambda: t[0], settlement='discrete', hold_breakeven=True)
    v = await sim.step([LINK])
    pos = v['positions'][0]
    assert pos['min_hold_h'] == 40.0 and pos['hold_until'] == T0 + 40 * 3600.0 and v['rule']['hold_breakeven'] is True
    t[0] += 3600.0
    v = await sim.step([LINK, HOT])                                    # the hourly scan: AAVE ranks first, LINK keeps its slot
    assert list(sim.pairs) == ['LINK'] and not sim.closed and v['rotation']['targets'] == ['LINK']
    assert any('kept to break-even: LINK 39.0 h' in m for m in v['new_events'])
    t[0] = T0 + 40 * 3600.0 + 1.0
    v = await sim.step([LINK, HOT])                                    # 40 h later the rotation is allowed again
    assert list(sim.pairs) == ['AAVE'] and sim.closed[-1]['why'].startswith('ROTATE')
    assert v['positions'][0]['min_hold_h'] == pytest.approx(15.0 / (7.0 / 8.0), abs=0.05)     # 17.1 h at 7 bps / 8 h

    old = cl.CarrySim(1000.0, slots=1, clock=lambda: t[0], settlement='discrete')    # library default: the hourly rotation
    t[0] = T0
    v = await old.step([LINK])
    assert v['positions'][0]['min_hold_h'] == 0.0 and v['rule']['hold_breakeven'] is False
    t[0] += 3600.0
    await old.step([LINK, HOT])
    assert list(old.pairs) == ['AAVE'] and old.closed[-1]['why'].startswith('ROTATE')


async def test_the_hold_does_not_delay_a_flip_a_basis_stop_or_a_free_slot():
    t = [T0]
    sim = cl.CarrySim(1000.0, slots=2, clock=lambda: t[0], settlement='discrete', hold_breakeven=True)
    await sim.step([LINK])
    t[0] += 3600.0
    await sim.step([LINK, HOT])                                        # a free slot is filled by the scan as before
    assert set(sim.pairs) == {'LINK', 'AAVE'}
    t[0] += 300.0
    await sim.step([LINK_FLIP, HOT])                                   # the spread flipped inside the hold: closed at once
    assert 'LINK' not in sim.pairs and 'flipped or gone' in sim.closed[-1]['why']
    t[0] += 300.0
    await sim.step([fx.FundingQuote('AAVE', 0.0001, 0.0001, 100.0, 100.10)])     # short leg +5 bps: the -3 bps stop
    assert not sim.pairs and sim.closed[-1]['why'].startswith('EJECT-BASIS')


async def test_the_demo_loop_holds_pairs_to_break_even():
    from types import SimpleNamespace
    assert eng.PROFILES['demo'].carry_hold_breakeven is True
    e0 = SimpleNamespace(account={'wallet': 1000.0}, p=eng.PROFILES['demo'], event=lambda m: None)
    r = SimpleNamespace(engines={'SOLUSDT': e0}, carry_state=None, carry_sim_state=None)
    await eng.Runner.funding_loop(r, once=True, fetch=lambda: [LINK])
    assert r.carry_sim.hold_breakeven is True and r.carry_sim_state['positions'][0]['min_hold_h'] == 40.0


# ============================================================================= 11-10 (i): a spread flip must last before it closes
def patient(t, **kw):
    return cl.CarrySim(1000.0, clock=lambda: t[0], settlement='discrete', hold_breakeven=True, flip_persist_s=3600.0, **kw)


async def test_a_flip_closes_a_pair_only_after_it_has_lasted_an_hour():
    t = [T0]
    sim = patient(t)
    v = await sim.step([LINK])
    assert v['positions'][0]['flip_since'] is None and v['rule']['flip_persist_s'] == 3600.0
    t[0] = T0 + 300.0
    v = await sim.step([LINK_FLIP])                                    # first flipped print: seen, not acted on
    assert 'LINK' in sim.pairs and v['positions'][0]['flip_since'] == T0 + 300.0 and v['positions'][0]['spread_8h'] < 0
    assert any('CARRY SIM FLIP LINK' in m and '60 min' in m for m in v['new_events'])
    t[0] = T0 + 600.0
    v = await sim.step([LINK_FLIP])
    assert 'LINK' in sim.pairs and not any('CARRY SIM FLIP' in m for m in v['new_events'])      # announced once
    t[0] = T0 + 900.0
    v = await sim.step([LINK])                                         # a positive print: the clock is cleared
    assert v['positions'][0]['flip_since'] is None
    t[0] = T0 + 1200.0
    await sim.step([LINK_FLIP])                                        # flipped again: a new clock from here
    t[0] = T0 + 1200.0 + 3599.0
    await sim.step([LINK_FLIP])
    assert 'LINK' in sim.pairs and not sim.closed
    t[0] += 1.0
    await sim.step([LINK_FLIP])
    assert 'LINK' not in sim.pairs and 'flipped or gone for 60 min' in sim.closed[-1]['why']


async def test_an_unknown_refresh_neither_resets_nor_closes_a_flip_and_the_stops_do_not_wait():
    t = [T0]
    sim = patient(t)
    await sim.step([LINK])
    t[0] = T0 + 300.0
    await sim.step([LINK_FLIP])
    t[0] = T0 + 4000.0
    await sim.step([fx.FundingQuote('LINK', None, None, None, None)])   # UNKNOWN past the hour: hold, decide nothing
    assert 'LINK' in sim.pairs and sim.pairs['LINK']['flip_since'] == T0 + 300.0
    t[0] = T0 + 4300.0
    await sim.step([LINK_FLIP])                                        # known again and still flipped: now it closes
    assert 'LINK' not in sim.pairs and 'flipped or gone' in sim.closed[-1]['why']

    t2 = [T0]
    stop = patient(t2)
    await stop.step([LINK])
    t2[0] = T0 + 300.0
    await stop.step([fx.FundingQuote('LINK', 0.0006, 0.00005, 20.0, 20.02)])     # flipped AND -5 bps of basis: the stop acts at once
    assert 'LINK' not in stop.pairs and stop.closed[-1]['why'].startswith('EJECT-BASIS')
    for bad in (-1.0, float('nan')):
        with pytest.raises(ValueError, match='flip_persist_s'):
            cl.CarrySim(1000.0, flip_persist_s=bad)
    assert cl.CarrySim(1000.0).flip_persist_s == 0.0                   # library default: a flip closes at once, as before


async def test_the_demo_loop_waits_one_funding_hour_on_a_flip():
    from types import SimpleNamespace
    assert eng.PROFILES['demo'].carry_flip_persist_s == 3600.0
    e0 = SimpleNamespace(account={'wallet': 1000.0}, p=eng.PROFILES['demo'], event=lambda m: None)
    r = SimpleNamespace(engines={'SOLUSDT': e0}, carry_state=None, carry_sim_state=None)
    await eng.Runner.funding_loop(r, once=True, fetch=lambda: [LINK])
    assert r.carry_sim.flip_persist_s == 3600.0 and r.carry_sim_state['rule']['flip_persist_s'] == 3600.0


# ============================================================================= 11-10 (j): the book's rows reach a file
def kinds(sim):
    return [r['kind'] for r in sim.records]


async def test_each_step_leaves_its_rows_and_the_snapshot_does_not_carry_them():
    t = [T0]
    sim = patient(t)
    v = await sim.step([LINK])
    assert kinds(sim) == ['START', 'OPEN', 'BOOK'] and 'records' not in v           # the cockpit frame stays small
    start, opened, book = sim.records
    assert start['capital'] == 1000.0 and start['rule']['flip_persist_s'] == 3600.0 and start['rule']['hold_breakeven'] is True
    assert (opened['coin'], opened['short'], opened['long'], opened['spread_8h'], opened['min_hold_h']) == ('LINK', 'HL', 'BN', 3.0, 40.0)
    assert opened['bn_mark'] == 20.0 and opened['hl_rate_1h'] == 0.00005 and opened['t'] == T0
    assert book['pnl'] == v['pnl'] and book['fees_total_usdt'] == v['fees_total_usdt'] and [x['coin'] for x in book['open']] == ['LINK']
    t[0] = T0 + 300.0
    await sim.step([LINK])
    assert kinds(sim) == ['BOOK']                                      # one mark per refresh, START only once


async def test_flips_their_reversions_and_every_close_reason_are_rows():
    t = [T0]
    sim = patient(t)
    await sim.step([LINK])
    t[0] = T0 + 300.0
    await sim.step([LINK_FLIP])
    flip = next(r for r in sim.records if r['kind'] == 'FLIP')
    assert flip['coin'] == 'LINK' and flip['spread_8h'] < 0 and flip['held_s'] == 300.0
    t[0] = T0 + 900.0
    await sim.step([LINK])                                             # reverted after two refreshes
    clear = next(r for r in sim.records if r['kind'] == 'FLIP_CLEAR')
    assert clear['lasted_s'] == 600.0 and clear['spread_8h'] == 3.0
    t[0] = T0 + 1200.0
    await sim.step([LINK_FLIP])
    t[0] = T0 + 4800.0
    await sim.step([LINK_FLIP])                                        # lasted the hour: closed
    close = next(r for r in sim.records if r['kind'] == 'CLOSE')
    assert (close['code'], close['coin'], close['flip_s'], close['hold_s']) == ('FLIP', 'LINK', 3600.0, 4800.0)
    assert close['net_usdt'] == sim.closed[-1]['net_usdt'] and close['fees_usdt'] > 0 and close['min_hold_h'] == 40.0
    assert close['settlements']['hl'] >= 1 and 'flipped or gone' in close['why']

    t2 = [T0]
    rot = cl.CarrySim(1000.0, slots=1, clock=lambda: t2[0], settlement='discrete')          # hourly rotation, as the library default
    await rot.step([LINK])
    t2[0] = T0 + 3600.0
    await rot.step([LINK, HOT])
    assert [(r['code'], r['flip_s']) for r in rot.records if r['kind'] == 'CLOSE'] == [('ROTATE', None)]
    assert [r['coin'] for r in rot.records if r['kind'] == 'OPEN'] == ['AAVE']

    t3 = [T0]
    jump = discrete(t3)
    await jump.step([LINK])
    t3[0] = T0 + 300.0
    await jump.step([fx.FundingQuote('LINK', 0.0001, 0.00005, 20.0, 20.05)])
    assert kinds(jump) == ['BASIS_FREEZE', 'CLOSE', 'BOOK']
    assert jump.records[1]['code'] == 'BASIS' and jump.records[0]['drag_bps'] == pytest.approx(-19.99, abs=0.02)


async def test_the_demo_loop_appends_the_rows_beside_the_ledger_and_never_depends_on_the_file(tmp_path):
    import json
    from dataclasses import replace
    from types import SimpleNamespace
    assert eng.PROFILES['demo'].carry_sim_log is True

    def runner(ledger, **prof):
        e0 = SimpleNamespace(account={'wallet': 1000.0}, p=replace(eng.PROFILES['demo'], **prof), event=lambda m: None)
        if ledger is not None:
            e0.ledger = ledger
        return SimpleNamespace(engines={'SOLUSDT': e0}, carry_state=None, carry_sim_state=None)

    r = runner(tmp_path / 'engine_trades.jsonl')
    await eng.Runner.funding_loop(r, once=True, fetch=lambda: [LINK])
    await eng.Runner.funding_loop(r, once=True, fetch=lambda: [LINK])
    rows = [json.loads(x) for x in (tmp_path / 'carry_sim.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [x['kind'] for x in rows] == ['START', 'OPEN', 'BOOK', 'BOOK']
    assert all(x['schema'] == 1 and 'code' in x and x['t'] > 0 for x in rows) and rows[1]['coin'] == 'LINK'

    off = runner(tmp_path / 'off' / 'engine_trades.jsonl', carry_sim_log=False)
    (tmp_path / 'off').mkdir()
    await eng.Runner.funding_loop(off, once=True, fetch=lambda: [LINK])
    assert not (tmp_path / 'off' / 'carry_sim.jsonl').exists() and off.carry_sim_state['positions']

    bare = runner(None)                                                # an engine without a ledger path: nothing is written
    await eng.Runner.funding_loop(bare, once=True, fetch=lambda: [LINK])
    assert bare.carry_sim_state['positions'] and eng.write_carry_rows(None, [{'kind': 'BOOK'}]) == 0

    gone = runner(tmp_path / 'missing_dir' / 'engine_trades.jsonl')    # the folder does not exist: the write fails quietly
    state = await eng.Runner.funding_loop(gone, once=True, fetch=lambda: [LINK])
    assert gone.carry_sim_state['positions'] and 'error' not in (state or {})
    assert eng.write_carry_rows(tmp_path / 'engine_trades.jsonl', [{'kind': 'BOOK', 'bad': object()}]) == 0      # not JSON: swallowed
    assert eng.write_carry_rows(tmp_path / 'engine_trades.jsonl', []) == 0


# ============================================================================= 11-10 (k): the book survives a restart
def saved(sim):
    import json
    return json.loads(json.dumps(sim.snapshot()))                     # exactly what the state file holds


NO_PRINTS = {'LINK': {'bn': [], 'hl': []}}


async def test_a_saved_book_comes_back_with_its_positions_costs_and_clocks():
    t = [T0]
    a = patient(t)
    await a.step([LINK])
    t[0] = T0 + 900.0
    va = await a.step([LINK_FLIP])                                     # one HL print banked, flipped since +900 s
    state = saved(a)
    t[0] = T0 + 1000.0                                                 # the next process starts 100 s later
    b = patient(t)
    info = b.restore(state, NO_PRINTS)
    assert info['restored'] and info['pairs'] == ['LINK'] and info['gap_s'] == 100.0 and info['funding_gap_usdt'] == 0.0
    pa, pb = a.pairs['LINK'], b.pairs['LINK']
    for k in ('qty', 'opened', 'hold_until', 'min_hold_h', 'flip_since', 'spread_8h_entry', 'short', 'long', 'maker_side'):
        assert pb[k] == pa[k], k
    assert pb['settled'] == pa['settled'] == {'bn': 0, 'hl': 1} and b.start == a.start and b.first_t == T0
    assert pb['bn'].fees == pa['bn'].fees and pb['hl'].funding == pa['hl'].funding and b.equity() == pytest.approx(a.equity())
    vb = await b.step([LINK_FLIP])
    assert [r['kind'] for r in b.records] == ['RESTORE', 'BOOK']       # no START and no OPEN: the same book goes on
    assert b.records[0]['rule']['flip_persist_s'] == 3600.0 and b.records[0]['gap_s'] == 100.0
    assert vb['fees_total_usdt'] == va['fees_total_usdt'] and vb['positions'][0]['fees_usdt'] == va['positions'][0]['fees_usdt']
    assert vb['restore']['restored'] and any('CARRY SIM RESTORED 1 pair' in m for m in vb['new_events'])
    assert vb['positions'][0]['flip_since'] == T0 + 900.0              # the flip clock kept running through the restart
    t[0] = T0 + 900.0 + 3600.0
    await a.step([LINK_FLIP])                                          # the twin that never restarted ...
    await b.step([LINK_FLIP])
    assert not a.pairs and not b.pairs                                 # ... closes on the same refresh, for the same money
    assert b.closed[-1]['net_usdt'] == pytest.approx(a.closed[-1]['net_usdt'], abs=1e-4)
    assert b.closed[-1]['funding_usdt'] == pytest.approx(a.closed[-1]['funding_usdt'], abs=1e-4)


async def test_offline_funding_comes_from_the_prints_the_venues_paid_and_is_never_guessed():
    t = [T0]
    a = patient(t)
    await a.step([LINK])                                               # short HL / long BN
    state, qty = saved(a), a.notional_per_leg / 20.0
    t[0] = T0 + 10 * 3600.0                                            # ten hours with no process
    hours = [(1_000_800_000 + i * 3_600_000, 0.00004) for i in range(10)]
    prints = {'LINK': {'bn': [(1_008_000_000, 0.0002)],
                       'hl': [(999_000_000, 0.9)] + hours + [(1_040_000_000, 0.9)]}}       # two prints outside the gap
    b = patient(t)
    info = b.restore(state, prints)
    p = b.pairs['LINK']
    assert info['settled'] == {'bn': 1, 'hl': 10} and info['unsettled'] == {'bn': 0, 'hl': 0} and info['unknown'] == []
    assert p['hl'].funding == pytest.approx(10 * qty * 20.01 * 0.00004)        # the short leg received ten real prints
    assert p['bn'].funding == pytest.approx(-qty * 20.0 * 0.0002)              # the long leg paid the one Binance print
    assert info['funding_gap_usdt'] == pytest.approx(p['hl'].funding + p['bn'].funding, abs=1e-4) and p['settle_t'] == t[0]
    v = await b.step([LINK])
    assert v['positions'][0]['settlements'] == {'bn': 1, 'hl': 10}             # nothing is paid a second time

    c = patient(t)
    info = c.restore(state, {})                                        # the history could not be read
    assert info['restored'] and info['unknown'] == ['LINK'] and info['unsettled'] == {'bn': 1, 'hl': 10}
    assert info['funding_gap_usdt'] == 0.0 and c.pairs['LINK']['hl'].funding == 0.0
    v = await c.step([LINK])                                           # and the next step does not pay them at a stale rate
    assert v['positions'][0]['unsettled'] == {'bn': 1, 'hl': 10} and v['positions'][0]['funding_usdt'] == 0.0
    assert any('11 instants UNKNOWN, not credited (LINK)' in m for m in v['new_events'])


async def test_a_break_even_hold_runs_on_wall_clock_time_across_restarts():
    def book(t):
        return cl.CarrySim(1000.0, slots=1, clock=lambda: t[0], settlement='discrete', hold_breakeven=True,
                           flip_persist_s=3600.0)
    t = [T0]
    a = book(t)
    await a.step([LINK])                                               # 40 h hold
    t[0] = T0 + 10 * 3600.0
    b = book(t)
    b.restore(saved(a), NO_PRINTS)
    await b.step([LINK, HOT])                                          # outranked ten hours in: thirty hours of hold are left
    assert list(b.pairs) == ['LINK'] and not b.closed
    t[0] = T0 + 40 * 3600.0 + 1.0
    c = book(t)
    c.restore(saved(b), NO_PRINTS)
    await c.step([LINK, HOT])                                          # third process, the hold is over: rotated
    assert list(c.pairs) == ['AAVE'] and c.closed[-1]['why'].startswith('ROTATE')
    close = next(r for r in c.records if r['kind'] == 'CLOSE')
    assert close['hold_s'] == 40 * 3600.0 + 1.0 and close['code'] == 'ROTATE'


async def test_a_state_that_cannot_be_trusted_leaves_a_fresh_book_and_says_why():
    t = [T0]
    a = patient(t)
    await a.step([LINK])
    state = saved(a)
    broken = {**state, 'pairs': {'LINK': {k: v for k, v in state['pairs']['LINK'].items() if k != 'bn'}}}
    cases = [(state, T0 + 8 * 86400.0, {}, 'd old'), (state, T0 - 10.0, {}, 'from the future'),
             ({**state, 'schema': 99}, T0 + 60.0, {}, 'schema'), (None, T0 + 60.0, {}, 'schema'),
             ('{}', T0 + 60.0, {}, 'schema'), (state, T0 + 60.0, {'slots': 2}, 'slots or utilization'),
             (broken, T0 + 60.0, {}, 'unreadable state'), ({**state, 'start': None}, T0 + 60.0, {}, 'time or capital')]
    for st, now, kw, word in cases:
        t[0] = now
        fresh = patient(t, **kw)
        info = fresh.restore(st, NO_PRINTS)
        assert info['restored'] is False and word in info['why'], (word, info)
        assert not fresh.pairs and fresh.start == 1000.0 and fresh.cash == 1000.0 and fresh.first_t is None
        await fresh.step([])
        assert [r['kind'] for r in fresh.records] == ['RESTORE_SKIP', 'START', 'BOOK'], word
    t[0] = T0 + 60.0
    assert 'already running' in a.restore(state, NO_PRINTS)['why'] and list(a.pairs) == ['LINK']


async def test_a_halted_book_and_its_closed_pairs_come_back_too():
    t = [T0]
    a = patient(t)
    await a.step([LINK])
    t[0] = T0 + 300.0
    await a.step([fx.FundingQuote('LINK', 0.0001, 0.00005, 20.0, 20.05)])       # a 20 bps jump: closed, new pairs frozen
    a.kill.halt('drawdown test')
    t[0] = T0 + 600.0
    b = patient(t)
    assert b.restore(saved(a), {})['pairs'] == []
    assert b.kill.halted == 'drawdown test' and b.basis_freeze['coin'] == 'LINK' and b.closed[-1]['why'].startswith('EJECT-BASIS')
    assert b.fees_closed == a.fees_closed > 0 and b.cash == pytest.approx(a.cash)
    v = await b.step([LINK, AAVE])
    assert not b.pairs and v['kill'] and v['basis_freeze']             # a restart is not a way around the kill switch


def test_the_print_fetcher_reads_both_venues_and_refuses_a_full_page():
    import io
    import json
    calls = []
    bn = [{'symbol': '1000PEPEUSDT', 'fundingTime': 5000, 'fundingRate': '0.00010000'},
          {'symbol': '1000PEPEUSDT', 'fundingTime': 3000, 'fundingRate': '-0.00020000'},
          {'symbol': '1000PEPEUSDT', 'fundingTime': 9500, 'fundingRate': '0.5'}]          # after the window
    hl = [{'coin': 'kPEPE', 'time': 4000, 'fundingRate': '0.0000125'}, {'coin': 'kPEPE', 'time': 1000, 'fundingRate': '0.9'}]

    def opener(rows_bn, rows_hl):
        def op(req, timeout=None):
            calls.append((req.full_url, req.data))
            return io.BytesIO(json.dumps(rows_bn if 'fundingRate?' in req.full_url else rows_hl).encode())
        return op
    out = fx.fetch_funding_prints('1000PEPE', 1000, 9000, opener=opener(bn, hl))
    assert out == {'bn': [(3000, -0.0002), (5000, 0.0001)], 'hl': [(4000, 0.0000125)]}      # oldest first, (start, end] only
    assert 'symbol=1000PEPEUSDT' in calls[0][0] and 'startTime=1001' in calls[0][0] and 'endTime=9000' in calls[0][0]
    assert calls[0][1] is None and calls[1][0] == fx.HL_INFO_URL
    assert json.loads(calls[1][1]) == {'type': 'fundingHistory', 'coin': 'kPEPE', 'startTime': 1001, 'endTime': 9000}
    with pytest.raises(ValueError, match='full page'):
        fx.fetch_funding_prints('LINK', 0, 10, opener=opener([], [{'time': 1, 'fundingRate': '0'}] * fx.HL_PRINT_PAGE))
    with pytest.raises(ValueError, match='two lists'):
        fx.fetch_funding_prints('LINK', 0, 10, opener=opener({'code': -1121, 'msg': 'Invalid symbol.'}, []))


def loop_runner(ledger, **prof):
    from dataclasses import replace
    from types import SimpleNamespace
    e0 = SimpleNamespace(account={'wallet': 1000.0}, p=replace(eng.PROFILES['demo'], **prof), event=lambda m: None)
    if ledger is not None:
        e0.ledger = ledger
    return SimpleNamespace(engines={'SOLUSDT': e0}, carry_state=None, carry_sim_state=None)


async def test_a_second_process_continues_the_book_of_the_first(tmp_path):
    import json
    assert eng.PROFILES['demo'].carry_sim_persist is True
    ledger, quotes = tmp_path / 'engine_trades.jsonl', (lambda: [LINK])
    r1 = loop_runner(ledger)
    await eng.Runner.funding_loop(r1, once=True, fetch=quotes)
    first = r1.carry_sim_state['positions'][0]
    assert (tmp_path / 'carry_sim_state.json').exists() and not (tmp_path / 'carry_sim_state.json.tmp').exists()
    assert json.loads((tmp_path / 'carry_sim_state.json').read_text(encoding='utf-8'))['pairs']['LINK']['qty'] > 0

    asked = []

    def prints(coin, a, b):
        asked.append((coin, a, b))
        return {'bn': [], 'hl': []}
    r2 = loop_runner(ledger)                                           # a new process: nothing in memory
    await eng.Runner.funding_loop(r2, once=True, fetch=quotes, fetch_prints=prints)
    st = r2.carry_sim_state
    assert [c for c, _, _ in asked] == ['LINK'] and asked[0][1] < asked[0][2]
    assert st['restore']['restored'] and st['restore']['pairs'] == ['LINK'] and st['restore']['unknown'] == []
    assert len(st['positions']) == 1 and st['positions'][0]['opened'] == first['opened']      # the same pair, no new entry
    assert st['fees_total_usdt'] == r1.carry_sim_state['fees_total_usdt']                    # and no second entry cost
    rows = [json.loads(x)['kind'] for x in (tmp_path / 'carry_sim.jsonl').read_text(encoding='utf-8').splitlines()]
    assert rows == ['START', 'OPEN', 'BOOK', 'RESTORE', 'BOOK']

    def offline(coin, a, b):
        raise OSError('no network')
    r3 = loop_runner(ledger)                                           # the history cannot be read: the pairs still come back
    await eng.Runner.funding_loop(r3, once=True, fetch=quotes, fetch_prints=offline)
    assert r3.carry_sim_state['restore']['restored'] and r3.carry_sim_state['positions'][0]['opened'] == first['opened']

    r4 = loop_runner(ledger, carry_sim_persist=False)                  # switched off: a fresh book, the file untouched
    before = (tmp_path / 'carry_sim_state.json').read_text(encoding='utf-8')
    await eng.Runner.funding_loop(r4, once=True, fetch=quotes, fetch_prints=offline)
    assert r4.carry_sim_state['restore'] is None and r4.carry_sim_state['positions'][0]['opened'] != first['opened']
    assert (tmp_path / 'carry_sim_state.json').read_text(encoding='utf-8') == before

    (tmp_path / 'carry_sim_state.json').write_text('{not json', encoding='utf-8')
    r5 = loop_runner(ledger)                                           # an unreadable file: a fresh book, no error
    state = await eng.Runner.funding_loop(r5, once=True, fetch=quotes, fetch_prints=offline)
    assert 'error' not in (state or {}) and r5.carry_sim_state['restore'] is None and r5.carry_sim_state['positions']
    assert eng.load_carry_state(None) is None and eng.save_carry_state(None, {}) is False
    assert eng.save_carry_state(tmp_path / 'nowhere' / 'engine_trades.jsonl', {}) is False     # no folder: swallowed
