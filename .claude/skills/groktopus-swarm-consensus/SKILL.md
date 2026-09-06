---
name: groktopus-swarm-consensus
description: Autonomous 8-agent swarm architecture (VESKA, NORO, LUMEN, TIDAL, ZEPHR, OKAPI, RUNE, MARIN), dual-threshold consensus gating (76.8% execute, 70.0% RUNE veto), single-use cryptographic AuthTokens, and dual-engine execution (Qwen 3.8 Dense vs CPU Pure Heuristic).
---

# GrokTOPUS 8-Agent Swarm Governance Architecture

## 1. System Philosophy: Not a bot, but an Autonomous Trading Floor

In traditional trading bots, AI directly moves wallet balances (AI -> Wallet -> Trade).
In GrokTOPUS, no single agent can ever move funds. Money can only move when a supermajority consensus of 8 specialized agents is achieved and authenticated.

## 2. The 8 Specialized Agents & Role Division

- TIDAL: Volatility Scanner (Directional: BUY/HOLD/SELL) - Cross-sectional momentum sigma, RSI stretch, liquidity sweeps.
- NORO: Fair-Value Arbitrageur (Directional) - Long-term drift forecast bps, VWAP Z-score reversion.
- LUMEN: Sentiment & Flow Analyst (Directional) - Orderbook Imbalance (OBI), Cumulative Volume Delta (CVD).
- ZEPHR: Liquidity & Cost Gate (Suitability: HOLD if bad) - Quoted spread, slippage, Edge-to-Cost ratio (> 1.8x).
- OKAPI: Exposure & Capacity Sizer (Suitability: HOLD if full) - Portfolio margin utilization vs 75% exposure cap.
- RUNE: Supreme Risk Guardian (Absolute Veto Power) - Hard limit verification, drawdown ceiling, fee safety.
- VESKA: Single Order Gateway (Executor) - Only acts when valid single-use AuthToken is presented.
- MARIN: Position & Exit Guardian (Settler) - Triple barrier monitoring, Avellaneda-Stoikov decay, BE lock.

## 3. Consensus Engine & Voting Mechanics

### Consensus Formula
Normalized weights w_i in (0, 1) such that sum(w_i) = 1.0.
Score = sum(w_i * confidence_i) for agreeing stance.

### Execution Thresholds & Gates
- Standard Execution Gate: >= 76.8% weighted consensus required to authorize trade.
- RUNE Absolute Veto Gate: If consensus < 70.0% OR any safety invariant is breached (VRAM overflow, ledger overdraft, excessive spread), RUNE casts an irreversible veto. A veto cannot be overridden by any score.
- Adaptive Regime Switching:
  * Strong Trend Regime (|Mom_sigma| > 1.0): lower execution hurdle to 72.0% to capture fast breakouts.
  * Chop/Range Regime (|Mom_sigma| <= 0.6): raise hurdle to 76.8% to avoid false whipsaws.

## 4. Dual Brain Engines: GPU Resident vs CPU Pure Heuristic

### Engine A: GPU Qwen 3.8-27B Dense (UD-IQ2_XXS)
- 9.8 GB resident VRAM on RTX 5060 Ti.
- High-level audit, narrative rationale, and macro reasoning.
- Latency: ~5.3s per vote cycle.

### Engine B: CPU Pure Heuristic (High-Frequency Mode)
- 0.0 GB VRAM (100% free GPU).
- Pure mathematical calculation of all 6 agent stances in Python on CPU.
- Speed: ~20,000,000 ops/sec (latency <= 0.05 ms).
- Cycle scan time: 2 - 3 seconds (enables instant micro-market reaction).
