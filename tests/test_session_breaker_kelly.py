"""2026-11-13: micro session stop after 2 losses in a row, MICRO_HARVESTER=on retired, micro-Kelly sizing and the growth
simulator (tools/micro_growth_sim.py). Synthetic data on the mock venue; none of it is evidence of profit."""
import math
import random
from datetime import datetime, timedelta

import pytest

from config.settings import micro_capital_env
from core.position_sizer import confluence_scale, fractional_kelly_risk
from tests.test_micro_capital_5usd import DOGE, micro_engine
from tools import micro_growth_sim as gs

NOON = datetime(2026, 11, 13, 12, 0, 0).timestamp()                     # local noon: no midnight inside the test


def row(t, pnl, tag='AUTO', micro=True):
    return {'t': t, 'mode': 'demo', 'symbol': DOGE, 'tag': tag, 'pnl': pnl, 'side': 'BUY', 'micro': micro}


def engine_at(tmp_path, now=NOON, **prof):
    e, _, _ = micro_engine(tmp_path, **prof)
    e.clock = lambda: now
    return e


# ============================================================================= 2-loss session stop
def test_two_micro_losses_today_stop_entries_until_the_next_day(tmp_path):
    e = engine_at(tmp_path)
    e.all_rows.extend([row(NOON - 900, -0.01), row(NOON - 300, -0.02)])
    nxt = datetime(2026, 11, 14).timestamp()
    assert e.micro_session_halt_until() == pytest.approx(nxt)
    ok, why = e.gates({}).rune
    assert not ok and why.startswith('MICRO SESSION STOP: 2 losses in a row today')
    e.clock = lambda: nxt + 1                                            # a new day: the stop is over
    assert e.micro_session_halt_until() is None


def test_one_loss_or_a_winner_in_between_does_not_stop(tmp_path):
    e = engine_at(tmp_path)
    e.all_rows.append(row(NOON - 900, -0.01))
    assert e.micro_session_halt_until() is None
    e.all_rows.extend([row(NOON - 600, +0.02), row(NOON - 300, -0.01)])
    assert e.micro_session_halt_until() is None                          # the winner reset the streak


def test_yesterday_losses_do_not_count_and_other_rows_are_skipped(tmp_path):
    e = engine_at(tmp_path)
    y = NOON - 86_400
    e.all_rows.extend([row(y, -0.01), row(y + 60, -0.01), row(NOON - 300, -0.01)])
    assert e.micro_session_halt_until() is None                          # only one loss today
    e.all_rows.extend([row(NOON - 200, -5.0, tag='TEST'), row(NOON - 150, -1.0, micro=None),
                       {'t': NOON - 120, 'tag': 'AUTO', 'micro': True, 'pnl': None}])
    assert e.micro_session_halt_until() is None                          # TEST, non-micro and UNKNOWN pnl: skipped
    e.all_rows.append(row(NOON - 60, -0.01))
    assert e.micro_session_halt_until() is not None


def test_off_switch_and_non_micro_accounts(tmp_path):
    rows_ = [row(NOON - 900, -0.01), row(NOON - 300, -0.01)]
    off = engine_at(tmp_path / 'o', micro_session_losses=0)
    off.all_rows.extend(rows_)
    assert off.micro_session_halt_until() is None
    big = engine_at(tmp_path / 'b', wallet=5000.0)
    big.all_rows.extend(rows_)
    assert not big.micro_active() and big.micro_session_halt_until() is None


# ============================================================================= chop harvester retired
def test_micro_harvester_on_is_refused_shadow_and_off_still_parse():
    with pytest.raises(ValueError, match='retired'):
        micro_capital_env({'MICRO_HARVESTER': 'on'})
    assert micro_capital_env({'MICRO_HARVESTER': 'shadow'}) == {'micro_harvester': 'shadow'}
    assert micro_capital_env({'MICRO_HARVESTER': 'OFF'}) == {'micro_harvester': 'off'}


# ============================================================================= micro-Kelly
def test_kelly_risk_is_zero_without_evidence_or_edge():
    assert fractional_kelly_risk(5.0, 0, 0, 2.0, 0.05) == 0.0             # p_lcb of Beta(1, 1) is far below 1/3
    assert fractional_kelly_risk(5.0, 30, 70, 2.0, 0.05) == 0.0           # 30 % wins at 2R: f* < 0
    assert fractional_kelly_risk(5.0, 60, 40, None, 0.05) == 0.0          # unknown payoff
    assert fractional_kelly_risk(0.0, 60, 40, 2.0, 0.05) == 0.0


def test_kelly_risk_rises_with_evidence_and_never_passes_the_caps():
    small = fractional_kelly_risk(5.0, 12, 8, 1.0, 0.05)
    big = fractional_kelly_risk(5.0, 120, 80, 1.0, 0.05)
    assert 0.0 <= small < big <= 0.05
    # hand check: Beta(121, 81): mean 0.599, sd 0.0344, LCB 0.542 -> f* 0.084 -> 0.25 x 0.084 x 5 = 0.105 -> cap 0.05
    assert big == pytest.approx(0.05)
    assert fractional_kelly_risk(1.0, 900, 100, 2.0, 0.05) == pytest.approx(0.03)  # 3 % of a 1 U account caps a big edge
    assert fractional_kelly_risk(5.0, 120, 80, 1.0, 0.05, conf_scale=0.1) < big    # low confluence only scales down


def test_confluence_scale_needs_3_of_3_and_strong_ofi_for_full_size():
    assert confluence_scale(3, 3, 0.80) == 1.0
    assert confluence_scale(3, 3, 0.50) == 0.5
    assert confluence_scale(3, 3, None) == 0.5                           # unknown OFI never earns the full size
    assert confluence_scale(2, 3, -0.90) == pytest.approx(2 / 3)         # |OFI| counts for either side
    assert confluence_scale(5, 3, 0.9) == 1.0                            # never above 1
    with pytest.raises(ValueError):
        confluence_scale(1, 0)


# ============================================================================= growth simulator
def test_required_trades_per_hour_for_10u():
    assert gs.required_per_hour(10.0, 0.05, 1.9, 1.0) == pytest.approx(10.0 / 0.095)   # 105.3 winners / hour
    assert gs.required_per_hour(10.0, 0.05, 1.9, 0.30) == math.inf                    # negative edge: never
    assert gs.required_per_hour(10.0, 0.05, 1.9, 0.55) == pytest.approx(10.0 / (0.05 * (0.55 * 1.9 - 0.45)))


def test_log_growth_and_kelly():
    assert gs.kelly_f(0.5, 1.0) == 0.0 and gs.kelly_f(0.6, 2.0) == pytest.approx(0.4)
    f = gs.kelly_f(0.6, 2.0)
    assert gs.log_growth(0.6, 2.0, f) > gs.log_growth(0.6, 2.0, f / 2) > 0 > gs.log_growth(0.6, 2.0, 0.9)
    with pytest.raises(ValueError):
        gs.log_growth(0.6, 2.0, 1.0)


def test_simulation_respects_rules_and_sign_of_the_edge():
    rng = random.Random(3)
    always_win = gs.simulate(lambda: 2.0, n_hour=6, hours=1, rng=rng)
    assert always_win['equity'] == pytest.approx(5.0 + 6 * 0.10) and always_win['trades'] == 6
    always_lose = gs.simulate(lambda: -1.0, n_hour=6, hours=24, rng=rng)
    assert always_lose['trades'] == 2                                    # 2-loss session stop, then the day ends
    assert always_lose['equity'] == pytest.approx(4.9)
    neg = gs.monte_carlo([-1.0, -1.0, 1.0, -1.0, 1.0], n_hour=6, hours=24 * 7, paths=300)   # mean -0.2 R
    pos = gs.monte_carlo([-1.0, 2.0], n_hour=6, hours=24 * 7, paths=300)                    # mean +0.5 R
    assert neg['median'] < 5.0 < pos['median'] and pos['p_ruin'] == 0.0
