from collections import deque
from dataclasses import dataclass
import math
import random
from core.models import dec, quantize


@dataclass(frozen=True)
class SizeResult:
    quantity: str
    notional: float
    reason: str


class PositionSizer:
    """Three-state edge lifecycle; the interval, never the mean, grants position size.

    At n=20 the settled ledger has SE = 3.5 bps against a 15.7 bps spread, so a
    point estimate of the mean is indistinguishable from noise for anything
    smaller than a ~10 bps edge. Sizing off that mean is reading noise. So:

      n < min_samples ................. kelly() is None -> caller uses the fixed
                                        bootstrap fraction (small, uncalibrated)
      n >= min_samples, CI low > 0 .... kelly() returns fractional Kelly
      n >= min_samples, CI straddles 0  kelly() is None -> keep collecting at the
                                        bootstrap fraction; an undetermined edge
                                        is not permission to size up
      n >= stop_samples, CI high < 0 .. kelly() returns 0 -> proven negative,
                                        stop. This is the termination rule that
                                        keeps 'collect more data' from running
                                        forever.
    """

    def __init__(self, fraction=.25, min_samples=60, max_position=.15, risk_per_trade=.005,
                 stop_samples=120, ci_draws=2000, ci_seed=17):
        if not .10 <= fraction <= .25 or min_samples < 2 or not 0 < max_position <= .75 or not 0 < risk_per_trade < 1:
            raise ValueError('Invalid sizing configuration')
        if stop_samples < min_samples or ci_draws < 100:
            raise ValueError('Invalid edge-interval configuration')
        self.fraction, self.min_samples = fraction, min_samples
        self.max_position, self.risk_per_trade = max_position, risk_per_trade
        self.stop_samples, self.ci_draws, self.ci_seed = stop_samples, ci_draws, ci_seed
        self.outcomes = deque(maxlen=200)
        self._interval = None

    def record(self, net_pnl, notional=None):
        if not math.isfinite(net_pnl) or (notional is not None and
                                          (not math.isfinite(notional) or notional <= 0)):
            raise ValueError('Invalid PnL')
        # Returns make the estimator invariant to account size and position size.
        self.outcomes.append(net_pnl / notional if notional is not None else net_pnl)
        self._interval = None

    def edge_interval(self):
        """Seeded percentile bootstrap on the mean net return; cached per sample.

        Deterministic so the same ledger always yields the same permission, and
        cached so the per-cycle sizing path does not pay for 2000 resamples.
        """
        if self._interval is not None:
            return self._interval
        values = list(self.outcomes)
        if len(values) < 2:
            self._interval = (float('-inf'), float('inf'))
            return self._interval
        rng = random.Random(self.ci_seed)
        n = len(values)
        means = sorted(math.fsum(rng.choices(values, k=n)) / n for _ in range(self.ci_draws))
        self._interval = (means[int(.025 * self.ci_draws)], means[int(.975 * self.ci_draws) - 1])
        return self._interval

    def edge_verdict(self):
        """'WARMUP' | 'UNDETERMINED' | 'POSITIVE' | 'NEGATIVE' -- the sizing state."""
        if len(self.outcomes) < self.min_samples:
            return 'WARMUP'
        low, high = self.edge_interval()
        if low > 0:
            return 'POSITIVE'
        if high < 0 and len(self.outcomes) >= self.stop_samples:
            return 'NEGATIVE'
        return 'UNDETERMINED'

    def tail_statistics(self):
        values = list(self.outcomes)
        if len(values) < 2:
            return {'skewness': 0.0, 'excess_kurtosis': 0.0}
        mean = sum(values) / len(values)
        centered = [x - mean for x in values]
        variance = sum(x*x for x in centered) / len(values)
        if variance <= 1e-24:
            return {'skewness': 0.0, 'excess_kurtosis': 0.0}
        m3 = sum(x**3 for x in centered) / len(values)
        m4 = sum(x**4 for x in centered) / len(values)
        sigma = math.sqrt(variance)
        return {'skewness': m3 / sigma**3, 'excess_kurtosis': m4 / variance**2 - 3.0}

    def kelly(self):
        verdict = self.edge_verdict()
        if verdict in ('WARMUP', 'UNDETERMINED'):
            # None means "not calibrated": the caller may still use the fixed
            # bootstrap fraction. It never means "edge is zero".
            return None
        if verdict == 'NEGATIVE':
            # Proven negative over stop_samples. Zero is a stop, not a warm-up.
            return 0.0
        wins, losses = [x for x in self.outcomes if x > 0], [-x for x in self.outcomes if x < 0]
        if not wins:
            return 0.0
        if not losses:
            return self.max_position
        p = len(wins)/len(self.outcomes)
        b = (sum(wins)/len(wins))/(sum(losses)/len(losses))
        legacy = max(0, self.fraction*(p*b-(1-p))/b)
        tail = self.tail_statistics()
        # Penalize only a genuinely dangerous tail: negative skew together with
        # positive excess kurtosis. A thin, symmetric/binary distribution keeps
        # the legacy estimate so this adjustment cannot punish ordinary samples.
        tail_risk = (max(0., -tail['skewness']) + max(0., tail['excess_kurtosis'])
                     if tail['skewness'] < 0 and tail['excess_kurtosis'] > 0 else 0.)
        adjusted = legacy / (1 + tail_risk)
        return min(self.max_position, max(0, adjusted))

    def size(self, equity, used_margin, price, stop, filters, bootstrap=False, available=None, roundtrip_fee=.0007, bootstrap_fraction=.01):
        if any(not math.isfinite(v) for v in (equity, used_margin, price, stop, roundtrip_fee, bootstrap_fraction)) or equity <= 0 or min(price, stop) <= 0 or used_margin < 0 or (available is not None and (not math.isfinite(available) or available < 0)) or not 0 < bootstrap_fraction <= .25:
            return SizeResult('0', 0, 'INVALID_CAPITAL')
        kelly = self.kelly()
        if kelly is None and not bootstrap:
            return SizeResult('0', 0, 'KELLY_WARMUP')
        if kelly is not None and kelly <= 0:
            return SizeResult('0', 0, 'NONPOSITIVE_EDGE')
        minimum = filters.min_notional*dec('1.10')
        raw_target = dec(equity*(kelly if kelly is not None else bootstrap_fraction))
        # Never manufacture edge by rounding a sub-minimum Kelly allocation upward.
        if kelly is not None and raw_target < minimum:
            return SizeResult('0', 0, 'KELLY_BELOW_MIN_NOTIONAL')
        target = max(minimum, raw_target)
        # Use unlevered capacity even when venue account leverage is larger.
        capacity = min(equity*self.max_position, max(0, equity*.75-used_margin),
                       available if available is not None else equity,
                       equity*self.risk_per_trade / max(abs(price-stop)/price+roundtrip_fee, 1e-9))
        qty = max(filters.min_qty, quantize(target/dec(price), filters.step, up=True))
        notional = qty*dec(price)
        if qty > filters.max_qty or notional > dec(capacity) or notional < minimum:
            return SizeResult('0', 0, 'MIN_NOTIONAL_EXCEEDS_RISK_CAP')
        return SizeResult(format(qty, 'f'), float(notional), 'OK')


class MicroCapitalSizer:
    """11-09 micro-capital rules for a wallet of ~5 USDT (pure: no I/O, no clock, no exchange call).

    The venue refuses an entry whose price x qty is under its MIN_NOTIONAL (5 USDT on DOGE / SOL, 100 on BTC; -4164), so
    on a small wallet the exchange minimum, not Kelly, sets the size: the smallest lot on the step grid whose notional is
    >= max(notional_min, 1.02 x venue minimum) and <= notional_max, or no trade at all.  A lot too coarse for the window
    (BTC, or a SOL lot of 0.1) is refused with the reason, never rounded into a rejected order.  Kelly / conviction /
    boost sizes are meant for hundreds of USDT and are not consulted while the mode is active."""

    def __init__(self, equity_max=15.0, notional_min=5.05, notional_max=6.0, max_loss_usdt=0.05, daily_kill_usdt=0.15,
                 loss_pause_s=86_400.0, max_consec_losses=3, max_positions=1, symbols=('DOGEUSDT',),
                 min_notional_buffer=1.02, margin_frac=0.85, growth_frac=0.20, accel_k=0.0):
        vals = (equity_max, notional_min, notional_max, max_loss_usdt, daily_kill_usdt, loss_pause_s, min_notional_buffer,
                margin_frac, growth_frac)
        if not all(math.isfinite(v) and v > 0 for v in vals) or notional_min > notional_max or max_positions < 1 \
                or max_consec_losses < 1 or not symbols or margin_frac > 1 or growth_frac > 1 \
                or not (math.isfinite(accel_k) and accel_k >= 0):
            raise ValueError('Invalid micro-capital parameters')
        self.equity_max, self.notional_min, self.notional_max = equity_max, notional_min, notional_max
        self.max_loss_usdt, self.daily_kill_usdt, self.loss_pause_s = max_loss_usdt, daily_kill_usdt, loss_pause_s
        self.max_consec_losses, self.max_positions = int(max_consec_losses), int(max_positions)
        self.symbols, self.min_notional_buffer = tuple(symbols), min_notional_buffer
        self.margin_frac, self.growth_frac, self.accel_k = margin_frac, growth_frac, float(accel_k)

    def active(self, wallet, mode='auto') -> bool:
        """'on' / 'off' force it; 'auto' = a known, positive wallet <= equity_max (an unknown wallet is not a small one)."""
        if mode == 'off':
            return False
        if mode == 'on':
            return True
        if mode != 'auto':
            raise ValueError('micro-capital mode must be auto, on or off')
        return wallet is not None and math.isfinite(wallet) and 0 < wallet <= self.equity_max

    def tradable(self, symbol) -> bool:
        return symbol in self.symbols

    def floor_notional(self, min_notional) -> float:
        return max(self.notional_min, float(min_notional) * self.min_notional_buffer)

    def compound_target(self, equity, leverage=5.0, pnl=None):
        """11-09 compounding target notional = min(margin_frac, growth_frac) x equity x leverage (None = equity unknown).

        The directive's max(5.10, min(0.85 x E x L, 0.25 x wallet_cap)) cannot scale as written (wallet_cap is
        undefined; a constant cap gives a constant, buying power as the cap gives 6.25 U at E = 5, above the 6.0 U window).
        margin_frac is the share of equity that may ever be margin (0.85); growth_frac (0.20) is the share of BUYING POWER
        the position tracks, so E = 5 -> 5.0 (under the venue floor: the smallest lot), E = 10 -> 10, E = 15 -> 15.

        11-09f accelerator (accel_k > 0 and a known realized `pnl`, USDT): target = base x max(0, 1 + accel_k x pnl), base = the
        plain target above (the pasted `notional_base` is undefined; this is the only reading that needs no extra input),
        capped by the margin share of ONE of max_positions slots (margin_frac x E x L / max_positions).  A realized loss
        shrinks it (factor < 1, to 0 at pnl = -1 / accel_k); a gain grows it faster than equity alone.  pnl None = off."""
        if equity is None or not (math.isfinite(equity) and equity > 0 and math.isfinite(leverage) and leverage > 0):
            return None
        plain = min(self.margin_frac, self.growth_frac) * equity * leverage
        if self.accel_k > 0 and pnl is not None and math.isfinite(pnl):
            share = self.margin_frac * equity * leverage / self.max_positions
            return min(plain * max(0.0, 1.0 + self.accel_k * pnl), max(plain, share))
        return plain

    def window_hi(self, equity=None, leverage=5.0, pnl=None):
        """Largest entry notional allowed now: the static cap, or the compounding target once it is higher."""
        t = self.compound_target(equity, leverage, pnl)
        return self.notional_max if t is None else max(self.notional_max, t)

    def risk_notional(self, sl_pct, fees_pct):
        """Largest notional whose loss at the stop (stop distance + fees, percent) stays inside max_loss_usdt."""
        d = sl_pct + fees_pct
        return self.max_loss_usdt / (d / 100.0) if d > 0 else None

    def qty(self, price, step, min_qty, min_notional, equity=None, leverage=5.0, risk_notional=None, pnl=None):
        """-> (qty, '') or (None, why).  Without a known equity above the venue floor: the smallest lot inside the
        notional window.  With one: the largest lot <= compound_target, never over window_hi, risk_notional (the loss
        ceiling) or below the venue floor."""
        if not (math.isfinite(price) and price > 0 and math.isfinite(step) and step > 0):
            return None, 'MICRO: no price / lot step'
        floor = self.floor_notional(min_notional)
        if floor > self.notional_max + 1e-9:
            return None, (f'MICRO: venue minimum notional {min_notional:g} U needs {floor:.2f} U > cap '
                          f'{self.notional_max:g} U')
        hi = self.window_hi(equity, leverage, pnl)
        if risk_notional is not None:
            hi = min(hi, risk_notional)
        if floor > hi + 1e-9:
            return None, f'MICRO: loss ceiling {hi:.2f} U is under the venue floor {floor:.2f} U'
        q = max(float(min_qty), round(math.ceil(round(floor / price / step, 9)) * step, 12))
        target = self.compound_target(equity, leverage, pnl)
        if target is not None and min(target, hi) > floor:
            q = max(q, round(math.floor(round(min(target, hi) / price / step, 9)) * step, 12))
        notional = q * price
        if notional > hi + 1e-9:
            return None, (f'MICRO: smallest lot {q:g} = {notional:.2f} U > cap {hi:g} U '
                          f'(lot step {step:g} is too coarse for a {self.notional_min:g}-{hi:g} U order)')
        return q, ''

    def loss_at_stop(self, notional, sl_pct, fees_pct) -> float:
        """Money lost when the stop is hit: notional x (stop distance + entry / exit fees and slip), both in percent."""
        return notional * (sl_pct + fees_pct) / 100.0

    def halt_until(self, rows, now=None, lookback_s=None):
        """Wall time until which entries stay halted by consecutive losses, else None.

        Same counter as the engine: AUTO-tag settled losses in a row since the last winner (other tags neither add nor
        reset); at max_consec_losses the halt runs loss_pause_s from the LAST loss.  Derived from the rows each time,
        so a restart cannot erase it.  A row without 't' cannot date the halt and is ignored (UNKNOWN, not zero)."""
        count, last_t = 0, None
        for r in reversed(list(rows)):
            pnl = r.get('pnl')
            if pnl is None:
                continue
            if now is not None and lookback_s and r.get('t') is not None and float(r['t']) < now - lookback_s:
                break                      # older than the window: not part of this streak (a 31 h old loss halted a fresh run)
            if float(pnl) > 0:
                break
            if r.get('tag') == 'AUTO' and float(pnl) < 0:
                if r.get('t') is None:
                    continue
                count += 1
                last_t = float(r['t']) if last_t is None else last_t
        if count < self.max_consec_losses or last_t is None:
            return None
        until = last_t + self.loss_pause_s
        return until if now is None or now < until else None

    def view(self, wallet, mode='auto') -> dict:
        return {'mode': mode, 'active': self.active(wallet, mode), 'wallet': wallet, 'equity_max': self.equity_max,
                'symbols': list(self.symbols), 'notional': [self.notional_min, self.notional_max],
                'max_loss_usdt': self.max_loss_usdt, 'daily_kill_usdt': self.daily_kill_usdt,
                'max_positions': self.max_positions, 'loss_pause_s': self.loss_pause_s,
                'margin_frac': self.margin_frac, 'growth_frac': self.growth_frac, 'accel_k': self.accel_k}


class DrawdownBrake:
    def __init__(self, max_drawdown=.08, max_losses=10):
        self.max_drawdown, self.max_losses = max_drawdown, max_losses
        self.peak = 0.0
        self.losses = 0
        self.halted = False

    def update(self, equity, pnl=None):
        if not math.isfinite(equity) or equity <= 0:
            self.halted = True
            return True
        self.peak = max(self.peak, equity)
        if pnl is not None:
            self.losses = self.losses+1 if pnl < 0 else 0
        self.halted = self.halted or (1-equity/self.peak >= self.max_drawdown) or self.losses >= self.max_losses
        return self.halted
