# Progress

## Current State
Installed local AI toolkit and added docs/AI_TOOLKIT.md. Application runtime is unchanged.

## Completed
- Three shared MCP definitions installed with backups.
- Toolkit adapter tests and protocol smoke checks passed.
- Project baseline: 756 passed.

## Next Steps
Read docs/AI_TOOLKIT.md. Sign in to NotebookLM when using it; configure DBX connections with a read-only allowlist. Paid APIs require their own keys and explicit data authorization.

## Verification
Run `./init.sh` from Git Bash. Record exact test results before marking any future feature done.

## 2026-10-06 Upgrade (Claude Opus 5.5 Cowork)
- Layout: launchers -> scripts/, FORM_*.md -> prompts/, drafts -> docs/archive/, governance -> docs/governance/, harness -> docs/harness/, TradingAgents -> third_party/. Root = README/CLAUDE/AGENTS/requirements/pytest.ini/init.sh/run.bat/.env.local.
- Backend: core/atomic_io.py; research/flow_gauge.py; sniper JEV score + GTX maker + dynamic TP + liq buckets; PM15 impulse trigger + parallel/cached scan; fast_runner flow + pnl; web_ui_server SSE /api/stream.
- Frontend: web_ui/terminal.js + deck (5 widgets), glass/neon theme layer.
- Verification: `python -m pytest -q` -> 774 passed (baseline 756 + 18 new). UI: HTTP 200 on / /terminal.js /api/state /api/fast /api/stream; headless render 61 fps, no horizontal scroll at 390 px.
- Open: live network smoke run not possible from sandbox (egress 403) - run `run.bat liq` on Windows; live order routing for sniper/PM15 intentionally NOT wired (shadow ledger first).

## 2026-10-06 (b) 2-mode active trading overhaul
- New: core/micro_signals.py (OBI/CVD, VWAP 1m, liq impulse), core/micro_swarm.py (8-agent vote on WEIGHTS, JEV fast path >= 8.0 with all gates), core/engine.py (Engine/Runner/Controller, Binance demo, mainnet, MOCK venues reusing live.radar_auto_trader primitives).
- Server: POST /api/force_test_trade (demo only), /api/switch_mode (live needs process armed by typed LIVE + confirm), /api/close_position; JSON + loopback Origin required.
- UI: control bar (DEMO/LIVE switch, FORCE TEST, close, sound), trigger strip, Binance-style position/orders/history, dynamic agent labels, win/loss vignette + WebAudio. Legacy panels collapsed.
- run.bat: demo | live | test only; old launchers -> scripts/legacy/.
- Verification: pytest 794 passed. Engine smoke (synthetic WS + MOCK): auto SWARM 77.1% BUY -> TIME close; FORCE TEST via HTTP -> filled, STOP_MARKET + TP shown, time stop closed; live switch refused while position open / unarmed. Real Binance not reachable from sandbox (proxy 403).

## 2026-10-07 Maker-only + MARIN breakeven + WS split fix
- Root cause "ZEPHR warmup 0/30": legacy wss://fstream.binance.com/stream only serves /public after Binance split; aggTrade never arrived. Fixed in core/micro_signals.py (public + market sockets) and research/fast_runner.py.
- core/engine.py: GTX-only entries (PostOnlyReject, no chase), CostModel bootstrap, MARIN manage(): BE lock +0.12 %, TP1 50 % +0.35 %, trailing 0.20 % after +0.50 %, no time stop; Gross/Fee/Net per trade; asyncio lock serialises entry / MARIN / REST sync.
- core/micro_swarm.py: confluence >= 2 of C1/C2/C3 on both swarm and JEV paths.
- Verification: pytest 801 passed. Smoke (synthetic WS, MOCK): entries maker 100 %, BE lock at +0.124..0.131 %, TP1 maker, exits TP1+BE net +0.32 U each (synthetic data, not evidence of edge).
- Known flaky (pre-existing, untouched): tests/test_spot_perp_carry.py::test_runtime_profit_target_requires_wallet_reconciliation failed once under full-suite load, passes in isolation and in later full runs.

## 2026-10-07 (b) CVD regime gate + 2-way + flow exit
- Incident: AUTO/JEV BUY 85,488.7 with CVD 60s -$3.44M, closed MANUAL after 355 s, net -0.110 U (ledger). Root: VWAP mean-reversion + C3 edge passed confluence with no flow-direction gate.
- core/micro_signals.py: regime() DOWN/UP/NEUTRAL/DRIFT, S4 TREND, MR only NEUTRAL, regime-banned signals listed in BANNED; per-symbol USD scale.
- core/micro_swarm.py: NORO=(TREND|VWAP), C4_TREND confluence, allow= hard side gate.
- core/engine.py: regime per tick, force test refuses banned side, adverse-flow exit (USD since entry in 15 s), micro-BE +0.08 %->+0.03 % gross, entry fee window fix (opened_ms = order post time - 1 s; the incident row missed the entry maker fee).
- Verification: pytest 811 passed.

## 2026-10-07 (c) Pegged maker entry, maker exit queue, MTF CVD, coin switch
- Trade #3 (ledger): BUY 0.002 @85313.5 -> 85327.0 FLOW, gross +0.027, fee 0.1024 (maker 2 bps in, demo taker 4 bps out), net -0.0754. Maker exit would give -0.0413 (saves 0.0341), NOT positive: round-trip maker fee = 4 bps = $34.1 BTC move on 0.002 BTC.
- core/engine.py: peg loop (score >= 0.80, 0.5 s, drift cap 0.05 %), micro-price quote on wide spreads, soft exit queue (start_soft_exit / work_soft_exit), hard SL only taker, xstop_kind for settle, switch_symbol + fee_model.
- core/micro_signals.py: cvd_3m (180 s) and multi-timeframe regime; TREND only in confirmed UP/DOWN.
- Verification: pytest 821 passed.

## 2026-10-07 (d) Super-edge IOC + aggressive peg
- 09:35 case (cons 80 %, JEV 9.6, CVD +$7.49M, 44 tokens, all GTX unfilled): router now sends LIMIT IOC (touch +/-0.03 %) when raw consensus >= 0.85 or (JEV >= 9 and |CVD60| >= $3M x scale); else pegged GTX 6 s / 0.15 % drift, ask-1 tick on wide spreads.
- Unfilled -> SCANNING + 1 s cooldown. IOC entries: taker-aware soft BE (+0.12 % trigger, level +0.09 %).
- tests/test_shutdown_control.py fixture isolated from the live data/telemetry_state.json (a running/killed engine made it fail).
- Verification: pytest 827 passed.
