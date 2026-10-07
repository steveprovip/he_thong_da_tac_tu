"""Snapshot watchdog: a circuit breaker against a runtime spinning on stale state.

A cycle that keeps re-deciding on a market snapshot that has not moved is not
trading, it is burning quota and rate limit on a frozen feed. The breaker
counts consecutive cycles whose snapshot digest is unchanged; at `limit`
repeats it parks. Any genuinely new snapshot wakes it on the spot.

Parking is deliberately cheap and reversible: it suppresses new authorization
while the feed is frozen, and it never touches inventory. Positions already on
the exchange stay protected by their resting stops, which is the only safe
reading of a stalled feed -- a watchdog that flattened on silence would turn a
data outage into a market order.
"""
import hashlib
import json
import math

__all__ = ['SnapshotWatchdog', 'snapshot_digest', 'DEFAULT_PARK_AFTER']

DEFAULT_PARK_AFTER = 3


def _canonical(value):
    """Stable, float-safe projection of a snapshot for digesting."""
    if isinstance(value, dict):
        return {str(k): _canonical(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        # NaN != NaN would make every frozen snapshot look new and defeat the
        # breaker, so non-finite floats get a stable token instead.
        if math.isnan(value):
            return '__nan__'
        if math.isinf(value):
            return '__inf__' if value > 0 else '__-inf__'
        return repr(value)
    return repr(value)


def snapshot_digest(snapshot):
    """Deterministic sha256 of a snapshot, stable across processes and key order."""
    payload = json.dumps(_canonical(snapshot), sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


class SnapshotWatchdog:
    """Parks after `limit` consecutive identical snapshots; wakes on a new one."""

    def __init__(self, limit=DEFAULT_PARK_AFTER):
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError('limit must be a positive int')
        self.limit = limit
        self.digest = None
        self.repeats = 0
        self.parked = False
        self.parks = 0
        self.wakes = 0

    def observe(self, snapshot):
        """Feed one cycle's snapshot. Returns 'ACTIVE' or 'PARKED'."""
        digest = snapshot_digest(snapshot)
        if digest == self.digest:
            self.repeats += 1
            if self.repeats >= self.limit and not self.parked:
                self.parked = True
                self.parks += 1
        else:
            if self.parked:
                self.wakes += 1
            self.digest = digest
            self.repeats = 1
            self.parked = False
        return 'PARKED' if self.parked else 'ACTIVE'

    def reset(self):
        """Force the breaker back to a clean active state."""
        self.digest = None
        self.repeats = 0
        self.parked = False

    def snapshot(self):
        return {'parked': self.parked, 'repeats': self.repeats, 'limit': self.limit,
                'parks': self.parks, 'wakes': self.wakes, 'digest': self.digest}
