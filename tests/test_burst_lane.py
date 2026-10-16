"""10-10 lane 2: sub-minute volatility-burst scalper (core/burst_lane.py) and its aggTrades replay
(tools/burst_scalper_replay.py).  Synthetic prints, MockVenue, temp files; no network, no real ledger."""
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

from core import burst_lane as bl  # noqa: E402
from core import engine as eng  # noqa: E402
from core import micro_signals as msig  # noqa: E402
import burst_scalper_replay as br  # noqa: E402

T0 = 1_791_000_000.0 + 3600.0           # 1 h after a 4H close: outside the trend quiet window


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def setup(tmp_path, mode='shadow', verdict=None, **kw):
    p = replace(eng.PROFILES['demo'], symbol='SOLUSDT', qty=eng.SYMBOL_QTY.get('SOLUSDT', 0.0), burst_lane=mode,
                micro_standalone=False, trend_lane='active', **kw)
    feed = msig.MicroFeed('SOLUSDT', msig.config_for('SOLUSDT'))
    venue = eng.MockVenue(p, quote=lambda s: eng._touch(feed))
    e = eng.Engine(p, venue, feed, None, ledger=tmp_path / 'micro.jsonl')
    e.spec, e.started, e.account = dict(eng.FALLBACK_SPECS['SOLUSDT']), True, {'wallet': 5000.0}
    events = []
    e.event = events.append
    clk = Clock(T0)
    tmp_path.mkdir(parents=True, exist_ok=True)
    vpath = tmp_path / 'verdict.json'
    if verdict:
        vpath.write_text(json.dumps({'SOLUSDT': {'verdict': verdict}}))
    lane = bl.BurstLane(e, tmp_path, clock=clk, verdict_path=vpath)
    return lane, e, feed, venue, events, clk


def book(feed, bid, ask, t):
    feed.on_message({'stream': 'solusdt@depth10@100ms', 'data': {'b': [[str(bid), '50']], 'a': [[str(ask), '50']]}},
                    now=t)


def trade(feed, px, t, sell_aggressor, q='1'):
    feed.on_message({'stream': 'solusdt@aggTrade', 'data': {'p': str(px), 'q': q, 'm': sell_aggressor}}, now=t)


def warm(feed, clk, secs=70, px=100.0):
    """70 quiet seconds of prints at a fixed price."""
    for k in range(secs):
        t = T0 - secs + k
        book(feed, px - 0.01, px, t)
        trade(feed, px, t + 0.5, k % 2 == 0)


def spike_up(feed, clk, px=100.3):
    """A +30 bps second (sigma_1m 30 bps -> burst, MR -> SELL at the ask)."""
    book(feed, px - 0.01, px, T0)
    trade(feed, px, T0 + 0.2, False)
    clk.t = T0 + 1.0


# ============================================================================ detector
def test_detector_sigma_burst_and_quiet_tape():
    d = bl.BurstDetector()
    for k in range(70):
        d.on_print(T0 - 70 + k, 100.0, 1.0, k % 2 == 0)
    q = d.state(T0)
    assert not q['burst'] and q['sig1m'] == pytest.approx(0.0)
    d.on_print(T0, 100.3, 1.0, False)
    s = d.state(T0 + 1.0)
    assert s['burst'] and 'sigma' in s['why'] and s['sig1m'] == pytest.approx(29.96, abs=0.1)
    assert s['move10'] > 0 and s['cvd10'] > 0
    st = d.state(T0 + 1.0, ofi=-0.9)
    assert st['ofi_l10'] == -0.9 and 'ofi' not in st['why']           # reported, not a trigger (replay used TFI)
    d2 = bl.BurstDetector(sig1m_bps=1e9)
    for k in range(25):
        d2.on_print(T0 - 5 + k * 0.2, 100.0, 1.0, False)               # 25 buyer-aggressor prints in 5 s
    assert d2.state(T0 + 1.0)['tfi'] == pytest.approx(1.0) and 'tfi' in d2.state(T0 + 1.0)['why']


def test_detector_volume_surge_needs_21_minutes():
    d = bl.BurstDetector(sig1m_bps=1e9)
    for k in range(1300):
        d.on_print(T0 - 1300 + k, 100.0, 1.0 if k < 1240 else 5.0, False)
    s = d.state(T0)
    assert s['surge'] == pytest.approx(5.0, rel=0.05) and s['burst'] and s['why'] == 'surge'
    d2 = bl.BurstDetector(sig1m_bps=1e9)
    for k in range(100):
        d2.on_print(T0 - 100 + k, 100.0, 50.0, False)
    assert d2.state(T0)['surge'] is None                 # no 20-min base -> UNKNOWN, never a burst


# ============================================================================ shadow lane
async def test_shadow_mr_fade_take_profit_writes_shadow_ledger(tmp_path):
    lane, e, feed, venue, events, clk = setup(tmp_path)
    warm(feed, clk)
    spike_up(feed, clk)
    await lane.step()
    o = lane.orders[0]
    assert o['side'] < 0 and o['px'] == pytest.approx(100.3) and o['kind'] == 'E' and lane.last['burst']
    trade(feed, 100.32, T0 + 1.5, False)                 # a buyer lifts through our 100.30 offer -> filled
    clk.t = T0 + 2.0
    await lane.step()
    assert lane.pos['side'] < 0 and lane.pos['entry'] == pytest.approx(100.3)
    tp = lane.orders[0]['px']
    assert tp == pytest.approx(100.3 * (1 - 0.003), abs=0.011)
    trade(feed, tp - 0.02, T0 + 3.0, True)                 # seller hits through our TP bid
    clk.t = T0 + 3.5
    await lane.step()
    rows = [json.loads(x) for x in (tmp_path / 'micro_trades_shadow.jsonl').read_text().splitlines()]
    assert rows[-1]['exit_how'] == 'TP' and rows[-1]['mode'] == 'shadow' and rows[-1]['net_bps'] > 20
    assert not (tmp_path / 'micro_trades.jsonl').exists() and not venue.orders     # shadow never calls the venue


async def test_shadow_stop_loss_pays_taker(tmp_path):
    lane, e, feed, venue, events, clk = setup(tmp_path)
    warm(feed, clk)
    spike_up(feed, clk)
    await lane.step()
    trade(feed, 100.32, T0 + 1.5, False)
    clk.t = T0 + 2.0
    await lane.step()
    book(feed, 100.63, 100.64, T0 + 2.5)
    trade(feed, 100.64, T0 + 2.6, False)                   # +34 bps against the short: beyond the 30 bps stop
    clk.t = T0 + 3.0
    await lane.step()
    row = json.loads((tmp_path / 'micro_trades_shadow.jsonl').read_text().splitlines()[-1])
    assert row['exit_how'] == 'SL' and row['net_bps'] < -30 and lane.pos is None


async def test_shadow_time_stop_maker_then_taker(tmp_path):
    lane, e, feed, venue, events, clk = setup(tmp_path, burst_time_exit='taker')     # the replayed policy
    warm(feed, clk)
    spike_up(feed, clk)
    await lane.step()
    trade(feed, 100.32, T0 + 1.5, False)
    clk.t = T0 + 2.0
    await lane.step()
    clk.t = T0 + 63.0
    await lane.step()                                      # 60 s: TP cancelled, maker exit at our touch
    assert lane.exit_phase == 'maker' and any(o['kind'] == 'X' for o in lane.orders)
    clk.t = T0 + 74.0
    await lane.step()
    row = json.loads((tmp_path / 'micro_trades_shadow.jsonl').read_text().splitlines()[-1])
    assert row['exit_how'] == 'TIME-TAKER'


async def test_no_entry_while_trend_owns_symbol_in_4h_window_or_after_breaker(tmp_path):
    lane, e, feed, venue, events, clk = setup(tmp_path)
    warm(feed, clk)
    spike_up(feed, clk)
    e.trend_owned = True
    await lane.step()
    assert not lane.orders
    e.trend_owned = False
    lane.day_pnl = -10.5
    lane.day = __import__('time').strftime('%Y-%m-%d', __import__('time').localtime(clk.t))
    await lane.step()
    assert not lane.orders
    lane.day_pnl = 0.0
    clk.t = (clk.t // 14400 + 1) * 14400 + 60.0            # 1 min after a 4H close
    assert 'trend lane' in lane._free(clk.t)


# ============================================================================ active gate
def test_active_refused_without_eligible_verdict(tmp_path):
    lane, e, *_ , events, clk = setup(tmp_path, mode='active')
    assert lane.mode == 'shadow' and any('refused' in m and 'UNKNOWN' in m for m in events)
    lane2, *_ , ev2, _ = setup(tmp_path / 'b', mode='active', verdict='SHADOW')
    assert lane2.mode == 'shadow' and any('SHADOW' in m for m in ev2)
    lane3, *_ = setup(tmp_path / 'c', mode='active', verdict='ACTIVE-ELIGIBLE')
    assert lane3.mode == 'active' and lane3.ledger.name == 'micro_trades.jsonl'


async def test_active_posts_real_gtx_on_the_demo_venue(tmp_path):
    lane, e, feed, venue, events, clk = setup(tmp_path, mode='active', verdict='ACTIVE-ELIGIBLE')
    warm(feed, clk)
    spike_up(feed, clk)
    await lane.step()
    cids = [c for c in venue.orders if c.startswith('gB-')]
    assert cids and venue.orders[cids[0]]['side'] == 'SELL' and venue.orders[cids[0]]['status'] == 'NEW'
    assert venue.orders[cids[0]]['qty'] * 100.3 <= 75.0 + 1e-6


def test_live_refuses_active(tmp_path):
    lane, e, *_ , events, clk = setup(tmp_path, mode='active', verdict='ACTIVE-ELIGIBLE')
    e.p = replace(e.p, mode='live')
    assert lane._mode('active', tmp_path / 'verdict.json') == 'shadow'


def test_feed_print_hooks_receive_aggtrades():
    feed = msig.MicroFeed('SOLUSDT', msig.config_for('SOLUSDT'))
    got = []
    feed.print_hooks.append(lambda *a: got.append(a))
    trade(feed, 100.0, T0, True, q='2')
    assert got == [(T0, 100.0, 2.0, True)]


@pytest.mark.production_profile
def test_profiles_burst_shadow_on_demo_off_on_live():
    assert eng.PROFILES['demo'].burst_lane == 'shadow' and eng.PROFILES['live'].burst_lane == 'off'
    v = json.loads((ROOT / 'config' / 'burst_verdict.json').read_text())
    assert all(x['verdict'] == 'SHADOW' for x in v.values())               # replay 10-10: no eligible symbol


# ============================================================================ replay helpers
def synth_day(px_path, sides, dt=0.5, t0=1_790_000_000.0):
    t = t0 + np.arange(len(px_path)) * dt
    return np.array(t), np.array(px_path, float), np.ones(len(px_path)), np.array(sides, np.int8)


def test_replay_features_flag_a_volatility_burst():
    n = 400
    px = [100.0] * 200 + [100.0 * (1.002 if k % 2 else 0.998) for k in range(200)]
    t, p, q, b = synth_day(px, [k % 2 for k in range(n)])
    f = br.features(t, p, q, b)
    assert not f['burst'][90] and f['burst'][150:].any()
    assert np.nanmax(f['sig1m'][110:]) > 15


def test_replay_touch_and_through_fill_rules():
    assert br._hit(1, 100.0, 99.99, 1, 'through') and not br._hit(1, 100.0, 100.0, 1, 'through')
    assert br._hit(1, 100.0, 100.0, 1, 'touch') and not br._hit(1, 100.0, 99.9, 0, 'touch')
    assert br._hit(-1, 100.0, 100.01, 0, 'through') and not br._hit(-1, 100.0, 100.01, 1, 'through')
    bid, ask = br.touch_arrays(np.array([100.0, 100.01, 99.99]), np.array([1, 0, 1]), 0.01)
    assert list(bid) == [100.0, 100.0, 99.99] and list(ask) == [100.01, 100.01, 100.01]


def test_replay_simulate_takes_profit_on_a_fade():
    # quiet 70 s, +40 bps spike (burst, MR -> SELL at the ask), fill through, then -40 bps
    t, px, bm = [], [], []
    t0 = 1_790_000_000.0
    for k in range(140):
        t.append(t0 + k * 0.5)
        px.append(100.0 if k % 2 else 100.01)
        bm.append(k % 2)
    seq = [(100.4, 0), (100.4, 0), (100.42, 0), (100.42, 0), (100.3, 1), (100.0, 1), (99.9, 1), (99.8, 1)]
    for j, (p_, s_) in enumerate(seq):
        t.append(t0 + 70 + 0.6 * j)
        px.append(p_)
        bm.append(s_)
    t, px, bm = np.array(t), np.array(px), np.array(bm, np.int8)
    bid, ask = br.touch_arrays(px, bm, 0.01)
    day = {'t': t, 'px': px, 'bm': bm, 'bid': bid, 'ask': ask, 'tick': 0.01,
           'f': br.features(t, px, np.ones(len(t)), bm)}
    tr = br.simulate(day, 'MR', 30.0, 'through', (2.0, 5.0))
    assert tr and tr[0]['side'] == -1 and tr[0]['exit'] == 'TP' and tr[0]['net_bps'] > 20


# ============================================================================ fee survey (task 3)
def test_fee_survey_marks_zero_maker_only_from_the_signed_endpoint():
    import io
    import fee_survey as fs

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    seen = []

    def opener(req, timeout):
        url = req.full_url
        seen.append((url, dict(req.header_items())))
        if 'exchangeInfo' in url:
            body = {'symbols': [{'symbol': 'SOLUSDC', 'contractType': 'PERPETUAL', 'status': 'TRADING', 'marginAsset': 'USDC'},
                                {'symbol': 'SOLUSDT', 'contractType': 'PERPETUAL', 'status': 'TRADING', 'marginAsset': 'USDT'}]}
        elif 'bookTicker' in url:
            body = [{'symbol': 'SOLUSDC', 'bidPrice': '100.00', 'askPrice': '100.02'}]
        elif '24hr' in url:
            body = [{'symbol': 'SOLUSDC', 'quoteVolume': '5e7'}]
        else:
            assert 'signature=' in url
            body = {'makerCommissionRate': '0.0000', 'takerCommissionRate': '0.0004'}
        return R(json.dumps(body).encode())
    rows = fs.survey('USDC', None, 'k', 's', opener=opener)
    assert [r['symbol'] for r in rows] == ['SOLUSDC'] and rows[0]['maker_bps'] == 0.0 and rows[0]['taker_bps'] == 4.0
    assert rows[0]['mm_candidate'] and rows[0]['spread_bps'] == pytest.approx(2.0, rel=1e-3)
    assert any(h.get('X-mbx-apikey') == 'k' for u, h in seen if 'commissionRate' in u)
    nokey = fs.survey('USDC', None, opener=opener)
    assert nokey[0]['maker_bps'] is None and not nokey[0]['mm_candidate'] and 'UNKNOWN' in nokey[0]['fee_source']
