"""10-08z fee drag: pre-stop maker exit, scaled TP1, demo caps (risk 5 U / $1 000 notional) with a breaker.
Behaviour tests; the economics are in docs/research_upgrade/report_fee_optimization_soft_maker_2026-10-08z.md."""
import json
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


async def long_with_ratchet(tmp_path, **prof):
    e, venue, feed = await btc_long_at(tmp_path, 84_000.0, **prof)
    p = e.pos
    p['sl'] = eng.round_tick(p['entry'] * 1.0008, 0.1, 'up')          # exchange ratchet at +0.08 %
    p['ratchet'] = True
    p['mfe_pct'] = 0.16
    return e, venue, feed, p


# ============================================================================ 1. pre-stop maker exit
async def test_soft_level_sits_above_the_ratchet_stop(tmp_path):
    e, venue, feed, p = await long_with_ratchet(tmp_path, pre_stop_maker=True)
    entry = p['entry']
    await e._manage_soft(p, 1, entry * 1.0015, 0.15)
    assert p['soft_kind'] == 'PRESTOP'
    assert p['soft_stop'] == pytest.approx(eng.round_tick(p['sl'] * 1.0004, 0.1, 'up'))
    assert p['soft_stop'] > p['sl'] and not p.get('exiting')


async def test_retrace_meets_the_maker_exit_before_the_taker_stop(tmp_path):
    e, venue, feed, p = await long_with_ratchet(tmp_path, pre_stop_maker=True)
    entry, sl = p['entry'], p['sl']
    await e._manage_soft(p, 1, entry * 1.0015, 0.15)
    mark = entry * 1.0010                                              # between the stop and the pre-stop level
    assert sl < mark < p['soft_stop']
    await e._manage_soft(p, 1, mark, 0.10)
    assert p.get('exiting') and p['exiting']['reason'] == 'SOFT-PRESTOP' and p['sl'] == sl   # stop stays


async def test_without_it_the_soft_be_sits_under_the_exchange_stop(tmp_path):
    """Trade #49: soft BE +0.06 % under the ratchet stop +0.08 % -> the STOP_MARKET (taker) fires first."""
    e, venue, feed, p = await long_with_ratchet(tmp_path, pre_stop_maker=False)
    await e._manage_soft(p, 1, p['entry'] * 1.0015, 0.15)
    assert p['soft_kind'] in ('BE', 'LOCK') and p['soft_stop'] < p['sl']


async def test_no_pre_stop_while_the_stop_is_under_the_entry(tmp_path):
    e, venue, feed = await btc_long_at(tmp_path, 84_000.0, pre_stop_maker=True)
    p = e.pos
    p['mfe_pct'] = 0.11
    await e._manage_soft(p, 1, p['entry'] * 1.0011, 0.11)
    assert p['soft_kind'] != 'PRESTOP' and p['sl'] < p['entry']


def test_trade_49_arithmetic():
    """BUY 2.04 @113.47, ratchet STOP_MARKET out at 113.55: gross 0.1632, fees maker 2 + taker 4 bps (demo)."""
    rows = [json.loads(x) for x in (ROOT / 'data' / 'engine_trades.jsonl').read_text(encoding='utf-8').splitlines()
            if x.strip()] if (ROOT / 'data' / 'engine_trades.jsonl').exists() else []
    r = next((x for x in rows if x.get('qty') == 2.04 and x.get('entry') == 113.47), None)
    if r is None:
        pytest.skip('needs ledger row #49')
    n = r['qty'] * r['entry']
    assert r['gross'] == pytest.approx(0.1632, abs=1e-4) and r['fee_bps'] == pytest.approx(6.0, abs=0.05)
    pre = eng.round_tick(113.56 * 1.0004, 0.01, 'up')                  # pre-stop maker level over the 113.56 stop
    net_maker = r['qty'] * (pre - r['entry']) - n * 4e-4               # maker in + maker out
    assert net_maker > 3 * r['pnl']


# ============================================================================ 2. scaled TP1
async def test_scaled_tp1_rests_half_at_030(tmp_path):
    e, venue, feed = prod_engine(tmp_path, tp1_scaled_pct=0.30, portfolio_risk_usdt=100.0)
    await e.sync()
    d, diag = passing_state(e)
    res = await fill_sol(e, feed, e.execute('BUY', d, 'SWARM'))
    p = e.pos
    assert res['ok'] and p['split']
    assert p['tp'] == pytest.approx(eng.round_tick(p['entry'] * 1.003, 0.01, 'up'))
    assert p['tp_plan'] > p['tp'] and p['tp_qty'] < p['qty']
    tp = venue.orders[p['tp_cid']]
    assert tp['price'] == pytest.approx(p['tp']) and tp['qty'] == pytest.approx(p['tp_qty'])


@pytest.mark.parametrize('kw,path', [({'tp1_scaled_pct': None}, 'SWARM'), ({'tp1_scaled_pct': 0.30}, 'MR')])
async def test_scaled_tp1_scope(tmp_path, kw, path):
    e, venue, feed = prod_engine(tmp_path, portfolio_risk_usdt=100.0, net_rr_min=0.0, **kw)
    await e.sync()
    d, diag = passing_state(e)
    if path == 'MR':
        import asyncio
        from tests.test_two_mode_engine import depth
        task = asyncio.ensure_future(e.execute('BUY', d, 'MR'))
        await asyncio.sleep(0.15)
        for _ in range(3):
            feed.on_message(depth(149.5, 149.51, sym='SOLUSDT'))
        await task
    else:
        await fill_sol(e, feed, e.execute('BUY', d, path))
    p = e.pos
    assert p and p['tp'] == p['tp_plan']


# ============================================================================ 3. demo caps + breaker
def test_demo_caps_and_live_unchanged():
    d, l = eng.PROFILES['demo'], eng.PROFILES['live']
    assert d.portfolio_risk_usdt == 5.0 and d.cs_max_notional == 350.0 and d.pre_stop_maker and d.tp1_scaled_pct == 0.30   # 10-08z2 cap $350
    assert l.portfolio_risk_usdt == 1.0 and l.tp1_scaled_pct is None and l.conviction_sizing == 'off'
    assert (l.max_loss_usdt, l.equity_cap) == (0.05, 3.85)


def sized(tmp_path, wallet, **prof):
    e, venue, feed = prod_engine(tmp_path, **prof)
    e.account = {'wallet': wallet, 'available': wallet}
    d, diag = passing_state(e)
    d = replace(d, score=0.97)
    e.diag = {**diag, 'consensus': 0.97}
    f = {**e.features, 'ofi_10s': 0.9}
    return e, d, f, e.bracket_plan('SWARM', f)['profile']


def test_notional_ceiling_clips(tmp_path):
    e, d, f, base = sized(tmp_path, 20_000.0, portfolio_risk_usdt=100.0, cs_max_notional=1000.0,
                         cs_trend_notional=1e9, cs_mid_notional=1e9, cs_chop_notional=1e9)
    cs = e.conviction_size(d, f, 150.0, base)
    assert cs['clip'] == 'notional ceiling' and cs['notional'] <= 1000.0 + 1e-9


def test_risk_cap_5_allows_the_10pct_tier_on_4922(tmp_path):
    """10-08z2: the 10 % tier ($492) is now cut by the regime cap (trend $315), not by the risk cap."""
    e, d, f, base = sized(tmp_path, 4922.0, portfolio_risk_usdt=5.0, cs_max_notional=1000.0)
    cs = e.conviction_size(d, f, 113.5, base)
    assert cs['frac'] == 0.10 and cs['clip'] == 'regime trend' and cs['notional'] <= 315.0


def test_breaker_pauses_conviction_sizing(tmp_path):
    e, d, f, base = sized(tmp_path, 4922.0, portfolio_risk_usdt=5.0)
    e.all_rows = [{'symbol': e.p.symbol, 'pnl': -1.1, 'conviction': {'qty': 4.3}} for _ in range(5)]
    cs = e.conviction_size(d, f, 113.5, base)
    assert cs['qty'] is None and cs['clip'].startswith('breaker -5.50 U')
    e.all_rows = e.all_rows[:4]
    assert e.conviction_size(d, f, 113.5, base)['qty']


# ============================================================================ replay tool
def test_replay_exit_state_machine():
    sys.path.insert(0, str(ROOT / 'tools'))
    import replay_exit_policies as rx
    entry = 100.0
    path = [100.0, 100.16, 100.14, 100.11, 100.105, 100.09, 100.07]          # MFE 0.16 % then retrace
    side = [1, 1, -1, -1, 1, -1, -1]                                          # +1 = buyer-initiated print
    t = np.arange(len(path), dtype=float)
    p, s = np.array(path), np.array(side, dtype=float)
    cur, why_cur = rx.run_exit(t, p, s, 0, entry, 1, 0.35, 0.88, False, 2.0, 5.0, 1.0)
    pre, why_pre = rx.run_exit(t, p, s, 0, entry, 1, 0.35, 0.88, True, 2.0, 5.0, 1.0)
    assert why_cur == 'STOP' and cur == pytest.approx(8 - 5 - 1)
    assert why_pre == 'SOFT' and pre == pytest.approx(10.5 - 2, abs=0.01)    # buyer lifts at +0.105 %
