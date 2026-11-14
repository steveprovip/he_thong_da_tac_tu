"""HRP between two research sleeves (2026-11-07): TIDAL-60 maker and XV funding carry. Offline research only.

Reads data/klines_1m and data/funding_cache (no network, no orders, no config or tunable writes):

    python -m research.hrp_alpha_sleeves --json docs/research_upgrade/snapshot_2026-11-07_hrp_sleeves/hrp_sleeves.json

1. Re-derives the figures it was handed, through the code paths that produced them:
   - TIDAL-60 and the 8-agent survivor-weighted meta-portfolio (claimed -1.51 bps/day): agent configs and
     weights from the factory maker run (snapshot factory_maker_agents.json), scripts/autoresearch_agent_swarm.py
     load_klines / portfolio, maker fill on trade-through, 8.5 bps, the run_trade 70/30 split + embargo.
   - XV HEDGE|H28|k2: tools/funding_spread_replay.py simulate / summarize on the cached funding and 4 h marks,
     with the original data end (exits <= 2026-10-09) and with the refreshed cache (exits <= 2026-11-04).
2. Daily sleeve returns, in bps of slot capital:
   - TIDAL: mean over the 4 symbols of each day's summed trade nets, booked on the entry day, idle = 0
     (autoresearch portfolio()); estimation-window trades are cut so their exits stay before the window end.
   - XV: mean over the 18 coin slots of daily mark-to-market: funding prints in (T, T+H] by day, basis change
     between the 00:00 UTC 4 h opens, half the round-trip cost at the open and half at the close. Per position the
     daily marks sum to the replay's net_bps. Capital = notional assumes leverage 2 on each venue (2/L = 1).
3. HRP (core.portfolio_hrp.hrp_allocate; with two sleeves it is inverse variance) estimated on
   2025-01-01..2026-03-14, applied unchanged to the 200-day TIDAL holdout 2026-03-15..2026-09-30, plus a monthly
   expanding-window re-estimate.
Limits (also written into the output): TIDAL-60 was picked after the fact from 1,406 trials, its train P&L is
negative, and its config is contrarian (coef < 0, 44-bar horizon) despite the name; the XV holdout was already
used to judge that sleeve. A positive portfolio figure on these windows is selection, not evidence. Nothing here
changes any weight, size or profile.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / 'scripts'):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import autoresearch_agent_swarm as ar  # noqa: E402
from core.portfolio_hrp import hrp_allocate  # noqa: E402
from tools import funding_spread_replay as fsr  # noqa: E402

SNAPSHOT = ROOT / 'docs' / 'research_upgrade' / 'snapshot_2026-11-07_hrp_sleeves'
AGENTS_JSON = SNAPSHOT / 'factory_maker_agents.json'
SYMBOLS = ('BTCUSDT', 'SOLUSDT', 'DOGEUSDT', 'BNBUSDT')
MONTHS = tuple(['2024-12'] + [f'2025-{m:02d}' for m in range(1, 13)] + [f'2026-{m:02d}' for m in range(1, 10)])
DAY_MS = 86_400_000
XV = {'kind': 'XV', 'h_days': 28, 'k': 2.0, 'mode': 'HEDGE'}
ORIGINAL_END, REFRESHED_END = '2026-10-09', '2026-11-04'
EST = ('2025-01-01', '2026-03-14')
CLAIMS = {'TIDAL-60': {'trades': 316, 'mean_net_bps': 8.33},
          'XV': {'n': 153, 'mean_net_bps': 14.6},
          'meta_portfolio_daily_bps': -1.51}


def day_of(day: str) -> int:
    return fsr.ms(day) // DAY_MS


def day_str(d: int) -> str:
    return datetime.fromtimestamp(d * 86400, timezone.utc).strftime('%Y-%m-%d')


# ------------------------------------------------------------------ TIDAL-60 sleeve (pure parts tested)
def kline_split(bars, holdout_frac: float = 0.30):
    """The run_trade split: (train, holdout) ms windows with the EMBARGO_BARS gap."""
    t_first = min(int(b.t_ms[min(ar.TRADE_WARMUP, b.t_ms.size - 1)]) for b in bars)
    t_last = max(int(b.t_ms[-1]) for b in bars) + ar.BAR_MS
    t_split = t_first + int((t_last - t_first) * (1.0 - holdout_frac))
    return (t_first, t_split), (t_split + ar.EMBARGO_BARS * ar.BAR_MS, t_last)


def tidal_daily(agent, bars, d_lo: int, d_hi: int, fill: str = 'maker') -> np.ndarray:
    """Days d_lo..d_hi inclusive. Decisions stop (horizon + max gap) bars before the end of day d_hi, so every
    exit price is read before that instant (no estimation trade reaches into the evaluation window)."""
    t_lo, t_end = d_lo * DAY_MS, (d_hi + 1) * DAY_MS
    t_hi = t_end - (agent.horizon + 5) * ar.BAR_MS
    nd = d_hi - d_lo + 1
    syms = sorted({b.symbol for b in bars})
    daily = np.zeros(nd)
    for b in bars:
        idx, _, net = ar.trade_eval(agent, b, t_lo, t_hi, fill)
        if idx.size:
            assert int(b.t_ms[idx + agent.horizon].max()) < t_end
            daily += np.bincount(b.t_ms[idx] // DAY_MS - d_lo, weights=net, minlength=nd)[:nd] / len(syms)
    return daily


# ------------------------------------------------------------------ XV carry sleeve (pure parts tested)
def xv_series(data: dict, coin: str):
    d = data[coin]
    if d['bn'] is None or d['hl'] is None or d['px_bn'] is None or d['px_hl'] is None:
        return None
    return {'bn': d['bn'], 'hl': d['hl'], 'px_bn': d['px_bn'], 'px_other': d['px_hl']}


def xv_trades(data: dict, end: str) -> list:
    out = []
    for coin in fsr.COINS:
        s = xv_series(data, coin)
        if s is not None:
            out += [dict(t, series=coin) for t in fsr.simulate(s, XV['kind'], coin, XV['h_days'], XV['k'], XV['mode'])]
    return [t for t in out if t['t'] * 1000 + t['days'] * DAY_MS <= fsr.ms(end)]


def xv_daily_mtm(trade: dict, s: dict) -> dict:
    """{day: bps} for one XV position. An event at instant x accrues to the UTC day holding x - 1 ms; the open
    cost lands on the open day and the close cost on the close day. A missing 00:00 mark is skipped (the change
    lands on the next mark), so the daily values always sum to fund + basis - cost of the replay."""
    T = int(round(trade['t'] * 1000))
    E = T + trade['days'] * DAY_MS
    short_bn = trade['legs'][0] == 'bn_perp'
    out = defaultdict(float)
    (bt, br), (ht, hr) = s['bn'], s['hl']
    for (t, r), sign in (((bt, br), 1.0 if short_bn else -1.0), ((ht, hr), -1.0 if short_bn else 1.0)):
        i, j = np.searchsorted(t, T, 'right'), np.searchsorted(t, E, 'right')
        for x, v in zip(t[i:j], r[i:j]):
            out[int((int(x) - 1) // DAY_MS)] += sign * float(v)
    pb0, po0 = fsr.price_at(*s['px_bn'], T), fsr.price_at(*s['px_other'], T)

    def value(pb, po):
        if short_bn:
            return ((pb0 - pb) / pb0 + (po - po0) / po0) * 1e4
        return ((po0 - po) / po0 + (pb - pb0) / pb0) * 1e4
    marks = list(range((T // DAY_MS + 1) * DAY_MS, E, DAY_MS)) + [E]
    prev = 0.0
    for m in marks:
        pb, po = fsr.price_at(*s['px_bn'], m), fsr.price_at(*s['px_other'], m)
        if pb is None or po is None:
            continue                       # UNKNOWN mark: carry the change to the next one (E always exists)
        v = value(pb, po)
        out[int((m - 1) // DAY_MS)] += v - prev
        prev = v
    out[T // DAY_MS] -= trade['cost_bps'] / 2
    out[E // DAY_MS] -= trade['cost_bps'] / 2
    return dict(out)


def xv_daily(trades: list, series: dict, d_lo: int, d_hi: int, slots: int) -> np.ndarray:
    daily = np.zeros(d_hi - d_lo + 1)
    for tr in trades:
        for d, v in xv_daily_mtm(tr, series[tr['series']]).items():
            if d_lo <= d <= d_hi:
                daily[d - d_lo] += v / slots
    return daily


# ------------------------------------------------------------------ portfolio statistics
def stats(daily: np.ndarray) -> dict:
    x = np.asarray(daily, dtype=float)
    sd = float(x.std(ddof=1)) if x.size > 1 else 0.0
    cum = np.cumsum(x)
    dd = float((np.maximum.accumulate(np.r_[0.0, cum])[1:] - cum).max()) if x.size else 0.0
    ac1 = float(np.corrcoef(x[:-1], x[1:])[0, 1]) if x.size > 2 and x[:-1].std() > 0 and x[1:].std() > 0 else None
    return {'days': int(x.size), 'mean_bps_day': float(x.mean()) if x.size else None, 'sd_bps_day': sd,
            'sharpe_ann': float(x.mean() / sd * math.sqrt(365)) if sd > 0 else None,
            'lag1_autocorr': ac1,            # > 0 means the iid Sharpe above overstates risk-adjusted return
            'total_bps': float(cum[-1]) if x.size else 0.0, 'max_drawdown_bps': dd,
            'worst_day_bps': float(x.min()) if x.size else None, 'active_days': int((x != 0).sum())}


def walk_forward(est: dict, ev: dict, ev_days: np.ndarray) -> tuple:
    """Monthly expanding-window HRP: each month uses only days before it. -> (daily, weights by month)."""
    names = tuple(sorted(est))
    out, months = np.zeros(ev_days.size), {}
    keys = np.array([day_str(int(d))[:7] for d in ev_days])
    for month in sorted(set(keys)):
        sel = keys == month
        first = int(np.flatnonzero(sel)[0])
        hist = {n: np.r_[est[n], ev[n][:first]] for n in names}
        w = hrp_allocate(hist, names=names).weights
        months[month] = w
        out[sel] = sum(w[n] * ev[n][sel] for n in names)
    return out, months


# ------------------------------------------------------------------ run
def load_funding_offline() -> dict:
    missing = []
    for coin in fsr.COINS:
        sym, hl = f'{coin}USDT', fsr.HL_NAME.get(coin, coin)
        spot = 'PEPEUSDT' if coin == '1000PEPE' else sym
        missing += [n for n in (f'bnf_{sym}', f'hlf_{hl}', f'bnk_{sym}', f'hlk_{hl}', f'bns_{spot}')
                    if not (fsr.CACHE / f'{n}.json').exists()]
    if missing:
        raise SystemExit(f'funding cache incomplete ({len(missing)} files, e.g. {missing[0]}); this tool never '
                         'downloads: run tools/funding_spread_replay.py --refresh yourself')
    return {c: fsr.load(c, False) for c in fsr.COINS}


def run(agents_json: Path = AGENTS_JSON, klines_dir: Path = ROOT / 'data' / 'klines_1m') -> dict:
    t0 = time.perf_counter()
    snap = json.loads(Path(agents_json).read_text(encoding='utf-8'))
    agents = {n: ar.TradeAgent(**a['config']) for n, a in snap['agents'].items()}
    weights = {n: float(a['weight']) for n, a in snap['agents'].items()}
    bars = [b for s in SYMBOLS for b in ar.load_klines(s, list(MONTHS), klines_dir)]
    if not bars:
        raise SystemExit(f'no klines under {klines_dir}')
    train, hold = kline_split(bars)
    split_ok = [list(train), list(hold)] == [snap['split_ms']['train'], snap['split_ms']['holdout']]
    tidal = agents['TIDAL-60']
    d_hold, n_hold = ar.portfolio(tidal, bars, *hold, 'maker')
    p_hold = ar.perf(d_hold, n_hold)
    _, n_train = ar.portfolio(tidal, bars, *train, 'maker')
    meta = {n: ar.portfolio(a, bars, *hold, 'maker')[0] for n, a in agents.items()}
    wsum = sum(weights.values())
    meta_daily = sum(weights[n] * meta[n] for n in agents) / wsum
    contrib = {n: float(weights[n] * meta[n].mean() / wsum) for n in agents}

    data = load_funding_offline()
    series = {c: xv_series(data, c) for c in fsr.COINS}
    xv_by_end = {}
    for end in (ORIGINAL_END, REFRESHED_END):
        tr = xv_trades(data, end)
        dev = [t for t in tr if t['t'] * 1000 + t['days'] * DAY_MS <= fsr.ms(fsr.HOLD[0])]
        xv_by_end[end] = {'holdout': fsr.summarize(tr, *fsr.HOLD), 'dev': fsr.summarize(dev, *fsr.DEV),
                          'holdout2': fsr.summarize(tr, *fsr.HOLD2)}
    trades_now = xv_trades(data, REFRESHED_END)
    gaps = [abs(sum(xv_daily_mtm(t, series[t['series']]).values()) - t['net_bps']) for t in trades_now]

    e_lo, e_hi = day_of(EST[0]), day_of(EST[1])
    v_lo = hold[0] // DAY_MS
    v_hi = v_lo + d_hold.size - 1
    slots = sum(1 for c in fsr.COINS if series[c] is not None)
    est = {'TIDAL-60': tidal_daily(tidal, bars, e_lo, e_hi), 'XV': xv_daily(trades_now, series, e_lo, e_hi, slots)}
    ev = {'TIDAL-60': d_hold, 'XV': xv_daily(trades_now, series, v_lo, v_hi, slots)}
    prop = hrp_allocate(est, names=('TIDAL-60', 'XV'))
    w = prop.weights
    ev_days = np.arange(v_lo, v_hi + 1)
    wf_daily, wf_months = walk_forward(est, ev, ev_days)
    variants = {'TIDAL-60 only': ev['TIDAL-60'], 'XV only': ev['XV'],
                'equal 50/50': 0.5 * ev['TIDAL-60'] + 0.5 * ev['XV'],
                'HRP static (est. window)': w['TIDAL-60'] * ev['TIDAL-60'] + w['XV'] * ev['XV'],
                'HRP monthly walk-forward': wf_daily,
                '8-agent meta-portfolio (claimed drag)': meta_daily}
    corr = float(np.corrcoef(est['TIDAL-60'], est['XV'])[0, 1])

    def verdict(ok: bool) -> str:
        return 'REPRODUCED (arithmetic only; not evidence of edge)' if ok else 'UNVERIFIED'
    xo = xv_by_end[ORIGINAL_END]['holdout']
    reproduced = {
        'TIDAL-60': {'claimed': CLAIMS['TIDAL-60'], 'derived': {k: p_hold[k] for k in (
            'trades', 'mean_net_bps', 'sharpe_ann', 'trades_per_day', 'days')},
            'train_trades': int(n_train.size), 'train_mean_net_bps': float(n_train.mean()) if n_train.size else None,
            'config': snap['agents']['TIDAL-60']['config'],
            'dsr_holdout_null_claimed': snap['agents']['TIDAL-60']['claimed_dsr_holdout_null'],
            'dsr_trials': snap['dsr_trials'],
            'status': verdict(p_hold['trades'] == 316 and round(p_hold['mean_net_bps'], 2) == 8.33 and split_ok)},
        'XV': {'claimed': CLAIMS['XV'], 'unit': 'bps per 28-day round on notional per leg',
               'derived_original_end': xo, 'derived_refreshed_cache': xv_by_end[REFRESHED_END],
               'status': verdict(xo.get('n') == 153 and round(xo['mean_net_bps'], 1) == 14.6)},
        'meta_portfolio': {'claimed_daily_bps': CLAIMS['meta_portfolio_daily_bps'],
                           'derived': stats(meta_daily), 'contribution_bps_day_by_agent': contrib,
                           'weights': weights,
                           'status': verdict(round(float(meta_daily.mean()), 2) == -1.51)}}
    return {'generated_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'inputs': {'agents_json': str(agents_json),
                       'agents_json_sha256': hashlib.sha256(Path(agents_json).read_bytes()).hexdigest(),
                       'klines_dir': str(klines_dir), 'symbols': SYMBOLS, 'months': MONTHS,
                       'funding_cache': str(fsr.CACHE), 'xv_variant': XV, 'xv_slots': slots},
            'split_ms': {'train': list(train), 'holdout': list(hold), 'matches_factory_report': split_ok},
            'reproduced': reproduced,
            'xv_mtm_max_abs_gap_bps': float(max(gaps)) if gaps else None,
            'hrp': {'estimation_window': [day_str(e_lo), day_str(e_hi)],
                    'evaluation_window': [day_str(v_lo), day_str(v_hi)],
                    'weights': w, 'observations': prop.observations, 'order': prop.order,
                    'estimation_stats': {n: stats(v) for n, v in est.items()},
                    'estimation_correlation': corr,
                    'walk_forward_weights_by_month': wf_months},
            'evaluation': {n: stats(v) for n, v in variants.items()},
            'limits': [
                'TIDAL-60 holdout was inspected and the agent chosen after 1,406 trials (OOS DSR 0.0027); its '
                'train P&L is negative, so its holdout result is a selection artefact until new dates confirm it.',
                'The TIDAL-60 config has coef < 0 and a 44-bar horizon: a contrarian rule, not 60-minute momentum.',
                'The XV holdout (2026-01..09) was already used to judge the sleeve; the HO2 segment is too small.',
                'XV daily marks use 4 h opens as marks, not fills; capital = notional assumes leverage 2 per venue.',
                'With two sleeves HRP is inverse variance: it ignores correlation and expected return.',
                'The -1.51 bps/day figure is the 8-agent survivor-weighted portfolio, not a TIDAL-60 + XV mix; '
                'dropping its losing agents after seeing the holdout is selection, not a fix.'],
            'seconds': round(time.perf_counter() - t0, 2)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--agents-json', type=Path, default=AGENTS_JSON)
    ap.add_argument('--klines-dir', type=Path, default=ROOT / 'data' / 'klines_1m')
    ap.add_argument('--json', type=Path, default=None)
    a = ap.parse_args(argv)
    out = run(a.agents_json, a.klines_dir)
    for name, r in out['reproduced'].items():
        print(f'{name:<16} {r["status"]}')
    print(f'HRP weights {out["hrp"]["weights"]} on {out["hrp"]["estimation_window"]}')
    for name, s in out['evaluation'].items():
        sh = 'n/a' if s['sharpe_ann'] is None else f'{s["sharpe_ann"]:+.2f}'
        print(f'  {name:<40} {s["mean_bps_day"]:+8.3f} bps/day  Sharpe {sh:>6}  maxDD {s["max_drawdown_bps"]:7.1f} bps')
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(out, indent=1, default=float) + '\n', encoding='utf-8')
        print(f'JSON: {a.json}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
