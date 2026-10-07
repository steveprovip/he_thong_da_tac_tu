"""Fixed policy quorum and order-bound, one-use HMAC-SHA256 authorization."""
from collections import deque
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
    threshold: float = .768
    regime: str = 'NORMAL'
    # Agents whose vote carried veto=True. Attribution only: `veto` above is
    # still the single source of truth. Without it every blocked signal reads
    # 'RUNE VETO' and telemetry cannot tell a cost gate from a risk breach.
    blockers: tuple = ()


class ConsensusEngine:
    def __init__(self, weights=None, threshold=.768, veto_threshold=.70, trend_floor=.70):
        self.weights = dict(WEIGHTS if weights is None else weights)
        self.threshold = threshold
        self.veto_threshold = veto_threshold
        self.trend_floor = trend_floor
        if not all(math.isfinite(x) for x in (threshold, veto_threshold, trend_floor)) or not 0 <= veto_threshold <= threshold <= 1 or threshold <= 0 or not 0 < trend_floor <= 1:
            raise ValueError('Invalid consensus thresholds')
        self.performance = {n: deque(maxlen=60) for n in ('NORO', 'LUMEN', 'TIDAL')}
        if set(self.weights) != set(WEIGHTS) or any(not math.isfinite(w) or w <= 0 for w in self.weights.values()):
            raise ValueError('Exactly eight positive weights required')
        total = sum(self.weights.values())
        self.weights = {k: w/total for k, w in self.weights.items()}

    def threshold_for(self, regime='NORMAL'):
        regime = str(regime or 'NORMAL').upper()
        if regime == 'TOXIC':
            return self.threshold
        if regime == 'TREND':
            return max(self.veto_threshold, min(self.threshold, self.trend_floor))
        if regime == 'CHOP':
            return max(self.threshold, self.trend_floor)
        if regime != 'NORMAL':
            raise ValueError('Unknown market regime')
        return self.threshold

    def record_outcome(self, votes, side, pnl):
        """Bayesian-like bounded update for alpha weights only.

        Safety-gate weights remain policy constants; successful alpha agents share
        exactly the original 60% alpha budget, so learning cannot subsidize a gate.
        """
        if side not in ('BUY', 'SELL') or not math.isfinite(pnl):
            raise ValueError('Invalid outcome')
        for vote in votes:
            if vote.agent in self.performance:
                self.performance[vote.agent].append(1 if pnl > 0 and vote.side == side else 0)
        alpha = tuple(n for n in ('NORO', 'LUMEN', 'TIDAL'))
        alpha_budget = math.fsum(WEIGHTS[n] for n in alpha)
        raw = {n: (1 + sum(self.performance[n])) / (2 + len(self.performance[n])) for n in alpha}
        total = math.fsum(raw.values())
        for n in alpha:
            self.weights[n] = alpha_budget * raw[n] / total
        for n in self.weights:
            if n not in alpha:
                self.weights[n] = WEIGHTS[n]

    def evaluate(self, votes, regime='NORMAL'):
        required = self.threshold_for(regime)
        if len(votes) != 8 or {v.agent for v in votes} != set(self.weights):
            return Decision('HOLD', 0, False, True, 'Missing or duplicate agent', required, regime)
        if any(v.side not in ('BUY', 'SELL', 'HOLD') or not math.isfinite(v.confidence) or not 0 <= v.confidence <= 1 for v in votes):
            return Decision('HOLD', 0, False, True, 'Invalid vote', required, regime)
        scores = {s: math.fsum(self.weights[v.agent]*v.confidence for v in votes if v.side == s) for s in ('BUY', 'SELL')}
        side = max(scores, key=scores.get)
        score = scores[side]
        hard = regime.upper() == 'TOXIC' or any(v.veto for v in votes) or any(v.agent == 'RUNE' and v.side == 'HOLD' for v in votes)
        veto = hard or score < self.veto_threshold - 1e-12
        allowed = not veto and score >= required - 1e-12
        blockers = tuple(v.agent for v in votes if v.veto)
        reason = ('AUTHORIZED' if allowed else
                  (f'RUNE VETO [{",".join(blockers)}]' if blockers else 'RUNE VETO') if veto else
                  f'BELOW {required*100:.1f}%')
        return Decision(side, score, allowed, veto, reason, required, regime.upper(), blockers)


class TokenAuthority:
    def __init__(self, clock=time.monotonic, threshold=.768, policy=None):
        self._key = secrets.token_bytes(32)
        self._issued = {}
        self._lock = threading.Lock()
        self.clock = clock
        self.threshold = threshold
        self.policy = policy
        if not math.isfinite(threshold) or not 0 < threshold <= 1:
            raise ValueError('Invalid token threshold')

    @staticmethod
    def _digest(intent):
        return hashlib.sha256(json.dumps(asdict(intent), sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    def issue(self, intent, decision):
        required = self.policy.threshold_for(decision.regime) if self.policy else self.threshold
        if (not decision.allowed or decision.veto or decision.score < required - 1e-12 or
                decision.threshold + 1e-12 < required or decision.side != intent.side):
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

    def issue_pair(self, intent, decision):
        from core.models import PairIntent
        if not isinstance(intent, PairIntent) or decision.score < .768 or decision.threshold < .768:
            raise PermissionError('Pair requires the fixed 76.8% policy')
        return PairAuthToken(self.issue(intent, decision))

    def consume_pair(self, token, intent):
        from core.models import PairIntent
        if not isinstance(token, PairAuthToken) or not isinstance(intent, PairIntent):
            raise PermissionError('Pair-bound authorization required')
        self.consume(token.value, intent)


@dataclass(frozen=True)
class PairAuthToken:
    value: str
