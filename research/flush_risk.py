"""Pure risk sizing, stop geometry and breakeven policy. No I/O, no network.

The binding constraint at 3.85 USDT is not the signal, it is the loss budget.
With N=5.03 USDT and risk_allowed=0.015 USDT the widest stop the account can
carry is 20.82 bps (spec section 6). A structural stop wider than that means the
setup does not fit this account -- the answer is to skip it, never to shrink the
stop to fit, which would only relocate the same loss to a higher hit rate.
"""
from typing import Dict, Tuple

S_MAX_BPS = 20.82         # widest stop the 0.015 USDT risk budget can carry at N=5.03
RISK_ALLOWED_USDT = 0.015
RESERVE_USDT = 0.005      # kept clear of the 5-cent floor
KILL_LOSS_USDT = 0.05

MAKER_FEE_BPS = 2.0
TAKER_FEE_BPS = 5.0
SLIP_STRESS_BPS = 2.0     # assumption until real fills exist; stress 5 and 10 too
ROUNDTRIP_STOP_BPS = MAKER_FEE_BPS + TAKER_FEE_BPS + SLIP_STRESS_BPS   # 9.0
ROUNDTRIP_TP_BPS = MAKER_FEE_BPS + MAKER_FEE_BPS                       # 4.0
PAYOFF_MIN = 1.2


def tick_bps(tick_size: float, price: float) -> float:
    return (tick_size / price) * 1e4 if price > 0 else 0.0


def structural_stop_bps(entry: float, structural_stop: float) -> float:
    if entry <= 0:
        return 0.0
    return abs(entry - structural_stop) / entry * 1e4


def resolve_stop_bps(s_structure: float, atr_bps: float, tick_bps_value: float) -> float:
    """s = max(s_structure, 0.8*a, 3*tick). Floors only widen, never narrow."""
    return max(s_structure, 0.8 * atr_bps, 3.0 * tick_bps_value)


def target_bps(side: str, s_bps: float) -> float:
    """T_long = max(25, 1.5s); T_short = max(25, 2.0s), gross."""
    mult = 2.0 if str(side).upper() == 'SELL' else 1.5
    return max(25.0, mult * s_bps)


def payoff_ratio(t_bps: float, s_bps: float) -> float:
    """(T-4)/(s+9). A preliminary screen, not proof of positive EV."""
    denom = s_bps + ROUNDTRIP_STOP_BPS
    return (t_bps - ROUNDTRIP_TP_BPS) / denom if denom > 0 else 0.0


def risk_usdt(qty: float, entry: float, s_bps: float,
              slip_stress_bps: float = SLIP_STRESS_BPS) -> float:
    """R = q*E*(s + maker + taker + slip)/10000 -- what one stop actually costs."""
    return qty * entry * (s_bps + MAKER_FEE_BPS + TAKER_FEE_BPS + slip_stress_bps) / 1e4


def remaining_budget_usdt(liquidation_equity: float, initial_equity: float) -> float:
    floor = initial_equity - KILL_LOSS_USDT
    return max(0.0, liquidation_equity - floor)


def risk_allowed_usdt(liquidation_equity: float, initial_equity: float) -> float:
    """min(0.015, 0.004*equity, remaining - reserve). Shrinks to 0 near the floor."""
    remaining = remaining_budget_usdt(liquidation_equity, initial_equity)
    return min(RISK_ALLOWED_USDT, 0.004 * liquidation_equity,
               max(0.0, remaining - RESERVE_USDT))


def stop_width_gate(s_bps: float, s_max_bps: float = S_MAX_BPS) -> Tuple[bool, str]:
    """The trade fits the account, or it is skipped. There is no third option.

    Narrowing a structural stop to pass this gate does not reduce risk: it moves
    the same loss behind a higher stop-out rate and breaks the link between the
    stop and the level that invalidated the setup.
    """
    if s_bps <= 0:
        return False, 'INVALID_STOP_WIDTH'
    if s_bps > s_max_bps:
        return False, f'STOP_TOO_WIDE_FOR_CAPITAL(s={s_bps:.2f}bps > {s_max_bps:.2f}bps)'
    return True, 'OK'


def entry_risk_gate(qty: float, entry: float, s_bps: float, liquidation_equity: float,
                    initial_equity: float, slip_stress_bps: float = SLIP_STRESS_BPS,
                    s_max_bps: float = S_MAX_BPS) -> Tuple[bool, str, Dict]:
    """Full pre-entry budget check. Returns (allowed, reason, diagnostics)."""
    width_ok, width_reason = stop_width_gate(s_bps, s_max_bps)
    allowance = risk_allowed_usdt(liquidation_equity, initial_equity)
    risk = risk_usdt(qty, entry, s_bps, slip_stress_bps)
    diag = {'s_bps': s_bps, 'risk_usdt': risk, 'risk_allowed_usdt': allowance,
            'remaining_usdt': remaining_budget_usdt(liquidation_equity, initial_equity)}
    if not width_ok:
        return False, width_reason, diag
    if qty <= 0 or entry <= 0:
        return False, 'INVALID_QUANTITY', diag
    if risk > allowance:
        return False, f'RISK_EXCEEDS_BUDGET({risk:.4f} > {allowance:.4f} USDT)', diag
    return True, 'OK', diag


def breakeven_trigger_bps(atr_bps: float, tick_bps_value: float,
                          slip_stress_bps: float = SLIP_STRESS_BPS) -> float:
    """trigger = max(15, b + max(3*tick, 0.3*a)), with b = 2+5+slip+1.

    A fixed +15 trigger is the wrong shape: in a quiet tape it sits far above the
    noise and never arms, and in a fast tape it arms inside the noise and books
    the premature stop-out this whole comparison exists to measure. Scaling the
    offset with ATR keeps the trigger at a constant distance in volatility units.
    """
    b = float(MAKER_FEE_BPS + TAKER_FEE_BPS + slip_stress_bps + 1.0)
    return max(15.0, b + max(3.0 * tick_bps_value, 0.3 * atr_bps))


def trail_distance_bps(atr_bps: float, tick_bps_value: float) -> float:
    """distance = max(3*tick, 0.5*a)."""
    return max(3.0 * tick_bps_value, 0.5 * atr_bps)


def desired_protection_bps(previous_protection: float, mfe_bps: float,
                           atr_bps: float, tick_bps_value: float,
                           slip_stress_bps: float = SLIP_STRESS_BPS) -> float:
    """max(previous, b, MFE - distance). Monotonic: a stop only ever tightens."""
    b = float(MAKER_FEE_BPS + TAKER_FEE_BPS + slip_stress_bps + 1.0)
    return max(previous_protection, b, mfe_bps - trail_distance_bps(atr_bps, tick_bps_value))
