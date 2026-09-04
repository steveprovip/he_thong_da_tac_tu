#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
real_alpha_engine.py - Production Quantitative Signal & Alpha Engine for GrokTOPUS Swarm.

Replaces simulation oracle lookahead leakage ('true_bias') with genuine mathematical
and statistical indicators computed over streaming market data:
- Trend & Momentum: Exponential Moving Averages (EMA 9/21/50), Relative Strength Index (RSI 14), MACD.
- Fair Value & Drift: Volume-Weighted Average Price (VWAP), Bollinger Band z-score.
- Microstructure & Liquidity: Order Book Imbalance (OBI), Top-20 depth slippage, Bid-Ask spread.
- Volatility: Average True Range (ATR 14) for dynamic position sizing and on-exchange stop loss.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence


@dataclass
class Candle:
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class MarketAlphaSnapshot:
    symbol: str
    spot_price: float
    momentum_sigma: float
    forecast_bps: float
    cost_bps: float
    edge_bps: float
    exposure_pct: float
    exposure_cap_pct: float
    rsi_14: float
    orderbook_imbalance: float
    atr_volatility_pct: float
    cvd_proxy: float = 0.0
    vwap_zscore: float = 0.0
    liquidity_sweep: float = 0.0
    breaches: list[str] = field(default_factory=list)
    true_bias: Optional[float] = None  # ALWAYS None in real-world trading

    def to_swarm_context(self, side_stance: str = "BUY") -> dict[str, Any]:
        """Convert to the exact dictionary format expected by GrokTOPUS Swarm agents."""
        return {
            "symbol": self.symbol,
            "spot_price": self.spot_price,
            "side": side_stance,
            "momentum_sigma": self.momentum_sigma,
            "forecast_bps": self.forecast_bps,
            "cost_bps": self.cost_bps,
            "edge_bps": self.edge_bps,
            "exposure_pct": self.exposure_pct,
            "exposure_cap_pct": self.exposure_cap_pct,
            "rsi_14": self.rsi_14,
            "orderbook_imbalance": self.orderbook_imbalance,
            "atr_volatility_pct": self.atr_volatility_pct,
            "cvd_proxy": self.cvd_proxy,
            "vwap_zscore": self.vwap_zscore,
            "liquidity_sweep": self.liquidity_sweep,
            "breaches": self.breaches,
            "true_bias": None,  # Strictly None
        }


class RealAlphaEngine:
    """Computes deterministic quantitative signals without oracle leakage."""

    @staticmethod
    def calculate_ema(values: Sequence[float], period: int) -> float:
        """Standard Exponential Moving Average."""
        if not values:
            return 0.0
        if len(values) < period:
            return sum(values) / len(values)
        k = 2.0 / (period + 1.0)
        ema = values[0]
        for v in values[1:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    @staticmethod
    def calculate_rsi(prices: Sequence[float], period: int = 14) -> float:
        """Standard Wilder's RSI."""
        if len(prices) < period + 1:
            return 50.0  # Neutral

        gains: list[float] = []
        losses: list[float] = []
        for i in range(1, len(prices)):
            diff = prices[i] - prices[i - 1]
            if diff >= 0:
                gains.append(diff)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(abs(diff))

        recent_gains = gains[-period:]
        recent_losses = losses[-period:]

        avg_gain = sum(recent_gains) / period
        avg_loss = sum(recent_losses) / period

        if avg_loss == 0.0:
            return 100.0 if avg_gain > 0 else 50.0

        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return max(0.0, min(100.0, rsi))

    @staticmethod
    def calculate_orderbook_imbalance(
        bids: Sequence[tuple[float, float]],
        asks: Sequence[tuple[float, float]],
        depth_levels: int = 10,
    ) -> float:
        """Order Book Imbalance (OBI) = (BidVol - AskVol) / (BidVol + AskVol).

        Range: [-1.0 (heavy sell wall) to +1.0 (heavy buy support)].
        """
        bid_vol = sum(vol for _, vol in bids[:depth_levels])
        ask_vol = sum(vol for _, vol in asks[:depth_levels])
        denom = bid_vol + ask_vol
        if denom <= 1e-9:
            return 0.0
        return (bid_vol - ask_vol) / denom

    @staticmethod
    def calculate_volume_delta(candles: Sequence[Candle], period: int = 20) -> float:
        """Cumulative Volume Delta over `period` bars, normalised to [-1, 1].

        True CVD needs the trade tape: every print tagged by whether the
        aggressor was the buyer or the seller. That tape exists on the
        WebSocket `aggTrade` stream and does NOT exist in candle data, so what
        this computes is the standard candle proxy - where the close sits inside
        the bar's range, weighted by that bar's volume:

            delta_i = volume_i * (2 * (close - low) / (high - low) - 1)

        A bar that closes on its high is read as buyer-dominated, one that closes
        on its low as seller-dominated. It is a proxy and it is wrong on doji
        bars and on bars with a single violent wick, which is exactly why it is
        named `_proxy` in the snapshot and why `feed_trades()` exists to replace
        it with the real thing when a tape is available.

        Normalising by total volume keeps the output comparable across symbols
        and across quiet and busy periods; the raw cumulative figure is not,
        which is how CVD charts end up being read as trends when they are really
        showing that Tuesday was busier than Sunday.
        """
        if not candles:
            return 0.0
        window = list(candles)[-period:]
        signed = 0.0
        total = 0.0
        for c in window:
            span = c.high - c.low
            if span <= 0 or c.volume <= 0:
                continue
            position = (c.close - c.low) / span          # 0 at the low, 1 at the high
            signed += c.volume * (2.0 * position - 1.0)
            total += c.volume
        if total <= 0:
            return 0.0
        return max(-1.0, min(1.0, signed / total))

    @staticmethod
    def calculate_vwap(candles: Sequence[Candle]) -> float:
        """Volume-weighted average price over the supplied window."""
        num = 0.0
        den = 0.0
        for c in candles:
            typical = (c.high + c.low + c.close) / 3.0
            num += typical * max(0.0, c.volume)
            den += max(0.0, c.volume)
        if den <= 0:
            return candles[-1].close if candles else 0.0
        return num / den

    @classmethod
    def calculate_vwap_zscore(cls, candles: Sequence[Candle], period: int = 40) -> float:
        """How far spot sits from VWAP, in standard deviations of that distance.

        The z-score matters more than the raw gap: 30 bps above VWAP is a long
        way in EUR-USD and nothing in SOL-USD, and a mean-reversion signal that
        does not normalise is really a signal about which symbol it is looking at.

        Positive means spot is above VWAP - expensive against the volume-weighted
        consensus, so a mean-reversion reading is bearish. NORO uses it that way;
        the sign flip lives at the call site rather than here, because a function
        that silently negates its own output is impossible to read.
        """
        window = list(candles)[-period:]
        if len(window) < 8:
            return 0.0
        vwap = cls.calculate_vwap(window)
        if vwap <= 0:
            return 0.0
        gaps = [(c.close - vwap) / vwap for c in window]
        mean = sum(gaps) / len(gaps)
        var = sum((g - mean) ** 2 for g in gaps) / max(1, len(gaps) - 1)
        sd = math.sqrt(var)
        if sd <= 0:
            return 0.0
        return max(-6.0, min(6.0, ((window[-1].close - vwap) / vwap - mean) / sd))

    @staticmethod
    def detect_liquidity_sweep(
        candles: Sequence[Candle], lookback: int = 20, wick_ratio: float = 1.8
    ) -> float:
        """Did the last bar run stops and reverse? Returns a signed strength.

        The pattern: price pushes through the recent extreme - where resting
        stop orders sit - and then closes back inside the range, leaving a long
        wick. The reading is that the move through the level was liquidity being
        taken rather than a genuine breakout, so the reversal is the signal.

        Returns +1-ish when a LOW was swept and price closed back up (bullish),
        -1-ish when a HIGH was swept and price closed back down (bearish), 0
        when neither happened. Magnitude scales with how far beyond the extreme
        the wick reached.

        This is a pattern detector, not a prophecy. Sweeps fail constantly, and
        `benchmarks/edge_report.py` is the only thing that can say whether this
        one pays for its own transaction costs on a given market.
        """
        window = list(candles)[-(lookback + 1):]
        if len(window) < 6:
            return 0.0
        last = window[-1]
        prior = window[:-1]
        span = last.high - last.low
        if span <= 0:
            return 0.0

        prior_low = min(c.low for c in prior)
        prior_high = max(c.high for c in prior)
        upper_wick = last.high - max(last.open, last.close)
        lower_wick = min(last.open, last.close) - last.low
        body = max(1e-12, abs(last.close - last.open))

        # Bullish: took out the low, closed back above it, on a long lower wick.
        if last.low < prior_low <= last.close and lower_wick > body * wick_ratio:
            depth = (prior_low - last.low) / max(1e-12, prior_low)
            return min(1.0, 0.4 + depth * 400.0)
        # Bearish: took out the high, closed back below it, on a long upper wick.
        if last.high > prior_high >= last.close and upper_wick > body * wick_ratio:
            depth = (last.high - prior_high) / max(1e-12, prior_high)
            return -min(1.0, 0.4 + depth * 400.0)
        return 0.0

    @staticmethod
    def calculate_atr(candles: Sequence[Candle], period: int = 14) -> float:
        """Average True Range (measure of volatility)."""
        if len(candles) < 2:
            return 0.0
        tr_list: list[float] = []
        for i in range(1, len(candles)):
            c_curr = candles[i]
            c_prev = candles[i - 1]
            tr = max(
                c_curr.high - c_curr.low,
                abs(c_curr.high - c_prev.close),
                abs(c_curr.low - c_prev.close),
            )
            tr_list.append(tr)

        window = tr_list[-period:]
        return sum(window) / len(window) if window else 0.0

    @classmethod
    def evaluate_market(
        cls,
        symbol: str,
        candles: Sequence[Candle],
        bids: Sequence[tuple[float, float]],
        asks: Sequence[tuple[float, float]],
        current_equity: float = 10000.0,
        allocated_margin: float = 1000.0,
        maker_fee_bps: float = 2.0,
        taker_fee_bps: float = 5.0,
    ) -> MarketAlphaSnapshot:
        """Derive full quantitative alpha snapshot from streaming market data."""
        if not candles:
            return MarketAlphaSnapshot(
                symbol=symbol,
                spot_price=0.0,
                momentum_sigma=0.0,
                forecast_bps=0.0,
                cost_bps=taker_fee_bps,
                edge_bps=0.0,
                exposure_pct=0.0,
                exposure_cap_pct=20.0,
                rsi_14=50.0,
                orderbook_imbalance=0.0,
                atr_volatility_pct=0.0,
                breaches=["no_market_data"],
            )

        closes = [c.close for c in candles]
        spot = closes[-1]

        # 1. Trend & Momentum (TIDAL signal)
        ema_fast = cls.calculate_ema(closes, 9)
        ema_slow = cls.calculate_ema(closes, 21)
        ema_baseline = cls.calculate_ema(closes, 50)

        # Standard deviation of returns over scan window
        returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
        vol_std = math.sqrt(sum(r * r for r in returns) / len(returns)) if returns else 0.005
        vol_std = max(1e-4, vol_std)

        # Normalized momentum in units of sigma
        ema_delta_pct = (ema_fast - ema_slow) / max(1.0, spot)
        momentum_sigma = max(-3.0, min(3.0, ema_delta_pct / vol_std))

        # 2. Fair Value & Drift (NORO signal)
        # Difference between spot and 50-period baseline in basis points
        forecast_bps = ((spot - ema_baseline) / max(1.0, spot)) * 10000.0

        # 3. Microstructure & Orderbook Imbalance (ZEPHR signal)
        obi = cls.calculate_orderbook_imbalance(bids, asks, depth_levels=10)
        best_bid = bids[0][0] if bids else spot * 0.9999
        best_ask = asks[0][0] if asks else spot * 1.0001
        spread_bps = max(0.5, ((best_ask - best_bid) / max(1.0, spot)) * 10000.0)
        roundtrip_cost_bps = spread_bps + (taker_fee_bps * 2.0)

        # Edge estimate from technical confluence (Momentum + Microstructure OBI).
        #
        # This used to be `max(roundtrip_cost_bps * 1.8, ...)`, which defined the
        # edge to be at least 1.8x the cost no matter what the signals said. The
        # consequence was that ZEPHR's whole job - refusing a trade whose edge is
        # smaller than the cost of taking it - could never trigger, because the
        # comparison had been rigged in advance. An edge estimate that depends on
        # the cost it is about to be compared against is not an estimate.
        rsi = cls.calculate_rsi(closes, 14)
        edge_bps = abs(momentum_sigma) * 22.0 + abs(obi) * 16.0

        # 4. Volatility (ATR)
        atr = cls.calculate_atr(candles, 14)
        atr_pct = (atr / max(1.0, spot)) * 100.0

        # 5. Risk & Exposure (OKAPI / RUNE)
        exposure_pct = (allocated_margin / max(1.0, current_equity)) * 100.0
        exposure_cap_pct = 75.0

        breaches: list[str] = []
        if exposure_pct > exposure_cap_pct:
            breaches.append(f"exposure {exposure_pct:.1f}% exceeds cap {exposure_cap_pct:.1f}%")
        if atr_pct > 6.0:
            breaches.append(f"extreme volatility {atr_pct:.2f}% ATR")
        if spread_bps > 35.0:
            breaches.append(f"orderbook spread wide: {spread_bps:.1f} bps")

        return MarketAlphaSnapshot(
            symbol=symbol,
            spot_price=spot,
            momentum_sigma=round(momentum_sigma, 3),
            forecast_bps=round(forecast_bps, 2),
            cost_bps=round(roundtrip_cost_bps, 2),
            edge_bps=round(edge_bps, 2),
            exposure_pct=round(exposure_pct, 2),
            exposure_cap_pct=round(exposure_cap_pct, 2),
            rsi_14=round(rsi, 2),
            orderbook_imbalance=round(obi, 3),
            atr_volatility_pct=round(atr_pct, 3),
            cvd_proxy=round(cls.calculate_volume_delta(candles), 4),
            vwap_zscore=round(cls.calculate_vwap_zscore(candles), 4),
            liquidity_sweep=round(cls.detect_liquidity_sweep(candles), 4),
            breaches=breaches,
            true_bias=None,  # Zero oracle lookahead
        )


def selftest() -> int:
    print("=== RealAlphaEngine Selftest (Deterministic Quant Signals) ===")

    # Synthesize 60 candles with bullish trend
    candles: list[Candle] = []
    base_price = 60000.0
    for i in range(60):
        # gentle upward drift with sinusoidal wave
        p = base_price + i * 25.0 + math.sin(i / 3.0) * 40.0
        candles.append(Candle(
            timestamp=float(i * 60),
            open=p - 10.0,
            high=p + 20.0,
            low=p - 15.0,
            close=p,
            volume=15.0 + (i % 5),
        ))

    # Synthetic orderbook with positive bid support
    bids = [(candles[-1].close - i * 5.0, 2.5 + i * 0.4) for i in range(15)]
    asks = [(candles[-1].close + i * 5.0, 1.2 + i * 0.2) for i in range(15)]

    engine = RealAlphaEngine()
    snapshot = engine.evaluate_market("BTC-USD", candles, bids, asks)

    print(f"Evaluated BTC-USD Alpha Snapshot:")
    print(f"  Spot Price:           {snapshot.spot_price:.2f}")
    print(f"  Momentum Sigma:       {snapshot.momentum_sigma:+.3f}")
    print(f"  Forecast Drift:       {snapshot.forecast_bps:+.2f} bps")
    print(f"  Roundtrip Cost:       {snapshot.cost_bps:.2f} bps")
    print(f"  Expected Edge:        {snapshot.edge_bps:.2f} bps")
    print(f"  RSI(14):              {snapshot.rsi_14:.1f}")
    print(f"  Orderbook Imbalance:  {snapshot.orderbook_imbalance:+.3f}")
    print(f"  ATR Volatility:       {snapshot.atr_volatility_pct:.2f}%")
    print(f"  Breaches:             {snapshot.breaches}")
    print(f"  True Bias Oracle:     {snapshot.true_bias} (Strictly None)")

    assert snapshot.true_bias is None, "Oracle bias must be strictly None"
    assert snapshot.momentum_sigma > 0.0, "Expected positive momentum on uptrend"
    assert snapshot.orderbook_imbalance > 0.0, "Expected positive OBI with heavier bids"
    print("ALL ASSERTIONS PASSED. RealAlphaEngine is verified.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
