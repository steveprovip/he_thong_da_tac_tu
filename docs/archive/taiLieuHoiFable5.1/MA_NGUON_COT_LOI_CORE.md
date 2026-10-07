# 💻 MÃ NGUỒN LÕI HỆ THỐNG (CORE PYTHON ENGINE)
### TẬP HỢP ĐẦY ĐỦ CÁC MODULE QUAN TRỌNG TRONG `core/`

Tài liệu này chứa nguyên vẹn mã nguồn của 6 tệp then chốt cấu thành nên GrokTOPUS 2.0 để Claude Fable 5.1 có thể phân tích trực tiếp trong bộ nhớ context:

---

## 1. `core/consensus_engine.py` (Động cơ đồng thuận & Thẩm quyền Token)
```python
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
    def __init__(self, weights=None, threshold=.768, veto_threshold=.70):
        self.weights = dict(WEIGHTS if weights is None else weights)
        self.threshold = threshold
        self.veto_threshold = veto_threshold
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
        veto = hard or score < self.veto_threshold - 1e-12
        allowed = not veto and score >= self.threshold - 1e-12
        return Decision(side, score, allowed, veto, 'AUTHORIZED' if allowed else 'RUNE VETO' if veto else f'BELOW {self.threshold*100:.1f}%')


class TokenAuthority:
    def __init__(self, clock=time.monotonic, threshold=.768):
        self._key = secrets.token_bytes(32)
        self._issued = {}
        self._lock = threading.Lock()
        self.clock = clock
        self.threshold = threshold

    @staticmethod
    def _digest(intent):
        return hashlib.sha256(json.dumps(asdict(intent), sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    def issue(self, intent, decision):
        if not decision.allowed or decision.veto or decision.score < self.threshold - 1e-12 or decision.side != intent.side:
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
```

---

## 2. `core/groktopus_swarm.py` (Đánh giá biểu quyết 8 Tác tử)
```python
"""Eight auditable heuristic roles. Suitability votes are not independent alpha."""
import math
from core.consensus_engine import Vote, ConsensusEngine


class GroktopusSwarm:
    def __init__(self, threshold=.768, veto_threshold=.70):
        self.consensus = ConsensusEngine(threshold=threshold, veto_threshold=veto_threshold)

    def evaluate(self, features, price, capacity_ok, risk_ok, gateway_ready, exits_ready, cross_momentum=0.0,
                 maker_fee=.0002, taker_fee=.0005, slippage_bps=1.0, toxic_vpin=.65, edge_multiple=1.8):
        f = features
        directional = {
            'NORO': math.tanh(-f.vwap_z/2 + .25*f.momentum),
            'LUMEN': math.tanh(1.5*f.obi + f.flow + .2*f.ofi),
            'TIDAL': math.tanh(.6*f.momentum + .3*cross_momentum + .5*f.sweep + (50-f.rsi)/100),
        }
        direction = 'BUY' if sum(directional.values()) > 0 else 'SELL'
        edge_bps = abs(sum(directional.values())/3)*f.atr/price*10000 if price else 0
        # A GTX entry rests inside the book and earns the spread; only the market exit
        # crosses it. Charging the full spread on both legs double counted a cost the
        # maker side never pays.
        cost_bps = (maker_fee+taker_fee)*10000+f.spread_bps/2+slippage_bps
        liquid = f.ready and f.vpin is not None and f.vpin <= toxic_vpin and edge_bps >= edge_multiple*cost_bps
        votes = [Vote(name, 'BUY' if value > .05 else 'SELL' if value < -.05 else 'HOLD',
                      min(1, abs(value)), f'signal={value:.3f}') for name, value in directional.items()]
        toxic = f.vpin is not None and f.vpin > toxic_vpin
        zephr_reason = (f'edge/cost={edge_bps/max(cost_bps, 1e-9):.2f}'
                        + (f' · VPIN {f.vpin:.2f}>{toxic_vpin:.2f}' if toxic else '')
                        + ('' if f.ready else ' · WARMUP'))
        flags = [('ZEPHR', liquid, zephr_reason),
                 ('OKAPI', capacity_ok, 'Capital capacity'), ('RUNE', risk_ok and liquid, 'Risk / fee / toxicity gate'),
                 ('MARIN', exits_ready, 'Exit readiness'), ('VESKA', gateway_ready, 'Gateway readiness')]
        votes.extend(Vote(name, direction if ok else 'HOLD', 1.0 if ok else 0.0, reason, veto=not ok)
                     for name, ok, reason in flags)
        return self.consensus.evaluate(votes), votes
```

---

## 3. `core/real_alpha_engine.py` (Động cơ Alpha & VolumeBuckets VPIN)
```python
"""Causal, bounded-memory features; VPIN uses observed aggressor classification."""
from collections import deque
from dataclasses import dataclass, asdict
import math
import statistics


def calibrate_bucket_volume(trades, seconds=15.0, floor_multiple=25, minimum=1e-9):
    quantities = sorted(float(t['q']) for t in trades)
    if not quantities:
        return minimum
    p95 = quantities[min(len(quantities) - 1, int(.95 * len(quantities)))]
    # Winsorize extreme outlier prints (> 3 * p95) so rare mega-prints (common on Testnet)
    # do not inflate bucket volume into taking hours to fill.
    cap = max(p95 * 3.0, minimum)
    total = math.fsum(min(q, cap) for q in quantities)
    stamps = [float(t['T']) / 1000 for t in trades if 'T' in t]
    span = (max(stamps) - min(stamps)) if len(stamps) > 1 else 0.0
    per_window = total / span * seconds if span > 0 else total / 100
    return max(per_window, floor_multiple * p95, minimum)


class VolumeBuckets:
    def __init__(self, volume=10.0, window=50, min_buckets=None):
        if not math.isfinite(volume) or volume <= 0 or window < 1:
            raise ValueError('Invalid bucket settings')
        self.volume, self.window = volume, window
        self.min_buckets = window if min_buckets is None else max(1, min(int(min_buckets), window))
        self.imbalances = deque(maxlen=window)
        self.filled = self.delta = 0.0

    def add(self, quantity, buyer_aggressor):
        if not math.isfinite(quantity) or quantity <= 0:
            raise ValueError('Invalid trade volume')
        # Outlier guard: a single execution print cannot physically represent dozens of future buckets.
        # It caps at 2.0x bucket volume to complete the current bucket and at most 1 spillover.
        remaining = min(quantity, self.volume * 2.0)
        while remaining > 1e-12:
            take = min(remaining, self.volume - self.filled)
            self.filled += take
            self.delta += take if buyer_aggressor else -take
            remaining -= take
            if self.filled >= self.volume - 1e-10:
                self.imbalances.append(abs(self.delta))
                self.filled = self.delta = 0.0

    @property
    def value(self):
        if len(self.imbalances) < self.min_buckets:
            return None
        return min(1.0, math.fsum(self.imbalances) / (len(self.imbalances) * self.volume))

    @property
    def saturation(self):
        if not self.imbalances:
            return None
        return sum(x >= self.volume - 1e-9 for x in self.imbalances) / len(self.imbalances)


@dataclass(frozen=True)
class Features:
    obi: float = 0
    ofi: float = 0
    cvd: float = 0
    flow: float = 0
    vpin: float | None = None
    vpin_saturation: float | None = None
    vwap_z: float = 0
    momentum: float = 0
    rsi: float = 50
    atr: float = 0
    sweep: int = 0
    spread_bps: float = 0
    ready: bool = False

    def to_dict(self):
        return asdict(self)


class AlphaEngine:
    def __init__(self, bucket_volume=10.0, bucket_window=50, window=120, min_buckets=None):
        self.min_buckets = min_buckets
        self.buckets = VolumeBuckets(bucket_volume, bucket_window, min_buckets)
        self.trades = deque(maxlen=window)
        self.bars = deque(maxlen=window)
        self.ofis = deque(maxlen=window)
        self.cvd = 0.0
        self.book = None
        self.last_trade_id = -1
        self.last_trade_time = -1.0
        self.sweep = 0

    def on_trade(self, price, quantity, buyer_aggressor, timestamp, trade_id):
        if trade_id <= self.last_trade_id or timestamp < self.last_trade_time:
            return False
        if any(not math.isfinite(x) or x <= 0 for x in (price, quantity)):
            raise ValueError('Invalid trade')
        self.buckets.add(quantity, buyer_aggressor)
        self.cvd += quantity if buyer_aggressor else -quantity
        self.trades.append((price, quantity, 1 if buyer_aggressor else -1))
        self.last_trade_id, self.last_trade_time = trade_id, timestamp
        return True

    def on_bar(self, timestamp, high, low, close):
        if self.bars and timestamp <= self.bars[-1][0]:
            return False
        if not all(math.isfinite(x) for x in (timestamp, high, low, close)) or not 0 < low <= close <= high:
            raise ValueError('Invalid closed bar')
        previous = list(self.bars)[-20:]
        self.sweep = 0
        if len(previous) >= 5:
            old_high, old_low = max(b[1] for b in previous), min(b[2] for b in previous)
            if low < old_low and close > old_low:
                self.sweep = 1
            elif high > old_high and close < old_high:
                self.sweep = -1
        self.bars.append((timestamp, high, low, close))
        return True

    def on_book(self, book):
        if self.book:
            old = self.book
            bp, bq = book.bids[0]
            ap, aq = book.asks[0]
            obp, obq = old.bids[0]
            oap, oaq = old.asks[0]
            ofi = (bq if bp >= obp else 0) - (obq if bp <= obp else 0)
            ofi -= (aq if ap <= oap else 0) - (oaq if ap >= oap else 0)
            self.ofis.append(ofi)
        self.book = book
        return True

    def reset_flow(self):
        self.buckets = VolumeBuckets(self.buckets.volume, self.buckets.window, self.min_buckets)
        self.trades.clear()
        self.ofis.clear()
        self.book = None
        self.cvd = 0

    def features(self):
        if not self.book:
            return Features()
        b, a = sum(q for _, q in self.book.bids[:5]), sum(q for _, q in self.book.asks[:5])
        volume = sum(q for _, q, _ in self.trades)
        vwap = sum(p*q for p, q, _ in self.trades) / volume if volume else self.book.mid
        variance = sum(q*(p-vwap)**2 for p, q, _ in self.trades) / volume if volume else 0
        vwap_z = (self.book.mid-vwap) / math.sqrt(variance) if variance > 1e-16 else 0
        bars = list(self.bars)
        changes = [bars[i][3]-bars[i-1][3] for i in range(1, len(bars))][-14:]
        true_ranges = [max(bars[i][1]-bars[i][2], abs(bars[i][1]-bars[i-1][3]), abs(bars[i][2]-bars[i-1][3])) for i in range(1, len(bars))][-14:]
        gain, loss = sum(max(x, 0) for x in changes), sum(max(-x, 0) for x in changes)
        rsi = 100*gain/(gain+loss) if gain+loss else 50
        scale = statistics.pstdev(changes) if len(changes) > 1 else 0
        momentum = statistics.mean(changes)/scale if scale > 1e-12 else 0
        return Features((b-a)/(b+a), sum(self.ofis)/(b+a), self.cvd,
                        sum(q*s for _, q, s in self.trades)/volume if volume else 0,
                        self.buckets.value, self.buckets.saturation, vwap_z, momentum, rsi,
                        statistics.mean(true_ranges) if true_ranges else 0, self.sweep,
                        self.book.spread_bps, len(bars) >= 20 and len(self.trades) >= 20 and self.buckets.value is not None)
```

---

## 4. `core/barrier_planner.py` (Kế hoạch Ba Rào cản & Ratchet)
```python
from dataclasses import dataclass
import math
from core.models import quantize


@dataclass(frozen=True)
class Barriers:
    stop: float
    take_profit: float
    deadline: float


class BarrierPlanner:
    def __init__(self, tp_atr=2.5, sl_atr=1.5, horizon=300.0, roundtrip_fee=.0007):
        if any(not math.isfinite(x) or x <= 0 for x in (tp_atr, sl_atr, horizon)) or roundtrip_fee < 0:
            raise ValueError('Invalid barrier settings')
        self.tp_atr, self.sl_atr, self.horizon, self.fee = tp_atr, sl_atr, horizon, roundtrip_fee

    def plan(self, side, entry, atr, tick, now):
        if side not in ('BUY', 'SELL') or min(entry, atr, tick) <= 0:
            raise ValueError('Invalid barrier inputs')
        sign = 1 if side == 'BUY' else -1
        stop = float(quantize(entry-sign*max(self.sl_atr*atr, 2*tick), tick, up=sign < 0))
        tp = float(quantize(entry+sign*max(self.tp_atr*atr, 2*tick), tick, up=sign > 0))
        if min(stop, tp) <= 0:
            raise ValueError('Non-positive barrier')
        return Barriers(stop, tp, now+self.horizon)

    def update(self, side, entry, mark, tick, opened, now, initial, current_stop):
        sign = 1 if side == 'BUY' else -1
        elapsed = max(0.0, min(1.0, (now-opened)/self.horizon))
        distance = max(abs(initial.take_profit-entry)*(1-.75*elapsed), entry*self.fee+2*tick)
        tp = entry+sign*distance
        stop = current_stop
        if sign*(mark-entry)/entry >= .0025:
            candidate = float(quantize(entry*(1+sign*self.fee), tick, up=sign > 0))
            if sign*(mark-candidate) >= 2*tick:
                stop = max(stop, candidate) if sign > 0 else min(stop, candidate)
        return Barriers(stop, float(quantize(tp, tick, up=sign > 0)), initial.deadline)
```

---

## 5. `core/position_sizer.py` (Định cỡ Vị thế Kelly & Thắng gấp Drawdown)
```python
from collections import deque
from dataclasses import dataclass
import math
from core.models import dec, quantize


@dataclass(frozen=True)
class SizeResult:
    quantity: str
    notional: float
    reason: str


class PositionSizer:
    def __init__(self, fraction=.25, min_samples=20, max_position=.15, risk_per_trade=.005):
        if not .10 <= fraction <= .25 or min_samples < 2 or not 0 < max_position <= .75 or not 0 < risk_per_trade < 1:
            raise ValueError('Invalid sizing configuration')
        self.fraction, self.min_samples = fraction, min_samples
        self.max_position, self.risk_per_trade = max_position, risk_per_trade
        self.outcomes = deque(maxlen=200)

    def record(self, net_pnl):
        if not math.isfinite(net_pnl):
            raise ValueError('Invalid PnL')
        self.outcomes.append(net_pnl)

    def kelly(self):
        if len(self.outcomes) < self.min_samples:
            return None
        wins, losses = [x for x in self.outcomes if x > 0], [-x for x in self.outcomes if x < 0]
        if not wins:
            return 0.0
        if not losses:
            return self.max_position
        p = len(wins)/len(self.outcomes)
        b = (sum(wins)/len(wins))/(sum(losses)/len(losses))
        return min(self.max_position, max(0, self.fraction*(p*b-(1-p))/b))

    def size(self, equity, used_margin, price, stop, filters, bootstrap=False, available=None, roundtrip_fee=.0007):
        if any(not math.isfinite(v) for v in (equity, used_margin, price, stop, roundtrip_fee)) or equity <= 0 or min(price, stop) <= 0 or used_margin < 0:
            return SizeResult('0', 0, 'INVALID_CAPITAL')
        kelly = self.kelly()
        if kelly is None and not bootstrap:
            return SizeResult('0', 0, 'KELLY_WARMUP')
        if kelly is not None and kelly <= 0:
            return SizeResult('0', 0, 'NONPOSITIVE_EDGE')
        minimum = filters.min_notional*dec('1.10')
        target = max(minimum, dec(equity*(kelly if kelly is not None else .01)))
        capacity = min(equity*self.max_position, max(0, equity*.75-used_margin),
                       available if available is not None else equity,
                       equity*self.risk_per_trade / max(abs(price-stop)/price+roundtrip_fee, 1e-9))
        qty = max(filters.min_qty, quantize(target/dec(price), filters.step, up=True))
        notional = qty*dec(price)
        if qty > filters.max_qty or notional > dec(capacity) or notional < minimum:
            return SizeResult('0', 0, 'MIN_NOTIONAL_EXCEEDS_RISK_CAP')
        return SizeResult(format(qty, 'f'), float(notional), 'OK')


class DrawdownBrake:
    def __init__(self, max_drawdown=.08, max_losses=10):
        self.max_drawdown, self.max_losses = max_drawdown, max_losses
        self.peak = 0.0
        self.losses = 0
        self.halted = False

    def update(self, equity, pnl=None):
        if not math.isfinite(equity) or equity <= 0:
            self.halted = True
            return True
        self.peak = max(self.peak, equity)
        if pnl is not None:
            self.losses = self.losses+1 if pnl < 0 else 0
        self.halted = self.halted or (1-equity/self.peak >= self.max_drawdown) or self.losses >= self.max_losses
        return self.halted
```
