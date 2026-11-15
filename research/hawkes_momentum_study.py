"""HWK-1: binned Hawkes volume-shock momentum on top movers, 10 USDT base (2026-11-15). Offline, public data, no orders.

    python -m research.hawkes_momentum_study daily      # 1d bars of every USDT perpetual (public REST)
    python -m research.hawkes_momentum_study universe   # top 5 gainers / losers per day (no outcome is read)
    python -m research.hawkes_momentum_study klines     # 1 m daily zips of D-1, D, D+1 (data.binance.vision, SHA-256)
    python -m research.hawkes_momentum_study matrix     # zips -> data/hawkes/matrix.npz
    python -m research.hawkes_momentum_study run        # T1, T2, T3, growth -> data/hawkes_momentum_study.json

Pre-registration: docs/research_upgrade/plan_hawkes_momentum_10u_2026_11.md (frozen before the `klines` stage).

The Hawkes process is observed in 1-minute bins (trade count per kline) and fitted as an INAR process by conditional
least squares on the previous day; the trigger is that intensity against a reference plus an hourly volume surge on
yesterday's top 5 gainers / losers; the trade is a taker entry with a stop / target bracket on 1 m bars.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import random
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PLAN = 'docs/research_upgrade/plan_hawkes_momentum_10u_2026_11.md'
DATA = ROOT / 'data' / 'hawkes'
DAILY = DATA / 'daily.json'
UNIVERSE = DATA / 'universe.json'
KLINES = DATA / 'klines_1m'
MANIFEST = DATA / 'klines_1m.manifest.jsonl'
MATRIX = DATA / 'matrix.npz'
FUNDING = ROOT / 'data' / 'funding_universe'
OUT = ROOT / 'data' / 'hawkes_momentum_study.json'
FAPI = 'https://fapi.binance.com'
VISION = 'https://data.binance.vision/data/futures/um/daily/klines'

MIN_MS, DAY_MS = 60_000, 86_400_000
L_DAY, L3 = 1440, 4320                          # one pair = D-1, D, D+1 on one minute axis; day D starts at L_DAY
EXCLUDE = ('BTCUSDT', 'ETHUSDT', 'USDCUSDT', 'FDUSDUSDT', 'TUSDUSDT', 'USDPUSDT', 'BUSDUSDT')
TOP_K, MIN_QV, MIN_AGE_DAYS = 5, 2e7, 14
MAX_MISSING = 0.05
DAILY_START = '2024-01-01'
PARTS = {'TRAIN': ('2024-02-01', '2025-12-31'), 'HOLDOUT': ('2026-01-01', '2026-09-30'),
         'FRESH': ('2026-10-01', '2026-11-13')}
SEED = 20261115


def ms(day: str) -> int:
    return int(datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp() * 1000)


def day_of(t_ms: int) -> str:
    return datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d')


def part_of(day: str) -> Optional[str]:
    for name, (lo, hi) in PARTS.items():
        if lo <= day <= hi:
            return name
    return None


# ================================================================================================ data: symbols, daily
def symbols(info: dict) -> list:
    """USDT-quoted COIN perpetuals of an exchangeInfo snapshot, any status, minus the plan's exclusions."""
    return sorted(s['symbol'] for s in info.get('symbols', [])
                  if s.get('quoteAsset') == 'USDT' and s.get('contractType') == 'PERPETUAL'
                  and s.get('underlyingType') == 'COIN' and s['symbol'] not in EXCLUDE)


def http_get(url: str, tries: int = 4, timeout: int = 60) -> Optional[bytes]:
    """Body, or None on 400 / 404 (not served: UNKNOWN). Other failures are retried, then raised."""
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'research'}),
                                        timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return None
            if k == tries - 1:
                raise
            time.sleep(10 * (k + 1) if e.code in (418, 429) else 2 * (k + 1))
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(2 * (k + 1))
    return None


def fetch_daily(syms: list, out: Path = DAILY, pause: float = 0.3, log=print) -> dict:
    """{symbol: [[t_ms, open, high, low, close, quote_volume], ...]} of complete UTC days; resumable."""
    raw = json.loads(out.read_text()) if out.exists() else {}
    today = ms(datetime.now(timezone.utc).strftime('%Y-%m-%d'))
    out.parent.mkdir(parents=True, exist_ok=True)
    todo = [s for s in syms if s not in raw]
    for i, s in enumerate(todo):
        body = http_get(f'{FAPI}/fapi/v1/klines?symbol={quote(s)}&interval=1d&startTime={ms(DAILY_START)}&limit=1500')
        rows = json.loads(body) if body else []
        raw[s] = [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7])]
                  for r in rows if int(r[0]) < today]
        time.sleep(pause)                                    # weight 10 per call: 200 calls / min of 2 400
        if i % 50 == 49 or i == len(todo) - 1:
            out.write_text(json.dumps(raw))
            log(f'  daily {i + 1}/{len(todo)}')
    return raw


def build_universe(daily: dict, start: str, end: str, k: int = TOP_K, min_qv: float = MIN_QV,
                   min_age: int = MIN_AGE_DAYS) -> dict:
    """{day D: [{'sym', 'tag' G|L, 'r', 'qv'}, ...]} from information of D-1 and earlier only (plan section 1)."""
    by_day, first = {}, {}
    for s, rows in daily.items():
        if s in EXCLUDE or not rows:
            continue
        first[s] = rows[0][0]
        for t, _o, _h, _l, c, qv in rows:
            by_day.setdefault(t, {})[s] = (c, qv)
    out, d = {}, ms(start)
    while d <= ms(end):
        y1, y2 = by_day.get(d - DAY_MS, {}), by_day.get(d - 2 * DAY_MS, {})
        cand = []
        for s, (c1, qv1) in y1.items():
            if s in y2 and y2[s][0] > 0 and c1 > 0 and qv1 >= min_qv and first[s] <= d - min_age * DAY_MS:
                cand.append((c1 / y2[s][0] - 1.0, s, qv1))
        if len(cand) >= 2 * k:
            cand.sort()
            pick = [(x, 'L') for x in cand[:k]] + [(x, 'G') for x in cand[-k:]]
            out[day_of(d)] = [{'sym': s, 'tag': tag, 'r': r, 'qv': qv} for (r, s, qv), tag in pick]
        d += DAY_MS
    return out


def pairs_of(universe: dict) -> list:
    """[(symbol, day D, tag, r)] sorted by symbol then day."""
    return sorted((x['sym'], d, x['tag'], x['r']) for d, rows in universe.items() for x in rows)


def files_of(universe: dict) -> list:
    """Unique (symbol, day) files: D-1, D and D+1 of every pair."""
    need = set()
    for s, d, _tag, _r in pairs_of(universe):
        t = ms(d)
        need.update((s, day_of(t + k * DAY_MS)) for k in (-1, 0, 1))
    return sorted(need)


# ================================================================================================ data: 1 m zips
def zip_name(sym: str, day: str) -> str:
    return f'{sym}-1m-{day}.zip'


def read_manifest(path: Path = MANIFEST) -> dict:
    """Rows of the manifest and of its shard files (`<stem>.<i>.jsonl`), a hash winning over a 404 of the same name."""
    out = {}
    for p in sorted(path.parent.glob(path.stem + '*' + path.suffix)):
        for line in p.read_text(encoding='utf-8').splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get('sha256') or r['name'] not in out:
                    out[r['name']] = r
    return out


def fetch_klines(universe: dict, out: Path = KLINES, manifest: Path = MANIFEST, workers: int = 16,
                 retry_missing: bool = False, shard: tuple = (0, 1), log=print) -> dict:
    """Daily 1 m zips, each checked against the archive's own SHA-256; the manifest records hashes and 404s.
    `shard` (i, n) takes every n-th open job from i and writes its own manifest file, so n processes can share a run."""
    out.mkdir(parents=True, exist_ok=True)
    seen, lock, count = read_manifest(manifest), threading.Lock(), {'done': 0, 'have': 0, 'missing': 0, 'bytes': 0}
    jobs = []
    for s, d in files_of(universe):
        r = seen.get(zip_name(s, d))
        if r and (r.get('sha256') and (out / r['name']).exists() or r.get('missing') and not retry_missing):
            count['have' if r.get('sha256') else 'missing'] += 1
            continue
        jobs.append((s, d))
    jobs = jobs[shard[0]::shard[1]]
    log(f'{len(jobs)} files to fetch, {count["have"]} on disk, {count["missing"]} known missing')
    if shard[1] > 1:
        manifest = manifest.with_name(f'{manifest.stem}.{shard[0]}{manifest.suffix}')
    fh = manifest.open('a', encoding='utf-8', newline='\n')

    def work(job):
        s, d = job
        name = zip_name(s, d)
        url = f'{VISION}/{quote(s)}/1m/{quote(name)}'
        chk = http_get(url + '.CHECKSUM')
        blob = http_get(url) if chk else None
        if blob is None:
            row = {'name': name, 'missing': True}
        else:
            got = hashlib.sha256(blob).hexdigest()
            if got != chk.decode().split()[0]:
                raise RuntimeError(f'checksum mismatch {name}')
            tmp = out / (name + '.part')
            tmp.write_bytes(blob)
            tmp.replace(out / name)
            row = {'name': name, 'sha256': got, 'bytes': len(blob)}
        with lock:
            fh.write(json.dumps(row, ensure_ascii=False) + '\n')
            count['missing' if blob is None else 'done'] += 1
            count['bytes'] += 0 if blob is None else len(blob)
            n = count['done'] + count['missing']
            if n % 100 == 0:
                fh.flush()
            if n % 1000 == 0:
                log(f'  {n}/{len(jobs)} files, {count["bytes"] / 1e6:.0f} MB')

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(work, jobs))
    fh.close()
    return count


def parse_day(blob: bytes, day: str) -> Optional[dict]:
    """One daily zip -> minute arrays of length 1440 (o, h, l, c, qv, n, tbq) and the mask of present minutes."""
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        txt = zf.read(zf.namelist()[0]).decode()
    if txt[:1] not in '0123456789':
        txt = txt[txt.index('\n') + 1:]
    if not txt.strip():
        return None
    a = np.loadtxt(io.StringIO(txt), delimiter=',', usecols=(0, 1, 2, 3, 4, 7, 8, 10), ndmin=2)
    t = a[:, 0] / 1000.0 if a[0, 0] > 1e14 else a[:, 0]             # archives after 2025 may stamp microseconds
    i = np.rint((t - ms(day)) / MIN_MS).astype(np.int64)
    ok = (i >= 0) & (i < L_DAY)
    i, a = i[ok], a[ok]
    out = {k: np.full(L_DAY, np.nan) for k in ('o', 'h', 'l', 'c')}
    out.update({k: np.zeros(L_DAY) for k in ('qv', 'n', 'tbq')})
    for j, k in enumerate(('o', 'h', 'l', 'c', 'qv', 'n', 'tbq')):
        out[k][i] = a[:, j + 1]
    out['ok'] = np.zeros(L_DAY, dtype=bool)
    out['ok'][i] = True
    return out


def fill_gaps(o, h, l, c):
    """Missing minutes carry the last close as a flat bar (in place); leading gaps take the first open."""
    last = math.nan
    for j in range(len(c)):
        if c[j] == c[j]:
            last = c[j]
        elif last == last:
            o[j] = h[j] = l[j] = c[j] = last
    first = np.flatnonzero(c == c)
    if first.size and first[0] > 0:
        o[:first[0]] = h[:first[0]] = l[:first[0]] = c[:first[0]] = o[first[0]]


def build_matrix(universe: dict, klines: Path = KLINES, max_missing: float = MAX_MISSING, log=print) -> dict:
    """Every usable pair on one 4 320-minute axis. Prices are divided by `base` (first close of D-1) and kept as
    float32 (relative precision 6e-8); `n_bars` is 2 880 when D+1 is not published."""
    cache, rows, meta, dropped = {}, [], [], {'no_file': 0, 'gaps': 0}

    def day(sym, d):
        key = (sym, d)
        if key not in cache:
            p = klines / zip_name(sym, d)
            cache[key] = parse_day(p.read_bytes(), d) if p.exists() else None
            if len(cache) > 4000:
                for k in list(cache)[:2000]:
                    del cache[k]
        return cache[key]

    for i, (s, d, tag, r) in enumerate(pairs_of(universe)):
        t = ms(d)
        parts = [day(s, day_of(t + k * DAY_MS)) for k in (-1, 0, 1)]
        if parts[0] is None or parts[1] is None:
            dropped['no_file'] += 1
            continue
        if min(parts[0]['ok'].mean(), parts[1]['ok'].mean()) < 1.0 - max_missing:
            dropped['gaps'] += 1
            continue
        use = parts if parts[2] is not None else parts[:2]
        cat = {k: np.concatenate([p[k] for p in use]) for k in ('o', 'h', 'l', 'c', 'qv', 'n', 'tbq', 'ok')}
        fill_gaps(cat['o'], cat['h'], cat['l'], cat['c'])
        base = float(cat['c'][0])
        row = {k: np.zeros(L3, dtype=np.float32) for k in ('o', 'h', 'l', 'c', 'qv', 'n', 'tbq')}
        for k in ('o', 'h', 'l', 'c'):
            row[k][:len(cat[k])] = cat[k] / base
            row[k][len(cat[k]):] = row[k][len(cat[k]) - 1]
        for k in ('qv', 'n', 'tbq'):
            row[k][:len(cat[k])] = cat[k]
        rows.append(row)
        meta.append((s, d, tag, r, base, len(cat['c'])))
        if i % 1000 == 999:
            log(f'  matrix {i + 1} pairs read, {len(rows)} kept')
    mx = {k: np.stack([r[k] for r in rows]) for k in ('o', 'h', 'l', 'c', 'qv', 'n', 'tbq')}
    mx.update(sym=np.array([m[0] for m in meta]), day=np.array([m[1] for m in meta]),
              tag=np.array([m[2] for m in meta]), r=np.array([m[3] for m in meta], dtype=np.float64),
              base=np.array([m[4] for m in meta], dtype=np.float64),
              n_bars=np.array([m[5] for m in meta], dtype=np.int32),
              dropped=np.array([dropped['no_file'], dropped['gaps']], dtype=np.int64))
    return mx


def load_matrix(path: Path = MATRIX) -> dict:
    with np.load(path, allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


# ================================================================================================ binned Hawkes
D0, D1 = L_DAY + 60, 2 * L_DAY                  # decision minutes of day D on the pair axis: D0 <= s < D1
CAL_LO, CAL_HI = 60, L_DAY                      # calibration window on D-1
MU_FLOOR, NBAR_MIN = 0.05, 1.0


def beta_of(hl: float) -> float:
    return math.log(2.0) / hl


def decay_state(n: np.ndarray, hl: float) -> np.ndarray:
    """A[..., s] = exp(-beta) * (A[..., s-1] + n[..., s-1]) with A[..., 0] = 0, along the minute axis."""
    from scipy.signal import lfilter
    q = math.exp(-beta_of(hl))
    return lfilter([0.0, q], [1.0, -q], np.asarray(n, dtype=np.float64), axis=-1)


def cls_fit(n: np.ndarray, a: np.ndarray, lo: int = CAL_LO, hi: int = CAL_HI) -> tuple:
    """Conditional least squares of N_s on (1, A_s) over minutes lo..hi-1 -> (mu_hat, alpha_hat, Nbar)."""
    x, y = np.asarray(a[..., lo:hi], dtype=np.float64), np.asarray(n[..., lo:hi], dtype=np.float64)
    xm, ym = x.mean(-1), y.mean(-1)
    var = ((x - xm[..., None]) ** 2).mean(-1)
    cov = ((x - xm[..., None]) * (y - ym[..., None])).mean(-1)
    alpha = np.where(var > 0, cov / np.where(var > 0, var, 1.0), 0.0)
    return ym - alpha * xm, alpha, ym


def intensity(n: np.ndarray, hl: float) -> dict:
    """Fit on D-1, then lam_next[..., s] = intensity of minute s+1 from the counts up to s (no later count enters)."""
    n = np.asarray(n[..., :D1], dtype=np.float64)
    a = decay_state(n, hl)
    mu, alpha, nbar = cls_fit(n, a)
    ok = (alpha > 0) & (nbar >= NBAR_MIN)
    mu_f = np.maximum(mu, MU_FLOOR * nbar)
    q = math.exp(-beta_of(hl))
    lam = mu_f[..., None] + alpha[..., None] * q * (a + n)
    return {'lam_next': lam, 'mu': mu, 'mu_f': mu_f, 'alpha': alpha, 'nbar': nbar, 'ok': ok,
            'n_hat': alpha / (math.exp(beta_of(hl)) - 1.0)}


def volume_ratio(qv: np.ndarray) -> tuple:
    """(V60, V60 / mean hourly volume of the 24 h that end 60 minutes earlier); NaN where the history is short."""
    qv = np.asarray(qv, dtype=np.float64)
    c = np.concatenate([np.zeros(qv.shape[:-1] + (1,)), np.cumsum(qv, axis=-1)], axis=-1)
    n = qv.shape[-1]
    v60, ratio = np.full(qv.shape, np.nan), np.full(qv.shape, np.nan)
    v60[..., 59:] = c[..., 60:n + 1] - c[..., 0:n - 59]
    if n > 1499:
        ref = (c[..., 1440:n - 59] - c[..., 0:n - 1499]) / 24.0
        with np.errstate(divide='ignore', invalid='ignore'):
            ratio[..., 1499:] = np.where(ref > 0, v60[..., 1499:] / ref, np.nan)
    return v60, ratio


def direction(c: np.ndarray, tbq: np.ndarray, qv: np.ndarray, w: int, mode: str) -> np.ndarray:
    """Side (+1 / -1 / 0) at each minute s from the last w minutes: PRICE, CVD, or BOTH (PRICE when CVD agrees)."""
    c = np.asarray(c, dtype=np.float64)
    px = np.zeros(c.shape, dtype=np.int8)
    px[..., w:] = np.sign(c[..., w:] - c[..., :-w]).astype(np.int8)
    if mode == 'PRICE':
        return px
    d = 2.0 * np.asarray(tbq, dtype=np.float64) - np.asarray(qv, dtype=np.float64)
    cs = np.concatenate([np.zeros(d.shape[:-1] + (1,)), np.cumsum(d, axis=-1)], axis=-1)
    cvd = np.zeros(c.shape, dtype=np.int8)
    cvd[..., w - 1:] = np.sign(cs[..., w:] - cs[..., :-w]).astype(np.int8)
    if mode == 'CVD':
        return cvd
    if mode == 'BOTH':
        return np.where(px == cvd, px, 0).astype(np.int8)
    raise ValueError(f'unknown direction {mode}')


# ================================================================================================ configuration
@dataclass(frozen=True)
class Cfg:
    hl: float = 5
    theta: float = 3.0
    ref: str = 'BAR'
    kappa: float = 3.0
    w: int = 15
    dir: str = 'BOTH'
    filt: str = 'ALL'
    t: float = 400.0
    rr: float = 2.5
    h: int = 240
    cooldown: int = 60
    arm: float = 0.0
    dist: float = 1.0

    @property
    def s(self) -> float:
        return self.t / self.rr

    @property
    def name(self) -> str:
        tail = '' if self.arm <= 0 else f'|R{self.arm:g}/{self.dist:g}'
        return (f'{self.ref}|hl{self.hl:g}|th{self.theta:g}|k{self.kappa:g}|W{self.w}|{self.dir}|{self.filt}|'
                f'{self.t:g}/{self.s:g}|H{self.h}|cd{self.cooldown}{tail}')


F0_REF, F0_BRACKETS, F0_DIR = ('MU', 'BAR'), ((300.0, 120.0), (400.0, 160.0), (600.0, 240.0)), ('BOTH', 'PRICE')
GRID = {'hl': (1, 2, 5, 10, 20, 30), 'theta': (2, 3, 4, 6, 8), 'ref': ('MU', 'BAR'), 'kappa': (2, 3, 4, 5),
        'w': (5, 15, 30, 60), 'dir': ('BOTH', 'PRICE', 'CVD'), 'filt': ('ALL', 'WITH', 'AGAINST'),
        't': (300, 400, 500, 600), 'rr': (2.5, 3.0, 4.0), 'h': (60, 120, 240, 480, 720),
        'cooldown': (30, 60, 120, 240), 'arm': (0.0, 1.0, 1.5, 2.0), 'dist': (0.5, 1.0, 1.5)}
ROUNDS = 30
MIN_TRAIN_N, MIN_HOLDOUT_N, MIN_FRESH_N = 200, 100, 20
MAKER, TAKER, SLIP = 2.0, 5.0, 3.0
SCHEDULES = {'BASE': (MAKER, TAKER, SLIP, False), 'SLIP1.5': (MAKER, TAKER, 1.5, False),
             'SLIP10': (MAKER, TAKER, 10.0, False), 'TP_TAKER': (MAKER, TAKER, SLIP, True),
             'ZERO': (0.0, 0.0, 0.0, False)}
STOP, TP, TIME, EOD = 0, 1, 2, 3
REASONS = ('STOP', 'TP', 'TIME', 'EOD')


def f0_cells() -> list:
    return [Cfg(ref=r, t=t, rr=t / s, dir=d) for r in F0_REF for t, s in F0_BRACKETS for d in F0_DIR]


def mutate(cfg: Cfg, rng: random.Random) -> Cfg:
    """One parameter moved to another grid value; a ratchet distance is not drawn while the ratchet is off."""
    while True:
        key = rng.choice(sorted(GRID))
        if key == 'dist' and cfg.arm <= 0:
            continue
        return replace(cfg, **{key: rng.choice([v for v in GRID[key] if v != getattr(cfg, key)])})


# ================================================================================================ trade on 1 m bars
def sim_trade(o, h, l, c, j0: int, side: int, t: float, s: float, hold: int, arm: float = 0.0, dist: float = 1.0,
              n_bars: Optional[int] = None) -> tuple:
    """Taker entry at o[j0]; -> (exit bar, gross bps, reason). Bar by bar: the stop first (at the level, or at the
    open when the bar opens beyond it), then the target; a bar that reaches both is a stop; else the close of bar
    j0 + hold - 1 (TIME) or of the last bar on disk (EOD). The ratchet uses the best price of completed bars only."""
    n_bars = len(c) if n_bars is None else int(n_bars)
    j1 = min(j0 + hold, n_bars)
    e = float(o[j0])
    k = side * 1e4 / e
    op = (np.asarray(o[j0:j1], dtype=np.float64) - e) * k
    hi = (np.asarray(h[j0:j1], dtype=np.float64) - e) * k
    lo = (np.asarray(l[j0:j1], dtype=np.float64) - e) * k
    up, dn = (hi, lo) if side > 0 else (lo, hi)
    stop = np.full(j1 - j0, -float(s))
    if arm > 0:
        prev = np.concatenate(([-np.inf], np.maximum.accumulate(up)[:-1]))
        stop = np.where(prev >= arm * s, np.maximum(-s, prev - dist * s), -float(s))
    hit_s, hit_t = dn <= stop, up >= t
    js = int(np.argmax(hit_s)) if hit_s.any() else -1
    jt = int(np.argmax(hit_t)) if hit_t.any() else -1
    if js >= 0 and (jt < 0 or js <= jt):
        return j0 + js, float(min(stop[js], op[js])), STOP
    if jt >= 0:
        return j0 + jt, float(t), TP
    return j1 - 1, float((float(c[j1 - 1]) - e) * k), TIME if j1 == j0 + hold else EOD


def cost_bps(reason, sched: str = 'BASE') -> np.ndarray:
    """Round-trip fees and slippage in bps of notional: taker entry; the target is a maker unless the schedule says taker."""
    maker, taker, slip, tp_taker = SCHEDULES[sched]
    reason = np.asarray(reason)
    exit_ = np.where((reason == TP) & (not tp_taker), maker, taker + slip)
    return (taker + slip) + exit_


class Funding:
    """Funding prints per symbol in bps ([[t_ms, rate_bps], ...]); a long pays a positive print."""

    def __init__(self, root: Path = FUNDING, ext: Optional[dict] = None):
        self.root, self.ext, self._s = root, ext or {}, {}

    def series(self, sym: str):
        if sym not in self._s:
            p = self.root / f'fr_{sym}.json'
            rows = json.loads(p.read_text()) if p.exists() else []
            last = rows[-1][0] if rows else -1
            rows = rows + [r for r in self.ext.get(sym, []) if r[0] > last]
            self._s[sym] = (np.array([r[0] for r in rows], dtype=np.int64),
                            np.array([r[1] for r in rows], dtype=np.float64)) if rows else None
        return self._s[sym]

    def paid(self, sym: str, side: int, t_entry: int, t_exit: int) -> tuple:
        """(funding in bps of notional, signed as PnL; known?) for prints with t_entry < t <= t_exit."""
        ser = self.series(sym)
        if ser is None or t_entry < ser[0][0] or t_exit > ser[0][-1] + 3_600_000:
            return 0.0, False
        t, r = ser
        lo, hi = np.searchsorted(t, t_entry, side='right'), np.searchsorted(t, t_exit, side='right')
        return float(-side * r[lo:hi].sum()), True


def fetch_funding_ext(syms: list, since_ms: int, out: Path = DATA / 'funding_ext.json', pause: float = 0.65) -> dict:
    """Prints after the cached range for the few symbols whose FRESH trades run past it (public REST)."""
    ext = json.loads(out.read_text()) if out.exists() else {}
    for s in syms:
        if s in ext:
            continue
        body = http_get(f'{FAPI}/fapi/v1/fundingRate?symbol={quote(s)}&startTime={since_ms}&limit=1000')
        ext[s] = [[int(round(int(r['fundingTime']) / MIN_MS)) * MIN_MS, float(r['fundingRate']) * 1e4]
                  for r in (json.loads(body) if body else [])]
        time.sleep(pause)
    out.write_text(json.dumps(ext))
    return ext


# ================================================================================================ triggers -> trades
class Features:
    """Trigger inputs of a set of pairs on the decision minutes of day D (columns D0..D1-1), cached per parameter."""

    def __init__(self, mx: dict, rows):
        self.mx, self.rows = mx, np.asarray(rows, dtype=np.int64)
        self._n = mx['n'][self.rows, :D1]
        v60, ratio = volume_ratio(mx['qv'][self.rows, :D1])
        self.v60, self.vr = v60[:, D0:D1].astype(np.float32), ratio[:, D0:D1].astype(np.float32)
        self._rho, self._side = {}, {}
        self.tag_g = mx['tag'][self.rows] == 'G'
        self.base_min = np.array([(ms(d) - DAY_MS) // MIN_MS for d in mx['day'][self.rows]], dtype=np.int64)

    def rho(self, hl: float) -> dict:
        if hl not in self._rho:
            f = intensity(self._n, hl)
            lam = f['lam_next'][:, D0:D1]
            self._rho[hl] = {'MU': (lam / f['mu_f'][:, None]).astype(np.float32),
                             'BAR': (lam / np.maximum(f['nbar'], 1e-12)[:, None]).astype(np.float32),
                             'ok': f['ok'], 'n_hat': f['n_hat']}
        return self._rho[hl]

    def side(self, w: int, mode: str) -> np.ndarray:
        if (w, mode) not in self._side:
            mx, r = self.mx, self.rows
            self._side[(w, mode)] = direction(mx['c'][r, :D1], mx['tbq'][r, :D1], mx['qv'][r, :D1], w, mode)[:, D0:D1]
        return self._side[(w, mode)]

    def mask(self, cfg: Cfg) -> tuple:
        """(trigger mask, side) on the decision minutes."""
        rho, side = self.rho(cfg.hl), self.side(cfg.w, cfg.dir)
        m = (rho[cfg.ref] >= cfg.theta) & (self.vr >= cfg.kappa) & (side != 0) & rho['ok'][:, None]
        if cfg.filt != 'ALL':
            with_ = np.where(self.tag_g[:, None], side > 0, side < 0)
            m &= with_ if cfg.filt == 'WITH' else ~with_
        return m, side


def run_trades(f: Features, cfg: Cfg, mode: str = 'MOM', seed: int = 0, counts: Optional[np.ndarray] = None) -> dict:
    """Trades of one configuration on the pairs of `f`, one position per symbol with the cooldown carried across that
    symbol's consecutive pairs. mode: MOM (the rule), FADE (side flipped), RANDOM (coin-flip side), RTIME (`counts`
    entries per pair at uniform decision minutes, side by PRICE)."""
    mx, rng = f.mx, np.random.default_rng(seed)
    if mode == 'RTIME':
        side = f.side(cfg.w, 'PRICE')
        m = np.zeros(side.shape, dtype=bool)
        for i in np.flatnonzero(counts):
            m[i, rng.choice(D1 - D0, size=min(int(counts[i]), D1 - D0), replace=False)] = True
        m &= side != 0
    else:
        m, side = f.mask(cfg)
    out = {k: [] for k in ('row', 'i', 's', 'j_exit', 'side', 'gross', 'reason', 'v60')}
    free, prev_sym = -1, None
    for i in np.flatnonzero(m.any(axis=1)):
        p = int(f.rows[i])
        sym = mx['sym'][p]
        if sym != prev_sym:
            free, prev_sym = -1, sym
        cols, base, nb = np.flatnonzero(m[i]) + D0, int(f.base_min[i]), int(mx['n_bars'][p])
        k = 0
        while k < len(cols):
            s = int(cols[k])
            if base + s < free:
                k = int(np.searchsorted(cols, free - base, side='left'))
                continue
            if s + 1 >= nb:
                break
            sd = int(side[i, s - D0])
            sd = -sd if mode == 'FADE' else (1 if rng.random() < 0.5 else -1) if mode == 'RANDOM' else sd
            j, gross, why = sim_trade(mx['o'][p], mx['h'][p], mx['l'][p], mx['c'][p], s + 1, sd, cfg.t, cfg.s, cfg.h,
                                      cfg.arm, cfg.dist, nb)
            free = base + j + cfg.cooldown
            for key, v in zip(out, (p, i, s, j, sd, gross, why, float(f.v60[i, s - D0]))):
                out[key].append(v)
            k += 1
    tr = {k: np.array(v, dtype=(np.float64 if k in ('gross', 'v60') else np.int64)) for k, v in out.items()}
    t0 = np.array([ms(d) - DAY_MS for d in mx['day'][tr['row']]], dtype=np.int64) if len(tr['row']) else np.zeros(0, np.int64)
    tr['t_entry'], tr['t_exit'] = t0 + (tr['s'] + 1) * MIN_MS, t0 + (tr['j_exit'] + 1) * MIN_MS
    tr['day'], tr['sym'], tr['tag'] = mx['day'][tr['row']], mx['sym'][tr['row']], mx['tag'][tr['row']]
    return tr


def price(tr: dict, fund: Optional[Funding], sched: str = 'BASE') -> dict:
    """Adds funding (bps, with a known flag) and net bps under one cost schedule."""
    n = len(tr['gross'])
    fb, known = np.zeros(n), np.zeros(n, dtype=bool)
    if fund is not None:
        for k in range(n):
            fb[k], known[k] = fund.paid(str(tr['sym'][k]), int(tr['side'][k]), int(tr['t_entry'][k]), int(tr['t_exit'][k]))
    return {**tr, 'funding': fb, 'funding_known': known, 'net': tr['gross'] - cost_bps(tr['reason'], sched) + fb}


# ================================================================================================ statistics
def deflated(x, n_trials: int) -> Optional[float]:
    """Deflated Sharpe of a per-trade series: null cross-trial variance 1 / n, `n_trials` configurations tried."""
    from core.fast_eval import dsr
    x = np.asarray(x, dtype=np.float64)
    if x.size < 3 or x.std(ddof=1) <= 0:
        return None
    z = (x - x.mean()) / x.std(ddof=1)
    v = dsr(float(x.mean() / x.std(ddof=1)), int(x.size), float((z ** 3).mean()), float((z ** 4).mean()),
            1.0 / x.size, n_trials)
    return None if v != v else float(v)


def cluster_stats(x, groups, n_trials: int = 0) -> dict:
    """Mean of x with an SE clustered by `groups` (UTC day), never below the i.i.d. SE; one-sided 95 % bounds."""
    x, g = np.asarray(x, dtype=np.float64), np.asarray(groups)
    if x.size == 0:
        return {'n': 0}
    out = {'n': int(x.size), 'mean': float(x.mean()), 'win': float((x > 0).mean()), 'days': int(np.unique(g).size)}
    iid = float(x.std(ddof=1) / math.sqrt(x.size)) if x.size > 1 else math.nan
    _, inv = np.unique(g, return_inverse=True)
    s = np.bincount(inv, weights=x / x.size)
    cl = float(math.sqrt(s.size / (s.size - 1.0) * ((s - s.mean()) ** 2).sum())) if s.size > 1 else math.nan
    se = math.nan if (iid != iid and cl != cl) else float(np.nanmax([iid, cl]))
    out.update(se=se, lcb95=float(x.mean() - 1.645 * se), ucb95=float(x.mean() + 1.645 * se))
    if n_trials >= 1:
        out['dsr'] = deflated(x, n_trials)
    return out


def summary(tr: dict, cfg: Cfg, n_trials: int = 0) -> dict:
    """Net bps (the verdict unit), net R, exits, funding coverage of one priced trade set."""
    if len(tr['net']) == 0:
        return {'n': 0}
    out = cluster_stats(tr['net'], tr['day'], n_trials)
    r = cluster_stats(tr['net'] / cfg.s, tr['day'])
    wins, losses = tr['net'][tr['net'] > 0].sum(), -tr['net'][tr['net'] <= 0].sum()
    out.update(mean_r=r['mean'], se_r=r['se'], lcb95_r=r['lcb95'], gross=float(tr['gross'].mean()),
               cost=float(cost_bps(tr['reason']).mean()), funding=float(tr['funding'].mean()),
               funding_known=float(tr['funding_known'].mean()), pf=float(wins / losses) if losses > 0 else None,
               exits={REASONS[k]: float((tr['reason'] == k).mean()) for k in range(4)},
               hold_min=float((tr['j_exit'] - tr['s']).mean()), long_share=float((tr['side'] > 0).mean()),
               breakeven_win=float((cfg.s + 2 * (TAKER + SLIP)) / (cfg.t + cfg.s)))
    return out


def verdict(holdout: dict, fresh: dict) -> dict:
    """Plan section 4: every check must hold; a thin FRESH leaves an otherwise passing rule UNCONFIRMED."""
    checks = {f'holdout_n>={MIN_HOLDOUT_N}': holdout.get('n', 0) >= MIN_HOLDOUT_N,
              'holdout_lcb95>0': holdout.get('lcb95', -math.inf) > 0,
              'holdout_dsr>=0.95': (holdout.get('dsr') or 0.0) >= 0.95,
              'fresh_mean>0': fresh.get('n', 0) > 0 and fresh.get('mean', -math.inf) > 0}
    core = all(v for k, v in checks.items() if not k.startswith('fresh'))
    if core and fresh.get('n', 0) < MIN_FRESH_N:
        return {'checks': checks, 'verdict': 'UNCONFIRMED'}
    return {'checks': checks, 'verdict': 'EDGE' if all(checks.values()) else 'NO EDGE'}


def choose(table: dict) -> Optional[str]:
    """TRAIN-best cell: highest mean net R among cells with n >= MIN_TRAIN_N."""
    ok = {k: v for k, v in table.items() if v.get('n', 0) >= MIN_TRAIN_N}
    return max(ok, key=lambda k: ok[k]['mean_r']) if ok else None


def breakdown(tr: dict, cfg: Cfg) -> dict:
    """Net bps by year, tag and side."""
    keys = {'year': np.array([d[:4] for d in tr['day']]) if len(tr['day']) else np.array([]),
            'tag': tr['tag'], 'side': np.where(tr['side'] > 0, 'LONG', 'SHORT')}
    out = {}
    for name, k in keys.items():
        for v in np.unique(k):
            sel = k == v
            st = cluster_stats(tr['net'][sel], tr['day'][sel])
            out[f'{name}={v}'] = {x: st.get(x) for x in ('n', 'mean', 'se', 'win')}
    return out


# ================================================================================================ T3 descriptives
def accuracy(mx: dict, rows, hl: float, chunk: int = 2000) -> dict:
    """One-step-ahead accuracy on day D with the D-1 parameters against LAST, MEAN and an EWMA of the same half-life;
    errors are scaled by the pair's Nbar so that pairs are comparable."""
    from scipy.signal import lfilter
    q = math.exp(-beta_of(hl))
    acc = {k: 0.0 for k in ('n', 'mae_H', 'mae_LAST', 'mae_MEAN', 'mae_EWMA', 'dev_H', 'dev_LAST', 'dev_MEAN', 'dev_EWMA',
                            'min_mu3', 'min_bar3')}
    n_hat, pairs, rows = [], 0, np.asarray(rows)
    for a in range(0, len(rows), chunk):
        n = mx['n'][rows[a:a + chunk], :D1].astype(np.float64)
        f = intensity(n, hl)
        ok = f['ok']
        n, nbar = n[ok], f['nbar'][ok][:, None]
        lam = f['lam_next'][ok]
        y = n[:, L_DAY + 1:D1]
        ew = lfilter([1.0 - q], [1.0, -q], n, axis=-1, zi=(q * nbar))[0]
        preds = {'H': lam[:, L_DAY:D1 - 1], 'LAST': n[:, L_DAY:D1 - 1], 'MEAN': np.broadcast_to(nbar, y.shape),
                 'EWMA': ew[:, L_DAY:D1 - 1]}
        for k, p in preds.items():
            p = np.maximum(p, 0.5)
            acc['mae_' + k] += float((np.abs(y - p) / nbar).sum())
            with np.errstate(divide='ignore', invalid='ignore'):
                dev = 2.0 * (np.where(y > 0, y * np.log(y / p), 0.0) - (y - p))
            acc['dev_' + k] += float((dev / nbar).sum())
        acc['n'] += y.size
        acc['min_mu3'] += float((lam[:, L_DAY:D1] / f['mu_f'][ok][:, None] >= 3.0).sum())
        acc['min_bar3'] += float((lam[:, L_DAY:D1] / nbar >= 3.0).sum())
        n_hat.append(f['n_hat'][ok])
        pairs += int(ok.sum())
    if not pairs:
        return {'pairs': 0}
    nh, n = np.concatenate(n_hat), acc['n']
    out = {'pairs': pairs, 'pairs_skipped': int(len(rows) - pairs), 'hl': hl,
           'n_hat': {'p10': float(np.quantile(nh, 0.1)), 'median': float(np.median(nh)), 'p90': float(np.quantile(nh, 0.9)),
                     'share_ge_2/3': float((nh >= 2.0 / 3.0).mean()), 'share_ge_1': float((nh >= 1.0).mean())},
           'share_minutes_rho_mu_ge3': acc['min_mu3'] / (pairs * L_DAY),
           'share_minutes_rho_bar_ge3': acc['min_bar3'] / (pairs * L_DAY)}
    for k in ('H', 'LAST', 'MEAN', 'EWMA'):
        out[k] = {'mae': acc['mae_' + k] / n, 'deviance': acc['dev_' + k] / n}
    out['skill_vs'] = {k: {'mae': 1.0 - out['H']['mae'] / out[k]['mae'], 'deviance': 1.0 - out['H']['deviance'] / out[k]['deviance']}
                       for k in ('LAST', 'MEAN', 'EWMA')}
    return out


def forward(mx: dict, tr: dict, horizons=(5, 15, 60, 240), levels=(250.0, 400.0, 600.0), window: int = 240) -> dict:
    """Signed gross move after the entry open at fixed horizons and the excursions inside `window` minutes."""
    if len(tr['row']) == 0:
        return {'n': 0}
    fwd, mfe, mae = {hz: [] for hz in horizons}, [], []
    for p, s, sd, day in zip(tr['row'], tr['s'], tr['side'], tr['day']):
        j0, nb = int(s) + 1, int(mx['n_bars'][p])
        e = float(mx['o'][p, j0])
        k = sd * 1e4 / e
        for hz in horizons:
            fwd[hz].append((float(mx['c'][p, min(j0 + hz, nb) - 1]) - e) * k)
        hi = (mx['h'][p, j0:min(j0 + window, nb)].astype(np.float64) - e) * k
        lo = (mx['l'][p, j0:min(j0 + window, nb)].astype(np.float64) - e) * k
        mfe.append(max(hi.max(), lo.max()))
        mae.append(min(hi.min(), lo.min()))
    mfe, mae = np.array(mfe), np.array(mae)
    out = {'n': len(mfe), 'mfe_median': float(np.median(mfe)), 'mae_median': float(np.median(mae))}
    for hz in horizons:
        st = cluster_stats(fwd[hz], tr['day'])
        out[f'fwd_{hz}m'] = {**{x: st[x] for x in ('mean', 'se', 'win')}, 'abs': float(np.abs(fwd[hz]).mean())}
    for lv in levels:
        out[f'reach_{lv:g}'] = {'favourable': float((mfe >= lv).mean()), 'adverse': float((mae <= -lv).mean())}
    return out


# ================================================================================================ growth (section 5)
EQUITY0, BASE_RISK, MAX_LEV, CAP_SHARE, MIN_NOTIONAL, RUIN_U = 10.0, 1.0, 20.0, 0.01, 5.0, 1.0
PATHS, DAYS, TARGET_PER_HOUR = 2000, 30, 20.0
SIZINGS = {'FLAT_1U': dict(base_risk=1.0, stack_frac=0.0, ratchet=False),
           'STACK50': dict(base_risk=1.0, stack_frac=0.5, ratchet=False),
           'STACK50_RATCHET': dict(base_risk=1.0, stack_frac=0.5, ratchet=True),
           'STACK70_RATCHET': dict(base_risk=1.0, stack_frac=0.7, ratchet=True),
           'FLAT_0.05U': dict(base_risk=0.05, stack_frac=0.0, ratchet=False)}
# Addendum A1 (after the frozen run, not pre-registered): "consecutive wins" read as two wins in a row.
ADDENDUM_SIZINGS = {'STACK50_AFTER_2_WINS': dict(base_risk=1.0, stack_frac=0.5, ratchet=False, min_streak=2),
                    'STACK50_AFTER_2_WINS_RATCHET': dict(base_risk=1.0, stack_frac=0.5, ratchet=True, min_streak=2)}


def one_slot(tr: dict) -> np.ndarray:
    """Indices of the trades one account can hold: time order, a trigger while a position is open is skipped."""
    keep, free = [], -1
    for k in np.lexsort((tr['row'], tr['t_entry'])):
        if tr['t_entry'][k] >= free:
            keep.append(int(k))
            free = int(tr['t_exit'][k])
    return np.array(keep, dtype=np.int64)


def day_book(tr: dict, days: list, stop_bps: float) -> list:
    """One list per calendar day of `days` (possibly empty): (net bps, stop bps, V60 in USDT, exit hour)."""
    idx = one_slot(tr)
    book = {d: [] for d in days}
    for k in idx:
        d = str(tr['day'][k])
        if d in book:
            book[d].append((float(tr['net'][k]), float(stop_bps), float(tr['v60'][k]),
                            int(tr['t_exit'][k] // 3_600_000 % 24)))
    return [book[d] for d in days]


def growth_path(book: list, rng: np.random.Generator, sizing: dict, days: int = DAYS, equity0: float = EQUITY0,
                marks=(1, 7, 14, 30)) -> dict:
    """One path: each day replays one calendar day of the book. Notional = risk / stop, capped at MAX_LEV x equity and
    CAP_SHARE x that trigger's hourly volume; under MIN_NOTIONAL no trade; a loss is clamped at the equity; the path
    stops below RUIN_U and, under the ratchet, when the sizer returns 0."""
    from core.position_sizer import profit_stack_risk
    eq, peak, low, streak, trades, skipped, best_hour = equity0, equity0, equity0, 0, 0, 0, 0.0
    ruined = halted = False
    at = {}
    for d in range(1, days + 1):
        hour = {}
        for net, stop, v60, hr in (book[int(rng.integers(0, len(book)))] if not (ruined or halted) else ()):
            risk = profit_stack_risk(eq, principal=equity0, streak=streak, peak=peak, **sizing)
            if risk <= 0:
                halted = True
                break
            notional = min(risk / (stop * 1e-4), MAX_LEV * eq, CAP_SHARE * v60)
            if notional < MIN_NOTIONAL:
                skipped += 1
                continue
            pnl = max(notional * net * 1e-4, -eq)
            eq, trades = eq + pnl, trades + 1
            streak = streak + 1 if pnl > 0 else 0
            peak, low = max(peak, eq), min(low, eq)
            hour[hr] = hour.get(hr, 0.0) + pnl
            if eq < RUIN_U:
                ruined = True
                break
        best_hour = max([best_hour] + list(hour.values()))
        if d in marks:
            at[d] = eq
    return {'equity': eq, 'at': at, 'ruined': ruined, 'halted': halted, 'low': low, 'trades': trades,
            'skipped': skipped, 'best_hour': best_hour}


def growth_mc(book: list, sizing: dict, paths: int = PATHS, seed: int = SEED, days: int = DAYS,
              equity0: float = EQUITY0, marks=(1, 7, 14, 30)) -> dict:
    if not book:
        return {'paths': 0}
    rng = np.random.default_rng(seed)
    res = [growth_path(book, rng, sizing, days, equity0, marks) for _ in range(paths)]
    eq = np.array([r['equity'] for r in res])
    q = lambda v: {'p05': float(np.quantile(v, 0.05)), 'median': float(np.median(v)), 'p95': float(np.quantile(v, 0.95))}
    return {'paths': paths, 'days': days, **q(eq), 'mean': float(eq.mean()), 'max': float(eq.max()),
            'at_day': {str(m): q(np.array([r['at'][m] for r in res])) for m in marks if m <= days},
            'p_ruin': float(np.mean([r['ruined'] for r in res])),
            'p_below_principal_end': float((eq < equity0).mean()),
            'p_principal_ever_impaired': float(np.mean([r['low'] < equity0 for r in res])),
            'p_halted_at_ratchet': float(np.mean([r['halted'] for r in res])),
            'p_double': float((eq >= 2 * equity0).mean()),
            'p_pace_20u_per_hour': float((eq >= equity0 + TARGET_PER_HOUR * 24 * days).mean()),
            'p_any_hour_ge_20u': float(np.mean([r['best_hour'] >= TARGET_PER_HOUR for r in res])),
            'mean_u_per_hour': float((eq.mean() - equity0) / (24.0 * days)),
            'median_u_per_hour': float((np.median(eq) - equity0) / (24.0 * days)),
            'mean_trades': float(np.mean([r['trades'] for r in res])),
            'mean_skipped_below_min_notional': float(np.mean([r['skipped'] for r in res]))}


def target_arithmetic(stop_bps: float, target_bps: float, trades_per_day: float, mean_net_bps: Optional[float],
                      equity: float = EQUITY0, risk: float = BASE_RISK) -> dict:
    """What +20 U per hour needs at the base size against the measured frequency and expectancy."""
    notional = risk / (stop_bps * 1e-4)
    win_u = notional * (target_bps - (TAKER + SLIP + MAKER)) * 1e-4
    out = {'notional_u': notional, 'leverage_x': notional / equity, 'net_win_u': win_u,
           'net_winners_per_hour_needed': TARGET_PER_HOUR / win_u, 'triggers_per_hour_one_slot': trades_per_day / 24.0,
           'net_bps_per_hour_needed_on_that_notional': TARGET_PER_HOUR / notional * 1e4}
    if mean_net_bps is not None:
        out['measured_u_per_hour'] = notional * mean_net_bps * 1e-4 * trades_per_day / 24.0
    return out


# ================================================================================================ run
def rows_of(mx: dict, part: str) -> np.ndarray:
    lo, hi = PARTS[part]
    return np.flatnonzero((mx['day'] >= lo) & (mx['day'] <= hi))


def calendar_days(part: str) -> list:
    lo, hi = ms(PARTS[part][0]), ms(PARTS[part][1])
    return [day_of(t) for t in range(lo, hi + 1, DAY_MS)]


def read_once(f: Features, cfg: Cfg, fund: Funding, n_trials: int, seeds: int = 5) -> tuple:
    """One out-of-sample read of a configuration -> (the rule, cost schedules, controls, breakdown; the priced trades)."""
    tr = price(run_trades(f, cfg), fund)
    out = {'stats': summary(tr, cfg, n_trials), 'by': breakdown(tr, cfg) if len(tr['net']) else {},
           'schedules': {k: cluster_stats(price(tr, None, k)['net'] + tr['funding'], tr['day']) for k in SCHEDULES}}
    fade = price(run_trades(f, cfg, 'FADE'), fund)
    out['fade'] = summary(fade, cfg)
    rnd = [price(run_trades(f, cfg, 'RANDOM', seed=SEED + k), fund) for k in range(seeds)]
    out['random_side'] = cluster_stats(np.concatenate([r['net'] for r in rnd]), np.concatenate([r['day'] for r in rnd]))
    counts = np.bincount(tr['i'], minlength=len(f.rows)) if len(tr['i']) else np.zeros(len(f.rows), dtype=np.int64)
    rt = [price(run_trades(f, cfg, 'RTIME', seed=SEED + 100 + k, counts=counts), fund) for k in range(seeds)]
    out['random_time'] = cluster_stats(np.concatenate([r['net'] for r in rt]), np.concatenate([r['day'] for r in rt]))
    out['forward'] = {'trigger': forward(f.mx, tr), 'random_time': forward(f.mx, rt[0])}
    return out, tr


def search(f: Features, start: Cfg, fund: Funding, rounds: int = ROUNDS, seed: int = SEED, log=print) -> dict:
    """TRAIN-only one-parameter mutations, kept when the lower 95 % bound of the mean net R rises with n >= MIN_TRAIN_N."""
    rng, seen = random.Random(seed), {}

    def score(cfg):
        if cfg not in seen:
            seen[cfg] = summary(price(run_trades(f, cfg), fund), cfg)
        return seen[cfg]

    best, s = start, score(start)
    hist = [{'round': 0, 'cfg': asdict(best), 'n': s.get('n', 0), 'mean_r': s.get('mean_r'), 'lcb95_r': s.get('lcb95_r'),
             'mean': s.get('mean'), 'kept': True}]
    for i in range(1, rounds + 1):
        cand = mutate(best, rng)
        c = score(cand)
        keep = c.get('n', 0) >= MIN_TRAIN_N and (s.get('n', 0) < MIN_TRAIN_N or c['lcb95_r'] > s['lcb95_r'])
        diff = {k: v for k, v in asdict(cand).items() if v != getattr(best, k)}
        hist.append({'round': i, 'cfg': asdict(cand), 'change': diff, 'n': c.get('n', 0), 'mean_r': c.get('mean_r'),
                     'lcb95_r': c.get('lcb95_r'), 'mean': c.get('mean'), 'kept': keep})
        log(f"  round {i}: {diff} -> TRAIN n {c.get('n', 0)} mean R {c.get('mean_r', float('nan')):+.4f} "
            f"LCB {c.get('lcb95_r', float('nan')):+.4f} {'KEEP' if keep else 'revert'}")
        if keep:
            best, s = cand, c
    return {'start': asdict(start), 'final': asdict(best), 'final_cfg': best, 'train_final': s,
            'configs_evaluated': len(seen), 'history': hist,
            'train_positive_configs': sum(1 for v in seen.values() if v.get('n', 0) >= MIN_TRAIN_N and v['mean'] > 0)}


def run(log=print, rounds: int = ROUNDS, paths: int = PATHS, matrix: Path = MATRIX, fund: Optional[Funding] = None) -> dict:
    mx = load_matrix(matrix)
    parts = {p: rows_of(mx, p) for p in PARTS}
    res = {'plan': PLAN, 'pairs': {p: int(len(r)) for p, r in parts.items()},
           'dropped_pairs': {'no_file': int(mx['dropped'][0]), 'gaps': int(mx['dropped'][1])},
           'symbols': int(np.unique(mx['sym']).size), 'no_d_plus_1': int((mx['n_bars'] < L3).sum())}
    if fund is None:                                         # prints after the cached range, for the last FRESH days
        fresh_syms = sorted(set(mx['sym'][parts['FRESH']][mx['day'][parts['FRESH']] >= '2026-11-10'].tolist()))
        fund = Funding(ext=fetch_funding_ext(fresh_syms, ms('2026-11-12')))
    log(f'pairs {res["pairs"]}')

    # ---- T1: TRAIN table of the 12 cells, one read of the TRAIN-best cell
    ftr = Features(mx, parts['TRAIN'])
    table, cells = {}, {c.name: c for c in f0_cells()}
    for name, c in cells.items():
        table[name] = summary(price(run_trades(ftr, c), fund), c)
        log(f"  T1 TRAIN {name}: n {table[name].get('n', 0)} mean R {table[name].get('mean_r', float('nan')):+.4f} "
            f"net {table[name].get('mean', float('nan')):+.2f} bps")
    res['t1'] = {'train': table, 'cell': choose(table)}

    # ---- T2: TRAIN-only search from the T1 cell
    start = cells[res['t1']['cell']] if res['t1']['cell'] else Cfg()
    sr = search(ftr, start, fund, rounds, log=log)
    final = sr.pop('final_cfg')
    res['t2'] = {'search': sr, 'final_name': final.name}
    res['t3'] = {'accuracy': {p: accuracy(mx, r, start.hl) for p, r in parts.items()}}
    if final.hl != start.hl:
        res['t3']['accuracy_final_hl'] = {p: accuracy(mx, r, final.hl) for p, r in parts.items()}
    res['t3']['train_forward'] = {'t1': forward(mx, run_trades(ftr, start)), 't2': forward(mx, run_trades(ftr, final))}
    res['t3']['train_by'] = {'t1': breakdown(price(run_trades(ftr, start), fund), start),
                             't2': breakdown(price(run_trades(ftr, final), fund), final)}
    del ftr

    # ---- the out-of-sample reads
    trials = {'t1': len(cells), 't2': len(cells) + sr['configs_evaluated']}
    oos, kept = {'t1': {}, 't2': {}}, {'t1': {}, 't2': {}}
    for part in ('HOLDOUT', 'FRESH'):
        f = Features(mx, parts[part])
        for key, cfg in (('t1', start), ('t2', final)):
            if key == 't1' and not res['t1']['cell']:
                continue
            oos[key][part], kept[key][part] = read_once(f, cfg, fund, trials[key])
            st = oos[key][part]['stats']
            log(f"  {key} {part} {cfg.name}: n {st.get('n', 0)} net {st.get('mean', float('nan')):+.2f} +- "
                f"{st.get('se', float('nan')):.2f} bps, R {st.get('mean_r', float('nan')):+.4f}, DSR {st.get('dsr')}")
        del f
    for key in ('t1', 't2'):
        if oos[key]:
            res[key].update(holdout=oos[key]['HOLDOUT'], fresh=oos[key]['FRESH'], n_trials=trials[key],
                            **verdict(oos[key]['HOLDOUT']['stats'], oos[key]['FRESH']['stats']))
        else:
            res[key]['verdict'] = 'NO OPPORTUNITY SET'

    # ---- growth from 10 U
    days = calendar_days('HOLDOUT') + calendar_days('FRESH')
    res['growth'] = {}
    for key, cfg in (('t1', start), ('t2', final)):
        if not kept[key]:
            continue
        tr = {k: np.concatenate([kept[key][p][k] for p in ('HOLDOUT', 'FRESH')]) for k in kept[key]['HOLDOUT']}
        book = day_book(tr, days, cfg.s)
        slot = [x for d in book for x in d]
        per_day = len(slot) / len(days)
        mean_net = float(np.mean([x[0] for x in slot])) if slot else None
        res['growth'][key] = {
            'cfg': cfg.name, 'one_slot_trades': len(slot), 'all_trades': int(len(tr['net'])), 'trades_per_day': per_day,
            'one_slot_mean_net_bps': mean_net, 'median_v60_usdt': float(np.median([x[2] for x in slot])) if slot else None,
            'min_loss_at_stop_at_min_notional_u': MIN_NOTIONAL * cfg.s * 1e-4,
            'target': target_arithmetic(cfg.s, cfg.t, per_day, mean_net),
            'sizing': {name: growth_mc(book, sz, paths) for name, sz in SIZINGS.items()},
            'sizing_addendum': {name: growth_mc(book, sz, paths) for name, sz in ADDENDUM_SIZINGS.items()},
            'trades': {'day': [str(d) for d in tr['day']], 'sym': [str(s) for s in tr['sym']],
                       'side': tr['side'].tolist(), 'net': np.round(tr['net'], 3).tolist(),
                       'reason': tr['reason'].tolist(), 't_entry': tr['t_entry'].tolist()}}
        g = res['growth'][key]['sizing']['FLAT_1U']
        log(f"  growth {key}: {per_day:.2f} one-slot trades / day, FLAT 1 U median {g.get('median')}, ruin {g.get('p_ruin')}")
    return res


def run_cli(a) -> int:
    t0 = time.time()
    res = run(rounds=a.rounds or ROUNDS, paths=a.paths or PATHS)
    res['runtime_s'] = time.time() - t0
    OUT.write_text(json.dumps(res, indent=1, default=float))
    print(f'T1 {res["t1"].get("verdict")}  T2 {res["t2"].get("verdict")}  -> {OUT} ({res["runtime_s"]:.0f} s)')
    return 0


# ================================================================================================ cli
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('stage', choices=('daily', 'universe', 'klines', 'matrix', 'run'))
    ap.add_argument('--workers', type=int, default=16)
    ap.add_argument('--retry-missing', action='store_true')
    ap.add_argument('--shard', default='0/1', help='i/n: this process takes every n-th open file from i')
    ap.add_argument('--rounds', type=int, default=None)
    ap.add_argument('--paths', type=int, default=None)
    a = ap.parse_args(argv)
    if a.stage == 'daily':
        syms = symbols(json.loads((FUNDING / 'exchangeInfo.json').read_text(encoding='utf-8')))
        raw = fetch_daily(syms)
        print(f'{len(raw)} symbols, {sum(len(v) for v in raw.values())} daily bars -> {DAILY}')
        return 0
    if a.stage in ('universe', 'klines', 'matrix'):
        uni = build_universe(json.loads(DAILY.read_text()), PARTS['TRAIN'][0], PARTS['FRESH'][1])
        UNIVERSE.write_text(json.dumps(uni))
        by = {}
        for d in uni:
            by[part_of(d)] = by.get(part_of(d), 0) + 1
        print(f'{len(uni)} days {by}, {len(pairs_of(uni))} pairs, {len(files_of(uni))} files, '
              f'{len({p[0] for p in pairs_of(uni)})} symbols')
        if a.stage == 'klines':
            i, n = (int(x) for x in a.shard.split('/'))
            print(fetch_klines(uni, workers=a.workers, retry_missing=a.retry_missing, shard=(i, n)))
        if a.stage == 'matrix':
            mx = build_matrix(uni)
            np.savez(MATRIX, **mx)
            print(f'{len(mx["sym"])} pairs kept, dropped (no file, gaps) {mx["dropped"].tolist()} -> {MATRIX}')
        return 0
    return run_cli(a)


if __name__ == '__main__':
    raise SystemExit(main())
