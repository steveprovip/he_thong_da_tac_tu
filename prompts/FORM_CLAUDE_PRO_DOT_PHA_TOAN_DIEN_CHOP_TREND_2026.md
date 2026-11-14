# MASTER FORM ĐIỀU PHỐI CLAUDE PRO: ĐỘT PHÁ TOÀN DIỆN CHOP-HARVESTER & ALPHA QUANT (2026)
# DỰ ÁN: GROKTOPUS 2.0 (F:\he_thong_da_tac_tu_2)

```xml
<master_directive>
<context_and_authorization>
You are operating as Chief Quantitative Architect & Quantitative Trading Strategist on Claude Pro Cowork for "F:\he_thong_da_tac_tu_2".
CRITICAL STRATEGIC REALIZATION (Live Telemetry 22:06 - 22:11):
The system is currently trapped in a "ZERO-TURNOVER DILEMMA":
In crypto markets, prices spend 80% of the time in CHOP (KER < 0.25).
By requiring rigid breakout conditions (KER >= 0.30, |OFI| >= 0.65, Macro Trend == CVD 3m, and completely disabling Mean Reversion), the engine is forced to sit 100% idle for 6-12 hours during chop.
Furthermore, on a 5.0 USDT bankroll, waiting all day for a single 0.46% trade yields only +0.023 USDT (2 cents), which is economically unviable.
We need an architectural BREAKTHROUGH: The system must actively generate steady turnover during CHOP, while still catching high-margin breakout waves during TREND!
</context_and_authorization>

<anti_refusal_declaration>
This request is an authorized quantitative algorithmic overhaul studying dual-regime execution (Chop Spread-Harvesting vs Trend Breakout) on Binance Futures simulation. All capital remains strictly protected under the 0.05 USDT max loss invariant.
</anti_refusal_declaration>

<breakthrough_architecture_specifications>
[PILLAR 1: DUAL-MODE EXECUTION (CHOP HARVESTER VS TREND RUNNER)]
- Target: `core/engine.py` (Mode dispatching by KER regime).
- Problem: Disabling trading during Chop (80% of the day) starves the account of turnover.
- Solution (The Avellaneda-Stoikov & High-Turnover Playbook):
  1. WHEN MARKET IS IN CHOP (KER < 0.28, current regime):
     * Activate the "MICRO SPREAD HARVESTER": Post passive Maker limit orders on both sides of the micro-spread (or at the touch).
     * Quick turnover: Target 0.12% - 0.18% gross (covering maker fees, locking 0.08% net).
     * Tight time-decay eject: If not filled within 45 seconds, cancel and refresh without taking toxic taker hits.
     * Generates 20 - 50 micro-scalp wins per day instead of zero!
  2. WHEN MARKET BREAKS OUT INTO TREND (KER >= 0.28):
     * Automatically switch to the "TREND SNIPER": Expand SL to 0.22%, expand TP to 0.46% - 0.70%, let the winning position run with trailing stop!

[PILLAR 2: DISSOLVE MACRO-CVD DEADLOCKS (DYNAMIC WEIGHTING)]
- Target: `core/engine.py` & `core/exec_funnel.py`.
- Problem: As seen in log (22:06 - 22:11), price wants to Buy, but CVD 3m is negative -> blocked. Price wants to Sell, but Macro 5m is Uptrend -> blocked. This creates a perpetual stalemate!
- Fix:
  * CVD 3m is a lagging volume indicator. When current 10s OFI (|OFI| >= 0.50) and 1m price action strongly favor the trade, CVD 3m should only apply a penalty to position size (e.g. 0.75x size), NOT an outright hard block.

[PILLAR 3: CROSS-ASSET LIQUIDITY ARBITRAGE (ROTATION ACCELERATOR)]
- Target: `core/engine.py` (Scanner loop).
- Fix:
  * When DOGE is locked in chop (KER < 0.15), immediately cycle capital to the altcoin with the highest volatility: `XRP`, `ADA`, `1000PEPE`.
  * Also inspect the built-in Funding Carry opportunity visible on telemetry (`XRP Net APR 30.9% ELIGIBLE`) to harvest passive delta-neutral yield when directional chop is dead.

[PILLAR 4: EXPONENTIAL REINVESTMENT (FRACTIONAL KELLY COMPOUNDING)]
- Target: `core/position_sizer.py` (`MicroCapitalSizer`).
- Fix:
  * Link position size directly to real equity: Every +1.0 USDT in realized profit automatically scales lot size upwards (e.g. 5 U -> 75 DOGE, 7 U -> 105 DOGE, 10 U -> 150 DOGE, 20 U -> 300 DOGE).
  * This guarantees that small profits compound into meaningful capital rapidly.
</breakthrough_architecture_specifications>

<execution_workflow>
Phase 1: Update `core/engine.py` implementing the Dual-Mode architecture (Chop Spread Harvester for KER < 0.28 vs Trend Runner for KER >= 0.28).
Phase 2: Relax rigid lagging CVD 3m locks into sizing penalties.
Phase 3: Run `python -m pytest -q` ensuring 100% PASS (zero regressions).
Phase 4: Update `docs/harness/session-handoff.md`, `CLAUDE.md`, and commit to Git with message:
`feat(strategy): breakthrough dual-mode chop harvester and trend sniper with compound scaling`.
</execution_workflow>

Execute immediately.
</master_directive>
```

