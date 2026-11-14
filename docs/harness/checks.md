# checks.md — verification log (newest first)

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
