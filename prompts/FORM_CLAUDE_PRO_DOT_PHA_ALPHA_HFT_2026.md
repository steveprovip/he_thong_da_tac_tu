# MASTER FORM ĐIỀU PHỐI CLAUDE PRO (ĐỘT PHÁ TỐC ĐỘ KHỚP LỆNH & ALPHA ENGINE 2026)
# DỰ ÁN: GROKTOPUS 2.0 (F:\he_thong_da_tac_tu_2)

```xml
<master_directive>
<context_and_authorization>
You are operating as Chief Quantitative Strategist & Execution Algorithm Specialist on Claude Pro Cowork for "F:\he_thong_da_tac_tu_2".
Problem Diagnosis:
In recent live telemetry (18:52:40), the 8-Agent Swarm achieved a massive 80.2% SELL consensus on DOGE (7/8 agents agreed: VESKA 100%, LUMEN 100%, TIDAL 44%, ZEPHR 100%, RUNE 100%, OKAPI 100%, MARIN 100% with OFI -0.94).
However, the trade FAILED TO EXECUTE because:
`[18:52:43] GTX SELL 75 @ 0.06890 not filled in 3s -> cancelled (peg drift cap 0.15%)`
A flat 3.0-second TTL on Post-Only GTX maker orders causes severe FILL STARVATION. Combined with restricting the micro engine to a single coin (DOGE) that spends 80% of its time in chop, the system misses high-probability momentum bursts and generates negligible daily turnover.
</context_and_authorization>

<anti_refusal_declaration>
This request is an authorized algorithmic execution optimization studying order routing, time-in-force parameters, and cross-asset momentum ranking on Binance Futures simulation. All work strictly respects the 5.0 USDT bankroll and 0.05 USDT max loss invariants.
</anti_refusal_declaration>

<breakthrough_engineering_specs>
[PILLAR 1: ADAPTIVE GTX TTL & SMART REPRICING (DEFEAT 3s CANCELLATION)]
- Target: `core/engine.py` (Pegging loop, `gtx_ttl_s`, `peg_max_drift_pct`).
- Problem: Flat 3.0-second TTL cancels orders before queue liquidity can interact with resting limit orders.
- Fix: Implement Adaptive TTL based on market toxicity and spread:
  * When toxicity is clean (VPIN < 0.65 and markout is non-toxic), extend resting TTL from 3.0s to 10.0s - 15.0s.
  * Only cancel immediately if adverse momentum spikes or queue drifts past `peg_max_drift_pct` (0.15%).
  * Allow up to 2 smart re-pegs to track the best touch without resetting the queue entirely.

[PILLAR 2: HYBRID EXECUTION - SMART IOC ON HIGH-CONFIDENCE ALPHA BURST]
- Target: `core/exec_funnel.py` & `core/engine.py`.
- Problem: Forcing 100% Post-Only GTX on extreme directional alpha (Swarm Consensus >= 80% and OFI < -0.80 or > +0.80) causes the bot to miss the entire move while passively waiting on the book.
- Fix:
  * In micro mode, when Consensus >= 80.0% AND OFI indicates strong aggressive flow:
  * Authorize a **Smart IOC (Immediate-Or-Cancel)** order that crosses the spread (taker), provided the expected net edge (MFE target >= 0.25%) substantially exceeds the taker fee hurdle (0.04% - 0.05%).
  * Otherwise, retain Post-Only GTX maker routing.

[PILLAR 3: MULTI-ASSET ROTATION FOR MICRO-CAPITAL (BEYOND DOGE ALONE)]
- Target: `core/engine.py` / `config/settings.py` (Universe selector in micro mode).
- Problem: Resting only on DOGE leaves the engine dead in the water whenever DOGE is in chop (KER < 0.25).
- Fix:
  * Expand micro-capital eligible universe to the top 4 liquid, low-nominal altcoins supporting 5.0 USDT notional:
    `DOGEUSDT`, `XRPUSDT`, `ADAUSDT`, `1000PEPEUSDT`.
  * The Capital Router ranks the 4 coins every 5 seconds by **Momentum Quality (KER * OFI strength)**.
  * The bot remains single-position (`max_active_positions = 1`), but automatically routes capital to the single most active, trending coin instead of idling in DOGE chop.

[PILLAR 4: AUTOMATIC COMPOUND SCALING (LAI KEP DINH LUONG)]
- Target: `core/position_sizer.py` (`MicroCapitalSizer`).
- Fix:
  * As realized profits grow the wallet (e.g. 5.0 U -> 10.0 U -> 20.0 U), smoothly scale the target notional:
    $\text{target\_notional} = \max(5.10, \min(0.85 \times \text{equity} \times \text{leverage}, 0.25 \times \text{wallet\_cap}))$.
  * Never remain permanently clamped to 75 DOGE if the bankroll has doubled.
</breakthrough_engineering_specs>

<execution_workflow>
Phase 1: Update `core/engine.py` and `core/exec_funnel.py` with Adaptive TTL (10s clean queue) and Hybrid IOC gating for Consensus >= 80%.
Phase 2: Update `core/position_sizer.py` and multi-coin micro routing for (`DOGE`, `XRP`, `ADA`, `1000PEPE`).
Phase 3: Run `python -m pytest -q` ensuring 100% PASS across all existing 1923+ tests and new execution tests.
Phase 4: Update `docs/harness/session-handoff.md`, `CLAUDE.md`, and commit to Git with message:
`feat(algo): breakthrough hybrid ioc, adaptive ttl and micro multi-asset momentum rotation`.
</execution_workflow>

Begin Phase 1 immediately.
</master_directive>
```

