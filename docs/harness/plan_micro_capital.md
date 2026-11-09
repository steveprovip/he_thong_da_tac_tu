# plan - micro-capital mode for a 5.0 USDT bankroll (2026-11-09, code + unit tests only)

Scope: no live start, no key use, no order. `live` stays unarmed; the 3.85 U margin cap (invariant I6) is NOT touched - see "What is not changed".
Baseline before any edit: `python -m pytest -q` = 1881 passed on the working tree, HEAD 30d59aa.

## 1. Which code is the production path
`run.bat demo|live` -> `python -m core.engine` (2-mode engine). Its sizing is `plan()` in `core/engine.py` + the `_execute` funnel,
NOT `core/position_sizer.py` (that file serves `core.runtime`, the older strict runtime). Per the directive the sizer class goes into
`core/position_sizer.py` (pure, no I/O) and `core.engine` imports it, so there is one implementation.

Already in the engine (not rebuilt): `plan()` raises any qty to `ceil_step(min_notional x 1.02 / price)` = 5.10 U for a 5 U filter;
live profile has `max_loss_usdt` 0.05 -> stop derived from the loss budget, `daily_kill_usdt` 0.15, `max_consec_losses` 3,
`equity_cap` 3.85 (margin cap), `observe_only`, `multi_slot` / `max_positions`.
Missing: an upper notional clamp, an equity-triggered mode, equity-based observer demotion, 1-position discipline in the scanner,
a 24 h halt (today `loss_pause_s` = 300 s, and `consec_losses` / `pause_until` reset to 0 on every restart).

## 2. Lot / tick / notional arithmetic (FALLBACK_SPECS; the engine overrides them with exchangeInfo when it can)
Window W = [5.10, 6.0] U (5.10 = the engine's existing 1.02 x 5 U floor, stricter than the directive's 5.05). Fees for loss math: maker in 2 + taker stop 5 + slip 1 = 0.08 %.

| symbol | price used | step (x px) | smallest lot in W | notional | lots in W | margin @5x | loss at SL 0.15 / 0.20 % |
|---|---|---|---|---|---|---|---|
| BTCUSDT | 74 000 | 0.001 (74 U) | 0.002 | 148 U (min notional is 100 U) | 0 | 29.6 U > 5 U wallet | 0.34 / 0.41 U |
| SOLUSDT | 86 | 0.01 (0.86 U) | 0.06 | 5.16 U | 1 | 1.03 U | 0.012 / 0.014 U |
| DOGEUSDT | 0.0711 (ledger 11-08) | 1 (0.071 U) | 72 | 5.12 U | 13 | 1.02 U | 0.012 / 0.014 U |
| DOGEUSDT | 0.0888 (10-07) | 1 | 58 | 5.15 U | 11 | 1.03 U | 0.012 / 0.014 U |

Consequences (several differ from the directive text):
1. **BTC is impossible by the venue rule**, not by "chunk size": min notional 100 U needs 20 U margin at 5x on a 5 U wallet.
2. **SOL is feasible on the engine's spec** (0.06 SOL = 5.16 U), but W holds exactly ONE lot (one lot = 0.86 U of the 0.9 U window). If the real LOT_SIZE is 0.1 SOL (8.6 U) it is infeasible. So the sizer must read the live filters and refuse when no lot fits, never assume. Locking SOL to observer is a policy choice (granularity), not forced by the spec.
3. **The directive's DOGE example is wrong at today's price.** 25-30 DOGE x 0.0711 = 1.78-2.13 U < 5 U -> Binance -4164. 25-30 DOGE only equals ~5.1 U at ~0.17-0.20. At 0.0711 the valid lots are 72-84 DOGE. Sizing must be price-driven.
4. **The 0.05 U loss cap never binds inside W.** Loss at stop = notional x (SL + 0.08 %): 5.16-6.0 U x 0.23-0.28 % = 0.012-0.017 U. The cap would bind only above 0.05 / 0.0028 = 17.9 U notional (SL 0.20 %). The binding constraint is the venue minimum, so risk per trade is ~0.3 % of the bankroll, far under the 1 % budget.
5. **A second blocker, found by the funnel test:** `portfolio_check` caps margin at 20 % of the wallet = 1.0 U on 5 U, but the smallest legal lot needs 5.10 U / 5x = 1.02 U, so RUNE refused every micro entry. The shipped live profile is hit too (0.77 U cap on its 3.85 U wallet). Micro mode lets one lot of the window through (6.0 U / 5x = 1.2 U); outside micro mode the guard is unchanged.
6. Reduce-only exits (TP1 half, stops) are exempt from the min-notional filter; the entry is the only order that must clear 5 U. A pegged GTX re-quote may drift 0.15 % - inside the 2 % buffer.

## 3. The "recent live SOL short" (item 3 of the directive)
- `data/engine_trades.jsonl`: 110 rows, ALL `mode=demo`, last row 11-08 14:36 (SOL ~89). No SOL trade near 85.98. `engine_orders.jsonl` has one `SHADOW_SCALP` line at 85.98 (10-09, no order). So the trade is not in the repo's data and cannot be replayed here.
- The numbers imply a demo-scale position: +0.41 % ROE at 5x = 0.082 % price move; +0.6321 U / (85.98 x 0.00082) = ~9 SOL = ~771 U notional. That is the 5 000 U DEMO account, not a micro-capital live trade (and +0.6321 U is likely unrealized gross; fees at 0.04 % round trip would take ~0.31 U).
- What the 11-09 fixes would do at +0.082 % favourable: soft BE needs MFE >= 0.12 % -> not armed; the EJECT guard only holds a scratch order while gross < 0.04 %, and 0.082 % is above it -> not held. So neither mechanism "preserved" that trade; it would have exited as before. Nothing is claimed as verified; a unit test pins this arithmetic only. If you want a replay, export the telemetry row.

## 4. Design
`core/position_sizer.py::MicroCapitalSizer` (frozen dataclass, pure): `active(wallet, mode)`, `tradable(symbol)`, `qty(price, step, min_qty, min_notional)` -> (qty, why), `loss_at_stop`, `halt_until(rows, now)`.
- active: mode `on`, or `auto` and 0 < wallet <= 15.0 (inclusive); unknown wallet in `auto` = inactive (UNKNOWN is not small).
- qty: smallest step multiple with notional >= max(5.05, 1.02 x min_notional), refuse if that exceeds 6.0 (reason names the lot). It replaces the whole Kelly / conviction / boost chain in micro mode (all of those are sized for hundreds of U).
- halt: 3 consecutive AUTO losses (same counter as the live engine) -> no entry for 86 400 s from the last loss. Derived statelessly from the ledger rows each gate check, so a restart cannot erase it.

`core/engine.py` (Profile: `micro_capital` 'auto'|'on'|'off', `micro_equity_max` 15, `micro_notional_min/max` 5.05 / 6.0, `micro_max_loss_usdt` 0.05, `micro_daily_kill_usdt` 0.15, `micro_loss_pause_s` 86 400, `micro_symbols` ('DOGEUSDT',), `micro_max_positions` 1):
- `observer` also true for a symbol outside `micro_symbols` while micro is active (BTC, SOL -> signals + regime only).
- `slot_block`: effective max positions = 1 while micro is active.
- `_execute`: before `mkpl`, `kq` = sizer qty (refuse if no lot fits); after `plan()`, refuse if loss at stop > 0.05 U or notional outside the window.
- gates: daily kill = min(configured, 0.15); 24 h halt from the ledger.
- settle: `pause_until` = micro_loss_pause_s.
- Telemetry `quant.micro_capital`.

`config/settings.py`: `Settings.micro_capital_mode` / `micro_capital_equity_max` from env `MICRO_CAPITAL_MODE` / `MICRO_CAPITAL_EQUITY_MAX` (validated); the Runner applies the same values to every Profile.

## 5. What is not changed
- Invariant I6 (`LIVE_EQUITY_CAP` 3.85) caps the MARGIN of one position, not the wallet. Micro positions use 1.0-1.2 U of margin, so a 5.0 U wallet needs no change to it; the live profile, `mock_balance` 3.85, AuthToken, RUNE veto, 0.768 quorum stay as they are.
- Default demo behaviour: wallet 5 000 U > 15 -> micro inactive -> unchanged.

## 6. Tests (`tests/test_micro_capital_5usd.py`)
Notional in [5.05, 6.0] for DOGE (3 prices) and SOL; refusal for BTC and for a coarse lot; loss at stop <= 0.05 U; BTC/SOL observer in micro, DOGE not; inactive above 15 U and in `off`; one position; daily kill 0.15; 24 h halt incl. restart; settings parsing; the +0.082 % arithmetic; engine funnel refuses an unsized symbol and sends a window-sized order on DOGE.
