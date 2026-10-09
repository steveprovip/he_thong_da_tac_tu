"""Adaptive capital router between the micro scalp lane and the 4H/1D trend lane - 10-09 (cc).

Inputs per tick: the micro features of the tradable symbols (KER 120 s, CVD 60 s) and the trend lane state.
  CHOP    every tradable symbol KER < 0.25           -> scalp allocation 0 (micro lane observer), trend lane keeps
                                                        its own vol-targeted paper weights
  BURST   some symbol KER > 0.50 AND |CVD 60 s| > $1M x usd_scale (SOL/DOGE 0.1 -> $100k)
                                                     -> scalp allocation 1, trend lane trailing stop armed
  NORMAL  otherwise                                  -> scalp allocation 1
What it deliberately does NOT do:
  * raise leverage or size on any lane (request item "mở rộng đòn bẩy" refused: risk may only go down
    autonomously; the trend lane is paper and its OOS DSR is 0.19);
  * move money between wallets - "allocation" is a gate on new micro entries, the trend lane is paper.
Note on scales: KER here is a 2-minute micro measure; it says little about a 4 h trend.  The router therefore
only gates the micro lane; the trend lane's trailing stop is its own 4h ATR chandelier (core/trend_lane.py),
armed or not by an A/B-tested setting.  The engine already chop-locks entries below KER 0.38, so CHOP mostly
documents a state the engine was in anyway (the counter in telemetry shows how often it binds).
"""
from __future__ import annotations

from typing import Dict, Optional

CHOP_KER, BURST_KER, BURST_CVD_USD = 0.25, 0.50, 1_000_000.0


def route(features: Dict[str, dict], usd_scale: Optional[Dict[str, float]] = None) -> dict:
    kers = {s: f.get('ker') for s, f in features.items() if f}
    known = {s: k for s, k in kers.items() if k is not None}
    burst = []
    for s, f in features.items():
        k, c = (f or {}).get('ker'), (f or {}).get('cvd_60s')
        lim = BURST_CVD_USD * (usd_scale or {}).get(s, 1.0)
        if k is not None and c is not None and k > BURST_KER and abs(c) > lim:
            burst.append(s)
    if burst:
        return {'regime': 'BURST', 'scalp_alloc': 1.0, 'trend_trail': True, 'symbols': burst,
                'why': f'KER > {BURST_KER} and |CVD 60 s| > scaled $1M on {",".join(burst)}'}
    if known and len(known) == len(features) and all(k < CHOP_KER for k in known.values()):
        return {'regime': 'CHOP', 'scalp_alloc': 0.0, 'trend_trail': False, 'symbols': list(known),
                'why': f'every symbol KER < {CHOP_KER}: micro lane observes, trend lane unaffected'}
    return {'regime': 'NORMAL', 'scalp_alloc': 1.0, 'trend_trail': False, 'symbols': [], 'why': ''}
