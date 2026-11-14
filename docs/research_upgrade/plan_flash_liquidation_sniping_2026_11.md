# FLS-1 — Flash liquidation sniping: pre-registration (frozen 2026-11-13, before any liquidation tape exists)

Owner directive 2026-11-13 (Phase 5). Freeze stamp: the SHA-256 of this file is logged in
`docs/research_upgrade/trial_log.jsonl` (row "FLS-1 PRE-REGISTERED"). Any change after the first data read is a NEW
study with a new id, never an edit of this one. Locked owner decisions apply: `MICRO_NET_EV_GATE=on`, OFI floor 0.65.

## 1. Hypothesis
A forced-liquidation cascade pushes the price past where liquidity would otherwise clear it (overshoot 0.20-1.50 %).
A post-only (GTX) bid placed under the liquidation price, on the side that absorbs the forced flow, is filled near the
exhaustion point and earns a mean-reverting rebound. Net expectancy after VIP0 costs is > 0.
Prior evidence (no claim yet): `research/fast_liquidation_sniper.py` (shadow, 1 s mark prices) has never been scored on
a recorded tape; the 2026-11-13 autopsy found zero gross edge in every lane measured so far.

## 2. Data (`tools/record_market.py --liq`)
```
python tools/record_market.py --liq --symbols BTCUSDT,ETHUSDT,SOLUSDT,DOGEUSDT,XRPUSDT,1000PEPEUSDT --min-free-gb 8
```
- `data/recordings/liquidations_<YYYYMMDD>.jsonl`: every `!forceOrder@arr` event of the market, with the latest
  bookTicker of that symbol attached (`book`, `age_ms`; `null` = symbol not subscribed).
- `data/recordings/<SYMBOL>/<YYYYMMDD-HH>.jsonl.gz`: `bookTicker` (touch) and `aggTrade` (prints) of the 6 symbols.
- Clock: every event is ordered by the local receive time `r` (what a live bot would have known), not by exchange time.
- ADA is excluded (1 tick = 5.65 bps > the 3 bps spread limit; ZEPHR vetoed it 879 / 879 times).

**Measurement limit (binding):** the venue pushes at most ONE liquidation per symbol per 1000 ms (the latest order).
Every cascade size below is a LOWER BOUND, and the directive's "> $100 k within 500 ms" cannot be observed: a 500 ms
window holds at most one event. The trigger is therefore defined on what the stream shows (section 3).

## 3. Cascade trigger (one trade attempt per cascade)
- Event notional = `z x ap` (filled quantity x average price) of a `forceOrder` row; side SELL = longs liquidated
  (the study BUYS), side BUY = shorts liquidated (the study SELLS).
- Cascade notional C(t) = sum of event notionals of one symbol and one side over (t - 5 s, t].
- Trigger at the first event where C(t) >= **$100 000**. A cascade ends after 60 s with no same-side event of that
  symbol; the next trigger needs a new cascade. Sensitivity rows at $50 k and $250 k are REPORTED, never selected.
- `P_liq` = `ap` of the triggering event.

## 4. Entry
- Limit price L = P_liq - k x ATR (buy; + for a sell), ATR = ATR(14) of the complete 1-minute bars of prints received
  before the minute that contains the trigger (fewer than 15 bars = UNKNOWN = no trade). k in {0.5, 1.0}.
- Post-only: if L would cross the touch at placement (buy: L >= best ask), GTX rejects it: no trade, counted
  `REJECT_CROSS`.
- Knife guard (OFI depletion), variant G in {G0, G1}:
  - G0: place at the trigger.
  - G1: place only once the level-1 order-flow imbalance of the last 5 s, measured from bookTicker updates
    (Cont-Kukanov-Stoikov e_n, OFI = sum e_n / sum |e_n|), is >= +0.65 WITH the trade (the owner's locked 0.65
    floor), waiting at most 60 s after the trigger; else `NO_FLIP`, no trade.
- Fill: only when a print trades STRICTLY through L (buy: price < L) within 60 s of placement; queue position is
  unknown, so a touch is not a fill. Unfilled = `MISSED`.

## 5. Exit (asymmetric bracket)
- SL = L - 1.0 x ATR (buy). Stop-market: the first print at or through SL, the fill print included; exit price = that
  print (gap included) minus 1 bp of slip; taker fee 5 bps.
- TP = L + R x (L - SL), R in {1.5, 2.0}; resting maker order, filled when a print trades strictly through TP;
  maker fee 2 bps.
- Time stop 15 min after the fill: exit at the touch (bid for a long) as a taker, + 1 bp slip.
- **Markout rule E1 (the directive's "reject condition", made causal):** at fill + 1 s and fill + 5 s, if the
  with-trade mid markout is negative, exit at the touch as a taker (+ 1 bp). The trade STAYS in every statistic.
  Dropping a setup because of its post-fill markout would use the outcome to select the sample (look-ahead) and is
  forbidden. A no-E1 row (E0) is reported for information only.
- One position per symbol; no new entry on a symbol while a position or order of it is open.

## 6. Costs
Binance VIP0 USD-M: maker 2 bps (entry, TP), taker 5 bps (SL, time stop, E1) + 1 bp slip on taker exits beyond the
observed print / touch. The Demo's cheaper taker (4 bps) is not used. Funding is ignored (holds <= 15 min).

## 7. Placebo
Five per filled trade: same symbol and side, a random receive time of the same UTC day at least 10 min away from any
cascade of that symbol, `P_liq` replaced by the touch mid at that time, the same k / ATR / R / G / E1 rules.
Edge = strategy - placebo (Welch).

## 8. Split, selection, acceptance
- First outcome read: when >= **21 full UTC days** are recorded (a full day = its liquidation file exists and every
  symbol has >= 23 hourly files). TRAIN = the first 15 of those 21 days, HOLDOUT = the last 6; later days are not FLS-1.
  Before that, the evaluator prints only data status (days, cascades per symbol): no outcome.
- Grid (8 variants): k {0.5, 1.0} x R {1.5, 2.0} x G {G0, G1}, all with E1. Chosen on TRAIN: highest mean net bps
  per trade with n >= 30; none -> INCONCLUSIVE (record longer; thresholds are not lowered).
- HOLDOUT, chosen variant only, read once. PASS requires all of:
  1. n >= 100 filled trades (fewer -> INCONCLUSIVE, record longer);
  2. LCB95 (one-sided, t) of mean net bps per trade > 0;
  3. mean net - mean placebo net > 0 with LCB95 > 0;
  4. positive mean net on >= half of the symbols with >= 10 trades.
- Always reported: fill rate, REJECT_CROSS / NO_FLIP / MISSED counts, mean markout at 1 / 5 / 30 s after fills,
  win rate, profit factor, per-trade Sharpe, max drawdown, net U per hour at micro sizing (0.05 U lost at the stop
  incl. fees, notional >= 5.05 U, <= 3 positions, 2-loss session stop, 0.15 U daily kill).

## 9. What a PASS allows
A shadow lane proposal only. Orders still need the net-EV gate (>= 30 real or virtual trades, LCB95 > 0) and an
owner decision; no live, no size or leverage increase.

## 10. Code
- Recorder: `tools/record_market.py --liq` (tests: `tests/test_liquidation_recorder.py`).
- Evaluator: `research/flash_liquidation_study.py` (tests: `tests/test_flash_liquidation_sniper.py`).
