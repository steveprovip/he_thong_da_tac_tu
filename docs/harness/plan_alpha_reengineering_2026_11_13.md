# plan_alpha_reengineering_2026_11_13.md — Phase 2 design + pre-registration (written BEFORE any backtest run)

Directive: "Alpha re-engineering" (owner paste, 2026-11-13). Evidence base: `docs/research_upgrade/AUTOPSY_REPORT_2026.md`
(131 AUTO trades: gross +0.62 ± 1.15 bps, fees 4.57 bps, net −3.95 ± 1.16 bps; refused signals −5.0 bps).
Scope: code + offline research only. No engine start / restart, no live, no capital / leverage raise.
Invariants untouched: 0.768 quorum, 0.70 RUNE veto, VESKA AuthToken, I6 3.85 U margin cap, 0.05 U loss at the stop,
0.15 U daily kill (tests/test_invariants_lock.py).

## A. What the directive asks vs. what the evidence allows

| Directive item | Decision | Why |
|---|---|---|
| Remove chop scalp TP 0.15 / SL 0.15 (break-even win rate 67.6 %) | `MICRO_HARVESTER=on` is refused by the env parser; `shadow` (markout logging) stays | Phase 2 tape study: TP-before-stop 45-47.5 %, 0 / 40 brackets positive. Already shadow in `run.bat micro`. |
| R:R >= 1.5-2.0 via Fibo discount + SMC sweep + OFI/CVD | Built as a causal detector `core/smc_fibo.py`, tested OFFLINE on 22 months of 1m klines (below). Engine wiring only if the pre-registered holdout passes. | On a driftless price a bracket's EV is −(c_win·SL + c_loss·TP)/(TP+SL) for ANY R:R; only directional skill helps. The 11-06 SMC event study was negative everywhere. |
| Eliminate every order with net expectancy <= 0 | `core/net_expectancy.py` + engine gate `micro_net_ev_gate` ('off' / 'shadow' / 'on'; Profile default 'shadow', `run.bat micro` sets 'on' via `MICRO_NET_EV_GATE`). Allow only if LCB95(net bps / trade) > 0 over >= 30 real or virtual trades of the micro lane; real evidence that is significantly negative overrides virtual. No evidence -> price treated as driftless -> EV = −costs -> refuse. Refusals arm the existing edge_shadow virtual trade (evidence keeps accruing). | The only gate consistent with "EV <= 0 never trades" when every lane measured so far is negative. |
| 2 consecutive losses -> stop the session | `micro_session_losses` (Profile default 2, 0 = off): two micro AUTO net losses in a row inside the current trading day (the daily-kill day) -> no entry until the next day. Ledger-derived (restart-proof). The 3-loss / 24 h halt stays. | Tightening; fewer trades on a zero edge = less fee drag. Not an edge. |
| Fee-shield BE + dynamic trailing | Exist: fee-aware soft/hard BE (11-09), runner trail max(0.20 %, 2 x ATR 1 m) from +0.50 % MFE (11-10 f). The backtest tests a fee-shield BE variant (stop to entry + 8 bps once MFE >= 1R). | No duplicate code. |
| Dynamic micro-Kelly, risk 0.05-0.15 U, amplify on streaks | `fractional_kelly_risk()` in `core/position_sizer.py`: quarter Kelly on the Beta-posterior LOWER bound of the win rate (streaks raise the posterior, so size rises with evidence, never with the streak itself), confluence/OFI scale <= 1, capped at `micro_max_loss_usdt` 0.05 U. Used by the growth simulator and the backtest's compounding run; NOT wired to the live sizer (the net-EV gate refuses entries until an edge exists). | Raising the 0.05 U cap to 0.15 U = raising risk (forbidden for research); one 0.15 U loss = the whole daily kill. |
| 5 U -> +10 U / hour | `tools/micro_growth_sim.py` computes what it requires and what the measured / backtested trade distributions give. | +200 % / hour. At 0.05 U risk and R:R 2 a winner nets <= ~0.09 U: >= 111 winners per hour with no loser. |
| Polymarket 15 m arb / flash liquidation sniping | Not run: Polymarket needs funds on another venue (outside scope), no forceOrder (liquidation) tape is recorded. Status UNKNOWN; listed in Next Steps. | No evidence, no claim. |

## B. Pre-registered SMC-Fibo study (frozen 2026-11-13 before the first run)

Data: `data/klines_1m/<SYM>-1m-<YYYY-MM>.zip` (Binance USD-M public 1m klines incl. taker-buy volume), the 11 symbols with
all 22 months: BTC, ETH, SOL, BNB, XRP, DOGE, ADA, LINK, SUI, ENA, 1000PEPE. TRAIN = 2024-12-01 .. 2025-12-31,
HOLDOUT = 2026-01-01 .. 2026-09-30 (time-separated; the holdout is read once, for the variant chosen on TRAIN).

Signal (long; short is the mirror), all on CLOSED bars, higher timeframe fed before 5 m at the same close time:
1. H4 context: `core.smc.SMCState(swing=2)` trend = +1.
2. H1 impulse leg: last confirmed H1 swing low L followed by a confirmed swing high H; H − L >= 2.0 x ATR14(H1);
   no H1 close below L and no trade above H since H (the pullback is live).
3. Discount zone: a 5 m bar whose low lies in [L, H − 0.705 (H − L)] (0.705 … 1.0 retracement; OTE 0.786 / 0.886 inside).
4. Liquidity sweep: that bar's low < the last confirmed 5 m swing low (fractal 2) and its close > that level.
5. Absorption / exhaustion: taker delta of the bar (2 x taker-buy − volume) has z <= −1.0 vs the last 48 5 m bars, and
   the close sits in the upper half of the bar (CLV >= 0.5).
6. Liquidity filter: ATR14(H1) / price >= 0.25 %; risk distance (entry − SL) / entry in [0.10 %, 1.5 %].
Orders: maker limit at the sweep bar's close, valid 15 min, filled only when a 1 m low trades >= 1 tick THROUGH it;
cancelled if the TP level trades first. SL = sweep low − 0.10 x ATR14(5 m), stop-market (taker 5 bps + 1 bp slip, gap
-> the bar open). TP = entry + R x (entry − SL), resting maker (2 bps), filled when a 1 m high trades >= 1 tick through.
Same 1 m bar touching SL and TP -> SL. Time exit 12 h at the close (taker + slip). One position per symbol.
Fees = mainnet VIP0 (maker 2 / taker 5 bps + 1 bp slip), not the cheaper Demo taker (4 bps). Funding ignored (<= 12 h).

Variants (4, the whole grid): R in {1.5, 2.0} x fee-shield BE in {off, on} (stop -> entry + 8 bps from the bar after
MFE >= 1R). Choice on TRAIN: highest mean net bps per trade with n >= 100.

Placebo: every filled trade gets 5 matched placebos: same symbol, side, SL and TP distances (%), entry at a random 5 m
close within +- 15 days, same fill / exit rules. Edge = strategy − placebo.

ACCEPTANCE (all on HOLDOUT, chosen variant only):
1. n >= 100 filled trades;
2. LCB95 of mean net bps per trade > 0 (t-based);
3. mean net − mean placebo net > 0 with LCB95 > 0 (Welch);
4. >= 6 of 11 symbols with positive mean net.
Pass -> shadow lane proposal (still no live, owner decision). Fail -> no engine wiring; the result is logged in
`docs/research_upgrade/trial_log.jsonl` and `experiment_register.md` either way.

Reported for every variant / split: n, win rate, mean net ± SE, LCB95, profit factor, per-trade Sharpe, daily Sharpe
(sqrt 365), max drawdown in R and at micro sizing (5 U start, 0.05 U lost at the stop incl. fees, min notional 5.05 U,
<= 3 positions, 2-loss session stop, 0.15 U daily kill), net U per calendar hour.

## C. Tests (new files)
- `tests/test_autopsy_2026.py`: decomposition, fee split, first-touch, tape window holes.
- `tests/test_net_expectancy.py`: driftless EV formula, break-even win rate, gate verdicts (no evidence / negative /
  proven real / proven virtual / real overrides virtual), engine wiring (shadow logs, on refuses + arms the virtual trade).
- `tests/test_smc_fibo.py`: zone / sweep / absorption on synthetic bars, no lookahead, short mirror, risk filter.
- `tests/test_session_breaker_kelly.py`: 2-loss session stop (ledger-derived, day boundary, winner resets, off at 0),
  `MICRO_HARVESTER=on` refused, `fractional_kelly_risk` (zero without edge, rises with evidence, capped), growth sim.

## D. Rollback
Profile `micro_session_losses=0`, `micro_net_ev_gate='off'`; delete the `MICRO_NET_EV_GATE` line in `run.bat`;
env parser change is one `if`. New modules are not imported by the engine except `core/net_expectancy.py`.
