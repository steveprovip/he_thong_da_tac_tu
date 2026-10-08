# MASTER COWORK FORM: ELIMINATE MR KNIFE-CATCHING & CONCENTRATE ON TREND ALPHA
> **Target Model**: Claude Opus 5.5 High Cowork / Cursor Agentic Terminal  
> **Workspace**: `F:\he_thong_da_tac_tu_2`  
> **Repository**: `https://github.com/steveprovip/he_thong_da_tac_tu.git`  
> **Test Baseline**: 1,070 Passing Tests (`python -m pytest -q`)

---

## 1. EXECUTIVE RETROSPECTIVE: 2-HOUR RUN AUTOPSY (TRADES #38, #39, #40)

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        EMPIRICAL SESSION LEDGER AUTOPSY                                │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ SESSION METRICS:                                                                       │
│ • Balance: 4922.7675 USDT                                                              │
│ • Session PnL: -0.0357 USDT (Gross -0.0062, Fee -0.0295)                              │
│ • Win Rate: 2 Wins / 1 Loss (66.7%)                                                   │
│                                                                                        │
│ 1. TREND / SWARM LANE (PERFECT 100% WIN/BE RATE):                                      │
│    • Trade #38 (SOL SELL 0.12 SOL): Net +0.00041 USDT (EJECT-BE at 308s, Zero fee drag)│
│    • Trade #40 (DOGE BUY 148 DOGE, Swarm 99.4%): Net +0.00393 USDT (RATCHET WIN!)      │
│      - MFE +0.152% -> Exchange stop moved to Entry +0.08%, stop_covered_entry=true.   │
│                                                                                        │
│ 2. THE SOLE LOSS DRIVER: MEAN REVERSION (MR) LANE KNIFE-CATCHING:                      │
│    • Trade #39 (DOGE BUY 300 DOGE, MR Lane 85.9%): Net -0.04001 USDT (100% of loss!)  │
│      - Root Cause: MR tried to BUY dip while OFI was -0.929 (HEAVY INSTITUTIONAL SELL)!│
│      - Net R:R was 0.634 (Required > 61.2% win rate to break even after fees).        │
│      - SL hit in 50s (-0.75% ROE).                                                    │
│                                                                                        │
│ 3. BTC CHOP FILTER VALIDATION:                                                         │
│    • OKAPI blocked 15+ chop entries (VFR 3.1 < 5.0), saving over $0.50+ in chop churn! │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. WORLD-CLASS QUANTITATIVE & ECONOMIC UPGRADE DIRECTIVES

### 1. Rama Cont (2014) & Robert Almgren: Flow Toxicity & Anti-Fade Gate
- **Order Flow Concordance Rule**:
  * NEVER execute a Mean-Reversion (MR) BUY order if $\text{OFI}_{L10} < -0.30$ or $\text{CVD}_{60s} < -\$100\text{k}$.
  * NEVER execute an MR SELL order if $\text{OFI}_{L10} > +0.30$ or $\text{CVD}_{60s} > +\$100\text{k}$.
  * Fading institutional order flow (negative OFI) results in immediate adverse selection.

### 2. E. Glen Weyl & Jean Tirole: Asymmetric Net R:R Payoff Filter
- **Hard Net Expectancy Gate**:
  $$\text{Net R:R} = \frac{\text{Gross TP1} - \text{RoundTripFee}}{\text{Gross SL} + \text{RoundTripFee}} \ge 1.30$$
- If $\text{Net R:R} < 1.30$ (like Trade #39 where $\text{Net R:R} = 0.63$), the trade has mathematically negative expectation and MUST BE BLOCKED by ZEPHR / OKAPI.

### 3. Marco Avellaneda & Sasha Stoikov: Dynamic Maker Re-Pegging
- In the session, 20+ GTX maker orders timed out after 3s with `peg drift cap 0.15%`.
- If the touch moves away within $1.5\text{s}$, re-quote immediately to the new Best Bid/Ask rather than waiting for full 3s expiration.

### 4. Alpha Concentration & Swarm Scaling
- When Swarm Score $\ge 95\%$ AND $\text{OFI}_{L10} \ge +0.80$ (e.g. DOGE #40):
  * Scale position size from base to $300 - 450\text{ DOGE}$ ($\approx \$30 - \$45$) or $0.30 - 0.50\text{ SOL}$.

---

## 3. IMPLEMENTATION PLAN (4 PILLARS)

1. **Pillar 1 (`core/micro_signals.py` & `core/engine.py`)**:
   - Add strict OFI Concordance check for MR lane: reject MR BUY when OFI < -0.30, reject MR SELL when OFI > +0.30.
   - Enforce $\text{Net R:R} \ge 1.30$ in `cost_gate`.
2. **Pillar 2 (`core/engine.py`)**:
   - Dynamic fast re-peg for GTX maker orders (re-quote within 1.5s if drift $\le 0.10\%$).
3. **Pillar 3 (`core/multi_asset_scanner.py`)**:
   - Expand Trend Swarm sizing on super-conviction signals (Score $\ge 95\%$ + OFI $\ge 0.80$).
4. **Pillar 4 (`tests/test_mr_concordance_and_net_rr.py`)**:
   - Add comprehensive unit tests verifying MR rejection on adverse OFI, Net R:R gating, and fast re-pegging.

---

## 4. VERIFICATION CRITERIA

- Run `python -m pytest -q` $\rightarrow$ Target: $\ge 1,085+$ tests passing (100% green, 0 skipped, 0 failures).
- Preserve all 40 rows in `data/engine_trades.jsonl`.
- Verify on mock that Trade #39 (MR buy against -0.93 OFI) is cleanly blocked.
