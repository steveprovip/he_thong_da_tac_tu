"""Fast, statistically honest evaluation helpers (pure stdlib; no scipy on the device).

Why: a fix used to be judged by hours of demo trading and a gut look at the PnL card.  With a per-trade
sigma of 20-50 bps and edges of a few bps that is noise.  These helpers answer, in seconds:

  * how many trades are needed before a result means anything     -> required_n / required_pairs
  * is the live/demo ledger already decisively good or bad          -> sprt / msprt (anytime-valid)
  * is variant B better than A on the SAME replayed events          -> paired_diff (variance / (1-rho))
  * is a backtest Sharpe still significant after N tried variants   -> psr / dsr / expected_max_sr
  * net bps per trade of the engine ledger                          -> ledger_bps

Formulas: Wald (1945) SPRT; Johari et al. (2017) mixture SPRT; Bailey & Lopez de Prado (2012, 2014)
PSR / DSR.  All p-values are approximations (normal); treat them as a triage tool, not a proof.
"""
from __future__ import annotations

import math
import random
from statistics import NormalDist

_N = NormalDist()
EULER_GAMMA = 0.5772156649015329


def norm_cdf(x: float) -> float:
    return _N.cdf(x)


def norm_ppf(p: float) -> float:
    return _N.inv_cdf(p)


def _mean(xs):
    return sum(xs) / len(xs)


def _std(xs, ddof=1):
    n = len(xs)
    if n <= ddof:
        return float('nan')
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - ddof))


def moments(xs):
    """-> (mean, std, skew, kurtosis [not excess]) of a sample."""
    n = len(xs)
    m = _mean(xs)
    s = _std(xs, 0)
    if not s or n < 3:
        return m, s, 0.0, 3.0
    sk = sum((x - m) ** 3 for x in xs) / n / s ** 3
    ku = sum((x - m) ** 4 for x in xs) / n / s ** 4
    return m, _std(xs), sk, ku


# ------------------------------------------------------------------ sample size / power
def required_n(delta: float, sigma: float, alpha: float = 0.05, power: float = 0.80,
               two_sided: bool = False) -> int:
    """Trades needed to detect a mean shift `delta` with per-trade std `sigma` (one-sample z test)."""
    if delta <= 0 or sigma <= 0:
        raise ValueError('delta and sigma must be > 0')
    za = norm_ppf(1 - alpha / (2 if two_sided else 1))
    zb = norm_ppf(power)
    return math.ceil(((za + zb) * sigma / delta) ** 2)


def required_pairs(delta: float, sigma: float, rho: float, alpha: float = 0.05, power: float = 0.80) -> int:
    """Paired A/B on the same events: Var(a-b) = 2 sigma^2 (1-rho)."""
    if not -1 < rho < 1:
        raise ValueError('rho must be in (-1, 1)')
    return required_n(delta, sigma * math.sqrt(2 * (1 - rho)), alpha, power)


def required_unpaired(delta: float, sigma: float, alpha: float = 0.05, power: float = 0.80) -> int:
    """Per-arm n for two independent arms (live A/B on different trades)."""
    return required_n(delta, sigma * math.sqrt(2), alpha, power)


# ------------------------------------------------------------------ sequential tests
def sprt(xs, mu0: float, mu1: float, sigma: float | None = None, alpha: float = 0.05, beta: float = 0.05):
    """Wald SPRT for a normal mean, H0: mu=mu0 vs H1: mu=mu1 (mu1 > mu0).

    Returns {decision: 'H1'|'H0'|'continue', llr, n, upper, lower, at}.  `at` = trade index where the
    boundary was first crossed (the test stops there; later trades are ignored).  sigma=None -> plug-in
    sample std of all xs (slightly anti-conservative for tiny n; we floor n at 5 before deciding).
    """
    xs = list(xs)
    if mu1 <= mu0:
        raise ValueError('need mu1 > mu0')
    if sigma is None:
        sigma = _std(xs) if len(xs) > 1 else float('nan')
    upper, lower = math.log((1 - beta) / alpha), math.log(beta / (1 - alpha))
    out = {'decision': 'continue', 'llr': 0.0, 'n': len(xs), 'upper': upper, 'lower': lower, 'at': None,
           'sigma': sigma}
    if not xs or not sigma or sigma != sigma:
        return out
    llr, k = 0.0, (mu1 - mu0) / sigma ** 2
    mid = (mu0 + mu1) / 2
    for i, x in enumerate(xs, 1):
        llr += k * (x - mid)
        if i >= 5 and (llr >= upper or llr <= lower):
            out.update(decision='H1' if llr >= upper else 'H0', llr=llr, at=i)
            return out
    out['llr'] = llr
    return out


def sprt_expected_n(mu0: float, mu1: float, sigma: float, alpha: float = 0.05, beta: float = 0.05,
                    true_mu: float | None = None) -> float:
    """Wald's approximation of E[N] under true_mu (defaults to mu1)."""
    true_mu = mu1 if true_mu is None else true_mu
    a, b = math.log((1 - beta) / alpha), math.log(beta / (1 - alpha))
    drift = (mu1 - mu0) / sigma ** 2 * (true_mu - (mu0 + mu1) / 2)
    if abs(drift) < 1e-15:
        return float('inf')
    p_up = 1 - beta if true_mu >= mu1 else (alpha if true_mu <= mu0 else 0.5)
    return (p_up * a + (1 - p_up) * b) / drift


def msprt(xs, tau: float, sigma: float | None = None, theta0: float = 0.0, alpha: float = 0.05):
    """Mixture SPRT (normal mixing prior N(theta0, tau^2)); anytime-valid p = min(1, 1/max Lambda).

    Peeking every trade is allowed.  Returns {Lambda, p_always_valid, decision, n}.
    """
    xs = list(xs)
    n = len(xs)
    if sigma is None:
        sigma = _std(xs) if n > 1 else float('nan')
    if n == 0 or not sigma or sigma != sigma:
        return {'Lambda': 1.0, 'p_always_valid': 1.0, 'decision': 'continue', 'n': n}
    s2, t2 = sigma ** 2, tau ** 2
    best, run = 1.0, 0.0
    for i, x in enumerate(xs, 1):
        run += x - theta0
        xbar = run / i
        lam = math.sqrt(s2 / (s2 + i * t2)) * math.exp(min(700.0, i * i * t2 * xbar ** 2 / (2 * s2 * (s2 + i * t2))))
        best = max(best, lam)
    p = min(1.0, 1.0 / best)
    mean = run / n
    dec = 'continue' if p > alpha else ('positive' if mean > theta0 else 'negative')
    return {'Lambda': best, 'p_always_valid': p, 'decision': dec, 'n': n}


# ------------------------------------------------------------------ paired A/B
def paired_diff(a, b, n_boot: int = 2000, block: int = 1, seed: int = 7):
    """b - a per paired event.  -> mean, se, t, ci95 (bootstrap, circular blocks), rho, efficiency."""
    if len(a) != len(b) or len(a) < 2:
        raise ValueError('need equal-length samples, n >= 2')
    d = [y - x for x, y in zip(a, b)]
    n = len(d)
    m, sd = _mean(d), _std(d)
    se = sd / math.sqrt(n) if sd == sd else float('nan')
    sa, sb = _std(a), _std(b)
    ma, mb = _mean(a), _mean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (n - 1)
    rho = cov / (sa * sb) if sa and sb else float('nan')
    rng = random.Random(seed)
    block = max(1, int(block))
    boots = []
    for _ in range(n_boot):
        acc, cnt = 0.0, 0
        while cnt < n:
            s = rng.randrange(n)
            for j in range(block):
                if cnt >= n:
                    break
                acc += d[(s + j) % n]
                cnt += 1
        boots.append(acc / n)
    boots.sort()
    lo, hi = boots[int(0.025 * n_boot)], boots[min(n_boot - 1, int(0.975 * n_boot))]
    # how many independent (unpaired) trades per arm the same precision would cost
    unpaired_var = sa ** 2 + sb ** 2
    eff = unpaired_var / sd ** 2 if sd else float('inf')
    return {'n': n, 'mean': m, 'se': se, 't': m / se if se else float('nan'), 'ci95': (lo, hi),
            'rho': rho, 'efficiency_vs_unpaired': eff}


# ------------------------------------------------------------------ Sharpe deflation
def psr(sr: float, n: int, skew: float = 0.0, kurt: float = 3.0, sr0: float = 0.0) -> float:
    """Probabilistic Sharpe Ratio: P(true SR > sr0) for a per-period SR measured over n periods."""
    if n < 2:
        return float('nan')
    den = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if den <= 0:
        return float('nan')
    return norm_cdf((sr - sr0) * math.sqrt(n - 1) / math.sqrt(den))


def expected_max_sr(sr_var: float, n_trials: int) -> float:
    """E[max SR] of n_trials unskilled variants with cross-trial SR variance sr_var (BLdP 2014)."""
    if n_trials < 2:
        return 0.0
    return math.sqrt(sr_var) * ((1 - EULER_GAMMA) * norm_ppf(1 - 1 / n_trials)
                                + EULER_GAMMA * norm_ppf(1 - 1 / (n_trials * math.e)))


def dsr(sr: float, n: int, skew: float, kurt: float, sr_var: float, n_trials: int) -> float:
    """Deflated Sharpe Ratio = PSR against the expected max SR of n_trials tried variants."""
    return psr(sr, n, skew, kurt, expected_max_sr(sr_var, n_trials))


# ------------------------------------------------------------------ ledger
def ledger_bps(rows, symbol: str | None = None, mode: str | None = None):
    """Net bps per closed trade (pnl / entry notional).  Skips rows without the needed fields."""
    out = []
    for r in rows:
        if symbol and r.get('symbol') != symbol:
            continue
        if mode and r.get('mode') != mode:
            continue
        try:
            notional = float(r['qty']) * float(r['entry'])
            pnl = float(r['pnl'])
        except (KeyError, TypeError, ValueError):
            continue
        if notional > 0:
            out.append(pnl / notional * 1e4)
    return out


def verdict(xs, good: float = 2.0, bad: float = -3.0, alpha: float = 0.05, beta: float = 0.05,
            sigma: float | None = None):
    """One-line triage of a stream of net bps per trade: SPRT(bad vs good) + mSPRT(vs 0) + sample size."""
    xs = list(xs)
    n = len(xs)
    m, sd, sk, ku = moments(xs) if n >= 2 else (float('nan'),) * 4
    sig = sigma if sigma else sd
    s = sprt(xs, bad, good, sig, alpha, beta)
    ms = msprt(xs, tau=max(1.0, good - bad), sigma=sig)
    need = required_n(good - bad, sig, alpha, 1 - beta) if sig and sig == sig else None
    exp_n = sprt_expected_n(bad, good, sig, alpha, beta) if sig and sig == sig else None
    return {'n': n, 'mean_bps': m, 'std_bps': sd, 'skew': sk, 'kurt': ku,
            'se_bps': sd / math.sqrt(n) if n >= 2 else float('nan'),
            'sprt': s, 'msprt': ms, 'fixed_n_needed': need, 'sprt_expected_n': exp_n,
            'decision': {'H1': 'GOOD (edge ~ %+.1f)' % good, 'H0': 'BAD (edge ~ %+.1f)' % bad}.get(
                s['decision'], 'UNDECIDED')}
