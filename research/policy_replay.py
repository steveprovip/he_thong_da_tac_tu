"""Dream-RSI: replay the Signal Lab as an off-policy simulator.

History as a replay simulator (DeepMind, arXiv:2609.14858). The Signal Lab
already opened a counterfactual probe for every directional signal the swarm
formed -- gated or not -- and resolved it against later books. That file is a
logged dataset of (context, action, outcome). So "what would a different gate
have earned" is an off-policy question that can be answered by re-reading the
log, with **0 live orders, 0 LLM calls and 0 exchange connections**.

What is replayable, and what is not
-----------------------------------
A probe records the aggregate consensus score, the edge and cost in bps, the
realised excursion, and which agents blocked it. From that, exactly two gates
can be re-derived honestly:

  * ZEPHR -- the edge hurdle `edge_bps >= m * cost_bps`, with m the
    `edge_multiple`. The probe carries both terms, so any m in [1.0, 2.5] is a
    pure re-evaluation, not an extrapolation.
  * RUNE  -- the consensus veto `consensus >= tau_v`, tau_v in [0.70, 0.768].

Anything else in `blockers` (VESKA gateway readiness, MARIN exit readiness) is
an *infrastructure* state, not a policy knob. No choice of m or tau_v makes a
disconnected private stream connected, so rows carrying such a blocker are
excluded from the statistics and counted, never silently treated as tradeable.

Directional weights
-------------------
The alpha block NORO + LUMEN + TIDAL owns a fixed 0.60 of the consensus budget.
The probe stores the aggregate score and `top_alpha` (the leading directional
agent), but not the per-agent split -- so a re-weighting is only defensible for
rows that carry `top_alpha`, and only as a first-order shift:

    consensus'(w) = consensus + (w[top_alpha] - 0.20) * conviction

Rows without `top_alpha` cannot be re-weighted at all. The default is to drop
them from a re-weighted run rather than leave them at baseline: leaving 36% of
the sample untouched would make every weight vector score nearly the same and
hide exactly the effect being measured.

Honest limits inherited from the lab itself: entry is assumed filled at the
quoted maker price, barriers resolve against top-of-book rather than the
venue's trigger logic, and costs are modelled. This engine cannot make those
assumptions truer. It can only stop a second set of errors being added on top.
"""
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path

__all__ = ['PolicyReplayEngine', 'ReplayProbe', 'PolicyCandidate', 'PolicyResult',
           'ReplaySchemaError', 'ALPHA_AGENTS', 'ALPHA_BUDGET', 'BASELINE_WEIGHT',
           'DERIVABLE_BLOCKERS', 'COST6', 'COST8', 'BASELINE', 'lcb', 'cvar',
           'edge_multiple_grid', 'veto_grid']

ALPHA_AGENTS = ('NORO', 'LUMEN', 'TIDAL')
ALPHA_BUDGET = 0.60                 # w_NORO + w_LUMEN + w_TIDAL, fixed by policy
BASELINE_WEIGHT = 0.20              # each alpha agent's shipped weight
DERIVABLE_BLOCKERS = frozenset({'ZEPHR', 'RUNE'})

# Two cost models appear in the recorded probes. COST8 is the legacy hardcoded
# 8.0 bps; COST6 is the roundtrip MEASURED against /fapi/v1/userTrades (2 bps
# maker entry + 4 bps taker exit). COST8 overstates cost by 2 bps and biases
# every calibrate_edge verdict in the same direction, so a replay that mixes
# them without saying so is comparing two different games.
COST6 = 6.0
COST8 = 8.0

EDGE_MULTIPLE_RANGE = (1.0, 2.5)
VETO_RANGE = (0.70, 0.768)
Z_95 = 1.6448536269514722           # one-sided 95% normal quantile

SUPPORTED_SCHEMAS = (2, 3)


class ReplaySchemaError(ValueError):
    """A row that cannot be parsed strictly enough to replay."""


# ------------------------------------------------------------------- policies
@dataclass(frozen=True)
class PolicyCandidate:
    """One counterfactual gate. `weights` is None for the shipped 0.20/0.20/0.20."""
    edge_multiple: float = 1.8
    veto: float = 0.768
    weights: tuple = None           # (w_NORO, w_LUMEN, w_TIDAL), must sum to 0.60
    label: str = ''

    def __post_init__(self):
        if not (math.isfinite(self.edge_multiple)
                and EDGE_MULTIPLE_RANGE[0] <= self.edge_multiple <= EDGE_MULTIPLE_RANGE[1]):
            raise ValueError('edge_multiple must lie in [1.0, 2.5]')
        if not (math.isfinite(self.veto) and VETO_RANGE[0] <= self.veto <= VETO_RANGE[1]):
            raise ValueError('veto threshold must lie in [0.70, 0.768]')
        if self.weights is not None:
            w = tuple(float(x) for x in self.weights)
            if len(w) != 3 or any(not math.isfinite(x) or x < 0 for x in w):
                raise ValueError('weights must be three non-negative finite numbers')
            if not math.isclose(math.fsum(w), ALPHA_BUDGET, rel_tol=0, abs_tol=1e-9):
                raise ValueError('directional weights must sum to exactly 0.60')
            object.__setattr__(self, 'weights', w)

    @property
    def weight_map(self):
        w = self.weights or (BASELINE_WEIGHT,) * 3
        return dict(zip(ALPHA_AGENTS, w))

    @property
    def reweighted(self):
        return self.weights is not None

    def name(self):
        if self.label:
            return self.label
        base = 'm=%.3f tau=%.4f' % (self.edge_multiple, self.veto)
        return base if not self.reweighted else base + ' w=' + '/'.join(
            '%.3f' % x for x in self.weights)


BASELINE = PolicyCandidate(edge_multiple=1.8, veto=0.768, label='SHIPPED')


# ---------------------------------------------------------------------- probes
@dataclass(frozen=True)
class ReplayProbe:
    symbol: str
    side: str
    opened: float
    closed: float
    outcome: str
    consensus: float
    conviction: float
    edge_bps: float
    cost_bps: float
    gross_bps: float
    net_bps: float
    gated: bool
    blockers: tuple
    top_alpha: str
    schema: int
    tier: str                        # 'schema2' | 'schema3'
    cost_model: str                  # 'cost6' | 'cost8' | 'cost:<value>'

    @property
    def replayable(self):
        return not (set(self.blockers) - DERIVABLE_BLOCKERS)

    @property
    def attributable(self):
        return bool(self.top_alpha)

    def net_at(self, cost_bps):
        """Re-price this probe under a different cost model.

        Exact, not modelled: the recorder stores net = gross - cost, and the
        parser has already verified that identity holds on every row, so
        swapping the cost term is arithmetic rather than inference.
        """
        return self.gross_bps - float(cost_bps)

    def consensus_under(self, policy):
        """Consensus score after the first-order directional re-weighting."""
        if not policy.reweighted:
            return self.consensus
        if not self.top_alpha:
            raise ReplaySchemaError('cannot re-weight a probe without top_alpha')
        shift = policy.weight_map[self.top_alpha] - BASELINE_WEIGHT
        return self.consensus + shift * self.conviction

    def selected_by(self, policy, cost_bps=None):
        """Would this policy have taken the trade? ZEPHR then RUNE, same order."""
        if not self.replayable:
            return False
        cost = self.cost_bps if cost_bps is None else float(cost_bps)
        if cost <= 0 or self.edge_bps < policy.edge_multiple * cost:
            return False
        return self.consensus_under(policy) >= policy.veto - 1e-12


_REQUIRED = ('symbol', 'side', 'opened', 'closed', 'outcome', 'consensus', 'conviction',
             'edge_bps', 'cost_bps', 'gross_bps', 'net_bps', 'gated', 'blockers')


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def parse_row(row, line_no=0):
    """Strict Schema 2/3 parse. Raises ReplaySchemaError on anything doubtful.

    Schema tiers, as they actually exist on disk:
      * schema 2 without `top_alpha` -- the original counterfactual probe.
      * schema 2 WITH `top_alpha`, or schema 3 -- the attribution tier. The
        recorder started emitting `top_alpha` without bumping the version, so
        the feature tier is detected from the payload rather than trusted from
        the number. Legacy rows with no `schema` key at all are refused: their
        cost column drifts per-cycle and mixing them in silently would blend two
        different cost models into one average.
    """
    if not isinstance(row, dict):
        raise ReplaySchemaError('line %d: row is not an object' % line_no)
    schema = row.get('schema')
    if schema not in SUPPORTED_SCHEMAS:
        raise ReplaySchemaError('line %d: schema %r is not in %r (legacy pre-schema '
                                'probes are not replayable)' % (line_no, schema, SUPPORTED_SCHEMAS))
    missing = [k for k in _REQUIRED if k not in row]
    if missing:
        raise ReplaySchemaError('line %d: missing %s' % (line_no, ', '.join(missing)))
    numbers = {k: row[k] for k in ('opened', 'closed', 'consensus', 'conviction',
                                   'edge_bps', 'cost_bps', 'gross_bps', 'net_bps')}
    bad = sorted(k for k, v in numbers.items() if not _finite(v))
    if bad:
        raise ReplaySchemaError('line %d: non-finite %s' % (line_no, ', '.join(bad)))
    if not isinstance(row['blockers'], list) or any(not isinstance(b, str) for b in row['blockers']):
        raise ReplaySchemaError('line %d: blockers must be a list of agent names' % line_no)
    if not isinstance(row['gated'], bool):
        raise ReplaySchemaError('line %d: gated must be a boolean' % line_no)
    if row['side'] not in ('BUY', 'SELL'):
        raise ReplaySchemaError('line %d: side %r' % (line_no, row['side']))
    if row['outcome'] not in ('TP', 'SL', 'TIMEOUT'):
        raise ReplaySchemaError('line %d: outcome %r' % (line_no, row['outcome']))
    if row['cost_bps'] < 0:
        raise ReplaySchemaError('line %d: negative cost' % line_no)
    if not math.isclose(row['gross_bps'] - row['cost_bps'], row['net_bps'],
                        rel_tol=1e-9, abs_tol=1e-9):
        # Without this identity the cost model cannot be swapped, and a replay
        # priced at cost6 on a row whose net was computed some other way is a
        # fabricated number.
        raise ReplaySchemaError('line %d: net != gross - cost' % line_no)
    if not 0 <= row['consensus'] <= 1 or not 0 <= row['conviction'] <= 1:
        raise ReplaySchemaError('line %d: consensus/conviction outside [0, 1]' % line_no)
    top_alpha = row.get('top_alpha') or ''
    if top_alpha and top_alpha not in ALPHA_AGENTS:
        raise ReplaySchemaError('line %d: top_alpha %r' % (line_no, top_alpha))
    cost = float(row['cost_bps'])
    model = ('cost6' if math.isclose(cost, COST6, abs_tol=1e-9)
             else 'cost8' if math.isclose(cost, COST8, abs_tol=1e-9)
             else 'cost:%.4f' % cost)
    return ReplayProbe(
        symbol=str(row['symbol']), side=row['side'], opened=float(row['opened']),
        closed=float(row['closed']), outcome=row['outcome'],
        consensus=float(row['consensus']), conviction=float(row['conviction']),
        edge_bps=float(row['edge_bps']), cost_bps=cost, gross_bps=float(row['gross_bps']),
        net_bps=float(row['net_bps']), gated=bool(row['gated']),
        blockers=tuple(sorted(row['blockers'])), top_alpha=top_alpha,
        schema=int(schema), tier='schema3' if (schema == 3 or top_alpha) else 'schema2',
        cost_model=model)


# ------------------------------------------------------------------ statistics
def lcb(samples, z=Z_95):
    """One-sided 95% lower confidence bound on the TOTAL of `samples`.

    total = n*mean, sd(total) = sd*sqrt(n)  ->  LCB = n*mean - z*sd*sqrt(n).
    Fewer than two samples has no usable bound, and saying so beats returning
    the point estimate dressed as one.
    """
    values = [float(v) for v in samples]
    n = len(values)
    if n == 0:
        return 0.0
    if n == 1:
        return float('-inf')
    mean = math.fsum(values) / n
    var = math.fsum((v - mean) ** 2 for v in values) / (n - 1)
    return n * mean - z * math.sqrt(var) * math.sqrt(n)


def cvar(samples, level=0.95):
    """CVaR of LOSSES at `level`: mean of the worst (1-level) tail, >= 0.

    Returned positive, so a larger number is a worse tail.
    """
    values = sorted(float(v) for v in samples)
    if not values:
        return 0.0
    k = max(1, int(math.ceil(len(values) * (1.0 - level))))
    tail = values[:k]
    return max(0.0, -(math.fsum(tail) / len(tail)))


def edge_multiple_grid(steps=7):
    lo, hi = EDGE_MULTIPLE_RANGE
    return [lo + (hi - lo) * i / (steps - 1) for i in range(steps)] if steps > 1 else [lo]


def veto_grid(steps=5):
    lo, hi = VETO_RANGE
    return [lo + (hi - lo) * i / (steps - 1) for i in range(steps)] if steps > 1 else [lo]


@dataclass(frozen=True)
class PolicyResult:
    policy: PolicyCandidate
    name: str
    trades: int
    baseline_trades: int
    net_bps: float
    baseline_net_bps: float
    delta_bps: float
    lcb_95: float
    cvar_95: float
    objective: float
    hit_rate: float
    oos_probes: int
    skipped_unattributable: int

    def as_dict(self):
        return {k: getattr(self, k) for k in
                ('name', 'trades', 'baseline_trades', 'net_bps', 'baseline_net_bps',
                 'delta_bps', 'lcb_95', 'cvar_95', 'objective', 'hit_rate',
                 'oos_probes', 'skipped_unattributable')}


# --------------------------------------------------------------------- engine
class PolicyReplayEngine:
    """Off-policy evaluation over recorded Signal Lab probes.

    Touches no gateway, opens no socket, spends no token. The only input is a
    JSONL file already on disk.
    """

    def __init__(self, probes, rejected=None, path=None, cost_model='as_recorded',
                 train_fraction=0.6):
        self.path = path
        self.probes = tuple(sorted(probes, key=lambda p: (p.opened, p.symbol, p.side)))
        self.rejected = list(rejected or [])
        self.cost_model = cost_model
        if not 0 < train_fraction < 1:
            raise ValueError('train_fraction must lie strictly inside (0, 1)')
        self.train_fraction = float(train_fraction)
        self.replayable = tuple(p for p in self.probes if p.replayable)

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, source, root=None, strict=True, **kwargs):
        """Load `data/{mode}-signal-lab.jsonl`, or an explicit path.

        A mode name resolves to the v2 (schema-stamped) file first, because the
        un-suffixed legacy file holds pre-schema rows this engine refuses.
        """
        path = cls.resolve(source, root)
        probes, rejected = [], []
        with open(path, 'r', encoding='utf-8') as handle:
            for n, line in enumerate(handle, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    probes.append(parse_row(json.loads(line), n))
                except (ValueError, ReplaySchemaError) as error:
                    if strict and not isinstance(error, ReplaySchemaError):
                        raise
                    rejected.append({'line': n, 'reason': str(error)})
        if not probes:
            raise ReplaySchemaError('no replayable probes in %s (%d rejected)'
                                    % (path, len(rejected)))
        return cls(probes, rejected, path=str(path), **kwargs)

    @staticmethod
    def resolve(source, root=None):
        candidate = Path(source)
        if candidate.suffix == '.jsonl':
            if not candidate.exists():
                raise FileNotFoundError(str(candidate))
            return candidate
        base = Path(root) if root else Path(__file__).resolve().parent.parent
        for name in ('%s-signal-lab-v2.jsonl' % source, '%s-signal-lab.jsonl' % source):
            path = base / 'data' / name
            if path.exists():
                return path
        raise FileNotFoundError('no signal lab file for mode %r under %s' % (source, base / 'data'))

    # ------------------------------------------------------------------ views
    def cost_for(self, probe):
        if self.cost_model in (None, 'as_recorded'):
            return probe.cost_bps
        if self.cost_model == 'cost6':
            return COST6
        if self.cost_model == 'cost8':
            return COST8
        cost = float(self.cost_model)
        if not math.isfinite(cost) or cost < 0:
            raise ValueError('invalid cost model %r' % (self.cost_model,))
        return cost

    def split(self):
        """Chronological in-sample / out-of-sample split. Never shuffled."""
        rows = self.replayable
        cut = int(len(rows) * self.train_fraction)
        return rows[:cut], rows[cut:]

    def summary(self):
        tiers, costs, blockers = {}, {}, {}
        for p in self.probes:
            tiers[p.tier] = tiers.get(p.tier, 0) + 1
            costs[p.cost_model] = costs.get(p.cost_model, 0) + 1
            for b in p.blockers:
                blockers[b] = blockers.get(b, 0) + 1
        in_sample, oos = self.split()
        return {'path': self.path, 'probes': len(self.probes), 'rejected': len(self.rejected),
                'replayable': len(self.replayable),
                'infrastructure_vetoed': len(self.probes) - len(self.replayable),
                'attributable': sum(1 for p in self.replayable if p.attributable),
                'tiers': tiers, 'cost_models': costs, 'blockers': blockers,
                'cost_model': self.cost_model,
                'in_sample': len(in_sample), 'out_of_sample': len(oos),
                'live_orders': 0, 'llm_calls': 0}

    # -------------------------------------------------------------- evaluation
    def evaluate(self, policy, baseline=BASELINE, lam=1.0, rows=None):
        """Score one candidate out-of-sample.

        objective = LCB_95(total delta PnL_OOS) - lam * CVaR_95(loss on trades taken)
        """
        if not math.isfinite(lam) or lam < 0:
            raise ValueError('lambda must be finite and non-negative')
        rows = self.split()[1] if rows is None else tuple(rows)
        skipped = 0
        deltas, taken, base_net = [], [], 0.0
        for probe in rows:
            if policy.reweighted and not probe.attributable:
                skipped += 1
                continue
            cost = self.cost_for(probe)
            net = probe.net_at(cost)
            mine = probe.selected_by(policy, cost)
            theirs = probe.selected_by(baseline, cost)
            if mine:
                taken.append(net)
            if theirs:
                base_net += net
            deltas.append((net if mine else 0.0) - (net if theirs else 0.0))
        total = math.fsum(taken)
        tail = cvar(taken)
        bound = lcb(deltas)
        wins = sum(1 for v in taken if v > 0)
        return PolicyResult(
            policy=policy, name=policy.name(), trades=len(taken),
            baseline_trades=sum(1 for p in rows
                                if p.selected_by(baseline, self.cost_for(p))),
            net_bps=total, baseline_net_bps=base_net, delta_bps=math.fsum(deltas),
            lcb_95=bound, cvar_95=tail,
            objective=(bound - lam * tail) if math.isfinite(bound) else float('-inf'),
            hit_rate=(wins / len(taken)) if taken else 0.0,
            oos_probes=len(rows) - skipped, skipped_unattributable=skipped)

    def sweep(self, candidates=None, baseline=BASELINE, lam=1.0):
        """Rank candidates by the objective. Best first; ties broken by name."""
        candidates = list(candidates if candidates is not None else self.grid())
        results = [self.evaluate(c, baseline, lam) for c in candidates]
        results.sort(key=lambda r: (-r.objective, r.name))
        return results

    def grid(self, edge_steps=7, veto_steps=5, weights=None):
        """Cartesian product of the two derivable knobs, optionally re-weighted."""
        out = []
        for m in edge_multiple_grid(edge_steps):
            for tau in veto_grid(veto_steps):
                if weights:
                    out.extend(PolicyCandidate(m, tau, w) for w in weights)
                else:
                    out.append(PolicyCandidate(m, tau))
        return out

    def best(self, candidates=None, baseline=BASELINE, lam=1.0):
        ranked = self.sweep(candidates, baseline, lam)
        return ranked[0] if ranked else None

    def report(self, results, limit=10):
        lines = ['# Dream-RSI off-policy replay', '',
                 'source: %s' % self.path, 'cost model: %s' % self.cost_model,
                 'live orders: 0 | LLM calls: 0', '',
                 '| policy | trades | net bps | delta bps | LCB95 | CVaR95 | objective |',
                 '|---|---:|---:|---:|---:|---:|---:|']
        for r in results[:limit]:
            lines.append('| %s | %d | %.2f | %.2f | %.2f | %.2f | %.2f |'
                         % (r.name, r.trades, r.net_bps, r.delta_bps,
                            r.lcb_95, r.cvar_95, r.objective))
        return '\n'.join(lines)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='Off-policy Signal Lab replay (no orders, no LLM).')
    ap.add_argument('mode', nargs='?', default='testnet')
    ap.add_argument('--cost-model', default='as_recorded',
                    choices=('as_recorded', 'cost6', 'cost8'))
    ap.add_argument('--lam', type=float, default=1.0)
    ap.add_argument('--train-fraction', type=float, default=0.6)
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    engine = PolicyReplayEngine.load(a.mode, cost_model=a.cost_model,
                                     train_fraction=a.train_fraction)
    results = engine.sweep(lam=a.lam)
    if a.json:
        print(json.dumps({'summary': engine.summary(),
                          'results': [r.as_dict() for r in results]}, indent=2, allow_nan=False))
    else:
        print(json.dumps(engine.summary(), indent=2, allow_nan=False))
        print()
        print(engine.report(results))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
