# NDG-1 — non-directional two-sided quoting gated by the ATLAS volatility forecast (pre-registration, frozen before any outcome)

Owner directive 2026-11-14 (g) ("non-directional alpha"): quote two-sided post-only orders only when ATLAS forecasts
quiet volatility (predicted 15 m dispersion below its 20th percentile), freeze on a tail event, and measure the net
yield of captured spread and maker rebates on the kline archive. Offline research only: no order, no engine start, no
live, no lock, gate or cap changed, nothing wired.

## 0. Premises corrected before anything runs
- "Maker rebates": Binance USD-M VIP0 CHARGES the maker 2 bps (taker 5). There is no rebate at this account's tier;
  a rebate schedule is reported as a re-pricing only, labelled hypothetical.
- "Captured spread": the quoted spread (public snapshot 2026-11-14) is 0.015 bps on BTC, 1.33 on SOL, 1.60 on DOGE.
  A round trip at the touch earns at most that and pays 4 bps of maker fees, so touch quoting is negative before any
  adverse selection. What is tested here is the only version that can clear the fee: quotes placed a multiple of the
  predicted volatility away from the price (a one-level grid re-centred every 15 minutes).
- "ATLAS's proven volatility skill": ATLAS-K1 beat the no-signal baseline by 1.6 % of pinball loss and lost to a fixed
  EWMA scale by 1.3 %. Both gates are run; the directive's (ATLAS) is the primary one.
- A two-sided quoter earns only if prices mean-revert inside its band. On a martingale every such strategy has zero
  expected gross P&L (optional stopping) and loses its fees. "Non-directional" is therefore still a bet on a
  conditional mean (reversion after a move); the variance ratio by regime is reported beside the P&L.

## 1. Data, decision points, partitions
Exactly ATLAS-K1 (`plan_atlas_density_2026_11.md` section 2): BTCUSDT, SOLUSDT, DOGEUSDT 1 m klines; decisions at
:00 / :15 / :30 / :45 UTC; TRAIN 2024-12-01..2025-12-31, HOLDOUT 2026-01-01..2026-09-30, FRESH 2026-10-01..2026-11-12.
HOLDOUT and FRESH were read by the directional rules of ATLAS-K1; no two-sided rule has been run on any partition.

## 2. Volatility gate (nothing re-fitted)
ATLAS gate: the final ATLAS-K1 ensemble E (the 8 forecasters and survivor weights of `data/atlas_density_study.json`,
written into the module as constants). W = Q95 - Q05 of its +15 m forecast; sigma = W / 3.29.
LOW = W below the 20th percentile of that symbol's TRAIN values of W; HIGH = above the 80th; ALL = no gate.
EWMA gate (comparison): s = sqrt(15 x EWMA(r^2, half-life 120 bars)), LOW = s below its TRAIN 20th percentile; the
quote distance and the band then use s and the TRAIN z-quantiles of ATLAS-K1's reference (-1.546 .. +1.508).

## 3. Quoting model (one window = the 15 one-minute bars after a decision close P0; log-return units from P0)
- Quotes: bid at -d, ask at +d, d = k x sigma, k in {0.25, 0.5, 1.0}; one unit per side; post-only.
- Fill: a quote is filled when a bar trades THROUGH it (low < -d for the bid, high > +d for the ask). Both in one bar
  from flat = one completed pair (+2d). While flat, a quote is posted for a bar only if the previous close is on the
  passive side of it (a marketable post-only order is rejected).
- Inventory: after one fill the opposite quote stays as the exit (+2d when filled); no second unit is added.
- Freeze F in {BAND, NONE}. BAND: when a bar leaves the forecast band (low < Q05 or high > Q95) every quote is
  cancelled, inventory is closed as a taker AT the band edge, and the window is over. A quote beyond the band edge is
  not posted. Same-bar ambiguity is resolved against the strategy: band exit before the favourable fill.
- Window end: remaining inventory is closed at the last close as a taker. Windows do not carry inventory.
- Costs: maker 2 bps per filled quote, taker 5 + 1.5 bps slippage per forced exit (`core.early_stop`).
  Reported beside it, without a verdict: 1.8 / 4.5 (BNB), maker 0, maker -0.5 (hypothetical rebate), all-maker exits
  (forced exits re-priced as maker fills with no slippage: an upper bound for any inventory-carrying variant), gross.
- Unit: net bps per ACTIVE window (quotes posted; a window with no fill counts 0) on one unit of notional per side.
  SE clustered by UTC day. Also: share of windows with a fill, pairs, freeze and window-end exits, bps per day.

## 4. Test (read once: TRAIN choice, then HOLDOUT, then FRESH)
- T1 = the cell (k, F) with the highest TRAIN mean net bps per active window under the ATLAS LOW gate (6 cells).
  EDGE iff on HOLDOUT: windows with a fill >= 300, mean - 1.645 SE > 0, deflated Sharpe >= 0.95 (null variance 1 / n,
  12 trials = 6 cells x 2 gates), and FRESH mean > 0 with >= 100 active windows. Anything else: NO EDGE.
- Reported without a verdict: all 6 cells on every partition; the T1 cell under ALL, HIGH and the EWMA LOW gate;
  the fee re-pricings; per symbol; the variance ratio VR(15) = var(15 m return) / (15 x var(1 m return)) of LOW / middle /
  HIGH windows (below 1 = reversion); the realised |15 m return| by regime (does the gate rank volatility).
- A positive cell that was not chosen on TRAIN, or a positive re-pricing at a fee this account does not have, is a
  hypothesis, not a result.

## 5. What any result allows
Nothing operational. EDGE motivates a shadow quoter (virtual fills, no order) and a queue-aware replay on depth data.
NO EDGE leaves every gate, lock and cap as it is (two-sided quoting stays out of the engine, as decided 2026-11-09 e).
