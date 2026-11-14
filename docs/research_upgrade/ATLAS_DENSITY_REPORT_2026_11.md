# ATLAS-K1 report — probabilistic +15 m forecasters, tail trigger, carry overlay (2026-11-14)

Plan (frozen before any kline outcome): `plan_atlas_density_2026_11.md`, SHA-256 `d9943d77...e3e5`, 2026-11-14T08:02:49Z.
Code: `research/atlas_density_study.py`, `tools/rec_to_l2_touch.py`, `tests/test_atlas_density.py` (23 tests).
Numbers: `data/atlas_density_study.json`, `data/atlas_l2_summary.json` (git-ignored; reproduce with section 9).
Offline research: no order, no engine start, no live, no gate / lock / cap changed, nothing wired.

## 0. Verdict
- The ensemble forecasts VOLATILITY, not direction. Out of sample it beats the no-signal baseline by 1.6 % of pinball
  loss, loses to a plain EWMA volatility scale by 1.3 %, and its three signal families add nothing: location skill
  -0.0006 +- 0.0007 bps (HOLDOUT), -0.0020 +- 0.0015 (FRESH).
- The directive's trigger (>= 85 % confidence of a net-positive trade) fired 0 times in 204 477 decision points.
  It needs a conditional mean of 33-47 bps per 15 min; the ensemble's largest median on HOLDOUT is 28 bps, its 99th
  percentile 6 bps. T1 = NO OPPORTUNITY SET.
- The best of 40 rule x fill cells on TRAIN (TAIL_0.1, maker in / taker out) is -8.84 bps on TRAIN, -9.31 +- 0.69 on
  HOLDOUT (n 6 687), -11.61 +- 2.32 on FRESH. Gross per trade is -3.0 .. +3.0 bps in every cell with n >= 500, against
  8.5 / 13 bps of costs. T2 = NO EDGE.
- Carry + overlay: the overlay is either empty (T1) or a cost (T2: -0.078 U / day, the 5 U account cannot margin one
  lot after a median 52 days). The carry sleeve is refused at 5 U (two-leg minimum 10 U); if it were fundable at 5 U
  per leg it would add +0.008 U per 28-day round (median 5.10 U after a year). P(+10 U) = 0 in every variant.

## 1. What was inspected (and three premises corrected)
- `scripts/autoresearch_agent_swarm.py`: 8 forecasters of THREE families — NORO fair value x3, LUMEN flow x3, TIDAL
  momentum x2. Each forecasts the +15 m log return as 400 block-bootstrap futures of its own lookback window plus a
  drift `coef x signal x sd x sqrt(horizon)`; loss = mean pinball over the 5 / 10 / 25 / 50 / 75 / 90 / 95 % quantiles.
  ZEPHR, RUNE, VESKA, OKAPI, MARIN are gates of the live swarm, not forecasters.
- Stored earlier runs (2026-11-06): L2 pinball, 40 rounds, BTC: train 2.864 -> 2.664, holdout 4.210 -> 4.416 (worse than
  the baseline 4.261); kline TRADE objective, 4 x 300 rounds: holdout portfolio -1.5 .. -23.7 bps per trade,
  `any_meets_criteria` false in all four.
- "Carry +16 bps holdout" = +16.0 bps per 28-DAY round on notional (2.09 % / yr), not per trade.
- Recordings on disk: BTC / SOL top of book 2026-09-08..10 (about 16 500 s each, 238 decision points), DOGE depth@100ms +
  snapshots 2026-11-10 02:07..12:28 UTC (37 235 s after the book rebuild, 1 152 points). The detached recorder of
  2026-11-10 stopped after 10.4 h without a log line (`recorder.pid` 5832 is stale): the week of depth data that
  `plan_micro_adverse_selection.md` counted on for 2026-11-17 does not exist.

## 2. L2 loop (30 rounds, fresh population, seed 20261114) — descriptive only
`python scripts/autoresearch_agent_swarm.py --data "data/l2/<SYM>/*.jsonl" --rounds 30 --seed 20261114`; mean pinball of the
8 forecasters in bps; last 30 % of the time-ordered points = holdout.

| run | kept | train start -> end | holdout start -> end | baseline holdout | ensemble - baseline (holdout) | 15 m windows in holdout |
|---|---|---|---|---|---|---|
| BTCUSDT | 14 / 30 | 2.8517 -> 2.7695 | 4.1923 -> 4.2067 | 4.1976 | -0.087 +- 0.028 | 3 |
| SOLUSDT | 12 / 30 | 4.1626 -> 4.0702 | 3.9649 -> 3.9557 | 3.8633 | -0.040 +- 0.027 | 3 |
| DOGEUSDT | 15 / 30 | 4.9711 -> 4.9226 | 4.5237 -> 4.4766 | 4.4987 | -0.064 +- 0.061 | 12 |
| pooled | 12 / 30 | 4.3354 -> 4.2989 | 4.9107 -> 4.8374 | 4.8667 | -0.121 +- 0.057 | 17 |

Train loss falls 1-3 % in every run. The holdouts hold 3 to 17 non-overlapping 15-minute windows, so no sign there
means anything (BTC got worse, SOL stayed above its baseline). This is why the test was moved to klines.

## 3. Kline loop: 60 TRAIN-only rounds (BTC / SOL / DOGE, decisions every 15 min)
TRAIN 2024-12..2025-12: 113 469 points (394 days). HOLDOUT 2026-01..09: 78 624 (273 days). FRESH 2026-10-01..11-12:
12 384 (43 days). Objective = pinball / baseline pinball, mean over the three symbols. HOLDOUT columns were computed
after the loop from the saved history and decided nothing.

| round | kept so far | mean TRAIN objective of the 8 | ensemble TRAIN (rel. B0 / bps) | ensemble HOLDOUT (rel. B0 / bps) |
|---|---|---|---|---|
| 0 | 0 | 1.00403 | 0.98643 / 8.1962 | 0.98448 / 6.3697 |
| 1 | 1 | 1.00283 | 0.98617 / 8.1941 | 0.98436 / 6.3690 |
| 5 | 3 | 1.00258 | 0.98556 / 8.1895 | 0.98407 / 6.3673 |
| 10 | 5 | 1.00065 | 0.98501 / 8.1853 | 0.98384 / 6.3659 |
| 15 | 6 | 1.00001 | 0.98450 / 8.1818 | 0.98355 / 6.3641 |
| 20 | 8 | 0.99907 | 0.98463 / 8.1829 | 0.98373 / 6.3654 |
| 25 | 11 | 0.99901 | 0.98465 / 8.1831 | 0.98379 / 6.3657 |
| 30 | 15 | 0.99852 | 0.98456 / 8.1825 | 0.98379 / 6.3657 |
| 40 | 21 | 0.99706 | 0.98452 / 8.1820 | 0.98380 / 6.3659 |
| 50 | 22 | 0.99694 | 0.98448 / 8.1817 | 0.98388 / 6.3664 |
| 60 | 24 | 0.99671 | 0.98433 / 8.1805 | 0.98377 / 6.3657 |

- 24 of 60 mutations kept; parameters tried / kept: lookback 9 / 5, block 12 / 3, signal 12 / 9, coef 12 / 5,
  vol_mult 15 / 2. The loop only mutates the worst forecaster: NORO-3, TIDAL-2, TIDAL-1 and NORO-1 took all 60
  mutations; NORO-2 and the three LUMEN kept their initial settings.
- The kept coefficient moves shrink the drift (one flips its sign): NORO-3 -0.50 -> +0.09, TIDAL-1 +0.20 -> +0.10, TIDAL-2 +0.30 -> -0.24.
- What the 60 rounds bought (final ensemble minus round-0 ensemble, paired, SE clustered by day): TRAIN -0.0157 +-
  0.0051 bps; HOLDOUT -0.0041 +- 0.0036; FRESH -0.0016 +- 0.0104. The HOLDOUT loss was lowest at round 15 and drifted
  back: the later rounds fitted TRAIN.
- 96 % of the ensemble's HOLDOUT advantage over the baseline was already there at round 0 (1.55 % of 1.62 %): it comes
  from averaging quantiles of forecasters with different lookbacks and blocks, not from the mutations.

## 4. Pinball loss, calibration and where the skill is

| pinball bps (equal-symbol mean) | TRAIN | HOLDOUT | FRESH |
|---|---|---|---|
| B0 no-signal baseline (1 440 / 1) | 8.3055 | 6.4698 | 5.4665 |
| E ensemble, survivor weights | 8.1805 | 6.3657 | 5.3542 |
| E with every coef = 0 | 8.1792 | 6.3662 | 5.3562 |
| E, equal weights | 8.1922 | 6.3683 | 5.3590 |
| REF: EWMA scale (half-life 120 bars) x TRAIN z-quantiles | 8.0830 | 6.2860 | 5.2505 |
| E - B0 (paired) | -0.1250 +- 0.0114 | -0.1041 +- 0.0089 | -0.1123 +- 0.0209 |
| E - REF (paired) | +0.0976 +- 0.0110 | +0.0797 +- 0.0088 | +0.1037 +- 0.0132 |
| location: E - E(coef 0) | +0.0013 +- 0.0006 | -0.0006 +- 0.0007 | -0.0020 +- 0.0015 |

HOLDOUT by symbol (E / B0 / REF): BTC 4.713 / 4.797 / 4.637, SOL 7.040 / 7.150 / 6.951, DOGE 7.343 / 7.462 / 7.271.
- Calibration on HOLDOUT (share of outcomes at or below each quantile; nominal 5 / 10 / 25 / 50 / 75 / 90 / 95 %):
  E 4.5 / 8.1 / 21.4 / 50.1 / 78.6 / 92.0 / 95.6 — the central 50 % band covers 57.2 % and the 90 % band 91.1 % (too wide
  in the middle: the bootstrap mixes quiet and busy hours of the lookback). REF 54.4 % / 90.4 %. B0 60.5 % / 91.5 %.
- Direction on HOLDOUT: corr(ensemble median, outcome) BTC -0.008, SOL +0.008, DOGE +0.008. sign(median) x outcome:
  -0.04 +- 0.15 bps over all points, -0.42 +- 0.46 in the top decile of |median| / sd (TRAIN -0.40 +- 0.17 and
  -0.91 +- 0.68; FRESH +0.04 +- 0.32 and +1.99 +- 0.84, n 1 544: one 43-day window, opposite sign to TRAIN).
- Upper bound for a linear location model on the same 8 signals (OLS on TRAIN): R2 +0.00026 on TRAIN (corr 0.016),
  -0.00020 on HOLDOUT (corr 0.002), -0.00017 on FRESH (corr -0.006).
- Reading: the whole improvement is SCALE (how wide the next 15 minutes will be). A linear futures position is paid
  by LOCATION only, and that is zero here. A fixed EWMA does the scale part better than the 8 forecasters.

## 5. Tail trigger and trading rules (15 min hold, HOLDOUT)
Costs: MT = post-only entry filled on a trade-through + taker exit, 8.5 bps; TT = taker both legs, 13 bps.

**T1, the directive's rule (P85 x MT): 0 decisions** on TRAIN, HOLDOUT and FRESH. Arithmetic: P(net > 0) >= 85 % needs a
conditional mean >= cost + 1.036 sd = 33.1 bps (BTC), 45.0 (SOL), 46.6 (DOGE) per 15 minutes on HOLDOUT (sd of the
15 m return 23.7 / 35.2 / 36.8 bps). The ensemble's |median|: mean 1.2 bps, p99 6.0, max 28.0. Even without the cost
hurdle, 65 % directional confidence occurred 2 times in 78 624 points, 60 % 61 times, 55 % 1 869 times (2.4 %).
For such a trigger to fire in 0.1 % / 1 % / 5 % of the periods at zero cost, the forecast must explain 9.0 % / 13.9 % /
21.9 % of the variance of the 15 m return. Measured: 0.026 % in sample, <= 0 out of sample.

| cell (MT, 8.5 bps) | n | net bps +- SE | gross | win rate | mean win / loss | break-even win rate | FRESH net (n) |
|---|---|---|---|---|---|---|---|
| D_0.55 (55 % direction) | 1 627 | -11.46 +- 2.06 | -2.96 | 34.0 % | 24.3 / 29.9 | 55.1 % | -4.49 +- 3.44 (196) |
| EV_0.25 (|median| >= 2.1 bps) | 11 238 | -9.45 +- 0.78 | -0.95 | 37.6 % | 28.8 / 32.5 | 53.0 % | -6.90 +- 1.64 (1 209) |
| EV_0.5 (>= 4.25 bps) | 2 133 | -8.79 +- 2.04 | -0.29 | 40.6 % | 37.3 / 40.3 | 51.9 % | -1.06 +- 3.29 (170) |
| EV_1 (>= 8.5 bps) | 202 | +2.02 +- 6.12 | +10.52 | 47.0 % | 51.7 / 42.1 | 44.9 % | n 2 |
| TAIL_0.05 (fat tail side) | 25 590 | -9.60 +- 0.32 | -1.10 | 30.5 % | 22.6 / 23.8 | 51.2 % | -10.37 +- 0.93 (3 538) |
| **TAIL_0.1 = T2** | 6 687 | -9.31 +- 0.69 | -0.81 | 30.7 % | 24.8 / 24.4 | 49.7 % | -11.61 +- 2.32 (801) |
| TAIL_0.2 | 528 | -9.18 +- 2.67 | -0.68 | 34.3 % | 28.4 / 28.8 | 50.3 % | -7.82 +- 6.03 (46) |
| AGREE85 (>= 85 % of weights agree) | 9 459 | -9.83 +- 0.42 | -1.33 | 31.9 % | 19.0 / 23.4 | 55.2 % | -9.33 +- 0.96 (1 461) |
| AGREE85_EV | 2 432 | -10.62 +- 1.19 | -2.12 | 37.0 % | 24.6 / 31.3 | 56.0 % | -6.58 +- 2.21 (251) |
| STACK_EV_0.25 (OLS location) | 300 | -15.83 +- 5.80 | -7.33 | 45.3 % | 51.2 / 71.4 | 58.2 % | -19.55 +- 13.26 (18) |
| random side, same points (MT) | — | about -9.6 | -1.1 | — | — | — | — |

- T2 (the cell with the best TRAIN mean among those with TRAIN n >= 300) = TAIL_0.1 x MT: TRAIN -8.84 (n 4 800, i.e.
  the least negative cell), HOLDOUT -9.31 +- 0.69, vs placebo -9.63, FRESH -11.61 +- 2.32, deflated Sharpe 0.00: NO EDGE.
- "Asymmetric tails": the predicted fat-tail side wins 30.5-34.3 % of the time with a payoff ratio of 0.95-1.01;
  break-even needs 49.7-51.2 %. Win rate x payoff is 7.6 bps earned against 16.9 bps lost per trade (TAIL_0.1).
- Taker fills (13 bps): the 8 cells with n >= 500 net -10.0 .. -14.7 bps; gross -1.7 .. +3.0.
- The only positive HOLDOUT cell with n >= 100 is EV_1 x MT (+2.02 +- 6.12, n 202). It was -21.50 on TRAIN (n 533), was
  not chosen, and has 2 FRESH trades: noise until shown otherwise.
- The MT fill itself costs about 1.1 bps (a resting order fills when the next bar trades through it).

## 6. Carry + overlay
- Carry sleeve, XV HEDGE|H28|k2, funding cache only, HOLDOUT entries 2026-01..09: n 165, +16.01 bps per 28-day round
  (CI95 by entry day +10.9 .. +21.2, by month +10.0 .. +22.8), win 72.7 %, funding 30.6 - cost 15.0 + basis 0.4
  (sd 6.7), 2.09 % / yr on notional. Entries from 2026-10-01 whose 28 days end inside the cache: n 4, +14.3 bps,
  CI95 -9.2 .. +22.8: uninformative.
- One slot (what one small account can hold): 10 rounds, +36.7 bps mean (ETH, LTC, TAO; the larger signal wins a tie),
  always in the market. n = 10: not a stable estimate; the 165-round mean is the defensible one.
- Fundability at 5 U: `two_leg_capital_gate(5.0)` -> RESEARCH_ONLY, "EQUITY 5.0000 < TWO_LEG_MIN 10.00 USDT". The sleeve
  cannot be opened with this bankroll. Hypothetical 5 U per leg: 16.0 bps x 5 U = 0.008 U per 28-day round.
- Overlay T1 (>= 85 %): 0 trades in 273 days. Overlay T2 at the 5.05 U micro lot, one position at a time: 17.2 trades
  per day at -8.90 bps = -0.0775 U per day.

## 7. 5 U trajectory (2 000 paths; HOLDOUT distributions taken as the truth; a path stops when it cannot margin one lot)

| sleeves | 30 d p05 / median / p95 | 90 d | 365 d | P(+10 U) | P(ruin) by 90 d |
|---|---|---|---|---|---|
| carry as the gate rules (not fundable), no overlay or overlay T1 | 5.000 / 5.000 / 5.000 | 5.000 | 5.000 | 0 | 0 |
| carry hypothetical 5 U per leg (any round, mean 16.0 bps) | 4.975 / 5.011 / 5.034 | 4.976 / 5.026 / 5.067 | 5.006 / 5.105 / 5.201 | 0 | 0 |
| carry hypothetical 5 U per leg (one-slot rounds, mean 36.7 bps, n 10) | 4.995 / 5.017 / 5.043 | 5.017 / 5.054 / 5.091 | 5.162 / 5.239 / 5.317 | 0 | 0 |
| overlay T2 (TRAIN-best rule), carry not fundable | 1.895 / 2.669 / 3.416 | 0.786 / 0.947 / 1.005 | stopped | 0 | 1.000 (median day 52) |
| overlay T2 + carry hypothetical (one slot) | 1.910 / 2.685 / 3.433 | 0.780 / 0.947 / 1.005 | stopped | 0 | 1.000 (median day 52) |

The hybrid is carry alone. The overlay the directive describes never trades; any overlay that does trade removes
about 1.5 % of the bankroll per day. In the engine the net-EV gate (LCB95 of net bps > 0 over >= 30 trades) would
refuse the T2 overlay from its first signal.

## 8. Limits of this evidence
- Klines carry no depth: LUMEN uses taker imbalance instead of book imbalance. Book-based signals at 1 s could only be
  scored on 4.6-10.3 h of recordings (section 2).
- Only 4 of the 8 forecasters were mutated (the loop's rule). The stacked OLS covers the question "what if every
  coefficient were fitted": no location skill either.
- MT fills are the repository's convention (trade-through on the next 1 m bar, no queue model); costs are VIP0 without
  rebates. Both lean optimistic for a maker entry; the result is negative regardless.
- HOLDOUT 2026-01..09 had been read by other rule families (never by a density model); FRESH has 43 days.
- Additions after the first complete run, reporting only (cells, losses and both test verdicts are bit-identical
  between the two runs): the trajectory stops a path that cannot margin one lot (the first version let equity go
  negative), a second hypothetical carry row drawing from all 165 rounds, and the paired "final minus round 0" gain.
- `data/atlas_k1_tunables_proposal.json` (LUMEN 1.8, NORO 0.54, TIDAL 0.44) must NOT be published: the weights reward
  volatility calibration (LUMEN-2's short lookback), not flow information.

## 9. Reproduce
```
python tools/rec_to_l2_touch.py --symbol DOGEUSDT
python scripts/autoresearch_agent_swarm.py --data "data/l2/BTCUSDT/*.jsonl" --rounds 30 --seed 20261114 --state data/atlas_l2_btcusdt_agents.json --ledger data/atlas_l2_btcusdt_ledger.json --proposal data/atlas_l2_btcusdt_proposal.json --report data/atlas_l2_btcusdt_report.json
python -m research.atlas_density_study --l2
python -m research.atlas_density_study          # 424 s: 60 rounds on TRAIN (186 s), then HOLDOUT / FRESH once
python -m pytest -q tests/test_atlas_density.py
```
