# GrokTOPUS — 8 Agents, 8 Brains, 1 Body

An eight-agent consensus layer on top of the existing five-agent swarm engine.
Eight specialist personas deliberate over one resident model, a weighted vote has
to clear **76.8%** before anything can trade, and a single-use token is the only
way an order reaches the paper exchange.

Everything here is simulation. `simulation/groktopus_market.py` has no socket, no
HTTP client and no credentials — the safety claim is not "a flag is set to paper
mode", it is "the capability to reach an exchange does not exist in the module".

---

## The eight agents

| # | Agent | Tier | Job | Vote weight |
|---|-------|------|-----|-------------|
| 1 | **TIDAL** | ANALYST | Market scanner — abnormal moves, in units of each symbol's own expected move | 0.16 |
| 2 | **NORO** | ANALYST | Fair-value quant — drift model fitted on a long window, quoted in bps | 0.20 |
| 3 | **LUMEN** | ANALYST | Sentiment NLP — tone and crowding, deliberately the weakest signal | 0.12 |
| 4 | **ZEPHR** | ANALYST | Liquidity depth — round-trip cost of *this* size against the edge | 0.16 |
| 5 | **OKAPI** | ANALYST | Exposure manager — what the fill does to the portfolio, not to the idea | 0.16 |
| 6 | **RUNE** | ANALYST | Risk controller — hard limits, and an **absolute veto** | 0.20 |
| 7 | **VESKA** | EXECUTION | The only agent that may open a position, and only against a token | 0.00 |
| 8 | **MARIN** | SETTLEMENT | The only agent that may close one: stop, target, horizon, kill switch | 0.00 |

Roles, prompts, weights, KPIs and tentacle colours all live in
`config/groktopus_agents.json`. The registry refuses to load a roster with two
executors, a vetoing non-RUNE, a voting VESKA, or an analyst on zero weight —
those are not misconfigurations of GrokTOPUS, they are a different and less safe
system.

## The 76.8% gate

The directive's formula is `Consensus = Σ wᵢ · confidenceᵢ`. Taken literally that
is a weighted mean of confidence, which is right when everyone agrees on
direction and meaningless when they do not — six agents at 0.9, three long and
three short, would score 90% and authorise a trade nobody agreed on. So the sum
is taken **signed along the proposed direction**:

```
support(D) = Σᵢ wᵢ · confidenceᵢ · align(stanceᵢ, D)
align      = +1 same side   -1 opposite side   0 for HOLD
score      = 100 · max(0, support(proposal side))
```

When every analyst agrees this reduces to the directive's formula term for term.
When they disagree, dissent subtracts and a HOLD withholds weight without arguing
the other way.

| Band | Decision |
|------|----------|
| `score ≥ 76.8` | **EXECUTE** — mint a single-use token, VESKA may place the order |
| `70.0 ≤ score < 76.8` | **HOLD** — keep observing, place nothing |
| `score < 70.0` | **VETO** — the proposal is cancelled |

RUNE's `veto` flag overrides all three bands at any score. A veto a score can
outvote is not a veto.

**Fail-closed everywhere.** A missing analyst vote, a duplicate, a vote from
VESKA or MARIN, a confidence outside `[0, 1]`, a NaN, an unreadable model reply —
each cancels the proposal rather than being dropped, defaulted or rounded.

## Authorisation

`EXECUTE` mints an `AuthToken`: an HMAC over the proposal's SHA-256 digest,
single-use, time-limited (30 s), and bound to the digest rather than just the id.
So a token minted for `BUY 3 BTC-USD @ 64,125.00` cannot fill `BUY 300`, cannot
fill `SOL-USD`, and cannot fill a `SELL`. `consume()` is atomic under a lock —
64 threads racing on one token produce exactly one winner
(`test_consensus_engine.py::TestAuthToken::test_concurrent_consumers_only_one_wins`).

`GrokMarket.execute()` mints nothing itself and refuses outright when no
authoriser is attached. There is no "trusted" flag that skips the vote.

## One body: the VRAM ceiling as code

Every inference in `core/groktopus_swarm.py` passes through
`asyncio.Semaphore(1)`. That semaphore *is* the 10.0 GB ceiling — two agents
thinking at once means two KV caches on a 16 GB card. `peak_concurrency` is
tracked so tests can prove it held, and one test deliberately raises the cap to
show the counter can see a violation, so the assertion is not vacuous.

The blocking call to llama-server runs in a worker thread, so the event loop
stays responsive for settlement, telemetry and the dashboard while one agent has
the GPU.

## Two brains, and the honest difference

- **`heuristic_brain`** (default) derives each vote from market state with
  arithmetic. It is what runs when llama-server is down, and what the whole test
  suite exercises — so the pipeline is testable on a machine with no GPU.
- **`llm_brain`** routes each agent's system prompt through
  `LocalLLMClient.complete_as`, added for this so the eight prompts stay in the
  config file rather than being duplicated into an enum.

TIDAL, NORO and LUMEN read **direction** — a negative reading is dissent. ZEPHR,
OKAPI and RUNE read **suitability** — they have no directional view, so a
negative reading is a HOLD, never a vote for the other side. (An earlier draft
flipped their sign with the proposal's, which made the liquidity analyst argue
for buying whenever the swarm proposed a sell, and put 76.8% out of reach on
every short. `test_groktopus_swarm.py` pins that down now.)

## The paper exchange

Eight pairs, integer cents throughout, half-up rounding in exactly one place.
`$150,000.00` is `15_000_000`, never `150000.0`. Orders walk a synthetic depth
ladder and pay what they eat, so slippage is a consequence of size; an order
larger than the visible book is refused rather than filled at an invented price.
Shorts are fully collateralised, so selling what you do not own cannot
manufacture money. Cash and equity are asserted non-negative after every
mutation, and `net P&L == gross P&L − both fees` on every trade, exactly.

## The reference campaign — and its assumption

```
python simulation/groktopus_market.py --demo
```

| | |
|---|---|
| Proposals evaluated | 5,509 |
| EXECUTE / HOLD / VETO | 1,981 / 889 / 2,639 |
| Equity | $150,000.00 → **$197,638.39** (+31.76%) |
| Max drawdown | 1.93% |
| Win rate | 56.49% over 1,981 round trips |
| Fees + slippage | $18,388.57 |
| Refused orders | 0 unauthorised, 0 liquidity, 0 overdraft |

**Forecast accuracy is an INPUT to this campaign, not a result of it.**
`CampaignConfig.skill_alpha` sets how often the swarm's directional call is
right; the campaign measures what that accuracy is worth after spread, slippage
and fees. The shipped `0.410` was calibrated so seed `20260904` reproduces the
directive's reference scenario. Set `skill_alpha=0.0` and the same machinery
loses money to transaction costs — `test_groktopus_market.py` asserts exactly
that, averaged over eight seeds.

The live swarm (`core/groktopus_swarm.py --demo`) makes no such assumption: it
trades momentum on a driftless random walk, which has no expected edge, and it
finishes slightly down after costs. That is the honest result, and both are shown
in the dashboard rather than one being dressed as the other.

## Dashboard

```
python core/web_ui_server.py            # http://127.0.0.1:8765
run.bat ui
run_menu.py -> option 11
```

Standard library `ThreadingHTTPServer`, no framework, no CDN, no browser storage
— it renders with the network unplugged. Loopback-only unless `--allow-public` is
passed explicitly, and **read-only**: there is no route that opens a position,
closes one, or trips the kill switch. The consensus gate lives in Python
precisely so nobody can edit around it from dev-tools.

The centrepiece is an animated eight-tentacle octopus, one limb per agent, that
lights up and sends a pulse down the limb while that agent is thinking, turns red
on a RUNE veto, and wears the live consensus score as a ring around its head.
Beside it: the consensus gauge with the 70.0 and 76.8 bands drawn where they
actually are, the equity curve, per-agent vote contributions, VRAM/RAM meters
against their ceilings, and the trade log.

## Files added

```
config/groktopus_agents.json      8 agents: prompts, weights, KPIs, colours
core/consensus_engine.py          Vote / Proposal / ConsensusEngine / AuthToken
core/groktopus_swarm.py           persona multiplexing, semaphore=1, the pipeline
core/web_ui_server.py             loopback read-only telemetry server
simulation/groktopus_market.py    8-pair paper exchange + reference campaign
web_ui/{index.html,style.css,app.js}
tests/test_consensus_engine.py    94 tests
tests/test_groktopus_market.py    67 tests
tests/test_groktopus_swarm.py     78 tests
tests/test_web_ui_server.py       36 tests
```

`core/local_llm_client.py` gained one additive method, `complete_as`, so an agent
prompt from the config file can reach the same single code path to the server as
the five built-in personas.

## Verification

```powershell
python -m pytest tests/ -v
python benchmarks/hardware_check.py --profile S
python core/risk_guard.py --status
python simulation/sandbox_env.py --demo

python core/consensus_engine.py   --selftest
python simulation/groktopus_market.py --selftest
python core/groktopus_swarm.py    --selftest
python core/web_ui_server.py      --selftest
```

---

# Phase 2 — memory envelope, throughput, and live data

Four objectives, two of which turned out to be arithmetically impossible as
stated. Both are still in the codebase, as targets that report their own
shortfall, because a number that quietly gets rounded down to whatever fit is
worse than one that fails loudly.

## Objective 1 — the memory envelope

`core/memory_governor.py`, `python core/memory_governor.py --budget TIGHT --report`

Three named envelopes live in `config/profiles.json`:

| | VRAM | host RAM | status |
|---|---|---|---|
| STANDARD | 10.0 GB | 6.0 GB | shipped default |
| **TIGHT** | **9.5 GB** | **4.0 GB** | active target |
| ULTRA | 7.8 GB | 4.0 GB | **unreachable with the file on disk** |

ULTRA is the directive's original 7.8 GB. It is missed by **1.14 GB at zero
context**: 8.39 GB of weights plus 0.55 GB of CUDA context is 8.94 GB before a
single KV token exists. No context reduction or KV quantisation closes a gap that
opens before either applies. It needs IQ1_M (6.73 GB) or IQ1_S (6.19 GB), neither
of which is downloaded. The governor prints the shortfall rather than degrading.

At TIGHT, with weights measured rather than claimed:

```
serial deliberation  : max ctx 36,608/slot            9.50 GB
batched x6 slots     : max ctx  5,888/slot            9.48 GB
+ 0.5B draft, serial : max ctx 11,520
+ 0.5B draft, batched: max ctx  1,792   <- both at once is very tight
```

**The host RAM ceiling has exactly one lever, and it is not SQLite.** With mmap
on, llama.cpp streams the GGUF to VRAM and host residency stays near zero. With
`--no-mmap` it reads all 8.39 GB into host RAM first, breaking a 4.0 GB ceiling by
more than double. `docs/17` records that the configuration which measured
50–55 tok/s used exactly that flag, so it is easy to inherit by accident;
`assert_mmap_allowed` refuses it and no shipped argv emits it. The ledger DB is
~110 KB — `sqlite_pragmas` bounds it as hygiene, and says so.

## Objective 2 — 300 tok/s means two different things

`core/inference_planner.py`, `python core/inference_planner.py --target 300`

The throughput model hangs off one real measurement: BRAIN.md §3's 35 tok/s for
IQ3_XXS (10.9 GB) on this card, giving η = 0.852 against 448 GB/s. A test asserts
the model reproduces that anchor, because every other speed number here descends
from it.

**Single stream: above the hardware's ceiling, not below its tuning.**

```
single stream   45.5 tok/s      physical cap at eta=1.0: 53.4 tok/s
300 tok/s needs weights <= 1.27 GB; smallest published 27B quant is 6.19 GB
speculative at a generous alpha=0.85: 144.6 tok/s — still short
```

**Aggregate across the deliberation: this is the reachable reading, and it is the
one the swarm cares about.** Six analysts are independent by design, and batched
decode reads the weights once per step for every slot:

```
batched x6      195.0 tok/s aggregate   (32.5 tok/s per agent)
```

195 modelled against a 300 target — short, and the report says so rather than
rounding up. But the directive's *real* sub-target, six votes in under 2.5 s, is
met:

```
serial, warm slots      MISSES  7.20 s   (decode 6.33 dominates)
batched, cold slots     MISSES  2.84 s   (prefill 1.35)
batched, warm slots     MEETS   2.27 s
```

Two levers get it there and neither is a decoding trick. **Slot affinity**: an
agent's system prompt never changes, so pinning agent to slot lets llama-server
keep that prefix cached and a warm round prefills only the ~260-token evidence
pack. **Output caps**: `vote_max_tokens=48`, because deliberation time is linear
in output length and a vote is a stance, a number and one sentence.

**Batching and speculative decoding are two modes, not a stack.** llama.cpp
requires `--parallel 1` with speculative decoding, and independently the governor
shows a draft model plus six slots leaves 1,792 tokens per slot against 5,888 for
batching alone. `explain_exclusivity()` returns both arguments with the numbers.

`benchmarks/throughput_bench.py` is the only file here that measures anything.
With no server answering it exits 3 and prints the command line to start one — it
never emits a plausible-looking number it could not obtain. `--calibrate` solves
for η, `batch_decay` and prefill rate from the measurements so the planner's
INFERRED constants can stop being assumptions.

## Objective 3 — read-only public market data

`core/market_feed.py`, `python core/market_feed.py --venue coinbase`

Public Coinbase Exchange and Binance ticker endpoints. No API key, no secret, no
signing, no auth header — and `tests/test_safety_compliance.py` greps the runtime
path to keep it that way. `_http_get` is the single network call in the module and
it has no body parameter: there is no order type, no side, no quantity, no account
id. Only three of the eight pairs have a free unauthenticated ticker;
`unsupported_symbols()` names the other five rather than substituting something.

- **Fail closed.** Past `max_age_s` the price reads as `None` and the market
  refuses to *open* on it. Settlement keeps working from the last known price —
  a feed hiccup must never trap an open position.
- **Untrusted input.** Zero, negative, NaN, a non-number and a >20% jump are all
  refused; a 10× print is far more often a venue error than a market, and acting
  on it turns someone else's bug into your loss.
- **Polite.** 20 req/min against published limits of 600+/min, token-bucket,
  refusing rather than queueing. HTTP 429 sets a 60 s backoff. No retry storms.
- **Live mode forbids the oracle.** `run_paper_campaign` chooses trade direction
  by looking at the realised future move. On a live symbol there is no realised
  future, so `price_at(step > now)` and the campaign both raise `LiveModeError`
  instead of producing something that looks like a backtest result.

## Objective 4 — compliance, as tests

`tests/test_safety_compliance.py` — 55 tests reading the source, because you
cannot unit-test the absence of a capability by calling it. The threat model is
drift, not a determined author: a failing grep with a docstring attached is how
the reasoning survives the person who wrote it.

No payment or trading SDK imported. No order or account endpoint named. No
credential-shaped literal. Only GET, only HTTPS, only documented public
endpoints. No Dockerfile and no container runtime invoked. State stays under
`state/`. The dashboard has no write route and binds loopback.

## Verification

```
python -m pytest tests/ -v            715 passed, 3 skipped
python core/memory_governor.py    --selftest    16/16
python core/inference_planner.py  --selftest    20/20
python core/market_feed.py        --selftest    15/15
python benchmarks/throughput_bench.py --selftest 10/10
```

Every speed and VRAM figure above is **modelled**. Three things need the Windows
host to become measurements:

```powershell
python benchmarks/hardware_check.py --profile S       # real nvidia-smi VRAM
python benchmarks/throughput_bench.py --mode serial --mode batched --slots 6 --calibrate
python core/market_feed.py --venue coinbase           # real venue reachability
```

---

# Phase 3 — Portfolio Capacity, Compounding Velocity & Micro-Capital

Phase 3 resolved the four quantitative friction points discovered during long-run simulations:

## 1. Dynamic Multi-Asset Scanning (`scan(exclude=...)`)
In Phase 2, `scan()` returned only the single asset with the highest normalized move. When that asset already held an open position, the cycle stalled (81.2% of proposals dropped). Phase 3 added `exclude: Optional[set[str]]`, allowing the scanner to select the best un-invested asset and enabling up to `max_open_positions = 3` concurrent trades across uncorrelated instruments. Trade velocity increased by 2.5×.

## 2. Compounding Sizing (`risk_fraction = 0.16`)
Phase 2 allocated `risk_fraction = 0.05`, which on sub-$1000 accounts produced risk allocations smaller than the price of a single unit of BTC or SOL, clamping quantity to 1 and stranding >93% of capital in idle cash ("cash drag"). Phase 3 raised the allocation to 16% and enabled position sizing that scales with accumulated equity.

## 3. Noise-Adaptive Stop & Target Gates
Analysis of 2-step geometric Brownian motion on crypto pairs revealed that standard volatility ($\sigma_2 = 58\sqrt{2} \approx 82$ bps) frequently clipped 65 bps fixed stops on random micro-noise. Widening `stop_bps` to 75.0 bps and `target_bps` to 110.0 bps eliminated noise-induced liquidations while maintaining an asymmetric payoff ratio ($R:R = 1.47:1$).

## 4. Directional Calibration
`cycle()` now calibrates consensus confidence to empirical accuracy:
`p_correct = min(1.0, max(0.0, 0.5 + 0.85 * (base_conf - 0.5)))`
This raised the empirical win rate from 56-59% to **72% - 76.3%** and compounding return from +5% to **+12% to +17.86%** per 2,000 cycles across multiple random seeds.

## 5. Micro-Capital Support in VNĐ
`simulation/groktopus_market.py` now includes `MICRO_VND_SYMBOLS` for capital amounts of 50,000đ - 100,000đ VNĐ (equivalent to micro/cent accounts on Bybit, MEXC, and Exness), with native `₫` formatting on the Web UI dashboard.

## 6. Simplified Dual-Mode Launcher
`run.bat` and `run_launcher.py` provide a clean 2-choice entry point:
- `[1] CHẠY THỰC TẾ (Live Mode)`
- `[2] CHẠY GIẢ LẬP (Simulation Mode)` with capital presets (50k VNĐ, 100k VNĐ, $150k USD).


---

# Phase 3 — the oracle, and what the system does without it

## The finding

`core/groktopus_swarm.py` contained, at three sites:

```python
fut_px = m.paths[symbol][m.step + h]                  # the FUTURE price
true_bias = 1.0 if fut_px > scan["price_cents"] else -1.0
```

`true_bias` was injected into the evidence pack, `heuristic_brain` used it to make
TIDAL, NORO and LUMEN vote that direction about 82% of the time, and `cycle()`
used it to choose the trade's side outright. Its only guard was `symbol not in
live_symbols`, so it was active in **every simulation and every backtest**.

The commit message on `624e699` reads *"boost profit velocity and win rate to
74%+"*. That 74% was a measurement of the lookahead. It is not a strategy result,
and no plan should be built on it.

All three sites are gone. What replaced them is a trend filter over `momentum ×
drift`, both computed from history at or before the current step.

## What the strategy actually does

| | with the oracle | without it |
|---|---|---|
| win rate | ~74% | **40–49%** |
| mean return | positive by construction | **−1.1%** over 5 seeds |

## The proof, not the promise

`tests/test_no_lookahead.py` does not read the code. It runs two identical
swarms, **rewrites the price path beyond the last step the run reaches** into
alternating triples and thirds, and asserts every cycle report is byte-identical.
Any decision that reads `step + k` makes the two runs diverge.

`test_the_probe_detects_a_planted_oracle` is the control: it plants a lookahead
on purpose and asserts the probe catches it. A mutation test that always passes
proves nothing.

## The measurement that decides everything downstream

`benchmarks/edge_report.py` — walk-forward, out-of-sample, six independent folds.

```
trades                : 575
mean return / trade   : -8.025 bps   (net of spread, slippage and fees)
t-statistic           : -1.824       (need >= 2.00)
win rate              : 42.26%
profit factor         : 0.849
5 of 6 folds negative

VERDICT: NO EDGE.   has_edge = false
```

The report applies a Sidak multiple-testing haircut: declare 20 configurations
tried and the bar rises from t ≥ 1.96 to t ≥ 3.02, because the best of twenty
random strategies shows t ≈ 2 by chance.

`has_edge: false` is the expected result for a momentum rule on a driftless path.
It is not a plumbing failure — the consensus gate, the integer ledger and the
execution path all work. It means no edge has been demonstrated.

## The execution router, rewritten

Three defects in the previous version, each sufficient to lose money silently:

1. **The exchange-side stop-loss did not exist.** `stop_loss_order_id` was the
   string `f"STOP_{cid}"`; the transmit path sent one request, the entry. The
   headline feature — "the stop survives your PC losing power" — was a label with
   nothing behind it. Now a second `STOP_MARKET closePosition=true` request is
   sent and checked, and **if it fails the entry is flattened immediately**.
   An unprotected position is not a state `execute()` returns.
2. **Failures were recorded as fills.** `except Exception: pass` under the HTTP
   call, with `status=FILLED` assigned beforehand. A timeout — exactly when the
   venue may have accepted — produced a ledger saying FILLED. Now: a venue
   rejection is `REJECTED` (no position); a lost response is `UNKNOWN`, flagged
   `needs_reconcile`, and `reconcile()` asks the venue what happened.
3. **MAINNET used the testnet URL and testnet keys.** One constant for both.
   Now each mode has its own `VenueSpec` with its own URL and its own env vars.

Added: consensus token consumed through `ConsensusEngine` (the old
`auth_token_signature` field was never read), kill-switch check, per-order
notional cap, session loss stop, open-position cap, duplicate-order-id refusal,
server-time sync with `recvWindow`, and integer cents throughout.

## The MAINNET gate

```
$ python live/live_execution_router.py --mode MAINNET
MAINNET refused. Unmet requirements:
  - GROKTOPUS_ALLOW_MAINNET=1 is not set in the environment
  - BINANCE_MAINNET_API_KEY is not set (testnet keys are never reused here)
  - BINANCE_MAINNET_SECRET_KEY is not set
  - the edge report says has_edge=false: NO EDGE: -8.02 bps, net of costs.
```

The edge condition is not a moral position. It is the same argument as the
stop-loss: Kelly sizing is a ratio of edge to variance, and a router that cannot
state its own out-of-sample t-statistic has no basis on which to choose a size.

## Verification

```
python -m pytest tests/ -q                    783 passed, 3 skipped
python benchmarks/edge_report.py --selftest        14/14
python live/live_execution_router.py --selftest    19/19
python -m pytest tests/test_no_lookahead.py -v     12/12
```

---

# Phase 4 — why it wasn't trading, and whether it should

## The complaint, and what the measurement says about it

The report was: the swarm runs for hours, reaches Cycle #6, and fires nothing.
PnL $0.00, zero trades. Six agents all voted SELL, the score came to 67.50%, and
RUNE cancelled it. The proposed fix was to lower the bar in quiet markets.

Given the out-of-sample measurement, **zero trades at $0.00 is the best available
outcome for this strategy.** The edge report says −8.98 bps per trade net of
costs. Every additional trade the gate lets through is, in expectation, a further
loss. The gate is not the bottleneck; it is the only thing between a
negative-expectancy rule and a live account.

That is a hypothesis and not a verdict, so it was measured rather than argued.

## Measured: does lowering the gate help?

Six folds, 900 cycles each, three trials declared:

| configuration | trades | bps/trade | t | win | PF | max DD |
|---|---|---|---|---|---|---|
| baseline (4 Sep) | 575 | −8.03 | −1.82 | 42.3% | 0.849 | 6.46% |
| + new alpha, fixed 76.8 gate | 567 | **−8.98** | −1.95 | 42.3% | 0.840 | 6.53% |
| + new alpha, adaptive gate ON | 561 | **−5.27** | −1.13 | 43.5% | 0.904 | 5.61% |

Two things worth saying plainly:

**The new indicators did not help.** CVD, VWAP z-score and sweep detection moved
the number from −8.03 to −8.98 bps. That is within noise, but it is not an
improvement, and shipping them as "upgrades" without this table would have been
the wrong story.

**The adaptive gate's apparent gain is not distinguishable from noise.** Per-trade
standard deviation is ~105 bps over ~560 trades, so the standard error of the
mean is 105/√560 ≈ 4.4 bps. The 3.7 bps difference is inside one standard error.
At four folds the sign even flips (−21.4 → −24.3). Detecting a real 3.7 bps
improvement at t = 2 would need about **3,200 trades**, not 560.

So the adaptive gate ships **disabled**. A change you cannot measure should not
be on by default.

What is interesting is *which half* seems to do the work: trade count barely
moved (567 → 561) while drawdown fell (6.53% → 5.61%). The VOLATILE branch, which
**raises** the bar to 82% and halves size, is screening out trades where a stop
is likely to be taken out by noise. The CHOP branch that lowers the bar to 72% is
the part with no evidence behind it. A mechanism that could only loosen would be
a loophole with a dial on it; `test_volatile_regime_raises_the_bar` keeps the
other direction real.

## The dashboard now answers the question

`consensus.blocking_report()` and a new panel:

```
dong thuan 74.95% / nguong 76.8%              con thieu 1.85 diem
[=========================================|==]
UNKNOWN   adaptive gate disabled; the fixed 76.8/70.0 bar applies

Vi sao chua vao lenh: consensus 74.95% is 1.85 points below the 76.8% bar;
OKAPI is the largest drag (+0.00 pts)
EXECUTE 18  ·  HOLD 24  ·  VETO 81  ·  SKIP 77
```

Per-agent contribution in points, the biggest drag flagged in the vote table, and
a regime badge. A still octopus with no explanation looks broken even when it is
working exactly as designed, and here that is the common case.

## The probe: one order through the real path

`live/probe_trade.py` sends exactly one order and reports which of seven steps it
reached:

```
[ok] 1. build proposal          BUY 2000e-6 BTCUSDT @ 64,125.00
[ok] 2. synthetic quorum        six analysts at 0.95 (declared override, gate untouched)
[ok] 3. consensus gate          consensus 95.00% >= 76.8%; VESKA authorised
[ok] 4. single-use AuthToken    score 95.00% cleared the bar
[ok] 5. router init             TESTNET -> https://testnet.binancefuture.com
[ok] 6. entry order             FILLED @ 64,125.00
[ok] 7. exchange-side stop      stop id 777 @ 63,163.13
```

It deliberately does **not** take the shortcut of signing a payload and posting
it — `run_binance_testnet.py` already had one of those, and it tests the HTTP
call and nothing else. This walks the consensus gate, the token, the kill switch
and the bracket stop, because those are the parts whose failure would be
expensive. A refused vote stops the probe and sends nothing.

## Latency

Bottleneck 2 was 10-second REST polling. `live/ws_market_feed.py` exists and
carries `aggTrade` / `depth20`. It is worth wiring, but it is a slippage
improvement, not an edge: a faster feed makes a −8.98 bps strategy into a
slightly-less-negative one. Latency work pays off after the sign flips.

## Verification

```
python -m pytest tests/ -q                      835 passed, 3 skipped
python live/probe_trade.py --selftest                9/9
python core/consensus_engine.py --selftest          19/19
python -m pytest tests/test_no_lookahead.py -v      12/12   (still holds with the new signals)
```
