"""10-08y position sizing: Dynamic Conviction Sizing (clipped to the existing caps) and the Monte Carlo tool.
Behaviour tests; the economics (ledger SOL mean -2.0 bps / trade, CI includes 0) are in the report."""
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from core import engine as eng
from core import micro_swarm as ms
from tests.test_execution_funnel_feasibility import passing_state, prod_engine
from tests.test_multi_asset_scanner import fill_sol
from tests.test_two_mode_engine import sig

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import simulate_position_sizing as sps  # noqa: E402

BUY = ms.evaluate(sig(FLOW=('BUY', 1.0, 20), TREND=('BUY', 1.0, 20)), ms.Gates(cost_bps=4))[0]


def sized(tmp_path, score=0.97, ofi=0.9, wallet=5000.0, **prof):
    e, venue, feed = prod_engine(tmp_path, **prof)
    e.account = {'wallet': wallet, 'available': wallet}
    d, diag = passing_state(e)
    d = replace(d, score=score)
    e.diag = {**diag, 'consensus': score}
    f = {**e.features, 'ofi_10s': ofi}
    base = e.bracket_plan('SWARM', f)['profile']
    return e, d, f, base, feed


def risk_pct(e, base):
    return (base.sl_pct + 0.08) / 100


# ============================================================================ tiers
@pytest.mark.parametrize('score,ofi,frac', [(0.97, 0.90, 0.10), (0.97, 0.50, 0.05), (0.92, 0.90, 0.05),
                                            (0.85, 0.90, 0.03), (0.80, 0.0, 0.03), (0.79, 0.95, None)])
def test_tiers(tmp_path, score, ofi, frac):
    e, d, f, base, feed = sized(tmp_path, score, ofi, portfolio_risk_usdt=100.0)
    cs = e.conviction_size(d, f, 150.0, base)
    assert cs['frac'] == frac
    if frac:
        assert cs['qty'] == pytest.approx(eng.floor_step(frac * 5000 / 150.0, 0.01)) and cs['clip'] is None


def test_super_tier_is_clipped_by_the_unchanged_risk_cap(tmp_path):
    """10 % of 5 000 U = $500 notional = 2.15 U at the stop > portfolio_risk_usdt 1.0: the size is cut, not refused,
    and the cap is not raised."""
    e, d, f, base, feed = sized(tmp_path)
    assert e.p.portfolio_risk_usdt == 1.0
    cs = e.conviction_size(d, f, 150.0, base)
    assert cs['clip'] == 'risk cap' and cs['qty'] * 150.0 * risk_pct(e, base) <= 1.0 + 1e-9
    assert cs['notional'] < 0.10 * 5000


def test_open_positions_use_up_the_budget(tmp_path):
    e, d, f, base, feed = sized(tmp_path, portfolio_risk_usdt=3.0)
    free = e.conviction_size(d, f, 150.0, base)['qty']

    class Peer:
        def risk_at_stop(self):
            return 2.0

        def margin_used(self):
            return 0.0
    e.open_peers = lambda: [Peer()]
    assert e.conviction_size(d, f, 150.0, base)['qty'] < free


def test_margin_cap_clips(tmp_path):
    e, d, f, base, feed = sized(tmp_path, portfolio_risk_usdt=100.0, portfolio_margin_frac=0.01)
    cs = e.conviction_size(d, f, 150.0, base)
    assert cs['clip'] == 'margin cap' and cs['notional'] <= 0.01 * 5000 * e.p.leverage + 1e-6


def test_btc_and_unknown_wallet_get_no_size(tmp_path):
    e, d, f, base, feed = sized(tmp_path, wallet=0.0, portfolio_risk_usdt=100.0)
    assert e.conviction_size(d, f, 150.0, base)['qty'] is None
    e2, d, f, base, feed = sized(tmp_path, portfolio_risk_usdt=100.0)
    e2.p = replace(e2.p, symbol='BTCUSDT')
    assert e2.conviction_size(d, f, 150.0, base)['tier'] is None


async def test_shadow_logs_and_keeps_the_size(tmp_path):
    sizes = {}
    for mode in ('off', 'shadow'):
        e, venue, feed = prod_engine(tmp_path / mode, conviction_sizing=mode, portfolio_risk_usdt=100.0)
        await e.sync()
        e.account = {'wallet': 5000.0, 'available': 5000.0}
        d, diag = passing_state(e)
        e.diag = {**diag, 'consensus': 0.97}
        res = await fill_sol(e, feed, e.execute('BUY', replace(d, score=0.97), 'SWARM'))
        assert res['ok']
        sizes[mode] = e.pos['qty']
        row = json.loads((tmp_path / mode / 'engine_orders.jsonl').read_text(encoding='utf-8').splitlines()[-1])
        if mode == 'shadow':
            assert row['cs_qty'] and row['cs_frac'] in (0.05, 0.10) and e.pos['conviction']['qty'] == row['cs_qty']
    assert sizes['off'] == sizes['shadow']


async def test_on_uses_the_conviction_size(tmp_path):
    e, venue, feed = prod_engine(tmp_path, conviction_sizing='on', portfolio_risk_usdt=100.0)
    await e.sync()
    e.account = {'wallet': 5000.0, 'available': 5000.0}
    d, diag = passing_state(e)
    e.diag = {**diag, 'consensus': 0.92}
    res = await fill_sol(e, feed, e.execute('BUY', replace(d, score=0.92), 'SWARM'))
    row = json.loads((tmp_path / 'engine_orders.jsonl').read_text(encoding='utf-8').splitlines()[-1])
    assert res['ok'] and e.pos['qty'] == pytest.approx(row['cs_qty']) and row['cs_frac'] == 0.05
    assert any(ev['message'].startswith('CONVICTION SIZE') for ev in e.events)


@pytest.mark.parametrize('path', ['MR', 'SCALP'])
async def test_mr_and_scalp_are_not_resized(tmp_path, path):
    e, venue, feed = prod_engine(tmp_path, conviction_sizing='on', portfolio_risk_usdt=100.0, net_rr_min=0.0)
    await e.sync()
    e.account = {'wallet': 5000.0, 'available': 5000.0}
    await e.execute('BUY', replace(BUY, score=0.97), path)
    rows = (tmp_path / 'engine_orders.jsonl').read_text(encoding='utf-8').splitlines()
    assert 'cs_qty' not in json.loads(rows[-1])


def test_defaults():
    d, l = eng.PROFILES['demo'], eng.PROFILES['live']
    assert d.conviction_sizing in ('shadow', 'on') and l.conviction_sizing == 'off'
    assert d.cs_tiers == ((0.95, 0.10), (0.90, 0.05), (0.80, 0.03)) and d.cs_super_ofi == 0.85
    assert d.portfolio_risk_usdt in (1.0, 2.5) and eng.SUPER_SIZE == {'SOLUSDT': 0.80, 'DOGEUSDT': 500.0}   # caps unchanged


# ============================================================================ Monte Carlo tool
def test_returns_are_pnl_over_notional():
    rows = [{'symbol': 'SOLUSDT', 'qty': 0.4, 'entry': 114.84, 'pnl': 0.0976}, {'symbol': 'DOGEUSDT', 'qty': 1,
            'entry': 1.0, 'pnl': None}]
    r = sps.returns(rows, 'SOLUSDT')
    assert r[0] * 1e4 == pytest.approx(21.25, abs=0.01) and len(sps.returns(rows)) == 1


def test_zero_size_keeps_the_equity():
    r = np.array([0.002, -0.001, 0.0005])
    out = sps.simulate(r, 0.0, 'boot', paths=200, n=50)
    assert out['terminal_p05'] == out['terminal_p95'] == 5000.0 and out['mdd_p99'] == 0.0


def test_drawdown_scales_with_size():
    r = np.array([0.002, -0.0025, 0.001, -0.001, 0.0015])
    a, b = sps.simulate(r, 0.05, 'boot', 2000, 100), sps.simulate(r, 0.10, 'boot', 2000, 100)
    assert b['mdd_p50'] == pytest.approx(2 * a['mdd_p50'], rel=0.1)


def test_null_removes_the_edge():
    r = np.array([0.003, 0.002, -0.001, 0.004])
    out = sps.simulate(r, 0.1, 'null', 4000, 200)
    assert abs(out['growth_per_trade_bps']) < 0.05 and 0.3 < out['p_loss'] < 0.7


def test_circular_block_sampling_is_unbiased():
    r = np.arange(15, dtype=float)
    x = sps.sample(r, 'block', 20000, 30, np.random.default_rng(1))
    assert x.mean() == pytest.approx(r.mean(), abs=0.05) and x.min() == 0 and x.max() == 14


def test_option_b_ruins_a_negative_sample():
    r = np.array([-0.0022, 0.0012, -0.0014, 0.0021, -0.0010, 0.0004])
    out = sps.simulate(r, 0.10 / 0.0043, 'boot', 2000, 200)
    assert out['p_dd_50_ruin'] > 0.5


def test_kelly_sign_follows_the_mean():
    assert sps.stats(np.array([0.001, -0.002, 0.0005, -0.0011]))['kelly_f'] < 0
    assert sps.stats(np.array([0.002, -0.001, 0.0015, -0.0005]))['kelly_f'] > 0


def test_walk_book():
    levels = [(100.0, 1.0), (100.1, 2.0), (100.2, 5.0)]
    assert sps.walk_book(levels, 0.5) == 0.0
    assert sps.walk_book(levels, 3.0) == pytest.approx((100.0 + 2 * 100.1) / 3 / 100 * 1e4 - 1e4, abs=1e-6)
    assert sps.walk_book(levels, 10.0) is None
