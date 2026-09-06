# 🐙 GROKTOPUS 2.0 WORKSPACE AGENTS SPECIFICATION

## 1. System Mission
This repository `F:\he_thong_da_tac_tu_2` is dedicated to rebuilding and perfecting the **GrokTOPUS Autonomous 8-Agent Quantitative Swarm** from scratch.

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
   - All tests in `tests/` must pass 100% without data leakage or lookahead bias.

## 3. Active Workspace Skills
Skills are located in `.agents/skills/`, `.claude/skills/`, and `.codex/skills/`:
- `anthropics-skills`, `superpowers`, `karpathy-skills`, `skills-for-real-engineers`, `ui-ux-pro-max-skill`, `caveman`, `addyosmani-agent-skills`, `taste-skill`, `awesome-claude-skills`, `i-have-adhd-skill`.
- `binance-futures-algo-guard`, `groktopus-swarm-consensus`, `quant-hft-microstructure`, `realtime-trading-telemetry`.
