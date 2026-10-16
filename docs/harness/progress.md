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

## 2026-10-07 (j) Multi-asset scanner + TREND/MR lanes
- engine: RiskBook (shared history/all_rows/cooldown/pause/trade_times/consec/day_pnl via properties), Engine.peer() slot lock in RUNE + execute, lane()/mr_profile()/radar(), build_scanner(), SCAN_SYMBOLS, Runner.build_multi/refocus/idle/demux/pump_multi, focus-only symbol switch in scanner mode, --no-scan; live stays single-engine.
- micro_signals: streams_multi, MR + DEPTH signals, ker 0.38/0.42, absorb move 0.025, macro slope >= 0.05 %/5 m; micro_swarm: NORO += MR, TIDAL += DEPTH, C2 += MR, C6 += DEPTH.
- UI latency chip shows GTX wait: p50 ~5.6 s in the 14:11 screenshot = entries timing out unfilled, not network.
- tests/test_multi_asset_scanner.py (15). Verification: pytest 907 passed. Runner smoke (mock, no keys): 3 engines start, radar + snapshot JSON ok (WS blocked by the sandbox proxy).

## 2026-10-07 (k) OFI L10, VPIN gate, ATR brackets, Avellaneda-Stoikov MR quote, quarter-Kelly
- micro_signals: mlofi() (CKS per level, L10 sum), atr() (Wilder), yang_zhang(), realized_sigma_bps(), as_quote(), toxicity(); MicroFeed depth levels + OFI deque (10 s, >= 5 events), VPIN buckets (exact aggressor, $1M x scale, 10), 1 m bars + seed_bars(); features ofi_10s/vpin/atr_pct/yz_pct/sigma_bps; OFI signal (TIDAL, C1).
- engine: Profile tp_pct 0.50 (static RR 2.5), ATR scaler (SL max(0.15, ATR), TP1 max(2.5 ATR, 2.5 SL), trail 1.2 ATR after TP1), MR TP clamp(|VWAP dev|, 0.12, 0.18) + SL 0.08 + A-S quote (gamma 0.1/bps, k 2/bps, tau 5 s, cap 2 bps, peg off), VPIN RUNE veto 30 s + maker-entry cancel in the GTX loop, kelly()/kelly_qty() (lambda 0.25, >= 20 trades/symbol, risk cap 0.50 U, notional cap $1000), bracket_plan() with rr_net/be_wr, quant_view() in snapshot, ledger fields; fetch_klines() + Runner.seed_bars(). Live: atr_scaler/kelly off.
- Real ledger: quarter-Kelly f* = -0.18 (22 trades) -> minimum size. Static/ATR floor economics: SL 0.15 / TP1 0.375 -> RR 2.5 gross, 1.46 after fees, break-even win rate 40.7 %; zero-drift hit rate of +2.5R before -1R = 28.6 %.
- tests/test_ultimate_quant.py (27). Verification: pytest 934 passed. Runner smoke: 3 engines, quant view JSON ok, kline seeds fail gracefully behind the sandbox proxy.

## 2026-10-07 (l) Triple flow concordance, DRIFT lock, scanner slot routing
- 15:25 SHORT (telemetry_state.json): VESKA AUTO SELL @ 83,979.2 (SWARM 80 %, ATR SL 84,105.2 / TP1 83,664.2); REGIME exit fired at 15:26:26 (gross +0.029 %, held by HOLD 79/90 s) and 15:31:34 (gross +0.011 %, held by FEE HURDLE); later -0.068 %. VPIN tripped 5x in 17 min (BTC 1.00/0.76, SOL 0.89/0.84).
- micro_signals.concordance(); engine: Profile concordance_gate/concordance_mr/drift_lock/slot_yield/hurdle_concordance_override, churn_gate DRIFT LOCK + FLOW CONFLICT, quality()/better_peer()/candidate (SCANNER YIELD), hurdle override hook, radar flow + DRIFT LOCK, snapshot['flow']; UI flow-chip.
- tests/test_concordance_guard.py (17). Verification: pytest 951 passed.

## 2026-10-07 (m) Multi-slot portfolio, convex sizing, exchange-stop ratchet, orphan adoption
- 17:21 screen: SHORT 0.001 @ 83,979.2 shown EXTERNAL (engine restarted): only the original stop 84,105.2 (+0.15 %, a loss) protected a +0.13 % position; RUNE vetoed every BTC entry. Telemetry 09:39-10:22 UTC: ~40 VPIN vetoes across 3 symbols in 45 min; 10:22:45 KILL SWITCH flatten.
- engine: Profile multi_slot/max_positions 3/portfolio_risk_usdt 1.0/portfolio_margin_frac 0.20, convex_* (swarm vote >= 0.90 + OFI >= 0.65 + CVD60 >= $1M -> 1.5x, >= 0.98 -> 2.0x), ratchet 0.15 -> 0.08, adopt_external (demo); risk_at_stop/margin_used/open_peers/slot_block/portfolio_check/portfolio_view/size_mult/min_qty/ratchet_stop/adopt; pending_risk for in-flight entries; better_peer off in multi-slot; snapshot['portfolio']; UI multi-row positions + portfolio chip.
- Fixed 2 time-boundary flaky tests in test_winrate_maximizer.py (live 5 m candle closing mid-test).
- tests/test_portfolio_multi_slot.py (20). Verification: pytest 971 passed.

## 2026-10-07 (n) Asset brackets, equal-risk size, asymmetric VPIN, -4130 stop fixes
- Ledger 23-26: #23 SOL NO_STOP (stop rejected -> market close in 1 s), #24 BTC REGIME +0.0610 (qty 0.001 = partial fill of a 2.0x order; 15x "move RATCHET failed: -4130"), #25 SOL SL -0.153 % (0.15 % floor), #26 SOL SL filled at -0.606 % with a 0.15 % stop (slippage on the demo venue).
- micro_signals: VPIN_BUCKET_USD per symbol via config_for, signed buckets + vpin_side(), vpin_max 0.80, toxicity side/blocks. engine: ASSET_BRACKETS + asset_floors(), parity_qty() (risk_target 0.15 U), toxic_side/toxic_blocks() in churn_gate (RUNE blanket veto removed), peg cancel only against the flow, swap_stop()/place_entry_stop() for -4130, move_stop crossed-check on the live book, ratchet 5 s back-off; radar sl/tp; live: asset_brackets/risk_parity off.
- tests/test_asset_calibrated_brackets.py (16). Verification: pytest 987 passed.

## 2026-10-07 (o) Asset KER gates, 180 s time-decay ejection, SOL/DOGE priority size, per-coin radar PnL
- Ledger 1-34: BTC 27 trades net -1.4531 U; SOL 7 trades net +0.2275 U (the "+0.33 U" = SOL winners #27/#29/#32 only, +0.3298). The "BTC -0.79 U" = #28/#30/#31 (KER 0.63/0.58/0.57, all >= 0.55: the KER gate would NOT have stopped them; all held >= 180 s with MFE < 0.08 %: the ejection targets them). #33 BTC KER 0.45 and #24 BTC +0.061 (KER 0.47) would be blocked. #34 SOL MR 0.81 SOL -> capped at 0.70 / $90.
- micro_signals: KER_GATES per symbol in config_for (BTC 0.55/0.55, MR off). engine: ALT_SIZE_CAP, Profile eject/eject_s/eject_mfe_pct/eject_hard_s/alt_boost/alt_boost_score/alt_reserve_usdt; eject_due/eject_floor, start_soft_exit(floor=), floor clamp in work_soft_exit, 420 s escalation; is_alt/risk_cap/alt_cap/alt_boosted, cap in execute, BTC reserve in slot_block/portfolio_check; coin_pnl in radar; ledger alt_boost/eject_floor; live off. desk: pnl-strip, EJECT/PRIORITY/floor.
- tests/test_sol_priority_chop_ejection.py (20); mock_engine/scanner default eject/alt_boost off. Verification: pytest 1007 passed.

## 2026-10-07 (p) Pure maker entries, VFR freeze, 85 % alt risk share, rolling Kelly SOL size
- Ledger 1-34 (unchanged, md5 427fd193...): gross +0.9582 U (form said +0.8885), fees 2.1141, net -1.2256, 9 wins. Gross >= 0 but net < 0: 8 trades #3/7/13/14/18/20/22/34 (form listed #10/#33, both gross < 0). The 12 IOC entries were net +0.2698 U; the 21 GTX ones net about -1.43 U. Stored 1 m ATR: BTC 0.046-0.066 % (VFR 1.2-1.7), SOL 0.078-0.135 % (VFR 2.0-3.4: winners #27/#29 < 3) -> the VFR reads ATR 15 m. Sept 5 m cache: BTC ATR 15 m p10/p50/p90 0.10/0.25/0.38 % (below 0.12 % 20 % of the time), SOL 0.31/0.45/0.70 %.
- micro_signals: bars_keep 240, bars_15m(), vfr(), atr15_pct/atr15_src/bars15_n. engine: super_edge False, gtx_ttl_s 3.0, gtx_backoff_ticks 2 post-only back-off, vfr_gate/vfr_min 3/vfr_full 5 + ATR15_FLOOR, vfr_view in churn_gate/radar/snapshot, tp_fee_mult 3, microbe 0.10/0.04, eject 0.06/360 s, ALT_SIZE (base, max, $), alt_kelly (window 15, min 5, full 0.10), alt_risk_share 0.85, btc_probe_qty 0.0001 + 30 s refuse cooldown, seed 240 1 m bars. Older tests pin the old T1 tier (OLD_TIERS) / opt in to IOC (super_edge=True) where they test that machinery.
- tests/test_world_quant_economic_engine.py (26). Verification: pytest 1033 passed.

## 2026-10-07 (q) Claude evidence-led research prompts and instructions
- Added prompts/FORM_CLAUDE_NANG_CAP_2026-10-07.md, continuation prompt, source register and Cowork setup guide. Updated CLAUDE.md with targeted research guidance and removed the unmeasured 80% token-savings claim from its heading. No trading runtime/configuration changed; no engine launched/restarted or API credentials read.
- Read-only ledger snapshot: 34 DEMO rows, 9 wins, PF 0.510952, net -1.22563407 U; 33 rows have gross/fee (0.95817999 / 2.11414320), and the first TEST row records only net -0.06967086 U. Missing accounting fields remain UNKNOWN; no ledger mutation.
- Selected primary Anthropic, quantitative-research and maintainer sources; distinguished Cowork app Instructions/model settings from Claude Code CLAUDE.md. App settings are prepared for manual entry, not reported as applied.
- Baseline: project pytest 1033 passed (73.75 s); AI toolkit 15 passed, 1 skipped. Post-edit init.sh via E:/Git/bin/bash.exe: project 1033 passed (73.83 s), toolkit 15 passed/1 skipped. Artifact UTF-8/fences/XML/copy/arithmetic assertions and git diff --check passed. Final result records and additional September preprint citations are documentation-only updates.

## 2026-10-07 (q) Evidence-led audit: accounting, order/latency instrumentation, sizing safety
- Data seen (read-only): engine_trades.jsonl 34 rows (md5 427fd193, unchanged), telemetry_state.json 13:56-14:29 UTC (code 46dcdf9), shutdown_request.json 14:29:02 UTC. Baseline via tools/ledger_audit.py -> docs/research_upgrade/baseline_ledger_2026-10-07.json.
- OBSERVED: dashboard session.gross 0.8885 counted the TEST row as gross (true known gross 0.95817999); KILL at 14:29 left the SOL SHORT 0.61 @116.01 out of the ledger (outcome UNKNOWN - check the demo account); alt_kelly_min_n=5 scaled SOL 0.45->1.06 from 7 trades + convex 1.5x; signal feed (mainnet) vs demo venue basis ~+25 bps and demo spread 8.6 bps in one snapshot; ledger latency_ms is decision->fill incl. GTX wait (decision compute p99 1.46 ms in VM).
- Patches: Q1 UNKNOWN-aware stats/session; Q2 kill_switch() closes via close('KILL'); Q3 engine_orders.jsonl per entry attempt with decide->send/send->ack/ack->fill, basis, policy, code (order_log flag); Q4 ledger schema 2 (+policy, code, signal/venue mid, basis entry/exit, ack/fill ms, markouts 1/5/30 s); Q5 convex_sizing False, alt_size_boost False, alt_kelly_min_n 20. Older mechanism tests opt in explicitly.
- Checks: python -m pytest -q 1052 passed, 0 skipped. init.sh toolkit leg (F:/AI_Agent_Toolkit) not runnable from the Cowork VM -> run on Windows Git Bash. No trading started, no live, no capital/leverage change.
- Docs: docs/research_upgrade/{audit,source_register,experiment_register,report_before_after}*.md.

## 2026-10-07 (r) Loss continuation brief: DOGE SOFT-BE and stop lifecycle
- Prepared prompts/FORM_CLAUDE_SUA_LO_SOFT_BE_2026-10-07.md and a targeted CLAUDE.md continuation pointer. Read-only snapshot: 37 DEMO trades, 11 wins, PF 0.511409, net -1.24659858 U; DOGE net -0.04524931 with be_locked=true, ratchet=false, 6 exit reprices. Order journal has 12 attempts: 6 UNFILLED, 4 REFUSED, 1 PARTIAL, 1 FILLED.
- Code evidence: soft-state flags record a computed exit target; SOFT-BE enters the re-peg queue without a floor. The brief requires reproducing actual execution/UI semantics, stop reject/cancel/restore races and cost/risk tradeoffs; clamping a floor indefinitely is not proposed as a remedy.
- Preserved prior schema-2/instrumentation/sizing changes; no runtime, trading mode, capital or leverage modification. Reused primary sources and verified relevant Opus 5.5, adverse-selection/latency, replay and September 2026 preprint references. Post-edit init.sh: 1052 project tests passed (86.99 s); toolkit 15 passed/1 skipped. Prompt structure/copy/snapshot assertions and git diff --check passed.

## 2026-10-08 (r) DOGE SOFT-BE loss + RATCHET failures: exit/stop lifecycle
- Data: engine_trades.jsonl 37 rows (unchanged by this task), engine_orders.jsonl 12 rows, telemetry 16:02 UTC; code f6ca5a7. Timeline: docs/research_upgrade/timeline_doge_soft_be_2026-10-07.md.
- Confirmed: be_locked was set for a SOFT target while the exchange SL stayed 0.08835 < entry (UI "BREAKEVEN LOCKED"); SOFT-BE maker exit chased the ask 6x to 0.08868 (reproduced exactly: net -0.04524931). RATCHET failures discarded the exchange code; no naked-window measurement; unlimited 5 s retries. ADOPT row #35 fee = one maker side (entry fee missing, ~0.01415 U). KILL at 14:29 did not flatten (cause UNKNOWN).
- Patches: R1 stop_covers_entry/protection + honest MARIN label + desk "CHƯA BẢO VỆ"; R2 bounded chase (soft_exit_escalate) implemented but OFF = shadow_escalate recorded; R3 err_txt (code + canonical meaning, no raw exchange text — existing sanitisation test kept), stop_events with context + naked_ms, ratchet back-off x2 and give-up after 3; R4 ADOPT entry_start_ms reconciliation + cost_complete flag; R5 kill_switch idempotent + reconcile report.
- Economics (tools/replay_soft_exit.py, mainnet bookTicker, no trades): bounded chase cost 0.4-1.1 bps/exit on holdout (SOL+LINK 2026-09-08, 672 events) and in-sample grid (164 events); tails improve (worst -15.5 -> -8.5 bps). Not enabled.
- Checks: python -m pytest -q 1070 passed, 0 skipped. init.sh toolkit leg not runnable in the Cowork VM.

## 2026-10-08 (s) MR anti-fade, net RR >= 1.30, re-peg evidence, super-conviction size
- Data: engine_trades 40 rows (not written by this task), engine_orders 56 rows, telemetry 03:11 UTC. #39 MR BUY DOGE OFI -0.929 rr_net 0.634 SL in 50 s (-0.04001 U).
- Patches: micro_signals.mr_flow_veto; engine churn_gate MR flow gate + net RR gate (bracket_plan rr_net), execute plan_rr check (covers live loss-budget SL); order log touch_last/touch_qty_last; super_conviction + SUPER_SIZE (SOL 0.50 / DOGE 450, demo only); ledger super_size. mock_engine/scanner isolate the new gates (default off in fixtures).
- Findings: net RR 1.30 makes the MR lane unreachable (max 0.875) and blocks every live entry (live rr 0.47); 25/32 unfilled GTX had no re-peg (queue, not drift); super-size rule fits 4 historical trades (+0.0443 U), not out-of-sample.
- Checks: python -m pytest -q 1102 passed, 0 skipped.

## 2026-10-08 (t) Opportunity starvation: VFR spread bug, BTC probe minimum, drift-accel probe, chop scalper (shadow)
- Data: orders log 63 rows (7 in the 03:26-04:12 UTC run: 3 GTX unfilled, 4 BTC refusals), telemetry 04:12 UTC. Starvation causes: VFR used the demo testnet spread (SOL 13 bps), BTC probe 0.0001 < $50 minimum, GTX fill rate 5/40 on demo queues.
- Patches: vfr_view spread from the signal feed; BTC probe uses the smallest tradable size within the 15 % budget; drift_acceleration() + drift_probe 0.5x; chop_scalper ('off'|'shadow'|'on', SCALP brackets 0.08/0.12, 90 s eject, half size, RUNE gates) default shadow -> SHADOW_SCALP rows; tools/replay_chop_scalper.py.
- Replay: scalper -7.46 bps [-9.53,-5.73] (cons) / -6.82 [-8.53,-5.69] (opt) in-sample; holdout 2026-09-08 -7.11 [-7.77,-6.29] / -6.31 [-6.69,-5.89].
- Checks: python -m pytest -q 1134 passed, 0 skipped.

## 2026-10-08 (u) BTC observe-only, SOL/DOGE size floor + super 0.80/500, 120 s low-velocity eject
- Ledger 41 rows: BTC 28 rows 6 wins -1.4999 (AUTO -1.4303), SOL 10 rows 6 wins +0.2522, DOGE 3 rows -0.0813. #41 = BTC 0.0007 probe, EJECT, -0.0468.
- Patches: Profile observe_only (BTC) blocks every entry path + scalper, keeps feed/telemetry/position management; ALT_SIZE_FLOOR SOL 0.40 / DOGE 300 (alt_size_floor); SUPER_SIZE 0.80 / 500; eject_fast_due (120 s, MFE < 0.08 %, |vel15| < 0.025 %) -> EJECT-FAST at touch; tools/replay_eject.py (B-A not distinguishable from 0).
- Checks: python -m pytest -q 1158 passed, 0 skipped.

## 2026-10-08 (v) Claude fill-starvation and net-EV research brief
- Added prompts/FORM_CLAUDE_KHOP_LENH_NET_EV_2026-10-08.md and a targeted CLAUDE.md pointer. Documentation only; no runtime/config change, trading API request or process start/restart.
- Read-only 13:05:19 Asia/Saigon snapshot: 41 closed trades, 13 wins, recorded net -1.32906026 U, PF 0.496227; Oct 8 has 4 trades net -0.08246168. Orders 386 rows: 314 SHADOW_SCALP, 43 UNFILLED, 23 REFUSED, 3 PARTIAL, 3 FILLED. Since 12:27:17: 6 real UNFILLED (5 DOGE SELL, 1 SOL BUY) vs 178 shadow events. Counts are snapshots, not a new data-collection run.
- Confirmed existing BTC observe-only, GTX TTL 3 s and net RR 1.30; prior experiment register identifies unreachable MR/live brackets. Brief requires lane-feasibility tests, non-starving scheduler, full funnel diagnostics and queue/latency/cost-aware replay; does not remove guards, enable negative-replay scalp, or increase size for fills. Aggregate touch quantity is not exact queue ahead.
- Verified primary Anthropic, Cont/Kukanov, Lehalle/Mounjid, Binance and hftbacktest references; September preprints are hypotheses rather than validated crypto alpha. Preserved r/u lifecycle/instrumentation work and the distinction between ledger net and fully reconciled costs.
- Baseline init.sh: project 1158 passed (83.01 s), toolkit 15 passed/1 skipped. Post-edit init.sh: project 1158 passed (79.90 s), toolkit 15 passed/1 skipped. UTF-8/XML/content/copy checks and git diff --check passed. Tests verify no software regressions; no economic improvement has been demonstrated by this prompt task.

## 2026-10-08 (v) Execution funnel, lane feasibility, GTX placement replay (implementation)
- Snapshot docs/research_upgrade/snapshot_2026-10-08v/latest (06:25 UTC, SHA256SUMS): ledger 41 rows unchanged (net -1.32906026); orders 480 rows, 54 sent / 6 filled (11.1 %); since 12:27:17 11 sent / 0 filled + 267 SHADOW_SCALP. 4 SOL/DOGE attempts were alone at the demo touch and still unfilled in 3 s; basis SOL p05/p95 -10.9/+18.9 bps.
- Patches: core/exec_funnel.py + Engine.churn_checks/funnel_sample (all blockers, 1 Hz, lane x stage, hard/cost/statistical/config); Engine.lane_feasibility; Runner.scanner_view publishes intents/fills/cancels/fill_rate/funnel/feasibility (app.js shows '—' for unknown); Runner.cadence by trading permission (observer slow, default focus = first tradable symbol); Engine.cancel_orphan_entry on errors after the GTX POST (reproduced: 2 tests fail on old code).
- Offline: tools/replay_gtx_placement.py (mainnet bookTicker SOL/LINK/BNB, holdout 2026-09-08): every placement negative net per opportunity; SKIP +1.87 bps [1.52, 2.18] vs current PEG_T3; TTL 5/10 s and IOC worse; JOIN_T3 +0.23 [0.11, 0.37] vs PEG (clear model), not distinguishable under the front model. tools/execution_funnel_report.py reproduces the funnel/latency/P&L tables.
- Checks: python -m pytest -q 1188 passed (Cowork Linux VM, Python 3.10); git diff --check clean. init.sh toolkit leg not run here. No engine start/restart, no order sent, no size/TTL/peg/gate change.

## 2026-10-08 (w) Super-Alpha crossing / dynamic TTL / ratchet 2 / venue retries
- Snapshot docs/research_upgrade/snapshot_2026-10-08w (07:56 UTC): ledger 44 rows, orders 927 rows (66 real SOL/DOGE attempts, 54 with vote >= 0.95, 1 ERROR -1008/503 at 14:44:12 handled by the (v) orphan path).
- Evidence (tools/counterfactual_super_alpha.py, public aggTrades demo + mainnet): crossing after 2 s -7.08 bps/opportunity on demo [-12.87,-1.71], -9.64 on mainnet [-15.69,-3.73]; immediate IOC worse; 10/15/20 s TTL worse than 3 s; 5-min MFE median 8.8 bps, max 28.3, 0/54 >= 50 bps.
- Patches: Profile super_cross/dyn_ttl ('off'|'shadow'|'on', demo shadow, live off), Engine.cross_check / cross_remaining (IOC remainder only, 3 bps cap, no MARKET) / dyn_keep, SHADOW_CROSS / SHADOW_DYN rows; fee-true ratchet tier 1 + ratchet2 (MFE 0.25 % -> fees + 0.05 %); BinanceVenue._post/_query_raw/cancel/order retries (-1008 resend same id; unknown -> query first; IOC never resent on unknown); Engine.orphans retried in sync().
- Checks: python -m pytest -q 1222 passed; git diff --check clean. No engine restart, no order, no live change.

## 2026-10-08 (x) Chop deadlock: adaptive KER + fixed MR bracket
- Snapshot docs/research_upgrade/snapshot_2026-10-08x (08:44 UTC); runtime funnel (code 4bf6fbb): KER sole blocker 3/1379 s SOL, 1/1424 DOGE; EDGE blocks 1373/1379; MR lane 0 samples; NET_RR never.
- Replay tools/replay_chop_lanes.py on data.binance.vision aggTrades SOL/DOGE 2026-10-01..07 (holdout 06-07): KER<0.38 81 % of time; every MR bracket negative (0.29/0.10 RR 1.389: -7.03 bps/fill [-10.76,-3.42], win 27 % vs 42 % break-even); TREND flow-surge proxy -7.9 to -8.2 bps/fill in every KER band.
- Patches: Profile adaptive_ker (+score/cvd/confluence/floor/slope), Engine.adaptive_ker + SHADOW_KER rows with other_blockers; mr_bracket/mr_tp_fixed/mr_sl_fixed in bracket_plan + lane_feasibility; exec_funnel NO_TRIGGER + combos_after_swarm.
- Checks: python -m pytest -q 1249 passed; git diff --check clean. No restart, no order, no live change.

## 2026-10-08 (y) Position sizing 10 % budget study
- Snapshot docs/research_upgrade/snapshot_2026-10-08y; ledger 46 rows (form said 47); today 9 trades 5W/4L net -0.0684; SOL 15 trades +0.266 U but -1.98 bps/trade on notional, realized RR 0.52.
- tools/simulate_position_sizing.py: 10 % notional median -19.4 U per 200 trades (boot), DD p99 1.24 %, null +0.3 U, stress -39.3 U; Option B 23x leverage, ruin 94.6 %. Kelly f* < 0.
- Liquidity (public depth20 + aggTrades, demo and mainnet): 0 bps taker slippage at 4.35 SOL / 5 500 DOGE; demo maker fill upper bound 10 % per 3 s at 4.35 SOL.
- Patches: Profile conviction_sizing/cs_tiers/cs_super_ofi; Engine.conviction_size (clip to free risk/margin caps), cs_* on order rows, 'conviction' on ledger rows. Demo shadow, live off. No cap or size raised.
- Checks: python -m pytest -q 1272 passed; git diff --check clean.

## 2026-10-08 (z) Fee drag: pre-stop maker exit, scaled TP1, demo caps
- Snapshot docs/research_upgrade/snapshot_2026-10-08z; ledger 49 rows. #49 BUY 2.04 SOL: ratchet exchange stop +0.08 % above the soft BE level +0.06 % -> STOP_MARKET, fee 6.0 bps (demo taker 4), net +0.0242.
- Patches: Profile pre_stop_maker/pre_stop_gap_pct (PRESTOP soft tier), tp1_scaled_pct 0.30 (demo; live None), cs_max_notional/cs_breaker_*; demo portfolio_risk_usdt 5.0 + cs_max_notional 1000; pos tp_plan.
- Replay tools/replay_exit_policies.py (aggTrades 10/01-07, holdout 06-07): PRE +0.25 [0.12,0.38] (through) / +0.38 (touch), PRE+TP30 +0.31 [0.17,0.48]; all policies still about -6.9 bps per position.
- Checks: python -m pytest -q 1285 passed; git diff --check clean. No restart, no order, live unchanged.

## 2026-10-08 (z2) Alpha gating & drawdown
- Snapshot docs/research_upgrade/snapshot_2026-10-08z2; ledger 55 rows; #49-55 gross +0.1721 fee 0.7267 net -0.5546. Mainnet CVD at entry: losers #51/#53/#55 all with the macro flow; shield would have blocked only #50/#54 (+0.008 U).
- Bugs fixed: soft maker exit could rest worse than the exchange stop (#51); SOFT-BE label on a raw loss (#55) -> '-LOSS' suffix.
- Patches: cvd_shield (on), align3 (shadow), early_invalidation (shadow), regime caps 170/225/315 + cs_max_notional 350 (demo), tp1_scaled_big_pct 0.35 for >= $225.
- Replay tools/replay_alpha_gating.py (aggTrades 10/01-07, holdout 06-07): BASE -6.56, SHIELD -6.42, ALIGN3 -7.27, EARLY -7.07 bps per position.
- Checks: python -m pytest -q 1313 passed; git diff --check clean. No restart, no order.

## 2026-10-08 (aa) Fast research loop & horizon pivot
- Deep research (4 parallel researchers + writer): docs/research_upgrade/research_alpha_after_fees_2026-10-08.md. Seconds-horizon OFI and VIP0 market making are negative after fees in the literature; only 4h-daily trend has published net-positive evidence.
- New: core/fast_eval.py (required_n, SPRT, mSPRT, paired_diff, PSR/DSR, ledger_bps, verdict), tools/edge_check.py (ledger verdict by code/symbol, --paired), tools/factor_scan.py (96 configs, 1h/4h/1d, DEV 2022-24 / VAL 2025 / HOLDOUT 2026-01..09, walk-forward, DSR), tools/record_market.py (/public depth@100ms+bookTicker, /market aggTrade+forceOrder, REST snapshots, hourly gz; --light).
- Results: ledger 9 code hashes in 55 trades, -3.48 bps/trade, SPRT BAD; 1h/4h trend negative OOS after taker costs; ENS 1d LS (pre-registered) OOS SR +0.21, +24.6 bps/RT net, PSR 0.61 (not significant).
- Checks: python -m pytest -q 1360 passed; recorder 20 s mainnet smoke OK. No restart, no order, engine/Profile unchanged; .gitignore += data/recordings/.

## 2026-10-09 (bb) High-speed replay, paired A/B, 1D/4H trend lane (paper)
- tools/engine_replay.py: real Engine + MicroFeed + MockVenue + Runner decide/sync loops on a virtual-time asyncio loop (time.time/monotonic/datetime patched, restored after); sources agg (synthetic book), l2 (data/l2 + aggTrades), rec (record_market). ~0.08-0.12 symbol-h per s per core (cloud); 1 year x 2 symbols ~60 h/core -> not 15-30 s.
- tools/ab_bench.py: micro (chunked engine replay, --a-set/--b-set, --a-code/--b-code worktrees, parallel) and trend (daily); paired CI, DSR vs logged trials, --register -> docs/research_upgrade/trial_log.jsonl + experiment_register AB rows. core/fast_eval.pbo_cscv.
- core/trend_lane.py + tools/trend_backtest.py: spec TSMOM 5/20/60 + Donchian 20, vol 20 %, funding overlay, band 5 %. 4H LS OOS SR +0.69 PSR 0.82 DSR 0.19; 1D LS +0.03; PBO 0.35. Profile.trend_lane 'paper' (demo) / 'off' (live); Runner.trend_loop paper only (data/trend_paper_<bar>.json/.jsonl, telemetry trend_lane), never calls the venue.
- Fix: RiskBook.roll() resets day_pnl at the local date change (was carried over midnight).
- micro_signals ker/realized_sigma newest_first fast path (identical output, test).
- Results: baseline replay Oct 1-7 108 positions -5.41 bps; AB-4 cvd_shield never binds (identical); AB-5 early_invalidation on +1.15 bps/trade, CI includes 0.
- Checks: python -m pytest -q 1396 passed. No restart, no order, ledger untouched.

## 2026-10-09 (cc) Autonomous self-learning & dreaming
- core/swarm_learner.py (NORO/LUMEN/TIDAL only; Brier, hit-rate, edge; weight 0.2-1.8 shrunk; mute 30 min after 3 losses), core/param_bandit.py (Thompson per KER regime: cs_super_ofi 0.85-0.95, hurdle_min_hold_s 30-90, tp1_scaled_pct 0.30-0.50; ban combo after 2 losses), core/tunables.py (whitelist, risk only down, expiry), core/dreaming_daemon.py (autopsy, CHURN/STREAK/ADVERSE/FEE_DRAG/NO_STOP/CONVICTION traps, data/brain_learnings.jsonl, config/dynamic_state.json, config/live_tunables.json), core/capital_router.py, core/autonomy.py (hub), Runner.autonomy_loop (5 s), Profile.autonomy ('on' demo, 'off' live), ledger fields agents/arms/agent_w/stop_reject, replay runs autonomy in temp dirs.
- Replay week 10/01-07 continuous: off 109 trades -8.51 U; on v1 (weights in vote) larger size via JEV path -8.90 U -> redesigned to veto-only; on v2 88 trades -6.87 U (+0.0097 U/h p 0.006, DSR 0.68; same bps/trade).
- Checks: python -m pytest -q 1433 passed. No restart, no order, ledger untouched.

## 2026-10-09 (dd) Execution timing & 4H trend activation
- core/trend_executor.py: 4H trend lane trades on the DEMO venue (sign changes only, size |w| x wallet clipped $150-300, exchange disaster stop 8 x ATR14(4h), policy maker/taker/swarm with IOC fallback after 300 s, state data/trend_active.json, trades data/trend_trades.jsonl, Engine.trend_owned blocks adopt). Runner.trend_active_loop (60 s). Profile: trend_lane 'active' + micro_standalone False on demo; live unchanged; demo-only guard in code.
- core/trend_lane.Brackets + backtest_brackets: requested TP1 1.35 % / SL 1.5 ATR / chandelier or Donchian-mid -> OOS SR -0.48/-0.54 vs +0.63 signal exits -> off; disaster 8 ATR +0.59.
- tools/trend_execution_replay.py: 261 OOS events, real aggTrades, engine MicroFeed: MAKER -1.20 bps vs TAKER [-1.84,-0.48]; SWARM-timed +0.87 bps worse than MAKER -> trend_exec='maker'.
- tests/conftest.py autouse: micro-lane tests see micro ON / trend paper; production defaults tested with @pytest.mark.production_profile. engine_replay defaults micro ON.
- Checks: python -m pytest -q 1452 passed. No restart, no order, ledger untouched.

## 2026-10-09 (ee) Regime breakthrough & 10 % sizing (trend 4H, demo)
- core/trend_lane: RegimeGate (TREND KER10>0.40 & ADX14>25, CHOP KER<0.25 & ADX<20; confirm vol>1.2xSMA20 + bar CVD with side; UNKNOWN volume -> no entry; chop_exit), Sizing min(10 %, 1 % / 8 ATR stop), Brackets lock_pct/soft; executor gate, CHOP/PROFIT-LOCK exits, size(atr); kline volume + taker-buy carried by fetch_klines/trend_fetch.
- tools/trend_regime_replay.py (pre-registered 8 gates x 3 locks x 2 sizings, 2022-01..2026-09, 148 trials): DEV-selected G6 (G4 + chop exit) + L1 chandelier 3.5 ATR armed at +2 %. Size 10 %: OOS SR 0.83 vs 0.43, DD 5.9 vs 9.6 %, DSR 0.30; 2025 SR +0.23 vs -0.32; paired daily diff CI [-3.0, +3.3] bps. Targets SR>1 / DSR>0.8 NOT met.
- Demo Profile: trend_regime='trend_only', trend_confirm, trend_chop_exit, trend_lock_pct=2.0, capital_allocation_pct=0.10, trend_risk_pct=0.01, trend_notional_max=600. Live unchanged.
- Checks: python -m pytest -q 1437 passed, 2 failed (pre-existing: missing local data/testnet signal lab + tools/check_models_live.py; same on stashed baseline). No restart, no order, ledgers untouched.

## 2026-10-09 (ff) BTC alignment, pyramiding, conviction, REST resilience (trend 4H, demo)
- Premise check: live "+2.68 % / -3.20 %" are 5x ROE (price +0.54 % / -0.64 %); position sizes match the (dd) $150-300 clip.
- tools/trend_regime_replay.py --stage ff (pre-registered, 159 trials): BTC shield A1/A2/A1E/A2E all lower OOS SR (0.59-0.78 vs B0 0.83; blocked alt trades were +2.3 % equity) -> trend_btc_align='shadow'; P1 break-even adopted (win 64 %, median +18 bps); P2 0.5x add rejected (DEV SR 1.25 < 1.27); C3 conviction adopted. FINAL P1+C3: OOS SR 1.06, +8.9 %/yr, DD 7.5 %, DSR 0.41; 25-40 %/yr needs x3-x5 leverage (DD 22-37 %) - not done.
- core/engine: rest_json (12 s, retry 0.5/1/2 s, no retry on 4xx), KLINE_CACHE, spot proxy without volume; fapi.binance.vision not resolvable / fapi1-3 redirect to the website -> REST_HOSTS = fapi.binance.com only.
- Checks: python -m pytest -q 1454 passed, 2 failed (pre-existing env). No restart, no order, ledgers untouched.

## 2026-10-10 Dual-engine & sub-minute burst scalper (lane 2, shadow)
- tools/burst_scalper_replay.py: 152 symbol-days aggTrades (SOL/DOGE USDT + USDC, 09-01..10-08), pre-registered burst (sigma_1m >= 15 bps | TFI 10 s > 0.85 | volume 3x), MOM/MR x TP 15/30, MM touch; fills through/touch, latency 0.25/0.5 s. Holdout DEV-best: SOLUSDT MR30 -7.17 [-7.36,-6.97], DOGEUSDT MR30 -7.69, SOLUSDC (0 % maker scenario) -3.98, DOGEUSDC -5.52 bps/trade; gross already negative (adverse selection); 20-28 trades/h, 90-200 per burst hour. Verdict SHADOW everywhere.
- core/burst_lane.py + Runner.burst_loop: shadow on demo (data/micro_trades_shadow.jsonl), active gated by config/burst_verdict.json, demo-only; coexists with the trend lane (one-way mode: only on a free symbol, 10 min quiet after each 4H close). tools/burst_lane_xcheck.py: live lane code = replay (SOL 168 vs 167 trades, -8.04 vs -7.85 bps).
- tools/fee_survey.py (signed commissionRate); 0 % USDC maker not confirmed (third-party: to 2026-02).
- Checks: python -m pytest -q 1469 passed, 2 failed (pre-existing env). No restart, no order, ledgers untouched.

## 2026-10-10 (b) Intraday 5m/15m micro-swing (lane 3, shadow)
- tools/intraday_replay.py (pre-registered, 1m klines 2024-07..2026-09, DEV 2025 / HOLDOUT 2026-01..09, through fills, stop-first): HOLDOUT S1_SESSION -7.09 bps [-8.33,-5.82] win 32.2 % (17/day), S1_ROLL100 -7.99, S2_slope3_tp60 -8.44 win 28 %; gross ~0; verdict SHADOW everywhere (n >= 100, CI > 0, win > 50 % rule).
- Workflow (9 agents): independent reimplementation matches; no look-ahead (5 checks); optimistic fill/fee bounds still negative, win rate <= 41 %; stats verified; 5 more families (intraday TSMOM, HTF MR, ORB, funding window, volume-shock reversal) negative on DEV, no holdout run.
- core/intraday_lane.py shadow-only (one virtual book per variant = replay trade by trade; would_block; 3-day kline store; server clock; data gap -> UNKNOWN). First active path removed after an adversarial review found order-safety defects; burst lane's similar latent path proposed as a separate task.
- Code review v2 (10 agents): 6 confirmed findings (BTC min-notional silent drop, ADX warm-up, SESSION after gaps, long gaps, clock skew, verdict floor) all fixed.
- Checks: python -m pytest -q 1488 passed, 2 failed (pre-existing env). No restart, no order, ledgers untouched.

## 2026-10-10 (c) Regime-aligned pull-back & 15m/1h swing (lane 3, shadow)
- core/intraday_lane.py: regime5 (UP/DOWN KER10(5m) > 0.40 or EMA20 slope > 0.10 %, CHOP KER < 0.25 & ADX15 < 20, else MIXED); R1 = S1 only in CHOP; P5 / P15 regime-aligned pull-backs to max(EMA20, session VWAP) with CVD exhaustion; TP1 50 % at +35/+50 bps (maker), break-even from the next minute, chandelier 2.5 ATR, 180 min; whole-trade rows (TP1+SL / TP1+TIME). tools/intraday_replay.py run_lane = the lane's own bar engine (single source of truth).
- Replay HOLDOUT 2026 (pooled): R1_SESSION -6.51 bps [-10.47,-2.31] win 34 %; P15_tp35 win 59.9 % but -7.07 [-10.83,-3.04] (avg win +29.6 / loss -61.7, break-even needs 67.6 %); P15_tp50 -6.92 win 52 %; P5 -8.1/-8.7. Gross ~0 everywhere -> SHADOW. Diagnostic: counter-trend S1 fades (-7.3/-8.2) are not worse than CHOP (-9.2) or MIXED (-7.2).
- Profile: intraday_variants=('R1_SESSION','P15_tp50'), intraday_close_margin_s=10.0. Report docs/research_upgrade/report_regime_aligned_pullback_2026-10-10.md, snapshot_2026-10-10c.
- Checks: python -m pytest -q 1493 passed, 28 skipped, 2 failed (pre-existing env). No restart, no order, ledgers untouched.

## 2026-10-10 (d) Exchange pivot & zero-maker-fee simulation (research + prototype adapters)
- tools/venue_fee_replay.py: one intraday + burst replay, every trade re-priced exactly (net = g - m - (1-tau) m - tau (t + s)) under 9 schedules. HOLDOUT: no variant positive with CI > 0 for any fee >= 0, not even zero fee (intraday gross -0.25..+1.73 CI contains 0; burst gross -2.2..-3.1). MEXC web 0/2: best intraday P15_rr25atr -0.68 [-7.14,+6.97], burst MR30 -3.00. MEXC API orders pay 6/8 bps since 2026-06-01 -> -13..-15. Only positive cell: MM touch-fill + -0.2 bps rebate (+0.20), unattainable.
- Binance: BNB -10 % = +0.6 bps; USDC-M 0 % maker promo ended 2025-12-10. P15_rr25 (100/40) HO -6.39 SR -4.0; P15_rr25atr -5.48 SR -1.8 -> added to demo shadow only.
- core/venues/: BaseVenue (place_post_only, cancel_order, position_view, set_disaster_stop), BinanceAdapter, MexcVenue / HyperliquidVenue prototypes (send nothing without transport/signer); tools/venue_probe.py. Not wired into the engine.
- Checks: python -m pytest -q 1503 passed, 28 skipped, 2 failed (pre-existing env). No restart, no order, ledgers untouched.

## 2026-10-10 (e) Burst log autopsy, 15m universe screener, funding arb architecture (research)
- Burst DEMO log 19:19-22:12: 53 shadow trades (form said 40), 2 wins, -455.7 bps / -3.40 U; gross -2.62 (= replay -2.2..-3.1), fee ~6 bps; 0 TP/SL, all 60 s time stops (MAKER-EXIT = passive time-stop exit); forward net -8.6 [-10.05,-7.01] vs replay HO -7.2/-7.7. Lanes unchanged (trend active, burst/intraday shadow).
- core/universe.py screen(): point-in-time USDT-perp universe (median 14 d quote volume >= 50 M, median daily range 150-1500 bps, age >= 90 d, COIN only, UNKNOWN excluded). Not wired. Live 10-10: 39 eligible, top 30 in snapshot_2026-10-10e/universe_2026-10-10.json (MAGIC/US/LUMIA pumps excluded).
- Funding (public API): Binance majors 1.2-6.8 % APR over 166 d, 7 d windows > 30 bps cost 0-3 %; Binance vs Hyperliquid spread 2.4-5.1 % APR, break-even ~3-4 weeks. Existing core/funding_carry + carry_runtime reused; phased plan (offline replay -> CarryVenue read-only -> shadow -> testnet) in report_log_autopsy_universe_funding_2026-10-10e.md.
- Open: multi-coin P15_rr25atr replay needs 1m kline download (~2-4 GB, user approval) and a decision on the win > 50 % gate for R:R >= 2 variants.
- Checks: python -m pytest -q 1533 passed before, 1540 passed after (+7 tests/test_universe.py). No restart, no order, ledgers untouched.

## 2026-10-10 (f) R:R >= 2 gate, P15 universe replay, funding arb Phase 0 (research)
- Gate (user decision): tools/intraday_replay.eligible - fixed-bracket planned R:R >= 2 -> HOLDOUT n >= 100 and CI95 lower > 0 (day bootstrap), no win-rate condition; others unchanged; venue_fee_replay uses it. No verdict file rewritten.
- tools/universe_months.py (point-in-time top 20 / month, 2025-01..2026-09, 52-symbol union), tools/fetch_klines_1m.py (504 monthly 1m zips, 818 MB, SHA256 vs .CHECKSUM; data/klines_1m gitignored), intraday_replay --universe/--workers. Repro BTC/SOL/DOGE = report d exactly.
- P15_rr25atr HOLDOUT on universe: n 11 673 (42.8/d) net -6.1 [-10.9,-1.2]; core 12: -6.6 [-12.4,-1.0]; 0/28 symbols CI > 0; 208/208 SHADOW. Universe not wired.
- tools/funding_spread_replay.py (pre-registered; public BN/HL funding + 4h opens, data/funding_cache): XV BN<->HL DEV-best HEDGE|H28|k2 HOLDOUT n 153 +14.6 bps/28 d [+9.2,+19.9] (month CI [+9.7,+20.4]; 9/9 months > 0; all-taker+3 bp stress +7.5 [+1.8,+13.0]) = ~1.9 %/yr on notional, edge in alts (TAO/WLD/ZEC), 79 % short HL. SP spot/perp negative (spot fee 10 bps). Verdict: XV eligible for read-only/shadow phases only; capital gate still blocks live.
- Checks: python -m pytest -q 1552 passed (1540 before, +12). No restart, no order, ledgers/Profile/verdicts untouched.

## 2026-10-16 RFC-2026-Q4: tri-state regime, Hawkes exhaustion, dual-leg stat-arb, funding carry (code only)
- Premise check: the session has no market data (fapi.binance.com, api.hyperliquid.xyz, data.binance.vision blocked by the egress proxy) -> no replay; every change is off or shadow, nothing selected by a live Profile.
- KER limitation: KER is scale-free (a 10x volatility compression leaves KER10 identical, tested); under a simulated random walk KER10 < 0.25 on 45 % and > 0.40 on 33 % of bars -> the 4H deadlock is estimator noise + the TREND & ADX conjunction, not compression.
- core/trend_lane: RollingHurst (Lo-MacKinlay overlapping VR(4) over 60 log returns, rolling sums, O(1); matches brute force to 1e-9), tri_state (> 0.55 TRENDING / 0.45-0.55 RANDOM_WALK / < 0.45 MEAN_REVERTING), STATE_LANES map (routing only, activates nothing), RegimeGate mode 'hurst' (entries only in TRENDING). Not selected by any Profile. Simulated: AR(+0.3) 87 % TRENDING, AR(-0.3) 92 % MR, random walk only 40 % RANDOM_WALK (27 % TRENDING, 32 % MR) -> one bar's state is weak evidence. Telemetry trend_lane targets carry hurst / state3.
- core/micro_signals.AsymmetricToxicityGuard: on_toxicity (Hawkes lambda over toxic VPIN buckets, tau_half = ln2 / beta from VPIN-excess decay, slope over 60 s; VETO at VPIN > 0.80 -> compute_quote_plan cancels both sides; FADE when >= 2.5 tau_half since the last toxic print, slope < -0.15 / min and lambda - mu <= 0.25 alpha; release to CLEAN below 0.75), fade_quote (GTX on the opposite touch). Library only - not wired into an order path.
- core/stat_arb_lane: DynamicZBand clamp(1.45, 2.10 (sigma15m / sigma4h)^0.5, 2.60) on EWMA innovation variance (static 2.10 until 4 h of history), StatArbLane z_alpha=0.5 default (shadow lane; rows carry z_entry_used), LeaderFollowerExecutor (leader GTX on the wider-spread leg, follower IOC per fill due in 150 ms, deadline / short IOC / late IOC -> reduce-only flatten, quote TTL cancel). Intents only.
- core/funding_executor.py: scan (spread >= 2.5 bps/8h, basis skew >= +1.5 bps, UNKNOWN skipped with reason), ShadowCarryBook (rotate only on +4.0 bps, close on flip / spread <= 0, mode != shadow refused), scenarios. 4 919.20 U, L1: replay HOLDOUT edge -> 3.90 U/month [2.46, 5.32] (0.079 %/month); the 2.5 bps/8h entry threshold held for a month would be 52.1 U - an upper bound the replay contradicts (spreads decay). RFC thresholds are not the pre-registered replay rule.
- Per-tick cost: Hurst 2 us, toxicity 5 us, Kalman + band 31 us (pre-existing O(n) OU fit) << 5 ms.
- Checks: python -m pytest -q 1548 passed, 28 skipped, 2 failed (pre-existing env: testnet probes, credential file) - 1530 before (+18). No restart, no order, ledgers untouched.

## 2026-10-16 (b) RFC-2026-Q4.2 log autopsy 08:52-12:42: burst exit, absorption gate, router hysteresis, stat-arb quotes
- Ledger truth (data/micro_trades_shadow.jsonl, 08:53:51-12:42:17): 119 BURST shadow trades (RFC said ~75), 15 net wins, net -1116.9 bps / -8.35 U virtual; 0 TP; exits MAKER-EXIT 65 (gross -1.7) / TIME-TAKER 50 (gross -5.0) / SL 4. Gross edge is negative before fees -> exit tuning cannot make the lane positive. All-time 690 rows, 5 TP.
- RFC premise corrections: lane signal is MR (fades the 10 s move), not breakout buying; the capital router does not gate the burst lane (only micro scalp_alloc); a maker exit stage already existed (10 s at our touch); a post-only exit at best_bid for a long is marketable (GTX reject) - the passive exit price is our own touch; engine.py had no TIME-TAKER; micro_signals.absorption (|CVD15| >= $500k x scale, move < 0.025 %) already existed and gated the engine, just not the burst lane.
- Replay (tools/burst_scalper_replay.simulate, SOL 09-05/06/08, MR30 through, n ~1 450; scripts in snapshot_2026-10-16q42/): base -7.30 [-7.71,-6.87]; maker until SL -6.61; maker + 240 s cap -6.62 (taker 52 % -> 1 %, gross -1.73 -> -2.58 adverse selection); engine absorption rule -7.19; cap + absorption -6.50 [-6.95,-6.11]. Symmetric absorption grids: no per-trade gain (fewer trades only).
- Code: burst_lane burst_time_exit 'taker'|'maker' (+ burst_exit_cap_s 240 -> TIME-CAP taker; SL unchanged), burst_absorb_gate (engine absorption, side-matched), rows carry 'absorb'; IOC-retry keeps its SL/TIME label. Demo profile (shadow lane only): maker + gate on; Profile default = replayed policy. micro_signals.absorption adds lambda_bps_m. capital_router CHOP_EXIT_KER 0.40 / CHOP_EXIT_HOLD_S 180 / BURST_COOLDOWN_S 300 applied in Autonomy.route (risk down only; no local Hurst feature exists, not invented). stat_arb_lane: QUOTE snapshot / pair / 60 s -> data/stat_arb_quotes.jsonl.
- Stat-arb finding (not fixed): 10-16 session wrote no stat-arb row. Kalman q_beta 1e-4/step with x = ln(DOGE) ~ -1.6 floors sqrt(S) ~160+ bps (1 m bars p50 261 bps) -> |z| p99 < 0.7, never 2.1; OU fit on innovations rarely stationary. Grid q 1e-7..1e-10 on SOL/DOGE 1 m 2026-07..09 (DEV Jul-Aug / HOLDOUT Sep, 12 bps cost): 0-25 trades, HOLDOUT net -44.5..+29.8 bps, no robust edge -> params unchanged, no retune on one day.
- Per-tick: absorption 1.8 us, chop_exit_confirmed 0.5 us.
- Checks: python -m pytest -q 1590 passed (1578 before, +12 tests/test_absorption_and_maker_exit_2026_10_16.py); two assertions updated to the new intended behaviour (router CHOP exit no longer immediate; burst time-stop taker test pinned to burst_time_exit='taker'). No restart, no order, ledgers untouched, burst lane stays SHADOW.
- Rollback: Profile demo burst_time_exit='taker', burst_absorb_gate=False; capital_router CHOP_EXIT_HOLD_S=0 and BURST_COOLDOWN_S=0 (CHOP_EXIT_KER=CHOP_KER) approximately restore the old router (unknown KER now never confirms an exit); quote_every_s=inf disables quotes.

## 2026-10-16 (c) RFC-2026-Q4.3 afternoon log 13:54-16:18: CHOP hibernation, VPIN veto, funding carry telemetry
- Ledger truth (micro_trades_shadow.jsonl 13:57:42-16:19:56, new 10-16 (b) policy live): 75 BURST shadow trades (RFC ~70), 23 wins, net -327.0 bps / -2.44 U virtual (RFC ~-220), 75/75 MAKER-EXIT, 0 SL / 0 TIME-CAP, worst -21.9; gross DOGE +0.7 / SOL -0.7 bps -> the loss is the 4 bps maker round trip. Morning -9.4 vs afternoon -4.4 bps/trade is confounded (policy AND regime changed); 2.4 h, n 75 is not evidence of the exit change. 73/75 entries came after the 14:01:22 CHOP switch.
- Replay (snapshot_2026-10-16q42/exp_chop_vpin.py, SOL 09-05/06/08, current demo policy, n 1417): -6.50 [-6.94,-6.08]; entry KER120 < 0.25 -6.66 [-7.18,-6.11] vs >= 0.25 -6.28 [-6.99,-5.57] (overlap: negative in every regime, not CHOP-specific); within 60 s of VPIN > 0.8 -7.08 (n 118); gates: CHOP -6.38 (n 839), VPIN any side 60 s -6.43, engine against-flow 30 s -6.43 -> fewer trades, no per-trade gain. "100 % of negative drift prevented" holds only because the lane trades ~0 in CHOP.
- Code: burst_lane._gate (router CHOP or KER < 0.25 -> 'BURST OBSERVE ... entry hibernated' once per episode; unknown KER does not gate; burst_vpin_veto_s after the engine's VPIN TOXIC mark, either side; open positions untouched). AsymmetricToxicityGuard is still not wired anywhere - the engine's live mark_toxic state is used instead. Profile burst_chop_gate / burst_vpin_veto_s off by default, demo on (shadow lane only).
- Funding carry: funding_executor.quotes_from / fetch_quotes (Binance premiumIndex + Hyperliquid metaAndAssetCtxs, public, read-only) / carry_view; Runner.funding_loop every 300 s (Profile funding_shadow, demo only) -> ShadowCarryBook -> telemetry 'funding_carry' (errors kept with the stale timestamp) -> web_ui T6 card. Virtual book, no order path.
- Window carry (carry_window_2026-10-16.py/.json, real BN + HL prints 06:54-09:18 UTC: 1 BN + 3 HL settlements): RFC rule (>= 2.5 bps/8h, basis >= +1.5) selected 0/18 coins (spreads 0.0-1.36 bps/8h) -> 0.00 U. Hindsight best LINK +0.43 bps = 0.025 U per 573.91 U leg vs 15 bps round trip; sum over 18 coins -4.36 bps. Carry did not pay in this chop window.
- Per-tick: _gate 0.5 us.
- Checks: python -m pytest -q 1600 passed (1590 before, +10 tests/test_chop_burst_gating_2026_10_16.py). UI: T6 renders full width, no horizontal scroll (served from disk by the running UI server). Running engine (pid started 13:54) not restarted: new code takes effect only on the user's next restart. No order, ledgers untouched.
- Rollback: demo burst_chop_gate=False, burst_vpin_veto_s=0, funding_shadow=False.

## 2026-10-16 (d) RFC-2026-Q4.4 evening log 16:51-20:52: hibernation hysteresis, carry sizing, SW1 + VPIN fade replays
- Ledger truth: 5 BURST shadow rows 16:57-17:03 (net -65.6 bps incl. DOGE SL -37.4), then 0 rows to 20:52; engine_trades / trend_trades / intraday shadow 0 rows; stat_arb_quotes 248 rows, all 'insufficient_cointegration_or_spread_vol', |z| max 0.01. The "3.5 h paralysis" is the router staying CHOP (telemetry: every symbol KER < 0.25) and the burst lane hibernating as designed - virtual lane, no money at stake.
- Bug 1 (spam + leak): hibernation was re-armed whenever KER ticked over 0.25 or the VPIN reason replaced the CHOP one (30 OBSERVE lines kept in telemetry within a minute) and 4 entries slipped through 17:01-17:03. Fix: burst_lane._gate state with the router's hysteresis (start router CHOP or KER < 0.25; wake only router != CHOP and KER >= 0.40; unknown KER never wakes), one OBSERVE + one WAKE line per episode.
- Bug 2: Runner.funding_loop built the carry book on the first pass before the account sync -> 116.67 U/leg (mock 1000) for the whole session. Fix: book kept on the Runner, capital = synced wallet every pass. Added ShadowCarryBook.legs (net delta = 0 by construction, reported), accrue (funding integrated at the last spread - ESTIMATE), cost per OPEN / ROTATE (15 bps x N); T6 shows delta and est. funding - cost. No allocation logic: the book already sizes from the wallet; "15-35 % APR risk-free" is not supported (replay HOLDOUT +14.6 bps / 28 d ~ 1.9 %/yr; 13:54-16:18 window: 0 eligible coins).
- SW1 (tools/swing_replay_sw1.py, pre-registered and committed 00b16ed before the first run; RFC rule 15m EMA20 trend + 1H MACD hist + TSMOM 24 h + VR > 1.5; TP 80 / SL 30 and TP 200 / SL 80; taker or post-only entry; BTC/SOL/DOGE 1m 2025-01..2026-09): gross -1.75..+3.23 bps/trade (RFC claimed +80..+250), 9-17 trades/day; DEV-best SW1_MK_B200 HOLDOUT n 2505 -5.75 [-11.33,-0.29], per symbol BTC -6.29 / SOL -4.53 / DOGE -6.45 -> REJECT; nothing wired. JSON: snapshot_2026-10-16q42/sw1_replay.json.
- VPIN climax fade (exp_vpin_fade.py, SOL 3 d, pre-registered in the header): VPIN >= 0.85 then -0.15 within 60 s, post-only against the flow: n 69 net -5.75 [-8.04,-3.56] win 0.23 (RFC claimed > 75 %); control with the flow -7.80; >= 0.95: -7.35 (n 33). Better than following the flow, still negative -> not wired (AsymmetricToxicityGuard stays library-only).
- Checks: python -m pytest -q 1608 passed (1600 before, +8 tests/test_break_doom_loop_2026_10_16.py). Running engine not restarted. No order, ledgers untouched.
- Rollback: revert burst_lane._gate to 10-16 (b) (or burst_chop_gate=False); funding_shadow=False.
