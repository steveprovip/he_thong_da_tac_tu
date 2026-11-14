"""2026-11-07 Pillar 1: funding-carry lane (normalization, replay-parity entry, dual-leg execution, risk, ledger)."""
import io
import json

import numpy as np
import pytest

from research import carry_lane as cl
from core import funding_executor as fx
from core.venues.base import PostOnlyRejected
from core.venues.paper import PaperPerpVenue
from tools import funding_spread_replay as fsr

H8 = 8 * cl.HOUR_MS


# ------------------------------------------------------------------ normalization
def test_rates_are_normalized_by_their_own_interval():
    assert cl.per_hour_bps(0.0001, 8) == pytest.approx(0.125)
    assert cl.per_hour_bps(0.0001, 4) == pytest.approx(0.25)
    assert cl.per_hour_bps(0.0001, None) is None and cl.per_hour_bps(float('nan'), 8) is None
    info = [{'symbol': 'TAOUSDT', 'fundingIntervalHours': 4}, {'symbol': 'BADUSDT', 'fundingIntervalHours': 'x'}]
    assert cl.bn_interval_hours('TAOUSDT', info) == 4
    assert cl.bn_interval_hours('BTCUSDT', info) == 8                     # absent = Binance default
    assert cl.bn_interval_hours('BADUSDT', info) is None
    assert cl.bn_interval_hours('BTCUSDT', None) is None                  # the call failed: UNKNOWN
    assert cl.interval_from_prints(np.arange(10) * 4 * cl.HOUR_MS) == 4
    assert cl.interval_from_prints([0, 1]) is None


def test_realtime_apr_matches_the_shadow_telemetry_scale():
    q = cl.CarryQuote('LINK', 0.0001, 8, 0.00005, 20.0, 20.01)          # BN 0.125 bps/h, HL 0.5 bps/h
    v, why = cl.realtime_apr(q)
    assert why == '' and v['short'] == 'HL' and v['long'] == 'BN'
    assert v['spread_bps_per_h'] == pytest.approx(0.375)
    assert v['gross_apr_pct'] == pytest.approx(0.375 * 8760 / 100)
    assert v['net_apr_pct'] == pytest.approx(v['gross_apr_pct'] - 15.0 / 28 * 365 / 100)
    fxq = fx.FundingQuote('LINK', 0.0001, 0.00005, 20.0, 20.01)
    c, _ = fx.evaluate(fxq)
    assert v['gross_apr_pct'] == pytest.approx(c.spread_8h_bps * 3 * 365 / 100)   # carry_view apr_pct
    assert cl.realtime_apr(cl.CarryQuote('TAO', 0.0001, None, 0.00005, 1.0, 1.0)) == (
        None, 'UNKNOWN funding rate or interval')


BN = [{'symbol': 'LINKUSDT', 'markPrice': '20.000', 'lastFundingRate': '0.0001'},
      {'symbol': 'TAOUSDT', 'markPrice': '400.0', 'lastFundingRate': '0.0001'}]
HL = [{'universe': [{'name': 'LINK'}, {'name': 'TAO'}]},
      [{'funding': '0.0000500', 'markPx': '20.010'}, {'funding': '0.0000100', 'markPx': '400.2'}]]
INFO = [{'symbol': 'TAOUSDT', 'fundingIntervalHours': 4}]


def test_quotes_from_scales_4h_binance_symbols_to_8h():
    legacy = {q.coin: q for q in fx.quotes_from(BN, HL, ('LINK', 'TAO'))}
    fixed = {q.coin: q for q in fx.quotes_from(BN, HL, ('LINK', 'TAO'), bn_funding_info=INFO)}
    assert legacy['TAO'].bn_rate_8h == pytest.approx(0.0001)              # the old reading: half the true rate
    assert fixed['TAO'].bn_rate_8h == pytest.approx(0.0002)
    assert fixed['LINK'].bn_rate_8h == legacy['LINK'].bn_rate_8h == pytest.approx(0.0001)
    bad = {q.coin: q for q in fx.quotes_from(BN, HL, ('TAO',), bn_funding_info=[{'symbol': 'TAOUSDT',
                                                                                 'fundingIntervalHours': 0}])}
    assert bad['TAO'].bn_rate_8h is None and fx.evaluate(bad['TAO'])[1] == 'UNKNOWN rate/price'
    # direction flips once TAO is read correctly: HL 0.8 bps/8h vs BN 1 (legacy) or 2 (fixed) bps/8h
    assert fx.evaluate(fixed['TAO'])[0].spread_8h_bps == pytest.approx(2.0 - 0.8)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_quotes_reads_funding_info_and_refuses_a_bad_payload():
    calls = []

    def opener(payloads):
        def op(req, timeout=None):
            calls.append(req.full_url)
            return FakeResponse(json.dumps(payloads[len(calls) - 1]).encode())
        return op
    qs = {q.coin: q for q in fx.fetch_quotes(('LINK', 'TAO'), opener=opener([BN, INFO, HL]))}
    assert calls == [fx.BN_PREMIUM_URL, fx.BN_FUNDING_INFO_URL, fx.HL_INFO_URL]
    assert qs['TAO'].bn_rate_8h == pytest.approx(0.0002)
    calls.clear()
    with pytest.raises(ValueError, match='fundingInfo'):
        fx.fetch_quotes(('TAO',), opener=opener([BN, {'code': -1}, HL]))


# ------------------------------------------------------------------ entry rule
def prints(start_ms, end_ms, step_ms, bps):
    t = np.arange(start_ms + step_ms, end_ms + 1, step_ms, dtype=np.int64)
    return t, np.full(t.size, float(bps))


def test_entry_signal_needs_the_replay_threshold_and_full_coverage():
    T = 100 * H8
    bn = prints(T - 10 * cl.DAY_MS, T, H8, 1.0)                   # 3 bps/day
    strong = cl.entry_signal(bn, prints(T - 10 * cl.DAY_MS, T, cl.HOUR_MS, 0.5), T)
    assert strong.enter and strong.short == 'HL'
    assert strong.spread_bps == pytest.approx(36.0 - 9.0) and strong.expected_bps == pytest.approx(27 / 3 * 28)
    assert cl.CarryRule().needed_bps == 30.0                       # max(2 x 15, 15 + 8.5)
    weak = cl.entry_signal(bn, prints(T - 10 * cl.DAY_MS, T, cl.HOUR_MS, 0.13), T)
    assert not weak.enter and weak.reason.startswith('OBSERVER')
    ht, hr = prints(T - 10 * cl.DAY_MS, T, cl.HOUR_MS, 0.5)
    keep = (ht < T - 40 * cl.HOUR_MS) | (ht > T - 36 * cl.HOUR_MS)  # a 4 h hole in the HL prints
    holed = cl.entry_signal(bn, (ht[keep], hr[keep]), T)
    assert not holed.enter and holed.reason.startswith('UNKNOWN')
    assert cl.entry_signal(None, (ht, hr), T).reason.startswith('UNKNOWN')


def _cache_ok(coins):
    return all((fsr.CACHE / f'{p}_{c}USDT.json').exists() and (fsr.CACHE / f'{q}_{fsr.HL_NAME.get(c, c)}.json').exists()
               for c in coins for p, q in (('bnf', 'hlf'), ('bnk', 'hlk')))


@pytest.mark.skipif(not _cache_ok(('BTC', 'TAO', 'WLD')), reason='data/funding_cache not present (git-ignored)')
def test_lane_enters_exactly_where_the_validated_replay_entered():
    for coin in ('BTC', 'TAO', 'WLD'):                             # TAO funds every 4 h on Binance
        d = fsr.load(coin, False)
        series = {'bn': d['bn'], 'hl': d['hl'], 'px_bn': d['px_bn'], 'px_other': d['px_hl']}
        want = [(int(round(t['t'] * 1000)), 'BN' if t['legs'][0] == 'bn_perp' else 'HL')
                for t in fsr.simulate(series, 'XV', coin, 28, 2.0, 'HEDGE')]
        got, (bt, _) = [], series['bn']
        T = fsr.ms(fsr.START) + fsr.LOOKBACK_MS
        T, H = T - T % H8 + H8, 28 * cl.DAY_MS
        while T + H <= int(bt[-1]):
            sig = cl.entry_signal(series['bn'], series['hl'], T)
            px = [fsr.price_at(*series[k], x) for k in ('px_bn', 'px_other') for x in (T, T + H)]
            if (not sig.enter or None in px or not cl.covered(bt, T, T + H, H8 + 1)
                    or not cl.covered(series['hl'][0], T, T + H, cl.HOUR_MS)):
                T += H8
                continue
            got.append((T, sig.short))
            T += H
        assert got == want and len(want) > 5, coin


# ------------------------------------------------------------------ kill switch, guard, ledger
def test_kill_switch_halts_at_15pct_drawdown_and_stays_halted():
    k = cl.KillSwitch()
    assert k.check(100.0)[0] and k.check(110.0)[0] and k.check(94.0)[0]   # 14.5 % from 110
    ok, why = k.check(93.5)
    assert not ok and 'drawdown' in why and k.halted
    assert not k.check(200.0)[0]                                       # sticky until a human resets it
    u = cl.KillSwitch()
    assert u.check(None) == (False, 'equity UNKNOWN') and u.halted is None


def legs(mr_bn=0.1, mr_hl=0.1, dist_bn=50.0, dist_hl=50.0):
    view = lambda mr, dist: ({'margin_ratio': mr}, {'mark': 100.0, 'liq': None if dist is None else 100.0 + dist})
    return {'BN': view(mr_bn, dist_bn), 'HL': view(mr_hl, dist_hl)}


def test_liquidation_guard_acts_on_the_pair_and_fails_closed():
    g = cl.LiquidationGuard()
    assert g.assess(legs(), True)[0] == cl.OK
    assert g.assess(legs(mr_hl=0.40), False)[0] == cl.BLOCK_ENTRY
    assert g.assess(legs(mr_bn=0.65), True)[0] == cl.DERISK
    assert g.assess(legs(dist_hl=10.0), True)[0] == cl.DERISK
    assert g.assess(legs(mr_bn=0.65, dist_hl=5.0), True)[0] == cl.CLOSE
    assert g.assess(legs(), True, leverage=3.0)[0] == cl.BLOCK_ENTRY
    assert g.assess({}, False)[0] == cl.BLOCK_ENTRY
    u = cl.LiquidationGuard()
    actions = [u.assess(legs(dist_bn=None), True)[0] for _ in range(3)]
    assert actions == [cl.BLOCK_ENTRY, cl.BLOCK_ENTRY, cl.DERISK]
    assert u.assess(legs(), True)[0] == cl.OK and u.unknown_streak == 0
    with pytest.raises(ValueError):
        cl.GuardPolicy(derisk_margin_ratio=0.9)


def test_funding_ledger_matches_prints_and_counts_missing_as_unknown():
    led = cl.FundingLedger()
    assert led.expect('HL', 'LINK', 3_600_000, 0.00005, -10.0, 20.0) == pytest.approx(0.01)   # short receives
    assert led.expect('BN', 'LINK', 28_800_000, 0.0001, 10.0, 20.0) == pytest.approx(-0.02)   # long pays
    led.expect('HL', 'LINK', 7_200_000, 0.00005, -10.0, 20.0)
    led.receive('HL', 'LINK', 3_600_500, 0.0100)
    led.receive('BN', 'LINK', 28_800_000, -0.0300)
    led.receive('HL', 'ETH', 3_600_000, 0.5)
    rep = led.reconcile()
    status = [r['status'] for r in rep['rows']]
    assert status == ['MATCHED', 'MISMATCH', 'MISSING', 'UNEXPECTED']
    assert rep['missing'] == 1 and rep['mismatch'] == 1 and rep['unexpected'] == 1
    assert rep['received_usdt'] == pytest.approx(0.01 - 0.03 + 0.5)


# ------------------------------------------------------------------ paper venue
def test_paper_venue_post_only_reduce_only_funding_and_liquidation():
    v = PaperPerpVenue('BN', 2.0, 5.0, cash_usdt=100.0, mmr=0.005)
    v.set_book('X', 99.9, 100.1)
    import asyncio
    with pytest.raises(PostOnlyRejected):
        asyncio.run(v.place_post_only('X', 'BUY', 1.0, 100.1))
    r = asyncio.run(v.place_post_only('X', 'SELL', 1.0, 100.1))
    assert v.fill_resting(r['order_id']) == 1.0 and v.positions['X']['amt'] == -1.0
    assert v.settle_funding('X', 0.0001, 0) == pytest.approx(100.0 * 0.0001)    # short receives
    pos = asyncio.run(v.position_view('X'))
    cash = 100.0 - v.fees + v.funding
    assert pos['liq'] == pytest.approx((cash + 100.1) / (1 + 0.005))
    with pytest.raises(Exception):
        asyncio.run(v.place_ioc_limit('X', 'SELL', 1.0, 99.0, reduce_only=True))  # would add to the short


# ------------------------------------------------------------------ dual-leg executor
class Clock:
    def __init__(self):
        self.t = 0.0
        self.events = []                         # (time, fn): run once the clock passes time

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.t += s
        for ev in [e for e in self.events if e[0] <= self.t]:
            self.events.remove(ev)
            ev[1]()


def pair(bn_cash=1000.0, hl_cash=1000.0):
    bn = PaperPerpVenue('BN', 2.0, 5.0, bn_cash)
    hl = PaperPerpVenue('HL', 1.5, 4.5, hl_cash)
    bn.set_book('LINKUSDT', 19.99, 20.01, 500.0, 500.0)
    hl.set_book('LINK', 19.995, 20.015, 500.0, 500.0)
    return bn, hl


def executor(bn, hl, clock, kill=None, policy=cl.ExecPolicy()):
    async def equity():
        return bn.equity() + hl.equity()
    return cl.DualLegExecutor(bn, hl, kill or cl.KillSwitch(), equity, policy, clock, clock.sleep)


async def _open_full():
    bn, hl = pair()
    clock = Clock()
    ex = executor(bn, hl, clock)
    clock.events.append((0.5, lambda: bn.fill_resting(next(iter(bn.orders)))))
    res = await ex.run('LINKUSDT', 'LINK', 'SELL', 10.0)
    return bn, hl, res


@pytest.mark.asyncio
async def test_maker_fill_is_hedged_with_an_ioc_and_the_pair_is_delta_neutral():
    bn, hl, res = await _open_full()
    assert res.state == 'HEDGED' and res.maker_filled == res.hedge_filled == 10.0
    assert bn.positions['LINKUSDT']['amt'] == -10.0 and hl.positions['LINK']['amt'] == 10.0
    kinds = [c[0] for c in bn.calls][:1] + [c[0] for c in hl.calls][:1]
    assert kinds == ['post_only', 'ioc']
    assert bn.calls[0][4] == 20.01                                   # SELL joins the ask: never crosses
    assert hl.calls[0][4] == pytest.approx(20.015 * (1 + 3 / 1e4))  # IOC limit = touch + 3 bps


@pytest.mark.asyncio
async def test_partial_fills_are_hedged_per_increment_and_the_rest_is_cancelled():
    bn, hl = pair()
    clock = Clock()
    ex = executor(bn, hl, clock, policy=cl.ExecPolicy(maker_timeout_s=5.0))
    oid = lambda: next(iter(bn.orders))                             # noqa: E731
    clock.events += [(1.0, lambda: bn.fill_resting(oid(), 3.0)), (2.0, lambda: bn.fill_resting(oid(), 2.0))]
    res = await ex.run('LINKUSDT', 'LINK', 'SELL', 10.0)
    assert res.state == 'HEDGED' and res.maker_filled == res.hedge_filled == 5.0
    assert res.naked_qty_max == 3.0 and bn.orders[oid()]['status'] == 'PARTIALLY_FILLED_CANCELED'
    assert [c[3] for c in hl.calls if c[0] == 'ioc'] == [3.0, 2.0]


@pytest.mark.asyncio
async def test_starved_hedge_widens_then_unwinds_the_maker_fill():
    bn, hl = pair()
    hl.ioc_fill_fraction = 0.0
    clock = Clock()
    ex = executor(bn, hl, clock)
    clock.events.append((0.5, lambda: bn.fill_resting(next(iter(bn.orders)))))
    res = await ex.run('LINKUSDT', 'LINK', 'SELL', 10.0)
    limits = [c[4] for c in hl.calls if c[0] == 'ioc']
    assert len(limits) == 4 and limits == sorted(limits)            # 3, 6, 12, 12 bps over the touch
    assert limits[-1] == pytest.approx(20.015 * (1 + 12 / 1e4))
    assert res.state == 'UNWOUND' and res.unwound == 10.0 and bn.positions['LINKUSDT']['amt'] == 0.0
    assert hl.positions.get('LINK', {'amt': 0.0})['amt'] == 0.0


@pytest.mark.asyncio
async def test_unhedgeable_and_unwindable_fill_is_broken_and_halts_the_lane():
    bn, hl = pair()
    hl.ioc_fill_fraction = bn.ioc_fill_fraction = 0.0
    clock = Clock()
    kill = cl.KillSwitch()
    ex = executor(bn, hl, clock, kill)
    clock.events.append((0.5, lambda: bn.fill_resting(next(iter(bn.orders)))))
    res = await ex.run('LINKUSDT', 'LINK', 'SELL', 10.0)
    assert res.state == 'BROKEN' and kill.halted.startswith('BROKEN_HEDGE')
    again = await ex.run('LINKUSDT', 'LINK', 'SELL', 10.0)
    assert again.state == 'REFUSED' and len([c for c in bn.calls if c[0] == 'post_only']) == 1


@pytest.mark.asyncio
async def test_kill_switch_is_checked_before_every_order():
    bn, hl = pair()
    kill = cl.KillSwitch()
    kill.halt('manual')
    res = await executor(bn, hl, Clock(), kill).run('LINKUSDT', 'LINK', 'SELL', 10.0)
    assert res.state == 'REFUSED' and bn.calls == [] and hl.calls == []
    # tripping between the maker fill and the hedge: the hedge (risk-adding) is refused, the unwind is not
    bn, hl = pair()
    clock, kill, n = Clock(), cl.KillSwitch(), {'calls': 0}

    async def equity():
        n['calls'] += 1
        return 2000.0 if n['calls'] == 1 else 1500.0                # -25 % by the hedge's check
    ex = cl.DualLegExecutor(bn, hl, kill, equity, cl.ExecPolicy(), clock, clock.sleep)
    clock.events.append((0.5, lambda: bn.fill_resting(next(iter(bn.orders)))))
    res = await ex.run('LINKUSDT', 'LINK', 'SELL', 10.0)
    assert res.state == 'UNWOUND' and hl.calls == [] and bn.positions['LINKUSDT']['amt'] == 0.0


@pytest.mark.asyncio
async def test_rejected_post_only_and_closing_a_pair():
    bn, hl = pair()

    async def reject(*a, **k):
        raise PostOnlyRejected('crossed')
    bn.place_post_only = reject
    assert (await executor(bn, hl, Clock()).run('LINKUSDT', 'LINK', 'SELL', 10.0)).state == 'REJECTED'
    bn, hl, _ = await _open_full()
    clock = Clock()
    ex = executor(bn, hl, clock)
    clock.events.append((0.5, lambda: bn.fill_resting(list(bn.orders)[-1])))
    res = await ex.run('LINKUSDT', 'LINK', 'BUY', 10.0, reduce_only=True)
    assert res.state == 'HEDGED'
    assert bn.positions['LINKUSDT']['amt'] == 0.0 and hl.positions['LINK']['amt'] == 0.0
    assert all(c[5] for c in hl.calls[-2:] if c[0] == 'ioc')          # closing hedge is reduce-only


# ------------------------------------------------------------------ lane decisions
def test_lane_refuses_live_and_decides_on_signal_capital_and_guard():
    with pytest.raises(ValueError, match='live'):
        cl.CarryLane('live')
    T = 100 * H8
    bn = prints(T - 10 * cl.DAY_MS, T, H8, 1.0)
    hl_strong, hl_weak = (prints(T - 10 * cl.DAY_MS, T, cl.HOUR_MS, x) for x in (0.5, 0.13))
    lane = cl.CarryLane('shadow')
    assert lane.decide('LINK', bn, hl_weak, T, 1000.0, 100.0)['action'] == 'OBSERVE'
    op = lane.decide('LINK', bn, hl_strong, T, 1000.0, 100.0)
    assert op['action'] == 'OPEN' and op['short'] == 'HL' and op['long'] == 'BN'
    poor = cl.CarryLane('shadow').decide('LINK', bn, hl_strong, T, 3.85, 1.0)
    assert poor['action'] == 'OBSERVE' and 'capital' in poor['reason']   # the 3.85 U book cannot fund two legs
    paper = cl.CarryLane('paper')
    assert paper.decide('LINK', bn, hl_strong, T, 1000.0, 100.0)['action'] == 'OBSERVE'   # no margin view: UNKNOWN
    held = {'opened_ms': T - 29 * cl.DAY_MS, 'short': 'HL'}
    assert lane.decide('LINK', bn, hl_strong, T, 1000.0, 100.0, held=held)['action'] == 'CLOSE'
    risky = paper.decide('LINK', bn, hl_strong, T, 1000.0, 100.0, legs=legs(mr_bn=0.7),
                         held={'opened_ms': T, 'short': 'HL'})
    assert risky['action'] == cl.DERISK
