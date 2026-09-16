"""T5: Kiem dinh microprice/OFI tren du lieu L2 that da ghi (BTC/SOL/BNB).

Cau hoi: tin hieu L2 (order-flow imbalance, microprice gap) du doan gia
30/60/180s truoc khong? Neu co, do markout thuc thi (mua ask, ban bid)
co vuot phi 6 bps khong?

Phuong phap:
- Stream bookTicker (97.7% dong), bo depth. Xay snapshot 1 giay (last tick/giay).
- S1: OFI_10s = sum(dBqty - dAqty, 10s), z-score rolling 5 phut.
- S2: microgap = (microprice - mid)/spread.
- Markout executable: LONG = bid(t+h)/ask(t) - 1 (mua ask, ban bid).
- Chia theo decile tin hieu; train 70% dau (theo thoi gian), holdout 30% cuoi.
GO: decile extrema holdout co |mean markout| > 6 bps va monotic giong train.

Chi doc file local, khong mang, khong lenh giao dich.
"""
import json
import statistics as st
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HORIZONS = (30, 60, 180)
Z_WIN = 300
OFI_WIN = 10
TRAIN_FRAC = 0.7


def stream_snapshots(path):
    snaps = {}
    last = None
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            if '"bookTicker"' not in line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue  # dong cut/gay khi recorder bi gian doan
            try:
                lt = d['lt']
                bid, ask = float(d['b']), float(d['a'])
                bqty, aqty = float(d['B']), float(d['A'])
            except (KeyError, ValueError):
                continue
            if bid <= 0 or ask <= 0 or bid >= ask:
                continue
            last = (bid, ask, bqty, aqty)
            snaps[int(lt)] = last
    return sorted((sec, *v) for sec, v in snaps.items())


def build_series(snaps):
    ts = [s[0] for s in snaps]
    bid = [s[1] for s in snaps]
    ask = [s[2] for s in snaps]
    bqty = [s[3] for s in snaps]
    aqty = [s[4] for s in snaps]
    mid = [(b + a) / 2 for b, a in zip(bid, ask)]
    micro = [(b * aq + a * bq) / (bq + aq) if (bq + aq) > 0 else m
             for b, a, bq, aq, m in zip(bid, ask, bqty, aqty, mid)]
    spread_bps = [(a - b) / m * 1e4 if m > 0 else 0 for a, b, m in zip(ask, bid, mid)]
    ofi1 = [0.0]
    for i in range(1, len(snaps)):
        ofi1.append((bqty[i] - bqty[i - 1]) - (aqty[i] - aqty[i - 1]))
    return ts, bid, ask, mid, micro, spread_bps, ofi1


def zscore_series(vals, win=Z_WIN):
    out = [0.0] * len(vals)
    for i in range(len(vals)):
        lo = max(0, i - win + 1)
        w = vals[lo:i + 1]
        m = st.fmean(w)
        sd = st.pstdev(w) if len(w) > 1 else 1.0
        out[i] = (vals[i] - m) / sd if sd > 1e-12 else 0.0
    return out


def rolling_sum(vals, win=OFI_WIN):
    out = []
    acc = 0.0
    for i, v in enumerate(vals):
        acc += v
        if i >= win:
            acc -= vals[i - win]
        out.append(acc)
    return out


def decile_table(sig, fwd, lo_i, hi_i, nb=10):
    pairs = [(sig[i], fwd[i]) for i in range(lo_i, hi_i)
             if sig[i] is not None and fwd[i] is not None]
    if len(pairs) < nb * 10:
        return None
    pairs.sort()
    n = len(pairs)
    rows = []
    for d in range(nb):
        seg = pairs[d * n // nb:(d + 1) * n // nb]
        vals = [p[1] for p in seg]
        rows.append((d, len(vals), st.fmean(vals), st.median(vals)))
    return rows


def markout_fwd(ask, bid, h):
    n = len(ask)
    fwd = [None] * n
    for i in range(n - h):
        if ask[i] > 0 and bid[i + h] > 0:
            fwd[i] = (bid[i + h] / ask[i] - 1) * 1e4
    return fwd


def verdict_for(symbol, snaps):
    ts, bid, ask, mid, micro, spread_bps, ofi1 = build_series(snaps)
    n = len(snaps)
    if n < 600:
        print(f'{symbol}: chi {n}s snapshot — khong du (<10 phut). SKIP.')
        return
    ofi_z = zscore_series(rolling_sum(ofi1, OFI_WIN), Z_WIN)
    microgap = [(mi - m) / sp if sp > 1e-9 else 0.0
                for mi, m, sp in zip(micro, mid, spread_bps)]
    print(f'\n=== {symbol}: {n}s snapshot ({(ts[-1]-ts[0])/3600:.2f}h), '
          f'spread TB {st.fmean(spread_bps):.2f} bps ===')
    split = int(n * TRAIN_FRAC)
    best = (0.0, '', 0)
    for name, sig in (('OFI_10s_z', ofi_z), ('MICROGAP', microgap)):
        for h in HORIZONS:
            fwd = markout_fwd(ask, bid, h)
            tr_rows = decile_table(sig, fwd, 0, split)
            ho_rows = decile_table(sig, fwd, split, n)
            if tr_rows is None or ho_rows is None:
                print(f'  {name} h={h}s: khong du mau')
                continue
            tr_gap = tr_rows[-1][2] - tr_rows[0][2]
            ho_gap = ho_rows[-1][2] - ho_rows[0][2]
            mono = (tr_rows[0][2] < tr_rows[len(tr_rows)//2][2] < tr_rows[-1][2]
                    or tr_rows[0][2] > tr_rows[len(tr_rows)//2][2] > tr_rows[-1][2])
            print(f'  {name} h={h:>3}s | train {tr_gap:+7.2f}bps '
                  f'(low {tr_rows[0][2]:+6.2f} hi {tr_rows[-1][2]:+6.2f}) '
                  f'| holdout {ho_gap:+7.2f}bps (low {ho_rows[0][2]:+6.2f} hi {ho_rows[-1][2]:+6.2f}) '
                  f'| mono {"Y" if mono else "N"} | bar6: {"VUOT" if abs(ho_gap) > 6 else "khong"}')
            if abs(ho_gap) > best[0]:
                best = (abs(ho_gap), name, h)
    if best[0] > 0:
        print(f'  >>> BEST holdout: {best[1]} h={best[2]}s gap {best[0]:.2f} bps '
              f'{"— VUOT 6bps (GO nghien cuu)" if best[0] > 6 else "— KHONG vuot 6bps (NO-GO)"}')


def main():
    files = sys.argv[1:] or [
        str(ROOT / 'data' / 'l2' / 'BTCUSDT' / '20260908.jsonl'),
        str(ROOT / 'data' / 'l2' / 'SOLUSDT' / '20260908.jsonl'),
        str(ROOT / 'data' / 'l2' / 'BNBUSDT' / '20260908.jsonl'),
    ]
    for path in files:
        p = Path(path)
        if not p.exists():
            print(f'SKIP: {path}')
            continue
        print(f'\nDoc {p.parent.name}/{p.name} ({p.stat().st_size/1e6:.0f} MB)...')
        t0 = time.time()
        snaps = stream_snapshots(p)
        print(f'  {len(snaps)} snapshot giay trong {time.time()-t0:.0f}s')
        verdict_for(p.parent.name, snaps)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())