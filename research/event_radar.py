"""Event Radar — Polymarket prediction markets: arbitrage checks, crypto-threshold
model edge, macro-event watch. Read-only (public Gamma API, no wallet, no orders).

Three outputs, from strongest to weakest evidence:
  1. ARB (negRisk multi-outcome events): exactly one outcome resolves YES, so
       sum(best YES asks) < 1 - fees  -> buy one YES of every outcome
       sum(best YES bids) > 1 + fees  -> buy one NO of every outcome
     Profit is locked at entry IF the listed outcomes are exhaustive and depth
     exists at the quoted price. Placeholder / "Other" outcomes break that: flagged.
  2. MODEL EDGE (crypto price-threshold markets: "Bitcoin above $X on <date>",
     "reach $X by", "dip to $X"): driftless-in-price GBM with realized 30d vol
     from Binance 1h bars. Terminal: N(d2). Touch: exact first-passage formula.
     Edge = p_model - ask - taker fee. Uncalibrated: fat tails and scheduled
     events are not in the model, so the board also shows the vol the market
     implies. Every flagged quote is logged for later calibration (Brier).
  3. MACRO WATCH (Fed / CPI / ETF / listing ...): market odds only, no edge claim.

Fees (docs.polymarket.com/trading/fees, 2026): makers pay 0; takers pay
shares * rate * p * (1-p); rate 0.07 crypto, 0.04 finance/politics/tech,
0 geopolitics, 0.05 otherwise.
"""
from __future__ import annotations

import json
import math
import re
import time
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

GAMMA = 'https://gamma-api.polymarket.com'
FAPI = 'https://fapi.binance.com'
YEAR_S = 365.0 * 86400.0


def _get_json(url: str, timeout: float = 10.0):
    req = urllib.request.Request(url, headers={'User-Agent': 'event-radar/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


@dataclass(frozen=True)
class EventConfig:
    events_limit: int = 300
    min_edge: float = 0.05            # probability points after fee
    min_liquidity: float = 1000.0
    max_spread: float = 0.06
    min_arb: float = 0.01             # locked profit per $1 set, after fees
    stake_usdt: float = 2.0           # $1-3 per bet on a 3.85 USDT account
    vol_days: int = 30
    macro_top: int = 12
    equity: float = 3.85              # account size: an arb is only actionable if one full set fits
    hot_edge: float = 0.15            # model edge (pp after fee) that goes on the alert board


# ----------------------------------------------------------------------------- math
def norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def p_terminal_above(s: float, k: float, sigma: float, t: float) -> float:
    if t <= 0 or sigma <= 0:
        return 1.0 if s > k else 0.0
    d2 = (math.log(s / k) - 0.5 * sigma * sigma * t) / (sigma * math.sqrt(t))
    return norm_cdf(d2)


def p_touch(s: float, k: float, sigma: float, t: float) -> float:
    """P(path touches k before t) for GBM with zero price drift (log drift -sigma^2/2)."""
    if s == k:
        return 1.0
    if t <= 0 or sigma <= 0:
        return 0.0
    nu = -0.5 * sigma * sigma
    b = math.log(k / s)
    st = sigma * math.sqrt(t)
    ratio = math.exp(2 * nu * b / (sigma * sigma))           # = s/k
    if b > 0:
        return min(1.0, norm_cdf((-b + nu * t) / st) + ratio * norm_cdf((-b - nu * t) / st))
    return min(1.0, norm_cdf((b - nu * t) / st) + ratio * norm_cdf((b + nu * t) / st))


def model_probability(kind: str, s: float, k: float, sigma: float, t: float) -> Optional[float]:
    if kind == 'above':
        return p_terminal_above(s, k, sigma, t)
    if kind == 'below':
        return 1.0 - p_terminal_above(s, k, sigma, t)
    if kind == 'touch_up':
        return 1.0 if s >= k else p_touch(s, k, sigma, t)
    if kind == 'touch_down':
        return 1.0 if s <= k else p_touch(s, k, sigma, t)
    return None


def implied_vol(kind: str, s: float, k: float, t: float, price: float) -> Optional[float]:
    """Vol at which the model reproduces the market price (bisection, 1%..500%)."""
    if not 0.01 < price < 0.99 or t <= 0:
        return None
    lo, hi = 0.01, 5.0
    f_lo = model_probability(kind, s, k, lo, t) - price
    f_hi = model_probability(kind, s, k, hi, t) - price
    if f_lo * f_hi > 0:
        return None
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        f = model_probability(kind, s, k, mid, t) - price
        if f * f_lo > 0:
            lo, f_lo = mid, f
        else:
            hi = mid
    return 0.5 * (lo + hi)


def realized_vol(closes: List[float], bars_per_year: float = 24 * 365) -> Optional[float]:
    rets = [math.log(b / a) for a, b in zip(closes[:-1], closes[1:]) if a > 0 and b > 0]
    if len(rets) < 24:
        return None
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var * bars_per_year)


def taker_fee(price: float, rate: float) -> float:
    return rate * price * (1.0 - price)


def fee_rate(text: str) -> float:
    t = (text or '').lower()
    if any(w in t for w in ('crypto', 'bitcoin', 'ethereum', 'solana', 'btc', 'eth ')):
        return 0.07
    if any(w in t for w in ('geopolit', 'world')):
        return 0.0
    if any(w in t for w in ('politic', 'election', 'finance', 'tech', 'mention', 'fed', 'rate')):
        return 0.04
    return 0.05


# ----------------------------------------------------------------------------- parsing
ASSETS = {'bitcoin': 'BTCUSDT', 'btc': 'BTCUSDT', 'ethereum': 'ETHUSDT', 'eth': 'ETHUSDT',
          'solana': 'SOLUSDT', 'sol': 'SOLUSDT', 'xrp': 'XRPUSDT', 'dogecoin': 'DOGEUSDT',
          'doge': 'DOGEUSDT', 'bnb': 'BNBUSDT'}
_NUM = r'\$\s?([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kKmMbB])?\b'
_KINDS = (
    ('touch_down', r'\b(?:dip|fall|drop|crash|sink|go)\s+(?:to|below|under)\b'),
    ('touch_up', r'\b(?:reach|hit|touch|surpass|break|rise to|climb to|go above)\b'),
    ('above', r'\b(?:above|over|greater than|higher than|at or above)\b'),
    ('below', r'\b(?:below|under|less than|lower than)\b'),
)


def parse_threshold_question(q: str) -> Optional[Tuple[str, str, float]]:
    """-> (binance_symbol, kind, strike) for single-strike crypto questions, else None."""
    text = (q or '').lower()
    if 'between' in text or ' or ' in text or 'up or down' in text:
        return None
    sym = next((ASSETS[w] for w in re.findall(r'[a-z]+', text) if w in ASSETS), None)
    if sym is None:
        return None
    kind = next((k for k, pat in _KINDS if re.search(pat, text)), None)
    nums = re.findall(_NUM, q or '')
    if not nums:   # "Bitcoin above 120,000 on October 3?" -> only comma-grouped or k-suffixed numbers
        nums = re.findall(r'\b([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?)()\b|\b([0-9]+(?:\.[0-9]+)?)\s*([kK])\b',
                          q or '')
        nums = [(x[0], x[1]) if x[0] else (x[2], x[3]) for x in nums]
    if kind is None or len(nums) != 1:
        return None
    val, suf = nums[0]
    strike = float(val.replace(',', '')) * {'k': 1e3, 'm': 1e6, 'b': 1e9}.get(suf.lower(), 1.0)
    return (sym, kind, strike) if strike > 0 else None


def _jl(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def parse_time(v) -> Optional[float]:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace('Z', '+00:00')).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


def normalize_market(m: dict, event: Optional[dict] = None) -> Optional[dict]:
    outcomes = _jl(m.get('outcomes')) or []
    prices = _jl(m.get('outcomePrices')) or []
    if len(outcomes) != 2 or len(prices) != 2:
        return None
    yes_i = 0 if str(outcomes[0]).lower() == 'yes' else 1 if str(outcomes[1]).lower() == 'yes' else 0
    yes = _num(prices[yes_i])
    bid, ask = _num(m.get('bestBid')), _num(m.get('bestAsk'))
    tags = ' '.join(str(t.get('label', '')) for t in (m.get('tags') or []) + ((event or {}).get('tags') or [])
                    if isinstance(t, dict))
    cat = ' '.join(filter(None, [m.get('category'), (event or {}).get('category'), tags]))
    return {'id': m.get('id'), 'slug': m.get('slug'), 'question': m.get('question', ''),
            'event': (event or {}).get('title'), 'event_slug': (event or {}).get('slug'),
            'yes': yes, 'bid': bid, 'ask': ask,
            'spread': (ask - bid) if bid is not None and ask is not None else None,
            'volume24h': _num(m.get('volume24hr')) or 0.0,
            'liquidity': _num(m.get('liquidityNum') or m.get('liquidity')) or 0.0,
            'end': parse_time(m.get('endDate')), 'change_1d': _num(m.get('oneDayPriceChange')),
            'active': bool(m.get('active', True)) and not m.get('closed', False),
            'accepting': m.get('acceptingOrders', True) is not False,
            'min_size': _num(m.get('orderMinSize')) or 5.0,
            'fee_rate': fee_rate(cat + ' ' + m.get('question', '')), 'category': cat.strip()}


# ----------------------------------------------------------------------------- analyses
PLACEHOLDER = re.compile(r'\b(other|another|none of|no one|field)\b', re.I)


def negrisk_arb(event: dict, markets: List[dict], cfg: EventConfig) -> Optional[dict]:
    live = [m for m in markets if m and m['active'] and m['accepting']]
    if not event.get('negRisk') or len(live) < 2 or len(live) != len(markets):
        return None
    asks = [m['ask'] for m in live]
    bids = [m['bid'] for m in live]
    out = None
    if all(a is not None and 0 < a < 1 for a in asks):
        cost = sum(asks) + sum(taker_fee(a, m['fee_rate']) for a, m in zip(asks, live))
        if 1.0 - cost >= cfg.min_arb:
            out = {'type': 'BUY_ALL_YES', 'edge': 1.0 - cost, 'sum': sum(asks), 'cost': cost}
    if out is None and all(b is not None and 0 < b < 1 for b in bids):
        n = len(live)
        cost = sum(1 - b for b in bids) + sum(taker_fee(1 - b, m['fee_rate']) for b, m in zip(bids, live))
        if (n - 1) - cost >= cfg.min_arb:
            out = {'type': 'BUY_ALL_NO', 'edge': (n - 1) - cost, 'sum': sum(bids), 'cost': cost}
    if out is None:
        return None
    # every leg needs the same share count and each leg must clear its orderMinSize
    shares = max(m['min_size'] for m in live)
    min_capital = shares * out['cost']
    ends = [m['end'] for m in live if m['end']]
    days = (max(ends) - time.time()) / 86400.0 if ends else None
    out.update(min_shares=shares, min_capital=min_capital, fits_account=min_capital <= cfg.equity,
               locked_usdc=shares * out['edge'], days_to_resolution=days,
               apr=(out['edge'] / out['cost'] * 365.0 / days) if days and days > 0 and out['cost'] > 0 else None)
    out.update(event=event.get('title'), slug=event.get('slug'), legs=len(live),
               warnings=(['outcome placeholder -> set may not be exhaustive']
                         if any(PLACEHOLDER.search(m['question']) for m in live) else [])
               + ([] if min_capital <= cfg.equity else
                  [f'needs >= {min_capital:.2f} USDC for one set ({shares:g} shares x {len(live)} legs) > account {cfg.equity}'])
               + ['check depth at each best price before sizing',
                  'profit is paid at resolution: capital is locked until then'])
    return out


def threshold_edge(m: dict, spot: float, sigma: float, now: float, cfg: EventConfig) -> Optional[dict]:
    parsed = parse_threshold_question(m['question'])
    if not parsed or m['end'] is None or m['bid'] is None or m['ask'] is None:
        return None
    sym, kind, strike = parsed
    t = (m['end'] - now) / YEAR_S
    if t <= 0 or t > 1.0:
        return None
    p = model_probability(kind, spot, strike, sigma, t)
    if p is None:
        return None
    yes_ask, no_ask = m['ask'], 1.0 - m['bid']
    ev_yes = p - yes_ask - taker_fee(yes_ask, m['fee_rate'])
    ev_no = (1 - p) - no_ask - taker_fee(no_ask, m['fee_rate'])
    side, ev, price = ('YES', ev_yes, yes_ask) if ev_yes >= ev_no else ('NO', ev_no, no_ask)
    mid = (m['bid'] + m['ask']) / 2
    shares = math.floor(cfg.stake_usdt / price) if price > 0 else 0
    return {'id': m['id'], 'question': m['question'], 'slug': m['slug'], 'event_slug': m['event_slug'],
            'symbol': sym, 'kind': kind, 'strike': strike, 'spot': spot, 'days': t * 365,
            'sigma': sigma, 'implied_vol': implied_vol(kind, spot, strike, t, mid),
            'p_model': p, 'bid': m['bid'], 'ask': m['ask'], 'side': side, 'price': price,
            'edge': ev, 'roi': ev / price if price > 0 else None,
            'payoff_ratio': (1 - price) / price if price > 0 else None,
            'shares': shares, 'stake': shares * price, 'min_size_ok': shares >= m['min_size'],
            'liquidity': m['liquidity'], 'spread': m['spread'],
            'flag': (ev >= cfg.min_edge and m['liquidity'] >= cfg.min_liquidity
                     and (m['spread'] or 1) <= cfg.max_spread)}


MACRO = re.compile(r'\b(fed|fomc|interest rate|rate cut|rate hike|cpi|inflation|pce|jobs report|'
                   r'nonfarm|unemployment|recession|gdp|etf|sec\b|listing|list on|binance|coinbase|'
                   r'tariff|treasury|powell)\b', re.I)


def scan(cfg: EventConfig = EventConfig(), http: Callable = _get_json,
         now: Optional[float] = None, spot_vol: Optional[Dict[str, Tuple[float, float]]] = None) -> dict:
    t0 = time.time()
    now = time.time() if now is None else now
    events = http(f'{GAMMA}/events?active=true&closed=false&limit={cfg.events_limit}'
                  f'&order=volume24hr&ascending=false') or []
    arbs, markets = [], []
    for ev in events:
        ms = [normalize_market(m, ev) for m in ev.get('markets') or []]
        a = negrisk_arb(ev, ms, cfg)
        if a:
            arbs.append(a)
        markets += [m for m in ms if m and m['active']]
    # crypto-threshold model edges
    needed = {p[0] for p in (parse_threshold_question(m['question']) for m in markets) if p}
    spot_vol = dict(spot_vol or {})
    errors = []
    for sym in needed - set(spot_vol):
        try:
            k = http(f'{FAPI}/fapi/v1/klines?symbol={sym}&interval=1h&limit={cfg.vol_days * 24}')
            closes = [float(r[4]) for r in k]
            sv = realized_vol(closes)
            if sv:
                spot_vol[sym] = (closes[-1], sv)
        except Exception as exc:
            errors.append(f'{sym} vol: {type(exc).__name__}')
    edges = []
    for m in markets:
        p = parse_threshold_question(m['question'])
        if p and p[0] in spot_vol:
            e = threshold_edge(m, *spot_vol[p[0]], now, cfg)
            if e:
                edges.append(e)
    edges.sort(key=lambda e: -e['edge'])
    macro = sorted((m for m in markets if MACRO.search(m['question'] + ' ' + (m['event'] or ''))),
                   key=lambda m: -m['volume24h'])[:cfg.macro_top]
    arbs = sorted(arbs, key=lambda a: -a['edge'])[:10]
    alerts = ([dict(a, kind='ARB') for a in arbs]
              + [dict(e, kind='EDGE') for e in edges if e['flag'] and e['edge'] >= cfg.hot_edge])
    return {'ts': time.time(), 'elapsed_s': time.time() - t0, 'events': len(events),
            'markets': len(markets), 'arbs': arbs, 'alerts': alerts[:12],
            'edges': [e for e in edges if e['flag']][:10], 'edges_all': edges[:25],
            'macro': macro, 'errors': errors, 'config': asdict(cfg),
            'spot_vol': {k: {'spot': v[0], 'vol': v[1]} for k, v in spot_vol.items()}}


def log_predictions(result: dict, path) -> int:
    """Append flagged model quotes for calibration once markets resolve."""
    rows = [{'t': result['ts'], **{k: e[k] for k in ('id', 'question', 'kind', 'strike', 'spot',
                                                      'sigma', 'p_model', 'bid', 'ask', 'side', 'edge')}}
            for e in result.get('edges_all', [])]
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a', encoding='utf-8') as fh:
        for r in rows:
            fh.write(json.dumps(r) + '\n')
    return len(rows)
