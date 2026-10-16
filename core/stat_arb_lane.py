from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import math
from pathlib import Path
import time
from typing import Deque, Optional, Sequence, Tuple


@dataclass(frozen=True)
class StatArbSignal:
    pair_id: str
    asset_a: str
    asset_b: str
    action: str          # "ENTER_LONG_SPREAD" | "ENTER_SHORT_SPREAD" | "EXIT_SPREAD" | "STOP_BREAKDOWN" | "HOLD"
    z_score: float
    hedge_ratio: float
    intercept: float
    spread_std_bps: float
    half_life_bars: float
    weight_a: float
    weight_b: float
    reason: str


class OnlineKalmanPairTracker:
    """
    Bộ lọc Không gian Trạng thái Kalman 2D ước lượng trực tuyến [alpha_t, beta_t]
    kết hợp ước lượng tham số Ornstein-Uhlenbeck (Half-Life & Equilibrium Spread Std).
    Không phụ thuộc thư viện ngoài, độ phức tạp O(1) mỗi bước.
    """

    def __init__(
        self,
        asset_a: str,
        asset_b: str,
        delta_alpha: float = 1e-5,
        delta_beta: float = 1e-4,
        obs_var_init: float = 1e-4,
        ou_window: int = 120,
    ) -> None:
        self.asset_a = asset_a
        self.asset_b = asset_b
        self.pair_id = f"{asset_a}_{asset_b}"

        # Trạng thái Kalman: theta = [alpha, beta]
        self.alpha: float = 0.0
        self.beta: float = 1.0
        self._initialized: bool = False

        # Ma trận hiệp phương sai trạng thái P (2x2)
        self.p00: float = 1.0
        self.p01: float = 0.0
        self.p10: float = 0.0
        self.p11: float = 1.0

        self.q00: float = float(delta_alpha)
        self.q11: float = float(delta_beta)
        self.r_var: float = float(obs_var_init)

        self._spread_history: Deque[float] = deque(maxlen=max(int(ou_window), 30))
        self.bars_in_trade: int = 0
        self.current_position: int = 0  # +1: Long Spread (Long A, Short B), -1: Short Spread, 0: Flat

    def update(self, price_a: float, price_b: float) -> Tuple[float, float, float]:
        """
        Cập nhật giá (price_a, price_b) và trả về (innovation_e, z_score, sqrt_S_bps).
        """
        y = math.log(max(float(price_a), 1e-9))
        x = math.log(max(float(price_b), 1e-9))

        if not self._initialized:
            self.beta = 1.0
            self.alpha = y - x
            self._initialized = True
            self._spread_history.append(0.0)
            return 0.0, 0.0, 0.0

        # 1. Bước dự báo tiên nghiệm: P_{t|t-1} = P_{t-1|t-1} + Q
        p00 = self.p00 + self.q00
        p01 = self.p01
        p10 = self.p10
        p11 = self.p11 + self.q11

        # 2. Sai số dự báo (Innovation Spread): e_t = y_t - (alpha + beta * x_t)
        y_hat = self.alpha + self.beta * x
        e_t = y - y_hat

        # Phương sai dự báo S_t = F * P * F^T + R, với F = [1, x]
        s_var = p00 + x * (p01 + p10) + (x * x) * p11 + self.r_var
        s_var = max(s_var, 1e-12)
        sqrt_s = math.sqrt(s_var)
        z_score = max(-6.0, min(6.0, e_t / sqrt_s))

        # 3. Hệ số Kalman Gain: K = P * F^T / S_t
        k0 = (p00 + p01 * x) / s_var
        k1 = (p10 + p11 * x) / s_var

        # 4. Cập nhật hậu nghiệm trạng thái theta
        self.alpha += k0 * e_t
        self.beta = max(0.20, min(3.50, self.beta + k1 * e_t))

        # 5. Cập nhật ma trận P = (I - K * F) * P_{t|t-1}
        self.p00 = (1.0 - k0) * p00 - k0 * x * p10
        self.p01 = (1.0 - k0) * p01 - k0 * x * p11
        self.p10 = -k1 * p00 + (1.0 - k1 * x) * p10
        self.p11 = -k1 * p01 + (1.0 - k1 * x) * p11

        # Cập nhật phương sai quan sát R_t theo EWMA
        self.r_var = 0.97 * self.r_var + 0.03 * max(e_t * e_t, 1e-8)

        # Lưu sai số dự báo e_t để ước lượng tham số OU
        self._spread_history.append(e_t)

        return e_t, z_score, sqrt_s * 1e4

    def estimate_ou_params(self) -> Tuple[float, float, bool]:
        """
        Ước lượng (half_life_bars, eq_spread_std_bps, is_stationary) từ chuỗi phần dư.
        Hồi quy AR(1): e_t - e_{t-1} = a + phi * e_{t-1} + xi_t
        """
        n = len(self._spread_history)
        if n < 25:
            return 999.0, 0.0, False

        xs = list(self._spread_history)[:-1]
        dys = [self._spread_history[i] - self._spread_history[i - 1] for i in range(1, n)]
        m = len(xs)

        mean_x = sum(xs) / m
        mean_dy = sum(dys) / m
        var_x = sum((x - mean_x) ** 2 for x in xs)
        if var_x <= 1e-12:
            return 999.0, 0.0, False

        cov_x_dy = sum((xs[i] - mean_x) * (dys[i] - mean_dy) for i in range(m))
        phi = cov_x_dy / var_x

        # Điều kiện hội tụ Ornstein-Uhlenbeck: -0.85 < phi < -0.008
        if phi >= -0.008 or phi <= -0.85:
            return 999.0, 0.0, False

        b = 1.0 + phi
        kappa = -math.log(b)
        half_life = math.log(2.0) / max(kappa, 1e-6)

        intercept_a = mean_dy - phi * mean_x
        resid_var = sum((dys[i] - (intercept_a + phi * xs[i])) ** 2 for i in range(m)) / max(m - 2, 1)
        eq_var = resid_var / max(1.0 - b * b, 1e-6)
        eq_std_bps = math.sqrt(max(eq_var, 0.0)) * 1e4

        is_valid = (2.0 <= half_life <= 96.0)
        return half_life, eq_std_bps, is_valid

    def evaluate_signal(
        self,
        price_a: float,
        price_b: float,
        round_trip_cost_bps: float = 14.0,
        z_entry: float = 2.10,
        z_exit: float = 0.25,
        z_stop: float = 3.60,
    ) -> StatArbSignal:
        _, z_score, sqrt_s_bps = self.update(price_a, price_b)
        half_life, eq_std_bps, is_stationary = self.estimate_ou_params()
        effective_std_bps = max(eq_std_bps, sqrt_s_bps)

        # Chuẩn hóa tỷ trọng Dollar-Neutral / Beta-Neutral
        total_leg_scale = 1.0 + self.beta
        w_a = 1.0 / total_leg_scale
        w_b = self.beta / total_leg_scale

        # Quản lý vị thế đang mở
        if self.current_position != 0:
            self.bars_in_trade += 1
            # 1. Kiểm tra đứt gãy cấu trúc hoặc Stop-Loss
            if abs(z_score) >= z_stop or self.bars_in_trade > max(int(2.5 * half_life), 120) or not is_stationary:
                self.current_position = 0
                self.bars_in_trade = 0
                return StatArbSignal(
                    pair_id=self.pair_id, asset_a=self.asset_a, asset_b=self.asset_b,
                    action="STOP_BREAKDOWN", z_score=z_score, hedge_ratio=self.beta,
                    intercept=self.alpha, spread_std_bps=effective_std_bps,
                    half_life_bars=half_life, weight_a=0.0, weight_b=0.0,
                    reason="structural_break_or_z_stop",
                )
            # 2. Kiểm tra hội tụ chốt lời
            if (self.current_position == 1 and z_score >= -z_exit) or (
                self.current_position == -1 and z_score <= z_exit
            ):
                self.current_position = 0
                self.bars_in_trade = 0
                return StatArbSignal(
                    pair_id=self.pair_id, asset_a=self.asset_a, asset_b=self.asset_b,
                    action="EXIT_SPREAD", z_score=z_score, hedge_ratio=self.beta,
                    intercept=self.alpha, spread_std_bps=effective_std_bps,
                    half_life_bars=half_life, weight_a=0.0, weight_b=0.0,
                    reason="mean_reversion_target_hit",
                )
            return StatArbSignal(
                pair_id=self.pair_id, asset_a=self.asset_a, asset_b=self.asset_b,
                action="HOLD", z_score=z_score, hedge_ratio=self.beta,
                intercept=self.alpha, spread_std_bps=effective_std_bps,
                half_life_bars=half_life, weight_a=w_a * self.current_position,
                weight_b=-w_b * self.current_position,
                reason="holding_active_spread",
            )

        # Khi chưa có vị thế: Kiểm tra điều kiện vào lệnh
        min_required_std_bps = 3.5 * float(round_trip_cost_bps)
        if not is_stationary or effective_std_bps < min_required_std_bps:
            return StatArbSignal(
                pair_id=self.pair_id, asset_a=self.asset_a, asset_b=self.asset_b,
                action="HOLD", z_score=z_score, hedge_ratio=self.beta,
                intercept=self.alpha, spread_std_bps=effective_std_bps,
                half_life_bars=half_life, weight_a=0.0, weight_b=0.0,
                reason="insufficient_cointegration_or_spread_vol",
            )

        if z_score <= -z_entry:
            self.current_position = 1
            self.bars_in_trade = 1
            return StatArbSignal(
                pair_id=self.pair_id, asset_a=self.asset_a, asset_b=self.asset_b,
                action="ENTER_LONG_SPREAD", z_score=z_score, hedge_ratio=self.beta,
                intercept=self.alpha, spread_std_bps=effective_std_bps,
                half_life_bars=half_life, weight_a=+w_a, weight_b=-w_b,
                reason="kalman_z_oversold",
            )

        if z_score >= z_entry:
            self.current_position = -1
            self.bars_in_trade = 1
            return StatArbSignal(
                pair_id=self.pair_id, asset_a=self.asset_a, asset_b=self.asset_b,
                action="ENTER_SHORT_SPREAD", z_score=z_score, hedge_ratio=self.beta,
                intercept=self.alpha, spread_std_bps=effective_std_bps,
                half_life_bars=half_life, weight_a=-w_a, weight_b=+w_b,
                reason="kalman_z_overbought",
            )

        return StatArbSignal(
            pair_id=self.pair_id, asset_a=self.asset_a, asset_b=self.asset_b,
            action="HOLD", z_score=z_score, hedge_ratio=self.beta,
            intercept=self.alpha, spread_std_bps=effective_std_bps,
            half_life_bars=half_life, weight_a=0.0, weight_b=0.0,
            reason="awaiting_z_threshold",
        )


class DynamicZBand:
    """10-16 volatility-scaled entry band (unvalidated by replay):
        Z_entry(t) = clamp(lo, base x (sigma_fast / sigma_slow)^alpha, hi)
    sigma = time-weighted EWMA of the squared Kalman innovation with time constants fast_s (15 m) and slow_s (4 h),
    weight 1 - exp(-dt / tau) per sample, O(1).  Until slow_s of history the band is `base` (static 2.10):
    a 4 h sigma from minutes of data would be noise."""

    def __init__(self, base: float = 2.10, lo: float = 1.45, hi: float = 2.60, alpha: float = 0.5,
                 fast_s: float = 900.0, slow_s: float = 14_400.0) -> None:
        self.base, self.lo, self.hi, self.alpha = float(base), float(lo), float(hi), float(alpha)
        self.fast_s, self.slow_s = float(fast_s), float(slow_s)
        self.var_fast: Optional[float] = None
        self.var_slow: Optional[float] = None
        self.t0: Optional[float] = None
        self.t_prev: Optional[float] = None

    def update(self, now: float, x: float) -> float:
        now, x2 = float(now), float(x) * float(x)
        if self.t_prev is None:
            self.t0 = self.t_prev = now
            self.var_fast = self.var_slow = x2
            return self.z_entry(now)
        dt = max(now - self.t_prev, 0.0)
        self.t_prev = now
        wf, ws = 1.0 - math.exp(-dt / self.fast_s), 1.0 - math.exp(-dt / self.slow_s)
        self.var_fast += wf * (x2 - self.var_fast)
        self.var_slow += ws * (x2 - self.var_slow)
        return self.z_entry(now)

    def z_entry(self, now: Optional[float] = None) -> float:
        if self.t0 is None or now is None or now - self.t0 < self.slow_s or not self.var_slow or self.var_slow <= 0:
            return self.base
        ratio = math.sqrt(max(self.var_fast, 0.0) / self.var_slow)
        return max(self.lo, min(self.base * ratio ** self.alpha, self.hi))


@dataclass(frozen=True)
class LegQuote:
    symbol: str
    bid: float
    ask: float
    depth_usd: float               # resting size near the touch (UNKNOWN -> pass 0: the leg is treated as thinnest)

    @property
    def spread_bps(self) -> float:
        mid = 0.5 * (self.bid + self.ask)
        return (self.ask - self.bid) / mid * 1e4 if mid > 0 else math.inf


class LeaderFollowerExecutor:
    """10-16 two-leg entry as order INTENTS (dicts in Binance USD-M fields); nothing is sent from here.
      leader    the leg with the wider spread (tie: less depth) rests as LIMIT GTX (post-only) on its own touch
      follower  every leader fill -> LIMIT IOC for the beta-scaled quantity, priced through the far touch by at
                most ioc_slip_bps, due within hedge_deadline_ms of the fill
      naked     a fill not hedged by its deadline, or an IOC that fills short, -> reduce-only MARKET flattening
                the unhedged leader quantity (risk exit, not a directional entry; the spread is abandoned); an
                IOC that still fills after that is flattened the same way
      ttl       an unfilled leader quote is cancelled after quote_ttl_s
    Latency is the caller's: the intent carries the deadline; check(now) enforces it."""

    def __init__(self, hedge_deadline_ms: float = 150.0, quote_ttl_s: float = 5.0, ioc_slip_bps: float = 3.0) -> None:
        self.hedge_deadline_ms = float(hedge_deadline_ms)
        self.quote_ttl_s = float(quote_ttl_s)
        self.ioc_slip_bps = float(ioc_slip_bps)
        self.state = 'IDLE'
        self.lead: Optional[dict] = None
        self.follow: Optional[dict] = None
        self.lead_filled = self.hedged = self.hedge_sent = 0.0
        self.t_quote = 0.0
        self.pending: list = []                 # [(fill_t, follower_qty)] awaiting an IOC result
        self.latency_ms: list = []

    @staticmethod
    def pick_leader(qa: LegQuote, qb: LegQuote) -> str:
        if qa.spread_bps != qb.spread_bps:
            return 'A' if qa.spread_bps > qb.spread_bps else 'B'
        return 'A' if qa.depth_usd <= qb.depth_usd else 'B'

    def plan(self, side: int, qty_a: float, qty_b: float, qa: LegQuote, qb: LegQuote, now: float) -> dict:
        """side +1 = long spread (BUY A, SELL B), -1 = short spread.  -> leader GTX intent."""
        if self.state != 'IDLE':
            raise RuntimeError(f'executor busy ({self.state})')
        if not (qty_a > 0 and qty_b > 0):
            raise ValueError('both leg quantities must be > 0')
        legs = {'A': (qa, 'BUY' if side > 0 else 'SELL', float(qty_a)),
                'B': (qb, 'SELL' if side > 0 else 'BUY', float(qty_b))}
        k = self.pick_leader(qa, qb)
        (lq, ls, lqty), (fq, fs, fqty) = legs[k], legs['B' if k == 'A' else 'A']
        self.lead = {'symbol': lq.symbol, 'side': ls, 'qty': lqty}
        self.follow = {'symbol': fq.symbol, 'side': fs, 'qty': fqty, 'quote': fq}
        self.lead_filled = self.hedged = self.hedge_sent = 0.0
        self.pending, self.state, self.t_quote = [], 'QUOTING', float(now)
        return {'symbol': lq.symbol, 'side': ls, 'type': 'LIMIT', 'timeInForce': 'GTX', 'quantity': lqty,
                'price': lq.bid if ls == 'BUY' else lq.ask, 'leg': 'leader'}

    def on_leader_fill(self, qty: float, now: float, follower_quote: Optional[LegQuote] = None) -> dict:
        """A (partial) leader fill -> follower IOC intent for its beta-scaled share."""
        if self.state not in ('QUOTING', 'HEDGING'):
            raise RuntimeError(f'fill in state {self.state}')
        q = follower_quote or self.follow['quote']
        self.follow['quote'] = q
        add = min(float(qty), self.lead['qty'] - self.lead_filled)
        self.lead_filled += add
        f_qty = add * self.follow['qty'] / self.lead['qty']
        self.hedge_sent += f_qty
        self.pending.append((float(now), f_qty))
        self.state = 'HEDGING'
        slip = self.ioc_slip_bps * 1e-4
        px = q.ask * (1 + slip) if self.follow['side'] == 'BUY' else q.bid * (1 - slip)
        return {'symbol': q.symbol, 'side': self.follow['side'], 'type': 'LIMIT', 'timeInForce': 'IOC',
                'quantity': f_qty, 'price': px, 'leg': 'follower',
                'deadline_ms': self.hedge_deadline_ms}

    def on_hedge_result(self, filled_qty: float, now: float) -> Optional[dict]:
        """Result of the oldest pending IOC.  A short fill -> flatten intent for the unhedged leader quantity."""
        if not self.pending:
            return None
        t_fill, want = self.pending.pop(0)
        self.latency_ms.append((float(now) - t_fill) * 1e3)
        got = min(max(float(filled_qty), 0.0), want)
        if self.state == 'NAKED':                   # late IOC fill after the leader was flattened -> undo it
            return None if got <= 0 else {
                'symbol': self.follow['symbol'], 'side': 'SELL' if self.follow['side'] == 'BUY' else 'BUY',
                'type': 'MARKET', 'reduceOnly': True, 'quantity': got, 'leg': 'flatten', 'reason': 'LATE_HEDGE'}
        self.hedged += got
        if got < want * (1 - 1e-9):
            return self._flatten('IOC_SHORT')
        if not self.pending and self.lead_filled >= self.lead['qty'] * (1 - 1e-9):
            self.state = 'DONE'
        return None

    def check(self, now: float) -> Optional[dict]:
        """Enforce the hedge deadline and the quote TTL.  -> flatten / cancel intent or None."""
        late = self.pending and (float(now) - self.pending[0][0]) * 1e3 > self.hedge_deadline_ms
        if self.state == 'HEDGING' and late:
            return self._flatten('HEDGE_DEADLINE')
        if self.state == 'QUOTING' and float(now) - self.t_quote > self.quote_ttl_s:
            self.state = 'CANCELLED'
            return {'symbol': self.lead['symbol'], 'action': 'CANCEL', 'leg': 'leader', 'reason': 'QUOTE_TTL'}
        return None

    def _flatten(self, reason: str) -> dict:
        unhedged_lead = self.lead_filled - self.hedged * self.lead['qty'] / self.follow['qty']
        self.state = 'NAKED'
        return {'symbol': self.lead['symbol'], 'side': 'SELL' if self.lead['side'] == 'BUY' else 'BUY',
                'type': 'MARKET', 'reduceOnly': True, 'quantity': max(unhedged_lead, 0.0), 'leg': 'flatten',
                'reason': reason, 'cancel_leader_rest': True}


class StatArbLane:
    """
    Stat-Arb Cointegration Lane:
    Operates on cointegrated pairs from the available active engines.
    Tracks spread in real-time using OnlineKalmanPairTracker.
    Estimates OU half-life and equilibrium standard deviation.
    Runs in 'shadow' (default paper simulation) or 'active' (real/mock venue orders).
    Logs to data/stat_arb_shadow.jsonl or data/stat_arb_trades.jsonl.
    """

    def __init__(
        self,
        engines: dict,
        data_dir: Path,
        mode: str = 'shadow',
        pairs: Optional[Sequence[Tuple[str, str]]] = None,
        notional_usdt: float = 50.0,
        round_trip_cost_bps: float = 12.0,
        z_entry: float = 2.10,
        z_exit: float = 0.25,
        z_stop: float = 3.60,
        every_s: float = 1.0,
        z_alpha: Optional[float] = 0.5,
        quote_every_s: float = 60.0,
    ) -> None:
        self.engines = engines
        self.z_alpha = z_alpha              # None = static z_entry; else DynamicZBand around z_entry
        self.data_dir = Path(data_dir)
        self.mode = mode
        self.notional_usdt = float(notional_usdt)
        self.round_trip_cost_bps = float(round_trip_cost_bps)
        self.z_entry = float(z_entry)
        self.z_exit = float(z_exit)
        self.z_stop = float(z_stop)
        self.every_s = float(every_s)
        self.ledger = self.data_dir / ('stat_arb_trades.jsonl' if mode == 'active' else 'stat_arb_shadow.jsonl')
        # 10-16: one synthetic-spread snapshot per pair every quote_every_s (why it does / does not enter), kept out
        # of the trade ledger; the 10-16 session wrote no stat-arb row at all
        self.quotes = self.data_dir / 'stat_arb_quotes.jsonl'
        self.quote_every_s = float(quote_every_s)
        self._quoted: dict[str, float] = {}

        if pairs:
            self.pairs = list(pairs)
        else:
            syms = [s for s in engines.keys() if s in ('SOLUSDT', 'DOGEUSDT', 'BTCUSDT', 'ETHUSDT')]
            if len(syms) >= 2:
                if 'SOLUSDT' in syms and 'DOGEUSDT' in syms:
                    self.pairs = [('SOLUSDT', 'DOGEUSDT')]
                else:
                    self.pairs = [(syms[0], syms[1])]
            else:
                self.pairs = []

        self.trackers: dict[str, OnlineKalmanPairTracker] = {}
        self.bands: dict[str, DynamicZBand] = {}
        self.positions: dict[str, dict] = {}
        self.last: dict[str, dict] = {}
        self.day_pnl: float = 0.0
        self.total_trades: int = 0

        for a, b in self.pairs:
            pair_id = f"{a}_{b}"
            self.trackers[pair_id] = OnlineKalmanPairTracker(
                asset_a=a, asset_b=b,
                delta_alpha=1e-5, delta_beta=1e-4, obs_var_init=1e-3, ou_window=120
            )
            if self.z_alpha is not None:
                self.bands[pair_id] = DynamicZBand(base=self.z_entry, alpha=self.z_alpha)

    async def step(self, now: Optional[float] = None) -> dict:
        now = time.time() if now is None else now
        results = {}

        for a, b in self.pairs:
            pair_id = f"{a}_{b}"
            ea = self.engines.get(a)
            eb = self.engines.get(b)
            if not ea or not eb:
                continue

            px_a = ea.feed.price() if getattr(ea, 'feed', None) else None
            px_b = eb.feed.price() if getattr(eb, 'feed', None) else None
            if not px_a or not px_b or px_a <= 0 or px_b <= 0:
                continue

            tracker = self.trackers[pair_id]
            band = self.bands.get(pair_id)
            z_entry = band.z_entry(now) if band else self.z_entry
            sig = tracker.evaluate_signal(
                px_a, px_b,
                round_trip_cost_bps=self.round_trip_cost_bps,
                z_entry=z_entry,
                z_exit=self.z_exit,
                z_stop=self.z_stop,
            )
            if band and tracker._spread_history:
                band.update(now, tracker._spread_history[-1])

            pos = self.positions.get(pair_id)

            if sig.action in ("ENTER_LONG_SPREAD", "ENTER_SHORT_SPREAD") and pos is None:
                side = +1 if sig.action == "ENTER_LONG_SPREAD" else -1
                pos = {
                    'pair_id': pair_id,
                    'side': side,
                    'entry_t': now,
                    'px_a_entry': px_a,
                    'px_b_entry': px_b,
                    'beta': sig.hedge_ratio,
                    'notional': self.notional_usdt,
                    'z_entry': sig.z_score,
                }
                self.positions[pair_id] = pos
                msg = f"STAT_ARB {self.mode.upper()} {sig.action} {a}/{b} Z={sig.z_score:+.2f} beta={sig.hedge_ratio:.2f}"
                if hasattr(ea, 'event'):
                    ea.event(msg)
                self._record_trade({
                    'type': 'ENTRY',
                    't': now,
                    'pair_id': pair_id,
                    'action': sig.action,
                    'side': side,
                    'px_a': px_a,
                    'px_b': px_b,
                    'beta': sig.hedge_ratio,
                    'z_score': sig.z_score,
                    'z_entry_used': z_entry,
                    'half_life': sig.half_life_bars,
                    'mode': self.mode,
                })

            elif sig.action in ("EXIT_SPREAD", "STOP_BREAKDOWN") and pos is not None:
                side = pos['side']
                ret_a = (px_a / pos['px_a_entry'] - 1.0)
                ret_b = (px_b / pos['px_b_entry'] - 1.0)
                raw_spread_ret = side * (ret_a - pos['beta'] * ret_b)
                gross_bps = raw_spread_ret * 1e4
                net_bps = gross_bps - self.round_trip_cost_bps
                pnl_usd = (net_bps * 1e-4) * pos['notional']

                self.day_pnl += pnl_usd
                self.total_trades += 1
                hold_s = now - pos['entry_t']
                del self.positions[pair_id]

                msg = f"STAT_ARB {self.mode.upper()} {sig.action} {a}/{b} Z={sig.z_score:+.2f} net={net_bps:+.1f} bps ({pnl_usd:+.3f} U) hold={hold_s:.0f}s"
                if hasattr(ea, 'event'):
                    ea.event(msg)
                self._record_trade({
                    'type': 'EXIT',
                    't': now,
                    'pair_id': pair_id,
                    'action': sig.action,
                    'side': side,
                    'px_a_exit': px_a,
                    'px_b_exit': px_b,
                    'beta': pos['beta'],
                    'z_score': sig.z_score,
                    'net_bps': net_bps,
                    'pnl_usd': pnl_usd,
                    'hold_s': hold_s,
                    'reason': sig.reason,
                    'mode': self.mode,
                })

            res = {
                'pair_id': pair_id,
                'action': sig.action,
                'z_score': round(sig.z_score, 2),
                'z_entry': round(z_entry, 2),
                'beta': round(sig.hedge_ratio, 3),
                'spread_std_bps': round(sig.spread_std_bps, 1),
                'half_life': round(sig.half_life_bars, 1),
                'in_trade': tracker.current_position != 0,
                'bars_in_trade': tracker.bars_in_trade,
                'day_pnl_usd': round(self.day_pnl, 4),
                'reason': sig.reason,
            }
            self.last[pair_id] = res
            if now - self._quoted.get(pair_id, -math.inf) >= self.quote_every_s:
                self._quoted[pair_id] = now
                self._append(self.quotes, {'type': 'QUOTE', 't': now, 'px_a': px_a, 'px_b': px_b,
                                           'spread_std_bps_min': 3.5 * self.round_trip_cost_bps,
                                           'mode': self.mode, **res})
            results[pair_id] = res

        return results

    def _record_trade(self, row: dict) -> None:
        self._append(self.ledger, row)

    @staticmethod
    def _append(path: Path, row: dict) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(row) + '\n')
        except OSError:
            pass

    def view(self) -> dict:
        return {
            'mode': self.mode,
            'pairs': self.pairs,
            'day_pnl_usd': round(self.day_pnl, 4),
            'total_trades': self.total_trades,
            'pairs_state': self.last,
        }

