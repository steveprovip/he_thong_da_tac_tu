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
