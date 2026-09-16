"""Pure regime/trend classification for the DOGE flush shadow. No I/O, no network.

Every threshold here is a hypothesis frozen before testing (spec section 4), not a
measured result. The module exists so those hypotheses can be tested on fixtures
without standing up an engine or touching the exchange.

Bar dicts carry 'open'/'high'/'low'/'close'/'volume' and the caller is responsible
for passing CLOSED bars only: a function here cannot tell a closed bar from an
open one, so leakage has to be prevented upstream by closeTime.
"""
from statistics import median
from typing import Dict, List, Optional, Tuple

DOWN, UP, RANGE, TRANSITION = 'DOWN', 'UP', 'RANGE', 'TRANSITION'

ADX_TREND = 25.0          # section 4: trend needs ADX14 >= 25
ADX_RANGE = 20.0          # range needs ADX14 <= 20
SLOPE_TREND = 0.10        # |slope| gate for a directional regime
SLOPE_RANGE = 0.05        # |slope| ceiling for range
EMA_GAP_RANGE = 0.5       # |EMA20-EMA50| <= 0.5 * ATR15 for range
LOOKBACK = 20             # support/resistance and volume reference window


def true_range(bar: Dict, prev_close: float) -> float:
    return max(bar['high'] - bar['low'],
               abs(bar['high'] - prev_close),
               abs(bar['low'] - prev_close))


def wilder_atr(bars: List[Dict], period: int = 14) -> float:
    """ATR seeded with an SMA of the first `period` true ranges, then Wilder.

    ATR_t = (13 * ATR_{t-1} + TR_t) / 14. The older SMA-only ATR in
    flush_shadow.calculate_atr14 is a different estimator and is kept under its
    own name rather than silently replaced -- the two disagree during expansion,
    which is exactly when the risk gate matters.
    """
    if len(bars) < period + 1:
        return 0.0
    trs = [true_range(bars[i], bars[i - 1]['close']) for i in range(1, len(bars))]
    if len(trs) < period:
        return 0.0
    atr = sum(trs[:period]) / float(period)
    for tr in trs[period:]:
        atr = ((period - 1) * atr + tr) / float(period)
    return atr


def to_bps(value: float, price: float) -> float:
    return (value / price) * 1e4 if price > 0 else 0.0


def ema_series(values: List[float], period: int) -> List[float]:
    """EMA seeded with the SMA of the first `period` values."""
    if len(values) < period or period <= 0:
        return []
    k = 2.0 / (period + 1.0)
    out = [sum(values[:period]) / float(period)]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1.0 - k))
    return out


def adx_di(bars: List[Dict], period: int = 14) -> Tuple[float, float, float]:
    """Wilder ADX14 with +DI/-DI. Returns (adx, di_plus, di_minus).

    A flat series has no directional movement, so DI and ADX are 0 rather than a
    divide-by-zero or an inherited previous reading.
    """
    if len(bars) < 2 * period + 1:
        return 0.0, 0.0, 0.0
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, len(bars)):
        up_move = bars[i]['high'] - bars[i - 1]['high']
        down_move = bars[i - 1]['low'] - bars[i]['low']
        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0.0)
        trs.append(true_range(bars[i], bars[i - 1]['close']))

    def smooth(series: List[float]) -> List[float]:
        acc = sum(series[:period])
        out = [acc]
        for v in series[period:]:
            acc = acc - acc / float(period) + v
            out.append(acc)
        return out

    tr_s, plus_s, minus_s = smooth(trs), smooth(plus_dm), smooth(minus_dm)
    dis = []
    for tr_v, p_v, m_v in zip(tr_s, plus_s, minus_s):
        if tr_v <= 0:
            dis.append((0.0, 0.0, 0.0))
            continue
        di_p, di_m = 100.0 * p_v / tr_v, 100.0 * m_v / tr_v
        denom = di_p + di_m
        dis.append((di_p, di_m, 100.0 * abs(di_p - di_m) / denom if denom > 0 else 0.0))
    dx = [d[2] for d in dis]
    if len(dx) < period:
        return 0.0, dis[-1][0], dis[-1][1]
    adx = sum(dx[:period]) / float(period)
    for v in dx[period:]:
        adx = ((period - 1) * adx + v) / float(period)
    return adx, dis[-1][0], dis[-1][1]


def ema_slope(ema20: List[float], atr15: float, lag: int = 3) -> float:
    """(EMA20_t - EMA20_{t-lag}) / (lag * ATR15): trend speed in ATR units."""
    if len(ema20) <= lag or atr15 <= 0:
        return 0.0
    return (ema20[-1] - ema20[-1 - lag]) / (lag * atr15)


def classify_regime(bars15: List[Dict]) -> Tuple[str, Dict]:
    """Classify the 15m regime from CLOSED bars. Returns (regime, diagnostics).

    Insufficient history is TRANSITION, never a tradeable regime: a missing input
    must not resolve into permission.
    """
    diag = {'adx': 0.0, 'di_plus': 0.0, 'di_minus': 0.0, 'slope': 0.0,
            'ema20': 0.0, 'ema50': 0.0, 'atr15': 0.0, 'close': 0.0}
    if len(bars15) < 60:
        return TRANSITION, diag
    closes = [b['close'] for b in bars15]
    e20, e50 = ema_series(closes, 20), ema_series(closes, 50)
    if not e20 or not e50:
        return TRANSITION, diag
    atr15 = wilder_atr(bars15)
    adx, di_p, di_m = adx_di(bars15)
    slope = ema_slope(e20, atr15)
    close = closes[-1]
    diag.update({'adx': adx, 'di_plus': di_p, 'di_minus': di_m, 'slope': slope,
                 'ema20': e20[-1], 'ema50': e50[-1], 'atr15': atr15, 'close': close})

    if close < e20[-1] < e50[-1] and slope <= -SLOPE_TREND and adx >= ADX_TREND and di_m > di_p:
        return DOWN, diag
    if close > e20[-1] > e50[-1] and slope >= SLOPE_TREND and adx >= ADX_TREND and di_p > di_m:
        return UP, diag
    if adx <= ADX_RANGE and abs(slope) <= SLOPE_RANGE and abs(e20[-1] - e50[-1]) <= EMA_GAP_RANGE * atr15:
        return RANGE, diag
    return TRANSITION, diag


def is_fast_down(bars1: List[Dict], atr_prev: float, lookback: int = LOOKBACK) -> bool:
    """1m capitulation guard. Either leg fires it; it always forbids new Longs."""
    if len(bars1) < lookback + 4 or atr_prev <= 0:
        return False
    signal, window = bars1[-1], bars1[-1 - lookback:-1]
    if signal['close'] < min(b['low'] for b in window) - 0.2 * atr_prev:
        return True
    return (signal['close'] - bars1[-4]['close']) <= -1.5 * atr_prev


def _window(bars1: List[Dict], lookback: int):
    """Signal bar plus the `lookback` bars BEFORE it (slice excludes bar t)."""
    return bars1[-1], bars1[-1 - lookback:-1]


def long_reclaim_signal(bars1: List[Dict], atr_prev: float,
                        lookback: int = LOOKBACK) -> Tuple[bool, Dict]:
    """Flush AND reclaim AND volume -- all three, never the old OR."""
    info = {'reason': 'INSUFFICIENT_BARS', 'support': 0.0}
    if len(bars1) < lookback + 2 or atr_prev <= 0:
        return False, info
    t, window = _window(bars1, lookback)
    support = min(b['low'] for b in window)
    vref = median([b['volume'] for b in window])
    rng = t['high'] - t['low']
    info.update({'support': support, 'vref': vref})
    if rng <= 0:
        info['reason'] = 'ZERO_RANGE'          # H == L makes the signal undefined
        return False, info
    flush = t['low'] <= support - 0.1 * atr_prev
    reclaim = t['close'] > support and (t['close'] - t['low']) / rng >= 0.60
    volume = t['volume'] >= 1.5 * vref
    info.update({'flush': flush, 'reclaim': reclaim, 'volume': volume})
    if flush and reclaim and volume:
        info['reason'] = 'LONG_CANDIDATE'
        return True, info
    info['reason'] = 'NO_SIGNAL'
    return False, info


def confirm_long(signal_bar: Dict, next_bar: Dict) -> bool:
    """t+1 must close above the signal high and hold its low. No confirm, no trade."""
    return next_bar['close'] > signal_bar['high'] and next_bar['low'] >= signal_bar['low']


def short_break_signal(bars1: List[Dict], atr_prev: float,
                       lookback: int = LOOKBACK) -> Tuple[bool, Dict]:
    info = {'reason': 'INSUFFICIENT_BARS', 'support': 0.0}
    if len(bars1) < lookback + 2 or atr_prev <= 0:
        return False, info
    t, window = _window(bars1, lookback)
    support = min(b['low'] for b in window)
    vref = median([b['volume'] for b in window])
    rng = t['high'] - t['low']
    info.update({'support': support, 'vref': vref})
    if rng <= 0:
        info['reason'] = 'ZERO_RANGE'
        return False, info
    broke = t['close'] < support - 0.2 * atr_prev
    weak_close = (t['close'] - t['low']) / rng <= 0.30
    volume = t['volume'] >= 1.5 * vref
    info.update({'broke': broke, 'weak_close': weak_close, 'volume': volume})
    if broke and weak_close and volume:
        info['reason'] = 'SHORT_BREAK'
        return True, info
    info['reason'] = 'NO_SIGNAL'
    return False, info


def short_retest(candidates: List[Dict], support: float, atr_signal: float,
                 max_bars: int = 3) -> Tuple[bool, Optional[Dict]]:
    """Look for the retest bar in at most `max_bars` bars after the break.

    Past the window the event expires: a break without a retest is not a trade,
    and a late touch is a different setup with a different stop.
    """
    if atr_signal <= 0:
        return False, None
    for bar in candidates[:max_bars]:
        high_ok = (support - 0.1 * atr_signal) <= bar['high'] <= (support + 0.3 * atr_signal)
        if high_ok and bar['close'] < support - 0.1 * atr_signal and bar['close'] < bar['open']:
            return True, bar
    return False, None


def long_permitted(regime: str, fast_down: bool, range_streak: int) -> bool:
    """Long needs RANGE now, two consecutive RANGE closes, and no FAST_DOWN.

    The hysteresis deliberately misses the first leg of a bounce. That is the
    cost of not buying into a 15m downtrend, and it is paid on purpose.
    """
    return regime == RANGE and not fast_down and range_streak >= 2


def short_permitted(regime: str) -> bool:
    return regime == DOWN
