# MASTER COWORK FORM: CONTINUOUS ACTIVE TRADING & MULTI-REGIME QUANTITATIVE OVERHAUL
> **Target Model**: Claude Opus 5.5 High Cowork / Cursor Agentic Terminal  
> **Workspace**: `F:\he_thong_da_tac_tu_2`  
> **Repository**: `https://github.com/steveprovip/he_thong_da_tac_tu.git`  
> **Test Baseline**: 1,102 Passing Tests (`python -m pytest -q`)

---

## 1. EXECUTIVE RETROSPECTIVE: THE IDLE BOTTLENECK AUTOPSY

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        EMPIRICAL IDLE BOTTLENECK DIAGNOSIS                             │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ OBSERVED TELEMETRY (SNAPSHOT AT 11:05 SAIGON):                                         │
│ • BTC: CHOPPY - LOCKED (KER 0.13 < 0.55, Box 0.034% -> 100% Blocked)                   │
│ • DOGE: DRIFT LOCK (CVD 60s +$4.51M vs 3m -$0.52M Conflict -> 100% Blocked)            │
│ • SOL: MR SCAN (MR Net R:R < 1.30 Gate -> 100% Blocked)                                │
│                                                                                        │
│ THE PROBLEM: ALL 3 ASSETS ARE CONCURRENTLY LOCKED BY BINARY HARD GATES!                │
│ • System has zero open positions and generates 0 trades for 1-2 hours straight.       │
│ • Binary gates (ALL or NOTHING) cause severe opportunity starvation.                   │
│ • In low volatility, freezing completely means earning 0 profit while market moves.    │
│                                                                                        │
│ THE GOAL: CONTINUOUS TRADING FREQUENCY WITH RIGOROUS RISK-ADJUSTED PROFITABILITY       │
│ • Transform binary hard gates into continuous sizing & multi-regime execution.         │
│ • Trade actively in BOTH Chop (Micro-Spread Scalping) and Trend (Momentum Breakout).  │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. WORLD-CLASS HFT & QUANTITATIVE ECONOMIC PRINCIPLES

### 1. Marco Avellaneda & Sasha Stoikov (2008): Active Market Making in Chop Regimes
- In low volatility / chop regimes ($\text{KER} < 0.40$), do NOT freeze!
- Switch to **Micro-Spread Liquidity Capture (GTX Maker Scalping)**:
  * Quote at the touch (Best Bid for BUY, Best Ask for SELL).
  * Target: $\text{Gross TP} = +0.08\%$ to $+0.12\%$ with $100\%$ Maker fills ($4\text{ bps}$ total round-trip fee $\rightarrow$ Net $+0.04\%$ to $+0.08\%$ profit per cycle).
  * Time-stop: Eject at $90\text{s}$ at net breakeven if price fails to move.

### 2. Jean Tirole & Robert Almgren: Continuous Confidence Sizing Curve
- Replace binary locks with **Dynamic Sizing Probes**:
  $$\text{Position Size} = \text{Base Size} \times \text{Regime Multiplier} \times \text{Confidence}$$
- **Mild Drift / Flow Conflict**: Instead of 100% block, open a **Micro-Probe** ($0.10\text{ SOL} \approx \$11$ or $100\text{ DOGE} \approx \$9$) with tight $90\text{s}$ breakeven stop.
- **Pristine Trend Alignment**: Open full scale ($0.50\text{ SOL}$ or $450\text{ DOGE}$) with profit ratchet.

### 3. Rama Cont (2014) & David Easley: Fast Flow Asymmetry Resolution
- If $\text{CVD}_{60s}$ is massively positive ($> +\$2\text{M}$) while $\text{CVD}_{3m}$ is slightly negative (lagging indicator):
  * Recognize the **Inflection Point Acceleration**: Short-term flow takes priority over stale multi-minute history. Allow trade entry with adaptive probe size.

---

## 3. ARCHITECTURAL SPECIFICATION (4 PILLARS)

### Pillar 1: Multi-Regime Dual Engine (Chop Scalper vs Trend Breakout) (`core/engine.py`)
1. **Trend Engine ($\text{KER} \ge 0.40$)**:
   - Standard Swarm momentum entry. $\text{TP} = +0.25\% - 0.50\%$, $\text{SL} = 0.20\%$, Ratchet active.
2. **Chop Micro-Scalper ($\text{KER} < 0.40$)**:
   - Active Maker GTX entry at touch when $\text{Spread} \le 1.5\text{ bps}$.
   - Calibrated $\text{TP} = +0.08\%$ (2x round-trip fee), $\text{SL} = 0.12\%$.
   - $90\text{s}$ Breakeven exit rule: if unfilled/flat after 90s, close maker at $\text{Entry} + 0.04\%$.

### Pillar 2: Continuous Sizing & Micro-Probe Flow Gate (`core/micro_signals.py`)
1. **Conflict Resolution**:
   - If $|\text{CVD}_{60s}| \ge 3 \times |\text{CVD}_{3m}|$ (momentum acceleration): Clear drift lock and authorize entry at $0.5\times$ base size.
2. **Non-Blocking Net R:R Scaling**:
   - For Micro-Scalper trades: Allow $\text{Net R:R} \ge 1.00$ provided Maker fee ($4\text{ bps}$) is guaranteed and $\text{Gross TP} \ge 0.08\%$.

### Pillar 3: Fast-Scan Opportunity Multiplexer (`core/engine.py`)
1. **Zero-Wait Polling**:
   - If an asset is in hard lock, immediately yield execution slot to other active assets within 100ms.
2. **Active Order Renewal**:
   - Keep resting GTX maker orders fresh by re-quoting at touch when queue depth thins out.

### Pillar 4: Comprehensive Test Suite (`tests/test_continuous_active_trading.py`)
1. Unit tests verifying active Chop Scalper execution on low KER.
2. Unit tests verifying CVD 60s/3m inflection acceleration resolution.
3. Unit tests verifying continuous micro-probe sizing without portfolio over-allocation.

---

## 4. ACCEPTANCE & VERIFICATION CRITERIA

- Run `python -m pytest -q` $\rightarrow$ Target: $\ge 1,125+$ tests passing (100% green, 0 skipped, 0 failures).
- Preserve all 40 lines in `data/engine_trades.jsonl`.
- Verify on mock that the 11:05 snapshot conditions generate valid micro-scalp and inflection trades instead of 100% idle lockout.
