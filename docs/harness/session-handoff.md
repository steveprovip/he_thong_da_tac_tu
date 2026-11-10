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

## Handoff 6C — 2026-10-24 (P0b: wiring + D1/D4/D5/D7)

### 1. Mục tiêu (Goal)
Commit tách hai lượt (docs, rồi core); nối bộ khóa bất biến vào `Engine.__init__` và parser của `core.runtime`; thực thi các quyết định D1 (chặn cứng ở mainnet), D4 (giữ JEV FAST, gắn nhãn mọi lệnh để so P&L), D5 (sẵn sàng ghi băng L2), D7 (sandbox dùng template). DoD: pytest ≥ 1627 passed, 0 failed, không hồi quy; chỉ hướng dẫn chạy DEMO/PAPER.

### 2. Ràng buộc (Constraints)
- Không mở kết nối tiền thật, không khởi động hay restart engine, không chạy recorder thay người dùng.
- Không stage `third_party/` hay file nháp. Giữ 76.8 % / 70 % / Post-Only GTX và hành vi JEV FAST hiện tại (D4).
- Commit P0b chờ người dùng duyệt.

### 3. Trạng thái thực tế (Real State)
Nhánh `docs/h2-regime-sandbox-arch` @ 2398e92 (commit `7aea842` docs(arch), `2398e92` feat(core)). P0b chưa commit:
- Sửa: `core/engine.py`, `core/runtime.py`, `core/invariants.py` (docstring), `tools/ledger_audit.py`, `tests/test_invariants_lock.py` (+13 test).
- Sửa docs: `docs/architecture/{README.md, 03_..., 04_..., sandbox_suite_v0.yaml}`, `docs/harness/{progress.md, feature_list.json}`, file này.
- Mới: `scripts/GHI_BANG_L2.bat`.
- Giữ nguyên, không thuộc phiên này: `AGENTS.md`, `CLAUDE.md`, `tests/test_upgrade_2026_10.py`, `.mcp.json`, `.engram/`, `prompts/*`, `third_party/*`.

### 4. Bằng chứng (Evidence)
- `python -m pytest -q tests/test_invariants_lock.py` trước khi nối: 13 failed, 19 passed; sau khi nối: 32 passed.
- `python -m pytest -q` (toàn bộ, `--junitxml`) → 1640 passed, 0 failed, 0 errors, 125.2 s.
- Chạy lại `tests/test_upgrade_2026_10.py`, `tests/test_invariants_lock.py`, `tests/test_research_instrumentation.py` sau khi có `.bat` mới → 69 passed.
- `python tools/engine_replay.py --source agg ... SOLUSDT 2026-09-08T00:00..06:00 --warmup-min 60` → 93,003 msgs, 0 vị thế; `summary`, `positions`, `hourly_pnl_u` giống hệt lần chạy trên 91ad6ff.
- `python tools/record_market.py --help` và `python -m core.engine --help` chạy được (không mở mạng). `python docs/architecture/tools/check_arch_docs.py` → OK: 0 errors.

### 5. Bước tiếp theo (Next Steps)
1. Người dùng tự khởi động lại bằng `run.bat demo` (DEMO Binance testnet; không có key thì MOCK trên giá live) để engine nạp P0b, rồi theo dõi Web UI tại http://127.0.0.1:8765.
2. Người dùng chạy `scripts\GHI_BANG_L2.bat` ở cửa sổ riêng để tích lũy ≥ 14 ngày băng L2.
3. So P&L theo đường đi: `python tools/ledger_audit.py --json data/ledger_audit.json`, xem `by_path` và `by_jev_lift`. Dòng trước P0b hiện là UNKNOWN.
4. Commit P0b sau khi duyệt (commit thứ ba: core + tests + tools + scripts + docs).
5. P1 (RegimeEpisode) và P2 (surface + `--sig-set`) theo doc 04.

### 6. Quyết định & Bài học (Decisions & Lessons)
- Profile sai chỉ bị **từ chối khi live đã arm** (`mode == 'live'` và `allow_live`); demo/replay chỉ phát `INVARIANT WARN`. Lý do: test và nghiên cứu dùng `super_cross='on'`/`super_edge` ở demo, và `test_executor_refuses_live_profile_and_mainnet_venue` dựng Engine live chưa arm từ profile demo. Quorum/token sai thì từ chối ở mọi mode.
- `LEDGER_SCHEMA` giữ 2: trường mới là additive và có test ghim `schema == 2`. `jev_lift = None` khi không biết consensus (UNKNOWN, không âm thầm là False).
- Launcher `.bat` ghi với CRLF như các file `.bat` khác; luật layout đòi `%~dp0..`.
- Bằng chứng replay yếu cho đường giao dịch (cửa sổ đó 0 vị thế). Hành vi lệnh được bảo vệ bởi 1640 test.

## Handoff 6C — 2026-10-24 (d) DEMO ACTIVE

### 1. Mục tiêu (Goal)
Đưa DEMO thoát "ngủ đông": micro lane SOL/DOGE và chop scalper vào lệnh Post-Only GTX liên tục để thu dữ liệu khớp lệnh thật; ngưỡng điểm linh hoạt theo chế độ; Kelly cửa sổ trượt 20 lệnh; chạy pytest và A/B replay; báo cáo trung thực kỳ vọng ròng. DoD: pytest 0 failed, A/B được ghi sổ, hướng dẫn `run.bat demo`.

### 2. Ràng buộc (Constraints)
- Quyết định chủ sở hữu (AskUserQuestion): giữ khóa I1/I3 (0.768 / 0.70) nên ngưỡng 0.70 / 0.65 được thay bằng sàn 0.768; chỉ bật Micro lane SOL/DOGE + Chop scalper; BTC vẫn observe_only; burst lane vẫn shadow.
- Không chạm profile live, không khởi động/restart engine, không gửi lệnh, không commit (chưa được yêu cầu). Giữ nguyên P0b chưa commit của phiên trước.

### 3. Trạng thái thực tế (Real State)
Branch `feat/demo-active-micro-lane` tách từ `docs/h2-regime-sandbox-arch` @ 2398e92, chưa commit (nhánh gốc không đổi).
- Sửa: `core/engine.py` (Profile scalp_min_score / kelly_window / chop_router_scalp / scalp_only, chop_scalp kiểm phía + điểm, stage SCALP_ONLY, nhãn radar, khối demo 10-24), `core/autonomy.py`, `core/exec_funnel.py`, `tests/conftest.py`, `tests/test_execution_funnel_feasibility.py`, `tests/test_chop_deadlock_resolution.py`, `tests/test_trend_activation_and_timing.py`, `docs/harness/{progress.md, feature_list.json}`, file này, `docs/research_upgrade/{trial_log.jsonl, experiment_register.md}` (AB-44).
- Mới: `tests/test_demo_active_2026_10_24.py` (8 test), `docs/research_upgrade/snapshot_2026-10-24_demo_active/` (ab_agg.json, rows_B_agg.json, rows_A_l2.json, rows_B_l2.json, ab_driver.py).
- `core/engine.py` chứa cả diff P0b chưa commit lẫn diff phiên này: commit phải tách hunk.

### 4. Bằng chứng (Evidence)
- Baseline `python -m pytest -q` (junitxml) trước khi sửa: 1640 passed, 0 failed, 142.6 s.
- Sau khi sửa: 1646 passed, 2 failed (hai assertion khóa giá trị demo cũ `mr_bracket == 'vwap'`, `micro_standalone is False`, verdict: thay đổi có chủ đích, đã cập nhật); lần cuối 1648 passed, 0 failed, 0 errors, 129.1 s.
- `inv.check_profile(PROFILES['demo'])` = [] (entry GTX, không IOC); `python -m core.engine --help` chạy được.
- `python tools/ab_bench.py micro --source agg ... --symbols SOLUSDT` 12 chunk 6 h (2026-09-05/06/08), A = snapshot code trước thay đổi: A 0 vị thế / 72 h; B 325 vị thế (108/ngày), -6.71 bps/lệnh CI95 [-7.87, -5.57], net -7.96 U; paired -0.1105 U/h CI95 [-0.138, -0.083] → `B WORSE (significant after deflation)`. 325/325 là SCALP, gross -1.78 bps trước phí, phí 4.92 bps.
- L2 thật SOL 09-08 06:00-09:20: A 0; B 17 SCALP, -8.97 bps CI95 [-16.1, -2.8] (mẫu 3 h, chỉ cùng dấu).

### 5. Bước tiếp theo (Next Steps)
1. Người dùng tự chạy `run.bat demo`, xem Web UI http://127.0.0.1:8765: radar SOL/DOGE hết OBSERVER, sự kiện `CHOP SCALP ...`, khi router CHOP có nhãn `SCALP ONLY (router CHOP)`; daily kill 5 U/ngày vẫn chặn.
2. Sau 1-3 ngày: `python tools/ledger_audit.py --json data/ledger_audit.json` (by_path, by_reason) và `data/engine_orders.jsonl` (FILLED / UNFILLED theo path SCALP) để đo fill rate và kết quả thật của SCALP trên DEMO.
3. Hoàn nguyên nếu không muốn equity ảo đi xuống: demo `micro_standalone=False`, bỏ khối 10-24 (xem progress.md 10-24 (d) Rollback).
4. Chạy `scripts\GHI_BANG_L2.bat` để có ≥ 14 ngày băng L2 trước mọi nghiên cứu vi cấu trúc mới.
5. Commit sau khi duyệt, tách hunk khỏi P0b.

### 6. Quyết định & Bài học (Decisions & Lessons)
- Ngưỡng 0.70 / 0.65 nằm dưới quorum token VESKA 0.768: hạ sẽ phá I1 và Engine.__init__ từ chối, nên sàn đặt đúng 0.768.
- chop_scalp cũ thay phía bằng dấu OFI nhưng giữ score của phía kia khi xin token; nay phải trùng phía swarm và score ≥ 0.768.
- MR 'vwap' bất khả thi về cấu trúc (net RR ≤ 0.875 < 1.30); demo dùng 'fixed' (RR 1.39) để lựa chọn "bỏ OBSERVER cho MR" có hiệu lực.
- Kelly âm chưa bao giờ chặn lệnh: kelly_qty trả None và size về parity / floor / tối thiểu sàn.
- Gross âm trước phí (-1.78 bps) nên không chỉnh TP/SL/BE nào làm net dương; kích hoạt chỉ mua dữ liệu, không mua lợi nhuận.
- Lỗi đã gặp: escape đường dẫn Windows bằng sed trong Git Bash hỏng, arm A âm thầm chạy code mới (A trùng B); dựng JSON bằng `json.dumps` thì đúng.

## Handoff 6C — 2026-11-06 (autoresearch + SMC)

### 1. Mục tiêu (Goal)
Hiện thực vòng autoresearch kiểu Karpathy/ATLAS (`scripts/autoresearch_agent_swarm.py`: 8 forecaster, 400 futures bootstrap, pinball walk-forward +15 phút, đột biến agent tệ nhất với git commit/rollback) và bộ dò SMC (OB, FVG, sweep, BOS/CHoCH) nối vào pipeline đặc trưng NORO/TIDAL; giữ RUNE veto, AuthToken HMAC của VESKA và I1-I8. DoD: pytest 0 failed, chạy thật trên dữ liệu L2/klines cục bộ, báo cáo trung thực cả holdout.

### 2. Ràng buộc (Constraints)
- Nghiên cứu offline: không khởi động/restart engine, không gửi lệnh, không ghi `config/live_tunables.json`, không đổi Profile, không commit (chưa được yêu cầu), không push.
- Không đổi WEIGHTS (khóa SHA), ngưỡng 0.768 / 0.70, RUNE veto, TokenAuthority. Trọng số học được chỉ đi qua `Autonomy.demote` (chỉ veto).
- Giữ nguyên 45 file chưa commit của chủ sở hữu (từ nhánh `feat/demo-active-micro-lane`).

### 3. Trạng thái thực tế (Real State)
Branch `feat/janas-autoresearch-smc` tách từ `feat/demo-active-micro-lane` @ 2398e92, chưa commit (nhánh gốc không đổi).
- Mới: `scripts/autoresearch_agent_swarm.py`, `core/smc.py`, `research/smc_backtest.py`, `tests/test_autoresearch_swarm.py` (12 test), `tests/test_smc.py` (6 test).
- Sửa: `core/real_alpha_engine.py` (+6 trường `Features.smc_*`, `AlphaEngine.smc`, `on_bar` cập nhật SMC), `core/groktopus_swarm.py` (`smc_noro`, `smc_tidal`, mặc định 0.0), `docs/harness/{progress.md, feature_list.json}`, file này.
- Đầu ra (git-ignored): `data/autoresearch_report_{all4,btc}.json`, `data/autoresearch_ledger_*.json`, `data/autoresearch_tunables_proposal_*.json`, `data/autoresearch_agents_*.json`, `data/smc_backtest_{BTC,ETH,SOL}USDT.json`, cache `data/npz/autoresearch_*.npz`.

### 4. Bằng chứng (Evidence)
- Baseline `python -m pytest -q` (junitxml) trước khi sửa: 1651 passed, 0 failed, 124.8 s. Sau khi sửa: 1669 passed, 0 failed, 0 errors, 126.9 s (+18 test mới).
- `python scripts/autoresearch_agent_swarm.py --data "data/l2/*/*.jsonl" --rounds 40` (dry run, không git): 4 symbol x 2.7 h liên tục (2026-09-08), 666 train / 286 holdout điểm; giữ 21/40 đột biến; pinball trung bình train 4.759 -> 4.493 bps nhưng holdout 6.224 -> 6.480 bps (xấu đi); baseline không tín hiệu có holdout 5.925, thắng mọi agent. Riêng BTC: train 2.864 -> 2.664, holdout 4.210 -> 4.416, baseline 4.261. Tổng 7.5 s.
- `python -m research.smc_backtest --symbol BTCUSDT --last 6`: 263,520 nến 1m (2026-04..09) trong 3.44 s; holdout |gross| <= 0.97 bps ở mọi sự kiện và khung 5/15/60 so với chi phí 8.5 bps. ETH <= 2.56 bps (BOS h60, in-sample cũng -2.33), SOL <= 3.98 bps (sweep h60, in-sample chỉ +0.07, không ổn định).
- Test khóa: dự báo nhân quả (đổi dữ liệu sau t không đổi quantile), CRN tất định, rollback bit-exact + `git diff --quiet HEAD`, commit đúng 1 file (file staged khác còn nguyên), proposal qua `core.tunables.validate` 0 rejected, `check_consensus(ConsensusEngine(trend_floor=.768)) == []`, RUNE veto và TokenAuthority vẫn từ chối; SMC không lookahead (prefix invariance); swarm bit-identical khi hệ số SMC = 0.

### 5. Bước tiếp theo (Next Steps)
1. Không publish `data/autoresearch_tunables_proposal_*.json`: holdout cho thấy overfit. Khi muốn chạy với git: `git worktree add ../he_thong_autoresearch -b autoresearch/btcusdt` rồi thêm `--git`.
2. Cần >= 14 ngày băng L2 (`scripts\GHI_BANG_L2.bat`; recorder đang khóa ngân sách 500 MB) trước khi đọc lại vòng autoresearch, hoặc thêm loader klines 1m (taker-buy imbalance thay OBI) để có hàng tháng dữ liệu.
3. SMC: giữ `smc_noro = smc_tidal = 0`; chỉ bật khi một biến thể vượt 8.5 bps với LCB95 > 0 trên holdout thời gian.
4. Agent-Reach (sentiment từ internet): chưa làm. Đường an toàn duy nhất: verdict dạng số qua `phan_tich.slow_brain_contract` -> `core.regime_permit` (chỉ chặn); không đưa text cào được vào prompt.
5. Commit sau khi chủ sở hữu duyệt, tách khỏi diff demo-active chưa commit.

### 6. Quyết định & Bài học (Decisions & Lessons)
- Không dùng "weights inside the vote" (autonomy.py: bản 10/01-07 làm lệnh to gấp 3); trọng số survivor -> `swarm_weights` (NORO/LUMEN/TIDAL, clip [0, 1.8]) -> `Autonomy.demote` chỉ veto.
- 8 agent autoresearch là forecaster theo họ (3 NORO, 3 LUMEN, 2 TIDAL), không phải 8 vai Groktopus; gate agents (ZEPHR, RUNE, OKAPI, MARIN, VESKA) không bị re-weight (I4).
- Không `git reset --hard` (cây làm việc có việc dở của chủ sở hữu); `git restore` với core.autocrlf=true ghi ra CRLF, nên rollback tự ghi lại byte cũ rồi kiểm `git diff --quiet HEAD`.
- ATLAS dùng Kraken BTC/USD 300 mức; repo chỉ có bookTicker + 10 mức của Binance nên dùng top-of-book. L2 có khoảng hở (0910 còn 3 đoạn < 45 phút), chỉ 0908 đủ warm-up 30 phút + horizon 15 phút.
- Khẳng định lại review 2026-11-05: mẫu ATLAS overfit khi không có holdout và baseline.

## Handoff 6C — 2026-11-06b (autoresearch net-EV trên klines 1m)

### 1. Mục tiêu (Goal)
Brief 2026-11-06 thrust 2: kỳ vọng ròng dương đã kiểm chứng (> 0 bps sau chi phí 8.5 bps), Sharpe holdout >= 1.8, vượt baseline không tín hiệu có ý nghĩa thống kê, hết đói lệnh. DoD: đạt cả ba tiêu chí trên holdout thời gian, hoặc ghi lại giới hạn kỹ thuật có bằng chứng.

### 2. Ràng buộc (Constraints)
- Offline: không khởi động engine, không gửi lệnh, không sửa Profile hay `config/live_tunables.json`, không commit, không push.
- Giữ I1-I8: 0.768 / 0.70, RUNE veto, AuthToken của VESKA, entry GTX (I5). Smart IOC chỉ đánh giá trong replay, không đưa vào đường lệnh.
- Holdout chỉ được chấm MỘT lần, sau vòng lặp. Lặp "đến khi holdout đạt" sẽ biến holdout thành train.

### 3. Trạng thái thực tế (Real State)
Branch `feat/janas-autoresearch-smc` @ 2398e92, chưa commit (tiếp nối entry 2026-11-06).
- Sửa: `scripts/autoresearch_agent_swarm.py` (+ `--source klines`: `TradeAgent`, `TRADE_BOUNDS`, `make_bars`/`load_klines`, `trade_eval` với 4 mô hình khớp maker/ideal/ioc/dynamic, `portfolio`, `perf`, `run_trade`; `mutate` nhận `bounds`), `tests/test_autoresearch_swarm.py` (+5 test, tổng 17).
- Đầu ra git-ignored: `data/autoresearch_trade_report_{maker,ideal,dynamic,ioc}.json`, `data/autoresearch_trade_run_*.log`, `data/autoresearch_trade_agents_*.json`, `data/autoresearch_trade_proposal_*.json`.

### 4. Bằng chứng (Evidence)
- `python -m pytest -q`: 1674 passed, 0 failed, 123.1 s (trước đó 1669; +5 test).
- Dữ liệu: klines 1m BTC/SOL/DOGE/BNB 2024-12 -> 2026-09 (22 tháng). Train 2024-12-03 -> 2026-03-15 (467 ngày), embargo 240 phút, holdout -> 2026-10-01 (200 ngày).
- Chi phí: maker 8.5 bps (COST_ALL_IN_BPS), IOC 13.0 bps (2 x (taker 5 + slippage 1.5)). Lệnh maker chỉ khớp khi nến kế tiếp xuyên qua giá (trade-through).
- `python scripts/autoresearch_agent_swarm.py --source klines --fill {maker,ideal,dynamic,ioc} --rounds 300`: 4 pivot = 1203 cấu hình, 9-10 s mỗi run. Cả 4 run: `any_meets_criteria = False`. DSR tốt nhất (train, phương sai null 1/T) = 0.061 (ioc NORO-60), so với ngưỡng 0.95.
- Portfolio holdout theo trọng số survivor: maker Sharpe 0.74 (+0.42 bps/ngày), ideal -1.67, dynamic -6.15, ioc -2.50.
- Cùng một agent, so sánh mô hình khớp (ideal-run TIDAL-60, 1698 lệnh holdout): ideal -11.47 bps/lệnh, maker -13.13 (adverse selection -1.7), dynamic -14.86, IOC -19.82.
- Holdout "dương" chỉ có ở agent < 60 lệnh (ví dụ 6 lệnh, Sharpe 2.62) hoặc agent có train âm: không phải bằng chứng.

### 5. Bước tiếp theo (Next Steps)
1. Không publish proposal nào (`data/autoresearch_trade_proposal_*.json`).
2. OBI/CVD/top-5 depth dưới 1 phút: dữ liệu hiện có không kiểm được (L2 khoảng 2.7 h liên tục mỗi symbol, aggTrades 2 ngày). Cần >= 14 ngày L2 (recorder cần `--override-budget` kèm giả thuyết) rồi mới chạy lại `--source l2`.
3. Giả thuyết duy nhất từng dương OOS trong repo: AA3 ENS 1d LS (SR +0.21) và funding carry XV, đều còn xa Sharpe 1.8. Nếu tiếp tục: đăng ký trước một giả thuyết, chạy một lần, giữ holdout.
4. Không đổi I5 (GTX): IOC và dynamic làm kỳ vọng tệ hơn trên replay.

### 6. Quyết định & Bài học (Decisions & Lessons)
- Không chạy "đến khi holdout đạt": với 1203 lần thử, vài agent ít lệnh sẽ có Sharpe holdout cao do ngẫu nhiên.
- PSR trên chuỗi ngày thưa bị skew dương thổi phồng (2 lệnh vẫn ra PSR 0.98), nên tiêu chí đòi >= 60 lệnh holdout.
- DSR dùng phương sai thực nghiệm bị thổi phồng khi các lần thử khác nhau về tần suất giao dịch (phí làm SR trải từ -26 đến +1, E[max SR] nhiễu 13.9-18.3): tiêu chí dùng phương sai null 1/T, báo cả hai.
- Đói lệnh là hệ quả đúng khi không có edge: optimizer tự đẩy hurdle lên (maker NORO-240 có 0 lệnh holdout). "Zero fills beats negative fills" (core/regime_permit.py).

## Handoff 6C — 2026-11-07 (integration from F:\factory-quant-opt: tick loader, regime classifier, HRP sleeves, nightly audit)

### 1. Goal
Bring the pieces reported as verified in `F:\factory-quant-opt` into this checkout:
- the PyArrow tick loader, with its run time re-measured here;
- a 4-state regime classifier wired into `core/regime_permit.py` and `core/market_risk.py` that can only tighten;
- HRP between TIDAL-60 maker and XV funding carry, with every claimed figure re-derived;
- a read-only nightly audit.

Done means code and tests for each piece, zero regressions against a baseline measured first, and the results recorded here and in progress.md.

### 2. Constraints
- Offline only: no engine start or restart, no order, no live mode, no capital or leverage change, no Profile or `config/live_tunables.json` edit.
- No commit (not requested), no push, no OS scheduled task (persistent configuration needs the owner).
- `WEIGHTS`, quorum 0.768, RUNE veto 0.70 and the VESKA HMAC `TokenAuthority` are unchanged. `tests/test_regime_classifier.py` asserts `check_consensus` and `check_token_authority` both return `[]`.
- The owner's uncommitted work is preserved. The only tracked file edited is `core/regime_permit.py`, which was clean at HEAD.
- Pytest temp, basetemp and cache stayed on F:\gt_scratch. C: had about 29 GB free and was not written by tests.

### 3. Real state
Branch `feat/janas-autoresearch-smc` @ 2398e92, uncommitted.

New code:
- `tools/replay_tick_alpha.py`: byte-identical port.
- `core/market_risk.py`: port, plus `escalate` and an `assess(regime=)` parameter.
- `core/portfolio_hrp.py`: port, plus `hrp_allocate`.
- `core/regime_classifier.py`.
- `research/hrp_alpha_sleeves.py`.
- `tools/nightly_audit.py`.

New tests: `tests/test_replay_tick_alpha.py` (port, 9), `tests/test_regime_classifier.py` (21), `tests/test_portfolio_hrp.py` (10), `tests/test_nightly_audit.py` (9).

Edited: `core/regime_permit.py` (opt-in `market_regime` and `style`).

Docs:
- `docs/research_upgrade/snapshot_2026-11-07_hrp_sleeves/`: README, `factory_maker_agents.json`, `hrp_sleeves.json`.
- `docs/research_upgrade/nightly_reviews/`: the real report for 2026-11-06, plus backfill examples for 2026-10-09 (slippage) and 2026-09-09 (regime).

Nothing in the engine or runtime calls the classifier, the regime permit path or `escalate` yet. Runtime behaviour after a restart is the same as before this session.

Not ported from the factory worktree (follow-ups):
- the GARCH RUNE veto inside `core/engine.py` and `core/micro_signals.py`;
- the HRP multipliers in `core/autonomy.py`;
- the web UI analytics;
- `tools/download_public_l2.py` and its `.gitignore` lines (`data/ticks/`, `data/public_depth/`);
- the factory fixes to `scripts/autoresearch_agent_swarm.py` (`t_hi` exit cap, OOS DSR gates).

### 4. Evidence
- Baseline (junitxml) before any edit: 1674 passed, 0 failed, 124.6 s. Final run on frozen code: 1723 passed, 0 failed, 0 errors, 0 skipped, 126.6 s. All 1674 baseline tests are present and passing, plus 49 new.
- Tick replay, data `--data-dir F:\factory-quant-opt\data\ticks`:
  - 24,835,929 ticks over three runs, 17.91 / 17.87 / 18.00 s in-tool (load 7.18-7.38 s), 18.30-18.45 s wall.
  - The claimed 17.685 s reproduces within about 2 %, probably with a warm OS cache.
  - The ledger is identical to the factory JSON: 11 rows, all net-negative.
- `fast_hands_permit` with `market_regime=None` is identical to HEAD on 378,000 input combinations (NaN/inf/bool included). Carry is unchanged in every regime.
- The `hrp_proposal` port matches the factory output exactly on 285 random cases. Two-series HRP equals inverse variance to 1e-12.
- Re-derived claims. All three reproduce as arithmetic on this checkout's code and data; none is evidence of edge.
  - TIDAL-60 maker, holdout 2026-03-15..09-30: 316 trades, +8.3347 bps, Sharpe 1.728, 1.58/day.
    - Train: 3,849 trades at -6.31 bps.
    - Selected after 1,406 trials, OOS DSR 0.0027.
    - The config is coef -0.15 with a 44-bar horizon, so it is contrarian.
    - In 2025-01-01..2026-03-14 it lost 16.38 bps/day (max DD 7,287 bps).
  - XV HEDGE|H28|k2: 153 rounds, +14.59 bps per 28-day round, CI95 by day +9.17..+19.92, 1.90 %/yr on notional. This uses exits up to 2026-10-09, the original data end. The refreshed cache gives 165 / +16.01, and HO2 has 1 round. The unit is per 28-day round and cannot be compared with TIDAL's per-trade bps.
  - -1.51 bps/day: -1.5148, Sharpe -1.555. This is the 8-agent survivor-weighted portfolio. Contributions: LUMEN-240 -1.897, TIDAL-240 -0.583, NORO-60 +0.601, NORO-15 +0.310 (2 trades), TIDAL-60 +0.096.
- HRP between the sleeves:
  - Covariance from 2025-01-01..2026-03-14 only (438 days, correlation -0.08).
  - Weights: TIDAL-60 0.05 %, XV 99.95 %. The monthly walk-forward gives the same.
  - 200-day evaluation: HRP +0.611 bps/day, Sharpe 5.07, max DD 8.6 bps, about the same as XV alone (+0.609). TIDAL alone +3.29 (max DD 194); 50/50 +1.95; meta-portfolio -1.515.
  - XV daily marks: lag-1 autocorrelation -0.12; mark-to-market telescoping error 1e-13 bps. The study is deterministic on rerun.
- Nightly audit:
  - Window 2026-11-06 is UNKNOWN for both sections: the ledger has nothing after 2026-10-24 12:41, and `data/recordings/` does not exist.
  - Backfill 2026-10-09: 15 DEMO round trips. All-in +8.20 bps (n 14, CI95 +5.94..+11.57) vs 8.5. Fees +4.14 vs 7.0; slippage +4.20 vs 1.5 (exit +3.19). n < 30, so no inference; DEMO is the testnet book.
- Regime classifier v0 backfill, public aggTrades 2026-09-01..09-14, BTC/SOL/DOGE/BNB, 14 days, 80,537 points every 60 s: accuracy 0.535 vs majority baseline 0.878 (beats it on 0/14 days). Labels are 87.8% SIDEWAYS and 5.4% JUMPY, but JUMPY is predicted 38.8% of the time (precision 0.068, recall 0.485); the cause is the 4.5-sigma tail memory. As a guard: protection recall 0.559 on 4,443 JUMPY/CRASH points vs false restriction 0.385 on 76,141 calm points. CRASH: 29/29 realized 150 bps / 5-min moves caught (max latency 0 s), 0 false episodes. Code SHA-256 nightly_audit 0d79b014c207, regime_classifier c7ce421e0fc5. Note: these dates were also used to develop v0, so this is not a time-separated test.

### 5. Next steps
1. Owner decision: register the nightly job. Nothing was created in this entry (registered later on 2026-11-07, see the (b) entry). The machine is UTC+7, so 00:00 UTC is 07:00 local; a 07:05 local run allows recorder flushes: `schtasks /Create /TN "Groktopus nightly audit" /SC DAILY /ST 07:05 /TR "C:\Users\Admin\AppData\Local\Programs\Python\Python310\python.exe F:\he_thong_da_tac_tu_2\tools\nightly_audit.py"`.
2. Run `scripts\GHI_BANG_L2.bat`. Without recordings, the regime section of every nightly report stays UNKNOWN.
3. Keep the classifier out of order flow. A v1 (for example a tail rule needing repeated tails, or a shorter tail memory) must be pre-registered and scored on dates not used here.
4. Record the stop/TP trigger as `exit_ref` on exchange-side exits. Today their exit slippage is UNKNOWN by construction.
5. Do not allocate capital from the HRP result. TIDAL-60 is a selection artefact, and the XV carry's +2.2 %/yr at L = 2 sits below the risk-free yield with unmodelled leg risk.
6. Commit only after owner review, split from the owner's uncommitted diff, with no AI trailer.

### 6. Decisions & Lessons
- The regime check sits after CARRY, so carry keeps G > 3C in every regime (CRASH means carry only). Once a regime is given, `style` must be stated; None fails closed.
- `policy_for` reads only the state name. A custom policy object cannot loosen a state, and an unknown name maps to UNKNOWN.
- The GARCH long-run anchor is its 30-sample warmup floored at 4 bps², which binds for per-second BTC and DOGE returns. The classifier therefore takes its vol ratio against a 6-hour EWMA.
- JUMPY over-prediction comes from the 4.5-sigma tail memory, not from the ratio, and warmup length does not change it. It was not retuned on the days that revealed it.
- I changed audit code twice while a backfill was running and restarted the batch both times. Lesson: freeze code and record its SHA-256 before batch measurements.
- The first CRASH latency metric timed detection from the start of the 5-minute move, which inflated it to about 300 s. It is now measured from the first second the move is visible to a causal rule.
- With two sleeves, "HRP" is just inverse variance. The question to answer is the economic purpose of each sleeve, not the allocator.

## Handoff 6C — 2026-11-07 (b) (Pillar 1: funding-carry lane BN <-> HL, shadow/paper)

### 1. Goal
The owner's 4-pillar request (relayed by the parent session), first steps only: inspect `core/engine.py`, `core/funding_carry.py` and `tools/nightly_audit.py`, verify the test baseline, and start Pillar 1. Pillar 1 is a delta-neutral carry lane with normalized funding, real-time net APR, a maker GTX leg plus an IOC hedge, liquidation-buffer guards and funding tracking. Resolve the 18-35 % APR claim before anything sizes on it.

### 2. Constraints
- Built offline: paper venues and shadow intents only. No real order, no live mode, no capital or leverage change, no keys. Hyperliquid account, wallet and key setup is the owner's job.
- Quorum 0.768, RUNE veto 0.70 and the VESKA HMAC token are untouched; the lane has its own kill switch before every order.
- Hurdle 8.5 bps: an edge at or below it is OBSERVER.
- Not touched: `core/web_ui_server.py` and `web_ui/*`, which another writer edited on 2026-11-07 (11:57 and 16:24 local).
- No commit. No scheduled task: the parent relayed the owner's words approving it, but a relayed approval does not count; the owner must confirm in this session.

### 3. Real state
New: `research/carry_lane.py`, `core/venues/paper.py`, `tests/test_carry_lane.py` (17). Edited: `core/funding_executor.py` (the `quotes_from` / `fetch_quotes` 4 h interval fix). Nothing in the engine calls the lane; `Engine.funding_loop` still runs `ShadowCarryBook` with the RFC thresholds, which were never replayed.

### 4. Evidence
- Baseline before edits: 1723 passed, 0 failed, 124.1 s. Full `python -m pytest -q` 1740 passed, 0 failed, 0 errors, 0 skipped, 126.1 s (1723 baseline + 17 new).
- APR on 2026-01..09 funding:
  - The median instantaneous |BN - HL| spread was 5.5 % APR (p75 10.5, p90 16.5); 8.3 % of coin-8 h windows reached 18 %.
  - The RFC threshold of 2.5 bps/8 h equals 27.4 % APR.
  - XV H28|k2 holdout entries: signal median 5.4 % APR, realized gross funding 4.2 %, realized net 1.90 %/yr.
  - Verdict: 18-35 % is a transient spread annualized as if it lasts a year. Use 1.9 %/yr on notional.
- 4 h bug: for ENA, HYPE and TAO, the old reading flipped the short venue on 37.0 / 43.9 / 37.2 % of 1,637 prints and erred by a median 5.4 APR points. The replay summed actual prints and was not affected.
- Replay parity: `entry_signal`, in the replay's loop, reproduces `tools/funding_spread_replay.simulate` exactly (entry times and short venue) for BTC, TAO and WLD.
- Executor on paper venues:
  - a full fill is hedged and the pair is delta-neutral;
  - partial fills are hedged per increment, then the rest is cancelled at the timeout;
  - a starved hedge escalates 3 -> 6 -> 12 -> 12 bps, then is unwound flat;
  - unhedgeable plus unwindable gives BROKEN, which halts, and the next run is REFUSED;
  - the kill switch refuses before any order, and a trip after the maker fill refuses the hedge but allows the unwind.

### 5. Next steps (gates)
1. Done: Scheduled task registered 2026-11-07 on the owner's direct approval in this session: `Groktopus nightly audit`, daily 07:05 local (UTC+7 = 00:05 UTC), runs `python.exe F:\he_thong_da_tac_tu_2\tools\nightly_audit.py` as Admin, logon mode Interactive only (no run while logged off). Test run: last result 0, nightly_2026-11-06 rewritten at 11:04:02 UTC. Remove with `schtasks /Delete /TN "Groktopus nightly audit" /F`.
2. Pillar 1 to shadow: replace the RFC thresholds in `Engine.funding_loop` with `carry_lane.entry_signal`, fed by 72 h of public print history. This changes shadow telemetry only; owner review.
3. Pillar 1 to paper on testnet: needs a Binance testnet key (exists) and a Hyperliquid testnet account and agent wallet (owner); keys only from `.env.local`.
4. Pillar 2: wire the classifier into the engine only after v1 is pre-registered and passes on unseen tick days. Downloading those days needs the owner's OK. JUMPY -70 % sizing and the CRASH flatten are order-path changes.
5. Pillar 3: markouts at 1 s / 5 s / 30 s already land in ledger rows (`markout_bps`); the toxicity guard is next. Pillar 4: the analytics panel looks in progress elsewhere (`web_ui/*` edited by another writer).

### 6. Decisions & Lessons
- The lane's entry rule is the replay's rule, so it trades only what was tested; the 8.5 bps hurdle never binds because 2 x 15 > 15 + 8.5.
- The lane sits in `research/`: `tests/test_venues.py` forbids `core.venues` in `core/*.py` and `live/*.py`. Moving it into trading code means changing that guard on purpose (owner decision).
- "No leg risk" cannot be promised. The executor bounds it (per-increment hedge, unwind, BROKEN halt) and measures the naked window.
- A Binance `lastFundingRate` is per that symbol's own interval. Any consumer must read `fundingInfo`; silence means 8 h, and an error means UNKNOWN.

## Handoff 6C — 2026-11-07 (c) (Pillar 3 toxicity guard, Pillar 2 regime gate wired into the engine)

### 1. Goal
The owner's request:
- Pillar 3: a VPIN toxicity guard plus 1 s / 5 s / 30 s markouts that skew or pull quotes when the 5 s markout is negative or VPIN > 0.65.
- Pillar 2: download unseen tick days (approved), tune the 4.5-sigma rule so JUMPY false alarms fall from 38.8 % to < 10 %, then wire the gate into `core/engine.py`.
- Keep `pytest -q` at 100 % and report briefly.

### 2. Constraints
- No engine start or restart, no live change (live keeps `tox_guard` and `regime_gate` 'off').
- Quorum 0.768, RUNE veto 0.70 and VESKA untouched; both gates run before any token is issued and can only refuse, deepen a quote or shrink size.
- Data stays on F:. No commit.

### 3. Real state
- New: `core/toxicity_guard.py`, `research/regime_v1_eval.py`, `tools/download_public_l2.py`, three test files, and `docs/research_upgrade/snapshot_2026-11-07_regime_v1/`.
- Edited: `core/engine.py`, `core/exec_funnel.py`, `core/micro_signals.py`, `core/trend_executor.py`, `core/regime_classifier.py`, `tools/nightly_audit.py`, `tests/conftest.py`, `tests/test_regime_classifier.py`, `.gitignore` and the experiment register (row RC-1).
- Data (git-ignored): `data/ticks/` for 2026-10-01..14.

### 4. Evidence
- RC-1, pre-registered before its single test: Oct 1-14 PASS.
  - JUMPY false alarm: v1 1.0 % vs v0 17.2 %.
  - CRASH: 18/18, 0 false.
  - Protection recall 0.085 > false restriction 0.013.
  - JUMPY recall is 4.9 % and accuracy 0.814 < majority 0.858, both recorded as limits.
- Dev finding: a stricter shared tail rule cost 5-8 of 29 crashes, so CRASH now has its own tail rule (test pinned).
- Expected quote-halt time from the VPIN rule: SOL 19.7 %, BNB 18.0 %, BTC 10.6 %, DOGE 4.4 % (Oct 1-14).
- Markout skew and pull triggers were rare on the tape (<= 2 % and <= 0.1 %, mid proxy).
- Full `python -m pytest -q`: 1780 passed, 0 failed, 0 errors, 0 skipped, 131.2 s (1740 before this request + 40 new; the only previous name not present is the JUMPY policy test renamed from 'halves' to 'cuts').

### 5. Next steps
1. The owner restarts demo (`run.bat demo`) to load both gates.
2. Then watch TOX HOLD / TOX SKEW / TOX PULL and REGIME GATE events, the outcomes TOX_HOLD / TOX_PULL / REGIME_REFUSED in `data/engine_orders.jsonl`, and the nightly report.
3. If SOL/BNB quoting pauses too often for data collection, the documented rollback is `tox_guard='shadow'`.
4. Re-score v1 on later unseen days. The regime half of the nightly audit needs `scripts\GHI_BANG_L2.bat` recordings, or tick downloads.
5. The JUMPY state is now rare; any further change needs its own pre-registration.

### 6. Decisions & Lessons
- VPIN's 0.65 halt is symmetric (owner rule) and sits beside the existing asymmetric 0.80 pause; nothing was loosened.
- Markout thresholds come from the cost model (the 3 bps adverse-selection allowance and half of it), not from fitting.
- Only AUTO entries are gated, so the operator TEST button still works. A JUMPY size below the venue minimum refuses the entry rather than rounding up.
- A float probe into an int64 `searchsorted` copies the array on every call. Precompute the cast once.

## Handoff 6C — 2026-11-07 (d) (Cockpit UI, 18 bps scalp edge floor, simulated carry book)

### 1. Goal
The owner's cockpit prompt:
- One 100vh, no-scroll, 3-column cockpit (24 / 46 / 30 %) in `web_ui/index.html` / `style.css`, with `app.js` updated.
- Directional scalps need >= 18 bps modeled edge (`EDGE TOO LOW FOR FEES`); the LUMEN/TIDAL scalping weights stay at 0.
- `research/carry_lane.py` in the demo engine loop: simulated dual-leg orders (BN post-only, HL IOC) when the spread is >= 2.5 bps/8 h and APR >= 20 %, with carry positions on the cockpit.
- Keep quorum 0.768, RUNE veto 0.70, the VESKA AuthToken and the kill switch; `pytest -q` at 100 %; check HTML/CSS/JS.

### 2. Constraints
- No engine start or restart, no live change (live: `carry_sim` False, scalper 'off').
- Carry orders are simulated on paper venues only; nothing reaches Binance or Hyperliquid. Hyperliquid account setup stays with the owner.
- No commit.

### 3. Real state
- Rewritten: `web_ui/index.html`, `web_ui/style.css`, `web_ui/app.js`. Pre-cockpit copies (they carried another writer's uncommitted 09 analytics panel, now ported): `F:/gt_scratch/web_ui_pre_cockpit_2026-11-07/`.
- Edited: `web_ui/terminal.js` (gauge radius clamp, resilient frame loop), `core/engine.py` (`scalp_min_edge_bps`, `carry_sim`, CarrySim in funding_loop, snapshot `carry_lane`), `research/carry_lane.py` (`CarrySim`), `core/venues/paper.py` (`auto_fill`), `tests/conftest.py`.
- New: `tests/test_cockpit_carry.py`, `.claude/launch.json` (preview config `cockpit-ui`).

### 4. Evidence
- Browser at 1920 x 1080: no page scroll, 0 cards overflowing, no JS exception. Contrast and other resolutions were not measured.
- The 18 bps floor would have blocked 3 of the 6 demo SCALP trades; the 3 at >= 18 bps also lost, so the edge is not calibrated.
- CarrySim uses the unvalidated RFC rule; the replay reference is 1.90 %/yr net.
- Full `python -m pytest -q`: 1790 passed, 0 failed, 0 errors, 0 skipped, 130.7 s (1780 before + 10 new; no previous test name missing). A one-line terminal.js fix made after that run (an inline comment had swallowed the OBI arc colour) was re-checked with the 87 UI-related tests (pass) and in the browser (emerald arc at 0.55 alpha).

### 5. Next steps
1. The owner restarts demo to load the floor and the simulated carry book.
2. Watch the `EDGE TOO LOW FOR FEES` events and EDGE_LOW in the funnel, plus the `CARRY SIM OPEN/CLOSE` events and the `carry_lane` telemetry.
3. The scalp bleed needs a structural fix before more scalps: the TP (~6.7 bps) sits below the 10 bps stop, and SCALP skips the ZEPHR net-RR gate. Pre-register any change.
4. Carry: replace the RFC entry rule with the replay-validated one before any capital decision.

### 6. Decisions & Lessons
- The floor reads the swarm's modeled edge, and UNKNOWN refuses (never read as 0). A floor of 0 disables it (micro-lane tests).
- The LUMEN/TIDAL weights were not touched: `WEIGHTS` is SHA-locked (I4), and the learner already holds them at 0.
- A rewrite of tracked UI files must keep what the old page showed: three characterization tests caught the dropped scanner counters, PnL strip and quant chips. Back up files that carry someone else's uncommitted edits before replacing them.
- A canvas measured before layout can be 1 px wide. Clamp every derived radius, and schedule the next frame before drawing.

## Handoff 6C — 2026-11-07 (e) (SCALP entries gated on net RR)

### 1. Goal
The owner's go-ahead ("Yes, gate SCALP"): route chop SCALP entries through the ZEPHR net-RR gate instead of exempting them.

### 2. Constraints
- Refusal-only. No engine restart, and no live change beyond the flag (live's scalper is 'off').
- Quorum 0.768, RUNE veto 0.70, VESKA and the kill switch untouched. No commit.

### 3. Real state
- Edited: `core/engine.py` (Profile `scalp_net_rr`, the `chop_scalp` RR_LOW refusal, the order-path ZEPHR check, `lane_feasibility`), `tests/conftest.py`, `tests/test_execution_funnel_feasibility.py`, and the experiment register (SCALP-RR-1).
- New: `tests/test_scalp_net_rr_gate.py`.

### 4. Evidence
- The shipped SCALP bracket (TP 0.08 % / SL 0.12 %) nets RR 0.20 (break-even win rate 83 %) < 1.30, so every 'on' scalp is refused.
- Prior: AB-44 DEMO ACTIVE worse (delta -0.111 U/h, CI95 [-0.138, -0.083]); lane replay holdout < 0 bps; 6 demo scalps net -0.055 U.
- Full `python -m pytest -q`: 1796 passed, 0 failed, 0 errors, 0 skipped, 132.6 s (1790 before + 6 new; no previous test name missing). The 10-minute event rate limit landed while that run was starting; the 6 gate tests were re-run after it (pass).

### 5. Next steps
1. The owner restarts demo to load the gate. Expect RR_LOW in the funnel and no SCALP fills.
2. While the router reads CHOP, SOL/DOGE place no micro entries (SCALP-only). Decide whether CHOP should keep muting SWARM entries.
3. Any new scalp bracket needs net RR >= 1.30 (TP >= 0.30 % at a 0.12 % stop), a pre-registration and a replay before demo.

### 6. Decisions & Lessons
- The gate reuses `bracket_plan`, the same fee model as TREND/MR, instead of a SCALP-only formula.
- Shadow rows stay ungated: they record would-be scalps, not orders.
- The refusal event comes from a static bracket, so it is rate-limited to once per 10 minutes per symbol; the funnel counts every refusal.

## Handoff 6C — 2026-11-08 (f)

### 1. Mục tiêu (Goal)
Resolve the demo CHOP execution deadlock with a high-consensus SWARM_OVERRIDE, make the scalp bracket clear net RR >= 1.30, rotate the three simulated carry slots hourly by net APR with a -3 bps adverse-basis eject, and show those states in the cockpit. Complete when tests pass and the 100vh view is checked.

### 2. Ràng buộc (Constraints)
- Worktree: `F:\he_thong_da_tac_tu_2`, branch `feat/janas-autoresearch-smc`, HEAD `2398e92`.
- Preserve all pre-existing local edits; no commit, engine restart, live change or actual order. Pytest temp files stay on `F:/gt_scratch/`; C: remained above the 20 GB floor.
- New override is demo-only. Keep 0.768 quorum, RUNE veto policy, VESKA AuthToken, drawdown switch and other entry gates intact. Carry stays on paper venues.

### 3. Trạng thái thực tế (Real State)
- Implemented in `core/engine.py`, `core/exec_funnel.py`, `research/carry_lane.py`, `core/carry_runtime.py`, and cockpit files `web_ui/index.html`, `web_ui/app.js`, `web_ui/desk.js`, `web_ui/style.css`.
- Added/updated coverage in `tests/test_swarm_override.py`, `tests/test_carry_lane.py`, `tests/test_cockpit_carry.py`, `tests/test_scalp_net_rr_gate.py`, `tests/test_continuous_active_trading.py`, `tests/test_execution_funnel_feasibility.py`, and `tests/conftest.py`.
- Appended this handoff and progress record; updated `docs/harness/feature_list.json`. Worktree still contains many unrelated/pre-existing modified and untracked files; none were cleaned or reverted.

### 4. Bằng chứng (Evidence)
- Baseline: 1795 passed, 1 failed. The existing failure was `test_runtime_profit_target_requires_wallet_reconciliation`: a daily brief tried to read absent leg fields from an open, unreconciled pair. The reporter now includes only closed, fully reconciled pairs.
- Focused suite: 104 passed. Full project suite, first command in Git Bash `./init.sh`: 1804 passed in 127.67 s. After documentation updates, `git diff --check` is clean, `feature_list.json` parses, and the strict six-section handoff validator passes.
- `init.sh` exited 1 only on its second, external toolkit command: the configured toolkit Python has no `pytest`. Running the same `tools` target with the project Python collected no tests; the directory contains only `local_docs.py` and `projects.json`.
- Browser preview used sample SSE telemetry, not a live engine: 1920x1080 viewport and document; no page scroll. Visible radar `SWARM_OVERRIDE SELL (92.7%)`, carry targets `1000PEPE/WLD/ETH`, 60-minute rotation and -3.0 bps basis stop. Temporary preview server and script were stopped/removed.
- No replay was run for the new override, scalp bracket or rotation. These are behavior checks, not evidence of positive expectancy. Existing carry replay reference is 1.90 %/yr net; the active carry entry thresholds remain unvalidated.

### 5. Bước tiếp theo (Next Steps)
1. The changes take effect after the owner's next demo restart; this session did not restart the engine.
2. Restore pytest or repair test collection in `F:/AI_Agent_Toolkit` before expecting the second `init.sh` step to pass.
3. Pre-register and run a time-separated replay before claiming the override, 0.32 % scalp TP or carry rotation improves net returns.

### 6. Quyết định & Bài học (Decisions & Lessons)
- The override skips only the KER lock. It requires raw consensus >= 0.85, eight unique weighted votes, directional agreement from ZEPHR/RUNE/OKAPI/MARIN/VESKA, the normal quorum, and then the existing tokenized execution. It uses GTX TP 0.50 % and SL 0.18 % (capped at 0.25 %); live stays disabled.
- `bracket_plan` uses the repository's route-specific fees (maker TP versus taker stop), yielding RR 1.40 at TP 0.32 % / SL 0.12 %. Keep the legacy 0.08 % test explicit instead of treating it as the shipped default.
- Carry selection is hourly and ranks net APR, with 1000PEPE then WLD as tie-breakers. The -3 bps stop measures relative mark return drag and prevents re-entry until the next scan. Paper only; no profitability inference.
- The briefing failure showed that status plus close time does not guarantee a complete settlement report; require the closed flag and all four reconciled cash-flow fields before publishing.

## Handoff 6C — 2026-11-08 (g)

### 1. Mục tiêu (Goal)
Resolve four demo entry blockers: let qualified `SWARM_OVERRIDE` pass known non-crash regime style checks, preserve SIDEWAYS SCALP net RR >= 1.30 after scaling, shorten demo regime warmup, and set the override raw-consensus floor to 0.80 while retaining the 0.768 quorum.

### 2. Ràng buộc (Constraints)
- Worktree: `F:\he_thong_da_tac_tu_2`, branch `feat/janas-autoresearch-smc`, HEAD `2398e92`.
- Preserve all existing local changes. No commit, engine restart, live change or actual order. Pytest temp data stays on `F:/gt_scratch/`.
- Keep the 0.768 quorum, RUNE veto <= 0.70, VESKA HMAC token, 15% drawdown kill switch and all unrelated risk gates active.

### 3. Trạng thái thực tế (Real State)
- Updated `core/engine.py`: `SWARM_OVERRIDE` rejects only `CRASH` / `UNKNOWN` at the regime gate; demo override floor 0.80; 300 s trend window / 240 s ER span; requested SIDEWAYS SCALP TP floor; demo profile wiring for router scalp-only, rolling Kelly, scalp eject, paper CarrySim and toxicity guard.
- Updated `docs/harness/feature_list.json` and appended progress/handoff entries. Existing task regression files remain in the dirty workspace and were included in validation; no cleanup or reset was performed.

### 4. Bằng chứng (Evidence)
- Full project suite through Git Bash `./init.sh`: 1813 passed, 0 failed in 132.07 s; pytest temp path was under `F:/gt_scratch/`.
- `./init.sh` then failed at its auxiliary toolkit step because `F:/AI_Agent_Toolkit/env/Scripts/python.exe` has no pytest. Running that tools path with the project Python collected no tests (exit 5); the repository suite above passed.
- `git diff --check` is clean; `feature_list.json` parses; strict six-section handoff validator passes. No live engine or order was used.

### 5. Bước tiếp theo (Next Steps)
1. The shipped demo profile changes take effect after the owner's next demo restart; this session did not restart it.
2. Repair the separate toolkit pytest environment only if its auxiliary `tools` check is expected to contain tests.

### 6. Quyết định & Bài học (Decisions & Lessons)
- Regime compatibility is bypassed only for an explicit eligible override in known regimes; `CRASH` and `UNKNOWN` remain fail-closed.
- The post-scaling scalp floor uses `(new_sl + 0.08) * net_rr_min + 0.04`; at a 0.09% stop it produces 0.261% TP and net RR 1.30. The general demo edge floor remains 30 bps; only the scalp-specific floor is 18 bps.
- Lowering the raw-score floor to 0.80 still cannot relax the hard 0.768 quorum, RUNE veto, other risk gates or token authorization. The CarrySim and toxicity paths stay simulated / quote-protective.

## Handoff 6C — 2026-11-08 (h)

### 1. Mục tiêu (Goal)
Resolve the remaining demo execution blockers: permit only a qualified `SWARM_OVERRIDE` through clean cold-start WARMUP, shorten demo regime initialization to a 45-second ER span, lower the demo-only scalp modeled edge floor to 10 bps, and expose elapsed warmup in the cockpit.

### 2. Ràng buộc (Constraints)
- Worktree: `F:\he_thong_da_tac_tu_2`, branch `feat/janas-autoresearch-smc`, HEAD `2398e92addd3e7d440b30cc3627fb6c2f05dc01d`.
- Existing dirty files were preserved. No main-branch edit, commit, engine restart, live change, or actual order. Pytest temporary data stays on `F:/gt_scratch/`.
- Preserve the 0.768 execution quorum, 0.80 demo override raw-consensus floor, RUNE agreement/veto, normal VESKA AuthToken path, 15% drawdown kill switch and all other entry/risk gates.

### 3. Trạng thái thực tế (Real State)
- `core/engine.py`: demo profile uses `scalp_min_edge_bps=10.0` (live remains 18.0), `regime_trend_window_s=60.0`, `regime_er_bucket_s=5.0`, and `regime_er_min_span_s=45.0`. Regime checks allow `SWARM_OVERRIDE` past style compatibility in known non-crash states; during UNKNOWN they allow only demo `WARMUP` with the full eligibility checks. Crash, data-gap and invalid-input states refuse. Order execution continues through the usual VESKA authorization path.
- `core/regime_classifier.py`: tracks clean warmup elapsed time; clean startup activates its first known regime when windows are filled, while fault-recovery confirmation remains in place.
- `web_ui/app.js`: bottom regime strip displays elapsed/required values such as `WARMUP 35s/45s`, and retains the fault cause during warmup after a gap.
- Updated focused regression coverage and `docs/harness/feature_list.json`, progress and this handoff. No workspace reset or unrelated file cleanup.

### 4. Bằng chứng (Evidence)
- Focused command covered `tests/test_swarm_override.py`, `tests/test_scalp_net_rr_gate.py`, `tests/test_regime_gate.py`, `tests/test_cockpit_carry.py` and `tests/test_regime_classifier.py`: 82 passed in 3.34 s.
- Full repository command used Python 3.10 with `--basetemp F:/gt_scratch/pytest-demo-warmup-20261108-h`: 1819 passed, 0 failed in 133.20 s.
- Browser sample telemetry rendered `WARMUP 35s/45s`. At 1920x1080, viewport/document dimensions were 1920x1080 and page scroll was false. The preview was local only; its temporary fixture and server were removed.
- Demo 12.2 bps modeled edge clears the 10 bps floor in the focused test. This does not establish positive realized expectancy after fees; no replay or P&L study was run. No live engine/order was used.

### 5. Bước tiếp theo (Next Steps)
1. The demo profile settings take effect after the owner's next demo restart; this session did not restart the engine.
2. Calibrate modeled scalp edge and evaluate realized post-fee performance on a pre-registered, time-separated replay before making profitability claims.

### 6. Quyết định & Bài học (Decisions & Lessons)
- The WARMUP exception is limited to the literal clean-start reason `WARMUP` and the full eligible demo override path. Requiring exact equality prevents `WARMUP after DATA_GAP ...` from inheriting the exception.
- Using 5-second ER buckets is necessary for a 45-second ER span to become observable; the trend window is 60 seconds. The clean first regime may activate immediately once inputs support it, while post-fault recovery remains confirmed.
- A 10 bps modeled edge floor is a filter, not a guarantee of positive post-fee payoff. Live retains its 18 bps setting.

## Handoff 6C — 2026-11-08 (i)

### 1. Mục tiêu (Goal)
Stop chop SCALP entries from fighting the macro trend or the 3-minute CVD (owner brief after the 11-08 SOL losses), floor the scalp stop at 0.15 % with fee-true net RR >= 1.35 and a 45 s maker EJECT-BE, and fix the regime warmup timing flake.

### 2. Ràng buộc (Constraints)
- Worktree `F:\he_thong_da_tac_tu_2`, branch `feat/janas-autoresearch-smc`, HEAD `2398e92`. Other writers' uncommitted work preserved; no main-branch edit, commit, engine restart, live change or order.
- Pytest temp data on `F:/gt_scratch/` (explicit `--basetemp`; `tests/conftest.py` also roots plain runs there).
- Quorum 0.768, RUNE veto 0.70, the VESKA AuthToken path and the 15 % kill switch unchanged. The new checks only refuse entries or widen the scalp bracket.

### 3. Trạng thái thực tế (Real State)
- `core/engine.py` Profile: `scalp_macro_lock`, `scalp_cvd3m_usd` 50000, `scalp_align3`, `scalp_sl_min_pct` 0.15, `scalp_net_rr_min` 1.35 (defaults, so demo and live); `scalp_be_s` 45. Live keeps `chop_scalper='off'`.
- `scalp_candidate` refuses MACRO / CVD_3M conflicts; `chop_scalp` 'on' refuses ALIGN3 misalignment (forced even with demo `align3='shadow'`) through `scalp_lock()` (funnel MACRO_LOCK / CVD3M_LOCK / ALIGN3_LOCK, rate-limited event). `align3_check(side, force, f)`.
- `bracket_plan('SCALP')` floors the SL and lifts the TP (`scalp_min_tp`); `execute` re-floors after regime scaling and gates SCALP at `scalp_rr_min()`; `lane_feasibility` shows the effective bracket against 1.35.
- Shipped demo scalp: TP 0.3505 % / SL 0.15 %, modeled net RR 1.35.
- Tests: new `tests/test_scalp_trend_lock.py`; legacy opt-outs in `tests/conftest.py`; three 45 s pins; the regime warmup tolerance.

### 4. Bằng chứng (Evidence)
- Trade ledger 11-08 SOL: two SCALP SELLs against CVD 3 m +245k / +61.9k with macro MIXED and regime DRIFT, stopped at 0.09 % after 14 s / 3 s; the third loss was SWARM_OVERRIDE. Total -0.127 U net.
- Baseline 1819 passed; focused 73 passed; full `python -m pytest "tests" -q`: 1832 passed, 0 failed (Python 3.10.11, basetemp `F:/gt_scratch/pt_scalp_lock_full2`).
- Behaviour tests only. No replay, P&L study or engine run; modeled RR is not realized RR (tick rounding, stop slippage).

### 5. Bước tiếp theo (Next Steps)
1. The owner restarts demo for the profile change to take effect (not done here).
2. Pre-register and replay the locked scalp (TP 0.3505 / SL 0.15 / EJECT 45 s) on time-separated data before any size or profitability claim, including how often all five ALIGN3 layers agree in chop.
3. Owner decision: keep scalp size with the wider stop (loss at the stop +67 %) or scale size back to the old per-trade risk.

### 6. Quyết định & Bài học (Decisions & Lessons)
- The brief's macro example was the CVD regime label; at entry the regime read DRIFT and the 5 m macro MIXED. A lock on `self.macro` alone would not have refused these trades, so the lock checks both, and CVD 3 m / ALIGN3 are what catch them.
- The SL floor has to apply after regime scaling: SIDEWAYS x0.75 is where the 0.09 % came from.
- Missing CVD 3 m passes the CVD check (as specified) but fails ALIGN3 in 'on'; shadow keeps its research rows.
- New gates follow the repo pattern: on in the shipped profile, off in the legacy micro-lane pin, covered by their own test file.

## Handoff 6C — 2026-11-08 (j)

### 1. Mục tiêu (Goal)
Owner decision on (i) ("Có, giảm size"): shrink scalp size whenever the 0.15 % stop floor widens the stop, so a stop-out costs what it did before the floor.

### 2. Ràng buộc (Constraints)
- Worktree `F:\he_thong_da_tac_tu_2`, branch `feat/janas-autoresearch-smc`, HEAD `2398e92`. No commit, engine restart, live change or order; pytest temp data on `F:/gt_scratch/`.
- Quorum 0.768, RUNE veto 0.70 and portfolio risk cap, the VESKA AuthToken path and the 15 % kill switch unchanged. The resize only lowers size or refuses.

### 3. Trạng thái thực tế (Real State)
- `core/engine.py`: Profile `scalp_floor_resize` (default on). In `_execute`, after the regime size step: size x (stop without the floor + plan fees) / (floored stop + plan fees), floored to the step; below the venue minimum -> `SCALP SIZE` refusal before any token. Position and ledger field `scalp_risk_mult`.
- SIDEWAYS demo scalp: SL 0.15 % at x0.739 size, i.e. the loss at the stop of the old 0.09 % bracket; TP 0.3505 %, net RR 1.35.

### 4. Bằng chứng (Evidence)
- `tests/test_scalp_trend_lock.py`: 15 passed (loss at the stop within one step of the unfloored bracket, never above; below-minimum refusal; SIDEWAYS x0.739).
- Focused set 148 passed; full suite 1834 passed, 0 failed (Python 3.10.11, basetemp `F:/gt_scratch/pt_scalp_size_full`).
- Behaviour tests only. Equal loss holds under the repo's fee model (maker 2, taker 5, slip 1 bps); realized fees and stop slippage differ. No replay or P&L claim.

### 5. Bước tiếp theo (Next Steps)
1. The owner restarts demo for (i) and (j) to take effect (not done here).
2. Pre-register and replay the locked, resized scalp before any size or profitability claim.

### 6. Quyết định & Bài học (Decisions & Lessons)
- "Same loss" is measured as `plan()` measures it (stop distance plus round-trip fees), so the factor is (raw + fees) / (floor + fees), not raw / floor.
- Below the venue minimum the scalp is refused rather than rounded up, matching the regime size gate; rounding up would break the equal-loss rule.
- Test sizes must respect RUNE's portfolio cap: the first version of the new tests was refused by RUNE (3.2 U > 1 U), which is the cap working.

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
- Decisions for the user: (a) RESOLVED 2026-11-09 by the owner: keep the MR (fade) lane effectively closed in micro mode (no separate non-aligned MR rules); (b) SL 0.22 vs 0.20 (TP 0.46 vs 0.432); (c) compare after a Demo run: stop-outs/timer exits/TP hits by `bracket`, refusals by `MICRO_KER` / `MICRO_ALIGN`.
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

## Handoff — 2026-11-10 (d) (adverse-selection Phase 1 built + recorder started; DEMO only)
- Approved by the owner: Phase 1 now; Phase 2 download (~60-600 MB aggTrades); recorder; payoff redesign (TP 0.35-0.50 % / SL 0.20 %) if Phase 2 finds no edge. Done now: Phase 1 and the recorder. `python -m pytest -q` 2101 -> 2113 passed (+ tests/test_harvest_markout.py 10, 2 recorder tests in test_fast_research_loop.py).
- Phase 1: `core/harvest_markout.py` + Engine `hlog_*` (post / fill / close / per-tick sample); `micro_harvest_log` True; `data/harvest_markouts.jsonl`; keys `harvest_key` on log / order / trade rows. Details: plan_micro_adverse_selection.md section 8. NOT live in the running bot: it started 08:08 with older code; restart `run.bat micro` (I did not restart anything).
- Recorder: `tools/record_market.py --light` did not exist (docs only); added `--light` and `--min-free-gb`. Started 09:07, PID in data/recordings/recorder.pid, 7 days, ~3 GB, stops itself under 8 GB free (F: had 15 GB, 92 % full). Windows shows 0-byte sizes for the open gz files; read them with gzip to check.
- Next: Phase 2 on the aggTrades-only features now (download), depth features after the recorder has its week; acceptance criteria are fixed in the plan (out-of-sample n >= 400, lower bound > 0, kept fills >= 25 %).
- Rollback: `micro_harvest_log=False`; stop the recorder with `Stop-Process -Id (Get-Content dataecordingsecorder.pid)`.

## Handoff — 2026-11-10 (e) (adverse-selection Phase 2 run: no filter, no bracket; DEMO untouched)
- Done: downloaded 81 daily aggTrades archives (408 MB, SHA-256 verified, `data/aggtrades_20261110/`, untracked) with `tools/fetch_aggtrades.py`; built `tools/phase2_harvest_study.py`; ran follow / fade / random x cons / opt (tables `data/phase2_*.csv.gz` and `*.results.json`, untracked). Results and the arithmetic: plan_micro_adverse_selection.md section 9.
- Result: 0.15/0.15 = -7.4 / -6.6 bps per fill; follow = fade = random; 0 of 40 brackets positive (TP 0.35-0.50 / SL 0.20 included: -6.0..-7.5); no feature (AUC 0.49-0.51); logistic filter holdout AUC 0.51; acceptance failed everywhere. A driftless price gives every bracket EV -(4 SL + 8 TP)/(TP + SL) bps, so a payoff redesign cannot rescue a no-edge entry.
- NOT done / not changed: no engine change; the Demo bot still has the harvester `on` and `patient` exits (run.bat); no payoff redesign was implemented because the study shows it cannot work. Open: Phase 2b with depth features when the recorder has >= 7 days (>= 2026-11-17).
- Decisions for the user: (a) switch `MICRO_HARVESTER` back to shadow in run.bat (the lane loses ~6-7 bps = 0.003-0.004 U per fill; 1.5 fills/h); (b) wait for Phase 2b before retiring it; (c) look at lanes whose targets dwarf the fee floor (the 4 h trend lane) or a lower fee tier.
- Rollback: delete data/aggtrades_20261110 and data/phase2_*; nothing else depends on them.

## Handoff 6C — 2026-11-10 (f) (owner directive: harvester to shadow, trend-sniper runner, carry settlement, regime-flip check, 15 U Demo bankroll; DEMO only, nothing started)

### 1. Mục tiêu (Goal)
Owner directive pasted 2026-11-10 after the 10:06-10:38 `run.bat micro` session, four pillars: (1) chop harvester back to shadow and "unleash" high-conviction swarm entries toward 1.0-2.5 % moves, (2) activate the BN/HL funding carry on the Demo with per-interval accrual and a cockpit stream, (3) stop the MARIN regime-flip exit from cutting a position after 20 s, (4) rehearse a 15-20 U bankroll. DoD: every claim checked against the ledger / code before any patch, changes opt-in and Demo-only, `python -m pytest -q` green, launcher values set, this entry.

### 2. Ràng buộc (Constraints)
Demo profile only; no engine start / restart, no order, ledgers read-only. Untouched: VESKA quorum 0.768 and AuthToken, RUNE veto, every entry gate (MICRO_KER, MICRO_ALIGN, net RR, regime gate), the exchange stop, RATCHET / RATCHET2, loss at the stop <= 0.05 U, daily kill 0.15 U, the 3-loss / 24 h halt, I6. The live profile refuses `micro_sniper_runner='on'` (invariant error) and never reads the Demo bankroll. No push. `web_ui/` carries the uncommitted 11-08 cockpit rework and was not committed here.

### 3. Trạng thái thực tế (Real State)
Branch `feat/janas-autoresearch-smc`, base commit `7becbd7`. Changed and committed: `core/engine.py` (Profile `micro_sniper_runner` / `micro_runner_tp_pct` 2.5 / `micro_runner_trail_atr` 2.0 / `carry_min_net_apr_pct` 25.0; `Engine.runner_grade`; runner TP cap in `_execute`; `eject_due` / `eject_fast_due` / `_manage_soft`; shadow markout post in `chop_scalp`; ledger fields `runner`, `tp_plan`; `Runner.funding_loop` builds `CarrySim(..., settlement='discrete')`), `core/invariants.py`, `config/settings.py` (`MICRO_SNIPER_RUNNER`), `research/carry_lane.py` (`CarrySim` `settlement`, `min_net_apr_pct`, `_settle`), `run.bat` (`MICRO_HARVESTER=shadow`, `MICRO_SNIPER_RUNNER=on`, `MICRO_CAPITAL_BANKROLL=15`), `tests/test_sniper_runner_carry_2026_11_10.py`, `CLAUDE.md`, this file. On disk, NOT committed: `web_ui/app.js` (the carry card reads `s.carry_sim`; it read `s.carry_lane`, a key the engine does not publish, so the card showed SHADOW while the simulated book ran).

### 4. Bằng chứng (Evidence)
- `python -m pytest -q`: 2124 passed before, 2142 passed after (18 new in tests/test_sniper_runner_carry_2026_11_10.py), 0 failed, 227.6 s.
- Ledger `data/engine_trades.jsonl` (130 rows): the 10:26 XRP row has `hold_s` 100.3, `bracket_src` HARVEST; `regime_flip_min_hold_s` 60 and `regime_flip_debounce_s` 20 already exist (engine.py), "held 20s" in the log line is the debounce; XRP limit 0.10M (usd_scale 0.1) vs CVD -0.11M. REGIME exits overall: 22 rows, 13 winners, -0.4885 U.
- The 10:34 PEPE winner: price move +0.19 % gross (0.0051398 -> 0.0051299), MFE 0.393 %, exit by the RATCHET2 stop; "+0.56 %" is ROE at 5x. The same signal (score 0.8865, OFI -0.83, KER 0.42) lost -0.0070 U at 10:22 on the SCALP path. SWARM_OVERRIDE has 2 trades in the ledger: 1 win, 1 loss (-0.0195 U, SOL, 11-08).
- MFE of the 129 settled trades that carry one: median 0.041 %, p90 0.177 %, p95 0.213 %, max 0.441 %; 0 trades reached +0.5 %, 0 reached +1.0 %. Micro trend-lane (path SWARM, micro) fills in the ledger: 0.
- The directive's own formula E = -(4 SL + 8 TP)/(TP + SL) bps gives -7.3 bps at TP 1.0 / SL 0.22 and -7.7 bps at TP 2.5 / SL 0.22: a wider target does not shrink the expected fee, it lowers the hit rate.
- Carry: the last snapshot of the 10:06-10:38 run (served by `python core/web_ui_server.py --port 8765`, read in the browser) shows the simulated book HELD AVAX and AAVE at 116.67 U per leg: funding +0.0038 U in ~30 min against 0.1516 U of entry fees, P&L -0.1473 U; AAVE's spread fell from 3.01 to 2.29 bps/8 h in that time. Replay reference stays 1.90 %/yr (XV H28|k2), not the 35 % instant APR.
- Cockpit after the fix, same snapshot: chip SIMULATED, line `SIM 2/3 · P&L -0.1473 U · funding +0.0038 U · equity 999.85 · AVAX -0.081 · AAVE -0.066`, no console error; at a 1024 px viewport the P&L + funding part needs 271 px of the 273 px line (the rest ellipsizes; full text in the tooltip).

### 5. Bước tiếp theo (Next Steps)
1. Owner: restart `run.bat micro` to load the new code (nothing was restarted). Expect far fewer fills: with the harvester in shadow only KER >= 0.28 entries trade.
2. After >= 30 rows with `runner: true`: compare their net per trade, MFE distribution and exit reasons with the non-runner rows before keeping the flag; no size increase on fewer.
3. Decide whether `web_ui/` (the 11-08 cockpit rework + the carry fix) is committed as its own commit.
4. Phase 2b of plan_micro_adverse_selection.md when the recorder has its week (>= 2026-11-17): shadow harvests now also write `shadow: true` quote records with `swarm_ok`.
5. Carry on a real second leg needs a Hyperliquid client, keys and its own invariant next to I5 (owner decision); `CarrySim` state is in memory only and restarts from zero; its capital is frozen at the first pass (116.67 U per leg = the 1 000 U fallback, not the 4 917 U wallet).

### 6. Quyết định & Bài học (Decisions & Lessons)
- Runner (`MICRO_SNIPER_RUNNER=on`, armed in run.bat :MICRO): a micro AUTO entry on the SWARM / SWARM_OVERRIDE path with raw score and raw consensus >= 0.80 keeps its planned bracket for every gate, rests its TP order at +2.5 % (a cap), is exempt from EJECT / EJECT-FAST, and trails max(0.20 %, 2 x ATR 1 m at entry) behind the peak from +0.50 % MFE. Unvalidated: it removes two exits and adds no edge; its only practical effect on the ledger's MFE distribution is the missing time eject.
- Rejected: exempting override entries from MICRO_KER / MICRO_ALIGN ("execute freely"). That would put directional trades into the KER < 0.28 tape where the 27-day study measured -7.4 bps per fill with follow = fade = random.
- Rejected: any regime-flip change. The 60 s grace is already unconditional (stricter than the requested "60 s unless CVD >= 3x"); adding the 3x clause would only add an earlier exit. Pinned by a test instead.
- Rejected: sending the Binance leg of a carry pair to the Demo account with the Hyperliquid leg simulated. The account would hold a naked directional leg next to a micro lane capped at 0.05 U per trade, and testnet funding is not mainnet funding, so it would measure nothing about the carry. Both legs stay on paper venues fed by mainnet quotes.
- Carry settlement: the continuous estimate credited funding to pairs that were opened and closed between two prints; the demo loop now pays only at the instants that passed (HL hourly, BN 00/08/16 UTC) at the rate seen before the instant. A Binance 4 h symbol is settled as its 8 h equivalent on the 8 h grid (approximation). Library default stays `continuous` so the older tests keep their meaning.
- Bankroll 15 U: lot = 0.20 x E x leverage x (1 + 5 x realized micro PnL) = 11.8 U today (micro PnL -0.0421 U), capped at 16.7 U by the 0.05 U loss ceiling at SL 0.22 % with a maker entry. A larger lot scales losses as much as gains.
- Lesson: a log line's "held 20s" was read as the position age; read the field's definition in the code before treating a pasted autopsy as a root cause. A cockpit card that silently falls back (SHADOW) hid a telemetry key mismatch: pin the key in a test.
- Rollback: delete the three new `set` lines in run.bat :MICRO (and set `MICRO_HARVESTER=on` to re-arm the harvester); `micro_sniper_runner='off'` is the code default; `git revert` the commit for the rest.

## Handoff 6C — 2026-11-10 (g) (owner directive: arm early invalidation for runners, stale-runner eject, carry basis freeze + cost telemetry; DEMO only, nothing started)

### 1. Mục tiêu (Goal)
Owner directive pasted 2026-11-10 after the 13:00-14:40 `run.bat micro` session (code `ce433dc`), three pillars: (1) arm early invalidation for `runner: true` positions (OFI <= -0.70 against, or CVD velocity <= -50 k/s, inside 45 s -> maker exit at the touch), (2) a stale-runner guard (hold >= 420 s with MFE < +0.20 % -> soft exit at the touch or the break-even floor), (3) carry: freeze new pairs when the cross-venue basis moves > 15 bps against a pair, and show a net yield that includes the round-trip cost. Done means: the ledger claims checked, the three changes implemented behind knobs, full suite green, handoff + CLAUDE.md updated, committed.

### 2. Ràng buộc (Constraints)
Demo profile only; no engine start / restart, no order, ledgers read-only. Untouched: VESKA quorum 0.768 and AuthToken, RUNE veto, every entry gate, the exchange stop, RATCHET / RATCHET2, the 0.05 U loss cap, the 15 U bankroll, `early_invalidation` profile-wide mode (`shadow` on demo, `off` on live). The guards act only on runner positions, and an armed live profile refuses `micro_sniper_runner='on'` (invariant from 11-10 e). Carry stays simulated on paper venues. No AI attribution in commits; nothing pushed.

### 3. Trạng thái thực tế (Real State)
Branch `feat/janas-autoresearch-smc`, base commit `2e1a5fa` (the owner's prompt commit on top of `ce433dc`). Changed: `core/engine.py` (Profile `micro_runner_inval_ofi` 0.70, `micro_runner_stale_s` 420.0, `micro_runner_stale_mfe_pct` 0.20; `early_invalid_due(..., runner=True)`; the armed block in `manage()`; the runner's per-position `eject_s` / `eject_mfe` / `eject_hard_s` at the fill; `eject_due` lets a runner's own schedule run; RUNNER STALE EJECT event; fill event text), `research/carry_lane.py` (`CarrySim(basis_freeze_bps=15.0)`, `basis_freeze`, `fees_closed`, `breakeven_h()`, view keys `basis_freeze`, `fees_total_usdt`, `net_apr_realized_pct`, `breakeven_h`, `rule.basis_freeze_bps`, `rule.round_trip_bps`), `web_ui/app.js` (book line shows fees, note line leads with BASIS FREEZE, tooltip on the note), `tests/test_runner_guards_2026_11_10.py` (new, 12 tests), `tests/test_sniper_runner_carry_2026_11_10.py` (one test renamed). No env flag and no `run.bat` change: the guards ride on `MICRO_SNIPER_RUNNER=on`. The bot was not restarted; a process that was started before this commit still runs the old exits.

### 4. Bằng chứng (Evidence)
- `python -m pytest -q tests/`: 2142 passed before, 2154 passed after (12 new in tests/test_runner_guards_2026_11_10.py), 0 failed, 239.2 s.
- Ledger `data/engine_trades.jsonl` (132 rows), both rows `runner: true`, path SWARM_OVERRIDE, IOC entries. XRP SELL 11.1 @ 1.0982 -> 1.0963: REGIME, hold 443.2 s, MFE 0.132 %, net +0.013778 U, `early_invalid` {t 11.8, fav +0.0455 %, "OFI flipped +0.32", shadow}. DOGE SELL 190 @ 0.06997 -> 0.07010: SL, hold 2215.5 s, MFE 0.093 %, net -0.035350 U (2 taker fills, 8.0 bps), `early_invalid` {t 14.7, fav +0.0357 %, "OFI flipped +0.93", shadow}. Day: 18 rows, -0.06365 U. The directive's numbers reproduce.
- What the directive left out: the XRP winner tripped the same shadow rule, 11.8 s after its own fill. At the profile-wide limit (0.30) arming would have closed BOTH: XRP -0.0018 U instead of +0.0138, DOGE -0.0032 U instead of -0.0353 (maker exit at the recorded `fav_pct`, taker entry 4 bps + maker exit 2 bps), session -0.0050 U instead of -0.0216 U. At 0.70 only DOGE's first trigger qualifies: session +0.0106 U (the directive says +0.0118). The ledger keeps the FIRST trigger only, so whether XRP's OFI later passed +0.70 inside 45 s is UNKNOWN.
- Shadow record, all 33 ledger rows that carry one (counterfactual = maker exit at the trigger, same fee assumption): better in 19, worse in 14, mean +0.56 bps of notional per trade (31 rows before today: +0.23 bps). First trigger at |OFI| >= 0.50: n 10, better 4, worse 6, mean -1.83 bps. At |OFI| >= 0.70: n 5, better 2, worse 3, mean +0.42 bps; without today's DOGE row n 4, better 1, worse 3, mean -5.51 bps (three of the four went on to win). 0.70 is the number that separates today's two rows; it is in-sample on n = 2.
- Stale guard against the two rows: DOGE would have been due at 420 s (MFE 0.093 %), 30 minutes before its stop; the XRP winner too (MFE 0.132 % < 0.20 %), 23 s before its REGIME exit. The directive's "XRP showed momentum in 443 s" does not hold against its own +0.20 % bar. Prices at 420 s are not in the ledger, so neither outcome is known. Ledger rows held >= 300 s: 19; the 5 whose final MFE reached 0.20 % all ended positive, 3 of the other 14 did (final MFE, not MFE at 420 s: a look-ahead, not a test of the rule).
- Carry, snapshot of the running session served by `python core/web_ui_server.py --port 8765` (read-only): book start 4 917.87 U (the Demo wallet, 573.75 U per leg), 1.67 h of run time, P&L -1.2814 U, funding +0.0237 U (funding APR 2.52 %), each pair's entry cost 0.3729 U in fees; closed WLD -0.5387 U and AAVE -0.2322 U after 55 minutes each. Annualized that is about -136 %/yr (from 1.67 h: only the sign and the 54:1 loss-to-funding ratio carry weight) while the per-coin column shows "net APR" 17.6 % and 29.5 % (today's spread held 28 days). `breakeven_h(3.28)` = 36.6 h: the AVAX print of the directive needs 36.6 h of unchanged spread to repay its 15 bps round trip, and the book rotates every hour.
- Cockpit (same server, DOM read after dispatching a `grok:tick` frame built from the live snapshot plus the new keys): book line `SIM 2/3 · P&L -1.2814 U · funding +0.0237 U · fees 1.3051 U · equity 4916.59 · AVAX -0.255 · AAVE -0.255`, note line `BASIS FREEZE AVAX -17.2 bps · ROTATION 60m · ...`; without the new keys (the old process) both lines render as before; no console error. At a 1024 px viewport the lines ellipsize at 273 px, the full text is in the tooltip. Not screenshotted.

### 5. Bước tiếp theo (Next Steps)
1. Owner: restart `run.bat micro` to load the new exits (nothing was restarted).
2. Judge the runner guards on ledger rows, not on today's two: >= 30 rows with `runner: true`; compare `reason` EARLY-INVALID (field `early_invalid.armed`) and runner EJECT-BE / EJECT rows with what the same rows' markouts say they would have made. `micro_runner_inval_ofi=0` or `micro_runner_stale_s=0` switches either guard off.
3. The carry book loses to its own turnover: decide whether `rotation_interval_s` 3600 and the instantaneous-spread entry stay (the replay-validated rule is H28|k2, 1.90 %/yr). Not changed here.
4. Phase 2b of plan_micro_adverse_selection.md when the recorder has its week (>= 2026-11-17).
5. `tests/test_cockpit_carry.py` and `tests/test_carry_lane.py` are still untracked (owner decision); the branch commits of this session are local unless pushed.

### 6. Quyết định & Bài học (Decisions & Lessons)
- Armed early invalidation is a second check for runner positions only (`early_invalid_due(runner=True)`, OFI limit 0.70, the CVD leg at the unchanged 50 k USD/s, not scaled per coin: 2 of the 33 shadow triggers were that leg, both SOL). The profile-wide rule stays `shadow` at 0.30 and keeps writing its first trigger; the armed exit adds `early_invalid.armed` {t, fav_pct, why} and the row's reason is EARLY-INVALID. Implemented as directed, marked UNVALIDATED in the Profile comment: the ledger does not show that an |OFI| >= 0.70 flip predicts a loser.
- Stale guard = the existing EJECT-BE machinery on the runner's own schedule (set at the fill): scratch order at the better of the touch and the fee-true break-even level from 420 s, floor dropped for a touch exit at 600 s (the profile's `eject_hard_s - eject_s` gap) under the usual fee-hurdle guard, so at the latest 720 s. Ledger reasons stay EJECT-BE / EJECT (with `runner: true`), not a new RUNNER-STALE-EJECT label: every reason-keyed rule (floor -> touch schedule, the -LOSS suffix) keeps working. The 11-10 (e) runner removed the time ejects and put nothing in their place; that was the zombie trade.
- Carry: the -3 bps basis stop already closes a pair long before -15 bps, so the 15 bps limit is a book-wide freeze on NEW pairs for one rotation interval when a held pair jumps that far in one 300 s refresh (a dislocation). "Spot-perp" in the directive is perp-perp here (BN vs HL marks). Telemetry adds what the book really made (`net_apr_realized_pct`, `fees_total_usdt`) and `breakeven_h`; the per-coin `net_apr_pct` keeps its 28-day definition.
- Not done: `early_invalidation='on'` profile-wide (it would also act on non-runner SWARM entries; shadow evidence +0.56 bps mean, mixed), any entry-gate change, a real carry leg.
- Lesson: the same shadow field that "proved" the loser's exit was sitting on the winner's row; read every row that carries the field before arming a rule from one of them. A policy that removes a time stop needs its replacement in the same commit.
- Rollback: set the three `micro_runner_*` knobs to 0 (guards off, runner as in 11-10 e), `basis_freeze_bps` is a constructor default; `git revert` the commit for the rest.

## Handoff 6C — 2026-11-10 (h) (carry sim: no rotation before a pair's break-even hours; DEMO sim only, nothing started)

### 1. Mục tiêu (Goal)
Owner approved (2026-11-10, after entry g) the follow-up: the simulated carry book holds a pair at least its break-even hours instead of rotating it out at the hourly scan. Done means: the rule in `CarrySim` behind a flag, the demo loop using it, tests, full suite green, committed.

### 2. Ràng buộc (Constraints)
Simulated book only (paper venues, no order leaves the process); no engine start / restart. Unchanged: entry rule (2.5 bps/8 h, gross 20 %, net-APR floor 25 % on demo, basis >= +1.5 bps), slots, guard, kill switch, the -3 bps basis stop, the 15 bps basis freeze, the spread-flip exit, discrete settlement. The library default of `CarrySim` stays the hourly rotation, so the existing carry tests keep their meaning. No AI attribution in commits; nothing pushed.

### 3. Trạng thái thực tế (Real State)
Branch `feat/janas-autoresearch-smc`, base commit `23370f7`. Changed: `research/carry_lane.py` (`CarrySim(hold_breakeven=False)`; each pair stores `min_hold_h` = `breakeven_h(entry spread)` and `hold_until`; the rotation scan keeps pairs inside their hold and fills free slots only; view keys `positions[].min_hold_h`, `positions[].hold_until`, `rule.hold_breakeven`; scan event lists the kept pairs), `core/engine.py` (Profile `carry_hold_breakeven` True; `Runner.funding_loop` passes it), `tests/test_runner_guards_2026_11_10.py` (3 new tests, 15 in the file). `web_ui/` untouched. The running bot still has the old book; a restart of `run.bat micro` loads this one (the book restarts from zero either way: its state is in memory).

### 4. Bằng chứng (Evidence)
- `python -m pytest -q tests/`: 2154 passed before, 2157 passed after (3 new in tests/test_runner_guards_2026_11_10.py), 0 failed, 233.6 s.
- The snapshot behind the change (entry g): 1.67 h, P&L -1.2814 U against +0.0237 U of funding; the two closed pairs lasted 55 minutes each: AAVE by "ROTATE net APR rank outside top 3" (-0.2322 U), WLD by "spread -0.36 bps/8h (flipped or gone)" (-0.5387 U). So one of the two logged closes is the rotation this change removes; the other is the spread-flip exit, which is unchanged. Carry events are not persisted (no log file holds "CARRY SIM CLOSE"), so n = 2 is all there is.
- Arithmetic: a round trip is 15 bps; at the entry floor (2.5 bps/8 h) the hold is 48 h, at 3 bps/8 h 40 h, at 7 bps/8 h 17.1 h. Sessions so far ran 0.5-1.7 h, so inside one session a pair is now never rotated out; it still pays its entry half (6.5 bps of fees plus slip: 0.37 U per pair at 573.75 U per leg) on the first refresh.

### 5. Bước tiếp theo (Next Steps)
1. Owner: restart `run.bat micro` to load it; watch `fees_total_usdt` against `funding_total_usdt` on the cockpit book line.
2. The spread-flip exit is now the largest source of round trips left (a -0.36 bps/8 h print closed WLD after 55 minutes). Whether a flip must persist before it closes a pair is an owner decision; not changed here.
3. Persist carry events (or the closed list) if the book is to be judged over days: today nothing survives a restart.
4. Runner guards (entry g): still need >= 30 `runner: true` rows.

### 6. Quyết định & Bài học (Decisions & Lessons)
- Scope = the ROTATE exit only, as approved ("instead of rotating hourly"). A pair inside its hold keeps its slot, so a better-ranked coin waits for a free slot; after `hold_until` the old rank rule applies again.
- The hold is fixed at entry from the entry spread (time, not realized funding): simple and testable. If the spread decays the pair has not actually broken even at `hold_until`; the docstring says so.
- Not a profit claim: the replay-validated rule is H28|k2 at 1.90 %/yr and says instantaneous spreads decay. This removes a cost the book charged itself every hour; a session shorter than the hold still ends below zero by its entry costs.
- Rollback: `carry_hold_breakeven=False` on the profile (or `hold_breakeven=False`), or `git revert` the commit.

## Handoff 6C — 2026-11-10 (i) (carry sim: a spread flip must last one funding hour before it closes a pair; DEMO sim only, nothing started)

### 1. Mục tiêu (Goal)
Owner approved (2026-11-10, after entry h) the second follow-up: a spread that goes <= 0 closes a simulated carry pair only when it persists, not on the first print. Done means: the rule in `CarrySim` behind a value, the demo loop using it, tests, full suite green, committed.

### 2. Ràng buộc (Constraints)
Simulated book only (paper venues); no engine start / restart. Unchanged: entry rule, slots, guard, kill switch, the -3 bps basis stop, the 15 bps basis freeze, the break-even minimum hold (entry h), discrete settlement. `CarrySim`'s library default stays "close at once" (`flip_persist_s=0`), so the earlier carry tests keep their meaning. No AI attribution in commits; nothing pushed.

### 3. Trạng thái thực tế (Real State)
Branch `feat/janas-autoresearch-smc`, base commit `9f26447`. Changed: `research/carry_lane.py` (`CarrySim(flip_persist_s=0.0)`; each pair tracks `flip_since`; the flip exit needs `now - flip_since >= flip_persist_s`; one CARRY SIM FLIP event when a flip is first seen; view keys `positions[].flip_since`, `rule.flip_persist_s`; close reason says how long the flip lasted), `core/engine.py` (Profile `carry_flip_persist_s` 3600.0; `Runner.funding_loop` passes it), `tests/test_runner_guards_2026_11_10.py` (3 new tests, 18 in the file). `web_ui/` untouched. The running bot still has the old book until `run.bat micro` is restarted.

### 4. Bằng chứng (Evidence)
- `python -m pytest -q tests/`: 2157 passed before, 2160 passed after (3 new in tests/test_runner_guards_2026_11_10.py), 0 failed, 238.4 s.
- The one logged flip close (snapshot of entry g): WLD, "spread -0.36 bps/8h (flipped or gone)" after 55 minutes, net -0.5387 U at 573.75 U per leg. Holding through that print for an hour costs 0.36 / 8 = 0.045 bps of notional; the close it triggered cost the exit half of a 15 bps round trip plus the next pair's entry. n = 1: carry events are not persisted, so how often a flip reverts inside an hour is NOT measured.
- Why 3600 s: funding is paid at settlement instants only (HL hourly, BN 8 h), so a predicted rate that dips between two instants and recovers costs nothing; one Hyperliquid hour is the shortest funding period on either venue. With the 300 s refresh the flip has to hold for 12 more refreshes with no known positive print.

### 5. Bước tiếp theo (Next Steps)
1. Owner: restart `run.bat micro` to load the book with both changes (h, i).
2. Persist carry events or the closed list: without it neither the flip-reversion rate nor the effect of (h) and (i) can be measured over more than one session.
3. A deep flip is not treated differently: at -5 bps/8 h the hour costs 0.6 bps. If spreads can flip by tens of bps, an immediate-exit threshold is the next owner decision; none is set.
4. Runner guards (entry g): still need >= 30 `runner: true` rows.

### 6. Quyết định & Bài học (Decisions & Lessons)
- Persistence is measured from the first flipped print with no KNOWN positive print in between. An UNKNOWN refresh neither resets the clock nor closes the pair (UNKNOWN is not a flip and not a recovery); the next known refresh decides.
- The guard and the basis stop run before the flip check and do not wait for it; a rotation after the break-even hold can still close a flipped pair.
- Not a profit claim, and not validated: it removes a round trip paid on a single print. If flips usually persist, the rule only adds an hour of negative carry before the same close.
- Rollback: `carry_flip_persist_s=0` on the profile (or `flip_persist_s=0`), or `git revert` the commit.

## Handoff 6C — 2026-11-10 (j) (carry sim rows persisted to data/carry_sim.jsonl; DEMO sim only, nothing started)

### 1. Mục tiêu (Goal)
Owner approved (2026-11-10, after entry i) the third follow-up: log the simulated carry book's opens and closes to a file, so the rules of entries g / h / i can be measured over more than one session. Done means: structured rows from `CarrySim`, the demo loop appending them to a file, tests, full suite green, committed.

### 2. Ràng buộc (Constraints)
Bookkeeping only: no decision reads the file, a failed write cannot stop the funding loop, an engine without a ledger path writes nothing. `CarrySim` itself still does no IO (it lives in research/). The cockpit snapshot does not carry the rows. No engine start / restart; no AI attribution in commits; nothing pushed.

### 3. Trạng thái thực tế (Real State)
Branch `feat/janas-autoresearch-smc`, base commit `1186712`. Changed: `research/carry_lane.py` (`CarrySim.records` = the rows of the last step via `_rec`; `_close(..., code)` with GUARD / BASIS / FLIP / ROTATE), `core/engine.py` (`write_carry_rows(ledger, rows)`, `CARRY_LOG_SCHEMA` 1, Profile `carry_sim_log` True, the call in `Runner.funding_loop`), `tests/test_runner_guards_2026_11_10.py` (3 new tests, 21 in the file). File: `carry_sim.jsonl` beside the trade ledger (`data/carry_sim.jsonl` in production, git-ignored by `data/*.jsonl`); it does not exist yet, the first refresh after a restart of `run.bat micro` creates it.

Row kinds (every row has `kind`, `t`, `schema`, `code` = commit of the checkout):
- `START` once per process: capital, slots, notional per leg, settlement, the rule values (so a session is self-describing).
- `OPEN`: coin, short / long, spread_8h, gross / net APR, basis_bps, min_hold_h, both marks and both rates. `OPEN_FAIL`: state, why.
- `CLOSE`: coin, `code`, why, opened, hold_s, net_usdt, funding_usdt, fees_usdt, spread at entry and at the close, basis_drag_bps, min_hold_h, flip_s (None when not flipped), settlements. `CLOSE_FAIL`: state, why.
- `FLIP` (first flipped print: spread_8h, held_s) and `FLIP_CLEAR` (lasted_s, measured between 300 s refreshes).
- `BASIS_FREEZE`: coin, drag_bps, until.
- `BOOK` once per refresh: equity, pnl, funding_total_usdt, fees_total_usdt, kill, and per open pair net / funding / fees / spread / basis drag / flip_since.

### 4. Bằng chứng (Evidence)
- `python -m pytest -q tests/`: 2160 passed before, 2163 passed after (3 new in tests/test_runner_guards_2026_11_10.py), 0 failed, 230.1 s. The suite wrote no `data/carry_sim.jsonl` (the tests use temporary ledgers).
- Size, measured on a three-pair book: `BOOK` 605 bytes, `OPEN` 338, `START` 410. One `BOOK` row per 300 s refresh is 0.17 MB per day of run time.

### 5. Bước tiếp theo (Next Steps)
1. Owner: restart `run.bat micro`; the file starts with the next refresh.
2. Readings the rows allow once there are sessions: flip reversion rate = `FLIP_CLEAR` / `FLIP` and the distribution of `lasted_s` (is one hour the right wait?); round trips per day and net per `CLOSE` by `code`; fees against funding from the last `BOOK` row of each `START`. No report tool was written.
3. The book still restarts from zero at every process start (state in memory): a pair's 40 h hold cannot complete in a 1-2 h session. Restoring the book from the file is a separate decision.
4. Runner guards (entry g): still need >= 30 `runner: true` rows.

### 6. Quyết định & Bài học (Decisions & Lessons)
- A `BOOK` mark per refresh goes beyond "opens and closes": with the break-even hold a pair rarely closes inside a session, and a pair that is open when the process stops has no CLOSE row. Without the mark the file would hold opens and no outcome.
- `FLIP_CLEAR` exists only for the log (no cockpit event): it is the numerator of the reversion rate that entry (i) could not measure.
- The reason code is passed by the caller, not parsed from the text of `why`.
- Rollback: `carry_sim_log=False` on the profile, or `git revert` the commit; the file can be deleted at any time.

## Handoff 6C — 2026-11-10 (k) (carry sim book saved every refresh and restored at start-up; DEMO sim only, nothing started)

### 1. Mục tiêu (Goal)
Owner approved (2026-11-10, after entry j) the fourth follow-up: restore the simulated carry book's open pairs at start-up so a break-even hold (17-48 h) can span restarts instead of every 1-2 h session paying new entry costs on a book that starts from zero. Done means: snapshot / restore in `CarrySim`, the demo loop saving and restoring, an honest rule for the funding of the offline gap, tests, full suite green, committed.

### 2. Ràng buộc (Constraints)
Simulated book only (paper venues, no order leaves the process); no engine start / restart. `CarrySim` still does no file IO and core/ still does not import core.venues (tests/test_venues.py). Bookkeeping failures (state file, history fetch) cannot stop the funding loop. Nothing is guessed for the offline gap: real prints or UNKNOWN. No AI attribution in commits; nothing pushed.

### 3. Trạng thái thực tế (Real State)
Branch `feat/janas-autoresearch-smc`, base commit `4ef4ea6`. Changed:
- `research/carry_lane.py`: `CarrySim.snapshot()` (plain JSON: capital, cash, closed pairs, rotation / freeze / kill-switch state, and per pair both paper accounts with positions, fees, realized, funding, last books, plus opening time, hold, flip clock, rates, settle time); `restore(state, prints)`; `offline_windows(state, now)`; `_venues`, `_rule`; rows `RESTORE` / `RESTORE_SKIP`; view keys `restore`, `positions[].unsettled`.
- `core/funding_executor.py`: `fetch_funding_prints(coin, start_ms, end_ms)` (Binance `GET /fapi/v1/fundingRate`, Hyperliquid `POST info {"type": "fundingHistory"}`; one page each, a full page raises).
- `core/engine.py`: `save_carry_state` (tmp + rename), `load_carry_state`, `carry_gap_prints`, Profile `carry_sim_persist` True, `Runner.funding_loop(..., fetch_prints=None)` restores when the sim is created and saves after every step.
- `tests/test_runner_guards_2026_11_10.py`: 7 new tests (28 in the file).
Files at run time: `data/carry_sim_state.json` (git-ignored by `data/*.json`; `.json.tmp` by `data/*.tmp`). Neither exists yet. The running bot has the old code.

### 4. Bằng chứng (Evidence)
- `python -m pytest -q tests/`: 2163 passed before, 2170 passed after (7 new in tests/test_runner_guards_2026_11_10.py), 0 failed, 231.7 s. The suite wrote no carry file into `data/`.
- A restored book equals its twin that never restarted: same positions, fees, banked funding, flip clock; both close on the same refresh for the same net (test). A 40 h hold opened in process 1 survives an outranked scan in process 2 at 10 h and is rotated in process 3 at 40 h (test).
- Live read-only check of the new fetcher (AVAX, last 12 h, 2026-11-10): Binance 2 prints 8 h apart, Hyperliquid 12 prints exactly 1.0 h apart (time stamps 60-80 ms after the hour); both lists parse. One call, not a monitor.

### 5. Bước tiếp theo (Next Steps)
1. Owner: restart `run.bat micro`. The first run starts a fresh book and writes the state file; from the second run on the cockpit event log shows `CARRY SIM RESTORED n pair(s) after x h offline ...`.
2. To start a fresh book on purpose (also the only reset of a halted kill switch): delete `data/carry_sim_state.json`.
3. Read `data/carry_sim.jsonl` after a few days: `RESTORE` rows give the gap and the offline funding; `BOOK` rows the path of fees against funding.
4. Runner guards (entry g): still need >= 30 `runner: true` rows.

### 6. Quyết định & Bài học (Decisions & Lessons)
- Offline funding = the prints the venues really paid in the gap, each settled on the saved position at the LAST SAVED mark (the mark at the print is not fetched: an error of the size of the price move over the gap, a few percent of the funding amount). When the history cannot be read the gap is UNKNOWN: nothing is credited, the missed instants are counted per pair in `unsettled` and shown; they are never paid later at a stale rate. The in-session rule (a failed refresh is settled at the last known rate) is unchanged: it covers minutes, not hours.
- The offline gap is unwatched, as a real position would be: the first step after a restore marks the pairs at the new quotes and applies every exit. A basis that moved past the -3 bps stop while offline closes at the new marks, not at the stop; a flip clock keeps running through the gap (no known positive print), so a pair that was flipped at shutdown and still is at start-up can close on the first refresh.
- Refused states leave a fresh book and a `RESTORE_SKIP` row with the reason: older than 7 days, time in the future, unknown schema, slots or utilization changed (the pairs were sized for another book), unreadable. The saved capital wins over the wallet of the new process: it is the same book.
- The kill switch and the basis freeze are restored: a restart is not a way around them.
- The new fetcher sends a neutral User-Agent, not the project name.
- Rollback: `carry_sim_persist=False` on the profile (fresh book every process, as before), or `git revert` the commit.
