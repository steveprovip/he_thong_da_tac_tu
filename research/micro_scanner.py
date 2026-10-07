"""Multi-asset tick-level micro-alpha SHADOW scanner (zero orders sent).

Why this replaces the single-asset flush engine for live feedback
-----------------------------------------------------------------
`flush_shadow` watched one symbol (DOGE) and waited for a closed 1m bar with a
>=12 bps flush, 1.25x volume and a non-RANGE 15m regime. In a 0.02 % range tape
that conjunction never fires: 30+ minutes, zero trades. The bottleneck is
structural (one asset x one slow bar-based trigger), not a threshold.

This engine scans N perpetuals at once on raw ticks:
  * OBI(L5)   = (sum bid qty L1..5 - sum ask qty) / total, time-EMA (tau 1 s).
  * CVD(60 s) = signed aggressor quote volume; "burst" when the last 15 s trade
                rate >= 1.5x the rolling 5 min rate AND |CVD60/gross60| >= 0.20.
  * Lead-lag  = BTC/ETH mid moved >= 6 bps in 10 s while the alt moved < 35 % of
                that in the same direction -> vote the leader's direction.
Consensus: >=2 agreeing votes (a strong lead-lag, >=1.5x threshold, counts 2),
zero opposing votes, spread <= 4 bps, symbol routable for the account size.

Execution model (shadow, honest fills):
  * Entry = post-only GTX limit at the touch (bid for long / ask for short).
    Fill only on trade-through, book-through, or queue consumed at our price;
    TTL 5 s then cancel. Unfilled intents are counted (fill rate on the board).
  * Exit = triple barrier per branch on the SAME fill:
      TP   +39 bps gross maker (= +35 net after 2 x 2 bps)
      SL   -20 bps STOP_MARKET (taker 5 bps + 2 bps slippage -> -29 net)
      TIME 180 s market exit (taker + slippage)
    Branches: BASELINE (no lock) | BE_15_10 (MFE>=15 -> SL +10 gross = +1 net)
              | BE_20_10 (MFE>=20 -> SL +10).
  * Account: 3.85 USDT, isolated 2x..3x, notional just above MIN_NOTIONAL,
    margin <= 2.6 USDT, worst-case loss per trade <= 0.05 USDT.
  * Kill: primary branch (BE_15_10) mark-to-market loss >= 0.05 USDT -> flatten
    every branch and halt.

Signals are unvalidated. Prior L2 study in this repo (phan_tich/markout_l2.py)
found maker markout of -0.7..-1.2 bps; treat every PnL here as a measurement,
not a promise. Nothing in this module talks to an order endpoint.

Run:  python -m research.micro_scanner [--seconds 900] [--symbols ...]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import time
import urllib.request
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Deque, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
TELEMETRY_STATE_FILE = ROOT / 'data' / 'telemetry_state.json'
TRADE_LOG_FILE = ROOT / 'data' / 'micro_scanner_trades.jsonl'
SHUTDOWN_FILE = ROOT / 'data' / 'shutdown_request.json'

UNIVERSE = ('DOGEUSDT', '1000PEPEUSDT', 'SOLUSDT', 'XRPUSDT', 'SUIUSDT',
            'BNBUSDT', 'BTCUSDT', 'ETHUSDT')
LEADERS = ('BTCUSDT', 'ETHUSDT')
WS_BASES = {'mainnet': 'wss://fstream.binance.com',
            'demo': 'wss://demo-fstream.binance.com'}
REST_BASES = {'mainnet': 'https://fapi.binance.com',
              'demo': 'https://demo-fapi.binance.com'}

STEP_SECONDS = 0.25
PUBLISH_SECONDS = 1.0
FEED_STALE_SECONDS = 3.0
MID_SAMPLE_SECONDS = 0.1
MID_HISTORY_SECONDS = 30.0
PRIMARY = 'be_15_10'
BRANCH_SPECS = (('baseline', None, None), ('be_15_10', 15.0, 10.0), ('be_20_10', 20.0, 10.0))
BRANCH_LABELS = {'baseline': 'BASELINE · SL −20 · TIME 180s',
                 'be_15_10': 'BE_15_10 · MFE +15 → SL +10 (+1 net)',
                 'be_20_10': 'BE_20_10 · MFE +20 → SL +10 (+1 net)'}


# --------------------------------------------------------------------------- config
@dataclass(frozen=True)
class ScannerConfig:
    equity: float = 3.85                 # ~100k VND
    kill_loss_usdt: float = 0.05         # session hard kill (primary branch MTM)
    max_trade_loss_usdt: float = 0.05    # worst-case single-trade loss cap
    leverage_choices: Tuple[int, ...] = (2, 3)
    margin_cap_usdt: float = 2.6         # keeps >= 1.25 USDT free buffer
    notional_buffer: float = 1.02        # stay clear of MIN_NOTIONAL after rounding
    maker_fee_bps: float = 2.0
    taker_fee_bps: float = 5.0
    slippage_bps: float = 2.0
    tp_gross_bps: float = 39.0           # +35 net
    sl_bps: float = 20.0
    max_hold_s: float = 180.0
    entry_ttl_s: float = 5.0
    max_spread_bps: float = 4.0
    obi_threshold: float = 0.55
    obi_tau_s: float = 1.0
    cvd_window_s: float = 60.0
    cvd_fast_s: float = 15.0
    cvd_baseline_s: float = 300.0
    cvd_imbalance: float = 0.20
    cvd_burst_ratio: float = 1.5
    lead_window_s: float = 10.0
    lead_move_bps: float = 6.0
    lag_fraction: float = 0.35
    strong_lead_mult: float = 1.5
    min_votes: int = 2
    cooldown_s: float = 20.0
    warmup_s: float = 15.0


# --------------------------------------------------------------------------- specs & sizing
@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    tick: float
    step: float
    min_qty: float
    min_notional: float


# Fallback only; `fetch_specs` replaces these from /fapi/v1/exchangeInfo at boot.
DEFAULT_SPECS: Dict[str, SymbolSpec] = {s.symbol: s for s in (
    SymbolSpec('DOGEUSDT', 0.00001, 1.0, 1.0, 5.0),
    SymbolSpec('1000PEPEUSDT', 0.0000001, 1.0, 1.0, 5.0),
    SymbolSpec('SOLUSDT', 0.01, 0.01, 0.01, 5.0),
    SymbolSpec('XRPUSDT', 0.0001, 0.1, 0.1, 5.0),
    SymbolSpec('SUIUSDT', 0.0001, 0.1, 0.1, 5.0),
    SymbolSpec('BNBUSDT', 0.01, 0.01, 0.01, 5.0),
    SymbolSpec('BTCUSDT', 0.1, 0.001, 0.001, 100.0),
    SymbolSpec('ETHUSDT', 0.01, 0.001, 0.001, 20.0),
)}


def _decimals(step: float) -> int:
    s = f'{step:.12f}'.rstrip('0')
    return len(s.split('.')[1]) if '.' in s else 0


def round_tick(price: float, tick: float) -> float:
    return round(round(price / tick) * tick, _decimals(tick))


def ceil_step(qty: float, step: float) -> float:
    return round(math.ceil(round(qty / step, 9)) * step, _decimals(step))


def parse_exchange_info(info: dict, symbols: Iterable[str]) -> Dict[str, SymbolSpec]:
    wanted = set(symbols)
    out: Dict[str, SymbolSpec] = {}
    for row in (info or {}).get('symbols', []):
        sym = row.get('symbol')
        if sym not in wanted:
            continue
        f = {x.get('filterType'): x for x in row.get('filters', [])}
        try:
            out[sym] = SymbolSpec(sym, float(f['PRICE_FILTER']['tickSize']),
                                  float(f['LOT_SIZE']['stepSize']), float(f['LOT_SIZE']['minQty']),
                                  float(f.get('MIN_NOTIONAL', {}).get('notional', 5.0)))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def fetch_specs(symbols: Iterable[str], rest_base: str = REST_BASES['mainnet'],
                timeout: float = 5.0) -> Tuple[Dict[str, SymbolSpec], str]:
    symbols = list(symbols)
    specs = {s: DEFAULT_SPECS[s] for s in symbols if s in DEFAULT_SPECS}
    try:
        with urllib.request.urlopen(f'{rest_base}/fapi/v1/exchangeInfo', timeout=timeout) as r:
            live = parse_exchange_info(json.loads(r.read()), symbols)
        specs.update(live)
        return specs, f'exchangeInfo ({len(live)}/{len(symbols)})'
    except Exception as exc:  # network down -> static table, said so on the board
        return specs, f'static fallback ({type(exc).__name__})'


@dataclass(frozen=True)
class OrderPlan:
    symbol: str
    qty: float
    notional: float
    leverage: int
    margin: float
    worst_loss_usdt: float


def plan_order(spec: SymbolSpec, price: float, cfg: ScannerConfig) -> Tuple[Optional[OrderPlan], str]:
    """Smallest order that clears MIN_NOTIONAL and fits a 3.85 USDT isolated account."""
    if price <= 0:
        return None, 'NO_PRICE'
    qty = ceil_step(max(spec.min_notional * cfg.notional_buffer / price, spec.min_qty), spec.step)
    notional = qty * price
    lev = next((lv for lv in sorted(cfg.leverage_choices)
                if notional / lv <= min(cfg.margin_cap_usdt, cfg.equity)), None)
    if lev is None:
        return None, 'MIN_NOTIONAL'          # e.g. BTC 100 USDT floor: lead signal only
    worst = notional * (cfg.sl_bps + cfg.slippage_bps + cfg.maker_fee_bps + cfg.taker_fee_bps) / 1e4
    if worst > cfg.max_trade_loss_usdt:
        return None, 'RISK_CAP'
    return OrderPlan(spec.symbol, qty, notional, lev, notional / lev, worst), 'OK'


# --------------------------------------------------------------------------- per-symbol book
class SymbolBook:
    """Top of book, L5 OBI (time-EMA), per-second signed flow buckets, mid history."""

    def __init__(self, symbol: str, cfg: ScannerConfig):
        self.symbol, self.cfg = symbol, cfg
        self.bid = self.ask = 0.0
        self.bid_qty = self.ask_qty = 0.0
        self.obi: Optional[float] = None
        self.obi_ema: Optional[float] = None
        self.obi_source = '—'
        self._obi_t: Optional[float] = None
        self._has_depth = False
        self.buckets: Deque[List[float]] = deque()      # [sec, signed_quote, gross_quote]
        self.mids: Deque[Tuple[float, float]] = deque()
        self.first_seen: Optional[float] = None
        self.last_update = 0.0
        self.updates = 0
        self.trades = 0
        self.trade_listeners: List = []

    # ---- inputs
    def _touch(self, now: float) -> None:
        if self.first_seen is None:
            self.first_seen = now
        self.last_update = now
        self.updates += 1
        mid = self.mid
        if mid and (not self.mids or now - self.mids[-1][0] >= MID_SAMPLE_SECONDS):
            self.mids.append((now, mid))
        while self.mids and now - self.mids[0][0] > MID_HISTORY_SECONDS:
            self.mids.popleft()

    def _set_obi(self, value: float, now: float, source: str) -> None:
        self.obi, self.obi_source = value, source
        if self.obi_ema is None or self._obi_t is None:
            self.obi_ema = value
        else:
            dt = max(0.0, now - self._obi_t)
            a = 1.0 - math.exp(-dt / self.cfg.obi_tau_s) if self.cfg.obi_tau_s > 0 else 1.0
            self.obi_ema += a * (value - self.obi_ema)
        self._obi_t = now

    def on_depth(self, bids, asks, now: float) -> bool:
        if not bids or not asks:
            return False
        bid, bq = float(bids[0][0]), float(bids[0][1])
        ask, aq = float(asks[0][0]), float(asks[0][1])
        if bid <= 0 or ask <= 0 or ask < bid:
            return False
        self.bid, self.ask, self.bid_qty, self.ask_qty = bid, ask, bq, aq
        b5 = sum(float(q) for _, q in bids[:5])
        a5 = sum(float(q) for _, q in asks[:5])
        if b5 + a5 > 0:
            self._has_depth = True
            self._set_obi((b5 - a5) / (b5 + a5), now, 'L5')
        self._touch(now)
        return True

    def on_book_ticker(self, bid: float, bq: float, ask: float, aq: float, now: float) -> bool:
        if bid <= 0 or ask <= 0 or ask < bid:
            return False
        self.bid, self.ask, self.bid_qty, self.ask_qty = bid, ask, bq, aq
        if not self._has_depth and bq + aq > 0:          # replay / bookTicker-only fallback
            self._set_obi((bq - aq) / (bq + aq), now, 'L1')
        self._touch(now)
        return True

    def on_trade(self, price: float, qty: float, buyer_is_maker: bool, now: float) -> None:
        quote = price * qty
        signed = -quote if buyer_is_maker else quote     # m=True: seller aggressed
        sec = math.floor(now)
        if self.buckets and self.buckets[-1][0] == sec:
            self.buckets[-1][1] += signed
            self.buckets[-1][2] += quote
        else:
            self.buckets.append([sec, signed, quote])
        while self.buckets and now - self.buckets[0][0] > self.cfg.cvd_baseline_s + 1:
            self.buckets.popleft()
        self.trades += 1
        for fn in self.trade_listeners:
            fn(self.symbol, price, qty, buyer_is_maker, now)

    # ---- derived
    @property
    def mid(self) -> Optional[float]:
        return (self.bid + self.ask) / 2.0 if self.bid > 0 and self.ask > 0 else None

    @property
    def spread_bps(self) -> Optional[float]:
        m = self.mid
        return (self.ask - self.bid) / m * 1e4 if m else None

    def fresh(self, now: float) -> bool:
        return self.last_update > 0 and now - self.last_update < FEED_STALE_SECONDS

    def warm(self, now: float) -> bool:
        return self.first_seen is not None and now - self.first_seen >= self.cfg.warmup_s

    def ret_bps(self, window: float, now: float) -> Optional[float]:
        """Mid return over [now-window, now]; None until the history spans the window."""
        if not self.mids or self.mid is None:
            return None
        target = now - window
        if self.mids[0][0] > target:
            return None
        past = None
        for t, m in self.mids:
            if t > target:
                break
            past = m
        return (self.mid / past - 1.0) * 1e4 if past else None

    def flow(self, now: float) -> dict:
        c = self.cfg
        cvd = gross = cvd_f = gross_f = gross_b = 0.0
        oldest = None
        for sec, s, g in self.buckets:
            age = now - sec
            if age > c.cvd_baseline_s:
                continue
            oldest = sec if oldest is None else oldest
            gross_b += g
            if age <= c.cvd_window_s:
                cvd += s
                gross += g
            if age <= c.cvd_fast_s:
                cvd_f += s
                gross_f += g
        span = min(c.cvd_baseline_s, now - oldest) if oldest is not None else 0.0
        burst = None
        if span >= c.cvd_window_s and gross_b > 0:
            burst = (gross_f / c.cvd_fast_s) / (gross_b / span)
        return {'cvd_60s': cvd, 'gross_60s': gross, 'cvd_fast': cvd_f,
                'imbalance': (cvd / gross) if gross > 0 else None, 'burst': burst}


# --------------------------------------------------------------------------- signal
@dataclass
class Signal:
    symbol: str
    side: Optional[str]          # 'BUY' | 'SELL' | None
    votes: int                   # agreeing weight (strong lead counts 2)
    opposed: int
    score: float                 # 0..1 for the board
    signed: float                # -1..1
    components: dict
    blockers: List[str] = field(default_factory=list)
    status: str = 'SCANNING'


def _leader_move(books: Dict[str, SymbolBook], leaders: Iterable[str], cfg: ScannerConfig,
                 now: float) -> Tuple[Optional[str], Optional[float]]:
    best, best_r = None, None
    for s in leaders:
        b = books.get(s)
        if b is None or not b.fresh(now):
            continue
        r = b.ret_bps(cfg.lead_window_s, now)
        if r is not None and (best_r is None or abs(r) > abs(best_r)):
            best, best_r = s, r
    return best, best_r


def evaluate(book: SymbolBook, books: Dict[str, SymbolBook], leaders: Iterable[str],
             cfg: ScannerConfig, now: float) -> Signal:
    comp: dict = {'obi': book.obi_ema, 'obi_src': book.obi_source}
    votes: Dict[str, int] = {}
    # OBI L5
    if book.obi_ema is not None and abs(book.obi_ema) >= cfg.obi_threshold:
        votes['obi'] = 1 if book.obi_ema > 0 else -1
    # CVD burst
    fl = book.flow(now)
    comp.update(cvd_60s=fl['cvd_60s'], cvd_imb=fl['imbalance'], burst=fl['burst'])
    imb, burst = fl['imbalance'], fl['burst']
    if (imb is not None and burst is not None and abs(imb) >= cfg.cvd_imbalance
            and burst >= cfg.cvd_burst_ratio and fl['cvd_fast'] * imb > 0):
        votes['cvd'] = 1 if imb > 0 else -1
    # Lead-lag (alts only)
    weight = {'obi': 1, 'cvd': 1, 'lead': 1}
    if book.symbol not in leaders:
        lead_sym, r_lead = _leader_move(books, leaders, cfg, now)
        r_alt = book.ret_bps(cfg.lead_window_s, now)
        comp.update(lead=lead_sym, r_lead=r_lead, r_alt=r_alt)
        if r_lead is not None and r_alt is not None and abs(r_lead) >= cfg.lead_move_bps:
            d = 1 if r_lead > 0 else -1
            if d * r_alt < cfg.lag_fraction * abs(r_lead):
                votes['lead'] = d
                if abs(r_lead) >= cfg.strong_lead_mult * cfg.lead_move_bps:
                    weight['lead'] = 2
    comp['votes'] = dict(votes)
    up = sum(weight[k] for k, v in votes.items() if v > 0)
    dn = sum(weight[k] for k, v in votes.items() if v < 0)
    side = 'BUY' if up > dn else 'SELL' if dn > up else None
    agree, opposed = (up, dn) if side == 'BUY' else (dn, up) if side == 'SELL' else (0, 0)
    score = min(1.0, agree / 3.0)
    signed = (score if side == 'BUY' else -score if side == 'SELL' else 0.0)
    sig = Signal(book.symbol, side, agree, opposed, score, signed, comp)
    if side and agree >= cfg.min_votes and opposed == 0:
        sig.status = 'SIGNAL'
    return sig


# --------------------------------------------------------------------------- branches
@dataclass
class Position:
    symbol: str
    side: str
    entry_time: float
    entry_price: float
    qty: float
    tick: float
    tp: float
    sl: float
    orig_sl: float
    trigger_bps: Optional[float]
    protect_bps: Optional[float]
    max_mfe_bps: float = 0.0
    protected: bool = False

    @property
    def d(self) -> int:
        return -1 if self.side == 'SELL' else 1


@dataclass
class Trade:
    branch: str
    symbol: str
    side: str
    entry_time: float
    exit_time: float
    entry_price: float
    exit_price: float
    qty: float
    gross_bps: float
    fee_bps: float
    net_bps: float
    pnl_usdt: float
    reason: str

    @property
    def hold_s(self) -> float:
        return self.exit_time - self.entry_time


class Branch:
    """One stop rule applied to the shared cohort entries."""

    def __init__(self, name: str, trigger_bps: Optional[float], protect_bps: Optional[float],
                 cfg: ScannerConfig):
        self.name, self.trigger_bps, self.protect_bps, self.cfg = name, trigger_bps, protect_bps, cfg
        self.pos: Optional[Position] = None
        self.trades: List[Trade] = []
        self.counts = {'TP': 0, 'PROTECT': 0, 'SL': 0, 'TIME': 0, 'KILL': 0}
        self.watchers: List[Tuple[Position, float]] = []
        self.premature = 0
        self.good_protect = 0

    def open(self, symbol: str, side: str, price: float, qty: float, tick: float, now: float) -> None:
        if self.pos is not None:
            raise RuntimeError(f'{self.name}: refusing to overwrite an open position')
        if side not in ('BUY', 'SELL'):
            raise ValueError(f'Unknown side {side!r}')
        d = -1 if side == 'SELL' else 1
        c = self.cfg
        tp = round_tick(price * (1 + d * c.tp_gross_bps / 1e4), tick)
        sl = round_tick(price * (1 - d * c.sl_bps / 1e4), tick)
        self.pos = Position(symbol, side, now, price, qty, tick, tp, sl, sl,
                            self.trigger_bps, self.protect_bps)

    def _close(self, exit_p: float, fee_bps: float, reason: str, now: float) -> Trade:
        p = self.pos
        gross = p.d * (exit_p / p.entry_price - 1.0) * 1e4
        net = gross - fee_bps
        t = Trade(self.name, p.symbol, p.side, p.entry_time, now, p.entry_price, exit_p, p.qty,
                  gross, fee_bps, net, net / 1e4 * p.entry_price * p.qty, reason)
        self.trades.append(t)
        self.counts[reason] += 1
        self.pos = None
        return t

    def _watch(self, bid: float, ask: float, now: float, symbol: str) -> None:
        """After a breakeven exit: did price reach TP (premature) or the old SL (good call)?"""
        keep = []
        for p, t_exit in self.watchers:
            if p.symbol != symbol:
                keep.append((p, t_exit))
                continue
            q = ask if p.d < 0 else bid
            if p.d * (q - p.tp) >= 0:
                self.premature += 1
            elif p.d * (q - p.orig_sl) <= 0:
                self.good_protect += 1
            elif now - t_exit <= self.cfg.max_hold_s:
                keep.append((p, t_exit))
        self.watchers = keep

    def update(self, symbol: str, bid: float, ask: float, now: float) -> Optional[Trade]:
        if self.watchers:
            self._watch(bid, ask, now, symbol)
        p = self.pos
        if p is None or p.symbol != symbol or bid <= 0 or ask <= 0:
            return None
        c = self.cfg
        d = p.d
        q = ask if d < 0 else bid                       # the side this position exits on
        slip = c.slippage_bps / 1e4 * p.entry_price
        taker = c.maker_fee_bps + c.taker_fee_bps
        if d * (q - p.sl) <= 0:                          # stop first (conservative)
            worst = min(q, p.sl) if d > 0 else max(q, p.sl)
            protect = p.protected and d * (p.sl - p.entry_price) > 0
            if protect:
                self.watchers.append((p, now))
            return self._close(round_tick(worst - d * slip, p.tick), taker,
                               'PROTECT' if protect else 'SL', now)
        if d * (q - p.tp) >= 0:
            return self._close(p.tp, 2 * c.maker_fee_bps, 'TP', now)
        if now - p.entry_time >= c.max_hold_s:
            return self._close(round_tick(q - d * slip, p.tick), taker, 'TIME', now)
        mfe = d * (q / p.entry_price - 1.0) * 1e4
        p.max_mfe_bps = max(p.max_mfe_bps, mfe)
        if p.trigger_bps is not None and not p.protected and p.max_mfe_bps >= p.trigger_bps:
            new_sl = round_tick(p.entry_price * (1 + d * p.protect_bps / 1e4), p.tick)
            if d * (new_sl - p.sl) > 0:                  # only ever tighten
                p.sl, p.protected = new_sl, True
        return None

    def force_exit(self, bid: float, ask: float, now: float, reason: str = 'KILL') -> Optional[Trade]:
        p = self.pos
        if p is None or bid <= 0 or ask <= 0:
            return None
        q = ask if p.d < 0 else bid
        exit_p = round_tick(q - p.d * self.cfg.slippage_bps / 1e4 * p.entry_price, p.tick)
        return self._close(exit_p, self.cfg.maker_fee_bps + self.cfg.taker_fee_bps, reason, now)

    def realized(self) -> float:
        return sum(t.pnl_usdt for t in self.trades)

    def unrealized(self, bid: float, ask: float) -> float:
        """Liquidation value of the open position (taker exit + slippage)."""
        p = self.pos
        if p is None or bid <= 0 or ask <= 0:
            return 0.0
        c = self.cfg
        q = ask if p.d < 0 else bid
        gross = p.d * (q / p.entry_price - 1.0) * 1e4
        net = gross - c.slippage_bps - c.maker_fee_bps - c.taker_fee_bps
        return net / 1e4 * p.entry_price * p.qty

    def stats(self) -> dict:
        n = len(self.trades)
        nets = [t.net_bps for t in self.trades]
        mean = sum(nets) / n if n else 0.0
        lower95 = -999.0
        if n >= 2:
            var = sum((x - mean) ** 2 for x in nets) / (n - 1)
            lower95 = mean - 1.96 * math.sqrt(var / n)
        n_prot = self.counts['PROTECT']
        n_sl = self.counts['SL'] + self.counts['KILL']
        return {
            'name': self.name, 'total_trades': n,
            'tp_count': self.counts['TP'], 'protect_count': n_prot,
            'sl_count': n_sl, 'time_count': self.counts['TIME'],
            'p_tp': self.counts['TP'] / n if n else 0.0,
            'p_protect': n_prot / n if n else 0.0,
            'p_sl': n_sl / n if n else 0.0,
            # Measured mean net bps per closed trade; there is no analytical shortcut
            # once a time barrier exists, so the board shows what was earned.
            'analytical_ev_bps': mean, 'empirical_ev_bps': mean, 'lower95_ev_bps': lower95,
            'premature_count': self.premature,
            'premature_rate': self.premature / n_prot if n_prot else 0.0,
            'cum_pnl_usdt': self.realized(), 'posterior_ev_bps': None, 'p_ev_positive': None,
            'fees_usdt': sum(t.fee_bps / 1e4 * t.entry_price * t.qty for t in self.trades),
        }


# --------------------------------------------------------------------------- pending maker entry
@dataclass
class PendingEntry:
    symbol: str
    side: str
    price: float
    plan: OrderPlan
    placed_at: float
    queue_ahead: float
    signal: dict
    filled_at: Optional[float] = None


def maker_fill_on_trade(pe: PendingEntry, price: float, qty: float, buyer_is_maker: bool) -> bool:
    """GTX at the touch fills on trade-through, or once the queue ahead is consumed."""
    if pe.side == 'BUY':
        if price < pe.price:
            return True
        if price == pe.price and buyer_is_maker:        # sellers hitting our bid level
            pe.queue_ahead -= qty
    else:
        if price > pe.price:
            return True
        if price == pe.price and not buyer_is_maker:
            pe.queue_ahead -= qty
    return pe.queue_ahead < 0


def maker_fill_on_book(pe: PendingEntry, bid: float, ask: float) -> bool:
    """Book moved through our level: the resting order must have been taken."""
    if pe.side == 'BUY':
        return (0 < bid < pe.price) or (0 < ask <= pe.price)
    return ask > pe.price or (0 < bid and bid >= pe.price)


# --------------------------------------------------------------------------- engine
class MicroScanner:
    def __init__(self, cfg: ScannerConfig = ScannerConfig(), specs: Optional[Dict[str, SymbolSpec]] = None,
                 symbols: Iterable[str] = UNIVERSE, leaders: Iterable[str] = LEADERS,
                 state_file: Path = TELEMETRY_STATE_FILE, trade_log: Optional[Path] = None):
        self.cfg = cfg
        self.symbols = list(dict.fromkeys(symbols))
        self.leaders = [s for s in leaders if s in self.symbols]
        self.specs = dict(specs or {s: DEFAULT_SPECS[s] for s in self.symbols if s in DEFAULT_SPECS})
        self.books = {s: SymbolBook(s, cfg) for s in self.symbols}
        for b in self.books.values():
            b.trade_listeners.append(self._on_trade)
        self.branches = {n: Branch(n, trg, prot, cfg) for n, trg, prot in BRANCH_SPECS}
        self.pending: Optional[PendingEntry] = None
        self.cooldown_until: Dict[str, float] = {}
        self.signals: Dict[str, Signal] = {}
        self.plans: Dict[str, Tuple[Optional[OrderPlan], str]] = {}
        self.intents = self.fills = self.cancels = 0
        self.cohorts: List[dict] = []
        self.killed = False
        self.kill_reason = ''
        self.status = 'WARMUP'
        self.events: Deque[dict] = deque(maxlen=40)
        self.history: Deque[dict] = deque(maxlen=600)
        self.state_file = state_file
        self.trade_log = trade_log
        self.feed_label = 'binance-ws'
        self.spec_source = 'static'
        self.started_at: Optional[float] = None
        self._peak = cfg.equity

    # ---- feed
    def on_message(self, msg: dict, now: float) -> bool:
        if not isinstance(msg, dict):
            return False
        data = msg.get('data', msg)
        if not isinstance(data, dict):
            return False
        sym = data.get('s') or str(msg.get('stream', '')).split('@')[0].upper()
        book = self.books.get(sym)
        if book is None:
            return False
        ev = data.get('e', '')
        try:
            if ev == 'depthUpdate':
                return book.on_depth(data.get('b') or [], data.get('a') or [], now)
            if ev == 'bookTicker':
                return book.on_book_ticker(float(data['b']), float(data['B']),
                                           float(data['a']), float(data['A']), now)
            if ev == 'aggTrade':
                book.on_trade(float(data['p']), float(data['q']), bool(data.get('m')), now)
                return True
        except (KeyError, TypeError, ValueError, IndexError):
            return False
        return False

    def _on_trade(self, symbol: str, price: float, qty: float, buyer_is_maker: bool, now: float) -> None:
        pe = self.pending
        if pe is not None and pe.symbol == symbol and now > pe.placed_at and not self.killed:
            if maker_fill_on_trade(pe, price, qty, buyer_is_maker):
                self._fill(now)

    # ---- helpers
    def _event(self, now: float, message: str) -> None:
        self.events.appendleft({'t': now, 'message': message[:240]})

    def active_symbol(self) -> Optional[str]:
        for b in self.branches.values():
            if b.pos is not None:
                return b.pos.symbol
        return self.pending.symbol if self.pending else None

    def flat(self) -> bool:
        return self.pending is None and all(b.pos is None for b in self.branches.values())

    def equity(self, name: str = PRIMARY) -> float:
        b = self.branches[name]
        u = 0.0
        if b.pos is not None:
            bk = self.books[b.pos.symbol]
            u = b.unrealized(bk.bid, bk.ask)
        return self.cfg.equity + b.realized() + u

    def _fill(self, now: float) -> None:
        pe = self.pending
        self.pending = None
        pe.filled_at = now
        self.fills += 1
        spec = self.specs[pe.symbol]
        for b in self.branches.values():
            b.open(pe.symbol, pe.side, pe.price, pe.plan.qty, spec.tick, now)
        self.cohorts.append({'symbol': pe.symbol, 'side': pe.side, 'price': pe.price,
                             'qty': pe.plan.qty, 'lev': pe.plan.leverage, 'placed_at': pe.placed_at,
                             'filled_at': now, 'signal': pe.signal})
        self._event(now, f'FILL {pe.symbol} {pe.side} {pe.plan.qty:g} @ {pe.price:g} '
                         f'(maker GTX, {now - pe.placed_at:.1f}s, {pe.plan.leverage}x, '
                         f'notional {pe.plan.notional:.2f}U)')

    def _log_trade(self, t: Trade) -> None:
        if self.trade_log is None:
            return
        cohort = self.cohorts[-1] if self.cohorts else {}
        row = {**asdict(t), 'hold_s': t.hold_s, 'signal': cohort.get('signal')}
        try:
            self.trade_log.parent.mkdir(parents=True, exist_ok=True)
            with open(self.trade_log, 'a', encoding='utf-8') as fh:
                fh.write(json.dumps(_json_safe(row), default=str) + '\n')
        except OSError:
            pass

    # ---- main step
    def step(self, now: float) -> str:
        if self.started_at is None:
            self.started_at = now
        if self.killed:
            self.status = 'HALTED'
            return self.status
        c = self.cfg
        # 1. pending maker entry: book-through fill or TTL cancel
        pe = self.pending
        if pe is not None:
            bk = self.books[pe.symbol]
            if now > pe.placed_at and maker_fill_on_book(pe, bk.bid, bk.ask):
                self._fill(now)
            elif now - pe.placed_at >= c.entry_ttl_s:
                self.pending = None
                self.cancels += 1
                self.cooldown_until[pe.symbol] = now + c.cooldown_s / 2
                self._event(now, f'CANCEL {pe.symbol} {pe.side} GTX @ {pe.price:g}: '
                                 f'unfilled in {c.entry_ttl_s:.0f}s (price left)')
        # 2. open branches
        sym = self.active_symbol()
        if sym is not None and self.pending is None:
            bk = self.books[sym]
            if bk.fresh(now):
                for b in self.branches.values():
                    t = b.update(sym, bk.bid, bk.ask, now)
                    if t is not None:
                        self._on_close(t, now)
                if self.flat():
                    self.cooldown_until[sym] = now + c.cooldown_s
        # 3. kill switch on primary mark-to-market
        eq = self.equity()
        self._peak = max(self._peak, eq)
        if eq <= c.equity - c.kill_loss_usdt:
            self._kill(now, f'KILL: primary equity {eq:.4f} <= {c.equity - c.kill_loss_usdt:.4f} '
                            f'(loss >= {c.kill_loss_usdt} USDT)')
            return self.status
        # 4. scan every symbol
        best: Optional[Signal] = None
        active = self.active_symbol()
        for s, bk in self.books.items():
            if bk.fresh(now):
                sig = evaluate(bk, self.books, self.leaders, c, now)
            else:
                sig = Signal(s, None, 0, 0, 0.0, 0.0, {}, status='STALE' if bk.last_update else 'WAITING')
            plan, why = plan_order(self.specs[s], bk.mid or 0.0, c) if s in self.specs else (None, 'NO_SPEC')
            self.plans[s] = (plan, why)
            if sig.status in ('STALE', 'WAITING'):
                pass
            elif not bk.warm(now):
                sig.status = 'WARMUP'
            elif plan is None:
                sig.blockers.append(why)
                sig.status = 'LEAD_ONLY' if s in self.leaders else 'BLOCKED'
            if sig.status == 'SIGNAL':
                sp = bk.spread_bps
                if sp is None or sp > c.max_spread_bps:
                    sig.blockers.append('WIDE_SPREAD')
                if now < self.cooldown_until.get(s, 0.0):
                    sig.blockers.append('COOLDOWN')
                if not self.flat():
                    sig.blockers.append('BUSY')
                if sig.blockers:
                    sig.status = 'SIGNAL_BLOCKED'
                elif best is None or (sig.votes, abs(sig.components.get('obi') or 0)) > \
                        (best.votes, abs(best.components.get('obi') or 0)):
                    best = sig
            if s == active:
                sig.status = 'ENTRY_PENDING' if self.pending else 'IN_POSITION'
            self.signals[s] = sig
        # 5. act on the best signal: post-only GTX at the touch
        if best is not None and self.flat():
            bk = self.books[best.symbol]
            plan = self.plans[best.symbol][0]
            price = bk.bid if best.side == 'BUY' else bk.ask
            queue = bk.bid_qty if best.side == 'BUY' else bk.ask_qty
            self.pending = PendingEntry(best.symbol, best.side, price, plan, now, queue,
                                        dict(best.components))
            self.intents += 1
            best.status = 'ENTRY_PENDING'
            v = best.components.get('votes', {})
            self._event(now, f'SIGNAL {best.symbol} {best.side} votes {"+".join(sorted(v))} '
                             f'-> GTX {plan.qty:g} @ {price:g} ({plan.leverage}x, margin {plan.margin:.2f}U)')
        n_live = sum(1 for b in self.books.values() if b.fresh(now))
        sym = self.active_symbol()
        self.status = (f'ENTRY_PENDING {sym}' if self.pending else f'IN_POSITION {sym}' if sym
                       else 'SCANNING' if n_live else 'WAITING_FEED')
        return self.status

    def _on_close(self, t: Trade, now: float) -> None:
        self._log_trade(t)
        if t.branch == PRIMARY or t.reason == 'KILL':
            self._event(now, f'CLOSE[{t.branch}] {t.symbol} {t.side} {t.reason} {t.net_bps:+.1f} bps '
                             f'{t.pnl_usdt:+.4f}U in {t.hold_s:.0f}s')

    def _kill(self, now: float, reason: str) -> None:
        self.pending = None
        for b in self.branches.values():
            if b.pos is not None:
                bk = self.books[b.pos.symbol]
                t = b.force_exit(bk.bid, bk.ask, now, 'KILL')
                if t is not None:
                    self._on_close(t, now)
        self.killed, self.kill_reason, self.status = True, reason, 'HALTED'
        self._event(now, reason)

    def shutdown(self, now: float, reason: str) -> None:
        if not self.killed:
            self._kill(now, reason)

    # ---- telemetry
    def snapshot(self, now: float) -> dict:
        c = self.cfg
        eq = self.equity()
        self.history.append({'t': now, 'value': round(eq, 6)})
        prim = self.branches[PRIMARY]
        markets = []
        for s in self.symbols:
            bk, sig = self.books[s], self.signals.get(s)
            plan, why = self.plans.get(s, (None, ''))
            fl = bk.flow(now) if bk.last_update else {}
            mine = [t for t in prim.trades if t.symbol == s]
            comp = sig.components if sig else {}
            votes = comp.get('votes', {})
            markets.append({
                'symbol': s, 'mid': bk.mid, 'spread': bk.spread_bps,
                'obi': bk.obi_ema, 'vpin': None, 'cvd_60s': fl.get('cvd_60s'),
                'score': sig.score if sig else 0.0,
                'directional': (sig.signed + 1) / 2 if sig else 0.5,
                'side': (sig.side or 'HOLD') if sig else 'HOLD',
                'edge_bps': (sum(t.gross_bps for t in mine) / len(mine)) if mine else None,
                'cost_bps': (sum(t.fee_bps for t in mine) / len(mine)) if mine else None,
                'reasons': list(sig.blockers) if sig else [],
                'status': sig.status if sig else 'WAITING',
                'votes': votes, 'r_lead': comp.get('r_lead'), 'r_alt': comp.get('r_alt'),
                'burst': comp.get('burst'), 'cvd_imb': comp.get('cvd_imb'),
                'routable': plan is not None,
                'plan': asdict(plan) if plan else None,
                'why': (f'OBI {bk.obi_source} ema {_f(bk.obi_ema, "+.2f")} (|x|>={c.obi_threshold})'
                        f' · CVD60 {_f(fl.get("cvd_60s"), "+.0f")}U imb {_f(comp.get("cvd_imb"), "+.2f")}'
                        f' burst {_f(comp.get("burst"), ".2f")}x'
                        f' · lead {comp.get("lead") or "—"} {_f(comp.get("r_lead"), "+.1f")}bps'
                        f' vs alt {_f(comp.get("r_alt"), "+.1f")}bps'
                        f' · votes {votes or "none"}'
                        + ('' if plan else f' · {why}')
                        + (f' · {plan.qty:g} @ {plan.leverage}x = {plan.notional:.2f}U' if plan else '')),
                'trades': len(mine),
            })
        positions = []
        if self.pending is not None:
            pe = self.pending
            positions.append({'symbol': pe.symbol, 'branch': 'pending', 'side': pe.side,
                              'size': pe.plan.qty, 'entry': pe.price, 'pending': True,
                              'age_s': now - pe.placed_at, 'ttl_s': c.entry_ttl_s,
                              'queue_ahead': pe.queue_ahead})
        for b in self.branches.values():
            p = b.pos
            if p is None:
                continue
            bk = self.books[p.symbol]
            q = bk.ask if p.d < 0 else bk.bid
            positions.append({'symbol': p.symbol, 'branch': b.name, 'side': p.side, 'size': p.qty,
                              'entry': p.entry_price, 'tp': p.tp, 'sl': p.sl,
                              'pnl_bps': p.d * (q / p.entry_price - 1) * 1e4 if q else None,
                              'mfe_bps': p.max_mfe_bps, 'protected': p.protected,
                              'age_s': now - p.entry_time, 'max_hold_s': c.max_hold_s})
        recent = sorted((t for b in self.branches.values() for t in b.trades),
                        key=lambda t: t.exit_time, reverse=True)[:30]
        fees = sum(t.fee_bps / 1e4 * t.entry_price * t.qty for t in prim.trades)
        volume = sum(2 * t.entry_price * t.qty for t in prim.trades)
        n = len(prim.trades)
        uptime = now - self.started_at if self.started_at else 0.0
        return {
            'mode': f'SHADOW · MULTI-ASSET MICRO-ALPHA · {len(self.symbols)} MARKETS · {self.feed_label.upper()}',
            'status': self.status if not self.killed else f'HALTED · {self.kill_reason}',
            'equity': round(eq, 4), 'wallet': round(c.equity + prim.realized(), 4),
            'pnl': round(eq - c.equity, 4), 'volume': round(volume, 2), 'fees': round(fees, 4),
            'score': max((m['score'] for m in markets), default=0.0),
            'drawdown': round(max(0.0, (self._peak - eq) / self._peak), 6) if self._peak > 0 else None,
            'kill_distance_usdt': round(eq - (c.equity - c.kill_loss_usdt), 4),
            'outcome_samples': n, 'armed': False, 'shutdown': False, 'shutdown_reason': '',
            'authorized': self.fills, 'execute_gate': c.min_votes / 3.0, 'veto_gate': 1 / 3.0,
            'threshold': c.min_votes / 3.0, 'veto_threshold': 1 / 3.0,
            'agents': [], 'market_data': self.feed_label,
            'policy': f'OBI{c.obi_threshold}+CVD+LEADLAG ≥{c.min_votes}/3 · specs {self.spec_source}',
            'branches': {k: b.stats() for k, b in self.branches.items()},
            'branch_labels': BRANCH_LABELS,
            'scanner': {'intents': self.intents, 'fills': self.fills, 'cancels': self.cancels,
                        'fill_rate': self.fills / self.intents if self.intents else None,
                        'uptime_s': uptime,
                        'trades_per_hour': n / (uptime / 3600.0) if uptime > 60 else None},
            'markets': markets, 'positions': positions,
            'trades': [{**asdict(t), 'hold_s': t.hold_s} for t in recent],
            'events': list(self.events), 'timestamp': now, 'updated_at': now,
            'history': list(self.history),
        }

    def publish(self, now: float) -> None:
        state = _json_safe(self.snapshot(now))
        path = self.state_file
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.json.tmp')
        try:
            tmp.write_text(json.dumps(state, allow_nan=False), encoding='utf-8')
            os.replace(tmp, path)                        # atomic on Windows and POSIX
        except (OSError, ValueError):
            try:
                tmp.unlink()
            except OSError:
                pass


def _f(v, fmt: str) -> str:
    return format(v, fmt) if isinstance(v, (int, float)) and math.isfinite(v) else '—'


def _json_safe(obj):
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


# --------------------------------------------------------------------------- runner
def ws_url(symbols: Iterable[str], base: str = WS_BASES['mainnet']) -> str:
    streams = []
    for s in symbols:
        s = s.lower()
        streams += [f'{s}@depth5@100ms', f'{s}@aggTrade']
    return f'{base}/stream?streams=' + '/'.join(streams)


def shutdown_requested(since: float, path: Path = SHUTDOWN_FILE) -> bool:
    try:
        return path.stat().st_mtime >= since
    except OSError:
        return False


async def _pump(url: str, scanner: MicroScanner, stats: dict) -> None:
    import websockets
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20, open_timeout=8,
                                          max_queue=8192, max_size=2 ** 22) as ws:
                stats['connected'], backoff = True, 1.0
                async for raw in ws:
                    try:
                        scanner.on_message(json.loads(raw), time.time())
                    except ValueError:
                        continue
        except asyncio.CancelledError:
            stats['connected'] = False
            raise
        except Exception as exc:
            stats['last_error'] = f'{type(exc).__name__}: {exc}'[:160]
        stats['connected'] = False
        stats['reconnects'] = stats.get('reconnects', 0) + 1
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2.0, 15.0)


def _console_line(sc: MicroScanner, now: float) -> str:
    prim = sc.branches[PRIMARY]
    c = prim.counts
    hot = sorted((s for s in sc.signals.values() if s.side), key=lambda s: -s.votes)[:3]
    hot_s = ' '.join(f'{s.symbol.replace("USDT", "")}:{s.side[0]}{s.votes}' for s in hot) or '—'
    pos = ''
    if prim.pos is not None:
        p, bk = prim.pos, sc.books[prim.pos.symbol]
        q = bk.ask if p.d < 0 else bk.bid
        pos = (f' | POS {p.symbol} {p.side} {p.d * (q / p.entry_price - 1) * 1e4:+.1f}bps '
               f'{now - p.entry_time:.0f}s{" BE" if p.protected else ""}')
    elif sc.pending is not None:
        pos = f' | GTX {sc.pending.symbol} {sc.pending.side} @ {sc.pending.price:g}'
    live = sum(1 for b in sc.books.values() if b.fresh(now))
    return (f'[{time.strftime("%H:%M:%S")}] live {live}/{len(sc.books)} | hot {hot_s}{pos} | '
            f'intents {sc.intents} fills {sc.fills} | trades {len(prim.trades)} '
            f'(TP{c["TP"]}/BE{c["PROTECT"]}/SL{c["SL"]}/T{c["TIME"]}) | eq {sc.equity():.4f}U')


async def run(cfg: ScannerConfig, symbols: List[str], seconds: Optional[float] = None,
              venue: str = 'mainnet') -> MicroScanner:
    specs, source = await asyncio.to_thread(fetch_specs, symbols, REST_BASES[venue])
    sc = MicroScanner(cfg, specs, symbols, trade_log=TRADE_LOG_FILE)
    sc.spec_source, sc.feed_label = source, f'binance-{venue}-ws'
    url = ws_url(symbols, WS_BASES[venue])
    print('=' * 88)
    print('  MULTI-ASSET MICRO-ALPHA SHADOW SCANNER  (zero orders sent)')
    print(f'  Markets: {", ".join(symbols)}  | leaders {", ".join(sc.leaders) or "none"}')
    print(f'  Account {cfg.equity} USDT · isolated {"/".join(map(str, cfg.leverage_choices))}x · '
          f'kill {cfg.kill_loss_usdt} USDT · specs: {source}')
    for s in symbols:
        sp = specs.get(s)
        print(f'    {s:<14} tick {sp.tick:g} step {sp.step:g} minNotional {sp.min_notional:g}'
              if sp else f'    {s:<14} NO SPEC')
    print(f'  Trigger: |OBI L5|>={cfg.obi_threshold} · CVD burst {cfg.cvd_burst_ratio}x imb>={cfg.cvd_imbalance}'
          f' · lead {cfg.lead_move_bps}bps/{cfg.lead_window_s:.0f}s · need {cfg.min_votes}/3 votes')
    print(f'  Barrier: TP +{cfg.tp_gross_bps:g} gross (+{cfg.tp_gross_bps - 2 * cfg.maker_fee_bps:g} net) · '
          f'SL -{cfg.sl_bps:g} · TIME {cfg.max_hold_s:.0f}s · BE 15/20 -> +10')
    print(f'  UI: http://127.0.0.1:8765   ·   log: {TRADE_LOG_FILE}')
    print('=' * 88)
    stats: dict = {}
    feed = asyncio.create_task(_pump(url, sc, stats))
    t0, wall0 = time.monotonic(), time.time()
    last_pub = last_print = 0.0
    last_status = None
    try:
        while True:
            now = time.time()
            status = sc.step(now)
            mono = time.monotonic()
            if status != last_status or mono - last_pub >= PUBLISH_SECONDS:
                sc.publish(now)
                last_pub = mono
            if mono - last_print >= 5.0 or (status != last_status and not status.startswith('SCAN')):
                print(_console_line(sc, now) + ('' if stats.get('connected') else
                                                f' | WS DOWN {stats.get("last_error", "")}'))
                last_print = mono
            last_status = status
            if sc.killed:
                print(f'\n[HALTED] {sc.kill_reason}')
                break
            if shutdown_requested(wall0):
                sc.shutdown(now, 'KILL SWITCH: dashboard shutdown request')
                print('\n[SHUTDOWN] dashboard kill switch -> shadow positions flattened')
                break
            if seconds and mono - t0 >= seconds:
                print(f'\n[DONE] {seconds:.0f}s run complete')
                break
            await asyncio.sleep(STEP_SECONDS)
    finally:
        feed.cancel()
        try:
            await feed
        except (asyncio.CancelledError, Exception):
            pass
        sc.publish(time.time())
        print(summary(sc))
    return sc


def summary(sc: MicroScanner) -> str:
    lines = ['-' * 88, f'  intents {sc.intents} · maker fills {sc.fills} · cancels {sc.cancels}']
    for name, b in sc.branches.items():
        s = b.stats()
        lines.append(f'  {name:<9} n={s["total_trades"]:<3} TP {s["tp_count"]} BE {s["protect_count"]} '
                     f'SL {s["sl_count"]} TIME {s["time_count"]} | mean {s["empirical_ev_bps"]:+.2f} bps '
                     f'L95 {s["lower95_ev_bps"]:+.1f} | pnl {s["cum_pnl_usdt"]:+.4f}U')
    lines.append('  Promotion rule: lower95(mean net bps) > 0 over >= 200 trades before any real order.')
    lines.append('-' * 88)
    return '\n'.join(lines)


def build_config(args: argparse.Namespace) -> ScannerConfig:
    return ScannerConfig(equity=args.equity, kill_loss_usdt=args.kill, tp_gross_bps=args.tp,
                         sl_bps=args.sl, max_hold_s=args.max_hold, obi_threshold=args.obi,
                         lead_move_bps=args.lead_bps, min_votes=args.min_votes,
                         max_spread_bps=args.max_spread)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description='Multi-asset tick-level micro-alpha SHADOW scanner')
    ap.add_argument('--seconds', type=float, default=None, help='stop after N seconds (default: until Ctrl+C)')
    ap.add_argument('--symbols', default=','.join(UNIVERSE))
    ap.add_argument('--venue', choices=sorted(WS_BASES), default='mainnet',
                    help='public market-data source (no key used)')
    ap.add_argument('--equity', type=float, default=3.85)
    ap.add_argument('--kill', type=float, default=0.05)
    ap.add_argument('--tp', type=float, default=39.0, help='TP gross bps (net = tp - 4)')
    ap.add_argument('--sl', type=float, default=20.0)
    ap.add_argument('--max-hold', type=float, default=180.0)
    ap.add_argument('--obi', type=float, default=0.55)
    ap.add_argument('--lead-bps', type=float, default=6.0)
    ap.add_argument('--min-votes', type=int, default=2)
    ap.add_argument('--max-spread', type=float, default=4.0)
    return ap


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    symbols = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    try:
        asyncio.run(run(build_config(args), symbols, args.seconds, args.venue))
    except KeyboardInterrupt:
        print('\n[STOP] Ctrl+C. Zero orders were sent.')


if __name__ == '__main__':
    main()
