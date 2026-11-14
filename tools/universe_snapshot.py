"""Live snapshot of the liquid USD-M perpetual universe (2026-11-14 e).  Public REST only, no keys, no orders.

    python tools/universe_snapshot.py --top 40 --out data/universe_snapshot_top40.json

USDT perpetuals in TRADING status, 24 h quote volume > --min-qv, ATR14(H1) / quoted spread > --min-ratio, the --top by
volume.  The spread is the bookTicker touch at the instant of the call: one sample, not a history.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.universe_months import FAPI, get  # noqa: E402


def atr_bps(klines: list, n: int = 14):
    """ATR of the last n CLOSED bars as bps of the last close (klines: Binance rows, the last one still open)."""
    rows = [(float(r[2]), float(r[3]), float(r[4])) for r in klines[:-1]]
    if len(rows) < n + 1:
        return None
    tr = [max(h - l, abs(h - rows[i - 1][2]), abs(l - rows[i - 1][2])) for i, (h, l, _) in enumerate(rows) if i > 0][-n:]
    return sum(tr) / n / rows[-1][2] * 1e4 if rows[-1][2] > 0 else None


def pick(rows: list, top: int = 40, min_qv: float = 50e6, min_ratio: float = 15.0) -> tuple:
    """rows: [{'symbol', 'qv', 'spread_bps', 'atr_bps'}] -> (picked by qv desc, {reason: count})."""
    why, ok = {'volume': 0, 'unknown': 0, 'atr_spread': 0, 'below_top': 0}, []
    for r in rows:
        if r.get('qv') is None or r['qv'] <= min_qv:
            why['volume'] += 1
        elif r.get('atr_bps') is None or not r.get('spread_bps') or r['spread_bps'] <= 0:
            why['unknown'] += 1                                  # no ratio without both: never assumed to pass
        elif r['atr_bps'] / r['spread_bps'] <= min_ratio:
            why['atr_spread'] += 1
        else:
            ok.append({**r, 'ratio': r['atr_bps'] / r['spread_bps']})
    ok.sort(key=lambda r: -r['qv'])
    why['below_top'] = max(0, len(ok) - top)
    return ok[:top], why


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--top', type=int, default=40)
    ap.add_argument('--min-qv', type=float, default=50e6)
    ap.add_argument('--min-ratio', type=float, default=15.0)
    ap.add_argument('--out', type=Path, default=ROOT / 'data' / 'universe_snapshot_top40.json')
    a = ap.parse_args(argv)
    info = get(FAPI + '/fapi/v1/exchangeInfo')
    perps = {s['symbol'] for s in info['symbols'] if s.get('quoteAsset') == 'USDT' and s.get('contractType') == 'PERPETUAL'
             and s.get('status') == 'TRADING' and s.get('underlyingType') == 'COIN' and s['symbol'].isascii()}
    vols = {t['symbol']: float(t.get('quoteVolume') or 0) for t in get(FAPI + '/fapi/v1/ticker/24hr')}
    books = {b['symbol']: b for b in get(FAPI + '/fapi/v1/ticker/bookTicker')}
    rows = []
    for s in sorted(perps, key=lambda s: -vols.get(s, 0.0)):
        qv = vols.get(s)
        b = books.get(s, {})
        bid, ask = float(b.get('bidPrice') or 0), float(b.get('askPrice') or 0)
        row = {'symbol': s, 'qv': qv, 'spread_bps': (ask - bid) / ((ask + bid) / 2) * 1e4 if bid > 0 and ask > 0 else None,
               'atr_bps': None}
        if qv is not None and qv > a.min_qv:                     # 1 h klines only for names that pass the volume bar
            row['atr_bps'] = atr_bps(get(f'{FAPI}/fapi/v1/klines?symbol={s}&interval=1h&limit=16'))
            time.sleep(0.1)
        rows.append(row)
    picked, why = pick(rows, a.top, a.min_qv, a.min_ratio)
    a.out.write_text(json.dumps({'_rule': f'USDT perps TRADING, 24 h quote volume > {a.min_qv:g}, ATR14(H1) / touch spread > '
                                          f'{a.min_ratio:g}, top {a.top} by volume',
                                 '_generated': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                                 'listed': len(perps), 'dropped': why, 'picked': picked}, indent=1))
    print(f'listed {len(perps)}, dropped {why}, picked {len(picked)}')
    for r in picked:
        print(f"  {r['symbol']:16s} qv {r['qv'] / 1e6:9.0f} M  spread {r['spread_bps']:6.2f} bps  ATR(H1) {r['atr_bps']:6.1f} bps  ratio {r['ratio']:7.1f}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
