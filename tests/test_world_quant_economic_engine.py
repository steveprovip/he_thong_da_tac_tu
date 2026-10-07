"""Economic upgrade (2026-10-07p): pure post-only maker entries (no IOC), VFR = ATR 15 m / (fees + spread)
freeze, 85 % of the risk pool to SOL/DOGE, BTC micro-probe, gross TP1 >= 3 x round-trip fee, MARIN lock
+0.10 % -> entry +0.04 %, 180 s ejection below MFE 0.06 % (escalation at 360 s), rolling quarter-Kelly SOL boost
0.70 -> 1.20 SOL."""
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
from tests.test_multi_asset_scanner import SOL, fill_sol, scanner, sol_feed
from tests.test_two_mode_engine import (book_at, btc_long_at, bullish_feed, depth, fill_through, lift, mock_engine,
                                        sig, super_feed)

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / 'data' / 'engine_trades.jsonl'
ENTRY = 84_000.0
BUY_DEC = ms.evaluate(sig(FLOW=('BUY', 1.0, 20), TREND=('BUY', 1.0, 20)), ms.Gates(cost_bps=4))[0]
DIAG = {'consensus': 0.8, 'jev': 9.5, 'gates_ok': True, 'edge_bps': 40.0}
NEW = {'microbe_trigger_pct': 0.10, 'microbe_lock_pct': 0.04}        # defaults; spelled out for the reader


def ledger_rows():
    rows = [json.loads(x) for x in LEDGER.read_text(encoding='utf-8').splitlines() if x.strip()] \
        if LEDGER.exists() else []
    if len(rows) < 34:
        pytest.skip('needs the 34-trade ledger')
    return rows[:34]


def at(pct, entry=ENTRY):
    return round(entry * (1 + pct / 100), 1)


def seed(feed, rng_pct, n=240, px=None, now=None):
    """n closed 1 m bars, each with high-low = rng_pct % of price (flat closes) -> ATR 1 m = ATR 15 m = rng_pct."""
    now = time.time() if now is None else now
    px = px or feed.price() or ENTRY
    t0 = math.floor(now / 60) * 60 - 60 * n
    h, l = px * (1 + rng_pct / 200), px * (1 - rng_pct / 200)
    return feed.seed_bars([(t0 + 60 * i, px, h, l, px) for i in range(n)], now)


def engine_for(tmp_path, sym, **prof):
    prof.setdefault('alt_boost', True)
    e, venue, feed = mock_engine(tmp_path, **prof)
    e.p = replace(e.p, symbol=sym, qty=eng.SYMBOL_QTY.get(sym, 0.0))
    e.spec = dict(eng.FALLBACK_SPECS[sym])
    e.sig_cfg = msig.config_for(sym)
    return e


# ============================================================================= Pillar 1: pure maker GTX
def test_defaults_pure_maker_tau_3s():
    for name in ('demo', 'live'):
        p = eng.PROFILES[name]
        assert not p.super_edge and p.entry == 'gtx' and p.gtx_ttl_s == 3.0 and p.gtx_backoff_ticks == 2


def test_router_never_picks_ioc_by_default(tmp_path):
    e, _, _ = mock_engine(tmp_path, super_edge=False)
    e.diag = {'consensus': 1.0, 'jev': 9.9}
    hot = {'cvd_10s': 50_000_000.0, 'ker': 0.99}                            # the strongest tape there is
    assert e.route(BUY_DEC, hot) == 'gtx'


async def test_super_momentum_now_rests_post_only_and_pays_maker(tmp_path):
    """The 09:35 tape (+$7.49M CVD) used to buy IOC taker (5 + 1 bps). Now: GTX at the bid, maker fill."""
    feed = super_feed()
    e, venue, _ = mock_engine(tmp_path, feed=feed, super_ker=0.0, super_edge=False)
    await e.sync()
    d = await fill_through(e, feed, e.tick())
    assert d.allowed and e.pos and e.pos['entry_type'] == 'GTX' and e.pos['entry_fee_bps'] == 2.0
    assert venue.fills and all(f['maker'] for f in venue.fills)
    assert not any('IOC' in ev['message'] for ev in e.events)


async def test_post_only_reject_backs_off_one_tick_never_takes(tmp_path):
    e, venue, feed = mock_engine(tmp_path)
    await e.sync()
    prices, orig = [], venue.limit_gtx

    async def flaky(sym, side, qty, price, cid):
        prices.append(float(price))
        if len(prices) == 1:
            raise eng.PostOnlyReject('-5022 post-only would take liquidity')
        return await orig(sym, side, qty, price, cid)
    venue.limit_gtx = flaky
    task = asyncio.ensure_future(e.tick())
    await asyncio.sleep(0.15)
    for _ in range(3):
        feed.on_message(depth(prices[-1] - 0.2, prices[-1] - 0.1))       # sellers trade through our backed-off bid
    d = await task
    assert d.allowed and e.pos and len(prices) >= 2
    assert prices[1] == pytest.approx(prices[0] - 0.1)                     # 1 tick AWAY from the touch (BUY)
    assert all(f['maker'] for f in venue.fills)
    assert any('back off 1 tick' in ev['message'] for ev in e.events)


async def test_repeated_post_only_rejects_give_up_without_market(tmp_path):
    e, venue, feed = mock_engine(tmp_path)
    await e.sync()
    calls = []

    async def always(sym, side, qty, price, cid):
        calls.append(float(price))
        raise eng.PostOnlyReject('-5022')
    venue.limit_gtx = always
    d = await e.tick()
    assert d.allowed and e.pos is None and not venue.fills and len(calls) == 3       # 1 + 2 back-offs
    assert calls == pytest.approx([calls[0], calls[0] - 0.1, calls[0] - 0.2])
    assert e.cooldown_until > e.clock() and any('no taker fallback' in ev['message'] for ev in e.events)


# ============================================================================= Pillar 2: VFR + 85 % to alts
def test_vfr_formula():
    assert msig.vfr(0.12, 4.0) == pytest.approx(3.0)
    assert msig.vfr(0.05, 4.0, 0.01) == pytest.approx(5 / 4.01)            # the form's dead-BTC example: 1.25
    assert msig.vfr(0.45, 4.0, 0.7) == pytest.approx(45 / 4.7)             # SOL median 15 m ATR (Sept cache)
    assert msig.vfr(None, 4.0) is None and msig.vfr(0.1, 0.0) is None


def test_bars_15m_aggregation_drops_partial_buckets():
    t0 = 1_800_000_000.0 - (1_800_000_000.0 % 900)
    bars = [(t0 + 60 * i, 100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(40)]   # 2 full buckets + 10 min
    b = msig.bars_15m(bars)
    assert len(b) == 2
    assert b[0] == (t0, 100, 115, 99, 114.5) and b[1][0] == t0 + 900
    assert len(msig.bars_15m(bars[:14] + bars[15:30])) == 2                 # 14/15 bars still counts


def quiet_feed():
    """Book only, no prints: no live 1 m bar can widen the seeded ranges (clock-alignment independent)."""
    f = msig.MicroFeed('BTCUSDT', msig.config_for('BTCUSDT'))
    for _ in range(3):
        f.on_message(depth(ENTRY - 0.05, ENTRY + 0.05))
    return f


def test_feed_atr15_from_bars_or_sqrt15_estimate():
    f = quiet_feed()
    assert seed(f, 0.20, n=240, px=ENTRY) > 0
    q = f.features()
    assert q['atr15_src'] == '15m' and q['atr15_pct'] == pytest.approx(0.20, rel=0.02) and q['bars15_n'] >= 14
    g = quiet_feed()
    seed(g, 0.05, n=20, px=ENTRY)                                           # 20 min: not 5 x 15 m bars yet
    q = g.features()
    assert q['atr15_src'] == 'EST' and q['atr15_pct'] == pytest.approx(q['atr_pct'] * math.sqrt(15), rel=1e-6)


def test_btc_frozen_sol_open(tmp_path):
    btc, sol = engine_for(tmp_path, 'BTCUSDT', vfr_gate=True), engine_for(tmp_path, SOL, vfr_gate=True)
    btc.features = {'atr15_pct': 0.08, 'atr15_src': '15m'}
    sol.features = {'atr15_pct': 0.45, 'atr15_src': '15m'}
    v = btc.vfr_view()
    assert v['frozen'] and v['vfr'] < 3.0 and v['why'].startswith('VFR FROZEN BTC')
    assert not sol.vfr_view()['frozen'] and sol.vfr_view()['vfr'] > 5.0
    ok, why = btc.churn_gate(BUY_DEC, DIAG)
    assert not ok and why.startswith('VFR FROZEN BTC')
    btc.features['atr15_pct'] = 0.13
    assert not btc.vfr_view()['frozen']
    assert eng.ATR15_FLOOR['BTCUSDT'] == 0.10
    btc.features = {}
    assert btc.vfr_view()['frozen'] and 'warming' in btc.vfr_view()['why']


async def test_dead_btc_tape_is_refused_on_a_real_tick(tmp_path):
    feed = bullish_feed()
    seed(feed, 0.04)                                                        # 15 m range 0.04 %: VFR ~1
    e, venue, _ = mock_engine(tmp_path, feed=feed, vfr_gate=True)
    await e.sync()
    d = await e.tick()
    assert not d.allowed and d.reason.startswith('VFR FROZEN BTC') and e.pos is None and not venue.fills
    assert e.radar()['label'].startswith('FROZEN VFR') and e.radar()['frozen']
    assert e.snapshot()['vfr']['frozen']
    json.dumps(e.snapshot(), default=str)


async def test_lively_btc_tape_passes_the_vfr_gate(tmp_path):
    feed = bullish_feed()
    seed(feed, 0.30)
    e, venue, _ = mock_engine(tmp_path, feed=feed, vfr_gate=True)
    await e.sync()
    d = await fill_through(e, feed, e.tick())
    assert d.allowed and e.pos and e.vfr_view()['vfr'] > 5.0


def test_risk_pool_85pct_to_alts(tmp_path):
    btc, sol, doge = (engine_for(tmp_path, s) for s in ('BTCUSDT', SOL, 'DOGEUSDT'))
    assert btc.risk_cap() == pytest.approx(0.15) and sol.risk_cap() == doge.risk_cap() == 1.0
    assert 1 - btc.risk_cap() / btc.p.portfolio_risk_usdt == pytest.approx(0.85)
    live = eng.PROFILES['live']
    assert live.vfr_gate and live.alt_risk_share == 0.0 and live.max_loss_usdt == 0.05 and live.equity_cap == 3.85


# ============================================================================= Pillar 3: 3x fee hurdle + locks
def test_tp1_at_least_3x_round_trip_fee(tmp_path):
    e, _, _ = mock_engine(tmp_path, mr_tp_min=0.05, tp_pct=0.10, asset_brackets=False, atr_scaler=False)
    mr = e.bracket_plan('MR', {'vwap_dev_pct': 0.06})
    assert mr['tp_pct'] == pytest.approx(0.12) and mr['profile'].tp_pct == pytest.approx(0.12)
    st = e.bracket_plan('SWARM', {})
    assert st['tp_pct'] == pytest.approx(0.12)
    assert e.bracket_plan('SWARM', {}, route='ioc')['tp_pct'] == pytest.approx(3 * (5 + 1 + 2) / 100)
    e.p = replace(e.p, tp_pct=0.50)
    assert e.bracket_plan('SWARM', {})['tp_pct'] == 0.50                   # wider targets untouched
    for sym in ('BTCUSDT', SOL, 'DOGEUSDT'):
        assert eng.ASSET_BRACKETS[sym][1] >= 0.12


async def test_mfe_010_locks_entry_plus_004(tmp_path):
    e, venue, feed = await btc_long_at(tmp_path, ENTRY, **NEW)
    book_at(feed, at(0.09))
    await e.tick()
    assert e.pos.get('soft_kind') is None                                   # +0.09 %: not yet
    book_at(feed, at(0.10))
    await e.tick()
    assert e.pos['soft_kind'] == 'LOCK' and e.pos['soft_stop'] == pytest.approx(at(0.04), abs=0.11)
    book_at(feed, round(e.pos['soft_stop'] - 0.1, 1))
    await e.tick()
    x = e.pos['exiting']
    assert x['reason'] == 'SOFT-LOCK'
    lift(feed, x['px'])
    await e.sync()
    h = e.history[0]
    assert h['reason'] == 'SOFT-LOCK' and h['taker_fills'] == 0 and h['pnl'] > -0.002   # ~net breakeven


async def test_eject_threshold_006(tmp_path):
    e, venue, feed = await btc_long_at(tmp_path, ENTRY, eject=True)
    book_at(feed, at(0.05))
    await e.tick()
    book_at(feed, at(0.0) + 3)
    e.pos['opened'] -= 181
    await e.tick()
    x = e.pos['exiting']
    assert x['reason'] == 'EJECT-BE' and x['floor'] == pytest.approx(eng.round_tick(ENTRY * 1.0004, 0.1, 'up'))


async def test_no_eject_at_mfe_007(tmp_path):
    e, venue, feed = await btc_long_at(tmp_path, ENTRY, eject=True)
    book_at(feed, at(0.07))
    await e.tick()
    book_at(feed, at(0.0) + 3)
    e.pos['opened'] -= 181
    await e.tick()
    assert not e.pos.get('exiting')


async def test_eject_escalates_at_360s(tmp_path):
    e, venue, feed = await btc_long_at(tmp_path, ENTRY, eject=True)
    e.pos['opened'] -= 181
    await e.tick()
    book_at(feed, at(-0.05))
    e.pos['opened'] -= 178                                                   # 359 s: still resting at BE
    e.pos['exiting']['posted'] -= 5
    await e.tick()
    assert e.pos['exiting']['reason'] == 'EJECT-BE'
    e.pos['opened'] -= 2                                                     # 361 s
    await e.tick()
    assert e.pos['exiting']['reason'] == 'EJECT' and e.pos['exiting']['floor'] is None


# ============================================================================= Pillar 4: Thorp-Kelly scaling
def test_rolling_kelly_on_the_7_sol_trades(tmp_path):
    sol = engine_for(tmp_path, SOL)
    sol.all_rows = [r for r in ledger_rows() if r.get('symbol') == SOL]
    k = sol.alt_kelly()
    assert k['n'] == 7 and k['p'] == pytest.approx(3 / 7)
    assert k['b'] == pytest.approx((0.3298 / 3) / (0.1022 / 4), rel=0.01)
    assert k['f_star'] == pytest.approx(0.296, abs=0.005) and k['f'] == pytest.approx(0.074, abs=0.002)
    assert sol.alt_cap(117.0, boosted=True) == pytest.approx(1.06)        # 0.70 + 0.50 x 0.74, floor 0.01
    assert sol.alt_cap(117.0) == pytest.approx(0.70)                        # no boost -> base


def test_kelly_floor_window_and_caps(tmp_path):
    sol = engine_for(tmp_path, SOL)
    sol.all_rows = [{'symbol': SOL, 'pnl': x} for x in (0.2, -0.05, 0.2, 0.2)]
    assert sol.alt_kelly()['scale'] == 0.0                                  # 4 < 5 trades
    sol.all_rows = [{'symbol': SOL, 'pnl': x} for x in [-0.1] * 20 + [0.3] * 10 + [-0.05] * 5]
    k = sol.alt_kelly()
    assert k['n'] == 15 and k['p'] == pytest.approx(10 / 15) and k['b'] == pytest.approx(6.0)   # last 15 only
    assert k['f_star'] == pytest.approx(0.6111, abs=1e-3) and k['scale'] == 1.0
    assert sol.alt_cap(100.0, True) == pytest.approx(1.20)                  # max 1.20 SOL
    assert sol.alt_cap(150.0, True) == pytest.approx(0.93)                  # $140 binds
    sol.all_rows = [{'symbol': SOL, 'pnl': x} for x in (-0.1, -0.1, 0.01, -0.1, -0.1)]
    assert sol.alt_kelly()['f_star'] == 0.0 and sol.alt_cap(100.0, True) == pytest.approx(0.70)
    doge = engine_for(tmp_path, 'DOGEUSDT')
    assert doge.alt_cap(0.20) == 300.0 and doge.alt_cap(0.50, True) == pytest.approx(280.0)


async def test_sol_90pct_vote_scales_with_the_real_ledger(tmp_path):
    feeds = {'BTCUSDT': bullish_feed(), SOL: sol_feed()}
    es, venue, _ = await scanner(tmp_path, feeds, alt_boost=True, ker_gate=False, min_edge_bps=0.0,
                                 concordance_gate=False, min_velocity_pct=0.0, fee_hurdle=False)
    sol = es[SOL]
    sol.risk.all_rows[:] = [r for r in ledger_rows() if r.get('symbol') == SOL]
    d = await fill_sol(sol, feeds[SOL], sol.tick())
    await sol.sync()
    p = sol.pos
    assert d.allowed and p['alt_boost'] and p['qty'] == pytest.approx(sol.alt_cap(p['entry'], True))
    assert 0.90 <= p['qty'] <= 1.20 and p['qty'] * p['entry'] <= 140.0 + 1e-6      # $140 binds at SOL 150
    assert sol.risk_at_stop() <= 0.85 + 1e-9
    assert any('ALPHA PRIORITY SOL' in ev['message'] and 'Kelly 7' in ev['message'] for ev in sol.events)


def demo_spec():
    return {'step': 0.0001, 'min_qty': 0.0001, 'min_notional': 5.0, 'tick': 0.1}


async def test_btc_micro_probe_in_low_vfr(tmp_path):
    e, venue, feed = mock_engine(tmp_path, alt_boost=True)
    e.spec = demo_spec()
    e.vfr_view = lambda f=None: {'vfr': 4.0, 'frozen': False, 'why': ''}   # tradable, but not a clean tape
    await e.sync()
    d = await fill_through(e, feed, e.tick())
    assert d.allowed and e.pos and e.pos['qty'] == pytest.approx(0.0001) and e.pos['probe'].startswith('VFR 4.00')
    assert e.risk_at_stop() < 0.03 and any('BTC MICRO-PROBE 0.0001' in ev['message'] for ev in e.events)


async def test_btc_probe_when_full_size_overruns_its_15pct(tmp_path):
    e, venue, feed = mock_engine(tmp_path, alt_boost=True)
    e.spec = demo_spec()
    e.vfr_view = lambda f=None: {'vfr': 8.0, 'frozen': False, 'why': ''}
    await e.sync()
    await fill_through(e, feed, e.tick())
    assert e.pos and e.pos['qty'] == pytest.approx(0.0001) and e.pos['probe'].startswith('risk 0.')


async def test_btc_probe_below_exchange_minimum_is_refused(tmp_path):
    e, venue, feed = mock_engine(tmp_path, alt_boost=True)                 # FALLBACK spec: 0.001 BTC / $100
    e.vfr_view = lambda f=None: {'vfr': 4.0, 'frozen': False, 'why': ''}
    await e.sync()
    d = await e.tick()
    assert e.pos is None and not venue.fills
    assert any('dưới sàn' in ev['message'] for ev in e.events)
    assert e.cooldown_until >= e.clock() + 29                               # 30 s rest, not a refusal per tick


# ============================================================================= ledger evidence / UI
def test_ledger_34_autopsy_numbers():
    rows = ledger_rows()
    gross = sum(r.get('gross') or 0 for r in rows)
    assert gross == pytest.approx(0.9582, abs=1e-3)                        # (form: 0.8885)
    assert sum(r.get('fee') or 0 for r in rows) == pytest.approx(2.1141, abs=1e-3)
    assert sum(r['pnl'] for r in rows) == pytest.approx(-1.2256, abs=1e-3) and sum(r['pnl'] > 0 for r in rows) == 9
    cannibal = [i for i, r in enumerate(rows, 1) if r.get('gross') is not None and r['gross'] >= 0 and r['pnl'] < 0]
    assert cannibal == [3, 7, 13, 14, 18, 20, 22, 34]                     # 8, not 9 (#10/#33 gross < 0)
    ioc = [r for r in rows if r.get('entry_type') == 'IOC']
    assert len(ioc) == 12 and sum(r['pnl'] for r in ioc) == pytest.approx(0.2698, abs=1e-3)   # IOC was net +


def test_ledger_1m_atr_would_freeze_sol_too():
    """Why VFR reads ATR 15 m: on the 1 m ATR stored per trade even SOL's winners #27/#29 are < 3."""
    rows = ledger_rows()
    btc = [r for r in rows if r.get('symbol') == 'BTCUSDT' and r.get('atr_pct')]
    sol = {i: r for i, r in enumerate(rows, 1) if r.get('symbol') == SOL and r.get('atr_pct')}
    assert all(msig.vfr(r['atr_pct'], 4.0) < 2.0 for r in btc)
    assert msig.vfr(sol[27]['atr_pct'], 4.0) < 3.0 and msig.vfr(sol[29]['atr_pct'], 4.0) < 3.0
    assert sol[27]['pnl'] > 0 and sol[29]['pnl'] > 0


def test_desk_shows_vfr():
    js = (ROOT / 'web_ui' / 'desk.js').read_text(encoding='utf-8')
    assert 'VFR ${' in js and "/FROZEN/.test(l)" in js
