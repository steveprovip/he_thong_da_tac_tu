"""Causal, bounded-memory features; VPIN uses observed aggressor classification."""
from collections import deque
from dataclasses import dataclass, asdict
import math
import statistics


def calibrate_bucket_volume(trades, seconds=15.0, floor_multiple=25, minimum=1e-9):
    """Bucket volume for VPIN: a slice of tape too wide for one print to own.

    Binance aggTrades merge same-side fills at one price, so their sizes are heavy
    tailed. Sizing a bucket at ~10x the mean trade lets a single sweep fill whole
    buckets alone, which forces |delta| == volume and pins VPIN near 1.0 no matter
    what the real flow looks like. Two constraints remove that artefact:
      * time  - a bucket spans `seconds` of typical tape, so VPIN moves on a useful scale;
      * grain - a bucket holds at least `floor_multiple` p95-sized trades, so no single
                print can dominate the imbalance.
    Measured on heavy-tailed sweep-heavy tape the old rule yields VPIN 0.73-0.96,
    always above the 0.65 toxicity gate; at seconds=15 this yields ~0.26 with zero
    saturated buckets, and needs roughly ten buckets (~2.5 min of tape) to report.
    """
    quantities = sorted(float(t['q']) for t in trades)
    if not quantities:
        return minimum
    total = math.fsum(quantities)
    stamps = [float(t['T']) / 1000 for t in trades if 'T' in t]
    span = (max(stamps) - min(stamps)) if len(stamps) > 1 else 0.0
    per_window = total / span * seconds if span > 0 else total / 100
    p95 = quantities[min(len(quantities) - 1, int(.95 * len(quantities)))]
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

    def add(self, quantity, buyer_aggressor):
        if not math.isfinite(quantity) or quantity <= 0:
            raise ValueError('Invalid trade volume')
        remaining = quantity
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
        """Share of buckets that were completely one-sided - VPIN's degeneracy tell.

        Near 1.0 means the bucket is too small to measure anything: read it before
        trusting a high VPIN."""
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


def cross_sectional_momentum(momentums):
    if len(momentums) < 2:
        return {k: 0.0 for k in momentums}
    mean = statistics.mean(momentums.values())
    sigma = statistics.pstdev(momentums.values())
    return {k: (v-mean)/sigma if sigma else 0.0 for k, v in momentums.items()}
