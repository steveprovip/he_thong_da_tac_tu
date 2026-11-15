# HWK-1 — Hawkes volume-shock momentum on top movers, 10 USDT base (pre-registration, frozen before any 1 m kline of the study universe is downloaded)

Owner directive 2026-11-15 ("frontier quant synthesis"): detect self-exciting clusters of trade arrivals on top
gainer / loser altcoins, enter with the shock, take 300-600 bps at R:R >= 2.5 with the stop sized to lose 1.0 U of a
10 U base, compound 50 % of the profit after wins, and report the path toward +20 U per hour. Offline research only:
no order, no engine start, no live, no lock, gate or cap changed, nothing wired.

## 0. Premises corrected before anything runs
- "Trigger when lambda(t) > 3.0 x mu." For an exponential kernel with branching ratio n the mean intensity is
  mu / (1 - n). Published fits of trade arrivals put n at 0.7-0.95 (Filimonov and Sornette 2012; Hardiman, Bercot and
  Bouchaud 2013), so whenever n > 2/3 the AVERAGE minute already exceeds 3 mu and the literal rule is the normal
  state, not a shock. Two references are registered: MU (the literal rule) and BAR (lambda against the previous
  day's mean arrival rate, the fitted mu / (1 - n) when the fit is exact). How often each fires is reported.
- "Model lambda over trade arrivals." No trade tape of 2024-2026 is on disk and the venue serves aggTrades by time
  for 2 days only; the public aggTrades archive of one alt-day is 5-100 MB. The study uses the BINNED process: the
  1 m kline carries the number of trades and the taker-buy volume of each minute, and a Hawkes process observed in
  bins is an INAR(infinity) process estimated by conditional least squares (Kirchner 2016, 2017). Clustering inside
  one minute is not observable here.
- "At a 400 bps target a 5 bps taker fee is 1.25 % of gross PnL: fee immunity." That is the fee against a WINNING
  trade. Costs are paid on every trade: with stop S, target T and round-trip cost c the break-even win rate is
  (S + c) / (T + S), i.e. 31.4 % at 400 / 160 with c = 16 bps against 28.6 % at zero cost, and with no directional
  information the expectancy of ANY bracket is about -c (handoff 2026-11-14 d). A wide target lowers the skill
  needed (here 2.9 points of win rate); it does not supply it. Whether the trigger supplies it is the test.
- "Momentum waves of 250-600 bps." Reported from the data as the excursion distribution after a trigger.
- "Stop at 1.0 U, principal locked, initial capital cannot be impaired." These cannot all hold: the first 1.0 U loss
  takes a 10 U account to 9 U. Section 5 implements both readings (the principal carries the 1.0 U base risk; or an
  account stop ratchets to the principal once one base risk of profit exists) and reports each.
- "Risk 1.0 U per trade" is 20x the standing 0.05 U cap, above the 0.15 U daily kill and the I6 margin cap. It is a
  simulation scenario; no cap, Profile default or engine call site changes.
- "+20 U per hour from 10 U without probability of ruin": at 1.0 U risk and R:R 2.5 one winner pays 2.5 U before
  costs, so the pace needs >= 8 net winners per hour at the base size. Reported against the measured frequency.
- Frontier B (Kronos-style tokenised market state) is not part of phases 1-5 and is not tested here.

## 1. Data and partitions
- Symbols: USDT-quoted PERPETUAL contracts with underlyingType COIN in `data/funding_universe/exchangeInfo.json`
  (snapshot 2026-11-14, TRADING and SETTLING). Contracts already removed from the venue are missing (survivorship);
  BTCUSDT, ETHUSDT and the stable pairs USDCUSDT, FDUSDUSDT, TUSDUSDT, USDPUSDT, BUSDUSDT are excluded.
- Daily bars: `/fapi/v1/klines?interval=1d` from 2024-01-01 -> `data/hawkes/daily.json`.
- Universe of UTC day D, known at 00:00 of D: symbols with a daily bar on D-1 and D-2, first daily bar >= 14 days
  before D, quote volume of D-1 >= 20 000 000 USDT; r = close(D-1) / close(D-2) - 1; the 5 highest r are tagged G,
  the 5 lowest L (a symbol cannot be both; fewer than 10 eligible symbols -> the day is skipped).
- 1 m klines: data.binance.vision daily zips of D-1, D and D+1 for every (symbol, D) of the universe, each checked
  against the archive's SHA-256 -> `data/hawkes/klines_1m/`. D-1 or D missing, or more than 5 % of their minutes
  missing -> the pair is dropped (UNKNOWN, never an empty day). D+1 missing -> open trades close at the last bar
  (reason EOD).
- Funding: `data/funding_universe/fr_<SYMBOL>.json` (2024-12-20..2026-11-13). A trade pays or receives every print
  with entry < t <= exit. A trade outside the cached range has funding UNKNOWN: it is flagged, counted as 0 in the
  net and its share is reported; HOLDOUT and FRESH are inside the range.
- Partitions by trigger day D: TRAIN 2024-02-01..2025-12-31, HOLDOUT 2026-01-01..2026-09-30,
  FRESH 2026-10-01..2026-11-13.

## 2. The binned Hawkes intensity
- N_s = trades in minute s. lambda_s = mu + alpha * A_s, A_s = exp(-beta) * (A_(s-1) + N_(s-1)), A = 0 at the first
  minute of D-1; beta = ln 2 / hl with the half-life hl in minutes.
- Calibration on D-1 only (minutes 60..1439): conditional least squares of N_s on (1, A_s) -> mu_hat, alpha_hat;
  Nbar = mean N_s of those minutes; n_hat = alpha_hat / (exp(beta) - 1). mu_f = max(mu_hat, 0.05 * Nbar).
  alpha_hat <= 0 or Nbar < 1 trade per minute -> the pair is skipped for that hl.
- On day D the intensity of minute s+1 uses the counts up to minute s: rho_MU = lambda_(s+1) / mu_f,
  rho_BAR = lambda_(s+1) / Nbar.
- Reported without a verdict (T3): one-step-ahead accuracy on day D with the D-1 parameters (mean absolute error and
  Poisson deviance against LAST = N_s, MEAN = Nbar and EWMA of the same half-life), the distribution of n_hat, the
  share of minutes with rho_MU >= 3 and rho_BAR >= 3.

## 3. Trigger and trade
Decision at the close of minute s of day D, 60 <= s < 1440 (minute of day), parameters in brackets = primary:
- intensity: rho_ref >= theta [3.0], ref in {MU, BAR};
- volume: quote volume of the last 60 minutes >= kappa [3.0] x the mean hourly quote volume of the 24 hours that end
  60 minutes before s;
- direction over W [15] minutes: PRICE = sign(close_s / close_(s-W) - 1); CVD = sign(sum of 2 x taker-buy quote -
  quote volume); BOTH = PRICE when CVD agrees, else no trade; side filter ALL (WITH = long G / short L only,
  AGAINST = short G / long L only);
- one position per symbol, a new trigger only cooldown [60] minutes after that symbol's previous exit.
Entry: taker at the open of minute s+1. Exit, checked bar by bar from the entry bar: stop S bps (stop-market: at the
level, or at the bar's open when the bar opens beyond it), then target T bps (resting limit, filled at the level when
the bar reaches it); a bar that reaches both counts as the stop; time exit at the close of bar H [240]. Ratchet
(search only, off in T1): once the best price of COMPLETED bars is >= a x S in favour, the stop is max(stop, best -
d x S); it only tightens.
Costs, bps of notional (schedule BASE): entry taker 5 + slip 3; target maker 2; stop / time / EOD taker 5 + slip 3;
funding as section 1. Sensitivities: slip 1.5, slip 10, target as a taker with slip, zero fees. Net R = net bps / S.
Controls on the same triggers: FADE (side flipped), RANDOM side (5 seeds); RANDOM TIME (same pairs, the same number of
entries at uniform minutes of [60, 1440), side by PRICE).

## 4. Tests
- T1, family F0 = ref {MU, BAR} x (T / S) {300 / 120, 400 / 160, 600 / 240} x direction {BOTH, PRICE} = 12 cells, the
  other parameters primary. The cell with the highest TRAIN mean net R among cells with TRAIN n >= 200 is read once
  on HOLDOUT and FRESH (no cell with n >= 200 -> NO OPPORTUNITY SET).
- T2, TRAIN-only search: 30 rounds from the T1 cell; a round changes one parameter to another grid value and is
  kept when the TRAIN lower 95 % bound of the mean net R rises with TRAIN n >= 200. Grid: hl {1, 2, 5, 10, 20, 30};
  theta {2, 3, 4, 6, 8}; ref {MU, BAR}; kappa {2, 3, 4, 5}; W {5, 15, 30, 60}; direction {BOTH, PRICE, CVD};
  filter {ALL, WITH, AGAINST}; T {300, 400, 500, 600}; R:R {2.5, 3.0, 4.0}; H {60, 120, 240, 480, 720};
  cooldown {30, 60, 120, 240}; ratchet arm a {off, 1.0, 1.5, 2.0}; ratchet distance d {0.5, 1.0, 1.5}.
  The final configuration is read once on HOLDOUT and FRESH.
- Verdict of T1 and of T2, every check must hold, else NO EDGE: HOLDOUT n >= 100; HOLDOUT lower 95 % bound of the
  mean net bps > 0 (one-sided 1.645, SE clustered by UTC day and never below the i.i.d. SE); deflated Sharpe ratio
  >= 0.95 with trials = 12 for T1 and 12 + every configuration evaluated for T2; FRESH mean net > 0 with FRESH
  n >= 20 (fewer -> UNCONFIRMED).
- T3, reported without a verdict: section 2 accuracy; signed forward return of the trigger at 5 / 15 / 60 / 240
  minutes against RANDOM TIME; share of triggers whose favourable excursion within 240 minutes reaches 250 / 400 /
  600 bps against the adverse one; results by year, tag (G / L) and side; the controls of section 3.

## 5. Sizing and growth (accounting, no verdict)
- `core.position_sizer.profit_stack_risk` (opt-in, no engine call site): risk = base_risk 1.0 U, plus stack_frac x
  (equity - principal) when the previous `min_streak` [1] trades won and equity > principal; with `ratchet` the
  account stop moves to the principal once the equity peak is >= principal + base_risk and the risk never exceeds
  equity - stop (0 = halt).
- Notional = risk / S, capped at 20 x equity and at 1 % of the quote volume of the 60 minutes before that trigger;
  below 5 U of notional no trade; one trade cannot lose more than the equity; a path stops for good below 1.0 U.
- One slot: triggers in time order, a trigger while a position is open on any symbol is skipped.
- 2 000 paths x 30 days from 10 U, seed 20261115, each simulated day = one calendar day of HOLDOUT + FRESH drawn
  with its ordered one-slot trades (possibly none); sources: the T1 cell and the T2 final; sizings: FLAT 1.0 U,
  STACK 50 %, STACK 50 % with ratchet, STACK 70 % with ratchet, FLAT 0.05 U (the standing cap).
- Reported: equity quantiles at day 1 / 7 / 14 / 30, P(ruin), P(equity < 10 U at day 30), P(principal ever
  impaired), P(halted at the ratchet stop), P(equity >= 10 + 20 x 24 x 30 U), P(any clock hour nets >= 20 U), mean
  U per hour.

## 6. What any result allows
EDGE on T1 or T2 is evidence at 1 m resolution with assumed slippage, not an order: wiring needs a separate
authorisation, a spread / depth measurement on the triggered symbols and the standing caps. NO EDGE closes the
family: no further search on TRAIN, HOLDOUT or FRESH of this plan.
