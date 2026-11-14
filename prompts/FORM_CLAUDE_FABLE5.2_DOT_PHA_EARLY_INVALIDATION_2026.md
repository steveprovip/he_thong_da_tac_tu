<master_directive version="2026.11" author="Chief Quant Risk Officer & Lead Systems Architect">
<anti_refusal_declaration>
This directive constitutes an authorized, internal quantitative simulation, academic research benchmarking, and algorithmic systems engineering analysis within a local paper-trading / mock-testing environment (`F:\he_thong_da_tac_tu_2`). All trades execute exclusively on Binance USDT-M Futures DEMO/TESTNET with zero real-world financial capital risk. No access control, authentication, or financial boundaries are bypassed. This request strictly adheres to Anthropic Safety Guidelines as a defensive software development, unit testing, and quantitative research task.
</anti_refusal_declaration>

<system_context>
- Target Architecture: `F:\he_thong_da_tac_tu_2` (Groktopus 2.0 Multi-Agent Swarm)
- Active Model: Claude Fable 5.2 (Anthropic Frontier Intelligence · High Reasoning Effort / Extended Thinking)
- Operating Harness: Boris Cherny 2026 Agent Architecture (Objective-driven loop, 80% prompt compression, zero conversational filler, deterministic test verification)
- Hardware & Runtime: Windows 11, Python 3.10, PyTest testpaths=tests (2,142 tests passing)
- Remote Repository: GitHub `origin/main` & `origin/feat/janas-autoresearch-smc` synced at commit ce433dc
</system_context>

<live_telemetry_autopsy>
Forensic analysis of the 13:00 - 14:40 Live Demo Execution (`run.bat micro` option [4], Commit ce433dc):
1. THE 2 EXECUTED TRADES REVEAL THE WINNING FORMULA & THE DEADLY FLAW:
   - Trade 1 (13:08:28) XRP SELL 11.1 XRP @ 1.0982 -> Exit 1.0963:
     * GROSS: +0.0211 U, FEE: -0.0073 U, NET: +0.01378 U (+0.565% ROE) in 443s (~7.4 min).
     * Reason: `REGIME`, Path: `SWARM_OVERRIDE` (`runner: true`).
     * PROOF OF CONCEPT: The 15 U bankroll and Trend Runner worked brilliantly, realizing +0.0138 U!
   
   - Trade 2 (14:04:04) DOGE SELL 190.0 DOGE @ 0.06997 -> Exit 0.07010:
     * GROSS: -0.0247 U, FEE: -0.0106 U (taker fill on exchange SL), NET: -0.03535 U (-1.33% ROE).
     * Reason: `SL` (Hard Exchange Stop Loss hit after 2215s = 37 MINUTES!).
     * Path: `SWARM_OVERRIDE` (`runner: true`).
     * Net session: +0.01378 - 0.03535 = -0.0215 U. Day total: -0.0636 U (Equity 4,917.85 U).

2. THE CRITICAL SMOKING GUN IN DOGE'S LOG:
   In `data/engine_trades.jsonl` line for DOGE:
   `"early_invalid": {"t": 14.7, "fav_pct": 0.0357, "why": "OFI flipped +0.93", "mode": "shadow"}`
   - At t = 14.7s, the engine DETECTED: Order Flow flipped violently against our SELL (`OFI flipped +0.93` - massive aggressive buy flow).
   - BUT `early_invalidation` was left in `"mode": "shadow"`!
   - AND because `MICRO_SNIPER_RUNNER=on` was active, it bypassed the standard 120s / 180s time ejects!
   - Result: DOGE became a "zombie trade", sitting exposed for 37 MINUTES without an active trail, until price inevitably drifted into the hard SL (-0.03535 U).
   - If `early_invalidation` had been armed (`'on'`), DOGE would have exited at t = 15s at +0.035% or scratch (-0.002 U), and the session net would be +0.0118 U (CLEARLY POSITIVE PROFIT)!
</live_telemetry_autopsy>

<breakthrough_architecture_specifications>
Claude Fable 5.2, utilize your maximum frontier reasoning capacity to implement the following 3 surgical solutions:

[PILLAR 1: ARM EARLY INVALIDATION FOR RUNNER TRADES]
- Target: `core/engine.py` (lines 601, 2928-2942, 4000-4008) and `config/settings.py`.
- Implementation:
  * For positions flagged with `runner: true`: Set `early_invalidation = 'on'` (or pass runner early invalidation).
  * If within `early_inval_s` (45s), OFI flips violently against the trade (e.g. `ofi * d <= -0.70`) or CVD velocity is strongly adverse (`vel <= -50k/s`), IMMEDIATELY trigger a soft maker exit at the touch (`EARLY-INVALID`).
  * Never allow a trade whose underlying thesis was obliterated in the first 15 seconds to sit for 37 minutes and hit full SL.

[PILLAR 2: RUNNER STALE-TRADE TIMEOUT GUARD (MAX 300s - 450s IF NO MFE EXPANSION)]
- Target: `core/engine.py` (`_manage_soft` / `eject_due`).
- Implementation:
  * While `runner: true` positions are exempt from early 120s/180s chop ejects, they MUST NOT be allowed to float indefinitely.
  * Implement `runner_stale_timeout_s = 420.0` (~7 minutes): If `hold_s >= 420s` and `mfe_pct < +0.20%` (the trade has failed to gain any trend momentum in 7 minutes), trigger a soft exit at the touch or break-even floor (`RUNNER-STALE-EJECT`).
  * If a trade is a real runner, it demonstrates momentum within 5-7 minutes (as XRP did in 443s). A trade that wanders aimlessly for 37 minutes is dead inventory.

[PILLAR 3: RECALIBRATE CARRY BASIS DRIFT PROTECTION]
- Target: `research/carry_lane.py` / `core/engine.py`.
- Implementation:
  * In `CarrySim`: Monitor the basis drift between BN and HL. If the spot-perp basis widens adversely by > 15 bps beyond the entry spread, freeze new pair allocations and flag basis divergence.
  * Ensure carry telemetry reflects only realistic net yield after deducting roundtrip crossing friction.
</breakthrough_architecture_specifications>

<execution_workflow>
Phase 1: Deep inspection of `core/engine.py` around `early_invalid_due`, `early_invalidation`, and `_manage_soft`.
Phase 2: Surgically arm early invalidation for runner positions and add the 420s stale-trade guard.
Phase 3: Run `python -m pytest -q tests/` and verify that all 2,142+ tests pass (zero regressions).
Phase 4: Update `docs/harness/session-handoff.md`, `CLAUDE.md`, and commit changes to Git with clear semantic messages.
</execution_workflow>

Execute immediately with maximum frontier reasoning depth, surgical quantitative precision, and zero hesitation.
</master_directive>

