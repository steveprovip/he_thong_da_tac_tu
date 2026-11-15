"""MCF-1: which non-CEX path can a 10 USD base use? Prop challenge, Solana DLMM LP, airdrop farming, local-AI income.

    python -m research.micro_capital_frontiers dlmm   # public snapshot: Meteora DLMM top pools + GeckoTerminal daily OHLCV
    python -m research.micro_capital_frontiers run    # models + decision matrix -> data/micro_capital/micro_capital_frontiers.json

Offline models and public snapshots only: no account, no order, no money. Every input that is not measured here is
an explicit assumption with its source or range in docs/research_upgrade/MICRO_CAPITAL_10U_FEASIBILITY_REPORT_2026_11.md.

F1 prop challenge   Monte Carlo of evaluation phases and a funded account (static or trailing floor, daily loss limit,
                    payout dates, profit split) for a strategy with a stated annual Sharpe; closed-form bounds for a
                    driftless strategy: P(pass) <= buffer / (target + buffer), E[withdrawals] <= loss buffer.
F2 DLMM             loss-versus-rebalancing (LVR) of a concentrated position against the pool's fee yield, on a
                    snapshot of the top Meteora pools and their 30-day realised volatility; rent and fees of a 10 USD
                    position.
F3 airdrops         expected value of farming programs with a 10 USD gas budget (scenario ranges, no data set exists).
F4 local AI         streaming funnel (concurrent viewers x revenue per viewer-hour) and a services funnel (proposals x
                    win rate x order value).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / 'data' / 'micro_capital'
OUT = OUT_DIR / 'micro_capital_frontiers.json'
SNAPSHOT = OUT_DIR / 'dlmm_snapshot.json'
SEED = 20261115
TRADING_DAYS = 252
CAPITAL = 10.0
HORIZON_DAYS = 90                                   # calendar days of the comparison window
HOURS = 24.0 * HORIZON_DAYS


# ================================================================================================ F1 prop challenge
@dataclass(frozen=True)
class Phase:
    target: float                    # profit target, fraction of the initial balance
    max_loss: float                  # loss buffer, fraction of the initial balance
    trailing: bool = False           # the floor follows the high-water mark
    daily_loss: float = math.inf     # loss from the day's opening equity that ends the account
    min_days: int = 0


@dataclass(frozen=True)
class Program:
    name: str
    size: float                      # USD of simulated capital
    entry_fee: float
    activation_fee: float            # due after passing, before the funded account starts
    phases: tuple
    funded: Phase                    # target unused; the floor of the funded account
    split: float                     # trader share of profits
    payout_days: int                 # trading days between payout dates
    min_payout: float                # smallest trader share paid (USD)
    lock_at_start: bool = True       # a trailing floor stops rising at the initial balance
    source: str = ''


PROGRAMS = {
    'FTUK_FLEX_5K': Program(
        'FTUK Flex 5k', 5_000.0, 9.0, 56.0, (Phase(0.04, 0.06, trailing=True, daily_loss=0.05),),
        Phase(0.0, 0.06, trailing=True, daily_loss=0.05), 0.80, 10, 50.0, True,
        'entry 9 USD, activation 56 USD after passing, 4 % target, 6 % trailing floor locking at the initial balance, '
        '5 % daily (TradeLocker / FXEmpire / Propvator listings); funded split, payout cycle and minimum assumed'),
    'RF_SILVER_5K': Program(
        'RebelsFunding Silver 5k (2-step)', 5_000.0, 20.0, 0.0,
        (Phase(0.08, 0.10, daily_loss=0.05), Phase(0.05, 0.10, daily_loss=0.05)),
        Phase(0.0, 0.10, daily_loss=0.05), 0.80, 10, 50.0, True,
        'targets 8 % / 5 %, 5 % daily, first payout after 14 days, split 75-90 % (PropFirmMap / TheTrustedProp); '
        'price 20 USD unverified; 10 % static floor assumed'),
    'MICRO_1K_4STEP': Program(
        'Hypothetical 10 USD micro tier (1k, 4-step)', 1_000.0, 10.0, 0.0,
        tuple(Phase(0.05, 0.10, daily_loss=0.05) for _ in range(4)), Phase(0.0, 0.10, daily_loss=0.05), 0.80, 10, 10.0,
        True, 'not a verified offer: what 10 USD could buy if a firm sold a 1k four-step challenge without activation'),
}


def pass_prob_static(target: float, buffer: float) -> float:
    """P(+target before -buffer) for a driftless continuous P&L path."""
    return buffer / (target + buffer)


def pass_prob_trailing(target: float, drawdown: float) -> float:
    """P(+target before a drawdown of `drawdown` from the running maximum), driftless Brownian path (target <= lock)."""
    return math.exp(-target / drawdown)


def _draw(rng, n: int, mu: float, sd: float, df: Optional[float]) -> np.ndarray:
    if df is None:
        return mu + sd * rng.standard_normal(n)
    return mu + sd * rng.standard_t(df, n) / math.sqrt(df / (df - 2.0))


def run_account(rng, n: int, ph: Phase, mu_d: float, sigma_d: float, days: int, steps: int = 8, df: Optional[float] = 4.0,
                lock_at_start: bool = True, payout_days: int = 0, split: float = 0.0, min_payout_frac: float = 0.0,
                stop_at_target: bool = True) -> dict:
    """Vectorised paths of an account's P&L (fraction of the initial balance), `steps` draws per trading day.
    Evaluation (stop_at_target): -> passed, day passed. Funded (payout_days > 0): every payout date a profit whose
    trader share is >= min_payout_frac is paid out (split x profit) and the account resets to its initial balance."""
    eq, hwm = np.zeros(n), np.zeros(n)
    alive, passed = np.ones(n, dtype=bool), np.zeros(n, dtype=bool)
    day_pass = np.full(n, np.inf)
    paid, n_paid, first_pay = np.zeros(n), np.zeros(n, dtype=np.int64), np.full(n, np.inf)
    hist = np.zeros((n, days // payout_days if payout_days else 0))     # amount paid at each payout date
    mu_s, sd_s = mu_d / steps, sigma_d / math.sqrt(steps)
    for d in range(days):
        open_eq = eq.copy()
        for _ in range(steps):
            live = alive & ~passed
            if not live.any():
                break
            eq = np.where(live, eq + _draw(rng, n, mu_s, sd_s, df), eq)
            hwm = np.maximum(hwm, eq)
            if ph.trailing:
                floor = np.minimum(hwm - ph.max_loss, 0.0) if lock_at_start else hwm - ph.max_loss
            else:
                floor = -ph.max_loss
            dead = live & ((eq <= floor) | (open_eq - eq >= ph.daily_loss))
            alive &= ~dead
            if stop_at_target:
                hit = alive & ~passed & (eq >= ph.target)
                passed |= hit
                day_pass[hit] = max(d + 1, ph.min_days)
        if payout_days and (d + 1) % payout_days == 0:
            pay = alive & (eq * split >= min_payout_frac)
            paid[pay] += split * eq[pay]
            hist[pay, (d + 1) // payout_days - 1] = split * eq[pay]
            n_paid[pay] += 1
            first_pay[pay & ~np.isfinite(first_pay)] = d + 1
            eq[pay], hwm[pay] = 0.0, 0.0
    return {'alive': alive, 'passed': passed, 'day_pass': day_pass, 'paid': paid, 'n_paid': n_paid, 'first_pay': first_pay,
            'final': eq, 'hist': hist}


def simulate_program(prog: Program, sharpe: float, sigma_d: float, n: int = 20_000, eval_days: int = 250,
                     funded_days: int = 250, seed: int = SEED, df: Optional[float] = 4.0) -> dict:
    """One program for a strategy with annual Sharpe `sharpe` at daily volatility `sigma_d` (fraction of the account).
    Calendar days ~ trading days x 7 / 5. USD figures per challenge bought."""
    rng = np.random.default_rng(seed)
    mu_d = sharpe * sigma_d / math.sqrt(TRADING_DAYS)
    ok, day = np.ones(n, dtype=bool), np.zeros(n)
    phase_rates = []
    for ph in prog.phases:
        r = run_account(rng, n, ph, mu_d, sigma_d, eval_days, df=df, lock_at_start=prog.lock_at_start)
        step_ok = r['passed']
        phase_rates.append(float(step_ok[ok].mean()) if ok.any() else 0.0)
        ok &= step_ok
        day = day + np.where(step_ok, r['day_pass'], 0.0)
    f = run_account(rng, n, prog.funded, mu_d, sigma_d, funded_days, df=df, lock_at_start=prog.lock_at_start,
                    payout_days=prog.payout_days, split=prog.split, min_payout_frac=prog.min_payout / prog.size,
                    stop_at_target=False)
    paid_usd = np.where(ok, f['paid'] * prog.size, 0.0)
    got = ok & (f['n_paid'] > 0)
    first_cal = (day + f['first_pay']) * 7.0 / 5.0                    # trading days -> calendar days
    in_window = ok & (first_cal <= HORIZON_DAYS)
    # payouts dated inside the 90-calendar-day window that starts at the purchase
    left = np.floor(HORIZON_DAYS * 5.0 / 7.0 - day)                    # funded trading days left in the window
    k_dates = np.clip(np.floor(left / prog.payout_days).astype(np.int64), 0, f['hist'].shape[1])
    cum = np.concatenate([np.zeros((n, 1)), np.cumsum(f['hist'], axis=1)], axis=1)
    window_usd = np.where(ok, cum[np.arange(n), k_dates] * prog.size, 0.0)
    p_pass = float(ok.mean())
    fees = prog.entry_fee + p_pass * prog.activation_fee
    out = {'program': prog.name, 'sharpe': sharpe, 'sigma_d': sigma_d, 'mu_d': mu_d, 'phase_pass': phase_rates,
           'p_pass': p_pass, 'p_payout': float(got.mean()), 'p_payout_given_funded': float(got[ok].mean()) if ok.any() else 0.0,
           'e_payout_usd': float(paid_usd.mean()), 'e_payout_given_funded_usd': float(paid_usd[ok].mean()) if ok.any() else 0.0,
           'payout_p50_given_paid': float(np.median(paid_usd[got])) if got.any() else 0.0,
           'payout_p95_given_paid': float(np.quantile(paid_usd[got], 0.95)) if got.any() else 0.0,
           'e_fees_usd': fees, 'ev_usd': float(paid_usd.mean() - fees),
           'p_lose_all': float(1.0 - got.mean()), 'first_payout_cal_days_p50': float(np.median(first_cal[got])) if got.any() else None,
           'p_first_payout_in_90d': float(in_window.mean()), 'funded_survival_1y': float(f['alive'][ok].mean()) if ok.any() else 0.0,
           'e_payout_90d_usd': float(window_usd.mean()), 'ev_90d_usd': float(window_usd.mean() - fees),
           'usd_per_hour_90d': float((window_usd.mean() - fees) / HOURS),
           'payout_year_p75_usd': float(np.quantile(paid_usd, 0.75)), 'payout_year_p90_usd': float(np.quantile(paid_usd, 0.90)),
           'bound_pass_driftless': float(np.prod([pass_prob_trailing(p.target, p.max_loss) if p.trailing
                                                  else pass_prob_static(p.target, p.max_loss) for p in prog.phases])),
           'bound_payout_given_funded_usd': prog.split * prog.funded.max_loss * prog.size}
    out['bound_ev_usd'] = out['bound_pass_driftless'] * (out['bound_payout_given_funded_usd'] - prog.activation_fee) - prog.entry_fee
    return out


def sharpe_needed(mu_d: float, sigma_d: float) -> float:
    """Annual Sharpe implied by a daily mean and volatility (fractions of the account)."""
    return mu_d / sigma_d * math.sqrt(TRADING_DAYS)


# ================================================================================================ F2 DLMM
def lvr_per_day(sigma_d: float, k: float = math.inf) -> float:
    """Loss-versus-rebalancing per day as a fraction of the position's value, at the centre of a symmetric range
    [p / k, p * k] (k = inf: full range, sigma^2 / 8). Milionis-Moallemi-Roughgarden-Zhang 2022 with the v3 value
    function V = L (2 sqrt p - sqrt pa - p / sqrt pb)."""
    if math.isinf(k):
        return sigma_d ** 2 / 8.0
    return sigma_d ** 2 / (8.0 * (1.0 - k ** -0.5))


def breakeven_sigma(fee_yield_d: float) -> float:
    """Largest daily volatility at which a full-range position's fees cover its LVR."""
    return math.sqrt(8.0 * max(fee_yield_d, 0.0))


def dlmm_budget(sol_usd: float, capital: float = CAPITAL, rent_sol: float = 0.059, bin_array_sol: float = 0.075,
                new_bin_array: bool = False, tx_sol: float = 0.0004, swap_cost: float = 0.003) -> dict:
    """What 10 USD leaves for liquidity: position rent locked (refundable at close), bin-array rent lost if the range
    touches an uninitialised array, transaction fees, and the swap of half the capital into the other token."""
    locked = rent_sol * sol_usd
    lost = (bin_array_sol * sol_usd if new_bin_array else 0.0) + tx_sol * sol_usd
    deploy = max(capital - locked - lost, 0.0)
    deploy -= deploy / 2.0 * swap_cost
    return {'sol_usd': sol_usd, 'rent_locked_usd': locked, 'lost_usd': lost, 'deployable_usd': max(deploy, 0.0),
            'deployable_share': max(deploy, 0.0) / capital}


STABLES = {'USDC', 'USDT', 'USDS', 'PYUSD', 'USD1', 'USDG', 'FDUSD', 'USDe', 'USDH', 'EURC', 'USDY'}


def pool_stats(snapshot: list) -> dict:
    """Per pool: fee yield (24 h fees / TVL), 30-day daily log-return sd of the pool price, full-range and +-10 %
    LVR, the drawdown of the price inside the window. Summary over pools with >= 20 days of prices."""
    rows = []
    for p in snapshot:
        px = p.get('closes') or []
        if len(px) < 21 or p.get('tvl', 0) <= 0:
            continue
        lr = np.diff(np.log(np.asarray(px, dtype=np.float64)))
        sd = float(lr.std(ddof=1))
        f = float(p['fees_24h']) / float(p['tvl'])
        rows.append({'name': p['name'], 'tvl': float(p['tvl']), 'fee_yield_d': f, 'vol_tvl': float(p['volume_24h']) / float(p['tvl']),
                     'sigma_d': sd, 'lvr_full': lvr_per_day(sd), 'lvr_10pct': lvr_per_day(sd, 1.10),
                     'net_full': f - lvr_per_day(sd), 'net_10pct': f - lvr_per_day(sd, 1.10),
                     'drawdown': float(1.0 - np.min(px) / np.max(px[:int(np.argmin(px)) + 1])), 'stable_pair': p.get('stable_pair', False),
                     'age_days': p.get('age_days')})
    if not rows:
        return {'n': 0}
    vol = [r for r in rows if not r['stable_pair']]
    q = lambda key, s, a: float(np.quantile([r[key] for r in s], a))
    return {'n': len(rows), 'n_volatile': len(vol),
            'fee_yield_d': {'p50': q('fee_yield_d', rows, 0.5), 'p90': q('fee_yield_d', rows, 0.9)},
            'sigma_d': {'p50': q('sigma_d', vol, 0.5), 'p90': q('sigma_d', vol, 0.9)},
            'share_fee_above_lvr_full': float(np.mean([r['net_full'] > 0 for r in vol])),
            'share_fee_above_lvr_10pct': float(np.mean([r['net_10pct'] > 0 for r in vol])),
            'net_full_p50': q('net_full', vol, 0.5), 'net_10pct_p50': q('net_10pct', vol, 0.5),
            'net_full_p75': q('net_full', vol, 0.75), 'net_full_p90': q('net_full', vol, 0.9),
            'share_drawdown_over_50pct': float(np.mean([r['drawdown'] > 0.5 for r in vol])),
            'share_drawdown_over_90pct': float(np.mean([r['drawdown'] > 0.9 for r in vol])),
            'share_vol_tvl_over_5': float(np.mean([r['vol_tvl'] > 5 for r in rows])),
            'rows': rows}


# ================================================================================================ F3 airdrops
AIRDROP_SCENARIOS = {
    # p_pay: a farmed program distributes within the window; p_qualify: a 10 USD single wallet clears its thresholds;
    # p_clear: not excluded by the sybil filter (LayerZero excluded 38-59 % of its snapshot); value: USD after the
    # post-TGE decline for a small wallet. Assumptions, not measurements.
    'pessimistic': {'p_pay': 0.3, 'p_qualify': 0.1, 'p_clear': 0.4, 'value': 5.0},
    'central': {'p_pay': 0.5, 'p_qualify': 0.3, 'p_clear': 0.6, 'value': 30.0},
    'optimistic': {'p_pay': 0.7, 'p_qualify': 0.6, 'p_clear': 0.8, 'value': 150.0},
}


def airdrop_ev(p_pay: float, p_qualify: float, p_clear: float, value: float, programs: int = 4, gas_usd: float = CAPITAL,
               tx_cost: float = 0.003) -> dict:
    p = p_pay * p_qualify * p_clear
    e = programs * p * value
    return {'p_program_pays': p, 'tx_affordable': int(gas_usd / tx_cost), 'e_value_usd': e, 'ev_usd': e - gas_usd,
            'p_lose_all': (1.0 - p) ** programs, 'usd_per_hour': (e - gas_usd) / (24.0 * 182.0)}


# ================================================================================================ F4 local AI
def stream_revenue(hours_per_day: float = 4.0, ccv_median: float = 1.0, p_ccv_ge5: float = 0.08,
                   usd_per_ccv_hour: float = 0.02, days: int = HORIZON_DAYS) -> dict:
    """Lognormal average concurrent viewers calibrated to a median and P(CCV >= 5); revenue = CCV x hours x rate."""
    from statistics import NormalDist
    z = NormalDist().inv_cdf(1.0 - p_ccv_ge5)
    s = math.log(5.0 / ccv_median) / z
    mean_ccv = ccv_median * math.exp(s * s / 2.0)
    e_day = mean_ccv * hours_per_day * usd_per_ccv_hour
    med_day = ccv_median * hours_per_day * usd_per_ccv_hour
    p75_ccv = ccv_median * math.exp(s * NormalDist().inv_cdf(0.75))
    p90_ccv = ccv_median * math.exp(s * NormalDist().inv_cdf(0.90))
    return {'sigma_ln': s, 'mean_ccv': mean_ccv, 'p_ccv_ge10': 1.0 - NormalDist().cdf(math.log(10.0 / ccv_median) / s),
            'e_usd_day': e_day, 'median_usd_day': med_day, 'e_usd_per_hour': e_day / 24.0,
            'days_to_50usd_median': 50.0 / med_day if med_day > 0 else math.inf, 'e_usd_window': e_day * days,
            'p75_ccv': p75_ccv, 'p75_usd_month': p75_ccv * hours_per_day * usd_per_ccv_hour * 30.0,
            'p90_ccv': p90_ccv, 'p90_usd_month': p90_ccv * hours_per_day * usd_per_ccv_hour * 30.0}


def services_funnel(weeks: float = 13.0, proposals_per_week: float = 10.0, win_rate: float = 0.03, order_value: float = 40.0,
                    fee: float = 0.15, hours_per_order: float = 4.0, hours_per_proposal: float = 0.25) -> dict:
    n = weeks * proposals_per_week
    orders = n * win_rate
    net = orders * order_value * (1.0 - fee)
    owner_h = orders * hours_per_order + n * hours_per_proposal
    return {'proposals': n, 'orders': orders, 'net_usd': net, 'owner_hours': owner_h,
            'usd_per_owner_hour': net / owner_h if owner_h else 0.0, 'usd_per_calendar_hour': net / (weeks * 7.0 * 24.0),
            'p_no_order': (1.0 - win_rate) ** n, 'weeks_to_first_order_mean': 1.0 / (win_rate * proposals_per_week)}


# ================================================================================================ decision matrix
CRITERIA = ('ruin', 'time_to_first_dollar', 'hourly_velocity', 'friction', 'scalability')
WEIGHTS = {'equal': {c: 0.2 for c in CRITERIA},
           'capital_safety': {'ruin': 0.35, 'time_to_first_dollar': 0.15, 'hourly_velocity': 0.2, 'friction': 0.1, 'scalability': 0.2},
           'velocity': {'ruin': 0.1, 'time_to_first_dollar': 0.2, 'hourly_velocity': 0.4, 'friction': 0.1, 'scalability': 0.2}}


def score_ruin(p_lose_all: float) -> float:
    return max(1.0, 100.0 * (1.0 - p_lose_all))


def score_time(days: Optional[float]) -> float:
    return 1.0 if days is None or not math.isfinite(days) else max(1.0, 100.0 * math.exp(-days / 30.0))


def score_velocity(usd_per_hour: float) -> float:
    """log scale: <= 0.001 USD/h -> 1, 1 USD/h (the target) -> 100."""
    if usd_per_hour <= 0.001:
        return 1.0
    return float(min(100.0, max(1.0, 100.0 * (math.log10(usd_per_hour) + 3.0) / 3.0)))


def score_friction(engineering_hours: float, gates: int) -> float:
    """Engineering hours to a first real attempt, minus 15 points per external gate (KYC, follower threshold, ...)."""
    return max(1.0, 100.0 * math.exp(-engineering_hours / 80.0) - 15.0 * gates)


def score_scalability(usd_month_p90: float) -> float:
    """log scale of the monthly net of a good (90th-percentile) outcome: 1 USD -> 1, 100 USD -> 50, 10 000 USD -> 100."""
    return float(min(100.0, max(1.0, 25.0 * math.log10(max(usd_month_p90, 1.0)))))


def decision_matrix(metrics: dict) -> dict:
    scores = {}
    for k, m in metrics.items():
        scores[k] = {'ruin': score_ruin(m['p_lose_all']), 'time_to_first_dollar': score_time(m['days_to_first_dollar']),
                     'hourly_velocity': score_velocity(m['usd_per_hour']), 'friction': score_friction(m['eng_hours'], m['gates']),
                     'scalability': score_scalability(m['usd_month_p90'])}
    totals = {w: {k: sum(s[c] * wt[c] for c in CRITERIA) for k, s in scores.items()} for w, wt in WEIGHTS.items()}
    ranks = {w: sorted(t, key=lambda k: -t[k]) for w, t in totals.items()}
    return {'scores': scores, 'totals': totals, 'ranks': ranks}


# ================================================================================================ DLMM snapshot fetch
def _get(url: str, tries: int = 5):
    """Parsed JSON; HTTP 429 waits a minute (GeckoTerminal's free tier), other failures back off briefly."""
    import urllib.error
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'research', 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if k == tries - 1:
                raise
            time.sleep(61 if e.code == 429 else 5 * (k + 1))
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(5 * (k + 1))
    return None


def fetch_dlmm(n_pages: int = 3, gap_s: float = 6.5, log=print) -> list:
    """Top pools by 24 h volume (Meteora data API) and 31 daily closes of each pool's own price (GeckoTerminal,
    quote-token units)."""
    pools, now = [], time.time()
    for pg in range(1, n_pages + 1):
        pools += _get(f'https://dlmm.datapi.meteora.ag/pools?page={pg}&page_size=50&sort_by=volume_24h:desc')['data']
        time.sleep(1.0)
    out = []
    for i, p in enumerate(pools):
        sx, sy = p['token_x']['symbol'], p['token_y']['symbol']
        rec = {'address': p['address'], 'name': p['name'], 'tvl': p['tvl'], 'volume_24h': p['volume']['24h'],
               'fees_24h': p['fees']['24h'], 'bin_step': p['pool_config']['bin_step'], 'base_fee_pct': p['pool_config']['base_fee_pct'],
               'stable_pair': sx in STABLES and sy in STABLES, 'age_days': (now * 1000 - p['created_at']) / 86_400_000,
               'verified': bool(p['token_x']['is_verified'] and p['token_y']['is_verified']), 'sol_usd': None}
        if p['name'] == 'SOL-USDC':
            rec['sol_usd'] = p['token_x']['price']
        if rec['stable_pair']:                                       # no volatility read needed
            rec['closes'] = []
            out.append(rec)
            continue
        try:
            o = _get(f'https://api.geckoterminal.com/api/v2/networks/solana/pools/{p["address"]}/ohlcv/day?limit=31&currency=token')
            rows = sorted(o['data']['attributes']['ohlcv_list'])
            rec['closes'] = [float(r[4]) for r in rows if r[4] and float(r[4]) > 0]
        except Exception as e:                                       # UNKNOWN: kept out of the volatility read
            rec['closes'], rec['error'] = [], str(e)[:120]
        out.append(rec)
        if i % 25 == 0:
            log(f'  {i + 1}/{len(pools)} {p["name"]}')
        time.sleep(gap_s)
    return out


# ================================================================================================ run
PROP_SCENARIOS = {'negative (Sharpe -1.5)': (-1.5, 0.01), 'negative (Sharpe -0.5)': (-0.5, 0.01), 'zero edge': (0.0, 0.01),
                  'edge (Sharpe 1.0)': (1.0, 0.01), 'edge (Sharpe 2.0)': (2.0, 0.01)}
# share of earned payouts never received (denial under discretionary terms, firm closure: 80-100 firms closed
# 2024-2025 per thepropfirmguide.com): an assumption applied to the F1* row of the matrix
COUNTERPARTY_HAIRCUT = 0.20


def run(snapshot: Optional[list] = None, n: int = 20_000, log=print) -> dict:
    res = {'capital_usd': CAPITAL, 'horizon_days': HORIZON_DAYS, 'seed': SEED}
    prop = {}
    for key, prog in PROGRAMS.items():
        prop[key] = {'rules': asdict(prog)}
        for label, (s, sd) in PROP_SCENARIOS.items():
            prop[key][label] = simulate_program(prog, s, sd, n=n)
            r = prop[key][label]
            log(f"  {prog.name:<45} {label:<24} P(pass) {r['p_pass']:.3f} P(payout) {r['p_payout']:.3f} "
                f"E[payout] {r['e_payout_usd']:7.2f} EV {r['ev_usd']:+7.2f} USD")
    res['prop'] = prop
    res['directive_bot'] = {f'sigma {sd:.3%}': sharpe_needed(0.005, sd) for sd in (0.005, 0.01, 0.02, 0.03)}
    if snapshot:
        sol = next((p['sol_usd'] for p in snapshot if p.get('sol_usd')), None)
        stats = pool_stats(snapshot)
        res['dlmm'] = {'pools': len(snapshot), 'with_prices': stats.get('n'), 'stats': {k: v for k, v in stats.items() if k != 'rows'},
                       'rows': stats.get('rows', []), 'sol_usd': sol,
                       'budget': {'existing_bin_array': dlmm_budget(sol), 'new_bin_array': dlmm_budget(sol, new_bin_array=True)} if sol else None}
    res['airdrop'] = {k: airdrop_ev(**v) for k, v in AIRDROP_SCENARIOS.items()}
    res['stream'] = {'p50_rate': stream_revenue(usd_per_ccv_hour=0.02), 'low_rate': stream_revenue(usd_per_ccv_hour=0.005),
                     'high_rate': stream_revenue(usd_per_ccv_hour=0.08)}
    res['services'] = {'new_seller': services_funnel(win_rate=0.02, order_value=30.0),
                       'central': services_funnel(win_rate=0.04, order_value=40.0),
                       'reviewed_seller': services_funnel(win_rate=0.10, order_value=60.0, proposals_per_week=15.0)}
    res['matrix_inputs'] = matrix_inputs(res)
    res['matrix'] = decision_matrix(res['matrix_inputs'])
    return res


CLEARING_DAYS = 14                                    # platform clearance before earnings can be withdrawn


def matrix_inputs(res: dict) -> dict:
    """The five quantities per frontier on a strict 10 USD budget, read from the models above; engineering hours and
    external gates are judgement inputs listed in the report. The F1* row prices the prop challenge with its
    activation fee paid (65 USD), outside the 10 USD constraint. Scalability = monthly net of a 90th-percentile outcome."""
    rules = res['prop']['FTUK_FLEX_5K']['rules']
    f1 = res['prop']['FTUK_FLEX_5K']['negative (Sharpe -0.5)']
    keep = 1.0 - COUNTERPARTY_HAIRCUT
    f1_fees = rules['entry_fee'] + f1['p_pass'] * rules['activation_fee']
    dl = res.get('dlmm') or {}
    st = dl.get('stats') or {}
    budget = (dl.get('budget') or {}).get('existing_bin_array') or {'deployable_usd': 0.0}
    dl_net_d = budget['deployable_usd'] * st.get('net_full_p50', 0.0)
    a, a_hi = res['airdrop']['central'], res['airdrop']['optimistic']
    s = res['stream']['p50_rate']
    sv, sv_hi = res['services']['central'], res['services']['reviewed_seller']
    return {
        'F1 prop challenge, strict 10 USD (FTUK Flex 5k)': {
            # a pass needs 56 USD more before any payout: on 10 USD every path ends with the entry fee lost
            'p_lose_all': 1.0, 'days_to_first_dollar': None, 'usd_per_hour': -rules['entry_fee'] / HOURS,
            'eng_hours': 40.0, 'gates': 2, 'usd_month_p90': 0.0},
        'F1* prop challenge, 56 USD activation paid (65 USD; Sharpe -0.5, 20 % payout haircut)': {
            'p_lose_all': 1.0 - keep * (1.0 - f1['p_lose_all']),
            'days_to_first_dollar': (f1['first_payout_cal_days_p50'] or math.inf) + CLEARING_DAYS,
            'usd_per_hour': (keep * f1['e_payout_90d_usd'] - f1_fees) / HOURS, 'eng_hours': 40.0, 'gates': 2,
            'usd_month_p90': keep * f1['payout_year_p90_usd'] / 12.0},
        'F2 Solana DLMM LP (median top-pool, 10 USD)': {
            'p_lose_all': st.get('share_drawdown_over_90pct', 1.0),
            'days_to_first_dollar': (1.0 / dl_net_d) if dl_net_d > 0 else None,
            'usd_per_hour': dl_net_d / 24.0, 'eng_hours': 30.0, 'gates': 0,
            'usd_month_p90': max(budget['deployable_usd'] * st.get('net_full_p90', 0.0) * 30.0, 0.0)},
        'F3 airdrop farming (10 USD of gas, central)': {
            'p_lose_all': a['p_lose_all'], 'days_to_first_dollar': 182.0, 'usd_per_hour': a['usd_per_hour'], 'eng_hours': 20.0,
            'gates': 1, 'usd_month_p90': max(a_hi['ev_usd'], 0.0) / 6.0},
        'F4a AI VTuber streaming (AI Linh)': {
            'p_lose_all': 0.0, 'days_to_first_dollar': s['days_to_50usd_median'], 'usd_per_hour': s['e_usd_per_hour'],
            'eng_hours': 25.0, 'gates': 2, 'usd_month_p90': s['p90_usd_month']},
        'F4b AI-assisted micro-services (local stack)': {
            'p_lose_all': sv['p_no_order'], 'days_to_first_dollar': 7.0 * sv['weeks_to_first_order_mean'] + CLEARING_DAYS,
            'usd_per_hour': sv['usd_per_calendar_hour'], 'eng_hours': 15.0, 'gates': 1, 'usd_month_p90': sv_hi['net_usd'] / 3.0},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('stage', choices=('dlmm', 'run'))
    ap.add_argument('--paths', type=int, default=20_000)
    a = ap.parse_args(argv)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if a.stage == 'dlmm':
        snap = fetch_dlmm()
        SNAPSHOT.write_text(json.dumps({'t': time.time(), 'pools': snap}))
        print(f'{len(snap)} pools -> {SNAPSHOT}')
        return 0
    snap = json.loads(SNAPSHOT.read_text())['pools'] if SNAPSHOT.exists() else None
    t0 = time.time()
    res = run(snap, n=a.paths)
    res['runtime_s'] = time.time() - t0
    OUT.write_text(json.dumps(res, indent=1, default=float))
    print('ranks', res['matrix']['ranks'], '->', OUT)
    return 0


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
