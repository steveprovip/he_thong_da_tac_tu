from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
from typing import Deque, Optional, Tuple


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
