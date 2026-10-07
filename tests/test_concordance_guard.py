"""Flow concordance guard (2026-10-07l): CVD 15 s + CVD 60 s + OFI L10 must agree with the entry, DRIFT locks
AUTO entries, the scanner gives the slot to the best-qualified symbol."""
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from core import engine as eng
from core import micro_signals as msig
from core import micro_swarm as ms
from tests.test_multi_asset_scanner import fill_sol, scanner, sol_feed, SOL
from tests.test_two_mode_engine import SYM, agg, book_at, depth, dump, mock_engine, opened_short, sig

ROOT = Path(__file__).resolve().parents[1]
PX = 84_000.0
BUY_DEC = ms.evaluate(sig(FLOW=('BUY', 1.0, 20), TREND=('BUY', 1.0, 20)), ms.Gates(cost_bps=4))[0]
SELL_DEC = ms.evaluate(sig(FLOW=('SELL', 1.0, 20), TREND=('SELL', 1.0, 20)), ms.Gates(cost_bps=4))[0]
DIAG = {'consensus': 0.8, 'jev': 9.5, 'gates_ok': True, 'edge_bps': 40.0}


def case_1534_feed(now=None, px=PX):
    """15:34: 3 minutes of selling (CVD 3m -2.36M) then a minute of buying (CVD 60 s +0.96M), book lifted (OFI > 0)."""
    now = time.time() if now is None else now
    f = msig.MicroFeed(SYM)
    for i in range(100):                                                  # -$3.32M, 175 s .. 76 s ago
        f.on_message(agg(px + 60 - i * 0.6, 3_320_000 / 100 / px, maker=True), now - 175 + i)
    for i in range(30):                                                   # +$0.96M in the last minute
        f.on_message(agg(px + i, 959_000 / 30 / px, maker=False), now - 55 + i * 1.8)
    for i in range(10):
        b = round(px + 29 - 0.1 * (9 - i), 1)
        f.on_message(depth(b, round(b + 0.1, 1), bq=6.0, aq=2.0), now)
    return f


def with_flow(e, **f):
    e.features = {**(e.features or {}), **f}
    e.flow = msig.concordance(e.features, e.sig_cfg)
    return e


# ============================================================================= 1. triple concordance
def test_concordance_rule():
    C = msig.concordance
    ok_buy = {'cvd_15s': 50_000.0, 'cvd_60s': 300_000.0, 'ofi_10s': 0.35}
    assert C(ok_buy)['state'] == 'BUY' and C(ok_buy)['buy_ok'] and not C(ok_buy)['sell_ok']
    for bad in ({'cvd_15s': 0.0}, {'cvd_60s': 299_999.0}, {'ofi_10s': 0.34}, {'cvd_60s': -10.0}):
        assert not C({**ok_buy, **bad})['buy_ok'], bad
    ok_sell = {'cvd_15s': -1.0, 'cvd_60s': -300_000.0, 'ofi_10s': -0.35}
    assert C(ok_sell)['state'] == 'SELL' and C(ok_sell)['sell_ok']
    assert C({**ok_sell, 'ofi_10s': 0.10})['state'] == 'CONFLICT'
    assert C({'cvd_15s': 1.0, 'cvd_60s': 400_000.0})['state'] == 'UNKNOWN'          # no OFI yet -> nothing passes
    sol = msig.config_for('SOLUSDT')
    assert C({'cvd_15s': 1.0, 'cvd_60s': 30_000.0, 'ofi_10s': 0.4}, sol)['buy_ok']   # $300k x 0.1


async def test_case_1534_sell_is_blocked_100_percent(tmp_path):
    """CVD 3m -2.36M, CVD 60 s +0.96M, OFI > 0: the regime is DRIFT and the flow says BUY -> no SELL, ever."""
    feed = case_1534_feed()
    x = feed.features()
    assert x['cvd_3m'] == pytest.approx(-2_361_000, rel=0.01) and x['cvd_60s'] == pytest.approx(959_000, rel=0.01)
    assert x['ofi_10s'] >= 0.35 and x['cvd_15s'] > 0
    e, venue, _ = mock_engine(tmp_path, feed=feed, concordance_gate=True)
    await e.sync()
    await e.tick()
    assert e.reg['name'] == 'DRIFT' and e.flow['state'] == 'BUY'
    ok, why = e.churn_gate(SELL_DEC, DIAG)
    assert not ok and why.startswith('DRIFT LOCK')
    e.p = replace(e.p, drift_lock=False)                                       # even without the DRIFT lock
    ok, why = e.churn_gate(SELL_DEC, DIAG)
    assert not ok and why.startswith('FLOW CONFLICT SELL (LOCKED)')
    assert not any(f['side'] == 'SELL' for f in venue.fills) and e.pos is None


async def test_concordant_buy_passes_and_each_missing_leg_blocks(tmp_path):
    e, _, _ = mock_engine(tmp_path, concordance_gate=True)
    e.reg = {'name': 'UP'}
    with_flow(e, cvd_15s=100_000.0, cvd_60s=900_000.0, ofi_10s=0.6, px_vel_15s=0.1)
    assert e.churn_gate(BUY_DEC, DIAG)[0]
    for leg in ({'cvd_15s': -1.0}, {'cvd_60s': 200_000.0}, {'ofi_10s': 0.2}, {'ofi_10s': None}):
        with_flow(e, **{'cvd_15s': 100_000.0, 'cvd_60s': 900_000.0, 'ofi_10s': 0.6, **leg})
        ok, why = e.churn_gate(BUY_DEC, DIAG)
        assert not ok and why.startswith('FLOW CONFLICT BUY'), leg


async def test_mr_lane_keeps_its_own_rules_unless_asked(tmp_path):
    from tests.test_multi_asset_scanner import mr_feed
    e, _, feed = mock_engine(tmp_path, feed=mr_feed(), ker_gate=True, concordance_gate=True)
    await e.sync()
    e.features = feed.features()
    e.reg = msig.regime(e.features)
    e.sig = msig.signals(e.features, reg=e.reg)
    e.flow = msig.concordance(e.features)
    d = ms.evaluate(e.sig, ms.Gates(cost_bps=4))[0]
    assert e.lane(d) == 'MR' and e.reg['name'] == 'NEUTRAL' and e.churn_gate(d, DIAG)[0]
    e.p = replace(e.p, concordance_mr=True)
    assert e.churn_gate(d, DIAG)[1].startswith('FLOW CONFLICT BUY')            # MR buys against the last minute


# ============================================================================= 2. DRIFT lock
@pytest.mark.parametrize('cvd60,cvd3m,label', [(959_000.0, -2_360_000.0, 'CONFLICT'), (250_000.0, 500_000.0, 'UNCONFIRMED')])
async def test_drift_locks_every_auto_entry(tmp_path, cvd60, cvd3m, label):
    e, _, _ = mock_engine(tmp_path)
    e.reg = msig.regime({'cvd_60s': cvd60, 'cvd_3m': cvd3m})
    assert e.reg['name'] == 'DRIFT' and label in e.reg['label']
    for dec in (BUY_DEC, SELL_DEC):
        ok, why = e.churn_gate(dec, DIAG)
        assert not ok and why.startswith('DRIFT LOCK')
    e.p = replace(e.p, drift_lock=False)
    assert e.churn_gate(BUY_DEC, DIAG)[0]


async def test_drift_tick_never_trades_and_radar_says_so(tmp_path):
    e, venue, feed = mock_engine(tmp_path, feed=case_1534_feed())
    await e.sync()
    d = await e.tick()
    assert not d.allowed and e.pos is None and not venue.fills
    assert e.radar()['label'] == 'DRIFT LOCK' and e.radar()['flow'] == 'BUY'


# ============================================================================= 3. slot routing
async def test_btc_drift_sol_clean_trend_sol_gets_the_slot(tmp_path):
    feeds = {'BTCUSDT': case_1534_feed(), SOL: sol_feed()}
    es, venue, _ = await scanner(tmp_path, feeds)
    btc, sol = es['BTCUSDT'], es[SOL]
    d = await btc.tick()
    assert not d.allowed and btc.reg['name'] == 'DRIFT' and btc.radar()['label'] == 'DRIFT LOCK' and btc.candidate is None
    d = await fill_sol(sol, feeds[SOL], sol.tick())
    assert d.allowed and sol.pos and sol.flow['state'] == 'BUY' and all(f['symbol'] == SOL for f in venue.fills)


async def test_better_candidate_takes_the_slot_first(tmp_path):
    es, _, _ = await scanner(tmp_path)
    btc, sol, doge = es['BTCUSDT'], es[SOL], es['DOGEUSDT']
    now = btc.clock()
    sol.candidate = (now, 0.95)
    assert btc.better_peer(0.85)[0] is sol
    assert btc.better_peer(0.99) is None
    sol.candidate = (now - 2.0, 0.95)                                         # stale (> 1 s): ignored
    assert btc.better_peer(0.85) is None
    sol.candidate, doge.candidate = (now, 0.90), (now, 0.90)
    assert doge.better_peer(0.90)[0] is sol                                   # tie -> SCAN_SYMBOLS order
    assert sol.better_peer(0.90) is None
    sol.pos = {'side': 'BUY'}
    assert btc.better_peer(0.85)[0] is doge                                   # a peer in a position is not a rival
    sol.pos = None
    btc.p = replace(btc.p, slot_yield=False)
    assert btc.better_peer(0.10) is None


async def test_tick_yields_to_a_better_peer(tmp_path):
    from tests.test_two_mode_engine import bullish_feed
    feeds = {'BTCUSDT': bullish_feed(), SOL: sol_feed()}
    es, venue, _ = await scanner(tmp_path, feeds, ker_gate=False, min_edge_bps=0.0, concordance_gate=False)
    btc, sol = es['BTCUSDT'], es[SOL]
    sol.candidate = (sol.clock(), 1.5)
    d = await btc.tick()
    assert not d.allowed and d.reason.startswith('SCANNER YIELD -> SOLUSDT') and not venue.fills


def test_quality_rewards_regime_and_flow(tmp_path):
    e, _, _ = mock_engine(tmp_path)
    e.reg, e.flow = {'name': 'UP'}, {'buy_ok': True, 'sell_ok': False}
    base = e.conviction(BUY_DEC, DIAG)
    assert e.quality(BUY_DEC, DIAG) == pytest.approx(base + 0.10)
    e.reg, e.flow = {'name': 'DRIFT'}, {'buy_ok': False, 'sell_ok': False}
    assert e.quality(BUY_DEC, DIAG) == pytest.approx(base)


# ============================================================================= exit side (opt-in)
async def test_hurdle_holds_a_regime_exit_unless_override(tmp_path):
    """15:26/15:31 log: the REGIME exit fired at +0.029 % / +0.011 % and the fee hurdle held it."""
    e, venue, feed = await opened_short(tmp_path, flow_exit=False, fee_hurdle=True)
    e.pos['opened'] -= 100
    cvd0 = feed.features()['cvd_60s']
    dump(feed, e.pos['entry'], 1_330_000 - cvd0, sell=False)
    await e.tick()
    e.pos['flip_since'] -= 21
    e.flow = {'state': 'BUY', 'buy_ok': True, 'sell_ok': False}
    await e.tick()
    e.flow = {'state': 'BUY', 'buy_ok': True, 'sell_ok': False}
    await e.tick()
    assert not e.pos.get('exiting') and e.pos['hurdle'].startswith('FEE HURDLE')
    e.p = replace(e.p, hurdle_concordance_override=True)
    e.flow = {'state': 'BUY', 'buy_ok': True, 'sell_ok': False}
    await e.manage()
    assert e.pos['exiting']['reason'] == 'REGIME'


# ============================================================================= UI / defaults
def test_flow_chip_and_snapshot(tmp_path):
    e, _, _ = mock_engine(tmp_path)
    snap = e.snapshot()
    assert {'state', 'buy_ok', 'sell_ok', 'why', 'gate', 'drift_lock', 'drift'} <= set(snap['flow'])
    json.dumps(snap, default=str)
    html = (ROOT / 'web_ui' / 'index.html').read_text(encoding='utf-8')
    js = (ROOT / 'web_ui' / 'desk.js').read_text(encoding='utf-8')
    assert 'id="flow-chip"' in html and "$('flow-chip')" in js
    assert 'FLOW: CONCORDANT' in js and 'FLOW: CONFLICT (LOCKED)' in js and 'DRIFT LOCK' in js


def test_concordance_defaults():
    c, p = msig.SignalConfig(), eng.PROFILES['demo']
    assert (c.conc_cvd60_usd, c.conc_ofi) == (300_000.0, 0.35)
    assert p.concordance_gate and p.drift_lock and p.slot_yield
    assert not p.concordance_mr and not p.hurdle_concordance_override


async def test_concordant_sell_passes_in_a_downtrend(tmp_path):
    e, _, _ = mock_engine(tmp_path, concordance_gate=True)
    e.reg = {'name': 'DOWN'}
    with_flow(e, cvd_15s=-80_000.0, cvd_60s=-700_000.0, ofi_10s=-0.5, px_vel_15s=-0.1)
    assert e.churn_gate(SELL_DEC, DIAG)[0]
    assert e.churn_gate(BUY_DEC, DIAG)[1].startswith('FLOW CONFLICT BUY')


async def test_override_needs_all_three_flows_against(tmp_path):
    e, venue, feed = await opened_short(tmp_path, flow_exit=False, fee_hurdle=True, hurdle_concordance_override=True)
    e.pos['opened'] -= 100
    cvd0 = feed.features()['cvd_60s']
    dump(feed, e.pos['entry'], 1_330_000 - cvd0, sell=False)
    await e.tick()
    e.pos['flip_since'] -= 21
    e.flow = {'state': 'CONFLICT', 'buy_ok': False, 'sell_ok': False}         # regime flipped, flows mixed
    await e.manage()
    assert not e.pos.get('exiting') and e.pos['hurdle'].startswith('FEE HURDLE')


async def test_scanner_radar_shows_drift_and_clean_trend(tmp_path):
    feeds = {'BTCUSDT': case_1534_feed(), SOL: sol_feed()}
    es, venue, _ = await scanner(tmp_path, feeds, gtx_ttl_s=0.2)
    for e in es.values():
        await e.tick()
    rows = {r['symbol']: r for r in (e.radar() for e in es.values())}
    assert rows['BTCUSDT']['label'] == 'DRIFT LOCK' and rows['BTCUSDT']['flow'] == 'BUY'
    assert rows[SOL]['regime'] == 'UP' and rows[SOL]['flow'] == 'BUY'
