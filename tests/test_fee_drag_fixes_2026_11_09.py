"""11-09 fee-drag fixes (docs/harness/plan.md): fee-aware SOFT-BE, scalp TP mode + EJECT guard, LUMEN/TIDAL
collinearity, net outcome label, Kelly NO-EDGE gate + raw quorum gate.  Behaviour tests on synthetic data (mock venue);
they are NOT evidence that any of this makes money."""
import json
from dataclasses import replace

import pytest

from core import engine as eng
from core import invariants as inv
from core import micro_swarm as ms
from core.consensus_engine import WEIGHTS, ConsensusEngine, Vote
from tests.test_two_mode_engine import book_at, btc_long_at, mock_engine, opened, sig

ENTRY = 84_000.0
BUY = ms.evaluate(sig(FLOW=('BUY', 1.0, 20), VWAP=('BUY', 1.0, 20)), ms.Gates(cost_bps=6.0))[0]


def at(pct, entry=ENTRY):
    return round(entry * (1 + pct / 100), 1)


def mk(tmp_path, **prof):
    """Engine with the BTC venue spec loaded (tick 0.1) for the pure price helpers."""
    e, _, _ = mock_engine(tmp_path, **prof)
    e.spec = dict(eng.FALLBACK_SPECS['BTCUSDT'])
    return e


def pos(**kw):
    return {'side': 'BUY', 'entry': 100_000.0, 'entry_fee_bps': 2.0, 'mfe_pct': 0.0, **kw}


# ============================================================================= Task 1: fee-aware SOFT-BE
@pytest.mark.production_profile
def test_profile_defaults_split_soft_and_hard_offsets():
    p = eng.PROFILES['demo']
    assert (p.be_offset_pct, p.be_offset_hard_pct) == (0.08, 0.02)
    assert (eng.BE_OFFSET_FLOOR_DEFAULT, eng.BE_OFFSET_FLOOR['SOLUSDT']) == (0.08, 0.065)


def test_soft_offset_floor_by_symbol(tmp_path):
    e, _, _ = mock_engine(tmp_path, be_offset_pct=0.0)
    assert e.be_offset_eff() == pytest.approx(0.08)                       # general floor
    e.p = replace(e.p, symbol='SOLUSDT')
    assert e.be_offset_eff() == pytest.approx(0.065)                      # SOL floor, below the 0.08 default
    e.p = replace(e.p, be_offset_pct=0.10)
    assert e.be_offset_eff() == pytest.approx(0.10)                       # a higher setting is kept
    e.p = replace(e.p, be_offset_pct=-0.5)
    assert e.be_offset_eff() == pytest.approx(0.065)                      # negative cannot bring the leak back


def test_soft_be_still_nets_positive_when_the_maker_exit_escalates_to_taker(tmp_path):
    """Maker in 2 + taker out 5 + slip 1 bps = 0.08 %.  The old target (0.06 %) lost 0.02 % on that path."""
    e = mk(tmp_path)
    taker_path = (e.p.maker_bps + e.p.taker_bps + e.p.slip_bps) / 100.0
    assert e.be_level_pct(pos()) - taker_path > 0.03                      # 0.12 - 0.08
    assert (e.p.maker_bps * 2) / 100.0 + 0.02 - taker_path < 0            # the pre-11-09 level: a loss


def test_be_arms_only_after_mfe_pays_the_level(tmp_path):
    e = mk(tmp_path)
    assert e.be_level_pct(pos()) == pytest.approx(0.12)
    assert e.be_arm_pct(pos()) == pytest.approx(0.12)
    assert not e.fee_hurdle_covered(pos(mfe_pct=0.119)) and e.fee_hurdle_covered(pos(mfe_pct=0.12))
    ioc = pos(entry_fee_bps=5.0)                                          # taker entry: 5 + 2 bps + 0.08 % = 0.15 %
    assert e.be_arm_pct(ioc) == pytest.approx(0.15)
    assert not e.fee_hurdle_covered(ioc | {'mfe_pct': 0.14}) and e.fee_hurdle_covered(ioc | {'mfe_pct': 0.15})


async def test_be_not_declared_below_the_level_and_declared_above(tmp_path):
    e, venue, feed = await opened(tmp_path)
    entry = e.pos['entry']
    book_at(feed, round(entry * 1.0011, 1))                               # +0.11 %: micro-lock only
    await e.tick()
    assert e.pos['soft_kind'] == 'LOCK' and not e.pos['be_locked']
    book_at(feed, round(entry * 1.0013, 1))                               # +0.13 % >= 0.12 %: BE at +0.12 %
    await e.tick()
    assert e.pos['soft_kind'] == 'BE' and e.pos['be_locked']
    assert e.pos['soft_stop'] == pytest.approx(entry * 1.0012, abs=0.2)


def test_hard_stop_breakeven_is_unchanged(tmp_path):
    """stop_out_pct() already holds the taker stop + slip: +0.02 % over it nets +0.02 %.  Raising it would arm the
    exchange-stop BE at +0.16 % and market-close the position between +0.12 % and +0.16 %."""
    e = mk(tmp_path)
    assert e.be_price(pos()) == pytest.approx(100_100.0)                  # (2 + 5 + 1) bps + 0.02 %
    assert e.be_arm_pct(pos(), soft=False) == pytest.approx(0.12)


# ============================================================================= Task 2: scalp TP + EJECT guard
@pytest.mark.parametrize('atr15, tp', [(0.05, 0.09), (0.20, 0.12), (0.30, 0.14), (0.60, 0.14)])
def test_scalp_tp_atr_mode_is_clamped(tmp_path, atr15, tp):
    e, _, _ = mock_engine(tmp_path, scalp_tp_mode='atr', scalp_net_rr=False, scalp_net_rr_min=0.0,
                          scalp_sl_min_pct=0.15)
    bp = e.bracket_plan('SCALP', {'atr15_pct': atr15})
    assert bp['tp_pct'] == pytest.approx(tp)
    # the price of a reachable TP: win = TP - 0.04 % maker round trip, loss = SL 0.15 + 0.08 % (maker in, taker stop,
    # slip) -> break-even win rate 70 % (TP 0.14) to 79 % (TP 0.09)
    assert bp['be_wr'] == pytest.approx(0.23 / ((tp - 0.04) + 0.23)) and bp['be_wr'] > 0.69


@pytest.mark.production_profile
def test_scalp_tp_atr_mode_needs_unknown_atr_to_fall_back_and_static_ignores_it(tmp_path):
    e, _, _ = mock_engine(tmp_path, scalp_tp_mode='atr', scalp_net_rr=False, scalp_net_rr_min=0.0)
    assert e.bracket_plan('SCALP', {})['tp_pct'] == pytest.approx(e.p.scalp_tp_pct)       # UNKNOWN is not zero
    s, _, _ = mock_engine(tmp_path, scalp_tp_mode='static', scalp_net_rr=False, scalp_net_rr_min=0.0)
    assert s.bracket_plan('SCALP', {'atr15_pct': 0.20})['tp_pct'] == pytest.approx(s.p.scalp_tp_pct)
    assert eng.PROFILES['demo'].scalp_tp_mode == 'static'                # opt-in: -6.3..-7.5 bps/scalp in replay


def test_net_rr_floor_still_lifts_an_atr_tp_while_it_is_on(tmp_path):
    e, _, _ = mock_engine(tmp_path, scalp_tp_mode='atr', scalp_net_rr=True, scalp_net_rr_min=1.35,
                          scalp_sl_min_pct=0.15)
    bp = e.bracket_plan('SCALP', {'atr15_pct': 0.20})
    assert bp['tp_pct'] == pytest.approx(0.3505, abs=1e-4)                # (0.15 + 0.08) x 1.35 + 0.04
    assert bp['rr_net'] == pytest.approx(1.35, abs=0.01)


def test_adverse_momentum_reads_each_signal_with_the_trade(tmp_path):
    e, _, _ = mock_engine(tmp_path)
    p = pos()
    assert e.adverse_momentum(p, {}) is None                              # unknown is not adverse
    assert 'CVD' in e.adverse_momentum(p, {'cvd_vel_10s': -60_000.0})
    assert 'OFI' in e.adverse_momentum(p, {'ofi_10s': -0.35})
    assert 'velocity' in e.adverse_momentum(p, {'px_vel_15s': -0.03})
    assert e.adverse_momentum(p, {'cvd_vel_10s': 60_000.0, 'ofi_10s': 0.35, 'px_vel_15s': 0.03}) is None
    assert 'CVD' in e.adverse_momentum(pos(side='SELL'), {'cvd_vel_10s': 60_000.0})   # sign flips with the side
    e.tox = {'toxic': True}
    assert e.adverse_momentum(p, {}) == 'toxic flow'


async def test_eject_floor_is_held_below_the_fee_hurdle_then_released_after_the_defer_window(tmp_path):
    e, venue, feed = await btc_long_at(tmp_path, ENTRY, eject=True)
    e.pos['opened'] -= 181
    await e.tick()
    floor = e.pos['exiting']['floor']
    book_at(feed, at(-0.05))
    e.pos['opened'] -= 180                                                # 361 s: hard_eject_s passed
    e.pos['exiting']['posted'] -= 5
    await e.tick()
    x = e.pos['exiting']
    assert x['reason'] == 'EJECT-BE' and x['floor'] == floor              # held: -0.05 % < 0.04 % hurdle, no adverse flow
    assert e.pos.get('eject_deferred') and any('EJECT held' in ev['message'] for ev in e.events)
    e.pos['opened'] -= e.p.eject_defer_max_s                              # 481 s: bounded, the time stop returns
    e.pos['exiting']['posted'] -= 5
    await e.tick()
    assert e.pos['exiting']['reason'] == 'EJECT' and e.pos['exiting']['floor'] is None


async def test_eject_escalates_at_once_on_confirmed_adverse_momentum(tmp_path):
    e, venue, feed = await btc_long_at(tmp_path, ENTRY, eject=True)
    e.pos['opened'] -= 361
    book_at(feed, at(-0.05))
    f = {'cvd_vel_10s': -5e6}
    assert e.eject_escalate_ok(e.pos, f, -0.05, 360.0) is True
    assert 'CVD' in e.pos['eject_adverse']
    assert e.eject_escalate_ok(e.pos, {}, -0.05, 360.0) is False          # no adverse flow, below the hurdle: hold
    assert e.eject_escalate_ok(e.pos, {}, 0.05, 360.0) is True            # gross above the hurdle: a touch exit nets > 0
    off = replace(e.p, eject_guard=False)
    e.p = off
    assert e.eject_escalate_ok(e.pos, {}, -0.05, 360.0) is True           # rollback switch


# ============================================================================= Task 3: LUMEN / TIDAL collinearity
GATES = ms.Gates(cost_bps=6.0)


def vote(votes, agent):
    return next(v for v in votes if v.agent == agent)


def test_lumen_and_tidal_on_one_flow_delta_cannot_reach_the_quorum_alone():
    s = sig(FLOW=('BUY', 1.0, 20), OFI=('BUY', 1.0, 10))
    d, votes, diag = ms.evaluate(s, GATES, jev_min=None, min_confluence=0)
    assert vote(votes, 'LUMEN').confidence == 1.0 and vote(votes, 'TIDAL').confidence == 0.5
    assert 'same flow as LUMEN' in vote(votes, 'TIDAL').reason
    assert diag['consensus'] == pytest.approx(0.70) and not d.allowed     # 0.20 + 0.10 + 0.40 gates < 0.768
    old, _, odiag = ms.evaluate(s, GATES, jev_min=None, min_confluence=0, collinear_penalty=1.0)
    assert odiag['consensus'] == pytest.approx(0.80) and old.allowed      # the pseudo-supermajority of before


@pytest.mark.parametrize('extra, label', [
    ({'TREND': ('BUY', 1.0, 10)}, 'NORO confirms'),
    ({'VWAP': ('BUY', 1.0, 10)}, 'NORO price-structure confirms')])
def test_an_independent_noro_confirmation_restores_the_quorum(extra, label):
    s = sig(FLOW=('BUY', 1.0, 20), OFI=('BUY', 1.0, 10), **extra)
    d, _, diag = ms.evaluate(s, GATES, jev_min=None, min_confluence=0)
    assert diag['consensus'] == pytest.approx(0.90) and d.allowed, label


def test_independent_tidal_sources_keep_full_weight():
    for key in ('LIQ', 'DEPTH'):
        s = sig(FLOW=('BUY', 1.0, 20), **{key: ('BUY', 1.0, 10)})
        d, votes, diag = ms.evaluate(s, GATES, jev_min=None, min_confluence=0)
        assert vote(votes, 'TIDAL').confidence == 1.0 and diag['consensus'] == pytest.approx(0.80) and d.allowed, key


def test_squeeze_repeats_surge_and_is_discounted():
    s = sig(SURGE=('BUY', 1.0, 12), SQUEEZE=('BUY', 1.0, 10))
    _, votes, diag = ms.evaluate(s, GATES, jev_min=None, min_confluence=0)
    assert vote(votes, 'TIDAL').confidence == 0.5 and diag['consensus'] == pytest.approx(0.70)


def test_a_lone_tidal_flow_signal_and_opposite_sides_are_not_touched():
    _, votes, _ = ms.evaluate(sig(OFI=('BUY', 1.0, 10)), GATES, jev_min=None, min_confluence=0)
    assert vote(votes, 'TIDAL').confidence == 1.0                         # no LUMEN signal: nothing repeated
    _, votes, _ = ms.evaluate(sig(FLOW=('SELL', 1.0, 20), OFI=('BUY', 1.0, 10)), GATES, jev_min=None,
                              min_confluence=0)
    assert vote(votes, 'TIDAL').confidence == 1.0                         # disagreement is not double counting


def test_jev_edge_does_not_count_the_repeated_flow_twice():
    s = sig(FLOW=('BUY', 1.0, 30), OFI=('BUY', 1.0, 10))
    assert ms.evaluate(s, GATES, jev_min=None, min_confluence=0)[2]['edge_bps'] == pytest.approx(35.0)
    assert ms.evaluate(s, GATES, jev_min=None, min_confluence=0, collinear_penalty=1.0)[2]['edge_bps'] \
        == pytest.approx(40.0)


@pytest.mark.production_profile
def test_collinearity_discount_leaves_the_pinned_invariants_alone():
    assert eng.PROFILES['demo'].collinear_penalty == 0.5
    assert inv.weights_sha256() == inv.WEIGHTS_SHA256
    assert inv.check_consensus(ConsensusEngine(trend_floor=.768), regimes=('NORMAL',)) == []


# ============================================================================= Task 4: net outcome label
def _votes(**sides):
    return [Vote(a, sides.get(a, 'BUY'), 1.0 if sides.get(a, 'BUY') != 'HOLD' else 0.0, 'unit') for a in WEIGHTS]


def test_gross_win_that_fees_turn_into_a_loss_is_not_a_win():
    c = ConsensusEngine()
    c.record_outcome(_votes(), 'BUY', 0.50, fee_paid=0.80)                # gross +0.50, fee 0.80 -> net -0.30
    assert [c.performance[a][-1] for a in ('NORO', 'LUMEN', 'TIDAL')] == [0, 0, 0]
    c.record_outcome(_votes(), 'BUY', 0.50, fee_paid=0.10)                # net +0.40
    assert [c.performance[a][-1] for a in ('NORO', 'LUMEN', 'TIDAL')] == [1, 1, 1]
    c.record_outcome(_votes(), 'BUY', -0.01)                              # pnl is NET without fee_paid
    assert [c.performance[a][-1] for a in ('NORO', 'LUMEN', 'TIDAL')] == [0, 0, 0]
    c.record_outcome(_votes(), 'BUY', 0.0)
    assert c.performance['NORO'][-1] == 0                                 # a scratch is not a win


def test_fee_adjusted_losses_do_not_reward_the_agents_that_voted_for_them():
    old, new = ConsensusEngine(), ConsensusEngine()
    for _ in range(20):                                                   # LUMEN dissented from a gross-positive, net-negative trade
        old.record_outcome(_votes(LUMEN='SELL'), 'BUY', 0.50)             # labelled on gross: NORO / TIDAL "won"
        new.record_outcome(_votes(LUMEN='SELL'), 'BUY', 0.50, fee_paid=0.80)
    assert old.weights['LUMEN'] < 0.1 < 0.25 < old.weights['NORO']       # polluted learning
    assert new.weights['NORO'] == pytest.approx(new.weights['LUMEN'])
    assert new.weights['LUMEN'] == pytest.approx(new.weights['TIDAL'])
    assert inv.check_consensus(new, regimes=('NORMAL',)) == []


def test_record_outcome_rejects_a_bad_fee():
    c = ConsensusEngine()
    for bad in (-0.1, float('nan'), float('inf')):
        with pytest.raises(ValueError):
            c.record_outcome(_votes(), 'BUY', 0.1, fee_paid=bad)


# ============================================================================= Task 5: Kelly NO EDGE + raw quorum
def rows(e, pnls, age=0.0):
    e.all_rows.extend({'symbol': e.p.symbol, 'pnl': x, 't': e.clock() - age} for x in pnls)


def test_no_edge_blocks_an_auto_entry_and_sample_does_not(tmp_path):
    e, _, _ = mock_engine(tmp_path, kelly_min_trades=3)
    e.diag = {'consensus': 0.9}
    rows(e, [-0.1, -0.1])
    assert e.entry_edge_block('SWARM', 'AUTO') is None                    # SAMPLE: UNKNOWN edge, window must fill
    rows(e, [-0.1])
    why = e.entry_edge_block('SWARM', 'AUTO')
    assert why.startswith('KELLY NO EDGE f* -1.000') and 'no minimum-size fallback' in why
    assert e.entry_edge_block('SWARM', 'TEST') is None                    # operator / test entries are not AUTO


def test_small_wins_big_losses_is_no_edge_and_positive_expectancy_is_not(tmp_path):
    e, _, _ = mock_engine(tmp_path, kelly_min_trades=4)
    e.diag = {'consensus': 0.9}
    rows(e, [0.01, -0.1, -0.1, -0.1])
    assert e.kelly()['f_star'] < 0 and e.entry_edge_block('SWARM', 'AUTO')
    e.all_rows.clear()
    rows(e, [0.2, 0.2, -0.1, 0.2])
    assert e.kelly()['f_star'] > 0 and e.entry_edge_block('SWARM', 'AUTO') is None


def test_kelly_of_is_the_one_kelly_formula(tmp_path):
    e, _, _ = mock_engine(tmp_path, kelly_min_trades=3)
    rows(e, [0.2, 0.2, -0.1])
    assert e.kelly() == e.kelly_of([0.2, 0.2, -0.1])
    assert e.kelly_of([-0.1, -0.1, -0.1])['state'] == 'NO EDGE' and e.kelly_of([0.2, 0.2])['state'] == 'SAMPLE'
    assert not hasattr(eng.Profile, 'kelly_no_edge_ttl_s')                  # the 6 h expiry was replaced by the shadow path


# ---- NO EDGE shadow path: virtual trades lift the gate, never the clock
def vpos(side='BUY', **kw):
    q = 100_000.0
    return {'side': side, 'path': 'SWARM', 'quote': q, 't': 0.0, 'state': 'PENDING', 'tp_pct': 0.30, 'sl_pct': 0.15,
            'mfe': 0.0, **kw}


def shadow_engine(tmp_path, **prof):
    e, venue, feed = mock_engine(tmp_path, kelly_min_trades=3, **prof)
    e.spec = dict(eng.FALLBACK_SPECS['BTCUSDT'])
    e.diag = {'consensus': 0.9}
    return e, venue, feed


def closed(tmp_path):
    f = tmp_path / 'engine_orders.jsonl'
    rows_ = [json.loads(x) for x in f.read_text(encoding='utf-8').splitlines()] if f.exists() else []
    return [r for r in rows_ if r.get('outcome') == 'SHADOW_EDGE_CLOSE']


async def test_no_edge_refusal_arms_one_virtual_trade_and_sends_no_order(tmp_path):
    e, venue, feed = shadow_engine(tmp_path)
    await e.sync()
    e.features = e.feed.features(e.clock())
    rows(e, [-0.1, -0.1, -0.1])
    r = await e.execute('BUY', BUY, 'SWARM')
    v = e.edge_shadow['pos']
    assert r['error'].startswith('KELLY NO EDGE') and 'shadow path 0/3' in r['error']
    assert v and v['state'] == 'PENDING' and v['tp_pct'] > 0 and v['sl_pct'] > 0
    assert e.pos is None and not venue.fills and not e.busy               # a virtual trade, not an order
    await e.execute('BUY', BUY, 'SWARM')
    assert e.edge_shadow['pos'] is v                                      # one at a time (and one per 20 s)
    assert e.edge_shadow_view()['open']['state'] == 'PENDING'


async def test_only_a_kelly_refusal_arms_the_shadow_path(tmp_path):
    e, venue, feed = shadow_engine(tmp_path)
    await e.sync()
    e.features = e.feed.features(e.clock())
    e.diag = {'consensus': 0.6}
    r = await e.execute('BUY', BUY, 'JEV')                                # SAMPLE Kelly, padded quorum
    assert r['error'].startswith('RAW QUORUM') and e.edge_shadow['pos'] is None
    rows(e, [-0.1, -0.1, -0.1])
    e.p = replace(e.p, edge_shadow=False)
    await e.execute('BUY', BUY, 'SWARM')
    assert e.edge_shadow['pos'] is None                                   # rollback switch: plain strict block


def test_virtual_buy_fills_only_when_the_tape_trades_through_the_quote(tmp_path):
    e, _, _ = shadow_engine(tmp_path)
    e.edge_shadow['pos'] = vpos(t=e.clock())
    e.edge_shadow_step({'price': 100_000.0})                              # touching is not enough
    e.edge_shadow_step({'price': 99_999.95})
    assert e.edge_shadow['pos']['state'] == 'PENDING'
    e.edge_shadow_step({'price': 99_999.0})
    assert e.edge_shadow['pos']['state'] == 'OPEN'


def test_unfilled_virtual_quote_expires_without_counting(tmp_path):
    e, _, _ = shadow_engine(tmp_path)
    e.edge_shadow['pos'] = vpos(t=e.clock() - e.p.edge_shadow_ttl_s - 1)
    e.edge_shadow_step({'price': 100_001.0})
    assert e.edge_shadow['pos'] is None and e.edge_shadow_rows() == [] and closed(tmp_path) == []


@pytest.mark.parametrize('side, px, net', [('BUY', 100_300.2, 0.26), ('SELL', 99_699.8, 0.26)])
def test_virtual_take_profit_nets_the_maker_round_trip(tmp_path, side, px, net):
    e, _, _ = shadow_engine(tmp_path)
    e.edge_shadow['pos'] = vpos(side, state='OPEN', t_fill=e.clock())
    e.edge_shadow_step({'price': px})
    assert e.edge_shadow_rows() == [pytest.approx(net)]                   # 0.30 % TP - 0.04 % maker in + out
    assert e.edge_shadow['pos'] is None
    row = closed(tmp_path)[0]
    assert row['why'] == 'TP' and row['net_pct'] == pytest.approx(net) and row['symbol'] == e.p.symbol


@pytest.mark.parametrize('side, px, net', [('BUY', 99_850.0, -0.23), ('SELL', 100_150.0, -0.23),
                                           ('BUY', 99_800.0, -0.28)])
def test_virtual_stop_pays_taker_and_slip_and_gaps_at_the_print(tmp_path, side, px, net):
    e, _, _ = shadow_engine(tmp_path)
    e.edge_shadow['pos'] = vpos(side, state='OPEN', t_fill=e.clock())
    e.edge_shadow_step({'price': px})
    assert e.edge_shadow_rows() == [pytest.approx(net)]                   # -SL - (2 + 5 + 1) bps; a gap loses more
    assert closed(tmp_path)[0]['why'] == 'SL'


def test_virtual_time_exit_is_at_the_touch_after_the_hold(tmp_path):
    e, _, _ = shadow_engine(tmp_path)
    e.edge_shadow['pos'] = vpos(state='OPEN', t_fill=e.clock() - 5)
    e.edge_shadow_step({'price': 100_050.0, 'bid': 100_049.9})
    assert e.edge_shadow['pos'] is not None                               # inside the hold: nothing
    e.edge_shadow['pos']['t_fill'] = e.clock() - e.p.edge_shadow_hold_s - 1
    e.edge_shadow_step({'price': 100_050.0, 'bid': 100_049.9})
    assert e.edge_shadow_rows() == [pytest.approx((100_049.9 / 100_000.0 - 1) * 100 - 0.04)]
    assert closed(tmp_path)[0]['why'] == 'TIME'


def test_shadow_edge_lifts_the_no_edge_gate_and_a_bad_shadow_keeps_it(tmp_path):
    e, _, _ = shadow_engine(tmp_path)
    rows(e, [-0.1, -0.1, -0.1])
    e.edge_shadow['pnls'] = [0.26, -0.23]
    why = e.entry_edge_block('SWARM', 'AUTO')
    assert why.startswith('KELLY NO EDGE') and 'shadow path 2/3 virtual trades' in why   # n < min: still strict
    e.edge_shadow['pnls'] = [-0.23, -0.23, 0.26]
    assert 'shadow path 3/3' in e.entry_edge_block('SWARM', 'AUTO')       # n ok, f* < 0: still strict
    e.edge_shadow['pnls'] = [0.26, 0.26, -0.23]
    assert e.entry_edge_block('SWARM', 'AUTO') is None                    # virtual Kelly EDGE: the gate lifts
    assert e.edge_shadow['released'] and any('lifted by the shadow path' in ev['message'] for ev in e.events)
    e.edge_shadow['pnls'].extend([-0.23, -0.23, -0.23])                   # the evidence turns: locked again
    assert e.entry_edge_block('SWARM', 'AUTO').startswith('KELLY NO EDGE') and not e.edge_shadow['released']
    assert any('KELLY NO EDGE back' in ev['message'] for ev in e.events)


async def test_a_lifted_gate_lets_the_entry_through_at_the_fallback_size(tmp_path):
    e, venue, feed = shadow_engine(tmp_path)
    await e.sync()
    rows(e, [-0.1, -0.1, -0.1])
    e.edge_shadow['pnls'] = [0.26, 0.26, -0.23]
    r = await e.execute('BUY', BUY, 'SWARM')
    assert not str(r.get('error') or '').startswith('KELLY NO EDGE')


def test_shadow_evidence_survives_a_restart_and_shows_in_the_snapshot(tmp_path):
    e, _, _ = shadow_engine(tmp_path)
    e.edge_shadow['pos'] = vpos(state='OPEN', t_fill=e.clock())
    e.edge_shadow_step({'price': 100_300.2})
    e2, _, _ = shadow_engine(tmp_path)                                    # same ledger dir, fresh engine
    assert e2.edge_shadow_rows() == [pytest.approx(0.26)]
    snap = e2.quant_view()['edge_shadow']
    assert snap['n'] == 1 and snap['state'] == 'SAMPLE' and snap['open'] is None


def test_kelly_gate_switches(tmp_path):
    e, _, _ = mock_engine(tmp_path, kelly_min_trades=3, kelly_no_edge_gate=False)
    e.diag = {'consensus': 0.9}
    rows(e, [-0.1, -0.1, -0.1])
    assert e.entry_edge_block('SWARM', 'AUTO') is None                    # rollback switch
    e.p = replace(e.p, kelly_no_edge_gate=True, kelly=False)
    assert e.entry_edge_block('SWARM', 'AUTO') is None                    # Kelly sizing off (live): no Kelly gate
    e.p = replace(e.p, kelly=True, max_loss_usdt=0.05)
    assert e.entry_edge_block('SWARM', 'AUTO') is None                    # fixed max-loss sizing (live micro-capital)


async def test_execute_sends_no_order_on_no_edge(tmp_path):
    e, venue, feed = mock_engine(tmp_path, kelly_min_trades=3)
    await e.sync()
    e.diag = {'consensus': 0.9}
    rows(e, [-0.1, -0.1, -0.1])
    r = await e.execute('BUY', BUY, 'SWARM')
    assert not r['ok'] and r['error'].startswith('KELLY NO EDGE')
    assert e.pos is None and not e.busy and not venue.fills
    assert any('KELLY NO EDGE' in ev['message'] for ev in e.events)


def test_raw_quorum_gate_refuses_a_jev_lifted_score(tmp_path):
    e, _, _ = mock_engine(tmp_path)
    e.diag = {'consensus': 0.60}
    why = e.entry_edge_block('JEV', 'AUTO')
    assert why.startswith('RAW QUORUM 60.0% < 76.8%')
    assert e.entry_edge_block('SWARM', 'AUTO') is None                    # a plain swarm path has no lift
    e.diag = {'consensus': 0.80}
    assert e.entry_edge_block('JEV', 'AUTO') is None                      # JEV label but the raw vote clears 0.768
    e.diag = None
    assert e.entry_edge_block('JEV', 'AUTO').startswith('RAW QUORUM')     # unknown raw score is not "enough"
    e.diag = {'consensus': 0.60}
    e.p = replace(e.p, raw_quorum_gate=False)
    assert e.entry_edge_block('JEV', 'AUTO') is None                      # rollback switch


async def test_execute_refuses_a_padded_quorum_but_ms_evaluate_keeps_the_pinned_jev_lift(tmp_path):
    d, _, diag = ms.evaluate(sig(FLOW=('BUY', 1.0, 60)), ms.Gates(cost_bps=6.0), jev_min=inv.JEV_MIN_FLOOR)
    assert d.allowed and diag['path'] == 'JEV' and diag['consensus'] == pytest.approx(0.60)   # D4 test stays valid
    e, venue, feed = mock_engine(tmp_path)
    await e.sync()
    e.diag = diag
    r = await e.execute('BUY', d, 'JEV')
    assert not r['ok'] and r['error'].startswith('RAW QUORUM') and not venue.fills and e.pos is None


@pytest.mark.production_profile
def test_new_gates_default_on_in_every_shipped_profile():
    for name, p in eng.PROFILES.items():
        assert (p.kelly_no_edge_gate, p.raw_quorum_gate, p.eject_guard, p.edge_shadow) == (True,) * 4, name
        assert inv.check_profile(p) == [], name
