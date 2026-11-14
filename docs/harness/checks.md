# checks.md — verification log (newest first)

## 2026-11-14 (g) — NDG-1 two-sided quoting under the ATLAS low-volatility gate (pre-registered) + recorder watchdog
| # | Command | Result |
|---|---|---|
| 1 | plan hash before any run: `sha256sum docs/research_upgrade/plan_non_directional_grid_2026_11.md` | `441c3d31...b776` = trial_log row `ndg1-prereg` (2026-11-14T08:51:56Z); no `data/non_directional_grid_study.json` existed |
| 2 | `python -m pytest -q tests/test_non_directional_grid.py` (before the first run) | 22 passed (hand cases from the plan text, scalar reference on 8 000 random paths, martingale gross -0.017 / -0.019 sigma per window, OU gross +0.97 sigma, z 314) |
| 3 | `python -m research.non_directional_grid_study` (88 s; run twice, JSON identical apart from the timer) | T1 = ATLAS k1 BAND: TRAIN -4.63 +- 0.29 (n 22 695), HOLDOUT -4.38 +- 0.19 bps (n 32 486, 16 338 with a fill, gross -0.12 +- 0.06, LCB95 -4.69, DSR 0.00), FRESH -4.25 +- 0.43 (n 6 070) = NO EDGE; 216 of 216 cell x fee schedule x partition means negative |
| 4 | same run, regimes | VR(15) HOLDOUT LOW 0.940 +- 0.022 / MID 0.931 +- 0.019 / HIGH 0.882 +- 0.057; T1 net LOW -4.38 / MID -3.96 / HIGH -4.53 / ALL -4.19; LOW share 41.3 % HOLDOUT, 49.0 % FRESH |
| 5 | `python tools/watchdog_recorder.py start --out <scratchpad> --child-cmd '["python","-c","import time; time.sleep(600)"]' --interval 2` then `taskkill /F` on the child, `ensure`, `stop` | launched via WMI (this session's job object refuses breakaway), heartbeat `job: {"in_job": false}`; child killed -> restarted after 5 s (restarts 1, last exit code 1); `ensure` on a live supervisor: no-op; `stop` exit 2 (STOPPED); `ensure` after a stop: stays stopped |
| 6 | live smoke, read-only public streams, scratch folder: `python tools/watchdog_recorder.py start --mode light --out <scratchpad>`, hard kill of the recorder after 31 s, `stop` | BEFORE the recorder fix: restart appended a gzip member behind the cut one, all 3 hour files unreadable at the join (invalid block type / EOFError). AFTER (`repair_gzip` + 5 s sync flush): restart in 5 s, heartbeat steps 10.10 / 10.08 / 10.06 s, stop "clean, exit 0", 3 hour files complete, 731-1 292 lines each over 68 s, largest gap 10.9-11.1 s across the kill |
| 7 | `python -m pytest -q tests/test_watchdog_recorder.py tests/test_liquidation_recorder.py` | 36 passed (19 watchdog incl. one real detached launch, 17 recorder incl. 3 new: stop file, repair before append, 5 s flush) |
| 8 | `python -m pytest -q` (final) | 2400 passed, 0 failed, 269.0 s (2356 + 44 new) |
| 9 | `check_handoff.py docs/harness/session-handoff.md --entry "Handoff 6C — 2026-11-14 (g)"` | exit 0, 6 / 6 sections |

Not run: Demo / live engine; the recorder on `data/recordings` (owner decision: 14.7 GB free on F:); the scheduled task for `ensure`. Not measured: touch fills and queue position of a resting quote (klines carry no depth); a supervisor surviving the end of THIS agent session (the launch leaves the session's job object, which is the evidence available from inside the session).

## 2026-11-14 (f) — ATLAS density forecasters: L2 loop, kline loop (pre-registered), tail trigger, carry + overlay
| # | Command | Result |
|---|---|---|
| 1 | `python tools/rec_to_l2_touch.py --symbol DOGEUSDT` (12 s) | 11 hourly depth files (last one truncated) -> `data/l2/DOGEUSDT/20261110.jsonl`, 321 350 quote lines, 37 235 s, one gap-free segment |
| 2 | `python scripts/autoresearch_agent_swarm.py --data "data/l2/<SYM>/*.jsonl" --rounds 30 --seed 20261114 ...` x 4 (BTC, SOL, DOGE, pooled) | kept 14 / 12 / 15 / 12 of 30; mean pinball bps train 2.852 -> 2.770, 4.163 -> 4.070, 4.971 -> 4.923, 4.335 -> 4.299; holdout 4.192 -> 4.207, 3.965 -> 3.956, 4.524 -> 4.477, 4.911 -> 4.837 |
| 3 | `python -m research.atlas_density_study --l2` | ensemble - baseline on the holdouts: -0.087 +- 0.028 (3 windows), -0.040 +- 0.027 (3), -0.064 +- 0.061 (12), -0.121 +- 0.057 (17): descriptive only |
| 4 | plan freeze (session script) | SHA-256 `d9943d77...e3e5`, 2026-11-14T08:02:49Z, before any kline outcome |
| 5 | `python -m pytest -q tests/test_atlas_density.py` | 23 passed |
| 6 | `python -m research.atlas_density_study` (424 s; second run after the reporting fixes, cells and losses bit-identical to the first) | 24 / 60 kept; HOLDOUT pinball E 6.3657 / B0 6.4698 / REF 6.2860 bps; E - B0 -0.1041 +- 0.0089; E - REF +0.0797 +- 0.0088; location -0.00055 +- 0.00065; T1 P85 x MT: 0 decisions in 204 477 points = NO OPPORTUNITY SET; T2 TAIL_0.1 x MT: HOLDOUT -9.31 +- 0.69 bps (n 6 687), FRESH -11.61 +- 2.32 = NO EDGE |
| 7 | same run, carry + overlay | carry n 165 +16.01 bps / 28 d (2.09 % / yr); gate at 5 U: EQUITY 5.0000 < TWO_LEG_MIN 10.00; 5 U after 365 d: 5.000 (as gated), 5.105 (hypothetical 5 U per leg); overlay T2 -0.0775 U / day, stops after a median 52 days; P(+10 U) 0 |
| 8 | `python -m pytest -q` (final) | 2356 passed, 0 failed, 251.4 s (2333 + 23 new) |
| 9 | `check_handoff.py docs/harness/session-handoff.md --entry "Handoff 6C — 2026-11-14 (f)"` | exit 0, 6 / 6 sections |

Not run: Demo / live engine, recorder, any network call. Not measured: depth-based signals at 1 s beyond 4.6-10.3 h of recordings (the 2026-11-10 recorder died after 10.4 h); a queue model for the maker fill; bracket exits on the density signals (not pre-registered).

## 2026-11-14 (e) — MLS-2 confirmation read (pre-registered), top-40 frequency, profit-funded Kelly (records written after the interrupted session)
| # | Command | Result |
|---|---|---|
| 1 | `python tools/fetch_klines_fresh.py --symbols <11> --start 2026-10-01` (prior session) | `data/klines/fresh/`: 11 symbols x (2026-10 monthly + 2026-11-01..12 daily) zips, SHA-256 checked; common data end 2026-11-13 00:00 UTC (43 days) |
| 2 | `python -m research.macro_sweep_validation` (prior session, 103 s; numbers re-read from `data/macro_sweep_validation.json`) | NOT CONFIRMED: U = F + X n 117, -6.89 +- 11.67 bps (LCB95 -26.10, UCB95 +12.31), win 27.4 %, PF 0.877, vs placebo -5.63 (LCB95 -25.21), DSR 0.0025; F n 32 -2.95 +- 27.11; X n 85 -8.38 +- 12.62; pooled HOLDOUT + F + X n 214 +1.79 +- 9.17 (LCB95 -13.29) |
| 3 | same run, frequency / Kelly / trajectory | 0.312 / 0.445 / 0.679 trades per day (11 symbols / top 20 measured / top 40 extrapolated); Kelly on the lower bound -0.0499 -> f 0; 5 U after 365 d median 5.22 / 5.37 / 5.58 U, P(+10 U) 0 |
| 4 | `python -m pytest -q` (2026-11-14, HEAD `e7b7291`, before any edit of this session) | 2333 passed, 0 failed, 248.0 s (2316 + 17 in `tests/test_macro_sweep_validation.py`) |
| 5 | `check_handoff.py docs/harness/session-handoff.md --entry "Handoff 6C — 2026-11-14 (e)"` | exit 0, 6 / 6 sections |

Not run again: the validation itself (its inputs and code are unchanged since the stored JSON was written at 10:52; a re-run would be the same read).

## 2026-11-14 (d) — ledger audit, FLS-1 fee re-pricing, MLS-1 macro liquidity sweep (pre-registered), funding-extreme scan
| # | Command | Result |
|---|---|---|
| 1 | `rm -rf data/recordings/historical/cm`; `df -h /f` | F: free 12 -> 14 GB; the 7 `fetch_*.log` files stay |
| 2 | ledger audit (session scratch script over `data/engine_orders.jsonl` + `data/engine_trades.jsonl`; totals match `tools/autopsy_2026.py`) | 131 AUTO trades: gross +0.62 +- 1.15, fee 4.57, net -3.95 +- 1.16 bps; GTX fill rate 21.1 % (93 / 441), fill wait p50 2.64 s; ack p50 146 ms / p95 753 ms; win rate 37.4 % vs break-even 59.9 % |
| 3 | exact re-pricing of the 336 trades in `data/fls1_cm_boundary.json`: net' = net + (2 - m)(1 + [TP]) + [not TP](5 - t) | 2 / 5 -3.79; MEXC web 0 / 2 +0.95 +- 3.12; -1.5 / 4.5 +1.00 +- 3.19 (LCB95 -4.24); 0 / 0 +2.43 +- 3.09: no flip |
| 4 | `python -m pytest -q tests/test_macro_sweep.py` | 35 passed |
| 5 | `python -m research.macro_sweep_study` (118 s) | ACCEPTANCE FAIL: chosen ABS_STRUCT HOLDOUT +6.79 +- 13.04 bps (n 79, LCB95 -14.66, vs placebo LCB -5.08, DSR 0.046); search final HOLDOUT +12.25 +- 12.48 (n 97, LCB95 -8.28, vs placebo LCB -0.73, DSR 0.109) |
| 6 | `python -m research.macro_sweep_study --exit-grid` | TRAIN: plain bracket +18.70, best trail / shield cell +16.11: no HOLDOUT read |
| 7 | funding scan (session scratch script, `tools/funding_spread_replay.simulate`, cache only) | BN vs HL spread >= 100 % APR in 1 of 35 487 coin-8h windows (ZEC 2025), 0 in 2026 |
| 8 | `python -m pytest -q` (final) | 2316 passed, 0 failed, 245.1 s (2281 + 35 new) |

Not run: Demo / live engine, recorder, any network call or download. Not measured: OFI L10 (no depth history; taker delta
substituted), fills and queue position on any venue other than the Binance klines, months after 2026-09 (not on disk).

## 2026-11-14 (c) — FLS-1-CM on the COIN-M liquidation archive + Polymarket benchmark
| # | Command | Result |
|---|---|---|
| 1 | S3 listing of `data.binance.vision` `data/futures/um/daily/` and `cm/daily/` | USD-M has no `liquidationSnapshot`; COIN-M has it 2023-06-25 .. 2024-10-14 (+ aggTrades, bookTicker) |
| 2 | `python -I tools/fetch_cm_liquidation_tape.py --liq-only` (5 symbols) + `--no-book --days ...` | SHA-256 checked; 53 398 BTC / 28 092 ETH / 14 540 SOL / 12 016 DOGE / 11 438 XRP liquidation rows; aggTrades 1 040 symbol-days, 24 hour files each, 1.9 GB; no download error (the background jobs' "exit 1" was a `tail -1 a b` syntax error) |
| 3 | `python -m pytest -q tests/test_fls1_cm_history.py tests/test_flash_liquidation_sniper.py tests/test_pm15_benchmark.py` | 11 + 16 + 9 passed |
| 4 | `python -m research.flash_liquidation_study --root data/recordings/historical/cm --symbols ... --history-split 2024-04-01 --rounds 30` | primary FAIL (chosen k0.5 R2 G0 HOLDOUT -10.83 +- 1.24 bps, n 637); search final HOLDOUT -8.38 +- 1.94 (n 435), DSR 1e-5; ~19 min |
| 5 | TRAIN-only sensitivity (11 configs) + one HOLDOUT read of the >= $150k E0 boundary | boundary -3.79 +- 3.14 bps (n 336), vs placebo +5.63 (LCB +0.37), DSR 0.0006 (50 trials) |
| 6 | `python -m research.pm15_benchmark` (7.5 s) | Brier 0.127 vs 0.25; impulse hit = model p; 24 real signals -0.010 +- 0.092 USDC/share |
| 7 | `python -m pytest -q tests/` | 2281 passed, 0 failed, 247.4 s (2261 + 20 new) |

Not run: any USD-M liquidation study (no archive), Polymarket book history (not downloaded), live recorder.

## 2026-11-13 (b) — owner locks (net-EV gate on, OFI >= 0.65) + liquidation recorder + FLS-1 pre-registration
| # | Command | Result |
|---|---|---|
| 1 | `python -m pytest -q tests/test_sniper_runner_carry_2026_11_10.py tests/test_micro_gate_liberation_and_knife_guard_2026_11_11.py` | 23 passed (launcher pin without `MICRO_OFI_MIN`; env refuses 0.45) |
| 2 | `python -m pytest -q tests/test_liquidation_recorder.py tests/test_fast_research_loop.py tests/test_fast_lane.py tests/test_upgrade_2026_10.py` | 99 passed (14 new) |
| 3 | `python -m pytest -q tests/test_flash_liquidation_sniper.py tests/test_liquidation_recorder.py` | 30 passed (16 + 14 new) |
| 4 | live smoke, read-only public streams: `python tools/record_market.py --liq --symbols BTCUSDT,ETHUSDT,SOLUSDT,DOGEUSDT,XRPUSDT,1000PEPEUSDT --duration 60 --stats-every 20 --out <scratchpad>` | 60 s: bookTicker 146 337, aggTrade 3 571, forceOrder 16 (all symbols; the one BTC event had its touch, age 0.0 ms); loop cost per frame p50 3.9 / p99 18.4 / max 555 us; receive -> written lag p99 9-10 ms; queue 0; write errors 0; 1.83 MB/min gz = ~2.6 GB/day (BTC 0.59 + ETH 0.73 MB/min) |
| 5 | `python -m pytest -q tests/` (final) | 2261 passed, 0 failed, 243.6 s (2231 + 30 new) |

Not run: the 21-day recording (2.6 GB/day x 21 = ~55 GB; free: F: 14 GB, C: 20 GB) and any FLS-1 outcome (0 days of tape).

## 2026-11-13 — alpha re-engineering directive
| # | Command | Result |
|---|---|---|
| 1 | `python -m pytest -q tests/` (first full run after the gate edits, before the breathing fix) | 2173 passed, 2 failed: `tests/test_anti_churn.py::test_500k_noise_does_not_cut_and_trade_reaches_tp1` (HEAD `6a621f8` made the 25 k knife shield apply to every mode) and `tests/test_sniper_runner_carry_2026_11_10.py::test_the_micro_launcher_arms_shadow_harvest_the_runner_and_a_15_u_bankroll_on_demo_only` (`6a621f8` added two `run.bat` lines without updating the pin; the new `MICRO_NET_EV_GATE` line too). Both traced by code reading, then fixed / re-pinned. |
| 2 | `python -m pytest -q tests/test_smc_fibo.py tests/test_net_expectancy.py tests/test_session_breaker_kelly.py` | 49 passed |
| 3 | `python -m pytest -q tests/test_autopsy_2026.py` | 7 passed |
| 4 | `python -m pytest -q tests/test_micro_dual.py tests/test_sniper_runner_carry_2026_11_10.py tests/test_anti_churn.py` | 66 passed |
| 5 | `python -m pytest -q tests/` (final) | 2231 passed, 0 failed, 248.2 s (2175 collected before + 56 new); afterwards the 5 files that read run.bat: 202 passed |
| 6 | `python tools/autopsy_2026.py` | 131 AUTO trades: net -4.0924 U, gross +1.1973 U, fees 5.2897 U; 94 rows with mainnet tape; 747 deduped refusals |
| 7 | `python -m research.smc_fibo_backtest` (83 s) | ACCEPTANCE pass false (chosen R2_BEoff: LCB95 -19.5, vs placebo LCB95 -10.6, 1/11 symbols) |
| 8 | `python tools/micro_growth_sim.py` | +10 U/h at 0.05 U risk, R 1.9: 105 trades/h at a 100 % win rate |
| 9 | gate on the current evidence (real micro rows + stored virtual rows) | `UNPROVEN`: 21 real micro trades -5.51 +- 3.37 bps, 0 virtual |

Not run: Demo / live engine, recorder, any network call. Not measured: OFI L10 part of the SMC-Fibo idea (klines carry no depth),
Polymarket 15 m arb and liquidation sniping (no data on disk).
