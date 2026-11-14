# ATLAS-K1 — probabilistic +15 m density forecasters, tail trigger and carry overlay (pre-registration, frozen before any kline outcome)

Owner directive 2026-11-14 (f) ("Atlas autoresearch sprint"): (1) inspect the 8 ATLAS forecasters and their pinball
loss on BTCUSDT / SOLUSDT / DOGEUSDT, (2) run >= 25 mutation rounds minimising the quantile error of the +15 m return
distribution, (3) test whether the ensemble isolates asymmetric tails that pay more than the fees, (4) evaluate the
funding-carry lane with an ATLAS overlay sized only at >= 85 % confidence, (5) project a 5 U bankroll.
Offline research only: no order, no engine start, no live, no lock, gate or cap changed, nothing wired.

## 0. Premises corrected before anything runs
- `scripts/autoresearch_agent_swarm.py` holds 8 forecasters of THREE families (NORO fair value x3, LUMEN flow x3, TIDAL
  momentum x2). ZEPHR, RUNE, VESKA, OKAPI and MARIN are gates / execution agents of the live swarm, not forecasters.
- "Carry yielded +16 bps holdout" is +16.0 bps per 28-DAY round on notional (XV HEDGE|H28|k2, n 165, CI95 +10.9 .. +21.2,
  about 2 % / yr; its freshest month had n = 1). It is not +16 bps per trade.
- The L2 / tick recordings on disk are small: BTC and SOL about 4.6 h of top of book each (2026-09-08..10), DOGE 10.3 h
  (2026-11-10, rebuilt from depth@100ms by `tools/rec_to_l2_touch.py`). That is 12-40 non-overlapping 15 m windows per
  symbol. The L2 loop is run as asked (section 1) and gets NO verdict; the verdict comes from 1 m klines (section 2).
- A pinball loss falls when the SCALE (volatility) of the forecast improves. Only the LOCATION (conditional mean /
  median) can pay a linear futures position. The two are reported separately (M3).

## 1. L2 loop (already run before this freeze, descriptive only)
`scripts/autoresearch_agent_swarm.py --rounds 30 --seed 20261114`, fresh default population, per symbol and pooled,
last 30 % of the time-ordered points as holdout (`data/atlas_l2_*_report.json`). Reported with the number of
non-overlapping windows and a paired block SE against the no-signal baseline. No decision rests on it.

## 2. Kline study (the test)
Data: Binance USD-M 1 m klines of BTCUSDT, SOLUSDT, DOGEUSDT: `data/klines_1m/` 2024-12 .. 2026-09 and
`data/klines/fresh/` 2026-10-01 .. 2026-11-12 (SHA-256 checked at download). Target y = ln(close[i+15] / close[i]).
Decision points: the close of every bar whose open minute is 14, 29, 44 or 59 (decisions at :00 / :15 / :30 / :45 UTC),
so targets do not overlap. A point needs 2 880 gap-free bars behind it and 15 ahead.
Partitions by decision time (the target must end inside the same partition):
TRAIN 2024-12-01 .. 2025-12-31 | HOLDOUT 2026-01-01 .. 2026-09-30 | FRESH 2026-10-01 .. 2026-11-12.
HOLDOUT has been read by other rules (SMCF-1, MLS-1, the 2026-11-06 kline trade loop); FRESH by MLS-2 only. Neither has
been read by a density forecaster. No number of HOLDOUT or FRESH is computed before the loop has ended.

Forecaster (the script's, in bars): demeaned 1 m log returns of the last `lookback` bars; 400 futures, each the sum of
15 bars drawn as blocks of `block` bars from a fixed table of uniforms (seed 20261114, common to all points and
configurations, column prefix shared across block sizes); forecast = vol_mult x sums + coef x signal x sd x sqrt(15);
quantiles 5, 10, 25, 50, 75, 90, 95 %. Signals in [-1, 1] over `signal` bars: NORO tanh(distance of the log close from
its window mean / (sd sqrt(w))), LUMEN mean taker imbalance (2 x taker buy - volume) / volume, TIDAL tanh(momentum z).
Bounds: lookback 60-2880, block 1-30, signal 5-2880 (<= lookback), coef -1.5..1.5, vol_mult 0.5-2.0.
Initial population (lookback, block, signal, coef; vol_mult 1): NORO-1 1440/5/240/-0.3, NORO-2 720/3/60/-0.2,
NORO-3 2880/15/720/-0.5, LUMEN-1 1440/5/15/0.3, LUMEN-2 360/1/5/0.2, LUMEN-3 720/3/60/0.4, TIDAL-1 1440/5/60/0.2,
TIDAL-2 2880/15/240/0.3. Baseline B0: 1440/1, coef 0, vol_mult 1 (no signal).
Reference REF (outside the family, nothing tuned): quantile = s x z, s^2 = 15 x EWMA(r^2, half-life 120 bars), z = the
TRAIN quantiles of y / s pooled over the symbols.

Loop: 60 rounds (>= 25 asked). Each round: survivor weights (top quartile x1.05, bottom x0.95, clamp 0.3-2.5), ONE
parameter of the WORST forecaster mutated (`mutate` of the script, rng seed [20261114, round]), kept iff its TRAIN
objective falls. Objective = mean over the three symbols of (pinball loss / B0 loss on the same points): scale-free,
each symbol weighs one third. Ensemble E = quantile-wise average with the final survivor weights (E-eq: equal weights).
After the loop, and for reporting only: the TRAIN / HOLDOUT loss of E after every round, from the saved history.

Metrics on TRAIN / HOLDOUT / FRESH, per symbol and as the equal-symbol mean; every SE clustered by UTC day:
- M1 pinball loss (bps), skill 1 - L / L_B0, paired difference to B0 and to REF.
- M2 calibration: empirical coverage of the 7 quantiles, of the central 50 % and 90 % intervals.
- M3 location skill: L(E) - L(E with every coef set to 0), paired. Negative = the signals carry information.
- M4 direction: correlation of E's median with y; mean of sign(median) x y in bps over all points and over the points
  whose |median| / sd is in the TRAIN top decile.

## 3. Trading rules on the ensemble (hold 15 bars, exit at the close as taker)
Fills: MT (primary) post-only at the decision close, filled only if the next bar trades through it (low < close for a
buy, high > close for a sell), cost 8.5 bps (maker 2 + taker 5 + 1.5 spread / slippage, `core.early_stop`);
TT taker on both legs, always filled, cost 13 bps. c = the cost of the fill. Q(p) = E's quantile function, linear
between its knots; m = Q(0.5).
- P85 (the directive's rule): long iff Q(0.15) > +c, short iff Q(0.85) < -c, i.e. P(net > 0) >= 85 %.
- D_p, p in {0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90}: long iff Q(1 - p) > 0, short iff Q(p) < 0.
- EV_h, h in {0.25, 0.5, 1.0}: side = sign(m) iff |m| >= h x c.
- TAIL_a, a in {0.05, 0.10, 0.20}: A = [(q95 - q50) - (q50 - q05)] / (q95 - q05); side = sign(A) iff |A| >= a.
- AGREE85: the weighted share of forecasters whose drift has the sign of m is >= 0.85; AGREE85_EV: and |m| >= 0.25 c.
- STACK (upper bound of a linear location model on the same signals): OLS on TRAIN of y / (sd1440 x sqrt(15)) on the 8
  final signals + intercept, pooled; mu = fitted x sd1440 x sqrt(15); rules EV_h on mu, h in {0.25, 0.5, 1.0}.
That is 17 + 3 rules x 2 fills = 40 cells. Per cell and partition: n, mean net bps +- SE, win rate, mean win, mean
loss, payoff, break-even win rate, gross mean, and a random-side placebo (same points, fills and costs, 200 draws).

Tests (read once, in this order: TRAIN choice, HOLDOUT, FRESH):
- T1 = P85 x MT. EDGE iff HOLDOUT n >= 100 and mean - 1.645 SE > 0 and (mean - placebo) - 1.645 SE > 0 and FRESH
  mean > 0 with FRESH n >= 20. HOLDOUT n < 30 = NO OPPORTUNITY SET (the rule does not fire).
- T2 = the cell with the highest TRAIN mean net among cells with TRAIN n >= 300. Same criteria, plus deflated Sharpe
  >= 0.95 (null variance 1 / n, 40 trials). No cell reaches TRAIN n >= 300 = NO OPPORTUNITY SET.
Anything else is NO EDGE. A positive cell that was not chosen on TRAIN is a hypothesis, not a result.

## 4. Carry + overlay (directive task 3)
- Carry sleeve: `tools/funding_spread_replay.simulate('XV', coin, 28, 2, 'HEDGE')` on the funding cache (no network),
  HOLDOUT entries 2026-01-01 .. 2026-09-30, reduced to ONE slot (earliest entry, then the larger signal; no overlap):
  net bps per 28-day round and time in the market. Fundability at 5 U from `core.funding_carry.two_leg_capital_gate`
  (10 U two-leg minimum) and the venue minimum order sizes; if the gate refuses, the sleeve is reported both as
  NOT FUNDABLE (0) and as the hypothetical 5 U per leg.
- Overlay sleeve: the T1 trades and the T2 trades on HOLDOUT at the micro lot (5.05 U notional, one position at a time,
  0.05 U budget = a 99 bps adverse move over the hold).
- Trajectory: 2 000 paths x 30 / 90 / 365 days from 5 U; carry rounds and overlay days bootstrapped from HOLDOUT; p05,
  median, p95, P(+10 U), P(< 4 U). It shows what the two sleeves do IF the HOLDOUT distributions were the truth.
- The overlay adds value only if its mean net bps has LCB95 > 0 on HOLDOUT; otherwise hybrid = carry alone and the
  overlay is a cost.

## 5. What any result allows
Nothing operational. EDGE motivates a shadow lane (signals and virtual fills, no order) and a pre-registered read on
months after 2026-11-12. NO EDGE / NO OPPORTUNITY SET leaves every gate, lock and cap as it is; the survivor weights
are written to `data/` only (never `config/live_tunables.json`).
