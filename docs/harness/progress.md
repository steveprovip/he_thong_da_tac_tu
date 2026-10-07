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

## 2026-10-07 (e) Velocity / L10 wall / squeeze / 4-tier MARIN / demo venue book
- Root cause found in the live demo log: demo matching engine prices differ from mainnet; entries priced off mainnet were rejected post-only, and MARIN measured MFE on mainnet (trades #4/#5 show mfe_pct 0.0 although TP1 filled at +0.35 % on demo). Fix: BinanceVenue.own_book + bookTicker loop; Engine.book() is the execution/MARIN price source.
- BinanceVenue.order tolerates -2013 (2 tries -> NOT_FOUND).
- Real ledger stats (5 demo trades): 2W/3L, win rate 40 %, PF 2.28, net +0.3258 U, expectancy +0.065 U/trade.
- Verification: pytest 834 passed.

## 2026-10-07 (f) Anti-churn: $2M flow exit + 30 s breath, churn governor, regime-flip exit
- Ledger (17 trades): gross +0.9493 (incl. manual), fees 1.3458, net -0.4662; 15 FLOW exits, 13 with adverse $0.50-0.97M; busiest hour 14 trades; IOC entries 8 trades net +0.380 (fees 0.806), GTX entries net -0.776.
- SignalConfig.flow_exit_usd 2M; Profile flow_breath_s 30, flow_exit_trigger_mult 2.5 (cap 3x), regime_flip_usd 1M, max_trades_hour 8, cooldown_s 30, min_score 0.82, min_edge_bps 30; Engine.churn_gate / conviction / flow_limit / regime_flipped; snapshot['governor']; ledger rows carry edge_bps + flow_limit.
- Swarm raw consensus is quantized (3 alpha agents x 20 %): 60/80/100 %. Governor score = max(raw, JEV/10 when all gates green); edge >= 30 bps is the binding filter (trend feed 27 bps blocked, super-momentum 42 bps passes).
- tests/test_anti_churn.py (17). Verification: pytest 851 passed.

## 2026-10-07 (g) KER chop lock, regime-flip hysteresis, TP1 0.45 %
- Ledger 19 trades: gross +0.8500, fees 1.5476, net -0.6976; trades 18/19 (REGIME exits) held 261 s / 206 s with MFE ~0 % - the 60 s min hold alone would not have saved them; the KER lock and 20 s debounce target them.
- micro_signals.ker() (5 s buckets, 120 s window, 60 s min span), chop_locked() hysteresis 0.40/0.45, features ker/ker_span_s; Engine.chop_locked + chop_reason in churn_gate; Profile tp_pct 0.45, regime_flip_min_hold_s 60, regime_flip_debounce_s 20, ker_gate; snapshot['chop'].
- tests/test_chop_filter.py (10). Verification: pytest 861 passed.

## 2026-10-07 (h) Absorption trap, 2-tier fee shield, strict maker routing, 5 m macro gate
- micro_signals: absorption() (CVD 15 s vs price velocity 15 s), macro_bias() (VWAP 5 m + EMA-20 of 5 m closes), MicroFeed vw5 per-second buckets, 5 m candles + EMA, seed_candles(); features cvd_15s, px_vel_15s, vwap_5m(_span), ema5_20/slope/n.
- engine: churn_gate adds MACRO + ABSORPTION; shield_price (max(entry+0.04 %, fee-true BE)) and plock_price (+0.15 % at MFE +0.25 %) in soft and hard modes; route() IOC = CVD10 with side >= $3M AND KER >= 0.60 AND (JEV >= 9.2 or consensus >= 85 %); gtx_ttl_s 5; fetch_klines_5m + Runner.seed_macro; snapshot absorption/macro; ledger px_vel_15s/macro/plock.
- Legacy IOC mechanics tests pin super_ker=0 (squeeze box has KER ~0.22 by construction).
- tests/test_winrate_maximizer.py (17). Verification: pytest 878 passed. Kline fetch not verifiable from the sandbox VM (proxy 403).

## 2026-10-07 (i) Fee-hurdle exit gating, KER 0.48, velocity 0.04 %
- Ledger #20 REGIME +0.0049 % (258 s, MFE 0.007 %), #21 REGIME +0.0963 % (net +0.0948), #22 FLOW +0.0049 % (195 s, adverse $9.78M, KER 0.40). Under the new rules #20/#22 exits are held, #21 unchanged; the 0.04 % velocity rule would have refused all three entries (|vel| 0.034-0.036 %).
- engine: hurdle_gate / note_hurdle_block in manage() (FLOW + REGIME only), Profile fee_hurdle/hurdle_pct 0.08/hurdle_min_hold_s 90/min_velocity_pct 0.04, churn_gate VELOCITY, ledger hurdle_* fields, position_row gross_pct/hurdle; micro_signals ker_unlock 0.48.
- tests/test_fee_hurdle.py (14). Verification: pytest 892 passed.
