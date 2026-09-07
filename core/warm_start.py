"""Descriptive summaries of assumed-fill probes; excluded from execution estimators.

These are not off-policy estimates of execution returns: fills, queue state,
previous inventory and real agent votes were not logged. Multiplying all wins
and losses by a constant does not reduce the binary Kelly ratio.
"""
import json
import math
from pathlib import Path

from core.consensus_engine import Vote

ALPHA_AGENTS = ('NORO', 'LUMEN', 'TIDAL')


def load_closed_probes(path):
    """Closed counterfactual probes only; malformed lines are skipped."""
    path = Path(path)
    if not path.exists():
        return []
    probes = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get('outcome') not in ('TP', 'SL', 'TIMEOUT'):
            continue
        if not isinstance(row.get('net_bps'), (int, float)) or not math.isfinite(row['net_bps']):
            continue
        probes.append(row)
    return probes


def policy_subset(probes, edge_multiple=1.8, toxic_vpin=.95, min_conviction=.5):
    """Signals the current gating policy would have accepted.

    The lab's edge_bps/cost_bps mirror the swarm's ZEPHR hurdle, vpin mirrors
    the RUNE toxicity gate and conviction is the directional score — so this
    subset is the counterfactual of THIS policy, not of the logging policy."""
    subset = []
    for p in probes:
        edge, cost = p.get('edge_bps'), p.get('cost_bps')
        conviction, side, vpin = p.get('conviction'), p.get('side'), p.get('vpin')
        if side not in ('BUY', 'SELL') or conviction is None or not min_conviction <= conviction <= 1:
            continue
        if edge is None or cost is None or cost <= 0 or edge < edge_multiple*cost:
            continue
        if vpin is None or not math.isfinite(vpin) or vpin > toxic_vpin:
            continue
        subset.append(p)
    return subset


def ope_summary(subset):
    """Direct off-policy estimate of the gated policy's expectancy (bps)."""
    nets = [float(p['net_bps']) for p in subset]
    if not nets:
        return {'n': 0, 'win_rate': None, 'mean_net_bps': None, 'sum_net_bps': 0.0}
    return {'n': len(nets), 'win_rate': sum(n > 0 for n in nets)/len(nets),
            'mean_net_bps': sum(nets)/len(nets), 'sum_net_bps': math.fsum(nets)}


def seed_estimators(sizer, consensus, probes, haircut=.75, cap=200):
    """Compatibility boundary: assumed fills never mutate capital estimators."""
    raise ValueError('Counterfactual assumed fills cannot seed Kelly or agent votes')


def warm_start(sizer, consensus=None, sources=(), edge_multiple=1.8, toxic_vpin=.65,
               min_probes=20, cap=200, min_mean_net_bps=0.0):
    """Report every source, including negative periods; never pick a winning log."""
    reports = []
    for label, path, haircut, seed_consensus in sources:
        subset = policy_subset(load_closed_probes(path), edge_multiple, toxic_vpin)
        reports.append({'source': label, **ope_summary(subset)})
    return {'seeded': 0, 'reason': 'ASSUMED_FILLS_NOT_EXECUTION_EVIDENCE',
            'sources': reports, 'n': sum(r['n'] for r in reports)}


if __name__ == '__main__':
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from core.position_sizer import PositionSizer
    mode = sys.argv[1] if len(sys.argv) > 1 else 'testnet'
    root = Path(__file__).resolve().parents[1]
    ordered = [(mode, root/'data'/f'{mode}-signal-lab.jsonl', .75, True),
               ('backtest', root/'data'/'backtest-signal-lab.jsonl', .5, False),
               ('paper', root/'data'/'paper-signal-lab.jsonl', .5, False)]
    print(json.dumps(warm_start(PositionSizer(), None, ordered), indent=2))
