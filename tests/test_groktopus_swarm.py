from dataclasses import replace
from core.groktopus_swarm import GroktopusSwarm
from core.real_alpha_engine import Features


def test_all_roles_and_toxicity_veto():
    swarm = GroktopusSwarm()
    f = Features(obi=.9, ofi=1, flow=.9, vpin=.5, vwap_z=-8, momentum=4, rsi=50, atr=5, spread_bps=1, ready=True)
    decision, votes = swarm.evaluate(f, 100, True, True, True, True)
    assert len({v.agent for v in votes}) == 8
    assert decision.allowed
    decision, _ = swarm.evaluate(replace(f, vpin=.6501), 100, True, True, True, True)
    assert decision.veto
    decision, _ = swarm.evaluate(f, 100, False, True, True, True)
    assert decision.veto
