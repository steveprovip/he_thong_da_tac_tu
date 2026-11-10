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

## Handoff — 2026-10-08 (s)
- Decide: live TP/SL (rr 0.47) now fails the 1.30 gate — live will refuse every entry until TP/SL or the loss budget changes (or net_rr_min is lowered for live).
- MR lane is effectively off (max rr 0.875); re-enable only with new MR brackets evaluated out of sample.
- super_size is ON in demo (risk up to ~0.25 U/trade); evaluate rows with super_size=True before keeping it; rollback super_size=False.
- Fill rate: GTX unfilled orders are queue-limited; next experiment is queue position (join vs improve 1 tick) using touch_qty_last, not faster re-peg.

## Handoff — 2026-10-08 (t)
- chop_scalper stays 'shadow' unless the user accepts a strategy measured at -6 to -7.5 bps per scalp; SHADOW_SCALP rows in engine_orders.jsonl give demo-venue opportunity counts.
- drift_accel (0.5x) and the BTC minimum-size probe are ON in demo: evaluate rows with drift_probe / probe set before keeping them.
- Next useful experiment: queue-imbalance-directed scalp (bookTicker has bid/ask qty) — preregister before running; demo fill starvation is a testnet queue artefact.

## Handoff — 2026-10-08 (u)
- BTC is observe-only in demo (rollback observe_only=()). Larger SOL/DOGE sizes are unvalidated: review rows by policy id after the next runs; rollback alt_size_floor=False and SUPER_SIZE 0.50/450.
- EJECT-FAST is mechanically neutral in replay; judge it on SOL/DOGE rows with reason EJECT-FAST vs EJECT-BE.

## Handoff — 2026-10-08 (v), research brief for fills and lane feasibility
- Read prompts/FORM_CLAUDE_KHOP_LENH_NET_EV_2026-10-08.md for the user's latest no-fill/loss request. Recompute the snapshot before drawing conclusions. BTC is deliberately observe-only; MR/live bracket feasibility and GTX expiry need separate investigation. SOL also logged an UNFILLED BUY 0.80 at 13:03:44; this is not solely a DOGE problem.
- Next implementation: choose one reproducible funnel/feasibility/execution bottleneck; compare risk-matched alternatives offline. Keep negative-replay scalp in shadow, preserve auth/risk/stop protections, and do not raise size just to obtain fills. No trading process, live mode or runtime parameter was changed by the prompt task.
- Completion: project 1158 passed (79.90 s), toolkit 15 passed/1 skipped; prompt-copy/structure and diff checks passed. Cowork app model/High and folder access still require selection in the app; writing CLAUDE.md does not apply them. Rollback for this documentation task is to remove the v brief/pointer/harness record only; preserve all r/u runtime changes.

## Handoff — 2026-10-08 (v)
- The running demo process still has the pre-(v) code. After the user restarts it, the dashboard shows real counters and `scanner.funnel` / `scanner.feasibility`; collect >= 2 h before choosing a statistical gate to ablate (use the "only blocker" table).
- Decisions for the user: (a) MR lane is enabled but cannot pass net RR 1.30 (max 0.875) — keep blocked, or turn it off explicitly; lowering the gate needs a >= 53 % MR win rate on holdout (history 0/2). (b) Live brackets are infeasible (rr 0.474: loss-budget SL 0.89 % > TP 0.50 %). (c) Candidate `peg=False` (replay +0.23 bps/opportunity on mainnet books) — not applied.
- Evidence: placement does not create edge; every entry policy is negative after fees on mainnet replay, so more fills alone will not fix P&L. Next data: aggTrade + bookTicker for SOL/DOGE (queue replay with hftbacktest RiskAverse/ProbQueue, MR VWAP), user-stream fill time `T`, >= 200 attempts under one policy.
- Rollback: see docs/research_upgrade/report_before_after_2026-10-08v.md §10.

## Handoff — 2026-10-08 (w)
- New code needs a user restart of demo. Then SHADOW_CROSS / SHADOW_DYN rows appear in data/engine_orders.jsonl (would-be taker price, OFI, taker RR). After >= 30 rows re-run tools/counterfactual_super_alpha.py; propose super_cross='on' only if net per opportunity has CI95 > 0 on demo AND mainnet.
- Decision for the user: super_cross / dyn_ttl stay shadow (evidence negative) unless the user explicitly wants to test them on demo money (`super_cross='on'`, `dyn_ttl='on'` in PROFILES['demo']); live is always off.
- Ratchet tier 2 and venue retries are active in demo.

## Handoff — 2026-10-08 (x)
- After the user restarts demo: read scanner.funnel.combos_after_swarm and SHADOW_KER rows (other_blockers empty = adaptive KER would have submitted). Propose adaptive_ker='on' only if those rows' after-fee markouts have CI95 > 0.
- MR stays closed by net RR with the default 'vwap' bracket; mr_bracket='fixed' opens it (RR 1.389) but the 7-day replay is negative for every MR bracket — user decision.
- The binding constraint is signal edge (EDGE/SCORE gates block > 95 % of seconds; flow proxies negative after fees), not a logic lock.

## Handoff — 2026-10-08 (y)
- Sizing was NOT raised. If the user wants larger demo sizes: conviction_sizing='on' plus an explicit portfolio_risk_usdt (e.g. 2.5 U for a 10 % SOL tier), pre-registered stop rule: after >= 50 trades under one policy, revert unless the bps/trade CI95 is > 0. Never risk-at-stop 10 %.
- Re-run tools/simulate_position_sizing.py after each ~50 new trades; cs_* fields on order rows show what the conviction tiers would have sized.

## Handoff — 2026-10-08 (z)
- After the user restarts demo: check SOFT-PRESTOP exits vs RATCHET exchange exits (exit_mode, fee_bps) in the ledger; expected fewer 6 bps exits. The conviction breaker pauses sizing after -5 U over 30 conviction trades (clip 'breaker ...' on order rows).
- Entry edge is still the binding problem (every exit policy negative overall in replay).

## Handoff — 2026-10-08 (z2)
- After restart: watch CVD_SHIELD in scanner.funnel, 'align3' and 'early_invalid' fields on ledger rows (shadow evidence), '-LOSS' suffixes, and conviction 'regime' caps. Re-run tools/replay_alpha_gating.py / ledger analysis after ~50 trades before enabling align3 or early_invalidation.

## Handoff — 2026-10-08 (aa)
- Judge every change with tools/edge_check.py --by code; freeze code until SPRT decides (~45 trades at sigma 15 bps). Do not interpret < 30 trades.
- Next: make replay tools emit per-position bps lists for edge_check --paired; optional paper/shadow daily trend sleeve (ENS 1d LS + ENS_VT 1d LO) monitored with mSPRT; run tools/record_market.py --light continuously for maker-fill replay.
- Live cap 3.85 U is below Binance min notional (5 U; BTC 100 U): any trend sleeve is demo/paper only.

## Handoff — 2026-10-09 (bb)
- Every micro change: `python tools/ab_bench.py micro --days <range> --b-set field=value --register` before demo; a code change: `git worktree add ../gt_old <commit>` + `--a-code ../gt_old`. Agg source = synthetic book (book gates approximate); prefer `--source rec` once tools/record_market.py --light has run.
- Trend lane is PAPER (demo profile trend_lane='paper'): starts only when the user restarts the engine; outputs data/trend_paper_1d|4h.json(l). Do not route it to orders unless OOS DSR > 0.95 (currently 0.19 for 4H LS).
- Open questions: CVD shield never binds on mainnet tape (thresholds in raw USD); early_invalidation needs ~262 paired hours.

## Handoff — 2026-10-09 (cc)
- Autonomy is ON in demo after the next restart: watch '⚡ AI SELF-ADAPTED' events and snapshot['autonomy']; override or clear config/live_tunables.json by hand at any time (validated, risk only down, expires).
- First dreaming pass on the current ledger will cap cs_max_notional at $150 for 30 min (CONVICTION: top tier worst). Decision pending for the user: JEV fast-path trades inherit score = JEV/10 -> top conviction tier.
- Learning is slow by construction (~100 trades/week spread over 3 regimes x 9 arms); evaluate with a continuous engine_replay run, not chunked ab_bench (state resets per chunk).

## Handoff — 2026-10-09 (dd)
- After restart, demo micro lane is OBSERVER (micro_standalone=False) and the 4H trend lane trades on the demo venue: watch 'TREND 4H ·' events, telemetry trend_lane['4h_active'], data/trend_trades.jsonl, data/trend_active.json. Expect ~6 trades/month, most small losers, a few large winners.
- Revert: Profile demo trend_lane='paper', micro_standalone=True. Live untouched.
- Open items: trend P&L is not in the micro daily-kill meter; evaluate the lane after ~100 trades (edge_check / mSPRT on trend_trades.jsonl).

## Handoff — 2026-10-09 (ee)
- After restart the demo 4H lane enters only in TREND with volume/CVD confirmation, goes flat on CHOP closes, soft-locks profits after +2 % (chandelier 3.5 ATR), sizes min(10 % wallet, 1 % wallet at the 8 ATR stop) <= $600. Watch 'held back', 'CHOP', 'PROFIT-LOCK' events and regime_entry in data/trend_trades.jsonl.
- Revert: demo trend_regime='off', trend_confirm=False, trend_chop_exit=False, trend_lock_pct=None, capital_allocation_pct=None, trend_notional_max=300.
- Evidence still a hypothesis (OOS DSR 0.30, ~3.4 trades/month): do not route to live; evaluate after ~100 trades.

## Handoff — 2026-10-09 (ff)
- Demo after restart: break-even stop at +2 % (event 'BREAK-EVEN'), conviction size up to min(30 %, 2 % risk) <= $1 500 when 1H+4H+1D agree ('CONVICTION'), BTC shield logs only ('(shadow) would hold back'). trend_trades rows carry conv / be.
- Revert: demo trend_be_pct=None, trend_conv_alloc_pct=None, trend_btc_align='off', trend_notional_max=600.
- Do not raise leverage for the 25-40 %/yr target without demo evidence (x3 = DD ~22 %). Re-check the shield on shadow logs after ~30 alt entries.

## Handoff — 2026-10-10
- After restart, lane 2 (burst scalper) runs SHADOW on SOL/DOGE: virtual rows in data/micro_trades_shadow.jsonl, events 'BURST SHADOW', telemetry burst_lane. Expect ~-7 bps per virtual trade (replay). Active needs config/burst_verdict.json ACTIVE-ELIGIBLE (re-run tools/burst_scalper_replay.py --write-verdict on new aggTrades); never live.
- Run tools/fee_survey.py --quote USDC on the user's machine to read the real commission (0 % maker unconfirmed).
- Revert: demo burst_lane='off'.

## Handoff — 2026-10-10 (b)
- After restart, lane 3 (intraday 5m/15m) runs SHADOW on BTC/SOL/DOGE: rows in data/intraday_trades_shadow.jsonl (variant, would_block), events 'INTRADAY SHADOW', telemetry intraday_lane. Expect ~17-20 virtual S1 trades/day at ~-7 bps (replay).
- 'active' is refused by design; build an executor only if config/intraday_verdict.json gets an ACTIVE-ELIGIBLE (re-run tools/intraday_replay.py --write-verdict on new data), reusing the trend lane's persisted reduce-only order path.
- Pending suggestion: fix or remove the burst lane's latent active order path (same defect class).
- Revert: demo intraday_lane='off'.

## Handoff — 2026-10-10 (c)
- After restart, lane 3 shadow records R1_SESSION (band fade only in CHOP, ~1/day) and P15_tp50 (15m/1h pull-back, ~5/day) instead of S1/S2; rows carry regime, tp1, exit_how 'TP1+…'. Expect ~-7 bps per virtual trade (replay). Kline admission margin is 10 s on the server clock.
- Nothing to activate: every variant negative on HOLDOUT. A higher win rate (P15_tp35 60 %) came from TP1 + break-even reshaping, not from an edge. Next research should target gross edge >= 10 bps or lower fees, not more filters on the same signal.
- Revert: intraday_variants=('S1_SESSION','S2_slope3_tp60'), intraday_close_margin_s=5.0; demo intraday_lane='off'.

## Handoff — 2026-10-10 (d)
- Answer to "switch exchange?": not for the current strategies. Zero fees do not create an edge (report_exchange_pivot_and_zero_fee_2026-10-10.md); MEXC API orders cost 6/8 bps (more than Binance).
- Before any venue work: verify the account's real fee via the venue API, run tools/venue_probe.py on the trading machine, and require a signal with HOLDOUT gross >= 3 bps and CI > 0 on Binance first.
- After restart, lane 3 shadow also records P15_rr25atr. Revert: intraday_variants=('R1_SESSION','P15_tp50').

## Handoff — 2026-10-16
- After restart the demo stat-arb SHADOW lane uses the dynamic Z band (static 2.10 for the first 4 h); trend telemetry shows hurst / state3. No trading behaviour changes otherwise.
- To evaluate before any use: replay RegimeGate(mode='hurst') with tools/trend_regime_replay.py on the user's machine (data needed); replay the Hawkes FADE on recorded aggTrades; replay the RFC funding rule (2.5 / +1.5 / 4.0) in tools/funding_spread_replay.py beside the pre-registered H28 k2 rule.
- Revert: StatArbLane(z_alpha=None); RegimeGate 'hurst' is opt-in only. Live carry stays blocked by core/funding_carry.two_leg_capital_gate.

## Handoff 6C — 2026-10-18 (harness: handoff + Engram + backup)

### 1. Mục tiêu (Goal)
Đưa chuẩn bàn giao 6 thành phần, bộ nhớ Engram và backup SQLite vào repo này mà không đụng tới runtime giao dịch. DoD: quy tắc và template nằm đúng chỗ, Engram MCP chạy được, backup có restore-check, bộ test không regression.

### 2. Ràng buộc (Constraints)
Không khởi động hay restart engine, không đặt lệnh, không sửa third_party/, không commit (chờ người dùng duyệt). Chỉ ghi trong F:\he_thong_da_tac_tu_2 và hub F:\1tr_AI_agent.

### 3. Trạng thái thực tế (Real State)
Branch `main` @ 91ad6ff, chưa commit.
- File mới: `.mcp.json`, `.engram/` (config.json, manifest.json, chunks/cf104684.jsonl.gz), `docs/harness/HANDOFF_PROTOCOL.md`, `docs/harness/templates/handoff_6c.md`.
- File sửa: `AGENTS.md` (+2 dòng), `CLAUDE.md` (+1 dòng), `tests/test_upgrade_2026_10.py` (thêm `.mcp.json` vào allowlist), cùng file này và `docs/harness/progress.md`.
- Untracked có từ trước, không thuộc phiên này: `prompts/FORM_CLAUDE_OPUS5.5_TEST_MANAGER_AND_10K_LUXURY_SPA.md`, `prompts/MASTER_FORM_OPUS_5_5.md`, `third_party/{Hexis,Paymenter,PersonalJarvis,airllm,databasus,docker-mailserver,engram,kyojin,twelve-dollar-server-challenge}/`.

### 4. Bằng chứng (Evidence)
- `python -m pytest -q` sau khi thêm `.mcp.json`: 1607 passed, 1 failed (`test_root_is_clean_and_launchers_live_in_scripts`, allowlist chưa có `.mcp.json`), 130 s. Sau khi cập nhật allowlist: `tests/test_upgrade_2026_10.py` 18 passed.
- `mcp_smoke.py .mcp.json engram --expect mem_search mem_save mem_session_summary` → ok: 26 tools, server engram vendored-4d7f2a2.
- `engram search "bàn giao" --project he_thong_da_tac_tu_2` → Found 1 (#1). `engram sync` → `.engram/chunks/cf104684.jsonl.gz`.
- `sqlite_backup.py`: `data/testnet-journal.sqlite` 2,244,608 B, 14,541 dòng, integrity ok, restore ok, sha256 a03a9d37e2d7…
- Bộ test đầy đủ sau khi cập nhật allowlist: `python -m pytest -q` → 1608 tests, 0 failed, 123.4 s (run_checks C4.2, 2026-10-18 10:38 UTC; hub checks 14/14 PASS).
- Lệnh đọc lại toàn bộ: `F:\1tr_AI_agent\.venv\Scripts\python F:\1tr_AI_agent\tools\harness\run_checks.py`

### 5. Bước tiếp theo (Next Steps)
1. Sau khi người dùng duyệt commit: `git add .mcp.json .engram docs/harness AGENTS.md CLAUDE.md tests/test_upgrade_2026_10.py`. Không add third_party/ hay prompts/ có từ trước.
2. Lần đầu mở Claude Code trong repo này: duyệt MCP server `engram` khai báo trong `.mcp.json`.
3. Cuối mỗi phiên: `engram save` tóm tắt 6C → `engram sync` → viết entry 6C mới → validate bằng `check_handoff.py --entry`.
4. Muốn backup theo lịch thì người dùng tự đăng ký trong Task Scheduler: `F:\1tr_AI_agent\.venv\Scripts\python F:\1tr_AI_agent\tools\harness\sqlite_backup.py`.

### 6. Quyết định & Bài học (Decisions & Lessons)
- Engram được build từ source vendored trong Docker `golang:1.25` (CGO off), không dùng release zip (Defender từng báo nhầm bản này). `third_party/engram` giữ nguyên, git status sạch.
- Databasus không hỗ trợ SQLite, nên backup bằng online-backup của sqlite3 kèm restore-check. Databasus để dành khi có PG/MariaDB (Paymenter, Engram Cloud).
- `.mcp.json` buộc phải ở gốc repo, nên allowlist của test layout được cập nhật (verdict: Wrong expectation, spec đã được duyệt). Quy tắc FORM_*/CHAY_* không nới.
- Bản backup nằm cùng ổ vật lý SATA với dữ liệu: chỉ chống hỏng logic. Muốn chống hỏng đĩa thì cần thêm một bản sao ngoài ổ.
- Đặt `ENGRAM_NO_UPDATE_CHECK=1` trong `.mcp.json` để server MCP không gọi GitHub mỗi lần khởi động.

## Handoff 6C — 2026-10-24 (arch: H2-Regime + sandbox 1000)

### 1. Mục tiêu (Goal)
Nghiên cứu và xuất tài liệu thiết kế + kế hoạch triển khai cho ba việc: thuật toán đồng thuận 8 agent; module Harness² (H2-Regime) trong vòng thích ứng khi chuyển regime; sandbox đối kháng 1000 kịch bản theo quy trình MiroFish, chạy trước khi cho đẩy lệnh DEMO/LIVE. DoD: tài liệu nằm trong `docs/architecture/`, spec máy đọc được đã kiểm với code, không regression, không đổi runtime.

### 2. Ràng buộc (Constraints)
- Giữ nguyên ngưỡng 76.8 % / veto 70 % và Post-Only GTX (`core/consensus_engine.py`, `core/engine.py`).
- Không quét mạng, không khai thác lỗ hổng, không khởi động lại engine, không đặt lệnh, không sửa `third_party/`.
- Không commit/push (chờ người dùng duyệt). MiroFish là AGPL: không import, không chép code.

### 3. Trạng thái thực tế (Real State)
Nhánh `docs/h2-regime-sandbox-arch` tạo từ `main` @ 91ad6ff, chưa commit.
- File mới: `docs/architecture/{README.md, 01_PHAN_TICH_KIEN_TRUC_HIEN_TAI.md, 02_THIET_KE_HARNESS2_REGIME.md, 03_THIET_KE_SANDBOX_MIROFISH_1000.md, 04_KE_HOACH_TRIEN_KHAI.md, h2_surface_v0.yaml, sandbox_suite_v0.yaml, tools/check_arch_docs.py}`.
- File sửa trong phiên này: `docs/harness/progress.md` (+ mục 2026-10-24), `docs/harness/feature_list.json` (+ entry `arch-h2-regime-sandbox-20261024`, status `design`), và file này.
- Thay đổi có từ trước, không thuộc phiên này, giữ nguyên: `AGENTS.md`, `CLAUDE.md`, `tests/test_upgrade_2026_10.py`, `docs/harness/HANDOFF_PROTOCOL.md`, `docs/harness/templates/`, `.mcp.json`, `.engram/`, `prompts/*`, `third_party/*`.
- Ngoài repo: thêm một bài học YAML vào `C:\Users\Admin\.claude\memory\learnings.md`.

### 4. Bằng chứng (Evidence)
- `python -m pytest -q`: trước khi viết 1608 passed, 0 failed, 126.7 s; sau khi viết 1608 passed, 0 failed, 0 errors, 126.3 s (đếm từ `--junitxml`).
- `python tools/engine_replay.py --source agg --data data/replay_cache/agg_zips --symbols SOLUSDT --start 2026-09-08T00:00 --end 2026-09-08T06:00 --warmup-min 60 --quiet` → 93,003 msgs, wall 74.8 s (0.08 sim-h/s), 0 vị thế.
- `python docs/architecture/tools/check_arch_docs.py` → exit 0: surface 84 lever (GUARD 23 / INV 27 / DOM 34), 29 field đóng băng; suite 1000 (P3 650 / P5 350); 85 tham chiếu `file:line` hợp lệ.
- Tái hiện JEV FAST: `micro_swarm.evaluate` với 1 trigger FLOW, năm gate xanh, cost 6 bps → consensus thô 0.60, path JEV, score 0.998, allowed. TokenAuthority issue + consume OK; consume lần 2 → `Unknown or replayed token`. `Gates(rune=False)` → `RUNE VETO [RUNE]`. `ConsensusEngine().threshold_for('TREND')` = 0.70.

### 5. Bước tiếp theo (Next Steps)
1. Chủ sở hữu đọc `docs/architecture/README.md` và chốt D1, D4, D5, D7 (doc 04 §5).
2. D5: chạy `tools/record_market.py` liên tục cho SOL/DOGE/BTC để có ≥ 14 ngày băng. Đây là đường găng.
3. Sau khi duyệt: `git add docs/architecture docs/harness/progress.md docs/harness/feature_list.json docs/harness/session-handoff.md`. Không add `third_party/` hay `prompts/` có từ trước.
4. P0 (khóa bất biến) trên nhánh `feat/h2-p0-invariants`: TDD, pytest xanh, replay cố định giống hệt trước–sau.
5. Tiếp P1, P2, rồi P3 theo doc 04.

### 6. Quyết định & Bài học (Decisions & Lessons)
- Chốt: **port** phương pháp harness2 (Apache-2.0) thay vì import. Lý do: Python ≥ 3.12, `fcntl`, proposer chạy `claude --dangerously-skip-permissions`. Đã loại: chạy harness2 nguyên bản trên host có `.env.local`.
- Chốt: Monte Carlo 1000 kịch bản là sandbox tất định trên engine production (`engine_replay` + `SandboxVenue`). MiroFish chỉ sinh cú sốc tường thuật từ seed công khai, chạy cô lập. Đã loại: 1000 mô phỏng OASIS (không tái lập, tốn LLM, là mạng xã hội chứ không phải sổ lệnh, dữ liệu phải qua Zep Cloud).
- Chốt: judge là holdout **tương lai** (băng ghi sau lúc commit) + sandbox. Chọn mù theo scorecard, không thấy số holdout hay sandbox.
- Phát hiện, chưa sửa code: veto < 70 % không tuyệt đối trên đường engine (JEV FAST); CLI `core.runtime` hạ được ngưỡng ở mainnet; trend_floor mặc định 0.70 là cố ý → chờ D1, D2, D4.
- Lỗi đã gặp: PyYAML đọc `off` không nháy thành `False`, đã quote; loader ở P2 phải từ chối enum không phải chuỗi. Định nghĩa `veto_bypass` ban đầu sẽ đánh trượt chính baseline (lệnh JEV), nên đổi thành `hard_veto_bypass` cộng một chỉ số chỉ theo dõi.

## Handoff 6C — 2026-10-24 (P0a: invariants lock)

### 1. Mục tiêu (Goal)
Làm P0a theo `docs/architecture/04_KE_HOACH_TRIEN_KHAI.md`: gom bất biến I1–I8 vào `core/invariants.py` (hằng số + hàm kiểm trả danh sách vi phạm) và ghim chúng cùng hành vi JEV FAST hiện tại bằng `tests/test_invariants_lock.py`. DoD: chưa nối vào runtime; test đỏ trước, xanh sau; toàn bộ pytest không regression.

### 2. Ràng buộc (Constraints)
- Không nối module vào Engine hay `core.runtime`. P0b cần quyết định D1/D2.
- Không đổi hành vi giao dịch, không khởi động engine, không đặt lệnh, không commit/push khi chưa được duyệt.
- Giữ nguyên 76.8 % / 70 % / Post-Only GTX.

### 3. Trạng thái thực tế (Real State)
Nhánh `docs/h2-regime-sandbox-arch` @ 91ad6ff, chưa commit.
- File mới: `core/invariants.py` (102 dòng), `tests/test_invariants_lock.py` (145 dòng, 19 test).
- File sửa: `docs/architecture/README.md`, `docs/architecture/04_KE_HOACH_TRIEN_KHAI.md` (dòng trạng thái P0), `docs/harness/progress.md`, `docs/harness/feature_list.json` (entry `invariants-lock-p0a-20261024`), và file này.
- `rg invariants` trên `core/`, `live/`, `scripts/`, `tools/`, `research/`: chỉ có `core/invariants.py`; `core/micro_swarm.py` chỉ nhắc chữ này trong docstring. Không có import nào.

### 4. Bằng chứng (Evidence)
- `python -m pytest -q tests/test_invariants_lock.py` trước khi có module → ImportError (đỏ); sau khi có module → 19 passed.
- Cùng `tests/test_consensus_engine.py`, `tests/test_policy_lock.py`, `tests/test_quant_risk.py` → 49 passed.
- `python -m pytest -q` (toàn bộ, đếm từ `--junitxml`) → 1627 passed, 0 failed, 0 errors, 124.1 s (1608 cũ + 19 mới).
- `python docs/architecture/tools/check_arch_docs.py` → OK: 0 errors.

### 5. Bước tiếp theo (Next Steps)
1. Chủ sở hữu chốt D1 (mainnet từ chối ngưỡng thấp), D2 (giữ trend_floor 0.70 mặc định + kiểm tra ở đường production) và D4 (cách hiểu veto < 70 %).
2. P0b: gọi `check_profile` và `check_consensus(regimes=('NORMAL',))` lúc Engine khởi tạo; gọi `check_runtime_args(args, mode)` trong parser của `core.runtime` (D1); đưa các hàm này vào hard gate của sandbox (P3).
3. Commit sau khi duyệt, tách hai commit: (a) `docs/architecture` + harness, (b) `core/invariants.py` + `tests/test_invariants_lock.py`.
4. P1/P2 theo doc 04.

### 6. Quyết định & Bài học (Decisions & Lessons)
- Hàm kiểm trả list, không raise. Người gọi tự quyết refuse, log hay test, nên P0b có thể nối từng chỗ một.
- `check_consensus` mặc định xét đủ 4 regime. Với `ConsensusEngine()` nó báo đúng 1 vi phạm (TREND 0.70), tức F3 được ghim như hành vi cố ý của class. Đường engine chỉ xét NORMAL.
- Test JEV FAST mô tả hành vi **hiện tại** (consensus thô 0.60 vẫn được cấp token), không phải hành vi mong muốn. Đổi hành vi đó là quyết định D4, test phải đổi theo một cách có chủ ý.
- Lỗi đã gặp: so sánh nối chuỗi `approx(0.60) < VETO` gây TypeError trong pytest; đã tách thành hai vế.

## Handoff — 2026-11-09 (fee-drag fixes, Tasks 1-5; DEMO/replay only, nothing started)
- Plan and premise check: docs/harness/plan.md. Baseline `python -m pytest -q` 1834 passed -> after 1881 passed, 0 failed (+47 in tests/test_fee_drag_fixes_2026_11_09.py). No trading process, no live flag, no size/leverage change. Economic effect is UNMEASURED: these are behaviour changes proven on a mock venue, not evidence of profit.
- Premise corrections (the pasted autopsy was wrong on these): (1) `be_price` = `stop_out_pct()` (maker in + taker stop + slip = 0.08 %) + 0.02 % and `soft_be_price` = (entry fee + maker out) + 0.02 % = +0.06 %: the offset sat ABOVE the fees. The real leak is the SOFT path: the target is a maker exit, an escalation to taker costs 5 - 2 + 1 = 0.04 % more, so +0.06 % netted -0.02 %; and `be_locked` was set when the soft target was chosen. (2) `core.runtime` already passes NET pnl to `ConsensusEngine.record_outcome`; `core.engine.Engine` never calls it. The label was only wrong for a caller passing gross.
- T1 math: soft BE level = entry x (1 + (fee_in + maker_out)/100 + max(be_offset_pct, floor)), floor 0.08 % (SOL 0.065 %) = fee_shield 0.04 + taker escalation 0.04. VIP0 maker entry: +0.04 + 0.08 = +0.12 % (IOC entry +0.07 + 0.08 = +0.15 %). A taker-escalated exit costs 0.08 %, so the level nets +0.04 % instead of -0.02 %. BE arms only when MFE >= max(be_trigger_pct 0.12, level); `be_locked` only when `fee_hurdle_covered`. HARD exchange-stop BE keeps `be_offset_hard_pct` 0.02 % (0.08 + 0.02 = +0.10 %): one shared 0.08 would put it at +0.16 % > be_trigger and `_manage_hard` would market-close at +0.12..0.16 % ("already crossed").
- T2 math: `scalp_min_tp(0.15)` = (0.15 + 0.08) x 1.35 + 0.04 = 0.3505 % - the reported TP is the owner's 11-08 net-RR floor, not an ATR artefact. A TP of 0.09-0.14 % at SL 0.15 % has net RR 0.22-0.43 and break-even win rate 0.23/(TP - 0.04 + 0.23) = 79 % (0.09) .. 70 % (0.14); the 10-08t replay of that shape lost -6.3..-7.5 bps/scalp (CI < 0). So `scalp_tp_mode='atr'` (TP = clamp(0.6 x ATR15, 0.09, 0.14)) is added OPT-IN (default 'static') and only bites with `scalp_net_rr=False`. EJECT guard is default ON: the scratch order keeps its floor past `eject_hard_s` while gross < fee hurdle (entry fee + maker exit = 0.04 %) and no adverse momentum (CVD vel 10 s <= -50k, OFI <= -0.30, 15 s velocity <= -0.025 %, toxic VPIN), at most `eject_defer_max_s` 120 s more; EJECT-FAST untouched.
- T3: `micro_swarm.evaluate(..., collinear_penalty=0.5)`: LUMEN (FLOW/SURGE) + TIDAL on OFI/SQUEEZE (flow family) same side -> TIDAL confidence x0.5 and its edge x0.5 (JEV path too). Score LUMEN+TIDAL+gates = 0.20 + 0.10 + 0.40 = 0.70 < 0.768; NORO (0.90) or TIDAL on LIQ/DEPTH (0.80) restores it. WEIGHTS/threshold/veto untouched (I1-I8 test green). Side effect: SUPER_SIZE, convex 1.5x and the squeeze 1.00 consensus now need independent confirmation (squeeze caps at 0.90); 3 sizing tests set `collinear_penalty=1.0` on purpose.
- T4: `record_outcome(votes, side, pnl, fee_paid=None)`: net = pnl - fee_paid when given, else pnl (NET); win iff net > 0 and vote.side == side.
- T5: `entry_edge_block` in `_execute` (AUTO only): Kelly state NO EDGE (f* <= 0, n >= kelly_min_trades 20) -> refuse, no minimum-size fallback, unless the SHADOW path lifts it (below); JEV path with raw consensus < 0.768 or unknown -> refuse (`ms.evaluate` and the D4 test unchanged). SAMPLE (< 20 trades) stays open. SHADOW PATH (owner choice 2026-11-09, replaces my 6 h TTL; the 10-07q handoff warned a NO EDGE gate "locks a symbol forever"): each NO EDGE refusal opens ONE virtual trade (`edge_shadow_arm`, no order, at most one per 20 s): the maker quote the entry would have joined + the same `bracket_plan` TP/SL. It fills only when the tape trades THROUGH the quote within 30 s (else NOFILL, uncounted); TP only when the tape trades through it (net = TP - 0.04 %); stop = print or SL with taker + slip (net = -SL - 0.08 %); no TP/SL after 600 s -> exit at the touch. Results append to engine_orders.jsonl (`SHADOW_EDGE_OPEN/CLOSE/NOFILL`, reloaded on restart) and feed `kelly_of`; the gate lifts only when the virtual window (kelly_window, n >= 20) is Kelly EDGE, and locks again when it turns. Lifted entries still use the minimum size (real f <= 0) until the real window recovers. Telemetry: `quant.edge_shadow`. Caveat: virtual fills ignore queue position and market impact, so this is a screening signal, not proof.
- Decisions for the user: (a) RESOLVED 2026-11-09 by the owner: keep `scalp_tp_mode='atr'` opt-in (default 'static'); needs 70-79 % wins, replay says negative. (b) RESOLVED 2026-11-09 by the owner: shadow path replaces the 6 h TTL (`edge_shadow=False` = strict block, no recovery). (c) `raw_quorum_gate` supersedes D4 at the funnel: expect fewer entries (JEV-lifted ones refused); do not raise size to compensate. (d) shadow-measure the guards before trusting them: SOFT-BE net, EJECT exits and refused-entry counts on a time-separated holdout.
- Tests edited because they pinned the old constants (each re-derived; DOGE #37 matched by hand: gross -0.02037, fee 0.0103324, pnl -0.0307024 at exit 0.08873): test_soft_exit_lifecycle (level 0.08886 -> 0.08891, prices shifted +0.00005), test_winrate_maximizer, test_two_mode_engine (soft BE 1.0006 -> 1.0012, IOC 0.15 %, squeeze 0.90), test_fee_optimization_soft_maker (soft BE now above the ratchet stop), test_sol_priority_chop_ejection + test_world_quant_economic_engine (legacy 360 s timer, `eject_guard=False`), test_mr_concordance_and_net_rr + test_portfolio_multi_slot (`collinear_penalty=1.0`). plan.md lives in docs/harness/ because tests/test_upgrade_2026_10.py keeps the repo root clean.
- Rollback (Profile fields): `BE_OFFSET_FLOOR_DEFAULT`/`BE_OFFSET_FLOOR` back to 0.02 plus `be_offset_pct=0.02` restore the old soft level; eject_guard=False; collinear_penalty=1.0; kelly_no_edge_gate=False; raw_quorum_gate=False; scalp_tp_mode='static'. Code: `git diff -- core/engine.py core/micro_swarm.py core/consensus_engine.py`.

## Handoff — 2026-11-09 (micro-capital fill starvation: adaptive TTL, hybrid IOC, 4-coin rotation, compounding)
- Plan, arithmetic, findings: docs/harness/plan_micro_rotation.md. Operator guide: docs/MICRO_CAPITAL_5USD_DEPLOYMENT.md. `python -m pytest -q` 1923 -> 1963 passed, 0 failed (+40 in tests/test_micro_rotation.py). Code and mock-venue tests only: nothing ran against an exchange, no live start. The 18:52:40 DOGE SELL is not in data/engine_orders.jsonl (last row 11-08 14:44), so no claim is replayed.
- Why 80.2 % was cancelled at 3 s: `gtx_ttl_s` 3.0 and the existing `dyn_ttl` extension is `shadow` in the demo profile AND needs score >= 0.90, so 0.802 could never extend; re-pegs were unbounded (each gives up queue priority); the message printed the configured TTL, not the wait.
- P1 (micro mode only, AUTO non-MR/SCALP): `micro_keep` extends 3 s -> 10 s (then +1 s to 15 s) while VPIN is KNOWN and < 0.65 and `tox_check` says QUOTE; cuts at once on mid -3 bps against the trade, touch beyond `peg_max_drift_pct` 0.15 %, dirty VPIN; <= 2 re-pegs (`micro_max_reprices`). Unknown VPIN keeps the old 3 s. Order rows carry `micro_ttl_s`, `ttl_cut`. Not measured: a longer rest raises fills that happen because price walked through the quote (adverse selection).
- P2: `micro_ioc_route`: IOC only if micro active, RAW consensus >= 0.80, OFI with the trade >= 0.80, TP >= 0.25 % AND fee-true net RR with a taker entry >= `net_rr_min` 1.30 (loss = SL + 0.12 %, win = TP - 0.08 %, so TP >= 0.43 % at SL 0.15 %, >= 0.50 % at SL 0.20 %: the directive's 0.25 % alone is a 61 % break-even win rate), tape guard QUOTE. Else Post-Only GTX. `micro_ioc` off | shadow | on, env `MICRO_IOC`. 'on' is an invariant I5 deviation: `check_profile` reports it, demo prints INVARIANT WARN, an ARMED LIVE ENGINE REFUSES TO START. Shipped profiles stay 'off'; `run.bat micro` sets 'on' for the demo rehearsal.
- P3: `MICRO_UNIVERSE` DOGE/XRP/ADA/1000PEPE (`micro_symbols`, env `MICRO_CAPITAL_SYMBOLS`), `FALLBACK_SPECS` + ADA/1000PEPE (offline; check exchangeInfo). Forced micro (`MICRO_CAPITAL_MODE=on`) scans BTC, SOL (observers) + the 4 coins; the router ranks KER x |OFI L10| every 5 s (`micro_scores`, shared via RiskBook) and `better_peer` now runs in micro mode, so the best-ranked candidate takes the single slot. Live is NOT rotated (one engine by design). XRP/ADA/1000PEPE signal thresholds are uncalibrated (default 0.1 USD scale).
- P4: `MicroCapitalSizer.qty(equity, leverage, risk_notional)`: target = min(0.85, 0.20) x E x L, largest lot <= target, floor 5.10, ceilings: 0.85 margin share, the 0.05 U loss at the stop (DOGE's SL floor is 0.50 %, so ~8.6 U; 17.9 U at a 0.20 % stop) and `equity_cap` x leverage. E = 5 -> 75 DOGE as before, E = 10 -> 125 DOGE on DOGE (145 without the loss ceiling). The directive's `max(5.10, min(0.85 x E x L, 0.25 x wallet_cap))` cannot scale as written (undefined cap; constant cap = constant; cap = buying power = 6.25 U at E = 5 > the 6.0 window), so growth_frac 0.20 keeps the 5 U behaviour; 0.25 is `micro_growth_frac`. A forced run on a wallet above 15 U uses `micro_bankroll` (5, env `MICRO_CAPITAL_BANKROLL`) as equity.
- GAP IN THE PREVIOUS DELIVERY, fixed here: `run.bat micro` (DOGE-only) still started the 4H trend lane (demo profile `trend_lane='active'`, 10 % of the wallet per entry, outside every micro rule). Now parked when the mode is forced and `TrendExecutor.step` refuses entries on any micro-active engine. If a DOGE 4H trend position appeared on the Demo account during an earlier micro run, that was the cause.
- Decisions for the user: (a) RESOLVED 2026-11-09 by the owner: keep `MICRO_IOC=on` in run.bat micro (Demo rehearsal) - live still needs an explicit relaxation of I5; (b) rotation on live (more than one engine) is not built; (c) growth_frac 0.20 vs 0.25; (d) calibrate XRP/ADA/1000PEPE before trusting their signals; (e) after a demo run compare fill rate / 5 s markout of `micro_ttl_s` orders vs 3 s ones, and IOC rows vs the GTX alternative, from engine_orders.jsonl.
- Rollback: `micro_ttl=False`; `MICRO_IOC=off`; `MICRO_CAPITAL_SYMBOLS=DOGEUSDT`; `micro_rotation=False`; `micro_growth_frac` tiny (no compounding); code: `git revert` the commit.

## Handoff — 2026-11-09 (b) (micro deadlock: RR floor 1.10, JUMPY scalping, 10 s TTL floor, IOC thresholds)
- Plan and arithmetic: docs/harness/plan_micro_unshackle.md (supersedes the TTL / IOC thresholds of the section above). `python -m pytest -q` 1963 -> 1986 passed, 0 failed (+ tests/test_micro_unshackle.py, updated tests/test_micro_rotation.py). Mock venue only; the 19:44-19:57 session is not in data/engine_orders.jsonl (last row 11-08 14:44), so the causes were read from the pasted log lines and the code. These changes remove refusals, they add no edge and nothing shows they make money.
- T1: the pasted `scalp_net_rr_min` is not the refusing gate; `net_rr_min` 1.30 is. The logged TP 0.217 / SL 0.075 is the MR fixed bracket 0.29 / 0.10 (RR 1.39, passes the churn pre-gate) x the SIDEWAYS policy x0.75 (fees stay fixed -> RR 1.15, refused in `_execute`): approved and refused on every tick. `Profile.micro_net_rr_min` 1.10 now drives `net_rr_eff()` / `scalp_rr_min()` in micro mode (churn gate, `_execute`, scalp TP lift). Break-even win rate 47.6 % (43.5 % at 1.30); it is NOT a guaranteed positive expectancy. A 7.5 bps stop is 5 DOGE ticks. Not done: re-lift TP to the floor after the regime multipliers (as SCALP does).
- T2: JUMPY + SCALP was two gates in series: the engine freeze (`regime_check`) and the policy size x0.3 falling under the venue lot. `micro_jumpy_scalp()` allows it in micro mode when spread < 2 bps and VPIN known < 0.70 (unknown = frozen); in micro mode a SCALP keeps the venue lot instead of being refused for x0.3 / the scalp stop-floor shrink (`micro_floor` in the order row); stop x1.5 stays. `core/regime_classifier.py` is NOT edited (it can only tighten; `check_policies()`). Gap risk: `micro_check` bounds the planned loss, a 5.1 U lot reaches 0.05 U at a 0.98 % gap.
- T3: `micro_ttl_s` 10 is a hard floor: only spread > 3 bps (`micro_max_spread_bps`), mid -3 bps against the trade (`micro_adverse_bps`; the spec said 0.15 %, kept tighter on purpose: set 15 for the literal reading) and touch beyond the 0.15 % peg cap cancel inside it; unknown / dirty VPIN and toxic markouts no longer do (the 3 s cancel was my own unknown-VPIN rule). After the floor, 15 s needs a clean book. <= 2 re-pegs.
- T4: IOC defaults RAW consensus 0.78, OFI 0.70, TP 0.20 %, `micro_ioc_net_rr_min` 1.05. Still mostly unreachable: RR 1.05 with taker entry + stop needs TP >= 1.05 x SL + 0.206 % (0.285 % at SL 0.075 ... 0.416 % at 0.20); the logged bracket is RR 0.70 with a taker entry (59 % break-even). `core/exec_funnel.py` untouched (the gate is `Engine.micro_ioc_route`).
- Decisions for the user: (a) mid-adverse cut 3 bps vs the literal 0.15 %; (b) re-lift TP to the RR floor after regime scaling instead of lowering the floor; (c) a wider TP for IOC bursts is the only way to make IOC reachable (unvalidated); (d) after a Demo run count refusals by reason (`OKAPI refused`, `REGIME GATE`, `ttl_cut`) and compare fills / 5 s markouts before and after.
- Rollback: `micro_net_rr_min` 1.30, `micro_jumpy_scalp=False`, `micro_ttl_s` small + `micro_adverse_bps`, IOC fields back to 0.80 / 0.80 / 0.25 / 1.30; code: `git revert` the commit.

## Handoff — 2026-11-09 (c) (micro TP lift after regime scaling)
- Owner chose the lift offered in (b). Plan: docs/harness/plan_micro_unshackle.md section 7. `python -m pytest -q` 1986 -> 1991 passed, 0 failed. Mock venue only.
- `Profile.micro_tp_lift` (True): in micro mode, for non-SCALP paths, TP is raised to the fee-true floor TP (`Engine.min_tp_for`) after the regime multipliers, capped at the UNSCALED TP (the regime may only tighten). At the current micro floor 1.10 the logged 1.145 setup needs no lift; with the floor at 1.30 it enters with TP 0.2415 % (was 0.2175 %). Floor 2.0 on the same setup lifts to the unscaled 0.29 % (RR 1.61) and still refuses. Order rows carry `tp_lift`. Not measured: hit rate of lifted TPs (11 % farther).
- Rollback: `micro_tp_lift=False`; code: `git revert` the commit.

## Handoff — 2026-11-09 (d) (micro noise floor after the 21:33 / 21:34 Demo losses)
- Plan: docs/harness/plan_micro_noise.md. `python -m pytest -q` 1991 -> 2012 passed, 0 failed (+ tests/test_micro_noise.py). Mock venue only; the diagnosis is read from the ledger rows (data/engine_trades.jsonl rows 111-112), nothing is replayed and nothing here adds edge.
- Ledger facts: both losses are MR SELLs (regime DOWN, macro MIXED) with the MR fixed bracket x SIDEWAYS 0.75 = SL 0.075 % / TP 0.2175 %, taken against POSITIVE OFI (+0.15 / +0.18) and stopped in 7 s / 11 s (exit +0.101 % / +0.082 % = the stop plus 0.7-2.5 bps slip). They were refused by the 1.30 floor until 11-09b lowered it to 1.10 (I flagged the 5-tick stop then): the floor change unmasked them.
- T1/T2: `micro_bracket`: SL >= max(0.22 %, 1.2 x ATR 1 m); TP raised to the fee-true net RR 1.40 (0.46 % at SL 0.22), >= 1.8 x ATR 1 m; applied in `bracket_plan` and again after the regime multipliers; scalp lane gets the SL floor and TP to RR 1.10; live (loss-budget SL) untouched. The directive's TP 0.38 % is RR 1.13, not 1.40, so TP follows the RR and the commit subject says 0.46 %. A driftless tape hits TP first 32 % of the time (SL / (SL + TP)) vs 41.7 % needed: the bracket cannot create edge; each stop-out costs ~0.0153 U (2x before). EJECT time-decay (180 s) unchanged: expect many near-break-even timer exits with a 0.46 % TP.
- T3: `micro_entry_block`: KER >= 0.30, 5 m macro not opposing, CVD 3 m not opposing, OFI L10 with the trade >= 0.65 (`ofi_10s`; the engine has no 1-second OFI); unknown = blocked; funnel blockers `MICRO_KER` / `MICRO_ALIGN` (exec_funnel GATE_CLASS) + enforced in `_execute` for every AUTO path. Both logged trades fail it. It effectively closes the MR (fade) lane in micro mode (a strategy decision) and leaves the 0.30-0.40 KER band to MR/scalp. No separate spread-volatility rule (no such feature). Opposite direction from 11-09b: expect far fewer entries; read the funnel counts.
- T4: the halt did not fire on 2 losses: it counted the 11-08 14:36 BUY SCALP loss (31 h old, old code, no micro rules) + PEPE + XRP = 3. Threshold already 3. Fix: ledger rows / positions carry `micro`; `micro_halt_until` counts only those and `halt_until(lookback_s=24 h)` ignores older losses. The two logged losses predate the flag, so the active halt clears on restart and the micro streak restarts at 0.
- Decisions for the user: (a) keep the MR lane effectively closed in micro mode, or give it its own (non-aligned) rules; (b) SL 0.22 vs 0.20 (TP 0.46 vs 0.432); (c) compare after a Demo run: stop-outs/timer exits/TP hits by `bracket`, refusals by `MICRO_KER` / `MICRO_ALIGN`.
- Rollback: `micro_bracket=False`, `micro_align=False`; halt filter: remove the `micro` filter in `Engine.micro_halt_until`; code: `git revert` the commit.

## Handoff — 2026-11-09 (e) (zero-turnover directive: dual mode CHOP harvester / TREND sniper; DEMO/replay only, nothing started)
- Plan: docs/harness/plan_micro_dual.md. `python -m pytest -q` 2012 -> 2043 passed, 0 failed (+ tests/test_micro_dual.py 31; tests/test_micro_noise.py KER pins 0.30 -> 0.28). Mock venue only; no process started, no live, no capital / leverage change. Economic effect UNMEASURED.
- Root cause (read from the code, not the pasted text): `micro_entry_block` (KER >= 0.30 + 5 m macro + CVD 3 m + OFI >= 0.65) ran for every AUTO path incl. SCALP, whose own lane is KER < 0.40 -> nothing could enter below KER 0.30. Every order also needs `TokenAuthority` (score >= 0.768): not relaxed.
- P1: `micro_lane` splits at KER 0.28 (`micro_ker_min` 0.30 -> 0.28; unknown KER = no lane). CHOP = `harvest_lane`: `chop_scalp` takes its mode from `micro_harvester` (**shadow** default; env `MICRO_HARVESTER`), bracket TP 0.15 % / SL 0.15 % (TP guard 0.12-0.18 %), maker only, 45 s resting entry with hard cuts, EJECT-BE 45 s, guard = break-even win rate <= 70 % (0.15/0.15 = 67.6 %), not a net-RR floor. TREND: SL 0.22 %, TP 0.46 % -> 0.70 % with KER 0.28 -> 0.60 (`micro_bracket`, idempotent). Live + `on` is an invariant error. No two-sided quoting (one-way mode, 1 position, 1.4 bps spread < 4 bps maker round trip).
- P2: `cvd3m_soft` (CVD 3 m opposing + OFI L10 >= 0.50 + 60 s move with the trade >= max(0.03 %, 0.5 ATR 1 m), new feature `px_vel_60s`) -> size x0.75 instead of a block in `micro_entry_block`, `scalp_candidate`, `align3_check` (3 m layer). The x0.75 is a no-op at the 5 U lot (0.75 x 5.05 U < 5 U venue minimum): at 5 U this is a gate removal. Macro 5 m stays a block.
- P3: rotation `harvest_score` = ATR 1 m x abs(OFI); dead tape (KER < 0.15) never holds the slot and yields to the best live-chop rival (`harvest_pick`, funnel `HARVEST_YIELD`). Funding carry NOT wired (XRP 30.9 % APR on 5 U = 0.0042 U / day, needs a spot leg; `carry_sim` stays virtual).
- P4: found `micro_equity()` = constant `micro_bankroll` 5.0 on a forced run over the Demo wallet -> a won USDT never raised the lot. Now bankroll + realized NET pnl of `micro` rows, in [0, 15]. Directive lots (75/105/150/300 DOGE) are 1.066 x E; the sizer gives E x 1.0 (72/98/140 at E = 5/7/10); 300 DOGE = 21.3 U would lose 0.064 U at the stop (> 0.05 U invariant), so the 16.7 U `risk_notional` ceiling stays.
- Money arithmetic: harvester EV per trade = w x 0.11 % - (1 - w) x 0.23 %: -0.026 % at w 0.60, 0 at 0.676, +0.025 % (+0.0013 U) at 0.75; 30 trades / day at w 0.75 = +0.038 U. P(3 straight losses in 20 trades) = 44 % at w 0.65 -> the 24 h halt will dominate an `on` run.
- Decisions for the user: (a) arm the harvester on the Demo (`set MICRO_HARVESTER=on` before `run.bat micro`) or keep shadow until a holdout; (b) accept the 3-loss / 24 h halt as is for a high-frequency lane; (c) read after a Demo run: shadow rows `harvest` / `be_wr`, order rows `harvest` / `cvd_soft`, funnel `HARVEST_YIELD` / `MICRO_ALIGN`, telemetry `micro_capital.lane / harvester / equity`.
- Rollback: `micro_harvester='off'`, `micro_cvd_soft=False`, `micro_sniper_tp_max_pct=0`, `micro_equity_track=False`, `micro_ker_min=0.30`; code: `git revert` the commit.

## Handoff — 2026-11-09 (f) (profit-acceleration directive: 3 slots, PnL-linked lot, soft 5 m macro, carry telemetry; DEMO only, nothing started)
- Plan: docs/harness/plan_micro_profit_scale.md. `python -m pytest -q` 2043 -> 2063 passed, 0 failed (+ tests/test_micro_profit_scale.py 20; 4 older tests pin `micro_max_positions=1`; test_invariants_lock count 3 -> 5). Mock venue only. Also commits the `run.bat micro` arming line (`MICRO_HARVESTER=on`, asked in chat) that was left uncommitted.
- P1: Demo `micro_max_positions` 3 (live 1; invariant). `risk_cap()` micro = min(portfolio cap, kill + day_pnl); `portfolio_check` margin = n window lots <= 0.85 x rehearsed equity; `better_peer` yields only when better candidates fill all free slots. Shared halt / kill meters unchanged: 3 correlated stop-outs can trip the 24 h halt at once.
- P2: `MicroCapitalSizer.compound_target(E, L, pnl)` = plain x max(0, 1 + k x pnl), cap max(plain, margin share / slots); Demo k 5.0, live 0. Fixed my 11-09e bug: a bankroll above 15 U was clipped to 15. `MICRO_BANKROLL_DEMO` alias; `MICRO_MAX_POSITIONS`, `MICRO_ACCEL_K` env. The 0.05 U / 0.15 U invariants stay, so the lot is capped at 21.7 U (SL 0.15 %); +0.03..+0.08 U per trade is unreachable without breaking them. All-win path to E = 10 U: 636 trades (k 0) vs 450 (k 5).
- P3: chop-lane 5 m macro softened at OFI >= 0.55 (resting maker). The 23:05-23:13 XRP / PEPE locks were the CVD flow regime, left hard. Order / shadow rows carry `macro_soft`.
- P4: the carry sim was already running; its state was not in the snapshot. Now `carry_sim` published with `funding_total_usdt`, `funding_apr_pct` (instantaneous-rate estimate on a paper book sized from the virtual Demo wallet; replay reference +1.90 %/yr net).
- Decisions for the user: (a) RESOLVED by the owner: same-side cap 2 (11-09g `micro_max_same_side`, `slot_block(side)`); (b) keep or relax the shared 3-loss / 24 h halt now that 3 coins trade; (c) read after a Demo run: `macro_soft` rows vs the rest, `micro_capital.open / pnl / risk_cap`, funnel refusals `PORTFOLIO FULL / RISK / MARGIN`.
- Rollback: `MICRO_MAX_POSITIONS=1`, `MICRO_ACCEL_K=0`, `micro_harvest_macro_soft=False`; code: `git revert` the commit.

## Handoff — 2026-11-10 (exit asymmetry directive: ledger check + opt-in patient exits; DEMO only, nothing started)
- Plan: docs/harness/plan_micro_asymmetry_cure.md. `python -m pytest -q` 2067 -> 2079 passed, 0 failed (+ tests/test_micro_asymmetry.py 12). Pillar 1 (same-side cap 2) committed as 5dbd862.
- Ledger (data/engine_trades.jsonl, 23:05 Nov 9 - 07:38 Nov 10): totals reproduce (net -0.02755 U, payoff 0.141). Contradicted: SL 0.22 % (harvest is 0.15 %), "SL never hit" (01:44 -0.155 %, 04:22 -0.153 % via 3 taker fills, labelled EJECT-BE-LOSS by the -LOSS suffix at engine.py:4353), "15 trades" (one is dust), "within the SL cushion" (3 dumps had MFE <= 0.021 %), "+0.24 U" (ten TP wins; the ledger has one TP). Order log: 55 harvest attempts -> 13 fills.
- P2/P3: `micro_exit_policy` 'legacy' (default, unchanged) | 'patient' (harvest positions): `micro_patient_be_s` 180, scratch only at MFE >= `micro_patient_be_mfe_pct` 0.09 (the directive's wording inverts the legacy test), `eject_toxic` (VPIN >= 0.80 known / adverse flow >= flow_limit / regime flip / a profitable touch), `eject_floor` + 1 tick; ledger field `exit_policy`. No new time stop.
- P4 not implemented: margin share caps one of 3 slots at 7.08 U on 5 U; the ceiling lot needs a ~15.4 U bankroll (`MICRO_CAPITAL_BANKROLL=16`, no code); the same night at 21.7 U lots is -0.117 U.
- Decisions for the user: (a) arm `patient` on the Demo (`set MICRO_EXIT_POLICY=patient` before `run.bat micro`) knowing the barrier model says no gain on a random walk; (b) download Binance public aggTrades for XRP / DOGE / 1000PEPE 2026-11-09/10 (`tools/download_public_l2.py`) to replay the 14 entries with both policies; (c) the three losing dumps entered against the flow (MFE ~0): entry selection / adverse fills are the next suspect, not the exit.
- Rollback: leave `micro_exit_policy='legacy'`; code: `git revert` the commit.

## Handoff — 2026-11-10 (b) (drift unshackle directive: evidence check, label / knob / counters, replay of the 13 harvest entries; DEMO only, nothing started)
- Plan: docs/harness/plan_micro_drift_unshackle.md. `python -m pytest -q` 2079 -> 2100 passed, 0 failed (+ tests/test_micro_drift_unshackle.py 7, tests/test_replay_overnight_exits.py 14). Also commits the `run.bat micro` line `MICRO_EXIT_POLICY=patient` (asked in chat) that was left uncommitted.
- Premises vs code / funnel (session counters 08:08-08:32): DRIFT LOCK was a label; DRIFT first blocker 8 of ~5 300 samples; harvest-lane outcomes HARVEST_YIELD 154, CVD3M_LOCK 38, MACRO_LOCK 22; the two PEPE refusals were KER 0.40 / 0.31 (trend lane). ADA ZEPHR veto 879/879 (tick 5.65 bps > 3.0).
- Changed: `drift_exempt` (+ radar label `CHOP HARVEST`, snapshot `flow.drift_lock`), `micro_harvest_ofi_min` 0.30, `micro_harvest_macro_ofi` 0.30, funnel `SWARM_*` counters. NOT changed: VESKA quorum, CVD flow regime lock, `micro_ofi_min` for KER >= 0.28, ADA's cap, the halt, caps.
- Replay (data/replay_overnight_20261110, 13 windows, untracked), CORRECTED: the Demo book is independent of mainnet (basis -8..+10 bps at the fill, up to 19 bps drift while open). Raw prints: legacy -0.0342 / -0.0327 U vs ledger -0.0307, patient -0.0294 / -0.0287, barriers -0.0433; rebased by the basis (default now, `--no-rebase` for the old way): legacy -0.0552 / -0.0522, patient -0.0424 / -0.0417, barriers -0.0486. Calibration error 0.0013-0.0023 U per trade: policy gaps are inside it; every policy and all 15 brackets are negative in both. Patient's toxic-flow branch is not simulated.
- Decisions for the user: (a) read `SWARM_*` funnel counters after the next run to see whether the harvester starves on the swarm quorum; (b) entry quality is the open problem (5 of 13 entries hit the 0.15 % stop even when held): next lever is an adverse-selection filter at the fill / markout, not the exit; (c) raise `max_spread_bps` for ADA or drop ADA from the rotation; (d) the replay can be re-run for later nights with `fetch` (new directory each time).
- Rollback: `micro_harvest_macro_ofi=0.55`; revert the commit for `drift_exempt`.

## Handoff — 2026-11-10 (c) (adverse-selection filter: SCOPE only; no engine change)
- Doc: docs/harness/plan_micro_adverse_selection.md. Evidence: the 13 logged harvest fills + their Binance public aggTrades. No pre-fill feature separates stop-outs from TPs (best AUC 0.74, p 0.18); the with-trade move at +60 s does (AUC 0.93, p 0.01) but is known only after the damage; a 30 s / -4 bps cut looks good in-sample (-0.049 -> -0.025 U, 4 cuts) and is curve-fitting.
- Existing: `tox_guard` (VPIN > 0.65, tape / own-fill markouts) held 9 of 55 attempts; `early_invalid_due` returns None for SCALP, so the harvest lane has no post-fill guard. To reach the 67.6 % break-even from a 46-50 % hit rate a filter needs AUC ~0.75 to keep 41-52 % of fills (0.65 keeps 13-23 %).
- Fix in the same commit: `tools/replay_overnight_exits.py` rebases each entry by the venue basis (see the CORRECTED replay line above); 15 tests.
- Decisions for the user: (a) download ~60-600 MB of Binance public aggTrades for the Phase 2 mainnet-tape study; (b) Phase 1 logging change (no behaviour change); (c) whether a negative Phase 2 result retires the harvester or sends it to a payoff redesign; (d) run `tools/record_market.py --light` overnight (public WS) for depth-based features.
