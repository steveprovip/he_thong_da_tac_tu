# 11-10 (b) micro "drift unshackle": what blocked the 08:08-08:30 run, what changed, and the replay of the 13 harvest entries

Scope: code + unit tests + one read-only replay tool. Nothing started or restarted. Orders are not opened by any change below.

## What the 08:08-08:32 run (session counters in data/telemetry_state.json) shows
- Order log: 3 rows in 22 minutes, 0 harvest. Two `MICRO ALIGN` refusals (PEPE BUY, KER 0.40 / OFI +0.42 and KER 0.31 / OFI +0.32) and one UNFILLED legacy scalp (DOGE SELL, KER 0.38, 3 s).
- Swarm-stage first blockers over ~5 300 samples (6 symbols): NO_TRIGGER 605-824 per coin, WEAK 36-214, **DRIFT 8 (SOL 7, DOGE 1)**, MICRO_KER 5-47, MICRO_ALIGN 2-12.
- Harvest lane (KER < 0.28) candidate outcomes: HARVEST_YIELD 154 (DOGE 69, XRP 50, PEPE 35), CVD3M_LOCK 38, MACRO_LOCK 22. The check that comes after them - the swarm must back the OFI side with
  score >= 0.768 (VESKA token) - returned silently and was not counted at all.
- ADA: `RUNE VETO [ZEPHR]` in 879 of 879 samples. ZEPHR = stale book / silent tape / spread > `max_spread_bps` 3.0; ADA's tick is 0.0001 at 0.177 = 5.65 bps, so a one-tick spread is
  always over the cap. ADA can never trade in the micro rotation as configured (not changed here).

## Pillar 1 - DRIFT LOCK
- The cockpit text "DRIFT LOCK" is `radar()`'s label, chosen from the CVD regime name alone. The lock itself is the `DRIFT` churn gate of the swarm AUTO path; the harvester (`chop_scalp` -> SCALP) never
  runs through `churn_checks`, and `scalp_candidate` does not read it (DRIFT allows both sides). It was not the binding blocker (8 samples).
- DRIFT does suppress the TREND and VWAP *signals* (`regime()`), which is part of why the swarm rarely reaches 76.8 % in chop (NO_TRIGGER / WEAK); that is a signal-layer rule, not the lock, and VESKA's quorum is not touched.
- Changed: `drift_exempt()` (micro account, harvester on/shadow, KER < 0.28, `micro_align` on) skips the DRIFT gate in `churn_checks`, labels the row `CHOP HARVEST`, and clears `flow.drift_lock` in the snapshot.
  Order behaviour is unchanged: below KER 0.28 the MICRO_KER gate still refuses every non-harvest AUTO entry (test). Trend lane (KER >= 0.28) keeps DRIFT LOCK.

## Pillar 2 - the OFI 0.65 hurdle
- Both PEPE rejections were TREND-lane candidates (KER 0.40 and 0.31 are >= `micro_ker_min` 0.28, inside the legacy scalp band to 0.42), where micro ALIGN (OFI >= 0.65) is the rule. Inside the chop lane
  `micro_entry_block` is already skipped and the floor is `scalp_ofi_min` 0.30. `micro_harvest_ofi_min` = 0.30 is now its own knob (same value, no behaviour change); it would not have admitted those two.
- Not changed: lowering `micro_ofi_min` for KER >= 0.28 would reopen the entries the owner closed on 11-09d (the 21:33 losses had OFI against).

## Pillar 3 - macro locks
- `micro_harvest_macro_ofi` 0.55 -> **0.30**: with the harvest OFI floor also 0.30 the 5 m macro (`MACRO LONG ONLY`) no longer gates a chop-lane maker at all; rows keep `macro_soft`.
- The dominant events, "MACRO STRONG UPTREND (LONG ONLY) forbids SELL" / "STRONG DOWNTREND (SHORT ONLY) forbids BUY", are the **CVD flow regime** (CVD 60 s >= 0.4 M and 3 m >= 0.6 M x scale),
  not the 5 m macro. It stays a hard block: a resting offer against strong one-sided taker flow is the adverse-selection case (the three non-stop dumps of the overnight ledger had MFE <= 0.021 %), and the
  swarm already bans that side in that regime, so lifting the check in `scalp_candidate` could not produce an order anyway.
- `CVD_3M conflict` stays a size penalty only when OFI >= 0.50 and the 60 s move agrees (11-09e); otherwise a lock.
- `slot_block` is portfolio count / risk / side, not a macro filter; `entry_edge_block` only refuses AUTO entries on Kelly NO EDGE or a JEV-padded raw score. Neither is a macro lock.

## Instrumentation added
`SWARM_NO_SIDE` / `SWARM_OPPOSITE` / `SWARM_WEAK` funnel outcomes for the silent swarm-agreement return of an `on` harvest (samples, not opportunities). Read them next run: they say whether the harvester
starves on the swarm (expected: yes) rather than on any lock.

## Pillar 4 - replay of the 13 logged harvest entries (tools/replay_overnight_exits.py)  [CORRECTED 11-10 after the first commit]
Data: Binance USD-M public `fapi/v1/aggTrades`, only the 13 windows [entry - 2 s, entry + 1 200 s] (2.3 MB in `data/replay_overnight_20261110/`, untracked), fetched at ~1 request/s so the Demo bot sharing the IP stays far under
the weight limit. The daily data.binance.vision archive for 2026-11-10 does not exist yet and would be hundreds of MB. Entries are the real fills (price, quantity, time = ledger t - hold_s); only the exit rule differs.

**Validity limit found afterwards**: the Demo venue book is independent of mainnet. The ledger's venue-vs-signal basis at the fill is -8.3..+9.9 bps (median |3.6|) and moves up to 19 bps while a trade is open (PEPE 04:22: +0.2 -> -18.6),
against a 15 bps TP. The first version compared Demo fill prices with mainnet prints as they are (`--no-rebase`); the tool now moves the entry to its mainnet equivalent by the basis at the fill (default). Neither reproduces the Demo
trade by trade, so neither can rank exit policies whose difference is smaller than the replay's own error:

net U over the 13 trades (cons / opt fills)      | raw prints      | rebased by the basis
- legacy                                        | -0.034 / -0.033 | -0.055 / -0.052
- patient                                       | -0.029 / -0.029 | -0.042 / -0.042
- barriers only (TP 0.15, stop 0.15)            | -0.043 / -0.043 | -0.049 / -0.049
- ledger (actual Demo)                          | -0.031          | -0.031
- legacy replay vs ledger, mean abs error / trade | 0.0013-0.0014 U | 0.0023 U (the average |trade| is 0.0049 U)
What survives both versions: **every policy is negative**; **all 15 TP x SL brackets (TP 0.10..0.46, stop 0.10..0.22, barriers only) are negative** (raw -0.014..-0.063 U, rebased -0.032..-0.077 U);
the barrier hit rate is 6 of 12-13 against a 67.6 % break-even; patient beats legacy in both (+0.005 / +0.013 U), which is inside the calibration error. Not simulated for `patient`: the VPIN / adverse-flow / regime-flip
escalation (the tape has none). Reading: the exit rule is second-order; no bracket rescues these entries; and a 15 bps harvest cannot be validated on a Demo book that wanders +-10-19 bps from mainnet.

## Not done
- No change to VESKA / RUNE / the 0.05 U cap / the halt; no change to ADA's spread cap; no relaxation of `micro_ofi_min`; the CVD flow regime lock stays.
- Replay limits: touch = last print, no queue, mainnet prints vs a Demo venue, entries chosen by the engine (survivorship of maker fills), n = 13.

## Verify / rollback
`python -m pytest -q`; tests/test_micro_drift_unshackle.py (7), tests/test_replay_overnight_exits.py (15). Rollback: `micro_harvest_macro_ofi=0.55`, `drift_exempt` returns False (revert the commit).
Reproduce the replay: `python -I tools/replay_overnight_exits.py replay --ledger data/engine_trades.jsonl --since 2026-11-09T23:00:00+07:00 --data data/replay_overnight_20261110 [--tp X --sl Y] [--no-rebase]`.
