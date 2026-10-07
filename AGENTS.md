# 🐙 GROKTOPUS 2.0 WORKSPACE AGENTS SPECIFICATION

## 1. System Mission
This repository `F:\he_thong_da_tac_tu_2` is dedicated to developing, testing, and running the **GrokTOPUS Autonomous 8-Agent Quantitative Swarm** with full test coverage (606+ passing tests).

## 2. Invariants & Architecture
1. **8 Specialized Agents**:
   - `VESKA`: Execution Gateway (Sole Fund Mover, AuthToken Authenticated).
   - `NORO`: Fair Value Arbitrageur (VWAP Z-Score, Fair Value Drift).
   - `LUMEN`: Sentiment & Flow Analyst (Orderbook Imbalance OBI, CVD).
   - `TIDAL`: Market Scanner (Cross-sectional momentum, liquidity sweeps).
   - `ZEPHR`: Liquidity & Cost Gate (Quoted spread, slippage, Edge-to-Cost ratio).
   - `RUNE`: Supreme Risk Guardian (Absolute Veto if consensus < 70% or hard risk breach).
   - `OKAPI`: Exposure & Capacity Sizer (Fractional Kelly, margin caps).
   - `MARIN`: Exit Guardian & Position Settler (Dynamic Triple Barrier, Breakeven Lock, Avellaneda-Stoikov decay).

2. **Dual-Threshold Consensus**:
   - Supermajority Execution Hurdle: `>= 76.8%`.
   - RUNE Veto Hurdle: `< 70.0%` triggers immediate veto.

3. **Dual Execution Modes**:
   - **Mode 1 (Testnet / Paper / Mock Trading)**: Uses Binance Futures Testnet (`https://testnet.binancefuture.com` + `wss://stream.binancefuture.com/ws`).
   - **Mode 2 (Live Mainnet Trading)**: Uses Binance USD-M Futures (`https://fapi.binance.com` + `wss://fstream.binance.com/ws`) with Post-Only Maker GTX limit orders and exchange-side STOP_MARKET orders.

4. **Zero Lookahead & Test Discipline**:
   - All tests in `tests/` must pass 100% (606+ tests) without data leakage or lookahead bias. Run: `python -m pytest -q`.

## 3. Active Workspace Skills
Skills are deployed in `.claude/skills/` and `.agents/skills/`:
- **Token Minimization & Routing**: `jev-system-one-router`, `ponytail`, `caveman`, `budi`, `vibe-coding-curated-stack`.
- **Engineering Excellence**: `addyosmani-agent-skills`, `anthropics-skills`, `agent-memory-dreaming`, `harness-creator`, `karpathy-skills`, `superpowers`, `taste-skill`, `ui-ux-pro-max-skill`, `anti-slop`, `canvas`, `local-ai-toolkit`.
- **Quantitative Trading & Telemetry**: `binance-futures-algo-guard`, `groktopus-swarm-consensus`, `quant-hft-microstructure`, `polymarket-binance-micro-arb`, `realtime-trading-telemetry`.
- **Safety & Verification**: `review-bugbot`, `review-security`, `split-to-prs`, `permissioned-github`.

## AI toolkit integration (2026-10-04)

Read `docs/AI_TOOLKIT.md` when using the installed MCP servers or AI tools. Preserve existing project invariants and verification commands.

## Session workflow

At startup, read docs/harness/feature_list.json and docs/harness/progress.md and run ./init.sh from Git Bash. Work on one feature at a time within the user's authorized scope. Before ending, record checks and unresolved issues in docs/harness/progress.md and docs/harness/session-handoff.md; require passing checks before marking new work done. Leave a clean restart path and preserve unrelated local changes.


## Repository layout (2026-10-06)
```
run.bat            2-mode launcher: demo | live (typed LIVE) | test  -> python -m core.engine
scripts/           TAT_GIAO_DIEN.bat + python entrypoints (run_*.py); scripts/legacy/ = old CHAY_*.bat
prompts/           FORM_*.md agent briefs
core/ research/ live/ simulation/ phan_tich/ config/   python packages
web_ui/            dashboard (served by core/web_ui_server.py :8765)
tests/             pytest suite (pythonpath = . scripts)
tools/             diagnostics / ops CLIs
docs/{governance,harness,archive}/   ROLE/RELEASES/SCHEDULE, harness state, old drafts
third_party/       vendored TradingAgents
data/ artifacts/   runtime outputs (gitignored)
```
