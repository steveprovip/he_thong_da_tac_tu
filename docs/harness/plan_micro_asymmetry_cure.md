# 11-10 micro exit asymmetry: ledger check of the overnight autopsy, opt-in "patient" exit policy

Scope: code + unit tests on the mock venue. Nothing started or restarted. Default policy is `legacy` (behaviour unchanged); the new policy is
opt-in and UNVALIDATED: the repo has no tape of that night, so the counterfactual "hold instead of scratch / dump" has never been replayed.

## Pillar 1 - done
`micro_max_same_side` = 2 committed as `5dbd862` (full suite 2067 passed at that state).

## The autopsy against data/engine_trades.jsonl (rows 23:05 Nov 9 - 07:38 Nov 10; table pinned in tests/test_micro_asymmetry.py)
Reproduced exactly: 15 rows, 10 wins, 5 losses, gross +0.00212, fees -0.02966, net -0.02755, avg win +0.00109, avg loss -0.00768, payoff 0.141.
What the ledger contradicts:
- **Harvest stop is 0.15 %, not 0.22 %.** `bracket_src=HARVEST`, rr_gross 1.0. 0.22 % is the trend-lane stop.
- **The stop was hit, twice.** 01:44 XRP BUY closed -0.155 % (EJECT, 5 re-pegs) and 04:22 PEPE SELL closed -0.153 % through 3 taker fills. The latter is labelled
  `EJECT-BE-LOSS` because `record_outcome` appends `-LOSS` to an EJECT-BE whose gross is < 0 (engine.py:4353): a scratch order cannot fill below its floor, and 3 taker fills at the
  stop distance (-0.153 %) are what the exchange STOP_MARKET looks like (inferred from those two facts, not from an order-level trace). The "BE order yielded a negative
  net" is the label, not a floor bug.
- **One of the 15 is dust**: 03:54 PEPE, notional 0.005 U, fee 0 (the remainder of a PARTIAL fill). 14 real trades; wins 9/14.
- **The 3 non-stop dumps (01:53 DOGE, 03:26 PEPE, 07:26 DOGE) had MFE 0.00-0.021 %**: they went against the fill and never recovered inside 203-227 s. They are not winners that
  were shaken out. The dumps are maker orders (fee = the 0.04 % round trip); the loss is price, not spread. Average net of the three: -0.114 % of notional.
- **Only 1 of 13 harvest trades reached TP** (25 s, +0.113 % net); no scratch ran 60 % of the TP (max MFE of the 7 harvest scratches 0.087 %).
- **"+0.24 U at 21.7 U"** counts ten TP wins. The ledger has one TP and eight scratches: the whole night at 21.7 U lots (x4.25) is net **-0.117 U** (fees -0.126 U),
  78 % of the 0.15 U daily kill.
- Order log, same window: 55 harvest attempts -> 13 fills (23.6 %), 33 unfilled, 8 TOX_HOLD, 1 TOX_PULL; 65 non-harvest REFUSED (MICRO ALIGN OFI under 0.65). 13 harvest
  trades in 8.6 h is not 20-50 a day. The run was the 4915c9d code: no `macro_soft` row exists, 3 `cvd_soft`.

## What is true in the diagnosis
The exit machine, not the bracket, sets the payoff. Legacy scalp positions carry `eject_mfe = 1e9`, so at `scalp_be_s` = 45 s every unfilled one cancels its TP and exits at
max(touch, BE floor): above the floor that is a +0.005..+0.01 % scratch; below it the order rests until `scalp_hard_s` 180 s and, once `eject_escalate_ok` allows (fee hurdle covered,
adverse momentum, or 120 s later), leaves at the touch: -0.06..-0.09 %. So the TP at +0.15 % is almost never live and the realized bracket is about +0.045 % / -0.09 %.

## Why the proposed rules are not a cure by arithmetic
Random-walk barrier model (TP +0.15 %, SL -0.15 %, net win +0.11 %, net loss -0.23 %, P(TP first) from x = (x + 0.15) / 0.30):
- scratch candidate at the BE floor (x = +0.045 %): P(TP) 65 %, EV hold = -0.009 % of notional vs scratch +0.006 %.
- dump candidate at x = -0.075 %: P(TP) 25 %, EV hold = -0.145 % vs dump -0.104..-0.126 %. The stop exit is taker + slip (0.06 %) against a 0.02 % maker dump.
Holding is a martingale before costs and pays more on the exit side, so it only helps if 1-3 minute price action in chop mean-reverts. That is a hypothesis; the ledger's MFE
distribution (12 of 13 harvest trades never reached +0.09 %) does not support it, and the break-even hit rate of the bracket is 67.6 %.

## Pillar 2 + 3 - implemented as `micro_exit_policy` (default `legacy`; env `MICRO_EXIT_POLICY=patient`)
Applies only to positions opened by the chop harvester (`xm['harvest']`); the order row gets `exit_policy: patient`.
- BE schedule `micro_patient_be_s` = 180 s (was 45 s): the TP stays live meanwhile.
- `eject_due` (patient): scratch only if age >= 180 s AND MFE >= `micro_patient_be_mfe_pct` 0.09 % (60 % of the TP) and TP not filled. **The directive's wording
  "prohibit EJECT-BE unless MFE >= 0.09 %" inverts the legacy test (`eject_mfe`: scratch when the trade has NOT moved); I implemented the directive's version, so a trade
  that never moved is never scratched and runs to TP or the exchange SL.**
- `eject_escalate_ok` (patient) = `eject_toxic`: leave at the touch only if gross already covers entry fee + maker exit fee (a profitable touch exit), or VPIN known and >= 0.80, or
  adverse aggressor flow >= the trade's `flow_limit`, or the CVD regime flipped against it. Time alone never escalates; there is no `eject_defer_max_s` bound.
- `eject_floor` (patient) = entry +/- (entry fee + maker exit fee) one tick further in our favour: strictly above the 0.04 % round trip.
- Unchanged and still live: exchange SL (0.15 %), FLOW and REGIME soft exits, daily kill, 3-loss halt. No new time stop: a patient position can hold a slot until TP / SL / toxic flow.
Cost: -0.0008 U per scratch trade and -0.001..-0.002 U per dump trade under the driftless model; positive only if chop mean-reverts. 12 new tests.

## Pillar 4 - not implemented, with the reason
- The lot is already `min(compound target, margin share of one slot, 0.05 U loss ceiling)`. The ceiling at SL 0.15 % is 21.74 U, but one of three slots may use only
  0.85 x E x 5 / 3 = 7.08 U at E = 5 U (the accelerator reaches that after +0.1 U). 21.7 U x 3 needs 13.0 U of margin against 4.25 U allowed; the ceiling lot is reachable
  from a bankroll of ~15.4 U (`MICRO_CAPITAL_BANKROLL=16` on the Demo), no code change.
- Scaling a lane with a measured net of -0.0276 U per night by 4.25 is -0.117 U per night. CLAUDE.md: small samples do not authorize size increases.

## To settle it with data (not done: needs the owner's OK to download)
Binance public aggTrades for XRPUSDT / DOGEUSDT / 1000PEPEUSDT on 2026-11-09/10 (`tools/download_public_l2.py` exists, untracked) -> replay the 14 entries with legacy vs patient
exits at realistic fills. Or run `MICRO_EXIT_POLICY=patient` on the Demo for a few nights and compare by `exit_policy` in the ledger (confounded by the market).

## Verify / rollback
`python -m pytest -q`; tests/test_micro_asymmetry.py. Rollback: leave `micro_exit_policy='legacy'` (default).
