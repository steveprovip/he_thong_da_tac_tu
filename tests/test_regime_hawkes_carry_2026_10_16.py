"""10-16 RFC-2026-Q4: tri-state Hurst regime, Hawkes VPIN exhaustion, dynamic Z band + leader/follower legs,
shadow cross-venue funding carry.  Code behaviour only - none of this is economic evidence."""
import math
import random
import time

import pytest

from core import trend_lane as tl
from core.funding_executor import FundingQuote, ShadowCarryBook, evaluate, scan, scenarios
from core.micro_signals import AsymmetricToxicityGuard
from core.stat_arb_lane import (DynamicZBand, LeaderFollowerExecutor, LegQuote, OnlineKalmanPairTracker,
                                StatArbLane)


def ar_closes(phi: float, n: int, seed: int = 7) -> list:
    rnd = random.Random(seed)
    c, r = [100.0], 0.0
    for _ in range(n):
        r = phi * r + rnd.gauss(0.0, 0.01)
        c.append(c[-1] * math.exp(r))
    return c


def lo_mackinlay_h(closes, n=60, q=4):
    r = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - n, len(closes))]
    mu = sum(r) / n
    var_a = sum((x - mu) ** 2 for x in r) / (n - 1)
    rq = [sum(r[i - q + 1:i + 1]) for i in range(q - 1, n)]
    m = q * (n - q + 1) * (1 - q / n)
    var_c = sum((x - q * mu) ** 2 for x in rq) / m
    return 0.5 + math.log(var_c / var_a) / (2 * math.log(q))


# ------------------------------------------------------------------ task 1: tri-state regime
def test_rolling_hurst_matches_brute_force_lo_mackinlay():
    c = ar_closes(0.1, 400)
    hs = tl.hurst_series(c)
    assert all(h is None for h in hs[:60]) and hs[60] is not None
    for i in (60, 150, 399):
        assert hs[i] == pytest.approx(lo_mackinlay_h(c[:i + 1]), abs=1e-9)


def test_tri_state_separates_persistent_and_anti_persistent_returns():
    def share(phi):
        st = [tl.tri_state(h) for h in tl.hurst_series(ar_closes(phi, 6000)) if h is not None]
        return {k: st.count(k) / len(st) for k in (tl.TRENDING, tl.RANDOM_WALK, tl.MEAN_REVERTING)}
    assert share(0.3)[tl.TRENDING] > 0.75
    assert share(-0.3)[tl.MEAN_REVERTING] > 0.75
    rw = share(0.0)
    # documented weakness: at n = 60 a pure random walk lands in the 0.45-0.55 band well under half the time
    assert 0.25 < rw[tl.RANDOM_WALK] < 0.6
    assert tl.tri_state(None) is None and tl.tri_state(0.55) == tl.RANDOM_WALK and tl.tri_state(0.45) == tl.RANDOM_WALK
    assert tl.STATE_LANES[tl.RANDOM_WALK] == ('funding_carry',)


def test_hurst_gate_enters_only_in_trending_state():
    g = tl.RegimeGate(mode='hurst', confirm=False)
    bars = {'c': ar_closes(0.0, 200), 'h': None, 'l': None}
    bars['h'] = [x * 1.001 for x in bars['c']]
    bars['l'] = [x * 0.999 for x in bars['c']]
    info = tl.regime_info(bars, g)
    assert {'hurst', 'state3'} <= set(info)
    ok, why = tl.gate_entry(info, 10, 1.0, g)
    assert not ok and 'UNKNOWN' in why
    for i in range(61, 201):
        ok, why = tl.gate_entry(info, i, 1.0, g)
        assert ok == (info['state3'][i] == tl.TRENDING), (i, why)
    # the existing KER/ADX gate is untouched by the new keys
    g0 = tl.RegimeGate(mode='trend_only', confirm=False)
    i0 = tl.regime_info(bars, g0)
    assert tl.gate_entry(i0, 199, 1.0, g0)[0] == (i0['regime'][199] == 'TREND')


def test_ker10_chop_flags_a_random_walk_often():
    """Why the 4H lane deadlocks: KER is scale-free (volatility compression alone does not move it), but KER10 of a
    pure random walk is < 0.25 on ~45 % of bars and > 0.40 on ~33 % - a noisy estimator, not a regime."""
    k = [x for x in tl.ker_series(ar_closes(0.0, 20000), 10) if x is not None]
    lo = sum(x < 0.25 for x in k) / len(k)
    assert 0.35 < lo < 0.55
    c = ar_closes(0.0, 500)
    compressed = [c[0] + (v - c[0]) * 0.1 for v in c]                 # same path, 10x lower volatility
    k0, k1 = tl.ker_series(c, 10)[10:], tl.ker_series(compressed, 10)[10:]
    assert all(a == pytest.approx(b) for a, b in zip(k0, k1))


# ------------------------------------------------------------------ task 2: Hawkes exhaustion
def drive(guard, path):
    st = None
    for t, v in path:
        st = guard.on_toxicity(t, v)
    return st


def test_vpin_spike_vetoes_quotes_then_fades_the_sweep_after_exhaustion():
    g = AsymmetricToxicityGuard(bucket_volume=1000.0, num_buckets=5, tau_half_prior_s=10.0)
    for _ in range(5):
        g.on_trade(qty=10.0, price=100.0, is_buyer_maker=False)          # aggressive BUY buckets
    bids, asks = [(99.9, 5.0)], [(100.1, 5.0)]
    st = g.on_toxicity(0.0, 0.92)
    assert st.phase == 'VETO' and st.cancel_all and st.sweep_side == 'BUY'
    assert st.intensity == pytest.approx(1.0)                          # buckets before the spike are not events
    plan = g.compute_quote_plan(bids, asks)
    assert plan.cancel_bid and plan.cancel_ask
    assert g.fade_quote(bids, asks, st) is None
    seen = []
    for t in range(1, 600):                                            # excess decays with a 10 s half-life
        st = g.on_toxicity(float(t), 0.45 + 0.40 * 0.5 ** (t / 10.0))
        seen.append(st.phase)
        if st.phase == 'FADE':
            break
    assert st.phase == 'FADE' and st.fade_side == 'SELL' and not st.cancel_all
    assert set(seen[:-1]) == {'VETO'}
    assert st.since_spike_s >= 2.5 * st.tau_half_s and st.slope_per_min < -0.15
    assert st.intensity <= 0.25
    q = g.fade_quote(bids, asks, st)
    assert q.side == 'SELL' and q.price == 100.1 and q.time_in_force == 'GTX'      # rests on the ask, never crosses
    st = g.on_toxicity(st.since_spike_s + st.tau_half_s + 1.0, 0.30)
    assert st.phase == 'CLEAN'


def test_no_fade_while_vpin_plateaus():
    g = AsymmetricToxicityGuard(tau_half_prior_s=10.0)
    st = drive(g, [(float(t), 0.85) for t in range(0, 101)])           # toxic until t = 100
    st = drive(g, [(float(t), 0.78) for t in range(101, 400)])         # small drop, then flat but elevated
    assert st.phase == 'VETO' and abs(st.slope_per_min) < 1e-12
    st = drive(g, [(float(t), 0.60) for t in range(400, 800)])         # settles below the toxic level: veto lifts
    assert st.phase == 'CLEAN' and st.fade_side is None


def test_half_life_is_estimated_from_the_vpin_excess_decay():
    g = AsymmetricToxicityGuard(vpin_safe_threshold=0.45, tau_half_prior_s=60.0)
    g.on_toxicity(0.0, 0.95)
    true_tau = 8.0
    for t in range(1, 80):
        g.on_toxicity(float(t), 0.45 + 0.40 * 0.5 ** (t / true_tau))
    assert g.tau_half_s == pytest.approx(true_tau, rel=0.1)


def test_repeated_toxic_buckets_raise_intensity_and_delay_the_fade():
    g = AsymmetricToxicityGuard(bucket_volume=100.0, num_buckets=5, tau_half_prior_s=10.0)
    g.on_toxicity(0.0, 0.90)
    for _ in range(8):
        g.on_trade(qty=1.0, price=100.0, is_buyer_maker=True)            # 8 SELL buckets while toxic
    st = g.on_toxicity(1.0, 0.95)
    assert st.intensity > 8.0 and st.sweep_side == 'SELL'
    phases = [g.on_toxicity(1.0 + t, max(0.2, 0.79 - 0.01 * t)) for t in range(1, 30)]
    assert phases[24].since_spike_s >= 2.5 * phases[24].tau_half_s and phases[24].slope_per_min < -0.15
    assert 'FADE' not in {x.phase for x in phases}                       # intensity not exhausted -> no fade


# ------------------------------------------------------------------ task 3: dynamic Z band + legs
def test_dynamic_z_band_formula_and_clamps():
    b = DynamicZBand()
    assert b.update(0.0, 1e-3) == 2.10
    t = 0.0
    while t < 14_400.0:
        t += 10.0
        b.update(t, 1e-3)
    assert b.z_entry(t) == pytest.approx(2.10, abs=1e-3)                   # flat vol -> base
    for _ in range(400):                                                   # compression -> floor
        t += 10.0
        b.update(t, 1e-5)
    assert b.z_entry(t) == 1.45
    for _ in range(400):                                                   # expansion -> cap
        t += 10.0
        b.update(t, 1e-1)
    assert b.z_entry(t) == 2.60
    b2 = DynamicZBand()
    b2.t0, b2.var_fast, b2.var_slow = 0.0, 1.2 ** 2, 1.0
    assert b2.z_entry(20_000.0) == pytest.approx(2.10 * 1.2 ** 0.5)


@pytest.mark.asyncio
async def test_stat_arb_lane_reports_the_entry_band(tmp_path):
    class F:
        def __init__(self, p): self.p = p
        def price(self): return self.p

    class E:
        def __init__(self, p): self.feed = F(p)
        def event(self, m): pass

    lane = StatArbLane({'SOLUSDT': E(150.0), 'DOGEUSDT': E(0.12)}, tmp_path)
    static = StatArbLane({'SOLUSDT': E(150.0), 'DOGEUSDT': E(0.12)}, tmp_path, z_alpha=None)
    for i in range(5):
        r = await lane.step(now=float(i))
        r0 = await static.step(now=float(i))
    assert r['SOLUSDT_DOGEUSDT']['z_entry'] == 2.10 and r0['SOLUSDT_DOGEUSDT']['z_entry'] == 2.10
    assert 'SOLUSDT_DOGEUSDT' in lane.bands and not static.bands


QA = LegQuote('SOLUSDT', 150.00, 150.03, 40_000.0)      # 2.0 bps
QB = LegQuote('DOGEUSDT', 0.12000, 0.12006, 25_000.0)   # 5.0 bps -> leader


def test_leader_rests_post_only_and_partial_fills_are_hedged_by_ioc():
    x = LeaderFollowerExecutor()
    lead = x.plan(+1, qty_a=1.0, qty_b=1250.0, qa=QA, qb=QB, now=0.0)
    assert lead == {'symbol': 'DOGEUSDT', 'side': 'SELL', 'type': 'LIMIT', 'timeInForce': 'GTX',
                    'quantity': 1250.0, 'price': 0.12006, 'leg': 'leader'}
    h1 = x.on_leader_fill(500.0, now=1.000)
    assert h1['symbol'] == 'SOLUSDT' and h1['side'] == 'BUY' and h1['timeInForce'] == 'IOC'
    assert h1['quantity'] == pytest.approx(0.4) and h1['price'] == pytest.approx(150.03 * 1.0003)
    assert h1['deadline_ms'] == 150.0
    assert x.on_hedge_result(0.4, now=1.040) is None and x.state == 'HEDGING'
    x.on_leader_fill(750.0, now=2.0)
    assert x.check(2.1) is None
    assert x.on_hedge_result(0.6, now=2.12) is None and x.state == 'DONE'
    assert max(x.latency_ms) < 150.0


def test_hedge_deadline_or_short_ioc_flattens_the_naked_leader():
    x = LeaderFollowerExecutor()
    x.plan(-1, 1.0, 1250.0, QA, QB, now=0.0)                 # short spread: SELL A, BUY B (leader B BUY at bid)
    x.on_leader_fill(1250.0, now=1.0)
    out = x.check(1.151)
    assert out['leg'] == 'flatten' and out['reduceOnly'] and out['side'] == 'SELL' and out['quantity'] == 1250.0
    assert out['reason'] == 'HEDGE_DEADLINE' and x.state == 'NAKED'
    late = x.on_hedge_result(1.0, now=1.3)                   # the IOC filled after all -> undo it too
    assert late['symbol'] == 'SOLUSDT' and late['side'] == 'BUY' and late['reduceOnly']

    y = LeaderFollowerExecutor()
    y.plan(+1, 1.0, 1250.0, QA, QB, now=0.0)
    y.on_leader_fill(1250.0, now=1.0)
    out = y.on_hedge_result(0.25, now=1.05)
    assert out['reason'] == 'IOC_SHORT' and out['quantity'] == pytest.approx(937.5)


def test_unfilled_leader_quote_is_cancelled_after_ttl():
    x = LeaderFollowerExecutor(quote_ttl_s=5.0)
    x.plan(+1, 1.0, 1250.0, QA, QB, now=0.0)
    assert x.check(4.9) is None
    assert x.check(5.1) == {'symbol': 'DOGEUSDT', 'action': 'CANCEL', 'leg': 'leader', 'reason': 'QUOTE_TTL'}
    with pytest.raises(ValueError):
        LeaderFollowerExecutor().plan(+1, 0.0, 1.0, QA, QB, 0.0)


# ------------------------------------------------------------------ task 4: funding carry (shadow)
def fq(coin, bn_bps, hl_bps_1h, bn=100.0, hl=100.0):
    return FundingQuote(coin, None if bn_bps is None else bn_bps * 1e-4, hl_bps_1h * 1e-4, bn, hl)


def test_scan_direction_criteria_and_unknown():
    c, _ = evaluate(fq('TAO', 1.0, 0.5, bn=100.0, hl=100.03))  # HL 4 bps/8h vs BN 1 -> short HL, spread 3
    assert (c.short_venue, c.long_venue) == ('HL', 'BN') and c.spread_8h_bps == pytest.approx(3.0)
    assert c.basis_skew_bps == pytest.approx(3.0, rel=1e-3)
    ok, why = scan([fq('TAO', 1.0, 0.5, 100.0, 100.03),     # eligible
                    fq('WLD', 1.0, 0.4, 100.0, 100.03),     # spread 2.2 < 2.5
                    fq('ZEC', 1.0, 0.5, 100.0, 100.01),     # basis +1.0 < +1.5
                    fq('SUI', None, 0.5)])                   # UNKNOWN
    assert [x.coin for x in ok] == ['TAO']
    assert 'spread' in why['WLD'] and 'basis' in why['ZEC'] and why['SUI'] == 'UNKNOWN rate/price'
    c, _ = evaluate(fq('ENA', 6.0, 0.25, bn=100.03, hl=100.0))  # BN 6 > HL 2 -> short BN
    assert c.short_venue == 'BN' and c.spread_8h_bps == pytest.approx(4.0) and c.basis_skew_bps > 1.5


def test_rotation_hysteresis_and_exit():
    b = ShadowCarryBook(4919.1996, slots=1)
    assert [i['kind'] for i in b.step([fq('TAO', 1.0, 0.5, 100.0, 100.03)])] == ['OPEN']       # spread 3.0
    assert b.step([fq('TAO', 1.0, 0.5, 100.0, 100.03), fq('WLD', 1.0, 0.98, 100.0, 100.03)]) == []  # +3.84
    out = b.step([fq('TAO', 1.0, 0.5, 100.0, 100.03), fq('WLD', 1.0, 1.0, 100.0, 100.03)])   # +4.0
    assert [(i['kind'], i['coin'], i['replaces']) for i in out] == [('ROTATE', 'WLD', 'TAO')]
    out = b.step([fq('WLD', 9.0, 1.0, 100.0, 100.03)])                                       # flipped
    assert out[0]['kind'] == 'CLOSE' and not b.held
    assert b.notional_per_leg == pytest.approx(4919.1996 * 0.7 / 2)
    with pytest.raises(ValueError):
        ShadowCarryBook(100.0, mode='live')


def test_monthly_yield_scenarios_on_demo_capital():
    s = scenarios(4919.1996)
    assert s['notional_per_leg'] == pytest.approx(2459.5998)
    assert s['replay_holdout_usdt_per_month']['mean'] == pytest.approx(3.90, abs=0.01)
    assert s['replay_holdout_usdt_per_month']['ci_low'] == pytest.approx(2.46, abs=0.01)
    assert s['threshold_persistent_usdt_per_month'] == pytest.approx(52.14, abs=0.05)


# ------------------------------------------------------------------ task 5: fee shield + per-tick budget
def test_no_directional_taker_market_intent():
    x = LeaderFollowerExecutor()
    intents = [x.plan(+1, 1.0, 1250.0, QA, QB, 0.0), x.on_leader_fill(1250.0, 1.0), x.check(1.2)]
    for i in intents:
        if i.get('type') == 'MARKET':
            assert i['reduceOnly'] and i['leg'] == 'flatten'
    assert intents[0]['timeInForce'] == 'GTX'


def test_per_tick_computation_under_5_ms():
    rh, g, band = tl.RollingHurst(), AsymmetricToxicityGuard(), DynamicZBand()
    tr = OnlineKalmanPairTracker('A', 'B', obs_var_init=1e-3)
    rnd = random.Random(3)
    pa = pb = 100.0
    n = 2000
    t0 = time.perf_counter()
    for i in range(n):
        pa *= math.exp(rnd.gauss(0, 1e-4))
        pb *= math.exp(rnd.gauss(0, 1e-4))
        rh.update(pa)
        g.on_trade(1.0, pa, rnd.random() < 0.5)
        g.on_toxicity(float(i), None)
        tr.evaluate_signal(pa, pb, z_entry=band.z_entry(float(i)))
        band.update(float(i), tr._spread_history[-1])
    per_tick_ms = (time.perf_counter() - t0) / n * 1e3
    assert per_tick_ms < 5.0
