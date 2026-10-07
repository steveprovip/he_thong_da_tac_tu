"""Instant opportunity scan: REST snapshot (<60 s), no L2 recording.

Channels, all priced against an explicit all-in cost and the invariant
gross > 3 x cost (System-1 score >= 8.5):

  SPOT_PERP_CARRY   Binance: long spot + short perp on POSITIVE funding.
                    C = 2 x spot fee + perp maker-in/taker-out + both spreads + slippage.
                    G = projected rate x settlements over the horizon, where the
                    projection is min(current, mean of last 21 settlements).
  NEEDS_SPOT_SHORT  Negative funding: the hedge needs a spot short (margin
                    borrow). Listed, never executable from here.
  XVENUE_FUNDING    Binance vs Bybit perp funding differential (both legs are
                    perps, no borrow).  Listed RESEARCH_ONLY: a differential has
                    no settled history in this snapshot, so G is unproven.
  LIQUIDATION_SHOCK Cascading forced orders >= 500k USDT / 60 s (WebSocket
                    !forceOrder@arr), shadow-faded and resolved on mark price.
                    Live only after an EarlyStopGate proves mean gross >= 25 bps.

Every fetch is injectable so the ranking is unit-tested offline.
"""
from collections import defaultdict, deque
from dataclasses import dataclass, asdict
import json
import math
import time
import urllib.parse
import urllib.request

from core.early_stop import COST_ALL_IN_BPS, EDGE_CONFIRMED, EarlyStopGate, DataBudget
from core.jev_gatekeeper import JevGatekeeper, PERMIT_SCORE, score_from_ratio

BINANCE_FAPI = 'https://fapi.binance.com'
BINANCE_SPOT = 'https://api.binance.com'
BYBIT = 'https://api.bybit.com'

SPOT_FEE_BPS = 10.0          # Binance spot VIP0 maker/taker
PERP_MAKER_BPS = 2.0
PERP_TAKER_BPS = 5.0
SLIPPAGE_BPS = 2.0
TOP_N = 20
MIN_QUOTE_VOLUME = 50e6
MIN_HISTORY = 21
LIQ_THRESHOLD_USDT = 500_000.0
LIQ_WINDOW_S = 60.0
LIQ_MIN_GROSS_BPS = 25.0


def fetch_json(url, timeout=10.0):
    parts = urllib.parse.urlsplit(url)
    encoded_url = urllib.parse.urlunsplit((
        parts.scheme,
        parts.netloc,
        urllib.parse.quote(parts.path),
        urllib.parse.quote(parts.query, safe='=&?'),
        urllib.parse.quote(parts.fragment)
    ))
    req = urllib.request.Request(encoded_url, headers={'User-Agent': 'groktopus-instant/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def _f(x, default=float('nan')):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _spread_bps(bid, ask):
    mid = (bid + ask) / 2
    return (ask - bid) / mid * 1e4 if mid > 0 and ask >= bid > 0 else float('nan')


@dataclass(frozen=True)
class Opportunity:
    channel: str
    symbol: str
    rate_bps_8h: float
    gross_bps: float
    cost_bps: float
    ratio: float
    score: float
    executable: bool
    reason: str

    @property
    def net_bps(self):
        return self.gross_bps - self.cost_bps

    def as_dict(self):
        d = asdict(self)
        d['net_bps'] = self.net_bps
        return {k: (round(v, 3) if isinstance(v, float) and math.isfinite(v) else v) for k, v in d.items()}


def binance_snapshot(fetch=fetch_json, top_n=TOP_N, min_volume=MIN_QUOTE_VOLUME):
    """One REST pass. Returns {symbol: row}. ~4 + 2*top_n requests."""
    premium = fetch(f'{BINANCE_FAPI}/fapi/v1/premiumIndex')
    info = {r['symbol']: r for r in fetch(f'{BINANCE_FAPI}/fapi/v1/fundingInfo')}
    vol = {r['symbol']: _f(r.get('quoteVolume'), 0.0) for r in fetch(f'{BINANCE_FAPI}/fapi/v1/ticker/24hr')}
    perp_book = {r['symbol']: r for r in fetch(f'{BINANCE_FAPI}/fapi/v1/ticker/bookTicker')}
    spot_book = {r['symbol']: r for r in fetch(f'{BINANCE_SPOT}/api/v3/ticker/bookTicker')}
    rows = []
    for p in premium:
        s = p.get('symbol', '')
        if not s.endswith('USDT') or not s.isascii() or vol.get(s, 0.0) < min_volume or s not in perp_book:
            continue
        interval = int(_f(info.get(s, {}).get('fundingIntervalHours'), 8.0) or 8)
        rate = _f(p.get('lastFundingRate'), 0.0) * 1e4
        rows.append({'symbol': s, 'rate_bps': rate, 'interval_h': interval,
                     'rate_bps_8h': rate * 8 / interval,
                     'next_funding': _f(p.get('nextFundingTime'), 0.0) / 1000,
                     'mark': _f(p.get('markPrice')), 'index': _f(p.get('indexPrice')),
                     'quote_volume': vol[s],
                     'perp_bid': _f(perp_book[s].get('bidPrice')), 'perp_ask': _f(perp_book[s].get('askPrice')),
                     'spot_bid': _f(spot_book.get(s, {}).get('bidPrice')),
                     'spot_ask': _f(spot_book.get(s, {}).get('askPrice'))})
    rows.sort(key=lambda r: -abs(r['rate_bps_8h']))
    rows = rows[:top_n]
    for r in rows:
        sym_q = urllib.parse.quote(r['symbol'])
        hist = fetch(f'{BINANCE_FAPI}/fapi/v1/fundingRate?symbol={sym_q}&limit={MIN_HISTORY}')
        r['history_bps_8h'] = [_f(h.get('fundingRate'), 0.0) * 1e4 * 8 / r['interval_h'] for h in hist]
        oi = fetch(f'{BINANCE_FAPI}/fapi/v1/openInterest?symbol={sym_q}')
        r['oi_usdt'] = _f(oi.get('openInterest'), 0.0) * _f(r['mark'], 0.0)
    return {r['symbol']: r for r in rows}


def bybit_snapshot(fetch=fetch_json):
    """Bybit v5 linear tickers + funding interval. Returns {symbol: row}."""
    tick = fetch(f'{BYBIT}/v5/market/tickers?category=linear')['result']['list']
    inst = fetch(f'{BYBIT}/v5/market/instruments-info?category=linear&limit=1000')['result']['list']
    minutes = {i['symbol']: _f(i.get('fundingInterval'), 480.0) for i in inst}
    out = {}
    for t in tick:
        s = t.get('symbol', '')
        if not s.endswith('USDT') or not s.isascii():
            continue
        hours = (minutes.get(s, 480.0) or 480.0) / 60
        rate = _f(t.get('fundingRate'), 0.0) * 1e4
        out[s] = {'symbol': s, 'rate_bps_8h': rate * 8 / hours, 'interval_h': hours,
                  'bid': _f(t.get('bid1Price')), 'ask': _f(t.get('ask1Price')),
                  'turnover': _f(t.get('turnover24h'), 0.0)}
    return out


def settlements(now, next_funding, interval_h, horizon_days):
    if not _finite_all(now, next_funding, interval_h, horizon_days) or interval_h <= 0 or next_funding <= now:
        return 0
    return 1 + int((now + horizon_days * 86400 - next_funding) // (interval_h * 3600))


def _finite_all(*v):
    return all(isinstance(x, (int, float)) and math.isfinite(x) for x in v)


def rank_opportunities(binance, bybit=None, now=None, horizon_days=7.0, gatekeeper=None):
    """Price every channel; executable only if basis/spread sane and gross > 3C."""
    now = time.time() if now is None else now
    gk = gatekeeper or JevGatekeeper()
    out = []
    for s, r in binance.items():
        hist = r.get('history_bps_8h') or []
        n = settlements(now, r['next_funding'], r['interval_h'], horizon_days)
        perp_spread = _spread_bps(r['perp_bid'], r['perp_ask'])
        spot_spread = _spread_bps(r['spot_bid'], r['spot_ask'])
        rate8 = r['rate_bps_8h']
        if rate8 < 0:
            cost = 2 * SPOT_FEE_BPS + PERP_MAKER_BPS + PERP_TAKER_BPS + SLIPPAGE_BPS
            gross = abs(rate8) * n * r['interval_h'] / 8
            ratio = gross / cost
            out.append(Opportunity('NEEDS_SPOT_SHORT', s, rate8, gross, cost, ratio, score_from_ratio(ratio),
                                   False, 'negative funding: hedge needs spot borrow'))
            continue
        if not math.isfinite(spot_spread):
            out.append(Opportunity('SPOT_PERP_CARRY', s, rate8, 0.0, float('nan'), 0.0, 0.0, False, 'NO_SPOT_MARKET'))
            continue
        cost = (2 * SPOT_FEE_BPS + PERP_MAKER_BPS + PERP_TAKER_BPS + spot_spread
                + perp_spread + SLIPPAGE_BPS)
        projected = min(rate8, sum(hist) / len(hist)) if hist else rate8
        gross = max(0.0, projected) * n * r['interval_h'] / 8
        ratio = gross / cost if cost > 0 else 0.0
        score = gk.score(max(spot_spread, perp_spread), 0.0, funding_bps_8h=max(0.0, projected),
                         funding_periods=int(n * r['interval_h'] / 8), carry_cost_bps=cost)
        basis = abs((r['spot_bid'] + r['spot_ask']) / 2 - r['mark']) / r['mark'] if r['mark'] > 0 else 1.0
        reasons = []
        if len(hist) < MIN_HISTORY:
            reasons.append(f'HISTORY {len(hist)}<{MIN_HISTORY}')
        if hist and sum(h > 0 for h in hist) / len(hist) < .8:
            reasons.append('FUNDING_NOT_PERSISTENT')
        if basis > .015:
            reasons.append(f'BASIS {basis*100:.2f}%>1.5%')
        if spot_spread >= 3:
            reasons.append(f'SPOT_SPREAD {spot_spread:.2f}>=3')
        if r.get('oi_usdt', 0.0) <= 20e6:
            reasons.append('OI<=20M')
        if score < PERMIT_SCORE:
            reasons.append(f'SCORE {score:.2f}<{PERMIT_SCORE} (G/C {ratio:.2f}<3)')
        out.append(Opportunity('SPOT_PERP_CARRY', s, rate8, gross, cost, ratio, score, not reasons,
                               'G>3C ELIGIBLE' if not reasons else '; '.join(reasons)))
    for s, b in (bybit or {}).items():
        a = binance.get(s)
        if a is None:
            continue
        diff = b['rate_bps_8h'] - a['rate_bps_8h']
        cost = 2 * (PERP_MAKER_BPS + PERP_TAKER_BPS) + _spread_bps(b['bid'], b['ask']) + \
            _spread_bps(a['perp_bid'], a['perp_ask']) + 2 * SLIPPAGE_BPS
        n8 = int(horizon_days * 3)
        gross = abs(diff) * n8
        ratio = gross / cost if cost > 0 and math.isfinite(cost) else 0.0
        out.append(Opportunity('XVENUE_FUNDING', s, diff, gross, cost, ratio, score_from_ratio(ratio), False,
                               'RESEARCH_ONLY: differential persistence unproven; needs Bybit account'))
    out.sort(key=lambda o: (not o.executable, -o.score, -o.ratio))
    return out


# --------------------------------------------------------- liquidation shock
@dataclass(frozen=True)
class Shock:
    symbol: str
    liquidated_side: str      # SELL = longs force-sold (price pushed down)
    notional_usdt: float
    at: float
    mark: float

    @property
    def fade_side(self):
        return 'BUY' if self.liquidated_side == 'SELL' else 'SELL'


class LiquidationWindow:
    """Rolling per-symbol, per-side forced-order notional; one shock per burst."""

    def __init__(self, threshold_usdt=LIQ_THRESHOLD_USDT, window_s=LIQ_WINDOW_S):
        if threshold_usdt <= 0 or window_s <= 0:
            raise ValueError('invalid liquidation window')
        self.threshold, self.window = threshold_usdt, window_s
        self.events = defaultdict(deque)
        self.cooldown = {}

    def add(self, symbol, side, price, qty, ts, mark=None):
        if side not in ('BUY', 'SELL') or not _finite_all(price, qty, ts) or price <= 0 or qty <= 0:
            return None
        key = (symbol, side)
        q = self.events[key]
        q.append((ts, price * qty))
        while q and ts - q[0][0] > self.window:
            q.popleft()
        total = sum(n for _, n in q)
        if total >= self.threshold and ts >= self.cooldown.get(key, -math.inf):
            self.cooldown[key] = ts + self.window
            q.clear()
            return Shock(symbol, side, total, ts, mark if mark and mark > 0 else price)
        return None


def parse_force_order(msg):
    """Binance !forceOrder@arr payload -> (symbol, side, price, qty, ts) or None."""
    o = (msg.get('data') or msg).get('o') if isinstance(msg, dict) else None
    if not o:
        return None
    return (o.get('s'), o.get('S'), _f(o.get('ap')) if _f(o.get('ap'), 0) > 0 else _f(o.get('p')),
            _f(o.get('z')) if _f(o.get('z'), 0) > 0 else _f(o.get('q')), _f(o.get('T')) / 1000)


class ShockShadow:
    """Fade each shock on paper, resolve on mark after `horizon_s`, gate the result."""

    def __init__(self, horizon_s=300.0, cost_bps=COST_ALL_IN_BPS, gate=None):
        self.horizon, self.cost = horizon_s, cost_bps
        self.open = []
        self.closed = []
        self.gate = gate or EarlyStopGate(budget=DataBudget(max_seconds=900))

    def open_shock(self, shock):
        self.open.append(shock)

    def resolve(self, marks, now):
        done = []
        for s in list(self.open):
            if now - s.at < self.horizon or s.symbol not in marks:
                continue
            sign = 1 if s.fade_side == 'BUY' else -1
            gross = sign * (marks[s.symbol] - s.mark) / s.mark * 1e4
            self.open.remove(s)
            self.closed.append((s, gross))
            self.gate.update(gross - self.cost)
            done.append((s, gross))
        return done

    def live_permitted(self):
        """Live fading needs a CONFIRMED edge AND mean gross >= 25 bps."""
        v = self.gate.verdict
        if v is None or v.status != EDGE_CONFIRMED:
            return False
        return v.mean_bps + self.cost >= LIQ_MIN_GROSS_BPS

    def summary(self):
        g = [x for _, x in self.closed]
        return {'shocks_open': len(self.open), 'shocks_closed': len(g),
                'mean_gross_bps': round(sum(g) / len(g), 3) if g else None,
                'gate': self.gate.verdict.as_dict() if self.gate.verdict else None,
                'live_permitted': self.live_permitted()}
