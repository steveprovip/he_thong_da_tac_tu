# ODH-1 — capital needed for 1 USD per hour from non-directional lanes: funding rotation, Binance-Bybit funding, pairs (report, 2026-11-15)

Plan, frozen before any price or Bybit history of the study was downloaded (SHA-256 LF
`dae22cd189c71c310d61eb1e2dfdafd05540a294a81bcabf998d6af907e8eede`): `docs/research_upgrade/plan_one_dollar_hour_breakthrough_2026_11.md`.
Addendum A1, registered before any mechanism was run on market data (SHA-256 LF
`b14a262ea1af251bf5ad9eabae47ad1aee180b8f72c5632031a2999effe9a0d8`): `docs/research_upgrade/plan_one_dollar_hour_addendum_a1_2026_11.md`.
Code: `research/one_dollar_hour_study.py`, `tests/test_one_dollar_hour.py` (42 tests). Result files (git-ignored):
`data/one_dollar_hour_study.json`, `data/odh/` (klines, Bybit funding, `posthoc.json`, run logs, run 1 kept; 387 MB).
Offline research on public data. No order, no account, no engine start, no cap or gate changed, nothing wired, no git
operation. Research output, not investment advice.

## 0. Answer

| Mechanism (repository module) | Verdict | HOLDOUT, bps a day of the budget | Net yield on capital (HOLDOUT + FRESH; XV HOLDOUT) | Capital for 1 USD/h (point / lower 95 % bound) |
|---|---|---|---|---|
| M1 funding rotation, Binance perp short + spot long, 655 perps scanned (`core/funding_carry.py` idea) | **NO EDGE** (24 HOLDOUT rounds < 30; deflated Sharpe 0.71 < 0.95) | +1.45 +- 0.64 (LCB95 +0.40, p 0.011) | +3.19 %/yr | 274 k / 896 k USD |
| M2 Binance perp vs Bybit perp, both directions | **NO EDGE** | -11.09 +- 7.48 (p 0.93) | -39.2 %/yr | not finite |
| M3 pairs with `core/stat_arb_lane.OnlineKalmanPairTracker`, unchanged | **NO EDGE** | -6.12 +- 6.82 (p 0.82) | -33.3 %/yr | not finite |
| M3R pairs, rolling Engle-Granger (addendum A1) | **NO EDGE** | -2.18 +- 2.85 (p 0.78) | -8.9 %/yr | not finite |
| Reference: stored XV rule, Binance vs Hyperliquid, 28-day rounds, 18 majors (no verdict here) | positive on every partition | +0.54 +- 0.05 | +1.96 %/yr | **447 k / 527 k USD**; 793 k for a 95 % chance of >= 1 USD/h in a 30-day window |

- **1 USD per hour is 8 760 USD a year**, so capital C* = 8 760 / net yield. The only lane with a stable positive yield
  in this repository earns about 2 % a year: it needs 0.45-0.8 M USD for 1 USD/h. From the directive's tiers:
  5 000 USD would need 175 % a year net, 50 USD 17 520 %.
- At 5 000 USD the best measured lane pays **+0.008 USD per hour** (XV, 30-day median); M1 +0.009 USD/h (NO EDGE);
  M2, M3, M3R lose money at every tier. Below about 1 240 USD the assumed 2 USD of monthly transfers exceeds XV's
  yield.
- Ruin under 1 % holds for XV (0 of 2 000 paths, but its series is booked as round accruals, section 5) and for M1
  (0 %, also with its TRAIN tails in the bootstrap, where its mean turns negative: -1.36 bps a day on capital, worst
  90-day drawdown -19 %); M3 ruins 4.9 % of 90-day paths.

## 1. What was run

- Grid: every 4 h boundary, 2024-08-01..2026-11-13 (prices = open of the 4 h bar on the leg's own venue). Partitions by
  the UTC day of a step: TRAIN 2025-01-01..2025-12-31 (365 days), HOLDOUT 2026-01-01..2026-09-30 (273), FRESH
  2026-10-01..2026-11-12 (43). Per mechanism the TRAIN-best cell (>= 30 TRAIN rounds) was read once on HOLDOUT and
  FRESH. EDGE needed >= 30 HOLDOUT rounds, one-sided Newey-West (7 lags) LCB95 of the HOLDOUT daily mean > 0,
  deflated Sharpe >= 0.95 (18 trials; 24 for M3R) and a positive FRESH mean.
- M1: short the Binance perp, long Binance spot (1000-prefixes mapped), only positive 8 h funding, 5 equal slots,
  entry F8 >= theta in {5, 10, 20} bps per 8 h, exit at F8 < theta_out in {0, theta / 2} after >= 8 h, cost 15 bps a
  side (spot taker 10 + slip 3 + perp maker 2), perp at 2x liquidated on a 45 % adverse move inside a step (+150 bps).
- M2: Binance perp against Bybit perp of the same name, both directions, theta in {3, 5, 10}, cost 10 bps a side
  (maker 2 + Binance taker 5 + slip 3), both legs at 2x with the same liquidation rule.
- M3: 18 majors (153 pairs), the module's tracker unchanged on 4 h and daily bars, z_entry {2.1, 2.5, 3.0}, cost 4 bps
  of gross a side, portfolio = sum over max(10, pairs held). M3R: same universe and accounting, OLS hedge on the 120
  bars before t, Engle-Granger t < -3.34, half-life 1-60 bars, beta 0.2-3.5, entry z_entry <= |z| < 3.6, exit at
  |z| <= 0.25, stop 3.6, time stop max(2.5 x half-life, 120) bars.
- Capital per unit of budget 1.5 (M1), 1.0 (M2, XV), 0.5 (M3, M3R). 2 000 paths x 30 and 90 days, 7-day blocks drawn
  from the HOLDOUT + FRESH daily returns, seed 20261117, profits not reinvested, ruin = equity <= half the start on any
  day.

Data (public REST, fetched after the freeze): 655 Binance USD-M perps (1 serves no klines: GAIB), 453 spot pairs (13
delisted, no klines), 465 Bybit linear perps with the same name (2 without klines, 1 without funding), Binance funding
of 659 perps from `data/funding_universe/`. Tradable symbol-steps from 2025: M1 28.5 %, M2 30.6 %.

## 2. Results

### M1 — Binance spot-perp funding rotation

| Cell (TRAIN) | bps/day | rounds | net / round | funding | price | liquidated |
|---|---|---|---|---|---|---|
| th5 out0 | -8.78 +- 6.80 | 92 | -205.9 | +88.4 | -236.6 | 17 |
| th5 out0.5 | -10.32 +- 5.14 | 161 | -116.9 | +37.8 | -114.5 | 11 |
| th10 out0 | -9.79 +- 6.65 | 53 | -338.9 | +124.0 | -401.7 | 11 |
| th10 out0.5 | -8.67 +- 5.13 | 82 | -193.0 | +63.4 | -215.5 | 6 |
| th20 out0 | -6.23 +- 5.72 | 30 | -378.8 | +156.8 | -485.6 | 4 |
| **th20 out0.5** (chosen, least negative) | -4.95 +- 3.65 | 38 | -237.6 | +97.6 | -293.3 | 3 |

- HOLDOUT: +1.450 +- 0.637 bps/day (LCB95 +0.402, p 0.011, deflated Sharpe 0.713), **24 rounds** on 15 symbols, net
  +82.5 per round (funding +87.4, price +31.4, cost 30.0, liquidation 6.2), 70.8 % of rounds positive, mean hold 15.5 h;
  a position on 11.4 % of HOLDOUT days. FRESH +0.436 +- 0.400 (1 round). Verdict NO EDGE: too few rounds and not significant after deflation.
- Every TRAIN cell lost money because of the price leg of a "delta-neutral" position: the coins with extreme positive
  funding are being squeezed. ALPACA, 2025-04-30 (before its spot delisting): +773 bps of funding, **-7 855 bps** of
  perp-over-spot move in 12 h; BNX 2025-03-16 -2 128; AMB 2025-02-19 -1 525 and liquidated. With TRAIN in the
  bootstrap (not pre-registered) M1 earns -1.36 bps a day on capital and its worst day is -8.4 % of capital.
- The signal decays: median entry print 23-25 bps per 8 h, median received while held 10.1-10.5 bps per 8 h (38-44 %).
- Costs: taker on both legs +1.24 / +0.38 bps/day (HOLDOUT / FRESH), BNB-discounted +1.55 / +0.46, zero cost +1.98 /
  +0.58.
- Capacity at C*: a slot of 36.6 k USD against a median 24 h volume of 17.4 M USD (0.21 %); at the lower bound 119.5 k
  (0.69 %).

### M2 — Binance perp vs Bybit perp

| Cell (TRAIN) | bps/day | rounds | net / round |
|---|---|---|---|
| th3 out0 | +0.15 +- 2.90 | 708 | +0.7 |
| th3 out0.5 | -12.81 +- 3.48 | 2 347 | -9.8 |
| **th5 out0** (chosen) | +0.16 +- 2.90 | 697 | +0.7 |
| th5 out0.5 | -17.46 +- 3.57 | 2 607 | -12.1 |
| th10 out0 | -1.37 +- 3.16 | 865 | -2.7 |
| th10 out0.5 | -12.14 +- 3.59 | 1 908 | -11.5 |

- HOLDOUT: -11.094 +- 7.480 bps/day (LCB95 -23.40, p 0.931, deflated Sharpe 0.000), 1 020 rounds on 216 symbols, net
  -15.0 per round (funding +55.8, price -41.2, cost 20.0, liquidation 9.6), 37.8 % of rounds positive, mean hold 34.4 h, 65
  rounds liquidated (6.4 %), worst day -615.8 bps of the budget. FRESH -8.504 +- 8.699 (92 rounds). NO EDGE.
- The losses are broad, not a few outliers: median round -23.1 bps on HOLDOUT, 5 %-trimmed mean -20.4.
- A cross-venue funding spread is transient: median entry spread 42 bps per 8 h on HOLDOUT, median received while held
  2.95 bps per 8 h; the median round keeps **9 %** of its entry spread (TRAIN 12 %, FRESH 14 %). The spread is the price of a dislocation that keeps going:
  the price leg loses 41 bps a round. Largest losses: H 2026-06-15 (-1 427 bps of price in 8 h), ARIA, RIVER, INIT
  (entry spread -1 505 bps per 8 h).
- Two venues are two margin accounts: a 2x leg dies on a 45 % spike even when the pair is flat.
- Zero cost: HOLDOUT +3.84 (LCB95 -8.25), FRESH +0.05 bps/day. Taker on both legs: -20.80 / -14.07.

### M3 — the module's Kalman tracker

| Cell (TRAIN) | bps/day | rounds | net / round | win |
|---|---|---|---|---|
| 4 h z2.1 | -5.08 +- 5.21 | 472 | -42.7 | 59.5 % |
| 4 h z2.5 | -5.13 +- 4.72 | 301 | -64.9 | 57.5 % |
| 4 h z3.0 | -0.61 +- 2.30 | 172 | -12.5 | 64.0 % |
| 1 d z2.1 | +6.47 +- 6.89 | 122 | +276.8 | 72.1 % |
| **1 d z2.5** (chosen) | +7.34 +- 3.71 | 61 | +426.1 | 75.4 % |
| 1 d z3.0 (28 rounds, not eligible) | +4.84 +- 2.61 | 28 | +475.4 | 82.1 % |

- HOLDOUT: -6.123 +- 6.818 bps/day (LCB95 -17.34, p 0.815, deflated Sharpe 0.001), 137 rounds, net -183.6 per round,
  51.8 % of rounds positive, mean hold 283 h, 60 of 137 exits are stops; worst day -900 bps (2026-06-03: WLD +41.6 % in a day hit
  every pair short WLD); HYPE/XRP held 120 days lost 62 % of its gross (HYPE +110 %, XRP -25 %). FRESH +5.374 +- 5.263
  (no new round). NO EDGE.
- Addendum A1 section 1 assumed the tracker could not open a position at bar resolution. That held on the synthetic
  pairs (kept as a test) but **not on market data**: it opened 28-472 TRAIN rounds per cell. M3 therefore has a
  verdict; M3R is a second, independent mechanism.

### M3R — rolling Engle-Granger pairs (addendum A1)

| Cell (TRAIN) | bps/day | rounds | net / round | stopped |
|---|---|---|---|---|
| 4 h z2.1 | -3.90 +- 2.39 | 911 | -18.2 | 577 |
| 4 h z2.5 | -1.10 +- 1.76 | 569 | -5.7 | 393 |
| 4 h z3.0 | -0.48 +- 1.12 | 227 | -7.8 | 171 |
| **1 d z2.1** (chosen) | +0.63 +- 2.46 | 75 | +37.8 | 49 |
| 1 d z2.5 | -1.51 +- 1.03 | 43 | -111.4 | 34 |
| 1 d z3.0 (20 rounds, not eligible) | -1.08 +- 0.49 | 20 | -200.1 | 20 |

- HOLDOUT: -2.176 +- 2.851 bps/day (LCB95 -6.87, p 0.777, deflated Sharpe 0.003 over 24 trials), 130 rounds, net -32.6
  per round (gross -24.6), 37.7 % of rounds positive, **81 of 130 exits are stops at |z| >= 3.6**. FRESH +4.852 +- 6.106 (3
  rounds, all stopped). NO EDGE. A spread that is 2 sd wide on a cointegration test fitted to the previous 120 bars
  widens to 3.6 sd more often than it returns to 0.25 sd.
- Costs are not the problem: zero cost HOLDOUT -1.83 bps/day; at 4 h bars the TRAIN gross per round is -10.2 to +2.3 bps.

### Reference — stored XV rule, Binance vs Hyperliquid (`tools/funding_spread_replay.py`, no verdict)

TRAIN +0.910 +- 0.108 bps/day (213 rounds, +28.0 per 28-day round); HOLDOUT +0.538 +- 0.050 (165 rounds, +16.0:
funding +30.6, price +0.4, cost 15.0; 72.7 % positive; price sd 6.7 bps a round); FRESH truncated (the cache ends
2026-11-04). 1.96 % a year on capital on HOLDOUT, 2.74 % with TRAIN.

## 3. Capital matrix (2 000 paths, median USD per hour over 30 days; P(>= 1 USD/h) and P(ruin) where not zero)

| Capital, USD | M1 (NO EDGE) | M2 | M3 | M3R | XV reference |
|---|---|---|---|---|---|
| 50 | +0.0001 | -0.0047 | -0.0004 (ruin 0.7 %) | -0.0004 | -0.0027 (transfers) |
| 500 | +0.0009 | -0.022 | -0.004 | -0.004 | -0.0017 |
| 1 000 | +0.0017 | -0.041 | -0.008 | -0.008 | -0.0006 |
| 5 000 | +0.0087 | -0.19 | -0.04 | -0.04 | +0.0080 |
| 10 000 | +0.017 | -0.38 | -0.08 | -0.08 | +0.019 |
| 50 000 | +0.087 | -1.90 | -0.40 | -0.42 | +0.105 |
| 100 000 | +0.17 (P 0.10) | -3.80 | -0.80 | -0.84 | +0.21 |
| 500 000 | +0.87 (P 0.45) | -19.0 | -4.0 | -4.2 | **+1.08 (P 0.62)** |

- 90-day horizon: M1 median +1.58 USD/h at 500 k (positive skew), XV +1.09; M3 ruin 4.9 % at every tier.
- From 500 USD every lane fills all its slots at the 5 USD (10 USD Hyperliquid) leg minimum; XV holds 5 / 10 of its 18
  slots at 50 / 100 USD. The 5 USD minimum understates BTC
  (50 USD) and ETH, LINK, LTC (20 USD) on Binance, which matters only far below any C*.
- Capital for a 5th-percentile hourly profit of 1 USD (not pre-registered): XV 793 k USD over 30 days, 594 k over 90;
  M1 not finite over 30 days, 15.7 M over 90; the others not finite.

## 4. Why it does not reach 1 USD per hour

- Funding is paid for carrying a risk the hedge does not remove. On spot-perp (M1) the received rate is 38-44 % of the
  print that triggered the entry, and the coins that print 20 bps per 8 h are the ones that squeeze: three TRAIN rounds
  lost 15-79 % of the slot on the perp-over-spot move. Across venues (M2) the received spread is 9-14 % of the entry
  spread and the price leg loses 41 bps a round: an extreme differential is a dislocation in progress, not a coupon.
- The stable lane is the boring one: XV holds 28 days on majors, ignores spikes, pays 15 bps a round and keeps 16. Its
  yield (2-3 % a year) is the size of the funding spread between two well-arbitraged venues.
- Mean reversion on crypto majors is short tail risk at daily horizons: stops are 44 % (M3) to 62 % (M3R) of exits on
  HOLDOUT, and single days of -9 % (M3) erase months.
- The target is a yield problem, not an execution problem: zero-cost versions stay at +2.0 (M1), +3.8 (M2), -5.8 (M3)
  and -1.8 (M3R) bps a day of the budget on HOLDOUT.

## 5. Not tested, limitations

- 4 h resolution: both legs fill at the 4 h open after the signal, slippage assumed (3 bps on the taken leg); the
  post-settlement move CCX-1 measured (-30 bps in the first minute against the receiver) is inside the first bar.
- Liquidation is checked per step against the step's opening price with last-price highs / lows; the venues liquidate
  on the mark price and on the move since entry. Hedged legs are marked at their own venue; re-levelling is the assumed
  2 USD / 30 days (M2, XV).
- XV is booked as accruals of each round's final net: intra-round marks, its drawdown and its ruin are not measured;
  its FRESH is truncated. The Monte Carlo resamples HOLDOUT + FRESH (plan), so M1's TRAIN tails are excluded from the
  pre-registered ruin numbers (post-hoc with TRAIN: worst day -8.4 % of capital, worst 90-day drawdown -19 %).
- Survivorship: today's listings on each venue. Bybit fees are the repo constant (maker 2.0 / taker 5.5 bps); the
  owner's eligibility on Bybit and Hyperliquid is not verified. Negative prints are not harvested on spot (no borrow
  data). Bybit vs Hyperliquid not tested. Idle capital earns 0.

## 6. Bugs found and fixed (no rule changed)

- Run 1 opened 0 M1 / M2 rounds: `trailing_sum` used a plain cumulative sum, so one leading NaN (every spot and Bybit
  series starts 2024-12-20; late listings) made every later 24 h volume NaN and the tradability gate never passed.
  M1 / M2 HOLDOUT were not read in run 1 (NO OPPORTUNITY SET at TRAIN). Fixed, regression test added (late listing and
  late spot in the end-to-end fixture); run 2 reproduces M3 and M3R exactly. Run 1 is kept in
  `data/odh/run1_one_dollar_hour_study.json` and logged in `trial_log.jsonl` (`odh1-note`).
- Before any run: the reference lane read field names the replay does not write; a funding-record hole (> 16 h without
  a print, 4 Binance files) counted as zero; pairs P&L was booked at the bar's end; kline paging could skip the bar that
  opens at the end time (no file was affected).

## 7. What this allows

- Nothing is wired. NO EDGE closes the M1, M2, M3 and M3R cell families on these partitions; HOLDOUT and FRESH are
  read for them.
- On these numbers, 1 USD per hour from a non-directional lane in this repository needs roughly half a million USD in
  the XV lane (two venues, 18 majors), and the venue, eligibility and counterparty questions that come with it. No
  mechanism here turns 50-5 000 USD into 1 USD per hour.
- A new test would need new data: unseen months for any M1 / M2 refinement (for example a squeeze filter), the
  sub-second settlement path for entry timing, or a marked-to-market XV book (the Demo carry sim already logs one to
  `data/carry_sim.jsonl`).

## 8. Reproduce

```
python -m research.one_dollar_hour_study fetch     # public REST, about 45 minutes, 387 MB under data/odh/
python -m research.one_dollar_hour_study run       # 49 s -> data/one_dollar_hour_study.json
python -m pytest -q tests/test_one_dollar_hour.py  # 42 passed
```
