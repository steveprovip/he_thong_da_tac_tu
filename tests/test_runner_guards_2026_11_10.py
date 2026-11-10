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
