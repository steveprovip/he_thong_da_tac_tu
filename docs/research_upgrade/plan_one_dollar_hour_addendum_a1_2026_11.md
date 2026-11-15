# ODH-1 addendum A1 — rolling Engle-Granger pairs (M3R) and implementation notes (registered 2026-11-15 before any outcome of the study was computed on market data)

Status at registration: `plan_one_dollar_hour_breakthrough_2026_11.md` (SHA-256 LF `dae22cd1...8eede`) is frozen and
unchanged. The Binance perp and spot 4 h klines are on disk, the Bybit funding history and klines are being fetched. No
mechanism of the study has been run on market data; the only runs so far are on synthetic series in the tests.

## 1. Why: the module's tracker cannot open a position at bar resolution
On synthetic pairs (A a random walk with 1 % steps, log B = log A + s, s an AR(1) with phi 0.9 and 1 % shocks, so a
spread of 229 bps sd; 3 000 bars), `core.stat_arb_lane.OnlineKalmanPairTracker` with its defaults gives |z| p99 of
0.21 / 0.38 / 1.87 / 0.45 at price levels 1e5 / 100 / 1 / 0.01 and `is_stationary` on 0 of 3 000 bars at every level:
- the state noise `delta_beta` 1e-4 per bar enters the innovation variance as x^2 x 1e-4 with x the UNCENTRED log
  price (x = 11.5 for BTC: sqrt(S) about 1 150 bps), so z stays far below `z_entry` and the Kalman gain is near one;
- `estimate_ou_params` fits its AR(1) to the innovations (one-step prediction errors), which such a filter makes white
  or weakly correlated: phi near -1 is rejected (phi <= -0.85) and a half-life under 2 bars is rejected (2 <= hl <= 96).
M3 runs as frozen. If no cell reaches 30 TRAIN rounds the runner reports NO OPPORTUNITY SET for M3: a statement about
the module, not about pairs trading. M3R below tests pairs trading itself.

## 2. M3R: rolling Engle-Granger pairs (an added mechanism with its own verdict)
Same 18 coins, 153 pairs, Binance perp prices on the 4 h grid, partitions, funding, costs (4 / 6 / 0 bps per side of
gross), portfolio (each held pair one unit of gross, bar return = sum over max(10, pairs held)), bar booking and
accounting as M3; only the signal differs.
- At each bar t of a pair without a position: OLS of log pA on log pB over the L = 120 bars before t (bar t excluded;
  both prices finite on all of them, else no entry). Window residuals r_j; sd = sqrt(sum r_j^2 / (L - 2)); Engle-Granger
  statistic = t-statistic of phi in dr_j = phi x r_(j-1) (no constant, no lag); half-life = -ln 2 / ln(1 + phi).
- Entry when all hold: EG statistic < -3.34 (MacKinnon 5 %, two variables, constant); 1 <= half-life <= 60 bars
  (L / 2); 0.20 <= beta <= 3.50 (the module's clamp); z_t = (log pA_t - alpha - beta x log pB_t) / sd with
  z_entry <= |z_t| < 3.6. Long spread when z_t < 0: w_A = +1 / (1 + beta), w_B = -beta / (1 + beta); short spread: the
  negatives. Prices at t are the entry prices.
- The entry's (alpha, beta, sd) define the trade's z. Exit at the first later bar with z >= -0.25 (long) or z <= +0.25
  (short); stop at |z| >= 3.6 against the position; time stop after max(2.5 x half-life, 120) bars held (the module's).
  No re-entry on the bar of an exit.
- Cells: bar {4 h, 1 day} x z_entry {2.1, 2.5, 3.0}. The TRAIN-best of these 6 (>= 30 TRAIN rounds) is read once on
  HOLDOUT and FRESH with the plan's EDGE rule; its deflated Sharpe counts 24 trials (the plan's 18 + these 6).
- Capital and Monte Carlo as M3 (0.5 of capital per unit of gross budget, 10 pairs, 10 USD of gross per pair).

## 3. Implementation notes (no rule of the plan changed)
- Funding record holes: a 4 h step that ends more than 16 h after the last print before it is UNKNOWN (NaN), not zero.
  4 of 659 Binance files have such a hole (AIA, CVX, SLP, PUMP). Same rule for Bybit.
- M3 P&L is booked at the bar's start (the carry engine's convention, so a daily bar's move falls on its own UTC day);
  the tracker's entry weights are held until its EXIT_SPREAD or STOP_BREAKDOWN (its HOLD weights follow the drifting
  beta: an uncosted rebalance every bar).
- Spot klines start 2024-12-20, not 2024-08-01: M1 looks back 24 h, so no scored day changes.
- Reference XV: the replay cache ends 2026-11-04 and a 28-day round must end inside it, so its FRESH is truncated and its
  capital matrix uses HOLDOUT only; the runner reads the replay's own fields (t, days, net_bps, cost_bps).
- Kline paging now includes a bar that opens exactly at the end time; files fetched before the fix are checked for it.
- The 5 USD leg minimum understates BTC (50 USD) and ETH, LINK, LTC (20 USD) on Binance (CCX-1); it changes only the
  fundability of tiers far below any C*.
