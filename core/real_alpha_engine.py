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
    if any(not math.isfinite(q) or q <= 0 for q in quantities):
        raise ValueError('Invalid calibration volume')
    total = math.fsum(quantities)
    stamps = [float(t['T']) / 1000 for t in trades if 'T' in t]
    span = (max(stamps) - min(stamps)) if len(stamps) > 1 else 0.0
    per_window = total / span * seconds if span > 0 else total / 100
    return max(per_window, floor_multiple * p95, minimum)


class VolumeBuckets:
    def __init__(self, volume=10.0, window=50, min_buckets=None):
        if not math.isfinite(volume) or volume <= 0 or window < 1:
            raise ValueError('Invalid bucket settings')
        self.volume, self.window = volume, window
        # Wider buckets fill more slowly, so VPIN may report from a partial window.
        self.min_buckets = window if min_buckets is None else max(1, min(int(min_buckets), window))
        self.imbalances = deque(maxlen=window)
        self.filled = self.delta = 0.0
        self.total_volume = 0.0
        self.completed = 0

    def add(self, quantity, buyer_aggressor):
        if not math.isfinite(quantity) or quantity <= 0:
            raise ValueError('Invalid trade volume')
        # Preserve every observed unit. Large executions really can fill many
        # buckets; silently clipping them changes the VPIN definition.
        self.total_volume += quantity
        remaining = quantity
        while remaining > 1e-12:
            if self.filled == 0 and remaining >= self.volume:
                count = int(remaining / self.volume)
                self.imbalances.extend([self.volume] * min(count, self.window))
                self.completed += count
                remaining -= count * self.volume
                continue
            take = min(remaining, self.volume - self.filled)
            self.filled += take
            self.delta += take if buyer_aggressor else -take
            remaining -= take
            if self.filled >= self.volume - 1e-10:
                self.imbalances.append(abs(self.delta))
                self.completed += 1
                self.filled = self.delta = 0.0

    @property
    def value(self):
        if len(self.imbalances) < self.min_buckets:
            return None
        return min(1.0, math.fsum(self.imbalances) / (len(self.imbalances) * self.volume))

    @property
    def saturation(self):
        """Share of buckets that were completely one-sided - VPIN's degeneracy tell.

        Near 1.0 can indicate small buckets OR genuinely one-sided trading.
        Saturation alone cannot establish the cause or justify bypassing risk."""
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
    micro_price: float = 0
    micro_delta_ticks: float = 0
    flow_acceleration: float = 0
    flow_reversal: bool = False
    vpin_jump: float = 0
    atr_ratio: float = 1
    variance_per_second: float = 0
    fast_momentum: float = 0
    fast_return: float = 0
    fast_timestamp: float = -1
    vpin_baseline: float | None = None

    def to_dict(self):
        return asdict(self)


class AlphaEngine:
    def __init__(self, bucket_volume=10.0, bucket_window=50, window=120, min_buckets=None, tick_size=None):
        if tick_size is not None and (not math.isfinite(tick_size) or tick_size <= 0):
            raise ValueError('Invalid tick size')
        self.tick_size = tick_size
        self._cached = None
        self.trade_times = deque(maxlen=window)
        self.vpin_history = deque(maxlen=window)
        self.fast_changes = deque(maxlen=window)
        self.fast_anchor = None
        self.fast_momentum = self.fast_return = 0.0
        self.fast_timestamp = -1.0
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
        if any(not math.isfinite(x) or x <= 0 for x in (price, quantity)) or not math.isfinite(timestamp):
            raise ValueError('Invalid trade')
        self._cached = None
        self.trade_times.append(timestamp)
        self.buckets.add(quantity, buyer_aggressor)
        if self.buckets.value is not None:
            self.vpin_history.append((timestamp, self.buckets.value))
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
        self._cached = None
        self.bars.append((timestamp, high, low, close))
        return True

    def on_book(self, book):
        old = self.book
        if old and (book.sequence <= old.sequence or book.timestamp < old.timestamp):
            return False
        if old:
            bp, bq = book.bids[0]
            ap, aq = book.asks[0]
            obp, obq = old.bids[0]
            oap, oaq = old.asks[0]
            ofi = (bq if bp >= obp else 0) - (obq if bp <= obp else 0)
            ofi -= (aq if ap <= oap else 0) - (oaq if ap >= oap else 0)
            self.ofis.append(ofi)
        self._cached = None
        # Non-overlapping >=200ms returns, standardized against PRIOR observations.
        # An extreme move cannot inflate its own denominator. Gaps reset warm-up.
        if self.fast_anchor is None or book.timestamp-self.fast_anchor[0] > 2:
            self.fast_changes.clear()
            self.fast_anchor = (book.timestamp, book.mid)
            self.fast_momentum = self.fast_return = 0.0
            self.fast_timestamp = -1.0
        elif book.timestamp-self.fast_anchor[0] >= .2:
            stamp, mid = self.fast_anchor
            elapsed = book.timestamp-stamp
            change = (book.mid-mid)/mid
            variance = statistics.mean(r*r/dt for r, dt in self.fast_changes) if self.fast_changes else 0
            floor = (self.tick_size or book.ask-book.bid)/mid
            self.fast_momentum = max(-6., min(6., change/max(math.sqrt(variance*elapsed), floor))) if len(self.fast_changes) >= 20 else 0.
            self.fast_return, self.fast_timestamp = change, book.timestamp
            self.fast_changes.append((change, elapsed))
            self.fast_anchor = (book.timestamp, book.mid)
        self.book = book
        return True

    def reset_flow(self):
        self.buckets = VolumeBuckets(self.buckets.volume, self.buckets.window, self.min_buckets)
        self.trades.clear()
        self.ofis.clear()
        self.book = None
        self.cvd = 0
        self._cached = None
        self.trade_times.clear()
        self.vpin_history.clear()
        self.fast_changes.clear()
        self.fast_anchor = None
        self.fast_momentum = self.fast_return = 0.0
        self.fast_timestamp = -1.0

    def features(self):
        if self._cached is not None:
            return self._cached
        if not self.book:
            return Features()
        b, a = sum(q for _, q in self.book.bids[:5]), sum(q for _, q in self.book.asks[:5])
        bid, ask = self.book.bid, self.book.ask
        best_bid_volume, best_ask_volume = self.book.bids[0][1], self.book.asks[0][1]
        micro = (best_bid_volume * ask + best_ask_volume * bid) / (best_bid_volume + best_ask_volume)
        tick = self.tick_size or max(ask - bid, 1e-12)
        micro_delta = (micro - self.book.mid) / tick
        volume = sum(q for _, q, _ in self.trades)
        vwap = sum(p*q for p, q, _ in self.trades) / volume if volume else self.book.mid
        variance = sum(q*(p-vwap)**2 for p, q, _ in self.trades) / volume if volume else 0
        vwap_z = (self.book.mid-vwap) / math.sqrt(variance) if variance > 1e-16 else 0
        signed = [q*s for _, q, s in self.trades]
        n = min(len(signed), 20)
        half = n // 2
        if half:
            prior, recent = signed[-n:-half], signed[-half:]
        else:
            prior, recent = [], []
        prior_volume = sum(abs(x) for x in prior)
        recent_volume = sum(abs(x) for x in recent)
        prior_flow = math.fsum(prior) / prior_volume if prior_volume else 0
        recent_flow = math.fsum(recent) / recent_volume if recent_volume else 0
        flow_acceleration = recent_flow - prior_flow if half else 0
        flow_reversal = bool(prior_flow and recent_flow and prior_flow * recent_flow < 0 and abs(flow_acceleration) >= .2)
        vpin = self.buckets.value
        vpin_jump = 0
        if len(self.vpin_history) >= 2:
            vpin_jump = self.vpin_history[-1][1] - self.vpin_history[-2][1]
        # Rolling median of this symbol's own recent VPIN readings. Venues with
        # pervasive wash flow pin VPIN near saturation, so the absolute level
        # stops discriminating; the baseline keeps the veto relative and causal
        # (only readings already observed before this snapshot are used).
        vpin_readings = [v for _, v in self.vpin_history][-30:]
        vpin_base = statistics.median(vpin_readings) if len(vpin_readings) >= 10 else None
        bars = list(self.bars)
        changes = [bars[i][3]-bars[i-1][3] for i in range(1, len(bars))][-14:]
        all_ranges = [max(bars[i][1]-bars[i][2], abs(bars[i][1]-bars[i-1][3]), abs(bars[i][2]-bars[i-1][3])) for i in range(1, len(bars))]
        true_ranges = all_ranges[-14:]
        recent_atr = statistics.mean(true_ranges) if true_ranges else 0
        baseline = statistics.mean(all_ranges[:-14]) if len(all_ranges) > 14 else recent_atr
        atr_ratio = recent_atr / baseline if baseline > 1e-12 else 1
        atr_ratio = max(.10, min(10., atr_ratio))
        gain, loss = sum(max(x, 0) for x in changes), sum(max(-x, 0) for x in changes)
        rsi = 100*gain/(gain+loss) if gain+loss else 50
        scale = statistics.pstdev(changes) if len(changes) > 1 else 0
        momentum = statistics.mean(changes)/scale if scale > 1e-12 else 0
        variance_per_second = statistics.mean((r * self.book.mid)**2 / dt for r, dt in self.fast_changes) if self.fast_changes else 0
        result = Features((b-a)/(b+a), sum(self.ofis)/(b+a), self.cvd,
                          sum(signed)/volume if volume else 0, vpin, self.buckets.saturation,
                          vwap_z, momentum, rsi, recent_atr, self.sweep, self.book.spread_bps,
                          len(bars) >= 20 and len(self.trades) >= 20 and vpin is not None,
                          micro, micro_delta, flow_acceleration, flow_reversal, vpin_jump,
                          atr_ratio, variance_per_second, self.fast_momentum, self.fast_return,
                          self.fast_timestamp, vpin_base)
        self._cached = result
        return result


def lead_lag_signal(symbol, features, now, max_age=2.0, min_age=.2, threshold=1.5):
    """Return a bounded, causal BTC/ETH impulse for an unreacted altcoin."""
    target = features.get(symbol)
    if target is None or not target.ready or not math.isfinite(now):
        return 0.0
    if target.fast_timestamp > now or target.fast_timestamp < 0:
        return 0.0
    impulses = []
    for leader in ('BTCUSDT', 'ETHUSDT'):
        f = features.get(leader)
        if f is None or not f.ready or f.fast_timestamp < 0 or f.fast_timestamp > now:
            continue
        age = now - f.fast_timestamp
        if min_age - 1e-9 <= age <= max_age + 1e-9 and abs(f.fast_momentum) > threshold:
            impulses.append(math.copysign(min(1., (abs(f.fast_momentum)-threshold)/threshold + .25), f.fast_return or f.fast_momentum))
    if symbol in ('BTCUSDT', 'ETHUSDT') or not impulses or any(x * impulses[0] < 0 for x in impulses[1:]):
        return 0.0
    if target.fast_timestamp > max(f.fast_timestamp for f in (features.get('BTCUSDT'), features.get('ETHUSDT')) if f is not None):
        return 0.0
    # A non-zero alt return at the same observation timestamp means the move has
    # already propagated; do not double-count the leader impulse.
    if abs(target.fast_return) > 1e-12:
        return 0.0
    return max(-1., min(1., statistics.mean(impulses)))


def cross_sectional_momentum(momentums):
    if len(momentums) < 2:
        return {k: 0.0 for k in momentums}
    mean = statistics.mean(momentums.values())
    sigma = statistics.pstdev(momentums.values())
    return {k: (v-mean)/sigma if sigma else 0.0 for k, v in momentums.items()}
