# AF-1 — asymmetric frontiers for a 10 USDT base: maker conversion, CEX-DEX arithmetic, longshot contracts (report, 2026-11-15)

Plan, frozen before any maker-entry outcome was simulated and before any Kalshi trade sample was downloaded (SHA-256
`ede2c17f099e285c4a2dccfb9c22c34ff3a1b33773e787eef74f886c9dbb278f`): `docs/research_upgrade/plan_asymmetric_frontier_10u_2026_11.md`.
Code: `research/asymmetric_frontier_study.py`, `tests/test_asymmetric_frontier.py` (20 tests). Result files (git-ignored):
`data/asymmetric_frontier_study.json`, `data/af/` (235 Kalshi trade windows, market results, Hyperliquid meta, logs; 73 MB).
Offline research on public data. No order, no account, no engine start, no cap or gate changed, nothing wired, no git operation.

## 0. Verdicts

| Frontier | Result |
|---|---|
| A. Hyperliquid maker rebate | The premise does not exist. Tier 0 charges the maker **+1.5 bps**; the venue's rebates are -0.1 to -0.3 bps and need more than 0.5-3.0 % of all maker volume. "-1.5 to -3.0 bps" is ten times the largest rebate |
| T-A, maker entry on the HWK-1 triggers under the real schedule | **NO EDGE.** HOLDOUT +5.76 +- 8.35 bps per fill (n 2 140, LCB95 -7.98, deflated Sharpe 0.06 over 47 trials); FRESH -34.03 +- 17.11 (n 455) |
| The same fills with the directive's -3.0 bps rebate | HOLDOUT +11.57 +- 8.42 (LCB95 -2.28); FRESH -28.42 +- 17.20. Zero fees and zero slippage: HOLDOUT +13.02 +- 8.32 (LCB95 -0.67), FRESH -26.51 |
| Maker against taker on the same triggers | The maker entry is not better: HOLDOUT +5.61 per trigger against +8.34 +- 8.26 for a taker entry. The 2.6 % of triggers the limit never filled averaged +393 bps |
| B. CEX-DEX on 10 U | Arithmetic only, not tested. A 5 U round breaks even at a 35 bps gap; a 100 bps gap won every time pays 0.0325 U, so +20 U/h needs 615 such rounds per hour |
| T-C, buying 1-2 cent contracts on Kalshi | **NO EDGE.** HOLDOUT return -67.4 % +- 18.3 % of the stake (n 17 100 trades, 86 windows): the contracts win 0.71 % of the time at a price of 2.02 cents. FRESH -24.1 % +- 39.9 % |
| Growth of 10 U | A at the 10 U minimum order: median 9.62 U after 30 days, 14.7 % ruined. C at 1 U stakes: median 0.39 U, 98.5 % ruined. No path of any sizing reaches the +20 U/h pace |

## 1. The fee schedules, read from the venues

| Venue | Taker | Maker | Source |
|---|---|---|---|
| Hyperliquid perps, tier 0 (any 14-day volume under 5 M USD) | 4.5 bps | **+1.5 bps** | fee page read 2026-11-15 |
| Hyperliquid tier 4 (> 500 M USD) | 2.8 bps | 0.0 bps | same |
| Hyperliquid maker rebate tiers | | -0.1 / -0.2 / -0.3 bps at > 0.5 / 1.5 / 3.0 % of the venue's 14-day maker volume | same |
| Binance USD-M VIP0 | 5.0 bps | +2.0 bps | repo constant |
| Kalshi | 0.07 x C x P x (1 - P), rounded up to the cent | 0 on most markets | repo constant verified 2026-09; the help page read today defers to the fee PDF |
| Polymarket | C x rate x p x (1 - p), rate 0.04-0.07 by category, geopolitics 0 | 0 | fee page read 2026-11-15; not used (section 4) |

Hyperliquid's minimum order is 10 USD (`core/venues/hyperliquid.py`). A 10 U account is tier 0 on every venue.

## 2. A — maker conversion (T-A)

**Cost identity.** With maker fee f_m, taker fee f_t, taker slippage s and a share p of fills that leave at the
resting target: E[cost] = f_m x (1 + p) + (f_t + s) x (1 - p). At p = 0.29 (measured) Hyperliquid tier 0 costs
1.5 x 1.29 + 7.5 x 0.71 = **7.26 bps**, against 14.2 bps for HWK-1's taker entry on Binance. A rebate of -3.0 bps
would take 4.5 x 1.29 = 5.8 bps off that. 71 % of exits are stops, and a stop is a taker order on every venue: the
cost reaches zero only at f_m = -(7.5 x 0.71) / 1.29 = **-4.13 bps**, fourteen times the venue's largest rebate.

**Test.** HWK-1's triggers and sides unchanged (its T1 cell, 600 / 240 bps bracket). Entry: post-only limit k bps
behind the trigger close, alive TTL minutes, filled only when a 1 m bar trades through it; target never credited in
the fill bar; stop counted in it. Binance 1 m paths are the proxy for Hyperliquid's.

TRAIN, net bps per fill under tier 0 (fill rate):

| Offset k | TTL 1 min | TTL 5 min | TTL 15 min |
|---|---|---|---|
| 0 bps | +0.55 +- 5.85 (96 %) | -0.49 +- 5.83 (99 %) | +0.83 +- 5.85 (99 %) |
| 10 bps | +7.63 +- 5.95 (83 %) | +5.95 +- 5.92 (93 %) | **+8.52 +- 5.94 (96 %)** |
| 25 bps | +4.07 +- 6.07 (61 %) | +1.09 +- 5.95 (83 %) | +2.97 +- 5.98 (90 %) |

The TRAIN-best cell (k 10, TTL 15) read once:

| k 10 bps, TTL 15 min | TRAIN | HOLDOUT | FRESH |
|---|---|---|---|
| Triggers / fills (fill rate) | 3 896 / 3 731 (95.8 %) | 2 198 / 2 140 (97.4 %) | 464 / 455 (98.1 %) |
| Net bps per fill, tier 0 | +8.52 +- 5.94 | +5.76 +- 8.35 | -34.03 +- 17.11 |
| One-sided 95 % bounds | -1.25 | -7.98 .. +19.50 | -62.17 |
| Gross / cost | +16.04 / 7.52 | +13.02 / 7.26 | -26.51 / 7.52 |
| Exits: stop / target / time | | 68.5 / 29.0 / 2.5 % | 73.4 / 24.6 / 2.0 % |
| Deflated Sharpe (38 + 9 trials) | | 0.059 | 0.00003 |
| Break-even maker fee f_m* | +8.34 bps | +5.97 bps | **-25.81 bps** |
| Binance VIP0 (2.0 / 5.0) | +7.52 | +4.76 | -35.03 |
| Hyperliquid best tier (-0.3 / 2.4) | +12.35 | +9.57 +- 8.36 | -30.20 |
| Directive -1.5 bps (hypothetical) | +12.26 | +9.63 +- 8.39 | -30.29 |
| Directive -3.0 bps (hypothetical) | +14.13 | +11.57 +- 8.42 (LCB95 -2.28) | -28.42 +- 17.20 |
| Zero fees, zero slippage | +16.04 | +13.02 +- 8.32 (LCB95 -0.67) | -26.51 +- 16.99 |
| Taker entry on the same triggers, tier 0, per trigger | +6.19 | +8.34 +- 8.26 | -36.76 +- 17.02 |
| Maker, per trigger | +8.16 | +5.61 | -33.37 |
| Taker gross of the triggers the maker filled / missed | +7.8 / +288.2 | +11.5 / +393.3 | -31.9 / +413.3 |

Checks: HOLDOUT fills >= 100 yes; lower bound > 0 no; deflated Sharpe >= 0.95 no; FRESH mean > 0 no.

- The fee is not what stands between this rule and a profit. On HOLDOUT the lower bound is negative with no fee at
  all, and on FRESH the gross is -26.5 bps: the rule would need a 25.8 bps rebate per maker fill to break even there.
- A limit 10 bps behind the close fills 96-98 % of the time on these symbols (median 1-minute move 13-19 bps), so
  adverse selection per fill is small. The cost is in the 2-4 % that never fill: those are the runaway moves
  (+288 to +413 bps each for a taker). 2.6 % x 393 = 10.2 bps per trigger, which cancels the 10 bps of price
  improvement. Per trigger the maker entry earns less than the taker entry on HOLDOUT.
- All nine cells on HOLDOUT lie between +2.30 and +8.62 bps with lower bounds between -5.15 and -11.31.
- Signal S2 (HWK-1's search final), same cell: HOLDOUT +6.95 +- 7.66 (n 2 406), FRESH -19.87 +- 19.50 (n 501).
- Symbols in Hyperliquid's current universe are 13-14 % of the triggers: HOLDOUT +26.74 +- 22.50 (n 288, LCB95
  -10.26), FRESH +26.21 +- 57.10 (n 62). Not significant, and the listing is today's (look-ahead).

## 3. B — CEX-DEX gap arithmetic (not tested)

One round of size q across a gap G: net = p_win x (1 - tau) x (q x (G - phi - f_c - s) - g) - (1 - p_win) x g_fail,
with pool fee phi, CEX taker fee f_c, slippage s, gas g, inclusion share tau, probability of being first p_win.
From 10 U split over two venues q = 5 U (Binance's minimum order). Gas figures are assumed orders of magnitude.

| Break-even gap G* = phi + f_c + s + g / q | gas 0.001 U | gas 0.02 U | gas 2 U |
|---|---|---|---|
| pool 5 bps, perp taker 5 bps | 15 bps | 53 bps | 4 013 bps |
| pool 25 bps, perp taker 5 bps | 35 bps | 73 bps | 4 033 bps |
| pool 100 bps, spot taker 10 bps | 115 bps | 153 bps | 4 113 bps |

| One 5 U round at pool 25 bps, gas 0.001 U | Net | Rounds per hour for +20 U/h |
|---|---|---|
| 100 bps gap, always first, no inclusion payment | +0.0325 U | 615 |
| 100 bps gap, 90 % of the profit paid for inclusion | +0.0033 U | 6 154 |
| 100 bps gap, first 1 time in 100 | -0.0007 U | never |
| 200 bps gap, always first | +0.0825 U | 242 |

A 5 U leg caps the win of a round at cents. The pace needs hundreds of 1-2 % dislocations per hour, each won against
searchers who are co-located and pay most of the gap for inclusion. The trade is two legs on two venues, not atomic,
and needs inventory on both. No block-level swap data is on disk and a 1 m bar cannot show who is first: no
empirical claim is made in either direction.

## 4. C — longshot contracts on Kalshi (T-C)

Sample: 235 windows of 300 seconds (TRAIN 122 in 2025, HOLDOUT 91 in 2026-01..09, FRESH 22 in 2026-10..11), start
minute rotating through the day, fixed before the download. 2 964 821 trades; 550 207 (18.6 %) are taker buys at 10
cents or less; 543 430 of those are in markets that resolved yes or no (6 777 unresolved, excluded). No window was
cut. Multi-leg combos are 34 % of them and are kept apart. Polymarket was not queried: the repo's own note records
it as blocked in Vietnam.

Return of one trade at a 1.00 U stake: floor(1 / p) contracts, taker fee 0.07 x C x p x (1 - p) rounded up to the
cent (0.07 U at 1-2 cents), payoff 1 per contract if the taker's side won. Break-even win rate at 2 cents: 2.14 %.

TRAIN, single-event markets, return per unit staked (win rate at mean price):

| Taker price | all | >= 24 h to close | < 24 h to close |
|---|---|---|---|
| <= 1.5 cents | -0.960 (0.04 % at 1.00 c) | -1.000 (0 of 294) | -0.954 |
| 1.5-2.5 cents | -0.648 (0.75 % at 2.00 c) | -0.850 | **-0.546 +- 0.334 (0.97 %)** |
| <= 2.5 cents | -0.871 | -0.923 | -0.859 |

The least negative TRAIN cell (2 cents, under 24 h to close) read once:

| 1.5-2.5 cents, < 24 h | TRAIN | HOLDOUT | FRESH |
|---|---|---|---|
| Trades (windows) | 618 (61) | 17 100 (86) | 13 554 (22) |
| Return per unit staked | -0.546 +- 0.334 | **-0.674 +- 0.183** | -0.241 +- 0.399 |
| One-sided lower 95 % bound | -1.096 | -0.976 | -0.897 |
| Win rate / mean price | 0.97 % / 2.00 c | 0.71 % / 2.02 c | 1.62 % / 2.04 c |
| Without the fee | -0.515 | -0.652 | -0.188 |
| YES takers / NO takers | | -0.835 +- 0.174 / -0.362 +- 0.476 | -0.227 +- 0.462 / -0.259 +- 0.602 |
| Weighted by contracts | -0.810 | -0.656 | +0.574 |
| Deflated Sharpe (9 trials) | | 0.00 | 0.00 |
| The seller of those contracts, per unit of collateral | +1.05 % | +1.34 % | +0.42 % |

Checks: HOLDOUT n >= 100 yes; lower bound > 0 no; deflated Sharpe >= 0.95 no; FRESH mean > 0 no.

Calibration of single-event markets on HOLDOUT (89 windows): the contracts win less often than they cost, in every
bucket.

| Taker price | Trades | Mean price | Win rate | Win rate / price | Return per unit staked |
|---|---|---|---|---|---|
| <= 1.5 cents | 43 657 | 0.89 c | 0.22 % | 0.25 | -0.750 +- 0.167 |
| 1.5-2.5 cents | 18 532 | 2.02 c | 0.70 % | 0.35 | -0.676 +- 0.171 |
| 2.5-5 cents | 53 893 | 3.91 c | 2.66 % | 0.68 | -0.394 +- 0.270 |
| 5-10 cents | 87 974 | 7.72 c | 5.06 % | 0.66 | -0.380 +- 0.166 |

- All nine HOLDOUT cells lie between -0.674 and -0.764. FRESH buckets: -0.515, -0.261, -0.613, -0.679.
- The FRESH contract-weighted figure (+0.574) comes from a few large winning trades in 22 windows; the equal-weight
  mean is -0.241 +- 0.399 and the HOLDOUT contract-weighted figure is -0.656.
- Combos on HOLDOUT: -0.633 +- 0.109 (<= 1.5 c), -0.404 +- 0.080, -0.344 +- 0.090, -0.325 +- 0.079; on FRESH -0.291,
  -0.187, -0.301, -0.139. In TRAIN (2025) there are 388 combo trades at 1 cent: +0.546 +- 1.139, noise.
- The other side of these trades earned 0.7-2.9 % of its collateral per contract on HOLDOUT. That is the payoff the
  directive wanted to avoid, reversed: 98 cents locked to earn about 1, and the whole 98 lost when the contract hits.
  It is a description of what resting sellers received, not a tested strategy.

## 5. Growth of 10 U (Phase 3)

2 000 paths x 30 days, each day replaying one calendar day (A, one position at a time: 2 297 fills, 7.25 a day,
mean net -1.68 bps) or one sampled window with up to 5 cell trades (C: 113 windows, 30 654 trades).

| Structure and sizing | Median day 30 | P05 / P95 | Ruin | P(profit) | P(double) | P(pace +20 U/h) | Mean U per hour |
|---|---|---|---|---|---|---|---|
| A, 10 U minimum order (loses 0.24 U at the stop) | 9.62 U | 1.85 / 18.96 | 14.7 % | 47.1 % | 3.3 % | 0 | -0.0004 |
| A, 1.0 U at the stop (41.7 U, capped at 5x) | 1.92 U | 1.77 / 42.96 | 71.6 % | 24.0 % | 18.0 % | 0 | -0.0011 |
| C, 1.00 U stakes | 0.39 U | 0.37 / 0.45 | 98.5 % | 1.4 % | 1.25 % | 0 | -0.0117 |
| C, 0.05 U stakes (the standing cap) | 4.71 U | 2.43 / 10.68 | 0 % | 6.9 % | 0.1 % | 0 | -0.0064 |

- A cannot be traded under the standing caps at all: Hyperliquid's 10 USD minimum order loses 0.24 U at a 240 bps
  stop, 4.8 times the 0.05 U cap. Ruin for A means equity under 2 U (the margin of one minimum order at 5x).
- C at 1 U stakes is ruined after 14 bets on average. At 0.05 U the fee rounds up to a whole cent: 20 % of a
  5-contract bet at 1 cent, 25 % of a 2-contract bet at 2 cents. Half the bankroll is gone in 30 days.
- The best single path of any row ends at 255 U (one 1-cent contract that hit). The pace needs 14 410 U.

## 6. What the three results have in common

- A: the binding cost is the taker stop, which no maker schedule removes, and the gross itself is unstable (+13 on
  HOLDOUT, -27 on FRESH). The rebate in the directive is not offered by the venue.
- B: the size cap of a 10 U account makes each win cents while the costs of losing the race are fixed.
- C: the "capped downside, 50-100x payoff" contract is priced 1.5 to 4 times above its realised win rate before fees.
  The convexity is paid for in full by the buyer.

None of the three converts a 10 U account into the target. The measured hourly expectancy is negative in all four
simulated rows.

## 7. Deviations, limits, not measured

- T-A reuses HWK-1's HOLDOUT and FRESH (read once for the taker rule); the plan registered that a pass would only be a
  CANDIDATE. Binance paths stand in for Hyperliquid's; funding is left out; fills assume a trade-through on 1 m bars
  with no queue model.
- The result lookups were run with 3-6 workers and 40-120 tickers per call after the freeze (a 250-ticker URL is
  refused); the sample, the cells and the statistics are as frozen. The maker and longshot analyses were each run once
  before the final combined run to check the pipeline; the combined run reproduces them exactly.
- T-C measures what takers who traded at those prices received. It does not model a new order's fill, and it covers
  Kalshi only. Kalshi's fee constant comes from the repo (verified 2026-09), not from a re-read of the fee PDF.
- The owner's eligibility on Kalshi and on Hyperliquid was not checked. Polymarket was not queried.
- B has no data behind it: gas, inclusion share and win probability are assumptions.
- Not tested: a maker stop (stop-limit) on Hyperliquid, which would trade the taker fee for non-execution risk on the
  68 % of exits that are stops; wider stops; Hyperliquid's own price paths.

## 8. Reproduce

    python -m research.asymmetric_frontier_study kalshi-fetch --workers 3 --batch 120   # about 2 h at the venue's pace
    python -m research.asymmetric_frontier_study run                                    # 13 s
    python -m pytest -q tests/test_asymmetric_frontier.py

`run` needs `data/hawkes/matrix.npz` (HWK-1; rebuild with `python -m research.hawkes_momentum_study klines` and
`matrix`).
