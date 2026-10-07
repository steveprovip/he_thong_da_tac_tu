"""Portfolio (2026-10-07m): one position per symbol (max 3, risk at all stops <= 1 U, margin <= 20 %), convex
size on very strong concordant setups, exchange-stop ratchet at MFE +0.15 %, re-adoption of orphaned positions."""
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from core import engine as eng
from core import micro_signals as msig
from core import micro_swarm as ms
from tests.test_multi_asset_scanner import SOL, fill_sol, scanner, sol_feed
from tests.test_two_mode_engine import (SYM, agg, book_at, bullish_feed, depth, fill_through, mock_engine, opened,
                                        opened_short, sig)
from tests.test_ultimate_quant import ofi_frames

ROOT = Path(__file__).resolve().parents[1]
RELAX = dict(ker_gate=False, min_edge_bps=0.0, concordance_gate=False, min_velocity_pct=0.0, fee_hurdle=False)
DEC95 = ms.evaluate(sig(FLOW=('BUY', 1.0, 20), TREND=('BUY', 1.0, 20)), ms.Gates(cost_bps=4))[0]


async def btc_and_sol(tmp_path, **prof):
    feeds = {'BTCUSDT': bullish_feed(), SOL: sol_feed()}
    es, venue, _ = await scanner(tmp_path, feeds, **{**RELAX, **prof})
    return es, venue, feeds


# ============================================================================= 1. parallel slots
async def test_btc_in_position_sol_signal_opens_a_second_position(tmp_path):
    es, venue, feeds = await btc_and_sol(tmp_path)
    btc, sol = es['BTCUSDT'], es[SOL]
    await fill_through(btc, feeds['BTCUSDT'], btc.tick())
    await btc.sync()
    assert btc.pos and btc.slot_block() is None and sol.slot_block() is None
    d = await fill_sol(sol, feeds[SOL], sol.tick())
    await sol.sync()
    assert d.allowed and sol.pos and btc.pos                                  # two positions at once
    assert {f['symbol'] for f in venue.fills} == {'BTCUSDT', SOL}
    pv = btc.portfolio_view()
    assert pv['count'] == 2 and pv['max'] == 3 and 0 < pv['risk'] < 1.0


async def test_max_positions_cap(tmp_path):
    es, venue, feeds = await btc_and_sol(tmp_path, max_positions=1)
    btc, sol = es['BTCUSDT'], es[SOL]
    await fill_through(btc, feeds['BTCUSDT'], btc.tick())
    await btc.sync()
    assert sol.slot_block() == 'PORTFOLIO FULL 1/1'
    d = await sol.tick()
    assert not d.allowed and not sol.pos and 'PORTFOLIO FULL' in sol.gates(sol.features).rune[1]


async def test_portfolio_risk_cap_blocks_and_counts_the_new_trade(tmp_path):
    es, venue, feeds = await btc_and_sol(tmp_path)
    btc, sol = es['BTCUSDT'], es[SOL]
    await fill_through(btc, feeds['BTCUSDT'], btc.tick())
    await btc.sync()
    r = btc.risk_at_stop()                                                    # 0.002 BTC x (0.20 % + fees)
    assert r == pytest.approx(0.002 * 99_800 * 0.0028, rel=0.02)
    sol.p = replace(sol.p, portfolio_risk_usdt=r)
    assert sol.slot_block().startswith('PORTFOLIO RISK')
    sol.p = replace(sol.p, portfolio_risk_usdt=r + 0.005)                    # room for BTC, not for BTC + SOL
    pl = eng.plan('BUY', 150.0, sol.spec, sol.p, 1_000.0)
    assert sol.slot_block() is None and sol.portfolio_check(pl).startswith('PORTFOLIO RISK')


def test_portfolio_margin_cap(tmp_path):
    e, _, _ = mock_engine(tmp_path)
    e.account = {'wallet': 100.0, 'available': 100.0}
    pl = eng.plan('BUY', 84_000.0, e.spec, e.p, 100.0)
    assert pl['margin'] > 20.0 and e.portfolio_check(pl).startswith('PORTFOLIO MARGIN')
    e.account = {'wallet': 5_000.0, 'available': 5_000.0}
    assert e.portfolio_check(pl) is None


def test_risk_at_stop_math(tmp_path):
    e, _, _ = mock_engine(tmp_path)
    e.pos = {'side': 'SELL', 'qty': 0.002, 'entry': 84_000.0, 'sl': 84_126.0, 'entry_fee_bps': 2.0}
    assert e.risk_at_stop() == pytest.approx(0.002 * 126 + 0.002 * 84_000 * 8 / 1e4)
    e.pos['sl'] = 83_932.8                                                    # ratchet: stop in profit
    assert e.risk_at_stop() == pytest.approx(0.0, abs=1e-9)                  # +0.08 % stop = the fees
    e.pos = None
    e.busy, e.pending_risk = True, 0.25                                      # entry being worked counts
    assert e.risk_at_stop() == 0.25


async def test_single_slot_mode_still_available(tmp_path):
    es, venue, feeds = await btc_and_sol(tmp_path, multi_slot=False)
    btc, sol = es['BTCUSDT'], es[SOL]
    await fill_through(btc, feeds['BTCUSDT'], btc.tick())
    await btc.sync()
    assert sol.slot_block().startswith('SCANNER slot: BTCUSDT')


# ============================================================================= 2. convex sizing
@pytest.mark.parametrize('score,ofi,cvd,mult', [
    (0.95, 0.70, 1_200_000.0, 1.5), (0.99, 0.70, 1_200_000.0, 2.0), (0.85, 0.90, 5e6, 1.0),
    (0.95, 0.50, 1_200_000.0, 1.0), (0.95, 0.70, 900_000.0, 1.0)])
def test_convex_size_multiplier(tmp_path, score, ofi, cvd, mult):
    e, _, _ = mock_engine(tmp_path, convex_sizing=True)   # OFF by default since 10-07q
    d = replace(DEC95, score=score)
    assert e.size_mult(d, {'consensus': score, 'jev': 9.9}, {'ofi_10s': ofi, 'cvd_60s': cvd}) == mult


def test_convex_is_directional_scaled_and_off_live(tmp_path):
    e, _, _ = mock_engine(tmp_path, convex_sizing=True)
    sell = replace(ms.evaluate(sig(FLOW=('SELL', 1.0, 20), TREND=('SELL', 1.0, 20)), ms.Gates())[0], score=0.95)
    assert e.size_mult(sell, {}, {'ofi_10s': -0.7, 'cvd_60s': -1.2e6}) == 1.5
    assert e.size_mult(sell, {}, {'ofi_10s': 0.7, 'cvd_60s': 1.2e6}) == 1.0
    e.feed.cfg = msig.config_for('SOLUSDT')
    assert e.size_mult(replace(DEC95, score=0.95), {}, {'ofi_10s': 0.7, 'cvd_60s': 120_000.0}) == 1.5
    low_vote = replace(DEC95, score=0.80)
    assert e.size_mult(low_vote, {'consensus': 0.80, 'jev': 9.9}, {'ofi_10s': 0.9, 'cvd_60s': 5e6}) == 1.0  # no JEV lift
    assert not eng.PROFILES['live'].convex_sizing


async def test_strong_concordant_setup_opens_1_5x(tmp_path):
    feed = bullish_feed()
    now = time.time()
    for i in range(10):
        feed.on_message(agg(99_800.0, 300_000 / 99_800.0 / 10, maker=False), now - 3 + i * 0.2)
    ofi_frames(feed)
    x = feed.features()
    assert x['ofi_10s'] >= 0.65 and x['cvd_60s'] >= 1_000_000
    e, venue, _ = mock_engine(tmp_path, feed=feed, convex_sizing=True)
    await e.sync()
    d = await fill_through(e, feed, e.tick())
    assert d.allowed and e.diag['consensus'] >= 0.90 and e.pos['size_mult'] == 1.5
    assert e.pos['qty'] == pytest.approx(0.003)                             # 0.002 BTC x 1.5


# ============================================================================= 3. ratchet
async def test_mfe_018_ratchets_the_exchange_stop_to_entry_plus_008(tmp_path):
    e, venue, feed = await opened(tmp_path)
    entry, sl0, cid0 = e.pos['entry'], e.pos['sl'], e.pos['sl_cid']
    book_at(feed, round(entry * 1.0018, 1))                                  # MFE +0.18 %
    await e.tick()
    p = e.pos
    assert p['ratchet'] and p['sl'] == pytest.approx(entry * 1.0008, abs=0.2) and p['sl'] > sl0
    assert p['sl_cid'] != cid0 and venue.orders[cid0]['status'] == 'CANCELED'
    assert venue.orders[p['sl_cid']]['price'] == pytest.approx(p['sl'])
    assert any('RATCHET' in ev['message'] for ev in e.events)
    assert e.risk_at_stop() <= p['qty'] * entry * 8e-4 + 1e-9                 # only fees left at risk


async def test_ratchet_stop_fills_without_marin(tmp_path):
    """Feed dies / process restarts: the exchange stop alone still exits at entry + 0.08 %."""
    e, venue, feed = await opened(tmp_path, soft_exits=True)
    entry = e.pos['entry']
    book_at(feed, round(entry * 1.0018, 1))
    await e.tick()
    lvl = e.pos['sl']
    book_at(feed, round(lvl, 1))                                             # drop straight onto the stop
    await e.sync()
    h = e.history[0]
    assert h['reason'] == 'RATCHET' and h['gross'] > 0 and h['ratchet']
    assert h['pnl'] == pytest.approx(h['qty'] * entry * (0.0008 - 0.0002 - 0.0005 - 0.0001), abs=0.004)


async def test_short_ratchet_and_no_early_trigger(tmp_path):
    e, venue, feed = await opened_short(tmp_path)
    entry = e.pos['entry']
    book_at(feed, round(entry * 0.9988, 1))                                  # +0.12 % for the short: not yet
    await e.tick()
    assert not e.pos.get('ratchet')
    book_at(feed, round(entry * 0.9983, 1))                                  # +0.17 %
    await e.tick()
    assert e.pos['ratchet'] and e.pos['sl'] == pytest.approx(entry * 0.9992, abs=0.2)
    sl = e.pos['sl']
    book_at(feed, round(entry * 0.9970, 1))
    await e.tick()
    assert e.pos['sl'] == sl                                                 # one ratchet, soft tiers do the rest


# ============================================================================= orphan adoption
async def test_orphaned_position_is_re_adopted_with_its_exchange_stop(tmp_path):
    """17:21 screen: SHORT 0.001 @ 83,979.2 left on the venue by a restart - MARIN 'EXTERNAL', nobody managing."""
    e, venue, feed = mock_engine(tmp_path)
    await venue.market(SYM, 'SELL', '0.001', 'manual')
    await venue.stop(SYM, 'BUY', '100100.0', 'gs-orphan')
    await e.sync()
    p = e.pos
    assert p and p['tag'] == 'ADOPTED' and p['side'] == 'SELL' and p['qty'] == pytest.approx(0.001)
    assert p['sl'] == 100_100.0 and p['sl_cid'] == 'gs-orphan'
    assert any('MARIN ADOPT SHORT' in ev['message'] for ev in e.events)
    assert 'position open' in e.gates(feed.features()).rune[1]
    book_at(feed, 100_100.0)                                                # the old stop still works
    await e.sync()
    assert e.pos is None and e.history[0]['tag'] == 'ADOPTED'


async def test_adoption_places_a_stop_when_none_is_left(tmp_path):
    e, venue, feed = mock_engine(tmp_path)
    await venue.market(SYM, 'BUY', '0.002', 'manual')
    await e.sync()
    p = e.pos
    assert p['tag'] == 'ADOPTED' and p['sl'] < p['entry']
    assert venue.orders[p['sl_cid']]['price'] == pytest.approx(p['sl'])
    assert not eng.PROFILES['live'].adopt_external


# ============================================================================= UI / defaults
def test_portfolio_snapshot_and_desk(tmp_path):
    e, _, _ = mock_engine(tmp_path)
    snap = e.snapshot()
    assert {'rows', 'count', 'max', 'upnl', 'risk', 'risk_cap'} <= set(snap['portfolio'])
    json.dumps(snap, default=str)
    html = (ROOT / 'web_ui' / 'index.html').read_text(encoding='utf-8')
    js = (ROOT / 'web_ui' / 'desk.js').read_text(encoding='utf-8')
    assert 'id="portfolio-chip"' in html and "$('portfolio-chip')" in js and 'plist.map(pos =>' in js
    assert 'RATCHET' in js and 'ADOPTED' in js


def test_portfolio_defaults():
    p = eng.PROFILES['demo']
    assert (p.multi_slot, p.max_positions, p.portfolio_risk_usdt, p.portfolio_margin_frac) == (True, 3, 1.0, 0.20)
    assert (p.convex_score, p.convex_ofi, p.convex_cvd60_usd, p.convex_mult, p.convex_top_mult) == (0.90, 0.65, 1e6, 1.5, 2.0)
    assert (p.ratchet_trigger_pct, p.ratchet_lock_pct) == (0.15, 0.08) and p.adopt_external
