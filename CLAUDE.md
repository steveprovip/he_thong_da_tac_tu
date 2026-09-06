# GROKTOPUS 2.0 CLAUDE CODE GUIDELINES

## 1. Operating Rules
- Follow surgical modification rules: never overwrite working modules when a line-range replacement is sufficient.
- Enforce strict Test-Driven Development: every new agent heuristic or alpha metric must have corresponding tests in `tests/`.
- Never commit or expose private API credentials outside `.env.local`.

## 2. Running Modes
- Testnet / Simulation mode: `python run_binance_testnet.py` or `python run_concurrent_paper.py`.
- Live Mainnet mode: `python run_concurrent_live.py`.
- Web UI Telemetry: `python core/web_ui_server.py` (served at `http://127.0.0.1:8765`).

## 3. Skills Access
- All 14 skills are pre-installed in `.claude/skills/` and ready for execution.
