"""Cross-venue funding carry scanner + SHADOW rotation book (Binance USD-M 8 h vs Hyperliquid 1 h), 10-16.

Delta-neutral pair per coin: short the venue whose funding is higher, long the other, equal notional N per leg.
    spread_8h_bps = (f_short - f_long) per 8 h, Hyperliquid 1 h rate x 8 (Binance rates normalised to 8 h)
    basis_skew_bps = (P_short - P_long) / mid x 1e4   (> 0: the short leg is sold richer than the long leg is bought)
Entry      spread_8h_bps >= min_spread_bps (2.5) and basis_skew_bps >= min_basis_bps (+1.5); a missing rate or
           price is UNKNOWN -> the coin is skipped with a reason, never treated as zero.
Rotation   a held coin is swapped only when candidate spread - held spread >= rotate_delta_bps (4.0).
Exit       held spread <= exit_spread_bps (0: the pair now pays).

Evidence: the thresholds above are the request's (RFC 2026-Q4) and have NOT been replayed.  The pre-registered
replay (tools/funding_spread_replay.py, 10-10 f) used a different rule (72 h trailing spread >= 2 x round-trip
cost, 28 d hold) and measured HOLDOUT +14.6 bps per 28 d hold per unit notional [+9.2, +19.9], n 153.
SHADOW only: this module builds intents and a virtual book; it has no venue client and refuses any other mode.
core/funding_carry.two_leg_capital_gate still blocks live carry on the 3.85 USDT book.

    python -m core.funding_executor --capital 4919.1996        # monthly yield scenarios
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

DAYS_PER_MONTH = 365.25 / 12
# HEDGE cost of the replay (one leg maker, one taker + 1 bp slip, at open and at close), bps per unit notional:
# min(BN maker 2 + HL taker 4.5, HL maker 1.5 + BN taker 5) + 1 = 7.5 per side -> 15 round trip.
HEDGE_ROUND_TRIP_BPS = 15.0
REPLAY_NET_BPS_PER_HOLD = (14.6, 9.2, 19.9)     # HOLDOUT mean, CI95 low, high (10-10 f), per 28 d hold
REPLAY_HOLD_DAYS = 28.0


@dataclass(frozen=True)
class FundingQuote:
    coin: str
    bn_rate_8h: Optional[float]     # fraction per 8 h (0.0001 = 1 bp)
    hl_rate_1h: Optional[float]     # fraction per 1 h
    bn_mark: Optional[float]
    hl_mark: Optional[float]


@dataclass(frozen=True)
class CarryCandidate:
    coin: str
    short_venue: str                # 'BN' | 'HL'
    long_venue: str
    spread_8h_bps: float
    basis_skew_bps: float


def evaluate(q: FundingQuote) -> Tuple[Optional[CarryCandidate], str]:
    """-> (candidate, '') or (None, reason).  Direction is always the one that earns the spread."""
    if None in (q.bn_rate_8h, q.hl_rate_1h, q.bn_mark, q.hl_mark):
        return None, 'UNKNOWN rate/price'
    if q.bn_mark <= 0 or q.hl_mark <= 0:
        return None, 'UNKNOWN price'
    bn, hl = float(q.bn_rate_8h) * 1e4, 8.0 * float(q.hl_rate_1h) * 1e4
    short, long_ = ('HL', 'BN') if hl >= bn else ('BN', 'HL')
    p_short, p_long = (q.hl_mark, q.bn_mark) if short == 'HL' else (q.bn_mark, q.hl_mark)
    mid = 0.5 * (p_short + p_long)
    return CarryCandidate(q.coin, short, long_, abs(hl - bn), (p_short - p_long) / mid * 1e4), ''


def scan(quotes: Iterable[FundingQuote], min_spread_bps: float = 2.5,
         min_basis_bps: float = 1.5) -> Tuple[List[CarryCandidate], Dict[str, str]]:
    """Eligible candidates by spread (desc) and {coin: reason} for every coin left out."""
    ok, why = [], {}
    for q in quotes:
        c, r = evaluate(q)
        if c is None:
            why[q.coin] = r
        elif c.spread_8h_bps < min_spread_bps:
            why[q.coin] = f'spread {c.spread_8h_bps:.2f} < {min_spread_bps:g} bps/8h'
        elif c.basis_skew_bps < min_basis_bps:
            why[q.coin] = f'basis {c.basis_skew_bps:+.2f} < {min_basis_bps:+g} bps'
        else:
            ok.append(c)
    ok.sort(key=lambda c: c.spread_8h_bps, reverse=True)
    return ok, why


class ShadowCarryBook:
    """Virtual book of at most `slots` pairs; step() returns intents (OPEN / CLOSE / ROTATE), never sends one."""

    def __init__(self, capital_usdt: float, slots: int = 3, leverage: float = 1.0, utilization: float = 0.7,
                 min_spread_bps: float = 2.5, min_basis_bps: float = 1.5, rotate_delta_bps: float = 4.0,
                 exit_spread_bps: float = 0.0, mode: str = 'shadow') -> None:
        if mode != 'shadow':
            raise ValueError('funding executor is shadow-only (live carry is blocked by the two-leg capital gate)')
        self.capital, self.slots = float(capital_usdt), max(int(slots), 1)
        self.leverage, self.utilization = float(leverage), float(utilization)
        self.min_spread, self.min_basis = float(min_spread_bps), float(min_basis_bps)
        self.rotate_delta, self.exit_spread = float(rotate_delta_bps), float(exit_spread_bps)
        self.held: Dict[str, CarryCandidate] = {}

    @property
    def notional_per_leg(self) -> float:
        """Margin on both venues = 2 N / L per pair, sized so all slots use `utilization` of the capital."""
        return self.capital * self.utilization * self.leverage / (2.0 * self.slots)

    def _intent(self, kind: str, c: CarryCandidate, why: str) -> dict:
        return {'kind': kind, 'coin': c.coin, 'short': c.short_venue, 'long': c.long_venue,
                'notional_per_leg': round(self.notional_per_leg, 4), 'spread_8h_bps': round(c.spread_8h_bps, 3),
                'basis_skew_bps': round(c.basis_skew_bps, 3), 'why': why, 'mode': 'shadow'}

    def step(self, quotes: Iterable[FundingQuote]) -> List[dict]:
        quotes = list(quotes)
        now = {q.coin: evaluate(q)[0] for q in quotes}
        out = []
        for coin, h in list(self.held.items()):             # exits: pair flipped / now pays (UNKNOWN -> keep)
            c = now.get(coin)
            if c is not None and (c.short_venue != h.short_venue or c.spread_8h_bps <= self.exit_spread):
                out.append(self._intent('CLOSE', h, 'spread flipped' if c.short_venue != h.short_venue
                                        else f'spread {c.spread_8h_bps:.2f} <= {self.exit_spread:g}'))
                del self.held[coin]
            elif c is not None:
                self.held[coin] = c
        cands, _ = scan(quotes, self.min_spread, self.min_basis)
        for c in (x for x in cands if x.coin not in self.held):
            if len(self.held) < self.slots:
                self.held[c.coin] = c
                out.append(self._intent('OPEN', c, 'entry criteria met'))
                continue
            worst = min(self.held.values(), key=lambda h: h.spread_8h_bps)
            if c.spread_8h_bps - worst.spread_8h_bps >= self.rotate_delta:
                del self.held[worst.coin]
                self.held[c.coin] = c
                gain = c.spread_8h_bps - worst.spread_8h_bps
                out.append(dict(self._intent('ROTATE', c, f'replaces {worst.coin} (+{gain:.2f} bps)'),
                                replaces=worst.coin))
            else:
                break                                       # sorted: no later candidate can clear the hysteresis
        return out


def monthly_yield_usdt(capital_usdt: float, net_bps_per_month: float, leverage: float = 1.0,
                       utilization: float = 1.0) -> float:
    """Capital-weighted: N per leg = capital x utilization x L / 2, yield = N x net bps / 1e4."""
    return capital_usdt * utilization * leverage / 2.0 * net_bps_per_month * 1e-4


def scenarios(capital_usdt: float, leverage: float = 1.0, utilization: float = 1.0) -> dict:
    """Zero-direction monthly yield on `capital_usdt`: (a) replay-measured edge, (b) the RFC entry threshold held
    unchanged for a month (an upper bound the replay does not support: spreads decay after entry)."""
    per_month = DAYS_PER_MONTH / REPLAY_HOLD_DAYS
    replay = {k: monthly_yield_usdt(capital_usdt, b * per_month, leverage, utilization)
              for k, b in zip(('mean', 'ci_low', 'ci_high'), REPLAY_NET_BPS_PER_HOLD)}
    thr_bps = 2.5 * 3 * DAYS_PER_MONTH - HEDGE_ROUND_TRIP_BPS * per_month
    return {'capital_usdt': capital_usdt, 'leverage': leverage, 'utilization': utilization,
            'notional_per_leg': capital_usdt * utilization * leverage / 2.0,
            'replay_holdout_usdt_per_month': {k: round(v, 2) for k, v in replay.items()},
            'replay_pct_per_month': round(replay['mean'] / capital_usdt * 100, 3),
            'threshold_persistent_usdt_per_month': round(monthly_yield_usdt(capital_usdt, thr_bps, leverage,
                                                                            utilization), 2),
            'threshold_persistent_net_bps_per_month': round(thr_bps, 1)}


# ------------------------------------------------------------------ 10-16 (b) live shadow telemetry
CARRY_COINS = ('BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'BNB', 'ADA', 'SUI', 'LINK', 'AVAX', 'ENA', 'AAVE', 'LTC', 'HYPE',
               'WLD', 'TAO', 'ZEC', '1000PEPE')            # = tools/funding_spread_replay.COINS
HL_NAME = {'1000PEPE': 'kPEPE'}
BN_PREMIUM_URL = 'https://fapi.binance.com/fapi/v1/premiumIndex'
HL_INFO_URL = 'https://api.hyperliquid.xyz/info'


def _f(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def quotes_from(bn_premium: list, hl_meta: list, coins: Iterable[str] = CARRY_COINS) -> List[FundingQuote]:
    """Binance GET premiumIndex (all symbols) + Hyperliquid POST {"type": "metaAndAssetCtxs"} -> quotes.
    A coin missing on a venue keeps None fields (evaluate() reports it UNKNOWN)."""
    bn = {x.get('symbol'): x for x in (bn_premium or []) if isinstance(x, dict)}
    hl = {}
    if isinstance(hl_meta, list) and len(hl_meta) == 2:
        for u, ctx in zip((hl_meta[0] or {}).get('universe', []), hl_meta[1] or []):
            hl[u.get('name')] = ctx
    out = []
    for c in coins:
        b, h = bn.get(f'{c}USDT') or {}, hl.get(HL_NAME.get(c, c)) or {}
        out.append(FundingQuote(c, _f(b.get('lastFundingRate')), _f(h.get('funding')), _f(b.get('markPrice')),
                                _f(h.get('markPx'))))
    return out


def fetch_quotes(coins: Iterable[str] = CARRY_COINS, timeout: float = 10.0, opener=None) -> List[FundingQuote]:
    """Two public read-only calls (blocking: run it in a thread).  Raises on network / parse errors."""
    op = opener or urllib.request.urlopen
    with op(urllib.request.Request(BN_PREMIUM_URL, headers={'User-Agent': 'groktopus-shadow'}), timeout=timeout) as r:
        bn = json.load(r)
    req = urllib.request.Request(HL_INFO_URL, data=json.dumps({'type': 'metaAndAssetCtxs'}).encode(),
                                 headers={'Content-Type': 'application/json', 'User-Agent': 'groktopus-shadow'})
    with op(req, timeout=timeout) as r:
        hl = json.load(r)
    return quotes_from(bn, hl, coins)


def carry_view(book: 'ShadowCarryBook', quotes: Iterable[FundingQuote], intents: List[dict], now: float,
               top: int = 6) -> dict:
    """Telemetry snapshot: best spreads (eligible or not) with APR = spread x 3 x 365, the virtual book, why-not."""
    quotes = list(quotes)
    cands = sorted((c for c in (evaluate(q)[0] for q in quotes) if c), key=lambda c: c.spread_8h_bps, reverse=True)
    _, why = scan(quotes, book.min_spread, book.min_basis)
    return {'t': now, 'mode': 'shadow', 'rule': {'min_spread_bps_8h': book.min_spread, 'min_basis_bps': book.min_basis},
            'notional_per_leg': round(book.notional_per_leg, 2),
            'top': [{'coin': c.coin, 'short': c.short_venue, 'long': c.long_venue,
                     'spread_8h_bps': round(c.spread_8h_bps, 3), 'apr_pct': round(c.spread_8h_bps * 3 * 365 / 100, 2),
                     'basis_bps': round(c.basis_skew_bps, 2), 'eligible': c.coin not in why}
                    for c in cands[:top]],
            'held': sorted(book.held), 'intents': intents[-6:],
            'unknown': sorted(k for k, v in why.items() if v.startswith('UNKNOWN'))}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--capital', type=float, default=4919.1996)
    ap.add_argument('--leverage', type=float, default=1.0)
    ap.add_argument('--utilization', type=float, default=1.0)
    a = ap.parse_args(argv)
    print(json.dumps(scenarios(a.capital, a.leverage, a.utilization), indent=2))


if __name__ == '__main__':
    main()
