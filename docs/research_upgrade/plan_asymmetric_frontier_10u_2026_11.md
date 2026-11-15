# AF-1 — asymmetric frontiers for a 10 USDT base: maker conversion, CEX-DEX arithmetic, longshot contracts (pre-registration, frozen before any maker-entry outcome is simulated and before any Kalshi trade sample is downloaded)

Owner directive 2026-11-15 (b): leave single-venue taker speculation and test three structures for a 10 U account:
(A) maker rebates on Hyperliquid to turn the gross momentum of HWK-1 into net yield, (B) CEX-DEX latency
dislocation, (C) binary event contracts bought at 1-2 cents. Offline research on public data only: no order, no
engine start, no live, no account, no lock, gate or cap changed, nothing wired, no git operation.

## 0. Premises corrected before anything runs
- "Hyperliquid tier 1 maker rebate of -1.5 to -3.0 bps (-0.015 % to -0.03 %)." The venue's fee page (read
  2026-11-15) lists perps tier 0 at taker 0.045 % and maker **+0.015 %** (a fee), falling to maker 0.000 % above
  500 M USD of 14-day volume. Rebates are **-0.001 %, -0.002 %, -0.003 %** (-0.1 to -0.3 bps) and require more than
  0.5 %, 1.5 %, 3.0 % of the venue's 14-day maker volume. The directive's figures are ten times the largest rebate
  and no tier pays them. A 10 U account is tier 0: its maker order costs 1.5 bps. The directive's figures are run as
  labelled hypothetical schedules, not as the venue's.
- "HWK-1 proved that gross momentum of +14 to +18 bps exists and is consumed by Binance fees." HWK-1 measured gross
  +14.6 (TRAIN), +18.8 (HOLDOUT) and **-27.7** (FRESH, negative before any fee) with a TAKER entry; of its 14.2 bps of
  cost 8.2 were fees and 6 were assumed slippage. A maker entry is a different fill process: a resting order on the
  momentum side fills when the price comes back. Whether the fills keep the gross is the test (T-A).
- Hyperliquid has no free history of its own 1 m bars before the last 5 000 candles, and lists a subset of the
  HWK-1 symbols. T-A replays Binance 1 m paths with Hyperliquid's fee schedule: a re-pricing on a proxy path, not a
  Hyperliquid backtest.
- T-A reuses HWK-1's HOLDOUT and FRESH, which were read once for the taker rule on the same signals. A pass on
  reused partitions is a CANDIDATE that needs unseen symbol-days; a fail is final.
- (B) A CEX-DEX trade is two legs on two venues, not atomic; it needs inventory on both; the gap closes inside one
  block for whoever is first. No block-level swap data is on disk and 1 m bars cannot show that race. B is cost
  arithmetic with stated assumptions, without a verdict.
- (C) "1-2 cent contracts with 50-100x payoff" are the longshot side of the favourite-longshot bias, which the
  literature reports as overpriced (from memory, not re-read). A taker also pays 7 % x (1 - p) of the stake on Kalshi
  (repo constant, verified 2026-09; the venue's help page read today defers to its fee PDF) and 0-7 % on Polymarket
  (fee page read today: fee = C x rate x p x (1 - p), rate 0.04-0.07, geopolitics 0). Tested on Kalshi's public trade
  history (T-C). Polymarket is not queried: `research/alternative_alphas/prediction_markets.py` records that it is
  blocked in Vietnam and must not be traded from there. The owner's eligibility on Kalshi is not verified.
- Venue minimums against the standing caps: Hyperliquid's minimum order is 10 USD, so a 240 bps stop loses at least
  0.24 U (cap 0.05 U); every sizing above the cap is a simulation scenario.

## 1. A — maker conversion of the HWK-1 momentum entries (T-A)
Data: `data/hawkes/matrix.npz` (10 169 symbol-days of HWK-1, Binance 1 m klines), partitions of HWK-1 by trigger day:
TRAIN 2024-02-01..2025-12-31, HOLDOUT 2026-01-01..2026-09-30, FRESH 2026-10-01..2026-11-13.
Signals, unchanged from HWK-1 (trigger minute and side): S1 = `BAR|hl5|th3|k3|W15|BOTH|ALL|600/240|H240|cd60`
(its T1 cell), S2 = `BAR|hl5|th2|k3|W30|BOTH|ALL|600/240|H120|cd60` (its T2 final). No signal parameter is searched.

Execution at a trigger of minute s with side d:
- post-only limit at L = close_s x (1 - d x k / 10 000), k in {0, 10, 25} bps, alive for TTL in {1, 5, 15} minutes;
- filled at L in the first bar j of s+1 .. s+TTL that trades THROUGH it (low_j < L for a long, high_j > L for a
  short); never filled -> cancelled, the symbol is free again from s + TTL + 1;
- from L: stop 240 bps (stop-market: at the level, or at the bar's open when the bar opens beyond it; the fill bar
  counts), target 600 bps (resting limit, filled at the level, never in the fill bar), a bar that reaches both is a
  stop, time exit at the close of the bar `hold` minutes after the fill (240 for S1, 120 for S2); one position per
  symbol, cooldown 60 minutes after an exit.
Costs in bps of notional: entry f_m; target f_m; stop / time / end of data f_t + slip 3. Funding is left out (the
venue differs; HWK-1 measured +0.16 and -0.60 bps per trade on HOLDOUT and FRESH). Schedules (f_m, f_t):
HL0 (+1.5, 4.5) the venue's tier 0, the verdict schedule; BN0 (+2.0, 5.0) Binance VIP0; HL_TOP (-0.3, 2.4) the
venue's best tier and rebate; DIR15 (-1.5, 4.5) and DIR30 (-3.0, 4.5) the directive's figures, hypothetical;
ZERO (0, 0) with no slip.

T-A: 9 cells (k x TTL) on S1. The cell with the highest TRAIN mean net R (net bps / 240) under HL0 among cells with
at least 200 TRAIN fills is read once on HOLDOUT and FRESH. Checks, all needed: HOLDOUT fills >= 100; one-sided lower
95 % bound of the mean net bps per fill > 0 (SE clustered by UTC day, never below the i.i.d. SE); deflated Sharpe
>= 0.95 with trials = 38 (HWK-1) + 9; FRESH mean > 0 with >= 20 fills (fewer -> UNCONFIRMED). All pass -> CANDIDATE
(section 0); else NO EDGE.
Reported without a verdict: fill rate; gross per fill against the taker gross of the same triggers, split into
filled and unfilled triggers (adverse selection); the break-even maker fee
f_m* = (gross - (f_t + slip) x (1 - p_TP)) / (1 + p_TP) per cell and partition; every schedule; the taker reference
of HWK-1 re-priced under HL0; S2; the subset of symbols in Hyperliquid's current `meta` universe (current listing
= look-ahead, descriptive).

## 2. B — CEX-DEX gap arithmetic (no verdict)
One round of size q: buy on the cheap venue, sell on the rich one, relative gap G. With pool fee phi, CEX taker fee
f_c, slippage s, gas g per transaction, a share tau of the profit paid for inclusion and a probability p_win of being
first: net = p_win x (1 - tau) x (q x (G - phi - f_c - s) - g) - (1 - p_win) x g_fail. Break-even gap at p_win = 1,
tau = 0: G* = phi + f_c + s + g / q. Reported for q = 5 U (10 U split over two venues; Binance's minimum order is
5 U), phi in {5, 25, 30, 100} bps, f_c in {5 (perp), 10 (spot)} bps, s = 3 bps, g in {0.001, 0.02, 2.0} U (orders of
magnitude for Solana, an L2 and Ethereum L1: assumptions, not measured), tau in {0, 0.5, 0.9}, p_win in
{1, 0.1, 0.01}, and the number of rounds per hour that +20 U per hour needs. Uses
`research.alternative_alphas.cex_dex.gap_trade` for the cost side.

## 3. C — longshot contracts on Kalshi (T-C)
Data: public trade history (`/historical/trades` before the venue's cutoff 2026-09-15, `/markets/trades` after) and
market results (`/historical/markets`, `/markets`). Sample windows, fixed before any download: day n counted from
2025-01-01; TRAIN every 3rd day 2025-01-01..2025-12-31, HOLDOUT every 3rd day 2026-01-01..2026-09-30, FRESH every
2nd day 2026-10-01..2026-11-12; each window is 300 seconds starting at 00:00 UTC + ((397 x n) mod 1440) minutes;
at most 120 pages of 1 000 trades per window (a longer window is cut and flagged).
Unit: one taker trade whose taker-side price p (YES price for a YES taker, NO price for a NO taker) is <= 0.10.
Its market must have result yes or no; anything else is UNKNOWN: excluded and counted. Tickers starting with
`KXMVE` (multi-leg combos) are a separate class.
Return of a trade for a 10 U account at a standard stake of 1.00 U: C = floor(1 / p) contracts, cost = C x p,
fee = 0.07 x C x p x (1 - p) rounded up to the cent, payoff = C if the taker's side won, r = (payoff - cost - fee) /
(cost + fee).
T-C, single-event markets only: 9 cells = price {p <= 0.015, 0.015 < p <= 0.025, p <= 0.025} x time from the trade
to the market's close {all, >= 24 h, < 24 h}. The cell with the highest TRAIN mean r among cells with at least 200
TRAIN trades in at least 20 windows is read once on HOLDOUT and FRESH. Checks, all needed, else NO EDGE: HOLDOUT
n >= 100; one-sided lower 95 % bound of the mean r > 0 (SE clustered by sample window, never below i.i.d.);
deflated Sharpe >= 0.95 over 9 trials; FRESH mean r > 0 with n >= 20 (fewer -> UNCONFIRMED).
Reported without a verdict: win rate against mean price by bucket up to 10 cents (calibration), YES and NO takers
separately, contract-weighted returns, the combo class, the zero-fee return, the same trades from the seller's side
(maker, fee 0), the share of trades in unresolved markets.

## 4. Growth of 10 U (accounting, no verdict)
2 000 paths x 30 days, seed 20261116, sources = HOLDOUT + FRESH outcomes of the T-A cell and of the T-C cell.
- A: one account, one position at a time; each simulated day replays one calendar day. Sizings: MIN = the 10 USD
  minimum order (loss at the stop 0.24 U plus fees); FLAT 1.0 U at the stop (41.7 U, capped at 5 x equity, an
  assumed Hyperliquid alt leverage). Schedule HL0. A path stops below the margin of one minimum order (2 U at 5x).
- C: each simulated day replays one sampled window with up to 5 trades of the cell drawn without replacement.
  Stakes: 1.00 U and 0.05 U (the standing cap; fee rounded up to the cent). A path stops when it cannot fund one
  stake.
Reported: equity quantiles at day 30, P(ruin), P(profit), P(double), P(equity >= 10 + 20 x 24 x 30 U), mean U per hour.

## 5. What any result allows
A CANDIDATE on T-A or an EDGE on T-C is evidence under stated fill assumptions, not an order: it needs unseen data
(T-A), the owner's venue eligibility, a separate authorisation and the standing caps. NO EDGE closes the cell family
on these partitions.
