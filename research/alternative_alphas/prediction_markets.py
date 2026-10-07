"""Prediction markets (Polymarket, Kalshi): binary mispricing and complement arbitrage.

Fee models (verified 2026-09 from venue docs; re-check before trading):
  Polymarket  taker fee = C x feeRate x p x (1-p); makers pay 0.
              feeRate: crypto .07, sports .05, economics/culture/weather/other .05,
              finance/politics/mentions/tech .04, geopolitics 0.
  Kalshi      taker fee = ceil_cent(0.07 x C x P x (1-P)); maker 0.0175 x C x P x (1-P).

Where the edge is (and is not):
  * Binary YES/NO on Polymarket share ONE book (NO ask = 1 - YES bid), so a
    YES+NO complement arbitrage is structurally ~0 there.
  * Multi-outcome (negRisk) events: sum of best YES asks < 1 - fees is a
    riskless basket held to resolution (capital locked until then).
  * Model mispricing: fair probability q from System 2 vs ask a. Not riskless;
    requires a calibrated q (Brier score on resolved markets) before sizing.
Access: Polymarket blocks e.g. US/UK/FR/DE/SG/TH/AU (help center list); Kalshi is a
US-regulated venue. VIETNAM: the Ministry of Public Security's cyber-security
department (Cuc A05) classified Polymarket as a gambling platform and blocks it
(reported early 2026); accessing it is treated as a violation of Vietnamese law.
This module is read-only research and must not be used to trade from Vietnam.
"""
import json
import math

POLYMARKET_FEE_RATE = {'crypto': .07, 'sports': .05, 'economics': .05, 'culture': .05,
                       'weather': .05, 'other': .05, 'finance': .04, 'politics': .04,
                       'mentions': .04, 'tech': .04, 'geopolitics': 0.0}
KALSHI_TAKER, KALSHI_MAKER = .07, .0175
GAMMA = 'https://gamma-api.polymarket.com'
CLOB = 'https://clob.polymarket.com'


def _check_price(p):
    if not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 < p < 1:
        raise ValueError('price must be a probability in (0, 1)')


def polymarket_fee(shares, price, category='other', maker=False):
    _check_price(price)
    if shares < 0:
        raise ValueError('shares must be non-negative')
    if maker:
        return 0.0
    rate = POLYMARKET_FEE_RATE.get(str(category).lower(), POLYMARKET_FEE_RATE['other'])
    return shares * rate * price * (1 - price)


def kalshi_fee(contracts, price, maker=False):
    _check_price(price)
    if contracts < 0:
        raise ValueError('contracts must be non-negative')
    raw = (KALSHI_MAKER if maker else KALSHI_TAKER) * contracts * price * (1 - price)
    return math.ceil(round(raw * 100, 9)) / 100


def basket_arbitrage(asks, fee_fn, shares=100.0):
    """Buy one share of EVERY outcome at its ask; exactly one pays 1.

    Returns dict(gross, fees, net, cost, net_bps, gross_bps, cost_bps, ratio, executable)."""
    if len(asks) < 2:
        raise ValueError('need at least two mutually exclusive outcomes')
    for a in asks:
        _check_price(a)
    outlay = shares * sum(asks)
    fees = sum(fee_fn(shares, a) for a in asks)
    gross = shares - outlay
    net = gross - fees
    capital = outlay + fees
    gross_bps = gross / capital * 1e4
    cost_bps = fees / capital * 1e4
    ratio = gross / fees if fees > 0 else (math.inf if gross > 0 else 0.0)
    return {'gross': gross, 'fees': fees, 'net': net, 'capital': capital,
            'gross_bps': gross_bps, 'cost_bps': cost_bps, 'net_bps': net / capital * 1e4,
            'ratio': ratio, 'executable': net > 0 and ratio > 3}


def model_mispricing(fair_prob, ask, fee_fn, shares=100.0):
    """Expected value of buying YES at `ask` when System 2 says P(YES) = fair_prob."""
    _check_price(fair_prob)
    _check_price(ask)
    fee = fee_fn(shares, ask)
    capital = shares * ask + fee
    ev = shares * (fair_prob - ask) - fee
    gross_bps = shares * (fair_prob - ask) / capital * 1e4
    cost_bps = fee / capital * 1e4
    ratio = (shares * (fair_prob - ask)) / fee if fee > 0 else (math.inf if ev > 0 else 0.0)
    return {'ev': ev, 'gross_bps': gross_bps, 'cost_bps': cost_bps, 'net_bps': ev / capital * 1e4,
            'ratio': ratio, 'executable': ev > 0 and ratio > 3,
            'note': 'model risk: requires calibrated fair_prob (Brier on resolved markets)'}


def scan_polymarket_events(fetch, limit=50, category_of=lambda e: 'other'):
    """Read-only: multi-outcome events whose best YES asks sum below 1 after fees."""
    events = fetch(f'{GAMMA}/events?active=true&closed=false&limit={int(limit)}')
    found = []
    for e in events:
        markets = [m for m in e.get('markets', []) if m.get('active') and not m.get('closed')]
        if len(markets) < 2 or not e.get('negRisk'):
            continue
        asks = []
        for m in markets:
            try:
                a = float(m.get('bestAsk'))
            except (TypeError, ValueError):
                asks = []
                break
            if not 0 < a < 1:
                asks = []
                break
            asks.append(a)
        if not asks:
            continue
        cat = category_of(e)
        res = basket_arbitrage(asks, lambda c, p: polymarket_fee(c, p, cat))
        res.update(event=e.get('title') or e.get('slug'), outcomes=len(asks), sum_asks=sum(asks))
        found.append(res)
    found.sort(key=lambda r: -r['net_bps'])
    return found


if __name__ == '__main__':
    import urllib.request

    def fetch(url):
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'groktopus'}), timeout=15) as r:
            return json.loads(r.read())
    for r in scan_polymarket_events(fetch)[:10]:
        print(f"{r['net_bps']:+8.1f} bps  sum={r['sum_asks']:.4f}  n={r['outcomes']:2d}  "
              f"{'EXEC' if r['executable'] else '----'}  {r['event']}")
