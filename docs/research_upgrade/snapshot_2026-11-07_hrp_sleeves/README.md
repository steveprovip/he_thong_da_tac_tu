# HRP between TIDAL-60 maker and XV funding carry: claims re-derived (2026-11-07)

Command (offline; reads `data/klines_1m` and `data/funding_cache`, writes only `--json`):

    python -m research.hrp_alpha_sleeves --json docs/research_upgrade/snapshot_2026-11-07_hrp_sleeves/hrp_sleeves.json

Files:
- `factory_maker_agents.json`: the 8 agent configs and survivor weights of the factory maker run, copied from
  `F:\factory-quant-opt\data\autoresearch_trade_gpt6_report.json` (SHA-256 `1c704a5b…461f`).
- `hrp_sleeves.json`: the full output (reproductions, daily-sleeve statistics, HRP weights, limits).

## Claims and what re-derivation shows

"Reproduced" means the same code and data give the same arithmetic. It does not make the figure evidence of an edge.

| Claim | Re-derived on this checkout | Status | Caveat |
|---|---|---|---|
| TIDAL-60 maker +8.33 bps net on 316 holdout trades | 316 trades, +8.3347 bps, Sharpe 1.728, 1.58 trades/day, 2026-03-15..09-30 | Reproduced | Picked after 1,406 trials (OOS DSR 0.0027). Train: 3,849 trades at -6.31 bps. The config has coef -0.15 and a 44-bar horizon, so it is a contrarian rule despite the "60" in its name. |
| XV funding carry +14.6 bps net on 153 holdout trades | 153 rounds, +14.59 bps per 28-day round, CI95 by day +9.17..+19.92, 1.90 %/yr on notional (exits up to 2026-10-09, the original data end) | Reproduced | The unit is per 28-day round, not per trade-hour. The refreshed cache gives 165 rounds at +16.01. The Oct-Nov segment has 1 round. |
| -1.51 bps/day portfolio drag | -1.5148 bps/day, Sharpe -1.555, 200 days | Reproduced | This is the 8-agent survivor-weighted meta-portfolio, not a TIDAL-60 + XV mix. Per agent: LUMEN-240 -1.897, TIDAL-240 -0.583, NORO-60 +0.601, NORO-15 +0.310 (2 trades), TIDAL-60 +0.096, the others about 0. |

## HRP result

The covariance was estimated only on 2025-01-01..2026-03-14 (438 days, correlation -0.08). With two sleeves, HRP is
inverse-variance weighting. Weights: TIDAL-60 0.05 %, XV 99.95 %. The monthly expanding re-estimate gives the same
split to within 1e-4.

| 200-day evaluation (2026-03-15..09-30) | bps/day | Sharpe (ann.) | max DD bps |
|---|---:|---:|---:|
| TIDAL-60 only | +3.292 | 1.73 | 193.8 |
| XV only | +0.609 | 5.06 | 8.6 |
| 50/50 | +1.951 | 2.04 | 88.4 |
| HRP static | +0.611 | 5.07 | 8.6 |
| HRP monthly walk-forward | +0.611 | 5.07 | 8.6 |
| 8-agent meta-portfolio | -1.515 | -1.55 | 339.4 |

Before the evaluation window, the TIDAL-60 sleeve lost 16.38 bps/day (Sharpe -2.55, max DD 7,287 bps).

## Reading

HRP does not repair the -1.51 bps/day portfolio. It replaces it with almost pure XV carry. +0.61 bps/day is about
2.2 %/yr on capital at leverage 2 per venue, below the risk-free yield noted in the 2026-10-10f report. Leg
liquidation, ADL, Hyperliquid bridge risk and real fills are not modelled. Both evaluation windows had already been
inspected, so a positive mix here is selection, not evidence. No weight, size, profile or tunable was changed.
