# Micro-capital mode (5 USDT wallet) - deployment guide

Status 2026-11-09: **code and unit tests only.** Nothing here was run against an exchange, no key was used, `live` was not armed.
Design and arithmetic: [harness/plan_micro_capital.md](harness/plan_micro_capital.md) (sizing, guards) and
[harness/plan_micro_rotation.md](harness/plan_micro_rotation.md) (adaptive TTL, hybrid IOC, 4-coin rotation, compounding). Not financial advice; a 5 USDT account is a
test of the plumbing, and nothing in this repo shows the strategy has a positive net edge.

## 1. What the mode does
It switches on by itself while the known wallet is **<= 15 USDT** (`MICRO_CAPITAL_MODE=auto`, the default). An unknown wallet keeps the normal rules.

| rule | value | where |
|---|---|---|
| entry notional | smallest lot with notional >= max(5.05, 1.02 x venue minimum) = 5.10 U on DOGE / SOL, and <= 6.0 U; no such lot -> no trade | `MicroCapitalSizer.qty`, `Engine._execute` |
| loss at the stop incl. fees | <= 0.05 U (about 0.012-0.017 U at SL 0.15-0.20 % inside the window) | `Engine.micro_check` |
| tradable symbols | `DOGEUSDT`, `XRPUSDT`, `ADAUSDT`, `1000PEPEUSDT` (`MICRO_CAPITAL_SYMBOLS`); BTC, SOL, ETH ... observe (signals, regime, no order) | `Engine.observer` |
| open positions | 1 in total; the router gives it to the coin with the best KER x abs(OFI L10), re-ranked every 5 s | `Engine.slot_block`, `micro_scores`, `better_peer` |
| size as the bankroll grows | largest lot <= min(0.85, 0.20) x equity x leverage (E = 10 U -> 10 U), never under the venue floor, never over the 0.05 U loss ceiling (DOGE's 0.50 % SL floor: 8.6 U; a 0.20 % stop: 17.9 U) | `MicroCapitalSizer.qty` |
| resting time of the maker entry | 3 s -> 10 s (up to 15 s) while VPIN < 0.65 and the markouts are clean; cut at once on an adverse 3 bps, a touch beyond the 0.15 % peg cap, dirty VPIN; at most 2 re-pegs | `Engine.micro_keep` |
| taker (IOC) entry | opt-in (`MICRO_IOC=on`): RAW consensus >= 0.80, OFI with the trade >= 0.80, TP >= 0.25 % AND fee-true net RR with a taker entry >= 1.30 (TP >= ~0.43-0.50 %), tape guard QUOTE; else Post-Only GTX | `Engine.micro_ioc_route` |
| margin guard | one lot of the window may use up to 6.0 U / 5x = 1.2 U (20 % of 5 U was 1.0 U and refused every lot) | `Engine.portfolio_check` |
| daily loss kill | 0.15 U | `Engine.daily_kill_eff` |
| 3 AUTO losses in a row | no entry for 24 h from the last loss; derived from the ledger, so a restart does not erase it | `MicroCapitalSizer.halt_until` |

Sizing is price-driven. DOGE at 0.0711 U needs **72-84 DOGE** (25-30 DOGE is 1.8-2.1 U and Binance rejects it with -4164). The size
replaces the Kelly / conviction / boost chain, which is built for hundreds of USDT. Kelly NO EDGE gate, shadow path, raw-quorum gate,
RUNE veto, AuthToken and the 0.768 quorum are unchanged and still apply.

Rotation is a DEMO feature: the live profile runs one engine by design and was not changed. Signal thresholds for XRP, ADA and
1000PEPE use the default 0.1 USD scale and KER gates; nothing says they fit those books, and `FALLBACK_SPECS` for ADA / 1000PEPE are offline
fallbacks (the exchangeInfo overrides them - check it). The 4H trend lane is parked while the mode is forced on (its size is 10 % of the
wallet) and refuses entries on any micro account.

Why BTC and SOL are out: BTC's minimum notional is 100 U (20 U of margin at 5x). SOL's lot on the engine's spec (0.01) allows exactly one lot in the window
(0.06 SOL = 5.16 U); if the real lot is 0.1 SOL it is infeasible and the sizer refuses with the reason. Locking SOL out is a policy choice.

## 2. Settings
| variable (`.env.local` or the shell) | meaning |
|---|---|
| `MICRO_CAPITAL_MODE` | `auto` (default), `on` (force, also on a big wallet - for a rehearsal), `off` |
| `MICRO_CAPITAL_EQUITY_MAX` | USDT threshold of `auto`, default 15, accepted range (0, 1000] |
| `MICRO_CAPITAL_BANKROLL` | equity a FORCED run on a wallet above the threshold pretends to have (default 5): `run.bat micro` rehearses a 5 U account, set 10 to rehearse the compounded size |
| `MICRO_CAPITAL_SYMBOLS` | comma list of the tradable universe, default `DOGEUSDT,XRPUSDT,ADAUSDT,1000PEPEUSDT` |
| `MICRO_IOC` | `off` (default), `shadow` (records `SHADOW_IOC`, sends nothing), `on`. `on` is an invariant I5 deviation: demo prints an INVARIANT WARN, an armed live engine refuses to start |

A typo raises at start-up instead of silently leaving the guard off. The per-rule numbers are `Profile.micro_*` fields in `core/engine.py`.
Invariant I6 (`LIVE_EQUITY_CAP` 3.85) caps the margin of ONE position, not the wallet; micro positions use 1.0-1.2 U, so it is **not** changed.

## 3. Rehearse on the DEMO account first (virtual money)
One click, from `F:\he_thong_da_tac_tu_2`:

```bat
run.bat micro
```

or run `run.bat` and pick **[4] DEMO MICRO**. The launcher sets `MICRO_CAPITAL_MODE=on` (the demo wallet is thousands of U, so the mode has to be forced),
frees port 8765 (`scripts\TAT_GIAO_DIEN.bat`), opens `http://127.0.0.1:8765` and runs
`python -m core.engine --mode demo` in scanner mode (BTC, SOL observe; DOGE, XRP, ADA, 1000PEPE rotate), with `MICRO_CAPITAL_MODE=on` and
`MICRO_IOC=on` set for that process only. The mode is hard-wired to `--mode demo`: this entry can never arm live.
Equivalent manual command (PowerShell):

```powershell
$env:MICRO_CAPITAL_MODE = 'on'; $env:MICRO_IOC = 'on'; python -m core.engine --mode demo
```

Expect every order to be a 5.1-6.0 U order on one of the four micro coins (Post-Only GTX, or a bounded IOC on a qualifying burst), BTC / SOL
never ordering, at most one position, `quant.micro_capital.active = true` with the 5 s `scores`.

Check before going further (all from the event log and `data/engine_orders.jsonl`):
1. each entry `qty_req x price` is inside [5.05, 6.0];
2. refusals read `OKAPI refused ... MICRO:` (lot / window / loss) or `RUNE portfolio refused` only when expected;
3. the gate sentences `KELLY NO EDGE ...`, `RAW QUORUM ...` still show up where the evidence says so - micro mode does not bypass them;
4. after 3 AUTO losses the log says `MICRO HALT: 3 losses in a row`; restart the process and confirm the halt is still there;
5. compare, in `data/engine_orders.jsonl`, fill rate and 5 s markout of orders with `micro_ttl_s` set against the 3 s ones, the `ttl_cut` reasons,
   and the IOC rows (`route: ioc`, `micro_ioc`) against their GTX alternatives: none of this was measured when it was written.

## 4. Live (owner action - not done here)
Preconditions you must create yourself: a funded USDT-M futures wallet of ~5 U, an API key with **trade permission only** (no withdrawals,
IP-restricted), the key in `.env.local` as `BINANCE_MAINNET_API_KEY` / `BINANCE_MAINNET_SECRET_KEY` (never in chat or git),
one-way mode. The live profile is DOGEUSDT, `max_loss_usdt` 0.05, `equity_cap` 3.85 (margin), kill 0.15 U/day, post-only GTX entries.

Start only through the launcher, which asks you to type `LIVE`: `run.bat live`. Do not pass `--arm-live` from a script.
Watch the first fills by hand: (a) the DOGE lot the exchange accepts, (b) the realised fee on a ~5 U round trip (about 0.002-0.005 U),
(c) that the exchange stop is on the book (`stop_covers_entry`, `protection`).

## 5. Known limits
- Fees do not shrink with the account: a round trip costs 4 bps (maker in + maker out) to 8 bps (stopped out: maker in, taker stop, slip), about 0.002-0.004 U on 5.1 U. The edge per trade must exceed that; nothing in the ledger shows that it does.
- The venue can change `MIN_NOTIONAL` or `LOT_SIZE`; the engine reads exchangeInfo, the sizer follows it, and refuses when no lot fits.
- 24 h halt + 0.15 U daily kill mean a bad start stops the account for a day by design; it is also the only protection against a streak of ~0.015 U losses piling up unnoticed.
- The "recent live SOL short (+0.41 % ROE, +0.6321 U)" is not in `data/engine_trades.jsonl` (110 rows, all demo, last 11-08 14:36). Its numbers imply ~9 SOL = ~771 U of notional, i.e. the 5 000 U demo account. Neither the +0.12 % soft BE nor the EJECT hold acts at a +0.082 % move.

## 6. Rollback
`MICRO_CAPITAL_MODE=off` (no code change) restores the previous sizing and guards - including the margin guard that refuses a 5 U wallet's entries.
Code: `git revert` the micro-capital commit.
