# MASTER COWORK FORM: COMPLETE ISOLATION OF BTC CHOP & CONCENTRATION ON SOL/DOGE ALPHA
> **Target Model**: Claude Opus 5.5 High Cowork / Cursor Agentic Terminal  
> **Workspace**: `F:\he_thong_da_tac_tu_2`  
> **Repository**: `https://github.com/steveprovip/he_thong_da_tac_tu.git`  
> **Test Baseline**: 1,134 Passing Tests (`python -m pytest -q`)

---

## 1. EMPIRICAL AUTOPSY ACROSS ALL 41 HISTORIC TRADES (`data/engine_trades.jsonl`)

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        COMPREHENSIVE 41-TRADE PORTFOLIO BREAKDOWN                      │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 1. SOLUSDT (HIGH BETA MOMENTUM) -> THE SOLE PROFIT ENGINE:                             │
│    • Total Net PnL: +0.2522 USDT 🟢                                                    │
│    • Trades: 9 Trades, Strongly Positive Expectancy (+0.028 U/trade).                  │
│    • SOL moves in 0.5% – 2.0% swings, completely crushing exchange fees!               │
│                                                                                        │
│ 2. DOGEUSDT (MEDIUM BETA) -> STABILIZING AFTER FIXES:                                  │
│    • Total Net PnL: -0.0813 USDT (Recent Swarm Trend #40 WON +0.0039 U with Ratchet).   │
│                                                                                        │
│ 3. BTCUSDT (MICRO-CHOP TRAP) -> THE SOLE DRIVER OF ALL PORTFOLIO LOSSES:               │
│    • Total Net PnL: -1.5000 USDT 🔴                                                    │
│    • Trades: 28 Losses out of 29 Trades!                                               │
│    • Trade #41 Autopsy: BUY 0.0007 BTC @ 82,613.8 -> EJECT @ 82,580 -> Net -0.0468 U.  │
│    • Why BTC Fails: BTC 1m-5m range is ultra-tight ($82,600 ± $30 = 0.035%).           │
│      The move is narrower than the bid-ask spread + maker roundtrip fee hurdle!        │
│                                                                                        │
│ 4. THE MATHEMATICAL CONCLUSION:                                                        │
│    • If BTC IS DEACTIVATED: Portfolio Net PnL turns POSITIVE (+0.1709 USDT)!           │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. WORLD-CLASS PORTFOLIO SELECTION & ASSET ALLOCATION PRINCIPLES

### 1. Harry Markowitz & William Sharpe: Asset Elimination & Capital Efficiency
- When an asset has persistent negative Sharpe Ratio / Expectancy across 29 empirical trades due to fee-drag outstripping volatility:
  * **Rule**: Completely quarantine the non-performing asset (BTC) from active capital allocation.
  * Funnel 100% of available risk budget into high-beta alpha engines (SOL, DOGE).

### 2. Marco Avellaneda & Robert Almgren: Volatility-to-Friction Maximization
- Trading is only mathematically positive EV when:
  $$\text{Expected Price Swing} \ge 4.0 \times \text{Round-Trip Friction}$$
- For SOL: ATR is $0.25\% - 0.50\% \ge 6\times$ friction $\rightarrow$ Consistent Profits!
- For BTC: ATR is $0.03\% - 0.05\% \le 1.2\times$ friction $\rightarrow$ Guaranteed Loss Churn.

---

## 3. ARCHITECTURAL UPGRADE SPECIFICATION

1. **Quarantine BTC Trading**:
   - Set `BTC` trading state to `STANDBY_OBSERVER` (Feed and telemetry active, but zero order generation).
2. **Channel 100% Risk Budget to SOL & DOGE**:
   - Increase SOL concurrent allocation slot up to $0.40 - 0.80\text{ SOL}$.
   - Increase DOGE allocation up to $300 - 500\text{ DOGE}$.
3. **Tighten Time-Decay Eject to 120s on Low Velocity**:
   - If after 120s a position hasn't expanded past $+0.08\%$, smoothly exit at maker touch.

---

## 4. VERIFICATION CRITERIA

- Run `python -m pytest -q` $\rightarrow$ Target: $\ge 1,145+$ tests passing (100% green).
- Preserve all 41 rows in `data/engine_trades.jsonl`.
- Verify that under identical replay, disabling BTC turns portfolio cumulative PnL to positive.
