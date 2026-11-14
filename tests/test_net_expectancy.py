"""2026-11-13 net-expectancy gate: core/net_expectancy.py and its engine wiring (Engine.net_ev_verdict, entry_edge_block,
MICRO_NET_EV_GATE). Synthetic numbers; the measured ledger figures come from docs/research_upgrade/AUTOPSY_REPORT_2026.md."""
import time

import pytest

from config.settings import micro_capital_env
from core import net_expectancy as nx
from tests.test_micro_capital_5usd import BUY, DOGE, micro_engine


def sample(mean, sd, n):
    """Deterministic sample with the given mean and (population) spread."""
    return [mean + sd] * (n // 2) + [mean - sd] * (n - n // 2)


# ============================================================================= bracket arithmetic
def test_driftless_ev_is_minus_the_cost_weighted_by_the_barrier_odds():
    ev = nx.driftless_ev_bps(46, 22, 4, 8)                               # micro trend bracket: TP 0.46 / SL 0.22 %
    assert ev == pytest.approx(-(4 * 22 + 8 * 46) / 68)                   # -6.71 bps
    assert ev == pytest.approx(nx.ev_bps(22 / 68, 46, 22, 4, 8))


@pytest.mark.parametrize('tp, sl', [(15, 15), (30, 15), (46, 22), (100, 25), (250, 22), (10, 40)])
def test_no_reward_to_risk_ratio_is_positive_without_directional_skill(tp, sl):
    assert nx.driftless_ev_bps(tp, sl, 4, 8) < 0
    assert nx.driftless_ev_bps(tp, sl, 0, 0) == pytest.approx(0.0, abs=1e-9)   # the costs are the whole loss


def test_breakeven_win_rate_of_the_retired_chop_bracket_is_67_6_percent():
    assert nx.breakeven_p(15, 15, 4, 8) == pytest.approx(23 / 34)         # 0.676 (CLAUDE.md 11-09e)
    assert nx.breakeven_p(46, 22, 4, 8) == pytest.approx(30 / 72)         # R:R 2.1 still needs 41.7 %
    with pytest.raises(ValueError):
        nx.driftless_p(0, 10)


def test_unknown_values_are_dropped_not_counted_as_zero():
    b = nx.mean_bounds([1.0, None, float('nan'), 3.0])
    assert b['n'] == 2 and b['mean'] == 2.0
    assert nx.mean_bounds([])['n'] == 0 and nx.mean_bounds([5.0])['lcb'] is None


# ============================================================================= verdicts
def test_no_evidence_is_refused_and_reports_the_driftless_ev():
    v = nx.verdict([], [], tp_bps=46, sl_bps=22)
    assert not v['allow'] and v['state'] == 'UNPROVEN' and '-6.71' in v['why']
    assert v['breakeven_p'] == pytest.approx(30 / 72)


def test_proven_negative_real_evidence_wins_over_good_virtual_evidence():
    v = nx.verdict(sample(-4.0, 5.0, 40), sample(6.0, 5.0, 40))
    assert not v['allow'] and v['state'] == 'NEGATIVE' and 'UCB95' in v['why']


def test_proven_positive_real_evidence_allows():
    v = nx.verdict(sample(5.0, 5.0, 40))
    assert v['allow'] and v['state'] == 'PROVEN_REAL'


def test_virtual_evidence_allows_only_while_real_is_short_or_inconclusive():
    assert nx.verdict(sample(1.0, 2.0, 10), sample(5.0, 5.0, 40))['state'] == 'PROVEN_VIRTUAL'
    v = nx.verdict(sample(1.0, 40.0, 40), [])                             # inconclusive real, nothing virtual
    assert not v['allow'] and v['state'] == 'UNPROVEN'


def test_min_n_is_a_hard_floor():
    assert not nx.verdict(sample(50.0, 1.0, 29))['allow']
    assert nx.verdict(sample(50.0, 1.0, 30))['allow']


def test_the_measured_ledger_is_refused():
    """AUTOPSY_REPORT_2026: 131 AUTO trades, net -3.95 +- 1.16 bps (spread 1.16 x sqrt(131) = 13.3 bps)."""
    assert nx.verdict(sample(-3.95, 13.3, 131))['state'] == 'NEGATIVE'


# ============================================================================= engine wiring
def micro_rows(n, net_bps, notional=10.0, t0=None):
    t0 = t0 if t0 is not None else time.time() - 10 * 86_400            # old days: the session stop stays out of it
    return [{'t': t0 + i, 'symbol': DOGE, 'tag': 'AUTO', 'micro': True, 'qty': notional / 0.1, 'entry': 0.1,
             'pnl': notional * net_bps[i % len(net_bps)] / 1e4, 'side': 'BUY', 'mode': 'demo'} for i in range(n)]


def test_on_refuses_unproven_shadow_logs_off_skips(tmp_path):
    e, _, _ = micro_engine(tmp_path, micro_net_ev_gate='on')
    assert e.micro_active()
    why = e.entry_edge_block('SWARM', 'AUTO')
    assert why and why.startswith('NET EV UNPROVEN') and e.net_ev_last['real']['n'] == 0
    assert e.entry_edge_block('SWARM', 'TEST') is None                    # operator / test entries are not AUTO
    sh, _, _ = micro_engine(tmp_path / 's', micro_net_ev_gate='shadow')
    assert sh.entry_edge_block('SWARM', 'AUTO') is None and sh.net_ev_last['state'] == 'UNPROVEN'
    assert any('NET EV GATE (shadow)' in str(x) for x in sh.events)
    off, _, _ = micro_engine(tmp_path / 'o', micro_net_ev_gate='off')
    assert off.entry_edge_block('SWARM', 'AUTO') is None and not hasattr(off, 'net_ev_last')


def test_gate_is_a_micro_rule(tmp_path):
    big, _, _ = micro_engine(tmp_path, wallet=5000.0, micro_net_ev_gate='on')
    assert not big.micro_active() and big.entry_edge_block('SWARM', 'AUTO') is None


def test_real_micro_evidence_opens_and_closes_the_gate(tmp_path):
    e, _, _ = micro_engine(tmp_path, micro_net_ev_gate='on')
    e.all_rows.extend([dict(r, micro=None) for r in micro_rows(40, [10.0, 0.0])])
    assert e.entry_edge_block('SWARM', 'AUTO').startswith('NET EV UNPROVEN')   # non-micro rows are not the lane
    e.all_rows.extend(micro_rows(40, [10.0, 0.0]))                       # +5 bps mean, LCB95 ~ +3.7
    assert e.entry_edge_block('SWARM', 'AUTO') is None and e.net_ev_last['state'] == 'PROVEN_REAL'
    e.all_rows.extend(micro_rows(100, [-10.0, 0.0]))                      # the window (last 100) turns negative
    assert e.entry_edge_block('SWARM', 'AUTO').startswith('NET EV NEGATIVE')


def test_virtual_edge_shadow_evidence_can_open_the_gate(tmp_path):
    e, _, _ = micro_engine(tmp_path, micro_net_ev_gate='on')
    e.edge_shadow['pnls'] = [0.10, 0.0] * 20                             # net_pct per virtual trade: +5 bps mean
    assert e.entry_edge_block('SWARM', 'AUTO') is None and e.net_ev_last['state'] == 'PROVEN_VIRTUAL'


async def test_a_net_ev_refusal_sends_no_order_and_arms_a_virtual_trade(tmp_path):
    e, venue, _ = micro_engine(tmp_path, micro_net_ev_gate='on')
    calls = []
    e.edge_shadow_arm = lambda side, path, why='KELLY NO EDGE': calls.append((side, path, why))
    await e.sync()
    e.features = e.feed.features(e.clock())
    r = await e.execute('BUY', BUY, 'SWARM')
    assert not r.get('ok') and str(r.get('error')).startswith('NET EV')
    assert calls == [('BUY', 'SWARM', 'NET EV')] and e.pos is None and not venue.fills


def test_env_flag_parses_and_validates():
    assert micro_capital_env({'MICRO_NET_EV_GATE': ' ON '}) == {'micro_net_ev_gate': 'on'}
    assert micro_capital_env({'MICRO_NET_EV_GATE': 'shadow'}) == {'micro_net_ev_gate': 'shadow'}
    with pytest.raises(ValueError, match='MICRO_NET_EV_GATE'):
        micro_capital_env({'MICRO_NET_EV_GATE': 'maybe'})
