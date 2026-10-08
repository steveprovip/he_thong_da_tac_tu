"""2-mode engine: micro signals -> 8-agent vote -> VESKA -> brackets -> settle, controls, gates."""
import http.client
import json
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from core import engine as eng
from core import micro_signals as msig
from core import micro_swarm as ms
from core.consensus_engine import TokenAuthority

SYM = 'BTCUSDT'


def agg(px, q, maker=False, sym=SYM):
    return {'stream': f'{sym.lower()}@aggTrade', 'data': {'p': str(px), 'q': str(q), 'm': maker}}


def depth(bid, ask, bq=5.0, aq=1.0, sym=SYM):
    return {'stream': f'{sym.lower()}@depth5@100ms', 'data': {'b': [[str(bid), str(bq)]], 'a': [[str(ask), str(aq)]]}}


def liq(side, usd, px=100_000.0, sym=SYM):
    return {'stream': f'{sym.lower()}@forceOrder', 'data': {'o': {'s': sym, 'S': side, 'q': str(usd / px),
                                                                  'ap': str(px), 'z': str(usd / px)}}}


def trend_feed(side='BUY', now=None, sym=SYM, px=99_800.0, f=None):
    """Trend leg: earlier opposite prints, then $600k of aggressive flow with a one-sided book,
    price through the 60 s VWAP in the direction of the flow."""
    now = time.time() if now is None else now
    d = 1 if side == 'BUY' else -1
    f = f or msig.MicroFeed(sym)
    for i in range(30):
        f.on_message(agg(px - d * (100 + (i % 2) * 250), 0.05, maker=d > 0), now - 50 + i)  # earlier, wide range
    for i in range(30):
        f.on_message(agg(px, 0.3, maker=d < 0), now - 10 + i * 0.3)               # +-$900k aggressor flow
    for _ in range(10):
        f.on_message(depth(px, px + 0.1, bq=6.0 if d > 0 else 1.0, aq=1.0 if d > 0 else 6.0), now)
    return f


def bullish_feed(now=None, sym=SYM):
    return trend_feed('BUY', now, sym)


def bearish_feed(now=None, sym=SYM):
    return trend_feed('SELL', now, sym)


def neutral_dip_feed(now=None):
    """Flat flow (|CVD| < $200k), price at the bottom of the 60 s range 0.1 % under VWAP, bids returning."""
    now = time.time() if now is None else now
    f = msig.MicroFeed(SYM)
    for i in range(40):
        f.on_message(agg(100_100.0, 0.05, maker=i % 2 == 0), now - 50 + i)
    for i in range(10):
        f.on_message(agg(99_900.0, 0.05, maker=i % 3 == 0), now - 6 + i * 0.5)
    for _ in range(10):
        f.on_message(depth(99_900.0, 99_900.1, bq=6.0, aq=1.0), now)
    return f


# ----------------------------------------------------------------------------- signals / regime gate
def test_uptrend_regime_allows_long_trend_and_bans_mean_reversion_shorts():
    f = bullish_feed().features()
    r = msig.regime(f)
    s = msig.signals(f, reg=r)
    assert r['name'] == 'UP' and r['allow'] == ('BUY',) and not r['mr_ok']
    assert f['cvd_60s'] >= 300_000 and f['vwap_dev_pct'] > 0
    assert s['TREND']['side'] == 'BUY' and s['FLOW']['side'] == 'BUY' and s['VWAP'] is None


def test_falling_knife_buy_is_banned_and_flipped_to_short():
    """The 2026-10-07 incident: CVD 60 s -$3.4M, pressure -25, bot bought the dip. Now: SHORT ONLY."""
    f = bearish_feed().features()
    f['cvd_60s'], f['cvd_3m'] = -3_440_000.0, -4_100_000.0
    r = msig.regime(f)
    s = msig.signals(f, reg=r)
    assert r['name'] == 'DOWN' and r['allow'] == ('SELL',) and 'SHORT ONLY' in r['label']
    assert s['TREND']['side'] == 'SELL' and s['FLOW']['side'] == 'SELL'
    knife = dict(f, liq_long_usd=40_000.0)                       # long flush -> counter-trend BUY signal
    s2 = msig.signals(knife)
    assert s2['LIQ'] is None and any(b['key'] == 'LIQ' and b['side'] == 'BUY' for b in s2['BANNED'])
    d, _, diag = ms.evaluate(s, ms.Gates(cost_bps=4), allow=r['allow'])
    assert d.allowed and d.side == 'SELL' and diag['confluence']['C4_TREND'] and diag['confluence']['C1_FLOW']


def test_mean_reversion_only_in_neutral_flow():
    f = neutral_dip_feed().features()
    r = msig.regime(f)
    assert r['name'] == 'NEUTRAL' and r['mr_ok'] and abs(f['cvd_60s']) <= 200_000
    s = msig.signals(f, reg=r)
    assert s['VWAP']['side'] == 'BUY' and f['vwap_dev_pct'] <= -0.08
    drift = dict(f, cvd_60s=-350_000.0, cvd_norm=-0.10)          # selling builds: no more dip buying
    assert msig.regime(drift)['name'] == 'DRIFT' and msig.signals(drift)['VWAP'] is None


def test_regime_thresholds_multi_timeframe_and_symbol_scale():
    R = msig.regime
    assert R({'cvd_60s': -400_000.0, 'cvd_3m': -600_000.0})['name'] == 'DOWN'
    assert R({'cvd_60s': 400_000.0, 'cvd_3m': 600_000.0})['name'] == 'UP'
    assert R({'cvd_60s': 830_000.0, 'cvd_3m': 350_000.0})['name'] == 'DRIFT'          # 60 s burst, 3 m unconfirmed
    assert R({'cvd_60s': 0.0, 'cvd_3m': 0.0, 'cvd_norm': -0.40})['name'] == 'NEUTRAL'  # norm alone no longer flips
    assert R({'cvd_60s': 150_000.0, 'cvd_3m': 300_000.0})['name'] == 'NEUTRAL'
    doge = msig.config_for('DOGEUSDT')
    assert R({'cvd_60s': -45_000.0, 'cvd_3m': -65_000.0}, doge)['name'] == 'DOWN'


def test_whipsaw_0824_up_to_0826_down_is_absorbed():
    """08:24 CVD 60 s +0.83M -> 08:26 -0.77M inside a sideways 3 m: never two opposite strong regimes."""
    up = msig.regime({'cvd_60s': 830_000.0, 'cvd_3m': 900_000.0})
    flip = msig.regime({'cvd_60s': -770_000.0, 'cvd_3m': 60_000.0})       # the 3 m window still holds the buying
    assert up['name'] == 'UP' and flip['name'] == 'DRIFT' and not flip['trend_ok'] and not flip['mr_ok']
    assert 'CONFLICT' in flip['label']
    f = {'cvd_60s': -770_000.0, 'cvd_3m': 60_000.0, 'obi': -0.4, 'vwap_dev_pct': -0.05, 'cvd_norm': -0.5}
    assert msig.signals(f)['TREND'] is None                                # no SHORT on a 60 s blip


def test_swarm_rejects_side_outside_regime_even_if_votes_agree():
    d, _, _ = ms.evaluate(sig(FLOW=('BUY', 1.0, 3), VWAP=('BUY', 1.0, 3)), ms.Gates(), allow=('SELL',))
    assert not d.allowed and d.reason.startswith('REGIME BAN BUY')


def test_liquidation_impulse_window_and_symbol_filter():
    f = msig.MicroFeed(SYM)
    f.on_message(liq('SELL', 6_000), 100.0)
    f.on_message(liq('SELL', 5_000), 101.0)
    f.on_message(liq('SELL', 50_000, sym='ETHUSDT'), 101.0)                    # other symbol ignored
    s = msig.signals(f.features(101.5))
    assert s['LIQ']['side'] == 'BUY' and 'LONG liq $11,000' in s['LIQ']['why']
    assert msig.signals(f.features(104.5))['LIQ'] is None                        # outside 3 s


def test_strength_curve_calibration():
    assert msig.strength(1.0) == pytest.approx(0.70) and msig.strength(1.5) == 1.0 and msig.strength(0.5) == pytest.approx(0.4)
    weak, _, _ = ms.evaluate(sig(FLOW=('BUY', 0.70, 1), VWAP=('BUY', 0.70, 1)), ms.Gates(), jev_min=None)
    three, _, _ = ms.evaluate(sig(FLOW=('BUY', 0.70, 1), VWAP=('BUY', 0.70, 1), LIQ=('BUY', 0.70, 1)), ms.Gates(),
                              jev_min=None)                       # C1 + C2 confluence
    assert not weak.allowed and three.allowed


def test_flow_needs_obi_and_cvd_to_agree():
    f = {'obi': 0.3, 'cvd_norm': -0.5, 'vwap_dev_pct': 0.01}
    assert msig.signals(f)['FLOW'] is None
    assert msig.signals({'obi': 0.10, 'cvd_norm': 0.9})['FLOW'] is None


# ----------------------------------------------------------------------------- swarm
def sig(**kw):
    base = {'FLOW': None, 'VWAP': None, 'LIQ': None}
    for k, (side, strength, edge) in kw.items():
        base[k] = {'side': side, 'strength': strength, 'edge_bps': edge, 'why': k}
    return base


def test_two_aligned_triggers_reach_supermajority_and_token_issues():
    d, votes, diag = ms.evaluate(sig(FLOW=('BUY', 1.0, 3), VWAP=('BUY', 1.0, 3)), ms.Gates())
    assert d.allowed and d.score == pytest.approx(0.80) and diag['path'] == 'SWARM' and len(votes) == 8
    intent = eng.MicroIntent(SYM, 'BUY', '0.001', 'market', 'AUTO', 1)
    a = TokenAuthority()
    a.consume(a.issue(intent, d), intent)


def test_jev_fast_path_needs_all_gates_edge_and_confluence():
    single = ms.evaluate(sig(LIQ=('BUY', 0.7, 40)), ms.Gates(cost_bps=12))[0]
    assert not single.allowed and single.reason.startswith('CONFLUENCE 1/3')      # C3 alone is not enough
    one = sig(LIQ=('BUY', 0.7, 40), FLOW=('BUY', 0.7, 1))
    d, _, diag = ms.evaluate(one, ms.Gates(cost_bps=12))
    assert d.allowed and diag['path'] == 'JEV' and d.score >= 0.8 and d.reason.startswith('JEV FAST')
    assert diag['confluence']['C1_FLOW'] and diag['confluence']['C3_EDGE'] and diag['confluence']['count'] == 2
    d2, _, _ = ms.evaluate(one, ms.Gates(cost_bps=12, rune=(False, 'daily loss')))
    assert not d2.allowed and d2.veto
    d3, _, _ = ms.evaluate(one, ms.Gates(cost_bps=12), jev_min=None)
    assert not d3.allowed and d3.reason.startswith('WEAK')


def test_no_trigger_reads_scanning():
    d, votes, _ = ms.evaluate(sig(), ms.Gates())
    assert not d.allowed and d.reason.startswith('SCANNING')


# ----------------------------------------------------------------------------- sizing / capital gate
def test_plan_demo_btc_and_live_capital_gate():
    btc, doge = eng.FALLBACK_SPECS['BTCUSDT'], eng.FALLBACK_SPECS['DOGEUSDT']
    d = eng.plan('BUY', 100_000.0, btc, eng.PROFILES['demo'])
    assert d['ok'] and d['qty_str'] == '0.002' and d['notional'] >= 100      # 0.002 BTC: TP1 can split in half
    assert eng.plan('BUY', 40_000.0, btc, eng.PROFILES['demo'])['qty_str'] == '0.003'   # min notional wins
    assert not eng.plan('BUY', 85_000.0, btc, replace(eng.PROFILES['demo'], max_loss_usdt=0.05))['ok']
    live = eng.PROFILES['live']
    bad = eng.plan('BUY', 100_000.0, btc, live)
    assert not bad['ok'] and 'CAPITAL GATE' in bad['why']
    ok = eng.plan('BUY', 0.2, doge, live, available=3.85)
    assert ok['ok'] and ok['loss_at_sl'] <= 0.05 + 1e-9 and ok['margin'] <= 3.85 and ok['sl_pct'] > 0.1
    assert not eng.plan('BUY', 0.2, doge, live, available=0.5)['ok']


def test_credentials_aliases():
    assert eng.credentials('live', {'BINANCE_API_KEY': 'k', 'BINANCE_API_SECRET': 's'}) == ('k', 's')
    assert eng.credentials('demo', {'BINANCE_TESTNET_API_KEY': 'k', 'BINANCE_TESTNET_API_SECRET': 's'}) == ('k', 's')


def test_live_venue_refuses_unarmed_or_wrong_host():
    for mode, rest, armed in (('mainnet', 'https://fapi.binance.com', False),
                              ('testnet', 'https://demo-fapi.binance.com', True)):
        c = SimpleNamespace(settings=SimpleNamespace(mode=mode, rest=rest, armed=armed))
        with pytest.raises(PermissionError):
            eng.LiveVenue(c)


# ----------------------------------------------------------------------------- engine on the MOCK broker
def mock_engine(tmp_path, feed=None, mode='demo', **prof):
    feed = feed or bullish_feed()
    prof.setdefault('gtx_ttl_s', 0.6)
    prof.setdefault('min_edge_bps', 0.0)          # churn edge floor has its own tests (test_churn_*)
    prof.setdefault('ker_gate', False)            # KER chop lock has its own tests (test_chop_filter.py)
    prof.setdefault('fee_hurdle', False)          # exit fee hurdle has its own tests (test_fee_hurdle.py)
    prof.setdefault('min_velocity_pct', 0.0)      # entry velocity gate: test_fee_hurdle.py
    prof.setdefault('concordance_gate', False)    # triple flow concordance: test_concordance_guard.py
    prof.setdefault('eject', False)               # 180 s time-decay ejection: test_sol_priority_chop_ejection.py
    prof.setdefault('alt_boost', False)           # SOL/DOGE priority size: test_sol_priority_chop_ejection.py
    prof.setdefault('vfr_gate', False)            # VFR (ATR 15 m / fees) freeze: test_world_quant_economic_engine.py
    prof.setdefault('mr_flow_gate', False)        # MR anti-fade + net RR gates: test_mr_concordance_and_net_rr.py
    prof.setdefault('net_rr_min', 0.0)
    prof.setdefault('super_size', False)          # super-conviction size: test_mr_concordance_and_net_rr.py
    prof.setdefault('chop_scalper', 'off')        # chop micro-scalper / drift acceleration: test_continuous_active_trading.py
    prof.setdefault('drift_accel', False)
    prof.setdefault('observe_only', ())           # BTC observer / alt size floor / fast eject: test_isolate_btc_sol_doge_alpha.py
    prof.setdefault('alt_size_floor', False)
    prof.setdefault('eject_fast', False)
    prof.setdefault('super_cross', 'off')        # 10-08w crossing / dynamic TTL / ratchet 2: test_breakthrough_alpha_execution.py
    prof.setdefault('dyn_ttl', 'off')
    prof.setdefault('ratchet2', False)
    p = replace(eng.PROFILES[mode], symbol=SYM, cooldown_s=0.0, **prof)
    venue = eng.MockVenue(p, quote=lambda s: (feed.bid or feed.price(), feed.ask or feed.price()))
    e = eng.Engine(p, venue, feed, ledger=tmp_path / 'trades.jsonl')
    e.spec = dict(eng.FALLBACK_SPECS[SYM])
    e.started = True
    e.phase = 'SCANNING'
    return e, venue, feed


async def fill_through(e, feed, coro):
    """Run an entry and let the book trade through the resting post-only bid."""
    import asyncio
    task = asyncio.ensure_future(coro)
    await asyncio.sleep(0.15)
    bid = feed.bid
    for _ in range(3):
        feed.on_message(depth(bid - 0.1, bid))                     # our bid is now inside -> traded through
    res = await task
    feed.on_message(depth(bid, bid + 0.1))                          # book back to the entry level
    return res


def book_at(feed, px):
    for _ in range(3):
        feed.on_message(depth(px, round(px + 0.1, 1)))


# MARIN T1 before 2026-10-07p (+0.08 % -> entry +0.03 %); the new +0.10 % -> +0.04 % tier is tested in
# tests/test_world_quant_economic_engine.py. IOC tests opt in to super_edge (off by default: pure maker).
OLD_TIERS = {'microbe_trigger_pct': 0.08, 'microbe_lock_pct': 0.03}
IOC_ON = {'super_edge': True}


async def opened(tmp_path, **prof):
    e, venue, feed = mock_engine(tmp_path, **prof)
    await e.sync()
    d = await fill_through(e, feed, e.tick())
    assert d.allowed and e.pos, (d, list(e.events)[:3])
    await e.sync()
    return e, venue, feed


async def test_entry_is_post_only_gtx_maker_at_best_bid(tmp_path):
    e, venue, feed = await opened(tmp_path)
    p = e.pos
    assert p['side'] == 'BUY' and p["entry"] == 99_800.0 and p['entry_type'] == 'GTX' and e.authorized == 1
    fills = [f for f in venue.fills if f['symbol'] == SYM]
    assert len(fills) == 1 and fills[0]['maker'] and fills[0]['fee'] == pytest.approx(99_800.0 * 0.002 * 2 / 1e4)
    assert {o['type'] for o in e.orders} == {'TP LIMIT', 'STOP_MARKET'}
    assert p['split'] and p['tp_qty'] == pytest.approx(0.001)                 # TP1 takes exactly half
    assert any(r['label'].startswith('LONG OPEN (TP1: +0.50%)') for r in e.agent_rows())


async def test_gtx_not_filled_is_cancelled_without_chasing(tmp_path):
    e, venue, feed = mock_engine(tmp_path)
    await e.sync()
    d = await e.tick()                                           # book never trades through
    assert d.allowed and e.pos is None and not venue.fills
    assert all(o['status'] == 'CANCELED' for o in venue.orders.values())
    assert any('not filled' in ev['message'] for ev in e.events)


def test_gtx_price_joins_touch_or_improves_one_tick(tmp_path):
    e, _, _ = mock_engine(tmp_path)
    assert e.gtx_price('BUY', {'bid': 100.0, 'ask': 100.1}) == 100.0
    assert e.gtx_price('BUY', {'bid': 100.0, 'ask': 100.5}) == pytest.approx(100.4)      # aggressive: ask - 1 tick
    assert e.gtx_price('SELL', {'bid': 100.0, 'ask': 100.5}) == pytest.approx(100.1)     # bid + 1 tick


async def test_micro_be_at_008_then_true_breakeven_at_012_never_turns_into_a_loss(tmp_path):
    e, venue, feed = await opened(tmp_path, soft_exits=False, **OLD_TIERS)
    entry = e.pos['entry']
    book_at(feed, round(entry * 1.0007, 1))                       # +0.07 %: nothing yet
    await e.tick()
    assert e.pos['stop_kind'] == 'SL'
    book_at(feed, round(entry * 1.0009, 1))                       # +0.09 % >= +0.08 %: micro-BE
    await e.tick()
    assert e.pos['stop_kind'] == 'MBE' and e.pos['mbe_locked'] and not e.pos['be_locked']
    assert e.pos['sl'] == pytest.approx(entry * 1.0003, abs=0.2)  # gross +0.03 %: loss capped near fees
    book_at(feed, round(entry * 1.0013, 1))                       # +0.13 % >= +0.12 %
    await e.tick()
    p = e.pos
    be = entry * (1 + (0.02 + 0.05 + 0.01 + 0.02) / 100)
    assert p['be_locked'] and p['stop_kind'] == 'BE' and p['sl'] == pytest.approx(be, abs=0.2)
    stops = [o for o in venue.orders.values() if o['kind'] == 'SL' and o['status'] == 'NEW']
    assert len(stops) == 1 and stops[0]['price'] == p['sl']        # old stop cancelled after the new one
    book_at(feed, round(p['sl'] - 0.1, 1))                        # reversal trades 1 tick through the BE stop
    await e.sync()
    h = e.history[0]
    assert h['reason'] == 'BE' and h['be_locked'] and h['pnl'] > 0
    assert h['gross'] - h['fee'] == pytest.approx(h['pnl']) and h['maker_fills'] == 1 and h['taker_fills'] == 1
    assert e.history and h['fee'] > h['qty'] * h['entry'] * 5 / 1e4          # entry maker fee is inside the window


async def test_breakeven_stop_is_a_stop_market_so_a_gap_can_still_slip(tmp_path):
    """Honest limit: STOP_MARKET fills at the next price. A one-tick cross keeps the BE profit;
    a 0.23 % gap in a single print fills below the stop (exchange reality, not a code path)."""
    e, venue, feed = await opened(tmp_path, soft_exits=False)
    entry = e.pos['entry']
    book_at(feed, round(entry * 1.0013, 1))
    await e.tick()
    book_at(feed, round(entry * 0.999, 1))
    await e.sync()
    assert e.history[0]['reason'] == 'BE' and e.history[0]['pnl'] < 0


async def test_tp1_half_then_trailing_runner(tmp_path):
    e, venue, feed = await opened(tmp_path, soft_exits=False)
    entry = e.pos['entry']
    book_at(feed, round(entry * 1.005, 1))                        # +0.50 %: TP1 (+0.45 %) fills half
    await e.sync()
    await e.tick()
    assert e.pos['tp1_filled'] and abs(e.view['amt']) == pytest.approx(0.001) and e.pos['be_locked']
    book_at(feed, round(entry * 1.008, 1))                        # +0.80 %: trail = peak - 0.20 %
    await e.tick()
    p = e.pos
    assert p['trail_on'] and p['stop_kind'] == 'TRAIL' and p['sl'] == pytest.approx(entry * 1.008 * 0.998, abs=0.3)
    book_at(feed, round(entry * 1.010, 1))                        # new peak ratchets the stop up
    await e.tick()
    assert e.pos['sl'] == pytest.approx(entry * 1.010 * 0.998, abs=0.3)
    book_at(feed, round(entry * 1.0070, 1))                       # pull back through the trail
    await e.sync()
    h = e.history[0]
    assert h['reason'] == 'TP1+TRAIL' and h['pnl'] > 0 and h['tp1'] and h['mfe_pct'] >= 0.99


async def test_hard_stop_and_loss_streak_pause(tmp_path):
    e, venue, feed = await opened(tmp_path, max_consec_losses=1)
    book_at(feed, round(e.pos['sl'] - 5, 1))
    await e.sync()
    h = e.history[0]
    assert h['reason'] == 'SL' and h['pnl'] < 0 and not h['be_locked'] and e.pause_until > time.time()
    assert h['pnl'] >= -(h['qty'] * h['entry'] * (0.20 + 0.08 + 0.01) / 100) - 1e-9   # SL 0.20 % + fees + slip
    assert not e.gates(e.feed.features()).rune[0]


async def test_no_time_stop_by_default(tmp_path):
    e, venue, feed = await opened(tmp_path)
    assert e.p.max_hold_s is None
    e.pos['opened'] -= 3600
    await e.sync()
    assert e.pos is not None


async def test_force_test_trade_uses_gtx_and_demo_only(tmp_path):
    flat = msig.MicroFeed(SYM)
    now = time.time()
    for i in range(40):
        flat.on_message(agg(100_000.0, 0.01, maker=i % 2 == 0), now - 40 + i)
    flat.on_message(depth(100_000.0, 100_000.1, 1, 1), now)
    e, venue, feed = mock_engine(tmp_path, feed=flat)
    await e.sync()
    assert not (await e.tick()).allowed                       # quiet market: swarm stays out
    r = await fill_through(e, flat, e.force_test_trade('BUY'))
    assert r['ok'] and r['entry_type'] == 'GTX' and e.pos['tag'] == 'TEST' and e.pos['hold_s'] is None
    assert venue.fills[0]['maker']
    assert not (await e.force_test_trade('BUY'))['ok']
    assert (await e.close_now())['ok'] and e.pos is None and e.history[0]['reason'] == 'MANUAL'
    live, _, _ = mock_engine(tmp_path, feed=flat, mode='live')
    assert 'DEMO' in (await live.force_test_trade('BUY'))['error']


async def test_rune_blocks_external_position_and_daily_kill(tmp_path):
    e, venue, feed = mock_engine(tmp_path, adopt_external=False)   # demo default re-adopts: test_portfolio_multi_slot
    await venue.market(SYM, 'SELL', '0.001', 'manual')            # user opened by hand on the venue
    await e.sync()
    d = await e.tick()
    assert not d.allowed and 'EXTERNAL' in e.gates(feed.features()).rune[1]
    e2, _, _ = mock_engine(tmp_path)
    e2.day_pnl = -5.0
    assert 'daily loss' in e2.gates(e2.feed.features()).rune[1]


def test_zephr_bootstrap_ready_without_trade_warmup_and_flags_silent_market_socket(tmp_path):
    f = msig.MicroFeed(SYM)
    t0 = time.time()
    f.on_message(depth(100_000.0, 100_000.1), t0)
    e, _, _ = mock_engine(tmp_path, feed=f)
    z = e.gates(f.features(t0 + 1)).zephr
    assert z[0] and 'bootstrap' not in z[1] or 'live' in z[1]       # ready with zero trades
    assert e.cost.view()['round_trip_bps'] == pytest.approx(4.0, abs=0.1)
    f.on_message(depth(100_000.0, 100_000.1), t0 + 16)
    z2 = e.gates(f.features(t0 + 16.5)).zephr
    assert not z2[0] and 'aggTrade silent' in z2[1]


def test_stale_feed_vetoes_and_snapshot_is_json(tmp_path):
    e, _, feed = mock_engine(tmp_path)
    f = feed.features(time.time() + 10)
    assert not e.gates(f).zephr[0] and 'STALE' in e.gates(f).zephr[1]
    snap = e.snapshot()
    assert snap['cost']['maker_bps'] == 2.0 and 'confluence' in (snap['decision'] or {'confluence': 1})
    json.dumps(snap, allow_nan=False, default=str)


def test_ws_routes_follow_binance_split():
    s = msig.streams('BTCUSDT')
    assert s['public'] == 'wss://fstream.binance.com/public/stream?streams=btcusdt@depth10@100ms'
    assert s['market'].startswith('wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade/')
    assert 'markPrice@1s' in s['market'] and 'forceOrder' in s['market'] and 'depth' not in s['market']


# ----------------------------------------------------------------------------- 2-way: SHORT lifecycle, flow exit
def dump(feed, px, usd, sell=True, n=50):
    """Aggressor prints worth `usd` at px (sell=True -> sellers hit the bid)."""
    q = usd / px / n
    for _ in range(n):
        feed.on_message(agg(px, q, maker=sell))


def past_breath(e, s=31.0):
    """Pretend the position is s seconds old (30 s breathing room over)."""
    e.pos['opened'] -= s


async def opened_short(tmp_path, **prof):
    import asyncio
    e, venue, feed = mock_engine(tmp_path, feed=bearish_feed(), **prof)
    await e.sync()
    task = asyncio.ensure_future(e.tick())
    await asyncio.sleep(0.15)
    ask = feed.ask
    for _ in range(3):
        feed.on_message(depth(ask, ask + 0.1, bq=1.0, aq=6.0))       # bid lifts into our resting ask
    d = await task
    for _ in range(3):
        feed.on_message(depth(ask - 0.1, ask, bq=1.0, aq=6.0))
    assert d.allowed and d.side == 'SELL' and e.pos, (d, list(e.events)[:3])
    await e.sync()
    return e, venue, feed


async def test_downtrend_opens_short_at_best_ask_with_mirrored_brackets(tmp_path):
    e, venue, feed = await opened_short(tmp_path)
    p = e.pos
    assert p['side'] == 'SELL' and p['entry'] == pytest.approx(99_800.1) and p['regime'] == 'DOWN'
    assert venue.fills[0]['maker'] and venue.fills[0]['side'] == 'SELL'
    assert p['sl'] > p['entry'] > p['tp']                          # stop above, TP1 below
    assert e.snapshot()['position']['side'] == 'SHORT'
    assert any(r['label'].startswith('SHORT OPEN') for r in e.agent_rows())


async def test_short_micro_be_be_and_tp1_trail_mirror(tmp_path):        # hard (exchange-stop) mode
    e, venue, feed = await opened_short(tmp_path, soft_exits=False, **OLD_TIERS)
    entry = e.pos['entry']
    book_at(feed, round(entry * 0.9991 - 0.1, 1))                  # -0.09 %: short is +0.09 %
    await e.tick()
    assert e.pos['stop_kind'] == 'MBE' and e.pos['sl'] == pytest.approx(entry * 0.9997, abs=0.2)
    book_at(feed, round(entry * 0.9987 - 0.1, 1))                  # +0.13 % for the short
    await e.tick()
    assert e.pos['stop_kind'] == 'BE' and e.pos['sl'] < entry
    book_at(feed, round(entry * 0.9945, 1))                         # TP1 (-0.50 %) fills half
    await e.sync()
    await e.tick()
    assert e.pos['tp1_filled'] and e.view['amt'] == pytest.approx(-0.001)
    book_at(feed, round(entry * 0.9920, 1))                         # +0.80 %: trailing above the low
    await e.tick()
    assert e.pos['trail_on'] and e.pos['sl'] == pytest.approx(entry * 0.9920 * 1.002, abs=0.5)
    book_at(feed, round(e.pos['sl'] + 0.1, 1))
    await e.sync()
    h = e.history[0]
    assert h['side'] == 'SELL' and h['reason'] == 'TP1+TRAIL' and h['pnl'] > 0


async def test_adverse_flow_exit_does_not_wait_for_the_stop(tmp_path):     # hard mode: maker 0.2 s then market
    e, venue, feed = await opened(tmp_path, flow_exit_ttl_s=0.2, soft_exits=False, regime_flip_exit=False)
    entry, sl = e.pos['entry'], e.pos['sl']
    past_breath(e)
    dump(feed, entry - 5, 4_000_000)                               # whales dump $4M after our long (net of pre-entry buys)
    book_at(feed, round(entry - 5, 1))
    await e.tick()
    h = e.history[0]
    assert e.pos is None and h['reason'] == 'FLOW' and h['adverse_usd'] >= 2_000_000
    full_sl_loss = h['qty'] * entry * (0.20 + 0.08) / 100           # SL 0.20 % + maker in + taker out + slip
    assert h['pnl'] > -full_sl_loss and 31 <= h['hold_s'] < 36          # after the 30 s breathing room
    assert any('FLOW EXIT' in ev['message'] for ev in e.events)


async def test_flow_exit_ignores_flow_from_before_entry(tmp_path):
    e, venue, feed = await opened(tmp_path, flow_exit_ttl_s=0.2)
    assert e.adverse_flow(e.pos) <= 0                               # the pre-entry buying is not counted
    await e.tick()
    assert e.pos is not None


async def test_force_test_buy_refused_in_downtrend_short_allowed(tmp_path):
    e, venue, feed = mock_engine(tmp_path, feed=bearish_feed())
    await e.sync()
    r = await e.force_test_trade('BUY')
    assert not r['ok'] and r['regime'] == 'DOWN' and 'CẤM BUY' in r['error'] and not venue.fills
    import asyncio
    task = asyncio.ensure_future(e.force_test_trade('SELL'))
    await asyncio.sleep(0.15)
    ask = feed.ask
    for _ in range(3):
        feed.on_message(depth(ask, ask + 0.1, bq=1.0, aq=6.0))
    r2 = await task
    assert r2['ok'] and r2['side'] == 'SELL' and e.pos['tag'] == 'TEST'


def test_snapshot_exposes_regime(tmp_path):
    import asyncio
    e, _, _ = mock_engine(tmp_path, feed=bearish_feed())
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(e.sync())
    e.reg = msig.regime(e.feed.features())
    snap = e.snapshot()
    assert snap['regime']['name'] == 'DOWN' and snap['regime']['label'] == 'STRONG DOWNTREND (SHORT ONLY)'


# ----------------------------------------------------------------------------- soft maker exits / peg / symbols
def lift(feed, px, sell_side=True):
    """A buyer lifts the ask (or a seller hits the bid): the resting maker exit at px trades."""
    for _ in range(3):
        feed.on_message(depth(px, round(px + 0.1, 1)) if sell_side else depth(round(px - 0.1, 1), px))


def test_trade3_fee_arithmetic_maker_exit_cannot_rescue_13_5_usd():
    """Ledger trade #3: BUY 0.002 @ 85313.5 -> 85327.0, gross +0.027, fee 0.1024, net -0.0754 (FLOW, taker exit).
    Demo fees: maker 2 bps, taker 4 bps. A maker exit saves exactly the taker-maker gap, but the ENTRY maker
    fee still exists: round trip maker = 4 bps of notional = $34.1 of BTC move on 0.002 BTC."""
    q, e_px, x_px, mk, tk = 0.002, 85313.5, 85327.0, 2e-4, 4e-4
    gross = (x_px - e_px) * q
    taker_net = gross - q * e_px * mk - q * x_px * tk
    maker_net = gross - q * e_px * mk - q * x_px * mk
    assert gross == pytest.approx(0.027) and taker_net == pytest.approx(-0.0754, abs=2e-4)
    assert maker_net == pytest.approx(-0.0413, abs=2e-4) and maker_net - taker_net == pytest.approx(0.0341, abs=2e-4)
    breakeven = e_px * 2 * mk / (1 - mk)                             # move where maker-maker net = 0
    assert breakeven == pytest.approx(34.13, abs=0.05)
    assert (40.0 * q - q * e_px * mk - q * (e_px + 40) * mk) > 0       # +$40 with maker exit: net positive
    assert (40.0 * q - q * e_px * mk - q * (e_px + 40) * tk) < 0       # same move, taker exit: still negative


async def btc_long_at(tmp_path, px, **prof):
    e, venue, feed = mock_engine(tmp_path, feed=trend_feed('BUY', px=px), **prof)
    await e.sync()
    d = await fill_through(e, feed, e.tick())
    assert d.allowed and e.pos and e.pos['entry'] == pytest.approx(px)
    await e.sync()
    return e, venue, feed


async def test_flow_exit_is_a_maker_queue_and_beats_trade3_by_the_taker_gap(tmp_path):
    e, venue, feed = await btc_long_at(tmp_path, 85313.5, regime_flip_exit=False)
    feed.on_message(depth(85326.9, 85327.0))
    past_breath(e)
    dump(feed, 85326.9, 4_000_000)                                    # $4M dumped on us after the 30 s
    await e.tick()
    x = e.pos['exiting']
    assert x['reason'] == 'FLOW' and x['px'] == pytest.approx(85327.0)  # SELL rests at the best ask, post-only
    assert e.pos['sl'] == e.pos['sl0']                                # exchange hard SL untouched
    lift(feed, 85327.0)
    await e.sync()
    h = e.history[0]
    assert h['reason'] == 'FLOW' and h['exit_mode'] == 'maker' and h['taker_fills'] == 0 and h['maker_fills'] == 2
    assert h['gross'] == pytest.approx(0.027, abs=1e-6)
    assert h['fee'] == pytest.approx(0.002 * (85313.5 + 85327.0) * 2e-4, abs=1e-6)
    assert h['pnl'] == pytest.approx(-0.0413, abs=2e-4) and h['pnl'] > -0.0754 + 0.03


async def test_soft_breakeven_exit_is_maker_and_net_positive(tmp_path):
    e, venue, feed = await opened(tmp_path, **OLD_TIERS)
    entry, sl0 = e.pos['entry'], e.pos['sl0']
    book_at(feed, round(entry * 1.0009, 1))                          # +0.09 %: T1 micro-lock
    await e.tick()
    assert e.pos['soft_kind'] == 'LOCK' and e.pos['soft_stop'] == pytest.approx(entry * 1.0003, abs=0.2)
    book_at(feed, round(entry * 1.0013, 1))                          # +0.13 %: T2 breakeven (maker in + out + 0.02)
    await e.tick()
    lvl = e.pos['soft_stop']
    assert e.pos['be_locked'] and lvl == pytest.approx(entry * 1.0006, abs=0.2) and e.pos['sl'] == sl0
    book_at(feed, round(lvl - 0.1, 1))                               # bid ticks under the level, ask == level
    await e.tick()
    assert e.pos['exiting']['reason'] == 'SOFT-BE' and e.pos['exiting']['px'] == pytest.approx(lvl, abs=0.11)
    lift(feed, e.pos['exiting']['px'])
    await e.sync()
    h = e.history[0]
    assert h['reason'] == 'SOFT-BE' and h['taker_fills'] == 0 and h['pnl'] > 0


async def test_soft_exit_repegs_down_the_book_then_fills(tmp_path):
    e, venue, feed = await opened(tmp_path, exit_repeg_s=0.0, regime_flip_exit=False)
    entry = e.pos['entry']
    past_breath(e)
    dump(feed, entry - 1, 4_000_000)
    await e.tick()
    first = e.pos['exiting']['px']
    book_at(feed, round(entry - 3.0, 1))                              # touch walks down 3 USD
    await e.tick()
    x = e.pos['exiting']
    assert x['reprices'] == 1 and x['px'] < first
    lift(feed, x['px'])
    await e.sync()
    assert e.history[0]['taker_fills'] == 0 and e.history[0]['reason'] == 'FLOW'


async def test_hard_sl_is_the_only_taker_exit_with_soft_exits(tmp_path):
    e, venue, feed = await opened(tmp_path)
    book_at(feed, round(e.pos['sl0'] - 5, 1))
    await e.sync()
    h = e.history[0]
    assert h['reason'] == 'SL' and h['taker_fills'] == 1 and h['exit_mode'] == 'exchange'


async def test_pegged_entry_follows_the_bid_within_drift_cap(tmp_path):
    import asyncio
    e, venue, feed = mock_engine(tmp_path, gtx_ttl_s=3.0)
    await e.sync()
    task = asyncio.ensure_future(e.tick())
    await asyncio.sleep(0.15)
    bid0 = feed.bid
    for _ in range(3):
        feed.on_message(depth(bid0 + 2.0, bid0 + 2.1, bq=6.0, aq=1.0))   # breakout: book walks up $2
    await asyncio.sleep(0.7)                                            # > peg interval 0.5 s
    for _ in range(3):
        feed.on_message(depth(bid0 + 1.9, bid0 + 2.0, bq=6.0, aq=1.0))   # traded through the re-pegged bid
    d = await task
    assert d.allowed and d.score >= 0.80 and e.pos['entry'] == pytest.approx(bid0 + 2.0)
    assert any(ev['message'].startswith('PEG BUY repriced x1') for ev in e.events)


async def test_peg_never_chases_past_drift_cap(tmp_path):
    import asyncio
    e, venue, feed = mock_engine(tmp_path)
    await e.sync()
    task = asyncio.ensure_future(e.tick())
    await asyncio.sleep(0.15)
    bid0 = feed.bid
    for _ in range(3):
        feed.on_message(depth(bid0 + 80.0, bid0 + 80.1, bq=6.0, aq=1.0))  # +0.08 % > 0.05 % cap
    d = await task
    assert d.allowed and e.pos is None and not venue.fills


def test_micro_price_quotes_inside_wide_spread(tmp_path):
    e, _, _ = mock_engine(tmp_path)
    heavy_bid = {'bid': 100.0, 'ask': 101.0, 'bid_qty': 9.0, 'ask_qty': 1.0}       # micro = 100.9
    assert e.gtx_price('BUY', heavy_bid) == pytest.approx(100.9)                    # aggressive: ask - tick
    e.p = replace(e.p, aggressive_quote=False)
    assert e.gtx_price('BUY', heavy_bid) == pytest.approx(100.8)                    # micro-price - tick
    assert e.gtx_price('SELL', heavy_bid) == pytest.approx(101.0) or e.gtx_price('SELL', heavy_bid) < 101.0
    assert e.gtx_price('BUY', {'bid': 100.0, 'ask': 100.1, 'bid_qty': 9, 'ask_qty': 1}) == 100.0  # 1 tick: join


async def test_symbol_switch_guards_and_auto_size():
    r = eng.Runner('demo', allow_live=False, env={}, serve=False)
    assert r.profile('demo').symbol == 'BTCUSDT' and r.profile('demo').qty == 0.002
    r.symbols['demo'] = 'DOGEUSDT'
    p = r.profile('demo')
    assert p.symbol == 'DOGEUSDT' and p.qty == 0.0
    doge = eng.plan('BUY', 0.17, eng.FALLBACK_SPECS['DOGEUSDT'], p)
    assert doge['ok'] and doge['notional'] < 6 and doge['notional'] * 2 * 2e-4 < 0.003   # ~0.002 U maker round trip
    r.engine = SimpleNamespace(p=SimpleNamespace(symbol='DOGEUSDT'), pos={'x': 1}, busy=False, view={'amt': 1})
    assert not (await r.switch_symbol('ETHUSDT'))['ok']
    assert 'đóng vị thế' in (await r.switch_symbol('SOLUSDT'))['error']
    assert (await r.switch_symbol('DOGEUSDT'))['ok']


# ----------------------------------------------------------------------------- super-edge IOC routing (09:35 case)
def super_feed(usd=7_490_000.0, px=99_800.0):
    f = trend_feed('BUY', px=px)
    now = time.time()
    q = usd / px / 50
    for i in range(50):
        f.on_message(agg(px, q, maker=False), now - 5 + i * 0.05)       # +$7.49M aggressive buying
    for _ in range(5):
        f.on_message(depth(px, px + 0.1, bq=8.0, aq=1.0), now)
    return f


async def test_super_momentum_cvd_749m_jev_96_routes_ioc_and_fills_under_100ms(tmp_path):
    feed = super_feed()
    e, venue, _ = mock_engine(tmp_path, feed=feed, super_ker=0.0, **IOC_ON)     # KER gate of IOC: test_winrate_maximizer
    await e.sync()
    t0 = time.perf_counter()
    d = await e.tick()
    elapsed_ms = (time.perf_counter() - t0) * 1000
    f = feed.features()
    assert f['cvd_60s'] >= 7_000_000 and e.diag['jev'] >= 9.0 and e.diag['consensus'] < 0.85
    assert d.allowed and e.pos and e.pos['entry_type'] == 'IOC' and e.pos['side'] == 'BUY'
    assert elapsed_ms < 100 and e.pos['latency_ms'] < 100
    fill = venue.fills[0]
    assert not fill['maker'] and fill['price'] <= 99_800.1 * (1 + 0.03 / 100) + 1e-6       # bounded IOC slippage
    await e.sync()
    assert {o['type'] for o in e.orders} == {'TP LIMIT', 'STOP_MARKET'}                   # MARIN armed at once
    assert any('IOC TAKER super-edge' in ev['message'] for ev in e.events)


def test_router_thresholds(tmp_path):
    e, _, feed = mock_engine(tmp_path, **IOC_ON)
    dec = ms.evaluate(sig(FLOW=('BUY', 1.0, 3), TREND=('BUY', 1.0, 3)), ms.Gates())[0]
    e.diag = {'consensus': 0.80, 'jev': 9.6}
    hot = {'cvd_10s': 3_100_000.0, 'ker': 0.65}
    assert e.route(dec, hot) == 'ioc'                                     # CVD 10 s >= $3M, KER >= 0.60, JEV >= 9.2
    assert e.route(dec, dict(hot, cvd_10s=2_900_000.0)) == 'gtx'          # CVD 10 s < $3M
    assert e.route(dec, dict(hot, cvd_10s=-3_100_000.0)) == 'gtx'         # flow AGAINST the BUY
    assert e.route(dec, dict(hot, ker=0.55)) == 'gtx'                     # tape not straight enough
    e.diag = {'consensus': 0.86, 'jev': 5.0}
    assert e.route(dec, hot) == 'ioc'                                     # raw consensus >= 85 % instead of JEV
    assert e.route(dec, {'cvd_60s': 9e6}) == 'gtx'                        # consensus alone no longer buys IOC
    e.diag = {'consensus': 0.80, 'jev': 9.1}
    assert e.route(dec, hot) == 'gtx'
    e.p = replace(e.p, super_edge=False)
    e.diag = {'consensus': 0.99, 'jev': 9.9}
    assert e.route(dec, hot) == 'gtx'


async def test_ioc_without_liquidity_inside_slip_expires_and_rescans_in_1s(tmp_path):
    feed = super_feed()
    e, venue, _ = mock_engine(tmp_path, feed=feed, ioc_max_slip_pct=0.0, super_ker=0.0, **IOC_ON)
    await e.sync()
    orig = venue.quote
    venue.quote = lambda s: (orig(s)[0], orig(s)[1] + 50)              # the ask gapped away before our IOC
    d = await e.tick()
    assert d.allowed and e.pos is None and not venue.fills and e.phase == 'SCANNING'
    assert 0 < e.cooldown_until - time.time() <= 1.01


async def test_unfilled_gtx_resets_to_scanning_with_1s_cooldown(tmp_path):
    e, venue, feed = mock_engine(tmp_path, gtx_ttl_s=0.3)
    await e.sync()
    r = await e.execute('BUY', ms.evaluate(sig(FLOW=('BUY', 1.0, 3), TREND=('BUY', 1.0, 3)), ms.Gates())[0], 'SWARM')
    assert not r['ok'] and r['route'] == 'GTX' and e.phase == 'SCANNING' and not e.busy
    assert 0 < e.cooldown_until - time.time() <= 1.01


async def test_ioc_entry_uses_taker_aware_breakeven(tmp_path):
    feed = super_feed()
    e, venue, _ = mock_engine(tmp_path, feed=feed, super_ker=0.0, **IOC_ON)     # KER gate of IOC: test_winrate_maximizer
    await e.sync()
    await e.tick()
    await e.sync()
    entry = e.pos['entry']
    book_at(feed, round(entry * 1.0010, 1))                              # +0.10 %: below the 0.12 % taker trigger
    await e.tick()
    assert not e.pos['be_locked']
    book_at(feed, round(entry * 1.0013, 1))
    await e.tick()
    assert e.pos['be_locked'] and e.pos['soft_stop'] == pytest.approx(entry * (1 + (5 + 2) / 1e4 + 0.0002), abs=0.2)


def test_default_execution_profile_values():
    p = eng.PROFILES['demo']
    assert (p.gtx_ttl_s, p.peg_max_drift_pct, p.peg_min_score, p.unfilled_cooldown_s) == (3.0, 0.15, 0.768, 1.0)
    assert not p.super_edge                                               # 2026-10-07p: pure maker entries
    assert (p.super_consensus, p.super_jev, p.super_cvd_usd, p.ioc_max_slip_pct) == (0.85, 9.2, 3_000_000.0, 0.03)
    assert p.super_ker == 0.60


# ----------------------------------------------------------------------------- velocity / wall / squeeze / tiers / stats
def test_cvd_velocity_surge_needs_pace_and_acceleration():
    base = {'cvd_60s': 0.0, 'cvd_3m': 0.0, 'obi': 0.0}
    hot = msig.signals(dict(base, cvd_vel_10s=80_000.0, cvd_vel_60s=20_000.0))
    assert hot['SURGE']['side'] == 'BUY' and '+80k/s' in hot['SURGE']['why']
    assert msig.signals(dict(base, cvd_vel_10s=40_000.0, cvd_vel_60s=5_000.0))['SURGE'] is None    # < $50k/s
    assert msig.signals(dict(base, cvd_vel_10s=60_000.0, cvd_vel_60s=45_000.0))['SURGE'] is None   # no acceleration
    assert msig.signals(dict(base, cvd_vel_10s=-70_000.0, cvd_vel_60s=-10_000.0))['SURGE']['side'] == 'SELL'


def test_depth_l10_wall_measured_and_blocks_entry_into_opposite_wall():
    f = msig.MicroFeed(SYM)
    f.on_message({'stream': 'btcusdt@depth10@100ms', 'data': {
        'b': [[str(100 - i * 0.1), '3'] for i in range(10)], 'a': [[str(100.1 + i * 0.1), '1'] for i in range(10)]}})
    imb = f.features()['depth10_imb']
    assert imb == pytest.approx(0.5) and msig.signals(f.features())['WALL']['side'] == 'BUY'
    up = sig(FLOW=('BUY', 1.0, 3), TREND=('BUY', 1.0, 3))
    up['WALL'] = {'side': 'BUY', 'strength': 0.5, 'edge_bps': 0.0, 'why': 'L10 wall +0.50'}
    d, _, diag = ms.evaluate(up, ms.Gates())
    assert d.allowed and diag['confluence']['C6_WALL']
    up['WALL'] = {'side': 'SELL', 'strength': 0.5, 'edge_bps': 0.0, 'why': 'L10 wall -0.50'}
    assert ms.evaluate(up, ms.Gates())[0].reason.startswith('WALL against BUY')


def squeeze_feed(px=99_800.0):
    """2 min coiled inside 0.02 %, then $900k of buying breaks the box high."""
    now = time.time()
    f = msig.MicroFeed(SYM)
    for i in range(100):
        f.on_message(agg(px + (i % 3) * 5, 0.01, maker=i % 2 == 0), now - 115 + i)
    for i in range(30):
        f.on_message(agg(px + 40, 0.3, maker=False), now - 10 + i * 0.3)
    for _ in range(5):
        f.on_message(depth(px + 40, px + 40.1, bq=6.0, aq=1.0), now)
    return f


async def test_squeeze_breakout_lifts_consensus_to_super_edge(tmp_path):
    feed = squeeze_feed()
    fx = feed.features()
    s = msig.signals(fx)
    assert fx['box_pct'] <= 0.08 and s['SQUEEZE']['side'] == 'BUY' and s['SURGE']['side'] == 'BUY'
    e, venue, _ = mock_engine(tmp_path, feed=feed, super_ker=0.0, gtx_ttl_s=0.2)
    await e.sync()
    d = await e.tick()
    assert e.diag['consensus'] >= 0.95 and e.diag['confluence']['C5_SQUEEZE']
    # 2026-10-07h strict maker: consensus 100 % alone no longer buys IOC - CVD 10 s is $0.87M < $3M -> pegged GTX
    assert d.allowed and e.route(d, fx) == 'gtx' and not any(not f['maker'] for f in venue.fills)


async def test_four_tier_profit_ladder_and_tight_trail(tmp_path):
    e, venue, feed = await opened(tmp_path, **OLD_TIERS)
    entry = e.pos['entry']
    steps = [(1.0009, 'LOCK', 1.0003), (1.0013, 'BE', 1.0006)]
    for k, kind, lvl in steps:
        book_at(feed, round(entry * k, 1))
        await e.tick()
        assert e.pos['soft_kind'] == kind and e.pos['soft_stop'] == pytest.approx(entry * lvl, abs=0.2)
    book_at(feed, round(entry * 1.0052, 1))                          # T3: TP1 (+0.50 %) maker fills half
    await e.sync()
    await e.tick()
    assert e.pos['tp1_filled']
    book_at(feed, round(entry * 1.0060, 1))                          # T4: trail 0.20 %
    await e.tick()
    assert e.pos['trail_gap'] == 0.20 and e.pos['soft_stop'] == pytest.approx(entry * 1.0060 * 0.998, abs=0.3)
    book_at(feed, round(entry * 1.0090, 1))                          # >= +0.70 %: trail tightens to 0.15 %
    await e.tick()
    assert e.pos['trail_gap'] == 0.15 and e.pos['soft_stop'] == pytest.approx(entry * 1.0090 * 0.9985, abs=0.3)
    lvl = e.pos['soft_stop']
    book_at(feed, round(lvl - 0.1, 1))
    await e.tick()
    lift(feed, e.pos['exiting']['px'])
    await e.sync()
    h = e.history[0]
    assert h['reason'] == 'TP1+SOFT-TRAIL' and h['taker_fills'] == 0 and h['pnl'] > 0
    # the tight tier keeps more of the run than a flat 0.20 % trail on the same path: +0.75 % vs +0.70 %
    assert (1.0090 * 0.9985 - 1) * 100 > (1.0090 * 0.998 - 1) * 100


async def test_demo_venue_book_drives_quotes_and_marin_not_mainnet(tmp_path):
    """09:46 demo log: TP1 filled at +0.35 % on the demo book while the mainnet mid never rose - MFE stayed 0,
    breakeven never armed. Quotes and MARIN now read the venue's own book when the venue has one."""
    e, venue, feed = mock_engine(tmp_path)
    vb = {'bid': 99_830.0, 'ask': 99_830.1, 'bid_qty': 5.0, 'ask_qty': 1.0}

    async def book_ticker(symbol):
        return dict(vb)
    venue.own_book = True
    venue.book_ticker = book_ticker
    await e.refresh_book(0.0)
    assert e.book()['bid'] == 99_830.0 and e.gtx_price('BUY', e.book()) == 99_830.0       # not the feed's 99_800
    venue.quote = lambda s: (vb['bid'], vb['ask'])                                       # fills follow the venue
    await e.sync()
    import asyncio
    task = asyncio.ensure_future(e.tick())
    await asyncio.sleep(0.15)
    vb.update(bid=99_829.9, ask=99_830.0)
    await e.refresh_book(0.0)
    d = await task
    assert d.allowed and e.pos and e.pos['entry'] == pytest.approx(99_830.0)
    await e.sync()
    vb.update(bid=round(99_830.0 * 1.0013, 1), ask=round(99_830.0 * 1.0013 + 0.1, 1))   # venue +0.13 %, feed flat
    await e.refresh_book(0.0)
    await e.tick()
    assert e.pos['mfe_pct'] >= 0.12 and e.pos['be_locked']


def test_win_rate_radar_on_the_real_demo_ledger(tmp_path):
    import shutil
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / 'data' / 'engine_trades.jsonl'
    rows = [json.loads(x) for x in src.read_text(encoding='utf-8').splitlines()[:5]] if src.exists() else []
    if len(rows) < 5:
        pytest.skip('ledger not present')
    e, _, _ = mock_engine(tmp_path)
    s = e.stats(rows)
    assert (s['wins'], s['losses']) == (2, 3) and s['win_rate'] == pytest.approx(0.4)
    assert s['net'] == pytest.approx(0.3258, abs=1e-3) and s['profit_factor'] == pytest.approx(2.278, abs=0.01)
    assert s['avg_win'] == pytest.approx(0.2904, abs=1e-3) and s['avg_loss'] == pytest.approx(-0.0850, abs=1e-3)
    json.dumps(e.stats([]), allow_nan=False)


async def test_order_query_tolerates_binance_2013(tmp_path):
    from live.binance_futures_client import BinanceError
    calls = []

    class C:
        settings = SimpleNamespace(mode='testnet', rest='https://demo-fapi.binance.com', armed=True)

        async def order(self, symbol, cid):
            calls.append(cid)
            raise BinanceError(-2013, 400)
    v = eng.BinanceVenue.__new__(eng.BinanceVenue)
    v.c = C()
    assert (await v.order('BTCUSDT', 'x'))['status'] == 'NOT_FOUND' and len(calls) == 2


# ----------------------------------------------------------------------------- runner / controls
async def test_switch_mode_guards():
    r = eng.Runner('demo', allow_live=False, env={}, serve=False)
    r.engine = SimpleNamespace(pos=None, busy=False, view={'amt': 0})
    assert 'run.bat live' in (await r.switch_mode('live', 'LIVE'))['error']
    r.allow_live = True
    assert 'BINANCE_MAINNET' in (await r.switch_mode('live', 'LIVE'))['error']
    r.env = {'BINANCE_MAINNET_API_KEY': 'k', 'BINANCE_MAINNET_SECRET_KEY': 's'}
    assert 'LIVE' in (await r.switch_mode('live', 'yes'))['error']
    r.engine = SimpleNamespace(pos={'x': 1}, busy=False, view={'amt': 1})
    assert 'đóng vị thế' in (await r.switch_mode('live', 'LIVE'))['error']
    assert not (await r.switch_mode('paper'))['ok']


def test_cli_refuses_live_without_typed_arm(monkeypatch):
    monkeypatch.delenv('GROKTOPUS_ARM_LIVE', raising=False)
    with pytest.raises(SystemExit) as ex:
        eng.main(['--mode', 'live'])
    assert 'LIVE' in str(ex.value)


def test_control_endpoints_json_origin_and_controller():
    from core.web_ui_server import Telemetry, make_server
    calls = []

    class Ctl:
        def force_test_trade(self, body):
            calls.append(body)
            return {'ok': True, 'side': body.get('side')}

        def switch_mode(self, body):
            return {'ok': False, 'error': 'locked'}

        def close_position(self, body):
            return {'ok': True}

    servers = [make_server(Telemetry(), port=0, controller=Ctl()), make_server(Telemetry(), port=0)]
    for s in servers:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        port, port0 = servers[0].server_address[1], servers[1].server_address[1]

        def post(path, body, ctype='application/json', origin=None, p=port):
            c = http.client.HTTPConnection('127.0.0.1', p, timeout=3)
            h = {'Host': '127.0.0.1', 'Content-Type': ctype}
            if origin:
                h['Origin'] = origin
            c.request('POST', path, body=json.dumps(body), headers=h)
            r = c.getresponse()
            return r.status, (json.loads(r.read() or b'{}') if r.status != 403 else None)
        assert post('/api/force_test_trade', {'side': 'BUY'}, origin=f'http://127.0.0.1:{port}') == (200, {'ok': True, 'side': 'BUY'})
        assert post('/api/force_test_trade', {'side': 'BUY'}, ctype='text/plain')[0] == 400
        assert post('/api/force_test_trade', {'side': 'BUY'}, origin='https://evil.example')[0] == 403
        assert post('/api/switch_mode', {'mode': 'live'})[0] == 409
        assert post('/api/close_position', {}, p=port0)[0] == 503
        assert calls == [{'side': 'BUY'}]
    finally:
        for s in servers:
            s.shutdown()
            s.server_close()


def test_dashboard_ships_trade_desk_controls():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / 'web_ui'
    html = (root / 'index.html').read_text(encoding='utf-8')
    for id_ in ("btn-demo", "btn-live", "btn-force", "btn-force-short", "btn-close", 'pos-rows', 'ord-rows', 'hist-rows', 'live-form'):
        assert f'id="{id_}"' in html
    js = (root / 'desk.js').read_text(encoding='utf-8')
    assert "'/api/force_test_trade'" in js and "'/api/switch_mode'" in js and "'Content-Type': 'application/json'" in js
    assert "'/api/switch_symbol'" in js and all(f'data-symbol="{s}"' in html for s in ('BTCUSDT', 'DOGEUSDT', 'SOLUSDT'))
