# CCX-1 — 10 USDT: carry account, funding snipe, compounding (report, 2026-11-14)

Plan, frozen before any snipe or XS outcome (SHA-256 `9e6bfedcf6fb9ed672b9ac76d9c638e8b6392eb8c848df867781b9fa39e434ec`):
`docs/research_upgrade/plan_carry_convex_10u_2026_11.md`.
Code: `research/carry_convex_study.py`, `tools/fetch_funding_universe.py`, `tools/snipe_tick_check.py`,
`core/position_sizer.streak_scaled_notional`, `tests/test_carry_convex.py` (36 tests).
Result files (git-ignored): `data/carry_convex_study.json`, `data/carry_convex_addendum.json`, `data/snipe_tick_check.json`.
Offline research on public data. No order, no engine start, no live, no cap or gate changed, nothing wired.

## 0. Verdicts

| Item | Result |
|---|---|
| +20 U per hour from 10 U | Not reachable. It needs 1 000 bps net per hour at 20x. The frictionless funding ceiling of the whole venue is 43 bps per hour (23x short); the best simulated cell is 19 bps per hour (53x short) and rests on an exit that section 3 shows is not available |
| XV carry lane on 10 U (accounting) | +0.36 U in 675 days at 2x if transfers between the venues are free (+0.000022 U per hour). One 1 U transfer fee: the account cannot open a single round at 2x |
| T2, Binance-only XS carry | NO EDGE (pre-registered). HOLDOUT +860 bps per round, month-bootstrap CI95 -423..+2 632; the mean is price, not funding |
| T3, funding snipe | EDGE by the frozen rule at 1-minute resolution (M\|X0, HOLDOUT +18.84 +- 1.37 bps, n 13 785). NOT CONFIRMED as tradable: the same entry with the exit one minute later is -12.04 +- 1.26 bps, and trade-level data shows the price gives back 84 % of the funding between 250 and 500 ms after the settlement |
| Win-streak / risk-fraction compounding | On the real ledger: 5 % risk ruins 17 % of paths in 30 days, 10 % risk 99.8 %. It multiplies whatever expectancy the trades have; the measured expectancy is negative |

## 1. Ledger and prior results at 10 U (Phase 1)

Sources: `data/engine_orders.jsonl` (8 738 rows, Demo, 2026-10-07..11-10), `data/engine_trades.jsonl` (133 rows),
`docs/research_upgrade/trial_log.jsonl` (68 rows before this run), `AUTOPSY_REPORT_2026.md`.

| Question | Measured |
|---|---|
| Margin, balance or liquidation refusals in the order log | 0 of 8 738 rows |
| What halted the 5 U runs | loss-count rules: 0.15 U daily kill, 2 / 3 losses in a row. None depends on equity |
| AUTO trades | 131 on 8 active days (16.4 per active day): gross +0.62, fees 4.57, net -3.95 +- 1.16 bps |
| Micro-flagged trades | 21: net -5.51 +- 3.37 bps |
| 30 days, 5.05 U lot, from 5 U | median 4.02 U (P05 3.76, P95 4.27), ruin 0 %, profit 0 % |
| 30 days, 5.05 U lot, from 10 U | median 9.02 U (P05 8.76, P95 9.27), ruin 0 %, profit 0 % |
| 30 days, 10 U lot, from 10 U | median 8.07 U (P05 7.54, P95 8.55), ruin 0 %, profit 0 % |
| Days to the 1 U floor at the measured drain | 122 (5 U, 5.05 lot), 275 (10 U, 5.05 lot), 139 (10 U, 10 U lot) |

(2 000 bootstrap paths of the 131 AUTO net-bps outcomes, 16.4 trades per day, no daily kill applied.)

The premise "5 U suffered binary margin-call halts" does not reproduce. No path is ruined inside 30 days at either
size; the loss is the per-trade expectancy times turnover. 10 U buys runway (275 days instead of 122) only if the lot
stays at 5 U. If the lot scales with equity the runway is the same and the loss per day doubles.

Every earlier negative result is a net-bps-per-trade number (trial log: -3.98..-14.1 bps on the holdout rows), so the
size of the account does not change it. One result was capital-gated at 5 U: the carry lane (`two_leg_capital_gate`:
EQUITY 5.00 < TWO_LEG_MIN 10.00).

What 10 U can post (Binance exchangeInfo 2026-11-14, 659 USDT perpetuals: 522 TRADING, 136 SETTLING, 1 pending):

| Structure | Floor | 10 U at 1x | 2x | 3x |
|---|---|---|---|---|
| Two perps in one Binance wallet (XS) | 5 U per leg on 653 symbols; 20 U on ETH / BCH / LTC / ETC / LINK; 50 U on BTC | 5 U per leg: fits, no buffer | 10 U | 15 U |
| Binance perp + Hyperliquid perp (XV, the validated lane) | Hyperliquid order >= 10 USD (third-party guides, not the venue's docs) | 5 U per leg: refused | 10 U per leg, 0 free margin | up to 15 U per leg (3.3 U free at the 10 U floor) |
| The same after one 1 U transfer | as above | refused | 9 U per leg: refused | up to 13.5 U per leg |

`two_leg_capital_gate(10.0)` now answers LIVE / TWO_LEG_FUNDED: its 10 U floor is the Binance-only arithmetic (5 + 5)
and does not know the Hyperliquid floor. It was not changed here.

## 2. Carry at 10 U (Phase 2, baseline)

### 2.1 XV lane as one slot on a two-venue account (accounting of an already-validated rule)

Replay re-computed from the cache: HOLDOUT n 165, +16.01 bps per 28-day round (CI95 by day +10.9..+21.2), 2.09 % a
year on the leg notional; FRESH n 4, +14.3. One slot, 2024-12..2026-11, guard on 4 h opens, maintenance margin 2.5 %:

| Leverage, transfer cost | Rounds | Skipped at the floor | Transfers | Guard closes | Net U | Net U per hour | HOLDOUT |
|---|---|---|---|---|---|---|---|
| 2x, free | 24 | 1 | 23 | 2 | +0.356 | +0.000022 | 9 rounds, +0.128 U, +13.9 bps |
| 2x, 1 U each | 0 | 397 | 0 | 0 | -1.000 | n/a | none |
| 3x, free | 28 | 4 | 17 | 13 | +0.432 | +0.000026 | 11 rounds, -0.006 U, -0.1 bps |
| 3x, 1 U each | 3 | 350 | 2 | 3 | -2.925 | -0.0018 | none |

- At 2x the pair needs every cent: 23 of 24 rounds needed the two balances re-levelled first, because one leg's
  price P&L leaves its venue under 5 U. With a real transfer fee the first round cannot be opened.
- At 3x the guard (liquidation within 6 % of the mark, 22.7 % adverse on the short leg) closed 13 of 28 rounds early
  and the HOLDOUT rounds net -0.1 bps. 3x is above the lane's own `GuardPolicy.max_leverage` 2.
- Worst adverse leg move inside a round: 41.6 % (2x), read on 4 h opens, so intrabar extremes are understated.
- LTC, LINK, BTC and ETH legs cannot be opened at this size (20 / 50 U floors).
- Transfer cost: Hyperliquid's withdrawal fee is reported as 1 USDC; Binance's USDC fee on Arbitrum could not be
  verified (secondary sources say zero to small). The table shows both ends.

### 2.2 XS: both legs on Binance (T2, pre-registered)

| Partition | Overlapping rounds | Mean net bps | CI95 by entry month | Funding | Price of the two legs (sd) |
|---|---|---|---|---|---|
| TRAIN | 1 094 | +99.7 | -437.7..+656.3 | +168.1 | -52.3 (3 326) |
| HOLDOUT | 819 | +859.6 | -423.0..+2 631.6 | +62.7 | +812.9 (3 932) |
| FRESH | 22 (one month) | +1 927.3 | n/a | +58.4 | +1 884.9 (1 553) |

T2 checks: rounds >= 100 pass, month-bootstrap lower bound > 0 fail, FRESH mean > 0 pass = **NO EDGE**.
The funding collected is 63 bps per round; the result is the relative price of two different coins, with a standard
deviation of 39 % per round. One slot on HOLDOUT (10 rounds): -5 965..+9 574 bps; at 5 U per leg the worst round
costs 2.98 U of a 10 U account.

## 3. Funding snipe (Phase 2, convex booster; T3)

Universe: funding history of all 659 USDT perpetuals, 2024-12-20..2026-11-13; 562 symbols with at least one print
>= 15 bps; 37 618 one-minute windows requested, 129 events without complete bars (UNKNOWN, no trade).
Events (signal P = previous print >= 15 bps; O = the print itself): P 12 401 TRAIN / 14 610 HOLDOUT / 1 284 FRESH;
O 12 423 / 14 629 / 1 288. Prints >= 15 bps on HOLDOUT: 53.6 per day (>= 100 bps: 3.7 per day; the cap is 200 bps).

### 3.1 The frozen grid (signal P, net bps per filled trade, SE clustered by UTC day)

| Cell | HOLDOUT n | Fill | Net | Price | Funding | Fees | TRAIN net | FRESH net |
|---|---|---|---|---|---|---|---|---|
| M\|X0 (T3 choice) | 13 785 | 0.94 | **+18.84 +- 1.37** | -8.32 | +34.17 | 7.0 | +23.78 +- 1.53 | +26.62 +- 3.95 |
| M\|X1 | 13 785 | 0.94 | +13.68 +- 1.36 | -16.24 | +34.17 | 4.2 | +14.03 | +28.08 |
| M\|TR15 | 13 785 | 0.94 | -3.46 +- 0.96 | -30.63 | +34.17 | 7.0 | -0.91 | +9.48 |
| M\|TR30 | 13 785 | 0.94 | -9.61 +- 1.07 | -36.77 | +34.17 | 7.0 | -8.63 | +6.74 |
| TK\|X0 | 14 610 | 1.00 | +20.17 +- 1.12 | -3.17 | +33.34 | 10.0 | +22.90 | +19.10 |
| TK\|X1 | 14 610 | 1.00 | +14.97 +- 1.04 | -11.13 | +33.34 | 7.2 | +13.05 | +20.47 |
| TK\|TR15 | 14 610 | 1.00 | -4.25 +- 0.64 | -27.60 | +33.34 | 10.0 | -2.13 | +1.87 |
| TK\|TR30 | 14 610 | 1.00 | -11.26 +- 0.82 | -34.61 | +33.34 | 10.0 | -11.22 | -1.51 |

T3 = M\|X0 (TRAIN-best). Checks on HOLDOUT: n >= 100 pass, LCB95 +16.59 > 0 pass, deflated Sharpe 1.00 (8 trials)
pass, FRESH mean > 0 pass = **EDGE by the frozen rule**. Signal O (bound): M\|X0 +25.91 +- 1.53, TK\|X0 +28.17 +- 1.33.
By size of the previous print (HOLDOUT, M\|X0): 15-30 bps +6.90 +- 1.04 (n 7 524), 30-50 +20.72 +- 2.10 (n 2 987),
50-100 +38.39 +- 3.45 (n 2 295), >= 100 +59.09 +- 9.71 (n 979).

### 3.2 Why this is not a tradable edge yet

Every positive cell leaves at the open of the settlement bar, i.e. at the FIRST print after T. Every cell that
leaves later is negative. The pre-registered drift table shows where the money goes (bps in favour of the side that
receives the funding, signal P):

| Partition | Funding received | T - 5 min -> T | T -> T + 1 min | T -> T + 10 min | T -> T + 60 min |
|---|---|---|---|---|---|
| TRAIN | +35.10 | -0.27 +- 1.10 | -35.35 +- 1.64 | -43.91 +- 2.63 | -39.35 +- 5.13 |
| HOLDOUT | +33.34 | -3.60 +- 0.96 | -30.15 +- 1.53 | -38.71 +- 2.54 | -45.11 +- 5.01 |
| FRESH | +35.30 | +1.61 +- 2.81 | -22.56 +- 4.06 | -24.79 +- 9.01 | -24.49 +- 18.71 |

The price moves against the receiver by about the funding amount inside the first minute after the settlement, and
does not come back within the hour. The funding is priced in; what the X0 cells earn is the assumption that a taker
exit trades at the first print, ahead of that move.

Addendum A2 (computed after the frozen run, outside its 8-cell grid, no verdict): the same entries with the taker
exit at the open of the T + 1 min bar.

| Signal, entry | TRAIN | HOLDOUT | FRESH |
|---|---|---|---|
| P, M | -12.28 +- 1.52 | **-12.04 +- 1.26** | +3.30 +- 3.31 |
| P, TK | -12.46 +- 1.07 | -9.99 +- 0.96 | -3.39 +- 3.33 |
| O, M | -11.93 +- 1.67 | -11.54 +- 1.37 | +1.42 +- 4.69 |
| O, TK | -10.67 +- 1.16 | -8.77 +- 0.97 | -3.84 +- 3.43 |

Even with the print known in advance (signal O) the trade loses its fees and more when it leaves a minute late.

Addendum A1 (`tools/snipe_tick_check.py`, not pre-registered): the venue serves trade-level history by time only for
the last 2 days, so the sample is the 66 settlements of the last 46 hours with signal P (9 symbols; INIT 28, BTW 12,
BARD 11, BMT 7; mean funding 40.3 bps). Price in favour of the receiver, measured from the last trade before T:

| Delay after T | first print (median 8.5 ms) | 50 ms | 100 ms | 150 ms | 250 ms | 500 ms | 1 s | 10 s | 60 s |
|---|---|---|---|---|---|---|---|---|---|
| Mean bps | -1.4 | -8.5 | -6.1 | -8.9 | -3.5 | **-34.0** | -33.8 | -32.9 | -29.9 |
| SE | 0.5 | 1.5 | 1.2 | 1.4 | 1.8 | 5.0 | 4.8 | 5.6 | 6.8 |

The repricing (84 % of the funding) happens between 250 and 500 ms after T; a median of 26 trades print in the first
100 ms and 274 in the first second. For 33 of the 66 windows the 1 000-trade request limit cut the tape before
T + 60 s, so the late columns are understated for the busiest symbols; the sub-second columns are complete.

What this leaves open, and it decides the trade:
- An exit that lands inside the first ~250 ms would keep most of the funding ONLY IF the position still counts for
  it. Binance's own FAQ says the actual funding transaction time can deviate by up to 15 seconds and that no exact
  settlement time is guaranteed. The move at 250-500 ms is consistent with that being the moment the entitlement is
  fixed (collectors sell when the fee has posted), in which case an earlier exit forfeits the funding and pays the
  fees. This is a hypothesis: public data cannot show when an account is credited.
- This machine's order acknowledgement is 146 ms at the median (Demo ledger), with 26 competing prints in the first
  100 ms. A fill at "the first print" is a latency race, not a property of the signal.
- Prices are last trades, not the bid a taker would hit; the spread of these symbols is not in the data (1.5 bps slip
  assumed; at 5 bps M\|X0 is +15.34).
- n = 66 from two days and nine symbols is a look, not a validation.

Until those are measured, T3 is a 1-minute-resolution upper bound and nothing may be sized on it.

### 3.3 Fees under VIP0

| Schedule (HOLDOUT, signal P) | M\|X0 | TK\|X0 | M\|TR15 |
|---|---|---|---|
| VIP0 maker 2 / taker 5 / slip 1.5 (primary) | +18.84 | +20.17 | -3.46 |
| VIP0, slip 5 | +15.34 | +13.17 | -6.95 |
| BNB discount 1.8 / 4.5 | +19.54 | +21.17 | -2.75 |
| Zero fees, zero slip | +27.34 | +33.17 | +4.82 |

Fees and slip take 8.5 bps of the 34.2 bps of funding on M\|X0 (25 %); the BNB discount is worth 0.7 bps. The carry
lane pays 15 bps of fees against 30.6 bps of funding per 28-day round (49 %). The fee tier is the public schedule;
the account's own `commissionRate` needs a signed call and was not read (the Demo ledger shows taker 4 bps).
The fee is not what decides the snipe: the 30-35 bps price move after the settlement is.

## 4. Sizing and growth (Phase 3)

`core.position_sizer.streak_scaled_notional` (opt-in, no engine call site): notional = base x (1 + k x cumulative PnL)
only after >= 2 consecutive wins at confluence 3 / 3 with cumulative PnL > 0; the extra loss at the stop is capped at
half of the cumulative PnL, so the money put in never carries more than the base lot's stop. No cap, Profile default
or gate was changed; the directive's 0.50-1.00 U risk per trade is simulated only.

2 000 bootstrap paths x 30 days from 10 U, notional <= 20 x equity, a path stops below 1 U.

| Source (HOLDOUT, one trade per settlement) | Sizing | Median U | P05 | P95 | Ruin | P(pace >= +20 U/h) | Mean U per hour |
|---|---|---|---|---|---|---|---|
| Ledger, real fills (-3.95 bps, 16.4 / day) | flat 10 U | 8.07 | 7.54 | 8.55 | 0 % | 0 | -0.0027 |
| | streak k 5 | 8.07 | 7.54 | 8.54 | 0 % | 0 | -0.0027 |
| | risk 5 % of equity | 1.38 | 0.99 | 2.25 | 17.0 % | 0 | -0.0119 |
| | risk 10 % of equity | 0.99 | 0.92 | 1.00 | 99.8 % | 0 | -0.0125 |
| Snipe, exit one minute late (A2: -16.8 bps, 15.3 / day) | flat 10 U | 2.12 | 0.48 | 7.87 | 42.4 % | 0 | -0.0097 |
| | streak k 5 | 1.38 | 0.37 | 6.85 | 48.0 % | 0 | -0.0104 |
| | risk 5 % / 10 % | 0.78 / 0.30 | 0.00 | 0.99 / 0.97 | 100 % | 0 | -0.013 |
| Snipe, exit at the first print (bound: +29.6 bps, 15.3 / day) | flat 10 U | 23.48 | 18.11 | 28.58 | 0 % | 0 | +0.0187 |
| | flat 200 U (20x) | 268.5 | 0.00 | 382.9 | 14.6 % | 0 | +0.32 |
| | risk 5 %, book capped at 2 000 U | 1 937 | 0.00 | 3 151 | 21.3 % | 0 | +2.33 |
| | risk 10 %, book capped at 2 000 U | 1 932 | 0.00 | 3 429 | 39.8 % | 0 | +2.14 |
| | streak k 5, book capped at 2 000 U | 451 | 0.00 | 1 484 | 24.4 % | 0 | +0.73 |

- A streak is not an edge: on the ledger the step-up never fires (no cumulative profit to spend) and the risk-fraction
  sizings only reach the 1 U floor faster.
- The only rows that grow are built on the first-print exit of section 3.2. With the exit one minute later the same
  sizing ruins 42-100 % of the paths.
- The capacity cap (2 000 U = 1 % of the lower-quartile 5-minute volume before a settlement, an assumption) was added
  after the run. Without it the plan's model prints P(pace >= +20 U/h) = 46.9 % for the 5 % risk row with a P95 of
  12 million U of equity, which no book of these symbols can fill; that number is an artefact of unlimited capacity on
  an unavailable exit, and it comes with 43.6 % ruin.
- Erratum E1: the first run let a single trade lose more than the equity (negative equity). The simulator now
  liquidates at zero. Sections A-D of the result file are identical between the two runs; only the growth table
  changed. The frozen plan was not edited.

## 5. Hourly trajectory against the target (Phase 5)

| Path from 10 U | Net U per hour | Multiple short of +20 U per hour |
|---|---|---|
| XV carry, 2x, free transfers (measured, 675 days) | +0.000022 | 900 000x |
| Ledger scalper as traded, 10 U lot | -0.0027 | negative |
| Snipe, realistic exit (one minute late), 10 U lot | -0.0097 | negative |
| Snipe, first-print exit (unavailable bound), 10 U lot | +0.019 | 1 070x |
| Same bound at 20x (200 U), 14.6 % ruin in 30 days | +0.32 | 62x |
| Frictionless funding ceiling at 20x: the largest print at every settlement, no price move, no fee | +0.86 | 23x |

+20 U per hour is 200 % of the account per hour. At the venue's highest leverage it needs 1 000 bps per hour; all
funding the venue pays to the single best-paid side sums to 43 bps per hour (HOLDOUT, 6 294 settlement times), and
that funding is given back in price within half a second.

## 6. Reported without a verdict

- Basis dislocation (Hyperliquid - Binance on 4 h opens, 18 coins): 263 signals in 71 536 bars (0.37 %), +9.99 bps
  after 21 bps of taker costs, win 57 %. Upper bound: the two opens are not simultaneous quotes. At 10 U per leg all
  263 signals of 22 months are worth 0.26 U.
- Cross-venue latency: not measurable. No Hyperliquid tick history is on disk and its API keeps the latest 5 000
  candles only.

## 7. Not measured, and what would settle T3

1. The sub-second price path with bid / ask, on thousands of settlements instead of 66: record aggTrade + bookTicker
   for the minute around each settlement on the symbols with a large print (the recorder and its watchdog exist; a
   windowed recording is small). Owner decision: it starts a long-running process.
2. When an account is actually credited: the `FUNDING_FEE` time of the account's own income history against T. It
   needs a position held through a print (Demo first; `tools/check_testnet_funding_settlement.py` reads it). Owner
   decision: it is an order.
3. Spreads and depth of the symbols that print >= 15 bps (the 1.5 bps slip is an assumption).
4. Delisted contracts that have left exchangeInfo are missing from the universe (136 SETTLING ones are included).

Nothing here authorises an order, a size, or a change of any cap.

## 8. Reproduce

```
python tools/fetch_funding_universe.py                  # 659 symbols, about 30 min (rate limit)
python -m research.carry_convex_study events            # setups only
python -m research.carry_convex_study klines --workers 16   # 37 618 windows, about 45 min, 220 MB
python -m research.carry_convex_study run               # 98 s
python -m research.carry_convex_study addendum          # A2 + capped growth
python tools/snipe_tick_check.py --refresh-funding      # A1: only the last 2 days are served
python -m pytest -q tests/test_carry_convex.py
```
