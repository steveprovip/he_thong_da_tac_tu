"""11-09 micro-capital fill starvation (docs/harness/plan_micro_rotation.md): adaptive GTX TTL with <= 2 smart re-pegs,
hybrid IOC on an extreme burst, 4-coin rotation with a Momentum Quality router, compounding sizer.  Mock venue and
synthetic books only; nothing talks to an exchange and none of it shows the changes make money."""
import asyncio
import json
import re
import time
from dataclasses import replace

import pytest

from config.settings import micro_capital_env
from core import engine as eng
from core import invariants as inv
from core import micro_signals as msig
from core import micro_swarm as ms
from core import toxicity_guard as tg
from core.position_sizer import MicroCapitalSizer
from tests.test_trend_activation_and_timing import bars_trend, data_for, setup as trend_setup
from tests.test_two_mode_engine import depth, mock_engine, sig

DOGE, SOL, XRP, ADA, PEPE = 'DOGEUSDT', 'SOLUSDT', 'XRPUSDT', 'ADAUSDT', '1000PEPEUSDT'
GATES = ms.Gates(cost_bps=6.0)
BUY = ms.evaluate(sig(FLOW=('BUY', 1.0, 20), VWAP=('BUY', 1.0, 20)), GATES)[0]
SELL = ms.evaluate(sig(FLOW=('SELL', 1.0, 20), VWAP=('SELL', 1.0, 20)), GATES)[0]
SPEC = eng.FALLBACK_SPECS
PX = 0.0689                                     # DOGE price of the 18:52 telemetry
TICK = 0.00001


def book(feed, bid, sym=DOGE):
    for _ in range(3):
        feed.on_message(depth(bid, round(bid + TICK, 5), bq=50_000.0, aq=50_000.0, sym=sym))


def rows(tmp_path):
    f = tmp_path / 'engine_orders.jsonl'
    return [json.loads(x) for x in f.read_text(encoding='utf-8').splitlines()] if f.exists() else []


async def doge_micro(tmp_path, wallet=5.0, vpin=0.2, frozen=None, **prof):
    """Micro-active DOGE engine on the mock venue; vpin = what the feed reports; frozen = [quote] to decouple the
    venue book from the signal feed (the demo venue book is not the signal feed)."""
    feed = msig.MicroFeed(DOGE, msig.config_for(DOGE))
    book(feed, PX)
    feed.vpin_now = lambda: vpin
    prof.setdefault('micro_align', False)          # the KER / alignment gate and the noise-floor bracket have their own tests (tests/test_micro_noise.py)
    prof.setdefault('gtx_ttl_s', 0.3)
    prof.setdefault('micro_ttl_s', 1.2)
    prof.setdefault('micro_ttl_max_s', 2.0)
    prof.setdefault('peg_interval_s', 0.05)
    e, venue, _ = mock_engine(tmp_path, feed=feed, mock_balance=wallet, **prof)
    e.p = replace(e.p, symbol=DOGE, qty=0.0)
    e.spec = dict(SPEC[DOGE])
    if frozen is not None:
        frozen[0] = (feed.bid, feed.ask)
        e.venue = eng.MockVenue(e.p, quote=lambda s: frozen[0])
    await e.sync()
    return e, e.venue, feed


async def trade_through(feed, frozen=None, sym=DOGE):
    """Sellers hit through our bid: the resting BUY fills."""
    bid = frozen[0][0] if frozen is not None else feed.bid
    if frozen is not None:
        frozen[0] = (round(bid - TICK, 5), bid)
    for _ in range(3):
        feed.on_message(depth(round(bid - TICK, 5), bid, sym=sym))


# ============================================================================= pillar 1: adaptive TTL
async def test_a_clean_book_extends_the_resting_order_past_3s_and_it_fills(tmp_path):
    e, venue, feed = await doge_micro(tmp_path)
    task = asyncio.ensure_future(e.execute('BUY', BUY, 'SWARM'))
    await asyncio.sleep(0.8)                                              # past the 0.3 s base TTL, inside micro_ttl_s
    await trade_through(feed)
    res = await task
    r = rows(tmp_path)[-1]
    assert res['ok'] and r['outcome'] == 'FILLED' and r['micro_ttl_s'] == 1.2 and r['ttl_cut'] is None


@pytest.mark.parametrize('vpin, reason', [(None, 'VPIN unknown'), (0.70, 'VPIN 0.70 >= 0.65'), (0.65, 'VPIN 0.65 >= 0.65')])
async def test_unknown_or_dirty_vpin_cannot_cancel_inside_the_floor_only_after_it(tmp_path, vpin, reason):
    """11-09b: 'never cancel at 3 s because VPIN is initialising'. The floor here is micro_ttl_s = 1.2 s (10 s in production)."""
    e, venue, feed = await doge_micro(tmp_path, vpin=vpin)
    t0 = time.monotonic()
    task = asyncio.ensure_future(e.execute('BUY', BUY, 'SWARM'))
    await asyncio.sleep(0.8)                                              # 2.7x the 0.3 s base TTL, inside the floor
    assert not task.done() and any(o['status'] == 'NEW' for o in venue.orders.values())
    res = await task
    r = rows(tmp_path)[-1]
    assert not res['ok'] and time.monotonic() - t0 >= 1.15                # it rested the whole floor
    assert r['ttl_cut'] == reason and r['micro_ttl_s'] == 1.2


async def test_a_fill_inside_the_floor_needs_no_vpin_reading(tmp_path):
    """The 19:57 XRP case: VPIN still warming up, the order must be allowed to wait for the queue."""
    e, venue, feed = await doge_micro(tmp_path, vpin=None)
    task = asyncio.ensure_future(e.execute('BUY', BUY, 'SWARM'))
    await asyncio.sleep(0.8)
    await trade_through(feed)
    res = await task
    assert res['ok'] and rows(tmp_path)[-1]['outcome'] == 'FILLED'


async def test_toxic_markouts_do_not_cut_inside_the_floor_but_end_the_extension_after_it(tmp_path):
    e, venue, feed = await doge_micro(tmp_path)
    e.tox_check = lambda side: (tg.PULL, 0, 'own 5 s markouts negative')
    task = asyncio.ensure_future(e.execute('BUY', BUY, 'SWARM'))
    await asyncio.sleep(0.8)
    assert not task.done()
    res = await task
    assert not res['ok'] and rows(tmp_path)[-1]['ttl_cut'].startswith('markout PULL')


async def test_a_spread_wider_than_3_bps_pulls_the_order_even_inside_the_floor(tmp_path):
    e, venue, feed = await doge_micro(tmp_path, peg=False)                # no chasing: the order stays where it was posted
    task = asyncio.ensure_future(e.execute('BUY', BUY, 'SWARM'))
    await asyncio.sleep(0.6)
    for _ in range(3):
        feed.on_message(depth(PX, round(PX + 3 * TICK, 5), bq=50_000.0, aq=50_000.0, sym=DOGE))   # 4.4 bps wide
    t0 = time.monotonic()
    res = await task
    assert not res['ok'] and time.monotonic() - t0 < 0.9 and rows(tmp_path)[-1]['ttl_cut'].startswith('spread 4.4 bps > 3')


def test_the_production_ttl_defaults_are_the_owner_numbers():
    p = eng.PROFILES['demo']
    assert (p.micro_ttl_s, p.micro_ttl_max_s, p.micro_max_spread_bps, p.micro_max_reprices) == (10.0, 15.0, 3.0, 2)


async def test_an_adverse_move_pulls_the_extended_order_at_once(tmp_path):
    q = [None]
    e, venue, feed = await doge_micro(tmp_path, frozen=q)
    task = asyncio.ensure_future(e.execute('BUY', BUY, 'SWARM'))
    await asyncio.sleep(0.6)                                              # extended: clean so far
    book(feed, round(PX * 0.9992, 5))                                     # the signal market drops ~8 bps
    t0 = time.monotonic()
    res = await task
    assert not res['ok'] and time.monotonic() - t0 < 0.9 and rows(tmp_path)[-1]['ttl_cut'].startswith('ADVERSE')


async def test_a_touch_that_leaves_beyond_the_peg_cap_pulls_the_order(tmp_path):
    q = [None]
    e, venue, feed = await doge_micro(tmp_path, frozen=q)
    task = asyncio.ensure_future(e.execute('BUY', BUY, 'SWARM'))
    await asyncio.sleep(0.6)
    book(feed, round(PX * 1.003, 5))                                      # the touch runs +0.30 % (> 0.15 % cap); the
                                                                          # engine reads the signal feed, the venue stays put
    res = await task
    assert not res['ok'] and rows(tmp_path)[-1]['ttl_cut'].startswith('touch left')


async def _walk_and_fill(tmp_path, **prof):
    """The touch walks up 0.029 % at a time (the venue follows the feed, so our rising bid never crosses its ask);
    then price comes back and trades through whatever price our order rests at."""
    e, venue, feed = await doge_micro(tmp_path, gtx_ttl_s=1.5, micro_ttl_s=3.0, micro_ttl_max_s=4.0, **prof)
    task = asyncio.ensure_future(e.execute('BUY', BUY, 'SWARM'))
    await asyncio.sleep(0.2)
    for i in range(4):
        book(feed, round(feed.bid + 2 * TICK, 5))
        await asyncio.sleep(0.25)
    ours = max(float(o['price']) for o in venue.orders.values() if o['kind'] == 'ENTRY' and o['status'] == 'NEW')
    for _ in range(3):
        feed.on_message(depth(round(ours - TICK, 5), ours, sym=DOGE))
    res = await task
    return res, rows(tmp_path)[-1]


async def test_at_most_two_smart_repegs_keep_the_queue_place(tmp_path):
    res, r = await _walk_and_fill(tmp_path)
    assert res['ok'] and r['reprices'] == 2                               # micro_max_reprices
    res2, r2 = await _walk_and_fill(tmp_path / 'ctl', micro_ttl=False)    # micro TTL off: the old unlimited chase
    assert res2['ok'] and r2['reprices'] >= 3


async def test_a_big_wallet_engine_is_not_touched_by_the_adaptive_ttl(tmp_path):
    e, venue, feed = await doge_micro(tmp_path, wallet=5000.0)
    assert not e.micro_active()
    t0 = time.monotonic()
    res = await e.execute('BUY', BUY, 'SWARM')
    r = rows(tmp_path)[-1]
    assert not res['ok'] and time.monotonic() - t0 < 1.0
    assert r['micro_ttl_s'] is None and r['ttl_cut'] is None
    assert any('not filled in 0.3s' in ev['message'] for ev in e.events)    # the configured TTL, as before


async def test_the_unfilled_message_prints_the_time_really_waited(tmp_path):
    e, venue, feed = await doge_micro(tmp_path)
    await e.execute('BUY', BUY, 'SWARM')
    msg = next(ev['message'] for ev in e.events if 'not filled in' in ev['message'])
    waited = float(re.search(r'not filled in ([\d.]+)s', msg).group(1))
    assert 1.8 <= waited <= 2.4 and rows(tmp_path)[-1]['micro_ttl_s'] == 2.0   # 3 s was never the whole story


# ============================================================================= pillar 2: hybrid IOC
def burst_engine(tmp_path, wallet=5.0, **prof):
    prof.setdefault('micro_align', False)          # the KER / alignment gate and the noise-floor bracket have their own tests (tests/test_micro_noise.py)
    e, venue, feed = mock_engine(tmp_path, mock_balance=wallet, **prof)
    e.p = replace(e.p, symbol=DOGE, qty=0.0)
    e.spec = dict(SPEC[DOGE])
    e.account = {'wallet': wallet, 'available': wallet}
    e.diag = {'consensus': 0.802}
    return e


def route(e, side='SELL', ofi=-0.94, **extra):
    xm = {}
    r = e.micro_ioc_route(SELL if side == 'SELL' else BUY, {'ofi_10s': ofi, **extra}, side, 'SWARM', xm)
    return r, xm.get('micro_ioc')


def test_an_extreme_burst_that_pays_for_a_taker_entry_routes_ioc(tmp_path):
    e = burst_engine(tmp_path, micro_ioc='on')
    assert route(e) == ('ioc', 'IOC')                                     # the 18:52 case: 80.2 %, OFI -0.94


@pytest.mark.parametrize('diag, ofi, prof, word', [
    ({'consensus': 0.77}, -0.94, {}, 'raw consensus 0.770 < 0.78'),
    (None, -0.94, {}, 'raw consensus — < 0.78'),
    ({'consensus': 0.9}, -0.69, {}, 'OFI +0.69 < 0.7'),
    ({'consensus': 0.9}, +0.90, {}, 'OFI -0.90 < 0.7'),                    # flow against the SELL
    ({'consensus': 0.9}, -0.94, {'micro_ioc_min_tp_pct': 2.0}, 'TP 1.250% < 2.0%'),
    ({'consensus': 0.9}, -0.94, {'micro_ioc_net_rr_min': 5.0}, 'net RR 1.89 < 5 with a taker entry')])
def test_every_ioc_gate_falls_back_to_post_only_gtx(tmp_path, diag, ofi, prof, word):
    e = burst_engine(tmp_path, micro_ioc='on', **prof)
    e.diag = diag
    assert route(e, ofi=ofi) == ('gtx', 'GTX: ' + word)


def test_a_toxic_tape_keeps_the_entry_maker(tmp_path):
    e = burst_engine(tmp_path, micro_ioc='on')
    e.tox_check = lambda side: (tg.PULL, 0, 'tape toxic')
    assert route(e) == ('gtx', 'GTX: tox PULL: tape toxic')


def test_ioc_needs_the_mode_and_a_micro_account(tmp_path):
    assert route(burst_engine(tmp_path, micro_ioc='off'))[0] == 'gtx'
    assert route(burst_engine(tmp_path / 'b', micro_ioc='on', wallet=5000.0))[0] == 'gtx'   # not a small account
    assert route(burst_engine(tmp_path / 'c', micro_ioc='on', micro_capital='off'))[0] == 'gtx'


def test_shadow_records_the_ioc_it_would_have_sent_and_stays_maker(tmp_path):
    e = burst_engine(tmp_path, micro_ioc='shadow')
    assert route(e) == ('gtx', 'IOC')
    assert [r['outcome'] for r in rows(tmp_path)] == ['SHADOW_IOC']


def test_the_ioc_arithmetic_the_directive_left_out():
    """Taker in 5 + 1 bps, stop 5 + 1 out: loss = SL + 0.12 %, win = TP - 0.08 %.  RR 1.30 needs TP >= 0.43 / 0.50 %."""
    for sl, tp_min in ((0.15, 0.4310), (0.20, 0.4960)):
        assert 1.30 * (sl + 0.12) + 0.08 == pytest.approx(tp_min, abs=1e-3)
    assert (0.25 - 0.08) / (0.15 + 0.12) < 1.30 and 0.27 / (0.17 + 0.27) > 0.6      # 0.25 % alone: break-even win rate 61 %


async def test_an_ioc_entry_is_sent_as_a_bounded_taker_and_sized_into_the_window(tmp_path):
    feed = msig.MicroFeed(DOGE, msig.config_for(DOGE))
    book(feed, PX)
    orig = feed.features
    feed.features = lambda *a, **k: {**orig(*a, **k), 'ofi_10s': -0.94}
    e, venue, _ = mock_engine(tmp_path, feed=feed, mock_balance=5.0, micro_ioc='on', gtx_ttl_s=0.3, micro_align=False)
    e.p = replace(e.p, symbol=DOGE, qty=0.0)
    e.spec = dict(SPEC[DOGE])
    await e.sync()
    e.diag = {'consensus': 0.802}
    res = await e.execute('SELL', SELL, 'SWARM')
    r = rows(tmp_path)[-1]
    assert res['ok'] and r['route'] == 'ioc' and r['micro_ioc'] == 'IOC' and r['outcome'] == 'FILLED'
    assert 5.05 <= r['qty_req'] * PX <= 6.0 and e.pos['entry_type'] == 'IOC'
    assert any(o['kind'] == 'IOC' for o in venue.orders.values()) and not [o for o in venue.orders.values()
                                                                           if o['kind'] == 'MARKET']


def test_micro_ioc_is_an_i5_deviation_demo_announces_and_armed_live_refuses(tmp_path):
    assert all(p.micro_ioc == 'off' for p in eng.PROFILES.values())
    assert inv.check_profile(eng.PROFILES['demo']) == []
    errs = inv.check_profile(replace(eng.PROFILES['demo'], micro_ioc='on'))
    assert len(errs) == 1 and 'micro_ioc' in errs[0]
    assert inv.check_profile(replace(eng.PROFILES['demo'], micro_ioc='shadow')) == []     # shadow sends nothing
    live = replace(eng.PROFILES['live'], micro_ioc='on')
    with pytest.raises(PermissionError, match='INVARIANT'):
        eng.Engine(live, eng.MockVenue(live, quote=lambda s: (100.0, 100.01)), msig.MicroFeed(live.symbol),
                   ledger=tmp_path / 'l.jsonl', allow_live=True)
    demo = replace(eng.PROFILES['demo'], symbol=DOGE, micro_ioc='on')
    e = eng.Engine(demo, eng.MockVenue(demo, quote=lambda s: (100.0, 100.01)), msig.MicroFeed(DOGE),
                   ledger=tmp_path / 'd.jsonl')
    assert any('micro_ioc' in x for x in e.invariant_errors)


# ============================================================================= pillar 3: rotation
def test_the_micro_universe_and_its_lots():
    assert eng.MICRO_UNIVERSE == (DOGE, XRP, ADA, PEPE) and eng.PROFILES['demo'].micro_symbols == eng.MICRO_UNIVERSE
    s = MicroCapitalSizer()
    for sym, px in ((DOGE, 0.0689), (XRP, 2.0), (ADA, 0.45), (PEPE, 0.0075)):
        sp = SPEC[sym]
        q, why = s.qty(px, sp['step'], sp['min_qty'], sp['min_notional'])
        assert why == '' and 5.10 <= q * px <= 6.0, sym
        assert sp['step'] * px <= 0.9, sym                                # one lot is narrower than the 0.9 U window


def test_scan_universe_adds_the_micro_coins_only_when_rotating():
    assert eng.scan_universe(False) == eng.SCAN_SYMBOLS
    rot = eng.scan_universe(True)
    assert rot[:3] == eng.SCAN_SYMBOLS and set(rot) == {'BTCUSDT', SOL, DOGE, XRP, ADA, PEPE} and len(rot) == 6
    assert eng.scan_universe(True, (DOGE, XRP)) == eng.SCAN_SYMBOLS + (XRP,)


def test_runner_scans_the_rotation_only_when_micro_is_forced_and_parks_the_4h_lane():
    r = eng.Runner('demo', False, env={'MICRO_CAPITAL_MODE': 'on'})
    assert len(r.scan_syms()) == 6 and r.profile('demo').trend_lane == 'off'
    plain = eng.Runner('demo', False, env={})
    assert plain.scan_syms() == eng.SCAN_SYMBOLS and plain.profile('demo').trend_lane == eng.PROFILES['demo'].trend_lane
    auto = eng.Runner('demo', False, env={'MICRO_CAPITAL_MODE': 'auto'})
    assert auto.scan_syms() == eng.SCAN_SYMBOLS
    two = eng.Runner('demo', False, env={'MICRO_CAPITAL_MODE': 'on', 'MICRO_CAPITAL_SYMBOLS': 'DOGEUSDT,XRPUSDT'})
    assert two.scan_syms() == eng.SCAN_SYMBOLS + (XRP,) and two.profile('demo').micro_symbols == (DOGE, XRP)


def test_settings_parse_the_new_micro_flags():
    assert micro_capital_env({'MICRO_IOC': ' ON ', 'MICRO_CAPITAL_BANKROLL': '8', 'MICRO_CAPITAL_SYMBOLS': 'dogeusdt, xrpusdt'}) \
        == {'micro_ioc': 'on', 'micro_bankroll': 8.0, 'micro_symbols': (DOGE, XRP)}
    for bad in ({'MICRO_IOC': 'yes'}, {'MICRO_CAPITAL_BANKROLL': '0'}, {'MICRO_CAPITAL_BANKROLL': 'x'},
                {'MICRO_CAPITAL_SYMBOLS': 'BTC'}, {'MICRO_CAPITAL_SYMBOLS': 'DOGEUSDT,DOGEUSDT'},
                {'MICRO_CAPITAL_SYMBOLS': ','}):
        with pytest.raises(ValueError):
            micro_capital_env(bad)


def scanner(tmp_path, wallet=5.0, **prof):
    syms = eng.scan_universe(True)
    feeds = {s: msig.MicroFeed(s, msig.config_for(s)) for s in syms}
    profiles = {s: replace(eng.PROFILES['demo'], symbol=s, qty=eng.SYMBOL_QTY.get(s, 0.0), cooldown_s=0.0,
                           observe_only=(), micro_capital='on', micro_bankroll=wallet, **prof) for s in syms}
    venue = eng.MockVenue(profiles['BTCUSDT'], quote=lambda s: eng._touch(feeds.get(s)))
    es = eng.build_scanner(profiles, venue, feeds, ledger=tmp_path / 'trades.jsonl')
    t = [1_000.0]
    for s, e in es.items():
        e.spec, e.started, e.phase, e.account, e.clock = dict(SPEC[s]), True, 'SCANNING', {'wallet': wallet}, lambda: t[0]
    return es, t


def feats(e, ker, ofi):
    e.features = {'ker': ker, 'ofi_10s': ofi}


def test_btc_and_sol_observe_and_the_four_coins_trade(tmp_path):
    es, _ = scanner(tmp_path)
    assert {s: e.observer for s, e in es.items()} == {'BTCUSDT': True, SOL: True, DOGE: False, XRP: False, ADA: False,
                                                      PEPE: False}


def test_the_router_ranks_ker_times_abs_ofi_every_5_seconds(tmp_path):
    es, t = scanner(tmp_path)
    feats(es[DOGE], 0.5, -0.9), feats(es[XRP], 0.4, 0.5), feats(es[ADA], None, 0.9), feats(es[PEPE], 0.6, 0.0)
    feats(es['BTCUSDT'], 0.9, 0.9)
    sc = es[DOGE].micro_scores()
    assert sc == {DOGE: 0.45, XRP: 0.2, ADA: None, PEPE: 0.0}             # unknown KER = no score, BTC is not ranked
    feats(es[XRP], 0.9, 0.9)
    t[0] += 4.9
    assert es[ADA].micro_scores()[XRP] == 0.2                              # same ranking inside the 5 s
    t[0] += 0.2
    assert es[ADA].micro_scores()[XRP] == pytest.approx(0.81)              # re-ranked, shared by every engine


def test_the_best_ranked_candidate_takes_the_single_slot_and_the_others_yield(tmp_path):
    es, t = scanner(tmp_path)
    feats(es[DOGE], 0.3, 0.5), feats(es[XRP], 0.8, 0.9), feats(es[ADA], 0.2, 0.2), feats(es[PEPE], 0.5, 0.4)
    diag = {'consensus': 0.9}
    q = {s: es[s].quality(BUY, diag) for s in (DOGE, XRP, ADA, PEPE)}
    assert max(q, key=q.get) == XRP and q[XRP] == pytest.approx(0.72, abs=0.01)
    for s in (DOGE, XRP, ADA, PEPE):
        es[s].candidate = (t[0], q[s])
    assert es[DOGE].better_peer(q[DOGE])[0].p.symbol == XRP               # DOGE yields to the more active coin
    assert es[XRP].better_peer(q[XRP]) is None                            # the leader does not
    es[XRP].candidate = None                                              # no authorised entry on XRP: nobody waits for it
    assert es[ADA].better_peer(q[ADA])[0].p.symbol in (PEPE, DOGE)


def test_outside_micro_mode_nobody_yields(tmp_path):
    es, t = scanner(tmp_path)
    for e in es.values():
        e.p = replace(e.p, micro_capital='off')
        e.account = {'wallet': 5000.0}
    es[XRP].candidate = (t[0], 0.99)
    assert es[DOGE].better_peer(0.1) is None                              # multi_slot: one slot per symbol, as before


def test_one_position_in_total_across_the_four_coins(tmp_path):
    es, _ = scanner(tmp_path)
    es[DOGE].pos = {'side': 'BUY'}
    assert es[XRP].slot_block() == 'PORTFOLIO FULL 1/1' and es[PEPE].slot_block() == 'PORTFOLIO FULL 1/1'
    es[DOGE].pos = None
    assert es[XRP].slot_block() is None


async def test_a_non_alt_micro_coin_gets_a_micro_lot_not_the_btc_probe(tmp_path):
    feed = msig.MicroFeed(XRP, msig.config_for(XRP))
    book(feed, 2.0, sym=XRP)
    e, venue, _ = mock_engine(tmp_path, feed=feed, mock_balance=5.0, gtx_ttl_s=0.2, alt_boost=True,
                              btc_probe_qty=0.001, micro_align=False)
    e.p = replace(e.p, symbol=XRP, qty=0.0)
    e.spec = dict(SPEC[XRP])
    await e.sync()
    await e.execute('BUY', BUY, 'SWARM')
    r = [x for x in rows(tmp_path) if x.get('qty_req')][-1]
    assert 5.05 <= r['qty_req'] * 2.0 <= 6.0 and not any('MICRO-PROBE' in ev['message'] for ev in e.events)


async def test_the_4h_trend_lane_makes_no_entry_on_a_micro_account(tmp_path):
    x, e, feed, venue, events, clk, hooks = trend_setup(tmp_path)
    e.p = replace(e.p, micro_capital='on', micro_symbols=(SOL,))
    await x.step(data_for(bars_trend()))
    assert 'SOLUSDT' not in x.state and not venue.fills
    assert any('micro-capital mode -> no 4H trend entry' in m for m in events)
    x2, e2, feed2, venue2, events2, *_ = trend_setup(tmp_path / 'b')       # the same bars on a normal account open
    await x2.step(data_for(bars_trend()))
    assert 'SOLUSDT' in x2.state


# ============================================================================= pillar 4: compounding
def doge_lot(equity, sl=0.20, **kw):
    s = MicroCapitalSizer(**kw)
    sp = SPEC[DOGE]
    cap = s.risk_notional(sl, 0.08)
    return s.qty(PX, sp['step'], sp['min_qty'], sp['min_notional'], equity=equity, leverage=5.0, risk_notional=cap)[0]


def test_the_lot_follows_the_bankroll_instead_of_staying_at_75_doge():
    assert doge_lot(5.0) == 75 and doge_lot(None) == 75                   # 5 U: the venue floor (growth 5.0 < 5.10)
    assert doge_lot(10.0) == 145 and 9.9 < 145 * PX <= 10.0               # 10 U -> 10 U of notional
    assert doge_lot(15.0) == 217
    assert doge_lot(40.0) == 259 and 259 * PX <= 0.05 / 0.0028            # the 0.05 U loss ceiling (17.9 U) binds
    assert doge_lot(10.0, sl=0.50) == 125                                  # DOGE's real SL floor 0.50 %: ceiling 8.6 U


def test_the_directive_formula_as_written_cannot_scale():
    """max(5.10, min(0.85 x E x L, 0.25 x cap)): a constant cap gives a constant; cap = buying power gives 6.25 at E = 5."""
    f = lambda e, cap: max(5.10, min(0.85 * e * 5, 0.25 * cap))
    assert {f(e, 15.0) for e in (5, 10, 20)} == {5.10}
    assert f(5, 5 * 5) == 6.25 > 6.0
    s = MicroCapitalSizer()
    assert s.compound_target(5.0) == 5.0 < s.floor_notional(5.0)          # the implemented target starts under the floor
    assert s.compound_target(10.0) == 10.0 and s.compound_target(0.0) is None and s.compound_target(None) is None


def test_the_ceilings_always_win():
    s = MicroCapitalSizer(growth_frac=1.0)
    sp = SPEC[DOGE]
    q, _ = s.qty(PX, sp['step'], sp['min_qty'], sp['min_notional'], equity=10.0, leverage=5.0)
    assert q * PX <= 0.85 * 10 * 5 + 1e-9                                  # margin_frac: never over 85 % of equity as margin
    q, why = s.qty(PX, sp['step'], sp['min_qty'], sp['min_notional'], equity=10.0, risk_notional=4.0)
    assert q is None and 'loss ceiling' in why
    with pytest.raises(ValueError):
        MicroCapitalSizer(growth_frac=1.5)


async def test_the_funnel_compounds_a_real_small_wallet_and_rehearses_a_bankroll_on_a_big_one(tmp_path):
    e, venue, feed = await doge_micro(tmp_path / 'a', wallet=10.0, gtx_ttl_s=0.2)
    assert e.micro_active() and e.micro_equity() == 10.0 and e.micro_window_hi() == 10.0
    await e.execute('BUY', BUY, 'SWARM')
    q = rows(tmp_path / 'a')[-1]['qty_req']
    assert 100 < q <= 125 and q * PX <= 8.62                              # DOGE's 0.50 % SL floor sets the loss ceiling
    assert q * PX * (0.5 + 0.08) / 100 <= 0.05
    big, venue, feed = await doge_micro(tmp_path / 'b', wallet=5000.0, gtx_ttl_s=0.2, micro_capital='on')
    assert big.micro_equity() == 5.0 and big.micro_window_hi() == 6.0     # a forced run on a 5 000 U demo wallet: 5 U account
    await big.execute('BUY', BUY, 'SWARM')
    assert rows(tmp_path / 'b')[-1]['qty_req'] == 75
    big2, *_ = await doge_micro(tmp_path / 'c', wallet=5000.0, micro_capital='on', micro_bankroll=10.0)
    assert big2.micro_equity() == 10.0


def test_the_margin_guard_follows_the_compounded_window(tmp_path):
    e = burst_engine(tmp_path, wallet=10.0)
    assert e.portfolio_check({'loss_at_sl': 0.04, 'margin': 2.0}) is None            # 20 % of 10 U
    assert e.portfolio_check({'loss_at_sl': 0.04, 'margin': 2.01}).startswith('PORTFOLIO MARGIN 2.01 U > 2.00 U')


async def test_runner_builds_the_six_engine_micro_scanner_without_keys():
    """What `run.bat micro` builds (no API key -> the mock venue here): BTC / SOL observers + the four micro coins,
    every engine on the micro profile with the 4H lane parked."""
    r = eng.Runner('demo', False, env={'MICRO_CAPITAL_MODE': 'on', 'MICRO_IOC': 'on'})
    es = await r.build_multi('demo')
    assert set(es) == set(eng.scan_universe(True)) and len(es) == 6
    assert all(e.p.micro_capital == 'on' and e.p.micro_ioc == 'on' and e.p.trend_lane == 'off' for e in es.values())
    assert len({id(e.risk) for e in es.values()}) == 1                    # one RiskBook: one governor, one slot
    assert sorted(s for s, e in es.items() if e.p.symbol in e.p.micro_symbols) == sorted(eng.MICRO_UNIVERSE)
    assert all(any('micro_ioc' in x for x in e.invariant_errors) for e in es.values())    # I5 announced on every engine
