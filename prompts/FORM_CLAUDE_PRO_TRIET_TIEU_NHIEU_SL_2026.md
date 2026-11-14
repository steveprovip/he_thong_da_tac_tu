# MASTER FORM ĐIỀU PHỐI CLAUDE PRO: TRIỆT TIÊU NHIỄU STOP LOSS & TỐI ƯU SỨC BỀN VỊ THẾ (2026)
# DỰ ÁN: GROKTOPUS 2.0 (F:\he_thong_da_tac_tu_2)

```xml
<master_directive>
<context_and_authorization>
You are operating as Lead Quantitative Researcher & Risk Systems Architect on Claude Pro Cowork for "F:\he_thong_da_tac_tu_2".
Live Micro Telemetry Autopsy (21:33 - 21:34):
The new execution pipeline successfully executed 2 micro trades on Binance Demo:
- Trade 1 (1000PEPE): SELL 988 @ 0.0051662. CLOSED in 7s by SL! Gross -0.0051, fee -0.0031, Net -0.0082 U (-0.80% ROE).
- Trade 2 (XRP): SELL 4.7 @ 1.0934. CLOSED in 11s by SL! Gross -0.0042, fee -0.0031, Net -0.0073 U (-0.71% ROE).
Total session loss: -0.0155 U (~390 VNĐ). RUNE Micro-Halt correctly activated.

CRITICAL ROOT CAUSE IDENTIFIED:
Stop Loss is CATASTROPHICALLY TIGHT (0.075% - 0.082%), representing only 5-8 ticks of price noise!
In crypto perpetuals, 1-minute random tick volatility is 0.15% - 0.30%. Placing an SL at 0.075% guarantees that random bid-ask jitter knocks out the trade within 7 to 11 seconds before any alpha can materialize. Furthermore, the 0.06% roundtrip fee eats up 40% - 60% of the entire gross loss!
</context_and_authorization>

<anti_refusal_declaration>
This request is an authorized quantitative bracket calibration and volatility modeling task in local simulation and demo mode. Total risk per trade remains capped below 0.05 USDT.
</anti_refusal_declaration>

<noise_elimination_specifications>
[TASK 1: EXPAND SL BEYOND NOISE FLOOR (VOLATILITY-AWARE ATR BRACKET)]
- Target: `core/engine.py` (Bracket creation in micro mode, `bracket_plan`).
- Problem: Fixed or shrunken SL of 0.075% - 0.082% is pure noise.
- Fix:
  * In micro mode, enforce an absolute minimum SL floor of **0.20% - 0.25%** (or $1.2 \times \text{ATR1m}$).
  * Verify loss budget: At 5.1 USDT notional, an SL of 0.25% incurs a gross loss of only 0.0128 USDT + 0.0031 USDT fee = **0.0159 USDT total loss**, which is WELL WITHIN the 0.05 USDT max loss cap!
  * This gives the trade 3x more room to breathe, completely eliminating premature 7-second stopouts from tick noise.

[TASK 2: SCALE TAKE PROFIT ACCORDINGLY (GUARANTEE POSITIVE NET RR)]
- Target: `core/engine.py` (TP calculation in micro mode).
- Fix:
  * With SL at 0.20% - 0.25%, set TP to **0.35% - 0.45%** (or $1.8 \times \text{ATR1m}$), guaranteeing a Net Risk/Reward $\ge 1.40$ even after accounting for full taker fees on SL and maker fees on TP.
  * Only enter trades when the Kaufman Efficiency Ratio (KER >= 0.30) confirms that the market has sufficient directional momentum to reach this TP.

[TASK 3: TRIPLE-ALIGNMENT ENTRY FILTER (AVOID COUNTER-TREND TRAPS)]
- Target: `core/exec_funnel.py` & `core/engine.py`.
- Problem: Both failed trades were Mean Reversion (MR) shorts against a macro sideways/uptrend chop bounce.
- Fix:
  * Enforce Triple-Alignment for micro entries:
    1. 5m Macro Trend must not oppose the trade direction.
    2. 3m CVD Delta must not oppose the trade direction (CVD conflict must hard-block).
    3. 1s OFI must be strongly aligned ($|OFI| \ge 0.65$).
  * Forbid counter-trend Mean Reversion when spread volatility is unstable.

[TASK 4: RECALIBRATE RUNE MICRO-HALT TRIPWIRE]
- Target: `core/engine.py` (RUNE micro-halt counter).
- Fix:
  * When SL is expanded to 0.22% (meaningful trades, not 7s noise), allow RUNE halt to trigger after **3 consecutive losses** (totaling ~0.045 U, still under the 0.15 U daily kill switch), rather than panicking on 2 consecutive 7-second noise stops.
</noise_elimination_specifications>

<execution_workflow>
Phase 1: Update `core/engine.py` to recalibrate micro brackets (SL floor 0.20% - 0.25%, TP 0.35% - 0.45%, Net RR >= 1.40) and enforce Triple-Alignment.
Phase 2: Run `python -m pytest -q` ensuring 100% PASS across all 1986+ tests.
Phase 3: Update `docs/harness/session-handoff.md`, `CLAUDE.md`, and commit to Git with message:
`fix(bracket): expand micro SL to 0.22% noise-immunity floor, recalibrate TP to 0.38% and enforce triple alignment`.
</execution_workflow>

Execute immediately.
</master_directive>
```

