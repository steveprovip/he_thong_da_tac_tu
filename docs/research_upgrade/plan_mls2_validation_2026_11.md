# MLS-2 — one confirmation read of the MLS-1 search final on unseen data (pre-registration, frozen before any download)

Owner directive 2026-11-14 (e) ("scale alpha"): download 2026-10 .. present klines for the 11 study symbols and run one
un-peeked pass of the MLS-1 search-final configuration; expand the radar to the top 40 liquid USD-M perpetuals and
simulate opportunity frequency and concurrency; wire a fractional-Kelly multiplier into `core/position_sizer.py` and
project a 5 U bankroll. Offline research only: no order, no engine start, no live, no lock or cap changed.

## 0. Premise corrected before anything runs
The directive calls MLS-1 "statistically validated" and "mathematically verified". It is neither: the search final read
+12.25 +- 12.48 bps on HOLDOUT (n 97), LCB95 -8.28, vs placebo LCB95 -0.73, deflated Sharpe 0.109 (39 trials); the
pre-registered primary variant FAILED. MLS-2 is the test that could validate it. Nothing below assumes E[net] > 0.

## 1. Frozen configuration (MLS-1 search final, `data/macro_sweep_study.json` -> search.final; no parameter may move)
trigger ABS, stop STRUCT, zone_lo 0.6319359610818935, zone_hi 0.8420870616291642, leg >= 2.0 ATR(H1), delta z 1.0, close
location 0.5, session window 3.0 h, hold 68.95329952863473 h, target floor 179.67696053016616 bps, target cap 300 bps, no
fee shield, no trail. Rules, fills, costs (maker 2, taker 5, slip 1 bp) and placebos exactly as MLS-1
(`plan_macro_liquidity_sweep_2026_11.md` sections 5, 7, 8); placebo seed 20261115. Evaluator: `research/macro_sweep_validation.py`.

## 2. Unseen partitions (no MLS rule has been run on either)
- **F (fresh months)**: the 11 MLS-1 symbols, fills from 2026-10-01 00:00 UTC to the last complete UTC day on
  `data.binance.vision` (`tools/fetch_klines_fresh.py` -> `data/klines/fresh/`, SHA-256 checked: the 2026-10 monthly zip
  plus daily zips for 2026-11). Detector state is warmed on the existing archive (2024-12 .. 2026-09) joined to the fresh
  files. Expected n about 15 (0.36 trades / day): with a per-trade sd near 123 bps the standard error is about 32 bps, so
  F alone can neither confirm nor refute +12 bps. It is reported, and it gets no verdict of its own.
- **X (unseen symbols)**: every symbol of `data/universe_months.json` (the point-in-time top-20 screen written
  2026-10-10) that is not one of the 11, on the klines already on disk. A trade counts only if its fill month is one of
  that symbol's universe months (the month before is detector warm-up). Each contiguous run of months is detected
  separately (state reset at a gap). Same costs: optimistic for the thinner names (1 bp slip, 1 tick trade-through).
  These symbol-months were chosen by volume and range on the 1st of each month, never by an MLS outcome; they
  over-represent months in which a coin was active. Same calendar period as TRAIN / HOLDOUT, so X tests transfer across
  symbols, not across time.

## 3. Confirmation test (read once): U = F + X
Standard error = the larger of the i.i.d. one and the one clustered by UTC fill day (trades of one day share the tape).
- CONFIRMED only if all hold: n >= 100; mean net bps - 1.645 SE > 0; beats its placebo with LCB95 > 0; at least half of
  the symbols with >= 5 trades are positive; deflated Sharpe >= 0.95 (null variance 1 / n, 49 trials = the 48 of MLS-1 + 1).
- REFUTED if mean + 1.645 SE < 0. Anything else: NOT CONFIRMED.
- Reported beside it, without a verdict: F and X separately; HOLDOUT + F + X pooled (the best single estimate, labelled as
  containing one partition that was already read); the 8 fee schedules of MLS-1.

## 4. Universe expansion (directive section 2): frequency and concurrency, same frozen configuration
- Measured: the point-in-time top-20 universe 2025-01 .. 2026-09 (the 11 + X, in-universe months only): trades per day
  (mean, median, p95, share of days with none), mean hold, open positions over time (time-weighted mean, p95, max),
  trades per symbol-day by group.
- Top 40: `tools/universe_months.py --top 40` on the cached daily klines (same screen: median 14-day quote volume
  >= $50 M, daily range 150-1500 bps, listed >= 90 days) gives the names of ranks 21-40 per month. Their 1 m klines are
  NOT on disk and the directive approved the download for the 11 symbols only, so the top-40 figures are an
  EXTRAPOLATION: trades scale with symbol-days at the measured X rate, mean open positions by Little's law (rate x mean
  hold). The download that would replace it with a measurement is sized and left to the owner.
- "ATR / spread > 15": ATR14(H1) over the quoted spread, read once from the live public endpoints for today's list
  (24 h ticker, bookTicker, 1 h klines); no historical spread exists, so it is a snapshot, not a point-in-time filter.

## 5. Kelly multiplier (directive section 3): evidence-gated, so that it is correct whatever MLS-2 says
`core/position_sizer.profit_funded_risk`: the money lost at the stop is
    risk = base + f x max(0, equity - bankroll),   base = the existing 0.05 U cap,   capped at 3 % of equity
    f    = 0.25 x Kelly fraction on the LOWER 95 % bound of the win rate (the rule of `fractional_kelly_risk`), 0 when <= 0
so the principal never carries more than 0.05 U per trade, any increase is paid by accumulated profit, and an edge
that is not proven adds nothing. Opt-in argument on `MicroCapitalSizer.risk_notional`; default behaviour unchanged, the
engine does not feed it, live is untouched. Evidence for f: the out-of-sample trades (HOLDOUT + F + X), wins and losses
on net R, payoff = mean win / mean loss.
Trajectory: 2 000 paths x 365 days from 5 U, bootstrap of the pooled out-of-sample net R, the rule above with the
evidence updated along each path, at three trade rates (11 symbols, top 20 measured, top 40 extrapolated); p05 / median /
p95 at 30, 90, 180 and 365 days. It shows what the sizing rule does IF the bootstrap distribution were the truth; it is
not a forecast, and it is reported whatever the verdict of section 3.

## 6. What any result allows
Nothing operational. CONFIRMED motivates a shadow lane (signals and virtual fills, no order). NOT CONFIRMED or REFUTED
leaves every gate, lock and cap as it is. No further parameter search on any of these partitions.
