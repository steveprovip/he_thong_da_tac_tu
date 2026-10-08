"""MR anti-fade + net RR + fast re-peg evidence + super-conviction size (2026-10-08s).

#39: MR BUY 300 DOGE at OFI -0.93 with a fee-true reward:risk of 0.63 (break-even win rate 61 %) -> SL in 50 s,
net -0.04001 U. Both new gates block it. Behaviour tests only; not evidence of profitability."""
import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from core import engine as eng
from core import micro_signals as msig
from core import micro_swarm as ms
from tests.test_multi_asset_scanner import SOL, fill_sol, mr_feed, scanner, sol_feed
from tests.test_two_mode_engine import depth, mock_engine, sig

ROOT = Path(__file__).resolve().parents[1]
DOGE = 'DOGEUSDT'
GATES = {'mr_flow_gate': True, 'net_rr_min': 1.30}
BUY = ms.evaluate(sig(FLOW=('BUY', 1.0, 20), TREND=('BUY', 1.0, 20)), ms.Gates(cost_bps=4))[0]
SELL = ms.evaluate(sig(FLOW=('SELL', 1.0, 20), TREND=('SELL', 1.0, 20)), ms.Gates(cost_bps=4))[0]


def engine_for(tmp_path, sym, **prof):
    e, venue, feed = mock_engine(tmp_path, **prof)
    e.p = replace(e.p, symbol=sym, qty=eng.SYMBOL_QTY.get(sym, 0.0))
    e.spec = dict(eng.FALLBACK_SPECS[sym])
    e.sig_cfg = msig.config_for(sym)
    e.feed.cfg = msig.config_for(sym)
    return e


def mr_case(e, side='BUY', ofi=-0.929, cvd=-20_000.0, dev=-0.1415, score=0.859):
    """The #39 decision: MR signal on the decision side, DOGE features at entry."""
    e.sig = {'MR': {'side': side, 'strength': 1.0, 'edge_bps': 14.1, 'why': 'MR'}}
    e.features = {'ofi_10s': ofi, 'cvd_60s': cvd, 'vwap_dev_pct': dev, 'atr_pct': 0.0932, 'ker': 0.03,
                  'px_vel_15s': 0.0}
    d = replace(BUY if side == 'BUY' else SELL, score=score)
    return d, {'consensus': score, 'jev': 0.0, 'gates_ok': False, 'edge_bps': 14.1}


def ledger():
    p = ROOT / 'data' / 'engine_trades.jsonl'
    rows = [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines() if x.strip()] if p.exists() else []
    if len(rows) < 40:
        pytest.skip('needs the 40-trade ledger')
    return rows[:40]


# ============================================================================= Pillar 1: MR anti-fade
@pytest.mark.parametrize('side,ofi,cvd,blocked', [
    ('BUY', -0.929, 0.0, True), ('BUY', -0.31, 0.0, True), ('BUY', -0.29, 0.0, False),
    ('BUY', 0.2, -100_001.0, True), ('BUY', 0.2, -99_999.0, False), ('SELL', 0.31, 0.0, True),
    ('SELL', -0.9, 0.0, False), ('SELL', 0.0, 100_001.0, True), ('BUY', None, 0.0, True)])
def test_mr_flow_veto_rule(side, ofi, cvd, blocked):
    why = msig.mr_flow_veto(side, {'ofi_10s': ofi, 'cvd_60s': cvd}, 0.30, 100_000.0)
    assert bool(why) is blocked and (not why or why.startswith(f'MR FLOW {side}'))


def test_trade_39_is_blocked_by_the_flow_gate(tmp_path):
    e = engine_for(tmp_path, DOGE, **GATES)
    d, diag = mr_case(e)
    assert e.lane(d) == 'MR'
    ok, why = e.churn_gate(d, diag)
    assert not ok and why.startswith('MR FLOW BUY vs OFI -0.93')


def test_cvd_threshold_is_scaled_per_symbol(tmp_path):
    e = engine_for(tmp_path, DOGE, **GATES)                                  # usd_scale 0.1 -> $10k
    d, diag = mr_case(e, ofi=0.1, cvd=-12_000.0)
    assert e.churn_gate(d, diag)[1].startswith('MR FLOW BUY vs CVD 60s -12k')


def test_trade_39_is_also_blocked_by_net_rr(tmp_path):
    e = engine_for(tmp_path, DOGE, mr_flow_gate=False, net_rr_min=1.30)
    d, diag = mr_case(e, ofi=0.1)
    bp = e.bracket_plan('MR', e.features)
    assert bp['rr_net'] == pytest.approx(0.634, abs=0.002) and bp['be_wr'] == pytest.approx(0.612, abs=0.002)
    ok, why = e.churn_gate(d, diag)
    assert not ok and why.startswith('NET RR 0.63 < 1.3 (MR') and 'break-even win rate 61%' in why


def test_mr_lane_cannot_reach_net_rr_130_with_current_brackets(tmp_path):
    """MR TP is clamped to <= 0.18 % with SL 0.08 %: best case (0.18 - 0.04) / (0.08 + 0.08) = 0.875.
    With the 1.30 gate the MR lane is effectively off until its brackets change (documented, intended)."""
    e = engine_for(tmp_path, DOGE)
    best = e.bracket_plan('MR', {'vwap_dev_pct': 5.0})
    assert best['tp_pct'] == pytest.approx(0.18) and best['rr_net'] == pytest.approx(0.875, abs=1e-3)
    assert best['rr_net'] < 1.30


@pytest.mark.parametrize('sym,atr', [('BTCUSDT', None), ('BTCUSDT', 0.05), (SOL, None), (SOL, 0.08),
                                     (DOGE, None), (DOGE, 0.09)])
def test_trend_brackets_clear_net_rr_130(tmp_path, sym, atr):
    e = engine_for(tmp_path, sym)
    assert e.bracket_plan('SWARM', {'atr_pct': atr} if atr else {})['rr_net'] >= 1.30


def test_ledger_replay_of_both_gates():
    rows = ledger()
    r39, r34 = rows[38], rows[33]
    assert r39['path'] == 'MR' and r39['side'] == 'BUY' and r39['pnl'] == pytest.approx(-0.0400122)
    assert r39['ofi'] == pytest.approx(-0.929, abs=1e-3) and r39['rr_net'] == pytest.approx(0.634, abs=1e-3)
    assert msig.mr_flow_veto('BUY', {'ofi_10s': r39['ofi'], 'cvd_60s': 0.0}) is not None
    assert r34['path'] == 'MR' and r34['side'] == 'SELL' and r34['ofi'] > 0.30 and r34['rr_net'] < 1.30
    swarm = [r for r in rows if r.get('path') == 'SWARM' and r.get('rr_net') is not None]
    assert swarm and min(r['rr_net'] for r in swarm) >= 1.30          # no TREND trade would have been blocked


async def test_mr_feed_tick_is_refused_with_the_gates(tmp_path):
    e, venue, feed = mock_engine(tmp_path, feed=mr_feed(), gtx_ttl_s=0.3, **GATES)
    await e.sync()
    d = await e.tick()
    assert e.lane(d) == 'MR' or d.reason.startswith(('MR FLOW', 'NET RR'))
    assert not d.allowed and d.reason.startswith(('MR FLOW', 'NET RR')) and e.pos is None and not venue.fills


# ============================================================================= Pillar 3: re-peg evidence
async def test_repeg_happens_within_1_5s_and_is_logged(tmp_path):
    e, venue, feed = mock_engine(tmp_path, gtx_ttl_s=3.0)
    await e.sync()
    t0 = time.monotonic()
    task = asyncio.ensure_future(e.tick())
    await asyncio.sleep(0.15)
    bid0 = feed.bid
    for _ in range(3):
        feed.on_message(depth(bid0 + 2.0, bid0 + 2.1, bq=6.0, aq=1.0))       # touch walks away
    while not any(ev['message'].startswith('PEG') for ev in e.events) and time.monotonic() - t0 < 1.5:
        posted = [o for o in venue.orders.values() if o['kind'] == 'ENTRY' and o['status'] == 'NEW']
        if posted and posted[-1]['price'] == pytest.approx(bid0 + 2.0):
            break
        await asyncio.sleep(0.05)
    assert time.monotonic() - t0 < 1.5                                       # re-quoted before 1.5 s
    for _ in range(3):
        feed.on_message(depth(bid0 + 1.9, bid0 + 2.0, bq=6.0, aq=1.0))
    d = await task
    o = [json.loads(x) for x in (tmp_path / 'engine_orders.jsonl').read_text(encoding='utf-8').splitlines()][-1]
    assert e.pos and o['outcome'] == 'FILLED' and o['reprices'] == 1 and o['px_last'] > o['px_first']


async def test_unfilled_order_logs_the_touch_at_expiry(tmp_path):
    e, venue, feed = mock_engine(tmp_path, gtx_ttl_s=0.6)
    await e.sync()
    await e.tick()                                                          # touch never moves, never trades
    o = [json.loads(x) for x in (tmp_path / 'engine_orders.jsonl').read_text(encoding='utf-8').splitlines()][-1]
    assert o['outcome'] == 'UNFILLED' and o['reprices'] == 0
    assert o['touch_last'] == pytest.approx(o['px_first']) and o['touch_qty_last'] is not None   # queue, not drift


def test_session_unfilled_orders_were_queue_not_drift():
    """Order log 10-08: of 32 UNFILLED GTX entries 25 had 0 re-pegs (px_last == px_first): the touch did not
    walk away, so a faster re-peg (already 0.5 s < 1.5 s) cannot fill them."""
    p = ROOT / 'data' / 'engine_orders.jsonl'
    rows = [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines()] if p.exists() else []
    unf = [r for r in rows if r['outcome'] == 'UNFILLED']
    if len(unf) < 30:
        pytest.skip('needs the 10-08 order log')
    still = [r for r in unf if not r.get('reprices')]
    assert len(still) / len(unf) > 0.7
    assert eng.PROFILES['demo'].peg_interval_s <= 1.5 and eng.PROFILES['demo'].peg_max_drift_pct >= 0.10


# ============================================================================= Pillar 4: super-conviction size
def test_super_conviction_rule(tmp_path):
    e = engine_for(tmp_path, DOGE, super_size=True)
    d = replace(BUY, score=0.994)
    assert e.super_conviction(d, {'consensus': 0.994}, {'ofi_10s': 0.833})                    # #40
    assert not e.super_conviction(replace(d, score=0.94), {'consensus': 0.94}, {'ofi_10s': 0.9})
    assert not e.super_conviction(d, {'consensus': 0.994}, {'ofi_10s': 0.79})
    assert e.super_conviction(replace(SELL, score=0.97), {'consensus': 0.97}, {'ofi_10s': -0.85})
    assert not e.super_conviction(replace(BUY, score=0.8), {'consensus': 0.8, 'jev': 9.9}, {'ofi_10s': 0.9})
    btc = engine_for(tmp_path, 'BTCUSDT', super_size=True)
    assert not btc.super_conviction(d, {'consensus': 1.0}, {'ofi_10s': 0.9})
    assert not eng.PROFILES['live'].super_size


async def test_sol_super_conviction_opens_the_concentrated_size(tmp_path):
    feeds = {'BTCUSDT': mr_feed(), SOL: sol_feed()}                         # sol_feed: vote 100 %, OFI +1.0
    es, venue, _ = await scanner(tmp_path, feeds, super_size=True, ker_gate=False, min_edge_bps=0.0,
                                 concordance_gate=False, min_velocity_pct=0.0, fee_hurdle=False)
    sol = es[SOL]
    d = await fill_sol(sol, feeds[SOL], sol.tick())
    await sol.sync()
    p = sol.pos
    assert d.allowed and p['super_size'] and p['qty'] == pytest.approx(eng.SUPER_SIZE[SOL])      # 0.80 since 10-08u
    assert p['qty'] * p['entry'] <= 140.0 and sol.risk_at_stop() < 0.85
    assert any('ALPHA CONCENTRATION SOL' in ev['message'] for ev in sol.events)


async def test_without_super_conviction_the_size_is_unchanged(tmp_path):
    feeds = {'BTCUSDT': mr_feed(), SOL: sol_feed()}
    es, venue, _ = await scanner(tmp_path, feeds, super_size=False, ker_gate=False, min_edge_bps=0.0,
                                 concordance_gate=False, min_velocity_pct=0.0, fee_hurdle=False)
    sol = es[SOL]
    await fill_sol(sol, feeds[SOL], sol.tick())
    await sol.sync()
    assert sol.pos and not sol.pos['super_size'] and sol.pos['qty'] < eng.SUPER_SIZE[SOL]


def test_super_size_table_and_risk():
    assert eng.SUPER_SIZE == {'SOLUSDT': 0.80, DOGE: 500.0}                  # 10-08u (was 0.50 / 450)
    doge_risk = 500 * 0.0890 * (0.50 + 0.08) / 100                         # DOGE SL floor 0.50 % + fees
    sol_risk = 0.80 * 116.0 * (0.35 + 0.08) / 100
    assert doge_risk < 0.26 and sol_risk < 0.40 and doge_risk + sol_risk < 1.0   # both inside the 1.0 U cap


def test_super_size_respects_the_notional_cap(tmp_path):
    e = engine_for(tmp_path, DOGE)
    assert eng.floor_step(min(eng.SUPER_SIZE[DOGE], eng.ALT_SIZE[DOGE][2] / 0.50), 1.0) == 280.0


def test_defaults():
    d, l = eng.PROFILES['demo'], eng.PROFILES['live']
    assert d.mr_flow_gate and d.mr_ofi_max == 0.30 and d.mr_cvd60_usd == 100_000.0 and d.net_rr_min == 1.30
    assert d.super_size and (d.super_score, d.super_ofi) == (0.95, 0.80)
    assert l.mr_flow_gate and l.net_rr_min == 1.30 and not l.super_size


# ============================================================================= net RR on the sized plan
def test_live_profile_plan_fails_net_rr_130():
    """Live DOGE: SL comes from the 0.05 U loss budget (~0.89 % at the $5 minimum) against TP 0.50 % ->
    fee-true RR ~0.47 (break-even win rate ~68 %). The execute-time gate refuses it: live stays flat until its
    TP/SL is reconsidered."""
    p = replace(eng.PROFILES['live'], symbol=DOGE)
    pl = eng.plan('BUY', 0.0890, dict(eng.FALLBACK_SPECS[DOGE]), p, 3.85)
    assert pl['ok'] and pl['sl_pct'] == pytest.approx(0.889, abs=0.01)
    e = type('E', (), {'p': p})()
    rr = eng.Engine.plan_rr(e, pl)
    assert rr == pytest.approx(0.475, abs=0.01) and rr < p.net_rr_min


async def test_execute_refuses_a_plan_below_net_rr(tmp_path):
    e, venue, feed = mock_engine(tmp_path, net_rr_min=1.30, tp_pct=0.10, sl_pct=0.20, asset_brackets=False,
                                 atr_scaler=False)
    await e.sync()
    res = await e.execute('BUY', BUY, 'SWARM')
    assert not res['ok'] and res['error'].startswith('ZEPHR NET RR 0.2') and not venue.fills
    o = [json.loads(x) for x in (tmp_path / 'engine_orders.jsonl').read_text(encoding='utf-8').splitlines()][-1]
    assert o['outcome'] == 'REFUSED'
