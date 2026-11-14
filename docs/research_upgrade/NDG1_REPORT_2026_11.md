# NDG-1 report — two-sided post-only quoting at +-k sigma under the ATLAS low-volatility gate (2026-11-14)

Plan (frozen before any quoting outcome): `plan_non_directional_grid_2026_11.md`, SHA-256 `441c3d31...b776`,
2026-11-14T08:51:56Z (trial_log row `ndg1-prereg`).
Code: `research/non_directional_grid_study.py`, `tests/test_non_directional_grid.py` (22 tests).
Numbers: `data/non_directional_grid_study.json`, `data/non_directional_grid_run.log` (git-ignored; section 10).
Offline research: no order, no engine start, no live, no gate / lock / cap changed, nothing wired.

## 0. Verdict
- **T1 = NO EDGE.** The TRAIN-best of the 6 cells is k = 1.0 with the band freeze (the widest quotes: the cell that
  trades least). Net per active 15 m window: TRAIN -4.63 +- 0.29 bps, HOLDOUT -4.38 +- 0.19 (32 486 windows, 16 338
  with a fill, LCB95 -4.69), FRESH -4.25 +- 0.43 (6 070 windows, 2 930 with a fill). Deflated Sharpe 0.00 (12 trials).
  Pre-registered checks: fill windows >= 300 pass; LCB95 > 0 fail; DSR >= 0.95 fail; FRESH mean > 0 fail.
- **The gross is not positive either**: HOLDOUT -0.12 +- 0.06 bps per window before any fee. What the strategy loses is
  its fee bill (4.26 bps per window: 1.05 maker + 3.21 forced taker exits), plus a small adverse-selection cost.
- **Nothing in the grid is positive**: 12 cells x 6 fee schedules x 3 partitions = 216 numbers, 216 negative, including
  zero fees, the hypothetical -0.5 bps maker rebate and the all-maker-exit upper bound.
- **The low-volatility gate does not create an edge**: LOW, MID, HIGH and ungated windows all lose 3.4-4.6 bps at k = 1
  (one exception, FRESH HIGH: -1.3 +- 0.6 on 345 windows); LOW is the worst or second-worst regime on every partition.
  The gate ranks volatility (realised |15 m move| 15 / 22 / 34 bps on HOLDOUT) and nothing else.
- At the 5.05 U micro lot the T1 cell costs 0.26 U per day on HOLDOUT (0.30 on FRESH): a 5 U account is gone in about
  19 days. Two-sided quoting stays out of the engine (decision of 2026-11-09 e stands).

## 1. What was tested
One window = the 15 one-minute bars after a decision close (:00 / :15 / :30 / :45 UTC), BTC / SOL / DOGE, same decision
points as ATLAS-K1 (TRAIN 113 469, HOLDOUT 78 624, FRESH 12 384). The final ATLAS-K1 ensemble (8 forecasters, survivor
weights, constants in the module) gives a +15 m band (Q05, Q95); sigma = (Q95 - Q05) / 3.29. Bid at -k sigma, ask at
+k sigma, k in {0.25, 0.5, 1.0}; a quote fills when a bar trades through it; the opposite quote is the exit; with the
BAND freeze a bar that leaves the forecast band closes inventory as a taker at the band edge and ends the window;
inventory left at the end is closed as a taker. Costs: maker 2 bps per fill, taker 5 + 1.5 bps slippage per forced
exit (Binance USD-M VIP0, `core.early_stop`). Gate: LOW = band width below the symbol's TRAIN 20th percentile.

Premises of the directive, corrected in the plan before the run: there is no maker rebate at this tier (the maker
PAYS 2 bps); the quoted spread (0.015-1.6 bps) cannot pay a 4 bps round trip, so only quotes a multiple of sigma away
are testable; ATLAS's volatility skill is 1.6 % of pinball loss and a plain EWMA beats it (both gates were run); on a
martingale a two-sided quoter earns zero gross and pays its fees, so the test is a test of mean reversion.

## 2. The gate
| symbol | TRAIN P20 of the band width | sigma at the threshold | LOW share TRAIN / HOLDOUT / FRESH |
|---|---|---|---|
| BTCUSDT | 44.3 bps | 13.5 bps | 20.0 % / 20.7 % / 26.3 % |
| SOLUSDT | 94.8 bps | 28.8 bps | 20.0 % / 49.1 % / 58.2 % |
| DOGEUSDT | 101.5 bps | 30.9 bps | 20.0 % / 54.2 % / 62.6 % |

"Below the 20th percentile" is a TRAIN statement: 2026 was quieter, so the fixed thresholds mark 41.3 % of the HOLDOUT
windows and 49.0 % of the FRESH windows as LOW (SOL and DOGE: half or more; BTC: about a quarter). Every LOW window
had both quotes inside its band at k = 1 (0 one-sided, 0 inactive on all three partitions); the median LOW band is
+-17 bps on BTC and +-36-39 bps on SOL / DOGE (HOLDOUT), sigma 10 / 22 / 24 bps.

## 3. All six cells under the ATLAS LOW gate (net bps per active window, SE clustered by UTC day)
| cell | partition | active windows | with a fill | pairs | forced exits (freeze / end) | quote distance bps | gross bps | net bps +- SE | LCB95 | net bps per day (3 symbols) |
|---|---|---|---|---|---|---|---|---|---|---|
| k0.25 BAND | TRAIN | 22 695 | 22 612 (99.6 %) | 14 379 | 3 623 / 14 016 | 5.0 | -1.32 +- 0.14 | -10.46 +- 0.62 | -11.49 | -603 |
| k0.25 BAND | HOLDOUT | 32 486 | 32 307 (99.4 %) | 20 209 | 5 188 / 20 629 | 5.0 | -1.15 +- 0.11 | -10.39 +- 0.42 | -11.09 | -1237 |
| k0.25 BAND | FRESH | 6 070 | 6 020 (99.2 %) | 3 510 | 923 / 3 928 | 5.1 | -1.29 +- 0.27 | -10.40 +- 0.96 | -11.97 | -1468 |
| k0.25 NONE | TRAIN | 22 695 | 22 612 (99.6 %) | 14 709 | 0 / 17 581 | 5.0 | -1.48 +- 0.16 | -10.66 +- 0.62 | -11.68 | -614 |
| k0.25 NONE | HOLDOUT | 32 486 | 32 307 (99.4 %) | 20 772 | 0 / 25 708 | 5.0 | -1.33 +- 0.14 | -10.62 +- 0.43 | -11.33 | -1264 |
| k0.25 NONE | FRESH | 6 070 | 6 020 (99.2 %) | 3 632 | 0 / 4 818 | 5.1 | -1.18 +- 0.33 | -10.32 +- 0.99 | -11.94 | -1457 |
| k0.5 BAND | TRAIN | 22 695 | 20 977 (92.4 %) | 4 800 | 4 229 / 14 666 | 10.0 | -0.40 +- 0.12 | -8.32 +- 0.51 | -9.16 | -479 |
| k0.5 BAND | HOLDOUT | 32 486 | 29 413 (90.5 %) | 6 111 | 5 923 / 20 844 | 10.1 | -0.40 +- 0.09 | -8.15 +- 0.34 | -8.70 | -970 |
| k0.5 BAND | FRESH | 6 070 | 5 435 (89.5 %) | 993 | 1 035 / 3 950 | 10.1 | -0.45 +- 0.23 | -8.08 +- 0.78 | -9.37 | -1141 |
| k0.5 NONE | TRAIN | 22 695 | 20 977 (92.4 %) | 5 045 | 0 / 18 852 | 10.0 | -0.56 +- 0.15 | -8.51 +- 0.52 | -9.36 | -490 |
| k0.5 NONE | HOLDOUT | 32 486 | 29 413 (90.5 %) | 6 502 | 0 / 26 695 | 10.1 | -0.55 +- 0.13 | -8.34 +- 0.34 | -8.90 | -992 |
| k0.5 NONE | FRESH | 6 070 | 5 435 (89.5 %) | 1 080 | 0 / 4 957 | 10.1 | -0.42 +- 0.33 | -8.07 +- 0.81 | -9.40 | -1140 |
| **k1 BAND (T1)** | TRAIN | 22 695 | 11 976 (52.8 %) | 447 | 4 593 / 7 122 | 20.0 | -0.17 +- 0.08 | **-4.63 +- 0.29** | -5.11 | -267 |
| **k1 BAND (T1)** | HOLDOUT | 32 486 | 16 338 (50.3 %) | 543 | 6 413 / 9 612 | 20.2 | -0.12 +- 0.06 | **-4.38 +- 0.19** | -4.69 | -521 |
| **k1 BAND (T1)** | FRESH | 6 070 | 2 930 (48.3 %) | 73 | 1 109 / 1 781 | 20.3 | -0.16 +- 0.13 | **-4.25 +- 0.43** | -4.96 | -600 |
| k1 NONE | TRAIN | 22 695 | 11 976 (52.8 %) | 578 | 0 / 11 686 | 20.0 | -0.32 +- 0.12 | -4.80 +- 0.30 | -5.30 | -276 |
| k1 NONE | HOLDOUT | 32 486 | 16 338 (50.3 %) | 748 | 0 / 15 963 | 20.2 | -0.28 +- 0.11 | -4.55 +- 0.21 | -4.89 | -541 |
| k1 NONE | FRESH | 6 070 | 2 930 (48.3 %) | 116 | 0 / 2 873 | 20.3 | -0.15 +- 0.27 | -4.25 +- 0.48 | -5.04 | -600 |

The ranking is the fee bill: the tighter the quotes, the more fills and the larger the loss. "Best" = trades least.
The band freeze changes the result by 0.0-0.2 bps: it converts window-end exits into earlier ones, it does not remove them.

## 4. Why T1 loses: the outcome split (k = 1, BAND)
| | TRAIN | HOLDOUT | FRESH |
|---|---|---|---|
| windows with no fill (count 0) | 47.2 % | 49.7 % | 51.7 % |
| windows where every fill was paired off by the opposite quote | 1.15 % at +37.5 bps | 0.96 % at +35.5 bps | 0.66 % at +35.3 bps |
| windows that needed a forced taker exit | 51.6 % at -9.81 bps | 49.3 % at -9.57 bps | 47.6 % at -9.42 bps |
| net per window WITH a fill | -8.78 bps | -8.70 bps | -8.81 bps |
| fee bill per active window (2 x maker fills + 6.5 x forced exits) | 4.47 bps | 4.26 bps | 4.10 bps |
| gross per active window | -0.17 +- 0.08 | -0.12 +- 0.06 | -0.16 +- 0.13 |

A pair needs the price to cross from -1 sigma to +1 sigma inside 15 minutes: 543 pairs were completed in 16 338
filled HOLDOUT windows, and 98 % of the filled windows ended in a forced taker exit. The window with a fill loses 8.7 bps,
which is the bracket constant of this repository once more (maker in + taker out = 8.5 bps, no information).
Break-even needs a gross of +4.26 bps per active window (+8.5 per filled window); the measured gross is -0.12.
The negative gross is consistent with a through-fill: a bar that trades through the bid has, on average, gone past it
(about -0.2 bps per entry at k = 1, -0.8 at k = 0.25; interpretation, not a separate test).

## 5. Regimes, symbols and the EWMA gate (reported without a verdict)
T1 cell (k = 1, BAND) by ATLAS regime:

| partition | regime | windows | fill share | gross bps +- SE | net bps +- SE |
|---|---|---|---|---|---|
| TRAIN | LOW | 22 695 | 52.8 % | -0.17 +- 0.08 | -4.63 +- 0.29 |
| TRAIN | MID | 68 079 | 50.5 % | -0.04 +- 0.08 | -4.31 +- 0.13 |
| TRAIN | HIGH | 22 695 | 46.5 % | +0.10 +- 0.24 | -3.85 +- 0.35 |
| TRAIN | ALL | 113 469 | 50.1 % | -0.03 +- 0.07 | -4.28 +- 0.08 |
| HOLDOUT | LOW | 32 486 | 50.3 % | -0.12 +- 0.06 | -4.38 +- 0.19 |
| HOLDOUT | MID | 37 833 | 46.3 % | -0.04 +- 0.09 | -3.96 +- 0.17 |
| HOLDOUT | HIGH | 8 305 | 43.9 % | -0.80 +- 0.41 | -4.53 +- 0.71 |
| HOLDOUT | ALL | 78 624 | 47.7 % | -0.15 +- 0.07 | -4.19 +- 0.09 |
| FRESH | LOW | 6 070 | 48.3 % | -0.16 +- 0.13 | -4.25 +- 0.43 |
| FRESH | MID | 5 969 | 42.4 % | +0.22 +- 0.19 | -3.36 +- 0.34 |
| FRESH | HIGH | 345 | 40.0 % | +2.12 +- 1.12 | -1.29 +- 0.59 |
| FRESH | ALL | 12 384 | 45.2 % | +0.09 +- 0.12 | -3.74 +- 0.19 |

The quiet regime is not the better one: on every partition LOW nets less than MID. FRESH HIGH (gross +2.12 +- 1.12,
n 345, one 43-day window, opposite sign on HOLDOUT) is one of 36 regime cells and was not chosen: not a result.

T1 cell per symbol (LOW gate):

| partition | symbol | windows | with a fill | quote distance bps | gross bps +- SE | net bps +- SE |
|---|---|---|---|---|---|---|
| TRAIN | BTCUSDT | 7 565 | 3 665 | 10.6 | -0.22 +- 0.07 | -4.32 +- 0.24 |
| TRAIN | SOLUSDT | 7 565 | 4 185 | 24.3 | -0.02 +- 0.14 | -4.70 +- 0.30 |
| TRAIN | DOGEUSDT | 7 565 | 4 126 | 25.0 | -0.27 +- 0.14 | -4.88 +- 0.33 |
| HOLDOUT | BTCUSDT | 5 420 | 2 624 | 10.1 | -0.05 +- 0.07 | -4.14 +- 0.31 |
| HOLDOUT | SOLUSDT | 12 870 | 6 476 | 21.4 | -0.06 +- 0.09 | -4.33 +- 0.18 |
| HOLDOUT | DOGEUSDT | 14 196 | 7 238 | 22.9 | -0.20 +- 0.09 | -4.51 +- 0.17 |
| FRESH | BTCUSDT | 1 085 | 488 | 9.4 | -0.17 +- 0.09 | -3.96 +- 0.68 |
| FRESH | SOLUSDT | 2 401 | 1 169 | 22.0 | -0.29 +- 0.18 | -4.42 +- 0.37 |
| FRESH | DOGEUSDT | 2 584 | 1 273 | 23.3 | -0.03 +- 0.21 | -4.22 +- 0.38 |

0 of 9 symbol x partition cells are positive, gross or net.

EWMA gate (s = sqrt(15 x EWMA(r^2, half-life 120)), LOW below its TRAIN P20, band from the TRAIN z-quantiles):

| cell | TRAIN net (n) | HOLDOUT net +- SE (n) | FRESH net +- SE (n) | HOLDOUT gross |
|---|---|---|---|---|
| k0.25 BAND | -10.43 (22 695) | -10.26 +- 0.43 (30 478) | -10.41 +- 1.00 (5 787) | -1.07 +- 0.11 |
| k0.25 NONE | -10.59 (22 695) | -10.42 +- 0.44 (30 478) | -10.46 +- 1.05 (5 787) | -1.20 +- 0.14 |
| k0.5 BAND | -8.38 (22 695) | -8.08 +- 0.34 (30 478) | -8.16 +- 0.82 (5 787) | -0.35 +- 0.09 |
| k0.5 NONE | -8.54 (22 695) | -8.26 +- 0.35 (30 478) | -8.25 +- 0.88 (5 787) | -0.51 +- 0.13 |
| k1 BAND | -4.63 (22 695) | -4.20 +- 0.18 (30 478) | -4.17 +- 0.41 (5 787) | -0.06 +- 0.05 |
| k1 NONE | -4.74 (22 695) | -4.35 +- 0.20 (30 478) | -4.27 +- 0.49 (5 787) | -0.20 +- 0.11 |

The two gates give the same numbers within 0.2 bps: which volatility forecast draws the band does not matter.

## 6. Fee re-pricings of the T1 cell (hypotheses at fees this account does not have; no verdict)
| fee schedule (per maker fill / per forced exit, bps) | TRAIN | HOLDOUT | FRESH |
|---|---|---|---|
| VIP0 2 / 5 + 1.5 slip (primary) | -4.632 | -4.377 | -4.252 |
| BNB discount 1.8 / 4.5 + 1.5 | -4.263 | -4.025 | -3.914 |
| maker 0 / taker 5 + 1.5 | -3.521 | -3.324 | -3.252 |
| hypothetical rebate -0.5 / taker 5 + 1.5 | -3.243 | -3.060 | -3.002 |
| all-maker exits (upper bound: forced exits re-priced as maker fills, no slippage) | -2.309 | -2.157 | -2.109 |
| gross (no fee at all) | -0.166 | -0.117 | -0.157 |

Arithmetic outside the registered schedules: under the all-maker bound the break-even maker fee is gross / (fills per
window) = -0.15 / -0.11 / -0.16 bps (TRAIN / HOLDOUT / FRESH), i.e. a venue would have to PAY at least that per fill
and every forced exit would have to be filled passively at the forced price. Binance VIP0 charges +2.0; the bound is
not a tradable schedule.

## 7. Variance ratio by regime (VR(15) = second moment of the 15 m return / sum of squared 1 m returns; 1 = martingale)
| partition | regime | share of windows | VR(15) +- SE | mean abs 15 m return bps | 1 m sd bps |
|---|---|---|---|---|---|
| TRAIN | LOW | 20.0 % | 0.951 +- 0.020 | 16.0 | 6.19 |
| TRAIN | MID | 60.0 % | 0.945 +- 0.014 | 25.2 | 9.71 |
| TRAIN | HIGH | 20.0 % | 0.753 +- 0.139 | 43.7 | 19.83 |
| TRAIN | ALL | 100.0 % | 0.839 +- 0.089 | 27.0 | 11.95 |
| HOLDOUT | LOW | 41.3 % | 0.940 +- 0.022 | 15.2 | 6.22 |
| HOLDOUT | MID | 48.1 % | 0.931 +- 0.019 | 21.8 | 8.88 |
| HOLDOUT | HIGH | 10.6 % | 0.882 +- 0.057 | 34.2 | 14.56 |
| HOLDOUT | ALL | 100.0 % | 0.918 +- 0.020 | 20.4 | 8.74 |
| FRESH | LOW | 49.0 % | 0.868 +- 0.062 | 14.7 | 6.36 |
| FRESH | MID | 48.2 % | 0.918 +- 0.051 | 19.0 | 7.93 |
| FRESH | HIGH | 2.8 % | 0.886 +- 0.072 | 24.0 | 10.20 |
| FRESH | ALL | 100.0 % | 0.897 +- 0.036 | 17.0 | 7.28 |

- The gate ranks volatility out of sample (|15 m move| LOW 15.2 < MID 21.8 < HIGH 34.2 bps on HOLDOUT).
- VR(15) is below 1 in every regime (5-13 % less 15 m variance than a random walk built from the 1 m closes). It is
  NOT lower in the quiet regime: LOW 0.940 +- 0.022 vs MID 0.931 +- 0.019 on HOLDOUT. A low-volatility forecast does
  not select mean reversion.
- That much reversion in 1 m closes did not reach the quoter: gross is <= 0 in all 36 LOW-gated cell x partition
  numbers (6 ATLAS + 6 EWMA cells x 3 partitions, sections 3 and 5). A VR of 0.94 measured on last-trade closes
  includes bid-ask bounce, which a quote 5-20 bps away cannot collect.

## 8. What it means for 5 U
T1 at one 5.05 U lot per side on the three symbols: HOLDOUT 119 active windows a day, -521 bps of notional a day =
-0.263 U / day; FRESH -0.303 U / day; TRAIN -0.135 U / day. From 5 U that is about 19 days (HOLDOUT rate) to zero, and
the 0.15 U daily kill would stop it every single day. No sizing rule changes the sign: every unit of turnover costs
about 8.7 bps per filled window.

## 9. Implementation notes and errata (the plan file is unchanged; its hash still verifies)
The module committed at 29d754d was a skeleton that had never been run (no output existed). Before the first run it
was checked line by line against plan section 3, and three gaps were closed; no outcome had been seen:
1. Inventory whose exit quote lies beyond the band edge ("a quote beyond the band edge is not posted"): the skeleton
   credited the unposted quote. Now that inventory leaves as a taker at the band edge. It concerns 0 LOW windows at
   k <= 1 (the band is always wider than 2 sigma there), so no reported LOW number depends on it.
2. "Active window (quotes posted)": windows with no postable quote are excluded from the unit. Count in this run: 0.
3. One bar that fills both quotes and breaks both band edges: the worse of the two orders is taken (the skeleton always
   took the lower one). Same-bar ambiguity "against the strategy", as the plan says.
A far-edge band break in a bar where only one quote could fill ends the window with nothing filled (the plan's "band
exit before the favourable fill", read literally).
The simulator is pinned by 9 hand-case tests (20 scenarios) written from the plan text, by a scalar reference implementation compared
window by window on 8 000 random paths, and by two property tests: a fine-grained martingale gives |gross| < 0.05
sigma per window and a negative net; an Ornstein-Uhlenbeck path gives a gross 300 standard errors above zero. The
study was run twice: the two JSON outputs are identical apart from the timer.

## 10. Limits of this evidence
- 1 m klines: the order of events inside a bar is unknown (resolved against the strategy), there is no queue, no
  latency and no touch fill. A through-fill rule is conservative on fill COUNT (a real quote also fills on a touch
  when the queue clears) and those extra fills are the least adversely selected ones; depth data is needed to price
  them. The band-edge exit price is optimistic by the overshoot (1.5 bps of slippage is charged for it).
- One unit per side, one level, re-centred every 15 minutes. Inventory-carrying grids, skewed quotes and multi-level
  ladders were not tested; the all-maker-exit bound (-2.2 bps) is the upper limit of any variant that only changes
  how inventory is carried out.
- The two-sided test reuses HOLDOUT and FRESH, which ATLAS-K1 already read with directional rules. The result is a
  rejection, so the reuse does not flatter it.
- Funding is ignored (holds are at most 15 minutes). Three symbols; nothing is said about illiquid alts, where the
  spread is wider and so is the adverse selection.

## 11. Reproduce
```
python -m research.non_directional_grid_study        # 88 s; TRAIN choice, then HOLDOUT, then FRESH -> data/non_directional_grid_study.json
python -m pytest -q tests/test_non_directional_grid.py   # 22 passed
```
