import math
import random
import pytest

from core.micro_signals import AsymmetricToxicityGuard, AsymmetricQuotePlan
from core.stat_arb_lane import OnlineKalmanPairTracker, StatArbSignal
from core.funding_carry import (
    DynamicXVQuote,
    DynamicXVPlan,
    evaluate_dynamic_xv_candidate,
    rank_dynamic_xv_opportunities,
    should_rotate_xv_position,
)


# =====================================================================
# 1. Tests for AsymmetricToxicityGuard (VPIN + Multi-Level OFI)
# =====================================================================

def test_asymmetric_toxicity_guard_vpin_calculation():
    guard = AsymmetricToxicityGuard(
        bucket_volume=10_000.0,
        num_buckets=5,
        base_half_spread_bps=2.0,
        vpin_safe_threshold=0.45,
        vpin_toxic_threshold=0.75,
    )
    assert guard.current_vpin == 0.0

    # Feed 5 full buckets of pure taker sells (is_buyer_maker=True)
    for _ in range(5):
        guard.on_trade(qty=100.0, price=100.0, is_buyer_maker=True)

    # All buckets had 10,000 sell, 0 buy -> imbalance = 1.0 each
    assert pytest.approx(guard.current_vpin, abs=1e-4) == 1.0


def test_asymmetric_toxicity_guard_ofi_and_queue_depletion():
    guard = AsymmetricToxicityGuard(ofi_window=20, decay_lambda=0.5)

    # Establish baseline with minor fluctuations around neutral
    guard.on_depth([(100.0, 10.0)], [(101.0, 10.0)])
    for i in range(10):
        dq = 0.5 if i % 2 == 0 else -0.5
        guard.on_depth([(100.0, 10.0 + dq)], [(101.0, 10.0 - dq)])

    # Sudden severe sell dump: bid depth slashed from 10 to 1, ask depth jumped from 10 to 30
    guard.on_depth([(100.0, 1.0)], [(100.5, 30.0)])

    # Shock produces sharp negative z-OFI
    assert guard.current_z_ofi < -1.5


def test_asymmetric_toxicity_guard_quote_and_cancellation():
    guard = AsymmetricToxicityGuard(
        bucket_volume=5_000.0,
        num_buckets=5,
        base_half_spread_bps=2.0,
        vpin_safe_threshold=0.45,
        vpin_toxic_threshold=0.75,
    )

    # 1. Clean market state
    bids = [(100.0, 10.0), (99.0, 10.0)]
    asks = [(101.0, 10.0), (102.0, 10.0)]
    guard.on_depth(bids, asks)
    plan_clean = guard.compute_quote_plan(bids, asks, inventory_skew=0.0)

    assert not plan_clean.cancel_bid
    assert not plan_clean.cancel_ask
    assert plan_clean.bid_price <= 100.0
    assert plan_clean.ask_price >= 101.0

    # 2. Trigger high VPIN via massive one-sided flow
    for _ in range(10):
        guard.on_trade(qty=50.0, price=100.0, is_buyer_maker=True)

    assert guard.current_vpin >= 0.75

    # 3. Simulate severe sell dump on depth
    guard.on_depth([(100.0, 10.0)], [(101.0, 10.0)])
    for i in range(8):
        dq = 0.5 if i % 2 == 0 else -0.5
        guard.on_depth([(100.0, 10.0 + dq)], [(101.0, 10.0 - dq)])
    guard.on_depth([(100.0, 1.0)], [(100.5, 35.0)])

    assert guard.current_z_ofi <= -1.5

    # Toxic sell flow combined with L1 depletion and lead return triggers pre-emptive cancel
    depleted_bids = [(100.0, 1.0), (99.0, 5.0)]
    plan_toxic = guard.compute_quote_plan(depleted_bids, asks, inventory_skew=0.0, btc_lead_return_bps=-4.0)
    assert plan_toxic.cancel_bid
    assert plan_toxic.hazard_bid >= 1.0
    # Bid half-spread expands under toxic sell pressure
    assert plan_toxic.bid_half_spread_bps > plan_clean.bid_half_spread_bps


# =====================================================================
# 2. Tests for OnlineKalmanPairTracker (Stat-Arb Cointegration)
# =====================================================================

def test_kalman_pair_tracker_tracking_and_stationarity():
    tracker = OnlineKalmanPairTracker(
        asset_a="SOL",
        asset_b="AVAX",
        delta_alpha=1e-6,
        delta_beta=1e-6,
        obs_var_init=1e-3,
        ou_window=60,
    )

    rng = random.Random(123)
    e = 0.0
    pb = 25.0

    for _ in range(80):
        e = 0.85 * e + rng.gauss(0.0, 0.02)
        pa = math.exp(math.log(pb) + e)
        tracker.update(pa, pb)

    # Beta should be tracking near 1.0
    assert 0.8 <= tracker.beta <= 1.2
    half_life, eq_std_bps, is_valid = tracker.estimate_ou_params()
    assert is_valid
    assert 2.0 <= half_life <= 96.0
    assert eq_std_bps > 0.0


def test_kalman_pair_tracker_trade_lifecycle():
    tracker = OnlineKalmanPairTracker(
        asset_a="ETH",
        asset_b="ARB",
        ou_window=60,
    )

    rng = random.Random(123)
    e = 0.0
    pb = 1.5

    for _ in range(50):
        e = 0.85 * e + rng.gauss(0.0, 0.02)
        pa = math.exp(math.log(2000.0) + tracker.beta * math.log(pb) + e)
        tracker.update(pa, pb)

    half_life, eq_std_bps, is_valid = tracker.estimate_ou_params()
    assert is_valid

    # 1. Trigger Long Spread Entry (Z <= -1.5)
    sig_entry = tracker.evaluate_signal(
        price_a=pa * 0.96,  # Undervalued A -> Z score drops
        price_b=pb,
        round_trip_cost_bps=2.0,
        z_entry=1.5,
    )
    assert sig_entry.action == "ENTER_LONG_SPREAD"
    assert tracker.current_position == 1
    assert sig_entry.weight_a > 0
    assert sig_entry.weight_b < 0

    # 2. Mean reversion exit (Z converges towards 0)
    sig_exit = tracker.evaluate_signal(
        price_a=pa * 1.01,
        price_b=pb,
        round_trip_cost_bps=2.0,
        z_exit=0.5,
    )
    assert sig_exit.action == "EXIT_SPREAD"
    assert tracker.current_position == 0

    # 3. Structural breakdown stop (|Z| >= z_stop)
    tracker.current_position = 1
    tracker.bars_in_trade = 5
    sig_stop = tracker.evaluate_signal(
        price_a=pa * 0.70,  # Massive decoupling
        price_b=pb,
        z_stop=3.0,
    )
    assert sig_stop.action == "STOP_BREAKDOWN"
    assert tracker.current_position == 0


# =====================================================================
# 3. Tests for Dynamic XV Funding Carry (Binance <-> Hyperliquid)
# =====================================================================

def test_dynamic_xv_candidate_evaluation():
    quote = DynamicXVQuote(
        symbol="SOLUSDT",
        binance_rate_8h_bps=1.0,         # 1.0 bps / 8h
        hyperliquid_rate_1h_bps=0.5,     # 0.5 bps/1h -> 4.0 bps / 8h
        ema_spread_fast_bps=3.0,
        ema_spread_slow_bps=2.5,
        persistence_rho=0.85,
        basis_entry_bps=2.5,             # +2.5 bps basis bonus
        min_depth_usd=100_000.0,
        round_trip_fee_bps=10.0,
    )

    plan = evaluate_dynamic_xv_candidate(
        quote,
        eval_horizon_cycles=12,
        capital_utilization=0.70,
        min_liquidity_usd=25_000.0,
    )

    assert plan is not None
    assert plan.symbol == "SOLUSDT"
    # Hyperliquid 8h eq (4.0) > Binance (1.0) -> Short HL, Long BN
    assert plan.direction == "LONG_BN_SHORT_HL"
    assert plan.instant_spread_8h_bps == 3.0
    # Effective cost is reduced by basis credit (10.0 - 2.5 = 7.5 bps)
    assert plan.effective_cost_bps == 7.5
    assert plan.breakeven_cycles_8h < 5.0
    assert plan.projected_net_apr_pct > 15.0
    assert plan.score > 0.0


def test_dynamic_xv_ranking_and_rotation():
    q1 = DynamicXVQuote(
        symbol="SOLUSDT",
        binance_rate_8h_bps=1.0,
        hyperliquid_rate_1h_bps=0.4,     # spread = 2.2 bps
        ema_spread_fast_bps=2.2,
        ema_spread_slow_bps=2.0,
        persistence_rho=0.80,
        basis_entry_bps=1.0,
        min_depth_usd=50_000.0,
    )
    q2 = DynamicXVQuote(
        symbol="TAOUSDT",
        binance_rate_8h_bps=0.5,
        hyperliquid_rate_1h_bps=0.7,     # spread = 5.1 bps
        ema_spread_fast_bps=5.0,
        ema_spread_slow_bps=4.5,
        persistence_rho=0.88,
        basis_entry_bps=2.0,
        min_depth_usd=60_000.0,
    )

    ranked = rank_dynamic_xv_opportunities([q1, q2])
    assert len(ranked) == 2
    assert ranked[0].symbol == "TAOUSDT"  # TAO has higher spread and persistence

    plan_sol = ranked[1]
    plan_tao = ranked[0]

    # Hysteresis Rotation test
    # If currently holding SOL, rotating to TAO requires net advantage > 4 bps
    can_rotate = should_rotate_xv_position(
        current_plan=plan_sol,
        candidate_plan=plan_tao,
        exit_cost_current_bps=4.0,
        hysteresis_buffer_bps=4.0,
    )
    assert can_rotate is True

    # Same symbol rotation should always be False
    assert should_rotate_xv_position(plan_tao, plan_tao) is False
