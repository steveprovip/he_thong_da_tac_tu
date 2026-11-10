#!/usr/bin/env python3
"""Phase 2 of docs/harness/plan_micro_adverse_selection.md: a mainnet-tape simulation of the chop harvester's maker fills, and a time-separated
search for (a) an entry / post-fill filter and (b) a payoff bracket that clears the fees.  Read-only on its inputs.

    python -I tools/phase2_harvest_study.py build   --data data/aggtrades_20261110 --symbols DOGEUSDT,XRPUSDT,1000PEPEUSDT --model cons --out data/phase2_cons.csv.gz
    python -I tools/phase2_harvest_study.py analyze --table data/phase2_cons.csv.gz [--json out.json]

THE SIMULATED LANE (aggTrades only - what the public archive has).  Per symbol and UTC day, every second:
  chop     KER(2 min, 5 s steps) < 0.28 (the micro lane boundary), known
  signal   |flow10| >= 0.30, flow10 = (taker buys - taker sells) / total over the last 10 s, >= 3 prints; side = sign.  This is a TRADES proxy for the
           engine's order-book OFI L10 (not in aggTrades); the swarm quorum is not modelled either, so the study sees MORE entries than the engine takes
  touch    BUY at the last seller-initiated print price (the bid), SELL at the last buyer-initiated print price (the ask); both updated within 10 s,
           ask >= bid and <= 3 bps apart
  fill     a print THROUGH the quote within ttl 45 s: opt = a print at or through, cons = strictly through (no queue position either way)
  one position per symbol: next entry only after the default bracket (0.15 / 0.15) exits + 30 s; after an unfilled quote, 20 s.
Exits are priced for every TP x SL pair at once from first-passage indices of the prints after the fill: TP = maker (filled by an opposite-aggressor print
at or through its price), stop = any print at or through (taker 5 bps + 1 bp slip), horizon 1 200 s -> maker at the last print.  Fees: maker 2 bps each side.
Only posts between 00:03 and 23:35 UTC are used so every lookback / horizon stays inside one day file.

THE SEARCH (pre-registered in the plan, fixed before the first run).  Days sorted; first 70 % = train, the rest = holdout, touched once per rule.
Acceptance: holdout n >= 400 fills, net per fill > 0 with the 95 % block-bootstrap (by day) lower bound > 0, kept fills >= 25 % of the unfiltered, and the net stays
> 0 after removing the best day.  Features / thresholds / models are chosen on train only; the number of rules tried is printed (Bonferroni)."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

TP_LEVELS = (0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50)       # % of the fill price
SL_LEVELS = (0.10, 0.15, 0.20, 0.25, 0.30)
CFG = {'ttl_s': 45.0, 'horizon_s': 1200.0, 'every_s': 20.0, 'cooldown_s': 30.0, 'ker_max': 0.28, 'flow_min': 0.30, 'min_prints': 3,
       'gap_max_bps': 3.0, 'stale_s': 10.0, 'maker_bps': 2.0, 'taker_bps': 5.0, 'slip_bps': 1.0, 'notional_usdt': 5.05,
       'side_mode': 'follow', 'default_tp': 0.15, 'default_sl': 0.15, 'first_s': 180, 'last_s': 86400 - 25 * 60}
FEATURES = ['flow3', 'flow10', 'flow30', 'flow60', 'ret15', 'ret60', 'rng60', 'ker', 'n10', 'n60', 'vol60', 'gap_bps', 'hour',
            'fill_wait', 'burst3_usd', 'fill_print_usd']


# ============================================================================= arrays per day
def load_day(path: Path) -> dict:
    z = np.load(path)
    return {k: z[k] for k in ('t', 'p', 'q', 'm')}


def rsum(a: np.ndarray, w: int) -> np.ndarray:
    """Trailing window sum over the last w entries (nan for the first w - 1)."""
    c = np.cumsum(np.nan_to_num(a, nan=0.0), dtype=np.float64)
    out = np.full(len(a), np.nan)
    out[w - 1:] = c[w - 1:] - np.concatenate(([0.0], c[:-w]))
    return out


def second_bars(t, p, q, m) -> dict:
    """Per-second arrays of one day (index 0 = the first second of the day, dense)."""
    day0 = (t[0] // 86_400_000) * 86_400
    n = 86_400
    sec = (t // 1000 - day0).astype(np.int64)
    usd = p * q
    buy = np.bincount(sec, weights=usd * (~m), minlength=n)
    sell = np.bincount(sec, weights=usd * m, minlength=n)
    cnt = np.bincount(sec, minlength=n).astype(np.float64)
    last = np.searchsorted(sec, np.arange(n), side='right') - 1         # index of the last print at or before each second
    px = np.where(last >= 0, p[np.clip(last, 0, None)], np.nan)
    starts = np.flatnonzero(np.r_[True, sec[1:] != sec[:-1]])
    hi, lo = np.full(n, np.nan), np.full(n, np.nan)
    hi[sec[starts]] = np.maximum.reduceat(p, starts)
    lo[sec[starts]] = np.minimum.reduceat(p, starts)
    hi, lo = np.where(np.isnan(hi), px, hi), np.where(np.isnan(lo), px, lo)

    def touch(flag):                                                      # last print price / age with the given aggressor flag
        pos = np.flatnonzero(flag)
        k = np.searchsorted(pos, last, side='right') - 1
        ok = (k >= 0) & (last >= 0)
        price = np.where(ok, p[pos[np.clip(k, 0, None)]], np.nan)
        age = np.where(ok, np.arange(n) - sec[pos[np.clip(k, 0, None)]], np.inf)
        return price, age
    bid, bid_age = touch(m)                                               # buyer is maker = a seller hit the bid
    ask, ask_age = touch(~m)
    return {'day0': day0, 'sec': sec, 'px': px, 'buy': buy, 'sell': sell, 'cnt': cnt, 'hi': hi, 'lo': lo, 'bid': bid, 'bid_age': bid_age,
            'ask': ask, 'ask_age': ask_age}


def ker_series(px: np.ndarray, window: int = 120, step: int = 5) -> np.ndarray:
    """Kaufman efficiency ratio on px sampled every `step` s over `window` s, evaluated at every second (nan until enough history)."""
    k = window // step
    out = np.full(len(px), np.nan)
    for r in range(step):
        sub = px[r::step]
        if len(sub) <= k:
            continue
        d = np.abs(np.diff(sub))
        path = rsum(d, k)[k - 1:]                                         # path[j] = sum |d| over sub[j .. j+k]
        net = np.abs(sub[k:] - sub[:-k])
        with np.errstate(divide='ignore', invalid='ignore'):
            ker = np.where(path > 0, net / path, 0.0)
        out[r + step * k::step][:len(ker)] = ker
    return out


def features_grid(b: dict) -> dict:
    px, buy, sell, cnt = b['px'], b['buy'], b['sell'], b['cnt']
    f = {}
    tot = buy + sell
    for w in (3, 10, 30, 60):
        bs, ss = rsum(buy, w), rsum(sell, w)
        with np.errstate(divide='ignore', invalid='ignore'):
            f[f'flow{w}'] = np.where(bs + ss > 0, (bs - ss) / (bs + ss), np.nan)
    f['n10'], f['n60'], f['vol60'] = rsum(cnt, 10), rsum(cnt, 60), rsum(tot, 60)
    for w in (15, 60):
        r = np.full(len(px), np.nan)
        r[w:] = (px[w:] / px[:-w] - 1.0) * 1e4
        f[f'ret{w}'] = r
    hi60 = pd.Series(b['hi']).rolling(60, min_periods=60).max().to_numpy()
    lo60 = pd.Series(b['lo']).rolling(60, min_periods=60).min().to_numpy()
    f['rng60'] = (hi60 - lo60) / px * 1e4
    f['ker'] = ker_series(px)
    with np.errstate(invalid='ignore'):
        f['gap_bps'] = (b['ask'] - b['bid']) / px * 1e4
    return f


# ============================================================================= fills and exits
def first_pass(runs: np.ndarray, levels: np.ndarray, strict: bool) -> np.ndarray:
    """First index where a non-decreasing array reaches each level (>= level, or > level when strict); len(runs) if it never does."""
    return np.searchsorted(runs, levels, side='right' if strict else 'left')


def passages(t, p, m, fi: int, side: str, q: float, cfg: dict, model: str):
    """Indices (relative to the first print after the fill) of the first TP / stop passage for every level, and the horizon exit price."""
    end = int(np.searchsorted(t, t[fi] + cfg['horizon_s'] * 1000, side='right'))
    pw, mw = p[fi + 1:end], m[fi + 1:end]
    n = len(pw)
    inf = n + 1
    tp_l, sl_l = np.array(TP_LEVELS) / 100.0, np.array(SL_LEVELS) / 100.0
    if n == 0:
        return np.full(len(tp_l), inf), np.full(len(sl_l), inf), q, 0
    strict = model == 'cons'
    if side == 'BUY':                                                      # long: TP by buyer-initiated prints up, stop by any print down
        up = np.maximum.accumulate(np.where(~mw, pw, -np.inf))
        tp_i = first_pass(up, q * (1 + tp_l), strict)
        dn = np.minimum.accumulate(pw)
        sl_i = first_pass(-dn, -q * (1 - sl_l), False)
    else:                                                                  # short: TP by seller-initiated prints down, stop by any print up
        dn = np.minimum.accumulate(np.where(mw, pw, np.inf))
        tp_i = first_pass(-dn, -q * (1 - tp_l), strict)
        up = np.maximum.accumulate(pw)
        sl_i = first_pass(up, q * (1 + sl_l), False)
    tp_i, sl_i = np.where(tp_i >= n, inf, tp_i), np.where(sl_i >= n, inf, sl_i)
    return tp_i, sl_i, float(pw[-1]), n


def candidate_mask(f: dict, b: dict, cfg: dict) -> np.ndarray:
    ok = np.isfinite(f['ker']) & (f['ker'] < cfg['ker_max']) & (np.abs(f['flow10']) >= cfg['flow_min']) & (f['n10'] >= cfg['min_prints'])
    ok &= (b['bid_age'] <= cfg['stale_s']) & (b['ask_age'] <= cfg['stale_s']) & (b['ask'] >= b['bid']) & (f['gap_bps'] <= cfg['gap_max_bps'])
    s = np.arange(len(ok))
    return ok & (s >= cfg['first_s']) & (s <= cfg['last_s'])


def build_day(symbol: str, day: str, arr: dict, cfg: dict, model: str) -> list:
    t, p, q, m = arr['t'], arr['p'], arr['q'], arr['m']
    if len(t) < 1000:
        return []
    b = second_bars(t, p, q, m)
    f = features_grid(b)
    cand = candidate_mask(f, b, cfg)
    rows, next_ok = [], 0
    rng = np.random.default_rng(sum(map(ord, symbol + day)))
    day0 = b['day0']
    for s in np.flatnonzero(cand):
        if s < next_ok:
            continue
        follow = 'BUY' if f['flow10'][s] > 0 else 'SELL'
        side = follow if cfg['side_mode'] == 'follow' else ('SELL' if follow == 'BUY' else 'BUY') if cfg['side_mode'] == 'fade' else \
            ('BUY' if rng.random() < 0.5 else 'SELL')                      # 'random': the cost-only baseline, no information
        quote = float(b['bid'][s] if side == 'BUY' else b['ask'][s])
        t_post = (day0 + s + 1) * 1000
        i0 = int(np.searchsorted(t, t_post, side='left'))
        i1 = int(np.searchsorted(t, t_post + cfg['ttl_s'] * 1000, side='left'))
        if i1 - i0 <= 0:
            next_ok = s + cfg['every_s']
            continue
        pw, mw = p[i0:i1], m[i0:i1]
        if side == 'BUY':
            through = mw & ((pw < quote) if model == 'cons' else (pw <= quote))
        else:
            through = (~mw) & ((pw > quote) if model == 'cons' else (pw >= quote))
        hit = np.flatnonzero(through)
        if len(hit) == 0:
            next_ok = s + cfg['every_s']
            continue
        fi = i0 + int(hit[0])
        tp_i, sl_i, last_px, n_after = passages(t, p, m, fi, side, quote, cfg, model)
        sign = 1 if side == 'BUY' else -1
        h_move = sign * (last_px / quote - 1.0) * 1e4
        sf = int((t[fi] // 1000) - day0)
        mk = {}
        for h in (15, 30, 60):
            mk[f'mk{h}'] = sign * (b['px'][min(sf + h, len(b['px']) - 1)] / quote - 1.0) * 1e4 if sf + h < len(b['px']) else np.nan
        d = {'symbol': symbol, 'day': day, 'sec': int(s), 'side': side, 'sign': sign, 'quote': quote, 'fill_wait': (t[fi] - t_post) / 1000.0,
             'h_move': h_move, 'n_after': n_after, 'hour': int(s // 3600),
             'burst3_usd': float(b['buy'][max(sf - 2, 0):sf + 1].sum() + b['sell'][max(sf - 2, 0):sf + 1].sum()),
             'fill_print_usd': float(p[fi] * q[fi]), **mk}
        for name in ('flow3', 'flow10', 'flow30', 'flow60', 'ret15', 'ret60'):
            d[name] = sign * f[name][s]                                    # with the trade
        for name in ('rng60', 'ker', 'n10', 'n60', 'vol60', 'gap_bps'):
            d[name] = f[name][s]
        for k, v in enumerate(tp_i):
            d[f'tp{k}'] = int(v)
        for k, v in enumerate(sl_i):
            d[f'sl{k}'] = int(v)
        rows.append(d)
        # one position at a time: the default bracket's exit, then the cool-down
        first = min(int(tp_i[TP_LEVELS.index(cfg['default_tp'])]), int(sl_i[SL_LEVELS.index(cfg['default_sl'])]))
        t_exit_ms = t[fi + 1 + first] if first < n_after else t[fi] + cfg['horizon_s'] * 1000
        next_ok = max(s + cfg['every_s'], t_exit_ms / 1000.0 - day0 + cfg['cooldown_s'])
    return rows


def cmd_build(a) -> None:
    cfg = {**CFG, 'side_mode': a.side}
    rows = []
    for sym in [x.strip().upper() for x in a.symbols.split(',') if x.strip()]:
        for path in sorted((Path(a.data) / sym).glob('*.npz')):
            day = path.stem
            r = build_day(sym, day, load_day(path), cfg, a.model)
            rows += r
            print(f'{sym} {day}: {len(r)} fills', flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(a.out, index=False, compression='gzip')
    print(f'{len(df)} fills -> {a.out}')


# ============================================================================= analysis
def auc(score: np.ndarray, label: np.ndarray) -> float:
    from scipy.stats import rankdata
    pos = label.astype(bool)
    n1, n0 = int(pos.sum()), int((~pos).sum())
    if n1 == 0 or n0 == 0:
        return float('nan')
    r = rankdata(score)
    return float((r[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def auc_z(a: float, n1: int, n0: int) -> float:
    se = np.sqrt((n1 + n0 + 1) / (12.0 * n1 * n0))
    return (a - 0.5) / se


def block_boot(values: np.ndarray, days: np.ndarray, b: int = 2000, seed: int = 7):
    """95 % interval of the mean of `values` when whole days are resampled."""
    rng = np.random.default_rng(seed)
    ud = np.unique(days)
    sums = {d: values[days == d].sum() for d in ud}
    cnts = {d: int((days == d).sum()) for d in ud}
    means = []
    for _ in range(b):
        pick = rng.choice(ud, size=len(ud), replace=True)
        n = sum(cnts[d] for d in pick)
        means.append(sum(sums[d] for d in pick) / n if n else np.nan)
    lo, hi = np.nanpercentile(means, [2.5, 97.5])
    return float(lo), float(hi)


def fit_logit(x: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 30) -> np.ndarray:
    xb = np.hstack([np.ones((len(x), 1)), x])
    w = np.zeros(xb.shape[1])
    for _ in range(iters):
        z = np.clip(xb @ w, -30, 30)
        pr = 1 / (1 + np.exp(-z))
        g = xb.T @ (pr - y) + l2 * np.r_[0.0, w[1:]]
        h = (xb * (pr * (1 - pr))[:, None]).T @ xb + l2 * np.eye(len(w))
        step = np.linalg.solve(h + 1e-9 * np.eye(len(w)), g)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return w


def passage_arrays(df: pd.DataFrame):
    tp = df[[f'tp{k}' for k in range(len(TP_LEVELS))]].to_numpy()
    sl = df[[f'sl{j}' for j in range(len(SL_LEVELS))]].to_numpy()
    return tp, sl


def bracket_net(df: pd.DataFrame, tp: float, sl: float, cfg: dict = CFG) -> np.ndarray:
    """Net bps per fill of the bracket, including the horizon exit (maker at the last print, 2 bps each way) when neither leg passes."""
    tp_i, sl_i = passage_arrays(df)
    k, j = TP_LEVELS.index(tp), SL_LEVELS.index(sl)
    ti, si = tp_i[:, k], sl_i[:, j]
    n_after = df['n_after'].to_numpy()
    maker, taker, slip = cfg['maker_bps'], cfg['taker_bps'], cfg['slip_bps']
    won = (ti < si) & (ti < n_after)
    lost = (si <= ti) & (si < n_after)
    horizon = df['h_move'].to_numpy() - 2 * maker
    return np.where(won, tp * 100 - 2 * maker, np.where(lost, -sl * 100 - maker - taker - slip, horizon))


def acceptance(net_bps: np.ndarray, days: np.ndarray, kept_frac: float, cfg: dict = CFG) -> dict:
    lo, hi = block_boot(net_bps, days) if len(net_bps) else (float('nan'), float('nan'))
    best = None
    if len(net_bps):
        per_day = {d: net_bps[days == d].sum() for d in np.unique(days)}
        best = max(per_day, key=per_day.get)
        rest = net_bps[days != best]
    else:
        rest = np.array([])
    out = {'n': int(len(net_bps)), 'net_bps_per_fill': float(net_bps.mean()) if len(net_bps) else float('nan'),
           'net_usdt_per_fill': float(net_bps.mean() / 1e4 * cfg['notional_usdt']) if len(net_bps) else float('nan'),
           'ci95_bps': [lo, hi], 'kept_frac': float(kept_frac), 'net_bps_without_best_day': float(rest.mean()) if len(rest) else float('nan'),
           'best_day': str(best)}
    out['pass'] = {'n>=400': out['n'] >= 400, 'lower95>0': bool(lo > 0), 'kept>=25%': kept_frac >= 0.25,
                   'without_best_day>0': bool(len(rest) and rest.mean() > 0)}
    out['accepted'] = all(out['pass'].values())
    return out


def cmd_analyze(a) -> None:
    df = pd.read_csv(a.table)
    df = df.sort_values(['day', 'sec']).reset_index(drop=True)
    days = sorted(df['day'].unique())
    cut = int(round(len(days) * 0.7))
    train_days, hold_days = set(days[:cut]), set(days[cut:])
    tr, ho = df[df['day'].isin(train_days)].reset_index(drop=True), df[df['day'].isin(hold_days)].reset_index(drop=True)
    res = {'table': str(a.table), 'fills': int(len(df)), 'days': len(days), 'train_days': len(train_days), 'holdout_days': len(hold_days),
           'train_fills': int(len(tr)), 'holdout_fills': int(len(ho))}
    print(f"{len(df)} fills over {len(days)} days (train {len(tr)} on {len(train_days)} days, holdout {len(ho)} on {len(hold_days)})")
    print('per symbol fills:', df.groupby('symbol').size().to_dict())
    dn = bracket_net(df, CFG['default_tp'], CFG['default_sl'])
    print('per symbol net bps/fill at 0.15 / 0.15:', {k: round(float(dn[(df['symbol'] == k).to_numpy()].mean()), 2) for k in sorted(df['symbol'].unique())})

    # 1. unfiltered default bracket, then the whole payoff grid (train -> choose -> holdout)
    dflt = bracket_net(df, CFG['default_tp'], CFG['default_sl'])
    tp_i, sl_i = passage_arrays(tr)
    n_after = tr['n_after'].to_numpy()
    resolved = (tp_i[:, 1] < n_after) | (sl_i[:, 1] < n_after)
    tp_first = ((tp_i[:, 1] < sl_i[:, 1]) & (tp_i[:, 1] < n_after))[resolved].mean()
    be = (15 + 8) / ((15 - 4) + (15 + 8))                      # break-even TP-first rate of 0.15 / 0.15: loss / (win + loss), in bps
    print(f"unfiltered 0.15 / 0.15: {dflt.mean():+.2f} bps/fill ({dflt.mean() / 1e4 * CFG['notional_usdt']:+.5f} U), "
          f"TP before stop {tp_first:.3f} of the resolved train fills (break-even {be:.3f})")
    grid = {}
    for tp in TP_LEVELS:
        for sl in SL_LEVELS:
            grid[(tp, sl)] = (bracket_net(tr, tp, sl).mean(), bracket_net(ho, tp, sl).mean())
    best = max(grid, key=lambda k: grid[k][0])
    print(f"payoff grid ({len(grid)} brackets): best on TRAIN TP {best[0]} / SL {best[1]}: train {grid[best][0]:+.2f} bps, holdout {grid[best][1]:+.2f} bps")
    pos_train = [k for k, v in grid.items() if v[0] > 0]
    print(f"  brackets with train net > 0: {len(pos_train)} of {len(grid)}; with holdout net > 0: {sum(1 for v in grid.values() if v[1] > 0)}")
    res['grid'] = {f'{k[0]}/{k[1]}': {'train_bps': float(v[0]), 'holdout_bps': float(v[1])} for k, v in grid.items()}
    res['best_bracket_train'] = {'tp': best[0], 'sl': best[1], 'train_bps': float(grid[best][0]), 'holdout_bps': float(grid[best][1])}
    nb = bracket_net(ho, best[0], best[1])
    res['best_bracket_holdout_acceptance'] = acceptance(nb, ho['day'].to_numpy(), 1.0)
    print('  best-bracket holdout acceptance:', json.dumps(res['best_bracket_holdout_acceptance']['pass']), f"net {nb.mean():+.2f} bps CI {res['best_bracket_holdout_acceptance']['ci95_bps']}")

    # 2. entry features on TRAIN: AUC against "default bracket wins"
    y = (bracket_net(tr, CFG['default_tp'], CFG['default_sl']) > 0).astype(int)
    rows, tested = [], 0
    for ftr in FEATURES:
        x = tr[ftr].to_numpy(float)
        ok = np.isfinite(x)
        a_ = auc(x[ok], y[ok])
        z = auc_z(a_, int(y[ok].sum()), int((1 - y[ok]).sum()))
        rows.append((ftr, a_, z))
        tested += 1
    rows.sort(key=lambda r: -abs(r[1] - 0.5))
    print(f"\nfeature AUC vs 'default bracket wins' on TRAIN (n {len(tr)}, {tested} features tried; Bonferroni |z| > {2.9:.1f})")
    for ftr, a_, z in rows:
        print(f'  {ftr:<14} AUC {a_:.3f}  z {z:+.1f}{"  *" if abs(z) > 2.9 else ""}')
    res['feature_auc_train'] = {r[0]: {'auc': r[1], 'z': r[2]} for r in rows}

    # 3. a logistic filter on the strongest train features, threshold chosen on train subject to kept >= 25 %, scored on the holdout once
    top = [r[0] for r in rows[:5]]
    x_tr = tr[top].to_numpy(float)
    ok_tr = np.isfinite(x_tr).all(axis=1)
    mu, sd = x_tr[ok_tr].mean(axis=0), x_tr[ok_tr].std(axis=0) + 1e-12
    w = fit_logit((x_tr[ok_tr] - mu) / sd, y[ok_tr])
    score = lambda d: np.hstack([np.ones((len(d), 1)), (np.where(np.isfinite(d[top].to_numpy(float)), d[top].to_numpy(float), mu) - mu) / sd]) @ w
    s_tr, s_ho = score(tr), score(ho)
    net_tr, net_ho = bracket_net(tr, CFG['default_tp'], CFG['default_sl']), bracket_net(ho, CFG['default_tp'], CFG['default_sl'])
    best_q, best_net = None, -1e9
    for qn in np.arange(0.0, 0.76, 0.05):                                  # keep the top (1 - qn) of the score
        thr = np.quantile(s_tr, qn)
        kept = s_tr >= thr
        if kept.mean() < 0.25:
            continue
        v = net_tr[kept].mean()
        if v > best_net:
            best_q, best_net = qn, v
    thr = np.quantile(s_tr, best_q)
    kept_ho = s_ho >= thr
    res['logit_filter'] = {'features': top, 'keep_quantile_train': float(best_q), 'train_net_bps': float(best_net),
                           'holdout_auc': float(auc(s_ho, (net_ho > 0).astype(int))), 'holdout': acceptance(net_ho[kept_ho], ho['day'].to_numpy()[kept_ho], float(kept_ho.mean()))}
    r = res['logit_filter']['holdout']
    print(f"\nlogit on {top}: train best keeps top {1 - best_q:.0%} (train {best_net:+.2f} bps/fill). HOLDOUT: AUC {res['logit_filter']['holdout_auc']:.3f}, "
          f"kept {r['kept_frac']:.0%}, n {r['n']}, net {r['net_bps_per_fill']:+.2f} bps/fill ({r['net_usdt_per_fill']:+.5f} U) CI {r['ci95_bps'][0]:+.2f}..{r['ci95_bps'][1]:+.2f}; "
          f"without best day {r['net_bps_without_best_day']:+.2f}; pass {r['pass']}  ACCEPTED: {r['accepted']}")

    # 4. the post-fill cut: leave at the touch (maker, 2 bps) when the with-trade move at +H s is <= -X bps; chosen on train, scored on the holdout
    best_cut, best_cut_net = None, -1e9
    base_tr = bracket_net(tr, CFG['default_tp'], CFG['default_sl'])
    for H in (15, 30, 60):
        for X in (2, 4, 6, 8):
            mk_tr = tr[f'mk{H}'].to_numpy(float)
            n_tr = np.where(np.isfinite(mk_tr) & (mk_tr <= -X), mk_tr - 2 * CFG['maker_bps'], base_tr)
            if n_tr.mean() > best_cut_net:
                best_cut, best_cut_net = (H, X), n_tr.mean()
    H, X = best_cut
    mk = ho[f'mk{H}'].to_numpy(float)
    cutm = np.isfinite(mk) & (mk <= -X)
    n_ho = np.where(cutm, mk - 2 * CFG['maker_bps'], net_ho)
    res['post_fill_cut'] = {'H': H, 'X': X, 'train_net_bps': float(best_cut_net), 'holdout_cut_frac': float(cutm.mean()),
                            'holdout': acceptance(n_ho, ho['day'].to_numpy(), 1.0), 'holdout_uncut_bps': float(net_ho.mean())}
    r = res['post_fill_cut']
    print(f"\npost-fill cut chosen on train: leave if move at +{H}s <= -{X} bps (train {best_cut_net:+.2f} vs uncut {net_tr.mean():+.2f}); HOLDOUT cut {r['holdout_cut_frac']:.0%} of fills: "
          f"{r['holdout']['net_bps_per_fill']:+.2f} bps/fill vs uncut {r['holdout_uncut_bps']:+.2f}; CI {r['holdout']['ci95_bps'][0]:+.2f}..{r['holdout']['ci95_bps'][1]:+.2f}")
    res['rules_tried'] = {'brackets': len(grid), 'features': tested, 'quantiles': 16, 'cuts': 12}
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1, default=float), encoding='utf-8')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build')
    b.add_argument('--data', required=True)
    b.add_argument('--symbols', required=True)
    b.add_argument('--model', choices=('cons', 'opt'), default='cons')
    b.add_argument('--side', choices=('follow', 'fade', 'random'), default='follow', help='follow = the harvester (side of the flow); fade / random = baselines')
    b.add_argument('--out', required=True)
    z = sub.add_parser('analyze')
    z.add_argument('--table', required=True)
    z.add_argument('--json')
    a = ap.parse_args(argv)
    {'build': cmd_build, 'analyze': cmd_analyze}[a.cmd](a)
    return 0


if __name__ == '__main__':
    sys.exit(main())
