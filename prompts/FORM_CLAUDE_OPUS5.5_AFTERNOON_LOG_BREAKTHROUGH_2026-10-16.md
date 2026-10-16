# QUANTITATIVE ARCHITECTURE SPECIFICATION & TELEMETRY AUTOPSY (RFC-2026-Q4.3)
# REPOSITORIES: F:\he_thong_da_tac_tu_2 | TARGET: GROKTOPUS 2.0
# SCOPE: AFTERNOON LIVE LOG AUDIT (13:54 - 16:18 UTC+7, 16/10/2026), 100% MAKER-EXIT VINDICATION, CHOP-BURST SUPPRESSION & HAWKES FADE CONVERSION

## 1. System Baseline & Operational Telemetry

- **Active Workspace:** `F:\he_thong_da_tac_tu_2` (Branch: `main`, 1,590 passing unit tests, clean tree).
- **Execution Mode:** Binance Demo Futures (BTCUSDT 0.001, SOLUSDT, DOGEUSDT, Cross 5x).
- **Session Duration:** 13:54:22 to 16:18:18 UTC+7 (2 hours 24 minutes continuous execution).
- **Capital Status:** 4,919.1996 USDT (100% capital preserved, Live trades locked).
- **Baseline Engine State:**
  * Tests Passing: 1,590 / 1,590 unit tests pass in 122s.
  * Invariants: Ledgers `data/engine_trades.jsonl` and `data/trend_trades.jsonl` must remain intact. All modifications must be strictly $O(1)$ algorithmic complexity.

---

## 2. Forensic Autopsy of the Afternoon Session (13:54 -> 16:18)

Comparison between the Morning Session (08:52 - 12:42) and this Afternoon Session reveals massive architectural wins alongside one final structural bottleneck:

```text
======================================================================================
COMPARATIVE TELEMETRY AUDIT: MORNING vs AFTERNOON
======================================================================================
1. Architectural Wins Vindicated:
   - "TIME-TAKER" Exits: Completely ELIMINATED (from > 35 occurrences down to EXACTLY 0).
     Every single exit in this 2.5-hour run was executed as a "MAKER-EXIT".
   - Hard Stop-Loss Disasters: ZERO occurrences (eliminated catastrophic -36.6 bps and -38.2 bps SL hits).
     Max loss capped at -21.9 bps (median loss ~ -6.8 bps).
   - Capital Router Stability: Hysteresis logic completely eliminated rapid oscillation.
     Entered CHOP once at 14:01:22 ("every symbol KER < 0.25: micro lane observes") and stayed disciplined.
   - Win Rate Expansion: Green trades expanded from 20% to ~33% (+0.7 to +10.1 bps across 23 profitable maker exits).

2. The Remaining Quantitative Bottleneck (Chop Entry Drift):
   - Despite 100% MAKER-EXIT execution, cumulative shadow PnL remains negative (~ -220 bps across ~70 trades).
   - Root Cause: During CHOP regimes (KER < 0.25), 1m price movement is pure mean-reverting noise.
     Entries triggered on transient 1m micro-momentum suffer immediate adverse drift.
     Although the exit order rests passively at inner touch, the price drifts away before fill, resulting in
     exits at -4.0 bps, -5.9 bps, -8.7 bps, -10.6 bps, -16.2 bps.
   - Empirical Proof: When the capital router adapted to CHOP at 14:01:22, the 1m Burst Lane continued taking
     shadow trades. The data conclusively proves that directional 1m scalping has negative expected value in CHOP!

3. Hawkes Toxicity Exhaustion Events:
   - 3 severe VPIN spikes were recorded:
     * [15:27:11] SOL · VPIN TOXIC 0.84 > 0.8 (SELL flow) -> immediately followed by [15:27:18] SOL BUY -10.6 bps
     * [15:48:45] SOL · VPIN TOXIC 0.85 > 0.8 (SELL flow) -> followed by [15:49:54] SOL SELL +1.6 bps
     * [15:57:16] SOL · VPIN TOXIC 0.81 > 0.8 (SELL flow) -> followed by [15:59:12] SOL SELL -12.4 bps
   - When institutional toxic flow hits, buying into the flow causes immediate adverse selection.
```

---

## 3. Institutional Quantitative Methodologies & Literature Sourcing

Deploy hedge fund microstructure mechanisms to solve the Chop Entry Drift:

### A. Regime-Dependent Horizon Hibernation (Cartea-Jaimungal, Bouchaud 2018)
- In market microstructure, directional momentum strategies possess zero informational edge when Hurst exponent $H \le 0.50$ and $\text{KER} < 0.25$.
- **Action:** When `CapitalRouter` is in `CHOP` regime, actively **hibernate/suppress** the Burst Scalper entries rather than allowing shadow fee bleed.
- Divert capital bandwidth entirely to **Delta-Neutral Carry (`core/funding_executor.py`)** and **Stat-Arb Cointegration (`core/stat_arb_lane.py`)**.

### B. Hawkes Toxicity Veto & Reversal Fade Execution
- As demonstrated by the 15:27 VPIN spike (0.84), entering on the same side or before the decay completes guarantees adverse selection.
- Enforce hard veto: When `VPIN > 0.80`, block all burst entries for $2.5 \cdot \tau_{\text{half}}$ seconds.
- Trigger the Hawkes FadeQuote on the opposite book touch only when toxicity decay velocity $\frac{d\text{VPIN}}{dt} < -0.15/\text{min}$.

### C. Continuous Carry Execution (Binance ↔ Hyperliquid Basis Arbitrage)
- During the 2.5 hours of afternoon chop, market price went nowhere, but funding accrual was continuous.
- Cross-venue funding carry (+14.6 bps holdout edge across 25 altcoins) represents the optimal capital allocation during zero-momentum regimes.

---

## 4. Required Implementation Specifications

Deliver production-ready Python modifications covering the following 4 tasks:

### Task 1: Complete CHOP Gating for Burst Lane (`core/burst_lane.py`)
1. In `core/burst_lane.py`, inspect `e.router_state` or symbol `KER`.
2. When the regime is `CHOP` (or `KER < 0.25`), suppress all entry orders (both active and shadow), logging:
   `BURST OBSERVE: CHOP regime active (KER < 0.25) -> entry hibernated`.
3. This single gating change mathematically eliminates ~70% of the negative drift trades observed in the afternoon log.

### Task 2: Strict VPIN Toxicity Hard Veto in `core/burst_lane.py`
1. Wire `AsymmetricToxicityGuard.state.cancel_all` directly into the burst entry condition.
2. If `VPIN > 0.80` on the target symbol within the last 60 seconds, veto any new position entry.
3. Eliminate the -10.6 bps loss observed at 15:27:18 immediately following the VPIN 0.84 spike.

### Task 3: Real-Time Telemetry & Funding Carry Integration
1. Ensure `core/funding_executor.py` quotes are exposed in the engine status loop so the Web UI (`http://127.0.0.1:8765`) displays real-time candidate spreads and annualized APR during CHOP.
2. Compute the exact theoretical carry yield generated during today's 2.5-hour consolidation window.

### Task 4: Production Python Code & Test Validation
1. Output clean, surgical Python diffs for `core/burst_lane.py` and `core/engine.py`.
2. Add dedicated test cases in `tests/test_chop_burst_gating_2026_10_16.py` verifying:
   - Burst entry is blocked when router is in CHOP.
   - VPIN toxicity spikes immediately veto new entries.
   - Normal operation resumes smoothly when KER exceeds the threshold.
3. Ensure all existing 1,590 unit tests pass 100% without regression.

---

## 5. Verification & Acceptance Criteria

1. **Test Coverage:** All unit tests must pass (`python -m pytest -q` yields $\ge 1,596$ passing tests).
2. **Capital Protection:** 100% of negative drift trades during range-bound chop are successfully prevented.
3. **Execution Latency:** Internal per-tick calculation cycle remains strictly $< 50\text{ µs}$.
4. **Safety Guarantee:** Live Mainnet trading remains locked; capital is 100% protected.
