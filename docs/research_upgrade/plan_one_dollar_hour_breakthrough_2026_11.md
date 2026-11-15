# ODH-1 — capital needed for 1 USD per hour from non-directional lanes: funding rotation, cross-venue funding, pairs (pre-registration, frozen before any price or Bybit history of the study is downloaded and before any outcome is computed)

Owner directive 2026-11-15 (c): stop directional tests; use the repository's carry and stat-arb modules to find a
non-directional cashflow of at least 1.00 USD per hour (24 per day, 8 760 per year) with ruin under 1 %, and compute
the capital C* each mechanism needs. Offline research on public data only: no order, no account, no engine start, no
live, no lock, gate or cap changed, nothing wired, no git operation.

## 0. Premises corrected before anything runs
- The target is a yield on capital. 1 USD per hour is 8 760 USD per year, so C* = 8 760 / y for a net annual yield y:
  438 000 USD at 2 %, 87 600 at 10 %, 43 800 at 20 %. From 5 000 USD it needs 175 % per year net; from 50 USD
  17 520 %. The directive's tiers (50 to 5 000 USD) are reported, with tiers up to 500 000 USD so that C* can fall
  inside the grid.
- What the repository already measured (not re-searched here): XV `HEDGE|H28|k2` (Binance perp against Hyperliquid
  perp, 18 majors) +16.0 bps per 28-day round, 2.09 % a year on notional; SP (Binance spot long, perp short, the
  same 18 majors) -2.7 bps per round [-11.5, +4.4]; XS (two Binance perps) NO EDGE; the funding snipe gives the print
  back in price within 500 ms. The untested parts of the directive are the ROTATION across every listed perpetual on
  extreme prints (M1), Binance against Bybit (M2) and the pairs tracker of `core/stat_arb_lane.py` (M3).
- "Capture extreme funding delta-neutral": a positive print is received by a perp short hedged with a spot long,
  which needs a spot listing and pays spot fees (10 bps a side at VIP0). A negative print would need a spot short:
  margin borrow rates and availability are not on disk, so negative prints are harvested only across venues (M2).
- "Cross-venue = zero borrowing, pure cashflow": two venues are two margin accounts; each leg is marked at its own
  venue; re-levelling needs a withdrawal. A fixed transfer cost enters the capital model as a stated assumption.
- "A 100-200 bps pair spread makes fees negligible": the fee is small against the spread; whether a spread at the
  entry band reverts is the test.
- Survivorship: each venue's symbol list is today's. Bybit fees are the repo's constant (maker 2.0 / taker 5.5 bps,
  recorded 2026-10-10; the fee page was unreachable today). The owner's eligibility on Bybit is not verified.

## 1. Data and partitions
- Binance funding prints of every USDT perpetual: `data/funding_universe/` (2024-12-20..2026-11-13, on disk).
- Downloaded after this freeze, public REST: Binance USD-M 4 h klines of those symbols and Binance spot 4 h klines
  of the spot pair of the same coin (prefixes 1000 / 1000000 / 1M removed when the plain spot symbol exists), from
  2024-08-01; Bybit linear USDT perpetuals with the same symbol name: funding history and 4 h klines from 2024-12-20.
- Grid: every 4 h boundary t (UTC). Price at t = open of the 4 h bar that starts at t, on the leg's own venue and
  market. Funding of a step = prints with t < print time <= t + 4 h. F8(t) = prints in (t - 8 h, t], in bps.
- A symbol is tradable at t when both legs have a price at t and at t + 4 h, the Binance perp traded at least
  5 000 000 USDT and the other leg at least 1 000 000 USDT in the 24 h before t, and the two legs' prices differ by
  less than 10 % (after the name's multiplier): otherwise no entry (UNKNOWN is never a zero-cost trade).
- Partitions by UTC day of the 4 h step: TRAIN 2025-01-01..2025-12-31, HOLDOUT 2026-01-01..2026-09-30,
  FRESH 2026-10-01..2026-11-12.

## 2. Carry engine (M1, M2)
A position of direction d on a symbol is short leg A and long leg B when d = +1 (reverse when d = -1).
- Step P&L in bps of the position's notional: d x (funding_A - funding_B) + d x (-(pA1 / pA0 - 1) + (pB1 / pB0 - 1)).
- Signal S(t) = F8_A(t) - F8_B(t). Entry when |S| >= theta, with d = sign(S); exit at the first t at least 8 h after
  the entry with d x S(t) < theta_out; a leg whose data ends closes the position at its last price.
- Margin: both perp legs at 2x (the lane guard's maximum). A perp leg that moves 45 % against the position inside
  one step (bar high for a short, bar low for a long, against the price at the step's start) is liquidated: the
  position closes with an extra 150 bps of notional.
- Portfolio: K = 5 slots of equal notional; at each t exits first, then free slots take the tradable symbols not
  held with the largest |S| >= theta. Return of a step = mean over the K slots (an empty slot earns 0); daily return
  = sum of the steps of the UTC day, in bps of the notional budget.
- Costs per side, bps of notional (one leg resting, the other taken, the cheapest assignment; slippage 3 bps on the
  taken leg because the symbols are small): M1 = spot taker 10 + 3 + perp maker 2 = 15; M2 = maker 2 + Binance taker
  5 + 3 = 10. Sensitivities: both legs taken (M1 21, M2 16.5), BNB-discounted Binance fees (M1 12.3, M2 9.5), zero.
- M1 (spot-perp rotation, Binance): A = perp, B = spot (no funding), only d = +1 (S >= theta).
  Cells: theta in {5, 10, 20} bps per 8 h x theta_out in {0, theta / 2}.
- M2 (Binance perp against Bybit perp): A = Binance, B = Bybit, both directions.
  Cells: theta in {3, 5, 10} bps per 8 h x theta_out in {0, theta / 2}.
- Reference without a verdict: the stored rule XV `HEDGE|H28|k2` on the Binance / Hyperliquid cache of 18 coins
  (`tools/funding_spread_replay.simulate`), as daily accruals of its rounds.

## 3. Pairs with the module's tracker (M3)
- Universe: the 18 coins of the funding cache (BTC ETH SOL XRP DOGE BNB ADA SUI LINK AVAX ENA AAVE LTC HYPE WLD TAO
  ZEC 1000PEPE), 153 pairs, Binance perp prices on the 4 h grid from 2024-08-01 (warm-up before 2025).
- Each pair runs `core.stat_arb_lane.OnlineKalmanPairTracker` unchanged, one `evaluate_signal(price_a, price_b,
  round_trip_cost_bps=8, z_entry, z_exit=0.25, z_stop=3.6)` per bar; the returned weights are held until the next
  signal. Bar P&L = w_a x r_a + w_b x r_b - (w_a x funding_a + w_b x funding_b); cost 4 bps of gross per side (one
  leg maker 2, the other taker 5 + slip 1, on majors); sensitivities 6 (both taken) and 0.
- Portfolio: each open pair is one unit of gross; the daily return is the sum of the open pairs' P&L divided by
  max(10, number of open pairs), in bps of the gross budget.
- Cells: bar in {4 h, 1 day (every sixth 4 h bar)} x z_entry in {2.1, 2.5, 3.0}.

## 4. Verdicts
For each of M1, M2, M3: the cell with the highest TRAIN mean daily return among cells with at least 30 TRAIN rounds
is read once on HOLDOUT and FRESH. EDGE needs all of: at least 30 HOLDOUT rounds; one-sided lower 95 % bound of the
HOLDOUT mean daily return > 0 (Newey-West SE, 7 lags, never below the i.i.d. SE); deflated Sharpe >= 0.95 over the
18 cells of this plan; FRESH mean daily return > 0. Else NO EDGE. The one-sided p-value of the HOLDOUT mean is
reported. Reported without a verdict: every cell on TRAIN; per-round funding, price and cost components; hold
lengths; the cost sensitivities; liquidations; the reference lane.

## 5. Capital matrix and Monte Carlo (accounting)
- Capital per unit of budget: M1 1.5 (spot paid in full + perp margin at 2x), M2 1.0 (two perp margins at 2x), M3
  0.5 (gross at 2x), reference 1.0. Daily return on capital = budget return / that figure.
- C* = 24 USD per day / mean daily return on capital (HOLDOUT + FRESH), at the point estimate and at the lower 95 %
  bound (not finite when the bound is <= 0).
- Tiers 50, 100, 500, 1 000, 2 500, 5 000, 10 000, 25 000, 50 000, 100 000, 250 000, 500 000 USD. Venue minimum: a
  slot leg below 5 USD (Binance, Bybit) or 10 USD (Hyperliquid) reduces the number of slots; under one slot the tier
  is NOT FUNDABLE. M2 and the reference pay 2 USD of transfers per 30 days (assumed: two 1 USD withdrawals).
- 2 000 paths x 30 and x 90 days, seed 20261117, 7-day blocks drawn from the HOLDOUT + FRESH daily returns on
  capital. Reported per mechanism and tier: hourly net profit (P05, median, P95), P(hourly >= 1 USD), P(loss),
  P(ruin) with ruin = equity at or under half the start on any day.
- Capacity, reported: the median 24 h Binance volume of the symbols held, against the slot notional at C*.

## 6. What any result allows
An EDGE here is a 4 h-resolution replay with assumed fills, not an order: wiring needs the owner's venue
eligibility, a separate authorisation and the standing caps. NO EDGE closes the cell family on these partitions.
