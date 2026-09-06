from dataclasses import replace
import pytest
from core.consensus_engine import ConsensusEngine, TokenAuthority, Vote, WEIGHTS
from core.models import Intent


def votes(confidence):
    return [Vote(n, 'BUY', confidence, 'test') for n in WEIGHTS]


@pytest.mark.parametrize('score,allowed,veto', [(.768, True, False), (.7679, False, False), (.70, False, False), (.6999, False, True), (1, True, False), (0, False, True)])
def test_boundaries(score, allowed, veto):
    result = ConsensusEngine().evaluate(votes(score))
    assert (result.allowed, result.veto) == (allowed, veto)


def test_veto_missing_duplicate_nonfinite():
    engine = ConsensusEngine()
    v = votes(1)
    v[4] = replace(v[4], veto=True)
    assert not engine.evaluate(v).allowed
    assert engine.evaluate(votes(1)[:-1]).veto
    assert engine.evaluate(votes(1)[:-1]+[votes(1)[0]]).veto
    assert engine.evaluate(votes(float('nan'))).veto


def intent():
    return Intent('BTCUSDT', 'BUY', '.01', '100', '99', '102', 300, 'testnet')


def test_token_replay_tamper_expiry():
    clock = [10.0]
    auth = TokenAuthority(lambda: clock[0])
    order = intent()
    decision = ConsensusEngine().evaluate(votes(1))
    token = auth.issue(order, decision)
    auth.consume(token, order)
    with pytest.raises(PermissionError):
        auth.consume(token, order)
    token = auth.issue(order, decision)
    with pytest.raises(PermissionError):
        auth.consume(token, replace(order, quantity='.02'))
    token = auth.issue(order, decision)
    clock[0] = 15.0
    with pytest.raises(PermissionError):
        auth.consume(token, order)


def test_weights_and_denied_issue():
    with pytest.raises(ValueError):
        ConsensusEngine({'RUNE': 1})
    with pytest.raises(PermissionError):
        TokenAuthority().issue(intent(), ConsensusEngine().evaluate(votes(.7)))
