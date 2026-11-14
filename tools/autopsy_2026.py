"""Loss autopsy of the Demo ledger (2026-11-13): where did the money go?

Read-only inputs: data/engine_trades.jsonl (closed trades), data/engine_orders.jsonl (funnel),
data/micro_trades_shadow.jsonl (shadow BURST lane), docs/research_upgrade/trial_log.jsonl, and the MAINNET
trade tape that overlaps the ledger:
    data/ticks/<SYM>/<YYYYMMDD>.jsonl.gz            BTC / SOL / DOGE / BNB, 2026-10-01..10-14
    data/aggtrades_20261110/<SYM>/<YYYY-MM-DD>.npz  DOGE / XRP / 1000PEPE, 2026-10-12..11-08
    data/recordings/<SYM>/<YYYYMMDD-HH>.jsonl.gz    DOGE / XRP / 1000PEPE, 2026-11-10 02..12 UTC (aggTrade lines)

Additive decomposition of every closed AUTO trade (USDT, exact on the ledger):
    net          = gross - fee
    fill_markout = notional * markout(30 s, else 5 s, else 1 s) / 1e4   spread capture + adverse selection
    body         = gross - fill_markout                                 signal + exit management
Counterfactuals on the mainnet tape (relative moves from the mainnet price at the entry instant, so the
Demo-vs-mainnet basis cancels to first order; the replay of the ACTUAL exit on the same tape is the control):
    hold-to-bracket   the entry keeps its TP (tp_plan) and SL (TP distance / rr_gross) for up to 30 min
    stop follow-through   after an SL, did price reach the TP level within 30 min; placebo = the same
                          distances at random instants of the same tape days (driftless null)
    refusals          with-signal mainnet move 1/5/15/30/60 min after REFUSED / REGIME_REFUSED / SHADOW_KER rows,
                      and the micro trend bracket (SL 0.22 % / TP 0.46 %, 30 min) from that instant
Fee model of the counterfactuals: maker 2 bps, taker 5 bps + 1 bp slip (core/engine.py Profile).
No network, no writes except --out.
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import gzip
import json
import math
import random
import re
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MAKER_BPS, TAKER_BPS, SLIP_BPS = 2.0, 5.0, 1.0           # mainnet VIP0 model (core/engine.py Profile)
DEMO_MAKER_BPS, DEMO_TAKER_BPS = 2.0, 4.0                # what the Demo venue charged (ledger fee / leg notional)
REFUSAL_DEDUP_S = 300                                    # one refusal per symbol + side + group per 5 min
HOLD_MAX_S = 1800.0
FOLLOW_S = 1800.0
REFUSAL_H_MIN = (1, 5, 15, 30, 60)
POST_EXIT_MIN = (1, 5, 15, 30)
MICRO_SL_PCT, MICRO_TP_PCT = 0.22, 0.46
EARLY_EXITS = {'REGIME', 'FLOW', 'EJECT', 'EJECT-BE', 'EJECT-FAST', 'SOFT-LOCK', 'SOFT-PRESTOP', 'SOFT-BE',
               'SOFT-LOCK-LOSS', 'TIME', 'SOFT-PROFIT', 'SOFT-BE-LOSS', 'KILL', 'EJECT-BE-LOSS', 'NO_STOP'}
BRACKET_EXITS = {'TP', 'SL'}


# ----------------------------------------------------------------------------------------------- ledger maths
def load_jsonl(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def side_sign(side: str) -> int:
    return 1 if str(side).upper() == 'BUY' else -1


def markout_pick(mk) -> tuple[float | None, str | None]:
    """With-trade markout of the venue mid from the fill price, longest horizon available (30 s > 5 s > 1 s)."""
    if not isinstance(mk, dict):
        return None, None
    for h in ('30s', '5s', '1s'):
        v = mk.get(h)
        if isinstance(v, (list, tuple)) and v and v[0] is not None:
            return float(v[0]), h
    return None, None


def decompose(r: dict) -> dict:
    """Exact additive split of one ledger row; UNKNOWN parts stay None (never zero)."""
    qty, entry = float(r.get('qty') or 0.0), float(r.get('entry') or 0.0)
    notional = qty * entry
    gross, fee, pnl = (float(r[k]) if r.get(k) is not None else None for k in ('gross', 'fee', 'pnl'))
    mk_bps, mk_h = markout_pick(r.get('markout_bps'))
    fill_mk = notional * mk_bps / 1e4 if (mk_bps is not None and notional > 0) else None
    body = gross - fill_mk if (gross is not None and fill_mk is not None) else None
    exit_px = float(r.get('exit') or entry or 0.0)
    filled = qty * entry + qty * exit_px                     # notional of both legs
    fee_m = fee_t = None
    if fee is not None and filled > 0:
        # recorded fee = maker rate x maker notional + taker rate x taker notional (Demo: 2 / 4 bps, measured
        # on every (maker_fills, taker_fills) class of the ledger): solve for the taker notional exactly
        tn = (fee - DEMO_MAKER_BPS / 1e4 * filled) / ((DEMO_TAKER_BPS - DEMO_MAKER_BPS) / 1e4)
        tn = min(max(tn, 0.0), filled)
        fee_t = DEMO_TAKER_BPS / 1e4 * tn
        fee_m = max(fee - fee_t, 0.0)
    return {'notional': notional, 'gross': gross, 'fee': fee, 'net': pnl, 'fill_mk': fill_mk, 'mk_bps': mk_bps,
            'mk_h': mk_h, 'body': body, 'fee_maker_est': fee_m, 'fee_taker_est': fee_t}


def share(part: float, total: float) -> float | None:
    return None if not total else part / total


# ------------------------------------------------------------------------------------------------- tape access
class Tape:
    """Mainnet trade prints per symbol, loaded per file on demand: arrays of (t_ms, price)."""

    def __init__(self, root: Path = ROOT):
        self.root = root
        self.cache: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}

    def _files_for(self, sym: str, day: dt.date) -> list[tuple[str, Path]]:
        out = []
        p = self.root / 'data' / 'aggtrades_20261110' / sym / f'{day.isoformat()}.npz'
        if p.exists():
            return [('npz', p)]
        p = self.root / 'data' / 'ticks' / sym / f'{day.strftime("%Y%m%d")}.jsonl.gz'
        if p.exists():
            return [('ticks', p)]
        rec = self.root / 'data' / 'recordings' / sym
        if rec.exists():
            for h in range(24):
                q = rec / f'{day.strftime("%Y%m%d")}-{h:02d}.jsonl.gz'
                if q.exists():
                    out.append(('rec', q))
        return out

    @staticmethod
    def _read(kind: str, path: Path) -> tuple[np.ndarray, np.ndarray]:
        if kind == 'npz':
            with np.load(path) as z:
                return z['t'].astype(np.int64).copy(), z['p'].astype(np.float64).copy()
        ts, ps = [], []
        try:
            with gzip.open(path, 'rt', encoding='utf-8') as f:
                for line in f:
                    if kind == 'ticks':
                        d = json.loads(line)
                        ts.append(int(d['ts_ns']) // 1_000_000)
                        ps.append(float(d['p']))
                    elif '"aggTrade"' in line:
                        d = json.loads(line).get('d') or {}
                        if d.get('e') == 'aggTrade':
                            ts.append(int(d['T']))
                            ps.append(float(d['p']))
        except (EOFError, OSError, json.JSONDecodeError):
            pass                                            # recorder killed mid-write: keep the complete lines
        t, p = np.asarray(ts, dtype=np.int64), np.asarray(ps, dtype=np.float64)
        o = np.argsort(t, kind='stable')
        return t[o], p[o]

    def day(self, sym: str, day: dt.date) -> tuple[np.ndarray, np.ndarray] | None:
        key = (sym, day)
        if key not in self.cache:
            parts = [self._read(k, p) for k, p in self._files_for(sym, day)]
            parts = [x for x in parts if len(x[0])]
            self.cache[key] = ((np.concatenate([a for a, _ in parts]), np.concatenate([b for _, b in parts]))
                               if parts else (np.zeros(0, np.int64), np.zeros(0)))
        t, p = self.cache[key]
        return (t, p) if len(t) else None

    def window(self, sym: str, t0_ms: int, t1_ms: int) -> tuple[np.ndarray, np.ndarray] | None:
        d0 = dt.datetime.fromtimestamp(t0_ms / 1000, dt.timezone.utc).date()
        d1 = dt.datetime.fromtimestamp(t1_ms / 1000, dt.timezone.utc).date()
        ts, ps, d = [], [], d0
        while d <= d1:
            got = self.day(sym, d)
            if got is None:
                return None                                 # a hole in the window: UNKNOWN, not partial
            ts.append(got[0])
            ps.append(got[1])
            d += dt.timedelta(days=1)
        t, p = np.concatenate(ts), np.concatenate(ps)
        i0, i1 = np.searchsorted(t, t0_ms, 'left'), np.searchsorted(t, t1_ms, 'right')
        if i0 == 0 or i1 - i0 < 2 or t[-1] < t1_ms:
            return None                                     # no print before t0 or tape ends early
        return t[i0 - 1:i1], p[i0 - 1:i1]


# --------------------------------------------------------------------------------------------- path functions
def first_touch(t: np.ndarray, p: np.ndarray, sign: int, d_tp: float, d_sl: float, t_end_ms: int) -> tuple[str, float, int]:
    """From the first print (the entry reference), which barrier is crossed first by trade prints.
    Returns (outcome 'TP'|'SL'|'TIME', with-trade return as a fraction, time of the event)."""
    p0 = p[0]
    ret = sign * (p / p0 - 1.0)
    end = int(np.searchsorted(t, t_end_ms, 'right'))
    ret_w = ret[1:end]
    hit_tp = np.flatnonzero(ret_w >= d_tp)
    hit_sl = np.flatnonzero(ret_w <= -d_sl)
    i_tp = hit_tp[0] if len(hit_tp) else None
    i_sl = hit_sl[0] if len(hit_sl) else None
    if i_tp is None and i_sl is None:
        k = max(end - 1, 0)
        return 'TIME', float(ret[k]), int(t[k])
    if i_sl is None or (i_tp is not None and i_tp < i_sl):
        return 'TP', d_tp, int(t[1 + i_tp])
    return 'SL', -d_sl, int(t[1 + i_sl])


def bracket_net_bps(outcome: str, ret: float, entry_maker: bool) -> float:
    fee_in = MAKER_BPS if entry_maker else TAKER_BPS + SLIP_BPS
    fee_out = MAKER_BPS if outcome == 'TP' else TAKER_BPS + SLIP_BPS
    return ret * 1e4 - fee_in - fee_out


def reaches_after(t: np.ndarray, p: np.ndarray, p_ref: float, sign: int, level: float, t_from: int, t_to: int) -> bool:
    """Did the with-trade return from p_ref reach `level` between t_from and t_to?"""
    i0, i1 = np.searchsorted(t, t_from, 'left'), np.searchsorted(t, t_to, 'right')
    if i1 <= i0:
        return False
    return bool(np.any(sign * (p[i0:i1] / p_ref - 1.0) >= level))


def mean_se(xs: list[float]) -> dict:
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    if not xs:
        return {'n': 0, 'mean': None, 'se': None}
    m = st.fmean(xs)
    se = st.stdev(xs) / math.sqrt(len(xs)) if len(xs) > 1 else None
    return {'n': len(xs), 'mean': round(m, 4), 'se': round(se, 4) if se is not None else None}


def norm_reason(s: str) -> str:
    s = re.sub(r'[-+]?\d+(\.\d+)?', '#', str(s or '')).strip()
    head = s.split(':')[0].strip()
    head = re.sub(r'\b(BUY|SELL)\b', '', head).strip()
    return head[:48] or 'UNSPECIFIED'


# ------------------------------------------------------------------------------------------------------ study
def ledger_section(trades: list[dict]) -> dict:
    auto = [r for r in trades if r.get('tag') == 'AUTO']
    camp = defaultdict(list)
    for r in auto:
        camp['micro (Nov)' if r.get('micro') else 'swarm (Oct-Nov, demo-sized)'].append(r)
    camp['ALL AUTO'] = auto
    out = {}
    for name, rows in camp.items():
        dec = [decompose(r) for r in rows]
        net = sum(d['net'] for d in dec if d['net'] is not None)
        gross = sum(d['gross'] for d in dec if d['gross'] is not None)
        fee = sum(d['fee'] for d in dec if d['fee'] is not None)
        with_mk = [d for d in dec if d['fill_mk'] is not None]
        fm = sum(d['fill_mk'] for d in with_mk)
        body = sum(d['body'] for d in with_mk)
        fee_m = sum(d['fee_maker_est'] for d in dec if d['fee_maker_est'] is not None)
        fee_t = sum(d['fee_taker_est'] for d in dec if d['fee_taker_est'] is not None)
        bps_net = [d['net'] / d['notional'] * 1e4 for d in dec if d['net'] is not None and d['notional'] > 0]
        bps_gross = [d['gross'] / d['notional'] * 1e4 for d in dec if d['gross'] is not None and d['notional'] > 0]
        bps_fee = [d['fee'] / d['notional'] * 1e4 for d in dec if d['fee'] is not None and d['notional'] > 0]
        loss = -net
        by_reason = defaultdict(lambda: [0, 0.0, 0.0])
        for r, d in zip(rows, dec):
            g = by_reason[r.get('reason')]
            g[0] += 1
            g[1] += d['net'] or 0.0
            g[2] += d['gross'] or 0.0
        out[name] = {
            'n': len(rows), 'wins': sum(1 for d in dec if (d['net'] or 0) > 0),
            'net_usdt': round(net, 5), 'gross_usdt': round(gross, 5), 'fee_usdt': round(fee, 5),
            'fee_maker_est_usdt': round(fee_m, 5), 'fee_taker_est_usdt': round(fee_t, 5),
            'n_with_markout': len(with_mk),
            'fill_markout_usdt': round(fm, 5), 'body_usdt_on_markout_rows': round(body, 5),
            'gross_usdt_on_markout_rows': round(sum(d['gross'] for d in with_mk), 5),
            'fee_usdt_on_markout_rows': round(sum(d['fee'] or 0 for d in with_mk), 5),
            'net_bps': mean_se(bps_net), 'gross_bps': mean_se(bps_gross), 'fee_bps': mean_se(bps_fee),
            'mk_bps': mean_se([d['mk_bps'] for d in dec]),
            'share_of_loss_fee': share(fee, loss) if loss > 0 else None,
            'by_reason': {k: {'n': v[0], 'net': round(v[1], 5), 'gross': round(v[2], 5)}
                          for k, v in sorted(by_reason.items(), key=lambda kv: kv[1][1])},
        }
    return out


def counterfactual_section(trades: list[dict], tape: Tape, rng: random.Random, n_placebo: int = 400) -> dict:
    """Bracket-free exit audit (only 3 of 131 rows carry tp_plan): with-trade MAINNET move AFTER each exit.
    A positive mean after early exits = money left on the table (premature); <= 0 = the exit saved money.
    SL rows are compared with a placebo: the with-side move over the same horizons from random instants."""
    rows, skipped = [], Counter()
    for r in trades:
        if r.get('tag') != 'AUTO':
            continue
        hold = float(r.get('hold_s') or 0.0)
        t_exit = int(float(r['t']) * 1000)
        t_entry = t_exit - int(hold * 1000)
        entry = float(r.get('entry') or 0)
        if not entry or not r.get('qty'):
            skipped['no entry / qty'] += 1
            continue
        w = tape.window(r['symbol'], t_entry, t_exit + max(POST_EXIT_MIN) * 60_000 + 1000)
        if w is None:
            skipped['no mainnet tape'] += 1
            continue
        t, p = w
        sign = side_sign(r.get('side'))
        i_x = max(int(np.searchsorted(t, t_exit, 'right')) - 1, 0)
        p_x = p[i_x]
        post = {}
        for h in POST_EXIT_MIN:
            i = max(int(np.searchsorted(t, t_exit + h * 60_000, 'right')) - 1, 0)
            post[h] = sign * (p[i] / p_x - 1.0) * 1e4
        notional = float(r['qty']) * entry
        gross_bps_ledger = float(r['gross']) / notional * 1e4 if r.get('gross') is not None else None
        rows.append({'symbol': r['symbol'], 't_exit': t_exit, 'reason': r.get('reason'), 'micro': bool(r.get('micro')),
                     'side': sign, 'notional': notional, 'post_bps': post,
                     'mainnet_gross_bps': sign * (p_x / p[0] - 1.0) * 1e4, 'ledger_gross_bps': gross_bps_ledger,
                     'pnl': float(r.get('pnl') or 0.0)})

    def group(sel):
        g = {'n': len(sel)}
        for h in POST_EXIT_MIN:
            usd = [x['post_bps'][h] * x['notional'] / 1e4 for x in sel]
            g[f'post_{h}m_bps'] = mean_se([x['post_bps'][h] for x in sel])
            g[f'post_{h}m_usdt'] = round(sum(usd), 5)
            if len(usd) > 1:                                # a few demo-sized positions dominate the sum: bootstrap it
                bs = sorted(sum(rng.choice(usd) for _ in usd) for _ in range(2000))
                g[f'post_{h}m_usdt_ci90'] = [round(bs[100], 4), round(bs[1899], 4)]
        return g

    early = [x for x in rows if x['reason'] in EARLY_EXITS]
    early_loss = [x for x in early if x['pnl'] < 0]
    sl = [x for x in rows if x['reason'] == 'SL']
    other = [x for x in rows if x['reason'] not in EARLY_EXITS and x['reason'] != 'SL']
    # placebo: same symbol / side / day, random instants, same horizons (driftless null and its spread)
    plc = defaultdict(list)
    for x in sl + early_loss:
        d0 = dt.datetime.fromtimestamp(x['t_exit'] / 1000, dt.timezone.utc).date()
        got = tape.day(x['symbol'], d0)
        if got is None:
            continue
        t_all, p_all = got
        lo, hi = int(t_all[0]), int(t_all[-1]) - max(POST_EXIT_MIN) * 60_000
        if hi <= lo:
            continue
        for _ in range(n_placebo):
            t0 = rng.randint(lo, hi)
            i0 = max(int(np.searchsorted(t_all, t0, 'right')) - 1, 0)
            for h in POST_EXIT_MIN:
                i = max(int(np.searchsorted(t_all, t0 + h * 60_000, 'right')) - 1, 0)
                plc[h].append(x['side'] * (p_all[i] / p_all[i0] - 1.0) * 1e4)
    basis_err = [x['mainnet_gross_bps'] - x['ledger_gross_bps'] for x in rows if x['ledger_gross_bps'] is not None]
    by_reason = {}
    for reason in sorted({x['reason'] for x in rows}):
        by_reason[reason] = group([x for x in rows if x['reason'] == reason])
    return {
        'n_rows': len(rows), 'skipped': dict(skipped),
        'replay_check_mainnet_minus_ledger_gross_bps': mean_se(basis_err),
        'replay_check_abs_err_bps_median': round(st.median([abs(e) for e in basis_err]), 3) if basis_err else None,
        'early_exits': group(early), 'early_exits_losing': group(early_loss), 'stop_losses': group(sl),
        'other_exits': group(other),
        'placebo_same_side_random_instants': {f'{h}m': mean_se(plc[h]) for h in POST_EXIT_MIN},
        'by_reason': by_reason,
    }


def refusal_section(orders: list[dict], tape: Tape) -> dict:
    keep = [r for r in orders if r.get('outcome') in ('REFUSED', 'REGIME_REFUSED', 'SHADOW_KER') and r.get('side')
            in ('BUY', 'SELL') and r.get('symbol')]
    groups = defaultdict(lambda: {'n': 0, 'n_tape': 0, 'n_dup': 0, 'moves': defaultdict(list), 'bracket_net': [],
                                  'outcomes': Counter()})
    last_kept: dict[tuple, float] = {}
    for r in sorted(keep, key=lambda x: float(x['t'])):
        key = f"{r['outcome']}: {norm_reason(r.get('error') or r.get('why'))}"
        g = groups[key]
        g['n'] += 1
        dk = (key, r['symbol'], r['side'])
        if float(r['t']) - last_kept.get(dk, -1e18) < REFUSAL_DEDUP_S:
            g['n_dup'] += 1                                 # same signal re-logged: overlapping windows are not new evidence
            continue
        last_kept[dk] = float(r['t'])
        t0 = int(float(r['t']) * 1000)
        w = tape.window(r['symbol'], t0, t0 + max(REFUSAL_H_MIN) * 60_000 + 1000)
        if w is None:
            continue
        g['n_tape'] += 1
        t, p = w
        sign = side_sign(r['side'])
        for h in REFUSAL_H_MIN:
            i = max(int(np.searchsorted(t, t0 + h * 60_000, 'right')) - 1, 0)
            g['moves'][h].append(sign * (p[i] / p[0] - 1.0) * 1e4)
        oc, ret, _ = first_touch(t, p, sign, MICRO_TP_PCT / 100, MICRO_SL_PCT / 100, t0 + int(HOLD_MAX_S * 1000))
        g['outcomes'][oc] += 1
        g['bracket_net'].append(bracket_net_bps(oc, ret, True))
    out = {}
    for k, g in sorted(groups.items(), key=lambda kv: -kv[1]['n']):
        out[k] = {'n': g['n'], 'n_dedup_dropped': g['n_dup'], 'n_tape': g['n_tape'],
                  'with_signal_move_bps': {f'{h}m': mean_se(g['moves'][h]) for h in REFUSAL_H_MIN},
                  'micro_bracket_net_bps': mean_se(g['bracket_net']), 'bracket_outcomes': dict(g['outcomes'])}
    allb = [x for g in groups.values() for x in g['bracket_net']]
    all60 = [x for g in groups.values() for x in g['moves'][60]]
    return {'groups': out, 'pooled_micro_bracket_net_bps': mean_se(allb), 'pooled_move_60m_bps': mean_se(all60),
            'n_rows': len(keep)}


def shadow_section(shadow: list[dict]) -> dict:
    by = defaultdict(list)
    for r in shadow:
        by[r.get('lane')].append(r)
    return {lane: {'n': len(v), 'gross_bps': mean_se([r.get('gross_bps') for r in v]),
                   'net_bps': mean_se([r.get('net_bps') for r in v]),
                   'win_rate': round(sum(1 for r in v if (r.get('net_bps') or 0) > 0) / len(v), 4),
                   'net_usdt_at_shadow_size': round(sum(r.get('net_usd') or 0 for r in v), 4),
                   'fees_model': Counter(r.get('fees_model') for r in v).most_common(1)[0][0]}
            for lane, v in by.items()}


def trial_section(trials: list[dict]) -> dict:
    out = defaultdict(list)
    for r in trials:
        out[r.get('kind')].append({'name': r.get('name'), 'mean_diff': r.get('mean_diff'), 'unit': r.get('unit'),
                                   'win_rate': r.get('win_rate'), 'ci95': r.get('ci95'),
                                   'decision': r.get('decision') or r.get('verdict') or r.get('chosen')})
    return dict(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', default=str(ROOT / 'data' / 'autopsy_2026.json'))
    ap.add_argument('--seed', type=int, default=20261113)
    ap.add_argument('--placebo', type=int, default=400)
    a = ap.parse_args(argv)
    trades = load_jsonl(ROOT / 'data' / 'engine_trades.jsonl')
    orders = load_jsonl(ROOT / 'data' / 'engine_orders.jsonl')
    shadow = load_jsonl(ROOT / 'data' / 'micro_trades_shadow.jsonl')
    trials = load_jsonl(ROOT / 'docs' / 'research_upgrade' / 'trial_log.jsonl')
    tape = Tape()
    res = {
        'generated_utc': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
        'inputs': {'trades': len(trades), 'orders': len(orders), 'shadow': len(shadow), 'trials': len(trials)},
        'ledger': ledger_section(trades),
        'counterfactual': counterfactual_section(trades, tape, random.Random(a.seed), a.placebo),
        'refusals': refusal_section(orders, tape),
        'shadow_lanes': shadow_section(shadow),
        'trials': trial_section(trials),
        'funnel': dict(Counter(r.get('outcome') for r in orders)),
    }
    Path(a.out).write_text(json.dumps(res, indent=1, default=str), encoding='utf-8')
    led = res['ledger']['ALL AUTO']
    cf = res['counterfactual']
    print(f"AUTO trades {led['n']}  net {led['net_usdt']:+.4f} U  gross {led['gross_usdt']:+.4f}  fees {led['fee_usdt']:.4f}"
          f"  (maker est {led['fee_maker_est_usdt']:.4f} / taker est {led['fee_taker_est_usdt']:.4f})")
    print(f"fill markout {led['fill_markout_usdt']:+.4f} U on {led['n_with_markout']} rows; body {led['body_usdt_on_markout_rows']:+.4f}")
    print(f"exit audit rows {cf['n_rows']} skipped {cf['skipped']}; replay check {cf['replay_check_mainnet_minus_ledger_gross_bps']}")
    for k in ('early_exits', 'early_exits_losing', 'stop_losses', 'other_exits'):
        g = cf[k]
        print(f"  {k:20s} n={g['n']:3d} post30m {g['post_30m_bps']} usdt {g['post_30m_usdt']:+.4f}")
    print(f"  placebo {cf['placebo_same_side_random_instants']['30m']}")
    print(f"refusals pooled bracket {res['refusals']['pooled_micro_bracket_net_bps']} move60 {res['refusals']['pooled_move_60m_bps']}")
    print(f"wrote {a.out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
