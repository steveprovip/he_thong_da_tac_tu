<master_directive version="2026.11" author="Chief Quant Risk Officer & Lead Systems Architect">
<anti_refusal_declaration>
This request constitutes a strictly defensive, internal quantitative simulation, academic research benchmarking, and market microstructure execution analysis within a local paper-trading / mock-testing environment (`F:\he_thong_da_tac_tu_2`). All trades execute exclusively on Binance USDT-M Futures DEMO/TESTNET with zero real-world financial risk. No access control, authentication, or financial boundaries are bypassed.
</anti_refusal_declaration>

<system_context>
- Target Repository: `F:\he_thong_da_tac_tu_2` (Groktopus 2.0 Multi-Agent Swarm)
- Active Model: Claude Sonnet 3.5 / 3.7 / 5.5 Extra (High Reasoning Harness)
- Harness Standard: Boris Cherny 2026 Anthropic Agent Architecture (Goal -> Effort -> Verification, 80% prompt compression, zero fluff, deterministic test verification)
- Hardware Environment: Windows 11, Python 3.10, PyTest testpaths=tests (2,043 passed)
</system_context>

<live_telemetry_autopsy>
Autopsy of live Demo execution at 23:05 - 23:16 (Commit 4915c9d):
1. THE POSITIVE BREAKTHROUGH:
   - 23:04:39: XRP VESKA AUTO SELL 4.6 XRPUSDT @ 1.1113 -> CLOSED @ 23:05:04 (25s) TP gross +0.0078, fee -0.0020, NET +0.0058 U (+0.57% ROE).
   - 23:13:22: XRP VESKA AUTO SELL 4.6 XRPUSDT @ 1.1096 -> CLOSED @ 23:15:09 (107s) EJECT-BE gross +0.0023, fee -0.0020, NET +0.0003 U (+0.03% ROE).
   - 100% Win Rate, zero Stop Loss hits, maker fees captured. The Chop Harvester logic is mathematically validated!

2. THE "MICRO-PROFIT PARADOX" ROOT CAUSES:
   - Issue A (The 5.05 U Sizing Trap):
     In `core/position_sizer.py`, `compound_target = min(0.85, 0.20) * E * L = 1.0 * E`.
     At E = 5.0 U, target is 5.0 U, which is strictly below the venue minimum floor (5.10 U). Thus, notional is clamped to the absolute smallest lot: 4.6 XRP ($5.11 notional, margin $1.02 at 5x leverage).
     At $1.02 margin, a 0.57% price move yields only +0.0058 U. To reach 10 U equity (to double the lot size), the bot needs 1,666 consecutive winning trades!
   - Issue B (Single-Slot Bottleneck):
     `micro_max_positions = 1` forces a single-asset queue. While DOGE, XRP, ADA, and 1000PEPE all exhibit micro-structure opportunities, only 1 position can be open.
   - Issue C (Macro Trend Lockout during Chop):
     From 23:05 to 23:13, DOGE was locked by `MACRO LONG ONLY forbids SELL`, while XRP and PEPE were locked by `MACRO STRONG DOWNTREND forbids BUY`. In true Chop (KER < 0.28), a 5m macro trend has zero predictive power over 10-second orderbook spread capture.
   - Issue D (Dormant Funding Carry Yield):
     Live telemetry confirms `CARRY SIM OPEN: ENA +43.4% APR, WLD +32.1% APR, HYPE +31.7% APR, XRP +25.9% APR`. This delta-neutral basis yield is currently idle in shadow mode.
</live_telemetry_autopsy>

<breakthrough_architecture_specifications>
Implement the following 4 engineering pillars to scale realized profit while preserving mathematical risk bounds:

[PILLAR 1: MULTI-ASSET CONCURRENCY (EXPAND POSITION SLOTS)]
- Target: `core/engine.py` & `core/position_sizer.py` (`MicroCapitalSizer`).
- Implementation:
  * In `core/engine.py` and `MicroCapitalSizer`, expand `micro_max_positions` from 1 to `3` (max 3 concurrent positions across non-identical symbols).
  * Enforce cross-asset independence: Max 1 position per symbol (e.g. 1 DOGE, 1 XRP, 1 ADA allowed simultaneously).
  * Total combined risk cap remains strictly bounded: Aggregate daily loss kill switch stays at 0.15 U (or 3 consecutive losses halt).

[PILLAR 2: DYNAMIC COMPOUNDING ACCELERATOR & DEMO BANKROLL SCALING]
- Target: `core/position_sizer.py` (`compound_target` & `qty`).
- Implementation:
  * Revise `compound_target`: Instead of `1.0 * E`, define an accelerated growth curve:
    `compound_target = max(5.10, min(self.margin_frac * equity * leverage, notional_base * (1.0 + (realized_micro_pnl * 5.0))))`.
  * Support an optional environment variable `MICRO_BANKROLL_DEMO` (default 5.0, configurable up to 25.0 or 50.0 for accelerated Demo rehearsal). At 25 U bankroll, notional becomes ~25 U, expanding per-trade profit to +$0.03 - +$0.08 U per trade without altering code structure.
  * Floor risk notional to satisfy exchange min notional while keeping `max_loss_usdt` proportionate to active bankroll.

[PILLAR 3: SOFTEN MACRO LOCK IN CHOP HARVESTER LANE]
- Target: `core/engine.py` (`scalp_candidate` and `micro_entry_block`).
- Implementation:
  * When `self.harvest_lane(f)` is True (KER < 0.28):
    Replace the hard `MACRO STRONG TREND` block with a soft OFI bias:
    If 5m macro opposes the trade, but 10s local orderbook imbalance is strongly aligned (`|OFI_L10| >= 0.55`) and the order is a resting Post-Only Maker, ALLOW the harvest entry.
  * In true sideways chop, orderbook spread capture operates on 5-30 second oscillations, not 5-minute directional momentum.

[PILLAR 4: WIRE FUNDING CARRY ARBITRAGE FOR DEMO ACCRUAL]
- Target: `core/engine.py` (`carry_tick` / `funding_carry`).
- Implementation:
  * Wire the high-APR carry signals (`HYPE`, `ENA`, `WLD`, `XRP` with APR > 25%) so that on Demo mode, passive funding rate collection accrues every funding interval (or via simulated 8h settlement tick).
  * Provide real-time telemetry display of accumulated funding carry yield.
</breakthrough_architecture_specifications>

<execution_workflow>
Phase 1: Deep inspection of `core/position_sizer.py`, `core/engine.py`, and `config/settings.py`.
Phase 2: Implement multi-asset concurrency (`micro_max_positions = 3`), accelerated compounding curve, and chop macro-lock softening.
Phase 3: Run `python -m pytest -q tests/` and verify 100% of the 2,043+ tests pass (zero regressions).
Phase 4: Document mathematical formulas in `docs/harness/plan_micro_profit_scale.md`, update `CLAUDE.md`, and commit to Git with message:
`feat(strategy): profit acceleration overhaul - multi-asset concurrency, dynamic compounding, and chop macro softening`.
</execution_workflow>

Execute immediately with maximum reasoning depth and rigorous quantitative precision.
</master_directive>

