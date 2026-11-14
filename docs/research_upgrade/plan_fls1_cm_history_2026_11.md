# FLS-1-CM — FLS-1 rules on the COIN-M liquidation history (amendment, frozen 2026-11-14 before any outcome)

Owner directive 2026-11-13 (c): evaluate FLS-1 on historical data now instead of waiting 21 days, then search the
parameters, then size a 5 U account. This file amends FLS-1 (`plan_flash_liquidation_sniping_2026_11.md`) for a
different data source. The FLS-1 live study (USD-M, recorder, 21 days) is unchanged and still the only USD-M test.

## 1. Why COIN-M, and what that costs
- `data.binance.vision` has NO USD-M liquidation archive. Listing of `data/futures/um/daily/` on 2026-11-13:
  aggTrades, bookDepth, bookTicker, indexPriceKlines, klines, markPriceKlines, metrics, premiumIndexKlines, trades.
  `liquidationSnapshot` exists only for COIN-M (`data/futures/cm/daily/`), 2023-06-25 .. 2024-10-14. 1000PEPE has
  no COIN-M contract.
- So this is a TRANSFER test: inverse, coin-margined perpetuals with less liquidity, in 2023-2024. A PASS here is not
  evidence for USD-M in 2026, and a FAIL is not proof against it. It is the only real liquidation history there is.

## 2. Data (`tools/fetch_cm_liquidation_tape.py`, SHA-256 checked)
- Symbols: SOLUSD_PERP, DOGEUSD_PERP, XRPUSD_PERP (the directive's coins) + ETHUSD_PERP, BTCUSD_PERP (deepest COIN-M
  books, most liquidations). Contract sizes from `dapi/v1/exchangeInfo`: 10 USD, BTC 100 USD.
- Liquidations: every listed `liquidationSnapshot` day; exact duplicate rows dropped; quantities (contracts) turned
  into coin-equivalent so that z x ap = USD notional.
- Tape: daily `aggTrades` of every day holding a cascade at the most permissive setting searched (section 6:
  $50 k in 10 s), plus the previous day when such a trigger falls in the first 30 min after 00:00 UTC (ATR history).
  Planned before any outcome: SOL 157 days (492 MB zipped), DOGE 93, XRP 20 (24.5 MB), ETH 343 (991 MB), BTC its own.
- Clock: exchange time. A liquidation row is acted on 250 ms after its timestamp (the archive has no receive time).

## 3. Substitutions (the archive has the prints, not the book on those days)
- Touch = trade-derived: bid = last seller-initiated print, ask = last buyer-initiated print (both needed, else
  UNKNOWN). Used for the post-only check, the E1 markouts, the time-stop exit.
- G1 = trade-flow imbalance over the last 5 s, (taker buy qty - taker sell qty) / total, >= +0.65 with the trade
  (same 0.65 floor, different flow measure). OFI L1 needs book sizes that are not stored for these days.
- Fills, stops and targets use the real prints exactly as in FLS-1 (trade-through rule, gap fills, SL before TP).
- PnL in price bps as for linear contracts. Inverse PnL (1 - entry/exit) differs by O(r^2): <= 4 bps at |r| = 2 %;
  the placebo uses the same formula.
- Costs: COIN-M VIP0 maker 2 bps, taker 5 bps, + 1 bp slip on taker exits (same as FLS-1).

## 4. Split (time-separated)
TRAIN = triggers before 2024-04-01 00:00 UTC; HOLDOUT = 2024-04-01 .. 2024-10-14. The HOLDOUT is read once per
candidate named in section 5 / 6, never to choose between them.

## 5. Primary test: the 8 FLS-1 variants
Trigger: >= $100 000 same-side notional in 5 s (FLS-1 section 3). Grid k {0.5, 1.0} x R {1.5, 2.0} x G {G0, G1-TFI},
all with E1; TTL 60 s, SL 1 ATR, time stop 15 min, 5 placebos per trade. Chosen on TRAIN (highest mean net bps,
n >= 30); acceptance on HOLDOUT exactly as FLS-1 section 8 (n >= 100 else INCONCLUSIVE; LCB95 > 0; beats placebo
with LCB95 > 0; >= half of the symbols with >= 10 trades positive). All 8 variants are reported on both partitions.

## 6. Exploratory search (the directive's "autoresearch" loop): TRAIN only
- Start: the TRAIN-best primary variant. 30 rounds; each round mutates ONE parameter at random within:
  threshold $50 k .. $250 k, window 2 .. 10 s, k 0.2 .. 1.5, R 1.0 .. 3.0, time stop 2 .. 30 min, TTL 10 .. 120 s,
  guard G0 / G1-TFI, E1 on / off.
- Keep a mutation only if TRAIN mean net bps improves with TRAIN n >= 30; otherwise revert. Seed 20261114.
- After round 30: ONE HOLDOUT evaluation of the final parameters; report the TRAIN path, the HOLDOUT result, the
  number of configurations tried, and the deflated Sharpe (null variance 1 / T, trials = all configurations scored).
- The "boundary where a 5 U account has positive net cashflow" is reported as the TRAIN region with mean net > 0,
  and whether it holds on HOLDOUT. A TRAIN-only positive region is a hypothesis, not a result.

## 7. Account growth
`tools/micro_growth_sim.py` bootstrap of the HOLDOUT net R of the final candidate: 5 U, 0.05 U lost at the stop incl.
fees (no raise of the micro cap), 2-loss session stop, 0.15 U daily kill, the observed trade rate per day.

## 8. What any result allows
Nothing operational: COIN-M 2023-24 evidence at most motivates keeping the USD-M recorder running. The net-EV gate
and the owner's locks are unchanged.
