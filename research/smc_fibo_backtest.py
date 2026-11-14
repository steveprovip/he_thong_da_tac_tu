"""Pre-registered SMC-Fibo study on Binance USD-M 1m klines (offline: no orders, no network).

Spec, variants and acceptance are frozen in docs/harness/plan_alpha_reengineering_2026_11_13.md section B.
    python -m research.smc_fibo_backtest                    # 11 symbols, TRAIN 2024-12..2025-12, HOLDOUT 2026-01..09
    python -m research.smc_fibo_backtest --symbols BTCUSDT --out data/smc_fibo_btc.json
Setups come from core/smc_fibo.py (the live code path). Every 1 m bar is simulated conservatively: entry = maker
limit filled only when a low trades >= 1 tick through it within 15 min (cancelled if TP trades first or both in one
bar); SL = stop-market at the level or the gapped open, taker 5 bps + 1 bp slip; TP = maker, filled when a high
trades >= 1 tick through; SL and TP in one bar -> SL; the fill bar can stop out but cannot take profit; time exit
after 12 h at the close (taker + slip). Placebo: same symbol / side / distances at random 5 m closes +- 15 days.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import random
import time
import zipfile
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from core.position_sizer import fractional_kelly_risk
from core.smc_fibo import Bar, SMCFibo

ROOT = Path(__file__).resolve().parents[1]
KLINES = ROOT / 'data' / 'klines_1m'
SYMBOLS = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'ADAUSDT', 'LINKUSDT', 'SUIUSDT',
           'ENAUSDT', '1000PEPEUSDT')
TRAIN = ('2024-12-01', '2026-01-01')
HOLDOUT = ('2026-01-01', '2026-10-01')
MAKER_BPS, TAKER_BPS, SLIP_BPS = 2.0, 5.0, 1.0
BE_LOCK_BPS = MAKER_BPS + TAKER_BPS + SLIP_BPS            # fee-shield break-even: the stop exit nets ~0
FILL_BARS, HOLD_BARS = 15, 720
VARIANTS = tuple((r, be) for r in (1.5, 2.0) for be in (False, True))
N_PLACEBO, PLACEBO_DAYS = 5, 15
MICRO = dict(equity=5.0, risk_usdt=0.05, min_notional=5.05, max_pos=3, daily_kill=0.15, session_losses=2)


def ms(day: str) -> int:
    return int(dt.datetime.fromisoformat(day).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


# ----------------------------------------------------------------------------------------------------- data
def load_1m(symbol: str, root: Path = KLINES) -> dict:
    files = sorted(root.glob(f'{symbol}-1m-*.zip'))
    frames = []
    for p in files:
        with zipfile.ZipFile(p) as z:
            name = z.namelist()[0]
            head = z.open(name).readline()
            df = pd.read_csv(z.open(name), header=0 if not head[:1].isdigit() else None, usecols=[0, 1, 2, 3, 4, 5, 9])
        df.columns = ['t', 'o', 'h', 'l', 'c', 'v', 'tb']
        frames.append(df)
    if not frames:
        raise FileNotFoundError(f'no 1m klines for {symbol} in {root}')
    df = pd.concat(frames, ignore_index=True).drop_duplicates('t').sort_values('t').reset_index(drop=True)
    out = {k: df[k].to_numpy(np.int64 if k == 't' else np.float64) for k in df.columns}
    out['files'] = [p.name for p in files]
    return out


def resample(m1: dict, minutes: int) -> dict:
    """Bars of `minutes` from 1 m bars; t = CLOSE time (bucket open + minutes). Buckets with any data are kept."""
    span = minutes * 60_000
    b = m1['t'] // span
    starts = np.flatnonzero(np.r_[True, b[1:] != b[:-1]])
    ends = np.r_[starts[1:], len(b)]
    return {'t': b[starts] * span + span, 'o': m1['o'][starts], 'h': np.maximum.reduceat(m1['h'], starts),
            'l': np.minimum.reduceat(m1['l'], starts), 'c': m1['c'][ends - 1],
            'v': np.add.reduceat(m1['v'], starts), 'tb': np.add.reduceat(m1['tb'], starts)}


def tick_of(prices: np.ndarray) -> float:
    u = np.unique(np.round(prices[: 50_000], 10))
    d = np.diff(u)
    d = d[d > 0]
    return float(np.round(d.min(), 10)) if len(d) else 0.0


def detect(m1: dict, det: SMCFibo) -> list:
    """Feed H4, H1 then 5 m bars in close-time order; return the setups."""
    m5, h1, h4 = resample(m1, 5), resample(m1, 60), resample(m1, 240)
    i1 = i4 = 0
    out = []
    t5 = m5['t']
    for i in range(len(t5)):
        t = t5[i]
        while i4 < len(h4['t']) and h4['t'][i4] <= t:
            det.on_h4(Bar(int(h4['t'][i4]), h4['o'][i4], h4['h'][i4], h4['l'][i4], h4['c'][i4], h4['v'][i4], h4['tb'][i4]))
            i4 += 1
        while i1 < len(h1['t']) and h1['t'][i1] <= t:
            det.on_h1(Bar(int(h1['t'][i1]), h1['o'][i1], h1['h'][i1], h1['l'][i1], h1['c'][i1], h1['v'][i1], h1['tb'][i1]))
            i1 += 1
        s = det.on_5m(Bar(int(t), m5['o'][i], m5['h'][i], m5['l'][i], m5['c'][i], m5['v'][i], m5['tb'][i]))
        if s is not None:
            out.append(s)
    return out


# ----------------------------------------------------------------------------------------------- simulation
def simulate(m1: dict, t_signal: int, side: int, entry: float, sl: float, tp: float, tick: float, be: bool,
             fill_bars: int = FILL_BARS, hold_bars: int = HOLD_BARS) -> dict:
    """Conservative 1 m path of one order (see the module docstring). Prices are mirrored for shorts."""
    t = m1['t']
    i0 = int(np.searchsorted(t, t_signal, 'left'))        # first 1 m bar opening at / after the signal close
    if i0 >= len(t):
        return {'filled': False, 'why': 'NO_DATA'}
    s = 1.0 if side > 0 else -1.0
    o, h, l, c = (m1['o'], m1['h'], m1['l'], m1['c']) if s > 0 else (-m1['o'], -m1['l'], -m1['h'], -m1['c'])
    e, stop, take = s * entry, s * sl, s * tp
    risk = e - stop
    if risk <= 0:
        return {'filled': False, 'why': 'BAD_RISK'}
    i1 = min(len(t), i0 + fill_bars)
    through = l[i0:i1] <= e - tick
    tp_seen = h[i0:i1] >= take + tick
    jt = int(np.argmax(through)) if through.any() else None
    jp = int(np.argmax(tp_seen)) if tp_seen.any() else None
    if jt is None or (jp is not None and jp <= jt):
        return {'filled': False, 'why': 'NO_FILL' if jt is None else 'TP_FIRST'}
    j = i0 + jt
    slip = lambda px: px - abs(px) * SLIP_BPS / 1e4
    if l[j] <= stop:                                      # stopped inside the fill bar (cannot take profit there)
        return _close(m1, j, j, s, entry, -1, slip(min(o[j] if o[j] < e else stop, stop)), 'SL', be=False)
    k0, k1 = j + 1, min(len(t), j + 1 + hold_bars)
    if k0 >= k1:
        return {'filled': False, 'why': 'NO_DATA'}
    hh, ll, oo = h[k0:k1], l[k0:k1], o[k0:k1]
    tp_hit = hh >= take + tick
    stop_lvl = np.full(len(hh), stop)
    armed = None
    if be:
        mfe = hh >= e + risk
        if mfe.any():
            a = int(np.argmax(mfe))
            first_tp = int(np.argmax(tp_hit)) if tp_hit.any() else None
            if first_tp is None or first_tp > a:
                stop_lvl[a + 1:] = e + abs(e) * BE_LOCK_BPS / 1e4
                armed = a
    sl_hit = ll <= stop_lvl
    ks = int(np.argmax(sl_hit)) if sl_hit.any() else None
    kt = int(np.argmax(tp_hit)) if tp_hit.any() else None
    if ks is not None and (kt is None or ks <= kt):
        lvl = stop_lvl[ks]
        why = 'BE' if armed is not None and ks > armed else 'SL'
        return _close(m1, j, k0 + ks, s, entry, -1, slip(min(oo[ks], lvl)), why, be=armed is not None)
    if kt is not None:
        return _close(m1, j, k0 + kt, s, entry, +1, take, 'TP', be=armed is not None)
    return _close(m1, j, k1 - 1, s, entry, -1, slip(c[k1 - 1]), 'TIME', be=armed is not None)


def _close(m1, j, k, s, entry, maker_exit, px_m, why, be) -> dict:
    exit_px = s * px_m
    gross = s * (exit_px / entry - 1.0) * 1e4
    fees = MAKER_BPS + (MAKER_BPS if maker_exit > 0 else TAKER_BPS)   # slip is already in the exit price
    return {'filled': True, 'why': why, 't_fill': int(m1['t'][j]), 't_exit': int(m1['t'][k]) + 60_000,
            'exit': exit_px, 'gross_bps': gross, 'net_bps': gross - fees, 'be_armed': be}


# --------------------------------------------------------------------------------------------------- statistics
def stats(net: list, risk_bps: list | None = None) -> dict:
    x = np.asarray(net, dtype=float)
    n = len(x)
    if n == 0:
        return {'n': 0}
    m = float(x.mean())
    se = float(x.std(ddof=1) / math.sqrt(n)) if n > 1 else None
    win, loss = x[x > 0], x[x <= 0]
    out = {'n': n, 'win_rate': round(float((x > 0).mean()), 4), 'mean_net_bps': round(m, 3),
           'se': None if se is None else round(se, 3), 'lcb95': None if se is None else round(m - 1.645 * se, 3),
           'profit_factor': round(float(win.sum() / -loss.sum()), 3) if loss.sum() < 0 else None,
           'sharpe_per_trade': round(m / float(x.std(ddof=1)), 4) if n > 1 and x.std(ddof=1) > 0 else None}
    if risk_bps is not None:
        r = x / np.asarray(risk_bps, dtype=float)
        eq = np.cumsum(r)
        out['mean_R'] = round(float(r.mean()), 4)
        out['mdd_R'] = round(float((np.maximum.accumulate(np.r_[0.0, eq]) - np.r_[0.0, eq]).max()), 3)
    return out


def welch_lcb(a: list, b: list) -> dict:
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 2 or len(b) < 2:
        return {'diff': None, 'lcb95': None}
    d = float(a.mean() - b.mean())
    se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return {'diff': round(d, 3), 'se': round(se, 3), 'lcb95': round(d - 1.645 * se, 3)}


def daily_sharpe(trades: list, start: int, end: int, key: str = 'net_R') -> dict:
    days = defaultdict(float)
    for x in trades:
        days[x['t_exit'] // 86_400_000] += x[key]
    nd = max(1, (end - start) // 86_400_000)
    v = np.zeros(nd)
    for d, s in days.items():
        i = int(d - start // 86_400_000)
        if 0 <= i < nd:
            v[i] += s
    sd = v.std(ddof=1) if nd > 1 else 0.0
    return {'days': nd, 'sharpe_ann': round(float(v.mean() / sd * math.sqrt(365)), 3) if sd > 0 else None}


def micro_portfolio(trades: list, start: int, end: int, kelly: bool = False) -> dict:
    """5 U account, 0.05 U lost at the stop incl. fees, min notional 5.05 U, <= 3 positions, 2 losses in a UTC day
    -> no entry until the next day, 0.15 U daily kill. Trades are taken in fill-time order across symbols."""
    cfg = MICRO
    eq, peak, mdd = cfg['equity'], cfg['equity'], 0.0
    open_until: list = []
    day, day_pnl, streak, skipped = None, 0.0, 0, defaultdict(int)
    wins = losses = 0
    sum_w = sum_l = 0.0
    taken = []
    pending: list = []                                    # (t_exit, net_R) of every strategy trade, taken or not
    for x in sorted(trades, key=lambda r: r['t_fill']):
        d = x['t_fill'] // 86_400_000
        if d != day:
            day, day_pnl, streak = d, 0.0, 0
        open_until = [u for u in open_until if u > x['t_fill']]
        # Kelly evidence = outcomes KNOWN at this instant (closed before this fill), real or virtual, as the engine's
        # edge_shadow path does; a trade never informs its own size
        known = [r for r in pending if r[0] <= x['t_fill']]
        pending = [r for r in pending if r[0] > x['t_fill']]
        for _, nr in known:
            if nr > 0:
                wins, sum_w = wins + 1, sum_w + nr
            else:
                losses, sum_l = losses + 1, sum_l - nr
        pending.append((x['t_exit'], x['net_R']))
        if streak >= cfg['session_losses']:
            skipped['session_stop'] += 1
            continue
        if day_pnl <= -cfg['daily_kill']:
            skipped['daily_kill'] += 1
            continue
        if len(open_until) >= cfg['max_pos']:
            skipped['max_positions'] += 1
            continue
        loss_frac = (x['risk_bps'] + MAKER_BPS + TAKER_BPS + SLIP_BPS) / 1e4
        risk = cfg['risk_usdt']
        if kelly:
            b = (sum_w / wins) / (sum_l / losses) if wins and losses and sum_l > 0 else None
            risk = fractional_kelly_risk(eq, wins, losses, b, cap_usdt=cfg['risk_usdt'])
            if risk <= 0:
                skipped['kelly_zero'] += 1
                continue
        notional = risk / loss_frac
        if notional < cfg['min_notional']:
            skipped['min_notional'] += 1
            continue
        pnl = notional * x['net_bps'] / 1e4
        eq += pnl
        day_pnl += pnl
        streak = streak + 1 if pnl < 0 else 0
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
        open_until.append(x['t_exit'])
        taken.append(pnl)
    hours = max(1e-9, (end - start) / 3_600_000)
    return {'trades': len(taken), 'end_equity': round(eq, 5), 'net_usdt': round(eq - cfg['equity'], 5),
            'net_usdt_per_hour': round((eq - cfg['equity']) / hours, 7), 'mdd_usdt': round(mdd, 5),
            'skipped': dict(skipped), 'hours': round(hours, 1)}


# ----------------------------------------------------------------------------------------------------- driver
def run_symbol(symbol: str, rng: random.Random, root: Path = KLINES) -> dict:
    m1 = load_1m(symbol, root)
    tick = tick_of(m1['c'])
    setups = detect(m1, SMCFibo(r_multiple=1.0))
    t5 = resample(m1, 5)['t']
    res = {'symbol': symbol, 'bars_1m': int(len(m1['t'])), 'tick': tick, 'setups': len(setups), 'trades': {}}
    for r_mult, be in VARIANTS:
        key = f'R{r_mult:g}_BE{"on" if be else "off"}'
        busy_until, rows = -1, []
        for s in setups:
            if s.t < busy_until:
                continue
            side = 1 if s.side == 'BUY' else -1
            tp = s.entry + side * r_mult * abs(s.entry - s.sl)
            out = simulate(m1, s.t, side, s.entry, s.sl, tp, tick, be)
            if not out['filled']:
                busy_until = s.t + FILL_BARS * 60_000
                continue
            busy_until = out['t_exit']
            risk_bps = abs(s.entry - s.sl) / s.entry * 1e4
            row = {'symbol': symbol, 'side': side, 't_signal': s.t, 'risk_bps': risk_bps, 'net_R': out['net_bps'] / risk_bps,
                   **{k: out[k] for k in ('t_fill', 't_exit', 'why', 'gross_bps', 'net_bps', 'be_armed')}}
            plc = []
            for _ in range(N_PLACEBO):
                lo = np.searchsorted(t5, s.t - PLACEBO_DAYS * 86_400_000)
                hi = np.searchsorted(t5, s.t + PLACEBO_DAYS * 86_400_000) - 1
                if hi <= lo:
                    break
                tq = int(t5[rng.randint(int(lo), int(hi))])
                k = int(np.searchsorted(m1['t'], tq, 'left')) - 1
                if k < 0:
                    continue
                pe = float(m1['c'][k])
                frac = abs(s.entry - s.sl) / s.entry
                po = simulate(m1, tq, side, pe, pe * (1 - side * frac), pe * (1 + side * r_mult * frac), tick, be)
                if po['filled']:
                    plc.append(po['net_bps'])
            row['placebo_net_bps'] = plc
            rows.append(row)
        res['trades'][key] = rows
    return res


def summarize(all_res: list) -> dict:
    out = {}
    for split, (a, b) in (('train', TRAIN), ('holdout', HOLDOUT)):
        lo, hi = ms(a), ms(b)
        for r_mult, be in VARIANTS:
            key = f'R{r_mult:g}_BE{"on" if be else "off"}'
            rows = [x for res in all_res for x in res['trades'][key] if lo <= x['t_fill'] < hi]
            plc = [p for x in rows for p in x['placebo_net_bps']]
            per_sym = {}
            for res in all_res:
                sr = [x for x in res['trades'][key] if lo <= x['t_fill'] < hi]
                per_sym[res['symbol']] = stats([x['net_bps'] for x in sr])
            exits = defaultdict(int)
            for x in rows:
                exits[x['why']] += 1
            out[f'{split}:{key}'] = {
                'stats': stats([x['net_bps'] for x in rows], [x['risk_bps'] for x in rows]),
                'gross': stats([x['gross_bps'] for x in rows]),
                'placebo': stats(plc), 'vs_placebo': welch_lcb([x['net_bps'] for x in rows], plc),
                'symbols_positive': sum(1 for v in per_sym.values() if v.get('n') and v['mean_net_bps'] > 0),
                'per_symbol': per_sym, 'exits': dict(exits), 'daily': daily_sharpe(rows, lo, hi),
                'micro': micro_portfolio(rows, lo, hi), 'micro_kelly': micro_portfolio(rows, lo, hi, kelly=True),
                'mean_risk_bps': round(float(np.mean([x['risk_bps'] for x in rows])), 2) if rows else None,
                'net_R_list': [round(x['net_R'], 4) for x in rows],
            }
    return out


def acceptance(summary: dict) -> dict:
    train = {k.split(':', 1)[1]: v for k, v in summary.items() if k.startswith('train:')}
    eligible = {k: v for k, v in train.items() if v['stats'].get('n', 0) >= 100}
    if not eligible:
        return {'chosen': None, 'pass': False, 'why': 'no variant with n >= 100 on TRAIN'}
    chosen = max(eligible, key=lambda k: eligible[k]['stats']['mean_net_bps'])
    h = summary[f'holdout:{chosen}']
    checks = {
        'n>=100': h['stats'].get('n', 0) >= 100,
        'lcb95>0': (h['stats'].get('lcb95') or -1e9) > 0,
        'beats_placebo_lcb95>0': (h['vs_placebo'].get('lcb95') or -1e9) > 0,
        'symbols_positive>=6': h['symbols_positive'] >= 6,
    }
    return {'chosen': chosen, 'checks': checks, 'pass': all(checks.values())}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--symbols', default=','.join(SYMBOLS))
    ap.add_argument('--out', default=str(ROOT / 'data' / 'smc_fibo_backtest.json'))
    ap.add_argument('--seed', type=int, default=20261113)
    a = ap.parse_args(argv)
    rng = random.Random(a.seed)
    t0 = time.time()
    all_res = []
    for sym in [s.strip() for s in a.symbols.split(',') if s.strip()]:
        t1 = time.time()
        res = run_symbol(sym, rng)
        all_res.append(res)
        print(f'{sym}: {res["bars_1m"]} 1m bars, tick {res["tick"]:g}, {res["setups"]} setups, '
              f'{ {k: len(v) for k, v in res["trades"].items()} } trades ({time.time() - t1:.1f} s)', flush=True)
    summary = summarize(all_res)
    verdict = acceptance(summary)
    Path(a.out).write_text(json.dumps({'spec': 'docs/harness/plan_alpha_reengineering_2026_11_13.md#B',
                                       'seed': a.seed, 'summary': summary, 'acceptance': verdict,
                                       'per_symbol_setups': {r['symbol']: r['setups'] for r in all_res},
                                       'seconds': round(time.time() - t0, 1)}, indent=1), encoding='utf-8')
    for k, v in summary.items():
        s, p = v['stats'], v['vs_placebo']
        print(f"{k:22s} n={s.get('n', 0):5d} win={s.get('win_rate')} net={s.get('mean_net_bps')}±{s.get('se')} "
              f"lcb={s.get('lcb95')} PF={s.get('profit_factor')} vsPlacebo={p.get('diff')} (lcb {p.get('lcb95')}) "
              f"sym+={v['symbols_positive']} mddR={s.get('mdd_R')} micro/h={v['micro']['net_usdt_per_hour']}")
    print('ACCEPTANCE', json.dumps(verdict))
    print(f'wrote {a.out} ({time.time() - t0:.0f} s)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
