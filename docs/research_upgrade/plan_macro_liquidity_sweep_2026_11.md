# MLS-1 — Macro liquidity sweep at the London / New York opens (pre-registration, frozen before any outcome)

Owner directive 2026-11-14 (d) ("autonomous breakthrough"): audit the ledger, state where net expectancy can be
positive, then test an H4 / H1 structural sweep with 150-300 bps targets and maker routing on the 22-month kline
archive, with a TRAIN / HOLDOUT split, placebos, a deflated Sharpe ratio and a 5 U Monte Carlo.
Offline research only: no order, no engine start, no live, no change to the owner's locks or risk caps.

Inspected before this freeze: SETUP COUNTS and their geometry only (how many setups each filter leaves, stop and
target distances). No trade was simulated and no PnL of any MLS-1 rule was seen. Evaluator: `research/macro_sweep_study.py`.

## 1. Audit baseline (Phase 2; `data/engine_orders.jsonl`, `data/engine_trades.jsonl`, `core/engine.py`)
The ledger is 100 % Binance DEMO (8 738 order rows, 133 trades, 2026-10-07 .. 11-10, 8 trading days).
- 131 AUTO trades: gross +0.62 +- 1.15 bps, fee 4.57 +- 0.08 bps, net -3.95 +- 1.16 bps; sums gross +1.197 U,
  fees 5.290 U, net -4.092 U. Win rate 37.4 %, avg win +7.02, avg loss -10.51 bps: payoff 0.67, break-even win rate
  59.9 %.
- Fee per round trip: 4.00 bps maker-in / maker-out (87 trades), 6.0 bps with one taker leg (Demo taker 4 bps;
  mainnet VIP0 charges 5, so 7.0), 8.0 bps taker / taker. Engine constants: maker 2, taker 5, slip 1 bps.
- Latency (444 sent orders): decide -> send p50 98 ms, p95 600 ms; ack p50 146 ms, p95 753 ms, p99 1 520 ms.
- GTX fill rate 21.1 % (93 of 441); fill wait p50 2.64 s, p95 15.9 s; re-pegs mean 0.34.
- Stops: 10 SL exits net -20.1 bps (hold p50 12 s), 2 NO_STOP exits -36.9 bps. Stop slippage beyond the planned
  level is measurable on 2 rows only (+8.3 +- 12.0 bps): UNKNOWN in practice.
- Demo-vs-mainnet basis at the fill: sd 12.8 bps (p5 -18.5, p95 +19.8). The Demo ledger cannot resolve a 5 bps effect.
- Not usable: `markout_bps['30s']` (p50 24.5, p95 30.2 looks clipped); the autopsy's mainnet-tape value is +1.26 bps.

## 2. Where E[net] > 0 (bracket arithmetic, costs in bps: maker m, taker t, slip s; target T, stop S)
    E      = p (T - 2m) - (1 - p)(S + m + t + s)
    p*     = (S + m + t + s) / (T + S + t + s - m)                      break-even win rate
    p0     = S / (T + S)                                                driftless price (no information)
    E0     = -[2 m S + T (m + t + s)] / (T + S)                         expectancy with no information
    E      = E0 + (p - p0)(T + S + t + s - m)                           so the skill needed is p* - p0
With m 2, t 5, s 1:

| shape (T / S bps) | p* | p0 | skill needed | E0 bps | E0 in R |
|---|---|---|---|---|---|
| 15 / 15 (chop harvester) | 67.6 % | 50.0 % | 17.6 pp | -6.00 | -0.400 |
| 46 / 22 (micro trend) | 41.7 % | 32.4 % | 9.3 pp | -6.71 | -0.305 |
| 200 / 27 (MLS-1 tight stop, median) | 15.2 % | 11.9 % | 3.3 pp | -7.52 | -0.279 |
| 200 / 80 (MLS-1 structural stop) | 31.0 % | 28.6 % | 2.4 pp | -6.86 | -0.086 |
| 300 / 90 | 24.9 % | 23.1 % | 1.8 pp | -7.08 | -0.079 |

- A wide target does NOT lower the cost per trade (6-7.5 bps whatever the shape, because most trades end at the
  taker stop). It lowers the win-rate skill that pays for it, from 17.6 pp to 2-3 pp. "Fees < 2.5 % of gross" is true
  of a winning trade only.
- Cost in R is about (m + t + s) / S: a 27 bps stop pays 0.28 R per trade, an 80 bps stop 0.09 R.
- Fee inversion moves E0 by (2 - m') per maker leg and (5 - t') per taker exit: at T 200 / S 80 a 0 / 2 schedule gives
  E0 -2.14 bps, a -1.5 / 4.5 schedule -2.00 bps. E0 >= 0 needs a maker rebate of at least T (t + s) / (2 S + T) =
  3.1 bps at 200 / 80; no listed tier pays that, so only p > p0 (information) can make the bracket pay.

## 3. Premises of the directive checked against the repository's own records
- "Hyperliquid -0.015 % maker rebate": the base maker fee is +1.5 bps (taker 4.5); rebates are -0.1 .. -0.3 bps and
  need >= 0.5 % of the venue's maker volume (experiment_register VX2, 2026-10-10). "MEXC 0 % maker": API orders pay
  6 / 8 bps since 2026-06-01; 0 / 2 applies to the web UI only (VX2).
- FLS-1 under a rebate (exact re-pricing of the 336 stored boundary HOLDOUT trades, `data/fls1_cm_boundary.json`):
  Binance 2 / 5 -3.79 +- 3.14; Hyperliquid 1.5 / 4.5 -2.79; Hyperliquid top rebate -0.3 / 4.5 -0.52; MEXC web 0 / 2
  +0.95 +- 3.12 (LCB95 -4.18); the directive's -1.5 / 4.5 +1.00 +- 3.19 (LCB95 -4.24); zero fees +2.43 +- 3.09
  (LCB95 -2.65). BTC + ETH (314 of 336 trades) stay negative under every schedule (-1.86 at -1.5 / 4.5) and the pooled
  mean turns negative once the single best trade is dropped (-0.34). It does not flip to "strongly positive".
- "OFI L10 >= 0.70": no L2 depth exists for the 22 months. Substitution in section 5 (taker-delta absorption).
- "+10 U per hour from 5 U, no probability of ruin": at the 0.05 U loss cap a trade that wins 300 bps on a 27 bps stop
  nets about 0.42 U (14.3 U notional), so +10 U/h needs ~24 such winners per hour with no loser; the full confluence below produces
  ~0.03 setups per hour over 11 symbols. Fixed-fraction sizing bounds ruin only for divisible stakes; the 5.05 U
  minimum notional makes it a floor, not zero. The target is reported as unreachable, not as a tuning goal.

## 4. Data and split
Binance USD-M 1 m klines (`data/klines_1m`, SHA-256 sidecars), the 11 symbols with all 22 months: BTC, ETH, SOL,
BNB, XRP, DOGE, ADA, LINK, SUI, ENA, 1000PEPE (USDT). TRAIN = fills 2024-12-01 .. 2026-01-01, HOLDOUT =
2026-01-01 .. 2026-10-01. The HOLDOUT is not virgin: SMCF-1, SW1, AB-29 and ID6 read it for other rules. A PASS
therefore needs a confirmation on months nobody has read (2026-10 onward) before anything else.

## 5. Rules (long; short = mirror). Detector `MacroSweep`, built on `core/smc_fibo.SMCFibo` state (closed bars only)
1. H4 structure: `core.smc.SMCState(swing 2)` trend = +1 (BOS / MSS trend).
2. H1 leg: confirmed swing low L then swing high H (fractal k 2), H - L >= 2 x ATR14(H1); since H no trade above H
   and no close below L.
3. Discount zone: the 5 m trigger bar's low retraces rho = (H - low) / (H - L) in [0.705, 0.886].
4. Session: the trigger bar OPENS in [07:00, 10:00) UTC (London) or [13:30, 16:30) UTC (New York).
5. Flow catalyst (substitute for OFI L10 + CVD absorption): taker delta 2 tb - v of the trigger bar has z <= -1.0
   against the previous 48 bars AND the bar closes in its upper half ((C - L) / (H - L) >= 0.5): aggressive selling
   absorbed. Trigger `SWP` also needs the bar to sweep the last unswept confirmed 5 m swing low (low < level < close).
6. ATR14(H1) / price >= 0.25 %.
Orders:
- Entry: post-only limit at the trigger close, filled only when a 1 m low trades >= 1 tick through it within 15 min;
  cancelled if the target trades first.
- Stop: `TIGHT` = trigger low - 0.10 x ATR14(5 m); `STRUCT` = L - 0.10 x ATR14(H1). Stop-market: taker + slip, a gap
  fills at the open; stop and target in one bar = stop; the fill bar can stop out but cannot take profit.
- Target: the leg high H, its distance clipped to [150, 300] bps of the entry; maker, filled on a 1 tick trade-through.
- Eligibility: 10 bps <= risk <= 91 bps (0.05 U lost at the 5.05 U minimum notional including the 8 bps stop-out
  cost) and target >= 1.8 x risk.
- Time exit 24 h after the fill at the 1 m close (taker + slip). One position per symbol; one FILLED trade per leg.

## 6. Primary variants (4): trigger {ABS, SWP} x stop {TIGHT, STRUCT}, all in-session
Setup counts seen before the freeze (legs, before fill attrition): ABS / TIGHT 320 TRAIN, 184 HOLDOUT; ABS / STRUCT
117 / 87; the SWP trigger leaves about 40 / 19.

## 7. Costs
Primary: Binance VIP0 maker 2, taker 5, slip 1 bp on taker exits. Reported next to it, with no selection on them:
the same trades re-priced exactly (entry and target maker, stop / time exit taker) under Binance + BNB 1.8 / 4.5,
Hyperliquid 1.5 / 4.5, Hyperliquid top rebate -0.3 / 4.5, MEXC API 6 / 8, MEXC web 0 / 2, the directive's
hypothetical -1.5 / 4.5, and 0 / 0. A schedule row is a sensitivity, never a venue claim: fills, queue position and
adverse selection on another venue are not in the Binance klines.

## 8. Controls
- Placebo: for every trade 5 random 5 m closes within +- 15 days on the same symbol, same side, same stop and target
  distances as fractions of price, same simulator. Seed 20261114.
- Session ablation `OFF`: the 4 variants on triggers outside both windows. Reported with the Welch difference
  session - OFF; not eligible for selection.

## 9. Acceptance (read once)
Chosen variant = highest TRAIN mean net bps among primary variants with TRAIN n >= 50. On the HOLDOUT it PASSES only
if all hold: n >= 100; LCB95 of mean net bps > 0; beats the placebo with LCB95 > 0; >= 6 of 11 symbols positive;
deflated Sharpe >= 0.95 (per-trade Sharpe, null variance 1 / n, trials = every configuration scored in sections 6, 8
and 10). n < 100 with every other check passing = INCONCLUSIVE; anything else = FAIL. All variants are reported on
both partitions.

## 10. Exploratory search ("autoresearch"): TRAIN only
Start = the chosen primary variant. 30 rounds, each mutating ONE parameter at random (Gaussian step 0.25 x range,
clipped; toggles flip), kept only if TRAIN mean net bps improves with TRAIN n >= 50. Seed 20261114. Space:
zone_lo 0.50 .. 0.80; zone_hi 0.80 .. 1.00; leg >= 1.0 .. 4.0 ATR(H1); delta z 0.0 .. 2.5; close location 0.30 .. 0.80;
session window 1 .. 4 h from each open; hold 4 .. 72 h; target floor 100 .. 200 bps; target cap 200 .. 500 bps;
trigger ABS / SWP; stop TIGHT / STRUCT; fee shield off / on (stop to entry + 8 bps once the trade has run 1 R in
favour, effective from the next bar); trail 0 (off) .. 3.0 x ATR14(H1) behind the best price once the trade has run
100 bps in favour (values under 0.5 = off; the target stays).
After round 30: ONE HOLDOUT read of the final configuration with placebos, the TRAIN path, the number of
configurations scored and the deflated Sharpe. A TRAIN-only positive region is a hypothesis, not a result.

## 11. Account growth
`tools/micro_growth_sim.py`, 2 000 paths, 30 days, bootstrap of the HOLDOUT net R of (a) the chosen variant and (b)
the search final, at the observed HOLDOUT trade rate: 5 U, 0.05 U lost at the stop including costs, 2-loss session
stop, 0.15 U daily kill; plus a compounding run at 1 % of equity per trade. Reported: p05 / median / p95 equity,
P(equity < 1 U), P(>= +10 U).

## 12. What any result allows
Nothing operational. A PASS motivates the confirmation of section 4 and a shadow lane; a FAIL or INCONCLUSIVE leaves
the net-EV gate, the OFI floor, the 0.05 U cap and the parked 4H lane exactly as they are.
