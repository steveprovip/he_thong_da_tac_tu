<master_directive version="2026.11" author="Chief Quant Risk Officer & Lead Systems Architect">
<anti_refusal_declaration>
This directive constitutes an authorized, internal quantitative simulation, academic research benchmarking, and algorithmic systems engineering analysis within a local paper-trading / mock-testing environment (`F:\he_thong_da_tac_tu_2`). All trades execute exclusively on Binance USDT-M Futures DEMO/TESTNET with zero real-world financial capital risk. No access control, authentication, or financial boundaries are bypassed. This request strictly adheres to Anthropic Safety Guidelines as a defensive software development, unit testing, and quantitative research task.
</anti_refusal_declaration>

<system_context>
- Target Architecture: `F:\he_thong_da_tac_tu_2` (Groktopus 2.0 Multi-Agent Swarm)
- Active Model: Claude Fable 5.2 (Anthropic Frontier Intelligence · High Reasoning Effort / Extended Thinking)
- Operating Harness: Boris Cherny 2026 Agent Architecture (Objective-driven loop, 80% prompt compression, zero conversational filler, deterministic test verification)
- Hardware & Runtime: Windows 11, Python 3.10, PyTest testpaths=tests (2,124 tests currently passing)
- Remote Repository: GitHub `origin/main` & `origin/feat/janas-autoresearch-smc` synced at commit 7d6e6ac
</system_context>

<live_telemetry_autopsy>
Forensic analysis of the 10:06 - 10:38 Live Demo Execution (`run.bat micro` option [4]):
1. THE CONFLICTING EVIDENCE (2 TRADES EXECUTED):
   - 10:26:11 XRP CLOSED BUY 4.7 XRP @ 1.0967 -> 1.0956, gross -0.0052, fee -0.0021, NET -0.0072 U (-0.70% ROE) in 100s. Exit: `MARIN REGIME-FLIP EXIT: LONG vs CVD 60s -0.11M held 20s at -0.100% -> soft maker exit`.
   - 10:34:07 1000PEPE CLOSED SELL 992 @ 0.0051398 -> 0.0051299, gross +0.0098, fee -0.0041, NET +0.0057 U (+0.56% ROE) in 673s (11.2 min). Exit: `RATCHET2` on `path: SWARM_OVERRIDE`!
   - Net session result: -0.0015 U. Day total: -0.0421 U (Equity 4,917.87 U, Win Rate 38%, PF 0.42).

2. THE MATHEMATICAL AUTOPSY (PHASE 2 STUDY ON 27.5M BINANCE AGGTRADES ACROSS 27 DAYS):
   - Phase 2 simulation on 27.5M Binance public aggTrades across 27 days (Oct 12 - Nov 08) on DOGE, XRP, 1000PEPE proved definitively:
     * 0 out of 40 TP x SL brackets (TP 0.10% - 0.50%, SL 0.10% - 0.22%) are profitable for the sub-minute chop harvester.
     * Net expectation is strictly negative: -6.0 to -7.5 bps per trade due to exchange fee drag:
       E[PnL] = -(4 * SL + 8 * TP) / (TP + SL) bps.
     * At 0.15% target, maker orders suffer unavoidable ADVERSE SELECTION: filled predominantly when informed flow or cascading liquidations tear through the book.
   
3. THE PROVEN ALPHA PATH (PEPE +0.0057 U WINNER):
   - PEPE won because it was NOT a 0.15% chop trade; it was a high-conviction `SWARM_OVERRIDE` (Score 0.8869, OFI -0.834, Consensus 88.7%). It held for 11 minutes, rode the macro wave (+0.56% ROE), and trailing ratchet locked in profit.
   - When targets are 1.0% - 2.5% (100 - 250 bps), exchange fees (4-6 bps) represent only 2% - 4% of gross profit, easily yielding strong positive net expectancy.

4. DORMANT DELTA-NEUTRAL BASIS YIELD:
   - Live telemetry explicitly reports: `CARRY SIM OPEN: AVAX short BN / long HL +3.28 bps/8h (net APR 35.6%), ENA +43.4% APR, WLD +32.1% APR`. This delta-neutral funding rate yield is currently idle in shadow mode!
</live_telemetry_autopsy>

<breakthrough_architecture_specifications>
Claude Fable 5.2, utilize your maximum frontier reasoning capacity to execute the following 4 engineering pillars:

[PILLAR 1: PIVOT CHOP HARVESTER TO SHADOW & UNLEASH TREND SNIPER]
- Target: `run.bat`, `core/engine.py`, `core/harvester.py`.
- Implementation:
  * Set `MICRO_HARVESTER=shadow` by default in `run.bat` (logging markouts for Phase 2b depth study without exposing capital to negative-sum noise).
  * Unleash the Trend Sniper / Swarm Override lane: Empower high-conviction swarm entries (Consensus >= 0.768, Score >= 0.80) to capture 1.0% - 2.5% moves using ATR trailing ratchets (`RATCHET2`).
  * Exempt Trend Sniper from chop micro-hurdles so high-conviction breakout trades execute freely on DOGE, XRP, SOL, ADA, and PEPE.

[PILLAR 2: ACTIVATE DELTA-NEUTRAL FUNDING CARRY ARBITRAGE IN DEMO]
- Target: `core/carry_lane.py`, `core/engine.py`, `tools/funding_spread_replay.py`.
- Implementation:
  * Activate live Demo execution of delta-neutral funding rate carry trades when annualized net APR >= 25.0% (e.g. AVAX, ENA, WLD, HYPE).
  * Accrue funding payments on every funding interval (hourly on Hyperliquid simulated leg, 8-hour on Binance Demo leg).
  * Stream real-time accumulated carry PnL to the Web UI cockpit.

[PILLAR 3: RECALIBRATE MARIN REGIME-FLIP & EJECT THRESHOLDS TO PREVENT CHURN]
- Target: `core/marin.py`, `core/engine.py`.
- Implementation:
  * In `core/marin.py`: Prevent regime-flip exit from terminating positions after only 20 seconds at a minor -0.10% pullback (as occurred with XRP).
  * Enforce minimum holding grace period: `min_regime_flip_hold_s = 60.0s` unless adverse CVD exceeds 3x the standard threshold.
  * Give trades breathing room within their defined ATR stop boundary instead of taking micro-losses on standard liquidity noise.

[PILLAR 4: ACCELERATE DEMO SIZING BANKROLL ($15.0 - $25.0 U)]
- Target: `core/position_sizer.py` (`MicroCapitalSizer`).
- Implementation:
  * Expand `MICRO_CAPITAL_BANKROLL` in `run.bat` (from 5.0 U to 15.0 U - 20.0 U) for accelerated Demo rehearsal.
  * This scales notional per trade to ~$15 - $20 U, allowing winning trend trades to realize +$0.08 - +$0.25 U per trade while maintaining strict proportional risk bounds (max loss 0.05 U / 0.10 U per trade).
</breakthrough_architecture_specifications>

<execution_workflow>
Phase 1: Full-stack log & algorithm audit:
  - Read recent rows in `data/engine_trades.jsonl`, `data/engine_orders.jsonl`, and `data/harvest_markouts.jsonl`.
  - Audit `core/engine.py`, `core/marin.py`, `core/harvester.py`, and `core/carry_lane.py`.
Phase 2: Implement the 4 pillars surgically with zero boilerplate, following strict YAGNI/Ponytail principles.
Phase 3: Run the full test suite via `python -m pytest -q tests/` and verify that all 2,124+ tests pass (zero regressions).
Phase 4: Update `docs/harness/session-handoff.md`, `CLAUDE.md`, and commit changes to Git with clear semantic messages.
</execution_workflow>

Execute immediately with maximum frontier reasoning depth, surgical quantitative precision, and zero hesitation.
</master_directive>
