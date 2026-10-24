"""Check docs/architecture against the code: surface spec, suite spec, file:line references.

    python docs/architecture/tools/check_arch_docs.py          (any cwd; exit 0 = no error)

1. h2_surface_v0.yaml: every lever/frozen field exists in core.engine.Profile or
   core.micro_signals.SignalConfig, baselines equal PROFILES['demo'] / ['live'] / config_for(symbol),
   baselines sit inside their bounds, consensus constants and bandit arms equal the code.
2. sandbox_suite_v0.yaml: families sum to 1000 and every family's parts sum to its count.
3. Every `path:line[-line]` in docs/architecture/*.md names an existing file and existing lines.
Phase 2 of 04_KE_HOACH_TRIEN_KHAI.md turns part 1 into tests/test_h2_surface.py.
"""
from __future__ import annotations

import dataclasses
import math
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
ARCH = ROOT / 'docs' / 'architecture'
sys.path.insert(0, str(ROOT))

from core import consensus_engine as ce   # noqa: E402
from core import engine as eng            # noqa: E402
from core import micro_signals as msig    # noqa: E402
from core import param_bandit as pb       # noqa: E402

REF = re.compile(r'`?([A-Za-z0-9_./\-]+\.(?:py|yaml|json|md)):(\d+)(?:-(\d+))?')
REF_BASES = ('', 'core', 'tools', 'tests', 'third_party/harness2/harness2/core', 'third_party/harness2',
             'third_party/MiroFish', 'third_party/MiroFish/backend', 'third_party/MiroFish/backend/app',
             'third_party/MiroFish/backend/app/services', 'third_party/MiroFish/backend/scripts')


def same(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-12)
        except (TypeError, ValueError):
            return False
    return a == b


def check_surface(errs: list, notes: list) -> None:
    s = yaml.safe_load((ARCH / 'h2_surface_v0.yaml').read_text(encoding='utf-8'))
    pf = {f.name for f in dataclasses.fields(eng.Profile)}
    sf = {f.name for f in dataclasses.fields(msig.SignalConfig)}
    demo, live = eng.PROFILES['demo'], eng.PROFILES['live']
    sig = {sym: msig.config_for(sym) for sym in ('BTCUSDT', 'SOLUSDT', 'DOGEUSDT')}
    if s['consensus_frozen'][0]['value'] != ce.WEIGHTS:
        errs.append('WEIGHTS differ from core/consensus_engine.py')
    c = ce.ConsensusEngine()
    if not (same(c.threshold, 0.768) and same(c.veto_threshold, 0.70)):
        errs.append('consensus thresholds differ')
    for k, v in s['owned_by_bandit'].items():
        if tuple(v['arms']) != pb.ARMS[k]:
            errs.append(f'bandit arms {k}: {v["arms"]} vs {pb.ARMS[k]}')
    seen = set()
    for f in s['frozen_profile_fields']:
        n = f['field']
        if n not in pf:
            errs.append(f'frozen field missing in Profile: {n}')
            continue
        if 'value' in f and not same(getattr(demo, n), f['value']):
            errs.append(f'frozen {n}: doc {f["value"]!r} vs demo {getattr(demo, n)!r}')
        for prof, label in ((demo, 'demo'), (live, 'live')):
            if 'allowed' in f and getattr(prof, n) not in f['allowed']:
                errs.append(f'frozen {n}: {label} {getattr(prof, n)!r} not in {f["allowed"]}')
        seen.add(n)
    dims: dict = {}
    for lv in s['levers']:
        n, scope, kind = lv['field'], lv['scope'], lv['kind']
        dims[lv['dim']] = dims.get(lv['dim'], 0) + 1
        if n in seen:
            errs.append(f'duplicate field {n}')
        seen.add(n)
        if (scope == 'profile' and n not in pf) or (scope == 'signal' and n not in sf):
            errs.append(f'{scope} field missing: {n}')
            continue
        if lv.get('per_symbol'):
            actual = {sym: getattr(cfg, n) for sym, cfg in sig.items()}
        elif scope == 'profile':
            actual = {'demo': getattr(demo, n), 'live': getattr(live, n)}
        else:   # SignalConfig has no demo/live split
            actual = {'demo': getattr(sig['SOLUSDT'], n), 'live': getattr(sig['DOGEUSDT'], n)}
        for key, v in lv['baseline'].items():
            if not same(actual[key], v):
                errs.append(f'{n}[{key}]: doc {v!r} vs code {actual[key]!r}')
        for v in (x for x in actual.values() if x is not None):
            if kind == 'range' and not lv['lo'] <= v <= lv['hi']:
                errs.append(f'{n}: baseline {v} outside [{lv["lo"]}, {lv["hi"]}]')
            elif kind == 'cap' and v < lv['lo']:
                errs.append(f'{n}: cap baseline {v} below floor {lv["lo"]}')
            elif kind == 'floor' and v > lv['hi']:
                errs.append(f'{n}: floor baseline {v} above ceiling {lv["hi"]}')
            elif kind in ('gate', 'enum') and not isinstance(v, bool):
                errs.append(f'{n}: {kind} baseline is not a bool: {v!r}')
    notes.append(f'surface: levers {dims} (total {sum(dims.values())}), frozen fields '
                 f'{len(s["frozen_profile_fields"])}; Profile {len(pf)} fields, SignalConfig {len(sf)}')


def check_suite(errs: list, notes: list) -> None:
    st = yaml.safe_load((ARCH / 'sandbox_suite_v0.yaml').read_text(encoding='utf-8'))
    fam = st['families']
    total = sum(v['count'] for v in fam.values())
    if total != st['total'] or total != 1000:
        errs.append(f'suite total {total} != {st["total"]} / 1000')
    stages: dict = {}
    for name, v in fam.items():
        parts = v.get('by_symbol') or v.get('by_type')
        if parts and sum(x if isinstance(x, int) else x['count'] for x in parts.values()) != v['count']:
            errs.append(f'family {name}: parts do not sum to {v["count"]}')
        for stage, n in (v.get('stage_counts') or {v.get('stage'): v['count']}).items():
            stages[stage] = stages.get(stage, 0) + n
    notes.append(f'suite: {[(k, v["count"]) for k, v in fam.items()]}, stages {stages}')


def check_refs(errs: list, notes: list) -> None:
    ok = 0
    for doc in sorted(ARCH.glob('*.md')):
        for m in REF.finditer(doc.read_text(encoding='utf-8')):
            rel, a = m.group(1), int(m.group(2))
            b = int(m.group(3) or a)
            path = next((ROOT / base / rel for base in REF_BASES if (ROOT / base / rel).is_file()), None)
            if path is None:
                errs.append(f'{doc.name}: {rel}:{a} -> file not found')
                continue
            with path.open(encoding='utf-8', errors='replace') as fh:
                n = sum(1 for _ in fh)
            if not 1 <= a <= b <= n:
                errs.append(f'{doc.name}: {rel}:{a}-{b} -> file has {n} lines')
            else:
                ok += 1
    notes.append(f'refs: {ok} file:line references resolve')


def main() -> int:
    errs: list = []
    notes: list = []
    check_surface(errs, notes)
    check_suite(errs, notes)
    check_refs(errs, notes)
    print('\n'.join(notes))
    print('ERRORS:' if errs else 'OK: 0 errors')
    for e in errs:
        print(' -', e)
    return 1 if errs else 0


if __name__ == '__main__':
    raise SystemExit(main())
