"""GROKTOPUS 2-mode engine - the single trading entry point.

    python -m core.engine --mode demo     Binance DEMO futures (demo-fapi) with BINANCE_TESTNET_* keys;
                                          no keys -> MOCK broker filled on live Binance prices.
    python -m core.engine --mode live     Binance MAINNET (fapi). Refuses to start unless the
                                          operator typed the arm phrase (run.bat live) AND the
                                          micro-capital gate passes: margin <= 3.85 U, loss at stop
                                          incl. fees <= 0.05 U, Post-Only GTX entries.

Loop: public mainnet WS (aggTrade, depth5@100ms, markPrice@1s, forceOrder) -> core.micro_signals
-> core.micro_swarm 8-agent vote (76.8 % / JEV >= 8.0) -> TokenAuthority (HMAC, 5 s, one use)
-> VESKA routes entry -> exchange STOP_MARKET + reduce-only GTX TP placed from the actual fill
-> MARIN time stop -> ledger data/engine_trades.jsonl. Telemetry + controls on :8765.
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
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from urllib.parse import urlsplit

from config.settings import Settings, read_env
from core import micro_signals as msig
from core import micro_swarm as swarm
from core.consensus_engine import ConsensusEngine, Decision, TokenAuthority
from live.radar_auto_trader import DemoVenue, ShadowVenue, fmt, floor_step, round_tick

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / 'data' / 'engine_trades.jsonl'
SHUTDOWN_FILE = ROOT / 'data' / 'shutdown_request.json'
MODES = ('demo', 'live')
SYMBOLS = ('BTCUSDT', 'DOGEUSDT', 'SOLUSDT')
SYMBOL_QTY = {'BTCUSDT': 0.002}                 # others: smallest tradable size (min notional x 1.02)
LIVE_CONFIRM = 'LIVE'
FALLBACK_SPECS = {
    'BTCUSDT': {'step': 0.001, 'min_qty': 0.001, 'min_notional': 100.0, 'tick': 0.1},
    'ETHUSDT': {'step': 0.001, 'min_qty': 0.001, 'min_notional': 20.0, 'tick': 0.01},
    'SOLUSDT': {'step': 0.01, 'min_qty': 0.01, 'min_notional': 5.0, 'tick': 0.01},
    'DOGEUSDT': {'step': 1.0, 'min_qty': 1.0, 'min_notional': 5.0, 'tick': 0.00001},
    'XRPUSDT': {'step': 0.1, 'min_qty': 0.1, 'min_notional': 5.0, 'tick': 0.0001},
}


@dataclass(frozen=True)
class Profile:
    mode: str
    symbol: str
    leverage: int = 5
    margin_type: str = 'CROSSED'
    entry: str = 'gtx'                  # Post-Only GTX only; MARKET is kept for emergency reduce-only exits
    gtx_ttl_s: float = 5.0              # pegged maker window; unfilled -> cancel, cooldown 1 s, never chase taker
    qty: float = 0.0                    # 0 -> smallest tradable size (min_qty / min_notional x 1.02)
    tp_pct: float = 0.45                # TP1: reduce-only GTX for tp1_frac of the size (0.35 -> 0.45, 2026-10-07g)
    tp1_frac: float = 0.5
    sl_pct: float = 0.20
    microbe_trigger_pct: float = 0.08   # MARIN phase 0: +0.08 % -> stop to entry +0.03 % gross (loss <= fees)
    microbe_lock_pct: float = 0.03
    be_trigger_pct: float = 0.12        # MARIN phase 1: +0.12 % in favour -> stop to fee-true breakeven
    flow_exit: bool = True              # adverse CVD >= $2M (x symbol scale) in 15 s since entry -> exit
    flow_breath_s: float = 30.0         # no adverse-flow exit in the first 30 s (exchange hard SL still live)
    flow_exit_trigger_mult: float = 2.5 # flow limit = max($2M, 2.5 x the 15 s CVD that triggered the entry)
    flow_exit_cap_mult: float = 3.0     # ... capped at 3 x the $2M floor (never 'no exit at all')
    regime_flip_exit: bool = True       # SHORT vs CVD 60 s >= +$1M / LONG vs <= -$1M -> soft maker exit
    regime_flip_usd: float = 1_000_000.0
    regime_flip_min_hold_s: float = 60.0  # no regime-flip exit in the first 60 s (hard SL still live)
    regime_flip_debounce_s: float = 20.0  # the flip must hold continuously 20 s before MARIN exits
    ker_gate: bool = True               # CHOP LOCK: no AUTO entry while KER(2 min) < 0.40 (unlock >= 0.45)
    flow_exit_ttl_s: float = 1.0        # (hard mode only) maker attempt before market
    soft_exits: bool = True             # BE / trail / flow exits = post-only maker queue; only hard SL is taker
    exit_repeg_s: float = 0.5           # re-peg the maker exit when the touch moves away
    peg: bool = True                    # pegged maker entry when consensus >= peg_min_score
    peg_min_score: float = 0.768
    peg_interval_s: float = 0.5
    peg_max_drift_pct: float = 0.15     # never chase further than 0.15 % from the first quote
    aggressive_quote: bool = True       # wide spread: BUY at ask - 1 tick / SELL at bid + 1 tick (still post-only)
    unfilled_cooldown_s: float = 1.0
    super_edge: bool = True             # IOC taker entry on super-momentum
    super_consensus: float = 0.85       # raw swarm consensus (not the JEV-lifted score)
    super_jev: float = 9.2
    super_cvd_usd: float = 3_000_000.0  # CVD 10 s IN the trade direction, x symbol usd_scale
    super_ker: float = 0.60             # IOC also needs KER(2 min) >= 0.60 (straight, steep tape)
    ioc_max_slip_pct: float = 0.03      # IOC limit = touch +/- 0.03 %: bounded taker, never a blind MARKET
    be_offset_pct: float = 0.02         # breakeven stop = entry + all fees + slip + 0.02 %
    fee_shield_pct: float = 0.04        # T2 at +0.12 %: exit level >= entry + 0.04 % (fee-true BE when higher)
    lock_trigger_pct: float = 0.25      # T2b PROFIT LOCK: MFE >= +0.25 % ...
    lock_pct: float = 0.15              # ... -> exit level entry + 0.15 %
    absorb_gate: bool = True            # block entries into an ABSORPTION TRAP (flow without price)
    macro_gate: bool = True             # block entries against the 5 m VWAP + EMA-20 bias
    trail_activate_pct: float = 0.50    # MARIN phase 2: runner trails once +0.50 % was reached
    trail_gap_pct: float = 0.20         # trail = peak -/+ 0.20 %
    trail_tight_pct: float = 0.70       # tier 4b: from +0.70 % the runner trails 0.15 % behind the peak
    trail_tight_gap_pct: float = 0.15
    trail_min_step_pct: float = 0.02    # do not re-place the stop for < 0.02 % improvement
    max_loss_usdt: Optional[float] = None   # set -> stop derived from it (live)
    min_sl_pct: float = 0.10
    max_hold_s: Optional[float] = None  # no time stop (2026-10-07): exits are TP1 / BE / trail / SL
    equity_cap: Optional[float] = None      # margin cap (live micro-capital)
    daily_kill_usdt: float = 5.0
    cooldown_s: float = 30.0            # churn governor: 30 s flat after every close
    max_trades_hour: int = 8
    min_score: float = 0.82             # churn governor: auto entry needs swarm score >= 82 % ...
    min_edge_bps: float = 30.0          # ... AND expected edge >= 30 bps (>= 5x a 6 bps maker round trip)
    max_consec_losses: int = 3
    loss_pause_s: float = 300.0
    max_spread_bps: float = 3.0
    taker_bps: float = 5.0
    maker_bps: float = 2.0
    slip_bps: float = 1.0
    jev_min: Optional[float] = 8.0
    min_confluence: int = 2
    edge_ratio_min: float = 2.5
    mock_balance: float = 1000.0
    test_hold_s: Optional[float] = None


PROFILES = {
    # 0.002 BTC so TP1 can take exactly half (step 0.001). A 0.05 U stop is impossible on BTC:
    # min notional ~$100 x (0.02 % maker + 0.05 % taker) = 0.07 U of fees alone -> demo uses SL 0.20 %.
    'demo': Profile('demo', 'BTCUSDT', qty=0.002, sl_pct=0.20, daily_kill_usdt=5.0),
    'live': Profile('live', 'DOGEUSDT', sl_pct=0.0, max_loss_usdt=0.05, equity_cap=3.85,
                    daily_kill_usdt=0.15, cooldown_s=60.0, max_trades_hour=6, max_spread_bps=8.0, mock_balance=3.85),
}


# ZEPHR bootstrap calibration: typical top-of-book spread (bps) per symbol, Binance VIP0 fees
# (maker 2 bps, taker 5 bps). Live depth5 overwrites the spread through an EMA from the first book.
SPREAD_BOOTSTRAP_BPS = {'BTCUSDT': 0.02, 'ETHUSDT': 0.05, 'SOLUSDT': 0.7, 'DOGEUSDT': 0.6, 'XRPUSDT': 0.4}


class CostModel:
    def __init__(self, p: Profile):
        self.maker, self.taker, self.slip = p.maker_bps, p.taker_bps, p.slip_bps
        self.spread = SPREAD_BOOTSTRAP_BPS.get(p.symbol, 1.0)
        self.samples = 0

    def observe(self, spread_bps: Optional[float]) -> None:
        if spread_bps is not None and math.isfinite(spread_bps) and spread_bps >= 0:
            self.spread = spread_bps if self.samples == 0 else 0.9 * self.spread + 0.1 * spread_bps
            self.samples += 1

    def round_trip_bps(self) -> float:
        """Planned path: GTX maker in + GTX maker TP out; the queue is joined, the spread is not paid."""
        return 2 * self.maker + 0.5 * self.spread

    def stop_out_pct(self) -> float:
        return (self.maker + self.taker + self.slip) / 100.0

    def view(self) -> dict:
        return {'maker_bps': self.maker, 'taker_bps': self.taker, 'spread_bps': self.spread,
                'round_trip_bps': self.round_trip_bps(), 'source': 'live' if self.samples else 'bootstrap',
                'samples': self.samples}


@dataclass(frozen=True)
class MicroIntent:
    symbol: str
    side: str
    quantity: str
    entry: str
    tag: str
    created_ms: int


def ceil_step(x: float, step: float) -> float:
    return round(math.ceil(round(x / step, 9)) * step, 12)


def plan(side: str, price: float, spec: dict, p: Profile, available: Optional[float] = None,
         qty: Optional[float] = None) -> dict:
    """Size + stop + target. -> {'ok': True, ...} or {'ok': False, 'why': ...} (OKAPI)."""
    if not price or price <= 0:
        return {'ok': False, 'why': 'no price'}
    step, min_qty, min_notional = spec['step'], spec['min_qty'], spec['min_notional']
    q = qty if qty else p.qty
    q = max(q or 0.0, min_qty, ceil_step(min_notional * 1.02 / price, step))
    q = ceil_step(q, step)
    notional = q * price
    entry_fee = p.maker_bps if p.entry == 'gtx' else p.taker_bps + p.slip_bps
    fees_pct = (entry_fee + p.taker_bps + p.slip_bps) / 100.0
    if p.max_loss_usdt is not None:
        sl_pct = p.max_loss_usdt / notional * 100.0 - fees_pct
        if sl_pct < p.min_sl_pct:
            return {'ok': False, 'why': f'CAPITAL GATE: {spec_name(spec)}min notional ${notional:,.2f} -> stop '
                                        f'{sl_pct:.3f}% < {p.min_sl_pct}% for max loss {p.max_loss_usdt} U'}
    else:
        sl_pct = p.sl_pct
    margin = notional / p.leverage
    if p.equity_cap is not None and margin > p.equity_cap + 1e-9:
        return {'ok': False, 'why': f'CAPITAL GATE: margin {margin:.2f} U > cap {p.equity_cap} U'}
    if available is not None and margin > available * 0.95:
        return {'ok': False, 'why': f'balance {available:.2f} U < margin {margin:.2f} U'}
    loss = notional * (sl_pct + fees_pct) / 100.0
    if p.max_loss_usdt is not None and loss > p.max_loss_usdt + 1e-9:
        return {'ok': False, 'why': f'loss at stop {loss:.4f} U > {p.max_loss_usdt} U'}
    return {'ok': True, 'qty': q, 'qty_str': fmt(q, step), 'notional': notional, 'margin': margin,
            'sl_pct': sl_pct, 'tp_pct': p.tp_pct, 'loss_at_sl': loss, 'fees_pct': fees_pct}


def spec_name(spec: dict) -> str:
    return f'{spec.get("symbol")} ' if spec.get('symbol') else ''


def brackets(side: str, fill: float, pl: dict, tick: float) -> tuple:
    d = 1 if side == 'BUY' else -1
    sl = round_tick(fill * (1 - d * pl['sl_pct'] / 100.0), tick, 'down' if d > 0 else 'up')
    tp = round_tick(fill * (1 + d * pl['tp_pct'] / 100.0), tick, 'up' if d > 0 else 'down')
    return sl, tp


def credentials(mode: str, env: dict) -> tuple:
    if mode == 'demo':
        key = env.get('BINANCE_TESTNET_API_KEY', '')
        sec = env.get('BINANCE_TESTNET_SECRET_KEY') or env.get('BINANCE_TESTNET_API_SECRET', '')
    else:
        key = env.get('BINANCE_MAINNET_API_KEY') or env.get('BINANCE_API_KEY', '')
        sec = env.get('BINANCE_MAINNET_SECRET_KEY') or env.get('BINANCE_API_SECRET', '')
    return key.strip(), sec.strip()


# ============================================================================ venues
class BinanceVenue(DemoVenue):
    """DemoVenue order primitives + cross margin + full position/order view for the UI."""
    label = 'BINANCE DEMO'

    def __init__(self, client, margin_type: str = 'CROSSED'):
        super().__init__(client)
        self.margin_type = margin_type

    async def prepare(self, symbol: str, leverage: int) -> None:
        from live.binance_futures_client import BinanceError
        try:
            await self.c.request('POST', '/fapi/v1/marginType', {'symbol': symbol, 'marginType': self.margin_type},
                                 signed=True)
        except BinanceError as e:
            if e.code not in (-4046, -4047):          # already set / has open orders
                raise
        await self.c.request('POST', '/fapi/v1/leverage', {'symbol': symbol, 'leverage': leverage}, signed=True)

    async def account_view(self) -> dict:
        a = await self.c.account()
        return {'wallet': float(a.get('totalWalletBalance') or 0), 'available': float(a.get('availableBalance') or 0),
                'upnl': float(a.get('totalUnrealizedProfit') or 0)}

    async def position_view(self, symbol: str) -> dict:
        rows = await self.c.positions(symbol)
        p = next((r for r in rows if float(r.get('positionAmt', 0))), rows[0] if rows else {})
        return {'amt': float(p.get('positionAmt') or 0), 'entry': float(p.get('entryPrice') or 0),
                'mark': float(p.get('markPrice') or 0), 'upnl': float(p.get('unRealizedProfit') or 0),
                'liq': float(p.get('liquidationPrice') or 0), 'notional': abs(float(p.get('notional') or 0)),
                'margin': float(p.get('initialMargin') or p.get('positionInitialMargin') or 0)}

    async def ioc(self, symbol: str, side: str, qty: str, price: str, cid: str) -> tuple:
        """LIMIT IOC: takes what is available up to `price`, cancels the rest. -> (avg_fill, filled_qty)."""
        r = await self.c.request('POST', '/fapi/v1/order', {
            'symbol': symbol, 'side': side, 'type': 'LIMIT', 'timeInForce': 'IOC', 'quantity': qty, 'price': price,
            'positionSide': 'BOTH', 'newClientOrderId': cid, 'newOrderRespType': 'RESULT'}, signed=True)
        filled = float(r.get('executedQty') or 0)
        avg = float(r.get('avgPrice') or 0)
        if filled > 0 and avg <= 0:
            cq = float(r.get('cumQuote') or 0)
            avg = cq / filled if cq > 0 else float(price)
        return avg, filled

    own_book = True                     # demo matching engine != mainnet: quote/manage on THIS venue's book

    async def book_ticker(self, symbol: str) -> dict:
        r = await self.c.request('GET', '/fapi/v1/ticker/bookTicker', {'symbol': symbol}, weight=2)
        return {'bid': float(r['bidPrice']), 'ask': float(r['askPrice']), 'bid_qty': float(r.get('bidQty') or 0),
                'ask_qty': float(r.get('askQty') or 0)}

    async def order(self, symbol: str, cid: str) -> dict:
        from live.binance_futures_client import BinanceError
        for attempt in range(2):
            try:
                return await super().order(symbol, cid)
            except BinanceError as e:
                if e.code != -2013:                       # -2013: not visible yet / expired post-only
                    raise
                await asyncio.sleep(0.3)
        return {'status': 'NOT_FOUND', 'filled': 0.0, 'avg': 0.0}

    async def realized_parts(self, symbol: str, start_ms: int) -> dict:
        trades, _ = await self.c.history_since(symbol, start_ms)
        gross = sum(float(x.get('realizedPnl', 0)) for x in trades)
        fee = sum(float(x.get('commission', 0)) for x in trades if x.get('commissionAsset', 'USDT') == 'USDT')
        return {'gross': gross, 'fee': fee, 'net': gross - fee,
                'maker_fills': sum(bool(x.get('maker')) for x in trades),
                'taker_fills': sum(not x.get('maker') for x in trades)}

    async def limit_gtx(self, symbol: str, side: str, qty: str, price: str, cid: str) -> None:
        from live.binance_futures_client import BinanceError
        try:
            await super().limit_gtx(symbol, side, qty, price, cid)
        except BinanceError as e:
            if e.code == -5022:
                raise PostOnlyReject('-5022 post-only would take liquidity') from None
            raise

    async def orders_view(self, symbol: str) -> list:
        out = []
        for o in await self.c.open_orders(symbol):
            out.append({'id': o.get('clientOrderId'), 'side': o.get('side'), 'type': o.get('type'),
                        'price': float(o.get('price') or 0), 'qty': float(o.get('origQty') or 0),
                        'reduce': bool(o.get('reduceOnly')), 'time': (o.get('time') or 0) / 1000})
        for a in await self.c.open_algos(symbol):
            out.append({'id': a.get('clientAlgoId'), 'side': a.get('side'), 'type': a.get('orderType') or 'STOP_MARKET',
                        'price': float(a.get('triggerPrice') or 0), 'qty': float(a.get('quantity') or 0),
                        'reduce': True, 'time': (a.get('createTime') or 0) / 1000})
        return out


class PostOnlyReject(RuntimeError):
    pass


class LiveVenue(BinanceVenue):
    label = 'BINANCE MAINNET'
    own_book = False                    # mainnet: the public WS book IS the venue book

    def __init__(self, client, margin_type: str = 'CROSSED'):
        host = urlsplit(client.settings.rest).hostname
        if client.settings.mode != 'mainnet' or host != 'fapi.binance.com' or not client.settings.armed:
            raise PermissionError(f'live venue needs armed mainnet settings, got {client.settings.mode}/{host}')
        self.c = client
        self.margin_type = margin_type


class MockVenue(ShadowVenue):
    """Paper broker on the engine's own live book (no key): taker+slip in, maker TP, taker stop."""
    label = 'MOCK · GIÁ LIVE BINANCE'

    def __init__(self, profile: Profile, quote):
        super().__init__(SimpleNamespace(slip_bps=profile.slip_bps, taker_bps=profile.taker_bps,
                                         maker_bps=profile.maker_bps), quote=quote)
        self.balance0 = profile.mock_balance
        self.leverage = profile.leverage

    async def specs(self) -> dict:
        try:
            return await super().specs()
        except Exception:
            return dict(FALLBACK_SPECS)

    def realized_total(self) -> float:
        return sum(p for _, _, p in self.pnl)

    def _fill(self, symbol: str, side: str, qty: float, price: float, fee_bps: float) -> None:
        super()._fill(symbol, side, qty, price, fee_bps)
        self.fills[-1]['fee'] = price * qty * fee_bps / 1e4
        self.fills[-1]['t'] = self.pnl[-1][1]
        self.fills[-1]['maker'] = fee_bps == self.cfg.maker_bps

    async def ioc(self, symbol: str, side: str, qty: str, price: str, cid: str) -> tuple:
        bid, ask = self.quote(symbol)
        lim, slip = float(price), self.cfg.slip_bps / 1e4
        if side == 'BUY':
            if ask > lim:
                return 0.0, 0.0
            px = min(ask * (1 + slip), lim)
        else:
            if bid < lim:
                return 0.0, 0.0
            px = max(bid * (1 - slip), lim)
        self._fill(symbol, side, float(qty), px, self.cfg.taker_bps)
        self.orders[cid] = {'symbol': symbol, 'side': side, 'qty': float(qty), 'price': px, 'kind': 'IOC',
                            'status': 'FILLED'}
        return px, float(qty)

    async def limit_gtx(self, symbol: str, side: str, qty: str, price: str, cid: str) -> None:
        bid, ask = self.quote(symbol)
        if (side == 'BUY' and float(price) >= ask) or (side == 'SELL' and float(price) <= bid):
            raise PostOnlyReject('-5022 post-only would take liquidity')
        await super().limit_gtx(symbol, side, qty, price, cid)

    async def realized_parts(self, symbol: str, start_ms: int) -> dict:
        rows = [f for f in self.fills if f['symbol'] == symbol and f.get('t', 0) >= start_ms]
        fee = sum(f['fee'] for f in rows)
        net = sum(f['pnl'] for f in rows)
        return {'gross': net + fee, 'fee': fee, 'net': net, 'maker_fills': sum(f['maker'] for f in rows),
                'taker_fills': sum(not f['maker'] for f in rows)}

    async def account_view(self) -> dict:
        upnl, used = 0.0, 0.0
        for sym, amt in self.amt.items():
            if amt:
                bid, ask = self.quote(sym)
                mark = (bid + ask) / 2
                upnl += (mark - self.entry[sym]) * amt
                used += abs(amt) * mark / self.leverage
        wallet = self.balance0 + self.realized_total()
        return {'wallet': wallet, 'available': wallet + min(upnl, 0) - used, 'upnl': upnl}

    async def position_view(self, symbol: str) -> dict:
        p = await self.position(symbol)
        amt, e = p['amt'], p['entry']
        liq = 0.0
        if amt:
            mm = 0.004
            liq = e * (1 - 1 / self.leverage + mm) if amt > 0 else e * (1 + 1 / self.leverage - mm)
        return {**p, 'liq': liq, 'notional': abs(amt) * p['mark'], 'margin': abs(amt) * p['mark'] / self.leverage}

    async def orders_view(self, symbol: str) -> list:
        self._match(symbol)
        return [{'id': cid, 'side': o['side'], 'type': {'ENTRY': 'LIMIT GTX', 'TP': 'TP LIMIT', 'SL': 'STOP_MARKET'}
                 .get(o['kind'], o['kind']), 'price': o['price'], 'qty': o['qty'] or 0.0,
                 'reduce': o['kind'] in ('TP', 'SL'), 'time': 0}
                for cid, o in self.orders.items() if o['symbol'] == symbol and o['status'] == 'NEW']

    async def flatten(self, symbol: str) -> None:
        await super().flatten(symbol)


# ============================================================================ engine
class Engine:
    def __init__(self, profile: Profile, venue, feed: msig.MicroFeed, telemetry=None, ledger: Path = LEDGER,
                 allow_live: bool = False, sig_cfg: Optional[msig.SignalConfig] = None, clock=time.time):
        self.p, self.venue, self.feed, self.tel = profile, venue, feed, telemetry
        self.ledger, self.allow_live, self.clock = ledger, allow_live, clock
        self.sig_cfg = sig_cfg or feed.cfg
        self.reg: dict = {}
        self.consensus = ConsensusEngine()
        self.cost = CostModel(profile)
        self.lock = asyncio.Lock()                  # one actor at a time: entry / MARIN / REST sync
        self.vbook: Optional[dict] = None           # venue's own top of book (demo), see refresh_book()
        self.vbook_at = 0.0
        self.all_rows = self._load_all()
        self.authority = TokenAuthority()
        self.spec: Optional[dict] = None
        self.started = False
        self.busy = False
        self.halted = ''
        self.pos: Optional[dict] = None             # engine-owned position
        self.view = {'amt': 0.0}                     # venue position view
        self.account = {}
        self.orders: list = []
        self.history = deque(maxlen=60)
        self.events = deque(maxlen=60)
        self.cooldown_until = 0.0
        self.chop_locked = True                       # KER unknown at start -> locked
        self.absorb = {'state': 'UNKNOWN', 'blocks': None}
        self.macro = {'name': 'WARMUP', 'allow': ('BUY', 'SELL')}
        self.pause_until = 0.0
        self.trade_times = deque()
        self.consec_losses = 0
        self.authorized, self.auth_at = 0, None
        self.latency = deque(maxlen=50)
        self.decision: Optional[Decision] = None
        self.votes: list = []
        self.diag: dict = {}
        self.sig: dict = {}
        self.features: dict = {}
        self.phase = 'BOOT'
        self.last_error = ''
        self.day_pnl = self._load_today()

    # ------------------------------------------------------------------ housekeeping
    def event(self, msg: str) -> None:
        self.events.appendleft({'t': self.clock(), 'message': msg[:240]})
        if self.tel is not None:
            self.tel.event(msg)
        print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)

    def _load_today(self) -> float:
        today, pnl = datetime.now().strftime('%Y-%m-%d'), 0.0
        try:
            for line in self.ledger.read_text(encoding='utf-8').splitlines():
                r = json.loads(line)
                if r.get('mode') == self.p.mode and r.get('day') == today:
                    pnl += float(r.get('pnl') or 0)
                    self.history.appendleft(r)
        except (OSError, ValueError):
            pass
        return pnl

    def _load_all(self) -> list:
        rows = []
        try:
            for line in self.ledger.read_text(encoding='utf-8').splitlines():
                r = json.loads(line)
                if r.get('mode') == self.p.mode:
                    rows.append(r)
        except (OSError, ValueError):
            pass
        return rows

    def stats(self, rows: Optional[list] = None) -> dict:
        """Win-rate radar over settled trades: net PnL after fees."""
        rows = self.all_rows if rows is None else rows
        pnl = [float(r.get('pnl') or 0) for r in rows]
        wins, losses = [x for x in pnl if x > 0], [x for x in pnl if x <= 0]
        gw, gl = sum(wins), -sum(losses)
        n = len(pnl)
        return {'n': n, 'wins': len(wins), 'losses': len(losses), 'win_rate': len(wins) / n if n else None,
                'avg_win': gw / len(wins) if wins else None, 'avg_loss': -gl / len(losses) if losses else None,
                'profit_factor': gw / gl if gl > 0 else None, 'no_losses': gl == 0 and gw > 0,
                'expectancy': sum(pnl) / n if n else None, 'net': sum(pnl),
                'fees': sum(float(r.get('fee') or 0) for r in rows)}

    @property
    def own_book(self) -> bool:
        return bool(getattr(self.venue, 'own_book', False))

    async def refresh_book(self, max_age: float = 0.25) -> None:
        if self.own_book and time.monotonic() - self.vbook_at >= max_age:
            try:
                self.vbook = await self.venue.book_ticker(self.p.symbol)
                self.vbook_at = time.monotonic()
            except Exception as exc:
                self.last_error = f'bookTicker: {type(exc).__name__}: {exc}'[:160]

    def book(self) -> dict:
        """Execution book: the venue's own touch (demo) or the market feed (mainnet / mock)."""
        if self.own_book:
            b = self.vbook or {}
            stale = not b or time.monotonic() - self.vbook_at > 3.0
            bid, ask = b.get('bid'), b.get('ask')
            return {'bid': bid, 'ask': ask, 'bid_qty': b.get('bid_qty'), 'ask_qty': b.get('ask_qty'),
                    'price': (bid + ask) / 2 if bid and ask else None, 'stale': stale, 'source': 'venue'}
        f = self.feed.features(self.clock())
        return {'bid': f.get('bid'), 'ask': f.get('ask'), 'bid_qty': f.get('bid_qty'), 'ask_qty': f.get('ask_qty'),
                'price': f.get('price'), 'stale': f.get('stale'), 'source': 'feed'}

    def _write_ledger(self, row: dict) -> None:
        try:
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            with open(self.ledger, 'a', encoding='utf-8') as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + '\n')
        except OSError:
            pass

    async def start(self) -> None:
        await self.venue.start()
        specs = await self.venue.specs()
        self.spec = specs.get(self.p.symbol) or FALLBACK_SPECS.get(self.p.symbol)
        if not self.spec:
            raise RuntimeError(f'no exchange filters for {self.p.symbol}')
        await self.venue.prepare(self.p.symbol, self.p.leverage)
        self.started = True
        self.phase = 'SCANNING'
        self.event(f'{self.venue.label} · {self.p.symbol} · {self.p.margin_type} {self.p.leverage}x · entry '
                   f'{self.p.entry.upper()} · TP +{self.p.tp_pct}% · '
                   + (f'max loss {self.p.max_loss_usdt} U' if self.p.max_loss_usdt else f'SL -{self.p.sl_pct}%'))

    # ------------------------------------------------------------------ gates
    def gates(self, f: dict) -> swarm.Gates:
        now = self.clock()
        spread = f.get('spread_bps')
        self.cost.observe(spread)
        rt = self.cost.round_trip_bps()
        if f.get('stale'):
            z = (False, 'STALE book (/public depth5 silent)')
        elif f.get('trades_silent'):
            z = (False, 'aggTrade silent > 15 s (/market socket) - CVD/VWAP blind')
        elif spread is None or spread > self.p.max_spread_bps:
            z = (False, f'spread {spread if spread is None else round(spread, 2)} bps > {self.p.max_spread_bps}')
        else:
            z = (True, f'cost {rt:.2f} bps RT ({self.cost.view()["source"]}) · spread {spread:.2f} bps')
        while self.trade_times and now - self.trade_times[0] > 3600:
            self.trade_times.popleft()
        if self.halted:
            r = (False, f'HALT: {self.halted}')
        elif self.day_pnl <= -self.p.daily_kill_usdt:
            r = (False, f'daily loss {self.day_pnl:+.4f} <= -{self.p.daily_kill_usdt} U')
        elif self.pos or abs(self.view.get('amt') or 0) > 0:
            r = (False, 'position open' if self.pos else 'EXTERNAL position on venue')
        elif self.busy:
            r = (False, 'order in flight')
        elif now < self.pause_until:
            r = (False, f'{self.consec_losses} losses · pause {self.pause_until - now:.0f}s')
        elif now < self.cooldown_until:
            r = (False, f'cooldown {self.cooldown_until - now:.0f}s')
        elif len(self.trade_times) >= self.p.max_trades_hour:
            r = (False, f'{len(self.trade_times)} trades/h cap')
        else:
            r = (True, f'day {self.day_pnl:+.4f} U · {len(self.trade_times)}/{self.p.max_trades_hour} per h')
        pl = plan('BUY', f.get('price') or 0, self.spec, self.p, self.account.get('available')) if self.spec \
            else {'ok': False, 'why': 'filters not loaded'}
        o = (True, f'{pl["qty_str"]} · margin {pl["margin"]:.2f} U · risk {pl["loss_at_sl"]:.4f} U') if pl['ok'] \
            else (False, pl['why'])
        m = (bool(self.spec), 'SL + BE lock +0.12% + TP1 50% + trail' if self.spec else 'no filters')
        v = (self.started and not self.busy, f'{self.venue.label} ready' if self.started else 'venue not started')
        return swarm.Gates(z, r, o, m, v, cost_bps=rt)

    # ------------------------------------------------------------------ decide
    async def tick(self) -> Optional[Decision]:
        async with self.lock:
            return await self._tick()

    async def locked_sync(self) -> None:
        async with self.lock:
            await self.sync()

    async def _tick(self) -> Optional[Decision]:
        f = self.features = self.feed.features(self.clock())
        if self.pos:                                  # MARIN reacts on every WS tick, not every REST poll
            await self.manage()
        self.chop_locked = msig.chop_locked(f.get('ker'), self.chop_locked, self.sig_cfg)
        self.absorb = msig.absorption(f, self.sig_cfg)
        self.macro = msig.macro_bias(f, self.sig_cfg)
        self.reg = msig.regime(f, self.sig_cfg)
        self.sig = msig.signals(f, self.sig_cfg, self.reg)
        d, votes, diag = swarm.evaluate(self.sig, self.gates(f), self.consensus, self.p.jev_min,
                                        self.p.min_confluence, self.p.edge_ratio_min, allow=self.reg['allow'])
        if d.allowed and not self.busy and not self.pos:
            ok, why = self.churn_gate(d, diag)
            if not ok:
                d = replace(d, allowed=False, reason=why)
        self.decision, self.votes, self.diag = d, votes, diag
        if d.allowed and not self.busy and not self.pos:
            await self.execute(d.side, d, diag.get('path') or 'SWARM')
        return d

    def conviction(self, d: Decision, diag: dict) -> float:
        """Swarm score the governor reads: raw vote (60/80/100 % steps with 3 alpha agents) or the JEV lift."""
        jev = (diag.get('jev') or 0.0) / 10.0 if diag.get('gates_ok') else 0.0
        return max(d.score, diag.get('consensus') or 0.0, jev)

    def chop_reason(self) -> str:
        f, c = self.features or {}, self.sig_cfg
        k = f.get('ker')
        if k is None:
            return f'CHOP LOCKED KER warming {f.get("ker_span_s") or 0:.0f}/{c.ker_min_span_s:.0f}s'
        if k < c.ker_lock:
            return f'CHOP LOCKED KER {k:.2f} < {c.ker_lock:.2f}'
        return f'CHOP LOCKED KER {k:.2f} < {c.ker_unlock:.2f} (hysteresis)'

    def churn_gate(self, d: Decision, diag: dict) -> tuple:
        """Anti-churn (2026-10-07f): auto entries need score >= min_score AND expected edge >= min_edge_bps."""
        score, edge = self.conviction(d, diag), diag.get('edge_bps') or 0.0
        if self.p.ker_gate and self.chop_locked:
            return False, self.chop_reason()
        if self.p.macro_gate and d.side not in self.macro.get('allow', ('BUY', 'SELL')):
            return False, f'MACRO 5m {self.macro["name"]}: cấm {d.side} · {self.macro.get("why", "")}'
        if self.p.absorb_gate and self.absorb.get('blocks') == d.side:
            return False, f'ABSORPTION TRAP {d.side} · {self.absorb.get("why", "")}'
        if score < self.p.min_score:
            return False, f'CHURN score {score * 100:.1f}% < {self.p.min_score * 100:.1f}%'
        if edge < self.p.min_edge_bps:
            return False, (f'CHURN edge {edge:.1f} bps < {self.p.min_edge_bps:g} bps '
                           f'(phí maker 2 chiều {2 * self.p.maker_bps:g} bps)')
        return True, ''

    # ------------------------------------------------------------------ VESKA (Post-Only GTX only)
    def gtx_price(self, side: str, f: dict) -> Optional[float]:
        """Join the touch; step 1 tick inside when the spread is >= 2 ticks (still post-only)."""
        tick, bid, ask = self.spec['tick'], f.get('bid'), f.get('ask')
        if not bid or not ask:
            return None
        bq, aq = f.get('bid_qty') or 0.0, f.get('ask_qty') or 0.0
        wide = ask - bid >= 2 * tick - 1e-12
        # micro-price: I = bid_qty / (bid_qty + ask_qty); P_micro = I*ask + (1-I)*bid (queue-weighted fair value)
        micro = (bq / (bq + aq)) * ask + (aq / (bq + aq)) * bid if bq + aq > 0 else (bid + ask) / 2
        if side == 'BUY':
            px = (ask - tick if self.p.aggressive_quote else max(bid + tick, micro - tick)) if wide else bid
            return round_tick(min(max(px, bid), ask - tick), tick, 'down')
        px = (bid + tick if self.p.aggressive_quote else min(ask - tick, micro + tick)) if wide else ask
        return round_tick(max(min(px, ask), bid + tick), tick, 'up')

    def route(self, decision: Decision, f: dict) -> str:
        """VESKA router: 'ioc' (bounded taker) on super-momentum, else 'gtx' (pegged maker)."""
        if not self.p.super_edge:
            return 'gtx'
        diag = self.diag or {}
        cons, jev = diag.get('consensus', 0.0), diag.get('jev', 0.0)
        cvd10 = (f.get('cvd_10s') or 0.0) * (1 if decision.side == 'BUY' else -1)   # flow WITH the trade
        need = self.p.super_cvd_usd * self.feed.cfg.usd_scale
        ker = f.get('ker') or 0.0
        if cvd10 >= need and ker >= self.p.super_ker and (cons >= self.p.super_consensus or jev >= self.p.super_jev):
            return 'ioc'
        return 'gtx'

    async def execute(self, side: str, decision: Decision, path: str, tag: str = 'AUTO',
                      entry: Optional[str] = None, hold_s: Optional[float] = None) -> dict:
        if self.busy or self.pos:
            return {'ok': False, 'error': 'busy or position open'}
        self.busy, self.phase = True, 'EXECUTING'
        sym, tick, step = self.p.symbol, self.spec['tick'], self.spec['step']
        t0 = time.perf_counter()
        try:
            await self.refresh_book(0.0)
            f = {**self.feed.features(self.clock()), **{k: v for k, v in self.book().items() if v is not None}}
            px = self.gtx_price(side, f)
            if px is None:
                return {'ok': False, 'error': 'no book'}
            route = self.route(decision, f) if tag == 'AUTO' else 'gtx'
            touch = f.get('ask') if side == 'BUY' else f.get('bid')
            pl = plan(side, px if route == 'gtx' else touch, self.spec,
                      replace(self.p, entry='gtx' if route == 'gtx' else 'market'), self.account.get('available'))
            if not pl['ok']:
                self.event(f'OKAPI refused {side}: {pl["why"]}')
                return {'ok': False, 'error': pl['why']}
            intent = MicroIntent(sym, side, pl['qty_str'], route, tag, int(self.clock() * 1000))
            token = self.authority.issue(intent, decision)
            self.authorized += 1
            self.auth_at = self.clock()
            self.authority.consume(token, intent)      # VESKA spends the one-use token right before routing
            cid = f'gk{tag[0].lower()}-' + uuid.uuid4().hex[:26]
            posted_ms = int(self.clock() * 1000)       # entry fill can land any time in the GTX window
            px0, reprices = px, 0
            if route == 'ioc':
                d = 1 if side == 'BUY' else -1
                lim = round_tick(touch * (1 + d * self.p.ioc_max_slip_pct / 100.0), tick, 'up' if d > 0 else 'down')
                fill, filled = await self.venue.ioc(sym, side, pl['qty_str'], fmt(lim, tick), cid)
                px = lim
            else:
                try:
                    await self.venue.limit_gtx(sym, side, pl['qty_str'], fmt(px, tick), cid)
                except PostOnlyReject:
                    self.cooldown_until = self.clock() + self.p.unfilled_cooldown_s
                    self.event(f'GTX {side} @ {fmt(px, tick)} rejected post-only (book moved) - retry after cooldown')
                    return {'ok': False, 'error': 'post-only rejected (would take liquidity)'}
                st, deadline = {}, time.monotonic() + self.p.gtx_ttl_s
                peg = self.p.peg and decision.score >= self.p.peg_min_score
                last_post = time.monotonic()
                while time.monotonic() < deadline:
                    st = await self.venue.order(sym, cid)
                    if st.get('status') == 'FILLED':
                        break
                    if peg and time.monotonic() - last_post >= self.p.peg_interval_s:
                        # Dynamic pegged maker: follow the touch while the book walks away, never cross it,
                        # never further than peg_max_drift_pct from the first quote (bounded chase).
                        await self.refresh_book(0.0)
                        new = self.gtx_price(side, self.book())
                        d = 1 if side == 'BUY' else -1
                        if (new is not None and d * (new - px) > 0
                                and abs(new / px0 - 1) * 100 <= self.p.peg_max_drift_pct):
                            await self.venue.cancel(sym, cid)
                            st = await self.venue.order(sym, cid)
                            if float(st.get('filled') or 0) > 0:      # partial/full fill raced the cancel: keep it
                                break
                            cid = f'gk{tag[0].lower()}-' + uuid.uuid4().hex[:26]
                            try:
                                await self.venue.limit_gtx(sym, side, pl['qty_str'], fmt(new, tick), cid)
                            except PostOnlyReject:
                                st = {}
                                break
                            px, last_post, reprices = new, time.monotonic(), reprices + 1
                        else:
                            last_post = time.monotonic()
                    await asyncio.sleep(0.1)
                if st.get('status') != 'FILLED':
                    await self.venue.cancel(sym, cid)
                    st = await self.venue.order(sym, cid)
                filled, fill = float(st.get('filled') or 0), float(st.get('avg') or px)
            if reprices:
                self.event(f'PEG {side} repriced x{reprices} {fmt(px0, tick)} -> {fmt(px, tick)} '
                           f'({"filled" if filled > 0 else "unfilled"})')
            ms = (time.perf_counter() - t0) * 1000
            self.latency.append(ms)
            if filled <= 0:
                self.cooldown_until = self.clock() + self.p.unfilled_cooldown_s
                self.phase = 'SCANNING'
                if route == 'ioc':
                    self.event(f'IOC {side} {pl["qty_str"]} limit {fmt(px, tick)} found no liquidity inside '
                               f'{self.p.ioc_max_slip_pct}% -> expired, rescan in {self.p.unfilled_cooldown_s:g}s')
                    return {'ok': False, 'error': f'IOC không khớp trong biên trượt {self.p.ioc_max_slip_pct}%',
                            'latency_ms': ms, 'route': 'IOC'}
                self.event(f'GTX {side} {pl["qty_str"]} @ {fmt(px, tick)} not filled in {self.p.gtx_ttl_s:g}s '
                           f'-> cancelled (peg drift cap {self.p.peg_max_drift_pct}%), rescan in {self.p.unfilled_cooldown_s:g}s')
                return {'ok': False, 'error': f'GTX không khớp trong {self.p.gtx_ttl_s:g}s (peg tối đa '
                                              f'{self.p.peg_max_drift_pct}%) - đã huỷ, quét lại sau {self.p.unfilled_cooldown_s:g}s',
                        'latency_ms': ms, 'route': 'GTX'}
            qty = floor_step(filled, step)
            qty_str = fmt(qty, step)
            sl, tp = brackets(side, fill, pl, tick)
            half = floor_step(qty * self.p.tp1_frac, step)
            split = half >= self.spec['min_qty'] and qty - half >= self.spec['min_qty'] - 1e-12
            opp = 'SELL' if side == 'BUY' else 'BUY'
            self.pos = {'symbol': sym, 'side': side, 'qty': qty, 'qty_str': qty_str, 'entry': fill,
                        'opened': self.clock(), 'opened_ms': posted_ms - 1000, 'sl': sl, 'tp': tp,
                        'sl0': sl, 'sl_cid': 'gs-' + uuid.uuid4().hex[:28], 'tp_cid': 'gt-' + uuid.uuid4().hex[:28],
                        'stop_kind': 'SL', 'tp_qty': half if split else qty, 'split': split, 'tp1_filled': False,
                        'be_locked': False, 'mbe_locked': False, 'trail_on': False, 'peak': fill, 'mfe_pct': 0.0,
                        'adverse_usd': 0.0, 'regime': (self.reg or {}).get('name'),
                        'trigger_usd': abs(f.get('cvd_exit_win') or 0.0), 'flow_limit': self.flow_limit(f),
                        'edge_bps': (self.diag or {}).get('edge_bps'), 'ker': f.get('ker'),
                        'px_vel_15s': f.get('px_vel_15s'), 'macro': self.macro.get('name'),
                        'tag': tag, 'path': path, 'score': decision.score, 'reason': decision.reason,
                        'hold_s': hold_s or self.p.max_hold_s, 'latency_ms': ms,
                        'tp_pct': pl['tp_pct'], 'sl_pct': pl['sl_pct'], 'entry_type': route.upper(),
                        'entry_fee_bps': self.p.taker_bps if route == 'ioc' else self.p.maker_bps}
            self.trade_times.append(self.clock())
            how = (f'IOC TAKER super-edge, cons {(self.diag or {}).get("consensus", 0) * 100:.1f}% JEV '
                   f'{(self.diag or {}).get("jev", 0):.1f}' if route == 'ioc' else 'POST-ONLY GTX maker')
            self.event(f'VESKA {tag} {side} {qty_str} {sym} @ {fill:g} ({how}, {ms:.0f} ms) · {path} '
                       f'{decision.score * 100:.1f}% · TP1 {tp:g} x{fmt(self.pos["tp_qty"], step)} · SL {sl:g}')
            try:
                await self.venue.stop(sym, opp, fmt(sl, tick), self.pos['sl_cid'])
            except Exception as exc:
                self.event(f'MARIN stop rejected ({type(exc).__name__}: {exc}) -> closing at market')
                await self.close('NO_STOP')
                return {'ok': False, 'error': 'stop rejected, closed'}
            if not await self.venue.take_profit(sym, opp, fmt(self.pos['tp_qty'], step), fmt(tp, tick),
                                                self.pos['tp_cid']):
                await self.close('TP')
            self.phase = 'POSITION'
            return {'ok': True, 'side': side, 'qty': qty_str, 'entry': fill, 'tp': tp, 'sl': sl, 'latency_ms': ms,
                    'entry_type': route.upper()}
        except Exception as exc:
            self.event(f'VESKA error {type(exc).__name__}: {exc}')
            self.cooldown_until = self.clock() + 10
            return {'ok': False, 'error': f'{type(exc).__name__}: {exc}'[:200]}
        finally:
            self.busy = False
            if not self.pos:
                self.phase = 'SCANNING'

    # ------------------------------------------------------------------ MARIN
    def be_price(self, p: dict) -> float:
        d = 1 if p['side'] == 'BUY' else -1
        off = self.cost.stop_out_pct() + self.p.be_offset_pct      # maker in + taker stop + slip + 0.02 %
        return round_tick(p['entry'] * (1 + d * off / 100.0), self.spec['tick'], 'up' if d > 0 else 'down')

    async def move_stop(self, price: float, kind: str) -> bool:
        """Place the new stop FIRST, then cancel the old one: never a moment unprotected."""
        p, sym, tick = self.pos, self.p.symbol, self.spec['tick']
        opp = 'SELL' if p['side'] == 'BUY' else 'BUY'
        cid = 'gs-' + uuid.uuid4().hex[:28]
        try:
            await self.venue.stop(sym, opp, fmt(price, tick), cid)
        except Exception as exc:                            # e.g. -2021 would trigger immediately
            mark = self.view.get('mark') or self.feed.price()
            d = 1 if p['side'] == 'BUY' else -1
            if mark and d * (mark - price) <= 0:
                self.event(f'MARIN {kind} {price:g} already crossed ({type(exc).__name__}) -> close reduce-only')
                await self.close(kind)
                return True
            self.event(f'MARIN move {kind} failed: {type(exc).__name__}: {exc}')
            return False
        old = p['sl_cid']
        p.update(sl=price, sl_cid=cid, stop_kind=kind, xstop_kind=kind)
        try:
            await self.venue.cancel_stop(old)
        except Exception:
            pass
        return True

    def mbe_price(self, p: dict) -> float:
        d = 1 if p['side'] == 'BUY' else -1
        return round_tick(p['entry'] * (1 + d * self.p.microbe_lock_pct / 100.0), self.spec['tick'],
                          'up' if d > 0 else 'down')

    def soft_be_price(self, p: dict) -> float:
        """Maker-exit breakeven: entry + maker in + maker out + offset (+0.06 % on VIP0)."""
        d = 1 if p['side'] == 'BUY' else -1
        off = (p.get('entry_fee_bps', self.p.maker_bps) + self.p.maker_bps) / 100.0 + self.p.be_offset_pct
        return round_tick(p['entry'] * (1 + d * off / 100.0), self.spec['tick'], 'up' if d > 0 else 'down')

    def shield_price(self, p: dict) -> float:
        """Fee shield: the better of entry + fee_shield_pct and the fee-true maker breakeven."""
        d = 1 if p['side'] == 'BUY' else -1
        fixed = round_tick(p['entry'] * (1 + d * self.p.fee_shield_pct / 100.0), self.spec['tick'],
                           'up' if d > 0 else 'down')
        be = self.soft_be_price(p)
        return max(fixed, be) if d > 0 else min(fixed, be)

    def plock_price(self, p: dict) -> float:
        d = 1 if p['side'] == 'BUY' else -1
        return round_tick(p['entry'] * (1 + d * self.p.lock_pct / 100.0), self.spec['tick'], 'up' if d > 0 else 'down')

    def adverse_flow(self, p: dict) -> float:
        """USD of aggressor flow AGAINST the position, since entry, inside the last flow_exit_s."""
        now, cfg = self.clock(), self.feed.cfg
        start = max(p['opened'], now - cfg.flow_exit_s)
        signed = sum(s for t, s in self.feed.flow.trades if t >= start)
        return -signed if p['side'] == 'BUY' else signed

    def exit_price(self, side_out: str, f: dict) -> Optional[float]:
        """Post-only reduce price: SELL at the best ask (BUY at the best bid); 1 tick inside if spread >= 2 ticks."""
        return self.gtx_price(side_out, f)

    async def start_soft_exit(self, reason: str) -> None:
        p = self.pos
        if p.get('exiting'):
            return
        p['exiting'] = {'reason': reason, 'cid': None, 'px': None, 'posted': 0.0, 'started': self.clock(),
                        'reprices': 0}
        try:
            await self.venue.cancel(self.p.symbol, p['tp_cid'])     # the exit order owns the whole size now
        except Exception:
            pass
        self.event(f'MARIN SOFT EXIT {reason}: post-only reduce at the touch, re-peg every '
                   f'{self.p.exit_repeg_s:g}s - no market order (hard SL {p["sl0"]:g} stays on the exchange)')
        await self.work_soft_exit()

    async def work_soft_exit(self) -> None:
        """Maker exit queue: keep one reduce-only GTX at the touch; re-peg when the touch moves away."""
        p, sym, step, tick = self.pos, self.p.symbol, self.spec['step'], self.spec['tick']
        x = p['exiting']
        opp = 'SELL' if p['side'] == 'BUY' else 'BUY'
        await self.refresh_book(0.3)
        want = self.exit_price(opp, self.book())
        if want is None:
            return
        now = time.monotonic()
        if x['cid']:
            st = await self.venue.order(sym, x['cid'])
            if st.get('status') == 'FILLED':
                return                                            # flat -> sync() settles
            moved = x['px'] is not None and abs(want - x['px']) >= tick - 1e-12
            if not moved or now - x['posted'] < self.p.exit_repeg_s:
                return
            await self.venue.cancel(sym, x['cid'])
            x['reprices'] += 1
        v = await self.venue.position_view(sym)
        amt = abs(v.get('amt') or 0.0)
        if amt <= 0:
            return
        cid = 'gx-' + uuid.uuid4().hex[:28]
        ok = await self.venue.take_profit(sym, opp, fmt(floor_step(amt, step), step), fmt(want, tick), cid)
        x.update(cid=cid if ok else None, px=want if ok else None, posted=now)

    async def flow_exit(self) -> None:
        await self.start_soft_exit('FLOW')

    def flow_limit(self, f: dict) -> float:
        """Adverse-flow exit threshold for a trade: max($2M, 2.5 x entry trigger flow), <= 3 x $2M (x usd_scale)."""
        base = self.feed.cfg.flow_exit_usd * self.feed.cfg.usd_scale
        trig = abs(f.get('cvd_exit_win') or 0.0) * self.p.flow_exit_trigger_mult
        return min(max(base, trig), base * self.p.flow_exit_cap_mult)

    def regime_flipped(self, p: dict, f: dict) -> bool:
        """SHORT while CVD 60 s >= +$1M (x scale), LONG while <= -$1M: the thesis is dead."""
        if not self.p.regime_flip_exit:
            return False
        cvd, lim = f.get('cvd_60s') or 0.0, self.p.regime_flip_usd * self.feed.cfg.usd_scale
        return cvd >= lim if p['side'] == 'SELL' else cvd <= -lim

    async def manage(self) -> None:
        """MARIN: adverse-flow exit -> breakeven tiers -> TP1 half -> trailing runner.
        soft_exits=True: BE / trail / flow exits are post-only maker orders at the touch; only the hard SL on the
        exchange (STOP_MARKET at the initial stop) can pay taker."""
        p, v = self.pos, self.view
        if not p or not v.get('amt'):
            return
        f = self.features or {}
        b = self.book()
        mark = (b.get('price') if not b.get('stale') else None) or v.get('mark') or self.feed.price()
        if not mark:
            return
        d = 1 if p['side'] == 'BUY' else -1
        p['peak'] = max(p['peak'], mark) if d > 0 else min(p['peak'], mark)
        fav = d * (mark / p['entry'] - 1) * 100
        peak_fav = d * (p['peak'] / p['entry'] - 1) * 100
        p['mfe_pct'] = max(p['mfe_pct'], peak_fav)
        if p.get('exiting'):
            await self.work_soft_exit()
            return
        now = self.clock()
        flip = self.regime_flipped(p, f)
        p['flip_since'] = (p.get('flip_since') or now) if flip else None
        if (flip and now - p['opened'] >= self.p.regime_flip_min_hold_s
                and now - p['flip_since'] >= self.p.regime_flip_debounce_s):
            self.event(f'MARIN REGIME-FLIP EXIT: {"SHORT" if d < 0 else "LONG"} vs CVD 60s '
                       f'{(f.get("cvd_60s") or 0) / 1e6:+.2f}M (limit ±${self.p.regime_flip_usd * self.feed.cfg.usd_scale / 1e6:.2f}M)'
                       f' held {now - p["flip_since"]:.0f}s at {fav:+.3f}% -> soft maker exit')
            if self.p.soft_exits:
                await self.start_soft_exit('REGIME')
            else:
                p['exit_reason'] = 'REGIME'
                await self.close('REGIME')
            return
        adverse = self.adverse_flow(p)
        p['adverse_usd'] = adverse
        limit = p.get('flow_limit') or self.flow_limit({})
        age = self.clock() - p['opened']
        p['breathing'] = age < self.p.flow_breath_s
        if self.p.flow_exit and adverse >= limit and not p['breathing']:
            self.event(f'MARIN FLOW EXIT: ${adverse / 1e6:.2f}M aggressor flow against {p["side"]} in '
                       f'{self.feed.cfg.flow_exit_s:g}s (limit ${limit / 1e6:.2f}M) at {fav:+.3f}%')
            if self.p.soft_exits:
                await self.start_soft_exit('FLOW')
            else:
                p['exit_reason'] = 'FLOW'
                await self.close('FLOW')
            return
        if p['split'] and not p['tp1_filled'] and abs(v['amt']) <= p['qty'] - p['tp_qty'] + 1e-12:
            p['tp1_filled'] = True
            self.event(f'MARIN TP1 filled {fmt(p["tp_qty"], self.spec["step"])} @ {p["tp"]:g} (maker) · runner trails')
        if self.p.soft_exits:
            await self._manage_soft(p, d, mark, fav)
        else:
            await self._manage_hard(p, d, mark, fav)

    async def _manage_soft(self, p: dict, d: int, mark: float, fav: float) -> None:
        """4 tiers (soft maker exits): T1 +0.08 % -> entry +0.03 % · T2 +0.12 % -> entry + fees + 0.02 %
        · T3 TP1 50 % at +0.35 % (resting maker order) · T4 runner trails 0.20 % from +0.50 %, 0.15 % from +0.70 %."""
        level, kind = p.get('soft_stop'), p.get('soft_kind')
        cands = []
        if p['mfe_pct'] >= self.p.microbe_trigger_pct:
            cands.append((self.mbe_price(p), 'LOCK'))
        if p['mfe_pct'] >= self.p.be_trigger_pct:
            cands.append((self.shield_price(p), 'BE'))
        if p['mfe_pct'] >= self.p.lock_trigger_pct:
            cands.append((self.plock_price(p), 'PROFIT'))
        if p['mfe_pct'] >= self.p.trail_activate_pct:
            gap = self.p.trail_tight_gap_pct if p['mfe_pct'] >= self.p.trail_tight_pct else self.p.trail_gap_pct
            cands.append((round_tick(p['peak'] * (1 - d * gap / 100.0), self.spec['tick'], 'down' if d > 0 else 'up'),
                          'TRAIL'))
            p['trail_gap'] = gap
        for px, k in cands:
            if level is None or d * (px - level) > 0:
                if k != kind:
                    self.event({'LOCK': f'MARIN T1 MICRO-LOCK at {fav:+.3f}% -> exit level {px:g} (entry +'
                                        f'{self.p.microbe_lock_pct}%, loss <= fees)',
                                'BE': f'MARIN T2 FEE-SHIELD at {fav:+.3f}% -> exit level {px:g} (>= entry +'
                                      f'{self.p.fee_shield_pct}% and entry + fees + {self.p.be_offset_pct}%)',
                                'PROFIT': f'MARIN T2b PROFIT LOCK at {fav:+.3f}% -> exit level {px:g} (entry +'
                                          f'{self.p.lock_pct}%)',
                                'TRAIL': f'MARIN T4 TRAIL {p.get("trail_gap")}% behind peak {p["peak"]:g} -> {px:g}'}[k])
                level, kind = px, k
        if kind in ('BE', 'PROFIT', 'TRAIL'):
            p['be_locked'] = True
        if kind in ('PROFIT', 'TRAIL'):
            p['plock'] = True
        if kind == 'LOCK':
            p['mbe_locked'] = True
        p['trail_on'] = kind == 'TRAIL'
        p['soft_stop'], p['soft_kind'] = level, kind
        if level is not None:
            p['stop_kind'] = kind
            if d * (mark - level) <= 0:
                await self.start_soft_exit(f'SOFT-{kind}')

    async def _manage_hard(self, p: dict, d: int, mark: float, fav: float) -> None:
        target, kind = None, None
        if p['stop_kind'] == 'SL' and p['mfe_pct'] >= self.p.microbe_trigger_pct:
            target, kind = self.mbe_price(p), 'MBE'
        if not p['be_locked'] and p['mfe_pct'] >= self.p.be_trigger_pct:
            target, kind = self.be_price(p), 'BE'
        if not p.get('plock') and p['mfe_pct'] >= self.p.lock_trigger_pct:
            lk = max(self.plock_price(p), self.be_price(p)) if d > 0 else min(self.plock_price(p), self.be_price(p))
            if d * (lk - (target if target is not None else p['sl'])) > 0:
                target, kind = lk, 'PROFIT'
        if p['mfe_pct'] >= self.p.trail_activate_pct:
            trail = round_tick(p['peak'] * (1 - d * self.p.trail_gap_pct / 100.0), self.spec['tick'],
                               'down' if d > 0 else 'up')
            floor = self.be_price(p)
            trail = max(trail, floor) if d > 0 else min(trail, floor)
            best = target if target is not None else p['sl']
            if d * (trail - best) >= p['entry'] * self.p.trail_min_step_pct / 100.0:
                target, kind = trail, 'TRAIL'
        if target is not None and d * (target - p['sl']) > 0 and d * (mark - target) > 0:
            if await self.move_stop(target, kind) and self.pos:
                if kind == 'MBE':
                    p['mbe_locked'] = True
                    self.event(f'MARIN MICRO-BE at {fav:+.3f}% -> stop {target:g}')
                elif kind == 'BE' and not p['be_locked']:
                    p['be_locked'] = True
                    self.event(f'MARIN BREAKEVEN LOCK at +{fav:.3f}% -> stop {target:g} (net >= +{self.p.be_offset_pct}%)')
                elif kind == 'PROFIT':
                    p['be_locked'] = p['plock'] = True
                    self.event(f'MARIN PROFIT LOCK at +{fav:.3f}% -> stop {target:g} (entry +{self.p.lock_pct}%)')
                elif kind == 'TRAIL':
                    p['be_locked'] = p['trail_on'] = True
        elif (target is not None and d * (mark - target) <= 0 and kind in ('MBE', 'BE', 'PROFIT')
              and d * (target - p['sl']) > 0):
            self.event(f'MARIN {kind} {target:g} already crossed at mark {mark:g} -> close reduce-only')
            await self.close(kind)

    async def close(self, reason: str) -> None:
        sym = self.p.symbol
        if self.pos:
            await self.venue.cancel(sym, self.pos['tp_cid'])
        v = await self.venue.position_view(sym)
        if v['amt']:
            await self.venue.close(sym, v['amt'], self.spec['step'])
        if self.pos:
            self.pos['exit_reason'] = reason
            try:
                await self.venue.cancel_stop(self.pos['sl_cid'])
            except Exception as exc:                       # already triggered / gone
                self.event(f'MARIN stop cancel: {type(exc).__name__}')
        await self.sync(force_settle=True)

    async def sync(self, force_settle: bool = False) -> None:
        sym = self.p.symbol
        self.view = await self.venue.position_view(sym)
        try:
            self.account = await self.venue.account_view()
        except Exception:
            pass
        self.orders = await self.venue.orders_view(sym)
        p = self.pos
        if p and not self.view['amt']:
            await self._settle(p)
        elif p and not force_settle:
            if p['hold_s'] and self.clock() - p['opened'] >= p['hold_s']:
                self.event(f'MARIN time stop {p["hold_s"]:.0f}s -> close {p["side"]} {p["qty_str"]}')
                await self.close('TIME')
            else:
                await self.manage()

    async def _settle(self, p: dict) -> None:
        sym = self.p.symbol
        reason = p.get('exit_reason')
        x = p.get('exiting')
        if x and not reason:
            reason = ('TP1+' if p['tp1_filled'] else '') + x['reason']
            if x.get('cid'):
                try:
                    await self.venue.cancel(sym, x['cid'])
                except Exception:
                    pass
        if not reason:
            st = await self.venue.order(sym, p['tp_cid'])
            tp_hit = st.get('status') == 'FILLED' or p['tp1_filled']
            if tp_hit and not p['split']:
                reason = 'TP'
            else:
                reason = ('TP1+' if tp_hit else '') + p.get('xstop_kind', 'SL')
        try:
            await self.venue.cancel(sym, p['tp_cid'])
        except Exception:
            pass
        try:
            await self.venue.cancel_stop(p['sl_cid'])
        except Exception:
            pass
        if hasattr(self.venue, 'realized_parts'):
            parts = await self.venue.realized_parts(sym, p['opened_ms'])
        else:
            net = await self.venue.realized(sym, p['opened_ms'])
            parts = {'gross': net, 'fee': 0.0, 'net': net}
        pnl = parts['net']
        d = 1 if p['side'] == 'BUY' else -1
        notional = p['entry'] * p['qty']
        row = {'t': self.clock(), 'day': datetime.now().strftime('%Y-%m-%d'), 'mode': self.p.mode,
               'venue': self.venue.label, 'symbol': sym, 'side': p['side'], 'qty': p['qty'], 'entry': p['entry'],
               'exit': p['entry'] * (1 + d * (parts['gross'] / notional)) if notional else None,
               'gross': parts['gross'], 'fee': parts['fee'], 'pnl': pnl,
               'fee_bps': parts['fee'] / notional * 1e4 if notional else None,
               'maker_fills': parts.get('maker_fills'), 'taker_fills': parts.get('taker_fills'),
               'pnl_pct': pnl / (notional / self.p.leverage) * 100 if notional else 0.0, 'reason': reason,
               'be_locked': p['be_locked'], 'mbe_locked': p.get('mbe_locked'), 'tp1': p['tp1_filled'],
               'mfe_pct': p['mfe_pct'], 'adverse_usd': p.get('adverse_usd'), 'regime': p.get('regime'),
               'exit_mode': 'maker' if x else 'exchange', 'exit_reprices': (x or {}).get('reprices'),
               'tag': p['tag'], 'path': p['path'], 'score': p['score'], 'edge_bps': p.get('edge_bps'),
               'ker': p.get('ker'), 'flow_limit': p.get('flow_limit'), 'macro': p.get('macro'),
               'px_vel_15s': p.get('px_vel_15s'), 'plock': p.get('plock', False), 'hold_s': self.clock() - p['opened'],
               'latency_ms': p['latency_ms'], 'entry_type': p.get('entry_type', 'GTX')}
        self.pos = None
        self.phase = 'SCANNING'
        self.day_pnl += pnl
        self.history.appendleft(row)
        self.all_rows.append(row)
        self._write_ledger(row)
        self.cooldown_until = self.clock() + self.p.cooldown_s
        if pnl < 0 and p['tag'] == 'AUTO':
            self.consec_losses += 1
            if self.consec_losses >= self.p.max_consec_losses:
                self.pause_until = self.clock() + self.p.loss_pause_s
        elif pnl > 0:
            self.consec_losses = 0
        self.event(f'CLOSED {row["side"]} {row["qty"]:g} {sym} {reason} gross {parts["gross"]:+.4f} fee '
                   f'{-parts["fee"]:+.4f} net {pnl:+.4f} U ({row["pnl_pct"]:+.2f}% ROE) in {row["hold_s"]:.0f}s '
                   f'· day {self.day_pnl:+.4f} U')

    # ------------------------------------------------------------------ operator controls
    async def force_test_trade(self, side: str = 'BUY') -> dict:
        """Demo only: one post-only GTX entry through VESKA (swarm bypassed, labelled), full MARIN exits."""
        if self.p.mode != 'demo':
            return {'ok': False, 'error': 'FORCE TEST chỉ cho phép ở chế độ DEMO'}
        if side not in ('BUY', 'SELL'):
            return {'ok': False, 'error': 'side must be BUY or SELL'}
        if not self.started:
            return {'ok': False, 'error': 'venue chưa sẵn sàng'}
        if self.pos or self.busy or abs(self.view.get('amt') or 0):
            return {'ok': False, 'error': 'đang có vị thế / lệnh - đóng trước khi test'}
        reg = msig.regime(self.feed.features(self.clock()), self.sig_cfg)
        if side not in reg['allow']:
            self.event(f'FORCE TEST {side} refused: {reg["label"]} · {reg["why"]}')
            return {'ok': False, 'error': f'CẤM {side}: {reg["label"]} ({reg["why"]}) - thử chiều '
                                          f'{reg["allow"][0]}', 'regime': reg['name']}
        d = Decision(side, 1.0, True, False, 'OPERATOR FORCE TEST (swarm bypassed)', .768, 'NORMAL')
        self.event(f'FORCE TEST {side} requested from dashboard (Post-Only GTX + BE lock + TP1/trail)')
        async with self.lock:
            res = await self.execute(side, d, 'OPERATOR', tag='TEST', hold_s=self.p.test_hold_s)
        return res

    async def close_now(self) -> dict:
        if not (self.pos or abs(self.view.get('amt') or 0)):
            return {'ok': False, 'error': 'không có vị thế'}
        async with self.lock:
            await self.close('MANUAL')
        return {'ok': True}

    # ------------------------------------------------------------------ telemetry
    def agent_rows(self) -> list:
        votes = {v.agent: v for v in self.votes}
        rows = []
        for name in ('VESKA', 'NORO', 'LUMEN', 'TIDAL', 'ZEPHR', 'RUNE', 'OKAPI', 'MARIN'):
            v = votes.get(name)
            side, conf = (v.side, v.confidence) if v else ('HOLD', 0.0)
            veto = bool(v and v.veto)
            if not self.started:
                state, label = 'VETO' if name == 'VESKA' else 'SCANNING', \
                    ('VENUE OFFLINE' if self.last_error else 'CONNECTING VENUE') if name == 'VESKA' else 'WAITING VENUE'
            elif self.phase == 'EXECUTING':
                dside = self.decision.side if self.decision else side
                state, label = 'EXECUTING', 'EXECUTING ORDER' if name == 'VESKA' else f'AUTHORIZED {dside}'
            elif self.pos:
                state = 'POSITION'
                ps = self.pos
                marin = ('TRAILING RUNNER' if ps.get('trail_on') else 'BREAKEVEN LOCKED' if ps.get('be_locked')
                         else 'MICRO-BE LOCKED' if ps.get('mbe_locked')
                         else f'{"LONG" if ps["side"] == "BUY" else "SHORT"} OPEN (TP1: +{ps["tp_pct"]:.2f}%)')
                label = (marin if name == 'MARIN' else
                         f'GUARD {ps.get("stop_kind", "SL")} {ps["sl"]:g}' if name == 'RUNE' else
                         f'FILLED {self.pos["side"]} {self.pos["qty_str"]}' if name == 'VESKA' else
                         f'HOLDING {self.pos["side"]}')
            elif veto:
                state, label = 'VETO', (v.reason if v else 'veto')[:48]
            elif side in ('BUY', 'SELL'):
                state, label = 'VOTING', f'VOTING {side} ({conf * 100:.0f}%)'
            else:
                state, label = 'SCANNING', 'SCANNING'
            rows.append({'agent': name, 'side': side, 'veto': veto, 'confidence': conf,
                         'reason': v.reason if v else 'boot', 'state': state, 'label': label})
        return rows

    def position_row(self) -> Optional[dict]:
        v = self.view
        amt = v.get('amt') or 0.0
        if not amt:
            return None
        entry, mark = v.get('entry') or 0.0, v.get('mark') or self.features.get('mark') or 0.0
        margin = v.get('margin') or abs(amt) * entry / self.p.leverage
        upnl = v.get('upnl') or 0.0
        p = self.pos or {}
        return {'symbol': self.p.symbol, 'side': 'LONG' if amt > 0 else 'SHORT', 'size': abs(amt),
                'entry': entry, 'mark': mark, 'upnl': upnl, 'roe_pct': upnl / margin * 100 if margin else 0.0,
                'liq': v.get('liq') or None, 'leverage': self.p.leverage,
                'margin_type': 'Cross' if self.p.margin_type == 'CROSSED' else 'Isolated', 'margin': margin,
                'tp': p.get('tp'), 'sl': p.get('sl'), 'age_s': self.clock() - p['opened'] if p else None,
                'stop_kind': p.get('stop_kind'), 'be_locked': p.get('be_locked'), 'trail_on': p.get('trail_on'),
                'mbe_locked': p.get('mbe_locked'), 'adverse_usd': p.get('adverse_usd'),
                'soft_stop': p.get('soft_stop'), 'soft_exits': self.p.soft_exits,
                'exiting': None if not p.get('exiting') else {k: p['exiting'][k] for k in
                                                               ('reason', 'px', 'reprices', 'started')},
                'flow_exit_usd': p.get('flow_limit') or self.feed.cfg.flow_exit_usd * self.feed.cfg.usd_scale,
                'breath_left_s': max(0.0, self.p.flow_breath_s - (self.clock() - p['opened'])) if p else None,
                'regime_flip_usd': self.p.regime_flip_usd * self.feed.cfg.usd_scale,
                'shield': bool(p.get('be_locked')), 'plock': bool(p.get('plock')),
                'fee_shield_trigger_pct': self.p.be_trigger_pct, 'lock_trigger_pct': self.p.lock_trigger_pct,
                'microbe_trigger_pct': self.p.microbe_trigger_pct,
                'tp1_filled': p.get('tp1_filled'), 'tp_qty': p.get('tp_qty'), 'mfe_pct': p.get('mfe_pct'),
                'be_price': self.be_price(p) if p and self.spec else None,
                'be_trigger_pct': self.p.be_trigger_pct, 'entry_type': p.get('entry_type'),
                'hold_s': p.get('hold_s'), 'tag': p.get('tag', 'EXTERNAL'), 'path': p.get('path')}

    def fee_model(self) -> Optional[dict]:
        px = (self.features or {}).get('price')
        if not px or not self.spec:
            return None
        pl = plan('BUY', px, self.spec, self.p, None)
        if not pl.get('ok'):
            return {'ok': False, 'why': pl.get('why')}
        n = pl['notional']
        return {'ok': True, 'qty': pl['qty_str'], 'notional': n, 'maker_rt_usdt': 2 * n * self.p.maker_bps / 1e4,
                'taker_exit_usdt': n * (self.p.maker_bps + self.p.taker_bps) / 1e4,
                'fee_breakeven_pct': 2 * self.p.maker_bps / 100}

    def snapshot(self) -> dict:
        d, f = self.decision, self.features
        acc = self.account or {}
        lat = sorted(self.latency)
        return {
            'mode': f'{self.p.mode.upper()} · {self.venue.label}', 'engine_mode': self.p.mode,
            'venue': self.venue.label, 'symbol': self.p.symbol,
            'status': self.halted or (f'POSITION {self.pos["side"]} {self.pos["qty_str"]} {self.p.symbol} · TP {self.pos["tp"]:g} '
                                      f'· SL {self.pos["sl"]:g} · {self.pos["tag"]}' if self.pos else
                                      'ORDER IN FLIGHT' if self.busy else (d.reason if d else
                                      f'VENUE OFFLINE · {self.last_error}' if self.last_error else 'CONNECTING VENUE')),
            'phase': self.phase,
            'armed': self.started, 'equity': acc.get('wallet'), 'wallet': acc.get('wallet'),
            'available': acc.get('available'), 'pnl': self.day_pnl + (acc.get('upnl') or 0.0),
            'day_pnl': self.day_pnl, 'volume': 0, 'fees': None,
            'score': d.score if d else 0.0, 'threshold': .768, 'veto_threshold': .70,
            'agents': self.agent_rows(), 'authorized': self.authorized, 'auth_at': self.auth_at,
            'decision': None if d is None else {'side': d.side, 'score': d.score, 'allowed': d.allowed,
                                                'reason': d.reason, **{k: self.diag.get(k) for k in
                                                                     ('jev', 'edge_bps', 'path', 'side',
                                                                      'confluence')}},
            'signals': {k: v for k, v in self.sig.items()},
            'regime': {k: v for k, v in (self.reg or {}).items()},
            'features': {k: f.get(k) for k in ('price', 'mark', 'bid', 'ask', 'spread_bps', 'obi', 'cvd_norm',
                                               'cvd_60s', 'cvd_10s', 'vwap', 'vwap_dev_pct', 'range_pos',
                                               'liq_long_usd', 'liq_short_usd', 'stale', 'trades_silent',
                                               'book_age_s', 'trade_age_s', 'trades', 'cvd_3m', 'cvd_vel_10s',
                                               'cvd_vel_60s', 'depth10_imb', 'box_pct', 'box_lo', 'box_hi',
                                               'ker', 'ker_span_s', 'cvd_15s', 'px_vel_15s', 'vwap_5m',
                                               'vwap_5m_span', 'ema5_20', 'ema5_slope', 'ema5_n')},
            'absorption': self.absorb, 'macro': {k: v for k, v in self.macro.items() if k != 'allow'},
            'chop': {'ker': f.get('ker'), 'locked': self.chop_locked, 'gate': self.p.ker_gate,
                     'lock': self.sig_cfg.ker_lock, 'unlock': self.sig_cfg.ker_unlock,
                     'reason': self.chop_reason() if self.chop_locked else None},
            'market_data': 'binance-ws' if not f.get('stale') else 'stale',
            'position': self.position_row(), 'open_orders': self.orders, 'trade_history': list(self.history)[:30],
            'session': {'trades': len(self.history), 'wins': sum(1 for r in self.history if (r.get('pnl') or 0) > 0),
                        'realized': self.day_pnl, 'kill': self.p.daily_kill_usdt,
                        'gross': sum(r.get('gross', r.get('pnl') or 0) or 0 for r in self.history),
                        'fees': sum(r.get('fee') or 0 for r in self.history)},
            'cost': self.cost.view(),
            'stats': self.stats(), 'stats_today': self.stats(list(self.history)),
            'exec_book': {k: v for k, v in self.book().items()} if self.spec else None,
            'profile': asdict(self.p), 'live_allowed': self.allow_live, 'symbols': list(SYMBOLS),
            'fee_model': self.fee_model(),
            'governor': {'min_score': self.p.min_score, 'min_edge_bps': self.p.min_edge_bps,
                         'max_trades_hour': self.p.max_trades_hour, 'trades_hour': len(self.trade_times),
                         'cooldown_s': self.p.cooldown_s, 'flow_breath_s': self.p.flow_breath_s,
                         'flow_exit_usd': self.feed.cfg.flow_exit_usd * self.feed.cfg.usd_scale,
                         'regime_flip_usd': self.p.regime_flip_usd * self.feed.cfg.usd_scale},
            'latency_ms': {'last': self.latency[-1] if self.latency else None,
                           'p50': lat[len(lat) // 2] if lat else None},
            'markets': [], 'events': list(self.events)[:30], 'updated_at': self.clock(),
        }


# ============================================================================ runner (one process: WS + engine + UI)
def fetch_klines_5m(symbol: str, limit: int = 40, timeout: float = 5.0) -> list:
    """Closed 5 m candles [(close_time_s, close)] from Binance USD-M mainnet public REST."""
    import urllib.request
    url = f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol.upper()}&interval=5m&limit={int(limit)}'
    with urllib.request.urlopen(url, timeout=timeout) as r:
        rows = json.loads(r.read().decode())
    now_ms = time.time() * 1000
    return [(k[6] / 1000.0, float(k[4])) for k in rows if k[6] < now_ms]


class Runner:
    def __init__(self, mode: str, allow_live: bool, env: Optional[dict] = None, symbol: Optional[str] = None,
                 port: int = 8765, serve: bool = True, overrides: Optional[dict] = None):
        self.env = read_env() if env is None else env
        self.allow_live, self.symbol, self.port, self.serve = allow_live, symbol, port, serve
        self.overrides = overrides or {}
        self.mode = self.start_mode = mode
        self.symbols = {mode: symbol.upper()} if symbol else {}
        self.engine: Optional[Engine] = None
        self.client = None
        self.tasks: list = []
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.tel = None
        self.ws = {'connected': False, 'error': '', 'reconnects': 0}

    def profile(self, mode: str) -> Profile:
        p = PROFILES[mode]
        sym = self.symbols.get(mode)
        if sym and sym != p.symbol:
            p = replace(p, symbol=sym, qty=SYMBOL_QTY.get(sym, 0.0) if mode == 'demo' else p.qty)
        if mode != self.start_mode:                       # CLI overrides belong to the mode they were typed for
            return p
        return replace(p, **{k: v for k, v in self.overrides.items() if v is not None})

    def live_ready(self) -> tuple:
        key, sec = credentials('live', self.env)
        if not self.allow_live:
            return False, 'khởi động bằng "run.bat live" (gõ xác nhận) mới mở được LIVE'
        if not (key and sec):
            return False, 'thiếu BINANCE_MAINNET_API_KEY / SECRET_KEY trong .env.local'
        return True, 'ok'

    async def build(self, mode: str) -> Engine:
        from live.binance_futures_client import BinanceFuturesClient
        p = self.profile(mode)
        feed = msig.MicroFeed(p.symbol, msig.config_for(p.symbol))
        key, sec = credentials(mode, self.env)
        if mode == 'demo' and not (key and sec):
            venue = MockVenue(p, quote=lambda s: (feed.bid or feed.price() or 0.0, feed.ask or feed.price() or 0.0))
        elif mode == 'demo':
            env = dict(self.env, BINANCE_TESTNET_API_KEY=key, BINANCE_TESTNET_SECRET_KEY=sec)
            self.client = await BinanceFuturesClient(Settings.load('testnet', env=env)).__aenter__()
            venue = BinanceVenue(self.client, p.margin_type)
        else:
            ok, why = self.live_ready()
            if not ok:
                raise PermissionError(why)
            env = dict(self.env, BINANCE_MAINNET_API_KEY=key, BINANCE_MAINNET_SECRET_KEY=sec)
            self.client = await BinanceFuturesClient(Settings.load('mainnet', arm=True, env=env)).__aenter__()
            venue = LiveVenue(self.client, p.margin_type)
        return Engine(p, venue, feed, self.tel, ledger=LEDGER, allow_live=self.allow_live)

    async def pump(self, feed: msig.MicroFeed, route: str = 'market') -> None:
        import websockets
        backoff = 1.0
        url = msig.streams(feed.symbol)[route]
        while True:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20,
                                              open_timeout=8, max_queue=4096) as ws:
                    self.ws.update(connected=True, error='')
                    self.ws[route] = True
                    backoff = 1.0
                    async for raw in ws:
                        try:
                            feed.on_message(json.loads(raw))
                        except ValueError:
                            continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.ws.update(connected=False, error=f'{route}: {type(exc).__name__}: {exc}'[:160])
            self.ws[route] = False
            self.ws['reconnects'] += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 15.0)

    async def decide_loop(self, e: Engine) -> None:
        while True:
            if e.started and e.feed.last_msg:
                try:
                    await e.tick()
                except Exception as exc:
                    e.event(f'tick error {type(exc).__name__}: {exc}')
            await asyncio.sleep(0.2)

    async def vbook_loop(self, e: Engine) -> None:
        while True:
            if e.started and e.own_book:
                await e.refresh_book(0.0)
                await asyncio.sleep(0.3 if (e.pos or e.busy) else 1.0)
            else:
                await asyncio.sleep(1.0)

    async def sync_loop(self, e: Engine) -> None:
        period = 0.5 if isinstance(e.venue, MockVenue) else 1.2
        while True:
            try:
                if not e.started:
                    await e.start()
                await e.locked_sync()
            except Exception as exc:
                e.last_error = f'{type(exc).__name__}: {exc}'[:160]
                e.event(f'sync error {e.last_error}')
                await asyncio.sleep(3)
            await asyncio.sleep(period)

    async def publish_loop(self) -> None:
        n = 0
        while True:
            e = self.engine
            if e is not None and self.tel is not None:
                snap = e.snapshot()
                live_ok, live_why = self.live_ready()
                snap.update(ws=dict(self.ws), live_ready=live_ok, live_reason=live_why,
                            demo_keys=all(credentials('demo', self.env)))
                self.tel.update(**snap)
                n += 1
                if n % 2 == 0:
                    self.tel.persist()
                if self.tel.state.get('shutdown'):
                    e.event('KILL SWITCH -> flatten + halt')
                    e.halted = 'KILL SWITCH'
                    try:
                        await e.venue.flatten(e.p.symbol)
                    except Exception as exc:
                        e.event(f'flatten error {exc}')
                    await asyncio.sleep(1.0)
                    raise SystemExit(0)
            await asyncio.sleep(0.5)

    async def activate(self, mode: str) -> Engine:
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []
        if self.client is not None:
            await self.client.__aexit__(None, None, None)
            self.client = None
        e = await self.build(mode)
        self.mode, self.engine = mode, e
        self.tasks = [asyncio.create_task(self.seed_macro(e)),
                      asyncio.create_task(self.pump(e.feed, 'public')), asyncio.create_task(self.pump(e.feed, 'market')),
                      asyncio.create_task(self.sync_loop(e)), asyncio.create_task(self.vbook_loop(e)),
                      asyncio.create_task(self.decide_loop(e))]
        return e

    async def seed_macro(self, e: Engine) -> None:
        """EMA-20(5 m) needs 100 min of candles: seed it once from mainnet public klines (no key)."""
        try:
            closes = await asyncio.to_thread(fetch_klines_5m, e.feed.symbol)
            n = e.feed.seed_candles(closes)
            e.event(f'MACRO 5m: seeded EMA-20 with {n} closed 5 m candles (VWAP 5 m warms from live prints)')
        except Exception as exc:
            e.event(f'MACRO 5m: kline seed failed ({type(exc).__name__}) -> EMA warms live (100 min)')

    async def switch_mode(self, mode: str, confirm: str = '') -> dict:
        if mode not in MODES:
            return {'ok': False, 'error': 'mode must be demo or live'}
        e = self.engine
        if mode == self.mode:
            return {'ok': True, 'mode': mode, 'note': 'đang ở chế độ này'}
        if e and (e.pos or e.busy or abs(e.view.get('amt') or 0)):
            return {'ok': False, 'error': 'đóng vị thế trước khi đổi chế độ'}
        if mode == 'live':
            ok, why = self.live_ready()
            if not ok:
                return {'ok': False, 'error': why}
            if confirm != LIVE_CONFIRM:
                return {'ok': False, 'error': f'gõ "{LIVE_CONFIRM}" để xác nhận tiền thật'}
        try:
            await self.activate(mode)
        except Exception as exc:
            await self.activate('demo')
            return {'ok': False, 'error': f'{type(exc).__name__}: {exc}'[:200]}
        return {'ok': True, 'mode': mode}

    async def switch_symbol(self, symbol: str) -> dict:
        symbol = str(symbol or '').upper()
        if symbol not in SYMBOLS:
            return {'ok': False, 'error': f'symbol must be one of {", ".join(SYMBOLS)}'}
        e = self.engine
        if e and e.p.symbol == symbol:
            return {'ok': True, 'symbol': symbol, 'note': 'đang dùng symbol này'}
        if e and (e.pos or e.busy or abs(e.view.get('amt') or 0)):
            return {'ok': False, 'error': 'đóng vị thế trước khi đổi coin'}
        old = self.symbols.get(self.mode)
        self.symbols[self.mode] = symbol
        try:
            await self.activate(self.mode)
        except Exception as exc:
            self.symbols[self.mode] = old
            await self.activate(self.mode)
            return {'ok': False, 'error': f'{type(exc).__name__}: {exc}'[:200]}
        return {'ok': True, 'symbol': symbol}

    async def run(self) -> None:
        from core.web_ui_server import Telemetry, make_server
        import threading
        self.loop = asyncio.get_running_loop()
        self.tel = Telemetry()
        if self.serve:
            server = make_server(self.tel, port=self.port, shutdown_file=SHUTDOWN_FILE, controller=Controller(self))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            print(f'  UI  http://127.0.0.1:{self.port}', flush=True)
        await self.activate(self.mode)
        await self.publish_loop()


class Controller:
    """Thread-safe bridge: HTTP handler thread -> engine event loop."""

    def __init__(self, runner: Runner):
        self.r = runner

    def _call(self, coro, timeout: float = 20.0) -> dict:
        try:
            return asyncio.run_coroutine_threadsafe(coro, self.r.loop).result(timeout)
        except Exception as exc:
            return {'ok': False, 'error': f'{type(exc).__name__}: {exc}'[:200]}

    def force_test_trade(self, body: dict) -> dict:
        if self.r.engine is None:
            return {'ok': False, 'error': 'engine chưa chạy'}
        return self._call(self.r.engine.force_test_trade(str(body.get('side', 'BUY')).upper()))

    def switch_mode(self, body: dict) -> dict:
        return self._call(self.r.switch_mode(str(body.get('mode', '')).lower(), str(body.get('confirm', ''))), 60)

    def switch_symbol(self, body: dict) -> dict:
        return self._call(self.r.switch_symbol(body.get('symbol', '')), 60)

    def close_position(self, body: dict) -> dict:
        if self.r.engine is None:
            return {'ok': False, 'error': 'engine chưa chạy'}
        return self._call(self.r.engine.close_now())


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description='GROKTOPUS 2-mode engine: demo | live')
    ap.add_argument('--mode', choices=MODES, default='demo')
    ap.add_argument('--symbol', default=None, help='default demo BTCUSDT, live DOGEUSDT')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--arm-live', default='', help=f'must equal "{LIVE_CONFIRM}" to allow mainnet orders')
    ap.add_argument('--tp', type=float, default=None, help='TP1 %% (50 %% of size, rest trails)')
    ap.add_argument('--sl', type=float, default=None, help='stop %% (demo; live derives it from max loss)')
    ap.add_argument('--hold', type=float, default=None, help='optional max hold seconds (default: none)')
    ap.add_argument('--jev-min', type=float, default=None)
    a = ap.parse_args(argv)
    allow_live = (a.arm_live or os.environ.get('GROKTOPUS_ARM_LIVE', '')) == LIVE_CONFIRM   # typed per session
    if a.mode == 'live' and not allow_live:
        raise SystemExit(f'LIVE bị khoá: chạy run.bat live và gõ {LIVE_CONFIRM} để xác nhận.')
    overrides = {'tp_pct': a.tp, 'sl_pct': a.sl if a.mode == 'demo' else None,
                 'max_hold_s': a.hold, 'jev_min': a.jev_min}
    print('=' * 86)
    print(f'  GROKTOPUS ENGINE · mode {a.mode.upper()} · live {"ARMED" if allow_live else "LOCKED"} · Ctrl+C dừng')
    print('=' * 86, flush=True)
    r = Runner(a.mode, allow_live, symbol=a.symbol, port=a.port, overrides=overrides)
    try:
        asyncio.run(r.run())
    except KeyboardInterrupt:
        print('\n[STOP] Ctrl+C. Vị thế (nếu có) vẫn được STOP_MARKET trên sàn bảo vệ.')


if __name__ == '__main__':
    main()
