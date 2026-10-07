"""Quant layer (2026-10-07k): multi-level OFI, VPIN toxicity gate, ATR(1 m) brackets RR >= 2.5, Avellaneda-Stoikov
MR quote, quarter-Kelly sizing."""
import asyncio
import json
import math
import time
from dataclasses import replace
from pathlib import Path

import pytest

from core import engine as eng
from core import micro_signals as msig
from core import micro_swarm as ms
from tests.test_two_mode_engine import (SYM, agg, book_at, bullish_feed, depth, dump, fill_through, lift, mock_engine,
                                        sig)

ROOT = Path(__file__).resolve().parents[1]
PX = 99_800.0


def ofi_frames(feed, px=PX, n=10):
    """Bids and asks step up 0.1 per frame into (px, px + 0.1): a book being lifted level after level."""
    for i in range(n):
        b = round(px - 0.1 * (n - 1 - i), 1)
        feed.on_message(depth(b, round(b + 0.1, 1), bq=6.0, aq=1.0))


def bars_rows(now, n=20, close=PX, half_range=100.0):
    start = math.floor(now / 60) * 60
    return [(start - 60 * (k + 1), close, close + half_range, close - half_range, close) for k in range(n)][::-1]


def ledger_rows(sym, wins, losses, w=0.2, l=0.1):
    return [{'symbol': sym, 'mode': 'demo', 'pnl': w} for _ in range(wins)] + \
           [{'symbol': sym, 'mode': 'demo', 'pnl': -l} for _ in range(losses)]


# ============================================================================= 1. OFI (Cont-Kukanov-Stoikov, L10)
def test_mlofi_event_rules_per_level():
    M = msig.mlofi
    assert M([(100.0, 5)], [(100.1, 2)], [(100.1, 3)], [(100.1, 2)]) == 3        # bid steps up: + new queue
    assert M([(100.0, 5)], [(100.1, 2)], [(99.9, 4)], [(100.1, 2)]) == -5        # bid steps down: - old queue
    assert M([(100.0, 5)], [(100.1, 2)], [(100.0, 7)], [(100.1, 2)]) == 2        # same price: delta queue
    assert M([(100.0, 5)], [(100.1, 2)], [(100.0, 5)], [(100.2, 6)]) == 2        # ask lifted away: + old ask queue
    assert M([(100.0, 5)], [(100.1, 2)], [(100.0, 5)], [(100.0, 6)]) == -6       # ask steps down: - new ask queue
    two = M([(100.0, 5), (99.9, 8)], [(100.1, 2), (100.2, 4)], [(100.0, 6), (99.9, 10)], [(100.1, 2), (100.2, 4)])
    assert two == 1 + 2                                                         # levels add up


def test_feed_ofi_is_normalised_windowed_and_needs_5_events():
    now = time.time()
    f = msig.MicroFeed(SYM)
    f.on_message(depth(PX, PX + 0.1, bq=6.0, aq=1.0), now - 1)
    for i in range(3):
        f.on_message(depth(PX + 0.1 * (i + 1), PX + 0.1 * (i + 2), bq=6.0, aq=1.0), now)
    assert f.features(now)['ofi_10s'] is None and f.features(now)['ofi_n'] == 3   # 3 updates < 5: undefined
    for i in range(3, 9):
        f.on_message(depth(PX + 0.1 * (i + 1), PX + 0.1 * (i + 2), bq=6.0, aq=1.0), now)
    x = f.features(now)
    assert x['ofi_10s'] == pytest.approx(1.0) and x['ofi_qty_10s'] == pytest.approx(9 * 7)
    assert f.features(now + 11)['ofi_10s'] is None                              # 10 s window


def test_ofi_signal_needs_cvd_15s_the_same_way():
    S = msig.signals
    assert S({'ofi_10s': 0.70, 'cvd_15s': 600_000.0})['OFI']['side'] == 'BUY'
    assert S({'ofi_10s': -0.70, 'cvd_15s': -600_000.0})['OFI']['side'] == 'SELL'
    assert S({'ofi_10s': 0.70, 'cvd_15s': -600_000.0})['OFI'] is None            # book vs tape disagree
    assert S({'ofi_10s': 0.60, 'cvd_15s': 900_000.0})['OFI'] is None
    assert S({'ofi_10s': 0.70, 'cvd_15s': 400_000.0})['OFI'] is None
    assert S({'ofi_10s': 0.70, 'cvd_15s': 60_000.0}, msig.config_for('SOLUSDT'))['OFI']['side'] == 'BUY'
    assert ms.ALPHA['TIDAL'][-1] == 'OFI' and 'OFI' in ms.SIGNAL_KEYS


async def test_ofi_sweep_with_cvd_lifts_consensus_over_85_and_opens(tmp_path):
    base = bullish_feed()
    e0, _, _ = mock_engine(tmp_path, feed=base, gtx_ttl_s=0.2)
    await e0.sync()
    await e0.tick()
    assert e0.diag['consensus'] == pytest.approx(0.80)                         # NORO TREND + LUMEN FLOW only
    feed = bullish_feed()
    ofi_frames(feed)
    x = feed.features()
    assert x['ofi_10s'] >= 0.65 and x['cvd_15s'] >= 500_000
    e, venue, _ = mock_engine(tmp_path, feed=feed)
    await e.sync()
    d = await fill_through(e, feed, e.tick())
    assert e.sig['OFI']['side'] == 'BUY' and e.diag['consensus'] >= 0.85 and e.diag['confluence']['C1_FLOW']
    assert d.allowed and e.pos and e.pos['ofi'] >= 0.65


# ============================================================================= 4. VPIN
def test_vpin_buckets_on_exact_aggressor_volume():
    f = msig.MicroFeed(SYM)
    now = time.time()
    for i in range(40):                                                         # balanced $500k chunks
        f.on_message(agg(PX, 500_000 / PX, maker=i % 2 == 0), now)
    assert f.features(now)['vpin'] == pytest.approx(0.0, abs=1e-6) and f.features(now)['vpin_n'] == 20
    for _ in range(12):
        f.on_message(agg(PX, 1_000_000 / PX, maker=True), now)                 # $12M of pure selling
    assert f.vpin_now() == pytest.approx(1.0)
    g = msig.MicroFeed(SYM)
    g.on_message(agg(PX, 2_500_000 / PX, maker=False), now)                    # one print spills over 3 buckets
    assert len(g.vb_closed) == 2 and g.vb_buy == pytest.approx(500_000) and g.vpin_now() is None   # warmup < 10
    sol = msig.MicroFeed('SOLUSDT', msig.config_for('SOLUSDT'))
    sol.on_message(agg(150.0, 250_000 / 150.0, maker=True, sym='SOLUSDT'), now)
    assert len(sol.vb_closed) == 2                                              # SOL bucket $100k


def test_toxicity_rule():
    T = msig.toxicity
    assert T({'vpin': 0.80})['toxic'] and T({'vpin': 0.80})['state'] == 'TOXIC'
    assert not T({'vpin': 0.75})['toxic'] and T({'vpin': 0.40})['state'] == 'CLEAN'
    assert T({})['state'] == 'WARMUP' and not T({})['toxic']


async def test_vpin_over_075_rune_vetoes_new_entries_for_30s(tmp_path):
    e, venue, feed = mock_engine(tmp_path)
    await e.sync()
    dump(feed, PX, 12_000_000, n=12)
    d = await e.tick()
    assert e.tox['toxic'] and not d.allowed and e.pos is None and not venue.fills
    assert e.gates(e.features).rune[1].startswith('VPIN TOXIC 1.00 > 0.75')
    assert 29.0 < e.toxic_until - e.clock() <= 30.0
    assert any('VPIN TOXIC' in ev['message'] for ev in e.events)


async def test_vpin_spike_cancels_a_resting_maker_entry(tmp_path):
    e, venue, feed = mock_engine(tmp_path, gtx_ttl_s=2.0)
    await e.sync()
    dec = ms.evaluate(sig(FLOW=('BUY', 1.0, 3), TREND=('BUY', 1.0, 3)), ms.Gates())[0]
    task = asyncio.ensure_future(e.execute('BUY', dec, 'SWARM'))
    await asyncio.sleep(0.2)
    assert venue.orders                                                         # post-only bid is resting
    dump(feed, PX, 12_000_000, n=12)                                            # toxic selling arrives
    r = await task
    assert not r['ok'] and r['error'].startswith('VPIN 1.00 > 0.75') and not venue.fills and e.pos is None
    assert e.toxic_until - e.clock() > 25


# ============================================================================= 3. ATR scaler
def test_wilder_atr_and_yang_zhang():
    bars = [(0, 100, 101, 99, 100), (60, 100, 102, 100, 101), (120, 101, 101.5, 98, 99)]
    assert msig.atr(bars) == pytest.approx((2 + 2 + 3.5) / 3)
    flat = [(60 * i, 100, 101, 99, 100) for i in range(15)] + [(900, 100, 116, 100, 100)]
    assert msig.atr(flat, 14) == pytest.approx((2 * 13 + 16) / 14)              # Wilder smoothing after n
    assert msig.yang_zhang([(60 * i, 100, 100, 100, 100) for i in range(6)]) == pytest.approx(0.0)
    yz = msig.yang_zhang([(60 * i, 100 + i % 2, 101 + i % 2, 99 + i % 2, 100.5 + i % 2) for i in range(10)])
    assert 0 < yz < 0.02


def test_feed_rolls_1m_bars_and_merges_the_rest_seed():
    t0 = 1_791_400_000.0 - 1_791_400_000.0 % 60
    f = msig.MicroFeed(SYM)
    for s in range(180):
        f.on_message(agg(PX + (s % 60) - 30, 0.001, maker=s % 2 == 0), t0 + s)
    assert len(f.bars) == 2 and f.bars[0] == (t0, PX - 30, PX + 29, PX - 30, PX + 29)
    rows = [(t0 - 60 * k, PX, PX + 50, PX - 50, PX) for k in (1, 2, 3)] + [(t0, 1, 1, 1, 1), (t0 + 120, 1, 1, 1, 1)]
    assert f.seed_bars(rows, t0 + 180) == 3 and [b[0] for b in f.bars] == [t0 - 180, t0 - 120, t0 - 60, t0, t0 + 60]


@pytest.mark.parametrize('atr_pct', [0.02, 0.05, 0.10, 0.15, 0.25, 0.60])
def test_bracket_plan_reward_risk_at_least_2_5(tmp_path, atr_pct):
    e, _, _ = mock_engine(tmp_path)
    bp = e.bracket_plan('SWARM', {'atr_pct': atr_pct})
    assert bp['src'] == 'ATR' and bp['rr_gross'] >= 2.5 - 1e-9
    assert bp['sl_pct'] == pytest.approx(max(0.15, atr_pct)) and bp['tp_pct'] >= 2.5 * atr_pct - 1e-9
    assert bp['trail_pct'] == pytest.approx(max(0.10, 1.2 * atr_pct))
    assert bp['rr_net'] < bp['rr_gross']                                       # fees always eat into it
    win, loss = bp['tp_pct'] - 0.04, bp['sl_pct'] + 0.08                       # GTX in + maker TP / taker stop + slip
    assert bp['rr_net'] == pytest.approx(win / loss) and bp['be_wr'] == pytest.approx(loss / (win + loss))


def test_static_and_mr_brackets_and_their_honest_rr(tmp_path):
    e, _, _ = mock_engine(tmp_path)
    st = e.bracket_plan('SWARM', {})
    assert st['src'] == 'STATIC' and st['rr_gross'] == pytest.approx(0.50 / 0.20)
    mr = e.bracket_plan('MR', {'vwap_dev_pct': -0.30})
    assert (mr['tp_pct'], mr['sl_pct']) == (0.18, 0.08) and mr['rr_gross'] == pytest.approx(2.25)
    assert mr['be_wr'] > 0.5                                                   # MR needs > 50 % winners after fees
    assert e.bracket_plan('MR', {'vwap_dev_pct': 0.05})['tp_pct'] == 0.12
    live = eng.PROFILES['live']
    assert not live.atr_scaler and not live.kelly


async def test_atr_brackets_on_a_live_entry(tmp_path):
    feed = bullish_feed()
    feed.seed_bars(bars_rows(time.time(), half_range=100.0))
    a = feed.features()['atr_pct']
    assert a is not None and 0.15 < a < 0.30
    e, venue, _ = mock_engine(tmp_path, feed=feed)
    await e.sync()
    d = await fill_through(e, feed, e.tick())
    p = e.pos
    assert d.allowed and p['bracket_src'] == 'ATR' and p['sl_pct'] == pytest.approx(a) and p['rr_gross'] >= 2.5 - 1e-9
    assert p['sl'] == pytest.approx(p['entry'] * (1 - a / 100), abs=0.2)
    assert p['tp'] == pytest.approx(p['entry'] * (1 + 2.5 * a / 100), abs=0.2)
    assert p['atr_trail_pct'] == pytest.approx(1.2 * a)


async def test_runner_trails_1_2_atr_after_tp1(tmp_path):
    feed = bullish_feed()
    feed.seed_bars(bars_rows(time.time(), half_range=60.0))                     # ATR ~0.12 %: SL floor 0.15
    e, venue, _ = mock_engine(tmp_path, feed=feed)
    await e.sync()
    await fill_through(e, feed, e.tick())
    await e.sync()
    p, entry = e.pos, e.pos['entry']
    gap = p['atr_trail_pct']
    assert p['sl_pct'] == pytest.approx(0.15) and p['tp_pct'] == pytest.approx(0.375) and 0.10 <= gap < 0.20
    book_at(feed, round(entry * 1.0038, 1))                                     # TP1 (+0.375 %) fills half
    await e.sync()
    await e.tick()
    assert e.pos['tp1_filled']
    book_at(feed, round(entry * 1.0042, 1))                                     # MFE 0.42 % < 0.50: only ATR trail
    await e.tick()
    assert e.pos['soft_kind'] == 'TRAIL' and e.pos['trail_gap'] == pytest.approx(gap, abs=1e-4)
    lvl = e.pos['soft_stop']
    assert lvl == pytest.approx(e.pos['peak'] * (1 - gap / 100), abs=0.2)
    book_at(feed, round(lvl - 0.1, 1))
    await e.tick()
    lift(feed, e.pos['exiting']['px'])
    await e.sync()
    h = e.history[0]
    assert h['reason'] == 'TP1+SOFT-TRAIL' and h['pnl'] > 0 and h['bracket_src'] == 'ATR'


# ============================================================================= 2. Avellaneda-Stoikov
def test_avellaneda_stoikov_quote():
    flat = msig.as_quote(100.0, 0.0, 1.0, 0.1, 2.0, 5.0)
    half = (0.1 * 1.0 * 5.0 + (2 / 0.1) * math.log(1 + 0.1 / 2.0)) / 2
    assert flat['reservation'] == 100.0 and flat['half_spread_bps'] == pytest.approx(half)
    assert flat['bid'] == pytest.approx(100 * (1 - half / 1e4)) and flat['ask'] == pytest.approx(100 * (1 + half / 1e4))
    long_ = msig.as_quote(100.0, 1.0, 1.0, 0.1, 2.0, 5.0)
    assert long_['reservation'] == pytest.approx(100 * (1 - 0.5 / 1e4))        # q gamma sigma^2 tau = 0.5 bps
    assert long_['ask'] < flat['ask'] and long_['bid'] < flat['bid']           # long inventory skews both down
    capped = msig.as_quote(100.0, 0.0, 10.0, 0.1, 2.0, 5.0, max_offset_bps=2.0)
    assert capped['bid'] == pytest.approx(100 * (1 - 2e-4))


def test_realized_sigma_bps():
    pts = [(float(i), 100.0 * (1.0001 if i % 2 else 1.0)) for i in range(61)]
    assert msig.realized_sigma_bps(pts, 60.0) == pytest.approx(1.0, rel=1e-3)
    assert msig.realized_sigma_bps([(float(i), 100.0) for i in range(61)], 60.0) == 0.0
    assert msig.realized_sigma_bps([(float(i), 100.0) for i in range(5)], 4.0) is None


def test_mr_quote_rests_behind_the_touch_never_through_it(tmp_path):
    e, _, _ = mock_engine(tmp_path)
    f = {'bid': 84_000.0, 'ask': 84_000.1, 'sigma_bps': 5.0}
    mid = 84_000.05
    b = e.mr_quote('BUY', f, 84_000.0)
    s = e.mr_quote('SELL', f, 84_000.1)
    assert mid * (1 - 2e-4) - 0.1 <= b <= 84_000.0 and 84_000.1 <= s <= mid * (1 + 2e-4) + 0.1
    assert e.mr_quote('BUY', {'bid': 1.0, 'ask': 1.1}, 1.0) == 1.0               # no sigma -> join the touch


# ============================================================================= 5. quarter-Kelly
def test_kelly_math_and_states(tmp_path):
    e, _, _ = mock_engine(tmp_path)
    e.all_rows = ledger_rows(SYM, 12, 8)                                       # p 0.6, b 2 -> f* 0.4
    k = e.kelly()
    assert k['state'] == 'EDGE' and k['f_star'] == pytest.approx(0.4) and k['f'] == pytest.approx(0.1)
    e.all_rows = ledger_rows(SYM, 6, 4)
    assert e.kelly()['state'] == 'SAMPLE' and e.kelly()['f'] is None
    e.all_rows = ledger_rows(SYM, 4, 16)                                        # p 0.2, b 2 -> f* -0.2
    assert e.kelly()['state'] == 'NO EDGE' and e.kelly()['f_star'] == pytest.approx(-0.2)
    e.all_rows = ledger_rows('SOLUSDT', 12, 8)                                  # other symbols do not count
    assert e.kelly()['n'] == 0


def test_real_ledger_kelly_is_negative_so_size_stays_minimum(tmp_path):
    path = ROOT / 'data' / 'engine_trades.jsonl'
    rows = [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()] if path.exists() else []
    if len(rows) < 22:
        pytest.skip('needs the 22-trade ledger')
    e, _, _ = mock_engine(tmp_path)
    e.all_rows = [r for r in rows if r.get('mode') == 'demo']
    k = e.kelly()
    assert k['n'] >= 20 and k['state'] == 'NO EDGE' and k['f_star'] < 0
    assert e.kelly_qty(84_000.0, e.p) is None


def test_kelly_scales_sol_but_btc_stays_at_minimum(tmp_path):
    e, _, _ = mock_engine(tmp_path)
    e.all_rows = ledger_rows(SYM, 12, 8)
    e.account = {'wallet': 5_000.0, 'available': 5_000.0}
    assert e.kelly_qty(84_000.0, e.p) is None                                  # 0.5 U at risk < 0.002 BTC minimum
    e.p = replace(e.p, symbol='SOLUSDT', qty=0.0)
    e.spec = dict(eng.FALLBACK_SPECS['SOLUSDT'])
    e.all_rows = ledger_rows('SOLUSDT', 12, 8)
    q = e.kelly_qty(150.0, e.p)
    assert q == pytest.approx(0.50 / (150.0 * (0.20 + 0.08) / 100), abs=0.01)   # risk 0.50 U to the stop
    assert q * 150.0 <= e.p.kelly_max_notional
    pl = eng.plan('BUY', 150.0, e.spec, e.p, 5_000.0, qty=q)
    assert pl['ok'] and pl['loss_at_sl'] == pytest.approx(0.50, abs=0.01)
    e.p = replace(e.p, kelly=False)
    assert e.kelly_qty(150.0, e.p) is None


# ============================================================================= telemetry / UI / defaults
def test_snapshot_quant_view_and_desk_chips(tmp_path):
    e, _, _ = mock_engine(tmp_path)
    snap = e.snapshot()
    q = snap['quant']
    assert {'ofi', 'vpin', 'tox', 'atr_pct', 'yz_pct', 'sigma_bps', 'brackets', 'kelly'} <= set(q)
    assert 'profile' not in q['brackets']
    json.dumps(snap, default=str)
    html = (ROOT / 'web_ui' / 'index.html').read_text(encoding='utf-8')
    js = (ROOT / 'web_ui' / 'desk.js').read_text(encoding='utf-8')
    for el in ('ofi-chip', 'vpin-chip', 'rr-chip', 'kelly-chip'):
        assert f'id="{el}"' in html and f"'{el}'" in js


def test_ultimate_defaults():
    c, p = msig.SignalConfig(), eng.PROFILES['demo']
    assert (c.ofi_levels, c.ofi_window_s, c.ofi_min, c.ofi_min_events) == (10, 10.0, 0.65, 5)
    assert (c.vpin_bucket_usd, c.vpin_buckets, c.vpin_max) == (1_000_000.0, 10, 0.75)
    assert (p.atr_sl_mult, p.atr_tp1_mult, p.atr_trail_mult, p.rr_min) == (1.0, 2.5, 1.2, 2.5)
    assert (p.kelly_lambda, p.kelly_min_trades, p.kelly_max_risk_usdt, p.tp_pct, p.sl_pct) == (0.25, 20, 0.50, 0.50, 0.20)
    assert (p.as_gamma, p.as_k, p.as_max_offset_bps, p.mr_sl_pct) == (0.10, 2.0, 2.0, 0.08)
