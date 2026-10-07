"""Polymarket 15-minute crypto Up/Down: Binance-implied fair value vs the book (READ-ONLY).

Market: event slug `{asset}-updown-15m-{window_start_unix}` (window starts are
multiples of 900 s), outcomes Up / Down. Settlement (since 2026-08-07) compares
Chainlink 60 s TWAPs at the window open and close - not a single print.

Fair value of Up at time t with tau minutes left, driftless log price, per-minute
vol sigma from Binance 1m bars, TWAP over the last k = 1 min:
    Var[ln S_twap(T) - ln S_t] = sigma^2 * (max(tau - k, 0) + min(tau, k) / 3)
    P(Up) = N( ln(S_t / S_open) / sqrt(Var) )
S_open is proxied by the Binance 1m open at the window start (basis vs the
Chainlink TWAP is a known error term, reported, not hidden).

Edge after the 2026 taker fee (shares x 0.07 x p x (1-p) on crypto):
    edge_up   = P(Up)   - ask_up   - fee(ask_up)
    edge_down = 1-P(Up) - ask_down - fee(ask_down)
Flag when the better side clears `min_edge`. Every flag is logged and settled
against Binance after the window closes, so the scanner reports its own hit
rate and realized EV instead of assuming one.

Why this is a measurement tool, not a money printer: the dynamic taker fee
(Jan 2026) and TWAP settlement (Aug 2026) were introduced specifically to remove
the Binance->Polymarket latency edge, market makers reprice in milliseconds, and
a 1-2 s Python poll from a home connection is the slow side of that race.
No wallet, no orders.

2026-10 upgrade - latency-impulse trigger (YES == "Up" share):
  impulse = Binance mark move over the last `impulse_window_s` (3-8 s oracle/AMM lag).
  IMPULSE flag when impulse >= +impulse_pct and Up ask < impulse_max_ask (mirror for
  Down) AND the model edge after fee on that side >= impulse_min_edge. The impulse
  is extra evidence the book is stale, so it lowers - never removes - the fee gate.
  Assets scan in parallel threads; tokens / sigma / open are cached per window so a
  2 s cadence costs 3 HTTP calls per asset instead of 6.

    python -m research.polymarket_15m_fast_arb --mode shadow
"""
from __future__ import annotations

import json
import math
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

GAMMA = 'https://gamma-api.polymarket.com'
CLOB = 'https://clob.polymarket.com'
FAPI = 'https://fapi.binance.com'
ASSETS = {'btc': 'BTCUSDT', 'eth': 'ETHUSDT', 'sol': 'SOLUSDT', 'xrp': 'XRPUSDT'}
WINDOW_S = 900


def _get_json(url: str, timeout: float = 5.0):
    req = urllib.request.Request(url, headers={'User-Agent': 'pm15/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


@dataclass(frozen=True)
class Pm15Config:
    assets: Tuple[str, ...] = ('btc', 'eth', 'sol', 'xrp')
    min_edge: float = 0.05            # probability points after fee
    fee_rate: float = 0.07            # crypto taker fee rate (docs.polymarket.com/trading/fees)
    vol_minutes: int = 120
    twap_min: float = 1.0
    min_tau_s: float = 20.0           # too close to the bell: skip (book and oracle both jump)
    stake_usdc: float = 2.0
    impulse_pct: float = 0.3          # Binance move (%) inside impulse_window_s
    impulse_window_s: float = 8.0
    impulse_max_ask: float = 0.45     # stale share still priced below this
    impulse_min_edge: float = 0.0     # model edge after fee still required (>= 0)
    cache_ttl_s: float = 60.0         # sigma / open refresh


# ----------------------------------------------------------------------------- math
def norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def window_start(now: float) -> int:
    return int(now // WINDOW_S * WINDOW_S)


def slug(asset: str, start: int) -> str:
    return f'{asset}-updown-15m-{start}'


def minute_vol(closes: List[float]) -> Optional[float]:
    r = [math.log(b / a) for a, b in zip(closes[:-1], closes[1:]) if a > 0 and b > 0]
    if len(r) < 10:
        return None
    m = sum(r) / len(r)
    return math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1))


def p_up(s_now: float, s_open: float, sigma_min: float, tau_min: float, twap_min: float = 1.0) -> float:
    var = sigma_min ** 2 * (max(tau_min - twap_min, 0.0) + min(tau_min, twap_min) / 3.0)
    x = math.log(s_now / s_open)
    if var <= 0:
        return 1.0 if x > 0 else 0.0 if x < 0 else 0.5
    return norm_cdf(x / math.sqrt(var))


def fee(price: float, rate: float) -> float:
    return rate * price * (1.0 - price)


def best_ask(book: dict) -> Optional[float]:
    asks = [float(a['price']) for a in (book or {}).get('asks', []) if float(a.get('size', 0)) > 0]
    return min(asks) if asks else None


def best_bid(book: dict) -> Optional[float]:
    bids = [float(b['price']) for b in (book or {}).get('bids', []) if float(b.get('size', 0)) > 0]
    return max(bids) if bids else None


def impulse_from_history(hist, now: float, window_s: float) -> Optional[float]:
    """% move from the oldest point inside `window_s` to the latest; hist = [(t, price), ...]."""
    pts = [(t, px) for t, px in hist if now - t <= window_s and px > 0]
    if len(pts) < 2:
        return None
    return (pts[-1][1] / pts[0][1] - 1.0) * 100.0


def evaluate(asset: str, start: int, now: float, s_now: float, s_open: float, sigma: float,
             up_book: dict, down_book: dict, cfg: Pm15Config, impulse_pct: Optional[float] = None) -> dict:
    tau = (start + WINDOW_S - now) / 60.0
    p = p_up(s_now, s_open, sigma, tau, cfg.twap_min)
    ua, da = best_ask(up_book), best_ask(down_book)
    eu = p - ua - fee(ua, cfg.fee_rate) if ua is not None else None
    ed = (1 - p) - da - fee(da, cfg.fee_rate) if da is not None else None
    side, edge, price = None, None, None
    for sd, e, a in (('UP', eu, ua), ('DOWN', ed, da)):
        if e is not None and (edge is None or e > edge):
            side, edge, price = sd, e, a
    alive = tau * 60 >= cfg.min_tau_s
    fair_flag = edge is not None and edge >= cfg.min_edge and alive
    imp_side = None
    if impulse_pct is not None and alive:
        if impulse_pct >= cfg.impulse_pct and ua is not None and ua < cfg.impulse_max_ask \
                and eu is not None and eu >= cfg.impulse_min_edge:
            imp_side = 'UP'
        elif impulse_pct <= -cfg.impulse_pct and da is not None and da < cfg.impulse_max_ask \
                and ed is not None and ed >= cfg.impulse_min_edge:
            imp_side = 'DOWN'
    if imp_side and not fair_flag:
        side, edge, price = imp_side, (eu if imp_side == 'UP' else ed), (ua if imp_side == 'UP' else da)
    trigger = ('FAIR+IMPULSE' if fair_flag and imp_side == side else 'FAIR' if fair_flag
               else 'IMPULSE' if imp_side else None)
    flag = trigger is not None
    shares = math.floor(cfg.stake_usdc / price) if price else 0
    return {'asset': asset, 'slug': slug(asset, start), 'start': start, 't': now, 'tau_s': tau * 60,
            'spot': s_now, 'open': s_open, 'move_pct': (s_now / s_open - 1) * 100, 'sigma_1m': sigma,
            'p_up': p, 'up_ask': ua, 'down_ask': da, 'up_bid': best_bid(up_book), 'down_bid': best_bid(down_book),
            'side': side, 'price': price, 'edge': edge, 'flag': flag, 'shares': shares,
            'payoff_ratio': (1 - price) / price if price else None,
            'impulse_pct': impulse_pct, 'trigger': trigger,
            'exp_roi': edge / price if (edge is not None and price) else None}


# ----------------------------------------------------------------------------- venue I/O
def market_tokens(event: dict) -> Optional[Dict[str, str]]:
    """{'UP': token_id, 'DOWN': token_id} from a gamma event (outcomes/clobTokenIds are JSON strings)."""
    for m in (event or {}).get('markets') or []:
        try:
            outs = m['outcomes'] if isinstance(m['outcomes'], list) else json.loads(m['outcomes'])
            toks = m['clobTokenIds'] if isinstance(m['clobTokenIds'], list) else json.loads(m['clobTokenIds'])
        except (KeyError, TypeError, ValueError):
            continue
        mp = {str(o).upper(): t for o, t in zip(outs, toks)}
        if 'UP' in mp and 'DOWN' in mp:
            return {'UP': mp['UP'], 'DOWN': mp['DOWN']}
    return None


def _scan_asset(asset: str, start: int, now: float, cfg: Pm15Config, http: Callable,
                cache: Optional[dict], impulse: Optional[float]) -> dict:
    sym = ASSETS.get(asset)
    key = (asset, start)
    c = cache.get(key) if cache is not None else None
    if c is None:
        ev = http(f'{GAMMA}/events?slug={slug(asset, start)}')
        ev = ev[0] if isinstance(ev, list) and ev else ev
        toks = market_tokens(ev)
        if not toks:
            raise LookupError(f'no live 15m market for {slug(asset, start)}')
        c = {'toks': toks, 'vol_at': -1e18}
    if now - c['vol_at'] >= cfg.cache_ttl_s:
        kl = http(f'{FAPI}/fapi/v1/klines?symbol={sym}&interval=1m&limit={cfg.vol_minutes + 1}')
        closes = [float(k[4]) for k in kl]
        opens = {int(k[0]) // 1000: float(k[1]) for k in kl}
        s_open = opens.get(start)
        if s_open is None:
            o = http(f'{FAPI}/fapi/v1/klines?symbol={sym}&interval=1m&startTime={start * 1000}&limit=1')
            s_open = float(o[0][1])
        c.update(sigma=minute_vol(closes[:-1]) or 0.0, s_open=s_open, vol_at=now)
    if cache is not None:
        cache[key] = c                                     # distinct key per thread: no race
    s_now = float(http(f'{FAPI}/fapi/v1/ticker/price?symbol={sym}')['price'])
    up_b = http(f'{CLOB}/book?token_id={c["toks"]["UP"]}')
    dn_b = http(f'{CLOB}/book?token_id={c["toks"]["DOWN"]}')
    return evaluate(asset, start, now, s_now, c['s_open'], c['sigma'], up_b, dn_b, cfg, impulse)


def scan_once(cfg: Pm15Config = Pm15Config(), http: Callable = _get_json, now: Optional[float] = None,
              cache: Optional[dict] = None, impulses: Optional[Dict[str, float]] = None,
              parallel: bool = True) -> dict:
    now = time.time() if now is None else now
    start = window_start(now)
    rows, errors = [], []
    impulses = impulses or {}
    if cache is not None:                                  # drop previous windows before fan-out
        for k in [k for k in cache if k[1] != start]:
            cache.pop(k, None)

    def one(asset):
        try:
            return _scan_asset(asset, start, now, cfg, http, cache, impulses.get(asset)), None
        except LookupError as exc:
            return None, f'{asset}: {exc}'
        except Exception as exc:
            return None, f'{asset}: {type(exc).__name__}: {exc}'[:160]

    if parallel and len(cfg.assets) > 1:
        with ThreadPoolExecutor(max_workers=len(cfg.assets)) as ex:
            results = list(ex.map(one, cfg.assets))
    else:
        results = [one(a) for a in cfg.assets]
    for row, err in results:
        if row is not None:
            rows.append(row)
        if err:
            errors.append(err)
    t1 = time.time()
    return {'t': now, 'window_start': start, 'rows': rows, 'errors': errors, 'scan_ms': (t1 - now) * 1000.0
            if abs(t1 - now) < 60 else None}


# ----------------------------------------------------------------------------- self-audit
class SignalLedger:
    """Logs each flagged (window, side) once, settles it after the window, keeps score."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path
        self.open: Dict[Tuple[str, str], dict] = {}
        self.settled: List[dict] = []

    def record(self, row: dict) -> bool:
        if not row.get('flag'):
            return False
        key = (row['slug'], row['side'])
        if key in self.open:
            return False
        self.open[key] = dict(row)
        return True

    def settle(self, now: float, final_price: Callable[[str, int], Optional[float]], rate: float = 0.07) -> List[dict]:
        done = []
        for key, r in list(self.open.items()):
            end = r['start'] + WINDOW_S
            if now < end + 90:                         # let the closing TWAP minute print
                continue
            px = final_price(r['asset'], end)
            if px is None:
                continue
            up = px > r['open']
            win = up if r['side'] == 'UP' else not up
            pnl = (1.0 if win else 0.0) - r['price'] - fee(r['price'], rate)
            out = dict(r, final=px, won=win, pnl_per_share=pnl, roi=pnl / r['price'])
            self.settled.append(out)
            done.append(out)
            del self.open[key]
            if self.path is not None:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with open(self.path, 'a', encoding='utf-8') as fh:
                        fh.write(json.dumps(out) + '\n')
                except OSError:
                    pass
        return done

    def score(self) -> dict:
        n = len(self.settled)
        if not n:
            return {'n': 0, 'open': len(self.open)}
        rois = [s['roi'] for s in self.settled]
        return {'n': n, 'open': len(self.open), 'hit_rate': sum(s['won'] for s in self.settled) / n,
                'mean_roi': sum(rois) / n, 'mean_edge_claimed': sum(s['edge'] for s in self.settled) / n}


def binance_close(http: Callable = _get_json) -> Callable[[str, int], Optional[float]]:
    """Mean of the last 1m bar before `end` (open+close)/2 ~ a 60 s TWAP proxy."""
    def f(asset: str, end: int) -> Optional[float]:
        try:
            k = http(f'{FAPI}/fapi/v1/klines?symbol={ASSETS[asset]}&interval=1m&startTime={(end - 60) * 1000}&limit=1')
            return (float(k[0][1]) + float(k[0][4])) / 2.0
        except Exception:
            return None
    return f


def main(argv=None):
    """Standalone Polymarket 15m scanner (shadow). Runs the fast lane with the sniper in observe mode
    (its 1 s mark stream feeds the impulse trigger)."""
    import argparse
    ap = argparse.ArgumentParser(description='Polymarket 15m latency/fair-value scanner (SHADOW - no wallet)')
    ap.add_argument('--mode', choices=['shadow'], default='shadow')
    _, rest = ap.parse_known_args(argv)
    from research import fast_runner
    fast_runner.main(['--no-liq', *rest])


if __name__ == '__main__':
    main()
