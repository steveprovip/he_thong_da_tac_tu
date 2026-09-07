import json
from urllib.request import urlopen
from pathlib import Path

try:
    with urlopen('http://127.0.0.1:8765/api/state', timeout=3) as response:
        s = json.load(response)
except OSError:
    s = json.loads((Path(__file__).resolve().parents[1]/'data'/'testnet-last-state.json').read_text(encoding='utf-8'))
ms = s.get('markets', [])
print(f"{'SYMBOL':<10} {'cons':>6} {'edge':>7} {'cost':>6} {'e/c':>5} {'vpin':>6} {'regime':<7} {'blocked_by'}")
for m in ms:
    if not isinstance(m, dict):
        continue
    sym = m.get('symbol', '?')
    cons = m.get('score') or 0
    edge = m.get('edge_bps') or m.get('edge') or 0
    cost = m.get('cost_bps') or m.get('cost') or 0
    ratio = (edge / cost) if cost else 0
    vpin = m.get('vpin')
    vpin_text = f'{vpin:.3f}' if vpin is not None else 'WARMUP'
    regime = m.get('regime', '?')
    blocked = ','.join(m.get('reasons') or m.get('blockers') or []) or m.get('status', '')
    print(f"{sym:<10} {cons:6.1%} {edge:7.2f} {cost:6.2f} {ratio:5.2f} {vpin_text:>6} {str(regime):<7} {blocked}")
print()
for k in ('threshold', 'mode', 'armed', 'quorum_floor', 'toxic_vpin'):
    if k in s:
        print(k, '=', s[k])
