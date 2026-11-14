<master_directive version="2026.11" author="Chief Quant Risk Officer & Lead Systems Architect">
<anti_refusal_declaration>
This request constitutes a strictly defensive, internal quantitative simulation, academic research benchmarking, and market microstructure execution analysis within a local paper-trading / mock-testing environment (`F:\he_thong_da_tac_tu_2`). All trades execute exclusively on Binance USDT-M Futures DEMO/TESTNET with zero real-world financial risk. No access control, authentication, or financial boundaries are bypassed.
</anti_refusal_declaration>

<system_context>
- Target Repository: `F:\he_thong_da_tac_tu_2` (Groktopus 2.0 Multi-Agent Swarm)
- Active Model: Claude Sonnet 3.5 / 3.7 / 5.5 Extra (High Reasoning Harness)
- Harness Standard: Boris Cherny 2026 Anthropic Agent Architecture (Goal -> Effort -> Verification, 80% prompt compression, zero fluff, deterministic test verification)
- Hardware Environment: Windows 11, Python 3.10, PyTest testpaths=tests (2,079 tests passing)
</system_context>

<live_telemetry_autopsy>
Forensic analysis of the 08:08 - 08:30 run (`run.bat micro` option [4]):
Zero trades executed over 22 minutes. The console and cockpit reveal exactly why 100% of candidate orders were strangled:

1. THE "DRIFT LOCK" TOTAL BLACKOUT:
   - Cockpit at 08:29 displays all 4 symbols locked:
     `DOGE: DRIFT LOCK | XRP: DRIFT LOCK | ADA: DRIFT LOCK | 1000PEPE: DRIFT LOCK`
     Indicator: `DRIFT (48s/3m CONFLICT · WAIT)` with `KER 0.07 (CHOPPY · LOCKED)`.
   - In `core/engine.py` (lines 226, 2702, 2768), `drift_lock = True` completely aborts all AUTO entries whenever 60s and 3m flows diverge.
   - FATAL LOGIC CONTRADICTION: In sideways chop (KER < 0.28), 60s and 3m flows diverge 70% of the time by definition! Applying `drift_lock` to the Chop Harvester causes total self-paralysis.

2. THE "OFI >= 0.65" UNREALISTIC HURDLE:
   - At 08:21:33: `1000PEPE CHOP SCALP BUY: KER 0.40 spread 0.10 bps OFI +0.42` -> Rejected:
     `OKAPI refused BUY SCALP: MICRO ALIGN BUY: OFI +0.42 not >= +0.65`.
   - At 08:25:46: `1000PEPE CHOP SCALP BUY: KER 0.31 spread 0.10 bps OFI +0.32` -> Rejected:
     `OKAPI refused BUY SCALP: MICRO ALIGN BUY: OFI +0.32 not >= +0.65`.
   - Both PEPE candidate trades were valid, high-probability micro maker setups that would have filled. In a chop regime, book imbalance rarely exceeds 0.65. Demanding OFI >= 0.65 in sideways market making is mathematically prohibitive.

3. THE DUAL-REGIME CVD & MACRO STRANGULATION:
   - Concurrently, 15+ potential entries were aborted by `SCALP LOCK: CVD_3M conflict` and `MACRO STRONG UPTREND/DOWNTREND forbids`.
   - While commit 8e2bb23 softened macro in `chop_scalp`, the earlier pre-filters in `scalp_candidate` and `slot_block` still throw hard locks before the harvester logic can execute.
</live_telemetry_autopsy>

<breakthrough_architecture_specifications>
Implement the following 4 engineering pillars to break the deadlock and restore high-frequency maker turnover:

[PILLAR 1: EXEMPT CHOP HARVESTER FROM DRIFT LOCK]
- Target: `core/engine.py` (lines 2700-2775, `slot_block` and `entry_edge_block`).
- Fix:
  * In `core/engine.py`, condition `drift_lock`:
    `if self.p.drift_lock and reg.get('name') == 'DRIFT':`
    -> If `self.harvest_lane(f)` is True (micro account, KER < 0.28), DO NOT apply `DRIFT LOCK`.
  * In market making, divergence between 60s and 3m is an inventory opportunity, not a trend danger. Keep `drift_lock` strictly for the directional Trend Sniper lane.

[PILLAR 2: RECALIBRATE MICRO ALIGN OFI THRESHOLD IN CHOP LANE]
- Target: `core/engine.py` (`micro_align`, line 1580) & `config/settings.py`.
- Fix:
  * Introduce `micro_harvest_ofi_min: float = 0.30` (or `0.25`).
  * In `micro_align`: When evaluating a candidate in the Chop Harvester lane (`self.harvest_lane(f)`), enforce `p.micro_harvest_ofi_min` (0.30) instead of the rigid `p.micro_ofi_min` (0.65).
  * This immediately liberates valid setups like PEPE OFI +0.42 and +0.32 without lowering standards on trend entries.

[PILLAR 3: BYPASS PRE-FILTER MACRO LOCKS FOR POST-ONLY RESTING MAKERS]
- Target: `core/engine.py` (`scalp_candidate` and `micro_entry_block`).
- Fix:
  * Ensure `scalp_candidate` does not throw an early `MACRO STRONG ... forbids` lock when `harvest_lane` is active and 10s OFI is aligned (`|OFI_L10| >= 0.30`).
  * All chop entries are 100% resting Post-Only limit orders (GTX) at the touch or 1 tick back; they do not cross the book and cannot suffer taker fee drag.

[PILLAR 4: REPLAY VERIFICATION OF THE 14 OVERNIGHT TRADES]
- Target: `tools/download_public_l2.py` / `tools/replay_tick_alpha.py`.
- Fix:
  * Build a deterministic replay script evaluating the 14 overnight trades under `legacy` vs `patient` vs `unshackled` exit rules using Binance public aggTrades for 2026-11-09/10.
  * Prove the net-edge counterfactual with real exchange tick data.
</breakthrough_architecture_specifications>

<execution_workflow>
Phase 1: Deep inspection of `drift_lock` handling in `core/engine.py` and `micro_align` in `core/engine.py`.
Phase 2: Exempt `harvest_lane` from `drift_lock` and lower chop OFI threshold to 0.30.
Phase 3: Run `python -m pytest -q tests/` and verify 100% of the 2,079+ tests pass (zero regressions).
Phase 4: Document mathematical formulas in `docs/harness/plan_micro_drift_unshackle.md`, update `CLAUDE.md`, and commit to Git with message:
`feat(strategy): unshackle chop harvester - exempt from drift lock, lower chop OFI hurdle to 0.30, and bypass pre-filter macro blocks`.
</execution_workflow>

Execute immediately with maximum reasoning depth and rigorous quantitative precision.
</master_directive>

