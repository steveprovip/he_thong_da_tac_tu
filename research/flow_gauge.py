"""Order-flow pressure gauge: L5 order-book imbalance + cumulative volume delta.

Feeds (Binance USD-M public, no key):
  <sym>@aggTrade        m=True -> buyer is maker -> SELL aggressor (signed -qty)
  <sym>@depth5@100ms    top-5 bids/asks -> OBI = (sum bid qty - sum ask qty) / total

pressure = 0.5 * OBI_ema + 0.5 * CVD_60s / gross_60s   in [-1, +1]
(+1 = one-sided buying). Descriptive telemetry for the UI and for LUMEN-style
filters; not a calibrated edge.
"""
from __future__ import annotations

from collections import deque
from typing import Deque, Optional, Tuple


class FlowGauge:
    def __init__(self, symbol: str = 'BTCUSDT', window_s: float = 60.0, ema_alpha: float = 0.2,
                 series_keep: int = 180):
        self.symbol = symbol.upper()
        self.window_s = window_s
        self.alpha = ema_alpha
        self.trades: Deque[Tuple[float, float]] = deque()      # (t, signed usd)
        self.buy_usd = self.sell_usd = 0.0                       # rolling window sums
        self.cvd_session = 0.0
        self.n_trades = 0
        self.last_price: Optional[float] = None
        self.obi: Optional[float] = None
        self.obi_ema: Optional[float] = None
        self.series: Deque[list] = deque(maxlen=series_keep)     # [t, cvd_60, obi_ema, price]
        self._last_sample = 0

    # ------------------------------------------------------------------ feeds
    def route(self, msg: dict, now: float) -> bool:
        """Consume a combined-stream frame if it belongs to this gauge."""
        stream = str(msg.get('stream', '')) if isinstance(msg, dict) else ''
        if not stream.startswith(self.symbol.lower() + '@'):
            return False
        data = msg.get('data') or {}
        if stream.endswith('@aggTrade'):
            self.on_agg_trade(data, now)
        elif '@depth' in stream:
            self.on_depth(data, now)
        else:
            return False
        return True

    def on_agg_trade(self, d: dict, now: float) -> None:
        try:
            px, qty, maker = float(d['p']), float(d['q']), bool(d['m'])
        except (KeyError, TypeError, ValueError):
            return
        usd = px * qty
        signed = -usd if maker else usd
        self.trades.append((now, signed))
        if signed > 0:
            self.buy_usd += usd
        else:
            self.sell_usd += usd
        self.cvd_session += signed
        self.n_trades += 1
        self.last_price = px
        self._expire(now)
        self._sample(now)

    def on_depth(self, d: dict, now: float) -> None:
        try:
            bq = sum(float(q) for _, q in (d.get('b') or [])[:5])
            aq = sum(float(q) for _, q in (d.get('a') or [])[:5])
        except (TypeError, ValueError):
            return
        if bq + aq <= 0:
            return
        self.obi = (bq - aq) / (bq + aq)
        self.obi_ema = self.obi if self.obi_ema is None else self.alpha * self.obi + (1 - self.alpha) * self.obi_ema
        self._sample(now)

    # ------------------------------------------------------------------ state
    def _expire(self, now: float) -> None:
        q = self.trades
        while q and now - q[0][0] > self.window_s:
            _, s = q.popleft()
            if s > 0:
                self.buy_usd -= s
            else:
                self.sell_usd += s
        if not q:
            self.buy_usd = self.sell_usd = 0.0

    def _sample(self, now: float) -> None:
        sec = int(now)
        if sec != self._last_sample:
            self._last_sample = sec
            self.series.append([sec, self.cvd_window(), self.obi_ema, self.last_price])

    def cvd_window(self) -> float:
        return self.buy_usd - self.sell_usd

    def pressure(self) -> Optional[float]:
        gross = self.buy_usd + self.sell_usd
        parts = []
        if self.obi_ema is not None:
            parts.append(self.obi_ema)
        if gross > 0:
            parts.append(self.cvd_window() / gross)
        return sum(parts) / len(parts) if parts else None

    def snapshot(self, now: float) -> dict:
        self._expire(now)
        return {'symbol': self.symbol, 'window_s': self.window_s, 'price': self.last_price,
                'obi': self.obi, 'obi_ema': self.obi_ema, 'cvd_window': self.cvd_window(),
                'cvd_session': self.cvd_session, 'buy_usd': self.buy_usd, 'sell_usd': self.sell_usd,
                'pressure': self.pressure(), 'trades': self.n_trades, 'series': list(self.series)[-120:]}
