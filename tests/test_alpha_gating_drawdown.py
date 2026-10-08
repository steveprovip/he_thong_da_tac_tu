"""10-08z2: hard macro CVD shield, three-layer alignment (shadow), regime-adaptive size caps, TP1 >= 0.35 % for
>= $225, early adverse invalidation (shadow), soft exits never worse than the exchange stop, honest SOFT-BE labels.
Behaviour tests; economics in docs/research_upgrade/report_alpha_gating_and_drawdown_elimination_2026-10-08z.md."""
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from core import engine as eng
from tests.test_execution_funnel_feasibility import passing_state, prod_engine
from tests.test_multi_asset_scanner import fill_sol
from tests.test_two_mode_engine import btc_long_at

ROOT = Path(__file__).resolve().parents[1]


# ============================================================================ 1. CVD shield
@pytest.mark.parametrize('side,c60,c3,blocked', [
    ('BUY', -300_001, 0, True), ('BUY', -299_999, 0, False), ('BUY', 0, -500_001, True), ('BUY', 0, -499_999, False),
    ('SELL', 300_001, 0, True), ('SELL', 0, 500_001, True), ('SELL', -1e6, -1e6, False), ('BUY', 1e6, 1e6, False)])
def test_shield_rule(tmp_path, side, c60, c3, blocked):
    e, _, _ = prod_engine(tmp_path, cvd_shield='on')
    e.features = {'cvd_60s': c60, 'cvd_3m': c3}
    why = e.cvd_shield(side)
    assert bool(why) is blocked and (not why or why.startswith(f'CVD SHIELD no {side}'))


def test_shield_blocks_the_entry_in_churn(tmp_path):
    e, _, _ = prod_engine(tmp_path, cvd_shield='on')
    d, diag = passing_state(e)
    assert e.churn_gate(d, diag)[0]
    e.features.update(cvd_60s=-450_000.0)
    ok, why = e.churn_gate(d, diag)
    assert not ok and why.startswith('CVD SHIELD no BUY') and 'CVD_SHIELD' in e.blockers


def test_shield_off(tmp_path):
    e, _, _ = prod_engine(tmp_path, cvd_shield='off')
    e.features = {'cvd_60s': -1e7, 'cvd_3m': -1e7}
    assert e.cvd_shield('BUY') is None


# ============================================================================ align3
def test_align3_shadow_records_without_blocking(tmp_path):
    e, _, _ = prod_engine(tmp_path, align3='shadow')
    d, diag = passing_state(e)
    e.features.update(cvd_3m=-1.0, cvd_60s=5.0, cvd_15s=5.0, obi=0.2)
    assert e.churn_gate(d, diag)[0] and e.align3_last.startswith('ALIGN3 cvd_3m')


def test_align3_on_blocks(tmp_path):
    e, _, _ = prod_engine(tmp_path, align3='on')
    d, diag = passing_state(e)
    e.features.update(cvd_3m=5.0, cvd_60s=5.0, cvd_15s=5.0, obi=-0.1)
    ok, why = e.churn_gate(d, diag)
    assert not ok and why.startswith('ALIGN3 obi')
    e.features.update(obi=0.1)
    assert e.churn_gate(d, diag)[0]


async def test_align3_is_written_on_the_position(tmp_path):
    e, venue, feed = prod_engine(tmp_path, align3='shadow', portfolio_risk_usdt=100.0)
    await e.sync()
    d, diag = passing_state(e)
    e.churn_gate(d, diag)
    await fill_sol(e, feed, e.execute('BUY', d, 'SWARM'))
    assert e.pos and e.pos['align3']


# ============================================================================ 2. regime caps + TP1
def sized(tmp_path, ker, aligned=True, **prof):
    e, venue, feed = prod_engine(tmp_path, portfolio_risk_usdt=100.0, **prof)
    e.account = {'wallet': 4922.0, 'available': 4922.0}
    d, diag = passing_state(e)
    d = replace(d, score=0.97)
    e.diag = {**diag, 'consensus': 0.97}
    e.features.update(ker=ker)
    e.flow = {'buy_ok': aligned, 'sell_ok': aligned}
    f = {**e.features, 'ofi_10s': 0.9}
    return e, d, f, e.bracket_plan('SWARM', f)['profile']


@pytest.mark.parametrize('ker,aligned,cap,reg', [(0.20, True, 170, 'regime chop'), (0.35, True, 225, 'regime mid'),
                                                 (0.50, True, 315, 'regime trend'), (0.50, False, 170, 'regime chop')])
def test_regime_caps(tmp_path, ker, aligned, cap, reg):
    e, d, f, base = sized(tmp_path, ker, aligned, cs_max_notional=350.0)
    cs = e.conviction_size(d, f, 112.0, base)
    assert cs['regime'] == reg and cs['clip'] == reg and cs['notional'] <= cap + 1e-9 and cs['notional'] > cap - 112


def test_hard_cap_350(tmp_path):
    e, d, f, base = sized(tmp_path, 0.6, True, cs_max_notional=350.0, cs_trend_notional=1e9)
    cs = e.conviction_size(d, f, 112.0, base)
    assert cs['clip'] == 'notional ceiling' and cs['notional'] <= 350.0


@pytest.mark.parametrize('mode,tp', [('on', 0.35), ('off', 0.30)])
async def test_tp1_rises_to_035_for_big_entries(tmp_path, mode, tp):
    """Conviction 5 % of 5 000 U = $250 >= $225 -> TP1 +0.35 %; the normal ~$105 size keeps +0.30 %."""
    e, venue, feed = prod_engine(tmp_path, tp1_scaled_pct=0.30, portfolio_risk_usdt=100.0, conviction_sizing=mode,
                                 cs_max_notional=350.0)
    await e.sync()
    e.account = {'wallet': 5000.0, 'available': 5000.0}
    d, diag = passing_state(e)
    d = replace(d, score=0.92)
    e.diag = {**diag, 'consensus': 0.92}
    await fill_sol(e, feed, e.execute('BUY', d, 'SWARM'))
    p = e.pos
    assert p and p['split'] and ((p['qty'] * p['entry'] >= 225.0) == (mode == 'on'))
    assert p['tp'] == pytest.approx(eng.round_tick(p['entry'] * (1 + tp / 100), 0.01, 'up'))


# ============================================================================ 3. early invalidation
async def test_early_invalidation_shadow_records_only(tmp_path):
    e, venue, feed = await btc_long_at(tmp_path, 84_000.0, early_invalidation='shadow')
    e.features = {**(e.features or {}), 'cvd_vel_10s': -60_000.0}
    await e.manage()
    assert e.pos['early_invalid']['why'].startswith('CVD') and not e.pos.get('exiting')


async def test_early_invalidation_on_exits_at_the_touch(tmp_path):
    e, venue, feed = await btc_long_at(tmp_path, 84_000.0, early_invalidation='on')
    e.features = {**(e.features or {}), 'ofi_10s': -0.5, 'cvd_vel_10s': 0.0}
    await e.manage()
    assert e.pos['exiting']['reason'] == 'EARLY-INVALID'


async def test_early_invalidation_window_is_45s(tmp_path):
    e, venue, feed = await btc_long_at(tmp_path, 84_000.0, early_invalidation='on')
    e.pos['opened'] -= 46
    e.features = {**(e.features or {}), 'cvd_vel_10s': -1e6}
    assert e.early_invalid_due(e.pos, e.clock(), e.features) is None


# ============================================================================ soft exit floor + labels
async def test_soft_exit_never_worse_than_the_exchange_stop(tmp_path):
    """#51: SELL 113.17, ratchet stop 113.08, the demo bid jumped to 113.26 -> the maker buy-back must not pay
    more than the stop price."""
    e, venue, feed = await btc_long_at(tmp_path, 84_000.0)
    p = e.pos
    p['sl'] = 84_070.0                                                 # long: stop in profit
    p['exiting'] = {'reason': 'SOFT-PRESTOP', 'cid': None, 'px': None, 'posted': 0.0, 'started': e.clock(),
                    'reprices': 0, 'floor': None, 'floor0': None, 'ref': 84_090.0}
    e.exit_price = lambda opp, book: 84_010.0                          # the touch fell under the stop
    await e.work_soft_exit()
    assert p['exiting']['px'] == pytest.approx(84_070.0)


async def test_soft_be_below_entry_is_labelled_loss(tmp_path):
    e, venue, feed = await btc_long_at(tmp_path, 84_000.0)
    e.pos['exiting'] = {'reason': 'SOFT-BE'}

    async def parts(symbol, start_ms):
        return {'gross': -0.05, 'fee': 0.03, 'net': -0.08, 'maker_fills': 2, 'taker_fills': 0}
    venue.realized_parts = parts
    await e._settle(e.pos)
    assert e.all_rows[-1]['reason'] == 'SOFT-BE-LOSS'


async def test_soft_be_at_or_above_entry_keeps_its_label(tmp_path):
    e, venue, feed = await btc_long_at(tmp_path, 84_000.0)
    e.pos['exiting'] = {'reason': 'SOFT-BE'}

    async def parts(symbol, start_ms):
        return {'gross': 0.01, 'fee': 0.03, 'net': -0.02, 'maker_fills': 2, 'taker_fills': 0}
    venue.realized_parts = parts
    await e._settle(e.pos)
    assert e.all_rows[-1]['reason'] == 'SOFT-BE'


# ============================================================================ defaults + replay tool
def test_defaults():
    d, l = eng.PROFILES['demo'], eng.PROFILES['live']
    assert (d.cvd_shield, d.align3, d.early_invalidation) == ('on', 'shadow', 'shadow')
    assert (d.cvd_shield_60s_usd, d.cvd_shield_3m_usd, d.early_inval_s, d.early_inval_vel_usd) == (3e5, 5e5, 45.0, 5e4)
    assert d.cs_max_notional == 350.0 and (d.cs_chop_notional, d.cs_mid_notional, d.cs_trend_notional) == (170, 225, 315)
    assert d.tp1_scaled_big_pct == 0.35 and l.align3 == 'off' and l.early_invalidation == 'off'


def test_replay_early_exit_rule():
    sys.path.insert(0, str(ROOT / 'tools'))
    import replay_alpha_gating as ra
    t = np.arange(0.0, 40.0, 1.0)
    p = np.full(len(t), 100.0)
    s = np.ones(len(t))
    q = np.ones(len(t))
    s[5:12] = -1.0
    q[5:12] = 10_000.0                                               # $1M sold in 7 s -> > 50 k/s against a long
    cs = np.concatenate([[0], np.cumsum(s * p * q)])
    p[13:] = 99.98
    out = ra.early_exit(t, p, s, cs, 0, 100.0, 1, 2.0)
    assert out is not None and out[0] == pytest.approx(-2.0)
