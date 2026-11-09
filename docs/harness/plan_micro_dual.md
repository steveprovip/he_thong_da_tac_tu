# 11-09e micro dual mode: CHOP harvester / TREND sniper (zero-turnover directive)

Scope: code + unit tests only (mock venue). No process started, no live, no capital / leverage change. Economic effect UNMEASURED.

## What the 22:06-22:11 telemetry actually shows
- The micro filter (11-09d) wanted KER >= 0.30, 5 m macro with the trade, CVD 3 m with the trade, OFI L10 >= 0.65, all at once, for every AUTO path
  including SCALP. The scalper's own lane is KER < 0.40, so below 0.30 (the chop that fills 80 % of the day) nothing could enter.
- Separately, `TokenAuthority.issue` refuses any decision under 76.8 %: an order of any lane needs the swarm behind the side. That invariant stays.

## Changes (all in core/engine.py unless noted)
| Pillar | Change | Flag (default) |
|---|---|---|
| 1 lanes | `micro_lane(f)`: KER < `micro_ker_min` (0.30 -> **0.28**) = CHOP, else TREND, unknown = no lane | `micro_ker_min` |
| 1 CHOP | `harvest_lane`: micro account + KER < 0.28 -> `chop_scalp` runs the **harvester**: own mode switch, bracket TP 0.15 % / SL 0.15 % (TP guard 0.12-0.18 %), maker only, resting entry 45 s with the hard cuts (adverse 3 bps / spread > 3 bps / touch drift), EJECT-BE 45 s (existing `scalp_be_s`), no micro KER/ALIGN, no regime x0.75, no net-RR floor -> **break-even win-rate cap** | `micro_harvester` = **shadow** (`MICRO_HARVESTER` off/shadow/on) |
| 1 TREND | `micro_bracket`: SL 0.22 %, TP slides 0.46 % (KER 0.28) -> 0.70 % (KER 0.60); idempotent; MARIN trail engages from +0.50 % MFE | `micro_sniper_tp_max_pct` 0.70, `micro_sniper_ker_full` 0.60 |
| 2 CVD | `cvd3m_soft`: CVD 3 m opposing + OFI L10 >= 0.50 with the trade + 60 s move with it >= max(0.03 %, 0.5 ATR 1 m) -> no block, size x0.75 (floored at the venue lot). Applies in `micro_entry_block`, `scalp_candidate`, `align3_check` (3 m layer only). New feature `px_vel_60s` (micro_signals) | `micro_cvd_soft` True |
| 3 rotation | `harvest_score` = ATR 1 m x abs(OFI); `harvest_pick`: a chop-lane coin yields to the live-chop rival with the higher score; KER < 0.15 = dead tape (no score, never holds the slot) | `micro_harvest_rotate_ker` 0.15 |
| 4 compounding | `micro_equity()` on a FORCED micro run over a big (Demo) wallet was the constant `micro_bankroll` = 5.0: a won USDT never raised the lot. Now bankroll + realized NET pnl of `micro`-flagged ledger rows, in [0, `micro_equity_max`] | `micro_equity_track` True |
| invariants | `check_profile`: live + `micro_harvester='on'` is an error (Demo may rehearse; shadow is allowed live) | |

## What I did NOT do, and why
1. **No two-sided quoting.** One-way position mode, `micro_max_positions=1`; DOGE spread ~1.4 bps vs 4 bps maker round trip: capturing the spread cannot pay. The harvester is a one-sided, flow-following (side = sign OFI L10) maker scalp.
2. **Harvester default is `shadow`.** Fee-true: win = TP - 0.04 %, loss = SL + 0.08 %. TP 0.15 / SL 0.15: break-even win rate **67.6 %** (SL 0.22: 73 %; SL 0.10: 62 %). The sister scalper shape replayed -6.3..-7.5 bps; project rule: no negative-replay scalper without a time-separated holdout. Arm for a Demo rehearsal: `set MICRO_HARVESTER=on` before `run.bat micro`.
3. **VESKA/RUNE untouched.** An `on` harvest still needs `d.score >= 0.768` on the OFI side, `gates(f)`, ALIGN3 (3 m layer soft), the 0.05 U loss cap, daily kill 0.15 U and the 3-loss / 24 h halt. The halt will dominate: P(3 straight losses somewhere in 20 trades) is 44 % at a 65 % hit rate (68 % at 55 %, 20 % at 75 %), so expect 24 h halts, not 50 trades a day.
4. **Macro 5 m stays a hard block** (pasted log "wants to Sell but Macro 5m is Uptrend"): the directive's fix covers CVD 3 m only; selling into a slope-confirmed 5 m uptrend is the fade the ledger punished.
5. **The x0.75 size penalty is a no-op at the 5 U lot**: 0.75 x 5.05 U < the 5 U venue minimum, so the lot stays 72 DOGE (row `cvd_soft` says "at the venue lot"). It bites from E ~ 7 U. At 5 U the CVD 3 m relaxation is therefore a gate removal, bounded by the 0.05 U stop.
6. **Pillar 4 numbers**: 75/105/150/300 DOGE = 1.066 x E at DOGE 0.0711; the sizer gives E x 1.0 (72/98/140 at E = 5/7/10). 300 DOGE = 21.3 U loses 0.064 U at the SL 0.22 stop (> 0.05 U invariant) and sits above `micro_equity_max` 15 U (micro ends there): the ceiling `risk_notional` = 16.7 U is kept.
7. **Carry not wired.** XRP 30.9 % APR on 5 U = 0.0042 U / day and needs a spot leg; `carry_sim` stays a virtual book.

## Money arithmetic (per trade, % of notional; 5.05 U lot)
EV = w x (TP - 0.04) - (1 - w) x (SL + 0.08), harvester 0.15 / 0.15 (EJECT-BE scratches ignored):
w 0.60 -> -0.026 % | 0.65 -> -0.009 % | 0.676 -> 0 | 0.70 -> +0.008 % | 0.75 -> +0.025 % (+0.0013 U) | 0.80 -> +0.042 %.
30 trades / day at w = 0.75 = +0.038 U / day. The trend lane's 0.46 % TP is +0.021 U per win. Neither is a live-money plan.

## Verify
`python -m pytest -q` (tests/test_micro_dual.py: 31 new; tests/test_micro_noise.py: KER 0.30 -> 0.28 pins).
Demo read-out: `engine_orders.jsonl` rows with `harvest: true` / `cvd_soft`; shadow rows `SHADOW_SCALP` + `harvest`, `tp_pct`, `sl_pct`, `be_wr`; funnel `HARVEST_YIELD`; telemetry `micro_capital.lane / harvester / harvest_score / equity`.

## Rollback
`micro_harvester='off'` (exact old scalp rules), `micro_cvd_soft=False`, `micro_sniper_tp_max_pct=0`, `micro_equity_track=False`, `micro_ker_min=0.30`; code: `git revert` the commit.
