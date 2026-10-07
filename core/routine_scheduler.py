"""Idempotent schedule coordinator for the four autonomous carry routines.

A scheduled employee that runs unattended has exactly one failure mode that
matters: doing the same thing twice. A process restart, a duplicated cron
entry, two terminals, a container respawn -- each of them replays whatever the
last run was in the middle of, and on a two-leg carry a replayed entry is a
naked leg. So this module does not schedule work. It *decides whether a piece
of work has already been decided*, and it persists that decision before the
work starts, not after.

The idempotency key is the one the spec fixes:

    H(mode, routine, symbol, period)   ->  sha256 hex

That key names a *stream* of firings, not one firing. Which firing is the
window index: floor((now - anchor) / period). A slot is (key, window_index),
and a slot may be claimed at most once, ever, across processes.

    PENDING -> EXECUTING -> RECONCILED -> DONE

Every transition is written through to disk with a temp-file replace before it
is returned to the caller, so a crash can only ever lose work that had not been
claimed. A slot found in EXECUTING at startup is NOT re-claimable: it is
surfaced by `recover()` for two-leg reconciliation, which is the only honest
reading of "we were mid-flight when the lights went out".

What this module deliberately does NOT do
-----------------------------------------
It has no timer, no thread and no sleep. It is a pure decision surface the
caller polls. In particular the RUNE/MARIN protection watchdog keeps its own
30s cadence and must never be routed through here: a routine interval is a
throughput knob, and exchange-side protection is not.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
import time

__all__ = ['RoutineScheduler', 'Routine', 'Lease', 'RoutineError', 'routine_key',
           'slot_key', 'SAIGON', 'ROUTINES', 'PENDING', 'EXECUTING', 'RECONCILED',
           'DONE', 'TRANSITIONS', 'DAILY_BRIEF_LOCAL_HOUR', 'brief_window_start',
           'brief_label']

SAIGON = timezone(timedelta(hours=7), 'Asia/Saigon')   # fixed offset, no DST since 1975
DAILY_BRIEF_LOCAL_HOUR = 7

PENDING = 'PENDING'
EXECUTING = 'EXECUTING'
RECONCILED = 'RECONCILED'
DONE = 'DONE'

# The machine is deliberately linear. There is no EXECUTING -> PENDING edge:
# "put it back" is exactly how a two-leg entry gets submitted twice.
TRANSITIONS = {PENDING: (EXECUTING,), EXECUTING: (RECONCILED,),
               RECONCILED: (DONE,), DONE: ()}

SCHEMA = 1
MAX_SLOTS = 2000


class RoutineError(RuntimeError):
    """Illegal transition, unknown routine, or a period the routine forbids."""


@dataclass(frozen=True)
class Routine:
    """One scheduled routine: how often it may fire and how long a firing stays open.

    `window` is the grace period after a slot opens. Past it the slot is stale
    and is skipped rather than executed late -- a funding scan acted on 50
    minutes after its snapshot is a different trade from the one that was
    scanned.

    `anchor_local_seconds` pins a routine to a wall-clock time in `tz`, and the
    default `tz` is UTC on purpose: R1-R3 ride the venue's funding clock, and
    Binance settles funding on UTC 00/04/08/12/16/20, not on a local hour. Only
    R4 is a human-facing time, 07:00 Asia/Saigon. Asia/Saigon is a fixed UTC+7
    with no DST, so that resolves to exactly 00:00 UTC and the arithmetic below
    never needs a timezone database at runtime.
    """
    name: str
    period: float
    window: float
    agents: tuple = ()
    periods_allowed: tuple = ()
    anchor_local_seconds: float = 0.0
    tz: timezone = timezone.utc
    description: str = ''

    def __post_init__(self):
        if not self.name or not isinstance(self.name, str):
            raise RoutineError('routine needs a name')
        if not (math.isfinite(self.period) and self.period > 0):
            raise RoutineError('routine period must be positive and finite')
        if not (math.isfinite(self.window) and 0 < self.window <= self.period):
            raise RoutineError('routine window must be in (0, period]')

    def anchor_offset(self, period=None):
        """Seconds past the Unix epoch at which this routine's windows align."""
        period = self.period if period is None else period
        utc_offset = self.tz.utcoffset(None).total_seconds()
        return (self.anchor_local_seconds - utc_offset) % period

    def resolve_period(self, period=None):
        if period is None:
            return float(self.period)
        period = float(period)
        if not (math.isfinite(period) and period > 0):
            raise RoutineError('period must be positive and finite')
        if self.periods_allowed and period not in self.periods_allowed:
            raise RoutineError('%s does not run on a %gs period (allowed: %s)'
                               % (self.name, period, ', '.join(map(str, self.periods_allowed))))
        return period


# R1/R2/R3 ride the funding clock: 1h, 4h or 8h depending on the venue's
# settlement interval for the symbol. R4 is a wall-clock daily brief.
ROUTINES = {
    'funding-scan': Routine(
        'funding-scan', period=3600.0, window=900.0, agents=('TIDAL', 'NORO'),
        periods_allowed=(3600.0, 14400.0, 28800.0),
        description='Rank funding across the universe: horizon 7-30d, >=21 settled '
                    'observations, OI > 20M USDT, projected APR > 15%.'),
    'envelope-size': Routine(
        'envelope-size', period=3600.0, window=600.0, agents=('OKAPI', 'RUNE'),
        periods_allowed=(3600.0, 14400.0, 28800.0),
        description='Size the envelope: spot cash + isolated margin, L <= 2, '
                    'basis <= 1.5%, margin ratio <= 65%, two-leg notional >= 10 USDT.'),
    'stage-pair': Routine(
        'stage-pair', period=3600.0, window=300.0, agents=('ZEPHR', 'VESKA'),
        periods_allowed=(3600.0, 14400.0, 28800.0),
        description='Post-Only GTX maker entry under a 5s AuthToken, two-leg '
                    'reconciliation on partial fill, net edge G > 3C.'),
    'reconcile-brief': Routine(
        'reconcile-brief', period=86400.0, window=3600.0, agents=('LUMEN', 'MARIN'),
        anchor_local_seconds=DAILY_BRIEF_LOCAL_HOUR * 3600, tz=SAIGON,
        description='Daily brief 07:00 Asia/Saigon: PnL = dV_spot + dV_perp '
                    '+ sum(funding) - sum(fees).'),
}


# ------------------------------------------------------------------------ keys
def routine_key(mode, routine, symbol, period):
    """H(mode, routine, symbol, period) -- the spec's idempotency key.

    Everything is normalised before hashing so that 'BTCUSDT'/'btcusdt' and
    3600/3600.0 cannot open two streams for the same work.
    """
    payload = json.dumps({'mode': str(mode).lower(), 'routine': str(routine).lower(),
                          'symbol': str(symbol).upper(), 'period': repr(float(period))},
                         sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def slot_key(mode, routine, symbol, period, window_index):
    """One firing of a stream. This is what is claimed at most once."""
    return hashlib.sha256(('%s:%d' % (routine_key(mode, routine, symbol, period),
                                      int(window_index))).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class Lease:
    """Proof that this process owns one slot. Carries no authority to trade."""
    slot: str
    key: str
    mode: str
    routine: str
    symbol: str
    period: float
    window_index: int
    window_start: float
    deadline: float
    state: str = PENDING
    claimed_at: float = 0.0
    history: tuple = ()


class RoutineScheduler:
    """Durable, idempotent coordinator for R1-R4.

    Usage:
        sched = RoutineScheduler(path, mode='shadow')
        lease = sched.claim('funding-scan', 'BTCUSDT', now)
        if lease:                       # None => already handled, or window shut
            lease = sched.mark_executing(lease, now)
            ...
            lease = sched.mark_reconciled(lease, now)
            lease = sched.mark_done(lease, now)
    """

    def __init__(self, path=None, mode='shadow', clock=time.time, routines=None,
                 max_slots=MAX_SLOTS):
        self.path = path
        self.mode = str(mode).lower()
        self.clock = clock
        self.routines = dict(ROUTINES if routines is None else routines)
        self.max_slots = int(max_slots)
        self.slots = {}
        self._load()

    # ------------------------------------------------------------- persistence
    def _load(self):
        if not self.path or not os.path.exists(str(self.path)):
            return
        with open(str(self.path), 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        if data.get('schema') != SCHEMA:
            raise RoutineError('routine state schema %r, expected %d' % (data.get('schema'), SCHEMA))
        if data.get('mode') != self.mode:
            raise RoutineError('routine state belongs to mode %r, not %r'
                               % (data.get('mode'), self.mode))
        for slot, row in data.get('slots', {}).items():
            if row.get('state') not in TRANSITIONS:
                raise RoutineError('unknown persisted state %r' % row.get('state'))
            self.slots[slot] = row

    def _flush(self):
        if not self.path:
            return
        keep = sorted(self.slots.items(), key=lambda kv: kv[1].get('claimed_at', 0.0))
        if len(keep) > self.max_slots:
            # Never evict an unfinished slot: that is exactly the row whose
            # absence would let a restart re-enter a half-open pair.
            unfinished = [kv for kv in keep if kv[1]['state'] != DONE]
            finished = [kv for kv in keep if kv[1]['state'] == DONE]
            finished = finished[max(0, len(keep) - self.max_slots):]
            keep = sorted(unfinished + finished, key=lambda kv: kv[1].get('claimed_at', 0.0))
            self.slots = dict(keep)
        payload = {'schema': SCHEMA, 'mode': self.mode, 'slots': dict(keep)}
        path = str(self.path)
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        temp = path + '.tmp'
        with open(temp, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
        os.replace(temp, path)

    # ------------------------------------------------------------------ windows
    def routine(self, name):
        try:
            return self.routines[name]
        except KeyError:
            raise RoutineError('unknown routine %r' % name) from None

    def window_index(self, name, now, period=None):
        r = self.routine(name)
        period = r.resolve_period(period)
        return int(math.floor((float(now) - r.anchor_offset(period)) / period))

    def window_start(self, name, index, period=None):
        r = self.routine(name)
        period = r.resolve_period(period)
        return index * period + r.anchor_offset(period)

    def window_open(self, name, now, period=None):
        """True while `now` is inside the current slot's grace window."""
        r = self.routine(name)
        period = r.resolve_period(period)
        start = self.window_start(name, self.window_index(name, now, period), period)
        return 0 <= float(now) - start < min(r.window, period)

    def next_window_start(self, name, now, period=None):
        period = self.routine(name).resolve_period(period)
        return self.window_start(name, self.window_index(name, now, period) + 1, period)

    # ------------------------------------------------------------------- state
    def _row(self, name, symbol, now, period=None):
        r = self.routine(name)
        period = r.resolve_period(period)
        index = self.window_index(name, now, period)
        slot = slot_key(self.mode, name, symbol, period, index)
        return slot, period, index, self.slots.get(slot)

    def state(self, name, symbol, now, period=None):
        """Current state of this routine/symbol's slot, or PENDING if untouched."""
        _, _, _, row = self._row(name, symbol, now, period)
        return row['state'] if row else PENDING

    def claim(self, name, symbol, now=None, period=None):
        """Claim this window's slot, or return None.

        None means one of exactly two things, and both are correct outcomes:
        the window is shut (too early/too late), or this slot has already been
        claimed -- by this process, an earlier run, or another machine sharing
        the state file.
        """
        now = float(self.clock() if now is None else now)
        r = self.routine(name)
        slot, period, index, row = self._row(name, symbol, now, period)
        if row is not None:
            return None
        if not self.window_open(name, now, period):
            return None
        start = self.window_start(name, index, period)
        record = {'slot': slot, 'key': routine_key(self.mode, name, symbol, period),
                  'mode': self.mode, 'routine': name, 'symbol': str(symbol).upper(),
                  'period': period, 'window_index': index, 'window_start': start,
                  'deadline': start + min(r.window, period), 'state': PENDING,
                  'claimed_at': now, 'history': [[PENDING, now]]}
        self.slots[slot] = record
        self._flush()
        return _lease(record)

    # -------------------------------------------------------------- transitions
    def advance(self, lease, state, now=None):
        now = float(self.clock() if now is None else now)
        row = self.slots.get(lease.slot)
        if row is None:
            raise RoutineError('slot %s is not claimed' % lease.slot[:12])
        current = row['state']
        if state not in TRANSITIONS[current]:
            raise RoutineError('illegal transition %s -> %s on %s/%s'
                               % (current, state, row['routine'], row['symbol']))
        row['state'] = state
        row['history'] = list(row['history']) + [[state, now]]
        self._flush()
        return _lease(row)

    def mark_executing(self, lease, now=None):
        return self.advance(lease, EXECUTING, now)

    def mark_reconciled(self, lease, now=None):
        return self.advance(lease, RECONCILED, now)

    def mark_done(self, lease, now=None):
        return self.advance(lease, DONE, now)

    # ----------------------------------------------------------------- recovery
    def recover(self):
        """Slots that were mid-flight when the process died.

        These are handed back so the caller can reconcile both legs against the
        venue. They are never re-claimable and never silently completed.
        """
        return [_lease(row) for row in sorted(self.slots.values(),
                                              key=lambda r: r.get('claimed_at', 0.0))
                if row['state'] == EXECUTING]

    def unfinished(self):
        return [_lease(row) for row in sorted(self.slots.values(),
                                              key=lambda r: r.get('claimed_at', 0.0))
                if row['state'] != DONE]

    def snapshot(self, now=None):
        now = float(self.clock() if now is None else now)
        counts = {s: 0 for s in TRANSITIONS}
        for row in self.slots.values():
            counts[row['state']] += 1
        return {'mode': self.mode, 'slots': len(self.slots), 'states': counts,
                'recovering': len(self.recover()),
                'routines': {name: {'period': r.period, 'window': r.window,
                                    'agents': list(r.agents),
                                    'open': self.window_open(name, now),
                                    'next_start': self.next_window_start(name, now)}
                             for name, r in self.routines.items()}}


def _lease(row):
    return Lease(slot=row['slot'], key=row['key'], mode=row['mode'], routine=row['routine'],
                 symbol=row['symbol'], period=row['period'], window_index=row['window_index'],
                 window_start=row['window_start'], deadline=row['deadline'],
                 state=row['state'], claimed_at=row['claimed_at'],
                 history=tuple(tuple(h) for h in row['history']))


# --------------------------------------------------------------- brief helpers
def brief_window_start(now, tz=SAIGON, hour=DAILY_BRIEF_LOCAL_HOUR):
    """Epoch seconds of the most recent `hour`:00 local on or before `now`."""
    local = datetime.fromtimestamp(float(now), tz)
    anchor = local.replace(hour=int(hour), minute=0, second=0, microsecond=0)
    if anchor > local:
        anchor -= timedelta(days=1)
    return anchor.timestamp()


def brief_label(now, tz=SAIGON, hour=DAILY_BRIEF_LOCAL_HOUR):
    """The business day a brief covers, e.g. '2026-09-21' for 07:00 Asia/Saigon."""
    return datetime.fromtimestamp(brief_window_start(now, tz, hour), tz).strftime('%Y-%m-%d')
