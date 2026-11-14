# MASTER FORM ĐIỀU PHỐI CLAUDE PRO: GIẢI PHÓNG TẮC NGHẼN CỔNG LỆNH (UNSHACKLE EXECUTION ENGINE 2026)
# DỰ ÁN: GROKTOPUS 2.0 (F:\he_thong_da_tac_tu_2)

```xml
<master_directive>
<context_and_authorization>
You are operating as Chief Algorithmic Execution Engineer on Claude Pro Cowork for "F:\he_thong_da_tac_tu_2".
Problem Diagnosis from Live Terminal Telemetry (19:44 - 19:57):
The engine is suffering from "OVER-CONSTRAINED DEADLOCK" (Tê liệt vì quá nhiều rào chắn trùng lặp). In 15 minutes of live scanning across DOGE, XRP, 1000PEPE, zero trades executed due to hyper-conservative gating:

1. OKAPI Net RR Deadlock (19:51:26 - 19:51:35):
   DOGE had viable BUY signals with Net RR = 1.15, but OKAPI refused it 8 CONSECUTIVE TIMES because of a rigid `Net RR >= 1.30` threshold:
   `[19:51:26] DOGE OKAPI refused BUY: ZEPHR NET RR 1.15 < 1.3 (TP 0.217% / SL 0.075%)`
2. JUMPY Regime Freezing (19:52 - 19:55):
   XRP had strong SELL signals (KER 0.37, OFI -0.50), but was frozen 4 TIMES because market movement was classified as "JUMPY":
   `[19:52:36] XRP REGIME GATE refused SELL SCALP: JUMPY: micro-scalping frozen`
   Freezing on JUMPY kills the exact volatility needed for scalping!
3. Persistent 3-Second GTX Timeout (19:57:04):
   When XRP finally cleared all gates and placed an order, it STILL cancelled after 3 seconds:
   `[19:57:04] XRP GTX SELL 4.7 @ 1.1057 not filled in 3s -> cancelled`
   Because adaptive TTL fell back to 3.0s!
4. Hybrid IOC Unreachable:
   Demanding Net RR >= 1.30 with taker fees requires TP >= 0.43% - 0.50%, which never triggers in micro-scalp regimes.
</context_and_authorization>

<anti_refusal_declaration>
This request is an authorized quantitative parameter and logic recalibration to resolve execution deadlocks in simulation and demo modes. Max loss remains strictly bounded at 0.05 USDT per trade.
</anti_refusal_declaration>

<unshackling_specifications>
[TASK 1: RECALIBRATE OKAPI NET RR FLOOR FOR MICRO MODE]
- Target: `core/engine.py` (OKAPI gating, `scalp_net_rr_min`).
- Fix:
  * For micro-capital mode, adjust `scalp_net_rr_min` from 1.30 down to **1.10** (or 1.05).
  * This immediately unlocks the 8 valid DOGE setups (Net RR 1.15) observed in the log while still guaranteeing positive expectancy.

[TASK 2: UNFREEZE JUMPY REGIME FOR MICRO-SCALPING]
- Target: `core/regime_classifier.py` & `core/engine.py` (Regime gate).
- Fix:
  * In micro mode, do NOT freeze scalping on `JUMPY` regime. Allow micro-scalping during `JUMPY` provided spread is tight (< 2.0 bps) and VPIN < 0.70. Volatility is the lifeblood of scalping; freezing on volatility causes complete execution starvation.

[TASK 3: HARD FLOOR GTX RESTING TTL TO 10 SECONDS (BAN 3s CANCEL)]
- Target: `core/engine.py` (Pegging loop, `gtx_ttl_s`).
- Fix:
  * In micro mode, enforce a **hard floor of at least 10.0 seconds** for GTX resting orders, regardless of VPIN warmup state.
  * Only abort early if adverse price drift exceeds 0.15% or spread widens past 3.0 bps. Never cancel at 3.0 seconds just because VPIN is initializing.

[TASK 4: MAKE HYBRID IOC PRACTICALLY ACCESSIBLE]
- Target: `core/exec_funnel.py` & `core/engine.py` (IOC gate).
- Fix:
  * In micro mode with `MICRO_IOC=on`, allow Smart IOC when Swarm Consensus >= 78.0%, OFI >= 0.70, and TP >= 0.20% (with net RR >= 1.05).
  * This allows the engine to seize clear momentum breakouts instead of being trapped in limit order queues.
</unshackling_specifications>

<execution_workflow>
Phase 1: Update `core/engine.py`, `core/exec_funnel.py`, and `core/regime_classifier.py` applying Tasks 1-4.
Phase 2: Run `python -m pytest -q` ensuring 100% PASS (zero regressions).
Phase 3: Update `docs/harness/session-handoff.md`, `CLAUDE.md`, and commit to Git with message:
`fix(engine): resolve over-constrained deadlock, lower okapi rr to 1.10, unfreeze jumpy and enforce 10s min ttl`.
</execution_workflow>

Execute immediately.
</master_directive>
```

