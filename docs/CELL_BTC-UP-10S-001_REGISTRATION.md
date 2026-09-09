# REGISTRATION — FROZEN CELL BTC-UP-10S-001 (candidate, NOT authorized to trade)
# Status: REGISTERED 2026-09-09 — research hypothesis only. No live/testnet
# exception granted. Any fills collected under this cell must use policy
# strict-2026-09-09 and be journaled with POLICY_STAMP.
# Source: GPT-6 Astra proposal, validated against core/runtime.py,
# core/barrier_planner.py, core/position_sizer.py, live/execution_gateway.py.
cell_id: BTC-UP-10S-001
t0: null  # set epoch when confirmation window opens; 30-day clock starts here

universe:
  symbol: BTCUSDT
  regime: trending-up
  regime_definition:
    bars: 12 x 5m CLOSED candles (no forming candle)
    return_1h: "> 0"
    efficiency_ratio: "net_move / sum(abs(per-candle_move)) >= 0.60"
  at_intent:
    spread_bps: "<= 2.0"
    atr_1m_bps: "6.0 - 20.0"

entry:
  side: BUY only
  price: best bid at submit, GTX post-only, no chase / no requote
  entry_ttl_s: 2.0 from exchange acknowledgement; cancel remainder, reconcile race
  horizon_s: 10.0 from FIRST fill
  max_positions: 1, no overlapping positions
  gate: must pass STRICT (1.8 / 0.768 / 0.70) — regime is an ADDITIONAL filter, not a substitute

exit:
  filled_part: taker at +10.0s (MARKET reduceOnly)
  stop: exchange STOP_MARKET 20bps from VWAP entry, placed BEFORE / at first fill;
    early exit if triggered. Stop does not guarantee max loss (gap/slippage).

policy_cost:
  policy: STRICT
  policy_version: strict-2026-09-09
  stamp_on_every_intent: true
  cost_bps: 6.0 round-trip (maker-in 2 + taker-out 4)
  gross_from: venue fill prices (spread/slip already in price)
  also_report: net from ACTUAL commission; never a lower fee to rescue the 6bps test

checkpoint:
  target_round_trips: 2500 filled (partial fills of one intent = ONE round-trip)
  log_all: every intent incl. no-fills
  single_final_test: true
  no_interim_looks: true  # no cell edits on interim results
  stop_first: "2500 round-trips OR 30 calendar days from T0, whichever first"

budget_if_probes_ever_allowed:
  max_notional_usdt: 100 per position
  kill_usdt: -25 cumulative realized + MTM
  note: LIMIT PROPOSAL ONLY — not a trading authorization

pass_kill:
  pass: one-sided 95% lower bound of EV/intent > 0, day-block bootstrap, full sample, net-at-actual-fees also passes
  kill: deadline short of sample OR lower <= 0 -> REJECT; budget hit -> STOP
  forbidden: extend deadline, loosen gate, reselect TTL, reuse confirmation set

limits:
  n_2500_is_design_target: power NOT guaranteed
  strict_may_yield_zero_fills: zero-fill => UNCONFIRMED, never a reason to loosen
