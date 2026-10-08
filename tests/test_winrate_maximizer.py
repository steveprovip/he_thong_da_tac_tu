"""Win-rate maximizer (2026-10-07h): absorption trap, 2-tier fee shield, strict maker routing, 5 m macro gate, UI."""
import time
from pathlib import Path

import pytest

from core import engine as eng
from core import micro_signals as msig
from core import micro_swarm as ms
from tests.test_two_mode_engine import (SYM, agg, book_at, depth, fill_through, lift, mock_engine, opened, sig,
                                        super_feed, trend_feed)

PX = 99_800.0
WEB = Path(__file__).resolve().parents[1] / 'web_ui'


def flow_feed(lead=3.0, now=None, px=PX):
    """Quiet tape at px - lead for 35 s, then $900k of aggressive buying at px within 10 s (one-sided book)."""
    now = time.time() if now is None else now
    f = msig.MicroFeed(SYM)
    for i in range(35):
        f.on_message(agg(px - lead, 0.02, maker=i % 2 == 0), now - 50 + i)
    for i in range(30):
        f.on_message(agg(px, 0.3, maker=False), now - 10 + i * 0.3)
    for _ in range(10):
        f.on_message(depth(px, px + 0.1, bq=6.0, aq=1.0), now)
    return f


# ============================================================================= 1. absorption trap
def test_absorption_rule_both_sides_and_scale():
    A = msig.absorption
    assert A({'cvd_15s': 600_000.0, 'px_vel_15s': 0.01})['blocks'] == 'BUY'
    assert A({'cvd_15s': 600_000.0, 'px_vel_15s': 0.05})['state'] == 'CLEAR'
    assert A({'cvd_15s': -600_000.0, 'px_vel_15s': -0.01})['blocks'] == 'SELL'
    assert A({'cvd_15s': -600_000.0, 'px_vel_15s': -0.04})['state'] == 'CLEAR'
    assert A({'cvd_15s': 300_000.0, 'px_vel_15s': 0.0})['state'] == 'CLEAR'          # flow too small to judge
    assert A({'cvd_15s': None, 'px_vel_15s': None})['state'] == 'UNKNOWN'
    assert A({'cvd_15s': 60_000.0, 'px_vel_15s': 0.0}, msig.config_for('SOLUSDT'))['blocks'] == 'BUY'


def test_feed_measures_price_delta_concordance():
    f = flow_feed(lead=3.0).features()
    assert f['cvd_15s'] == pytest.approx(30 * 0.3 * PX, rel=0.01) and 0 < f['px_vel_15s'] < 0.01
    assert msig.absorption(f)['state'] == 'TRAPPED'
    g = flow_feed(lead=60.0).features()
    assert g['px_vel_15s'] == pytest.approx(60 / (PX - 60) * 100, rel=1e-3)
    assert msig.absorption(g)['state'] == 'CLEAR'


async def test_cvd_burst_with_flat_price_is_blocked_as_absorption_trap(tmp_path):
    """$900k bought in 10 s, price +0.003 %: the swarm says BUY (TREND + FLOW), the trap guard says no."""
    e, venue, feed = mock_engine(tmp_path, feed=flow_feed(lead=3.0))
    await e.sync()
    d = await e.tick()
    assert e.diag['side'] == 'BUY' and not d.allowed and d.reason.startswith('ABSORPTION TRAP BUY')
    assert e.pos is None and not venue.fills
    assert e.snapshot()['absorption']['state'] == 'TRAPPED'


async def test_same_burst_that_moves_price_trades(tmp_path):
    e, venue, feed = mock_engine(tmp_path, feed=flow_feed(lead=60.0))
    await e.sync()
    d = await fill_through(e, feed, e.tick())
    assert d.allowed and e.pos and e.absorb['state'] == 'CLEAR' and e.pos['px_vel_15s'] >= 0.03


# ============================================================================= 2. 2-tier fee shield
async def test_fee_shield_at_012_survives_a_retrace_to_entry_with_net_profit(tmp_path):
    e, venue, feed = await opened(tmp_path)
    entry = e.pos['entry']
    book_at(feed, round(entry * 1.0013, 1))                       # MFE +0.13 %
    await e.tick()
    lvl = e.pos['soft_stop']
    assert e.pos['soft_kind'] == 'BE' and lvl >= entry * 1.0004 and e.position_row()['shield']
    assert lvl == pytest.approx(entry * 1.0006, abs=0.2)           # fee-true maker BE (0.06 %) beats the 0.04 % floor
    book_at(feed, round(lvl - 0.1, 1))                             # price rolls back over the level ...
    await e.tick()
    lift(feed, e.pos['exiting']['px'])                             # ... and trades there on the way to entry
    await e.sync()
    book_at(feed, entry)
    h = e.history[0]
    assert h['reason'] == 'SOFT-BE' and h['taker_fills'] == 0 and h['pnl'] > 0


async def test_fee_shield_is_not_a_guarantee_on_a_one_way_gap(tmp_path):
    """Honest limit: the shield is a MAKER exit. If price gaps from +0.13 % straight to -0.05 % and never trades
    at the level, the exit re-pegs down the book: the loss is small and capped by the exchange hard SL, not zero."""
    e, venue, feed = await opened(tmp_path, exit_repeg_s=0.0)
    entry = e.pos['entry']
    book_at(feed, round(entry * 1.0013, 1))
    await e.tick()
    book_at(feed, round(entry * 0.9995, 1))                        # one-way drop through the level
    await e.tick()
    lift(feed, e.pos['exiting']['px'])
    await e.sync()
    h = e.history[0]
    assert h['reason'] == 'SOFT-BE-LOSS' and h['pnl'] < 0      # 10-08z2: honest label
    assert h['pnl'] > -(h['qty'] * entry * (0.20 + 0.08) / 100)    # far better than the hard SL


def test_fee_shield_level_after_an_ioc_entry(tmp_path):
    e, _, _ = mock_engine(tmp_path)
    e.spec = dict(eng.FALLBACK_SPECS[SYM])
    gtx = {'side': 'BUY', 'entry': 100_000.0, 'entry_fee_bps': 2.0}
    ioc = dict(gtx, entry_fee_bps=5.0)
    assert e.shield_price(gtx) == pytest.approx(100_060.0)        # 2 + 2 bps + 0.02 %
    assert e.shield_price(ioc) == pytest.approx(100_090.0)        # 5 + 2 bps + 0.02 %
    assert e.shield_price({'side': 'SELL', 'entry': 100_000.0, 'entry_fee_bps': 2.0}) == pytest.approx(99_940.0)
    e.p = eng.replace(e.p, be_offset_pct=-0.5)                     # degenerate offset: the 0.04 % floor holds
    assert e.shield_price(gtx) == pytest.approx(100_040.0)


async def test_profit_lock_at_025_banks_015(tmp_path):
    e, venue, feed = await opened(tmp_path)
    entry = e.pos['entry']
    book_at(feed, round(entry * 1.0026, 1))                       # MFE +0.26 %
    await e.tick()
    lvl = e.pos['soft_stop']
    assert e.pos['soft_kind'] == 'PROFIT' and lvl == pytest.approx(entry * 1.0015, abs=0.2) and e.pos['plock']
    assert e.position_row()['plock']
    book_at(feed, round(lvl - 0.1, 1))
    await e.tick()
    lift(feed, e.pos['exiting']['px'])
    await e.sync()
    h = e.history[0]
    net = 0.002 * entry * (0.0015 - 0.0004)                        # +0.15 % less 2 maker fills
    assert h['reason'] == 'SOFT-PROFIT' and h['pnl'] == pytest.approx(net, abs=0.002) and h['plock']


async def test_profit_lock_hard_mode_moves_the_exchange_stop(tmp_path):
    e, venue, feed = await opened(tmp_path, soft_exits=False)
    entry = e.pos['entry']
    book_at(feed, round(entry * 1.0026, 1))
    await e.tick()
    assert e.pos['plock'] and e.pos['sl'] == pytest.approx(entry * 1.0015, abs=0.2)
    assert any('PROFIT LOCK' in ev['message'] for ev in e.events)


# ============================================================================= 3. strict maker routing
def test_ioc_needs_cvd10_ker_and_conviction_together(tmp_path):
    e, _, _ = mock_engine(tmp_path, super_edge=True)
    dec = ms.evaluate(sig(FLOW=('BUY', 1.0, 3), TREND=('BUY', 1.0, 3)), ms.Gates())[0]
    e.diag = {'consensus': 0.80, 'jev': 9.3}
    hot = {'cvd_10s': 3_000_000.0, 'ker': 0.60}
    assert e.route(dec, hot) == 'ioc'
    for cold in ({'cvd_10s': 2_999_999.0}, {'ker': 0.59}, {'cvd_10s': -3e6}):
        assert e.route(dec, dict(hot, **cold)) == 'gtx'
    e.diag = {'consensus': 0.84, 'jev': 9.19}
    assert e.route(dec, hot) == 'gtx'
    p = eng.PROFILES['demo']
    assert p.gtx_ttl_s == 3.0 and p.entry == 'gtx' and not p.super_edge   # tau <= 3 s, pure maker (10-07p)


async def test_super_momentum_without_straight_tape_rests_as_maker(tmp_path):
    """CVD +$7.49M and JEV 10, but KER unknown (50 s of tape): pegged GTX, no taker fill."""
    feed = super_feed()
    e, venue, _ = mock_engine(tmp_path, feed=feed, gtx_ttl_s=0.2)
    await e.sync()
    d = await e.tick()
    assert d.allowed and e.route(d, feed.features()) == 'gtx'
    assert all(f['maker'] for f in venue.fills)


# ============================================================================= 4. macro 5 m
def test_macro_bias_rules():
    M = msig.macro_bias
    base = {'price': 100.0, 'vwap_5m': 99.0, 'vwap_5m_span': 290.0, 'ema5_slope': 0.06, 'ema5_20': 100.0, 'ema5_n': 25}
    assert M(base)['name'] == 'LONG ONLY' and M(base)['allow'] == ('BUY',)
    assert M(dict(base, price=98.0, ema5_slope=-0.06))['allow'] == ('SELL',)
    assert M(dict(base, ema5_slope=-0.06))['name'] == 'MIXED'
    assert M(dict(base, ema5_slope=0.04))['name'] == 'MIXED'                 # < 0.05 %/5 m: no one-side lock
    assert M(dict(base, ema5_n=5))['name'] == 'WARMUP' and M(dict(base, vwap_5m_span=100.0))['name'] == 'WARMUP'


def test_feed_vwap5m_and_ema20_from_seed_and_live_candles():
    now = 1_791_400_000.0
    f = msig.MicroFeed(SYM)
    assert f.seed_candles([(now - 300 * k, 99_000.0 + (25 - k) * 60) for k in range(1, 26)], now) == 25
    assert f.seed_candles([(now + 60, 1.0)], now) == 0                       # open candle is never seeded
    for i in range(150):
        f.on_message(agg(100_500.0 + i, 0.01, maker=i % 2 == 0), now - 290 + i * 2)
    x = f.features(now)
    assert x['ema5_n'] >= 25 and x['ema5_slope'] > 0 and x['vwap_5m_span'] >= 280
    assert x['vwap_5m'] < x['price'] and msig.macro_bias(x)['name'] == 'LONG ONLY'


async def test_short_above_rising_5m_vwap_is_blocked(tmp_path):
    """60 s says SHORT (DOWN regime), the 5 m frame is LONG ONLY: no counter-trend short."""
    now = time.time()
    feed = msig.MicroFeed(SYM)
    for i in range(40):                                                     # heavy volume 4-5 min ago, far lower
        feed.on_message(agg(PX - 400, 0.5, maker=i % 2 == 0), now - 280 + i * 2)
    trend_feed('SELL', now=now, f=feed)
    feed.seed_candles([(now - 300 * k, 99_000.0 + (25 - k) * 100) for k in range(1, 26)], now)
    e, venue, _ = mock_engine(tmp_path, feed=feed)
    await e.sync()
    d = await e.tick()
    assert e.reg['name'] == 'DOWN' and e.macro['name'] == 'LONG ONLY'
    assert not d.allowed and d.reason.startswith('MACRO 5m LONG ONLY: cấm SELL') and not venue.fills
    assert e.snapshot()['macro']['name'] == 'LONG ONLY'


async def test_runner_seeds_macro_from_klines(monkeypatch, tmp_path):
    e, _, feed = mock_engine(tmp_path)
    r = eng.Runner('demo', allow_live=False, env={}, serve=False)
    monkeypatch.setattr(eng, 'fetch_klines_5m', lambda s: [(time.time() - 300 * k, 100.0 + k) for k in range(1, 31)])
    await r.seed_macro(e)
    assert feed.ema5_n >= 30 and any('seeded EMA-20 with 30' in ev['message'] for ev in e.events)

    def boom(s):
        raise OSError('offline')
    monkeypatch.setattr(eng, 'fetch_klines_5m', boom)
    await r.seed_macro(e)
    assert any('kline seed failed' in ev['message'] for ev in e.events)


# ============================================================================= 5. telemetry / UI
def test_snapshot_and_dashboard_ship_absorption_macro_shield(tmp_path):
    import json
    e, _, _ = mock_engine(tmp_path)
    snap = e.snapshot()
    json.dumps(snap, default=str)
    assert {'absorption', 'macro', 'chop'} <= set(snap) and 'px_vel_15s' in snap['features']
    html = (WEB / 'index.html').read_text(encoding='utf-8')
    js = (WEB / 'desk.js').read_text(encoding='utf-8')
    for el in ('absorb-badge', 'macro-chip', 'shield-chip'):
        assert f'id="{el}"' in html and f"'{el}'" in js
    assert 'ABSORPTION: TRAPPED' in js and 'SHIELD: ARMED' in js


def test_defaults():
    p = eng.PROFILES['demo']
    assert (p.fee_shield_pct, p.be_trigger_pct, p.lock_trigger_pct, p.lock_pct) == (0.04, 0.12, 0.25, 0.15)
    assert p.absorb_gate and p.macro_gate and (p.super_cvd_usd, p.super_ker, p.super_jev) == (3e6, 0.60, 9.2)
    c = msig.SignalConfig()
    assert (c.absorb_window_s, c.absorb_usd, c.absorb_min_move_pct) == (15.0, 500_000.0, 0.025)
