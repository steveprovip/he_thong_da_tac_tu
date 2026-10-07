---
name: polymarket-binance-micro-arb
description: High-frequency latency arbitrage and flash liquidation sniping engine between Binance USD-M Futures and Polymarket 15m binary prediction markets.
---

# Polymarket-Binance Micro-Alpha & Latency Arbitrage Engine

## 1. Core Mechanics
1. **Binance Flash Liquidation Cascade Sniping**:
   - Stream real-time liquidation feed: `wss://fstream.binance.com/ws/!forceOrder@arr`.
   - Threshold: Aggregate liquidation volume >= $50,000 USD within a rolling 5-second window.
   - Signal: Mean-reverting wick reaction (30s - 60s window).
   - Execution: Counter-trend limit entry at bid/ask with Post-Only GTX; TP: +0.8% - +1.5%, SL: 0.05 USDT.

2. **Polymarket 15m Latency Arbitrage**:
   - Monitor Binance L2 spot/futures micro-price jumps (dt < 200ms).
   - Poll / stream Polymarket 15m BTC/ETH price prediction contracts ("Will BTC be above $X at HH:MM?").
   - Exploit Polymarket Oracle / AMM update latency (3 - 8 seconds delay).
   - When Binance surges > +0.3% and Polymarket YES shares are priced < $0.45, instantly fire aggressive buy order for YES tokens.
   - Target gain: +20% to +50% upon market resolution.

## 2. Micro-Capital Guardrails
- **Max Account Capital**: 100k VNĐ (~3.85 USDT).
- **Risk Per Trade**: Fixed at $0.05 - $0.20 USDT.
- **Leverage**: 2x - 3x on Binance Futures, 1x on Polymarket.
- **Execution Gate**: Reject trades if estimated taker fee exceeds 30% of projected gross edge.
