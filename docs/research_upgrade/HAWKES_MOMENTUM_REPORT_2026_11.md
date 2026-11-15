# HWK-1 — Hawkes volume-shock momentum on top movers, 10 USDT base (report, 2026-11-15)

Plan, frozen before any 1 m kline of the study universe was downloaded (SHA-256
`59725a43e11c032814ffe7936de08d42164990c18e19964e563b96ad26d393c2`): `docs/research_upgrade/plan_hawkes_momentum_10u_2026_11.md`.
Code: `research/hawkes_momentum_study.py`, `core/position_sizer.profit_stack_risk`, `tests/test_hawkes_momentum.py` (36 tests).
Result files (git-ignored): `data/hawkes_momentum_study.json`, `data/hawkes/` (daily bars, 23 962 day zips, matrix, logs).
Offline research on public data. No order, no engine start, no live, no cap or gate changed, nothing wired.

## 0. Verdicts

| Item | Result |
|---|---|
| T1, TRAIN-best of 12 pre-registered cells (`BAR`, 600 / 240 bps, direction BOTH) | **NO EDGE.** HOLDOUT +4.74 +- 8.29 bps net per trade (n 2 163, LCB95 -8.90, deflated Sharpe 0.14 over 12 trials); FRESH -42.87 +- 17.10 (n 454) |
| T2, 30-round TRAIN-only search final (theta 2, W 30, H 120, 600 / 240) | **NO EDGE.** HOLDOUT +4.59 +- 7.64 (n 2 444, LCB95 -7.99, deflated Sharpe 0.06 over 38 trials); FRESH -24.86 +- 17.82 (n 507). No searched configuration has a TRAIN lower bound above zero |
| Does the Hawkes intensity forecast trade arrivals? | Yes against a flat rate (Poisson deviance -79 %, MAE -51 % on HOLDOUT). No against an EWMA of the same half-life (deviance 1.3 % worse). It is a volatility-clustering model |
| Does the trigger forecast direction? | No. Favourable and adverse excursions are the same: 600 bps is reached within 4 h by 59.7 % of triggers in favour and 59.0 % against. The same entries at random minutes net +3.23 +- 4.37 against +4.74 +- 8.29 for the trigger |
| "lambda > 3 mu" as a shock detector | It is the normal state: the fitted branching ratio has a median of 0.92-0.93 and the rule is true in 90-94 % of all minutes |
| Compounding 10 U at 1.0 U risk | Flat 1 U: median 0.84 U after 30 days, 76 % of paths ruined. Stacking 50 % of profit: 99 % ruined. With the ratchet: median 9.93 U, no path of 2 000 ends above 10.12 U |
| +20 U per hour from 10 U | Not reachable. It needs 8.1 net winners per hour at the base size; one account sees 0.30 triggers per hour, so a 100 % win rate would pay 0.75 U per hour. Measured: -0.007 U per hour |
| The standing 0.05 U cap | Cannot post this bracket at all: the 5 U minimum order loses 0.12 U at a 240 bps stop |

## 1. Data

| Step | Result |
|---|---|
| Symbols | 652 USDT COIN perpetuals of the 2026-11-14 exchangeInfo snapshot (TRADING and SETTLING), 7 exclusions; one (GAIBUSDT) served no daily bars |
| Universe | top 5 gainers and top 5 losers of UTC day D-1 (volume >= 20 M USDT, listed >= 14 days), known at 00:00 of D |
| Days / pairs | 1 017 days (TRAIN 700, HOLDOUT 273, FRESH 44), 10 170 pairs, 628 symbols |
| 1 m klines | 23 974 day files (D-1, D, D+1), 1.33 GB, each verified against the archive's SHA-256; 12 not published (10 of them 2026-11-14) |
| Usable pairs | 10 169 (1 dropped, no file); 11 without D+1 (their open trades close at the last bar) |
| Funding | cached prints 2024-12-20 onwards plus a tail fetch: known for 100 % of HOLDOUT and FRESH trades, 60 % of TRAIN (unknown before 2024-12-20: counted as 0 and flagged) |

What the universe looks like (computed after the run, descriptive):

| | TRAIN | HOLDOUT | FRESH |
|---|---|---|---|
| Median absolute 1-minute move on day D | 13.2 bps | 18.6 bps | 18.3 bps |
| 99th percentile of the 1-minute move | 130 bps | 221 bps | 246 bps |
| Median high / low range of day D | 15.4 % | 27.4 % | 27.9 % |
| Median absolute return of D-1 | 13.8 % | 24.3 % | 23.7 % |
| Tick-size proxy (smallest price step), median / 90th percentile | 1.2 / 5.5 bps | 1.4 / 5.8 bps | 1.4 / 5.1 bps |

## 2. The binned Hawkes intensity (Phase 1; T3, no verdict)

lambda_s = mu + alpha x A_s with A_s = e^-beta (A_(s-1) + N_(s-1)) on the trade count of each 1 m kline, half-life 5
minutes, fitted by conditional least squares on day D-1 only and applied to day D.

| | TRAIN | HOLDOUT | FRESH |
|---|---|---|---|
| Pairs fitted (none skipped) | 6 999 | 2 730 | 440 |
| Branching ratio n, median (10th-90th percentile) | 0.915 (0.81-0.97) | 0.929 (0.84-0.97) | 0.932 (0.86-0.97) |
| Pair-days with n >= 2/3 | 99.2 % | 99.8 % | 99.8 % |
| Minutes with lambda >= 3 mu (the literal rule) | 93.5 % | 90.2 % | 91.1 % |
| Minutes with lambda >= 3 x yesterday's mean rate (`BAR`) | 3.9 % | 6.0 % | 7.8 % |
| One-step MAE, Hawkes / LAST / MEAN / EWMA (in units of the mean rate) | 0.428 / 0.456 / 0.771 / 0.426 | 0.472 / 0.498 / 0.962 / 0.467 | 0.553 / 0.569 / 1.092 / 0.550 |
| Poisson deviance skill against MEAN | +73.2 % | +79.2 % | +80.4 % |
| against LAST (the previous minute) | +11.6 % | +9.8 % | +5.0 % |
| against an EWMA of the same half-life | -1.0 % | -1.3 % | -1.0 % |

The intensity forecasts the next minute's activity far better than a constant and slightly better than the last
minute. A two-parameter EWMA does the same job. With n near 0.92 the mean intensity is about 12 mu, so "lambda > 3 mu"
selects almost every minute; the usable shock definition is the ratio to the mean rate.

What follows a trigger (T1 cell, HOLDOUT, gross bps, signed by the trade side):

| Horizon after entry | Trigger: mean (share > 0) | mean absolute | Random time: mean (share > 0) | mean absolute |
|---|---|---|---|---|
| 5 min | +15.2 +- 8.2 (47.5 %) | 231 | -1.1 +- 4.6 (45.4 %) | 109 |
| 15 min | +29.9 +- 12.8 (49.4 %) | 374 | +5.9 +- 9.3 (48.0 %) | 191 |
| 60 min | +33.1 +- 21.8 (48.8 %) | 651 | +20.6 +- 16.8 (49.8 %) | 403 |
| 240 min | +84.4 +- 40.5 (49.9 %) | 1 182 | +73.6 +- 38.6 (52.7 %) | 941 |

| Excursion within 240 min | Trigger, in favour / against | Random time, in favour / against |
|---|---|---|
| 250 bps | 80.4 % / 80.9 % | 73.6 % / 70.9 % |
| 400 bps | 70.6 % / 71.0 % | 60.4 % / 57.1 % |
| 600 bps | 59.7 % / 59.0 % | 46.9 % / 42.7 % |
| Median | +801 / -777 bps | +541 / -489 bps |

The 250-600 bps waves exist and the trigger does select larger moves than a random minute (the absolute move doubles
at 5 minutes). They are symmetric: the move goes against the entry as often as with it, and fewer than half of the
signed returns are positive at every horizon. The trigger fires late: the median 15-minute move already made at the
decision minute is 392 bps (quartiles 178 and 756). On FRESH the signed mean is negative at 5 and 15 minutes
(-16.1 +- 18.7, -20.8 +- 30.1).

## 3. T1: the 12 pre-registered cells (Phase 2)

Trigger: intensity ratio >= 3, last-hour volume >= 3x the hourly mean of the previous 24 h, side = the 15-minute price
move (BOTH = confirmed by the taker-flow sign), taker entry at the next open, target / stop bracket, 240-minute time
exit, 60-minute cooldown. Costs: taker 5 + slip 3 in, maker 2 at the target, taker 5 + slip 3 at the stop or the
time exit, funding.

TRAIN (2024-02-01..2025-12-31), net bps per trade +- SE clustered by day:

| Reference | Target / stop | BOTH | PRICE |
|---|---|---|---|
| MU | 300 / 120 | -9.47 +- 2.55 (n 5 630) | -11.78 +- 2.53 (n 5 726) |
| MU | 400 / 160 | -4.42 +- 3.43 (n 5 379) | -7.62 +- 3.38 (n 5 470) |
| MU | 600 / 240 | -3.78 +- 5.01 (n 4 971) | -4.42 +- 4.97 (n 5 058) |
| BAR | 300 / 120 | -9.10 +- 2.96 (n 4 239) | -12.39 +- 2.91 (n 4 329) |
| BAR | 400 / 160 | -4.03 +- 3.99 (n 4 081) | -7.64 +- 3.92 (n 4 162) |
| BAR | 600 / 240 | **+0.51 +- 5.89 (n 3 799)** | -1.38 +- 5.82 (n 3 878) |

11 of 12 cells are negative on TRAIN; the chosen cell is +0.002 R. Its one out-of-sample read:

| `BAR` 600 / 240 BOTH | TRAIN | HOLDOUT | FRESH |
|---|---|---|---|
| Trades (days) | 3 799 | 2 163 (271) | 454 (43) |
| Net bps per trade | +0.51 +- 5.89 | +4.74 +- 8.29 | -42.87 +- 17.10 |
| One-sided 95 % bounds | -9.18 | -8.90 .. +18.38 | -70.99 |
| Net R (net / stop) | +0.002 | +0.020 +- 0.035 | -0.179 +- 0.071 |
| Gross / fees and slip / funding | +14.62 / 14.53 / +0.42 | +18.80 / 14.22 / +0.16 | -27.72 / 14.55 / -0.60 |
| Exits: stop / target / time | 62.3 / 24.6 / 13.1 % | 67.8 / 29.7 / 2.5 % | 73.8 / 24.2 / 2.0 % |
| Deflated Sharpe (12 trials) | | 0.136 | 0.00005 |
| FADE (side flipped) | | -10.74 +- 8.12 | -29.94 +- 17.46 |
| RANDOM side (5 seeds) | | -7.13 +- 4.85 | -30.34 +- 10.33 |
| RANDOM TIME, side by the 15-minute move | | +3.23 +- 4.37 (n 8 940) | -12.02 +- 8.81 |
| Slip 1.5 / slip 10 / target as taker / zero fees | | +7.29 / -7.19 / +2.96 / +18.96 | -40.23 / -55.17 / -44.32 / -28.32 |

Checks of the plan: HOLDOUT n >= 100 yes; lower bound > 0 no; deflated Sharpe >= 0.95 no; FRESH mean > 0 no.

- A driftless price reaches +600 before -240 with probability 240 / 840 = 28.6 %. The target was reached by 29.7 %
  of HOLDOUT trades and 24.2 % of FRESH trades. The break-even share with 16 bps of round-trip cost is 30.5 %.
- The median hold is 9 minutes and a tenth of the trades end in the entry minute: a 240 bps stop is about 13 median
  minutes of movement on these symbols.
- HOLDOUT gross is positive (+18.8 bps at zero fees, 2.3 SE) and so is TRAIN (+14.6). The same symbols entered at
  random minutes in the direction of the last 15 minutes gross about the same (+3.23 net is about +17.6 gross). The
  Hawkes and volume conditions add nothing to it, the cost removes it, and FRESH is negative before any fee.
- By side and tag on HOLDOUT: G +3.3 +- 9.9, L +8.2 +- 16.2, long +7.2 +- 11.2, short +1.8 +- 12.3. On TRAIN: 2024
  -10.1 +- 8.8, 2025 +8.0 +- 8.0, G +8.5 +- 8.1, L -11.3 +- 8.9. No stable sub-group.

## 4. T2: 30-round TRAIN-only search (Phase 2)

One parameter per round over the registered grid (half-life, theta, reference, kappa, direction window and mode, side
filter, target, R:R, hold, cooldown, ratchet arm and distance), kept when the TRAIN lower bound of the mean net R
rises. 26 distinct configurations were scored (5 draws repeated one already scored); 9 have a positive TRAIN mean; 3
moves were kept: hold 240 -> 120 (round 14), window 15 -> 30 (round 21), theta 3 -> 2 (round 30).

| Final: `BAR`, hl 5, theta 2, kappa 3, W 30, BOTH, 600 / 240, H 120 | TRAIN | HOLDOUT | FRESH |
|---|---|---|---|
| Trades | 4 507 | 2 444 (273 days) | 507 (44 days) |
| Net bps per trade | +4.03 +- 5.15 | +4.59 +- 7.64 | -24.86 +- 17.82 |
| Lower 95 % bound | -4.44 | -7.99 | -54.17 |
| Net R | +0.017 (LCB -0.019) | +0.019 +- 0.032 | -0.104 +- 0.074 |
| Deflated Sharpe (38 trials) | | 0.057 | 0.0002 |
| FADE / RANDOM side / RANDOM TIME | | -18.11 +- 7.33 / -8.43 +- 4.35 / +4.20 +- 4.04 | -13.99 / -28.84 / -3.54 |
| Slip 10 / zero fees | | -7.46 / +18.91 +- 7.59 | -37.09 / -10.37 |

Rounds that the directive's design asked about, TRAIN mean net R: ratchet armed at 1.0 S -0.015, at 1.5 S -0.006 and
-0.014 (every trailing ratchet lowered the mean); R:R 4.0 -0.019 and -0.044; target 300 -0.076 and -0.119; target
500 -0.020; kappa 5 -0.010 and, from a later incumbent, +0.032 on 1 454 trades (lower bound -0.037, not kept);
half-life 10 +0.013; reference MU -0.016 and +0.004; side filter WITH -0.004. The best TRAIN lower bound of the
whole search is -0.019 R.

## 5. Compounding 10 U (Phase 3)

`core.position_sizer.profit_stack_risk` (opt-in, no engine call site): risk = 1.0 U, plus 50 % of the profit above
the principal after a win; with `ratchet` an account stop moves to the principal once the peak is >= 11 U and the
risk never exceeds equity minus that stop. Notional = risk / stop (41.7 U at 240 bps, 4.2x), capped at 20x equity and
1 % of the trigger's hourly volume; minimum order 5 U; a trade cannot lose more than the equity; a path stops below
1 U. 2 000 paths x 30 days, each day replaying one calendar day of HOLDOUT + FRESH as one account would trade it
(one position at a time: 7.31 trades per day for T1, mean net -5.63 bps; 8.10 for T2, +1.44 bps).

T1 book:

| Sizing | Median day 1 / 7 / 14 / 30 | P05 / P95 day 30 | Ruin | Principal ever impaired | Halted at the ratchet | P(pace +20 U/h) | P(one hour >= +20 U) |
|---|---|---|---|---|---|---|---|
| FLAT 1.0 U | 9.29 / 5.92 / 0.90 / 0.84 | 0.52 / 39.96 | 75.9 % | 97.5 % | | 0 | 0 |
| STACK 50 % | 8.93 / 0.91 / 0.81 / 0.77 | 0.52 / 0.98 | 98.7 % | 100 % | | 0 | 13.0 % |
| STACK 50 % + ratchet | 9.91 / 9.93 / 9.93 / 9.93 | 0.67 / 10.08 | 19.9 % | 82.0 % | 55.1 % | 0 | 3.3 % |
| STACK 70 % + ratchet | 9.84 / 9.87 / 9.87 / 9.87 | 0.64 / 10.02 | 21.2 % | 96.6 % | 71.6 % | 0 | 3.3 % |
| FLAT 0.05 U (standing cap) | 10 / 10 / 10 / 10 | 10 / 10 | 0 | 0 | | 0 | 0 |

T2 book: FLAT 1.0 U median 0.87 (ruin 68.1 %, P95 48.6); STACK 50 % median 0.77 (ruin 97.9 %); STACK 50 % + ratchet
median 9.93 (ruin 18.3 %, halted 56.5 %); STACK 70 % + ratchet median 9.85 (ruin 19.4 %, halted 76.1 %).

- FLAT 1.0 U: about 220 trades in 30 days, each +-1 U with a payoff spread of about 1.5 R, give a 30-day P&L spread of
  about 22 U on a 10 U account. With an expectancy of about zero, three paths in four reach the 1 U floor. The 16 %
  that double are the upper tail of the same coin flip.
- STACK 50 %: after a win the next trade stakes 1 U plus half the profit, and that trade loses 68-74 % of the time.
  Stacking raises ruin from 76 % to 99 %. One clock hour of +20 U occurs on 13 % of these paths; 98.7 % of them end
  ruined.
- Ratchet: 20 % of paths are ruined before the ratchet arms (the principal carries the 1 U base risk until the peak
  reaches 11 U). Of the rest, 55 % halt at 9.93 U (a stop-out costs 256 bps on a 240 bps risk, so the halt lands
  under the principal by the fees) and the remainder sit at 10.00-10.10 U, unable to post the 5 U minimum (which
  loses 0.12 U at this stop). No path of 2 000 ends above 10.12 U.
- FLAT 0.05 U: zero trades. 0.05 U at a 240 bps stop is a 2.1 U order, under the 5 U minimum.
- Addendum A1, not pre-registered ("consecutive wins" read as two in a row): STACK 50 % after 2 wins: median 0.78,
  ruin 95.1 % (T1) and 92.0 % (T2); with the ratchet median 9.96, P95 10.05, best path 107.6 U, ruin 21.1 %, halted
  70.0 %. On the T2 book 1 path of 2 000 (0.05 %) of the no-ratchet variant ends above the +20 U/h pace.
- Kelly on the HOLDOUT trades of T1: win 31.4 %, payoff 2.24 gives 0.83 % of equity (0.08 U on 10 U); at the lower
  95 % bound of the win rate it is negative, as it is on FRESH (-7.6 %) and on HOLDOUT + FRESH together (-0.6 %).
  The 1.0 U stake is 12x the most favourable of these.

Against +20 U per hour:

| Quantity | Value |
|---|---|
| Notional at 1.0 U risk and a 240 bps stop | 41.7 U (4.2x) |
| One net winner at 600 bps | +2.46 U |
| Net winners per hour needed | 8.1 |
| Triggers per hour one account can take (T1 / T2) | 0.30 / 0.34 |
| Pace at a 100 % win rate and the base size | 0.75 / 0.83 U per hour |
| Measured (one-slot mean net x frequency) | -0.007 / +0.002 U per hour |

## 6. Literature the directive cites (from memory, not re-read in this session)

- Hawkes processes in finance (Bacry, Mastromatteo and Muzy 2015 review; Filimonov and Sornette 2012; Hardiman,
  Bercot and Bouchaud 2013) describe how activity clusters: an event raises the rate of further events. The branching
  ratio near 0.9 measured here matches those papers. An unmarked intensity is a statement about how much will trade,
  not about which way. Direction needs a marked or bivariate model (buy and sell arrivals with cross-excitation),
  which needs the trade tape: the 1 m kline gives only the taker-buy share of each minute, used here as the CVD sign.
- Kronos (tokenised K-lines, autoregressive transformer pre-trained on more than 12 billion bars) reports forecasting
  metrics. It was not run here: the weights are a download, and a test would need the same frozen holdout with costs.
- CEX-DEX arbitrage is the payment liquidity providers make to whoever re-prices a pool first; it is taken by
  searchers integrated with block builders. A 10 U account has no latency position and pays gas and bridge fees per
  trade. Not tested.

## 7. Deviations, limits, not measured

- Deviations from the plan text: the funding cache ended at 2026-11-14 00:00, so prints after it were fetched for the
  26 symbols of the last FRESH days (public REST); 12 day files were not published; the stacking rule was registered
  with `min_streak` 1 and the two-win reading is addendum A1; the universe descriptives of section 1 and the Kelly
  figures were computed after the run.
- 1 m resolution: a stop or target fills at its level (the stop at the bar's open when the bar opens beyond it). On
  symbols whose 1-minute move is 2.2 % at the 99th percentile that is optimistic; 0.3 % of trades lose more than 300 bps here. No bid /
  ask exists for these symbol-days; slippage 3 bps per taker side is an assumption (a quarter of the pairs have a
  tick of 3 bps or more). At 10 bps of slippage HOLDOUT is -7.19.
- Survivorship: contracts removed from the venue before 2026-11-14 are missing.
- Sub-minute clustering and a marked (buy / sell) Hawkes model are not observable in klines.
- Stops wider than 240 bps were outside the registered grid (R:R >= 2.5, target <= 600) and were not tested; the
  plan closes this family, so no further search was run on these partitions.
- Not tested: a maker entry (it would cut 6 bps of entry cost and face adverse selection on a momentum entry; needs
  depth data), an intraday rolling top-mover list, Kronos, CEX-DEX.

## 8. Reproduce

    python -m research.hawkes_momentum_study daily      # 652 calls, public REST
    python -m research.hawkes_momentum_study klines     # 23 974 day zips, 1.33 GB (--shard i/n to parallelise)
    python -m research.hawkes_momentum_study matrix     # -> data/hawkes/matrix.npz, 1.23 GB
    python -m research.hawkes_momentum_study run        # 24-49 s -> data/hawkes_momentum_study.json
    python -m pytest -q tests/test_hawkes_momentum.py

Two runs gave identical results (the second adds only addendum A1). `data/hawkes/` holds 2.5 GB; the zips and the
matrix can be deleted and rebuilt with the commands above.
