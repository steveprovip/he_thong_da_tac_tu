"""Fixed policy quorum and order-bound, one-use HMAC-SHA256 authorization."""
from dataclasses import dataclass, asdict
import hashlib
import hmac
import json
import math
import secrets
import threading
import time

WEIGHTS = {'NORO': .20, 'LUMEN': .20, 'TIDAL': .20, 'ZEPHR': .10,
           'RUNE': .10, 'OKAPI': .08, 'MARIN': .07, 'VESKA': .05}


@dataclass(frozen=True)
class Vote:
    agent: str
    side: str
    confidence: float
    reason: str
    veto: bool = False


@dataclass(frozen=True)
class Decision:
    side: str
    score: float
    allowed: bool
    veto: bool
    reason: str


class ConsensusEngine:
    def __init__(self, weights=None):
        self.weights = dict(WEIGHTS if weights is None else weights)
        if set(self.weights) != set(WEIGHTS) or any(not math.isfinite(w) or w <= 0 for w in self.weights.values()):
            raise ValueError('Exactly eight positive weights required')
        total = sum(self.weights.values())
        self.weights = {k: w/total for k, w in self.weights.items()}

    def evaluate(self, votes):
        if len(votes) != 8 or {v.agent for v in votes} != set(self.weights):
            return Decision('HOLD', 0, False, True, 'Missing or duplicate agent')
        if any(v.side not in ('BUY', 'SELL', 'HOLD') or not math.isfinite(v.confidence) or not 0 <= v.confidence <= 1 for v in votes):
            return Decision('HOLD', 0, False, True, 'Invalid vote')
        scores = {s: math.fsum(self.weights[v.agent]*v.confidence for v in votes if v.side == s) for s in ('BUY', 'SELL')}
        side = max(scores, key=scores.get)
        score = scores[side]
        hard = any(v.veto for v in votes) or any(v.agent == 'RUNE' and v.side == 'HOLD' for v in votes)
        veto = hard or score < .70 - 1e-12
        allowed = not veto and score >= .768 - 1e-12
        return Decision(side, score, allowed, veto, 'AUTHORIZED' if allowed else 'RUNE VETO' if veto else 'BELOW 76.8%')


class TokenAuthority:
    def __init__(self, clock=time.monotonic):
        self._key = secrets.token_bytes(32)
        self._issued = {}
        self._lock = threading.Lock()
        self.clock = clock

    @staticmethod
    def _digest(intent):
        return hashlib.sha256(json.dumps(asdict(intent), sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    def issue(self, intent, decision):
        if not decision.allowed or decision.veto or decision.score < .768 - 1e-12 or decision.side != intent.side:
            raise PermissionError('Consensus denied')
        with self._lock:
            now = self.clock()
            self._issued = {n: v for n, v in self._issued.items() if v[0] > now}
            nonce = secrets.token_hex(16)
            digest = self._digest(intent)
            mac = hmac.new(self._key, (nonce+digest).encode(), hashlib.sha256).hexdigest()
            self._issued[nonce] = (now+5.0, digest, mac)
            return nonce+'.'+mac

    def consume(self, token, intent):
        with self._lock:
            nonce, sep, mac = token.partition('.')
            issued = self._issued.pop(nonce, None)
            if not sep or issued is None:
                raise PermissionError('Unknown or replayed token')
            deadline, digest, expected = issued
            if self.clock() >= deadline or digest != self._digest(intent) or not hmac.compare_digest(mac, expected):
                raise PermissionError('Expired or tampered token')
