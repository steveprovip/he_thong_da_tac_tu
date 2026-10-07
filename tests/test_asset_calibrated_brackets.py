"""Per-coin calibration (2026-10-07n): SL/TP1 floors per asset, equal-risk size for SOL/DOGE, per-symbol VPIN
buckets with an asymmetric veto, and the Binance one-closePosition-stop rule (-4130) on ratchet / entry stops."""
import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from core import engine as eng
from core import micro_signals as msig
from core import micro_swarm as ms
from live.binance_futures_client import BinanceError
from tests.test_multi_asset_scanner import SOL, fill_sol, scanner, sol_feed
from tests.test_two_mode_engine import (SYM, book_at, depth, dump, fill_through, mock_engine, opened, sig)

ROOT = Path(__file__).resolve().parents[1]
RELAX = dict(ker_gate=False, min_edge_bps=0.0, concordance_gate=False, min_velocity_pct=0.0, fee_hurdle=False)
BUY_DEC = ms.evaluate(sig(FLOW=('BUY', 1.0, 20), TREND=('BUY', 1.0, 20)), ms.Gates(cost_bps=4))[0]
SELL_DEC = ms.evaluate(sig(FLOW=('SELL', 1.0, 20), TREND=('SELL', 1.0, 20)), ms.Gates(cost_bps=4))[0]
DIAG = {'consensus': 0.8, 'jev': 9.5, 'gates_ok': True, 'edge_bps': 40.0}


def engine_for(tmp_path, sym, **prof):
    e, venue, feed = mock_engine(tmp_path, **prof)
    e.p = replace(e.p, symbol=sym, qty=eng.SYMBOL_QTY.get(sym, 0.0))
    e.spec = dict(eng.FALLBACK_SPECS[sym])
    return e


def sol_book(feed, px):
    for _ in range(3):
        feed.on_message(depth(round(px, 2), round(px + 0.01, 2), sym=SOL))


# ============================================================================= 1. per-coin brackets
@pytest.mark.parametrize('sym,atr,sl,tp', [
    ('BTCUSDT', 0.05, 0.15, 0.38), ('SOLUSDT', 0.08, 0.35, 0.875), ('DOGEUSDT', 0.10, 0.50, 1.25),
    ('SOLUSDT', 0.50, 0.50, 1.25)])                                            # big ATR still wins
def test_asset_floors_under_the_atr_scaler(tmp_path, sym, atr, sl, tp):
    e = engine_for(tmp_path, sym)
    bp = e.bracket_plan('SWARM', {'atr_pct': atr})
    assert bp['src'] == 'ATR' and bp['sl_pct'] == pytest.approx(sl) and bp['tp_pct'] == pytest.approx(tp)
    assert bp['rr_gross'] >= 2.5 - 1e-9


def test_static_path_and_switch_off(tmp_path):
    sol = engine_for(tmp_path, SOL)
    st = sol.bracket_plan('SWARM', {})
    assert st['src'] == 'STATIC' and (st['sl_pct'], st['tp_pct']) == (0.35, pytest.approx(0.875))
    btc = engine_for(tmp_path, 'BTCUSDT')
    assert (btc.bracket_plan('SWARM', {})['sl_pct'], btc.bracket_plan('SWARM', {})['tp_pct']) == (0.20, 0.50)
    sol.p = replace(sol.p, asset_brackets=False)
    assert sol.bracket_plan('SWARM', {'atr_pct': 0.08})['sl_pct'] == 0.15
    assert not eng.PROFILES['live'].asset_brackets and not eng.PROFILES['live'].risk_parity


async def test_sol_survives_a_020_dip_and_takes_tp1_at_0875(tmp_path):
    from tests.test_two_mode_engine import bullish_feed
    feeds = {'BTCUSDT': bullish_feed(), SOL: sol_feed()}
    es, venue, _ = await scanner(tmp_path, feeds, **RELAX)
    sol = es[SOL]
    d = await fill_sol(sol, feeds[SOL], sol.tick())
    await sol.sync()
    p, entry = sol.pos, sol.pos['entry']
    assert d.allowed and p['sl_pct'] == pytest.approx(0.35) and p['tp_pct'] == pytest.approx(0.875)
    assert p['sl'] == pytest.approx(entry * 0.9965, abs=0.011)
    assert entry * 0.998 < entry * (1 - 0.15 / 100) + 0.01 and entry * 0.998 > p['sl']   # the old 0.15 % stop would die
    sol_book(feeds[SOL], entry * 0.998)                                       # -0.20 % jerk
    await sol.sync()
    await sol.tick()
    assert sol.pos is not None and not sol.pos.get('exiting')
    sol_book(feeds[SOL], entry * 1.0092)                                      # +0.92 %: TP1 (+0.875 %) fills
    await sol.sync()
    await sol.tick()
    assert sol.pos['tp1_filled'] and sol.pos['ratchet']
    tp_fill = [f for f in venue.fills if f['price'] > entry][0]
    gross = tp_fill['qty'] * (tp_fill['price'] - entry)
    assert tp_fill['maker'] and gross == pytest.approx(tp_fill['qty'] * entry * 0.00875, rel=0.05)


# ============================================================================= 3. equal-risk size
def test_parity_size_sol_doge_btc(tmp_path):
    sol = engine_for(tmp_path, SOL)
    prof = sol.bracket_plan('SWARM', {'atr_pct': 0.08})['profile']
    q = sol.parity_qty(117.0, prof)
    assert q == pytest.approx(0.30) and q * 117.0 * (0.35 + 0.08) / 100 == pytest.approx(0.15, rel=0.03)
    doge = engine_for(tmp_path, 'DOGEUSDT')
    qd = doge.parity_qty(0.089, doge.bracket_plan('SWARM', {})['profile'])
    assert qd == pytest.approx(291.0) and qd * 0.089 * 0.0058 == pytest.approx(0.15, rel=0.01)
    btc = engine_for(tmp_path, 'BTCUSDT')
    assert btc.parity_qty(84_000.0, btc.p) is None                           # fixed 0.002 (exchange minimum)
    sol.p = replace(sol.p, risk_parity=False)
    assert sol.parity_qty(117.0, prof) is None


# ============================================================================= 2. VPIN per coin, asymmetric
def test_vpin_bucket_per_symbol():
    for sym, usd in (('BTCUSDT', 2.5e6), ('SOLUSDT', 2.5e5), ('DOGEUSDT', 2e5)):
        c = msig.config_for(sym)
        assert c.vpin_bucket_usd * c.usd_scale == pytest.approx(usd) and c.vpin_max == 0.80


async def test_vpin_082_with_the_breakout_is_allowed_against_it_blocked(tmp_path):
    e, venue, feed = mock_engine(tmp_path)
    e.reg = {'name': 'UP'}
    e.features = {'px_vel_15s': 0.1}
    e.mark_toxic(0.82, 'BUY')                                                 # toxic BUYING
    assert e.churn_gate(BUY_DEC, DIAG)[0]
    ok, why = e.churn_gate(SELL_DEC, DIAG)
    assert not ok and why.startswith('VPIN TOXIC 0.82 BUY flow · cấm SELL')
    assert 'day' in e.gates(feed.features()).rune[1]                          # no blanket RUNE pause any more


async def test_vpin_side_from_the_tape(tmp_path):
    e, venue, feed = mock_engine(tmp_path)
    await e.sync()
    dump(feed, 99_800.0, 12_000_000, sell=False, n=12)                        # $12M of buying
    await e.tick()
    assert e.tox['toxic'] and e.tox['side'] == 'BUY' and e.toxic_side == 'BUY'
    assert not e.toxic_blocks('BUY') and e.toxic_blocks('SELL')


async def test_resting_bid_survives_toxic_buying(tmp_path):
    e, venue, feed = mock_engine(tmp_path, gtx_ttl_s=1.5)
    await e.sync()
    task = asyncio.ensure_future(e.execute('BUY', BUY_DEC, 'SWARM'))
    await asyncio.sleep(0.2)
    dump(feed, 99_800.0, 12_000_000, sell=False, n=12)                        # toxic flow WITH our bid
    await asyncio.sleep(0.3)
    bid = feed.bid
    for _ in range(3):
        feed.on_message(depth(bid - 0.1, bid))
    r = await task
    assert r['ok'] and e.pos and e.pos['side'] == 'BUY'


# ============================================================================= -4130: one closePosition stop per side
def strict_stops(venue):
    """Binance rule: a second closePosition stop on the same side -> -4130."""
    orig = venue.stop

    async def stop(symbol, side, price, cid):
        if any(o['kind'] == 'SL' and o['status'] == 'NEW' and o['symbol'] == symbol and o['side'] == side
               for o in venue.orders.values()):
            raise BinanceError(-4130, 400)
        await orig(symbol, side, price, cid)
    venue.stop = stop


async def test_ratchet_works_under_the_4130_rule(tmp_path):
    e, venue, feed = await opened(tmp_path)
    strict_stops(venue)
    entry, cid0 = e.pos['entry'], e.pos['sl_cid']
    book_at(feed, round(entry * 1.0018, 1))
    await e.tick()
    p = e.pos
    live = [c for c, o in venue.orders.items() if o['kind'] == 'SL' and o['status'] == 'NEW']
    assert p['ratchet'] and p['sl'] == pytest.approx(entry * 1.0008, abs=0.2) and live == [p['sl_cid']] != [cid0]


async def test_failed_swap_restores_the_old_stop_and_backs_off(tmp_path):
    e, venue, feed = await opened(tmp_path)
    strict_stops(venue)
    inner, calls = venue.stop, []

    async def flaky(symbol, side, price, cid):
        calls.append(float(price))
        if abs(float(price) - e.pos['entry'] * 1.0008) < 1.0:
            raise BinanceError(-2021, 400)                                    # new level refused after the cancel
        await inner(symbol, side, price, cid)
    venue.stop = flaky
    sl0 = e.pos['sl']
    book_at(feed, round(e.pos['entry'] * 1.0018, 1))
    await e.tick()
    n = len(calls)
    assert not e.pos.get('ratchet') and e.pos['sl'] == sl0 and e.pos['ratchet_retry_at'] > e.clock()
    live = [o for o in venue.orders.values() if o['kind'] == 'SL' and o['status'] == 'NEW']
    assert len(live) == 1 and live[0]['price'] == pytest.approx(sl0)          # never naked
    await e.tick()
    assert len(calls) == n                                                    # 5 s back-off, no spam


async def test_stale_stop_at_entry_is_cleared_not_no_stop(tmp_path):
    """#23 SOL NO_STOP: an old closePosition stop on the venue made the new stop fail -> market close."""
    e, venue, feed = mock_engine(tmp_path)
    stale = {'gs-stale': True}
    orig_stop, orig_cancel, orig_view = venue.stop, venue.cancel_stop, venue.orders_view

    async def stop(symbol, side, price, cid):
        if stale.get('gs-stale'):
            raise BinanceError(-4130, 400)
        await orig_stop(symbol, side, price, cid)

    async def cancel_stop(cid):
        if cid == 'gs-stale':
            stale['gs-stale'] = False
        await orig_cancel(cid)

    async def orders_view(symbol):
        rows = await orig_view(symbol)
        return rows + ([{'id': 'gs-stale', 'side': 'SELL', 'type': 'STOP_MARKET', 'price': 1.0, 'qty': 0.0,
                         'reduce': True, 'time': 0}] if stale['gs-stale'] else [])
    venue.stop, venue.cancel_stop, venue.orders_view = stop, cancel_stop, orders_view
    await e.sync()
    d = await fill_through(e, feed, e.tick())
    assert d.allowed and e.pos and not stale['gs-stale'] and not e.history
    assert any('stale stop cleared' in ev['message'] for ev in e.events)


def test_ledger_sol_losses_were_not_only_tight_stops():
    path = ROOT / 'data' / 'engine_trades.jsonl'
    rows = [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()] if path.exists() else []
    if len(rows) < 26:
        pytest.skip('needs the 26-trade ledger')
    r23, r25, r26 = rows[22], rows[24], rows[25]
    loss = lambda r: (r['exit'] / r['entry'] - 1) * 100 * (1 if r['side'] == 'BUY' else -1)
    assert r23['reason'] == 'NO_STOP' and r23['hold_s'] < 5                  # stop never placed (fixed: -4130 retry)
    assert r25['reason'] == 'SL' and loss(r25) == pytest.approx(-0.153, abs=0.01)   # the 0.15 % stop
    assert r26['reason'] == 'SL' and loss(r26) < -0.5                        # 0.15 % stop filled at -0.61 %: slippage


# ============================================================================= UI / defaults
def test_ui_and_defaults(tmp_path):
    js = (ROOT / 'web_ui' / 'desk.js').read_text(encoding='utf-8')
    assert "String(qv.symbol || '').replace('USDT', '')} ${br.src} SL" in js and 'r.sl_pct' in js
    e, _, _ = mock_engine(tmp_path)
    row = e.radar()
    assert {'sl_pct', 'tp_pct', 'src'} <= set(row) and e.snapshot()['quant']['symbol'] == SYM
    assert eng.ASSET_BRACKETS == {'BTCUSDT': (0.15, 0.38), 'SOLUSDT': (0.35, 0.85), 'DOGEUSDT': (0.50, 1.25)}
    assert eng.PROFILES['demo'].risk_target_usdt == 0.15 and msig.VPIN_BUCKET_USD['SOLUSDT'] == 250_000.0
