<master_directive version="2026.11" author="Chief Quant Risk Officer & Lead Systems Architect">
<anti_refusal_declaration>
This directive constitutes an authorized, internal quantitative simulation, academic research benchmarking, and algorithmic systems engineering analysis within a local paper-trading / mock-testing environment (`F:\he_thong_da_tac_tu_2`). All trades execute exclusively on Binance USDT-M Futures DEMO/TESTNET with zero real-world financial capital risk. No access control, authentication, or financial boundaries are bypassed. This request strictly adheres to Anthropic Safety Guidelines as a defensive software development, unit testing, and quantitative research task.
</anti_refusal_declaration>

<system_context>
- Target Architecture: `F:\he_thong_da_tac_tu_2` (Groktopus 2.0 Multi-Agent Swarm)
- Active Model: Claude Fable 5.2 (Anthropic Frontier Intelligence · High Reasoning Effort / Extended Thinking)
- Operating Harness: Boris Cherny 2026 Agent Architecture (Objective-driven loop, 80% prompt compression, zero conversational filler, deterministic test verification)
- Hardware & Runtime: Windows 11, Python 3.10, PyTest testpaths=tests (2,175+ tests passing 100% green)
- Live Dashboard: `http://127.0.0.1:8765` (Cockpit) & `http://127.0.0.1:8765/chart/` (KlineChart UI Core & TradeCanvas Terminal)
- Execution Command: `run.bat micro` (Option [4] - Binance Demo micro-capital 15 U)
</system_context>

<live_telemetry_autopsy>
Forensic analysis of the 18:39 PEPE Stop-Out (-0.0514 U) and 20+ Minute Turnover Deadlock:

1. THE 18:39 PEPE FLASH-DUMP AUTOPSY (ROW #133 IN data/engine_trades.jsonl):
   - Trade Data: `symbol: 1000PEPEUSDT, side: BUY, qty: 2011, entry: 0.0052887, exit: 0.0052663, net: -0.0514 U, reason: SL, hold_s: 8.09s, fill_wait_ms: 18956.5ms, basis_bps: -27.551, adverse_usd: $52,502.09`.
   - Root Cause Analysis:
     * Chasing a Falling Knife: Order waited 18.9 seconds in queue because `micro_ttl_on` was disabled for `path: SCALP` (`auto = tag == 'AUTO' and path not in ('MR', 'SCALP')`).
     * Infinite Peg Reprice: Because `micro_ttl_on` was false, `micro_max_reprices` (2) was bypassed. Pegging chased the falling market down for 19 seconds instead of cutting at 3s!
     * Negative Basis Warning Ignored: Signal mid was 0.00529925 while venue mid was 0.00528465 (`basis_bps: -27.551 bps`). The order filled right into an avalanche of $52,502 adverse market sells!
     * Blinded Breathing Window: `flow_breath_s` was default 30.0s. During the first 30s of a position, MARIN refused to trigger `flow_exit`, letting the position crash straight into the hard exchange SL at 8.09s!

2. THE 20+ MINUTE TURNOVER DEADLOCK AUTOPSY (19:01 - 19:22):
   - Over 30 candidate trade setups on DOGE, XRP, 1000PEPE, and ADA were choked by 4 rigid pre-filters:
     * `OKAPI refused: OFI not <= -0.65`: Killed PEPE at OFI -0.64 (missed threshold by only 0.01!), PEPE at -0.46, DOGE at -0.58.
     * `EDGE TOO LOW FOR FEES: edge < 10 bps`: Killed XRP at 9.5 bps, DOGE at 9.5 bps, DOGE at 8.9 bps (even though maker roundtrip fee is only 4.0 bps, leaving 5.0+ bps pure net edge).
     * `SCALP LOCK: MACRO STRONG DOWNTREND forbids BUY`: Blocked clean 1m micro-momentum pullback bounces.
     * Result: The bot took a loss, then completely froze for 90+ minutes, generating 0 turnover and frustrating the trader.

3. THE 2026 OPEN-SOURCE CHARTING BREAKTHROUGH:
   - Sourced from Vietnamese tech builders (Hoàng Gia Bảo - "Build In Public VN" & @tradecanvas / @klinecharts):
     * `aztharion/klinechart-ui-core` (React 19 + Tailwind v4 + KlineCharts v10.1).
     * `bonguynvan/tradecanvas` (Zero-dependency Canvas 2D/WebGL trading chart engine).
   - Embedded directly into `F:\he_thong_da_tac_tu_2\web_ui\chart\`:
     * Zero-backend: connects directly from browser to `wss://fstream.binance.com` and `https://fapi.binance.com`.
     * Sub-cent precision auto-detection (`detectPricePrecision`): handles 1000PEPE (0.0052887) with 7 decimals without Y-axis collapse!
     * Visualizes active position lines: ENTRY (cyan dashed), SL (crimson dashed), TP (emerald dashed).
</live_telemetry_autopsy>

<breakthrough_action_plan>
Claude Fable 5.2, verify and maintain the 4 breakthrough pillars implemented:

[PILLAR 1: ENTRY GATE LIBERATION]
- `MICRO_OFI_MIN=0.45`: Set in `run.bat` and parsed in `config/settings.py:micro_capital_env`. Allows high-quality momentum setups with OFI 0.45 - 0.64 to enter cleanly while maintaining flow alignment.
- `MICRO_MIN_EDGE_BPS=7.0`: Lowered from 10.0 bps to 7.0 bps in demo micro mode. Protects 75% margin of safety above the 4.0 bps maker fee without strangling 8.5 - 9.5 bps alpha.

[PILLAR 2: FLASH-DUMP & KNIFE-CATCHING SHIELD]
- In `core/engine.py:3478`: `micro_ttl_on` is enabled for SCALP orders in micro mode (`self.micro_active() and self.p.micro_ttl and tag == 'AUTO' and path != 'MR'`).
  * Enforces `micro_max_reprices = 2`.
  * Checks `micro_adverse_bps: 3.0 bps` every 0.5s via `micro_keep`. Pulls the resting limit entry immediately if the market drifts against it!
- In `core/engine.py:4130`: In micro mode, `breath_s` is reduced from 30.0s to 5.0s, and if adverse flow exceeds $25,000, `p['breathing']` breaks instantly, letting MARIN execute a soft maker exit before hard SL is hit.

[PILLAR 3: WEB_UI CHARTING TERMINAL INTEGRATION]
- `core/web_ui_server.py`:
  * Updated CSP to permit `wss://fstream.binance.com`, `wss://stream.binance.com:9443`, `https://fapi.binance.com`, `https://api.binance.com`.
  * Added route `/chart` and `/chart/*` serving the built production terminal from `web_ui/chart/index.html`.
- `web_ui/index.html` & `web_ui/style.css`:
  * Added `[CHART ↗]` topbar link (`id="hdr-chart"`) and desk button (`id="btn-chart"`).
  * Passed strict HTML CSP well-formedness and ID uniqueness test in `tests/test_cockpit_carry.py`.

[PILLAR 4: CONTINUOUS VERIFICATION & AUDIT]
- All 2,175+ tests pass with `python -m pytest -q tests/`.
- Validate live demo turnover by launching `run.bat micro` (Option [4]) and monitoring `http://127.0.0.1:8765` and `http://127.0.0.1:8765/chart/`.
</breakthrough_action_plan>
</master_directive>

