# CCX-1 — carry and convex overlays on a 10 USDT account (pre-registration, frozen before any snipe or XS outcome)

Owner directive 2026-11-14 (h) ("10 USD hyperbolic growth"): with 10 U of starting capital run the funding-carry lane,
add a funding-spike sniper, compound with a win-streak Kelly model, and report the hourly growth against a target of
+20 U per hour. Offline research only: no order, no engine start, no live, no lock, gate or cap changed, nothing wired.

## 0. Premises corrected before anything runs
- "The carry lane was blocked SOLELY by Binance MIN_NOTIONAL 5 U per leg." The validated lane (XV HEDGE|H28|k2,
  +16.01 bps per 28-day round on HOLDOUT, n 165) is Binance perp against **Hyperliquid** perp. Hyperliquid takes
  orders of 10 USD or more (third-party guides, not the venue's own docs: treat as reported), and two venues cannot
  share margin. From 10 U split in half the per-leg notional is 5 U x leverage: 1x fails the Hyperliquid floor, 2x
  meets it with zero free margin. `core.funding_carry.two_leg_capital_gate(10.0)` passes, but its 10 U is the
  Binance-only arithmetic (5 + 5).
- "Dual legs on Binance (long the negative-funding perp, short the positive one)" is not a hedge of one coin: a long
  and a short of the SAME Binance symbol receive and pay the same print. Two different symbols are a relative bet
  on two coins that collects a funding spread. It is tested here as XS, not assumed to inherit the XV result.
- Binance MIN_NOTIONAL (exchangeInfo, 2026-11-14): 5 U on 653 of 659 USDT perpetuals, 20 U on ETH / BCH / LTC / ETC /
  LINK, 50 U on BTC. A 10-15 U leg cannot open those six.
- "+16 bps" is per 28-DAY round: 2.09 % a year on the leg notional. On 10 U of notional one round is 0.016 U.
- "5 U suffered binary margin-call halts": the ledger (8 738 order rows, 133 trades, Demo) has no margin, balance or
  liquidation refusal. The halts were loss-count rules (0.15 U daily kill, 2 / 3 losses), which do not depend on
  equity. Reported in section 5 from the ledger.
- "+20 U per hour from 10 U" is +200 % of equity per hour. At 20x leverage (200 U notional) it needs 1 000 bps net
  per hour. Binance caps a funding print at 2 % (726 of 822 symbols): the largest possible print on 200 U is 4 U,
  once per 4 or 8 hours, before price and fees. The target is reported against measured numbers, not promised.
- "Risk per trade 0.50-1.00 U (5-10 % of equity)" is above the standing caps (0.05 U at the stop, 0.15 U daily kill,
  3 % of equity). It is simulated as a scenario; no cap, Profile default or engine call site changes.

## 1. Data and partitions
- Funding history of every USDT perpetual the venue lists today (TRADING and SETTLING), `/fapi/v1/fundingRate`,
  2024-12-20..2026-11-13, fetched by `tools/fetch_funding_universe.py` (public, no key). Contracts delisted long
  enough to have left exchangeInfo are missing.
- 1 m klines `/fapi/v1/klines` for T - 10 min .. T + 88 min around each event (public). A window with a missing
  minute in T - 10 .. T + 60 is UNKNOWN: no trade, counted.
- XV / XS: the 18-coin cache of `tools/funding_spread_replay.py` (`data/funding_cache/`, 2024-12-20..2026-11-04),
  nothing re-fetched.
- TRAIN 2025-01-01..2025-12-31, HOLDOUT 2026-01-01..2026-09-30, FRESH 2026-10-01..2026-11-12 (by settlement time
  for the snipe, by entry time for carry rounds).
- Already read: XV HEDGE|H28|k2 on every partition (10-10 f, 11-14 f). Never read: any price outcome around a
  settlement, any XS round.

## 2. A / B — the XV lane as one slot on a two-venue 10 U account (accounting, not a new hypothesis)
- Rule unchanged: `funding_spread_replay.simulate(.., 'XV', coin, 28, 2.0, 'HEDGE')`, 18 coins.
- One slot: earliest entry first, then the larger signal (as ATLAS-K1-H).
- Account: equity 10 U minus one transfer cost c, split in half. A round's notional per leg = L x the SMALLER venue
  balance; it must be >= max(Binance MIN_NOTIONAL of the symbol, Hyperliquid 10 USD), else the round is skipped. If
  re-levelling the two balances (cost c) makes the round possible, the transfer is made.
- Per-venue P&L: funding of that leg + price move of that leg (4 h opens of its own venue) - fees (Binance maker
  2 x 2 = 4 bps, Hyperliquid taker (4.5 + 1) x 2 = 11 bps: the HEDGE 15 bps).
- Guard: maintenance margin 2.5 % on both venues (assumed), liquidation move (1/L - mmr) / (1 -+ mmr); both legs are
  closed at the first 4 h open where either leg's liquidation price is within 6 % of the mark
  (`GuardPolicy.close_liq_dist_pct`); funding accrues to that bar. Intrabar extremes are not seen (understated).
- Grid reported: L in {2, 3} x c in {0, 1.0 U}. L = 1 is infeasible by section 0. L = 3 is above the lane's own
  `GuardPolicy.max_leverage` 2 and is shown because the directive asks for 1x-3x.
- Reported: rounds taken, rounds skipped at the floor, transfers, guard closes, net U by partition and in total,
  net U per hour. No verdict: the lane's holdout result stands as it is; this is what it pays at this size.

## 3. C — XS: the same rule with both legs on Binance (T2)
- Every 8 h decision time T: S_c = trailing 72 h funding sum of each coin on Binance; short the coin with the
  highest S, long the lowest; enter iff (S_high - S_low) / 3 d x 28 d >= 2 x 16 bps; hold 28 days. H and k are the
  XV lane's, not searched. Cost 16 bps = one maker + one taker + 1 bp slip at each end (Binance 2 / 5).
- P&L = funding(short) - funding(long) + price of both legs (4 h opens) - 16 bps. Rounds overlap (one per decision
  time); CI by bootstrap over entry MONTHS (holds last 28 days).
- T2 EDGE iff on HOLDOUT: overlapping rounds >= 100, the month-bootstrap CI95 lower bound of the mean net > 0, and the
  FRESH mean > 0. Anything else: NO EDGE. Setups counted before the freeze (no outcome read): 1 094 TRAIN, 819
  HOLDOUT, 22 FRESH overlapping rounds; one slot 13 / 10 / 0.

## 4. D — funding snipe (T3)
- Event: a settlement T of a symbol. Signal P (strictly causal, the verdict): the PREVIOUS print of that symbol, at
  most 8 h + 1 min earlier, was >= 15 bps in size; the side is the one that received it (long if it was negative).
  Signal O (optimistic bound, no verdict): the print AT T is >= 15 bps; side from its sign. The venue shows a running
  estimate before T, so a live rule sits between P and O; O is the ceiling.
- Payout: -side x the print at T (a long pays a positive print), on the notional.
- Entry (2): M = post-only at the last trade before T - 5 min, filled only if a 1 m bar of [T - 5, T) trades THROUGH
  that price, else no trade; TK = taker at the open of the T - 1 min bar.
- Exit (4): X0 = taker at the open of the settlement bar; X1 = post-only at that open, filled if a bar of
  [T, T + 10 min) trades through it, else taker at T + 10 min; TR15 = trailing stop 15 bps off the best price, armed
  once the price has paid the round-trip fees (8.5 bps), hard stop 50 bps from the entry, taker at T + 60 min;
  TR30 = trail 30 bps, hard stop 100 bps. Inside a bar the adverse extreme is read first; a bar that opens beyond a
  stop exits at its open.
- Costs: maker 2, taker 5, slip 1.5 bps per taker fill (`core.early_stop`). Reported beside it without a verdict:
  slip 5 bps, 1.8 / 4.5 (BNB), zero fees, and M entries that always fill (upper bound).
- Unit: net bps of notional per FILLED trade; SE = max(i.i.d., clustered by UTC day of T).
- T3: the cell (entry x exit, 8 cells) with the highest TRAIN mean under signal P among cells with >= 100 trades.
  EDGE iff on HOLDOUT: trades >= 100, mean - 1.645 SE > 0, deflated Sharpe >= 0.95 (null variance 1 / n, 8 trials),
  and FRESH mean > 0. Anything else: NO EDGE.
- Reported without a verdict: all 8 cells x {P, O} x partition; |signal| tiers 15-30 / 30-50 / 50-100 / >= 100 bps;
  price move in favour of the receiving side over T - 5..T, T..T + 1, T + 10, T + 60 min; fill rates; one trade per
  settlement time (the strongest signal, no trade if it does not fill) = what a 10 U account can take.
- Setups counted before the freeze (funding prints only; no kline was requested):

  Counted on the 294 of 659 symbols whose funding history had arrived when the plan was frozen (alphabetical order, the fetch was still running): lower bounds. 254 symbols with at least one event, 16299 kline windows. The counts of the full universe are written to the result file.

  | signal | partition | events | symbols | settlement times | UTC days |
  |---|---|---|---|---|---|
  | P | TRAIN | 5027 | 200 | 2686 | 357 |
  | P | HOLDOUT | 6411 | 184 | 2789 | 272 |
  | P | FRESH | 780 | 52 | 402 | 43 |
  | O | TRAIN | 5034 | 201 | 2681 | 357 |
  | O | HOLDOUT | 6424 | 186 | 2770 | 271 |
  | O | FRESH | 782 | 52 | 396 | 43 |

## 5. E — sizing and growth
- `core.position_sizer.streak_scaled_notional` (opt-in, no engine call site): notional = base x (1 + k x cumulative
  PnL) only after >= 2 consecutive wins at confluence 3 / 3 with cumulative PnL > 0, and the extra loss at the stop is
  capped at half of the cumulative PnL (the principal never carries more than the base lot's stop).
- 2 000 bootstrap paths x 30 days from 10 U, trades arriving Poisson at the measured rate, a path stops for good
  below 1 U, notional <= 20 x equity. Outcome sources: (i) T3's cell, signal P, one trade per settlement time, HOLDOUT;
  (ii) the same for signal O with its own TRAIN-best cell (bound); (iii) the 131 AUTO ledger trades.
  Sizing: flat 10 U; streak k = 5 (confluence taken as 3 / 3 on every trade: an upper bound on the step-up); risk
  fraction 5 % and 10 % of equity at the stop (stop = the cell's hard stop, 50 bps for sources without one).
- Reported: P05 / median / P95 equity, P(ruin), P(profit), P(double), P(mean pace >= +20 U per hour), mean U per
  hour; the ledger source also at the 5.05 U micro lot from 5 U and from 10 U (does 10 U change survival).
- Target arithmetic: net bps per hour needed at 20x against the measured bps per hour of the best source.

## 6. Reported without a verdict
- Basis dislocation (cross-venue "latency" proxy): Hyperliquid - Binance basis on 4 h opens, 18 coins; fade it when
  it is >= 21 bps (two taker round trips + slip) from its trailing 7-day median, leave at the next bar. The two opens
  are not simultaneous quotes, so this is an UPPER bound. Tick-level cross-venue latency cannot be measured: no
  Hyperliquid tick history exists on disk and its API keeps only the latest 5 000 candles.
- A positive cell that was not chosen on TRAIN, signal O, or a re-pricing at a fee this account does not have is a
  hypothesis, not a result.

## 7. What any result allows
Nothing operational. An EDGE verdict motivates a shadow run (virtual fills, no order) and an owner decision; it does
not start, restart or resize anything, and the caps of section 0 stay where they are.
