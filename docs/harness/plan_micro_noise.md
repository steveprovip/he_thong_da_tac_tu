# plan - micro noise floor after the 21:33 / 21:34 Demo losses (2026-11-09 d)

Scope: code + unit tests on the mock venue. No live start. Baseline `python -m pytest -q` = 1991 passed, HEAD 43dade9; after: 2012.
Evidence read from `data/engine_trades.jsonl` (rows 111-112, code 43dade9...), not replayed. A wider stop and a stricter filter remove specific
ways to lose; nothing here adds edge or shows a profit.

## 1. What the two ledger rows say
| | 1000PEPE SELL | XRP SELL |
|---|---|---|
| path / tag / regime / macro | MR / AUTO / DOWN / MIXED | MR / AUTO / DOWN / MIXED |
| entry -> exit | 0.0051662 -> 0.0051714 (+0.1008 %) | 1.0934 -> 1.0943 (+0.0823 %) |
| hold, reason | 7 s, SL | 11 s, SL |
| gross / fee / net | -0.00515 / 0.00306 / -0.00821 U | -0.00423 / 0.00309 / -0.00732 U |
| KER 2 min / OFI at entry | 0.11 / **+0.15** | 0.33 / **+0.18** |
- Both are the MR fixed bracket 0.29 / 0.10 x SIDEWAYS 0.75 = TP 0.2175 / **SL 0.075 %** (the exit is the stop plus 0.7-2.5 bps of slip). Both are shorts taken against POSITIVE OFI, i.e. fading buying flow; the MR flow veto only blocks at |OFI| > 0.30.
- **These brackets were being refused by the 1.30 net-RR floor (RR 1.15) until the micro floor was lowered to 1.10 two changes ago** (11-09b). I flagged then that a 7.5 bps stop is 5 ticks and inside noise; the 21:33 trades are that case. The floor change unmasked them; it did not create them.

## 2. Tasks 1 and 2 - noise-floor bracket
`Profile.micro_bracket` (True), `Engine.micro_bracket(sl, tp, f, route, scalp)`:
- SL >= max(`micro_sl_floor_pct` 0.22 %, `micro_sl_atr_mult` 1.2 x ATR 1 m). TP only ever raised, to the TP whose fee-true net RR at that stop is `micro_bracket_rr` 1.40: TP = (SL + 0.08) x 1.40 + 0.04 = **0.46 %** at SL 0.22 %, and at least 1.8 x ATR 1 m (non-scalp). A wider existing bracket (DOGE asset floor 0.50 / 1.25) is left alone.
- Applied at the source (`bracket_plan`, so the churn pre-gate, feasibility and shadow probes see the same numbers) and again after the regime multipliers in `_execute` (SIDEWAYS x0.75 of 0.22 / 0.46 comes back to 0.22 / 0.46; JUMPY's stop x1.5 is kept and TP follows the RR). Live (loss-budget SL) is untouched.
- Scalp lane: the noise floor yes, TP only to the micro RR floor 1.10 (0.37 % at SL 0.22): its 45 s / 180 s timers cannot wait for 0.46 %.
- **Arithmetic differs from the directive.** TP 0.38 % at SL 0.22 % is net RR **1.13**, not 1.40 (break-even win rate 47 %); RR 1.40 needs TP 0.46 % at SL 0.22 % (0.432 % at SL 0.20 %). I built the TP for the stated requirement (RR 1.40), so the commit subject says 0.46 %, not 0.38 %.
- Loss budget: planned loss at the stop = notional x (0.22 + 0.08) % = 0.0153-0.018 U for 5.1-6.0 U (0.0159 U for the directive's SL 0.25 %), inside 0.05 U; but each stop-out now costs ~2x the 0.075 % stop (0.0153 vs 0.0079 U at 5.1 U). Compounding ceiling at this stop: 0.05 / 0.0030 = 16.7 U.
- What the bracket cannot do: on a driftless tape P(TP first) = SL / (SL + TP) = 0.22 / 0.68 = 32 %, below the 41.7 % that RR 1.40 needs; fees make a driftless strategy lose. The edge has to come from the entry filter (section 3). A TP of 0.46 % is ~6x the old target: the EJECT time-decay (180 s, MFE < 0.06 % -> scratch exit) is unchanged and will end many trades near break-even minus fees; `bracket` / `micro_bracket` in the order row and the exit reasons show how often.

## 3. Task 3 (and the KER rule of task 2) - KER + triple alignment
`Engine.micro_entry_block(side, f)` -> blockers `MICRO_KER`, `MICRO_ALIGN` (registered as statistical gates in `core/exec_funnel.py`, so they appear in the why-no-trade funnel via `churn_checks`, and enforced for every AUTO path in `_execute`):
- KER (2 min) known and >= 0.30; 5 m macro does not forbid the side; CVD 3 m does not oppose it (any opposing sign, `micro_cvd3m_usd` = 0 tolerance); OFI L10 WITH the trade >= 0.65 (`ofi_10s` is the engine's OFI L10 feature, not a 1-second one). A missing feature cannot show alignment and blocks.
- Both logged trades fail it (OFI +0.15 / +0.18 for a SELL; PEPE also KER 0.11). XRP's KER 0.33 would have passed the KER rule alone.
- **Consequence:** a fade of the last move has flow against it by construction, so the MR lane is effectively closed in micro mode (the MR lane exists for KER < 0.38, the scalp lane for KER < 0.40; only the 0.30-0.40 band is left for them, and they must pass the alignment too). That is a strategy decision, not a tuning: it removes one of the repo's two engines in micro mode. The "forbid counter-trend MR when spread volatility is unstable" clause is not implemented separately: the engine has no spread-volatility feature, and the alignment already forbids counter-flow MR at any spread.
- This is the opposite direction from 11-09b ("unshackle"): expect far fewer entries. `MICRO_KER` / `MICRO_ALIGN` counts in the funnel measure it.

## 4. Task 4 - the halt
The directive says the halt fired after 2 losses. It fired after 3: `halt_until` counted the trailing streak of AUTO losses in the whole ledger, and the row before the two new losses is the **11-08 14:36 BUY SCALP loss (EJECT, code 2398e92, 31 h old, a different run without the micro rules)**. 14:36 + PEPE + XRP = 3 in a row. The threshold (3, ~0.045 U against the 0.15 U daily kill) was already what is asked; the defect was the streak's scope.
- Fix: ledger rows and positions carry `micro` (taken under the micro rules); `Engine.micro_halt_until` counts only those, and `halt_until(..., lookback_s)` ignores losses older than the 24 h pause window.
- The two logged losses predate the flag, so after this change they do not count and the active halt clears at the next restart; the streak starts again from 0. That is more permissive than the truth for this one session (2 real micro losses already happened).

## 5. Tests
`tests/test_micro_noise.py` (21): ledger arithmetic, bracket numbers incl. ATR, RR 1.40 vs 1.13, loss budget, `bracket_plan`/funnel with the regime block, KER + alignment boundaries/unknowns/macro/CVD, funnel blockers and every AUTO path refused, the 31 h halt case, lookback, `micro` flag into the ledger. The older micro fixtures switch the new filter off (`micro_align=False`) and the raw-bracket tests pin `micro_bracket=False`.
