# Scope: an adverse-selection filter for the chop harvester (SCOPING ONLY - no engine change in this document's commit)

Question: can something decided before the fill, at the fill, or in the first seconds after it remove enough of the stop-outs (-0.0117 U each) to lift the harvester's TP hit rate from ~46-50 % to the
67.6 % its 0.15 % / 0.15 % bracket needs, without removing most of the fills? Everything below comes from the 13 logged harvest fills (23:05 Nov 9 - 07:38 Nov 10), their Binance public aggTrades
(data/replay_overnight_20261110, untracked) and the code. 13 trades = hypothesis generation, not evidence.

## 1. What the 13 fills show
- Under the barrier-only replay (rebased entry): 6 TP / 7 SL. The stops are not slow: the 7 SL trades have a mean with-trade price move of **-8.7 bps at +60 s** against **+1.4 bps** for the 6 TPs (AUC 0.93,
  permutation p 0.01). At +30 s: -4.1 vs +3.4 bps (AUC 0.71, p 0.23); at +15 s: -3.5 vs +3.5 (0.76, p 0.14); at +1 s / +5 s there is nothing (0.57 / 0.52).
- **No pre-fill feature separates them** (AUC, 13 trades, permutation p): KER 0.74 (0.18), venue-basis 0.74 (0.18), fill wait 0.71 (0.23: the TP fills waited 16 s on average, the stopped ones 8 s - quick fills lose), OFI with the trade 0.31 (0.30: the
  stronger the OFI, the worse), VPIN 0.38, ATR 0.55, 15 s velocity 0.40, score 0.63, modeled edge_bps 0.43. Nothing passes even an uncorrected 0.10.
- The three non-stop dumps and the two stop-distance exits of the ledger all had a peak favourable move <= 0.021 %: they went the wrong way from the fill.
- The Demo book is not the mainnet tape: basis at the fill -8.3..+9.9 bps, moving up to 19 bps while a trade is open (TP = 15 bps). The engine's own 1 s / 5 s markouts and the mainnet-print markouts of the same fills
  disagree by 1-24 bps (median ~3). A filter measured on Demo fills measures the Demo matching engine as much as the market.

## 2. What already exists (extend, do not duplicate)
- `core/toxicity_guard.py` (`tox_guard='on'` in the Demo profile): PULL on VPIN > 0.65, tape maker markout 5 s <= -3 bps over >= 30 samples, or own-fill markouts (n >= 10); SKEW one tick back at -1.5 bps.
  Overnight: 8 TOX_HOLD + 1 TOX_PULL in 55 harvest attempts. VPIN at the 13 fills was 0.29-0.55, so the VPIN branch could not have caught them.
- `early_invalid_due` (engine.py:2839): CVD velocity 10 s <= -50 k$/s or OFI flipped, within 45 s of the fill. It is `'shadow'` in the Demo profile and returns None for `path == 'SCALP'`: **the harvest lane has no post-fill guard.**
- Ledger rows already carry `markout_bps` (1 s / 5 s; the 30 s sample is missing when the trade closes first), `fill_wait_ms`, `basis_bps`, `ofi`, `vpin`, `ker`, `atr_pct`, `px_vel_15s`. Unfilled attempts carry none of it.

## 3. Candidate filters (what each needs, what is known)
- **A. Before the quote** (skip a candidate): OFI persistence, queue depth at the touch vs recent trade rate, touch age, 3-5 s aggressor imbalance, trade-through rate at our price, spread regime. Hook: `scalp_candidate`.
  Needs depth/queue history for most; aggTrades alone gives only flow, velocity, KER, ATR, VPIN (exact aggressor side). Evidence now: none.
- **B. At the fill** (accept or flatten at once): fast fill = price ran through us. The `fill_wait` AUC of 0.71 points that way. Hook: `Engine.sync` after the fill. Evidence: weak (n = 13).
- **C. First 15-60 s after the fill** (a markout cut: leave at the touch when the with-trade move <= -X bps at +H s). Hook: `manage()`, extending `early_invalid_due` to SCALP with a price rule.
  In-sample on the 13: H 30 s / X 4 bps cuts 4 trades, none of which would have been a TP, and moves the replayed night from -0.049 to -0.025 U; other (H, X) cells range -0.047..-0.029. That is curve-fitting on 4 cuts. It is the only
  candidate with a visible signal, and also the one that costs a maker exit's price drift (the cut exits at the touch of an already adverse market).
- **D. Quote placement** (join the touch vs one tick behind; TTL): trades fill rate (23.6 % overnight) for price. Needs the tape simulator to price, not the Demo.
- **E. Venue-basis awareness** (skip when |basis| > X bps): Demo-only artefact; irrelevant on mainnet. Useful only to make Demo results interpretable.

## 4. How good a filter has to be (binormal score, TP-vs-SL only; base hit rate 46 % / 50 % -> 67.6 %)
Fills kept by the loosest threshold that reaches P(TP | pass) = 67.6 %:
AUC 0.60: 1.7 % / 5.4 %   | 0.65: 13 % / 23 %   | 0.70: 28 % / 40 %   | 0.75: 41 % / 52 %   | 0.80: 51 % / 61 %   | 0.90: 64 % / 71 %.
At 1.5 fills/h that is 0.2-0.4 fills/h for an AUC-0.65 filter. A single weak feature cannot rescue this bracket; the realistic outcomes are "AUC >= 0.75 from a combination" or "retire / redesign the lane (payoff ratio, TP / SL)".

## 5. How much data it takes to see it
Per group (TP / SL), two-sided 5 %, power 80 %: AUC 0.60 -> 122 (207 with 10 features Bonferroni-corrected); 0.65 -> 53 (90); 0.70 -> 29 (48); 0.75 -> 17 (29); 0.80 -> 11 (19). A 0.65 feature on the Demo at 1.5 fills/h is ~70 h for
one test and ~120 h for ten, on a venue that is not the mainnet tape. **The Demo cannot settle this; a mainnet-tape simulation of the lane can** (thousands of simulated fills per week).

## 6. Proposed phases (each ends in a decision for you)
0. Done: ledger + tape evidence, `tools/replay_overnight_exits.py`, this scope.
1. **Instrument, no behaviour change** (~0.5 day): for every harvest *attempt* (filled or not) log the feature vector at decision and at fill; post-fill with-trade markouts at 1 / 5 / 15 / 30 / 60 s from BOTH the signal feed and the venue book;
   add `shadow` rows for the first 60 s-cut candidates. Funnel counters `SWARM_*` (already in) say how many candidates reach this stage at all. Tests on the mock venue.
2. **Mainnet-tape study, offline** (~1-2 days + the download): simulate the lane over N days of public aggTrades (fill = print through the touch within the TTL, the engine's exits, fees 2 / 5 + 1 bps), 4 coins x 14-21 days;
   features computable from aggTrades only (flow, velocity, KER, ATR, VPIN, trade rate, fill speed, post-fill markout). Depth-based features (OFI, queue) need a recording: run the existing recorder
   (`tools/record_market.py --light`, public WS, no orders) in parallel for the next days. Time-separated split (last 30 % of days held out), block bootstrap by day, deflate for the number of features tried.
   Estimated download: the 13 windows came to 55 k prints / 2.3 MB JSON; a coin-day is ~0.1-0.9 M prints (DOGE lowest, PEPE highest), roughly 1-11 MB zipped, so ~60-600 MB for 4 coins x 14 days (estimate from that sample, not measured).
3. **Filter in shadow** (`micro_adverse='shadow'`, ~1 day): implement only what Phase 2 supports; log would-block / would-cut next to the real outcome for >= 400 fills. No effect on orders.
4. **Arm on the Demo** (`'on'`) only if Phases 2 and 3 agree out of sample; never on the live profile (invariant, like the harvester).

Acceptance for Phase 2 (stated before looking): out-of-sample n >= 400 simulated fills; the filtered lane's net per fill > 0 with the lower 95 % block-bootstrap bound > 0 after 2 bps maker / 5 + 1 bps stop fees;
kept fills >= 25 %; the improvement survives removing the best day. If no filter or combination meets it, the conclusion is that the 0.15 % / 0.15 % harvester has no edge on its entries and should be retired or re-bracketed, not tuned.

## 7. What I need from you
(a) OK to download ~60-600 MB of Binance public aggTrades (data.binance.vision daily zips, 4 coins x 14-21 days) for Phase 2 - or a smaller window; (b) OK for the Phase 1 logging change (no behaviour change);
(c) whether a negative Phase 2 result ends the harvester or sends it to a redesign of the payoff ratio; (d) run the recorder on the PC overnight (public WS only).

## Risks
Multiple testing on a few features; regime change between weeks; the maker-fill model is optimistic (no queue position: real fills are fewer and more adverse); the simulator reproduces the mainnet, not the Demo; the single visible
signal (C) is a post-fill damage detector, so it can only shrink the loss, not create the edge; and a filter that keeps 10-25 % of fills may leave too few trades to matter at 5 U lots (one TP nets +0.0056 U).
