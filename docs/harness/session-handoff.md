# Session Handoff

## Current State
AI toolkit installed on 2026-10-04; evidence in docs/AI_TOOLKIT.md and F:/AI_Agent_Toolkit/mcp-verification.json.

## Next Steps
Read AGENTS.md, README.md, feature_list.json and progress.md. Select one concrete unfinished feature from the user's request. Run ./init.sh before and after changing code. Update feature state and progress with actual evidence.

## Known Limitations
NotebookLM has no authenticated profile yet. JEV/Clef original weights are not installed; native SpikingBrain dependencies require a compatible Linux GPU environment. Optional MAI and AgentOps cloud integrations remain disabled until configured.

## Clean Restart
No new automatic model loading or paid agent runs. Existing app launchers remain the startup path. Preserve all existing datasets, voices, weights, keys and uncommitted changes.

## Research prompt handoff — 2026-10-07
- Brief: prompts/FORM_CLAUDE_NANG_CAP_2026-10-07.md. Sources: prompts/NGUON_NANG_CAP_2026-10-07.md. Continuation/setup prompts are in the same directory; CLAUDE.md points at the evidence-led brief.
- Recompute current logs before using snapshot figures. Row 1 TEST has no gross/fee; 33 remaining rows reconcile. BTC and SOL, GTX and IOC historical groups differ in context; sample sizes do not establish causal edge or authorize scaling.
- Next implementation task: select one verified data/cost/fill/calibration bottleneck; no runtime change or live execution was performed by the prompt-preparation task. Cowork Instructions and model/effort still need user entry in the app.
- Checks: baseline project 1033 passed; post-edit init.sh project 1033 passed (73.83 s), toolkit 15 passed/1 skipped; artifact consistency/arithmetic and git diff --check passed. No claim of economic improvement has been made.

## Handoff — 2026-10-07 (q)
- FIRST: check the Binance DEMO account for SOLUSDT: the 14:26:50 UTC SHORT 0.61 @116.01 (STOP 116.42, TP 114.99 reduce-only) was open when the dashboard KILL fired; old code flattened without settling, so the ledger has no row and the close is UNKNOWN. A restart with adopt_external would re-adopt a leftover position.
- Data collection (no code needed): run demo as usual; every entry attempt now lands in data/engine_orders.jsonl and every trade carries policy/code/basis/markouts. Analyse with `python tools/ledger_audit.py` and the order log once >= 200 attempts / >= 100 fills exist under ONE policy id (E1-E3 in docs/research_upgrade/experiment_register.md).
- Decisions for the user: (a) NO EDGE gate (f* <= 0 over >= 20 trades -> no entry) — needs a shadow path or a policy-version window, else it locks a symbol forever; (b) whether demo (USD-M testnet book) is acceptable evidence at all given the basis — consider mainnet-book paper replay (hftbacktest + recorded L2) instead.
- Next code steps for Claude Code: (1) capture fill time from the user data stream (ORDER_TRADE_UPDATE `T`) instead of 0.1 s REST polling; (2) calibrate edge_bps vs markout/net on time-split holdout (E3) before touching any threshold; (3) VPIN bucket/threshold study (7 TOXIC flags in 33 min on SOL).
- Rollback: order_log=False; convex_sizing/alt_size_boost=True, alt_kelly_min_n=5 restore round (p) sizing; schema-2 fields are additive.
- Checks: python -m pytest -q 1052 passed (Cowork Linux VM, Python 3.10); toolkit tests not run here.

## Handoff — loss continuation brief, 2026-10-07 (r)
- Latest request/brief: prompts/FORM_CLAUDE_SUA_LO_SOFT_BE_2026-10-07.md, referenced by CLAUDE.md. Recompute latest ledger; 37-row snapshot net -1.24659858 U is DEMO only.
- First task: reproduce DOGE BUY 291 @0.08880 -> SOFT-BE average exit 0.08868, net -0.04524931 despite be_locked=true; trace soft-target vs confirmed exchange protection. Then instrument/reproduce SOL RATCHET cancellation/new-stop failure/restore using actual sanitized error codes. No profitability improvement or runtime fix was performed by this brief task.
- SOL ADOPT has now closed in the ledger; investigate accounting ownership/entry fees instead of treating its earlier UNKNOWN outcome as still open. Keep prior instrumentation and sizing patches.
- Post-edit init.sh: 1052 project tests passed (86.99 s), toolkit 15 passed/1 skipped; prompt consistency/snapshot and git diff --check passed. No trading process was started or restarted by this brief task.

## Handoff — 2026-10-08 (r)
- Decision for the user: enable `soft_exit_escalate` (caps tail, costs ~0.4-1.1 bps per protective exit in replay) or keep shadow; or move the exchange stop to the BE level at arm time (replay C: mean -1.5 bps, tightest tail). Data in docs/research_upgrade/experiment_register.md R2.
- Next demo run will record `stop_events` (code/meaning/trigger/bid/ask/mark/naked_ms) for any stop move failure and `shadow_escalate` for protective exits — check them before changing stop workingType (hypothesis: -2021 with CONTRACT_PRICE on the testnet book).
- Possible next code step (needs demo test, docs do not confirm): reduceOnly+quantity STOP_MARKET placed before cancelling the old stop to remove the naked window; watch for -2022/-4045.
- Rollback: soft_exit_escalate stays False; ratchet_max_fail large restores endless retries; all ledger fields additive.
