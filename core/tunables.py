"""Hot-reloadable tunables: config/live_tunables.json -> running engines, no restart - 10-09 (cc).

Writers: core/dreaming_daemon.py (directives), a human editing the file, or any tool.  Reader: the Runner polls
the file's mtime every 5 s (TunableWatcher) and applies a VALIDATED subset to every engine.

File schema (all keys optional):
  {"version": 3, "source": "dreaming", "reason": "...", "expires_at": <epoch s>,
   "profile": {"cs_max_notional": 100, ...},        whitelisted Profile fields, see LIMITS
   "observe": true,                                  observer mode: no new entries (positions still managed)
   "swarm_weights": {"LUMEN": 1.4, "TIDAL": 0.5}}    directional agents only, clipped to [0, 1.8]

Safety (binance-futures-algo-guard / CLAUDE.md): a tunable may only keep or REDUCE risk versus the baseline
Profile the engine started with.  Size, risk budget, kill switch and trade rate can go down, never above the
baseline; leverage, keys, venue, live/demo mode and the RUNE/VESKA gates are not tunable at all.  Anything
outside the whitelist or bounds is rejected and reported.  Expiry (expires_at) restores the baseline.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Optional

# field -> (kind, lo, hi).  kind: 'cap' = only <= baseline (lo is the floor); 'range' = any value in [lo, hi];
# 'floor' = only >= baseline (hi is the ceiling) - used where higher means safer.
LIMITS = {
    'cs_max_notional': ('cap', 100.0, None),
    'portfolio_risk_usdt': ('cap', 0.5, None),
    'daily_kill_usdt': ('cap', 1.0, None),
    'max_trades_hour': ('cap', 1, None),
    'max_positions': ('cap', 1, None),
    'cooldown_s': ('floor', None, 1800.0),
    'cs_super_ofi': ('floor', None, 0.95),
    'hurdle_min_hold_s': ('range', 30.0, 90.0),
    'tp1_scaled_pct': ('range', 0.30, 0.50),
}
SWARM_AGENTS = ('NORO', 'LUMEN', 'TIDAL')


def validate(doc: dict, base) -> tuple:
    """-> (profile_changes, observe, swarm_weights, rejected[list of str]).  `base` = baseline Profile."""
    rejected, changes = [], {}
    for k, v in (doc.get('profile') or {}).items():
        lim = LIMITS.get(k)
        if lim is None or not hasattr(base, k):
            rejected.append(f'{k}: not tunable')
            continue
        kind, lo, hi = lim
        try:
            x = float(v)
        except (TypeError, ValueError):
            rejected.append(f'{k}: not a number')
            continue
        b = getattr(base, k)
        if kind == 'cap':
            if b is None:
                rejected.append(f'{k}: baseline unset')
                continue
            x = min(x, float(b))
            if lo is not None:
                x = max(x, lo)
        elif kind == 'floor':
            x = max(x, float(b) if b is not None else x)
            if hi is not None:
                x = min(x, hi)
        else:
            if (lo is not None and x < lo) or (hi is not None and x > hi):
                rejected.append(f'{k}: {x} outside [{lo}, {hi}]')
                continue
        changes[k] = int(x) if isinstance(b, int) and not isinstance(b, bool) else x
    weights = {}
    for a, w in (doc.get('swarm_weights') or {}).items():
        if a not in SWARM_AGENTS:
            rejected.append(f'swarm {a}: gate agents are not re-weightable')
            continue
        try:
            weights[a] = max(0.0, min(1.8, float(w)))
        except (TypeError, ValueError):
            rejected.append(f'swarm {a}: not a number')
    return changes, bool(doc.get('observe')), weights, rejected


def atomic_write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(doc, indent=1, default=float), encoding='utf-8')
    os.replace(tmp, path)


class TunableWatcher:
    """Polls `path` (mtime) and keeps the validated state; engines read `state` through Autonomy."""

    def __init__(self, path: Path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.mtime: Optional[float] = None
        self.doc: dict = {}
        self.version = 0
        self.error = ''

    def poll(self) -> bool:
        """True when the file changed (or disappeared) since the last poll."""
        try:
            m = self.path.stat().st_mtime
        except OSError:
            if self.mtime is not None:
                self.mtime, self.doc = None, {}
                return True
            return False
        if m == self.mtime:
            return False
        try:
            doc = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(doc, dict):
                raise ValueError('top level must be an object')
        except (OSError, ValueError) as exc:
            self.error = f'{type(exc).__name__}: {exc}'[:160]
            self.mtime = m                      # do not re-read a broken file every 5 s; keep the last good doc
            return False
        self.mtime, self.doc, self.error = m, doc, ''
        self.version += 1
        return True

    def active(self) -> dict:
        exp = self.doc.get('expires_at')
        if exp is not None and self.clock() >= float(exp):
            return {}
        return self.doc


def apply_profile(base, changes: dict, observe: bool, symbols) -> object:
    """Effective Profile = baseline + validated changes (+ every symbol observe-only when `observe`)."""
    p = replace(base, **changes) if changes else base
    if observe:
        p = replace(p, observe_only=tuple(sorted(set(p.observe_only or ()) | set(symbols))))
    return p
