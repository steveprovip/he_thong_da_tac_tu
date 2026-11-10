# 11-09f micro profit scale: 3 slots, PnL-linked lot, soft 5 m macro in the chop lane, carry yield telemetry

Scope: code + unit tests (mock venue). Nothing started or restarted; Demo only (live profile unchanged and guarded by invariants).
Economic effect UNMEASURED.

## What the 23:04-23:15 autopsy does and does not show
- Two fills: XRP SELL TP (+0.0058 U) and XRP SELL EJECT-BE (+0.0003 U, a scratch). That is 2 of 2, not "validated". Break-even win rate of the
  0.15 / 0.15 harvester is 67.6 %; the 95 % lower bound (Clopper-Pearson) of 2/2 is 15.8 %. **10 straight wins with no loss** would be the
  smallest sample whose lower bound (69.2 %) clears break-even.
- Profit per trade is notional x 0.11 % (TP 0.15 % minus 0.04 % maker round trip): 5.11 U -> 0.0056 U. That, not the slot count, is the scale.
- The 23:05-23:13 XRP / PEPE locks were the **CVD flow regime** ("STRONG DOWNTREND (SHORT ONLY)"), DOGE's was the 5 m macro ("LONG ONLY").
  "5 m macro has zero predictive power in chop" is a hypothesis; no replay in the repo measures it.

## Changes
| Pillar | Change | Flag |
|---|---|---|
| 1 slots | Demo profile `micro_max_positions` 1 -> **3** (live stays 1; `check_profile` errors on live > 1). One position per coin is inherent (one engine per symbol). `risk_cap()` in micro = min(portfolio cap, daily kill + day_pnl): money at **all** stops never passes the 0.15 U kill (3 x 0.05 U = exactly it). `portfolio_check` margin cap = n lots of the window, never above 0.85 x the **rehearsed** equity (the 5 000 U Demo wallet no longer lifts it). `better_peer`: a coin yields only when the better candidates fill every FREE slot | `micro_max_positions`, env `MICRO_MAX_POSITIONS` 1-3 |
| 2 curve | `MicroCapitalSizer.compound_target(E, L, pnl)`: `plain x max(0, 1 + k x pnl)`, `plain = min(margin_frac, growth_frac) x E x L`, capped at `max(plain, margin_frac x E x L / slots)`. The pasted `notional_base` is undefined; the plain compounding target is the reading that needs no new input. Applied in `qty`, `window_hi`, `micro_check`. Demo `micro_accel_k` 5.0, Profile / live 0 | `micro_accel_k`, env `MICRO_ACCEL_K` 0-10 |
| 2 bankroll | `MICRO_BANKROLL_DEMO` = alias of the existing `MICRO_CAPITAL_BANKROLL` (both set and different = error). **Bug in my 11-09e**: `micro_equity()` clipped any bankroll to `micro_equity_max` 15, so a 25 U bankroll sized as 15; now `max(micro_equity_max, bankroll)` | env |
| 3 macro | `scalp_candidate`: in the chop harvester lane a 5 m macro that forbids the side is ignored when OFI L10 is with the trade by >= 0.55 (the harvest is always a resting post-only maker). **Only the 5 m macro**; the CVD flow regime stays a block (different guard; the swarm already bans that side). Rows carry `macro_soft` | `micro_harvest_macro_soft` True, `micro_harvest_macro_ofi` 0.55 |
| 4 carry | The simulated book (`CarrySim`, Demo `carry_sim=True`) already accrued funding per refresh and opened ENA / WLD / HYPE / XRP; what was missing is that its state never reached the SSE snapshot (only the shadow `funding_carry` did). Now `snap['carry_sim']` + `funding_total_usdt / open / closed`, `run_days`, `funding_apr_pct` (None until 1 h of run time) and `funding_usdt` per closed pair | none |

## Arithmetic
- **Accelerator (E = 5 U + realized pnl, 3 slots)**: pnl 0 -> 5.00 U; +0.1 -> 7.225 U (plain 5.10); +0.2 -> 7.37; +0.5 -> 7.79; -0.1 -> 2.45 (venue lot);
  -0.2 -> 0. One slot only: +0.1 -> 7.65 U.
- **All-win illustration** (win = 0.11 % of notional, XRP lots, SL 0.15 % loss ceiling 21.7 U): trades to reach E = 6 / 7.5 / 10 U with k = 0:
  168 / 372 / 636; with k = 5: 121 / 265 / 450. At a 100 % hit rate the accelerator saves ~30 % of the trades, not 99 %: the pasted
  "1 666 trades" is 5 U / 0.003 U, and the true figure at 100 % wins is already ~640.
- **Loss ceiling is the cap, not the bankroll**: `risk_notional(0.15 %, 0.08 %)` = 0.05 / 0.23 % = **21.7 U** (trend lane SL 0.22 %: 16.7 U). A 25 U bankroll
  therefore sizes 21.7 U; one harvester TP there nets 21.7 x 0.11 % = **+0.024 U**. The pasted +0.03..+0.08 U/trade needs 27-73 U notional, i.e. a loss at the stop
  of 0.06-0.17 U, over the 0.05 U per-trade invariant. I kept the invariant: "max_loss proportional to bankroll" with the daily kill fixed at 0.15 U lets one trade lose more than
  the day's budget (25 U x 1 % = 0.25 U > 0.15 U), so the two cannot both hold.
- **3 slots**: worst case at the stops 3 x 0.05 U = 0.15 U = the daily kill (headroom shrinks with `day_pnl`). Margin of three smallest lots 3 x 1.02 = 3.06 U <= 0.85 x 5 = 4.25 U.
  Not independent: DOGE / XRP / ADA / PEPE move together, so three same-side positions behave like one 3x position, and the shared 3-loss / 24 h halt can fire on one correlated move.
- **Carry**: instantaneous 25-43 % APR; the repo's own replay reference is +1.90 %/yr net realized for entries taken at such spreads. `funding_apr_pct` is that estimate accrued on a
  paper book sized from the **virtual Demo wallet** (not the 5 U micro bankroll), so it never lifts the micro lot.

## Not done
- No two-sided quoting, no real funding settlement, no change to the 3-loss halt, VESKA token (score >= 0.768), RUNE veto, 0.05 U cap or 0.15 U kill.
- (done 11-09g, see below) Same-direction exposure cap across the 3 slots.
- UI display of `carry_sim` (web_ui has uncommitted owner edits; the SSE field is there for it).

## 11-09g same-side cap (owner: "Cap at 2")
`Profile.micro_max_same_side` = 2 (0 = off, env `MICRO_MAX_SAME_SIDE` 0-3): `slot_block(side)` refuses a third BUY (or SELL) while two micro positions or in-flight entries are
already on that side (`PORTFOLIO SIDE 2/2 BUY already open (correlated coins)`, surfaced by `_execute` as `SCANNER: ...`, before any token or order). An open position whose side is
unknown counts as the same side (UNKNOWN is not "other"). Micro accounts with more than one slot only; the slot count still answers first. It limits net exposure to 2 coins one way
(max 2 same-side + 1 opposite = a net of one lot at worst for 3 slots); it does not make DOGE / XRP / ADA independent, and the shared 3-loss / 24 h halt is unchanged.

## Verify / rollback
`python -m pytest -q` (tests/test_micro_profit_scale.py: 20 new; tests/test_micro_dual.py control for shadow rows; 4 older tests pin `micro_max_positions=1`).
Read-out after a Demo run: order rows `macro_soft`, `harvest`, `cvd_soft`; telemetry `micro_capital.slots / open / pnl / risk_cap / accel_k`, `carry_sim.funding_total_usdt`.
Rollback: `MICRO_MAX_POSITIONS=1`, `MICRO_ACCEL_K=0`, `micro_harvest_macro_soft=False`; code: `git revert` the commit.
