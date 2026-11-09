# 🐙 GROKTOPUS 2.0 SOTA CLAUDE CODE & OPUS 5.5 COWORK HARNESS

## 1. Core Directives & Context Efficiency (measure savings per task)
- **Style**: Caveman + Ponytail mode. Zero conversational fluff, zero throat-clearing preambles ("Sure", "Here is..."), zero unsolicited markdown tables.
- **Context Engineering**: Strict XML tag boundary separation (`<system_role>`, `<context>`, `<task_directive>`, `<decision_ladder>`, `<verification_gate>`).
- **Surgical Edits**: Line-range targeted modifications only. Never re-dump full files when targeted diff chunks suffice.
- **TDD Verification Gate**: Every logic or UI state change MUST pass `python -m pytest -q`. Zero regressions allowed.
- **Security & Micro-Capital Guard**: Read secrets exclusively from `.env.local`. Max test/real capital risk capped at <= 3.85 USDT (100k VNĐ).

## 2. Command & Execution Reference
- **Run Tests**: `python -m pytest -q` (or `run.bat test`)
- **Master Launcher (demo | live)**: `run.bat demo` | `run.bat live` (types LIVE) | `run.bat test` | `run.bat micro` (= menu [4], DEMO with the 5 U micro-capital rules: `MICRO_CAPITAL_MODE=on` + `MICRO_IOC=on`, 4-coin rotation DOGE/XRP/ADA/1000PEPE, BTC/SOL observe, 4H lane parked) → `python -m core.engine --mode demo|live`; legacy launchers in `scripts/legacy/`, briefs in `prompts/`
- **Polymarket 15m Arb (shadow)**: `python -m research.polymarket_15m_fast_arb --mode shadow`
- **Run Fast JEV / Alpha Engine**: `python -m research.fast_liquidation_sniper` or `scripts\CHAY_SIEU_TOC_JEV.bat`
- **Run Paper / Simulation**: `python scripts/run_concurrent_paper.py` or `scripts\CHAY_DOGE_1M.bat`
- **Run Live Mainnet**: `python scripts/run_concurrent_live.py`
- **Telemetry Web UI Server**: `python core/web_ui_server.py` (served at `http://127.0.0.1:8765`)

## 3. Active Workspace Skills (.claude/skills/ & .agents/skills/)
- **Token & Routing Engines**: `jev-system-one-router`, `ponytail`, `caveman`, `budi`, `vibe-coding-curated-stack`.
- **Engineering, UI & Memory**: `addyosmani-agent-skills`, `anthropics-skills`, `agent-memory-dreaming`, `harness-creator`, `ui-ux-pro-max-skill`, `taste-skill`, `canvas`, `local-ai-toolkit`.
- **Trading, Alpha & Microstructure**: `groktopus-swarm-consensus`, `quant-hft-microstructure`, `binance-futures-algo-guard`, `polymarket-binance-micro-arb`, `realtime-trading-telemetry`.
- **Auditing & Safety**: `review-bugbot`, `review-security`, `anti-slop`, `split-to-prs`, `permissioned-github`.

## 4. Architectural Invariants & Milestones (v2.1.0)
- **Swarm**: 8 specialized agents (`VESKA`, `NORO`, `LUMEN`, `TIDAL`, `ZEPHR`, `RUNE`, `OKAPI`, `MARIN`).
- **Consensus Thresholds**: Supermajority Execution `>= 76.8%`, Supreme Risk Veto `< 70.0%`. Single-use HMAC AuthTokens for `VESKA`.
- **Execution Protocol**: 100% Post-Only GTX Maker default, Smart IOC for God-Candle momentum, on-exchange TP/SL bracket orders.
- **Micro-Risk Guards**: Kaufman Efficiency Ratio (KER < 0.38 CHOP LOCK), Absorption Trap Guard, Fee-Hurdle Exit Gating (+-0.08%), 90s Min Holding Discipline.
- **Multi-Asset Engine**: Live WebSocket multiplexing across `BTCUSDT`, `SOLUSDT`, `DOGEUSDT` in DEMO; Dual-Mode (Trend + Mean Reversion).
- **Telemetry & UI**: Atomic SSE stream `/api/stream` at `http://127.0.0.1:8765`, Multi-Asset Radar Bar, Realtime Position Card.
- **Release Tags**: Semantic version tags maintained on GitHub (`v1.0.0` through `v2.1.0`).

## 5. Evidence-led research brief (2026-10-07)
For profitability research requested by the user, read `prompts/FORM_CLAUDE_NANG_CAP_2026-10-07.md`; consult `prompts/NGUON_NANG_CAP_2026-10-07.md` only as needed. Historical milestone parameters above are not a substitute for the current code/config/logs. Read git status and the harness checkpoint before edits.
Optimize net expectancy after actual costs within existing risk limits, then latency. Missing ledger fields are UNKNOWN, never silently zero. Heuristic edge_bps and consensus are not calibrated probabilities. Small samples do not authorize size increases; trace NO EDGE/minimum-size fallbacks. Skills and old prompts supply hypotheses, not proof of profitability or current API/fee constants.
Use targeted context and 1–3 relevant skills. Freeze baselines, record experiments, validate strategy changes on time-separated holdout/replay with realistic fills and costs, and distinguish code tests from economic evidence. Preserve VESKA/AuthToken, RUNE veto and risk caps. Do not start/restart trading, enable live, or raise capital/leverage as part of research. Run required repo checks and record actual results/limitations and rollback in the harness.
This file configures repository guidance for Claude Code; Cowork Instructions and model/effort must be selected in the app, not inferred from this file.

Latest continuation after the 23:02 DEMO loss: `prompts/FORM_CLAUDE_SUA_LO_SOFT_BE_2026-10-07.md`. Reproduce DOGE SOFT-BE with be_locked=true but negative realized net, and failed RATCHET/restore lifecycle before researching strategy changes. Soft targets, acknowledged orders and confirmed exchange protection require distinct states; do not promise guaranteed breakeven fills. Recompute snapshots and preserve the previous instrumentation/sizing patches.

Latest continuation for the 2026-10-08 fill-starvation request: `prompts/FORM_CLAUDE_KHOP_LENH_NET_EV_2026-10-08.md`. Audit BTC observe-only, lane feasibility under net_rr_min, and real GTX attempts separately from SHADOW_SCALP. Improve opportunity capture and execution within risk limits; do not force continuous trades, enable the negative-replay scalper, or raise size to obtain fills. Preserve the r/u lifecycle and instrumentation patches. Treat testnet queue evidence and aggregate touch quantities as limited evidence, not exact mainnet fill probabilities. Research/code changes remain offline until separately authorized for operation.

Latest continuation 2026-11-09 (fee-drag fixes): `docs/harness/plan.md` + handoff entry "2026-11-09". Soft BE offset 0.08 % (SOL floor 0.065 %) with `be_locked` only once MFE pays the level; hard-stop BE offset stays 0.02 %; EJECT escalation guarded by fee hurdle / adverse momentum (`eject_guard`); LUMEN+TIDAL collinearity discount (`collinear_penalty` 0.5); `record_outcome` labels on NET; AUTO entries refused on Kelly NO EDGE (`kelly_no_edge_gate`; lifted only by the virtual shadow path `edge_shadow`: n >= 20 virtual trades with Kelly EDGE, queue/impact ignored) and on a JEV-lifted raw score < 0.768 (`raw_quorum_gate`). `scalp_tp_mode='atr'` is opt-in: TP 0.09-0.14 % needs a 70-79 % win rate and replayed at -6.3..-7.5 bps, so do not switch it on (or set `scalp_net_rr=False`) without a time-separated holdout. Verify a pasted "root cause" against the formula before patching it (the 0.02 % BE offset sat on top of the fees). 1881 tests passed; economic effect unmeasured.

Latest continuation 2026-11-09 (micro-capital 5 USDT; launch on the Demo account with `run.bat micro` / menu [4]): `docs/harness/plan_micro_capital.md`, `docs/MICRO_CAPITAL_5USD_DEPLOYMENT.md`, `tests/test_micro_capital_5usd.py`. `MicroCapitalSizer` (core/position_sizer.py) + `Profile.micro_*` + env `MICRO_CAPITAL_MODE` (auto|on|off) / `MICRO_CAPITAL_EQUITY_MAX` (15): wallet <= 15 U -> BTC/SOL observe, DOGE/XRP/ADA/1000PEPE trade one at a time, notional window [5.05, 6.0] U sized from the price (DOGE 0.0711 needs 72-84 coins, not 25-30), loss at stop <= 0.05 U, one position, daily kill 0.15 U, 24 h halt after 3 AUTO losses from the ledger, margin guard lets one window lot through. Code and unit tests only: no live start, I6 (3.85 U margin cap) untouched. The reported live SOL short is not in the ledger and is demo-sized (~771 U). Verify lot / price numbers against the ledger before copying them from a prompt.

Latest continuation 2026-11-09 (micro fill starvation): `docs/harness/plan_micro_rotation.md`, `tests/test_micro_rotation.py`. Micro mode only: GTX rests >= 10 s (hard floor; 15 s on a clean book; cut on spread > 3 bps, adverse 3 bps, drift 0.15 %; <= 2 re-pegs), opt-in hybrid IOC (`MICRO_IOC`: RAW consensus >= 0.78 + OFI >= 0.70 + TP >= 0.20 % + fee-true net RR with a taker entry >= 1.05, i.e. TP >= 1.05 x SL + 0.206 %; `on` is an I5 deviation, armed live refuses it), 4-coin rotation ranked by KER x |OFI| every 5 s, compounding size (E = 10 U -> 10 U, ceiling: 0.05 U loss at the stop, DOGE ~8.6 U). The 4H trend lane is parked while micro is forced and never enters on a micro account (it was NOT isolated before). Not measured: fill rate, adverse selection, signal fit of XRP/ADA/1000PEPE; 1963 tests passed.

Latest continuation 2026-11-09 (b) (micro deadlock): `docs/harness/plan_micro_unshackle.md`, `tests/test_micro_unshackle.py`. Micro mode only: net-RR floor 1.10 (the logged 1.15 refusals were the MR 0.29/0.10 bracket x SIDEWAYS 0.75 with fixed fees; break-even win rate 47.6 %, no guaranteed edge), JUMPY no longer freezes the scalp lane when spread < 2 bps and VPIN known < 0.70 (venue lot kept; `regime_classifier.py` untouched), 10 s resting floor. The TP >= 0.20 % IOC floor is moot: RR 1.05 needs TP >= 1.05 x SL + 0.206 %. These remove refusals, they add no edge; 1986 tests passed.
