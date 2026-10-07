"""Liquidation-cascade sniper (SHADOW: simulated fills, zero orders).

Feed: Binance USD-M `!forceOrder@arr` (all-market liquidations) + `!markPrice@arr@1s`.

Venue facts that bound what this can see (developers.binance.com, 2026):
  * forceOrder pushes at most ONE liquidation per symbol per 1000 ms (the latest).
    A "cascade" summed from this stream is therefore a LOWER BOUND of real size.
  * markPrice@1s is the pricing source for exits here; the last-trade wick is
    deeper than the mark, so shadow entries at mark + slippage are optimistic on
    the best case and the stop model below is the honest part.

Signal (per symbol, per side):
  cascade  = sum of liquidated notional (avg price x filled qty) of one side in
             the last `window_s` seconds >= `cascade_usd`.
  wick     = mark moved >= `min_drop_pct` against the liquidated side within the
             last `drop_lookback_s` (longs liquidated -> price fell).
  action   = counter-trend: longs liquidated (forced SELL) -> BUY; shorts -> SELL.
Exit: TP +tp_pct (maker), stop sized so loss incl. fees <= max_loss, `max_hold_s`
timeout at mark (taker). One position account-wide, per-symbol cooldown.
"""
from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from typing import Deque, Dict, List, Optional, Tuple

WS_STREAMS = '!forceOrder@arr/!markPrice@arr@1s'


@dataclass(frozen=True)
class SniperConfig:
    window_s: float = 5.0
    cascade_usd: float = 50_000.0
    min_drop_pct: float = 0.4
    drop_lookback_s: float = 30.0
    tp_pct: float = 1.0                 # 0.8 .. 1.5
    max_loss: float = 0.05              # USDT per trade incl. fees + slippage
    max_hold_s: float = 60.0
    equity: float = 3.85
    max_margin: float = 2.6
    leverage_choices: Tuple[int, ...] = (2, 3)
    taker_bps: float = 5.0
    maker_bps: float = 2.0
    slip_bps: float = 10.0              # liquidation wicks slip more than quiet books
    cooldown_s: float = 120.0
    feed_keep: int = 60                 # liquidations kept for the board
    big_usd: float = 10_000.0           # feed rows below this are dropped from the board


def parse_force_order(msg: dict, now: Optional[float] = None) -> Optional[dict]:
    data = msg.get('data', msg) if isinstance(msg, dict) else None
    if not isinstance(data, dict) or data.get('e') != 'forceOrder':
        return None
    o = data.get('o') or {}
    try:
        qty = float(o.get('z') or o.get('q'))
        px = float(o.get('ap') or o.get('p'))
        side = o['S']
        sym = o['s']
    except (KeyError, TypeError, ValueError):
        return None
    if qty <= 0 or px <= 0 or side not in ('BUY', 'SELL'):
        return None
    return {'symbol': sym, 'side': side, 'qty': qty, 'price': px, 'notional': qty * px,
            't': (o.get('T') or data.get('E') or 0) / 1000.0 or (now or time.time()),
            'recv': now if now is not None else time.time(),
            # a forced SELL closes a LONG: longs are being liquidated, price is pushed down
            'liquidated': 'LONG' if side == 'SELL' else 'SHORT'}


class CascadeDetector:
    def __init__(self, cfg: SniperConfig):
        self.cfg = cfg
        self.events: Dict[Tuple[str, str], Deque[Tuple[float, float]]] = defaultdict(deque)

    def add(self, ev: dict, now: float) -> Tuple[float, int]:
        """-> (notional of this symbol/side in the window, count)."""
        q = self.events[(ev['symbol'], ev['liquidated'])]
        q.append((now, ev['notional']))
        return self.total(ev['symbol'], ev['liquidated'], now)

    def total(self, symbol: str, liquidated: str, now: float) -> Tuple[float, int]:
        q = self.events[(symbol, liquidated)]
        while q and now - q[0][0] > self.cfg.window_s:
            q.popleft()
        return sum(n for _, n in q), len(q)


class MarkBook:
    def __init__(self, keep_s: float = 180.0):
        self.keep_s = keep_s
        self.hist: Dict[str, Deque[Tuple[float, float]]] = defaultdict(deque)

    def on_mark_array(self, msg, now: float) -> int:
        data = msg.get('data', msg) if isinstance(msg, dict) else msg
        rows = data if isinstance(data, list) else [data]
        n = 0
        for r in rows:
            if not isinstance(r, dict) or r.get('e') != 'markPriceUpdate':
                continue
            try:
                self.set(r['s'], float(r['p']), now)
                n += 1
            except (KeyError, TypeError, ValueError):
                continue
        return n

    def set(self, symbol: str, price: float, now: float) -> None:
        if price <= 0:
            return
        q = self.hist[symbol]
        q.append((now, price))
        while q and now - q[0][0] > self.keep_s:
            q.popleft()

    def last(self, symbol: str) -> Optional[float]:
        q = self.hist.get(symbol)
        return q[-1][1] if q else None

    def move_pct(self, symbol: str, now: float, lookback: float, liquidated: str) -> Optional[float]:
        """How far price travelled in the liquidation's direction over `lookback` (positive = wick)."""
        q = self.hist.get(symbol)
        if not q:
            return None
        pts = [p for t, p in q if now - t <= lookback]
        if len(pts) < 2:
            return None
        last = pts[-1]
        if liquidated == 'LONG':                      # price was pushed down from the recent high
            return (max(pts) - last) / max(pts) * 100.0
        return (last - min(pts)) / min(pts) * 100.0


def plan(price: float, spec: dict, cfg: SniperConfig) -> Optional[dict]:
    step, min_qty = spec.get('step', 1e-9), spec.get('min_qty', 0.0)
    min_notional = spec.get('min_notional', 5.0)
    qty = max(min_notional * 1.02 / price, min_qty)
    dec = len(f'{step:.12f}'.rstrip('0').split('.')[1]) if '.' in f'{step:.12f}'.rstrip('0') else 0
    qty = round(math.ceil(round(qty / step, 9)) * step, dec)
    notional = qty * price
    lev = next((lv for lv in sorted(cfg.leverage_choices) if notional / lv <= cfg.max_margin), None)
    cost_pct = (2 * cfg.taker_bps + 2 * cfg.slip_bps) / 100.0          # market in + stop out, both slipped
    sl_pct = cfg.max_loss / notional * 100.0 - cost_pct
    if lev is None or sl_pct <= 0.1:
        return None
    return {'qty': qty, 'notional': notional, 'leverage': lev, 'margin': notional / lev,
            'sl_pct': sl_pct, 'tp_pct': cfg.tp_pct, 'rr': cfg.tp_pct / sl_pct}


class Sniper:
    def __init__(self, cfg: SniperConfig = SniperConfig(), specs: Optional[Dict[str, dict]] = None):
        self.cfg = cfg
        self.specs = specs or {}
        self.detector = CascadeDetector(cfg)
        self.marks = MarkBook(keep_s=max(180.0, cfg.drop_lookback_s * 2))
        self.feed: Deque[dict] = deque(maxlen=cfg.feed_keep)
        self.cascades: Deque[dict] = deque(maxlen=30)
        self.pos: Optional[dict] = None
        self.trades: List[dict] = []
        self.cooldown: Dict[str, float] = {}
        self.stats = {'liquidations': 0, 'liq_usd': 0.0, 'cascades': 0, 'entries': 0, 'skipped': 0}
        self.events: Deque[dict] = deque(maxlen=40)

    def _event(self, now: float, msg: str) -> None:
        self.events.appendleft({'t': now, 'message': msg[:240]})

    # ---- feed
    def on_message(self, msg: dict, now: float) -> Optional[dict]:
        if isinstance(msg, list):
            self.marks.on_mark_array(msg, now)
            return None
        stream = msg.get('stream', '') if isinstance(msg, dict) else ''
        if 'markPrice' in stream or isinstance(msg.get('data'), list):
            self.marks.on_mark_array(msg, now)
            return None
        ev = parse_force_order(msg, now)
        if ev is None:
            return None
        self.stats['liquidations'] += 1
        self.stats['liq_usd'] += ev['notional']
        if ev['notional'] >= self.cfg.big_usd:
            self.feed.appendleft(ev)
        total, count = self.detector.add(ev, now)
        if total >= self.cfg.cascade_usd:
            return self._on_cascade(ev, total, count, now)
        return None

    def _on_cascade(self, ev: dict, total: float, count: int, now: float) -> Optional[dict]:
        sym, liq = ev['symbol'], ev['liquidated']
        cas = {'t': now, 'symbol': sym, 'liquidated': liq, 'usd_window': total, 'count': count,
               'move_pct': self.marks.move_pct(sym, now, self.cfg.drop_lookback_s, liq)}
        if self.cascades and self.cascades[0]['symbol'] == sym and now - self.cascades[0]['t'] < self.cfg.window_s:
            self.cascades[0].update(cas)                      # same burst: update in place
        else:
            self.cascades.appendleft(cas)
            self.stats['cascades'] += 1
        why = self._blocked(cas, now)
        cas['action'] = why or ('BUY' if liq == 'LONG' else 'SELL')
        if why:
            return cas
        self._open(cas, now)
        return cas

    def _blocked(self, cas: dict, now: float) -> Optional[str]:
        sym = cas['symbol']
        if self.pos is not None:
            return 'BUSY'
        if now < self.cooldown.get(sym, 0.0):
            return 'COOLDOWN'
        if sym not in self.specs:
            return 'NO_SPEC'
        if self.marks.last(sym) is None:
            return 'NO_MARK'
        mv = cas['move_pct']
        if mv is None or mv < self.cfg.min_drop_pct:
            return f'NO_WICK ({0 if mv is None else mv:.2f}% < {self.cfg.min_drop_pct}%)'
        return None

    def _open(self, cas: dict, now: float) -> None:
        sym, c = cas['symbol'], self.cfg
        mark = self.marks.last(sym)
        side = 'BUY' if cas['liquidated'] == 'LONG' else 'SELL'
        d = 1 if side == 'BUY' else -1
        entry = mark * (1 + d * c.slip_bps / 1e4)          # market order into the wick, slipped
        p = plan(entry, self.specs[sym], c)                 # size + stop from the price actually paid
        if p is None:
            self.stats['skipped'] += 1
            cas['action'] = 'SIZE'
            return
        self.pos = {'symbol': sym, 'side': side, 'entry': entry, 'qty': p['qty'], 'opened': now,
                    'tp': entry * (1 + d * p['tp_pct'] / 100.0), 'sl': entry * (1 - d * p['sl_pct'] / 100.0),
                    'plan': p, 'cascade_usd': cas['usd_window'], 'move_pct': cas['move_pct'], 'mark': mark}
        self.cooldown[sym] = now + c.cooldown_s
        self.stats['entries'] += 1
        self._event(now, f'SNIPE {sym} {side} {p["qty"]:g} @ {entry:g} after ${cas["usd_window"]:,.0f} '
                         f'{cas["liquidated"]} liquidations, wick {cas["move_pct"]:.2f}% · TP +{p["tp_pct"]}% '
                         f'SL -{p["sl_pct"]:.2f}% · {p["leverage"]}x')

    # ---- exits
    def step(self, now: float) -> Optional[dict]:
        p = self.pos
        if p is None:
            return None
        c = self.cfg
        mark = self.marks.last(p['symbol'])
        if mark is None:
            return None
        p['mark'] = mark
        d = 1 if p['side'] == 'BUY' else -1
        slip = c.slip_bps / 1e4
        if d * (mark - p['sl']) <= 0:
            exit_px, reason, fee = min(mark, p['sl']) * (1 - slip) if d > 0 else max(mark, p['sl']) * (1 + slip), 'SL', c.taker_bps
        elif d * (mark - p['tp']) >= 0:
            exit_px, reason, fee = p['tp'], 'TP', c.maker_bps
        elif now - p['opened'] >= c.max_hold_s:
            exit_px, reason, fee = mark * (1 - d * slip), 'TIME', c.taker_bps
        else:
            return None
        notional = p['entry'] * p['qty']
        gross = d * (exit_px / p['entry'] - 1.0) * 100.0
        net_pct = gross - (c.taker_bps + fee) / 100.0
        t = {'symbol': p['symbol'], 'side': p['side'], 'entry': p['entry'], 'exit': exit_px, 'qty': p['qty'],
             'reason': reason, 'net_pct': net_pct, 'pnl': notional * net_pct / 100.0,
             'hold_s': now - p['opened'], 'cascade_usd': p['cascade_usd'], 'move_pct': p['move_pct'],
             'opened': p['opened'], 'closed': now}
        self.trades.insert(0, t)
        self.pos = None
        self._event(now, f'EXIT {t["symbol"]} {reason} {net_pct:+.2f}% ({t["pnl"]:+.4f} U) in {t["hold_s"]:.0f}s')
        return t

    def unrealized(self) -> float:
        p = self.pos
        if p is None or p.get('mark') is None:
            return 0.0
        d = 1 if p['side'] == 'BUY' else -1
        c = self.cfg
        exit_px = p['mark'] * (1 - d * c.slip_bps / 1e4)
        return p['entry'] * p['qty'] * (d * (exit_px / p['entry'] - 1) * 100 - 2 * c.taker_bps / 100) / 100

    def summary(self) -> dict:
        n = len(self.trades)
        nets = [t['net_pct'] for t in self.trades]
        mean = sum(nets) / n if n else None
        sd = (sum((x - mean) ** 2 for x in nets) / (n - 1)) ** 0.5 if n > 1 else None
        return {'n': n, 'wins': sum(x > 0 for x in nets), 'mean_net_pct': mean,
                'lower95_pct': mean - 1.96 * sd / math.sqrt(n) if sd is not None else None,
                'pnl_usdt': sum(t['pnl'] for t in self.trades),
                'by_reason': {r: sum(t['reason'] == r for t in self.trades) for r in ('TP', 'SL', 'TIME')}}

    def snapshot(self, now: float) -> dict:
        p = self.pos
        return {'config': asdict(self.cfg), 'stats': dict(self.stats), 'summary': self.summary(),
                'feed': list(self.feed)[:30], 'cascades': list(self.cascades)[:15],
                'position': None if p is None else {k: v for k, v in p.items() if k != 'plan'}
                | {'age_s': now - p['opened'], 'upnl': self.unrealized(), 'sl_pct': p['plan']['sl_pct']},
                'trades': self.trades[:20], 'events': list(self.events)[:20],
                'marks_tracked': len(self.marks.hist)}
