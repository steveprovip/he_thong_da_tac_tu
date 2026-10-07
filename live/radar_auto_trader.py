"""Radar auto-trader: research.alpha_radar signal -> ONE bracketed position.

Venues:
  shadow  simulated fills on real Binance public prices (no key, no money)
  demo    real orders on Binance DEMO (demo-fapi.binance.com) with the
          BINANCE_TESTNET_* key in .env.local. Mainnet is not reachable from
          this module: the venue refuses any REST host outside DEMO_HOSTS.

Lifecycle (one position max, account-wide):
  SCAN   every --scan-every s: alpha_radar.scan() on mainnet public data.
  PICK   surge >= 3x, CHASE false, move toward the trade <= +2 %, RSI 55-75
         (long) / 25-45 (short), plan tradeable, isolated 2-3x, margin <= 2.6 U.
  ENTRY  RETEST (default): post-only LIMIT at the pullback zone the radar computed
         (nearest support among breakout level / EMA20 / VWAP / Fib 38.2 inside a
         0.5-1.2 % pullback), alive --retest-ttl s (240), then cancelled - no chase.
         A partial fill cancels the rest and is protected at once.
         Also: MARKET, or GTX at the touch. Trend gate: 1h AND 4h close vs EMA50.
  PROTECT from the ACTUAL fill: STOP_MARKET closePosition placed FIRST.
         v3 (sl_mode='atr'): stop = 2.0 x ATR14(5m) (outside the wick noise that
         took 74-81 % of the old 0.8 % stops); loss incl. fees <= 0.15 U or the
         signal is skipped. Legacy sl_mode='money': loss capped at max_loss.
         If the stop cannot be placed the position is closed at market.
  TP     reduce-only LIMIT GTX, TP1 = max(4.5 %, 2.5 x stop) for half, TP2 =
         TP1 x 8/4.5 for the rest; one TP when the size cannot be split. After
         TP1 the stop moves to entry (placed before the old one is cancelled).
  EXIT   flat on the venue -> leftovers cancelled, realized PnL taken from
         userTrades (realizedPnl - commission), ledger row written.
  KILL   today's realized + this position's unrealized <= -kill (0.15 U), the dashboard
         EMERGENCY STOP, or Ctrl+C -> cancel all, close reduce-only, halt. A
         KILL row in the ledger keeps the trader halted for the rest of the
         local day across restarts (--reset-kill to override deliberately).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from research import alpha_radar as ar

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / 'data' / 'telemetry_state.json'
LEDGER_FILE = ROOT / 'data' / 'auto_trader_ledger.jsonl'
SHUTDOWN_FILE = ROOT / 'data' / 'shutdown_request.json'
DEMO_HOSTS = {'demo-fapi.binance.com', 'testnet.binancefuture.com'}
BENIGN_CANCEL = (-2011, -2013)
POST_ONLY_REJECT = -5022


@dataclass(frozen=True)
class TraderConfig:
    equity: float = 3.85
    max_loss: float = 0.15              # per trade, incl. fees + slippage (~3.9 % of 3.85 U)
    kill_loss: float = 0.15             # daily realized + open unrealized: one full stop ends the day
    max_margin: float = 2.6
    leverage_choices: Tuple[int, ...] = (2, 3)
    surge_min: float = 5.0
    taker_min: float = 0.60
    max_move_pct: float = 2.0
    rsi_lo: float = 55.0
    rsi_hi: float = 75.0
    entry: str = 'market'               # 'market' | 'retest' | 'gtx'  (146 real signals: retest filled
    #                                     25 % and those fills averaged -0.40 % vs -0.20 % market)
    gtx_ttl_s: float = 20.0
    retest_ttl_s: float = 240.0
    tp_pcts: Tuple[float, ...] = (4.5, 8.0)
    sl_mode: str = 'atr'                # 'atr' | 'money' (legacy fixed-loss stop)
    atr_sl_mult: float = 2.0
    rr_min: float = 2.5
    tp_split: float = 0.5
    breakeven_after_tp1: bool = True
    scan_every_s: float = 120.0
    poll_s: float = 2.0
    cooldown_s: float = 300.0
    max_hold_s: float = 12 * 3600.0
    taker_bps: float = 5.0
    maker_bps: float = 2.0
    slip_bps: float = 5.0

    def radar_config(self) -> ar.RadarConfig:
        return ar.RadarConfig(surge_min=self.surge_min, equity=self.equity, max_loss_usdt=self.max_loss,
                              leverage_choices=self.leverage_choices,
                              margin_frac=self.max_margin / self.equity, tp_pcts=self.tp_pcts,
                              taker_bps=self.taker_bps, maker_bps=self.maker_bps, slip_bps=self.slip_bps,
                              sl_mode=self.sl_mode, atr_sl_mult=self.atr_sl_mult, rr_min=self.rr_min,
                              taker_long=self.taker_min, taker_short=1.0 - self.taker_min)


# ----------------------------------------------------------------------------- pure helpers
def _dec(step: float) -> int:
    s = f'{step:.12f}'.rstrip('0')
    return len(s.split('.')[1]) if '.' in s else 0


def fmt(x: float, step: float) -> str:
    return f'{x:.{_dec(step)}f}'


def floor_step(x: float, step: float) -> float:
    return round(math.floor(round(x / step, 9)) * step, _dec(step))


def round_tick(x: float, tick: float, mode: str = 'nearest') -> float:
    q = x / tick
    n = math.ceil(round(q, 9)) if mode == 'up' else math.floor(round(q, 9)) if mode == 'down' else round(q)
    return round(n * tick, _dec(tick))


def eligible(pick: dict, cfg: TraderConfig) -> Tuple[bool, str]:
    long = pick.get('side') == 'LONG'
    rsi = pick.get('rsi')
    plan = pick.get('plan') or {}
    if (pick.get('surge') or 0) < cfg.surge_min:
        return False, f'SURGE<{cfg.surge_min:g}x'
    if pick.get('chase'):
        return False, 'CHASE'
    if (1 if long else -1) * (pick.get('ret_pct') or 0) > cfg.max_move_pct:
        return False, f'MOVE>{cfg.max_move_pct:g}%'
    lo, hi = (cfg.rsi_lo, cfg.rsi_hi) if long else (100 - cfg.rsi_hi, 100 - cfg.rsi_lo)
    if rsi is None or not lo <= rsi <= hi:
        return False, f'RSI {rsi if rsi is None else round(rsi)} ∉ [{lo:g},{hi:g}]'
    taker = pick.get('taker')
    if taker is not None and (taker < cfg.taker_min if long else taker > 1.0 - cfg.taker_min):
        return False, f'TAKER {taker:.2f}'
    if pick.get('trend_ok') is False:
        return False, 'TREND ' + ' '.join(f'{k}:{v}' for k, v in (pick.get('trend') or {}).items())
    if cfg.entry == 'retest' and not (pick.get('retest') or {}).get('price'):
        return False, 'NO_RETEST_ZONE'
    if not plan.get('tradeable'):
        return False, 'PLAN'
    if plan.get('leverage') not in cfg.leverage_choices or (plan.get('margin') or 99) > cfg.max_margin:
        return False, 'MARGIN'
    return True, 'OK'


def choose(radar: Optional[dict], cfg: TraderConfig, cooldown: Dict[str, float],
           now: float) -> Tuple[Optional[dict], List[str]]:
    rejects = []
    for p in (radar or {}).get('picks', []):
        if now < cooldown.get(p['symbol'], 0.0):
            rejects.append(f'{p["symbol"]}: COOLDOWN')
            continue
        ok, why = eligible(p, cfg)
        if ok:
            return p, rejects
        rejects.append(f'{p["symbol"]}: {why}')
    return None, rejects


def bracket(side: str, fill: float, qty: float, spec: dict, cfg: TraderConfig,
            entry_maker: bool = False, sl_pct: Optional[float] = None,
            tp_pcts: Optional[List[float]] = None) -> dict:
    """Bracket from the ACTUAL fill.
    sl_pct None -> legacy money cap (stop = whatever distance costs max_loss).
    sl_pct given (ATR plan) -> that distance; raises if it would cost > max_loss."""
    tick, step = spec['tick'], spec['step']
    d = 1 if side == 'BUY' else -1
    notional = fill * qty
    cost_pct = ((cfg.maker_bps if entry_maker else cfg.taker_bps) + cfg.taker_bps + cfg.slip_bps) / 100.0
    if sl_pct is None:
        sl_pct = cfg.max_loss / notional * 100.0 - cost_pct
    elif notional * (sl_pct + cost_pct) / 100.0 > cfg.max_loss * 1.02:     # 2 % slack for fill drift
        raise ValueError(f'ATR stop {sl_pct:.2f}% costs {notional * (sl_pct + cost_pct) / 100:.3f} U '
                         f'> {cfg.max_loss} U')
    if sl_pct <= 0:
        raise ValueError('position too large for the loss cap')
    # round the stop TOWARD the entry: a tighter stop never breaks the cap
    sl = round_tick(fill * (1 - d * sl_pct / 100.0), tick, 'up' if d > 0 else 'down')
    tps = [round_tick(fill * (1 + d * tp / 100.0), tick) for tp in (tp_pcts or cfg.tp_pcts)]
    q1 = floor_step(qty * cfg.tp_split, step)
    q2 = round(qty - q1, _dec(step))
    legs = ([(tps[0], q1), (tps[-1], q2)] if len(tps) > 1 and q1 >= step and q2 >= step
            else [(tps[0], qty)])
    return {'sl': sl, 'sl_pct': sl_pct, 'tps': legs,
            'worst_loss': notional * (abs(fill - sl) / fill + cost_pct / 100.0)}


# ----------------------------------------------------------------------------- ledger
class Ledger:
    def __init__(self, path: Path, mode: str):
        self.path, self.mode = path, mode

    def rows(self) -> List[dict]:
        try:
            lines = self.path.read_text(encoding='utf-8').splitlines()
        except OSError:
            return []
        out = []
        for ln in lines:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if r.get('mode') == self.mode:
                out.append(r)
        return out

    def write(self, kind: str, **payload) -> dict:
        row = {'t': time.time(), 'day': datetime.now().strftime('%Y-%m-%d'), 'mode': self.mode,
               'kind': kind, **payload}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(row) + '\n')
        return row

    def today(self) -> Tuple[float, bool, List[dict]]:
        """(realized pnl today, halted today, trades today). A RESET clears earlier rows."""
        day = datetime.now().strftime('%Y-%m-%d')
        rows = [r for r in self.rows() if r.get('day') == day]
        last_reset = max((i for i, r in enumerate(rows) if r['kind'] == 'RESET'), default=-1)
        rows = rows[last_reset + 1:]
        trades = [r for r in rows if r['kind'] == 'TRADE']
        return (sum(r.get('pnl', 0.0) for r in trades), any(r['kind'] == 'KILL' for r in rows), trades)


# ----------------------------------------------------------------------------- venues
class DemoVenue:
    """Binance DEMO futures through live.binance_futures_client (never mainnet)."""
    name = 'demo'

    def __init__(self, client):
        host = urlsplit(client.settings.rest).hostname
        if client.settings.mode != 'testnet' or host not in DEMO_HOSTS:
            raise PermissionError(f'auto-trader only trades Binance demo/testnet, not {host}')
        self.c = client

    async def start(self) -> None:
        await self.c.sync_time()
        dual = await self.c.request('GET', '/fapi/v1/positionSide/dual', signed=True)
        if dual.get('dualSidePosition'):
            raise PermissionError('switch the demo account to one-way mode (Hedge Mode off)')

    async def specs(self) -> Dict[str, dict]:
        return ar.parse_specs(await self.c.exchange_info())

    async def exposure(self) -> List[str]:
        pos = await self.c.positions()
        orders = await self.c.request('GET', '/fapi/v1/openOrders', signed=True, weight=40)
        return sorted({p['symbol'] for p in pos if float(p.get('positionAmt', 0))}
                      | {o['symbol'] for o in orders})

    async def available(self) -> Optional[float]:
        acc = await self.c.account()
        return float(acc.get('availableBalance', 0))

    async def prepare(self, symbol: str, leverage: int) -> None:
        from live.binance_futures_client import BinanceError
        try:
            await self.c.request('POST', '/fapi/v1/marginType', {'symbol': symbol, 'marginType': 'ISOLATED'},
                                 signed=True)
        except BinanceError as e:
            if e.code != -4046:                      # already isolated
                raise
        res = await self.c.request('POST', '/fapi/v1/leverage', {'symbol': symbol, 'leverage': leverage},
                                   signed=True)
        if int(res.get('leverage', 0)) != leverage:
            raise RuntimeError('leverage not confirmed')

    async def book(self, symbol: str) -> Tuple[float, float]:
        d = await self.c.depth(symbol)
        return float(d['bids'][0][0]), float(d['asks'][0][0])

    async def market(self, symbol: str, side: str, qty: str, cid: str) -> Tuple[float, float]:
        r = await self.c.request('POST', '/fapi/v1/order', {
            'symbol': symbol, 'side': side, 'type': 'MARKET', 'quantity': qty, 'positionSide': 'BOTH',
            'newClientOrderId': cid, 'newOrderRespType': 'RESULT'}, signed=True)
        if r.get('status') != 'FILLED':
            r = await self.c.order(symbol, cid)
        fill = float(r.get('avgPrice') or 0)
        filled = float(r.get('executedQty') or 0)
        if fill <= 0 and filled > 0:
            cum_quote = float(r.get('cumQuote') or 0)
            if cum_quote > 0:
                fill = cum_quote / filled
            else:
                p = await self.position(symbol)
                fill = p.get('entry') or 0.0
                if fill <= 0:
                    bid, ask = await self.book(symbol)
                    fill = ask if side == 'BUY' else bid
        return fill, filled

    async def limit_gtx(self, symbol: str, side: str, qty: str, price: str, cid: str) -> None:
        await self.c.request('POST', '/fapi/v1/order', {
            'symbol': symbol, 'side': side, 'type': 'LIMIT', 'timeInForce': 'GTX', 'quantity': qty,
            'price': price, 'positionSide': 'BOTH', 'newClientOrderId': cid}, signed=True)

    async def order(self, symbol: str, cid: str) -> dict:
        r = await self.c.order(symbol, cid)
        return {'status': r.get('status'), 'filled': float(r.get('executedQty') or 0),
                'avg': float(r.get('avgPrice') or 0)}

    async def cancel(self, symbol: str, cid: str) -> None:
        from live.binance_futures_client import BinanceError
        try:
            await self.c.cancel(symbol, cid)
        except BinanceError as e:
            if e.uncertain or e.code not in BENIGN_CANCEL:
                raise

    async def take_profit(self, symbol: str, side: str, qty: str, price: str, cid: str) -> bool:
        from live.binance_futures_client import BinanceError
        try:
            await self.c.reduce_maker(symbol, side, qty, price, cid)
            return True
        except BinanceError as e:
            if e.code == POST_ONLY_REJECT:           # already through the target: caller takes it
                return False
            raise

    async def stop(self, symbol: str, side: str, stop: str, cid: str) -> None:
        r = await self.c.protect(symbol, side, stop, cid)
        if r.get('algoStatus', 'NEW') not in ('NEW', 'WORKING', 'ACCEPTED'):
            raise RuntimeError(f'stop not working: {r.get("algoStatus")}')

    async def stop_live(self, symbol: str, cid: str) -> bool:
        return any(a.get('clientAlgoId') == cid for a in await self.c.open_algos(symbol))

    async def cancel_stop(self, cid: str) -> None:
        from live.binance_futures_client import BinanceError
        try:
            await self.c.cancel_algo(cid)
        except BinanceError as e:
            if e.uncertain or e.code not in BENIGN_CANCEL:
                raise

    async def position(self, symbol: str) -> dict:
        rows = await self.c.positions(symbol)
        amt = sum(float(p.get('positionAmt', 0)) for p in rows)
        p = rows[0] if rows else {}
        return {'amt': amt, 'entry': float(p.get('entryPrice') or 0), 'mark': float(p.get('markPrice') or 0),
                'upnl': sum(float(x.get('unRealizedProfit', 0)) for x in rows)}

    async def realized(self, symbol: str, start_ms: int) -> float:
        trades, _ = await self.c.history_since(symbol, start_ms)
        return sum(float(t.get('realizedPnl', 0)) - (float(t.get('commission', 0))
                   if t.get('commissionAsset', 'USDT') == 'USDT' else 0.0) for t in trades)

    async def close(self, symbol: str, amt: float, step: float) -> None:
        await self.c.reduce_position(symbol, 'SELL' if amt > 0 else 'BUY', fmt(abs(amt), step),
                                     'rk-' + uuid.uuid4().hex[:28])

    async def flatten(self, symbol: str) -> None:
        from live.flatten import flatten_venue
        await flatten_venue(self.c, [symbol])


class ShadowVenue:
    """In-memory exchange on real public quotes: taker in/out with slippage, maker TPs."""
    name = 'shadow'

    def __init__(self, cfg: TraderConfig, quote: Optional[Callable[[str], Tuple[float, float]]] = None,
                 http: Callable = ar._get_json):
        self.cfg, self.http = cfg, http
        self.quote = quote or self._public_quote
        self.amt: Dict[str, float] = {}
        self.entry: Dict[str, float] = {}
        self.orders: Dict[str, dict] = {}          # cid -> {symbol, side, qty, price, kind, status}
        self.pnl: List[Tuple[str, float, float]] = []   # (symbol, t_ms, net usdt)
        self.fills: List[dict] = []

    def _public_quote(self, symbol: str) -> Tuple[float, float]:
        r = self.http(f'{ar.FAPI}/fapi/v1/ticker/bookTicker?symbol={symbol}')
        return float(r['bidPrice']), float(r['askPrice'])

    async def start(self) -> None:
        return None

    async def specs(self) -> Dict[str, dict]:
        return ar.parse_specs(await asyncio.to_thread(self.http, f'{ar.FAPI}/fapi/v1/exchangeInfo'))

    async def exposure(self) -> List[str]:
        return sorted({s for s, a in self.amt.items() if a} |
                      {o['symbol'] for o in self.orders.values() if o['status'] == 'NEW'})

    async def available(self) -> Optional[float]:
        return None

    async def prepare(self, symbol: str, leverage: int) -> None:
        return None

    async def book(self, symbol: str) -> Tuple[float, float]:
        return self.quote(symbol)

    def _fill(self, symbol: str, side: str, qty: float, price: float, fee_bps: float) -> None:
        d = 1 if side == 'BUY' else -1
        amt = self.amt.get(symbol, 0.0)
        fee = price * qty * fee_bps / 1e4
        pnl = -fee
        if amt == 0 or (amt > 0) == (d > 0):         # open / add
            new = amt + d * qty
            self.entry[symbol] = (self.entry.get(symbol, 0) * abs(amt) + price * qty) / abs(new)
            self.amt[symbol] = new
        else:                                         # reduce
            closing = min(qty, abs(amt))
            pnl += (price - self.entry[symbol]) * closing * (1 if amt > 0 else -1)
            self.amt[symbol] = round(amt + d * closing, 12)
        self.pnl.append((symbol, time.time() * 1000, pnl))
        self.fills.append({'symbol': symbol, 'side': side, 'qty': qty, 'price': price, 'pnl': pnl})

    async def market(self, symbol: str, side: str, qty: str, cid: str) -> Tuple[float, float]:
        bid, ask = self.quote(symbol)
        slip = self.cfg.slip_bps / 1e4
        px = ask * (1 + slip) if side == 'BUY' else bid * (1 - slip)
        self._fill(symbol, side, float(qty), px, self.cfg.taker_bps)
        self.orders[cid] = {'symbol': symbol, 'side': side, 'qty': float(qty), 'price': px,
                            'kind': 'MARKET', 'status': 'FILLED'}
        return px, float(qty)

    async def limit_gtx(self, symbol: str, side: str, qty: str, price: str, cid: str) -> None:
        self.orders[cid] = {'symbol': symbol, 'side': side, 'qty': float(qty), 'price': float(price),
                            'kind': 'ENTRY', 'status': 'NEW'}

    async def order(self, symbol: str, cid: str) -> dict:
        self._match(symbol)
        o = self.orders.get(cid, {})
        return {'status': o.get('status'), 'filled': o.get('qty', 0.0) if o.get('status') == 'FILLED' else 0.0,
                'avg': o.get('price', 0.0)}

    async def cancel(self, symbol: str, cid: str) -> None:
        if cid in self.orders and self.orders[cid]['status'] == 'NEW':
            self.orders[cid]['status'] = 'CANCELED'

    async def take_profit(self, symbol: str, side: str, qty: str, price: str, cid: str) -> bool:
        bid, ask = self.quote(symbol)
        if (side == 'SELL' and bid >= float(price)) or (side == 'BUY' and ask <= float(price)):
            return False
        self.orders[cid] = {'symbol': symbol, 'side': side, 'qty': float(qty), 'price': float(price),
                            'kind': 'TP', 'status': 'NEW'}
        return True

    async def stop(self, symbol: str, side: str, stop: str, cid: str) -> None:
        self.orders[cid] = {'symbol': symbol, 'side': side, 'qty': None, 'price': float(stop),
                            'kind': 'SL', 'status': 'NEW'}

    async def stop_live(self, symbol: str, cid: str) -> bool:
        self._match(symbol)
        return self.orders.get(cid, {}).get('status') == 'NEW'

    async def cancel_stop(self, cid: str) -> None:
        if cid in self.orders and self.orders[cid]['status'] == 'NEW':
            self.orders[cid]['status'] = 'CANCELED'

    def _match(self, symbol: str) -> None:
        bid, ask = self.quote(symbol)
        slip = self.cfg.slip_bps / 1e4
        for cid, o in list(self.orders.items()):
            if o['symbol'] != symbol or o['status'] != 'NEW':
                continue
            amt = self.amt.get(symbol, 0.0)
            if o['kind'] == 'ENTRY':
                if (o['side'] == 'BUY' and (ask <= o['price'] or bid < o['price'])) or \
                        (o['side'] == 'SELL' and (bid >= o['price'] or ask > o['price'])):
                    o['status'] = 'FILLED'
                    self._fill(symbol, o['side'], o['qty'], o['price'], self.cfg.maker_bps)
            elif o['kind'] == 'SL' and amt:
                if (o['side'] == 'SELL' and bid <= o['price']) or (o['side'] == 'BUY' and ask >= o['price']):
                    px = min(bid, o['price']) * (1 - slip) if o['side'] == 'SELL' else max(ask, o['price']) * (1 + slip)
                    o['status'] = 'FILLED'
                    self._fill(symbol, o['side'], abs(amt), px, self.cfg.taker_bps)
            elif o['kind'] == 'TP' and amt:
                if (o['side'] == 'SELL' and bid >= o['price']) or (o['side'] == 'BUY' and ask <= o['price']):
                    o['status'] = 'FILLED'
                    self._fill(symbol, o['side'], min(o['qty'], abs(amt)), o['price'], self.cfg.maker_bps)
            if not self.amt.get(symbol) and o['kind'] in ('TP', 'SL') and o['status'] == 'NEW':
                o['status'] = 'EXPIRED'                 # reduce-only / closePosition die with the position

    async def position(self, symbol: str) -> dict:
        self._match(symbol)
        bid, ask = self.quote(symbol)
        amt = self.amt.get(symbol, 0.0)
        e = self.entry.get(symbol, 0.0)
        mark = (bid + ask) / 2
        return {'amt': amt, 'entry': e, 'mark': mark, 'upnl': (mark - e) * amt if amt else 0.0}

    async def realized(self, symbol: str, start_ms: int) -> float:
        return sum(p for s, t, p in self.pnl if s == symbol and t >= start_ms)

    async def close(self, symbol: str, amt: float, step: float) -> None:
        await self.market(symbol, 'SELL' if amt > 0 else 'BUY', str(abs(amt)), 'rk-' + uuid.uuid4().hex[:8])

    async def flatten(self, symbol: str) -> None:
        for o in self.orders.values():
            if o['symbol'] == symbol and o['status'] == 'NEW':
                o['status'] = 'CANCELED'
        amt = self.amt.get(symbol, 0.0)
        if amt:
            await self.close(symbol, amt, 1e-12)


# ----------------------------------------------------------------------------- trader
class AutoTrader:
    def __init__(self, venue, cfg: TraderConfig, radar_fn: Callable[[], dict], ledger: Ledger,
                 state_file: Path = STATE_FILE, shutdown_file: Path = SHUTDOWN_FILE,
                 clock: Callable[[], float] = time.time):
        self.v, self.cfg, self.radar_fn, self.ledger = venue, cfg, radar_fn, ledger
        self.state_file, self.shutdown_file, self.clock = state_file, shutdown_file, clock
        self.specs: Dict[str, dict] = {}
        self.pos: Optional[dict] = None
        self.pending: Optional[dict] = None
        self.cooldown: Dict[str, float] = {}
        self.next_scan = 0.0
        self.last_radar: Optional[dict] = None
        self.rejects: List[str] = []
        self.events: deque = deque(maxlen=40)
        self.history: deque = deque(maxlen=600)
        self.trades: List[dict] = []
        self.halted, self.halt_reason = False, ''
        self.started = clock()
        self.last_status = 'BOOT'

    # ---- helpers
    def event(self, msg: str) -> None:
        self.events.appendleft({'t': self.clock(), 'message': msg[:240]})
        print(f'[{time.strftime("%H:%M:%S")}] {msg}')

    def _cid(self, tag: str) -> str:
        return f'ra{tag}-{uuid.uuid4().hex[:20]}'

    def today(self) -> Tuple[float, bool, List[dict]]:
        return self.ledger.today()

    async def boot(self) -> None:
        await self.v.start()
        self.specs = await self.v.specs()
        pnl, killed, _ = self.today()
        if killed or pnl <= -self.cfg.kill_loss:
            self.halted, self.halt_reason = True, (f'daily kill already hit ({pnl:+.4f} U today); '
                                                   f'run with --reset-kill to override')
        ex = await self.v.exposure()
        if ex:
            self.halted, self.halt_reason = True, (f'existing exposure on {", ".join(ex)}: close it on '
                                                   f'the demo site (or tools/shutdown_system.py) first')
        self.event(f'BOOT {self.v.name}: {len(self.specs)} symbols · today {pnl:+.4f} U'
                   + (f' · HALTED: {self.halt_reason}' if self.halted else ''))

    # ---- one cycle
    async def step(self) -> str:
        now = self.clock()
        if self.shutdown_file is not None and _mtime(self.shutdown_file) >= self.started:
            await self.kill('dashboard EMERGENCY STOP', latch=False)
            return 'SHUTDOWN'
        if self.pending is not None:
            await self.poll_pending(now)
        elif self.pos is not None:
            await self.manage(now)
        elif not self.halted and now >= self.next_scan:
            self.next_scan = now + self.cfg.scan_every_s
            try:
                self.last_radar = await asyncio.to_thread(self.radar_fn)
            except Exception as exc:
                self.event(f'SCAN failed: {type(exc).__name__}: {exc}'[:200])
                self.last_radar = None
            pick, self.rejects = choose(self.last_radar, self.cfg, self.cooldown, now)
            if self.rejects:
                self.event('SKIP ' + ' | '.join(self.rejects[:3]))
            if pick is not None:
                await self.open(pick)
        self.last_status = ('HALTED' if self.halted and not (self.pos or self.pending)
                            else f'ENTRY_PENDING {self.pending["symbol"]}' if self.pending
                            else f'IN_POSITION {self.pos["symbol"]}' if self.pos else 'SCANNING')
        self.publish()
        return self.last_status

    async def open(self, pick: dict) -> None:
        sym, cfg = pick['symbol'], self.cfg
        spec = self.specs.get(sym)
        if spec is None:
            self.cooldown[sym] = self.clock() + cfg.cooldown_s
            self.event(f'SKIP {sym}: not listed on {self.v.name}')
            return
        ex = await self.v.exposure()
        if ex:
            self.event(f'SKIP {sym}: exposure already open on {", ".join(ex)} (max 1 position)')
            return
        side = 'BUY' if pick['side'] == 'LONG' else 'SELL'
        d = 1 if side == 'BUY' else -1
        # --- entry price: retest zone (default), touch (gtx) or market
        limit = None
        if cfg.entry in ('retest', 'gtx'):
            bid, ask = await self.v.book(sym)
            touch = bid if side == 'BUY' else ask
            if cfg.entry == 'retest':
                zone = round_tick(pick['retest']['price'], spec['tick'], 'down' if d > 0 else 'up')
                # already traded into the zone: rest at the touch instead (post-only must not cross)
                limit = min(zone, touch) if d > 0 else max(zone, touch)
            else:
                limit = touch
        plan_px = limit if limit is not None else pick['price']
        plan = ar.trade_plan(plan_px, pick['side'], spec, cfg.radar_config(), atr=pick.get('atr_pct'),
                             entry_maker=limit is not None)
        if not plan['tradeable'] or plan['leverage'] not in cfg.leverage_choices or plan['margin'] > cfg.max_margin:
            self.event(f'SKIP {sym}: plan not within 2-3x / 2.6 U margin')
            return
        avail = await self.v.available()
        if avail is not None and avail < plan['margin'] * 1.1:
            self.event(f'SKIP {sym}: available {avail:.2f} U < margin {plan["margin"]:.2f} U')
            return
        qty_s = fmt(plan['qty'], spec['step'])
        try:
            await self.v.prepare(sym, plan['leverage'])
        except Exception as exc:
            self.cooldown[sym] = self.clock() + cfg.cooldown_s
            self.event(f'SKIP {sym}: prepare failed {type(exc).__name__}: {exc}'[:200])
            return
        start_ms = int(self.clock() * 1000) - 1000
        entry_cid = self._cid('e')
        signal = {k: pick.get(k) for k in ('surge', 'window_min', 'ret_pct', 'rsi', 'taker', 'obi', 'score',
                                           'trend', 'price')}
        if limit is None:
            self.event(f'ENTRY {sym} {side} {qty_s} MARKET {plan["leverage"]}x · surge {pick["surge"]:.1f}x '
                       f'RSI {pick["rsi"]:.0f} move {pick["ret_pct"]:+.2f}%')
            fill, filled = await self.v.market(sym, side, qty_s, entry_cid)
            if fill <= 0 and filled > 0:
                p = await self.v.position(sym)
                fill = p.get('entry') or (await self.v.book(sym))[0]
            if filled <= 0:
                self.cooldown[sym] = self.clock() + cfg.cooldown_s
                self.event(f'CANCEL {sym}: market order not filled')
                return
            await self._protect(sym, side, fill, filled, plan, signal, start_ms, entry_maker=False)
            return
        try:
            await self.v.limit_gtx(sym, side, qty_s, fmt(limit, spec['tick']), entry_cid)
        except Exception as exc:
            self.event(f'ENTRY {sym} post-only rejected: {exc}'[:200])
            self.cooldown[sym] = self.clock() + cfg.cooldown_s
            return
        ttl = cfg.retest_ttl_s if cfg.entry == 'retest' else cfg.gtx_ttl_s
        pull = d * (pick['price'] - limit) / pick['price'] * 100.0
        self.pending = {'symbol': sym, 'side': side, 'cid': entry_cid, 'price': limit, 'qty': plan['qty'],
                        'plan': plan, 'signal': signal, 'start_ms': start_ms, 'placed': self.clock(),
                        'deadline': self.clock() + ttl, 'ttl': ttl, 'pullback_pct': pull,
                        'anchor': (pick.get('retest') or {}).get('anchor', 'touch') if cfg.entry == 'retest' else 'touch'}
        self.event(f'{cfg.entry.upper()} {sym} {side} {qty_s} @ {limit:g} (-{pull:.2f}% vs signal {pick["price"]:g}, '
                   f'{self.pending["anchor"]}) · post-only · TTL {ttl:.0f}s · {plan["leverage"]}x '
                   f'· surge {pick["surge"]:.1f}x RSI {pick["rsi"]:.0f}')

    async def poll_pending(self, now: float) -> None:
        """Resting entry: FILLED -> protect. Partial -> cancel rest, protect what filled.
        TTL / venue cancel -> drop it (price never pulled back: no trade, no loss)."""
        pe = self.pending
        sym = pe['symbol']
        st = await self.v.order(sym, pe['cid'])
        done = st['status'] in ('CANCELED', 'EXPIRED', 'REJECTED', 'EXPIRED_IN_MATCH')
        if st['status'] != 'FILLED' and (st['filled'] > 0 or now >= pe['deadline'] or done):
            await self.v.cancel(sym, pe['cid'])
            st = await self.v.order(sym, pe['cid'])
        elif st['status'] != 'FILLED':
            return                                             # still resting
        self.pending = None
        if st['filled'] <= 0:
            self.cooldown[sym] = self.clock() + self.cfg.cooldown_s
            self.event(f'NO RETEST {sym}: price never came back to {pe["price"]:g} in {pe["ttl"]:.0f}s '
                       f'-> cancelled (no chase, 0 loss)')
            return
        fill = st['avg'] or pe['price']
        await self._protect(sym, pe['side'], fill, st['filled'], pe['plan'], pe['signal'], pe['start_ms'],
                            entry_maker=True)

    async def _protect(self, sym: str, side: str, fill: float, filled: float, plan: dict, signal: dict,
                       start_ms: int, entry_maker: bool) -> None:
        cfg, spec = self.cfg, self.specs[sym]
        close_side = 'SELL' if side == 'BUY' else 'BUY'
        try:
            b = bracket(side, fill, filled, spec, cfg, entry_maker=entry_maker,
                        sl_pct=plan['sl_pct'] if cfg.sl_mode == 'atr' else None,
                        tp_pcts=[t['tp_pct'] for t in plan['tps']])
        except ValueError as exc:
            self.event(f'RISK {sym}: {exc} -> closing at market'[:200])
            self.pos = {'symbol': sym, 'side': side, 'qty': filled, 'entry': fill, 'start_ms': start_ms,
                        'opened': self.clock(), 'sl_cid': None, 'tps': [], 'signal': signal}
            await self.v.close(sym, filled if side == 'BUY' else -filled, spec['step'])
            await self._settle('RISK')
            return
        self.pos = {'symbol': sym, 'side': side, 'qty': filled, 'entry': fill, 'start_ms': start_ms,
                    'opened': self.clock(), 'sl': b['sl'], 'sl_pct': b['sl_pct'], 'sl_cid': self._cid('s'),
                    'tps': [], 'be': False, 'leverage': plan['leverage'], 'margin': fill * filled / plan['leverage'],
                    'entry_mode': cfg.entry if entry_maker else 'market', 'signal': signal}
        try:
            await self.v.stop(sym, close_side, fmt(b['sl'], spec['tick']), self.pos['sl_cid'])
        except Exception as exc:
            self.event(f'STOP FAILED {sym}: {exc} -> closing at market'[:200])
            await self.v.close(sym, filled if side == 'BUY' else -filled, spec['step'])
            await self._settle('NO_STOP')
            return
        for i, (px, q) in enumerate(b['tps']):
            cid = self._cid(f't{i + 1}')
            ok = await self.v.take_profit(sym, close_side, fmt(q, spec['step']), fmt(px, spec['tick']), cid)
            if ok:
                self.pos['tps'].append({'cid': cid, 'price': px, 'qty': q, 'filled': False})
        self.event(f'FILLED {sym} {side} {filled:g} @ {fill:g} ({self.pos["entry_mode"]}) · SL {b["sl"]:g} '
                   f'(-{b["sl_pct"]:.2f}%, max -{b["worst_loss"]:.3f} U) · TP '
                   + ' / '.join(f'{p:g}×{q:g}' for p, q in b['tps']))
        if not self.pos['tps']:
            self.event(f'TP {sym}: price already beyond +{cfg.tp_pcts[0]:g}% -> closing at market')
            self.pos['exit_reason'] = 'TP_MARKET'
            await self.v.close(sym, filled if side == 'BUY' else -filled, spec['step'])

    async def manage(self, now: float) -> None:
        p, cfg = self.pos, self.cfg
        sym, spec = p['symbol'], self.specs[p['symbol']]
        st = await self.v.position(sym)
        p['mark'], p['upnl'], p['amt'] = st['mark'], st['upnl'], st['amt']
        if abs(st['amt']) < spec['step'] / 2:
            await self._settle(None)
            return
        realized_open = await self.v.realized(sym, p['start_ms'])
        day_pnl, _, _ = self.today()
        p['realized'] = realized_open
        if day_pnl + realized_open + st['upnl'] <= -cfg.kill_loss:
            await self.kill(f'loss {day_pnl + realized_open + st["upnl"]:+.4f} U <= -{cfg.kill_loss} U')
            return
        close_side = 'SELL' if p['side'] == 'BUY' else 'BUY'
        for tp in p['tps']:
            if not tp['filled'] and (await self.v.order(sym, tp['cid']))['status'] == 'FILLED':
                tp['filled'] = True
                self.event(f'TP HIT {sym} {tp["qty"]:g} @ {tp["price"]:g}')
        if cfg.breakeven_after_tp1 and not p['be'] and p['tps'] and p['tps'][0]['filled']:
            new_cid = self._cid('b')
            try:
                await self.v.stop(sym, close_side, fmt(p['entry'], spec['tick']), new_cid)
                await self.v.cancel_stop(p['sl_cid'])
                p['sl_cid'], p['sl'], p['be'] = new_cid, p['entry'], True
                self.event(f'BREAKEVEN {sym}: stop -> entry {p["entry"]:g}')
            except Exception as exc:
                self.event(f'BREAKEVEN {sym} failed ({exc}); original stop kept'[:200])
        if not await self.v.stop_live(sym, p['sl_cid']):
            st = await self.v.position(sym)
            if abs(st['amt']) < spec['step'] / 2:
                await self._settle(None)
                return
            cid = self._cid('r')
            try:
                await self.v.stop(sym, close_side, fmt(p['sl'], spec['tick']), cid)
                p['sl_cid'] = cid
                self.event(f'RE-PROTECT {sym}: stop was missing, replaced @ {p["sl"]:g}')
            except Exception as exc:
                self.event(f'RE-PROTECT {sym} failed ({exc}) -> closing'[:200])
                await self.v.close(sym, st['amt'], spec['step'])
        if now - p['opened'] >= cfg.max_hold_s:
            self.event(f'TIME STOP {sym}: {cfg.max_hold_s / 3600:g} h')
            p['exit_reason'] = 'TIME'
            await self.v.close(sym, st['amt'], spec['step'])

    async def _settle(self, reason: Optional[str]) -> None:
        p = self.pos
        sym = p['symbol']
        n_tp = 0
        for tp in p['tps']:                       # read fill state first, then clean up
            if tp['filled'] or (await self.v.order(sym, tp['cid']))['status'] == 'FILLED':
                tp['filled'] = True
                n_tp += 1
            else:
                await self.v.cancel(sym, tp['cid'])
        if p.get('sl_cid'):
            await self.v.cancel_stop(p['sl_cid'])
        pnl = await self.v.realized(sym, p['start_ms'])
        reason = reason or p.get('exit_reason')
        if reason is None:
            reason = ('TP_ALL' if p['tps'] and n_tp == len(p['tps']) else
                      'TP1+BE' if n_tp else 'STOP' if pnl < 0 else 'EXIT')
        row = {'symbol': sym, 'side': p['side'], 'qty': p['qty'], 'entry': p['entry'], 'pnl': pnl,
               'reason': reason, 'opened': p['opened'], 'closed': self.clock(), 'signal': p['signal']}
        self.ledger.write('TRADE', **row)
        self.trades.insert(0, row)
        self.cooldown[sym] = self.clock() + self.cfg.cooldown_s
        self.pos = None
        self.event(f'CLOSED {sym} {reason} {pnl:+.4f} U ({pnl * ar.USDT_VND:+,.0f} đ)')
        day_pnl, _, _ = self.today()
        if day_pnl <= -self.cfg.kill_loss:
            await self.kill(f'daily realized {day_pnl:+.4f} U <= -{self.cfg.kill_loss} U')

    async def kill(self, reason: str, latch: bool = True) -> None:
        """Flatten + halt. latch=True (loss kills) keeps the day halted across restarts."""
        if self.pending is not None:
            pe, self.pending = self.pending, None
            await self.v.cancel(pe['symbol'], pe['cid'])
            st = await self.v.order(pe['symbol'], pe['cid'])
            if st['filled'] > 0:                               # filled in the race: close it too
                self.pos = {'symbol': pe['symbol'], 'side': pe['side'], 'qty': st['filled'],
                            'entry': st['avg'] or pe['price'], 'start_ms': pe['start_ms'],
                            'opened': self.clock(), 'signal': pe['signal']}
        if self.pos is not None:
            sym = self.pos['symbol']
            try:
                await self.v.flatten(sym)
            finally:
                pnl = await self.v.realized(sym, self.pos['start_ms'])
                self.ledger.write('TRADE', symbol=sym, side=self.pos['side'], qty=self.pos['qty'],
                                  entry=self.pos['entry'], pnl=pnl, reason='KILL', opened=self.pos['opened'],
                                  closed=self.clock(), signal=self.pos['signal'])
                self.trades.insert(0, {'symbol': sym, 'side': self.pos['side'], 'pnl': pnl, 'reason': 'KILL',
                                       'entry': self.pos['entry'], 'qty': self.pos['qty'],
                                       'opened': self.pos['opened'], 'closed': self.clock()})
                self.pos = None
        if latch and not self.halted:
            self.ledger.write('KILL', reason=reason)
        self.halted, self.halt_reason = True, reason
        self.event(f'KILL: {reason} -> orders cancelled, position closed, trading halted')
        self.publish()

    # ---- telemetry (same schema the dashboard already renders)
    def snapshot(self) -> dict:
        now = self.clock()
        day_pnl, _, day_trades = self.today()
        p = self.pos
        open_pnl = ((p.get('realized') or 0.0) + (p.get('upnl') or 0.0)) if p else 0.0
        eq = self.cfg.equity + day_pnl + open_pnl
        self.history.append({'t': now, 'value': round(eq, 6)})
        positions, orders = [], []
        if p:
            d = 1 if p['side'] == 'BUY' else -1
            mark = p.get('mark') or p['entry']
            positions.append({'symbol': p['symbol'], 'branch': self.v.name, 'side': p['side'], 'size': p['qty'],
                              'entry': p['entry'], 'tp': p['tps'][-1]['price'] if p['tps'] else None, 'sl': p['sl'],
                              'pnl_bps': d * (mark / p['entry'] - 1) * 1e4, 'protected': p['be'],
                              'age_s': now - p['opened'], 'max_hold_s': self.cfg.max_hold_s})
            orders = ([{'symbol': p['symbol'], 'kind': 'STOP_MARKET', 'price': p['sl'], 'cid': p['sl_cid']}]
                      + [{'symbol': p['symbol'], 'kind': f'TP{i + 1} LIMIT GTX', 'price': t['price'], 'qty': t['qty'],
                          'status': 'FILLED' if t['filled'] else 'NEW'} for i, t in enumerate(p['tps'])])
        if self.pending:
            pe = self.pending
            positions.insert(0, {'symbol': pe['symbol'], 'branch': 'retest', 'side': pe['side'], 'size': pe['qty'],
                                 'entry': pe['price'], 'pending': True, 'age_s': now - pe['placed'],
                                 'ttl_s': pe['ttl'], 'queue_ahead': None})
            orders.insert(0, {'symbol': pe['symbol'], 'kind': f'ENTRY LIMIT GTX ({pe["anchor"]}, '
                                                        f'-{pe["pullback_pct"]:.2f}%)',
                              'price': pe['price'], 'qty': pe['qty']})
        radar = self.last_radar or {}
        markets = [{'symbol': r['symbol'], 'mid': r['price'], 'spread': None, 'obi': r.get('obi'), 'vpin': None,
                    'score': min(1.0, r['score'] / 12.0), 'directional': 1.0 if r['side'] == 'LONG' else 0.0,
                    'side': 'BUY' if r['side'] == 'LONG' else 'SELL', 'edge_bps': None, 'cost_bps': None,
                    'reasons': [x.split(': ', 1)[1] for x in self.rejects if x.startswith(r['symbol'] + ':')],
                    'status': ('IN_POSITION' if p and p['symbol'] == r['symbol'] else
                               'SIGNAL' if eligible(r, self.cfg)[0] else 'SIGNAL_BLOCKED'),
                    'why': f'surge {r["surge"]:.1f}x/{r["window_min"]}m · move {r["ret_pct"]:+.2f}% · RSI {r["rsi"]:.0f}'}
                   for r in radar.get('picks', [])]
        wins = [t for t in day_trades if t.get('pnl', 0) > 0]
        return {
            'mode': f'{self.v.name.upper()} · RADAR AUTO-TRADER · 1 POSITION MAX · KILL {self.cfg.kill_loss} U',
            'status': (f'HALTED · {self.halt_reason}' if self.halted else self.last_status),
            'equity': round(eq, 4), 'wallet': round(self.cfg.equity + day_pnl, 4), 'pnl': round(eq - self.cfg.equity, 4),
            'volume': round(sum(2 * t.get('entry', 0) * t.get('qty', 0) for t in day_trades), 2), 'fees': None,
            'score': max((m['score'] for m in markets), default=0.0), 'drawdown': None,
            'kill_distance_usdt': round(eq - (self.cfg.equity - self.cfg.kill_loss), 4),
            'outcome_samples': len(day_trades), 'armed': self.v.name == 'demo' and not self.halted,
            'shutdown': False, 'shutdown_reason': '', 'authorized': len(day_trades) + (1 if p else 0),
            'agents': [], 'market_data': 'binance-rest', 'policy': 'ALPHA_RADAR surge≥3x · RSI 55-75 · move≤2%',
            'markets': markets, 'positions': positions, 'open_orders': orders,
            'active_positions': positions, 'realized_pnl': round(day_pnl, 6),
            'trades': [{'branch': self.v.name, 'symbol': t['symbol'], 'side': t['side'], 'entry_price': t.get('entry', 0),
                        'exit_price': None, 'reason': t['reason'], 'pnl_usdt': t['pnl'],
                        'net_bps': (t['pnl'] / (t['entry'] * t['qty']) * 1e4) if t.get('entry') and t.get('qty') else 0,
                        'exit_time': t.get('closed', now), 'hold_s': t.get('closed', now) - t.get('opened', now)}
                       for t in self.trades[:30]],
            'scanner': {'intents': len(day_trades) + (1 if p else 0), 'fills': len(day_trades) + (1 if p else 0),
                        'cancels': 0, 'fill_rate': None,
                        'trades_per_hour': None, 'wins': len(wins)},
            'events': list(self.events), 'timestamp': now, 'updated_at': now, 'history': list(self.history),
        }

    def publish(self) -> None:
        try:
            state = _safe(self.snapshot())
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(state, allow_nan=False, ensure_ascii=False), encoding='utf-8')
            os.replace(tmp, self.state_file)
        except (OSError, ValueError):
            pass


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _safe(obj):
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe(v) for v in obj]
    return obj


# ----------------------------------------------------------------------------- CLI
async def run(args) -> None:
    cfg = TraderConfig(entry=args.entry, scan_every_s=args.scan_every, surge_min=args.surge,
                       retest_ttl_s=args.retest_ttl, sl_mode=args.sl_mode, atr_sl_mult=args.atr_mult,
                       max_loss=args.max_loss, kill_loss=args.max_loss)
    ledger = Ledger(LEDGER_FILE, args.mode)
    if args.reset_kill:
        ledger.write('RESET', reason='--reset-kill')
    radar_cfg = cfg.radar_config()
    radar_fn = lambda: ar.scan(radar_cfg)   # noqa: E731  mainnet PUBLIC data only
    if args.mode == 'demo':
        from config.settings import Settings
        from live.binance_futures_client import BinanceFuturesClient
        settings = Settings.load('testnet')
        async with BinanceFuturesClient(settings) as client:
            await _loop(AutoTrader(DemoVenue(client), cfg, radar_fn, ledger), args)
    else:
        await _loop(AutoTrader(ShadowVenue(cfg), cfg, radar_fn, ledger), args)


async def _loop(trader: AutoTrader, args) -> None:
    try:
        await trader.boot()
    except Exception as exc:
        print(f'[LOI] boot failed: {type(exc).__name__}: {exc}'[:300])
        print('      Kiem tra mang toi Binance va key BINANCE_TESTNET_* trong .env.local. Khong co lenh nao duoc gui.')
        return
    t0 = time.monotonic()
    errors = 0
    try:
        while True:
            try:
                status = await trader.step()
                errors = 0
            except Exception as exc:   # network blip: the exchange-side SL/TP keep guarding the position
                errors += 1
                trader.event(f'ERROR #{errors} {type(exc).__name__}: {exc}'[:200])
                status = 'ERROR'
                await asyncio.sleep(min(30.0, 2.0 * errors))
                if errors >= 20 and trader.pos is not None:
                    await trader.kill(f'{errors} consecutive errors', latch=False)
                    break
            if status == 'SHUTDOWN' or (args.seconds and time.monotonic() - t0 >= args.seconds):
                break
            if trader.halted and trader.pos is None and args.exit_on_halt:
                break
            await asyncio.sleep(trader.cfg.poll_s)
    except (KeyboardInterrupt, asyncio.CancelledError):
        await trader.kill('Ctrl+C', latch=False)
        raise
    finally:
        if trader.pos is not None and args.flatten_on_exit:
            await trader.kill('runner exit', latch=False)
        trader.publish()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description='Alpha Radar auto-trader (shadow | Binance DEMO)')
    ap.add_argument('--mode', choices=('shadow', 'demo'), default='shadow')
    ap.add_argument('--entry', choices=('market', 'retest', 'gtx'), default='market',
                    help='market (default: better than retest on 146 real signals) | retest = post-only at the pullback zone')
    ap.add_argument('--retest-ttl', type=float, default=240.0, help='seconds a retest limit may rest')
    ap.add_argument('--scan-every', type=float, default=120.0)
    ap.add_argument('--surge', type=float, default=5.0)
    ap.add_argument('--sl-mode', choices=('atr', 'money'), default='atr')
    ap.add_argument('--atr-mult', type=float, default=2.0)
    ap.add_argument('--max-loss', type=float, default=0.15, help='USDT per trade (also the daily kill)')
    ap.add_argument('--seconds', type=float, default=0.0)
    ap.add_argument('--reset-kill', action='store_true', help='clear today\'s kill latch (logged)')
    ap.add_argument('--exit-on-halt', action='store_true')
    ap.add_argument('--no-flatten-on-exit', dest='flatten_on_exit', action='store_false')
    args = ap.parse_args(argv)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print('\n[STOP] Ctrl+C: orders cancelled and position closed on the venue.')


if __name__ == '__main__':
    main()
