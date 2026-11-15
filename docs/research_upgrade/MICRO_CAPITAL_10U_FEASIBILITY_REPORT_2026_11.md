# MCF-1 — which non-CEX path can a 10 USD base use? Prop challenges, Solana DLMM, airdrops, local-AI income (report, 2026-11-15)

Code: `research/micro_capital_frontiers.py` (models, Monte Carlo, public snapshot fetch, decision matrix),
`tests/test_micro_capital_frontiers.py` (18 tests). Result files (git-ignored): `data/micro_capital/dlmm_snapshot.json`
(150 Meteora DLMM pools, 117 with 30 daily closes), `data/micro_capital/micro_capital_frontiers.json`.
Offline models and public read-only snapshots. No account, no order, no deposit, no live start, nothing wired into the
trading engine, no git operation. Not investment, legal or tax advice: venue eligibility from Vietnam and tax treatment
are not verified here.

## 0. Answer

**The target.** 1 USD per hour is 24 USD a day, 720 USD a month. From 10 USD that is a return of 240 % a day. No
capital-based path can produce it from 10 USD; only hours of work can. For scale: Vietnam's 2026 Region I minimum wage
is 25 500 VND per *worked* hour ([Decree 293/2025/ND-CP](https://www.bakermckenzie.com/en/insight/publications/2026/03/vietnam-change-to-the-regional-minimum-wage-in-2026)),
1.00 USD at the reference rate of 25 600 VND/USD ([SBV, 2026-11-10](https://en.vietnamplus.vn/reference-exchange-rate-down-10-vnd-on-november-10-post355117.vnp)).
The 720 USD-a-month target is about 3.5 times the Region I monthly minimum (5.31 M VND, about 207 USD).

**Ranking (equal weights; the same order under the "capital safety" and "velocity" weightings):**

| Rank | Frontier | Total (1-100) | Why |
|---|---|---|---|
| **1** | **F4b AI-assisted micro-services from the local stack** | **65.8** | No capital at risk; first order in 2.5-5 weeks, cash about 2 weeks later; central funnel 3.3 USD per owner hour; it is the only path that can reach 720 USD a month (about 92 owner hours a month at a reviewed seller's 7.85 USD/h) |
| 2 | F4a AI VTuber streaming (AI Linh) | 39.3 | No capital at risk, but 0 donation events in AI Linh's logs; 625 days to Twitch's 50 USD payout threshold at the median channel |
| 3 | F2 Solana DLMM LP | 37.7 | Rent locks 43 % of 10 USD; the median top pool nets about 0.013 USD a day on the rest |
| 4 | F3 airdrop farming | 26.8 | Central EV +0.80 USD over 6 months with a 69 % chance of nothing; scripted "organic" farming is what sybil filters exclude |
| 5 | F1* prop challenge with the activation paid (65 USD, outside the budget) | 19.0 | Positive model EV over a year (+31 USD at Sharpe -0.5) but 81 % of buyers get nothing after a 20 % payout haircut |
| 6 | F1 prop challenge on a strict 10 USD | 6.9 | The only verified sub-10 USD offer (FTUK Flex, 9 USD) needs 56 USD more on passing: every 10 USD path ends with the fee lost |

**Winner: F4b.** It is labour income. The 10 USD stays in the pocket or buys about 66 Upwork Connects. The
implementation plan is section 7. **No frontier reaches 1 USD per calendar hour within 90 days on a 10 USD base**:
the best model value in that window is 0.08 USD/h (F4b central), 0.46 USD/h for a reviewed seller.

## 1. Decision matrix

Scores (1-100). Each score is a fixed function of a modelled input, listed with its source in the input table:
- **Ruin:** 100 x (1 - P(lose the whole 10 USD)).
- **Time to first dollar:** 100 x exp(-days / 30).
- **Velocity:** log scale; 0.001 USD/h = 1, 1 USD/h (the target) = 100.
- **Friction:** 100 x exp(-engineering hours / 80), minus 15 per external gate (KYC, follower threshold, sybil filter).
- **Scalability:** 25 x log10 of the monthly net a 90th-percentile outcome reaches; 100 USD = 50, 10 000 USD = 100.

| Frontier | Ruin | First dollar | Velocity | Friction | Scalability | Equal | Capital safety | Velocity-first |
|---|---|---|---|---|---|---|---|---|
| F1 strict 10 USD (FTUK Flex) | 1.0 | 1.0 | 1.0 | 30.7 | 1.0 | 6.9 | 4.0 | 4.0 |
| F1* activation paid (65 USD) | 19.2 | 13.4 | 1.0 | 30.7 | 30.6 | 19.0 | 18.1 | 14.2 |
| F2 Solana DLMM LP | 99.1 | 7.9 | 1.0 | 68.7 | 11.8 | 37.7 | 45.3 | 21.1 |
| F3 airdrop farming | 31.4 | 1.0 | 1.0 | 62.9 | 37.6 | 26.8 | 25.2 | 17.6 |
| F4a VTuber streaming | 100.0 | 1.0 | 26.9 | 43.2 | 25.4 | 39.3 | 49.9 | 30.4 |
| **F4b micro-services** | **99.5** | **35.0** | **63.6** | **67.9** | **63.0** | **65.8** | **72.2** | **61.8** |

Weights: equal 0.2 each; capital safety = ruin 0.35, time 0.15, velocity 0.2, friction 0.1, scalability 0.2;
velocity-first = ruin 0.1, time 0.2, velocity 0.4, friction 0.1, scalability 0.2.

| Input (model output unless marked) | F1 strict | F1* | F2 | F3 | F4a | F4b |
|---|---|---|---|---|---|---|
| P(lose the whole 10 USD) | 1.000 | 0.808 | 0.009 | 0.686 | 0 (no spend) | 0.005 (no order in 13 weeks) |
| Days to first dollar | never | 60 | 76 | 182 (assumed TGE) | 625 | 31.5 |
| Net USD per calendar hour, 90 days | -0.0042 | +0.0009 | +0.0006 | +0.0002 | +0.0064 | +0.081 |
| Engineering hours / external gates (judgement) | 40 / 2 | 40 / 2 | 30 / 0 | 20 / 1 | 25 / 2 | 15 / 1 |
| Monthly net of a P90 outcome, USD | 0 | 16.8 | 3.0 | 31.9 | 10.4 | 331.5 |

**The lead is robust.** F4b stays first under every weighting in each of these cases:
- new-seller funnel (2 % win rate, 30 USD orders): 58.5 / 64.7 / 52.3;
- friction raised to 40 hours and 2 gates: 58.4 / 68.5 / 58.1;
- both: 51.1 / 60.9 / 48.6;
- both, with its P90 cut to the new-seller monthly net: 45.2 / 55.1 / 42.7.
Second place is F4a at 39.3 / 49.9 / 30.4.

## 2. F1 — prop-firm micro-challenges

**Offers (third-party listings; firms change terms, check before paying):**
- **FTUK Flex 5k** ([FXEmpire](https://fxempire.com/prop-firms/ftuk), [Propvator](https://propvator.com/prop-firms/ftuk/offers/), [TradeLocker hub](https://tradelocker.com/hub/prop-firms/ftuk/5k-1-step-1225)):
  - 9 USD entry, plus a 56 USD activation fee due after passing.
  - 4 % target; 6 % trailing floor that locks at the initial balance; 5 % daily limit.
  - Consistency rules: the largest day may be 20-40 % of total profit ([PropFirmMap](https://propfirmmap.com/firms/ftuk)).
  - EAs are allowed, except tick scalping, martingale, grid, HFT and latency or hedge arbitrage ([FTUK FAQ as quoted on Trustpilot](https://www.trustpilot.com/review/ftuk.com?page=8), [Propvator](https://propvator.com/prop-firms/ftuk/)). FTUK Futures bans EAs.
- **FTUK two-step:** 10 % static floor in phase 1 and 5 % in phase 2, 5 % daily ([TradingFinder](https://tradingfinder.com/props/ftuk/rules)).
- **RebelsFunding Silver (2-step):** targets 8 % / 5 %, 5 % daily, first payout after 14 days, split 75-90 %. The cheapest 5k challenge is about 20 USD (unverified) ([PropFirmMap](https://propfirmmap.com/vs/fundingpips-vs-rebelsfunding), [PropFirmMap summary](https://propfirmmap.com/firms/11003/summary), [TheTrustedProp](https://thetrustedprop.com/prop-firms/rebels-funding/challenges)).
- **The5ers Bootcamp:** 3 steps of 6 %, 4-5 % floor, split from 50 %; no reliable price found ([TradingFinder](https://tradingfinder.com/props/the-5ers/rules)).

**Industry outcomes:**
- **FPFX Tech** (Sept 2024; 300 000+ accounts, 100 000 traders, 10 firms): 7 % of traders reached a payout, about 45 % of funded traders were paid, and the typical payout was about 4 % of plan size ([Finance Magnates](https://www.financemagnates.com/forex/exclusive-only-7-of-300000-prop-trading-accounts-achieved-payouts/)).
- **ATFunded** (June 2025): 22.6 % passed phase 1, 26.9 % of those were funded, about 6 % overall ([Finance Magnates](https://www.financemagnates.com/forex/prop-firm-atfunded-reports-6-of-traders-reached-funded-status-in-june-trading-data/)).
- **The Funded Trader:** 5-10 % pass, 20 % of passers reach a payout ([Finance Magnates](https://www.financemagnates.com/forex/prop-firm-the-funded-trader-paid-traders-386k-in-march-could-have-earned-3x-more/)).
- **Firm failures:** an estimated 80-100 firms closed between February 2024 and the end of 2025 ([closure list](https://thepropfirmguide.com/prop-firms-that-shut-down/)). True Forex Funds closed in May 2024 owing about 1.2 M USD to about 300 traders ([report](https://fundedtrading.com/true-forex-funds-shutdown/)).

**Payoff formulas (driftless P&L, continuous path):**
- **Static floor:** P(pass) = buffer / (target + buffer).
- **Trailing floor:** P(pass) = exp(-target / drawdown).
- **Funded account:** E[withdrawals] <= loss buffer L, by optional stopping. Every account that dies ends below its floor by the overshoot of the last step (Siegmund: 0.5826 x step sd). Simulated: dead accounts end at -(L + 0.5826 sd) exactly.
- **Challenge:** EV = P(pass) x (split x E[withdrawals] - activation) - entry.
- **FTUK Flex at zero edge:** P(pass) <= e^(-4/6) = 0.513; trader payout <= 0.8 x 6 % x 5 000 = 240 USD; EV <= +85.5 USD per attempt.

**Monte Carlo (20 000 paths per cell, seed 20261115):**
- Daily P&L with Student-t (df 4) tails and 8 draws per trading day.
- Daily volatility 1 % of the account; annual Sharpe as stated.
- Evaluation up to 250 trading days; funded account 250 trading days.
- Payout every 10 trading days if the trader share is >= 50 USD; the balance then resets with a fresh buffer. This is an optimistic assumption for FTUK's funded stage.

| Program / strategy | P(pass) | P(any payout) | E[payout] / yr | Fees (expected) | EV / yr | EV in 90 days | First payout (median days) |
|---|---|---|---|---|---|---|---|
| FTUK Flex 5k, Sharpe -1.5 | 0.321 | 0.127 | 26.75 | 26.95 | -0.20 | -6.10 | 43 |
| FTUK Flex 5k, Sharpe -0.5 | 0.451 | 0.240 | 65.75 | 34.25 | +31.49 | +10.91 | 46 |
| FTUK Flex 5k, zero edge | 0.522 | 0.306 | 99.57 | 38.24 | +61.33 | +24.76 | 46 |
| FTUK Flex 5k, Sharpe 1.0 | 0.664 | 0.473 | 229.94 | 46.16 | +183.78 | +71.51 | 45 |
| RF Silver 5k, zero edge | 0.351 | 0.266 | 127.82 | 20.00 | +107.82 | -14.49 | 181 |
| RF Silver 5k, Sharpe -0.5 | 0.216 | 0.146 | 54.78 | 20.00 | +34.78 | -17.02 | 178 |
| 10 USD micro tier (hypothetical 1k, 4-step), zero edge | 0.182 | 0.138 | 13.16 | 10.00 | +3.16 | -9.96 | 267 |

**Reading.**
- **Why the model EV is positive at zero edge.** Under the published rules, a strategy without edge has a positive model EV because the trader's loss is capped at the fee: the challenge is an option. The firms' margin comes from three places:
  - traders who do worse than zero: the industry's 7 % payout rate sits below the model's Sharpe -1.5 cell (12.7 %);
  - terms the model leaves out: consistency caps, payout discretion, funded floors that do not reset;
  - firm failure.
- **The directive's bot is not plausible.** A bot that makes 0.5 % a day (25 USD on 5k) needs an annual Sharpe of 15.9 at 0.5 % daily volatility, 7.9 at 1 %, and 4.0 at 2 %. At 2 % the 5 % daily limit is 2.5 sd away. No strategy tested in this repository has a positive Sharpe after costs.
- **The strict 10 USD budget.** FTUK Flex is the only verified offer under 10 USD, and passing it costs 56 USD more. On 10 USD the path ends at the fee (F1 row).
- **With 65 USD (F1* row: Sharpe -0.5 for gold costs without an edge, 20 % of payouts lost to denial or closure):** about 19 % of buyers are paid anything, and the 90-day value is 0.0009 USD/h.

## 3. F2 — Solana DLMM micro-LP

**Costs at 10 USD** ([Meteora rent FAQ](https://docs.meteora.ag/user-faq/getting-started-lping/sol-required-for-rent), [DLMM FAQ](https://docs.meteora.ag/faq/dlmm-faq)):
- A position locks about 0.057-0.059 SOL of refundable rent.
- A range that touches an uninitialised bin array burns 0.075 SOL, non-refundable.
- At the snapshot's SOL price of 72.61 USD: 4.28 USD is locked and 5.68 USD can be deployed (existing bin array). With a new bin array, 5.48 USD is lost and 0.24 USD remains.

**Loss-versus-rebalancing** (Milionis, Moallemi, Roughgarden, Zhang 2022, arXiv 2208.06046; v3 value function V = L (2 sqrt p - sqrt pa - p / sqrt pb)):
- **Full range:** LVR per day = sigma_d^2 / 8 of the position.
- **Symmetric range [p / k, p k]:** sigma_d^2 / (8 (1 - k^-1/2)). At +-10 % this is 2.69 sigma_d^2, 21.5 times the full range. The test pins it to the numerical curvature of V.
- **Break-even daily volatility for a full-range position:** sqrt(8 x fee yield).

**Snapshot, 2026-11-15:**
- Sources: top 150 pools by 24 h volume from Meteora's data API (140 962 pools listed), and 31 daily closes per pool from GeckoTerminal in quote-token units. 117 pools have at least 21 closes; younger pools are left out.
- Fee yield (24 h fees / TVL): median 0.31 % a day, P90 2.37 %. Daily volatility: median 3.9 %, P90 16.9 %.
- 30.8 % of pools trade more than 5x their TVL a day.
- Fees exceed **full-range** LVR in 91 % of pools (median net +0.23 % a day; P90 +1.74 %), but **+-10 %-range** LVR in only 40 % (median net -0.13 % a day).
- Pool-level fee/TVL against sigma^2/8 is an *upper bound* on LP profitability, for three reasons:
  - DLMM liquidity is concentrated, so real LVR per unit of TVL is a multiple of sigma^2/8;
  - daily closes understate the variance arbitrageurs trade on;
  - the fee figure is one day, taken during a 10 % SOL decline that raised volume.
- Survivorship: a pool that rugged no longer ranks by volume, so the 0.9 % share with a 30-day drawdown above 90 % (15.4 % above 50 %) understates the tail.
- One "BTC-SOL" pool with 59.7 % daily volatility and a 93 % drawdown is a token named BTC, not bitcoin.
- External evidence: 49.5 % of Uniswap v3 LPs had negative returns in May-September 2021, with 260.1 M USD of impermanent loss against 199 M USD of fees ([Loesch et al. 2021](https://arxiv.org/pdf/2111.09192); commissioned by Bancor, a competitor, as [Nasdaq notes](https://www.nasdaq.com/articles/half-of-uniswap-liquidity-providers-are-losing-money)).

**10 USD economics:**
- At the median pool: 5.68 USD x 0.23 % = 0.013 USD a day (0.0006 USD/h); the first net dollar takes about 76 days.
- At the P90 pool: 0.10 USD a day.
- The whole 10 USD is hard to lose only because 43 % of it is refundable rent; the deployed 5.68 USD carries the token risk.
- Not a $10 lane, but worth a separate study at larger size: tokenized-equity pools (AVGOx, TTWO, MCDx) showed 3.4-4.1 % daily fee yield at 2-3.6 % daily volatility in this one-day snapshot. That needs a fee time series, not one day.

## 4. F3 — airdrop farming with 10 USD of gas

**Constraint.** The directive's "simulate organic user behaviour" is a design to defeat sybil filters. The programs exclude such wallets:
- LayerZero filtered about 803 000 wallets, 38-59 % of its snapshot depending on the source ([Cointelegraph](https://cointelegraph.com/news/layerzero-concludes-sybil-self-reporting-phase), [The Defiant](https://thedefiant.io/layerzero-tells-sybil-farmers-to-out-themselves-or-face-airdrop-exclusion)).
- zkSync's sybil handling was disputed ([ForkLog](https://forklog.com/en/zksync-airdrop-sparks-criticism-over-sybil-accounts/)).

No such script was designed here. The economics below apply to transparent use of one wallet.

**Model:**
- 10 USD at 0.003 USD per transaction = 3 333 transactions.
- EV = programs x P(pay in window) x P(10 USD wallet qualifies) x P(clears the filter) x value - gas.
- No data set gives these probabilities for a 10 USD wallet in 2026. The scenarios are assumptions; the historical upper anchor is Arbitrum 2023, with 625 143 eligible wallets at 625-10 250 ARB each ([NFTNow](https://nftnow.com/news/the-arbitrum-airdrop-is-coming-tomorrow-heres-what-to-know/)).

| Scenario (4 programs) | P(program pays) | E[value] | EV | P(nothing) |
|---|---|---|---|---|
| Pessimistic (0.3 x 0.1 x 0.4, 5 USD) | 0.012 | 0.24 | -9.76 | 0.953 |
| Central (0.5 x 0.3 x 0.6, 30 USD) | 0.090 | 10.80 | +0.80 | 0.686 |
| Optimistic (0.7 x 0.6 x 0.8, 150 USD) | 0.336 | 201.60 | +191.60 | 0.194 |

Time to the first dollar is the next distribution date: unknown, months. The value is a lottery whose parameters
cannot be measured before the program publishes its rules.

## 5. F4 — local AI

**Assets already on this machine:**
- **AI Linh** (`E:\AI_Linh_vtuber\...\AILinh-VTuber-DLinh-V1`): orchestrator, TikTok and Gank connectors, donation service, Telegram and Discord services, and faster-whisper large-v3-turbo with VAD (about 1.6 GB of VRAM per its config). 2 047 tests passed on 2026-10-08.
- **This repository:** resumable public-REST fetchers with pacing and 429 back-off (this study's and ODH-1's), and caption and edit workflows (BashCut skills).

### F4a — streaming
- **Platform gates:**
  - TikTok LIVE: 1 000 followers to go live, 18+ to receive gifts ([SocialBu](https://socialbu.com/blog/how-many-followers-on-tiktok-to-go-live), [TT Calculator](https://ttcalculator.net/learn/tiktok-live-requirements/)).
  - YouTube fan funding: 500 subscribers, 3 uploads in 90 days, and either 3 000 watch hours or 3 M Shorts views; Super Chat pays 70/30 ([YouTube help](https://support.google.com/youtube/answer/13429240?hl=en-GB), [AIR](https://air.io/en/monetization/youtube-partner-program-requirements-2026-the-complete-guide)).
  - Twitch Affiliate: reportedly 25 followers / 4 hours / 4 days / 3 average viewers since June 2026 (earlier 50 / 500 min / 7 days / 3). Payout threshold 50 USD; subs commonly 50/50 ([streamer.guide](https://streamer.guide/guides/twitch-affiliate-requirements)).
- **Audience distribution:** 61 % of channels average fewer than 10 viewers ([Stream Hatchet](https://streamhatchet.com/?p=18982)). About 95 % average fewer than 5 ([ExpertBeacon](https://expertbeacon.com/how-many-viewers-do-the-top-1-of-twitch-streamers-have/), methodology unverified).
- **AI Linh's own record:**
  - `logs/donations.log` holds 42 lines, all start-ups, and 0 donation events.
  - `logs/tiktok.log` (last written 2026-11-08) shows 11 crashes of `services_v2/tiktok_connector.py`: NameError `os` and TypeError unhashable list.
  - Its progress notes say there has been no live, payment or audio-quality verification, and list open P0s (producer authentication, durable money-event inbox).
- **Funnel:** average concurrent viewers lognormal with median 1 and P(>= 5) 8 % (mean 1.93, P(>= 10) 2.2 %), 4 hours a day, 0.02 USD per viewer-hour (assumed; 0.005-0.08 range):
  - expected 0.15 USD a day, median 0.08 USD a day;
  - 625 days to Twitch's 50 USD threshold at the median (156-2 500 days across the rate range).

### F4b — AI-assisted micro-services (the winner)

**Platform costs:**
- Upwork's freelancer fee has been variable at 0-15 % since May 2025; Connects cost 0.15 USD, and 10 are free each month ([Upwork](https://www.upwork.com/resources/is-upwork-free), [goLance](https://golance.com/blogs/upwork-fees-explained-2026)).
- Fiverr keeps 20 % of the seller's price ([fee survey](https://www.jobbers.io/the-freelance-platform-fee-encyclopedia-every-fee-on-every-platform-documented/)).
- Clearance before withdrawal is assumed at 14 days.

| Funnel (13 weeks) | Proposals | Win rate | Order | Orders | Net USD | Owner hours | USD / owner hour | USD / calendar hour | Weeks to first order |
|---|---|---|---|---|---|---|---|---|---|
| New seller | 130 | 2 % | 30 | 2.6 | 66 | 43 | 1.55 | 0.030 | 5.0 |
| Central | 130 | 4 % | 40 | 5.2 | 177 | 53 | 3.32 | 0.081 | 2.5 |
| Reviewed seller | 195 | 10 % | 60 | 19.5 | 995 | 127 | 7.85 | 0.455 | 0.7 |

Win rates, order values and hours are assumptions, chosen at the low end for a new account. Reaching 720 USD a month
(1 USD per calendar hour) takes about 92 owner hours a month at the reviewed rate, or full time at the central rate.

## 6. What none of the frontiers can do

Turn 10 USD into 1 USD per calendar hour inside 90 days. The capital frontiers (F1-F3) scale with capital, and 10 USD
caps them at cents a day before costs. The labour frontier (F4b) scales with owner hours and reputation, not with
the 10 USD.

## 7. Winner implementation plan (F4b)

**Phase A (days 1-3): a service kit.** Put it in its own folder outside this trading repository, with tests and a
one-command demo per offer.
- **A1 `transcribe`:**
  - faster-whisper large-v3-turbo int8_float16 with VAD (the setting AI Linh already runs); input a file or folder, output SRT, VTT, TXT and DOCX; vi / en / auto.
  - Measure before selling: real-time factor and a manual word-error spot check on 3 Vietnamese and 3 English public-domain clips.
  - Price anchor 5-15 USD per 30 minutes of audio.
- **A2 `scraper`:**
  - A template built on the `Pacer` and `_get` patterns of `research/one_dollar_hour_study.py` and `research/micro_capital_frontiers.py`: pacing, retry, 429 back-off and resumable per-item files.
  - CSV / XLSX / Google Sheets export and a scheduler. Public pages only: respect robots.txt and site terms; no login walls, no personal data.
  - 20-60 USD per scraper.
- **A3 `bot`:**
  - A Telegram or Discord bot (alerts, FAQ, moderation) from AI Linh's `telegram_service.py` / `discord_voice_service.py` patterns, with an optional local LLM through Ollama.
  - Delivered as code plus a deploy guide; the client hosts it.
  - 30-100 USD.

**Phase B (days 3-7): listings.**
- Three demo artifacts (subtitles of a public-domain clip, a public-dataset scrape, a bot screen recording).
- Three Fiverr gigs with fixed scope and turnaround, and an Upwork profile (spend the 10 USD on Connects only for briefs that match A1-A3).
- Vietnamese channels for subtitle work (Facebook groups, local freelance boards).
- Payment rail: Payoneer to a Vietnamese bank (KYC).
- Rules: client files are processed locally and deleted on delivery; no off-platform payment for platform clients; voices only with a clear licence (AI Linh's own audit flags voice rights).

**Phase C (weeks 2-6): a funnel ledger.**
- One row per proposal / order: channel, reply, order, value, hours, review. Read it weekly.
- Gate: at least 1 order per 25 proposals by week 4, otherwise change the offer, price or channel.
- Drop any offer that pays below the Region I minimum wage (25 500 VND per owner hour).

**Phase D (weeks 6-13): pricing and repeat work.**
- After 3-5 reviews, raise prices 20-30 % and turn repeat requests into fixed packages (subtitle batches, maintained scrapers).
- Use AI Linh as a showcase of the voice stack, not as a revenue line.
- Model milestones: central 5 orders / 177 USD in 13 weeks; reviewed 19.5 orders / 995 USD. 720 USD a month is a reviewed seller working about 92 hours a month.

## 8. Limitations

- **Prop challenges:**
  - Rules come from third-party listings; FTUK's funded-stage split, payout cycle and minimum are assumed, and so is its post-payout buffer reset (optimistic).
  - Consistency caps and payout discretion are not modelled.
  - Gold costs enter only through the negative-Sharpe cells.
- **DLMM:** a one-day fee snapshot against 30 days of volatility; survivorship in the top-volume list; LVR from daily closes is a lower bound; JIT liquidity and MEV are not modelled.
- **Airdrops, streaming and services:** the parameters are assumptions with stated ranges; only the platform gates and fees are sourced.
- **Engineering hours and gates** are judgement inputs (the sensitivity in section 1 shows the ranking does not hinge on them).
- **Not verified:** the owner's eligibility on each venue from Vietnam, tax treatment, and FTUK's and RebelsFunding's own current terms (their sites did not resolve from the fetch service).

## 9. Reproduce

```
python -m research.micro_capital_frontiers dlmm   # public snapshot, about 16 min at GeckoTerminal's free-tier pace
python -m research.micro_capital_frontiers run    # 57 s -> data/micro_capital/micro_capital_frontiers.json
python -m pytest -q tests/test_micro_capital_frontiers.py   # 18 passed
```
