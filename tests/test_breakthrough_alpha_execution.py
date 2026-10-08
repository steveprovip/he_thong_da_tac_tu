"""10-08w: Super-Alpha crossing fallback, dynamic TTL, second exchange-stop ratchet, venue retries.

Behaviour tests on mock / fake venues only.  The counterfactual on the real attempts (tools/counterfactual_super_alpha.py)
is the economic evidence and it is negative for crossing; nothing here shows profitability."""
import asyncio
import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import engine as eng
from core import micro_signals as msig
from core import micro_swarm as ms
from live.binance_futures_client import BinanceError
from tests.test_multi_asset_scanner import SOL, sol_feed
from tests.test_two_mode_engine import depth, mock_engine, sig

ROOT = Path(__file__).resolve().parents[1]
BUY = ms.evaluate(sig(FLOW=('BUY', 1.0, 20), TREND=('BUY', 1.0, 20)), ms.Gates(cost_bps=4))[0]
STRONG = replace(BUY, score=0.98)


def sol_engine(tmp_path, quote=None, ofi=0.9, **prof):
    feed = sol_feed()
    prof.setdefault('gtx_ttl_s', 1.0)
    prof.setdefault('peg', False)
    e, _, _ = mock_engine(tmp_path, feed=feed, **prof)
    e.p = replace(e.p, symbol=SOL, qty=0.0)
    e.spec = dict(eng.FALLBACK_SPECS[SOL])
    e.sig_cfg = e.feed.cfg = msig.config_for(SOL)
    q = quote or [None]
    venue = eng.MockVenue(e.p, quote=lambda s: q[0] or (feed.bid or feed.price(), feed.ask or feed.price()))
    e.venue = venue
    e.diag = {'consensus': 0.98, 'edge_bps': 35.0, 'jev': 0.0, 'gates_ok': True}
    hold = {'ofi': ofi}
    orig = e.feed.features
    e.feed.features = lambda now=None: {**orig(now if now is not None else time.time()), 'ofi_10s': hold['ofi']}
    return e, venue, feed, hold


def rows(tmp_path):
    p = tmp_path / 'engine_orders.jsonl'
    return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines()] if p.exists() else []


def entry_orders(venue):
    return [o for o in venue.orders.values() if o['kind'] in ('ENTRY', 'IOC')]


# ============================================================================ defaults / two-mode architecture
def test_defaults_demo_shadow_live_off():
    d, l = eng.PROFILES['demo'], eng.PROFILES['live']
    assert d.super_cross == 'shadow' and d.dyn_ttl == 'shadow' and d.ratchet2 is True
    assert l.super_cross == 'off' and l.dyn_ttl == 'off' and l.ratchet2 is False
    assert (d.super_cross_after_s, d.super_cross_score, d.super_cross_ofi, d.super_cross_edge_bps,
            d.super_cross_slip_bps, d.super_cross_rr_min) == (2.0, 0.95, 0.85, 30.0, 3.0, 1.50)
    assert d.super_cross_symbols == ('SOLUSDT', 'DOGEUSDT') and d.dyn_ttl_max_s <= 20.0
    assert d.super_edge is False                                  # no blanket IOC route


# ============================================================================ 1. crossing gate
async def test_cross_check_passes_with_every_condition(tmp_path):
    e, venue, feed, _ = sol_engine(tmp_path)
    await e.sync()
    base = e.bracket_plan('SWARM', e.feed.features())['profile']
    pl = eng.plan('BUY', feed.bid, e.spec, base, None, qty=0.40)
    why, info = await e.cross_check(STRONG, 'BUY', pl, base)
    assert why is None and info['rr_ioc'] >= 1.50
    assert info['limit'] <= info['touch'] * (1 + 3.0 / 1e4) + 1e-9          # inside the 3 bps cap


@pytest.mark.parametrize('case,expect', [
    ('vote', 'vote'), ('ofi', 'OFI'), ('edge', 'edge'), ('symbol', 'not a crossing symbol'), ('toxic', 'VPIN'),
    ('rr', 'taker RR')])
async def test_cross_check_refuses(tmp_path, case, expect):
    e, venue, feed, hold = sol_engine(tmp_path)
    await e.sync()
    d = STRONG
    if case == 'vote':
        d = replace(BUY, score=0.94)
        e.diag['consensus'] = 0.94
    elif case == 'ofi':
        hold['ofi'] = 0.5
    elif case == 'edge':
        e.diag['edge_bps'] = 20.0
    elif case == 'symbol':
        e.p = replace(e.p, super_cross_symbols=('DOGEUSDT',))
    elif case == 'toxic':
        e.toxic_until, e.toxic_side = e.clock() + 30, 'SELL'
    elif case == 'rr':
        e.p = replace(e.p, super_cross_rr_min=5.0)
    base = e.bracket_plan('SWARM', e.feed.features())['profile']
    pl = eng.plan('BUY', feed.bid, e.spec, base, None, qty=0.40)
    why, _ = await e.cross_check(d, 'BUY', pl, base)
    assert why and expect in why


# ============================================================================ 1. crossing in execute
async def test_unfilled_gtx_crosses_the_full_quantity_after_the_fallback(tmp_path):
    e, venue, feed, _ = sol_engine(tmp_path, super_cross='on', super_cross_after_s=0.3)
    await e.sync()
    res = await e.execute('BUY', STRONG, 'SWARM')
    r = rows(tmp_path)[-1]
    assert res['ok'] and e.pos and e.pos['entry_type'] == 'IOC' and r['outcome'] == 'FILLED' and r['crossed'] > 0
    ask = feed.ask
    assert e.pos['entry'] <= ask * (1 + 3.0 / 1e4) + 1e-9                    # never beyond the slip cap
    assert e.pos['entry_fee_bps'] == pytest.approx(e.p.taker_bps + e.p.slip_bps)
    assert any(ev['message'].startswith('SUPER-ALPHA CROSS') for ev in e.events)


async def test_partial_gtx_then_cross_takes_only_the_remainder(tmp_path):
    class Partial(eng.MockVenue):
        async def cancel(self, symbol, cid):
            o = self.orders.get(cid)
            if o and o['kind'] == 'ENTRY' and o['status'] == 'NEW':
                o['status'], o['part'] = 'CANCELED', round(o['qty'] / 2, 2)
                self._fill(symbol, o['side'], o['part'], o['price'], self.cfg.maker_bps)
                return
            await super().cancel(symbol, cid)

        async def order(self, symbol, cid):
            o = self.orders.get(cid, {})
            if o.get('part'):
                return {'status': 'CANCELED', 'filled': o['part'], 'avg': o['price']}
            return await super().order(symbol, cid)

    e, _, feed, _ = sol_engine(tmp_path, super_cross='on', super_cross_after_s=0.3)
    venue = Partial(e.p, quote=lambda s: (feed.bid, feed.ask))
    e.venue = venue
    await e.sync()
    res = await e.execute('BUY', STRONG, 'SWARM')
    gtx = next(o for o in venue.orders.values() if o['kind'] == 'ENTRY')
    ioc = [o for o in venue.orders.values() if o['kind'] == 'IOC']
    assert res['ok'] and len(ioc) == 1 and ioc[0]['qty'] == pytest.approx(gtx['qty'] - gtx['part'])
    assert e.pos['qty'] == pytest.approx(gtx['qty']) and e.pos['entry_type'] == 'GTX+IOC'
    assert e.p.maker_bps < e.pos['entry_fee_bps'] < e.p.taker_bps + e.p.slip_bps


async def test_shadow_logs_the_would_be_cross_and_sends_nothing(tmp_path):
    e, venue, feed, _ = sol_engine(tmp_path, super_cross='shadow', super_cross_after_s=0.3)
    await e.sync()
    res = await e.execute('BUY', STRONG, 'SWARM')
    out = [r['outcome'] for r in rows(tmp_path)]
    assert not res['ok'] and e.pos is None and 'SHADOW_CROSS' in out and out[-1] == 'UNFILLED'
    assert not [o for o in venue.orders.values() if o['kind'] == 'IOC']
    sx = next(r for r in rows(tmp_path) if r['outcome'] == 'SHADOW_CROSS')
    assert sx['limit'] and sx['rr_ioc'] >= 1.5 and e.funnel.counters()['shadow_cross'] == 1


async def test_cap_exceeded_means_no_fill_and_never_a_market_order(tmp_path):
    q = [None]
    e, venue, feed, _ = sol_engine(tmp_path, quote=q, super_cross='on', super_cross_after_s=0.3)
    await e.sync()

    class Jump(eng.MockVenue):                     # the venue ask jumps 10 bps between check and IOC
        async def ioc(self, symbol, side, qty, price, cid):
            q[0] = (feed.bid, feed.ask * 1.001)
            return await super().ioc(symbol, side, qty, price, cid)
    e.venue = Jump(e.p, quote=lambda s: q[0] or (feed.bid, feed.ask))
    res = await e.execute('BUY', STRONG, 'SWARM')
    assert not res['ok'] and e.pos is None
    assert all(o['kind'] in ('ENTRY', 'IOC') for o in e.venue.orders.values())      # no MARKET


async def test_signal_gone_at_fallback_time_no_cross(tmp_path):
    e, venue, feed, hold = sol_engine(tmp_path, super_cross='on', super_cross_after_s=0.3)
    await e.sync()
    hold['ofi'] = -0.2                                               # flow turned by the time GTX is stale
    res = await e.execute('BUY', STRONG, 'SWARM')
    assert not res['ok'] and not [o for o in venue.orders.values() if o['kind'] == 'IOC']
    assert rows(tmp_path)[-1]['cross_check'].startswith('OFI')


@pytest.mark.parametrize('path', ['MR', 'SCALP'])
async def test_mr_and_scalp_never_cross(tmp_path, path):
    e, venue, feed, _ = sol_engine(tmp_path, super_cross='on', super_cross_after_s=0.2, gtx_ttl_s=0.6)
    await e.sync()
    await e.execute('BUY', STRONG, path)
    assert not [o for o in venue.orders.values() if o['kind'] == 'IOC']


# ============================================================================ 2. dynamic TTL
async def test_dynamic_ttl_keeps_the_order_while_the_signal_holds_and_fills(tmp_path):
    e, venue, feed, _ = sol_engine(tmp_path, dyn_ttl='on', gtx_ttl_s=0.4, dyn_ttl_max_s=3.0)
    await e.sync()
    task = asyncio.ensure_future(e.execute('BUY', STRONG, 'SWARM'))
    await asyncio.sleep(1.0)                                          # past the 0.4 s base TTL
    bid = feed.bid
    for _ in range(3):
        feed.on_message(depth(round(bid - 0.01, 2), bid, sym=SOL))  # sellers trade through our bid
    res = await task
    r = rows(tmp_path)[-1]
    assert res['ok'] and r['outcome'] == 'FILLED' and r['dyn_ttl_s'] and r['dyn_ttl_s'] > 0.4


async def test_dynamic_ttl_is_finite(tmp_path):
    e, venue, feed, _ = sol_engine(tmp_path, dyn_ttl='on', gtx_ttl_s=0.3, dyn_ttl_max_s=1.2)
    await e.sync()
    t0 = time.monotonic()
    res = await e.execute('BUY', STRONG, 'SWARM')
    took = time.monotonic() - t0
    assert not res['ok'] and 1.0 < took < 2.5
    assert all(o['status'] != 'NEW' for o in entry_orders(venue))


async def test_dynamic_ttl_cancels_at_once_on_adverse_move(tmp_path):
    q = [None]
    e, venue, feed, _ = sol_engine(tmp_path, quote=q, dyn_ttl='on', gtx_ttl_s=0.3, dyn_ttl_max_s=5.0)
    q[0] = (feed.bid, feed.ask)                                      # venue book frozen (demo != signal feed)
    await e.sync()
    task = asyncio.ensure_future(e.execute('BUY', STRONG, 'SWARM'))
    await asyncio.sleep(0.6)
    m = feed.price()
    for _ in range(3):                                               # signal market drops ~8 bps
        feed.on_message(depth(round(m * 0.9992, 2), round(m * 0.9992 + 0.01, 2), sym=SOL))
    t0 = time.monotonic()
    res = await task
    r = rows(tmp_path)[-1]
    assert not res['ok'] and time.monotonic() - t0 < 1.0 and r['dyn_cut'].startswith(('ADVERSE', 'OFI'))


async def test_dynamic_ttl_needs_the_flow_to_persist(tmp_path):
    e, venue, feed, hold = sol_engine(tmp_path, dyn_ttl='on', gtx_ttl_s=0.3, dyn_ttl_max_s=5.0, ofi=0.2)
    await e.sync()
    t0 = time.monotonic()
    await e.execute('BUY', STRONG, 'SWARM')
    assert time.monotonic() - t0 < 1.2 and rows(tmp_path)[-1]['dyn_cut'].startswith('OFI')


async def test_dynamic_ttl_shadow_logs_and_keeps_the_3s_cancel(tmp_path):
    e, venue, feed, _ = sol_engine(tmp_path, dyn_ttl='shadow', gtx_ttl_s=0.3, dyn_ttl_max_s=5.0)
    await e.sync()
    t0 = time.monotonic()
    await e.execute('BUY', STRONG, 'SWARM')
    assert time.monotonic() - t0 < 1.2
    assert [r['outcome'] for r in rows(tmp_path)] == ['SHADOW_DYN', 'UNFILLED']


# ============================================================================ 3. ratchet tier 2
async def long_at(tmp_path, entry_fee_bps=None, **prof):
    from tests.test_isolate_btc_sol_doge_alpha import btc_long_at
    e, venue, feed = await btc_long_at(tmp_path, 84_000.0, **prof)
    if entry_fee_bps is not None:
        e.pos['entry_fee_bps'] = entry_fee_bps
    return e, venue, feed


@pytest.mark.parametrize('fee,lock1,lock2', [(None, 0.08, 0.13), (6.0, 0.12, 0.17)])
async def test_ratchet_tiers_are_fee_true(tmp_path, fee, lock1, lock2):
    e, venue, feed = await long_at(tmp_path, entry_fee_bps=fee, ratchet2=True)
    p = e.pos
    entry = p['entry']
    p['mfe_pct'] = 0.16
    await e.ratchet_stop(p, 1, entry * 1.0016, 0.16)
    assert p['ratchet'] is True and p['sl'] == pytest.approx(eng.round_tick(entry * (1 + lock1 / 100), 0.1, 'up'))
    p['mfe_pct'] = 0.26
    await e.ratchet_stop(p, 1, entry * 1.0026, 0.26)
    assert p['ratchet2'] is True and p['sl'] == pytest.approx(eng.round_tick(entry * (1 + lock2 / 100), 0.1, 'up'))
    net_bps = (p['sl'] / entry - 1) * 1e4 - (fee or 2.0) - 5.0 - 1.0
    assert net_bps >= 4.9                                             # stop-out AT the stop price is net >= +0.05 %


async def test_ratchet2_off_keeps_one_ratchet(tmp_path):
    e, venue, feed = await long_at(tmp_path, ratchet2=False)
    p = e.pos
    p['mfe_pct'] = 0.16
    await e.ratchet_stop(p, 1, p['entry'] * 1.0016, 0.16)
    sl1 = p['sl']
    p['mfe_pct'] = 0.40
    await e.ratchet_stop(p, 1, p['entry'] * 1.004, 0.40)
    assert p['sl'] == sl1 and not p.get('ratchet2')


async def test_ratchet2_never_crosses_the_market(tmp_path):
    e, venue, feed = await long_at(tmp_path, ratchet2=True)
    p = e.pos
    p['mfe_pct'], p['ratchet'] = 0.30, True
    sl0 = p['sl']
    await e.ratchet_stop(p, 1, p['entry'] * 1.0005, 0.05)              # price already fell back under the target
    assert p['sl'] == sl0 and not p.get('ratchet2')


# ============================================================================ 4. venue retries (-1008 / 503)
class FakeClient:
    def __init__(self, script):
        self.settings = SimpleNamespace(rest='https://demo-fapi.binance.com', mode='testnet')
        self.script, self.sent, self.queries, self.cancels, self.book = script, [], [], [], {}

    async def request(self, method, path, params=None, signed=False, weight=1, key=False):
        self.sent.append(dict(params or {}))
        act = self.script.pop(0) if self.script else 'ok'
        if isinstance(act, BinanceError):
            if getattr(act, 'lands', False):
                self.book[params['newClientOrderId']] = {'status': 'NEW', 'executedQty': '0'}
            raise act
        if params.get('timeInForce') == 'IOC':
            self.book[params['newClientOrderId']] = {'status': 'FILLED', 'executedQty': params['quantity'],
                                                     'avgPrice': params['price']}
            return self.book[params['newClientOrderId']]
        self.book[params['newClientOrderId']] = {'status': 'NEW', 'executedQty': '0'}
        return self.book[params['newClientOrderId']]

    async def order(self, symbol, cid):
        self.queries.append(cid)
        if cid in self.book:
            return {**self.book[cid], 'avgPrice': self.book[cid].get('avgPrice', '0')}
        raise BinanceError(-2013, 400, False)

    async def cancel(self, symbol, cid):
        self.cancels.append(cid)
        act = self.script.pop(0) if self.script else 'ok'
        if isinstance(act, BinanceError):
            raise act
        return {}


def venue_with(script):
    v = eng.BinanceVenue(FakeClient(script))
    v.retry_base_s = 0.001
    return v


def err(code, status=503, uncertain=True, lands=False):
    e = BinanceError(code, status, uncertain)
    e.lands = lands
    return e


async def test_1008_throttle_is_retried_with_the_same_client_id():
    v = venue_with([err(-1008), err(-1008)])
    await v.limit_gtx('SOLUSDT', 'BUY', '0.40', '115.00', 'gka-1')
    assert len(v.c.sent) == 3 and {s['newClientOrderId'] for s in v.c.sent} == {'gka-1'}


async def test_unknown_outcome_is_queried_before_any_resend():
    v = venue_with([err('TRANSPORT', 0, True, lands=True)])
    await v.limit_gtx('SOLUSDT', 'BUY', '0.40', '115.00', 'gka-2')
    assert len(v.c.sent) == 1 and v.c.queries == ['gka-2']          # it landed: no second order


async def test_unknown_outcome_not_found_resends_the_same_id():
    v = venue_with([err('TRANSPORT', 0, True, lands=False)])
    await v.limit_gtx('SOLUSDT', 'BUY', '0.40', '115.00', 'gka-3')
    assert len(v.c.sent) == 2 and v.c.sent[1]['newClientOrderId'] == 'gka-3'


async def test_ioc_unknown_outcome_is_never_resent():
    v = venue_with([err('TRANSPORT', 0, True, lands=False)])
    with pytest.raises(BinanceError):
        await v.ioc('SOLUSDT', 'BUY', '0.40', '115.00', 'gxa-1')
    assert len(v.c.sent) == 1                                          # a filled IOC is not open: no double fill


async def test_ioc_throttled_is_resent():
    v = venue_with([err(-1008)])
    avg, filled = await v.ioc('SOLUSDT', 'BUY', '0.40', '115.00', 'gxa-2')
    assert filled == pytest.approx(0.40) and len(v.c.sent) == 2


async def test_post_only_reject_is_not_retried():
    v = venue_with([err(-5022, 400, False)])
    with pytest.raises(eng.PostOnlyReject):
        await v.limit_gtx('SOLUSDT', 'BUY', '0.40', '115.00', 'gka-4')
    assert len(v.c.sent) == 1


async def test_retries_are_bounded():
    v = venue_with([err(-1008)] * 10)
    with pytest.raises(BinanceError):
        await v.limit_gtx('SOLUSDT', 'BUY', '0.40', '115.00', 'gka-5')
    assert len(v.c.sent) == v.retries + 1


async def test_cancel_retries_a_503():
    v = venue_with([err('HTTP_ERROR', 503, True), 'ok'])
    await v.cancel('SOLUSDT', 'gka-6')
    assert len(v.c.cancels) == 2


async def test_orphan_cancel_failure_is_retried_on_the_next_sync(tmp_path):
    e, venue, feed = mock_engine(tmp_path)
    await e.sync()

    calls = {'n': 0}
    orig = venue.cancel

    async def flaky(symbol, cid):
        calls['n'] += 1
        if calls['n'] == 1:
            raise RuntimeError('503')
        await orig(symbol, cid)
    venue.cancel = flaky
    await venue.limit_gtx(eng.PROFILES['demo'].symbol, 'BUY', '0.002', str(feed.bid), 'gka-7')
    await e.cancel_orphan_entry({'cid_live': 'gka-7'})
    assert 'gka-7' in e.orphans
    await e.sync()
    assert 'gka-7' not in e.orphans and venue.orders['gka-7']['status'] == 'CANCELED'


# ============================================================================ counterfactual tool
def test_counterfactual_fill_rules():
    sys.path.insert(0, str(ROOT / 'tools'))
    import counterfactual_super_alpha as c
    tr = [(100.0, 115.00, True), (100.5, 115.01, False), (101.0, 114.99, True), (400.0, 115.50, False)]
    assert c.rest_fill(tr, 'BUY', 115.00, 99.0, 103.0) == (101.0, 115.00)        # printed through our bid
    assert c.rest_fill(tr, 'BUY', 114.98, 99.0, 103.0) is None
    t, px = c.taker_fill(tr, 'BUY', 100.0, 115.00)                                 # buyer-taker print at 115.01
    assert t == 100.5 and px == pytest.approx(115.01 * 1.0001)
    assert c.taker_fill(tr, 'BUY', 100.0, 114.90) is None                         # beyond the 3 bps cap
    net, why = c.bracket(tr, 'BUY', 100.5, 115.0, 6.0, 'SOLUSDT')
    assert why == 'TIME'
