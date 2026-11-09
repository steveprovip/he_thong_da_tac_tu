"""11-09b over-constrained micro deadlock (docs/harness/plan_micro_unshackle.md): micro net-RR floor 1.10, JUMPY no longer
freezes the scalp lane, 10 s resting floor, IOC thresholds 0.78 / 0.70 / 0.20 / RR 1.05.  Mock venue, synthetic books.
None of it shows that the looser gates make money: they remove refusals, they do not add edge."""
import pytest

from core import engine as eng
from core import regime_classifier as rc
from tests.test_micro_rotation import BUY, PX, burst_engine, doge_micro, rows, route


def regime(e, state):
    e.regime_state = rc.RegimeState(state, 'test', 0.0, rc.POLICIES[state], None, None, None, 1)


# ============================================================================= task 1: micro net-RR floor
def test_the_observed_refusal_is_the_sideways_multiplier_not_the_bracket():
    """MR 0.29 / 0.10 is RR 1.39 (the 10-08x fee-true bracket); SIDEWAYS x0.75 scales TP and SL, the fees stay: RR 1.15."""
    rr = lambda tp, sl: (tp - 0.04) / (sl + 0.08)
    assert rr(0.29, 0.10) == pytest.approx(1.389, abs=1e-3) and rr(0.29 * 0.75, 0.10 * 0.75) == pytest.approx(1.145, abs=1e-3)
    assert 1 / (1 + 1.30) == pytest.approx(0.435, abs=1e-3) and 1 / (1 + 1.10) == pytest.approx(0.476, abs=1e-3)   # win rate to break even


def test_micro_mode_lowers_every_rr_floor_to_1_10_and_nothing_else_does(tmp_path):
    e = burst_engine(tmp_path)                                            # micro active (5 U wallet)
    assert (e.net_rr_eff(), e.scalp_rr_min()) == (1.10, 1.10)
    big = burst_engine(tmp_path / 'b', wallet=5000.0, net_rr_min=1.30, scalp_net_rr_min=1.35)   # the 5 000 U demo account
    assert (big.net_rr_eff(), big.scalp_rr_min()) == (1.30, 1.35)
    off = burst_engine(tmp_path / 'c', micro_capital='off', net_rr_min=1.30)
    assert off.net_rr_eff() == 1.30
    assert eng.PROFILES['demo'].micro_net_rr_min == 1.10 and eng.PROFILES['live'].net_rr_min == 1.30


def test_the_scalp_tp_lift_follows_the_micro_floor(tmp_path):
    e = burst_engine(tmp_path)
    big = burst_engine(tmp_path / 'b', wallet=5000.0, net_rr_min=1.30, scalp_net_rr_min=1.35)
    assert e.scalp_min_tp(0.15) == pytest.approx((0.15 + 0.08) * 1.10 + 0.04)
    assert big.scalp_min_tp(0.15) == pytest.approx((0.15 + 0.08) * 1.35 + 0.04)
    assert big.scalp_min_tp(0.15) > e.scalp_min_tp(0.15)


async def _mr_buy(tmp_path, **prof):
    """The logged setup: an MR entry (fixed bracket 0.29 / 0.10) in a SIDEWAYS regime (stop and TP x0.75)."""
    e, venue, feed = await doge_micro(tmp_path, regime_gate='on', mr_bracket='fixed', gtx_ttl_s=0.2, **prof)
    regime(e, rc.SIDEWAYS)
    return e, await e.execute('BUY', BUY, 'MR')


async def test_the_logged_doge_setup_passes_at_1_10_with_no_lift_needed(tmp_path):
    e, res = await _mr_buy(tmp_path / 'a')
    r = rows(tmp_path / 'a')[-1]
    assert r['qty_req'] and 5.05 <= r['qty_req'] * PX <= 6.0              # reached the venue (unfilled on the static book)
    assert not any('NET RR' in ev['message'] for ev in e.events) and r['tp_lift'] is None   # RR 1.145 >= 1.10


async def test_without_the_lift_a_1_30_floor_refuses_the_logged_setup(tmp_path):
    old, res = await _mr_buy(tmp_path, micro_net_rr_min=1.30, micro_tp_lift=False)
    assert not res['ok'] and res['error'].startswith('ZEPHR NET RR 1.15 < 1.3 (TP 0.217% / SL 0.075% + fees 0.080%)')


# ---- TP lift (11-09c): re-lift TP to the floor after the regime multipliers instead of refusing
def test_min_tp_for_is_the_scalp_formula_with_any_floor(tmp_path):
    e = burst_engine(tmp_path)
    assert e.min_tp_for(0.075, 'gtx', 1.30) == pytest.approx((0.075 + 0.08) * 1.30 + 0.04) == pytest.approx(0.2415)
    assert e.min_tp_for(0.15, 'gtx', e.scalp_rr_min()) == pytest.approx(e.scalp_min_tp(0.15))
    assert e.min_tp_for(0.075, 'ioc', 1.05) == pytest.approx((0.075 + 0.12) * 1.05 + 0.08)


async def test_the_lift_turns_the_1_15_refusal_into_an_entry_with_the_floor_tp(tmp_path):
    e, res = await _mr_buy(tmp_path, micro_net_rr_min=1.30)
    r = rows(tmp_path)[-1]
    assert r['qty_req'] and not any('NET RR' in ev['message'] for ev in e.events)
    assert r['tp_lift'].startswith('0.2175->0.2415') and 'unscaled 0.2900' in r['tp_lift']   # 0.29 x 0.75 -> the 1.30 TP
    assert (0.2415 - 0.04) / (0.075 + 0.08) == pytest.approx(1.30, abs=1e-3)


async def test_the_lift_never_goes_above_the_unscaled_tp_and_then_the_gate_still_refuses(tmp_path):
    """Floor 2.0 would need TP 0.35 % at SL 0.075; the unscaled bracket has 0.29 %: lift to 0.29 (RR 1.61) and refuse."""
    e, res = await _mr_buy(tmp_path, micro_net_rr_min=2.0)
    assert not res['ok'] and res['error'].startswith('ZEPHR NET RR 1.61 < 2 (TP 0.290% / SL 0.075%')
    assert rows(tmp_path)[-1:] == [] or rows(tmp_path)[-1].get('qty_req') is None


async def test_no_lift_outside_micro_mode(tmp_path):
    e, res = await _mr_buy(tmp_path, wallet=5000.0, net_rr_min=1.30)       # the 5 000 U demo account: refused as before
    assert not e.micro_active() and not res['ok']
    assert res['error'].startswith('ZEPHR NET RR 1.15 < 1.3') and all(not r.get('tp_lift') for r in rows(tmp_path))


# ============================================================================= task 2: JUMPY does not freeze the scalp lane
def jumpy(tmp_path, spread=1.0, vpin=0.5, **prof):
    e = burst_engine(tmp_path, regime_gate='on', **prof)
    e.features = {'spread_bps': spread}
    e.feed.vpin_now = lambda: vpin
    regime(e, rc.JUMPY)
    return e


def test_micro_scalping_is_allowed_in_jumpy_on_a_tight_spread_and_a_known_clean_vpin(tmp_path):
    e = jumpy(tmp_path)
    why, pol = e.regime_check('SCALP', 'AUTO')
    assert why is None and pol.state == rc.JUMPY and pol.stop_mult == 1.5     # the policy's wider stop still applies


@pytest.mark.parametrize('spread, vpin, word', [(2.0, 0.5, 'spread 2.0 bps'), (3.5, 0.5, 'spread 3.5 bps'),
                                               (None, 0.5, 'spread — bps'), (1.0, 0.70, 'VPIN 0.70'),
                                               (1.0, 0.95, 'VPIN 0.95'), (1.0, None, 'VPIN —')])
def test_jumpy_still_freezes_the_scalp_lane_when_the_condition_cannot_be_shown(tmp_path, spread, vpin, word):
    e = jumpy(tmp_path, spread=spread, vpin=vpin)
    why, _ = e.regime_check('SCALP', 'AUTO')
    assert why.startswith('JUMPY: micro-scalping frozen') and word in why


def test_the_jumpy_exception_is_micro_only_and_leaves_the_other_states_alone(tmp_path):
    big = jumpy(tmp_path / 'b', wallet=5000.0)
    assert big.regime_check('SCALP', 'AUTO')[0] == 'JUMPY: micro-scalping frozen'          # not a micro account
    assert jumpy(tmp_path / 'c', micro_jumpy_scalp=False).regime_check('SCALP', 'AUTO')[0].startswith('JUMPY')
    for state in (rc.CRASH, rc.UNKNOWN):                                                  # the circuit breaker is untouched
        e = jumpy(tmp_path / state)
        regime(e, state)
        assert e.regime_check('SCALP', 'AUTO')[0].startswith(state)
    assert rc.check_policies() == [] and rc.POLICIES[rc.JUMPY].size_mult == 0.3             # the classifier did not change


async def test_a_jumpy_scalp_is_sent_at_the_venue_lot_not_refused_for_the_0_3_size_policy(tmp_path):
    e, venue, feed = await doge_micro(tmp_path, regime_gate='on', gtx_ttl_s=0.2)
    regime(e, rc.JUMPY)
    e.features = {'spread_bps': 1.0}
    res = await e.execute('BUY', BUY, 'SCALP')
    r = rows(tmp_path)[-1]
    assert r['qty_req'] and 5.05 <= r['qty_req'] * PX <= 6.0 and r['regime_gate'] == 'JUMPY·MICRO-SCALP'
    assert r['micro_floor'].startswith('JUMPY size x0.3')


# ============================================================================= task 3 (the floor itself is in test_micro_rotation)
async def test_the_scalp_stop_floor_shrink_cannot_refuse_a_micro_lot(tmp_path):
    """SIDEWAYS x0.75 puts the scalp stop (0.09 %) under its 0.15 % floor; the size shrink that keeps the loss equal would go
    below the venue lot ('SCALP SIZE ... below the venue minimum'). The micro lot is the minimum; micro_check bounds the loss."""
    e, venue, feed = await doge_micro(tmp_path, regime_gate='on', scalp_sl_min_pct=0.15, gtx_ttl_s=0.2,
                                      alt_size_floor=False, risk_parity=False)     # the chain asks for the bare lot
    regime(e, rc.SIDEWAYS)
    await e.execute('BUY', BUY, 'SCALP')
    r = rows(tmp_path)[-1]
    assert r['qty_req'] and 5.05 <= r['qty_req'] * PX <= 6.0 and r['micro_floor'].startswith('scalp stop floor size')


# ============================================================================= task 4: IOC thresholds
def test_ioc_defaults_are_the_new_owner_numbers():
    p = eng.PROFILES['demo']
    assert (p.micro_ioc_consensus, p.micro_ioc_ofi, p.micro_ioc_min_tp_pct, p.micro_ioc_net_rr_min) == (0.78, 0.70, 0.20, 1.05)


@pytest.mark.parametrize('consensus, ofi', [(0.78, -0.70), (0.80, -0.72), (0.9, -0.94)])
def test_the_new_thresholds_route_ioc_at_the_boundary(tmp_path, consensus, ofi):
    e = burst_engine(tmp_path, micro_ioc='on')
    e.diag = {'consensus': consensus}
    assert route(e, ofi=ofi) == ('ioc', 'IOC')


def test_the_tp_floor_is_moot_and_the_observed_micro_brackets_still_cannot_take_a_taker_entry():
    """Taker in + taker stop = 0.12 % of fees: net RR 1.05 needs TP >= 1.05 x SL + 0.206 %, whatever the 0.20 % floor says."""
    need = lambda sl: 1.05 * sl + 0.206
    assert [need(sl) for sl in (0.075, 0.10, 0.15, 0.20)] == pytest.approx([0.28475, 0.311, 0.3635, 0.416])
    assert all(need(sl) > 0.20 for sl in (0.075, 0.10, 0.15, 0.20))
    rr_ioc = (0.2175 - 0.08) / (0.075 + 0.12)                              # the logged DOGE bracket with a taker entry
    assert rr_ioc == pytest.approx(0.705, abs=1e-3) and rr_ioc < 1.05      # break-even win rate 59 %: stays maker
    assert (0.285 - 0.08) / (0.075 + 0.12) == pytest.approx(1.05, abs=1e-2)


def test_the_micro_ioc_bracket_is_the_gate_not_the_global_floor(tmp_path):
    """DOGE's real bracket (TP 1.25 / SL 0.50): RR 1.89 passes 1.05 and 1.30; at a 5.0 floor only the IOC-specific one refuses."""
    assert route(burst_engine(tmp_path, micro_ioc='on', net_rr_min=5.0)) == ('ioc', 'IOC')            # global floor irrelevant
    r, why = route(burst_engine(tmp_path / 'b', micro_ioc='on', micro_ioc_net_rr_min=5.0))
    assert r == 'gtx' and why.startswith('GTX: net RR 1.89 < 5')
