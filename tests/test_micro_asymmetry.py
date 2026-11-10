"""11-10 exit-asymmetry directive (docs/harness/plan_micro_asymmetry_cure.md).  Two kinds of test:
  * the 15 overnight ledger rows (data/engine_trades.jsonl, 23:05 Nov 9 - 07:38 Nov 10) pinned as constants: the autopsy's totals
    reproduce, and the premises that do not (SL 0.22 %, "SL never hit", "9 of 10 wins are EJECT-BE", "+0.24 U") are asserted false;
  * the opt-in `micro_exit_policy='patient'` on the mock venue: wiring and arithmetic only.  Nothing here shows that holding longer
    pays: the repo has no tape of that night, so no replay of the counterfactual exists."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from config.settings import micro_capital_env
from core import engine as eng
from core.position_sizer import MicroCapitalSizer
from tests.test_micro_rotation import BUY, DOGE, SELL, burst_engine, doge_micro, rows, trade_through

# (time, coin, side, reason, bracket, hold_s, gross, fee, net, notional, mfe_pct, taker_fills)
LEDGER = [
    ('11-09 23:05', 'XRPU', 'S', 'TP', 'HARVEST', 25, 0.00782, 0.00204, 0.00578, 5.112, 0.013, 0),
    ('11-09 23:15', 'XRPU', 'S', 'EJECT-BE', 'HARVEST', 107, 0.0023, 0.00204, 0.00026, 5.104, 0.05, 0),
    ('11-10 00:59', 'XRPU', 'S', 'EJECT-BE', 'HARVEST', 77, 0.00235, 0.00206, 0.00029, 5.14, 0.041, 0),
    ('11-10 01:00', '1000', 'S', 'EJECT-BE', 'HARVEST', 58, 0.00248, 0.002, 0.00047, 5.006, 0.051, 0),
    ('11-10 01:44', 'XRPU', 'B', 'EJECT', 'HARVEST', 215, -0.00799, 0.00206, -0.01005, 5.15, 0.014, 0),
    ('11-10 01:53', 'DOGE', 'S', 'EJECT', 'HARVEST', 227, -0.0037, 0.00207, -0.00577, 5.168, 0.021, 0),
    ('11-10 03:02', 'XRPU', 'S', 'EJECT-BE', 'HARVEST', 123, 0.00235, 0.00206, 0.00029, 5.143, 0.041, 0),
    ('11-10 03:26', '1000', 'B', 'EJECT', 'HARVEST', 203, -0.00321, 0.002, -0.00522, 5.014, 0, 0),
    ('11-10 03:54', '1000', 'B', 'EJECT-BE', 'SCALP', 54, 1e-05, 0.0, 1e-05, 0.005, 0.222, 0),
    ('11-10 04:22', '1000', 'S', 'EJECT-BE-LOSS', 'HARVEST', 105, -0.0078, 0.00307, -0.01087, 5.104, 0, 3),
    ('11-10 05:06', 'XRPU', 'B', 'EJECT-BE', 'HARVEST', 62, 0.00235, 0.00205, 0.0003, 5.133, 0.078, 0),
    ('11-10 05:18', '1000', 'S', 'EJECT-BE', 'HARVEST', 51, 0.00208, 0.00201, 7e-05, 5.036, 0.07, 0),
    ('11-10 07:26', 'DOGE', 'S', 'EJECT', 'HARVEST', 205, -0.00444, 0.00207, -0.00651, 5.165, 0, 0),
    ('11-10 07:31', 'XRPU', 'S', 'EJECT-BE', 'SCALP', 77, 0.00517, 0.00207, 0.0031, 5.18, 0.1, 0),
    ('11-10 07:38', 'XRPU', 'S', 'EJECT-BE', 'HARVEST', 163, 0.00235, 0.00206, 0.00029, 5.155, 0.087, 0),
]
NET, GROSS, FEE, HOLD, NOTIONAL, MFE = 8, 6, 7, 5, 9, 10


# ============================================================================= what the ledger says
def test_the_autopsy_totals_reproduce():
    net = [r[NET] for r in LEDGER]
    wins, losses = [x for x in net if x > 0], [x for x in net if x <= 0]
    assert (len(LEDGER), len(wins), len(losses)) == (15, 10, 5)
    assert sum(r[GROSS] for r in LEDGER) == pytest.approx(0.00212, abs=2e-5) and sum(r[FEE] for r in LEDGER) == pytest.approx(0.02966, abs=2e-5)
    assert sum(net) == pytest.approx(-0.02755, abs=2e-5)
    assert sum(wins) / len(wins) == pytest.approx(0.00109, abs=2e-5) and sum(losses) / len(losses) == pytest.approx(-0.00768, abs=2e-5)
    assert (sum(wins) / len(wins)) / abs(sum(losses) / len(losses)) == pytest.approx(0.141, abs=0.002)       # the payoff ratio


def test_the_premises_the_ledger_contradicts():
    harvest = [r for r in LEDGER if r[4] == 'HARVEST']
    assert len(harvest) == 13 and sum(1 for r in LEDGER if r[3] == 'TP') == 1                       # 1 TP in 13 harvest trades
    # 1. one of the "15 trades" is a dust remainder of a partial fill (0.005 U notional, no fee): 14 real trades
    assert [r[0] for r in LEDGER if r[NOTIONAL] < 1.0] == ['11-10 03:54']
    # 2. the harvest stop is 0.15 %, not 0.22 %; two losses closed AT that distance (-0.155 %, -0.153 %), one of them through 3 taker fills
    at_stop = [r for r in harvest if r[GROSS] / r[NOTIONAL] * 100 <= -0.15]
    assert [(r[0], r[3], r[11]) for r in at_stop] == [('11-10 01:44', 'EJECT', 0), ('11-10 04:22', 'EJECT-BE-LOSS', 3)]
    assert 0.15 + 0.08 == pytest.approx(0.23)                                                         # SL + fees = the break-even loss
    # 3. 8 of the 10 wins are EJECT-BE scratches (7 harvest + 2 legacy-SCALP rows incl. the dust), 1 TP; none reached the 0.15 % TP
    be_wins = [r for r in LEDGER if r[3] == 'EJECT-BE' and r[NET] > 0]
    assert len(be_wins) == 9 and max(r[MFE] for r in be_wins if r[4] == 'HARVEST') < 0.09           # no harvest scratch ran 60 % of the TP
    # 4. the 3 non-stop dumps never went favourable (MFE <= 0.021 %): they were wrong from the fill, not shaken out of a winner
    dumps = [r for r in harvest if r[3] == 'EJECT']
    assert len(dumps) == 4 and max(r[MFE] for r in dumps) <= 0.021 and min(r[HOLD] for r in dumps) >= 203
    # 5. the dumps are maker orders (fee = the 0.04 % round trip), not taker spread: the loss is price, not fee
    assert all(r[FEE] == pytest.approx(0.04 / 100 * r[NOTIONAL], rel=0.03) for r in dumps)


def test_why_the_scratch_and_the_dump_are_the_real_exit_rules():
    """Legacy scalp positions carry eject_mfe = 1e9, so at scalp_be_s (45 s) EVERY unfilled one cancels its TP and exits at
    max(touch, BE floor): above the floor that is a small win, below it the order rests until 180 s and then leaves at the touch."""
    scratches = [r for r in LEDGER if r[3] == 'EJECT-BE' and r[4] == 'HARVEST']
    assert len(scratches) == 7 and sum(r[NET] for r in scratches) == pytest.approx(0.00197, abs=1e-5)
    assert all(51 <= r[HOLD] <= 163 for r in scratches)


# ============================================================================= the counterfactual the directive implies
def barrier_ev(x, tp=0.15, sl=0.15, win_net=0.11, loss_net=0.23):
    """Driftless price at x % from entry, absorbing barriers +tp / -sl: P(TP first) and the expected NET % of notional."""
    p = (x + sl) / (tp + sl)
    return p, p * win_net - (1 - p) * loss_net


def test_hold_to_the_barriers_is_not_better_than_the_scratch_on_a_random_walk():
    be = 0.006                                                    # % of notional a harvest scratch nets (ledger: +0.005..+0.009)
    p, ev = barrier_ev(0.045)                                     # a trade sitting at the BE floor at 45 s
    assert p == pytest.approx(0.65) and ev == pytest.approx(-0.009) and ev < be
    p, ev = barrier_ev(-0.075)                                    # a typical dump point (-0.064..-0.086 %)
    assert p == pytest.approx(0.25) and ev == pytest.approx(-0.145)
    dump_net = -0.104                                             # ledger: net % of the 3 non-stop dumps (-0.104, -0.112, -0.126)
    assert ev < dump_net                                          # holding costs ~0.02-0.04 % more: the stop exit is taker + slip
    # the break-even hit rate of the barrier bracket is 67.6 %: P(TP first) from entry is 50 % without drift
    assert barrier_ev(0.0)[0] == 0.5 and barrier_ev(0.0)[1] < 0


def test_the_ceiling_utilisation_claim_does_not_survive_the_ledger():
    net = sum(r[NET] for r in LEDGER)
    scale = 21.7 / 5.1
    assert net * scale == pytest.approx(-0.117, abs=0.001)         # the same night at 21.7 U lots: -0.117 U, 78 % of the 0.15 U daily kill
    assert abs(net * scale) / 0.15 > 0.75
    assert 10 * 0.024 == pytest.approx(0.24)                       # the directive's "+0.24 U" counts ten TP wins; the ledger has one TP
    assert sum(r[NET] for r in LEDGER if r[NET] > 0) * scale == pytest.approx(0.047, abs=0.002)   # all ten wins at 21.7 U


def test_21_7_u_is_not_reachable_on_a_5_u_bankroll_whatever_the_curve():
    s = MicroCapitalSizer(max_positions=3, accel_k=5.0)
    ceiling = s.risk_notional(0.15, 0.08)
    assert ceiling == pytest.approx(21.74, abs=0.01)
    # margin share of one of 3 slots at E = 5 U and 5x: 0.85 x 5 x 5 / 3 = 7.08 U - however large the realized pnl
    assert max(s.compound_target(5.0 + p, 5.0, p) for p in (0.1, 0.5, 1.0)) <= 0.85 * 6.0 * 5 / 3 + 1e-9
    assert 21.74 / 5 * 3 > 0.85 * 5                                # 3 lots of 21.7 U need 13.0 U of margin on a 5 U bankroll (4.25 U allowed)
    # the bankroll that carries three ceiling lots: 3 x 4.35 U margin <= 0.85 x E  ->  E >= 15.4 U
    assert 3 * ceiling / 5 / 0.85 == pytest.approx(15.35, abs=0.05)
    q, _ = s.qty(0.0711, 1.0, 1.0, 5.0, equity=16.0, leverage=5.0, risk_notional=ceiling, pnl=1.0)
    assert q * 0.0711 == pytest.approx(ceiling, abs=0.1)           # with a 16 U bankroll + 1 U won, the 0.05 U ceiling is what binds


# ============================================================================= the opt-in patient policy
def pos(side='SELL', entry=1.1000, **kw):
    return {'side': side, 'entry': entry, 'opened': 0.0, 'mfe_pct': 0.0, 'sl': entry * 1.0015, 'qty': 4.6, 'entry_fee_bps': 2.0, **kw}


def test_the_policy_defaults_to_legacy_and_parses():
    assert eng.PROFILES['demo'].micro_exit_policy == 'legacy' and eng.PROFILES['live'].micro_exit_policy == 'legacy'
    assert micro_capital_env({'MICRO_EXIT_POLICY': ' Patient '}) == {'micro_exit_policy': 'patient'}
    with pytest.raises(ValueError, match='MICRO_EXIT_POLICY'):
        micro_capital_env({'MICRO_EXIT_POLICY': 'never'})


async def open_harvest(tmp_path, **prof):
    e, venue, feed = await doge_micro(tmp_path, micro_harvester='on', micro_harvest_ttl_s=3.0, scalp_min_edge_bps=0.0,
                                      scalp_align3=False, **prof)
    e.diag = {'consensus': 0.9, 'edge_bps': 40.0}
    f = {**feed.features(), 'ker': 0.20, 'spread_bps': 0.5, 'ofi_10s': 0.6, 'atr_pct': 0.10, 'cvd_3m': 0.0, 'px_vel_60s': 0.0}
    task = asyncio.ensure_future(e.chop_scalp(BUY, f))
    await asyncio.sleep(0.5)
    await trade_through(feed)
    await task
    assert e.pos
    return e, venue, feed


async def test_a_patient_harvest_position_carries_the_schedule_legacy_keeps_45s(tmp_path):
    e, _, _ = await open_harvest(tmp_path / 'a', micro_exit_policy='patient')
    assert (e.pos['eject_s'], e.pos['eject_hard_s'], e.pos['patient'], e.pos['patient_mfe']) == (180.0, 180.0, True, 0.09)
    old, _, _ = await open_harvest(tmp_path / 'b')
    assert old.pos['eject_s'] == 45.0 and 'patient' not in old.pos and old.pos['eject_mfe'] == 1e9


def test_the_patient_scratch_needs_a_trade_that_ran_and_has_aged(tmp_path):
    e = burst_engine(tmp_path)
    p = pos(eject_s=180.0, patient=True, patient_mfe=0.09)
    assert not e.eject_due(p, 100.0)                                            # too young, whatever the MFE
    assert not e.eject_due({**p, 'mfe_pct': 0.05}, 400.0)                       # never ran 60 % of the TP: no scratch, ever
    assert e.eject_due({**p, 'mfe_pct': 0.10}, 181.0)
    assert not e.eject_due({**p, 'mfe_pct': 0.10, 'tp1_filled': True}, 400.0)
    legacy = pos(eject_s=45.0, eject_mfe=1e9)
    assert e.eject_due(legacy, 46.0) and not e.eject_due(legacy, 44.0)          # unchanged: the 45 s scratch of every unfilled scalp


def test_a_patient_position_leaves_at_a_negative_touch_only_on_verified_toxic_flow(tmp_path):
    e = burst_engine(tmp_path)
    p = pos(patient=True, flow_limit=200_000.0)
    quiet = {'vpin': 0.50, 'cvd_60s': 0.0}
    e.adverse_flow = lambda pp: 10_000.0
    assert e.eject_escalate_ok(p, quiet, -0.07, 180.0) is False
    p['opened'] = -10_000.0                                                     # time alone never escalates (legacy: after hard_s + 120 s)
    assert e.eject_escalate_ok(p, quiet, -0.07, 180.0) is False
    assert e.eject_escalate_ok(p, {**quiet, 'vpin': None}, -0.07, 180.0) is False   # VPIN unknown is not toxic
    assert e.eject_escalate_ok(p, {**quiet, 'vpin': 0.79}, -0.07, 180.0) is False
    assert e.eject_escalate_ok(p, {**quiet, 'vpin': 0.80}, -0.07, 180.0) is True
    e.adverse_flow = lambda pp: 200_000.0
    assert e.eject_escalate_ok({**p, 'eject_adverse': None}, quiet, -0.07, 180.0) is True      # adverse flow >= the trade's limit
    e.adverse_flow = lambda pp: 0.0
    flip = 1.0e6 * e.feed.cfg.usd_scale                                         # regime_flip_usd x the feed's USD scale
    assert e.eject_escalate_ok(p, {**quiet, 'cvd_60s': flip * 0.99}, -0.07, 180.0) is False
    assert e.eject_escalate_ok(p, {**quiet, 'cvd_60s': flip}, -0.07, 180.0) is True              # a SELL while CVD 60 s >= +$1M x scale
    assert e.eject_escalate_ok(p, quiet, +0.05, 180.0) is True                  # a touch exit that books a profit is not a "negative touch"
    legacy = pos(eject_s=45.0)
    assert e.eject_escalate_ok({**legacy, 'opened': -10_000.0}, quiet, -0.07, 180.0) is True   # legacy: the 11-09 time bound still dumps


def test_the_patient_scratch_floor_is_one_tick_above_the_fees(tmp_path):
    e = burst_engine(tmp_path)
    e.spec = {**e.spec, 'tick': 0.0001}
    for side, entry in (('BUY', 1.1000), ('SELL', 1.1000)):
        d = 1 if side == 'BUY' else -1
        old, new = e.eject_floor(pos(side, entry)), e.eject_floor(pos(side, entry, patient=True))
        assert d * (new - old) == pytest.approx(0.0001)                         # exactly one tick better for us
        gross = d * (new / entry - 1) * 100
        assert gross > 0.04 and gross == pytest.approx(0.0545, abs=0.002)       # strictly over the 0.04 % maker round trip


async def test_a_quiet_underwater_patient_position_is_not_dumped_legacy_is(tmp_path):
    for policy, expect in (('patient', None), ('legacy', 'EJECT-BE')):
        e, venue, feed = await open_harvest(tmp_path / policy, micro_exit_policy=policy)
        e.pos['opened'] -= 200.0                                                # 200 s old, TP unfilled, MFE ~0
        e.pos['mfe_pct'] = 0.0
        await e.sync()                                                          # sync() runs manage() on the venue view
        got = (e.pos.get('exiting') or {}).get('reason')
        assert got == expect, (policy, got)
    e, venue, feed = await open_harvest(tmp_path / 'ran', micro_exit_policy='patient')
    e.pos['opened'] -= 200.0
    e.pos['mfe_pct'] = 0.12                                                     # ran 80 % of the TP, then stalled
    await e.sync()
    assert (e.pos.get('exiting') or {}).get('reason') == 'EJECT-BE' and e.pos['exiting']['floor'] is not None
