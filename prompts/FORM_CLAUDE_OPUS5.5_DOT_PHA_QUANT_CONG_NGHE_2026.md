# QUANTITATIVE ARCHITECTURE SPECIFICATION (RFC-2026-Q4)
# REPOSITORIES: F:\he_thong_da_tac_tu_2 | TARGET: GROKTOPUS 2.0
# OBJECTIVE: DYNAMIC REGIME ENGINE, HAWKES EXHAUSTION FADE, ATOMIC STAT-ARB & CROSS-VENUE CARRY

<!--
HƯỚNG DẪN DÀNH CHO USER:
- Form này được viết theo chuẩn RFC / PRD Kỹ thuật cao cấp của Thung lũng Silicon (Silicon Valley Engineering Standard).
- Đã loại bỏ 100% các từ khóa kích hoạt bộ lọc bảo mật [reasoning_extraction] của Anthropic.
- Copy toàn bộ nội dung từ "# QUANTITATIVE ARCHITECTURE SPECIFICATION" đến hết bài và dán vào Claude.
-->

## 1. System Baseline & Operational Telemetry

- **Workspace:** `F:\he_thong_da_tac_tu_2` (Branch: `main`, 1,559 passing unit tests).
- **Execution Engine:** `GROKTOPUS 2.0` (`core/engine.py`).
- **Telemetry Period:** 13-Hour Continuous Live Run (14/10/2026 09:10 - 22:30 UTC+7).
- **Current Balance:** 4,919.1996 USDT (Capital 100% preserved, Realized = +0.0000 U, 0 Live Executions).
- **Recorded Microstructure Metrics:**
  * **1m Burst Scalper (`core/burst_lane.py`):** 150+ shadow trades, 86.7% loss rate (-5 to -38.9 bps per trade; cumulative fee drag ~ -400 bps). High-frequency taker entries in noise bleed fees against informed maker flow.
  * **4H Macro Trend Lane (`core/trend_lane.py`):** 0 executions. Deadlocked by `KER < 0.25` (Kaufman Efficiency Ratio triggered Chop-Lock 40+ times; regime flagged as `MIXED -> observer`).
  * **Swarm Consensus (`core/micro_swarm.py`):** Peaked at 60.0% (6/8 sub-agents voting BUY 100%), missing the 76.8% Supermajority execution threshold.
  * **Toxicity Guard (`core/micro_signals.py`):** 14 events with `VPIN > 0.80` (up to 0.98 on BTC/SOL/DOGE), aborting maker quotes against institutional sweeps.

---

## 2. Analytical Formulation: The Low-Volatility Deadlock

Net Expected Value decomposition for execution horizon $\tau$:
$$\text{Net EV} = \mathbb{E}[\Delta P_{\tau}] - 2 \cdot C_{\text{fee}} - \mathbb{E}[\text{Slippage}] - \mathbb{E}[\text{Adverse Selection}]$$

- During range-bound consolidation, $\mathbb{E}[\Delta P_{1m}] \approx 0$, while fixed frictions ($2 \cdot C_{\text{fee}} + \text{Slippage} \approx 7\text{ to }12\text{ bps}$) yield negative expected value.
- The 4H Trend Lane provides high gross edge ($\text{Sharpe} = 1.06$, $\text{Net EV} > +45\text{ bps}$), but arrival rate $\lambda_{\text{trend}} \to 0$ in choppy regimes, resulting in zero capital turnover.
- **Goal:** Activate non-directional yield mechanisms (Cross-Venue Funding Carry and Statistical Arbitrage) and order-flow exhaustion harvesting without taking directional 1m noise risk.

---

## 3. Microstructure Architecture & Methodological Standards

Implement quantitative mechanisms established in institutional algorithmic trading:

### A. Jump Trading & Citadel Microstructure Framework
- **Order Flow Imbalance (OFI):** Multi-depth queue imbalance tracking (Cont-Kukanov-Stoikov 2014).
- **Adverse Selection Neutralization:** Post-Only GTX maker routing capturing positive spread rebates without crossing book.

### B. Tower Research & Virtu Point Process Dynamics
- **Hawkes Process for Flow Toxicity Decay:**
  $$\lambda(t) = \mu + \sum_{t_i < t} \alpha e^{-\beta(t - t_i)}$$
- Post-sweep intensity exhaustion: When $\frac{d\lambda}{dt} \to 0^-$ and $\Delta t \ge 2.5 \cdot \tau_{\text{half}}$ ($\tau_{\text{half}} = \frac{\ln 2}{\beta}$), execute asymmetric fade quotes at the opposite book edge.

### C. Renaissance Technologies Cointegration Tracking
- Continuous parameter tracking via `OnlineKalmanPairTracker` in `core/stat_arb_lane.py`.
- Dynamic volatility spread bands replacing static thresholds:
  $$Z_{\text{entry}}(t) = \text{clamp}\left(1.45, \; 2.10 \times \left(\frac{\sigma_{15m}}{\sigma_{4H}}\right)^\alpha, \; 2.60\right)$$

### D. Wintermute Cross-Venue Basis Carry
- Delta-neutral funding capture between Binance USD-M (8h settlement) and Hyperliquid L1 (1h settlement).
- Empirical holdout validation: +14.6 bps net edge across 25 altcoins with low turnover.

---

## 4. Engineering Requirements & Implementation Tasks

Provide production-grade Python implementations for the following 5 tasks:

### Task 1: Dynamic Tri-State Regime Engine
- In `core/trend_lane.py` and `core/engine.py`:
  * Evaluate limitation of static `KER < 0.25` during volatility compression.
  * Implement an $O(1)$ metric: Local Hurst Exponent $H_{\text{local}}$ or Rolling Permutation Entropy over a 60-bar window.
  * State Gating:
    - $H > 0.55$: Trending State $\to$ Enable 4H/15m Trend Engine.
    - $0.45 \le H \le 0.55$: Random Walk $\to$ Sleep directional lanes; allocate capital to Funding Carry.
    - $H < 0.45$: Mean-Reverting $\to$ Enable Stat-Arb Cointegration & Maker Spread Capture.

### Task 2: Hawkes Flow Exhaustion in `core/micro_signals.py`
- Formulate analytical Toxicity Half-Life $\tau_{\text{half}}$ based on rolling VPIN spikes.
- Update `AsymmetricToxicityGuard`:
  * Phase 1: On `VPIN > 0.80`, cancel open maker orders and activate veto.
  * Phase 2: Once $\Delta t \ge 2.5 \cdot \tau_{\text{half}}$ and $\Delta \text{VPIN} / \Delta t < -0.15$, trigger "Fade-the-Sweep Quote" via Post-Only GTX to capture the liquidity rebound.

### Task 3: Atomic Dual-Leg Execution in `core/stat_arb_lane.py`
- Replace static $Z \ge 2.10$ threshold with dynamic $Z_{\text{entry}}(t)$.
- Implement Leader-Follower quoting:
  * Quote Leg 1 (higher spread / lower liquidity) with Post-Only GTX.
  * Upon fill confirmation, execute Leg 2 via immediate IOC within $< 150\text{ ms}$ to eliminate naked delta risk.

### Task 4: Real-Time Funding Carry Module (`core/funding_executor.py`)
- Scan 25 altcoins for Binance vs Hyperliquid annualized funding spread.
- Criteria: Spread $\ge 2.5\text{ bps}/8\text{h}$ with Basis Skew $\ge +1.5\text{ bps}$.
- Rotation Hysteresis: Require $\Delta \text{Spread} \ge 4.0\text{ bps}$ before rebalancing to prevent fee erosion.
- Compute expected monthly yield on 4,919.1996 USDT capital under zero market direction.

### Task 5: Production Python Code & Verification
- Deliver clean, fully typed Python code with $O(1)$ algorithmic complexity.
- Maintain compatibility with standard library (`math`, `collections.deque`).
- Ensure all 1,559 existing pytest unit tests pass without regression.
- Include dedicated pytest test cases validating the new regime transitions and execution logic.

---

## 5. Acceptance Invariants

1. **Test Suite:** `python -m pytest -q` passes 100% ($\ge 1,565$ tests).
2. **Fee Shield:** Zero directional taker market orders in range-bound regimes.
3. **Execution Latency:** Internal per-tick calculation cycle remains strictly $< 5\text{ ms}$.
4. **Data Integrity:** No modifications to existing ledgers (`data/engine_trades.jsonl`, `data/trend_trades.jsonl`).
