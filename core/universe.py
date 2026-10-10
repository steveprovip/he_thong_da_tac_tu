"""Point-in-time universe screen for the intraday 15m lane (research input; not wired into the engine).

The caller supplies listings (exchangeInfo) and DAILY bars it already fetched, so one function picks the universe
live (REST) and in replay (data.binance.vision as of each month). Only bars that CLOSED before `as_of_ms` count,
which keeps a replay free of look-ahead; feeding it delisted symbols' archives keeps it free of survivorship.

Rank = median daily quote volume over the lookback (a 24 h ticker is one spike away from a pump coin).
Median daily range is capped: on 10-10 the 24 h top-by-volume list held MAGIC / US / LUMIA at 53-77 % ranges,
where a 15m bracket is a gap bet, not a pull-back. Missing data is UNKNOWN -> excluded, never 0.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from statistics import median
from typing import Optional

DAY_MS = 86_400_000


@dataclass(frozen=True)
class Listing:
    symbol: str
    quote: str
    contract: str                 # 'PERPETUAL' | delivery types
    status: str                   # 'TRADING' | ...
    onboard_ms: Optional[int]
    underlying: Optional[str]     # 'COIN' | 'INDEX' | ... (None = UNKNOWN)


@dataclass(frozen=True)
class Bar:
    open_ms: int
    high: float
    low: float
    close: float
    quote_volume: float


def listings_from_exchange_info(info: dict) -> list:
    return [Listing(s['symbol'], s.get('quoteAsset'), s.get('contractType'), s.get('status'),
                    s.get('onboardDate'), s.get('underlyingType')) for s in info.get('symbols', ())]


def bars_from_klines(rows) -> list:
    """Binance kline rows [open_ms, o, h, l, c, v, close_ms, quote_volume, ...] -> Bar list."""
    return [Bar(int(r[0]), float(r[2]), float(r[3]), float(r[4]), float(r[7])) for r in rows]


def screen(listings, daily: dict, as_of_ms: int, *, top_n: int = 30, lookback_days: int = 14,
           min_median_qv: float = 50e6, min_range_bps: float = 150.0, max_range_bps: float = 1500.0,
           min_age_days: float = 90.0, quote: str = 'USDT'):
    """-> (picked, reasons). picked: [{'symbol', 'median_qv', 'median_range_bps'}] by median_qv desc (<= top_n);
    reasons: Counter of why the rest were dropped (same idea as data/carry-scan counters)."""
    why, ok = Counter(), []
    for li in listings:
        if li.quote != quote or li.contract != 'PERPETUAL' or li.status != 'TRADING':
            why['not_usdt_perp_trading'] += 1
            continue
        if li.underlying != 'COIN':
            why['not_coin'] += 1
            continue
        if li.onboard_ms is None or (as_of_ms - li.onboard_ms) / DAY_MS < min_age_days:
            why['young_or_unknown_age'] += 1
            continue
        bars = [b for b in daily.get(li.symbol, ()) if b.open_ms + DAY_MS <= as_of_ms][-lookback_days:]
        if len(bars) < lookback_days or any(b.close <= 0 for b in bars):
            why['short_history'] += 1
            continue
        qv = median(b.quote_volume for b in bars)
        rng = median((b.high - b.low) / b.close * 1e4 for b in bars)
        if qv < min_median_qv:
            why['volume'] += 1
        elif not min_range_bps <= rng <= max_range_bps:
            why['range'] += 1
        else:
            ok.append({'symbol': li.symbol, 'median_qv': qv, 'median_range_bps': rng})
    ok.sort(key=lambda r: -r['median_qv'])
    why['below_top_n'] += max(0, len(ok) - top_n)
    return ok[:top_n], why
