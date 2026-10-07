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

2026-10 upgrade:
  * JEV System-1 recovery score (pure arithmetic, measured in us, budget 30 ms):
      conf     = 0.4 + 0.4*intensity(log2 usd/threshold, /3) + 0.2*breadth(count)
      rebound  = rebound_frac * wick% * conf          (expected mean-reversion)
      score    = core.jev_gatekeeper.score_from_ratio(rebound / round-trip cost)
    so score >= 8.5 <=> expected rebound > 3x all-in cost (same JEV invariant).
    Wick deeper than max_wick_pct = trend break, score 0.
  * entry_mode='maker': Post-Only GTX limit `maker_offset_bps` deeper into the
    wick; fills only if the 1 s mark trades THROUGH it within maker_ttl_s, else
    MISSED (adverse-selection-honest: no fill on the bounce you wanted).
    Binance VIP0 maker = 0.02 % fee (a cost, not a rebate) - modelled as such.
  * dynamic_tp: TP in [tp_min_pct, tp_max_pct] scaled by score (0.8 .. 1.5 %).
  * per-second liquidation buckets (LONG/SHORT USD) for the UI cascade stream.

    python -m research.fast_liquidation_sniper --mode shadow   (zero orders)
"""
from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from typing import Deque, Dict, List, Optional, Tuple

from core.jev_gatekeeper import score_from_ratio

WS_FORCE_ORDER = 'wss://fstream.binance.com/ws/!forceOrder@arr'
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
    # ---- 2026-10 upgrade (defaults keep the original taker behaviour)
    entry_mode: str = 'taker'           # 'taker' | 'maker' (Post-Only GTX)
    maker_offset_bps: float = 5.0       # GTX limit this far beyond mark, into the wick
    maker_ttl_s: float = 3.0            # cancel unfilled GTX after this
    dynamic_tp: bool = False
    tp_min_pct: float = 0.8
    tp_max_pct: float = 1.5
    min_score: float = 0.0              # JEV recovery score gate (0..10)
    rebound_frac: float = 0.5           # expected retrace fraction of the wick
    max_wick_pct: float = 3.0           # deeper = trend break, not a wick
    score_budget_ms: float = 30.0
    enabled: bool = True                # False: feed/telemetry only, never enters
    bucket_keep_s: int = 120


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


def recovery_score(usd: float, count: int, move_pct: Optional[float], cfg: SniperConfig,
                   entry_fee_bps: Optional[float] = None) -> dict:
    """JEV System-1 reflex: expected rebound vs all-in cost -> 0..10 (no I/O)."""
    if move_pct is None or move_pct <= 0:
        return {'score': 0.0, 'ratio': 0.0, 'rebound_pct': 0.0, 'conf': 0.0, 'reason': 'NO_WICK'}
    if move_pct > cfg.max_wick_pct:
        return {'score': 0.0, 'ratio': 0.0, 'rebound_pct': 0.0, 'conf': 0.0, 'reason': 'TRENDBREAK'}
    intensity = min(1.0, max(0.0, math.log2(max(usd, 1.0) / cfg.cascade_usd) / 3.0))
    breadth = min(1.0, max(0.0, (count - 1) / 4.0))
    conf = 0.4 + 0.4 * intensity + 0.2 * breadth
    rebound = cfg.rebound_frac * move_pct * conf
    if entry_fee_bps is None:
        entry_fee_bps = cfg.maker_bps if cfg.entry_mode == 'maker' else cfg.taker_bps + cfg.slip_bps
    cost = (entry_fee_bps + cfg.taker_bps + cfg.slip_bps) / 100.0
    ratio = rebound / cost if cost > 0 else 0.0
    return {'score': score_from_ratio(ratio), 'ratio': ratio, 'rebound_pct': rebound, 'conf': conf,
            'reason': 'OK'}


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


def plan(price: float, spec: dict, cfg: SniperConfig, tp_pct: Optional[float] = None,
         entry_cost_bps: Optional[float] = None) -> Optional[dict]:
    step, min_qty = spec.get('step', 1e-9), spec.get('min_qty', 0.0)
    min_notional = spec.get('min_notional', 5.0)
    qty = max(min_notional * 1.02 / price, min_qty)
    dec = len(f'{step:.12f}'.rstrip('0').split('.')[1]) if '.' in f'{step:.12f}'.rstrip('0') else 0
    qty = round(math.ceil(round(qty / step, 9)) * step, dec)
    notional = qty * price
    lev = next((lv for lv in sorted(cfg.leverage_choices) if notional / lv <= cfg.max_margin), None)
    if entry_cost_bps is None:                                          # market in, slipped
        entry_cost_bps = cfg.taker_bps + cfg.slip_bps
    cost_pct = (entry_cost_bps + cfg.taker_bps + cfg.slip_bps) / 100.0  # + stop out, slipped
    sl_pct = cfg.max_loss / notional * 100.0 - cost_pct
    if lev is None or sl_pct <= 0.1:
        return None
    tp = cfg.tp_pct if tp_pct is None else tp_pct
    return {'qty': qty, 'notional': notional, 'leverage': lev, 'margin': notional / lev,
            'sl_pct': sl_pct, 'tp_pct': tp, 'rr': tp / sl_pct}


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
        self.stats = {'liquidations': 0, 'liq_usd': 0.0, 'cascades': 0, 'entries': 0, 'skipped': 0,
                      'maker_posted': 0, 'maker_filled': 0, 'maker_missed': 0, 'score_us_max': 0.0}
        self.events: Deque[dict] = deque(maxlen=40)
        self.pending: Optional[dict] = None          # resting Post-Only GTX entry
        self.buckets: Deque[list] = deque(maxlen=cfg.bucket_keep_s)   # [sec, long_usd, short_usd]

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
        self._bucket(ev, now)
        if ev['notional'] >= self.cfg.big_usd:
            self.feed.appendleft(ev)
        total, count = self.detector.add(ev, now)
        if total >= self.cfg.cascade_usd:
            return self._on_cascade(ev, total, count, now)
        return None

    def _bucket(self, ev: dict, now: float) -> None:
        sec = int(now)
        if not self.buckets or self.buckets[-1][0] != sec:
            self.buckets.append([sec, 0.0, 0.0])
        self.buckets[-1][1 if ev['liquidated'] == 'LONG' else 2] += ev['notional']

    def _on_cascade(self, ev: dict, total: float, count: int, now: float) -> Optional[dict]:
        sym, liq = ev['symbol'], ev['liquidated']
        cas = {'t': now, 'symbol': sym, 'liquidated': liq, 'usd_window': total, 'count': count,
               'move_pct': self.marks.move_pct(sym, now, self.cfg.drop_lookback_s, liq)}
        if self.cascades and self.cascades[0]['symbol'] == sym and now - self.cascades[0]['t'] < self.cfg.window_s:
            self.cascades[0].update(cas)                      # same burst: update in place
        else:
            self.cascades.appendleft(cas)
            self.stats['cascades'] += 1
        t0 = time.perf_counter()
        cas['jev'] = recovery_score(total, count, cas['move_pct'], self.cfg)
        us = (time.perf_counter() - t0) * 1e6
        cas['jev']['us'] = us
        self.stats['score_us_max'] = max(self.stats['score_us_max'], us)
        why = self._blocked(cas, now)
        cas['action'] = why or ('BUY' if liq == 'LONG' else 'SELL')
        if why:
            return cas
        self._open(cas, now)
        return cas

    def _blocked(self, cas: dict, now: float) -> Optional[str]:
        sym = cas['symbol']
        if not self.cfg.enabled:
            return 'OBSERVE'
        if self.pos is not None or self.pending is not None:
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
        j = cas.get('jev') or {}
        if j.get('us', 0.0) > self.cfg.score_budget_ms * 1000:
            return 'LATENCY'
        if j.get('reason') == 'TRENDBREAK':
            return 'TRENDBREAK'
        if j.get('score', 10.0) < self.cfg.min_score:
            return f'JEV {j.get("score", 0):.1f} < {self.cfg.min_score:g}'
        return None

    def _tp_for(self, cas: dict) -> float:
        c = self.cfg
        if not c.dynamic_tp:
            return c.tp_pct
        s = (cas.get('jev') or {}).get('score', 0.0)
        return round(c.tp_min_pct + (c.tp_max_pct - c.tp_min_pct) * min(max(s, 0.0), 10.0) / 10.0, 3)

    def _open(self, cas: dict, now: float) -> None:
        sym, c = cas['symbol'], self.cfg
        mark = self.marks.last(sym)
        side = 'BUY' if cas['liquidated'] == 'LONG' else 'SELL'
        d = 1 if side == 'BUY' else -1
        tp_pct = self._tp_for(cas)
        score = (cas.get('jev') or {}).get('score')
        if c.entry_mode == 'maker':
            limit = mark * (1 - d * c.maker_offset_bps / 1e4)   # Post-Only: rests beyond mark, never crosses
            p = plan(limit, self.specs[sym], c, tp_pct, entry_cost_bps=c.maker_bps)
            if p is None:
                self.stats['skipped'] += 1
                cas['action'] = 'SIZE'
                return
            self.pending = {'symbol': sym, 'side': side, 'limit': limit, 'posted': now, 'plan': p,
                            'cascade_usd': cas['usd_window'], 'move_pct': cas['move_pct'], 'score': score}
            self.cooldown[sym] = now + c.cooldown_s
            self.stats['maker_posted'] += 1
            cas['action'] = f'GTX {side}'
            self._event(now, f'GTX {sym} {side} {p["qty"]:g} @ {limit:g} post-only ttl {c.maker_ttl_s:g}s '
                             f'after ${cas["usd_window"]:,.0f} {cas["liquidated"]} liq · JEV {score or 0:.1f} '
                             f'· TP +{tp_pct}%')
            return
        entry = mark * (1 + d * c.slip_bps / 1e4)          # market order into the wick, slipped
        p = plan(entry, self.specs[sym], c, tp_pct)         # size + stop from the price actually paid
        if p is None:
            self.stats['skipped'] += 1
            cas['action'] = 'SIZE'
            return
        self._fill(sym, side, entry, p, now, cas['usd_window'], cas['move_pct'], mark, c.taker_bps, score)
        self.cooldown[sym] = now + c.cooldown_s

    def _fill(self, sym, side, entry, p, now, cascade_usd, move_pct, mark, entry_fee_bps, score) -> None:
        d = 1 if side == 'BUY' else -1
        self.pos = {'symbol': sym, 'side': side, 'entry': entry, 'qty': p['qty'], 'opened': now,
                    'tp': entry * (1 + d * p['tp_pct'] / 100.0), 'sl': entry * (1 - d * p['sl_pct'] / 100.0),
                    'plan': p, 'cascade_usd': cascade_usd, 'move_pct': move_pct, 'mark': mark,
                    'entry_fee_bps': entry_fee_bps, 'score': score,
                    'entry_mode': 'maker' if entry_fee_bps == self.cfg.maker_bps and self.cfg.entry_mode == 'maker'
                    else 'taker'}
        self.stats['entries'] += 1
        self._event(now, f'SNIPE {sym} {side} {p["qty"]:g} @ {entry:g} after ${cascade_usd:,.0f} '
                         f'liquidations, wick {move_pct or 0:.2f}% · TP +{p["tp_pct"]}% '
                         f'SL -{p["sl_pct"]:.2f}% · {p["leverage"]}x')

    def _work_pending(self, now: float) -> None:
        o = self.pending
        mark = self.marks.last(o['symbol'])
        d = 1 if o['side'] == 'BUY' else -1
        if mark is not None and d * (o['limit'] - mark) >= 0:   # mark traded THROUGH the resting order
            self.pending = None
            self.stats['maker_filled'] += 1
            self._fill(o['symbol'], o['side'], o['limit'], o['plan'], now, o['cascade_usd'], o['move_pct'],
                       mark, self.cfg.maker_bps, o['score'])
        elif now - o['posted'] >= self.cfg.maker_ttl_s:
            self.pending = None
            self.stats['maker_missed'] += 1
            self._event(now, f'GTX MISS {o["symbol"]} {o["side"]} @ {o["limit"]:g} - wick bounced before fill')

    # ---- exits
    def step(self, now: float) -> Optional[dict]:
        if self.pending is not None:
            self._work_pending(now)
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
        net_pct = gross - (p.get('entry_fee_bps', c.taker_bps) + fee) / 100.0
        t = {'symbol': p['symbol'], 'side': p['side'], 'entry': p['entry'], 'exit': exit_px, 'qty': p['qty'],
             'reason': reason, 'net_pct': net_pct, 'pnl': notional * net_pct / 100.0,
             'hold_s': now - p['opened'], 'cascade_usd': p['cascade_usd'], 'move_pct': p['move_pct'],
             'opened': p['opened'], 'closed': now, 'score': p.get('score'),
             'entry_mode': p.get('entry_mode', 'taker')}
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
        fees = (p.get('entry_fee_bps', c.taker_bps) + c.taker_bps) / 100
        return p['entry'] * p['qty'] * (d * (exit_px / p['entry'] - 1) * 100 - fees) / 100

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
                'marks_tracked': len(self.marks.hist),
                'pending': None if self.pending is None else
                {k: v for k, v in self.pending.items() if k != 'plan'} | {'age_s': now - self.pending['posted']},
                'liq_series': [b for b in self.buckets if now - b[0] <= self.cfg.bucket_keep_s]}


def main(argv=None):
    """Standalone sniper (shadow). Delegates to the fast-lane runner with Polymarket off."""
    import argparse
    ap = argparse.ArgumentParser(description='Flash liquidation sniper (SHADOW - zero orders)')
    ap.add_argument('--mode', choices=['shadow'], default='shadow',
                    help='shadow only: live routing must pass core consensus + VESKA AuthToken')
    _, rest = ap.parse_known_args(argv)
    from research import fast_runner
    fast_runner.main(['--no-pm', *rest])


if __name__ == '__main__':
    main()
