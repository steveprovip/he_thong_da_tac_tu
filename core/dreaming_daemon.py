"""Dreaming daemon: out-of-band autopsy -> trap detection -> atomic lessons -> hot directives - 10-09 (cc).

Agent-memory pattern (repo skill agent-memory-dreaming): the engine does not wait for a human to read the log.
Every `every_s` (15 min) or after `every_trades` (3) new closed trades, the daemon:
  1. AUTOPSY     reads the ledger (READ-ONLY) and decomposes each recent trade: fee bps, gross bps, net bps,
                 entry slippage vs the signal mid (taker / crossed entries), adverse markout 1 s / 5 s / 30 s.
  2. TRAPS       CHURN    last >= 5 trades: median |gross| < 3 bps while median fee >= 3.5 bps (the market moves
                          less than the fee)            -> size cap $100 + observer 30 min
                 STREAK   >= 4 losing trades in a row      -> observer 30 min
                 ADVERSE  median 5 s markout <= -3 bps on >= 5 trades (fills get run over) -> size cap $100
                 FEE_DRAG fees > |gross| summed over the window with net < 0 -> size cap $150
  3. MEMORY      appends atomic lessons to data/brain_learnings.jsonl ("pattern -> cause -> standard", with the
                 numbers), consolidates them in config/dynamic_state.json (count, first/last seen); a lesson seen
                 >= 3 times is PROMOTED to a standing rule (its directive is re-applied at the next trap without
                 waiting for the full window); lessons unseen for 24 h decay out.
  4. DIRECTIVES  writes config/live_tunables.json (atomic) -> the engine hot-reloads it (core/tunables.py).
                 Directives only ever REDUCE risk and expire (default 30 min); no trap -> the file is cleared
                 back to the baseline once the last directive expires.
Run inside the Runner (dream_loop) or standalone:  python -m core.dreaming_daemon --once
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / 'data' / 'engine_trades.jsonl'
LEARNINGS = ROOT / 'data' / 'brain_learnings.jsonl'
STATE = ROOT / 'config' / 'dynamic_state.json'
TUNABLES = ROOT / 'config' / 'live_tunables.json'

WINDOW = 8
DIRECTIVE_S = 1800.0
PROMOTE_AT = 3
DECAY_S = 24 * 3600.0

LESSONS = {
    'CHURN': ('market range < fee: median |gross| {g:.1f} bps vs fee {f:.1f} bps over {n} trades',
              'scalping a tape that moves less than the round-trip fee',
              'cap size at $100 and observe 30 min; resume when the tape widens'),
    'STREAK': ('{k} losing trades in a row', 'signal out of phase with the current regime',
               'observe 30 min before trading again'),
    'ADVERSE': ('median 5 s markout {m:+.1f} bps over {n} fills', 'adverse selection: fills are run over',
                'cap size at $100 until markouts recover'),
    'FEE_DRAG': ('fees {fee:.3f} U > |gross| {gross:.3f} U over {n} trades', 'fees exceed the gross edge',
                 'cap size at $150'),
    'CONVICTION': ('top conviction tier {n} trades {top:+.1f} bps vs lower tiers {low:+.1f} bps',
                   'the 10 % size tier is not better calibrated than the small tiers',
                   'cap notional at $150 (no 10 % tier) until the top tier earns more than the rest'),
    'NO_STOP': ('exchange stop rejected at entry, market close {b:+.1f} bps', 'price spiked through the planned '
                'stop before it was placed (demo book / fast tape)', 'cap size at $150 for 30 min'),
}
DIRECTIVES = {
    'CHURN': {'profile': {'cs_max_notional': 100.0}, 'observe': True},
    'STREAK': {'observe': True},
    'ADVERSE': {'profile': {'cs_max_notional': 100.0}},
    'FEE_DRAG': {'profile': {'cs_max_notional': 150.0}},
    'NO_STOP': {'profile': {'cs_max_notional': 150.0}},
    'CONVICTION': {'profile': {'cs_max_notional': 150.0}},
}


# ------------------------------------------------------------------ autopsy
def read_rows(path: Path, mode: Optional[str] = 'demo') -> list:
    rows = []
    try:
        with open(path, encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if mode is None or r.get('mode') == mode:
                    rows.append(r)
    except OSError:
        pass
    return rows


def autopsy(r: dict) -> dict:
    notional = float(r.get('qty') or 0) * float(r.get('entry') or 0)
    def bps(x):
        return float(x) / notional * 1e4 if notional and x is not None else None
    slip = None
    sm, e = r.get('signal_mid'), r.get('entry')
    if sm and e and r.get('entry_type') not in (None, 'GTX'):
        d = 1 if r.get('side') == 'BUY' else -1
        slip = d * (float(e) - float(sm)) / float(sm) * 1e4          # paid above the signal mid (taker / cross)
    mk = r.get('markout_bps') or {}
    def m(k):
        v = mk.get(k)
        return float(v[0]) if isinstance(v, (list, tuple)) and v and v[0] is not None else None
    return {'t': r.get('t'), 'symbol': r.get('symbol'), 'side': r.get('side'), 'reason': r.get('reason'),
            'net_bps': bps(r.get('pnl')), 'gross_bps': bps(r.get('gross')), 'fee_bps': r.get('fee_bps'),
            'fee_u': r.get('fee'), 'gross_u': r.get('gross'), 'net_u': r.get('pnl'),
            'slip_bps': slip, 'markout_1s': m('1s'), 'markout_5s': m('5s'), 'markout_30s': m('30s'),
            'tier': ((r.get('conviction') or {}).get('tier') or '')}


def detect(trades: list) -> list:
    """trades = autopsy rows, oldest first.  -> [(trap, numbers)]"""
    out = []
    if not trades:
        return out
    k = 0
    for t in reversed(trades):
        if (t['net_u'] or 0) < 0:
            k += 1
        else:
            break
    if k >= 4:
        out.append(('STREAK', {'k': k}))
    top = [t['net_bps'] for t in trades[-60:] if 'OFI>=' in t.get('tier', '') and t['net_bps'] is not None]
    low = [t['net_bps'] for t in trades[-60:] if 'OFI>=' not in t.get('tier', '') and t['net_bps'] is not None]
    if len(top) >= 10 and len(low) >= 10:
        mt, ml = sum(top) / len(top), sum(low) / len(low)
        if mt < 0 and mt < ml:
            out.append(('CONVICTION', {'n': len(top), 'top': mt, 'low': ml}))
    last = trades[-1]
    if 'NO_STOP' in str(last.get('reason')) and (last.get('net_bps') or 0) < -20:
        out.append(('NO_STOP', {'b': last['net_bps']}))
    w = trades[-WINDOW:]
    if len(w) >= 5:
        g = [abs(t['gross_bps']) for t in w if t['gross_bps'] is not None]
        f = [float(t['fee_bps']) for t in w if t['fee_bps'] is not None]
        if g and f and statistics.median(g) < 3.0 and statistics.median(f) >= 3.5:
            out.append(('CHURN', {'g': statistics.median(g), 'f': statistics.median(f), 'n': len(w)}))
        mk = [t['markout_5s'] for t in w if t['markout_5s'] is not None]
        if len(mk) >= 5 and statistics.median(mk) <= -3.0:
            out.append(('ADVERSE', {'m': statistics.median(mk), 'n': len(mk)}))
        fee = sum(float(t['fee_u'] or 0) for t in w)
        gross = abs(sum(float(t['gross_u'] or 0) for t in w))
        net = sum(float(t['net_u'] or 0) for t in w)
        if fee > gross and net < 0:
            out.append(('FEE_DRAG', {'fee': fee, 'gross': gross, 'n': len(w)}))
    return out


# ------------------------------------------------------------------ memory
def load_state(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def consolidate(state: dict, traps: list, now: float) -> tuple:
    """Update the lesson book; -> (state, new atomic lessons, promoted names)."""
    book = state.setdefault('lessons', {})
    new, promoted = [], []
    for name, nums in traps:
        pat, cause, std = LESSONS[name]
        text = pat.format(**nums)
        e = book.get(name)
        if e is None:
            e = book[name] = {'count': 0, 'first_seen': now, 'rule': False}
        e['count'] += 1
        e['last_seen'] = now
        e['last'] = text
        if e['count'] >= PROMOTE_AT and not e['rule']:
            e['rule'] = True
            promoted.append(name)
        new.append({'t': now, 'pattern': name, 'evidence': text, 'cause': cause, 'standard': std,
                    'count': e['count'], 'rule': e['rule'], 'numbers': nums})
    for name in list(book):                                  # decay
        if now - book[name].get('last_seen', now) > DECAY_S:
            del book[name]
    return state, new, promoted


def directive_for(traps: list, now: float, version: int) -> Optional[dict]:
    if not traps:
        return None
    prof, observe, reasons = {}, False, []
    for name, _ in traps:
        d = DIRECTIVES[name]
        for k, v in (d.get('profile') or {}).items():
            prof[k] = min(prof.get(k, v), v)                  # the tightest cap wins
        observe = observe or bool(d.get('observe'))
        reasons.append(name)
    return {'version': version, 'source': 'dreaming', 'created': now, 'expires_at': now + DIRECTIVE_S,
            'reason': '+'.join(reasons), 'profile': prof, 'observe': observe}


def run_once(ledger: Path = LEDGER, learnings: Path = LEARNINGS, state_path: Path = STATE,
             tunables: Path = TUNABLES, now: Optional[float] = None, mode: Optional[str] = 'demo',
             extra_state: Optional[dict] = None) -> dict:
    """One dreaming pass.  Never writes the ledger.  -> summary dict."""
    from core.tunables import atomic_write
    now = time.time() if now is None else now
    rows = read_rows(ledger, mode)
    trades = [autopsy(r) for r in rows]
    state = load_state(state_path)
    seen = state.get('trades_seen', 0)
    fresh = len(trades) > seen
    traps = detect(trades) if fresh else []
    state, new, promoted = consolidate(state, traps, now)
    # promoted rules re-arm their directive on a shorter window (3 trades) without waiting for 5
    if fresh and not traps:
        for name, e in (state.get('lessons') or {}).items():
            if e.get('rule') and name in ('CHURN', 'ADVERSE') and len(trades) >= 3:
                short = trades[-3:]
                g = [abs(t['gross_bps']) for t in short if t['gross_bps'] is not None]
                if name == 'CHURN' and g and max(g) < 3.0:
                    traps.append(('CHURN', {'g': max(g), 'f': 4.0, 'n': 3}))
    version = int(state.get('version', 0)) + (1 if traps else 0)
    doc = directive_for(traps, now, version)
    cur = load_state(tunables)
    wrote = None
    if doc is not None:
        atomic_write(tunables, doc)
        wrote = doc
    elif cur.get('source') == 'dreaming' and cur.get('expires_at') and now >= float(cur['expires_at']):
        atomic_write(tunables, {'version': version, 'source': 'dreaming', 'created': now, 'reason': 'expired',
                                'profile': {}, 'observe': False})
        wrote = 'cleared'
    if new:
        learnings.parent.mkdir(parents=True, exist_ok=True)
        with open(learnings, 'a', encoding='utf-8') as fh:
            for x in new:
                fh.write(json.dumps(x, default=float) + '\n')
    recent = trades[-WINDOW:]
    nb = [t['net_bps'] for t in recent if t['net_bps'] is not None]
    state.update({'version': version, 'updated': now, 'trades_seen': len(trades),
                  'window': {'n': len(recent), 'mean_net_bps': sum(nb) / len(nb) if nb else None,
                             'fee_u': sum(float(t['fee_u'] or 0) for t in recent),
                             'net_u': sum(float(t['net_u'] or 0) for t in recent)},
                  'traps': [n for n, _ in traps], 'directive': wrote if isinstance(wrote, dict) else None,
                  'promoted': promoted})
    if extra_state:
        state.update(extra_state)
    atomic_write(state_path, state)
    return {'traps': traps, 'lessons': new, 'promoted': promoted, 'directive': wrote, 'trades': len(trades)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--every', type=float, default=900.0)
    ap.add_argument('--ledger', type=Path, default=LEDGER)
    ap.add_argument('--mode', default='demo')
    a = ap.parse_args(argv)
    while True:
        res = run_once(a.ledger, mode=a.mode)
        print(json.dumps({'t': time.time(), 'traps': [n for n, _ in res['traps']],
                          'promoted': res['promoted'], 'trades': res['trades'],
                          'directive': res['directive'] if res['directive'] != 'cleared' else 'cleared'},
                         default=float), flush=True)
        if a.once:
            return 0
        time.sleep(a.every)


if __name__ == '__main__':
    raise SystemExit(main())
