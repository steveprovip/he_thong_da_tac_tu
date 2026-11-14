# plan.md - Fee-drag fixes, Tasks 1-5 (2026-11-09, DEMO/shadow/replay only)

Baseline before any edit: `python -m pytest -q` = **1834 passed, 0 failed** (153 s).
Scope: no trading started, no live enabled, no capital/leverage raised. Weights, 0.768 quorum, 0.70 veto, AuthToken, RUNE veto untouched (invariants I1-I8, `tests/test_invariants_lock.py`).

## Directive vs. code: what I verified before touching anything

| Directive claim | What the code says | Consequence for the patch |
|---|---|---|
| `be_offset_pct` 0.02 % < round-trip fee, so BE locks a loss | `be_price` = `stop_out_pct()` (maker in + taker stop + slip = 0.08 %) **+ 0.02 %**; `soft_be_price` = (entry fee + maker out) **+ 0.02 %** = +0.06 %. The offset sits ON TOP of fees. The real leak is the SOFT path: a maker exit that escalates to taker (+3 bps fee, +1 bp slip) loses 0.04 %, and `be_locked` is set when the soft target is chosen, not when protection exists. | Floor the SOFT offset at 0.08 % (SOL 0.065 %), arm BE only after MFE covers the target, keep the HARD path at +0.02 % (its stop-out cost already includes taker + slip; raising it would arm BE at +0.16 % and make `_manage_hard` market-close at +0.12..0.16 %). |
| Scalp TP 0.3505 % unreachable | True: `scalp_tp_pct` 0.32 lifted by the owner's 11-08 net-RR floor 1.35 at SL floor 0.15 -> `scalp_min_tp` = 0.3505 %. | See Task 2: TP 0.09-0.14 % with SL 0.15 % has net RR 0.35 (break-even win rate 70-79 % over the clamp). Repo replay (CLAUDE.md, `chop_scalper` comment): that TP/SL shape lost -6.3..-7.5 bps/scalp, CI < 0. TP mode is added **opt-in**; EJECT guard is default-on. |
| LUMEN/TIDAL vote identically | `micro_swarm.ALPHA`: LUMEN = FLOW/SURGE, TIDAL = SQUEEZE/LIQ/DEPTH/OFI. FLOW, SURGE, OFI all read CVD/OBI; SQUEEZE requires SURGE. LIQ and DEPTH are independent. | Discount TIDAL's vote and edge when both fire on flow-family signals, same side. WEIGHTS stay pinned (I8). |
| `record_outcome` labels on gross | `core/runtime.py:448,518` already pass **net** PnL. `core.engine.Engine` never calls it. The label is only wrong if a caller passes gross. | Make the contract explicit: `fee_paid=` kw turns a gross `pnl` into net; label is always net > 0. |
| Kelly NO EDGE falls back to min size | True: `kelly_qty` returns None -> `base.qty`. | Gate in `_execute`. Added a staleness TTL, otherwise the 20-trade window never refreshes and the symbol is locked forever. |
| Raw quorum padded | True: JEV FAST lifts `score = max(score, jev/10)`, label `jev_lift`. Pinned by owner decision D4 at `ms.evaluate` level. | Refuse at the funnel (`_execute`), leave `ms.evaluate` and its D4 test intact. |

## Task 1 - fee-aware SOFT-BE (`core/engine.py`)
- L206 `be_offset_pct` 0.02 -> **0.08** (soft maker BE). New `be_offset_hard_pct = 0.02` for the HARD stop.
- New module constants `BE_OFFSET_FLOOR_DEFAULT = 0.08`, `BE_OFFSET_FLOOR = {'SOLUSDT': 0.065}`; derivation: `fee_shield_pct 0.04 + taker escalation (5-2+1) bps = 0.04`.
- New methods `be_offset_eff()`, `be_level_pct(p)`, `be_arm_pct(p)`, `fee_hurdle_covered(p)`.
- L2884 `be_price` -> `be_offset_hard_pct`. L2989 `soft_be_price` -> `be_offset_eff()`.
- L3307 (`_manage_soft`) BE candidate only when `mfe >= be_arm_pct`; L3338 `be_locked = True` only when `fee_hurdle_covered`.
- L3355 (`_manage_hard`) BE arms at `max(be_trigger_pct, level of be_price)`.

## Task 2 - scalp TP + EJECT (`core/engine.py`)
- Fields near L263: `scalp_tp_mode='static'` ('static'|'atr'), `scalp_tp_atr_mult=0.6`, `scalp_tp_min_pct=0.09`, `scalp_tp_max_pct=0.14`; `eject_guard=True`, `eject_defer_max_s=120`.
- `bracket_plan('SCALP')` (L1878): mode 'atr' with `atr15_pct` known -> `tp = clamp(0.6 x ATR15, 0.09, 0.14)`. The 11-08 net-RR lift/gate still applies while `scalp_net_rr` is on, so the clamp only takes effect with `scalp_net_rr=False` (owner switch). Reported `be_wr` shows the hurdle (70-79 % over the clamp).
- L3110 escalation: `floor` is dropped at `eject_hard_s` only if `eject_escalate_ok`: gross >= fee hurdle (entry fee + maker exit, 4 bps), or adverse momentum confirmed (CVD velocity / OFI flip / 15 s velocity against, or toxic VPIN), or `eject_defer_max_s` elapsed. Bounded, so the time stop survives.

## Task 3 - LUMEN/TIDAL collinearity (`core/micro_swarm.py`)
- `FLOW_FAMILY = {FLOW, SURGE, OFI, SQUEEZE}`. In `evaluate`, if LUMEN and TIDAL vote the same side and TIDAL's signal is flow-family: TIDAL confidence x `collinear_penalty` (0.5) and its edge is discounted the same way. Independent TIDAL sources (LIQ, DEPTH) are not penalised. LUMEN + TIDAL + gates then = 0.70 < 0.768; NORO's own signal restores the quorum.
- `Profile.collinear_penalty` plumbed through the two `swarm.evaluate` calls (L1707, L1710).

## Task 4 - outcome label (`core/consensus_engine.py:66`)
- `record_outcome(votes, side, pnl, fee_paid=None)`: `net = pnl - fee_paid` when `fee_paid` is given, else `pnl` (documented as NET). Label `1 if net > 0 and vote.side == side`.

## Task 5 - Kelly NO EDGE + raw quorum (`core/engine.py`)
- Fields: `kelly_no_edge_gate=True`, `raw_quorum_gate=True`, shadow path `edge_shadow=True` (+ `_every_s` 20, `_ttl_s` 30, `_hold_s` 600; replaces the first draft's 6 h TTL, owner choice 2026-11-09).
- `kelly()` split into `kelly()` + `kelly_of(rows)`; new `entry_edge_block(path, tag)` -> reason or None; `edge_shadow_arm/step/view` run the virtual trades.
- `_execute` (L2358) calls it after the observer/slot checks for `tag == 'AUTO'`: state `NO EDGE` / `f* <= 0` -> refuse, no fallback size; `jev_lift is True` (or UNKNOWN on the JEV path) -> refuse.
- SAMPLE state (< `kelly_min_trades`) is not NO EDGE: UNKNOWN is not zero, entries stay allowed at minimum size so the window can fill.

## Tests
- New: `tests/test_fee_drag_fixes_2026_11_09.py`, one group per task.
- Existing tests that pin the old constants (0.02 soft offset, JEV-lifted entries) get an explicit, commented opt-out; each failure is triaged before any edit (max 3 loops).
- Gate: `python -m pytest -q` must end 0 failed.

## Rollback
Every behavioural change is a Profile field: `be_offset_pct=0.02`, `eject_guard=False`, `kelly_no_edge_gate=False`, `raw_quorum_gate=False`, `collinear_penalty=1.0`, `scalp_tp_mode='static'`. Code rollback: `git diff -- core/engine.py core/micro_swarm.py core/consensus_engine.py`.

## Result (2026-11-09)
`python -m pytest -q`: 1881 passed, 0 failed (baseline 1834 + 47 new). 12 tests re-pinned to the new fee-aware levels, 4 opted out with a comment (legacy 360 s EJECT timer x2, sizing mechanics x2). Handoff: docs/harness/session-handoff.md "2026-11-09". Not run: demo, replay, live. Economic effect unmeasured.

---
# 2026-11-13 — Alpha re-engineering directive (autopsy, net-EV gate, session stop, SMC-Fibo study)
Design + pre-registration: [plan_alpha_reengineering_2026_11_13.md](plan_alpha_reengineering_2026_11_13.md) (frozen before the first run).
Autopsy: [../research_upgrade/AUTOPSY_REPORT_2026.md](../research_upgrade/AUTOPSY_REPORT_2026.md). Checks run: [checks.md](checks.md).
Baseline before edits: HEAD `6a621f8` had 2 failing tests of its own (`tests/test_anti_churn.py` breathing, launcher pin in
`tests/test_sniper_runner_carry_2026_11_10.py`); both fixed / re-pinned here.
Result: loss = fees (4.57 bps/trade) on a zero gross edge (+0.62 +- 1.15 bps); SMC-Fibo HOLDOUT -14.1 +- 3.3 bps (REJECT, not
wired); the net-EV gate (on in `run.bat micro`) refuses every micro AUTO entry on the current ledger. Nothing started.
Rollback: Profile `micro_net_ev_gate='off'`, `micro_session_losses=0`; delete `MICRO_NET_EV_GATE` in `run.bat`; the
`MICRO_HARVESTER=on` refusal is one `if` in `config/settings.py`.
