# checks.md — verification log (newest first)

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
