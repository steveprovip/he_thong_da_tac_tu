# 🐙 GROKTOPUS 2.0 SOTA CLAUDE CODE & OPUS 5.5 COWORK HARNESS

## 1. Core Directives & Token Minimization (80% Reduction Stack)
- **Style**: Caveman + Ponytail mode. Zero conversational fluff, zero throat-clearing preambles ("Sure", "Here is..."), zero unsolicited markdown tables.
- **Context Engineering**: Strict XML tag boundary separation (`<system_role>`, `<context>`, `<task_directive>`, `<decision_ladder>`, `<verification_gate>`).
- **Surgical Edits**: Line-range targeted modifications only. Never re-dump full files when targeted diff chunks suffice.
- **TDD Verification Gate**: Every logic or UI state change MUST pass `python -m pytest -q`. Zero regressions allowed.
- **Security & Micro-Capital Guard**: Read secrets exclusively from `.env.local`. Max test/real capital risk capped at <= 3.85 USDT (100k VNĐ).

## 2. Command & Execution Reference
- **Run Tests**: `python -m pytest -q` (or `run.bat test`)
- **Master Launcher (2 modes only)**: `run.bat demo` | `run.bat live` (types LIVE) | `run.bat test` → `python -m core.engine --mode demo|live`; legacy launchers in `scripts/legacy/`, briefs in `prompts/`
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
