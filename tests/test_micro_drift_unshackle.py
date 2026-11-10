"""11-10 'unshackle' directive (docs/harness/plan_micro_drift_unshackle.md).  The live cockpit said DRIFT LOCK on all four coins and the
order log showed two PEPE candidates refused by MICRO ALIGN OFI 0.65.  Checked against the code and the session funnel:
  * DRIFT LOCK was a radar LABEL taken from the CVD regime name; the lock itself is a churn gate of the swarm AUTO path, which the
    chop harvester (chop_scalp -> SCALP) never runs through.  It was the first blocker in 8 of ~5 300 funnel samples.
  * both PEPE rejections had KER 0.40 / 0.31 >= 0.28: legacy-scalp band = TREND lane, where micro ALIGN (OFI 0.65) is the rule.
The changes are label / instrumentation / one explicit knob; none opens a new order path.  Mock venue, wiring only."""
from dataclasses import replace

from core import engine as eng
from tests.test_micro_rotation import BUY, SELL, burst_engine, doge_micro, rows

DRIFT = {'name': 'DRIFT', 'allow': ('BUY', 'SELL'), 'label': 'DRIFT (48s/3m CONFLICT · WAIT)', 'why': 'CVD 60s +0.1M · 3m -0.4M'}
CHOP = {'ker': 0.20, 'spread_bps': 0.5, 'atr_pct': 0.10, 'cvd_3m': 0.0, 'px_vel_60s': 0.0, 'ofi_10s': -0.40}
DIAG = {'consensus': 0.9, 'edge_bps': 40.0}


def drift_engine(tmp_path, ker, **prof):
    prof.setdefault('micro_align', True)
    e = burst_engine(tmp_path, **prof)
    e.reg, e.features, e.diag = DRIFT, {**CHOP, 'ker': ker}, DIAG
    return e


def gates(e):
    return [g for g, _ in e.churn_checks(SELL, DIAG)[0]]


# ============================================================================= pillar 1: drift
def test_the_chop_lane_is_not_judged_by_the_drift_lock_the_trend_lane_still_is(tmp_path):
    chop, trend = drift_engine(tmp_path, 0.20), drift_engine(tmp_path / 'b', 0.40)
    assert chop.drift_exempt() and not trend.drift_exempt()
    assert 'DRIFT' not in gates(chop) and 'MICRO_KER' in gates(chop)             # the swarm AUTO entry is still refused, by its own gate
    assert 'DRIFT' in gates(trend)                                              # KER 0.40 = trend lane: unchanged
    assert 'DRIFT' in gates(drift_engine(tmp_path / 'c', 0.20, micro_harvester='off'))
    assert 'DRIFT' in gates(drift_engine(tmp_path / 'd', 0.20, micro_align=False))
    big = drift_engine(tmp_path / 'e', 0.20, wallet=5000.0)                     # not a micro account
    assert not big.drift_exempt() and 'DRIFT' in gates(big)


def test_the_cockpit_label_and_the_flow_flag_stop_claiming_a_lock_the_chop_lane_does_not_obey(tmp_path):
    chop, trend = drift_engine(tmp_path, 0.20), drift_engine(tmp_path / 'b', 0.40)
    assert chop.radar()['label'] == 'CHOP HARVEST' and trend.radar()['label'] == 'DRIFT LOCK'
    assert chop.snapshot()['flow']['drift_lock'] is False and trend.snapshot()['flow']['drift_lock'] is True


def test_no_trend_entry_gains_a_path_below_the_micro_ker_floor(tmp_path):
    """The exemption is only safe because MICRO_KER refuses every non-harvest AUTO entry under KER 0.28."""
    e = drift_engine(tmp_path, 0.20)
    blk = e.micro_entry_block('SELL', {**CHOP, 'ker': 0.20, 'ofi_10s': -0.90})
    assert [g for g, _ in blk] == ['MICRO_KER']                                  # even with perfect OFI and no CVD conflict


# ============================================================================= pillar 2: the OFI floor
def test_the_two_pepe_rejections_were_trend_lane_candidates_not_chop(tmp_path):
    e = burst_engine(tmp_path, micro_align=True, scalp_ker_max=0.42)           # the shipped demo ceiling (tests/conftest.py uses 0.40)
    for ker, ofi in ((0.40, 0.42), (0.31, 0.32)):                                # 08:21:33 and 08:25:46
        f = {**CHOP, 'ker': ker, 'ofi_10s': ofi}
        assert not e.harvest_lane(f)                                             # KER >= 0.28
        assert e.scalp_candidate(f)[0] == 'BUY'                                  # the scalp rule accepts it ...
        assert [g for g, _ in e.micro_entry_block('BUY', f)] == ['MICRO_ALIGN']  # ... and the trend-lane micro ALIGN (OFI 0.65) refuses it
    f = {**CHOP, 'ker': 0.20, 'ofi_10s': 0.32}                                   # the same OFI inside the chop lane
    assert e.harvest_lane(f) and e.scalp_candidate(f)[0] == 'BUY'


def test_the_harvest_ofi_floor_is_its_own_knob(tmp_path):
    p = eng.PROFILES['demo']
    assert (p.micro_harvest_ofi_min, p.micro_ofi_min, p.scalp_ofi_min, p.micro_harvest_macro_ofi) == (0.30, 0.65, 0.30, 0.30)
    e = burst_engine(tmp_path, micro_harvest_ofi_min=0.45)
    chop = {**CHOP, 'ker': 0.20}
    assert e.scalp_candidate({**chop, 'ofi_10s': 0.44})[0] is None and e.scalp_candidate({**chop, 'ofi_10s': 0.45})[0] == 'BUY'
    assert e.scalp_candidate({**chop, 'ker': 0.35, 'ofi_10s': 0.31})[0] == 'BUY'  # outside the chop lane scalp_ofi_min 0.30 applies
    assert e.scalp_candidate({**chop, 'ofi_10s': 0.29})[0] is None


# ============================================================================= pillar 3: the macro lock in the chop lane
LONG_ONLY = {'name': 'LONG ONLY', 'allow': ('BUY',), 'why': 'x'}
STRONG_UP = {'name': 'UP', 'allow': ('BUY',), 'label': 'STRONG UPTREND (LONG ONLY)', 'why': 'CVD 60s +0.5M · 3m +0.9M'}


def test_at_the_default_threshold_the_5m_macro_no_longer_gates_a_chop_harvest_but_the_cvd_regime_does(tmp_path):
    e = burst_engine(tmp_path, scalp_macro_lock=True)                           # tests/conftest.py switches the 11-08 locks off
    f = {**CHOP, 'ofi_10s': -0.30}
    e.macro = LONG_ONLY
    side, why = e.scalp_candidate(f)
    assert side == 'SELL' and 'macro LONG ONLY softened (OFI -0.30)' in why
    e.macro, e.reg = {'name': 'MIXED', 'allow': ('BUY', 'SELL')}, STRONG_UP         # the cockpit's "MACRO STRONG UPTREND (LONG ONLY)"
    assert e.scalp_candidate(f) == (None, 'MACRO STRONG UPTREND (LONG ONLY) forbids SELL scalp')
    e.macro, e.reg = LONG_ONLY, {}
    assert e.scalp_candidate({**f, 'ker': 0.35})[0] is None                         # trend lane: the 5 m macro stands


# ============================================================================= instrumentation of the silent return
async def test_the_swarm_agreement_gate_of_an_on_harvest_is_counted_not_silent(tmp_path):
    e, venue, feed = await doge_micro(tmp_path, micro_harvester='on', scalp_min_edge_bps=0.0, scalp_align3=False)
    f = {**feed.features(), **CHOP, 'ofi_10s': -0.60}
    await e.chop_scalp(replace(SELL, score=0.60), f)                                 # swarm leans the right way but under 76.8 %
    await e.chop_scalp(BUY, f)                                                       # swarm on the other side
    await e.chop_scalp(replace(SELL, side=None), f)                                  # no side at all
    await e.chop_scalp(replace(SELL, veto=True), f)
    out = e.funnel.outcomes
    assert (out['SWARM_WEAK'], out['SWARM_OPPOSITE'], out['SWARM_NO_SIDE']) == (1, 1, 2)
    assert rows(tmp_path) == [] and not venue.orders                                  # still no order: VESKA is not bypassed
