# RC-1: regime classifier v1, tuned on seen days, tested once on unseen days (2026-11-07)

Tool: `python -m research.regime_v1_eval` (labels and metrics from `tools/nightly_audit.py`). Each variant runs once
over the continuous per-second aggTrade price series of each symbol. Every 60 s its causal state is compared with the
ex-post label of the next 15 minutes.

Files:
- `rc1_dev.json`: grid 1, 11 variants, Sept 1-14. Here the CRASH trigger shared the JUMPY tail rule.
- `rc1_dev2.json`: grid 2, decoupled. CRASH keeps v0's single 4.5-sigma tail.
- `preregistration.json`: v1, the selection rule, the pass rule and code hashes. Written at 11:50:06 UTC, before the test.
- `rc1_unseen.json`: the single test run on 2026-10-01..14.

## Development (2026-09-01..14, the days that exposed v0)

- v0: the JUMPY state covered 39.0 % of points, with a false-alarm rate (JUMPY on calm-labelled points) of 38.4 %.
- Every grid-1 variant under 10 % false alarms also lost 5-8 of the 29 crashes. CRASH below a 300 bps move needs vol x3
  or a tail, and it shared the JUMPY tail flag.
- Grid 2 gave CRASH its own tail rule. Rule: among variants under 10 % false alarms with CRASH 29/29, take the highest
  protection recall. Result: **v1 = n3c**. JUMPY needs 3 tails of 4.5 sigma inside 300 s; CRASH keeps 1 tail of
  4.5 sigma. On dev: false alarm 3.8 %, protection recall 0.188 vs false restriction 0.040, CRASH 29/29.

## Test (2026-10-01..14, unseen, one run)

| | v0 | v1 (n3c) | pass rule |
|---|---|---|---|
| JUMPY false alarm | 0.172 | **0.010** | < 0.10 |
| CRASH caught / false episodes | 18/18 / 0 | **18/18 / 0** | >= 90 %, false <= v0 |
| protection recall vs false restriction | 0.338 vs 0.174 | **0.085 vs 0.013** | recall > restriction |
| JUMPY precision / recall | 0.117 / 0.301 | 0.262 / 0.049 | report only |
| accuracy vs majority baseline | 0.697 vs 0.858 | 0.814 vs 0.858 | report only |

**PASS.** Following the pre-registered decision, demo runs `regime_gate='on'` with v1; live stays off.

Limits:
- v1 catches only 4.9 % of truly jumpy periods. It is mainly a CRASH breaker and a TREND/MR style router.
- It still does not beat always guessing SIDEWAYS (0.814 vs 0.858).
- v0's false alarms were 38 % in September and 17 % in October, so this rate depends on the regime.
- Labels come from the same thresholds the classifier uses; agreement with them is not trading evidence.
