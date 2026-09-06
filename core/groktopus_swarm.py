"""Eight auditable heuristic roles. Suitability votes are not independent alpha."""
import math
from core.consensus_engine import Vote, ConsensusEngine


class GroktopusSwarm:
    def __init__(self):
        self.consensus = ConsensusEngine()

    def evaluate(self, features, price, capacity_ok, risk_ok, gateway_ready, exits_ready, cross_momentum=0.0, maker_fee=.0002, taker_fee=.0005):
        f = features
        directional = {
            'NORO': math.tanh(-f.vwap_z/2 + .25*f.momentum),
            'LUMEN': math.tanh(1.5*f.obi + f.flow + .2*f.ofi),
            'TIDAL': math.tanh(.6*f.momentum + .3*cross_momentum + .5*f.sweep + (50-f.rsi)/100),
        }
        direction = 'BUY' if sum(directional.values()) > 0 else 'SELL'
        edge_bps = abs(sum(directional.values())/3)*f.atr/price*10000 if price else 0
        cost_bps = f.spread_bps+(maker_fee+taker_fee)*10000+2.0
        liquid = f.ready and f.vpin is not None and f.vpin <= .65 and edge_bps >= 1.8*cost_bps
        votes = [Vote(name, 'BUY' if value > .05 else 'SELL' if value < -.05 else 'HOLD',
                      min(1, abs(value)), f'signal={value:.3f}') for name, value in directional.items()]
        flags = [('ZEPHR', liquid, f'edge/cost={edge_bps/max(cost_bps, 1e-9):.2f}'),
                 ('OKAPI', capacity_ok, 'Capital capacity'), ('RUNE', risk_ok and liquid, 'Risk / fee / toxicity gate'),
                 ('MARIN', exits_ready, 'Exit readiness'), ('VESKA', gateway_ready, 'Gateway readiness')]
        votes.extend(Vote(name, direction if ok else 'HOLD', 1.0 if ok else 0.0, reason, veto=not ok)
                     for name, ok, reason in flags)
        return self.consensus.evaluate(votes), votes
