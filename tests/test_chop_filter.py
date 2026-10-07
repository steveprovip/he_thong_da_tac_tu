"""Chop filter (2026-10-07g): KER(2 min) lockout, regime-flip 60 s hold + 20 s debounce, TP1 +0.45 %."""
import time

import pytest

from core import engine as eng
from core import micro_signals as msig
from tests.test_two_mode_engine import agg, depth, dump, mock_engine, opened_short

PX = 84_060.0


def tape(path, now, size=0.02, burst_px=PX):
    """One print per second for 120 s: path(i) -> price, alternating aggressor side (net CVD ~ 0)."""
    f = msig.MicroFeed('BTCUSDT')
    for i in range(110):
        f.on_message(agg(path(i), size, maker=i % 2 == 0), now - 120 + i)
    for i in range(30):                                                 # 10 s burst of buying: $750k
        f.on_message(agg(burst_px, 0.3, maker=False), now - 10 + i * 0.3)
    for _ in range(10):
        f.on_message(depth(burst_px, burst_px + 0.1, bq=6.0, aq=1.0), now)
    return f


def chop_feed(now=None):
    """84,000-84,120 sideway (0.14 % box), zig-zag every 10 s - the 11:44/11:48 tape."""
    now = time.time() if now is None else now
    return tape(lambda i: 84_000.0 if (i // 10) % 2 == 0 else 84_120.0, now)


def smooth_trend_feed(now=None):
    """Same flow burst, but price climbed 0.36 % in a straight line over 2 min."""
    now = time.time() if now is None else now
    return tape(lambda i: PX - 300.0 + i * 300.0 / 110, now, burst_px=PX + 30.0)   # the burst lifts price +0.04 %


# ----------------------------------------------------------------------------- KER math
def test_ker_formula_trend_zigzag_flat_and_warmup():
    assert msig.ker([(i, 100.0 + i) for i in range(121)], 120)[0] == pytest.approx(1.0)
    zig, span = msig.ker([(i, 100.0 + (i // 5) % 2) for i in range(121)], 120)
    assert zig < 0.10 and span == 120
    assert msig.ker([(i, 100.0) for i in range(121)], 120)[0] == 0.0           # no path -> no trend
    assert msig.ker([(i, 100.0 + i) for i in range(80, 121)], 120)[0] is None  # 40 s of prints: warming


def test_ker_bucket_sampling_ignores_bid_ask_bounce():
    """Tick prints bounce bid/ask (+-0.1) on a clean trend: per-tick KER ~0.1, 5 s buckets keep the trend."""
    pts = [(i * 0.25, 84_000.0 + i * 0.05 + (0.1 if i % 2 else 0.0)) for i in range(481)]
    tick_ker = abs(pts[-1][1] - pts[0][1]) / sum(abs(b[1] - a[1]) for a, b in zip(pts, pts[1:]))
    k, _ = msig.ker(pts, 120.0)
    assert tick_ker < 0.5 and k > 0.9


def test_chop_hysteresis_040_045():
    L = msig.chop_locked
    assert L(None) and L(0.36, False) and L(0.40, True) and not L(0.40, False) and not L(0.42, True)
    assert L(0.41, True)                                                  # band 0.38-0.42 since 2026-10-07j


def test_feed_exposes_ker():
    assert chop_feed().features()['ker'] < 0.40
    assert smooth_trend_feed().features()['ker'] >= 0.45


# ----------------------------------------------------------------------------- CHOP LOCK (simulation)
async def test_sideway_chop_refuses_entry_even_with_flow_burst(tmp_path):
    """Trade #18/#19 setup: 0.14 % box, $750k burst. The swarm says GO, the KER lock says no."""
    feed = chop_feed()
    e, venue, _ = mock_engine(tmp_path, feed=feed, ker_gate=True)
    await e.sync()
    d = await e.tick()
    assert not d.allowed and d.reason.startswith('CHOP LOCKED KER') and e.pos is None and not venue.fills
    assert e.diag['side'] == 'BUY'                                     # the swarm alone would have traded
    snap = e.snapshot()
    assert snap['chop']['locked'] and snap['chop']['ker'] < 0.40 and snap['features']['ker'] < 0.40


async def test_smooth_trend_unlocks_and_trades(tmp_path):
    feed = smooth_trend_feed()
    e, venue, _ = mock_engine(tmp_path, feed=feed, ker_gate=True, gtx_ttl_s=0.2)
    await e.sync()
    await e.tick()
    assert not e.chop_locked and not (e.decision.reason or '').startswith('CHOP')
    assert e.decision.allowed


async def test_warming_feed_is_locked(tmp_path):
    from tests.test_two_mode_engine import bullish_feed
    e, venue, _ = mock_engine(tmp_path, feed=bullish_feed(), ker_gate=True)       # 50 s of prints only
    await e.sync()
    d = await e.tick()
    assert not d.allowed and 'warming' in d.reason and not venue.fills


# ----------------------------------------------------------------------------- regime-flip hysteresis
async def test_regime_flip_needs_60s_hold_and_20s_continuous_flip(tmp_path):
    e, venue, feed = await opened_short(tmp_path, flow_exit=False)
    cvd0 = feed.features()['cvd_60s']
    dump(feed, e.pos['entry'], 1_330_000 - cvd0, sell=False)            # CVD 60 s +1.33M vs our SHORT
    await e.tick()
    assert not e.pos.get('exiting')                                      # < 60 s since entry
    e.pos['opened'] -= 61
    await e.tick()
    assert not e.pos.get('exiting') and e.pos['flip_since'] is not None  # debounce clock started
    e.pos['flip_since'] -= 21
    await e.tick()
    assert e.pos['exiting']['reason'] == 'REGIME'
    assert any('held 21s' in ev['message'] for ev in e.events)


async def test_cvd_flicker_resets_debounce(tmp_path):
    """CVD pokes over +$1M for a moment and falls back: the 11:44 trap - no exit, no fee burnt."""
    e, venue, feed = await opened_short(tmp_path, flow_exit=False)
    e.pos['opened'] -= 120
    cvd0 = feed.features()['cvd_60s']
    dump(feed, e.pos['entry'], 1_100_000 - cvd0, sell=False)
    await e.tick()
    assert e.pos['flip_since'] is not None and not e.pos.get('exiting')
    dump(feed, e.pos['entry'], 400_000)                                  # sellers back: CVD 60 s +0.7M
    await e.tick()
    assert e.pos['flip_since'] is None and not e.pos.get('exiting') and not e.history


# ----------------------------------------------------------------------------- TP1 +0.45 %
def test_tp1_045_and_the_real_payout():
    p = eng.PROFILES['demo']
    assert p.tp_pct == 0.45 and p.tp1_frac == 0.5
    _, tp = eng.brackets('BUY', 84_000.0, {'tp_pct': p.tp_pct, 'sl_pct': p.sl_pct}, eng.FALLBACK_SPECS['BTCUSDT']['tick'])
    assert tp == pytest.approx(84_000.0 * 1.0045, abs=0.1)
    # 0.002 BTC: TP1 takes HALF -> 0.001 x 84,000 x 0.45 % = 0.378 U, not 0.75 U.
    half = 0.001 * 84_000 * 0.0045
    fees = 0.002 * 84_000 * 4e-4                                          # GTX in + GTX out
    runner_be = 0.001 * 84_000 * 0.0006                                   # runner closed at soft breakeven
    assert half == pytest.approx(0.378) and 0.30 < half + runner_be - fees < 0.40
    full = 0.002 * 84_000 * 0.0045 - fees                                 # only if the whole size made +0.45 %
    assert full == pytest.approx(0.6888, abs=1e-4)
