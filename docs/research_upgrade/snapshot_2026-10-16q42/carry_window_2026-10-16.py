"""RFC-2026-Q4.3 Task 3.2: funding carry accrued on 2026-10-16 13:54-16:18 UTC+7 (06:54-09:18 UTC) from real prints.
Public read-only endpoints (Binance fapi fundingRate / klines, Hyperliquid info fundingHistory / candleSnapshot).
Rule per coin = core.funding_executor.evaluate on the rates known at 06:54 UTC (last BN 8 h print, last HL 1 h print)
and the 1 m marks at 06:54; accrual = BN settlement(s) + HL hourly prints inside the window, signed for that direction.
    python docs/research_upgrade/snapshot_2026-10-16q42/carry_window_2026-10-16.py"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from core.funding_executor import HEDGE_ROUND_TRIP_BPS, FundingQuote, ShadowCarryBook, evaluate, scan  # noqa: E402
from tools.funding_spread_replay import COINS, HL_NAME, _get, bn_funding, hl_funding  # noqa: E402

A = int(datetime(2026, 10, 16, 6, 54, tzinfo=timezone.utc).timestamp() * 1000)
B = int(datetime(2026, 10, 16, 9, 18, tzinfo=timezone.utc).timestamp() * 1000)
CAPITAL = 4919.1996


def bn_mark(sym):
    k = _get(f'https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1m&startTime={A - 60_000}&limit=1')
    return float(k[0][4]) if k else None


def hl_mark(coin):
    k = _get('https://api.hyperliquid.xyz/info', {'type': 'candleSnapshot', 'req': {
        'coin': coin, 'interval': '1m', 'startTime': A - 60_000, 'endTime': A}})
    return float(k[-1]['c']) if k else None


rows, quotes = [], []
for c in COINS:
    sym, hl = f'{c}USDT', HL_NAME.get(c, c)
    bn = bn_funding(sym, A - 9 * 3600_000, B)                     # bps per 8 h, (t_ms, bps)
    hlr = hl_funding(hl, A - 2 * 3600_000, B)                     # bps per 1 h
    bn_prev = [r for t, r in bn if t <= A]
    hl_prev = [r for t, r in hlr if t <= A]
    q = FundingQuote(c, bn_prev[-1] / 1e4 if bn_prev else None, hl_prev[-1] / 1e4 if hl_prev else None,
                     bn_mark(sym), hl_mark(hl))
    quotes.append(q)
    cand, why = evaluate(q)
    bn_in = [r for t, r in bn if A < t <= B]
    hl_in = [r for t, r in hlr if A < t <= B]
    if cand is None:
        rows.append({'coin': c, 'why': why})
        continue
    sgn = 1.0 if cand.short_venue == 'BN' else -1.0               # short BN / long HL earns BN - HL
    acc = sgn * (sum(bn_in) - sum(hl_in))
    rows.append({'coin': c, 'short': cand.short_venue, 'spread_8h_bps': round(cand.spread_8h_bps, 3),
                 'basis_bps': round(cand.basis_skew_bps, 2), 'bn_prints': len(bn_in), 'hl_prints': len(hl_in),
                 'accrued_bps': round(acc, 3)})

ok, why = scan(quotes)
book = ShadowCarryBook(CAPITAL)
intents = book.step(quotes)
n = book.notional_per_leg
held = [r for r in rows if r['coin'] in book.held]
gross_usdt = sum(r['accrued_bps'] for r in held) / 1e4 * n
print(json.dumps({'window_utc': ['06:54', '09:18'], 'rows': rows, 'rfc_rule_selected': [i['coin'] for i in intents],
                  'excluded': why, 'notional_per_leg_usdt': round(n, 2),
                  'book_gross_accrued_usdt': round(gross_usdt, 4),
                  'book_round_trip_cost_usdt': round(len(held) * HEDGE_ROUND_TRIP_BPS / 1e4 * n, 4),
                  'all_coins_best_accrued_bps': max((r.get('accrued_bps', -1e9) for r in rows), default=None)},
                 indent=1))
