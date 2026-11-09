"""Four-state market regime machine for the fast lanes; it can only tighten (2026-11-07).

States, checked in this order on every causal price update (nothing reads a later price):

  CRASH       circuit breaker: the 5-minute move from the window extreme to now (ln(hi/p) or ln(p/lo)) is
              >= 300 bps, or >= 150 bps while the volatility ratio is >= 3.0 or a 4.5-sigma tail fired in
              the last 5 minutes.  Policy: no directional entries, flatten directional exposure
              (reduce-only), funding carry only (core.regime_permit still requires G > 3C for carry).
  JUMPY       volatility ratio >= 1.8, `tail_count` tails of `tail_z` sigma (v0: 1 of 4.5) in the last 5 minutes,
              or a TOXIC core.market_risk.MarketRisk passed in.  Policy: size x0.3 (owner 11-07: -70 %), stop
              x1.5 (risk at the stop x0.45), gross-edge hurdle and LCB floor x1.5.
              CRASH reads its own tail rule (crash_tail_z / crash_tail_count, default = the JUMPY rule): RC-1
              found that a stricter JUMPY tail rule also hid crashes when the two were coupled.
  CALM_TREND  Kaufman efficiency ratio over 15 minutes >= 0.42; it stays until the ratio drops below 0.38.
              Policy: trend-style directional maker entries (GTX entry, invariant I5); multipliers 1.0.
  SIDEWAYS    otherwise.  Policy: mean-reversion (range) entries only, stop and take-profit x0.75.
  UNKNOWN     warmup, a data gap longer than `stale_s`, or invalid input.  Fail closed: no directional
              entries.  During CRASH a gap or bad tick keeps CRASH and restarts its cooldown.

Hysteresis: a stricter state applies at once; a looser one applies only after the stricter trigger has
been absent for `confirm_s` (60 s).  CRASH clears after `crash_cooldown_s` (15 min) without a crash
trigger and steps down through JUMPY.

Volatility ratio = sqrt(GARCH(1,1) conditional variance / 6-hour EWMA of the same squared returns).  The
GARCH is core.market_risk.GarchTailRisk fed one sample per second (sample-and-hold, the same as its
MicroFeed wiring in the factory worktree); its own long-run anchor is the 30-sample warmup, which is why the
ratio uses a slower baseline.  0.38 / 0.42 is the repo's KER lock band (core.micro_signals.KER_GATES,
SOL/DOGE); 1.8 / 3.0 are GarchTailRisk's elevated / toxic ratios.  The CRASH move sizes are a stated
judgement, not fitted; tools/nightly_audit.py reports how the states behave on recorded data.

Policies are multipliers on the existing profile.  check_policies() pins that no state raises size or
risk at the stop and none lowers the edge hurdle, so the classifier cannot loosen an existing guard.
Nothing in the engine consumes this module yet; wiring it into order flow is a separate owner decision.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import asdict, dataclass
from typing import Optional

from core.market_risk import GarchTailRisk, MarketRisk

CALM_TREND, SIDEWAYS, JUMPY, CRASH, UNKNOWN = 'CALM_TREND', 'SIDEWAYS', 'JUMPY', 'CRASH', 'UNKNOWN'
STATES = (CALM_TREND, SIDEWAYS, JUMPY, CRASH, UNKNOWN)
RANK = {CALM_TREND: 0, SIDEWAYS: 1, JUMPY: 2, CRASH: 3}    # higher = stricter; UNKNOWN sits outside the order
TREND, MEAN_REVERSION = 'TREND', 'MEAN_REVERSION'
STYLES = (TREND, MEAN_REVERSION)


@dataclass(frozen=True)
class RegimePolicy:
    state: str
    directional: bool       # may open directional (non-carry) entries
    carry: bool             # funding carry may still be permitted (core.regime_permit applies G > 3C)
    flatten: bool           # close directional exposure now (reduce-only)
    styles: tuple           # entry styles allowed
    size_mult: float        # x planned quantity
    stop_mult: float        # x planned stop distance
    tp_mult: float          # x planned take-profit distance
    edge_mult: float        # x gross-edge hurdle and x LCB floor in core.regime_permit

    def as_dict(self) -> dict:
        return asdict(self)


POLICIES = {
    CALM_TREND: RegimePolicy(CALM_TREND, True, True, False, (TREND,), 1.0, 1.0, 1.0, 1.0),
    SIDEWAYS: RegimePolicy(SIDEWAYS, True, True, False, (MEAN_REVERSION,), 1.0, 0.75, 0.75, 1.0),
    JUMPY: RegimePolicy(JUMPY, True, True, False, STYLES, 0.3, 1.5, 1.0, 1.5),     # owner 11-07: size -70 %
    CRASH: RegimePolicy(CRASH, False, True, True, (), 0.0, 1.0, 1.0, 1.0),
    UNKNOWN: RegimePolicy(UNKNOWN, False, True, False, (), 0.0, 1.0, 1.0, 1.0),
}


def _finite(*values) -> bool:
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)


def check_policies(policies=None) -> list:
    """Every state may only tighten the profile. -> list of violations (empty = OK)."""
    policies = POLICIES if policies is None else policies
    bad = []
    if set(policies) != set(STATES):
        bad.append(f'states {sorted(policies)} != {sorted(STATES)}')
    for name, p in policies.items():
        if p.state != name:
            bad.append(f'{name}: policy names {p.state}')
        if not _finite(p.size_mult, p.stop_mult, p.tp_mult, p.edge_mult):
            bad.append(f'{name}: non-finite multiplier')
            continue
        if not 0.0 <= p.size_mult <= 1.0:
            bad.append(f'{name}: size_mult {p.size_mult} outside [0, 1]')
        if p.stop_mult <= 0.0 or p.size_mult * p.stop_mult > 1.0 + 1e-12:
            bad.append(f'{name}: risk at the stop grows ({p.size_mult} x {p.stop_mult})')
        if not 0.0 < p.tp_mult <= 1.0:
            bad.append(f'{name}: tp_mult {p.tp_mult} outside (0, 1]')
        if p.edge_mult < 1.0:
            bad.append(f'{name}: edge_mult {p.edge_mult} < 1 lowers the hurdle')
        if not set(p.styles) <= set(STYLES):
            bad.append(f'{name}: unknown styles {p.styles}')
        if p.directional != bool(p.styles) or (not p.directional and p.size_mult != 0.0):
            bad.append(f'{name}: directional flag, styles and size disagree')
        if p.flatten and p.directional:
            bad.append(f'{name}: flattens and opens at once')
    for name in (CRASH, UNKNOWN):
        if name in policies and policies[name].directional:
            bad.append(f'{name} must not open directional entries')
    if CRASH in policies and not (policies[CRASH].flatten and policies[CRASH].carry):
        bad.append('CRASH must flatten directional exposure and leave carry to its own gate')
    return bad


def policy_for(regime) -> RegimePolicy:
    """Canonical policy for a state name, RegimeState or RegimePolicy. Custom policy objects are ignored
    (only the state name is read) and an unrecognised name is UNKNOWN, so a caller cannot loosen a state."""
    name = getattr(regime, 'state', regime)
    return POLICIES.get(name, POLICIES[UNKNOWN]) if isinstance(name, str) else POLICIES[UNKNOWN]


def apply_policy(regime, qty: float, stop_bps: float, tp_bps: float) -> tuple:
    """Scale a planned entry. -> (qty, stop_bps, tp_bps); qty is 0 when the state opens no directional entry."""
    if not _finite(qty, stop_bps, tp_bps) or qty < 0 or stop_bps <= 0 or tp_bps <= 0:
        raise ValueError('qty must be >= 0 and stop/tp distances > 0')
    p = policy_for(regime)
    if not p.directional:
        return 0.0, float(stop_bps), float(tp_bps)
    return qty * p.size_mult, stop_bps * p.stop_mult, tp_bps * p.tp_mult


@dataclass(frozen=True)
class RegimeConfig:
    trend_window_s: float = 900.0
    er_bucket_s: float = 30.0
    er_min_span_s: float = 600.0
    er_lock: float = 0.38              # CALM_TREND -> SIDEWAYS below this
    er_unlock: float = 0.42            # SIDEWAYS -> CALM_TREND at or above this
    jumpy_vol_ratio: float = 1.8
    crash_vol_ratio: float = 3.0
    crash_window_s: float = 300.0
    crash_move_bps: float = 150.0
    crash_hard_move_bps: float = 300.0
    tail_memory_s: float = 300.0
    confirm_s: float = 60.0
    crash_cooldown_s: float = 900.0
    stale_s: float = 30.0              # use > the input cadence (e.g. 180 s for 1 m bars)
    baseline_halflife_s: float = 21600.0
    garch_warmup: int = 30
    tail_z: float = 4.5                # GARCH |z| that counts as a tail (GarchTailRisk default)
    tail_count: int = 1                # tails needed inside tail_memory_s (1 = v0)
    crash_tail_z: Optional[float] = None      # tail rule of the CRASH trigger; None = the JUMPY rule (v0)
    crash_tail_count: Optional[int] = None

    def __post_init__(self):
        values = tuple(v for v in asdict(self).values() if v is not None)
        if not _finite(*values) or min(values) <= 0:
            raise ValueError('regime config values must be finite and positive')
        if not self.er_lock <= self.er_unlock <= 1.0:
            raise ValueError('need er_lock <= er_unlock <= 1')
        if not 1.0 < self.jumpy_vol_ratio < self.crash_vol_ratio:
            raise ValueError('need 1 < jumpy_vol_ratio < crash_vol_ratio')
        if not self.crash_move_bps < self.crash_hard_move_bps:
            raise ValueError('need crash_move_bps < crash_hard_move_bps')
        if self.er_min_span_s > self.trend_window_s or self.er_bucket_s * 3 > self.trend_window_s:
            raise ValueError('efficiency window too short for its bucket / minimum span')
        if self.crash_tail_count is not None and (not isinstance(self.crash_tail_count, int)
                                                  or self.crash_tail_count < 1):
            raise ValueError('crash_tail_count must be an int >= 1 or None')
        if not isinstance(self.tail_count, int) or self.tail_count < 1:
            raise ValueError('tail_count must be an int >= 1')
        if not isinstance(self.garch_warmup, int) or self.garch_warmup < 10:
            raise ValueError('garch_warmup must be an int >= 10')


@dataclass(frozen=True)
class RegimeState:
    state: str
    reason: str
    since: float | None
    policy: RegimePolicy
    vol_ratio: float | None
    efficiency: float | None
    move_bps: float | None
    observations: int

    def as_dict(self) -> dict:
        return asdict(self)


class RegimeClassifier:
    """Online, causal. Feed `update(t_seconds, price)` in time order (any cadence; one GARCH sample per second)."""

    def __init__(self, config: RegimeConfig | None = None, garch: GarchTailRisk | None = None):
        self.cfg = config or RegimeConfig()
        self.garch = garch or GarchTailRisk(warmup=self.cfg.garch_warmup, tail_z=self.cfg.tail_z)
        self._crash_z = self.cfg.tail_z if self.cfg.crash_tail_z is None else self.cfg.crash_tail_z
        self._crash_n = self.cfg.tail_count if self.cfg.crash_tail_count is None else self.cfg.crash_tail_count
        self.state, self.reason, self.since = UNKNOWN, 'WARMUP', None
        self.warmup_since: float | None = None
        self.n = 0
        self.last_t = None
        self.baseline_var = None
        self.last_tail_t = None
        self._tails = deque()              # times of recent JUMPY tails (only the last tail_count matter)
        self._ctails = deque()             # times of recent CRASH tails
        self._sec = self._sec_px = None
        self._relief_since = None         # a looser candidate has held since then
        self._crash_clear_since = None    # no crash trigger since then
        self._unknown_cause = None        # why the windows are refilling (a data gap)
        self._reset_windows()
        self.last = self._snapshot(None, None, None)

    # ----------------------------------------------------------------- windows (O(1) amortised per update)
    def _reset_windows(self):
        self._closes = deque()            # (bucket index, last price in bucket), completed buckets
        self._bucket = self._bucket_px = None
        self._path = 0.0
        self._hi, self._lo = deque(), deque()   # monotonic (t, px) deques over crash_window_s

    def _efficiency(self, t: float, px: float) -> float | None:
        cfg = self.cfg
        b = math.floor(t / cfg.er_bucket_s)
        if self._bucket is None:
            self._bucket, self._bucket_px = b, px
        elif b > self._bucket:
            self._closes.append((self._bucket, self._bucket_px))
            self._bucket, self._bucket_px = b, px
            oldest = b - math.ceil(cfg.trend_window_s / cfg.er_bucket_s)
            while self._closes and self._closes[0][0] < oldest:
                self._closes.popleft()
            pts = [p for _, p in self._closes]
            self._path = sum(abs(y - x) for x, y in zip(pts, pts[1:]))
        else:
            self._bucket_px = px
        if len(self._closes) < 2 or (b - self._closes[0][0]) * cfg.er_bucket_s < cfg.er_min_span_s:
            return None
        path = self._path + abs(px - self._closes[-1][1])
        return abs(px - self._closes[0][1]) / path if path > 0 else 0.0

    def _move(self, t: float, px: float) -> tuple:
        lo_t = t - self.cfg.crash_window_s
        for dq, worse in ((self._hi, lambda a, b: a <= b), (self._lo, lambda a, b: a >= b)):
            while dq and worse(dq[-1][1], px):
                dq.pop()
            dq.append((t, px))
            while dq[0][0] < lo_t:
                dq.popleft()
        down, up = math.log(self._hi[0][1] / px) * 1e4, math.log(px / self._lo[0][1]) * 1e4
        return (down, 'DOWN') if down >= up else (up, 'UP')

    def _sample(self, t: float, px: float) -> None:
        """One GARCH sample per second: the return from the last price seen in an earlier second."""
        sec = math.floor(t)
        if self._sec is None:
            self._sec, self._sec_px = sec, px
            return
        if sec <= self._sec:
            self._sec_px = px
            return
        elapsed = sec - self._sec
        r = math.log(px / self._sec_px) * 1e4 / math.sqrt(elapsed)
        self.garch.update(r)
        if self.garch.variance is not None:
            if self.baseline_var is None:
                self.baseline_var = float(self.garch.long_run_variance)
            else:
                keep = 0.5 ** (elapsed / self.cfg.baseline_halflife_s)
                self.baseline_var = keep * self.baseline_var + (1.0 - keep) * r * r
            z = self.garch.last_z
            if z is not None and abs(z) >= self.cfg.tail_z:
                self.last_tail_t = t
                self._tails.append(t)
                while len(self._tails) > self.cfg.tail_count:
                    self._tails.popleft()
            if z is not None and abs(z) >= self._crash_z:
                self._ctails.append(t)
                while len(self._ctails) > self._crash_n:
                    self._ctails.popleft()
        self._sec, self._sec_px = sec, px

    def vol_ratio(self) -> float | None:
        if self.garch.variance is None or self.baseline_var is None:
            return None
        floor = GarchTailRisk.MIN_VARIANCE_BPS2
        return math.sqrt(max(self.garch.variance, 1e-12) / max(self.baseline_var, floor))

    # ----------------------------------------------------------------- state machine
    def _set(self, state: str, t: float | None, why: str) -> None:
        self.state, self.since, self.reason = state, t, why
        self._relief_since = self._crash_clear_since = None
        if state != UNKNOWN:
            self._unknown_cause = None

    def _snapshot(self, vol, er, move) -> RegimeState:
        return RegimeState(self.state, self.reason, self.since, POLICIES[self.state], vol, er, move, self.n)

    def _bad_input(self, t, why: str) -> RegimeState:
        self.warmup_since = None
        if self.state == CRASH:
            self.reason, self._crash_clear_since = f'CRASH held: {why}', None
        else:
            self._set(UNKNOWN, t, why)
            self._unknown_cause = why
        self.last = self._snapshot(None, None, None)
        return self.last

    def update(self, t: float, price: float, market_risk=None) -> RegimeState:
        cfg = self.cfg
        if not _finite(t, price) or price <= 0 or (self.last_t is not None and t < self.last_t):
            return self._bad_input(t if _finite(t) else self.last_t, 'INVALID_INPUT')
        if self.last_t is not None and t - self.last_t > cfg.stale_s:
            self._reset_windows()          # windows would straddle the hole
            gap = t - self.last_t
            self.last_t = t
            self._unknown_cause = f'DATA_GAP {gap:.0f}s'
            self._bad_input(t, self._unknown_cause)
        self.last_t = t
        self.n += 1
        self._sample(t, price)
        er = self._efficiency(t, price)
        move, direction = self._move(t, price)
        vol = self.vol_ratio()
        tail = (len(self._tails) >= cfg.tail_count and t - self._tails[0] <= cfg.tail_memory_s)
        ctail = (len(self._ctails) >= self._crash_n and t - self._ctails[0] <= cfg.tail_memory_s)
        risk = market_risk.as_dict() if isinstance(market_risk, MarketRisk) else market_risk
        toxic = isinstance(risk, dict) and risk.get('regime') == 'TOXIC'
        window = f'{cfg.crash_window_s:g}s'
        if move >= cfg.crash_hard_move_bps:
            cand, why = CRASH, f'MOVE {direction} {move:.0f}>={cfg.crash_hard_move_bps:g} bps/{window}'
        elif move >= cfg.crash_move_bps and (ctail or (vol is not None and vol >= cfg.crash_vol_ratio)):
            cand, why = CRASH, (f'MOVE {direction} {move:.0f}>={cfg.crash_move_bps:g} bps/{window} with '
                                + ('TAIL' if ctail else f'VOL x{vol:.2f}'))
        elif tail or toxic or (vol is not None and vol >= cfg.jumpy_vol_ratio):
            cand, why = JUMPY, ('TAIL<=' + f'{cfg.tail_memory_s:g}s' if tail else
                                f'MARKET_RISK TOXIC {risk.get("reason")}' if toxic else f'VOL x{vol:.2f}')
        elif vol is None or er is None:
            cand, why = UNKNOWN, 'WARMUP' if self._unknown_cause is None else f'WARMUP after {self._unknown_cause}'
        elif er >= cfg.er_unlock or (self.state == CALM_TREND and er >= cfg.er_lock):
            cand, why = CALM_TREND, f'ER {er:.2f}'
        else:
            cand, why = SIDEWAYS, f'ER {er:.2f}'
        self._transition(t, cand, why)
        if self.state == UNKNOWN and self.reason.startswith('WARMUP'):
            if self.warmup_since is None:
                self.warmup_since = t
        else:
            self.warmup_since = None
        self.last = self._snapshot(vol, er, move)
        return self.last

    def _transition(self, t: float, cand: str, why: str) -> None:
        cfg, cur = self.cfg, self.state
        if cur == CRASH:
            if cand == CRASH:
                self.reason, self._crash_clear_since = why, None
                return
            if self._crash_clear_since is None:
                self._crash_clear_since = t
            held = t - self._crash_clear_since
            if held >= cfg.crash_cooldown_s:
                self._set(JUMPY, t, f'CRASH cleared after {held:.0f}s; {why}')
            else:
                self.reason = f'CRASH cooldown {held:.0f}/{cfg.crash_cooldown_s:g}s; now {why}'
            return
        if cand == cur:
            self.reason, self._relief_since = why, None
            return
        if cur == UNKNOWN and self._unknown_cause is None and cand != UNKNOWN:
            # On a clean cold start, enter the first data-supported regime as soon as its windows are ready.
            # Hysteresis still applies after data gaps / invalid input and between known regimes.
            self._set(cand, t, why)
            return
        if cand in (CRASH, UNKNOWN) or (cur != UNKNOWN and RANK[cand] > RANK[cur]):
            self._set(cand, t, why)                 # stricter: at once
            return
        if self._relief_since is None:              # looser: after confirm_s without the stricter trigger
            self._relief_since = t
        held = t - self._relief_since
        if held >= cfg.confirm_s:
            self._set(cand, t, why)
        else:
            self.reason = f'hold {cur} {held:.0f}/{cfg.confirm_s:g}s before {cand} ({why})'


def classify_series(times, prices, config: RegimeConfig | None = None) -> list:
    """Run one classifier over aligned (t seconds, price) sequences. -> list of RegimeState."""
    clf = RegimeClassifier(config)
    return [clf.update(float(t), float(p)) for t, p in zip(times, prices)]
