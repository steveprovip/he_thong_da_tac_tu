# MASTER COWORK FORM: TOP-TIER ECONOMIC & QUANTITATIVE HFT ENGINE UPGRADE
> **Target LLM Agent**: Claude Opus 5.5 High Cowork / Cursor Agentic Terminal  
> **Workspace**: `F:\he_thong_da_tac_tu_2`  
> **Execution Mode**: Strict Pair-Programming / Zero AI Slop / Surgical Implementation  
> **Repository**: `https://github.com/steveprovip/he_thong_da_tac_tu.git`  
> **Baseline Test Count**: 1,007 Passing Tests (`python -m pytest -q`)

---

## 1. EXECUTIVE RETROSPECTIVE & 34-TRADE LEDGER AUTOPSY

### Current System Financial Telemetry (`data/engine_trades.jsonl` - 34 Trades)
- **Account Balance**: `4,922.8412 USDT` (Initial 4,924.00 USDT)
- **Realized Net PnL**: `-1.2256 USDT`
- **Gross Market PnL**: `+0.8885 USDT` (**ALPHA IS POSITIVE! Market direction timing works!**)
- **Total Exchange Fees Paid**: `-2.1141 USDT` (**FEE DRAG IS 238% OF GROSS GAINS!**)
- **Realized Win Rate**: $9\text{ Wins} / 25\text{ Losses} = 26.47\%$

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        EMPIRICAL 34-TRADE ROOT CAUSE AUTOPSY                           │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 1. THE FEE CANNIBALIZATION PARADOX (9 TRADES):                                         │
│    • 9 trades (#7, #10, #13, #14, #18, #20, #22, #33, #34) had GROSS PnL ≥ 0.00 USDT,   │
│      yet ended in NET LOSSES because exchange fees (4–10 bps) wiped out 100% of profit.│
│    • Real market timing win rate is 18 / 34 = 52.94% (Gross Positive / Breakeven).     │
│                                                                                        │
│ 2. THE ASSET VOLATILITY DIVERGENCE:                                                    │
│    • SOL TRADES (High Beta): Generated +0.2275 USDT NET PROFIT!                        │
│      - Trade #27: +0.1024 U (Gross +0.1404, MFE +0.20%, Ratchet Active)               │
│      - Trade #29: +0.0189 U (Gross +0.0378, MFE +0.09%)                                │
│      - Trade #32: +0.2085 U (Gross +0.2295, MFE +0.44%, Massive Win!)                  │
│      - SOL price swings (0.8% – 2.0%) completely crush the 4 bps round-trip maker fee. │
│    • BTC TRADES (Dead Low Volatility): Generated -1.4531 USDT NET LOSS.                │
│      - BTC price moved in a tight $83,480–$83,600 box (0.09% range).                   │
│      - The range is narrower than the bid-ask spread + maker fee hurdle.               │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. WORLD-CLASS ECONOMIC & QUANTITATIVE FINANCE FOUNDATIONS

To transform this system into a profitable institutional-grade execution organism, implement the following peer-reviewed frameworks:

### 1. Robert Almgren & Neil Chriss (2000) / Rama Cont (2014): Optimal Execution & OFI Thresholding
- **Pure Post-Only (GTX) Maker Execution**:
  * Eliminate all `IOC` (Immediate-or-Cancel) taker market orders from regular trading flow.
  * Taker fee is $0.05\%$ (5 bps) vs Maker fee $0.02\%$ (2 bps). Crossing the spread costs $2.5\times$ more!
  * Enforce strict `GTX` peg orders at Best Bid / Best Ask with dynamic timeout $\tau \le 3000\text{ms}$. If unfilled, cleanly cancel and recalculate without crossing.
- **Order Flow Imbalance (OFI) Strict Gating**:
  * Only execute when $|\text{OFI}_{L10}| \ge 0.65$ with book delta concordance $\ge 70\%$.

### 2. Marco Avellaneda & Sasha Stoikov (2008) / Maureen O'Hara (VPIN): Asset Volatility Siphoning
- **Dynamic Volatility-to-Fee Ratio ($VFR$)**:
  $$\text{VFR} = \frac{\text{ATR}_{15m} (\text{Asset})}{\text{RoundTripFeeBps} + \text{SpreadBps}}$$
- **Rule**: If $\text{VFR} < 3.0$ (e.g., BTC when ATR is $0.05\% \le 5\text{ bps}$ and fee is $4\text{ bps} \rightarrow \text{VFR} = 1.25$), **BTC risk allocation is strictly throttled to 0.0 USDT** (or $0.0001\text{ BTC}$ micro-probe).
- Reroute $85\%+$ of trading capital to assets with $\text{VFR} \ge 5.0$ (SOL, DOGE).

### 3. E. Glen Weyl & Jean Tirole (2012) / Benoît Mandelbrot: Asymmetric Fee-Hurdle Take-Profit
- **Minimum Net Profit Margin ($MNPM$)**:
  $$\text{Target Gross TP1} \ge 3.0 \times \text{Round-Trip Fee} = 3 \times 4\text{ bps} = 12\text{ bps } (0.12\%)$$
- Hard lock: Never execute an intentional early profit exit unless $\text{Net PnL} \ge +0.06\%$.

### 4. Edward O. Thorp & John L. Kelly Jr.: Empirical Rolling Kelly Allocator
- Compute rolling 15-trade empirical win rate $p$ and win/loss payoff ratio $b$:
  $$f^* = \max\left(0, \frac{p(b + 1) - 1}{b}\right) \times \text{SafetyFraction } (0.25)$$
- Dynamically scale winning asset sizing (SOL up to $1.00 - 1.20\text{ SOL} \approx \$140$) while clamping underperforming assets.

---

## 3. ARCHITECTURAL UPGRADE SPECIFICATION (4 PILLARS)

### Pillar 1: Pure Maker-Only GTX Routing Engine (`core/engine.py` & `core/binance_client.py`)
1. **Zero IOC Taker Entries**: Force all auto entries to use `GTX` (Post-Only) limit orders pegged to `best_bid` (for BUY) or `best_ask` (for SELL).
2. **Anti-Taker Safety Guard**: If Binance rejects with code `-2010` / `Order would immediately trigger`, do not retry as MARKET; back off by 1 tick ($0.01\text{ SOL}$ / $0.1\text{ BTC}$) and re-quote.
3. **Execution Fee Shield**: Guarantee that 100% of auto-opened positions incur strictly the 2 bps maker fee.

### Pillar 2: Dynamic Asset Volatility Siphoning & BTC Chop Freeze (`core/micro_signals.py`)
1. **Asset Volatility-to-Fee Ratio Filter**:
   - Compute `vfr = (atr_pct * 10000) / (fee_bps + spread_bps)`.
   - If `vfr < 3.0`: Flag asset as `CHOP_FROZEN`. Block all new entries on that asset.
   - For BTC: Require `KER >= 0.55` AND `ATR_15m >= 0.10%` AND `vfr >= 3.0`.
2. **Capital Rerouting**:
   - Allocate $0.70\text{ USDT}$ of the $1.00\text{ USDT}$ total risk pool to SOL/DOGE.
   - Restrict BTC to max $0.30\text{ USDT}$ only when momentum conditions are pristine.

### Pillar 3: Dynamic 3x Fee-Hurdle Profit Lock (`core/engine.py`)
1. **Hard Fee Barrier**:
   - `MIN_PROFIT_EXIT_PCT = 0.08%` (2x round-trip fee).
   - `OPTIMAL_TP_PCT = 0.25%` for SOL / `0.15%` for BTC.
2. **Soft Lock Protection**: When `MFE >= 0.10%`, instantly move stop to `Entry + 0.04%` (Guaranteed Net Positive Maker Exit).
3. **180s Time-Decay Ejector**: If position has been held for 180s and `MFE < 0.06%`, place a Maker limit exit order at `Entry + 0.04%` (Net Breakeven Maker). If not filled by 360s, smoothly adjust to current mid-price maker.

### Pillar 4: High-Beta Dynamic Size Expansion (`core/multi_asset_scanner.py`)
1. **SOL Sizing Matrix**:
   - Normal Swarm Score ($80\% - 89\%$): $0.35 - 0.45\text{ SOL}$ ($\approx \$40 - \$50$).
   - High Conviction Score ($\ge 90\%$): $0.70 - 1.00\text{ SOL}$ ($\approx \$80 - \$115$).
2. **DOGE Sizing Matrix**:
   - High Conviction Score: $300 - 500\text{ DOGE}$ ($\approx \$45 - \$75$).
3. **BTC Clamping**: Locked to $0.001\text{ BTC}$ maximum ($0.0001\text{ BTC}$ probe on initial test).

---

## 4. VERIFICATION & SAFETY CRITERIA

1. **Test Suite Integrity**:
   - Run `python -m pytest -q`.
   - **Requirement**: Must achieve $\ge 1,020+$ passing tests with ZERO failures (100% green).
2. **Ledger Immutability**:
   - Preserve all existing 34 rows in `data/engine_trades.jsonl`.
3. **Strict 2-Mode Separation**:
   - Keep `run.bat demo` (Binance Demo Futures) and `run.bat live` (Real Funds) strictly isolated.
   - Maintain hard stop loss limits ($0.05\text{ U}$ / trade max risk on live).

---

## 5. INSTRUCTION FOR CLAUDE OPUS 5.5

Execute the 4 pillars systematically:
1. Implement the GTX Maker-Only enforcement and VFR Volatility Siphon in `core/micro_signals.py` and `core/engine.py`.
2. Add comprehensive unit tests in `tests/test_world_quant_economic_engine.py` covering all 4 pillars.
3. Validate with `python -m pytest -q` to confirm $\ge 1,020+$ tests pass.
4. Report back the exact test count and key findings.
