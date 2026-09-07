"""Counterfactual signal recorder: measures the trades the gates never let happen.

Every cycle the swarm forms an opinion on every symbol, but ZEPHR and RUNE stop
almost all of them before they become orders. So the only outcomes the system ever
observes are the ones the gate already approved -- exactly the sample that cannot
tell you whether the gate is calibrated. Without this, "raise the threshold until
it trades" is the only available move, and it is a guess.

The lab opens a paper probe for every directional signal, gated or not, plans the
same triple barrier the live planner would, and resolves it against later books.
It is a pure observer: it reads state and appends rows, and holds no reference to
the gateway.

Honest limits, so the numbers are not over-read:
  * Entry is assumed filled at the quoted maker price. Real GTX entries queue and
    some never fill, so measured hit rates are optimistic on entry selection.
  * Barriers resolve against top-of-book, not against the venue's trigger logic.
  * Costs are modelled, not charged: `net_bps` subtracts `cost_bps`, which
    defaults to the roundtrip commission MEASURED on the venue rather than the
    swarm's conservative estimate. Reconciling 14 audited round trips against
    /fapi/v1/userTrades fits fee = 2bps*entry_notional + 4bps*exit_notional to a
    mean absolute error of 5e-6 bps, i.e. maker entry + taker exit = 6.00 bps.
    No spread term is added: `gross_bps` is already measured bid->bid for a long
    (ask->ask for a short), so the entry quote and the exit mark carry it.
These assumptions do not validate executable profits and cannot justify
loosening a risk gate. Records are diagnostic counterfactuals only.
"""
from dataclasses import asdict, dataclass, field
import json
import math
import time

# Roundtrip commission measured on the venue: 2 bps maker entry + 4 bps taker
# exit. Verified against /fapi/v1/userTrades on 14 audited round trips (mean
# absolute fit error 5e-6 bps). The previous hardcoded 8.0 overstated cost by
# 2 bps and biased every calibrate_edge verdict in the same direction.
MEASURED_ROUNDTRIP_BPS = 6.0


@dataclass
class Probe:
    symbol: str
    side: str
    entry: float
    stop: float
    take_profit: float
    deadline: float
    opened: float
    conviction: float
    consensus: float
    edge_bps: float
    cost_bps: float
    atr_bps: float
    spread_bps: float
    vpin: float | None
    saturation: float | None
    gated: bool
    blockers: list = field(default_factory=list)
    top_alpha: str = ''
    outcome: str = ''
    exit_price: float = 0.0
    closed: float = 0.0
    mfe_bps: float = 0.0
    mae_bps: float = 0.0
    gross_bps: float = 0.0
    net_bps: float = 0.0

    @property
    def sign(self):
        return 1 if self.side == 'BUY' else -1

    def mark(self, book):
        """Exit side of the book: a long leaves at the bid, a short at the ask."""
        return book.bid if self.side == 'BUY' else book.ask


class SignalLab:
    """Opens, tracks and closes counterfactual probes. Never touches execution."""

    def __init__(self, path=None, max_open=1, min_gap=20.0, horizon=300.0, source='unspecified',
                 cost_bps=MEASURED_ROUNDTRIP_BPS):
        if max_open < 1 or min_gap <= 0:
            raise ValueError('Invalid lab configuration')
        if not math.isfinite(cost_bps) or cost_bps < 0:
            raise ValueError('Invalid lab cost model')
        self.cost_bps = float(cost_bps)
        self.path = path
        self.source = source
        self.max_open = max_open
        self.min_gap = min_gap
        self.horizon = horizon
        self.open = {}
        self.last_opened = {}
        self.closed = 0
        self.tally = {'TP': 0, 'SL': 0, 'TIMEOUT': 0}
        self.net_total = 0.0
        self.alpha_stats = {}

    # ---------------------------------------------------------------- opening
    def observe(self, symbol, features, book, decision, votes, plan, now, conviction):
        """Record the plan the runtime would have made, whether or not it may act."""
        if not features.ready or plan is None:
            return None
        probes = self.open.setdefault(symbol, [])
        if len(probes) >= self.max_open or now - self.last_opened.get(symbol, -math.inf) < self.min_gap:
            return None
        entry = book.bid if decision.side == 'BUY' else book.ask
        if entry <= 0 or not math.isfinite(entry):
            return None
        blockers = [v.agent for v in votes if v.veto]
        directional = [v for v in votes if v.agent in ('NORO', 'LUMEN', 'TIDAL')
                       and v.side == decision.side and v.confidence > 0]
        top_alpha = max(directional, key=lambda v: v.confidence).agent if directional else ''
        probe = Probe(symbol=symbol, side=decision.side, entry=entry, stop=plan.stop,
                      top_alpha=top_alpha,
                      take_profit=plan.take_profit, deadline=now + self.horizon, opened=now,
                      conviction=conviction, consensus=decision.score,
                      edge_bps=features.atr / entry * 10000 * conviction,
                      # Entry quote and exit bid/ask already include spread.
                      cost_bps=self.cost_bps,
                      atr_bps=features.atr / entry * 10000, spread_bps=features.spread_bps,
                      vpin=features.vpin, saturation=features.vpin_saturation,
                      gated=decision.allowed, blockers=blockers)
        probes.append(probe)
        self.last_opened[symbol] = now
        return probe

    # ---------------------------------------------------------------- closing
    def resolve(self, symbol, book, now):
        """Advance every open probe on this symbol against the current book."""
        probes = self.open.get(symbol)
        if not probes:
            return []
        mark = None
        done = []
        for probe in list(probes):
            mark = probe.mark(book)
            excursion = probe.sign * (mark - probe.entry) / probe.entry * 10000
            probe.mfe_bps = max(probe.mfe_bps, excursion)
            probe.mae_bps = min(probe.mae_bps, excursion)
            if probe.sign * (mark - probe.stop) <= 0:
                outcome = 'SL'
            elif probe.sign * (mark - probe.take_profit) >= 0:
                outcome = 'TP'
            elif now >= probe.deadline:
                outcome = 'TIMEOUT'
            else:
                continue
            probe.outcome, probe.exit_price, probe.closed = outcome, mark, now
            probe.gross_bps = excursion
            probe.net_bps = excursion - probe.cost_bps
            probes.remove(probe)
            self._record(probe)
            done.append(probe)
        return done

    def _record(self, probe):
        self.closed += 1
        self.tally[probe.outcome] += 1
        self.net_total += probe.net_bps
        key = probe.top_alpha or 'mixed'
        stats = self.alpha_stats.setdefault(key, {'n': 0, 'net_bps': 0.0, 'tp': 0})
        stats['n'] += 1
        stats['net_bps'] += probe.net_bps
        stats['tp'] += 1 if probe.outcome == 'TP' else 0
        if not self.path:
            return
        with open(self.path, 'a', encoding='utf-8') as handle:
            handle.write(json.dumps({**asdict(probe), 'recorded': time.time(), 'schema': 2,
                                    'source': self.source, 'evidence': 'counterfactual_assumed_fill'},
                                    sort_keys=True, allow_nan=False) + '\n')

    # ------------------------------------------------------------- reporting
    def summary(self):
        total = self.closed
        by_alpha = {}
        for key, stats in self.alpha_stats.items():
            if stats['n']:
                by_alpha[key] = {'n': stats['n'], 'net_bps': stats['net_bps']/stats['n'],
                                 'tp_rate': stats['tp']/stats['n']}
        if not total:
            return {'probes': 0, 'open': sum(len(v) for v in self.open.values()), 'by_alpha': by_alpha}
        return {'probes': total, 'open': sum(len(v) for v in self.open.values()),
                'tp': self.tally['TP'] / total, 'sl': self.tally['SL'] / total,
                'timeout': self.tally['TIMEOUT'] / total,
                'net_bps': self.net_total / total, 'by_alpha': by_alpha}
