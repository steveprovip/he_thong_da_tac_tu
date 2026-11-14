"""11-10 (e) owner directive "trend sniper / carry / regime flip / bankroll" (docs/harness/session-handoff.md, entry 2026-11-10 e).

What was built: an opt-in RUNNER exit policy for high-conviction micro trend entries, quote markouts of SHADOW harvests, discrete
funding settlement + a net-APR floor in the simulated carry book, the cockpit reading the key the engine publishes, the micro
launcher (harvester shadow, runner on, 15 U rehearsed bankroll).  What was NOT changed and is pinned here: the regime-flip exit
already waits 60 s (the 10:26 XRP exit happened at 100 s; "held 20s" in its log line is the debounce).

Mock venue, synthetic books.  These tests prove wiring and arithmetic; none of them is evidence that any of it makes money."""
import asyncio
import re
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from config.settings import micro_capital_env
from core import engine as eng
from core import funding_executor as fx
from core import invariants as inv
from research import carry_lane as cl
from tests.test_harvest_markout import CHOP, open_harvest, read
from tests.test_micro_rotation import BUY, PX, SELL, burst_engine, doge_micro, rows, trade_through
from tests.test_two_mode_engine import dump, opened

ROOT = Path(__file__).resolve().parents[1]
HI = replace(BUY, score=0.85)
TREND_F = {'ker': 0.45, 'ofi_10s': 0.8, 'atr_pct': 0.10, 'cvd_3m': 1.0e5}


# ============================================================================= pillar 1 (a): the runner flag
def test_the_runner_is_off_by_default_parsed_from_the_env_and_refused_on_live():
    assert eng.PROFILES['demo'].micro_sniper_runner == 'off' and eng.PROFILES['live'].micro_sniper_runner == 'off'
    assert micro_capital_env({'MICRO_SNIPER_RUNNER': ' ON '}) == {'micro_sniper_runner': 'on'}
    assert micro_capital_env({'MICRO_SNIPER_RUNNER': 'off'}) == {'micro_sniper_runner': 'off'} and micro_capital_env({}) == {}
    with pytest.raises(ValueError, match='MICRO_SNIPER_RUNNER'):
        micro_capital_env({'MICRO_SNIPER_RUNNER': 'shadow'})
    live = [x for x in inv.check_profile(replace(eng.PROFILES['live'], micro_sniper_runner='on')) if 'runner' in x]
    assert live == ["live micro_sniper_runner 'on': the runner exit policy is a Demo rehearsal"]
    assert not [x for x in inv.check_profile(replace(eng.PROFILES['demo'], micro_sniper_runner='on')) if 'runner' in x]


def test_runner_grade_needs_the_flag_a_demo_micro_auto_trend_entry_and_both_scores_at_80(tmp_path):
    e = burst_engine(tmp_path, micro_sniper_runner='on')
    e.diag = {'consensus': 0.85}
    assert e.runner_grade('SWARM', 'AUTO', HI) and e.runner_grade('SWARM_OVERRIDE', 'AUTO', HI)
    assert e.runner_grade('SWARM', 'AUTO', replace(BUY, score=0.80))                       # the floor itself
    for path in ('SCALP', 'MR', 'JEV', 'OPERATOR'):
        assert not e.runner_grade(path, 'AUTO', HI), path
    assert not e.runner_grade('SWARM', 'TEST', HI) and not e.runner_grade('SWARM', 'AUTO', None)
    assert not e.runner_grade('SWARM', 'AUTO', replace(BUY, score=0.79))                   # the decision score is under 80 %
    assert not e.runner_grade('SWARM', 'AUTO', replace(BUY, score=float('nan')))
    for diag in ({'consensus': 0.79}, {'consensus': None}, {}, None):                      # raw consensus low or UNKNOWN
        e.diag = diag
        assert not e.runner_grade('SWARM', 'AUTO', HI), diag
    e.diag = {'consensus': 0.9}
    e.p = replace(e.p, mode='live')
    assert not e.runner_grade('SWARM', 'AUTO', HI)                                         # never on a live profile
    off = burst_engine(tmp_path / 'b')
    off.diag = {'consensus': 0.9}
    assert not off.runner_grade('SWARM', 'AUTO', HI)                                       # the shipped default
    big = burst_engine(tmp_path / 'c', wallet=5000.0, micro_sniper_runner='on')
    big.diag = {'consensus': 0.9}
    assert not big.runner_grade('SWARM', 'AUTO', HI)                                       # not a micro account


def test_the_entry_bracket_and_its_net_rr_do_not_see_the_runner(tmp_path):
    on, off = burst_engine(tmp_path, micro_sniper_runner='on'), burst_engine(tmp_path / 'b')
    for path in ('SWARM', 'SWARM_OVERRIDE'):
        a, b = on.bracket_plan(path, TREND_F), off.bracket_plan(path, TREND_F)
        assert {k: a[k] for k in ('sl_pct', 'tp_pct', 'rr_net', 'be_wr', 'src')} == {k: b[k] for k in ('sl_pct', 'tp_pct', 'rr_net', 'be_wr', 'src')}
        assert a['tp_pct'] < on.p.micro_runner_tp_pct                                      # the gate judges the real bracket, not the 2.5 % cap


async def swarm_fill(root, score, **prof):
    e, venue, feed = await doge_micro(root, **prof)
    e.diag = {'consensus': score}
    task = asyncio.ensure_future(e.execute('BUY', replace(BUY, score=score), 'SWARM'))
    await asyncio.sleep(0.8)
    await trade_through(feed)
    res = await task
    assert res['ok'] and e.pos, res
    return e, venue, feed


async def test_a_runner_fill_rests_its_tp_at_the_cap_keeps_the_planned_tp_and_trails_by_atr(tmp_path):
    e, venue, feed = await swarm_fill(tmp_path, 0.85, micro_sniper_runner='on')
    p = e.pos
    assert p['runner'] is True and p['tp'] == pytest.approx(p['entry'] * 1.025, abs=2e-5) and p['tp_plan'] < p['tp']
    assert p['tp_plan'] == pytest.approx(p['entry'] * (1 + p['tp_pct'] / 100.0), abs=2e-5)  # the bracket the gates judged
    assert p['sl'] == pytest.approx(p['entry'] * (1 - p['sl_pct'] / 100.0), abs=2e-5)        # the stop is the planned stop
    assert any('RUNNER' in ev['message'] for ev in e.events)
    # +0.60 % MFE: the plain trail would sit 0.20 % behind the peak; the runner uses max(0.20, 2 x ATR 0.15) = 0.30 %
    peak = round(p['entry'] * 1.006, 5)
    p.update(peak=peak, mfe_pct=0.6, atr_pct=0.15)
    await e._manage_soft(p, 1, peak, 0.6)
    assert p['trail_gap'] == pytest.approx(0.30) and p['soft_kind'] == 'TRAIL'
    assert p['soft_stop'] == pytest.approx(peak * (1 - 0.30 / 100.0), abs=2e-5)
    # past +0.70 % the plain trail tightens to 0.15 %: a runner does not; an unknown ATR falls back to the plain 0.20 %
    peak2 = round(p['entry'] * 1.009, 5)
    p.update(peak=peak2, mfe_pct=0.9)
    await e._manage_soft(p, 1, peak2, 0.9)
    assert p['trail_gap'] == pytest.approx(0.30)
    p.update(atr_pct=None)
    await e._manage_soft(p, 1, peak2, 0.9)
    assert p['trail_gap'] == pytest.approx(0.20)

    plain, _, _ = await swarm_fill(tmp_path / 'b', 0.85)                                    # flag off: the bracket TP
    q = plain.pos
    assert q['runner'] is False and q['tp'] == q['tp_plan'] and not any('RUNNER' in ev['message'] for ev in plain.events)
    q.update(peak=round(q['entry'] * 1.009, 5), mfe_pct=0.9, atr_pct=0.15)
    await plain._manage_soft(q, 1, q['peak'], 0.9)
    assert q['trail_gap'] == pytest.approx(0.15)

    low, _, _ = await swarm_fill(tmp_path / 'c', 0.78, micro_sniper_runner='on')            # under 80 %: not a runner
    assert low.pos['runner'] is False and low.pos['tp'] == low.pos['tp_plan']


def test_a_runner_skips_the_chop_ejects_and_everything_else_still_gets_them(tmp_path):
    # 11-10 (g): a runner now carries its own 420 s stale schedule from the fill (tests/test_runner_guards_2026_11_10.py);
    # the bare dict below has none, which is what micro_runner_stale_s = 0 leaves
    e = burst_engine(tmp_path, eject=True, eject_fast=True)
    now = 1_000.0
    stale = {'opened': now - 500.0, 'mfe_pct': 0.0, 'tp1_filled': False}
    f = {'px_vel_15s': 0.0}
    assert e.eject_due(stale, now) and e.eject_fast_due(stale, now, f)
    assert not e.eject_due({**stale, 'runner': True}, now) and not e.eject_fast_due({**stale, 'runner': True}, now, f)
    scalp = {**stale, 'eject_s': 45.0, 'eject_mfe': 1e9}                                     # the scalp lane's own schedule is untouched
    assert e.eject_due(scalp, now)


async def test_the_ledger_row_says_runner_and_keeps_the_planned_tp(tmp_path):
    e, venue, feed = await swarm_fill(tmp_path, 0.85, micro_sniper_runner='on')
    plan = e.pos['tp_plan']
    await e.close('MANUAL')
    await e.sync()
    assert e.history and e.history[0]['runner'] is True and e.history[0]['tp_plan'] == plan
    e2, _, _ = await swarm_fill(tmp_path / 'b', 0.85)
    await e2.close('MANUAL')
    await e2.sync()
    assert e2.history[0]['runner'] is None                                                  # absent, not False: older rows read the same


# ============================================================================= pillar 1 (b): shadow harvests keep their markouts
async def test_a_shadow_harvest_writes_its_quote_markouts_and_sends_no_order(tmp_path):
    e, venue, feed = await doge_micro(tmp_path)                                             # the shipped default: micro_harvester 'shadow'
    assert e.p.micro_harvester == 'shadow'
    await e.chop_scalp(BUY, {**feed.features(), **CHOP})
    assert e.pos is None and not venue.fills and not [r for r in rows(tmp_path) if r.get('qty_req')]
    assert [r['outcome'] for r in rows(tmp_path)] == ['SHADOW_SCALP']
    [key] = list(e.harvest_log.open)
    r = e.harvest_log.open[key]
    assert r['shadow'] is True and r['swarm_ok'] is True and r['filled'] is False and r['side'] == 'BUY'
    assert r['quote_px'] == feed.bid and r['closed_t'] is not None and r['features']['ker'] == pytest.approx(0.2)
    now = time.time()
    e.clock = lambda: now + 61.0
    e.hlog_step({'price': feed.price()})
    [row] = read(e.harvest_log.path)
    assert row['complete'] is True and row['shadow'] is True and row['f_sig'] == {}
    assert set(row['q_sig']) == {'1s', '5s', '15s', '30s', '60s'}


async def test_a_shadow_record_says_whether_the_swarm_would_have_backed_it(tmp_path):
    for i, (d, ok) in enumerate(((SELL, False), (None, False), (replace(BUY, score=0.70), False), (replace(BUY, veto=True), False),
                                 (BUY, True))):
        e, venue, feed = await doge_micro(tmp_path / str(i))
        await e.chop_scalp(d, {**feed.features(), **CHOP})                                   # the tape says BUY (OFI +0.6)
        [r] = e.harvest_log.open.values()
        assert r['shadow'] is True and r['swarm_ok'] is ok and r['side'] == 'BUY', (i, r['swarm_ok'])


async def test_an_armed_harvest_order_is_not_flagged_shadow_and_a_non_harvest_shadow_scalp_is_not_logged(tmp_path):
    e, venue, feed = await open_harvest(tmp_path)
    assert 'shadow' not in e.harvest_log.open[e.pos['harvest_key']]
    e2, venue2, feed2 = await doge_micro(tmp_path / 'b', chop_scalper='shadow', scalp_ker_max=0.42)
    await e2.chop_scalp(BUY, {**feed2.features(), **CHOP, 'ker': 0.35})                      # KER >= 0.28: the plain scalper, not the harvester
    assert not e2.harvest_log.open


# ============================================================================= pillar 2: simulated carry, discrete settlement
LINK = fx.FundingQuote('LINK', 0.0001, 0.00005, 20.0, 20.01)          # BN 1 bps / 8 h, HL 0.5 bps / h: short HL, long BN
LINK_HOT = fx.FundingQuote('LINK', 0.0001, 0.00008, 20.0, 20.01)      # the HL rate after the instant: next hour's estimate
LINK_FLIP = fx.FundingQuote('LINK', 0.0006, 0.00005, 20.0, 20.01)
LINK_UNKNOWN = fx.FundingQuote('LINK', None, None, None, None)
T0 = 1_000_000.0                                                      # next HL hour: 1 000 800; next BN 8 h instant: 1 008 000


def discrete(t, **kw):
    return cl.CarrySim(1000.0, clock=lambda: t[0], settlement='discrete', **kw)


async def test_funding_is_paid_only_at_the_settlement_instants_that_passed():
    t = [T0]
    sim = discrete(t)
    await sim.step([LINK])
    qty = sim.notional_per_leg / 20.0
    t[0] += 300.0
    v = await sim.step([LINK])                                         # no instant yet: the continuous book would show 5 min of funding
    assert v['positions'][0]['funding_usdt'] == 0.0 and v['positions'][0]['settlements'] == {'bn': 0, 'hl': 0}
    assert v['settlement'] == 'discrete' and v['next_settlement'] == {'bn': 1_008_000.0, 'hl': 1_000_800.0}
    t[0] = 1_000_900.0
    v = await sim.step([LINK_HOT])                                     # one HL hour passed: paid at the rate seen BEFORE it (0.5 bps)
    assert v['positions'][0]['settlements'] == {'bn': 0, 'hl': 1}
    assert sim.pairs['LINK']['hl'].funding == pytest.approx(qty * 20.01 * 0.00005)
    assert sim.pairs['LINK']['bn'].funding == 0.0
    t[0] = 1_008_100.0
    v = await sim.step([LINK])                                         # 2 more HL hours at the hot rate, and the first BN print
    p = sim.pairs['LINK']
    assert v['positions'][0]['settlements'] == {'bn': 1, 'hl': 3}
    assert p['hl'].funding == pytest.approx(qty * 20.01 * (0.00005 + 2 * 0.00008))
    assert p['bn'].funding == pytest.approx(-qty * 20.0 * 0.0001)      # the BN long pays the full 8 h rate once
    assert v['funding_total_usdt'] == pytest.approx(p['hl'].funding + p['bn'].funding, abs=1e-4)


async def test_a_pair_opened_and_closed_between_two_instants_earns_no_funding():
    t = [T0]
    sim = discrete(t)
    await sim.step([LINK])
    t[0] += 500.0
    await sim.step([LINK_FLIP])
    assert 'LINK' not in sim.pairs and sim.closed[-1]['funding_usdt'] == 0.0 and sim.funding_closed == 0.0
    old = cl.CarrySim(1000.0, clock=lambda: t[0])                      # the continuous estimate credited that interval
    t[0] = T0
    await old.step([LINK])
    t[0] += 500.0
    await old.step([LINK_FLIP])
    assert old.settlement == 'continuous' and old.closed[-1]['funding_usdt'] != 0.0


async def test_instants_skipped_by_unknown_quotes_are_settled_at_the_next_known_refresh():
    t = [T0]
    sim = discrete(t)
    await sim.step([LINK])
    qty = sim.notional_per_leg / 20.0
    t[0] = 1_000_900.0
    v = await sim.step([LINK_UNKNOWN])                                 # UNKNOWN: hold, settle nothing, guess nothing
    assert 'LINK' in sim.pairs and v['positions'][0]['funding_usdt'] == 0.0
    t[0] = 1_004_500.0
    v = await sim.step([LINK])                                         # two HL hours passed in total, both at the last known rate
    assert v['positions'][0]['settlements'] == {'bn': 0, 'hl': 2}
    assert sim.pairs['LINK']['hl'].funding == pytest.approx(2 * qty * 20.01 * 0.00005)


async def test_the_net_apr_floor_and_the_constructor_guards():
    warm = fx.FundingQuote('LINK', 0.0001, 0.0000425, 20.0, 20.01)     # spread 2.4 bps / 8 h: gross 26.3 %, net 24.3 %
    t = [T0]
    loose = cl.CarrySim(1000.0, clock=lambda: t[0], min_spread_8h=2.0)
    strict = cl.CarrySim(1000.0, clock=lambda: t[0], min_spread_8h=2.0, min_net_apr_pct=25.0)
    await loose.step([warm])
    v = await strict.step([warm])
    assert 'LINK' in loose.pairs and 'LINK' not in strict.pairs and v['top'][0]['status'] == 'BELOW'
    assert v['rule']['min_net_apr_pct'] == 25.0 and v['top'][0]['net_apr_pct'] == pytest.approx(24.32, abs=0.05)
    await strict.step([LINK])                                          # 3 bps / 8 h: net 30.9 %
    assert 'LINK' in strict.pairs
    with pytest.raises(ValueError, match='settlement'):
        cl.CarrySim(1000.0, settlement='hourly')
    with pytest.raises(ValueError):
        cl.CarrySim(1000.0, min_net_apr_pct=float('nan'))
    assert cl.CarrySim(1000.0).settlement == 'continuous' and cl.CarrySim(1000.0).min_net_apr == 0.0   # library default unchanged


async def test_the_demo_loop_runs_the_book_with_discrete_settlement_and_the_profile_floor():
    assert eng.PROFILES['demo'].carry_min_net_apr_pct == 25.0
    events = []
    e0 = SimpleNamespace(account={'wallet': 1000.0}, p=eng.PROFILES['demo'], event=events.append)
    r = SimpleNamespace(engines={'SOLUSDT': e0}, carry_state=None, carry_sim_state=None)
    await eng.Runner.funding_loop(r, once=True, fetch=lambda: [LINK])
    assert r.carry_sim.settlement == 'discrete' and r.carry_sim.min_net_apr == 25.0
    st = r.carry_sim_state
    assert st['settlement'] == 'discrete' and set(st['next_settlement']) == {'bn', 'hl'} and st['mode'].startswith('SIMULATED')
    assert [x['coin'] for x in st['positions']] == ['LINK'] and st['positions'][0]['settlements'] == {'bn': 0, 'hl': 0}
    p = r.carry_sim.pairs['LINK']
    assert type(p['bn']).__name__ == type(p['hl']).__name__ == 'PaperPerpVenue'            # both legs simulated: no order leaves the process


def test_the_cockpit_reads_the_key_the_engine_publishes():
    js = (ROOT / 'web_ui' / 'app.js').read_text(encoding='utf-8')
    src = (ROOT / 'core' / 'engine.py').read_text(encoding='utf-8')
    assert "snap['carry_sim']" in src and "snap['carry_lane']" not in src
    if 'ck-carry-book' not in js:
        pytest.skip('web_ui/app.js is not the single-screen cockpit (the 11-08 rework is not in this checkout)')
    assert re.search(r'const sim = s\.carry_sim\b', js)               # it read s.carry_lane: the SIM BOOK card stayed on "SHADOW"
    assert 'sim.funding_total_usdt' in js


# ============================================================================= pillar 3: the regime-flip grace (unchanged, pinned)
async def test_a_regime_flip_cannot_exit_before_60_s_and_needs_20_s_of_flip(tmp_path):
    p = eng.PROFILES['demo']
    assert (p.regime_flip_min_hold_s, p.regime_flip_debounce_s) == (60.0, 20.0)
    e, venue, feed = await opened(tmp_path, regime_flip_min_hold_s=60.0, regime_flip_debounce_s=20.0)
    dump(feed, e.pos['entry'] - 1, feed.features()['cvd_60s'] + 1_050_000)                  # LONG vs CVD 60 s -1.05M
    await e.tick()
    assert e.pos and not e.pos.get('exiting') and e.pos['flip_since'] is not None           # flip seen, clock started
    e.pos['opened'] -= 55.0
    e.pos['flip_since'] -= 25.0                                                             # flip held 25 s, position 55 s old
    await e.tick()
    assert e.pos and not e.pos.get('exiting')                                               # the 60 s grace binds
    e.pos['opened'] -= 6.0                                                                  # 61 s old
    await e.tick()
    assert e.pos['exiting']['reason'] == 'REGIME'

    e2, venue2, feed2 = await opened(tmp_path / 'b', regime_flip_min_hold_s=60.0, regime_flip_debounce_s=20.0)
    dump(feed2, e2.pos['entry'] - 1, feed2.features()['cvd_60s'] + 1_050_000)
    e2.pos['opened'] -= 100.0                                                               # the 10:26 XRP case: 100 s old ...
    await e2.tick()
    assert e2.pos and not e2.pos.get('exiting')                                             # ... a fresh flip still waits its 20 s
    e2.pos['flip_since'] -= 20.5
    await e2.tick()
    assert e2.pos['exiting']['reason'] == 'REGIME'


# ============================================================================= pillar 4 + launcher
def test_the_micro_launcher_arms_shadow_harvest_the_runner_and_a_15_u_bankroll_on_demo_only():
    text = (ROOT / 'run.bat').read_text(encoding='utf-8')
    micro = text[text.index('\n:MICRO'):text.index('\n:LIVE')]
    rest = text.replace(micro, '')
    sets = dict(re.findall(r'^set "(MICRO_[A-Z_]+)=([^"]*)"', micro, flags=re.M))
    # 11-11 (6a621f8) added MICRO_OFI_MIN / MICRO_MIN_EDGE_BPS without updating this pin; 11-13 adds MICRO_NET_EV_GATE=on
    # and the owner rejected MICRO_OFI_MIN=0.45 (the profile floor 0.65 stays)
    assert sets == {'MICRO_CAPITAL_MODE': 'on', 'MICRO_IOC': 'on', 'MICRO_HARVESTER': 'shadow', 'MICRO_SNIPER_RUNNER': 'on',
                    'MICRO_CAPITAL_BANKROLL': '15', 'MICRO_EXIT_POLICY': 'patient',
                    'MICRO_MIN_EDGE_BPS': '7.0', 'MICRO_NET_EV_GATE': 'on'}
    assert '--mode demo' in micro and '--mode live' not in micro
    assert not re.findall(r'^set "MICRO_', rest, flags=re.M)                                 # demo / live / test set none of them
    env = micro_capital_env(sets)                                                            # the launcher's values parse
    assert env['micro_harvester'] == 'shadow' and env['micro_sniper_runner'] == 'on' and env['micro_bankroll'] == 15.0
    assert env['micro_net_ev_gate'] == 'on'


def test_a_15_u_rehearsal_sizes_from_15_u_and_the_loss_at_the_stop_stays_under_5_cents(tmp_path):
    e = burst_engine(tmp_path, wallet=5000.0, micro_capital='on', micro_bankroll=15.0)
    assert e.micro_equity() == 15.0
    e.all_rows = [{'pnl': -0.0421, 'micro': True}]                                           # today's realized micro PnL
    assert e.micro_equity() == pytest.approx(14.9579)
    s, lev = e.micro_sizer(), e.p.leverage
    target = s.compound_target(e.micro_equity(), lev, e.micro_pnl())
    assert target == pytest.approx(0.20 * 14.9579 * lev * (1 - 5.0 * 0.0421))                # the accelerator shrinks the lot after a loss
    for sl, fees in ((0.22, 0.08), (0.22, 0.12), (0.25, 0.12)):                              # maker / IOC entry, widest override stop
        ceiling = s.risk_notional(sl, fees)
        q, why = s.qty(PX, 1.0, 1.0, 5.0, equity=e.micro_equity(), leverage=lev, risk_notional=ceiling, pnl=e.micro_pnl())
        assert q and q * PX <= ceiling + 1e-9 and s.loss_at_stop(q * PX, sl, fees) <= 0.05 + 1e-9, (sl, fees, why)
    assert s.risk_notional(0.22, 0.08) == pytest.approx(16.67, abs=0.01)                     # the ceiling, whatever the bankroll
    assert s.daily_kill_usdt == 0.15 and s.max_loss_usdt == 0.05
