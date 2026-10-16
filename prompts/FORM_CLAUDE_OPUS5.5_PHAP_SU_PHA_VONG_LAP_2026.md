# QUANTITATIVE ARCHITECTURE SPECIFICATION: BREAKING THE DOOM LOOP (RFC-2026-Q4.4)
# REPOSITORIES: F:\he_thong_da_tac_tu_2 | TARGET: GROKTOPUS 2.0
# SCOPE: ROOT CAUSE ANALYSIS OF 1M CHURN DOOM LOOP (16:51 - 20:52 UTC+7, 16/10/2026), HORIZON PIVOT (15M/1H), DELTA-NEUTRAL CARRY PROMOTION & VPIN 1.00 EXHAUSTION FADE

## 1. System Baseline & Operational Invariants

- **Active Workspace:** `F:\he_thong_da_tac_tu_2` (Branch: `main`, 1,600 passing unit tests, clean tree).
- **Execution Mode:** Binance Demo Futures (BTCUSDT 0.001, SOLUSDT, DOGEUSDT, Cross 5x).
- **Session Duration:** 16:51:37 to 20:52:59 UTC+7 (4 hours 21 minutes continuous execution).
- **Capital Status:** 4,919.1996 USDT (100% capital preserved, Live trades locked).
- **Baseline Engine State:**
  * Tests Passing: 1,600 / 1,600 unit tests pass in 126s.
  * Invariants: Ledgers `data/engine_trades.jsonl` and `data/trend_trades.jsonl` must remain intact. All modifications must be strictly $O(1)$ algorithmic complexity.

---

## 2. Forensic Autopsy: Deconstructing the "Doom Loop"

Between 16:51:37 and 20:52:59, the system demonstrated the classic algorithmic "Doom Loop" (Fix -> Run -> Bleed / Freeze -> Fix):

```text
======================================================================================
LOG CHRONOLOGY & FORENSIC EVIDENCE (16:51:37 -> 20:52:59)
======================================================================================
1. Episode 1 (16:52 -> 17:00): Over-Suppression / Noise Spam
   - The new CHOP gate successfully hibernated entries, but spammed "BURST OBSERVE: CHOP ... hibernated" 
     over 40 times in rapid succession.

2. Episode 2 (17:01 -> 17:03): The 2-Minute Fatal Trap
   - At 17:01:07: BTC signaled VPIN TOXIC 0.82 (SELL flow).
   - At 17:01:36: SOL MAKER-EXIT -25.7 bps (-0.191 U)
   - At 17:01:39: DOGE BUY SL -37.4 bps (-0.280 U)  <-- The disaster stop was hit!
   - At 17:02:07: Router mistakenly adapted "NORMAL -> BURST" (KER > 0.5 & CVD > $1M).
   - At 17:03:04: DOGE BUY MAKER-EXIT -7.7 bps (-0.057 U)
   - At 17:03:32: SOL BUY MAKER-EXIT +2.6 bps (+0.019 U)
   - Net Loss in 2 minutes: -65.6 bps (-0.490 U).

3. Episode 3 (17:05 -> 20:52): 3.5 Hours of Complete Paralysis
   - At 17:05:23: SOL signaled extreme VPIN TOXIC 1.00 > 0.8 (institutional liquidation wave).
   - For the next 3 hours 47 minutes: ZERO trades were executed.
   - 12 VPIN TOXIC events (ranging from 0.80 to 1.00) fired across BTC and SOL.
   - 19:01:04: DOGE 4H Trend held back due to regime MIXED.
   - Result: 0 capital turnover, 0 cash flow, 100% frozen capital.
```

### The Mathematical Diagnosis of the Doom Loop:
1. **Sub-second Co-location Disadvantage:** In 1m crypto futures, non-co-located algorithms (50-100ms latency) cannot extract directional momentum alpha against HFT market makers. $\mathbb{E}[\Delta P_{1m}] - \text{AdverseSelection} - \text{Friction} < 0$.
2. **Binary Paralysis Dilemma:** 
   - Loose gating $\implies$ Overtrading in noise, losing -400 to -700 bps to fee drag.
   - Tight gating $\implies$ Complete operational paralysis (3.5 hours of 0 trades), punctuated by getting trapped on false breakout spikes (17:02:07).

---

## 3. World-Class Prop Desk Literature & Wizard Breakthroughs

To permanently break the Doom Loop, implement the foundational architecture used by tier-1 quantitative prop firms (Citadel, Wintermute, Jane Street):

### Pillar A: Horizon Pivot – Shift Directional Alpha from 1m Noise to 15m/1H Swing
- The 1m horizon is dominated by microstructure noise; the 4H horizon is too infrequent.
- The institutional "Sweet Spot" is **15-minute to 1-hour momentum** (Cartea-Jaimungal, Guéant 2016):
  * Gross edge is $+80\text{ to }+250\text{ bps}$, making 5 bps fees mathematically negligible.
  * Trade frequency: 3 to 8 high-conviction trades per day instead of 100 noise gambles.

### Pillar B: Delta-Neutral Carry Promotion as Primary Yield Engine
- While directional alpha waits for real momentum, **Cross-Venue Funding Carry (`core/funding_executor.py`)** must actively generate continuous cash flow.
- Earning 15% to 35% APR risk-free basis carry between Binance and Hyperliquid replaces zero-velocity paralysis with compounding daily yield.

### Pillar C: Hawkes Point Process – Fading the VPIN 1.00 Exhaustion
- The log recorded: `[17:05:23] SOL · VPIN TOXIC 1.00 > 0.8 (SELL flow)`.
- In market microstructure, $\text{VPIN} \to 1.00$ indicates terminal liquidation exhaustion (order flow climax).
- Once intensity $\lambda(t)$ peaks and decays ($\Delta t \ge 2.5 \cdot \tau_{\text{half}}$), the market experiences an immediate liquidity vacuum rebound. Fading this exhaustion yields $> 75\%$ win rate with positive asymmetric payoff.

---

## 4. Required Implementation Specifications

Deliver production-ready Python modifications covering the following 4 tasks:

### Task 1: Complete Demotion of 1m Directional Taker Scalping
1. In `core/burst_lane.py`, permanently disable market-taking momentum breakout entries during compression regimes.
2. Fix the log spam: Consolidate `BURST OBSERVE: CHOP ... hibernated` so it logs strictly once per regime entry and remains silent while hibernating.

### Task 2: Activation of 15m/1H Swing Momentum Engine
1. In `core/intraday_lane.py` and `core/engine.py`, elevate the 15m Intraday Swing Lane (`R1_SESSION` / `Donchian ATR`) to become the primary directional alpha vehicle.
2. Entry rules: Confirm 15m trend alignment with 1H MACD/TSMOM and Volume Ratio $> 1.5\text{x}$.
3. Target: Reward/Risk ratio $\ge 2.5 : 1$ with minimum target $+80\text{ bps}$ and stop loss $-30\text{ bps}$.

### Task 3: Hawkes VPIN 1.00 Climax Reversal Engine
1. In `core/micro_signals.py` and `core/engine.py`, wire the `FadeQuote` logic to trigger specifically after extreme toxicity spikes ($\text{VPIN} \ge 0.85$ up to $1.00$):
   - Wait for flow deceleration: $\frac{d\text{VPIN}}{dt} < -0.15/\text{min}$.
   - Post an asymmetric Post-Only GTX maker quote on the opposite book touch to capture the mean-reversion snapback.

### Task 4: Real-Time Funding Carry Allocation Engine
1. In `core/engine.py`, allocate active virtual balance to `core/funding_executor.py` when directional lanes are hibernating.
2. Stream real-time annualized APR to the Web UI (Card T6) and verify zero naked delta risk.
3. Write unit tests in `tests/test_break_doom_loop_2026_10_16.py` verifying all 4 tasks pass $\ge 1,608$ tests.

---

## 5. Acceptance Invariants

1. **Test Suite:** `python -m pytest -q` passes 100% ($\ge 1,608$ unit tests).
2. **Log Hygiene:** Zero log spam during hibernation states.
3. **Execution Latency:** Internal per-tick calculation cycle remains strictly $< 50\text{ µs}$.
4. **Safety Guarantee:** Live Mainnet trading remains locked; capital is 100% protected.
