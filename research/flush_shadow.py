"""DOGEUSDT 1m Volatility Flush / Liquidation Spike Shadow Engine.

Micro-Capital Paradigm:
- Starting capital: 100,000 VND (~3.85 USDT).
- Target: DOGEUSDT on Binance Futures USD-M.
- Position sizing: 61 DOGE (~5.07 USDT notional, passes MIN_NOTIONAL >= 5.0 filter).
- Leverage: Isolated 2x (Margin ~2.54 USDT, Buffer ~1.31 USDT).
- Cost structure: Maker 2 bps / Taker 5 bps / Market SL Slippage 2 bps.
  - TP Net: +39.67 - 4.0 = +35.67 bps.
  - SL Net: -20.44 - 2.0 (slippage) - 7.0 (2 maker + 5 taker fee) = -29.44 bps.
  - Protection Net (+10 bps): +10.0 - 2.0 (slippage) - 7.0 (fee) = +1.0 bps.
- 3-Outcome EV Model:
  EV = 35.67 * p_TP + 1.0 * p_protect - 29.44 * p_SL > 0
- Kill Switch: Abort immediately if mark-to-market liquidation equity loss >= 0.05 USDT.
- Parallel Shadow Branches on identical entry:
  - BASELINE: Fixed SL -20.44 bps (no protection)
  - BE_15_10: Trigger MFE >= 15.0 bps -> Move SL to +10.0 bps (Primary Candidate)
  - BE_20_10: Trigger MFE >= 20.0 bps -> Move SL to +10.0 bps (Benchmark)
- Premature stopout tracking & statistical gating: lower95(EV) > 0 out-of-sample.
"""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import random
from collections import deque
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import sys
import time
import urllib.request
from typing import Dict, List, Optional, Tuple

from research import flush_regime as regime_mod
from core.early_stop import DataBudget, EarlyStopGate
from research import flush_risk as risk_mod


SYMBOL = 'DOGEUSDT'
STARTING_EQUITY = 3.85       # ~100k VND
KILL_LOSS_USDT = 0.05        # 5 cents
MIN_NOTIONAL = 5.0           # Binance USD-M minimum order value
LOT_STEP = 1.0               # Step size for DOGE
TICK_SIZE = 0.00001          # Tick size for DOGE
LEVERAGE = 2.0
TP_BPS = 39.67               # Take profit (+39.67 bps)
SL_BPS = 20.44               # Stop loss (-20.44 bps)
MAKER_FEE_BPS = 2.0          # Maker fee per leg
TAKER_FEE_BPS = 5.0          # Taker fee for stop
SLIPPAGE_BPS = 2.0           # Slippage penalty on market stop (applied once in exit price)
BREAKEVEN_WINRATE = 0.4521   # 45.21% (2-outcome baseline)
S_MAX_BPS = risk_mod.S_MAX_BPS   # 20.82: widest stop the 0.015 USDT budget carries

ROOT = Path(__file__).resolve().parents[1]
TELEMETRY_STATE_FILE = ROOT / 'data' / 'telemetry_state.json'
COHORT_LOG_FILE = ROOT / 'data' / 'flush_cohorts.jsonl'   # entry OBI/CVD vs outcome, for calibration

# Real-time feed. One combined stream carries everything the engine reads:
# top of book (fills/stops), L5 depth (OBI), aggressor flow (CVD), 1m bars.
_SYM = SYMBOL.lower()
WS_URL = (f'wss://fstream.binance.com/stream?streams={_SYM}@bookTicker/'
          f'{_SYM}@depth5@100ms/{_SYM}@aggTrade/{_SYM}@kline_1m')
TICK_SECONDS = 0.5              # engine cadence on the WebSocket feed
REST_POLL_SECONDS = 2.0         # cadence when the socket is down (REST fallback)
TELEMETRY_SECONDS = 1.0         # dashboard publish cadence (immediately on a status change)
BARS15_REFRESH_SECONDS = 60.0
CVD_WINDOW_SECONDS = 60.0
FEED_STALE_SECONDS = 3.0
EV_TP_NET, EV_PROTECT_NET, EV_SL_NET = 35.67, 1.0, -29.44   # 3-outcome net bps


@dataclass
class TradeRecord:
    trade_id: int
    entry_time: float
    exit_time: float
    entry_price: float
    exit_price: float
    qty: float
    is_win: bool
    gross_bps: float
    fee_bps: float
    net_bps: float
    pnl_usdt: float
    reason: str


@dataclass
class EngineMetrics:
    total_intents: int = 0
    total_fills: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    protects: int = 0
    win_rate: float = 0.0
    fill_rate: float = 0.0
    cum_pnl_usdt: float = 0.0
    peak_equity_usdt: float = STARTING_EQUITY
    current_equity_usdt: float = STARTING_EQUITY
    is_killed: bool = False
    kill_reason: Optional[str] = None


@dataclass
class BranchPosition:
    branch_name: str
    entry_time: float
    entry_price: float
    tp_price: float
    sl_price: float
    orig_sl_price: float
    qty: float
    side: str = 'BUY'
    trigger_bps: Optional[float] = None
    protect_sl_bps: Optional[float] = None
    max_mfe_bps: float = 0.0
    is_protected: bool = False
    atr_14_bps: float = 0.0
    atr_14_raw: float = 0.0

    @property
    def d(self) -> int:
        """+1 long, -1 short. Every price comparison flips with this sign."""
        return -1 if self.side == 'SELL' else 1


@dataclass
class PrematureWatcher:
    branch_name: str
    entry_price: float
    tp_price: float
    orig_sl_price: float
    exit_time: float
    side: str = 'BUY'
    resolved: bool = False
    result: Optional[str] = None  # 'PREMATURE_STOPOUT', 'GOOD_PROTECTION', 'TIMEOUT'


_POSTERIOR_CACHE: Dict[Tuple[int, int, int], Tuple[float, float]] = {}


def posterior_ev(tp: int, protect: int, sl: int, draws: int = 4000) -> Tuple[float, float]:
    """Dirichlet(1,1,1) posterior over (p_TP, p_protect, p_SL) -> (mean EV bps, P(EV > 0)).

    The point estimate reads +35.67 bps after one lucky TP; the posterior says how
    much of that is sample size. Seeded by the counts and cached, so a 0.5 s tick
    only pays the Monte Carlo when a trade actually closes.
    """
    key = (int(tp), int(protect), int(sl))
    hit = _POSTERIOR_CACHE.get(key)
    if hit is not None:
        return hit
    a, b, c = key[0] + 1, key[1] + 1, key[2] + 1
    mean = (EV_TP_NET * a + EV_PROTECT_NET * b + EV_SL_NET * c) / (a + b + c)
    rng = random.Random(key[0] * 1_000_003 + key[1] * 1_009 + key[2])
    gv = rng.gammavariate
    pos = sum(1 for _ in range(draws)
              if EV_TP_NET * gv(a, 1.0) + EV_PROTECT_NET * gv(b, 1.0) + EV_SL_NET * gv(c, 1.0) > 0)
    if len(_POSTERIOR_CACHE) > 4096:
        _POSTERIOR_CACHE.clear()
    _POSTERIOR_CACHE[key] = (mean, pos / draws)
    return _POSTERIOR_CACHE[key]


def calculate_lot_size(price: float, min_notional: float = MIN_NOTIONAL) -> float:
    """Calculate integer lot size ensuring notional exceeds minimum notional."""
    if price <= 0:
        return 0.0
    raw_qty = (min_notional + 0.01) / price
    qty = math.ceil(raw_qty / LOT_STEP) * LOT_STEP
    if qty * price < min_notional:
        qty += LOT_STEP
    return float(qty)


def round_tick(price: float, tick: float = TICK_SIZE) -> float:
    return round(round(price / tick) * tick, 5)


def calculate_atr14(klines: List[dict]) -> Tuple[float, float]:
    """Calculate 14-period ATR from closed 1m bars.
    Returns (atr_raw, atr_bps_of_last_close).
    """
    if not klines or len(klines) < 15:
        return 0.0, 0.0
    bars = klines[:-1]  # exclude ongoing unfinished bar
    if len(bars) < 14:
        bars = klines
    tr_list = []
    for i in range(1, len(bars)):
        high = bars[i]['high']
        low = bars[i]['low']
        prev_close = bars[i - 1]['close']
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)
    if not tr_list:
        return 0.0, 0.0
    recent_tr = tr_list[-14:]
    atr_raw = sum(recent_tr) / float(len(recent_tr))
    last_close = bars[-1]['close']
    atr_bps = (atr_raw / last_close) * 1e4 if last_close > 0 else 0.0
    return atr_raw, atr_bps


class ShadowBranch:
    """Autonomous shadow execution branch with premature stopout tracking."""

    def __init__(self, name: str, trigger_bps: Optional[float] = None,
                 protect_sl_bps: Optional[float] = None, adaptive: bool = False):
        self.name = name
        self.trigger_bps = trigger_bps
        self.protect_sl_bps = protect_sl_bps
        # An adaptive branch recomputes its trigger per position from ATR:
        # a fixed +15 sits above the noise in a quiet tape and inside it in a
        # fast one, which is what produces the premature stop-outs being measured.
        self.adaptive = adaptive
        self.active_pos: Optional[BranchPosition] = None
        self.trades: List[TradeRecord] = []
        self.watchers: List[PrematureWatcher] = []
        self.tp_count = 0
        self.protect_count = 0
        self.sl_count = 0
        self.premature_count = 0
        self.good_protect_count = 0

    def open_position(self, entry_time: float, entry_price: float, qty: float,
                      atr_raw: float, atr_bps: float, side: str = 'BUY'):
        if side not in ('BUY', 'SELL'):
            raise ValueError(f'Unknown side {side!r}')
        if self.active_pos is not None:
            # A cohort must never be opened over a live position: the branches
            # only stay comparable while they share entries, and overwriting one
            # silently deletes the trade that was going to disagree.
            raise RuntimeError(f'{self.name}: refusing to overwrite an open position')
        trigger = self.trigger_bps
        if self.adaptive:
            trigger = risk_mod.breakeven_trigger_bps(
                atr_bps, risk_mod.tick_bps(TICK_SIZE, entry_price))
        # A short mirrors the long about the entry: tp below, stop above.
        d = -1 if side == 'SELL' else 1
        tp_p = round_tick(entry_price * (1.0 + d * (TP_BPS / 1e4)))
        sl_p = round_tick(entry_price * (1.0 - d * (SL_BPS / 1e4)))
        self.active_pos = BranchPosition(
            branch_name=self.name,
            entry_time=entry_time,
            entry_price=entry_price,
            side=side,
            tp_price=tp_p,
            sl_price=sl_p,
            orig_sl_price=sl_p,
            qty=qty,
            trigger_bps=trigger,
            protect_sl_bps=self.protect_sl_bps,
            max_mfe_bps=0.0,
            is_protected=False,
            atr_14_bps=atr_bps,
            atr_14_raw=atr_raw,
        )

    def update(self, cur_bid: float, now: float, cur_ask: Optional[float] = None) -> Optional[dict]:
        """Advance one observation. `cur_ask` defaults to the bid.

        A long leaves at the bid and a short at the ask, so every threshold test
        uses the quote the position would actually trade against. Passing only a
        bid keeps the long path byte-identical to before.
        """
        ask = cur_bid if cur_ask is None or cur_ask <= 0 else cur_ask
        # 1. Update existing premature stopout watchers.
        # Resolved watchers are dropped once counted: the counters carry the
        # result, so keeping the objects only grows the list without bound and
        # makes this rescan O(n) on every 2s tick for the life of the process.
        if self.watchers:
            still_open = []
            for w in self.watchers:
                wq = ask if w.side == 'SELL' else cur_bid
                wd = -1 if w.side == 'SELL' else 1
                if wd * (wq - w.tp_price) >= 0:
                    w.resolved = True
                    w.result = 'PREMATURE_STOPOUT'
                    self.premature_count += 1
                elif wd * (wq - w.orig_sl_price) <= 0:
                    w.resolved = True
                    w.result = 'GOOD_PROTECTION'
                    self.good_protect_count += 1
                elif (now - w.exit_time) > 7200.0:  # 2h holding window
                    w.resolved = True
                    w.result = 'TIMEOUT'
                else:
                    still_open.append(w)
            self.watchers = still_open

        if not self.active_pos:
            return None

        pos = self.active_pos
        entry_p = pos.entry_price
        notional = entry_p * pos.qty
        d = pos.d
        quote = ask if d < 0 else cur_bid          # the side this position exits on

        # 2. Check OLD stop FIRST (kiểm tra stop cũ trước)
        if d * (quote - pos.sl_price) <= 0:
            is_protect = pos.is_protected and (d * (pos.sl_price - entry_p) > 0)
            slippage_val = (SLIPPAGE_BPS / 1e4) * entry_p
            # One slippage step, against the position, priced into the exit.
            worst = min(quote, pos.sl_price) if d > 0 else max(quote, pos.sl_price)
            exit_p = round_tick(worst - d * slippage_val)
            gross_bps = d * ((exit_p / entry_p) - 1.0) * 1e4
            # Fee is strictly Maker in + Taker out (2 + 5 = 7.0 bps)
            fee_bps = MAKER_FEE_BPS + TAKER_FEE_BPS
            net_bps = gross_bps - fee_bps
            pnl = (net_bps / 1e4) * notional

            if is_protect:
                self.protect_count += 1
                reason = 'PROTECTION_SL_HIT'
                is_win = net_bps > 0
                # Spawn watcher to measure if market subsequent touches TP before original SL
                self.watchers.append(PrematureWatcher(
                    branch_name=self.name,
                    entry_price=entry_p,
                    tp_price=pos.tp_price,
                    orig_sl_price=pos.orig_sl_price,
                    exit_time=now,
                    side=pos.side,
                ))
            else:
                self.sl_count += 1
                reason = 'ORIGINAL_SL_TRIGGERED'
                is_win = False

            trade = TradeRecord(
                trade_id=len(self.trades) + 1,
                entry_time=pos.entry_time,
                exit_time=now,
                entry_price=entry_p,
                exit_price=exit_p,
                qty=pos.qty,
                is_win=is_win,
                gross_bps=gross_bps,
                fee_bps=fee_bps,
                net_bps=net_bps,
                pnl_usdt=pnl,
                reason=reason,
            )
            self.trades.append(trade)
            self.active_pos = None
            return {'status': 'TRADE_CLOSED', 'result': 'PROTECT' if is_protect else 'SL', 'trade': trade}

        # 3. Check TP
        elif d * (quote - pos.tp_price) >= 0:
            exit_p = pos.tp_price
            gross_bps = d * ((exit_p / entry_p) - 1.0) * 1e4
            fee_bps = MAKER_FEE_BPS + MAKER_FEE_BPS  # 4.0 bps
            net_bps = gross_bps - fee_bps
            pnl = (net_bps / 1e4) * notional

            self.tp_count += 1
            trade = TradeRecord(
                trade_id=len(self.trades) + 1,
                entry_time=pos.entry_time,
                exit_time=now,
                entry_price=entry_p,
                exit_price=exit_p,
                qty=pos.qty,
                is_win=True,
                gross_bps=gross_bps,
                fee_bps=fee_bps,
                net_bps=net_bps,
                pnl_usdt=pnl,
                reason='TP_REACHED_GTX',
            )
            self.trades.append(trade)
            self.active_pos = None
            return {'status': 'TRADE_CLOSED', 'result': 'TP', 'trade': trade}

        # 4. Position alive: Update MFE & Trailing Protection (có hiệu lực từ quan sát sau)
        else:
            cur_mfe = d * ((quote / entry_p) - 1.0) * 1e4
            pos.max_mfe_bps = max(pos.max_mfe_bps, cur_mfe)

            if pos.trigger_bps is not None and pos.max_mfe_bps >= pos.trigger_bps and not pos.is_protected:
                new_sl = round_tick(entry_p * (1.0 + d * (pos.protect_sl_bps / 1e4)))
                # A stop only ever tightens, in whichever direction that means.
                if d * (new_sl - pos.sl_price) > 0:
                    pos.sl_price = new_sl
                    pos.is_protected = True

            return {'status': 'IN_POSITION', 'cur_mfe': cur_mfe, 'max_mfe': pos.max_mfe_bps, 'protected': pos.is_protected}

    def force_exit(self, cur_bid: float, now: float, reason: str,
                   cur_ask: Optional[float] = None) -> Optional[TradeRecord]:
        """Reduce-only market exit used by the kill latch. Taker + slippage.

        Settles at an executable quote, never at an untouched stop: a kill is a
        forced market exit, so it must not be priced as if the stop had filled.
        """
        pos = self.active_pos
        if pos is None or cur_bid <= 0:
            return None
        ask = cur_bid if cur_ask is None or cur_ask <= 0 else cur_ask
        entry_p = pos.entry_price
        notional = entry_p * pos.qty
        d = pos.d
        quote = ask if d < 0 else cur_bid
        exit_p = round_tick(quote - d * (SLIPPAGE_BPS / 1e4) * entry_p)
        gross_bps = d * ((exit_p / entry_p) - 1.0) * 1e4
        fee_bps = MAKER_FEE_BPS + TAKER_FEE_BPS
        net_bps = gross_bps - fee_bps
        trade = TradeRecord(
            trade_id=len(self.trades) + 1, entry_time=pos.entry_time, exit_time=now,
            entry_price=entry_p, exit_price=exit_p, qty=pos.qty,
            is_win=net_bps > 0, gross_bps=gross_bps, fee_bps=fee_bps,
            net_bps=net_bps, pnl_usdt=(net_bps / 1e4) * notional, reason=reason,
        )
        self.trades.append(trade)
        self.sl_count += 1
        self.active_pos = None
        return trade

    def get_stats(self) -> dict:
        n = len(self.trades)
        if n == 0:
            return {
                'name': self.name,
                'total_trades': 0,
                'tp_count': 0,
                'protect_count': 0,
                'sl_count': 0,
                'p_tp': 0.0,
                'p_protect': 0.0,
                'p_sl': 0.0,
                'analytical_ev_bps': 0.0,
                'empirical_ev_bps': 0.0,
                'lower95_ev_bps': -999.0,
                'premature_count': 0,
                'premature_rate': 0.0,
                'cum_pnl_usdt': 0.0,
                'posterior_ev_bps': None,
                'p_ev_positive': None,
                'fees_usdt': 0.0,
            }

        p_tp = self.tp_count / n
        p_prot = self.protect_count / n
        p_sl = self.sl_count / n
        # 3-outcome analytical EV formula: EV = 35.67*p_TP + 1.0*p_protect - 29.44*p_SL
        analytical_ev = (35.67 * p_tp) + (1.0 * p_prot) - (29.44 * p_sl)

        net_list = [t.net_bps for t in self.trades]
        mean_net = sum(net_list) / n
        cum_pnl = sum(t.pnl_usdt for t in self.trades)

        if n >= 2:
            variance = sum((x - mean_net) ** 2 for x in net_list) / (n - 1)
            std_err = math.sqrt(variance / n)
            lower95 = mean_net - 1.96 * std_err
        else:
            lower95 = -999.0

        premature_rate = (self.premature_count / self.protect_count) if self.protect_count > 0 else 0.0
        post_mean, p_pos = posterior_ev(self.tp_count, self.protect_count, self.sl_count)

        return {
            'name': self.name,
            'total_trades': n,
            'tp_count': self.tp_count,
            'protect_count': self.protect_count,
            'sl_count': self.sl_count,
            'p_tp': p_tp,
            'p_protect': p_prot,
            'p_sl': p_sl,
            'analytical_ev_bps': analytical_ev,
            'empirical_ev_bps': mean_net,
            'lower95_ev_bps': lower95,
            'premature_count': self.premature_count,
            'premature_rate': premature_rate,
            'cum_pnl_usdt': cum_pnl,
            'posterior_ev_bps': post_mean,
            'p_ev_positive': p_pos,
            'fees_usdt': sum(t.fee_bps / 1e4 * t.entry_price * t.qty for t in self.trades),
        }


def _parse_klines(raw) -> List[dict]:
    return [{'time': b[0], 'open': float(b[1]), 'high': float(b[2]),
             'low': float(b[3]), 'close': float(b[4]), 'volume': float(b[5]),
             'close_time': b[6]} for b in raw]


def fetch_rest_market_data() -> Tuple[Optional[dict], Optional[List[dict]],
                                      Optional[float], Optional[List[dict]]]:
    """bookTicker, 1m klines, open interest and 15m klines for DOGEUSDT.

    The 15m series is what the regime gate runs on. Without it classify_regime
    returns TRANSITION and the gate has no input -- which blocks nothing and
    permits nothing, so the filter silently does not exist. Fetching it is what
    makes the gate bite in a live session.

    Four independent GETs, issued in parallel: warm start costs one round trip,
    not four. 1m limit is 30 because detect_short_setup needs >= 24 bars; with
    20 the short path could never fire.
    """
    base = 'https://fapi.binance.com/fapi/v1'
    urls = {
        'ticker': f'{base}/ticker/bookTicker?symbol={SYMBOL}',
        'k1': f'{base}/klines?symbol={SYMBOL}&interval=1m&limit=30',
        'oi': f'{base}/openInterest?symbol={SYMBOL}',
        'k15': f'{base}/klines?symbol={SYMBOL}&interval=15m&limit=60',
    }
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {k: pool.submit(_get_json, u) for k, u in urls.items()}
    out = {}
    for k, fut in futures.items():
        try:
            out[k] = fut.result()
        except Exception:
            out[k] = None
    ticker = out['ticker'] if isinstance(out['ticker'], dict) else None
    try:
        klines = _parse_klines(out['k1']) if out['k1'] else None
    except (TypeError, ValueError, IndexError):
        klines = None
    try:
        oi = float(out['oi'].get('openInterest', 0.0)) if out['oi'] else None
    except (AttributeError, TypeError, ValueError):
        oi = None
    try:
        bars15 = _parse_klines(out['k15']) if out['k15'] else None
    except (TypeError, ValueError, IndexError):
        bars15 = None
    return ticker, klines, oi, bars15


def _get_json(url: str, timeout: float = 4.0):
    req = urllib.request.Request(url, headers={'User-Agent': 'FlushShadow/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def fetch_bars15(limit: int = 60) -> Optional[List[dict]]:
    """15m bars only (regime refresh / warm-start priming). None on any failure."""
    try:
        return _parse_klines(_get_json(
            f'https://fapi.binance.com/fapi/v1/klines?symbol={SYMBOL}&interval=15m&limit={int(limit)}'))
    except Exception:
        return None


class MicroFeed:
    """Binance USD-M combined stream -> top of book, L5 OBI, rolling CVD, live 1m bars.

    `on_message` is synchronous and I/O-free (tests drive it with recorded
    frames); `run` only pumps frames off the socket and reconnects with backoff.
    """

    def __init__(self, url: str = WS_URL, cvd_window: float = CVD_WINDOW_SECONDS):
        self.url = url
        self.cvd_window = cvd_window
        self.bid = self.ask = 0.0
        self.bid_qty = self.ask_qty = 0.0
        self.obi: Optional[float] = None
        self.cvd_session = 0.0
        self._flow: deque = deque()
        self._flow_sum = 0.0
        self.klines: List[dict] = []
        self.last_msg = 0.0
        self.frames = 0
        self.connected = False
        self.reconnects = 0

    def seed_klines(self, klines: Optional[List[dict]]) -> None:
        if klines and len(klines) > len(self.klines):
            self.klines = [dict(k) for k in klines[-60:]]

    def on_message(self, msg: dict, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        data = msg.get('data', msg) if isinstance(msg, dict) else {}
        stream = msg.get('stream', '') if isinstance(msg, dict) else ''
        ev = data.get('e', '')
        try:
            if ev == 'bookTicker' or stream.endswith('@bookTicker'):
                bid, ask = float(data['b']), float(data['a'])
                if bid <= 0 or ask <= 0:
                    return False
                self.bid, self.ask = bid, ask
                self.bid_qty, self.ask_qty = float(data['B']), float(data['A'])
            elif ev == 'depthUpdate' or '@depth' in stream:
                bids, asks = data.get('b') or [], data.get('a') or []
                bq = sum(float(q) for _, q in bids[:5])
                aq = sum(float(q) for _, q in asks[:5])
                if bq + aq <= 0:
                    return False
                self.obi = (bq - aq) / (bq + aq)
            elif ev == 'aggTrade':
                quote = float(data['p']) * float(data['q'])
                signed = -quote if data.get('m') else quote   # m: buyer is maker -> seller aggressed
                self.cvd_session += signed
                self._flow.append((now, signed))
                self._flow_sum += signed
            elif ev == 'kline':
                k = data['k']
                bar = {'time': k['t'], 'open': float(k['o']), 'high': float(k['h']),
                       'low': float(k['l']), 'close': float(k['c']), 'volume': float(k['v']),
                       'close_time': k['T']}
                if self.klines and self.klines[-1]['time'] == bar['time']:
                    self.klines[-1] = bar
                elif not self.klines or bar['time'] > self.klines[-1]['time']:
                    self.klines.append(bar)
                    del self.klines[:-60]
            else:
                return False
        except (KeyError, TypeError, ValueError, AttributeError):
            return False
        cutoff = now - self.cvd_window
        while self._flow and self._flow[0][0] < cutoff:
            self._flow_sum -= self._flow.popleft()[1]
        self.last_msg = now
        self.frames += 1
        return True

    def ready(self, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        return (self.bid > 0 and self.ask > 0 and len(self.klines) >= 15
                and (now - self.last_msg) < FEED_STALE_SECONDS)

    def ticker(self) -> dict:
        return {'bidPrice': repr(self.bid), 'askPrice': repr(self.ask)}

    def snapshot(self, now: Optional[float] = None) -> dict:
        now = time.time() if now is None else now
        cutoff = now - self.cvd_window
        while self._flow and self._flow[0][0] < cutoff:
            self._flow_sum -= self._flow.popleft()[1]
        qsum = self.bid_qty + self.ask_qty
        micro = ((self.bid * self.ask_qty + self.ask * self.bid_qty) / qsum) if qsum > 0 else None
        return {'obi': self.obi, 'cvd_60s': self._flow_sum, 'cvd_session': self.cvd_session,
                'microprice': micro, 'frames': self.frames,
                'age_s': (now - self.last_msg) if self.last_msg else None}

    async def run(self) -> None:
        try:
            import websockets
        except ImportError:
            return                      # engine stays on the REST fallback
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(self.url, ping_interval=20, ping_timeout=20,
                                              open_timeout=5, max_queue=4096) as ws:
                    self.connected, backoff = True, 1.0
                    async for raw in ws:
                        try:
                            self.on_message(json.loads(raw))
                        except ValueError:
                            continue
            except asyncio.CancelledError:
                self.connected = False
                raise
            except Exception:
                pass
            self.connected = False
            self.reconnects += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, 15.0)


class FlushShadowEngine:
    """Micro-capital shadow engine running comparative branches on identical fills."""

    def __init__(self, starting_equity: float = STARTING_EQUITY):
        self.equity = starting_equity
        self.metrics = EngineMetrics(current_equity_usdt=starting_equity, peak_equity_usdt=starting_equity)
        self.trades: List[TradeRecord] = []
        self.active_order: Optional[dict] = None
        self.active_position: Optional[dict] = None  # Active candidate view (BE_15_10)
        self.trade_counter = 0

        # Comparative shadow branches on the exact same entry fills
        self.shadow_branches: Dict[str, ShadowBranch] = {
            'baseline': ShadowBranch('baseline'),
            'be_15_10': ShadowBranch('be_15_10', trigger_bps=15.0, protect_sl_bps=10.0),
            'be_20_10': ShadowBranch('be_20_10', trigger_bps=20.0, protect_sl_bps=10.0),
            # Fixed triggers stay untouched so the existing comparison keeps its
            # meaning; the ATR-scaled rule enters as a fourth competitor.
            'be_adaptive': ShadowBranch('be_adaptive', protect_sl_bps=10.0, adaptive=True),
        }
        self.regime = regime_mod.TRANSITION
        self.regime_diag: Dict = {}
        self.range_streak = 0
        self.fast_down = False
        self.bars15: List[dict] = []
        self._short_break: Optional[dict] = None
        self.last_block_reason = ''
        # Microstructure read from MicroFeed each tick (OBI L5, CVD 60s, microprice).
        self.micro: Dict = {}
        self.micro_confirm = False          # opt-in: OBI and CVD must agree with the side
        self.cohort_log: Optional[Path] = None
        self._cohort_micro: Dict = {}
        self.feed_label = 'binance-rest'

    def prime_regime(self, bars15_long: Optional[List[dict]]) -> str:
        """Warm start: read the last TWO 15m windows from history at once.

        long_permitted wants two consecutive RANGE reads. Counting them live costs
        up to 30 minutes after boot; the same two reads already exist in a 61+ bar
        history, so the hysteresis is satisfied (or not) at t=0 without being weakened.
        """
        if not bars15_long or len(bars15_long) < 61:
            return self.regime
        prev, _ = regime_mod.classify_regime(bars15_long[-61:-1])
        cur, diag = regime_mod.classify_regime(bars15_long[-60:])
        self.bars15 = bars15_long[-60:]
        self.regime, self.regime_diag = cur, diag
        self.range_streak = (2 if prev == regime_mod.RANGE else 1) if cur == regime_mod.RANGE else 0
        return self.regime

    def micro_confirms(self, side: str) -> bool:
        """Book and tape lean the same way as the trade. Missing data fails closed."""
        obi, cvd = self.micro.get('obi'), self.micro.get('cvd_60s')
        if obi is None or cvd is None:
            return False
        return (obi >= 0 and cvd >= 0) if side == 'BUY' else (obi <= 0 and cvd <= 0)

    def _log_cohort(self, trade: 'TradeRecord') -> None:
        if self.cohort_log is None:
            return
        row = {'t': round(trade.exit_time, 3), 'side': (self.active_position or {}).get('side', 'BUY'),
               'entry': trade.entry_price, 'exit': trade.exit_price,
               'hold_s': round(trade.exit_time - trade.entry_time, 1),
               'net_bps': round(trade.net_bps, 3), 'reason': trade.reason, **self._cohort_micro}
        try:
            self.cohort_log.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cohort_log, 'a', encoding='utf-8') as fh:
                fh.write(json.dumps(row, allow_nan=False, default=str) + '\n')
        except (OSError, ValueError):
            pass

    def cohort_open(self) -> bool:
        """True while ANY branch still holds the cohort's position.

        Reading only the primary branch is the bug: when BE_15_10 protects out
        early and baseline is still running, the primary looks flat, a new entry
        is admitted, and baseline's open trade is overwritten by the next fill.
        The comparison then silently drops exactly the trades where the branches
        disagreed -- the only trades that carry information.
        """
        return any(b.active_pos is not None for b in self.shadow_branches.values())

    def open_shadow_position(self, entry_time: float, entry_price: float, qty: float,
                             atr_raw: float = 0.0, atr_bps: float = 0.0, side: str = 'BUY'):
        """Open one cohort across every branch. Refuses while a cohort is live."""
        if self.cohort_open():
            self.last_block_reason = 'COHORT_STILL_OPEN'
            return False
        for branch in self.shadow_branches.values():
            branch.open_position(entry_time, entry_price, qty, atr_raw, atr_bps, side=side)
        pos = self.shadow_branches['be_15_10'].active_pos
        self.active_position = {
            'entry_time': entry_time,
            'side': side,
            'entry_price': entry_price,
            'tp_price': pos.tp_price,
            'sl_price': pos.sl_price,
            'qty': qty,
            'max_mfe_bps': 0.0,
            'is_protected': False,
            'atr_14_bps': atr_bps,
            'atr_14_raw': atr_raw,
        }
        return True

    def force_flat(self, cur_bid: float, now: float,
                   cur_ask: Optional[float] = None) -> Tuple[int, List[TradeRecord]]:
        """Kill latch: cancel pending entries, market-exit every open branch.

        Returns (remaining_open_branches, settled_trades). HALTED is not FLAT:
        the run may only report HALTED_FLAT once quantity is confirmed zero.
        """
        self.active_order = None
        settled = []
        for branch in self.shadow_branches.values():
            trade = branch.force_exit(cur_bid, now, 'KILL_FORCED_EXIT', cur_ask=cur_ask)
            if trade is not None:
                settled.append(trade)
                if branch.name == 'be_15_10':
                    self.equity += trade.pnl_usdt
                    self.trades.append(trade)
                    self.metrics.total_trades += 1
                    self.metrics.losses += 1
        self.active_position = None
        if settled:
            self._recalculate_metrics()
        remaining = sum(1 for b in self.shadow_branches.values() if b.active_pos is not None)
        return remaining, settled

    def get_liquidation_equity(self, cur_bid: Optional[float] = None,
                               cur_ask: Optional[float] = None) -> float:
        """Cash plus floating PnL less the full cost of getting out."""
        if not self.active_position or cur_bid is None or cur_bid <= 0:
            return self.equity
        pos = self.active_position
        entry_p = pos['entry_price']
        notional = entry_p * pos['qty']
        d = -1 if pos.get('side') == 'SELL' else 1
        quote = cur_ask if (d < 0 and cur_ask and cur_ask > 0) else cur_bid
        # A forced market exit pays taker AND slips: 2 maker in + 5 taker out + 2
        # slippage. Omitting the slippage made the kill threshold read 2 bps
        # better than any exit the run could actually get.
        unrealized_bps = (d * ((quote / entry_p) - 1.0) * 1e4
                          - (MAKER_FEE_BPS + TAKER_FEE_BPS + SLIPPAGE_BPS))
        unrealized_pnl = (unrealized_bps / 1e4) * notional
        return self.equity + unrealized_pnl

    def check_kill_switch(self, cur_bid: Optional[float] = None,
                          cur_ask: Optional[float] = None) -> bool:
        liq_equity = self.get_liquidation_equity(cur_bid, cur_ask)
        loss = STARTING_EQUITY - liq_equity
        if loss >= KILL_LOSS_USDT:
            self.metrics.is_killed = True
            self.metrics.kill_reason = f'KILL TRIGGERED: Liquidation loss {loss:.4f} USDT >= {KILL_LOSS_USDT} USDT (5 cents limit)'
            return True
        return False

    def update_regime(self, bars15: Optional[List[dict]], bars1: List[dict]) -> str:
        """Refresh the 15m regime and the 1m capitulation flag from CLOSED bars."""
        if bars15:
            self.bars15 = bars15
        self.regime, self.regime_diag = regime_mod.classify_regime(self.bars15)
        # The streak counts 15m READS, so only a cycle that carries 15m bars may
        # advance it. Counting every 1m cycle met the two-close hysteresis in two
        # polls (4 s at REST cadence, 1 s on the socket) -- i.e. never enforced it.
        if self.regime != regime_mod.RANGE:
            self.range_streak = 0
        elif bars15:
            self.range_streak += 1
        atr_prev = regime_mod.wilder_atr(bars1[:-1]) if len(bars1) > 15 else 0.0
        self.fast_down = regime_mod.is_fast_down(bars1, atr_prev)
        return self.regime

    def long_allowed(self) -> bool:
        return regime_mod.long_permitted(self.regime, self.fast_down, self.range_streak)

    def short_allowed(self) -> bool:
        return regime_mod.short_permitted(self.regime)

    def detect_short_setup(self, klines: List[dict], cur_ask: float) -> Tuple[bool, float, str]:
        """Breakout then retest, on CLOSED 1m bars only.

        The break arms nothing by itself: without a retest inside three bars the
        event expires. Chasing the break is a different trade with a different
        stop, and treating every FAST_DOWN as permission to short is exactly the
        reading the spec refuses.
        """
        if len(klines) < 24 or cur_ask <= 0:
            return False, 0.0, 'INSUFFICIENT_BARS'
        closed = klines[:-1]                      # drop the bar still forming
        bar_time = closed[-1]['time']
        if self._short_break and bar_time == self._short_break.get('bar_time'):
            return False, 0.0, 'AWAITING_NEW_BAR'

        atr_prev = regime_mod.wilder_atr(closed[:-1])
        if self._short_break is None:
            broke, info = regime_mod.short_break_signal(closed, atr_prev)
            if not broke:
                return False, 0.0, info.get('reason', 'NO_SIGNAL')
            self._short_break = {'support': info['support'], 'atr': atr_prev,
                                 'bars_left': 3, 'bar_time': bar_time}
            return False, 0.0, 'SHORT_BREAK_AWAITING_RETEST'

        state = self._short_break
        state['bar_time'] = bar_time
        state['bars_left'] -= 1
        found, _ = regime_mod.short_retest([closed[-1]], state['support'], state['atr'])
        if found:
            self._short_break = None
            return True, round_tick(cur_ask), 'SHORT_RETEST_CONFIRMED'
        if state['bars_left'] <= 0:
            self._short_break = None
            return False, 0.0, 'SHORT_RETEST_EXPIRED'
        return False, 0.0, 'SHORT_AWAITING_RETEST'

    def detect_flush_signal(self, klines: List[dict], ticker: dict) -> Tuple[bool, float, str]:
        """Detect volatility spike / flush pattern on 1m bars."""
        if not klines or len(klines) < 10:
            return False, 0.0, 'INSUFFICIENT_BARS'

        cur_bid = float(ticker.get('bidPrice', 0.0))
        if cur_bid <= 0:
            return False, 0.0, 'INVALID_TICKER'

        recent = klines[-2]  # last completed 1m bar
        avg_vol = sum(b['volume'] for b in klines[-15:-2]) / 13.0 if len(klines) >= 15 else recent['volume']

        bar_range = recent['high'] - recent['low']
        down_move_bps = ((recent['open'] - recent['low']) / recent['open']) * 1e4 if recent['open'] > 0 else 0
        bounce = (recent['close'] - recent['low']) / bar_range if bar_range > 0 else 0

        is_volume_spike = recent['volume'] > (1.25 * avg_vol)
        is_flush_dip = down_move_bps >= 12.0
        is_bounce_confirmed = bounce >= 0.25

        if is_flush_dip and (is_volume_spike or is_bounce_confirmed):
            entry_price = round_tick(cur_bid)
            return True, entry_price, f'FLUSH_DETECTED(dip={down_move_bps:.1f}bps, vol_x={recent["volume"]/avg_vol:.2f})'

        return False, 0.0, 'NO_SIGNAL'

    def _place_entry(self, side: str, entry_price: float, reason: str, klines: List[dict],
                     cur_bid: float, cur_ask: float, now: float) -> dict:
        """Size, gate and rest one GTX order. Both sides pass the SAME gates.

        Shorts get no discount on the risk ceiling: the loss budget is a property
        of the account, not of the direction.
        """
        qty = calculate_lot_size(entry_price)
        notional = entry_price * qty
        margin = notional / LEVERAGE
        if margin > self.equity:
            return {'status': 'BLOCKED',
                    'reason': f'MARGIN_EXCEEDS_EQUITY({margin:.2f} > {self.equity:.2f})'}

        # Capital risk ceiling. s never gets narrowed to fit the budget: a stop
        # that no longer sits behind the level which invalidates the setup is not
        # a smaller risk, only a more frequent one.
        _, atr_bps_now = calculate_atr14(klines)
        tickb = risk_mod.tick_bps(TICK_SIZE, entry_price)
        s_bps = risk_mod.resolve_stop_bps(SL_BPS, atr_bps_now, tickb)
        allowed, risk_reason, diag = risk_mod.entry_risk_gate(
            qty, entry_price, s_bps, self.get_liquidation_equity(cur_bid, cur_ask), STARTING_EQUITY)
        if not allowed:
            self.last_block_reason = risk_reason
            return {'status': 'BLOCKED', 'reason': risk_reason, 's_bps': s_bps, 'side': side,
                    'risk_usdt': diag['risk_usdt'], 'risk_allowed_usdt': diag['risk_allowed_usdt']}

        self.active_order = {'placed_time': now, 'price': entry_price, 'qty': qty,
                             'notional': notional, 'reason': reason, 'side': side}
        self.metrics.total_intents += 1
        self._recalculate_metrics()
        return {'status': 'ORDER_PLACED', 'side': side, 'price': entry_price,
                'qty': qty, 'notional': notional, 'reason': reason}

    def update_cycle(self, ticker: dict, klines: List[dict], oi: Optional[float] = None,
                     bars15: Optional[List[dict]] = None) -> dict:
        """Process one poll cycle: manage orders, update multi-branch positions and check kill switch."""
        cur_bid = float(ticker.get('bidPrice', 0.0))
        cur_ask = float(ticker.get('askPrice', 0.0))
        now = time.time()
        if bars15 or klines:
            self.update_regime(bars15, klines)

        # 0. Kill latch. Reaching the loss threshold starts a forced exit; it is
        # not itself an exit. Returning HALTED here while positions were still
        # open left the run reporting a halt it had not actually reached, with
        # unsettled quantity and no trade in any ledger.
        if self.metrics.is_killed or self.check_kill_switch(cur_bid, cur_ask):
            remaining, settled = self.force_flat(cur_bid, now, cur_ask)
            if remaining:
                return {'status': 'KILL_PENDING', 'halted': True,
                        'remaining_branches': remaining,
                        'reason': self.metrics.kill_reason}
            return {'status': 'HALTED_FLAT', 'halted': True,
                    'settled': len(settled), 'reason': self.metrics.kill_reason}

        # 1. Update all parallel shadow branches
        branch_results = {}
        for bname, branch in self.shadow_branches.items():
            b_res = branch.update(cur_bid, now, cur_ask)
            if b_res:
                branch_results[bname] = b_res

        # Primary engine reflects BE_15_10 (the prioritized candidate)
        cand_branch = self.shadow_branches['be_15_10']
        cand_res = branch_results.get('be_15_10')

        if cand_res and cand_res['status'] == 'TRADE_CLOSED':
            trade = cand_res['trade']
            self.equity += trade.pnl_usdt
            self.trades.append(trade)
            self.trade_counter += 1
            self.metrics.total_trades += 1
            if cand_res['result'] == 'WIN' or trade.reason == 'TP_REACHED_GTX':
                self.metrics.wins += 1
            elif cand_res['result'] == 'PROTECT':
                self.metrics.protects += 1
                if trade.is_win:
                    self.metrics.wins += 1
                else:
                    self.metrics.losses += 1
            else:
                self.metrics.losses += 1

            self._log_cohort(trade)
            self.active_position = None
            self._recalculate_metrics()
            self.check_kill_switch(cur_bid, cur_ask)
            return {'status': 'TRADE_CLOSED', 'result': cand_res['result'], 'trade': trade, 'branch_results': branch_results}

        elif cand_branch.active_pos:
            pos = cand_branch.active_pos
            self.active_position = {
                'entry_time': pos.entry_time,
                'side': pos.side,
                'entry_price': pos.entry_price,
                'tp_price': pos.tp_price,
                'sl_price': pos.sl_price,
                'qty': pos.qty,
                'max_mfe_bps': pos.max_mfe_bps,
                'is_protected': pos.is_protected,
                'atr_14_bps': pos.atr_14_bps,
                'atr_14_raw': pos.atr_14_raw,
            }
            float_bps = pos.d * ((( cur_ask if pos.d < 0 else cur_bid) / pos.entry_price) - 1.0) * 1e4
            return {'status': 'IN_POSITION', 'entry': pos.entry_price, 'cur': cur_bid, 'float_bps': float_bps, 'protected': pos.is_protected}

        else:
            self.active_position = None

        # 2. Check active pending limit order
        if self.active_order:
            order = self.active_order
            side = order.get('side', 'BUY')
            # The market must trade THROUGH our resting limit, on the far side.
            filled = (cur_ask <= order['price']) if side == 'BUY' else (cur_bid >= order['price'])
            if filled:
                fill_price = order['price']
                qty = order['qty']
                atr_raw, atr_bps = calculate_atr14(klines)

                # A fill may only open a cohort when EVERY branch is flat.
                if self.cohort_open():
                    self.active_order = None
                    return {'status': 'BLOCKED', 'reason': 'COHORT_STILL_OPEN'}
                for branch in self.shadow_branches.values():
                    branch.open_position(
                        entry_time=now,
                        entry_price=fill_price,
                        qty=qty,
                        atr_raw=atr_raw,
                        atr_bps=atr_bps,
                        side=side,
                    )

                pos = self.shadow_branches['be_15_10'].active_pos
                self.active_position = {
                    'entry_time': now,
                    'side': side,
                    'entry_price': fill_price,
                    'tp_price': pos.tp_price,
                    'sl_price': pos.sl_price,
                    'qty': qty,
                    'max_mfe_bps': 0.0,
                    'is_protected': False,
                    'atr_14_bps': atr_bps,
                    'atr_14_raw': atr_raw,
                }
                self._cohort_micro = {'entry_obi': self.micro.get('obi'),
                                      'entry_cvd_60s': self.micro.get('cvd_60s'),
                                      'atr_bps': round(atr_bps, 2), 'regime': self.regime}
                self.metrics.total_fills += 1
                self.active_order = None
                self._recalculate_metrics()
                return {'status': 'ORDER_FILLED', 'side': side, 'entry': fill_price,
                        'tp': pos.tp_price, 'sl': pos.sl_price, 'atr_bps': atr_bps}

            # Cancel order if stale (> 180s) or price ran away (> 25 bps above limit)
            ran_away = (cur_bid > order['price'] * 1.0025) if side == 'BUY' \
                else (cur_ask < order['price'] * 0.9975)
            if (now - order['placed_time'] > 180.0) or ran_away:
                self.active_order = None
                return {'status': 'ORDER_EXPIRED', 'reason': 'STALE_OR_PRICE_RAN_AWAY'}

            return {'status': 'ORDER_PENDING', 'price': order['price']}

        # 3. No active order or position: look for a flush signal.
        if self.cohort_open():
            return {'status': 'BLOCKED', 'reason': 'COHORT_STILL_OPEN'}

        # 3a. Short first. A confirmed 15m downtrend is the only regime this
        #     engine may short in, and it is also the regime where Long is
        #     locked, so there is no ambiguity about which side to look for.
        if self.bars15 and self.short_allowed():
            ok, short_entry, short_reason = self.detect_short_setup(klines, cur_ask)
            if ok:
                if self.micro_confirm and not self.micro_confirms('SELL'):
                    self.last_block_reason = 'MICRO_UNCONFIRMED'
                    return {'status': 'BLOCKED', 'reason': 'MICRO_UNCONFIRMED', 'regime': self.regime}
                return self._place_entry('SELL', short_entry, short_reason, klines, cur_bid, cur_ask, now)
            return {'status': 'NO_SIGNAL', 'reason': short_reason, 'regime': self.regime}

        has_signal, entry_price, reason = self.detect_flush_signal(klines, ticker)
        if has_signal:
            # Regime gate for the Long side: a confirmed 15m downtrend or a 1m
            # capitulation removes the permission entirely -- a large bounce
            # inside a downtrend is the signal, not the exception to it.
            if self.bars15 and not self.long_allowed():
                block = 'FAST_DOWN' if self.fast_down else f'LONG_LOCKED_IN_{self.regime}'
                self.last_block_reason = block
                return {'status': 'BLOCKED', 'reason': block, 'regime': self.regime}
            if self.micro_confirm and not self.micro_confirms('BUY'):
                self.last_block_reason = 'MICRO_UNCONFIRMED'
                return {'status': 'BLOCKED', 'reason': 'MICRO_UNCONFIRMED', 'regime': self.regime}
            return self._place_entry('BUY', entry_price, reason, klines, cur_bid, cur_ask, now)

        return {'status': 'WAITING_SIGNAL', 'reason': reason}

    def _recalculate_metrics(self):
        self.metrics.current_equity_usdt = self.equity
        self.metrics.cum_pnl_usdt = self.equity - STARTING_EQUITY
        if self.equity > self.metrics.peak_equity_usdt:
            self.metrics.peak_equity_usdt = self.equity

        if self.metrics.total_trades > 0:
            self.metrics.win_rate = self.metrics.wins / self.metrics.total_trades
        if self.metrics.total_intents > 0:
            self.metrics.fill_rate = self.metrics.total_fills / self.metrics.total_intents

    def write_telemetry_snapshot(self, cur_bid: float, cur_ask: float, status: str):
        state_file = TELEMETRY_STATE_FILE
        state_file.parent.mkdir(parents=True, exist_ok=True)

        spread_bps = ((cur_ask / cur_bid) - 1.0) * 1e4 if cur_bid > 0 else 1.0
        m = self.metrics
        now = time.time()

        if not hasattr(self, 'history_buffer'):
            # Bounded deques: list.pop(0) is O(n) and ran on every tick.
            self.history_buffer = deque(maxlen=600)
            self.event_buffer = deque(maxlen=20)

        # Mark-to-market equity (cash + floating - exit cost): the trace moves
        # with every tick while a position is open, not only when one closes.
        liq = self.get_liquidation_equity(cur_bid, cur_ask) if cur_bid > 0 else self.equity
        self.history_buffer.append({'t': now, 'value': round(liq, 6)})

        branch_stats = {name: b.get_stats() for name, b in self.shadow_branches.items()}
        primary = branch_stats['be_15_10']
        micro = self.micro or {}
        obi, cvd = micro.get('obi'), micro.get('cvd_60s')

        self.event_buffer.appendleft({'t': now, 'message': (
            f'DOGE {cur_bid:.5f} | {status} | Trades: {m.total_trades}'
            + (f' | OBI {obi:+.2f}' if obi is not None else '')
            + (f' | CVD60 {cvd:+.0f}U' if cvd is not None else ''))})

        # Realized edge vs cost of the primary branch: NET EDGE on the board is
        # what closed trades actually earned, not a constant.
        n_primary = len(self.trades)
        edge_bps = cost_bps = None
        if n_primary:
            edge_bps = sum(t.gross_bps for t in self.trades) / n_primary
            cost_bps = sum(t.fee_bps for t in self.trades) / n_primary
        fees = sum(t.fee_bps / 1e4 * t.entry_price * t.qty for t in self.trades)
        volume = sum(2.0 * t.entry_price * t.qty for t in self.trades)
        peak = max(m.peak_equity_usdt, liq)
        p_pos = primary.get('p_ev_positive')

        positions = []
        if self.active_position and cur_bid > 0:
            ap = self.active_position
            d = -1 if ap.get('side') == 'SELL' else 1
            quote = cur_ask if d < 0 else cur_bid
            positions.append({'symbol': SYMBOL, 'side': ap.get('side', 'BUY'), 'size': ap['qty'],
                              'entry': ap['entry_price'], 'tp': ap.get('tp_price'), 'sl': ap.get('sl_price'),
                              'pnl_bps': d * ((quote / ap['entry_price']) - 1.0) * 1e4,
                              'mfe_bps': ap.get('max_mfe_bps', 0.0),
                              'protected': ap.get('is_protected', False)})

        blocked = status == 'BLOCKED' and self.last_block_reason
        state = {
            'mode': f'SHADOW · {SYMBOL} 1m FLUSH · {self.feed_label.upper()}',
            'status': f'DOGE 1m: {status}',
            'equity': round(liq, 4),
            'wallet': round(self.equity, 4),
            'pnl': round(liq - STARTING_EQUITY, 4),
            'volume': round(volume, 2),
            'fees': round(fees, 4),
            'score': round(p_pos, 4) if p_pos is not None else 0.0,
            'drawdown': round(max(0.0, (peak - liq) / peak), 6) if peak > 0 else None,
            'kill_distance_usdt': round(liq - (STARTING_EQUITY - KILL_LOSS_USDT), 4),
            'outcome_samples': n_primary,
            'armed': False,
            'shutdown': False,
            'shutdown_reason': '',
            'authorized': m.total_fills,
            'execute_gate': 0.768,
            'veto_gate': 0.70,
            'agents': [],                    # no swarm runs in this engine: show none rather than invent votes
            'market_data': self.feed_label,
            'policy': 'FLUSH_1M' + ('+MICRO' if self.micro_confirm else ''),
            'branches': branch_stats,
            'micro': micro,
            'markets': [{
                'symbol': SYMBOL,
                'mid': (cur_bid + cur_ask) / 2.0,
                'spread': spread_bps,
                'obi': obi,
                'vpin': None,
                'cvd_60s': cvd,
                'microprice': micro.get('microprice'),
                'score': p_pos if p_pos is not None else 0.0,
                'directional': (obi + 1.0) / 2.0 if obi is not None else 0.5,
                'edge_bps': edge_bps,
                'cost_bps': cost_bps,
                'reasons': [self.last_block_reason] if blocked else [],
                'regime': self.regime,
                'status': status,
                'why': (f'1m flush · regime {self.regime}'
                        + (' · FAST_DOWN' if self.fast_down else '')
                        + (f' · CVD60 {cvd:+.0f} USDT' if cvd is not None else '')
                        + ' · score = P(EV>0) of BE_15_10 posterior'),
            }],
            'positions': positions,
            'events': list(self.event_buffer),
            'timestamp': now,
            'updated_at': now,
            'history': list(self.history_buffer),
        }
        # Atomic publish: the dashboard polls this file ~1x/s while the engine
        # rewrites it every 2s. A plain write_text lets the reader observe a
        # half-written file and fall back to a stale snapshot; write-then-rename
        # makes every read see one complete version. os.replace is atomic on
        # Windows and POSIX when both paths share a volume.
        temp_file = state_file.with_suffix('.json.tmp')
        try:
            temp_file.write_text(json.dumps(state, allow_nan=False), encoding='utf-8')
            os.replace(temp_file, state_file)
        except Exception:
            try:
                temp_file.unlink()
            except OSError:
                pass


SHADOW_BUDGET_SECONDS = 12 * 3600   # data budget when --seconds is not given


async def run_shadow_loop(seconds: Optional[int] = None, gate: Optional[EarlyStopGate] = None,
                          use_ws: bool = True, micro_confirm: bool = False):
    """Real-time shadow loop: WebSocket ticks every 0.5 s, REST fallback every 2 s.

    Boot is one parallel REST round trip (1m/15m bars, book, OI) plus the socket
    handshake, so the first engine tick lands in ~1-2 s. Never open-ended: an
    EarlyStopGate over closed-trade net bps ends the run on REGIME_UNPROFITABLE /
    EDGE_CONFIRMED (>= 200 trades, 95%), and its DataBudget ends it after
    `seconds` or SHADOW_BUDGET_SECONDS, whichever applies. Returns the engine.
    """
    t_boot = time.monotonic()
    engine = FlushShadowEngine()
    engine.micro_confirm = micro_confirm
    gate = gate or EarlyStopGate(budget=DataBudget(max_seconds=float(seconds or SHADOW_BUDGET_SECONDS)))
    feed = MicroFeed() if use_ws else None
    feed_task = None
    bars15_task = None
    prime_task = None
    bars15: Optional[List[dict]] = None
    bars15_at = 0.0
    bars15_key = None
    booted = False
    seen_trades = 0
    last_status, last_print, last_publish, last_feed = None, 0.0, 0.0, None
    print('=' * 80)
    print('  DOGEUSDT 1m VOLATILITY FLUSH SHADOW ENGINE (3-BRANCH HARVESTER)')
    print(f'  Capital: {STARTING_EQUITY} USDT (~100k VND) | Max Loss Kill: {KILL_LOSS_USDT} USDT (5 cents)')
    print(f'  Min Notional: {MIN_NOTIONAL} USDT | Leverage: {LEVERAGE}x Isolated')
    print('  Branches: [Baseline fixed -20.44] vs [BE+15->+10] vs [BE+20->+10]')
    print(f'  Feed: {"WebSocket bookTicker+depth5+aggTrade+kline_1m" if use_ws else "REST only"}'
          f' | Micro gate: {"ON" if micro_confirm else "OFF (recorded only)"}')
    print('  Target: Lower95(EV) > 0 before live promotion')
    print('=' * 80)

    start_time = time.time()
    try:
        while True:
            if seconds and (time.time() - start_time >= seconds):
                print(f'\n[SHADOW] Completed {seconds}s target run duration.')
                break
            spent = gate.budget.exhausted() if gate.budget is not None else None
            if spent:
                print(f'\n[SHADOW] BUDGET_EXHAUSTED: {spent} after {gate.n} closed trades. Data loop closed.')
                break

            if not booted:
                booted = True
                engine.cohort_log = COHORT_LOG_FILE
                if feed is not None:
                    feed_task = asyncio.create_task(feed.run())
                # Priming history (99 x 15m) runs beside the warm start, not before it.
                prime_task = asyncio.create_task(asyncio.to_thread(fetch_bars15, 99))
            if prime_task is not None and prime_task.done():
                primed, prime_task = prime_task.result(), None
                if primed and len(primed) >= 61:
                    engine.prime_regime(primed)
                    bars15, bars15_at, bars15_key = primed[-60:], time.time(), primed[-1]['time']

            now = time.time()
            live = feed is not None and feed.ready(now)
            if live:
                ticker, klines, oi = feed.ticker(), list(feed.klines), None
                if bars15_task is None and now - bars15_at >= BARS15_REFRESH_SECONDS:
                    bars15_task = asyncio.create_task(asyncio.to_thread(fetch_bars15))
                fresh15 = None
                if bars15_task is not None and bars15_task.done():
                    fresh15 = bars15_task.result()
                    bars15_task, bars15_at = None, time.time()
                engine.feed_label = 'binance-ws'
            else:
                ticker, klines, oi, fresh15 = await asyncio.to_thread(fetch_rest_market_data)
                if feed is not None and klines:
                    feed.seed_klines(klines)
                engine.feed_label = 'binance-rest'
            if fresh15:
                bars15 = fresh15
            # Hand the 15m series to the regime gate once per NEW 15m bar only:
            # range_streak counts reads, and re-reading the same bar every tick
            # would satisfy the two-close hysteresis in one second.
            pass15 = None
            if bars15 and bars15[-1]['time'] != bars15_key:
                pass15, bars15_key = bars15, bars15[-1]['time']
            if feed is not None and feed.frames:
                engine.micro = feed.snapshot(now)

            if ticker and klines:
                res = engine.update_cycle(ticker, klines, oi, bars15=pass15)
                m = engine.metrics
                bid = ticker.get('bidPrice', 'nan')
                ask = ticker.get('askPrice', 'nan')
                status = res.get('status', 'WAITING')
                if seen_trades == 0 and last_status is None:
                    print(f'[BOOT] first tick {time.monotonic() - t_boot:.2f}s via {engine.feed_label}')

                mono = time.monotonic()
                if status != last_status or mono - last_print >= 5.0:
                    b_be15 = engine.shadow_branches['be_15_10'].get_stats()
                    b_base = engine.shadow_branches['baseline'].get_stats()
                    mic = engine.micro or {}
                    obi_s = f'{mic["obi"]:+.2f}' if mic.get('obi') is not None else ' n/a'
                    cvd_s = f'{mic["cvd_60s"]:+.0f}' if mic.get('cvd_60s') is not None else 'n/a'
                    p_pos = b_be15['p_ev_positive']
                    print(
                        f'[{time.strftime("%H:%M:%S")}] '
                        f'DOGE: {float(bid):.5f}/{float(ask):.5f} | '
                        f'{status} [{engine.regime}{"/FAST_DOWN" if engine.fast_down else ""}] | '
                        f'OBI {obi_s} CVD60 {cvd_s}U | '
                        f'Trades: {m.total_trades} (W:{m.wins}/P:{m.protects}/L:{m.losses}) | '
                        f'BE15 EV: {b_be15["analytical_ev_bps"]:+.2f}bps (L95: {b_be15["lower95_ev_bps"]:+.1f}'
                        f'{", P(EV>0) %.0f%%" % (p_pos * 100) if p_pos is not None else ""}) | '
                        f'Base EV: {b_base["analytical_ev_bps"]:+.2f}bps | '
                        f'Premature: {b_be15["premature_count"]}/{b_be15["protect_count"]} ({b_be15["premature_rate"]*100:.0f}%) | '
                        f'Eq: {engine.get_liquidation_equity(float(bid), float(ask)):.4f}U'
                    )
                    last_print = mono
                if (status != last_status or engine.feed_label != last_feed
                        or mono - last_publish >= TELEMETRY_SECONDS):
                    try:
                        engine.write_telemetry_snapshot(float(bid), float(ask), status)
                    except Exception:
                        pass
                    last_publish, last_feed = mono, engine.feed_label
                last_status = status

                for trade in engine.trades[seen_trades:]:
                    gate.update(float(trade.net_bps))
                seen_trades = len(engine.trades)
                if gate.verdict is not None and gate.verdict.stop:
                    v = gate.verdict
                    print(f'\n[SHADOW] {v.status}: {v.reason} (n={v.n}, mean {v.mean_bps:+.2f} bps). Data loop closed.')
                    break

                if m.is_killed:
                    print(f'\n[HALTED] {m.kill_reason}')
                    break
            else:
                print(f'[{time.strftime("%H:%M:%S")}] Fetching REST market data retry...')

            await asyncio.sleep(TICK_SECONDS if live else REST_POLL_SECONDS)
    finally:
        for task in (feed_task, bars15_task, prime_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
    return engine


def main():
    parser = argparse.ArgumentParser(description='DOGE 1m Flush Shadow Engine')
    parser.add_argument('--seconds', type=int, default=None, help='Run duration in seconds')
    parser.add_argument('--rest-only', action='store_true', help='Skip the WebSocket feed (2 s REST polling)')
    parser.add_argument('--micro-confirm', action='store_true',
                        help='Entry also needs OBI(L5) and CVD(60s) on the trade side. Off by default: '
                             'unvalidated; data/flush_cohorts.jsonl records both at every entry for calibration')
    args = parser.parse_args()

    try:
        asyncio.run(run_shadow_loop(seconds=args.seconds, use_ws=not args.rest_only,
                                    micro_confirm=args.micro_confirm))
    except KeyboardInterrupt:
        print('\n[SHADOW] Stopped cooperatively by user (Ctrl+C). Zero orders sent to exchange.')


if __name__ == '__main__':
    main()
