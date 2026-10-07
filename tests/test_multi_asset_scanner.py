"""Multi-asset scanner (2026-10-07j): BTC/SOL/DOGE engines share one slot + governor; TREND lane (KER >= 0.42)
and MR lane (KER < 0.38); velocity 0.025 %; macro one-side lock only when EMA-20(5 m) moves >= 0.05 %."""
import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from core import engine as eng
from core import micro_signals as msig
from core import micro_swarm as ms
from tests.test_chop_filter import chop_feed
from tests.test_two_mode_engine import agg, book_at, depth, dump, fill_through, mock_engine
from tests.test_winrate_maximizer import flow_feed

ROOT = Path(__file__).resolve().parents[1]
SOL = 'SOLUSDT'


def sol_feed(now=None, px=150.0, burst_usd=90_000.0):
    """SOL: 2 min straight climb (+0.3 %), then $90k of buying lifts it +0.04 % (SOL thresholds are BTC x 0.1)."""
    now = time.time() if now is None else now
    f = msig.MicroFeed(SOL, msig.config_for(SOL))
    for i in range(110):
        f.on_message(agg(round(px - 0.45 + i * 0.45 / 110, 2), 1.0, maker=i % 2 == 0, sym=SOL), now - 120 + i)
    top = round(px + 0.06, 2)
    for i in range(30):
        f.on_message(agg(top, burst_usd / top / 30, maker=False, sym=SOL), now - 10 + i * 0.3)
    for i in range(10):                                                     # book lifted level by level: OFI > 0
        b = round(top - 0.01 * (9 - i), 2)
        f.on_message(depth(b, round(b + 0.01, 2), bq=900.0, aq=100.0, sym=SOL), now)
    return f


def mr_feed(now=None):
    """BTC sideway 84,000 <-> 84,200 every 10 s (KER ~0.05), last 10 s dip to 83,950 at the range low,
    bids stacked 6:1 - price 0.13 % under VWAP 60 s, box 0.30 %."""
    now = time.time() if now is None else now
    f = msig.MicroFeed('BTCUSDT')
    for i in range(110):
        f.on_message(agg(84_000.0 if (i // 10) % 2 == 0 else 84_200.0, 0.01, maker=i % 2 == 0), now - 120 + i)
    for i in range(10):
        f.on_message(agg(83_950.0, 0.01, maker=True), now - 9 + i)
    for _ in range(10):
        f.on_message(depth(83_950.0, 83_950.1, bq=6.0, aq=1.0), now)
    return f


async def scanner(tmp_path, feeds=None, **prof):
    feeds = feeds or {'BTCUSDT': chop_feed(), SOL: sol_feed(), 'DOGEUSDT': msig.MicroFeed('DOGEUSDT',
                                                                                           msig.config_for('DOGEUSDT'))}
    prof.setdefault('gtx_ttl_s', 0.6)
    prof.setdefault('alt_boost', False)           # SOL/DOGE priority size: test_sol_priority_chop_ejection.py
    prof.setdefault('eject', False)               # 180 s time-decay ejection: test_sol_priority_chop_ejection.py
    prof.setdefault('vfr_gate', False)            # VFR (ATR 15 m / fees) freeze: test_world_quant_economic_engine.py
    profiles = {s: replace(eng.PROFILES['demo'], symbol=s, qty=eng.SYMBOL_QTY.get(s, 0.0), cooldown_s=0.0, **prof)
                for s in feeds}
    venue = eng.MockVenue(profiles['BTCUSDT'], quote=lambda s: eng._touch(feeds.get(s)))
    es = eng.build_scanner(profiles, venue, feeds, ledger=tmp_path / 'trades.jsonl')
    for s, e in es.items():
        e.spec, e.started, e.phase = dict(eng.FALLBACK_SPECS[s]), True, 'SCANNING'
        await e.sync()
    return es, venue, feeds


async def fill_sol(e, feed, coro):
    task = asyncio.ensure_future(coro)
    await asyncio.sleep(0.15)
    bid = feed.bid
    for _ in range(3):
        feed.on_message(depth(round(bid - 0.01, 2), bid, sym=SOL))           # sellers trade through our bid
    res = await task
    feed.on_message(depth(bid, round(bid + 0.01, 2), sym=SOL))
    return res


# ============================================================================= 1. scanner
async def test_btc_sideway_sol_breakout_scanner_trades_sol(tmp_path):
    """Full default gates: BTC is CHOP-locked, SOL breaks out -> the SOL engine takes the single slot."""
    es, venue, feeds = await scanner(tmp_path)
    btc, sol, doge = es['BTCUSDT'], es[SOL], es['DOGEUSDT']
    d = await btc.tick()
    assert not d.allowed and d.reason.startswith('CHOP LOCKED') and btc.radar()['label'] in ('CHOP', 'MR SCAN')
    d = await fill_sol(sol, feeds[SOL], sol.tick())
    assert d.allowed and sol.pos and sol.pos['symbol'] == SOL and sol.pos['entry_type'] == 'GTX'
    assert all(f['symbol'] == SOL and f['maker'] for f in venue.fills)
    assert sol.radar()['label'] == 'LONG OPEN' and doge.radar()['label'] == 'WARMUP'
    fee_rt = sol.pos['qty'] * sol.pos['entry'] * 4e-4                       # equal-risk size (2026-10-07n)
    assert fee_rt < 0.02 and sol.risk_at_stop() == pytest.approx(0.15, rel=0.05)


async def test_one_slot_blocks_every_other_symbol(tmp_path):                 # legacy single-slot mode
    from tests.test_two_mode_engine import bullish_feed
    feeds = {'BTCUSDT': bullish_feed(), SOL: sol_feed()}
    es, venue, _ = await scanner(tmp_path, feeds, ker_gate=False, min_edge_bps=0.0, multi_slot=False)
    sol, btc = es[SOL], es['BTCUSDT']
    await fill_sol(sol, feeds[SOL], sol.tick())
    assert sol.pos and btc.peer() is sol
    d = await btc.tick()
    assert not d.allowed and 'SCANNER slot: SOLUSDT' in btc.gates(btc.features).rune[1]
    r = await btc.force_test_trade('BUY')
    assert not r['ok'] and not any(f['symbol'] == 'BTCUSDT' for f in venue.fills)


async def test_governor_cooldown_and_ledger_are_shared(tmp_path):
    es, venue, feeds = await scanner(tmp_path)
    sol, btc = es[SOL], es['BTCUSDT']
    assert btc.risk is sol.risk and btc.trade_times is sol.trade_times and btc.history is sol.history
    await fill_sol(sol, feeds[SOL], sol.tick())
    assert len(btc.trade_times) == 1
    sol.p = replace(sol.p, cooldown_s=30.0)
    for _ in range(3):
        feeds[SOL].on_message(depth(round(sol.pos['sl0'] - 0.05, 2), round(sol.pos['sl0'] - 0.04, 2), sym=SOL))
    await sol.sync()
    assert sol.pos is None and btc.history[0]['symbol'] == SOL and btc.all_rows[-1]['symbol'] == SOL
    assert 'cooldown' in btc.gates(btc.feed.features()).rune[1]
    assert any(ev['message'].startswith('SOL · ') for ev in sol.events)


async def test_fee_hurdle_still_holds_on_the_scanned_symbol(tmp_path):
    es, venue, feeds = await scanner(tmp_path, regime_flip_exit=False)
    sol = es[SOL]
    await fill_sol(sol, feeds[SOL], sol.tick())
    await sol.sync()
    sol.pos['opened'] -= 100
    feed = feeds[SOL]
    for i in range(50):
        feed.on_message(agg(sol.pos['entry'], 4_000_000 * 0.1 / sol.pos['entry'] / 50, maker=True, sym=SOL))
    await sol.tick()
    assert not sol.pos.get('exiting') and sol.pos['hurdle'].startswith('FEE HURDLE')


# ============================================================================= 2. runner plumbing
def test_combined_streams_and_demux():
    urls = msig.streams_multi(eng.SCAN_SYMBOLS)
    assert urls['public'].count('@depth10@100ms') == 3 and 'solusdt@depth10' in urls['public']
    assert all(f'{s.lower()}@aggTrade' in urls['market'] for s in eng.SCAN_SYMBOLS)
    assert msig.streams_multi(['BTCUSDT']) == msig.streams('BTCUSDT')
    feeds = {s: msig.MicroFeed(s, msig.config_for(s)) for s in eng.SCAN_SYMBOLS}
    assert eng.Runner.demux(feeds, agg(150.0, 2.0, sym=SOL)) and feeds[SOL].n_trades == 1
    assert feeds['BTCUSDT'].n_trades == 0 and not eng.Runner.demux(feeds, agg(1.0, 1.0, sym='ETHUSDT'))


async def test_runner_builds_three_engines_on_one_venue_and_focus_follows_the_slot():
    r = eng.Runner('demo', allow_live=False, env={}, serve=False)
    r.engines = await r.build_multi('demo')
    assert list(r.engines) == list(eng.SCAN_SYMBOLS)
    venues = {id(e.venue) for e in r.engines.values()}
    risks = {id(e.risk) for e in r.engines.values()}
    assert len(venues) == 1 and len(risks) == 1
    assert r.engines['BTCUSDT'].p.qty == 0.002 and r.engines[SOL].p.qty == 0.0
    r.focus_sym = 'BTCUSDT'
    assert r.refocus() is r.engines['BTCUSDT']
    assert (await r.switch_symbol('DOGEUSDT'))['ok'] and r.engine is r.engines['DOGEUSDT']
    r.engines[SOL].pos = {'side': 'BUY'}
    assert r.refocus() is r.engines[SOL]                                     # the slot owner takes the screen
    r.engines[SOL].pos = None


def test_live_stays_single_symbol():
    import inspect
    src = inspect.getsource(eng.Runner.activate)
    assert "self.scan and mode == 'demo'" in src
    assert eng.PROFILES['live'].symbol == 'DOGEUSDT' and eng.PROFILES['live'].max_loss_usdt == 0.05


# ============================================================================= 3. dual mode
def test_mr_signal_rules():
    base = {'ker': 0.20, 'vwap_dev_pct': -0.15, 'range_pos': 0.05, 'range_lo': 100.0, 'range_hi': 100.30,
            'depth10_imb': 0.40, 'obi': 0.0, 'cvd_60s': 0.0, 'cvd_3m': 0.0, 'last': 99.9}
    s = msig.signals(base)
    assert s['MR']['side'] == 'BUY' and s['DEPTH']['side'] == 'BUY'
    for bad in ({'ker': 0.40}, {'ker': None}, {'vwap_dev_pct': -0.10}, {'range_hi': 100.60}, {'range_pos': 0.5}):
        assert msig.signals({**base, **bad})['MR'] is None, bad
    assert msig.signals({**base, 'depth10_imb': -0.40})['DEPTH'] is None           # book leans against
    up = msig.signals({**base, 'vwap_dev_pct': 0.15, 'range_pos': 0.95, 'depth10_imb': -0.4})
    assert up['MR']['side'] == 'SELL' and up['DEPTH']['side'] == 'SELL'
    d, _, diag = ms.evaluate(s, ms.Gates(cost_bps=4))
    assert d.allowed and d.side == 'BUY' and diag['confluence']['C2_VWAP'] and diag['confluence']['C6_WALL']
    alone, _, _ = ms.evaluate(msig.signals({**base, 'depth10_imb': 0.0}), ms.Gates(cost_bps=4), jev_min=None)
    assert not alone.allowed                                                   # MR without the L10 vote = 60 %


async def fill_deep(e, feed, coro, low):
    """Sellers sweep down to `low`: every resting bid above it trades (the MR quote rests under the touch)."""
    task = asyncio.ensure_future(coro)
    await asyncio.sleep(0.15)
    bid = feed.bid
    for _ in range(3):
        feed.on_message(depth(round(low - 0.1, 1), low))
    res = await task
    feed.on_message(depth(bid, round(bid + 0.1, 1)))
    return res


async def test_mr_lane_trades_the_sideway_with_tp015_sl010(tmp_path):
    """MR (2026-10-07k): Avellaneda-Stoikov quote <= 2 bps under mid, TP = VWAP distance in [0.12, 0.18] %, SL 0.08 %."""
    e, venue, feed = mock_engine(tmp_path, feed=mr_feed(), ker_gate=True, min_edge_bps=30.0, min_velocity_pct=0.025)
    await e.sync()
    d = await fill_deep(e, feed, e.tick(), 83_920.0)
    assert e.chop_locked and e.features['ker'] < 0.38 and e.lane(d) == 'MR'
    assert d.allowed and e.pos and e.pos['path'] == 'MR' and e.pos['entry_type'] == 'GTX'
    entry, mid = e.pos['entry'], (83_950.0 + 83_950.1) / 2
    assert entry == pytest.approx(mid * (1 - 2e-4), abs=0.11) and entry < 83_950.0       # A-S quote, capped 2 bps
    dev = 0.13005
    assert e.pos['tp'] == pytest.approx(entry * (1 + dev / 100), abs=0.2) and e.pos['sl'] == pytest.approx(entry * 0.9992, abs=0.2)
    assert e.pos['bracket_src'] == 'MR' and not e.pos['split'] and e.radar()['label'] == 'LONG OPEN'
    book_at(feed, round(e.pos['tp'] + 0.1, 1))                                 # back toward VWAP: TP fills
    await e.sync()
    h = e.history[0]
    net = h['qty'] * entry * (dev / 100 - 0.0004)
    assert h['pnl'] == pytest.approx(net, abs=0.003) and h['taker_fills'] == 0


async def test_dead_band_038_042_trades_neither_lane(tmp_path):
    e, venue, feed = mock_engine(tmp_path, feed=mr_feed(), ker_gate=True)
    await e.sync()
    e.features = feed.features()
    e.chop_locked = msig.chop_locked(0.40, True)
    assert e.chop_locked and msig.signals({**e.features, 'ker': 0.40})['MR'] is None


async def test_mr_needs_the_mr_score(tmp_path):
    e, _, feed = mock_engine(tmp_path, feed=mr_feed(), ker_gate=True)
    await e.sync()
    e.features = feed.features()
    e.sig = msig.signals(e.features)
    d = ms.evaluate(e.sig, ms.Gates(cost_bps=4))[0]
    ok, _ = e.churn_gate(d, {'consensus': 0.8, 'jev': 0.0, 'gates_ok': True, 'edge_bps': 13})
    assert ok
    ok, why = e.churn_gate(replace(d, score=0.6), {'consensus': 0.6, 'jev': 0.0, 'gates_ok': True, 'edge_bps': 13})
    assert not ok and why.startswith('MR score 60.0%')


# ============================================================================= 4. velocity / macro calibration
async def test_velocity_0025_lets_a_0025_push_through(tmp_path):
    e, venue, feed = mock_engine(tmp_path, feed=flow_feed(lead=25.0), min_velocity_pct=0.025)
    await e.sync()
    d = await fill_through(e, feed, e.tick())
    assert e.features['px_vel_15s'] == pytest.approx(0.02506, abs=1e-4) and d.allowed and e.pos
    e2, _, _ = mock_engine(tmp_path, feed=flow_feed(lead=20.0), min_velocity_pct=0.025)
    await e2.sync()
    d2 = await e2.tick()
    assert not d2.allowed and d2.reason.startswith('ABSORPTION TRAP BUY')     # 0.020 % under $900k


def test_macro_locks_one_side_only_on_a_steep_ema():
    M = msig.macro_bias
    f = {'price': 84_100.0, 'vwap_5m': 84_000.0, 'vwap_5m_span': 290.0, 'ema5_20': 84_000.0, 'ema5_n': 30}
    assert M({**f, 'ema5_slope': 84_000 * 0.0004})['name'] == 'MIXED'       # +0.04 %/5 m: both sides open
    assert M({**f, 'ema5_slope': 84_000 * 0.0006})['name'] == 'LONG ONLY'
    assert M({**f, 'price': 83_900.0, 'ema5_slope': -84_000 * 0.0006})['name'] == 'SHORT ONLY'


def test_scanner_defaults():
    c, p = msig.SignalConfig(), eng.PROFILES['demo']
    assert (c.ker_lock, c.ker_unlock, c.mr_ker_max, c.mr_dev_pct, c.absorb_min_move_pct) == (0.38, 0.42, 0.38, 0.12, 0.025)
    assert (p.min_velocity_pct, p.mr_tp_min, p.mr_tp_max, p.mr_sl_pct, p.mr_min_score) == (0.025, 0.12, 0.18, 0.08, 0.80)
    assert eng.SCAN_SYMBOLS == ('BTCUSDT', 'SOLUSDT', 'DOGEUSDT') and ms.ALPHA['NORO'][-1] == 'MR'


# ============================================================================= 5. UI
def test_desk_ships_the_multi_asset_radar(tmp_path):
    html = (ROOT / 'web_ui' / 'index.html').read_text(encoding='utf-8')
    js = (ROOT / 'web_ui' / 'desk.js').read_text(encoding='utf-8')
    assert 'id="scan-radar"' in html and "$('scan-radar')" in js and 'radar-chip' in js
    for sym in ('BTCUSDT', 'DOGEUSDT', 'SOLUSDT'):
        assert f'data-symbol="{sym}"' in html
    e, _, _ = mock_engine(tmp_path)
    json.dumps(e.radar())
