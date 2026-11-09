# plan - micro-capital fill starvation: adaptive TTL, hybrid IOC, 4-coin rotation, compounding (2026-11-09)

Scope: code + unit tests on the mock venue. No live start, no key use. Baseline: `python -m pytest -q` = 1923 passed, HEAD d0ee7fa.
Evidence status: the 18:52:40 DOGE SELL is not in `data/engine_orders.jsonl` (last row 11-08 14:44), so none of the claims below is replayed;
the fixes are mechanisms with order-log fields that let the next run measure them.

## 0. Why the 80.2 % SELL was cancelled after 3 s - what the code already had
- `gtx_ttl_s` = 3.0 and `dyn_ttl` (10-08w) is the existing "extend while the signal holds" feature, but the shipped demo has it in **`shadow`** (records only), and even in `on` it requires `score >= dyn_ttl_score` = **0.90** and the touch within 0.05 %. A 0.802 vote can never extend: the order is cancelled at 3 s whatever the mode.
- Peg loop: re-pegs every 0.5 s (each re-peg cancels and re-posts, i.e. gives up queue priority) up to a 0.15 % drift cap; there is no cap on the number of re-pegs.
- The message "not filled in 3s" prints the configured TTL, not the time actually waited.
- `route()` is IOC-capable (`super_edge`, OFF since 10-07p, invariant I5 "Post-Only GTX entries"); `check_profile` flags `super_edge` / `super_cross`.
- Universe: `SCAN_SYMBOLS` = BTC, SOL, DOGE. `run.bat micro` passed `--symbol DOGEUSDT --no-scan`, so one engine. `USD_SCALE` already has XRP 0.1; unknown symbols default to 0.1; `FALLBACK_SPECS` lacks ADA and 1000PEPE.
- Compounding: none. The micro sizer always returns the smallest legal lot.

## 1. Pillar 1 - adaptive TTL (micro mode only)
`Profile.micro_ttl` (True), `micro_ttl_s` 10, `micro_ttl_max_s` 15, `micro_vpin_clean` 0.65, `micro_max_reprices` 2. Active only while `micro_active()` for AUTO non-MR / non-SCALP GTX entries, so every existing test and the 5 000 U demo path are untouched.
- At the 3.0 s deadline: extend to 10 s if CLEAN = VPIN known and < 0.65 AND `tox_check(side)` says QUOTE (own-fill and tape markouts non-toxic). Unknown VPIN = not clean (UNKNOWN is not zero) -> the old 3 s.
- After 10 s: +1 s steps up to 15 s while still clean.
- Cut at once (checked every 0.5 s while resting): mid moved >= `dyn_ttl_adverse_bps` (3 bps) against the trade, touch left > `peg_max_drift_pct` (0.15 %) from the first quote, VPIN >= 0.65 or markout toxic. Reason lands in the order row (`ttl_cut`), extension in `micro_ttl_s`.
- At most 2 re-pegs (`micro_max_reprices`); beyond that the order keeps its queue place.
- Cost of the idea, not measured: a longer resting time raises the share of fills that happen because price walked THROUGH the quote (adverse selection). The order log now has what is needed to compare fill rate and 5 s markout of extended vs 3 s orders.

## 2. Pillar 2 - hybrid IOC (micro mode, opt-in)
`Profile.micro_ioc` 'off' | 'shadow' | 'on', `micro_ioc_consensus` 0.80, `micro_ioc_ofi` 0.80, `micro_ioc_min_tp_pct` 0.25. Env `MICRO_IOC`.
Route becomes IOC only when ALL hold: micro active; AUTO, not MR / SCALP; RAW swarm score (not the JEV-lifted one) >= 0.80; OFI L10 with the trade >= 0.80; and the IOC bracket is fee-true worth it: TP >= 0.25 % **and** `rr_net(ioc) >= net_rr_min`. Otherwise the order stays Post-Only GTX.
Arithmetic the directive's "MFE >= 0.25 %" leaves out: a taker entry costs 5 + 1 bps in, the stop 5 + 1 out, so loss = SL + 0.12 % and win = TP - 0.08 %. For net RR 1.30: SL 0.15 % needs TP >= 0.43 %, SL 0.20 % needs TP >= 0.50 %. The 0.25 % floor alone would let a trade with break-even win rate 61 % through, so the existing RR gate stays and decides; IOC will fire only on genuinely wide brackets.
Invariant I5: `check_profile` reports `micro_ioc == 'on'` like `super_edge`. Demo announces it (INVARIANT WARN), an armed live engine REFUSES to start. The shipped profiles keep 'off' (the lock test stays green); `run.bat micro` sets `MICRO_IOC=on` for the demo rehearsal. Live needs an owner decision to relax I5.
IOC limit = touch +/- `ioc_max_slip_pct` 0.03 %; no fill inside it = expired, never a market order.

## 3. Pillar 3 - 4-coin rotation
Universe `MICRO_UNIVERSE` = DOGE, XRP, ADA, 1000PEPE (`Profile.micro_symbols`, env `MICRO_CAPITAL_SYMBOLS`). BTC and SOL stay in the scanner as observers (signals, regime, no order).
- Lot arithmetic at a 5.10-6.00 U window (step x price must be <= the 0.90 U window width): XRP step 0.1 (0.2 U at 2 U/coin), ADA step 1 (0.45 U at 0.45), 1000PEPE step 1 (~0.01 U): all feasible. The sizer already refuses when a real lot does not fit. `FALLBACK_SPECS` gains ADA and 1000PEPE (offline fallback; the live exchangeInfo overrides it - check it).
- Scanner: `Runner` adds the missing coins to the scan only when `micro_capital == 'on'` (explicit) and `micro_rotation`; the normal demo scanner stays BTC/SOL/DOGE. LIVE runs ONE engine by design and is not changed: rotation is a demo feature until the owner wants more.
- Router (every 5 s, shared in the RiskBook): score = KER x |OFI L10| per micro coin (unknown = no score). One position (`max_positions_eff` = 1). A coin with an authorised entry yields to a fresh candidate of a higher-ranked coin (the existing `better_peer`, which was disabled for `multi_slot`, now runs in micro mode with this quality). A coin with no candidate never blocks the others.
- Not calibrated: signal thresholds for XRP / ADA / 1000PEPE use the default 0.1 USD scale and KER gates; nothing says they fit. The DOGE-only micro mode was also uncalibrated beyond DOGE's own history.
- BTC-only code that must not touch these coins in micro mode: the BTC micro-probe branch (`btc_probe_qty` would be applied to a coin as a coin quantity) is skipped while micro is active.

## 4. Pillar 4 - compounding
Directive: `target = max(5.10, min(0.85 x equity x leverage, 0.25 x wallet_cap))`. Read literally it cannot do what it says: `wallet_cap` is undefined; with a constant cap the result is a constant (any cap < 20.4 U gives 5.10 forever), and with `wallet_cap` = buying power it is 6.25 U at 5 U equity (over the 6.0 U window just shipped) and the 0.85 term is dead.
Implemented instead (`MicroCapitalSizer`, parameters `margin_frac` 0.85, `growth_frac` 0.20):
`target(E) = min(0.85 x E x L, 0.20 x E x L, loss ceiling)`, size = largest lot <= target, never below the venue floor 5.10.
- E = 5: growth target 5.0 < floor -> 5.10 (unchanged, 75 DOGE at 0.0689). E = 10 -> 10 U (145 DOGE). E = 15 -> 15 U.
- Loss ceiling: loss at stop <= `micro_max_loss_usdt` 0.05 U -> notional <= 0.05 / ((SL + 0.08 %)/100) = 17.9 U at SL 0.20 %, but **8.6 U on DOGE, whose asset floor is SL 0.50 %** (`ASSET_BRACKETS`), so DOGE compounding stops near 8.6 U (the 0.05 U invariant, LIVE_MAX_LOSS); scaling past it is a loss-budget decision, not done here.
- The mode ends above `micro_equity_max` 15 U (wallet), after which the normal profile sizing applies. For a demo rehearsal the equity used is `Profile.micro_bankroll` (5.0, env `MICRO_CAPITAL_BANKROLL`) when the mode is forced on a wallet above 15 U, so `run.bat micro` rehearses a 5 U account instead of a 5 000 U one.
- Margin guard: cap = max(20 % of wallet, window upper bound / leverage), so a compounded lot is not refused.
Use `growth_frac=0.25` to follow the directive's coefficient; at E = 5 it asks for 6.25 U, i.e. the window top has to move too.

## 5. Findings while building (one of them is a gap in the previous delivery)
- **The 4H trend lane was not isolated from `run.bat micro` as shipped in the previous commit.** The demo profile has `trend_lane='active'` and `Runner.activate` starts the TrendExecutor whenever an engine has it, even with `--symbol DOGEUSDT --no-scan`. It acts once per closed 4H bar and sizes by 10 % of the wallet (5 000 U demo -> hundreds of U on DOGE), outside every micro rule. Now: `Runner.profile` parks it (`trend_lane='off'`) while the mode is forced on, and `TrendExecutor.step` refuses any entry on a micro-active engine (exits of an existing trend position still run). If a DOGE trend position appeared on the demo account during an earlier micro run, that is where it came from.
- `Engine.book()` reads the SIGNAL feed while it is fresh, not the venue quote, so the engine's view of the touch (peg, drift, adverse checks) follows the feed; the venue quote only decides fills. The tests move the feed and let the venue follow.
- BTC-only branch `btc_probe_qty` would have been applied to a non-alt micro coin as a coin quantity; skipped while micro is active.
- DOGE's SL floor is 0.50 % (not the 0.15-0.20 % of the directive), so a DOGE micro trade risks 0.030-0.035 U at the stop, still under 0.05.

## 6. Tests (`tests/test_micro_rotation.py`)
TTL: extends on clean, stays 3 s on unknown / toxic VPIN or toxic markout, cuts on adverse / drift, <= 2 re-pegs, order-row fields, message prints the wait, non-micro untouched. IOC: every gate, RR math, off / shadow / on, invariant report, live refusal. Rotation: universe, specs, scan symbols, ranking cadence, one position, yield. Compounding: 5 / 10 / 15 / 40 U, loss ceiling, bankroll.
