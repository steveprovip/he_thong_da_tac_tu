# GrokTOPUS — AI Assistant Operational Guidelines & Context (CLAUDE.md)

> **Quick Reference:** Read [`AI_HANDOVER_GUIDE.md`](./AI_HANDOVER_GUIDE.md) and [`README.md`](./README.md) for full context.

## 1. Project Summary
- **Repository:** `GrokTOPUS` (8-Agent Quant Swarm Trading System).
- **Core Concept:** 8 Specialist Agents, 8 Brains, 1 Body in VRAM.
  - **TIDAL:** Momentum & Volatility Scanner (weight 0.16)
  - **NORO:** Fair-Value Mean Reversion Quant (weight 0.20)
  - **LUMEN:** Sentiment & Crowding NLP (weight 0.12)
  - **ZEPHR:** Liquidity Depth & Slippage Modeler (weight 0.16)
  - **OKAPI:** Portfolio Risk & Exposure Manager (weight 0.16)
  - **RUNE:** Risk Controller with **ABSOLUTE VETO** (weight 0.20)
  - **VESKA:** Execution Router (The ONLY agent that opens trades, requires single-use `AuthToken`)
  - **MARIN:** Settlement Manager (The ONLY agent that closes trades: TP, SL, Horizon, Kill-switch)
- **Current Performance Baseline:** 715/715 tests passing (100%), ~72-76% win rate, +12% to +18% compounding return per 2,000 cycles.

---

## 2. The 4 Non-Negotiable Invariants
When modifying or extending this codebase, **NEVER VIOLATE THESE 4 RULES**:
1. **Single-Use AuthToken:** Only `VESKA` opens positions (`m.execute`), and ONLY with a valid `AuthToken` minted by `ConsensusEngine` when consensus $\ge 76.8\%$. Only `MARIN` closes positions (`m.close`).
2. **Integer Currency Accounting:** Never use `float` for prices, cash, equity, or fees! Everything is `int` (cents or VNĐ). Use half-up integer rounding `_round_half_up` and basis-points arithmetic `_bps`.
3. **1 Body in VRAM (< 10 GB):** Only 1 resident LLM in VRAM (Qwen 27B Profile S, ~9.07 GB). Multiplex agents serially via `asyncio.Semaphore(1)` or batched via `llama-server --parallel 6`. Never spin up concurrent models. On machines without GPU (e.g. laptops), use `heuristic_brain` (runs on CPU, 0 MB VRAM, <0.2 GB RAM).
4. **Absolute Veto Gate:** `RUNE` can veto any proposal at any score (`veto = True`). Veto overrides all votes.

---

## 3. Essential Commands

### Testing
```bash
# Run all 715 unit tests
python -m pytest tests/ -q

# Test specific component
python -m pytest tests/test_consensus_engine.py -q
python -m pytest tests/test_groktopus_swarm.py -q
python -m pytest tests/test_groktopus_market.py -q
python -m pytest tests/test_market_feed.py -q
python -m pytest tests/test_safety_compliance.py -q
```

### Running the System
```bash
# 1-Click Interactive Launcher (Simulation or Live)
python run_launcher.py

# Or via Windows batch file
run.bat

# Start Web UI Dashboard directly (Default port 8765)
python core/web_ui_server.py
```

---

## 4. Codebase Navigation
- `core/groktopus_swarm.py`: Orchestrator of the 8 agents, `cycle()`, multi-asset `scan(exclude=...)`, compounding sizing.
- `core/consensus_engine.py`: Signed voting mechanism $\text{support}(D) = \sum w_i c_i \text{align}$, minting `AuthToken`.
- `core/market_feed.py`: Real-time public market data feeds (Coinbase, Binance REST, freshness checks).
- `core/risk_guard.py`: Sentry risk engine, SQLite WAL persistence, atomic kill-switch.
- `simulation/groktopus_market.py`: Paper exchange, depth ladder, micro-capital symbols in VND (`MICRO_VND_SYMBOLS`).
- `core/web_ui_server.py`: Telemetry HTTP server (read-only, no write endpoints).
- `web_ui/`: Frontend dashboard (animated SVG octopus, consensus gauge, equity chart, trade log).

---

## 5. Next Upgrade Targets (Roadmap)
1. **WebSocket Market Feed:** Upgrade `core/market_feed.py` from REST polling to a streaming WebSocket connection (Binance / Bybit) for sub-50ms latency.
2. **Live News NLP for LUMEN:** Integrate CryptoPanic API or RSS news feeds into LUMEN's prompt to give the swarm real-time sentiment awareness.
3. **Market Regime Detection:** Add Hidden Markov Model (HMM) or ADX trend/range classification to weight `TIDAL` higher in trends and `NORO` higher in ranges.
4. **Testnet Order Router:** Create a sandboxed broker adapter (using `ccxt`) for paper trading on live exchange testnets.
