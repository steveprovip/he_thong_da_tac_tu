"""Phase 1 of docs/harness/plan_micro_adverse_selection.md: attempt-level feature and post-fill markout log of the chop harvester.

Pure bookkeeping: no orders, no exchange calls, no decisions.  The engine opens one record per harvest ORDER (filled or not), tells it when
the order fills, and calls sample() with the current mid prices; a finished record is appended as one JSON line to the log.

Two series per record, both signed WITH the trade (BUY: price up is +, SELL: price down is +), in bps:
  q_*   from the moment the order was posted, against the quote price rebased to mainnet terms
        (quote x signal_mid / venue_mid): the "if it had filled at the quote" series.  It exists for UNFILLED orders too, so the
        fill-conditioned markout can be compared with the unconditional one - that gap is the adverse selection.
  f_*   from the fill, against the fill price rebased the same way (filled orders only).
Each series is taken from the SIGNAL feed (the mainnet mid) and from the VENUE book (the Demo mid); the Demo book wanders +-10-19 bps from
mainnet, so only the first is a clean read of the market.  A missing price is recorded as None (UNKNOWN is not zero).

A record is complete when its last horizon of the series that applies has been sampled, or when it is older than max_age_s
(`complete: false`, the horizons reached so far are kept).  Failures of the log never propagate: the engine's trading path must not depend on it."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Optional

HORIZONS = (1.0, 5.0, 15.0, 30.0, 60.0)
SCHEMA = 1


def signed_bps(side: str, ref: Optional[float], px: Optional[float]) -> Optional[float]:
    """Move of px against ref in bps, positive when it favours `side` (BUY wants it up, SELL down)."""
    if not ref or not px or ref <= 0 or px <= 0:
        return None
    return round((1 if side == 'BUY' else -1) * (px / ref - 1.0) * 1e4, 3)


def rebase(quote: float, signal_mid: Optional[float], venue_mid: Optional[float]) -> float:
    """A venue price expressed in mainnet terms: the Demo book sits basis bps above / below the signal feed."""
    if signal_mid and venue_mid and signal_mid > 0 and venue_mid > 0:
        return quote * signal_mid / venue_mid
    return quote


class MarkoutLog:
    def __init__(self, path, horizons=HORIZONS, clock: Callable[[], float] = time.time, max_age_s: float = 240.0,
                 max_open: int = 64):
        self.path = Path(path)
        self.horizons = tuple(horizons)
        self.clock = clock
        self.max_age_s, self.max_open = float(max_age_s), int(max_open)
        self.open: dict = {}
        self.errors = 0
        self.written = 0

    # ------------------------------------------------------------------ lifecycle
    def post(self, key: str, symbol: str, side: str, quote_px: float, signal_mid: Optional[float], venue_mid: Optional[float],
             features: Optional[dict] = None, extra: Optional[dict] = None) -> None:
        """An order was posted at quote_px (venue price)."""
        if len(self.open) >= self.max_open:                      # a stuck record can never grow the dict without bound
            self.flush(force_oldest=True)
        now = self.clock()
        self.open[key] = {
            'schema': SCHEMA, 'key': key, 'symbol': symbol, 'side': side, 't_post': now, 'quote_px': quote_px,
            'quote_ref': rebase(quote_px, signal_mid, venue_mid), 'sig0': signal_mid, 'ven0': venue_mid,
            'basis_bps': round((venue_mid / signal_mid - 1) * 1e4, 3) if signal_mid and venue_mid else None,
            'features': features or {}, **(extra or {}),
            'filled': False, 't_fill': None, 'fill_px': None, 'fill_ref': None, 'fill_sig0': None, 'fill_ven0': None,
            'fill_features': None, 'q_sig': {}, 'q_ven': {}, 'f_sig': {}, 'f_ven': {}}

    def filled(self, key: str, fill_px: float, signal_mid: Optional[float], venue_mid: Optional[float],
               fill_features: Optional[dict] = None) -> None:
        r = self.open.get(key)
        if r is None or r['filled']:
            return
        r.update(filled=True, t_fill=self.clock(), fill_px=fill_px, fill_ref=rebase(fill_px, signal_mid, venue_mid),
                 fill_sig0=signal_mid, fill_ven0=venue_mid, fill_features=fill_features)

    # ------------------------------------------------------------------ sampling
    def sample(self, signal_mid: Optional[float], venue_mid: Optional[float], symbol: Optional[str] = None) -> int:
        """Record every horizon that has come due for the open records of `symbol` (all when None).  Returns the number written."""
        now, n = self.clock(), 0
        for key in list(self.open):
            r = self.open[key]
            if symbol is not None and r['symbol'] != symbol:
                continue
            for h in self.horizons:
                name = f'{h:g}s'
                if name not in r['q_sig'] and now - r['t_post'] >= h:
                    r['q_sig'][name] = signed_bps(r['side'], r['quote_ref'], signal_mid)
                    r['q_ven'][name] = signed_bps(r['side'], r['quote_px'], venue_mid)
                if r['filled'] and name not in r['f_sig'] and now - r['t_fill'] >= h:
                    r['f_sig'][name] = signed_bps(r['side'], r['fill_ref'], signal_mid)
                    r['f_ven'][name] = signed_bps(r['side'], r['fill_px'], venue_mid)
            last = f'{self.horizons[-1]:g}s'
            # filled: complete when the series from the FILL reached the last horizon (the quote series ended before it);
            # unfilled: complete once the engine said the order is dead (close()) and the quote series is full
            done = (last in r['f_sig']) if r['filled'] else (last in r['q_sig'] and r.get('closed_t') is not None)
            if done:
                n += self._write(key, complete=True)
            elif now - r['t_post'] >= self.max_age_s:
                n += self._write(key, complete=False)
        return n

    def close(self, key: str) -> None:
        """The order will not fill any more (cancelled / expired): its quote series may now complete."""
        r = self.open.get(key)
        if r is not None:
            r['closed_t'] = self.clock()

    def flush(self, force_oldest: bool = False) -> int:
        """Write what is open (incomplete) - at shutdown, or to make room."""
        keys = sorted(self.open, key=lambda k: self.open[k]['t_post'])
        if force_oldest:
            keys = keys[:1]
        return sum(self._write(k, complete=False) for k in keys)

    # ------------------------------------------------------------------ output
    def _write(self, key: str, complete: bool) -> int:
        r = self.open.pop(key, None)
        if r is None:
            return 0
        r['complete'] = complete
        r['t_written'] = self.clock()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, 'a', encoding='utf-8') as fh:
                fh.write(json.dumps(r, ensure_ascii=False, separators=(',', ':')) + '\n')
            self.written += 1
            return 1
        except (OSError, TypeError, ValueError):
            self.errors += 1                                     # never raise into the trading loop
            return 0
