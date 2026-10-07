"""Eight auditable heuristic roles. Suitability votes are not independent alpha."""
import math
from core.consensus_engine import Vote, ConsensusEngine
from core.net_ev import evaluate_net_ev, fill_probability, roundtrip_cost_bps


def adverse_selection(features, side, toxic_vpin):
    """Reject quotes exposed to a toxic sweep or an immediate flow reversal."""
    if features is None or side not in ('BUY', 'SELL') or not math.isfinite(toxic_vpin):
        return True
    values = (features.vpin, features.vpin_jump, features.flow_acceleration, features.sweep)
    if any(x is not None and not math.isfinite(x) for x in values):
        return True
    toxic = features.vpin is not None and features.vpin > toxic_vpin
    jump = features.vpin_jump >= .15
    reversal = features.flow_reversal and ((side == 'BUY' and features.flow_acceleration < 0) or
                                            (side == 'SELL' and features.flow_acceleration > 0))
    # A sweep is alpha only while it is orderly. A sweep plus a sudden VPIN jump,
    # reversal, or already-toxic VPIN is treated as an adverse-selection event for
    # both sides; do not quote into the queue that is being run over.
    toxic_sweep = bool(features.sweep) and (toxic or features.vpin_jump >= .10 or features.flow_reversal)
    return toxic or jump or reversal or toxic_sweep


class GroktopusSwarm:
    def __init__(self, threshold=.768, veto_threshold=.70, trend_floor=.70):
        self.consensus = ConsensusEngine(threshold=threshold, veto_threshold=veto_threshold,
                                         trend_floor=trend_floor)

    def evaluate(self, features, price, capacity_ok, risk_ok, gateway_ready, exits_ready, cross_momentum=0.0,
                 maker_fee=.0002, taker_fee=.0005, slippage_bps=1.0, toxic_vpin=.65, edge_multiple=1.8,
                 lead_lag=0.0, regime=None, ev_samples=None, ev_gross_sigma_bps=None,
                 cost_unfilled_bps=0.0, permit=None):
        """`permit` is a core.regime_permit.Permit from the slow brain. It can only
        BLOCK (RUNE veto with the permit's reason); it never adds confidence and
        never lifts another gate. None keeps the pre-permit behaviour."""
        f = features
        required = ('obi', 'ofi', 'flow', 'vwap_z', 'momentum', 'rsi', 'atr', 'spread_bps',
                    'micro_delta_ticks', 'flow_acceleration', 'vpin_jump', 'atr_ratio', 'variance_per_second')
        invalid = any(not math.isfinite(getattr(f, name)) for name in required)
        invalid |= not math.isfinite(price) or price <= 0 or any(not math.isfinite(x) for x in
                                                                  (cross_momentum, lead_lag, maker_fee, taker_fee, slippage_bps, toxic_vpin, edge_multiple))
        if regime is None:
            toxic_signal = ((f.vpin is not None and f.vpin > toxic_vpin) or
                            f.vpin_jump >= .15 or (f.sweep != 0 and f.flow_reversal))
            regime = 'TOXIC' if toxic_signal else (
                'TREND' if max(abs(f.momentum), abs(cross_momentum), abs(lead_lag)) > 1.2 else 'CHOP')
        directional = {
            'NORO': math.tanh(-f.vwap_z/(2*max(f.atr_ratio, .25)) + .25*f.momentum),
            'LUMEN': math.tanh(1.5*f.obi + f.flow + .2*f.ofi + .35*f.micro_delta_ticks + .25*f.flow_acceleration),
            'TIDAL': math.tanh(.6*f.momentum + .3*cross_momentum + .5*f.sweep + .5*lead_lag + (50-f.rsi)/100),
        }
        if invalid:
            directional = {name: 0.0 for name in directional}
        direction = 'BUY' if sum(directional.values()) > 0 else 'SELL'
        edge_bps = abs(sum(directional.values())/3)*f.atr/price*10000 if price else 0
        # A GTX entry rests inside the book and earns the spread; only the market exit
        # crosses it. Charging the full spread on both legs double counted a cost the
        # maker side never pays.
        cost_bps = (maker_fee+taker_fee)*10000+f.spread_bps/2+slippage_bps
        atr_bps = f.atr/price*10000 if price > 0 else 0
        # Calibrated net-EV head. The ATR term above measures gross excursion only; it
        # never asked whether the resting quote fills, so it priced an edge the book may
        # never hand over. EV weights that excursion by p_fill against a taker/taker
        # round trip, and LCB_95 refuses an edge statistically indistinguishable from
        # zero. Without a settled sample no interval exists, so the head reports
        # UNCALIBRATED and the edge/cost hurdle alone keeps gating -- it never
        # substitutes an invented interval for a missing one.
        ev_ready = (not invalid and math.isfinite(edge_bps) and math.isfinite(atr_bps)
                    and math.isfinite(f.spread_bps))
        p_fill, extrapolated = fill_probability(f.spread_bps, atr_bps) if ev_ready else (0.0, False)
        net_ev = evaluate_net_ev(p_fill, edge_bps if ev_ready else 0.0,
                                 cost_fill_bps=roundtrip_cost_bps(taker_fee),
                                 cost_unfilled_bps=cost_unfilled_bps,
                                 gross_sigma_bps=ev_gross_sigma_bps,
                                 samples=ev_samples if ev_ready else None,
                                 extrapolated=extrapolated)
        self.net_ev = net_ev
        self.diagnostics = {'edge_bps': edge_bps, 'cost_bps': cost_bps,
                            'edge_ratio': edge_bps/max(cost_bps, 1e-9),
                            'atr_bps': atr_bps,
                            'edge_model': net_ev.model,
                            'net_ev_bps': net_ev.ev_bps, 'lcb_95_bps': net_ev.lcb_bps,
                            'p_fill': net_ev.p_fill, 'ev_cost_bps': net_ev.cost_fill_bps,
                            'ev_samples': net_ev.samples, 'ev_hold': net_ev.hold,
                            'ev_extrapolated': net_ev.extrapolated}
        dangerous = invalid or adverse_selection(f, direction, toxic_vpin)
        liquid = (f.ready and f.vpin is not None and f.vpin <= toxic_vpin and not dangerous
                  and edge_bps >= edge_multiple*cost_bps and not net_ev.hold)
        votes = [Vote(name, 'BUY' if value > .05 else 'SELL' if value < -.05 else 'HOLD',
                      min(1, abs(value)), f'signal={value:.3f}') for name, value in directional.items()]
        toxic = f.vpin is not None and f.vpin > toxic_vpin
        zephr_reason = (f'edge/cost={edge_bps/max(cost_bps, 1e-9):.2f}'
                        + (f' · LCB95 {net_ev.lcb_bps:.2f}bps<=0' if net_ev.hold else '')
                        + (f' · VPIN {f.vpin:.2f}>{toxic_vpin:.2f}' if toxic else '')
                        + (' · ADVERSE SELECTION' if dangerous and not toxic else '')
                        + ('' if f.ready else ' · WARMUP'))
        permit_ok = permit is None or bool(permit.allowed)
        rune_reason = ('Risk / fee / toxicity gate' if permit_ok else
                       f'SLOW_BRAIN {permit.mode}: {permit.reason}')
        self.diagnostics['permit'] = None if permit is None else permit.as_dict()
        flags = [('ZEPHR', liquid, zephr_reason),
                 ('OKAPI', capacity_ok, 'Capital capacity'), ('RUNE', risk_ok and liquid and permit_ok, rune_reason),
                 ('MARIN', exits_ready, 'Exit readiness'), ('VESKA', gateway_ready, 'Gateway readiness')]
        votes.extend(Vote(name, direction if ok else 'HOLD', 1.0 if ok else 0.0, reason, veto=not ok)
                     for name, ok, reason in flags)
        return self.consensus.evaluate(votes, regime=regime), votes
