"""Run the live swarm inside hftbacktest, with queue position and latency modelled.

This is the tier the system was missing. `simulation/paper_engine.py` answers
"if my order filled, what would happen"; it cannot answer "does my order fill at
all", because it has no queue model and no latency. That second question decides
whether a post-only maker strategy is viable, so it cannot stay unanswered.

The same `AlphaEngine`, `GroktopusSwarm`, `BarrierPlanner` and `PositionSizer`
objects that run live are driven here -- no reimplementation, no second copy of
the strategy to drift out of sync. Probes are recorded through the same
`SignalLab`, writing the same JSONL schema, so `tools/calibrate_edge.py` reads
backtest and live output identically and the two can be compared directly.
"""
from dataclasses import dataclass, field
import math

from hftbacktest import BUY_EVENT, FILLED, EXPIRED, CANCELED, GTC, GTX, LIMIT, MARKET

from core.barrier_planner import BarrierPlanner
from core.groktopus_swarm import GroktopusSwarm
from core.models import Filters, dec
from core.position_sizer import DrawdownBrake, PositionSizer
from core.real_alpha_engine import AlphaEngine, calibrate_bucket_volume
from core.runtime import DIRECTIONAL, VPIN_MIN_BUCKETS, directional_score
from core.signal_lab import SignalLab
from research.book_adapter import MinuteBars, build_book

NS = 1_000_000_000


@dataclass
class BacktestConfig:
    symbol: str = 'BTCUSDT'
    tick_size: float = 0.10
    lot_size: float = 0.001
    min_qty: float = 0.001
    max_qty: float = 1000.0
    min_notional: float = 100.0
    equity: float = 4930.88
    horizon: float = 300.0
    quote_ttl: float = 10.0
    maker_fee: float = 0.0002
    taker_fee: float = 0.0005
    interval_ms: int = 500          # the live cycle is 0.5 s; keep them identical
    bootstrap: bool = True
    lab_path: str = None
    edge_multiple: float = 1.8
    toxic_vpin: float = 0.65
    # Live `initialize()` sizes the VPIN bucket from 1000 aggTrades before trading.
    # The backtest must do the same or it measures a different feature than production.
    warmup_trades: int = 1000
    last_trades_capacity: int = 10_000

    def filters(self):
        return Filters(dec(self.tick_size), dec(self.lot_size), dec(self.min_qty),
                       dec(self.max_qty), dec(self.min_notional))


@dataclass
class BacktestReport:
    ticks: int = 0
    books: int = 0
    signals: int = 0
    authorized: int = 0
    entries_submitted: int = 0
    entries_filled: int = 0
    entries_expired: int = 0
    entries_cancelled: int = 0
    maker_fills: int = 0
    taker_fills: int = 0
    exits: int = 0
    barrier_tp: int = 0
    barrier_sl: int = 0
    barrier_timeout: int = 0
    position: float = 0.0
    balance: float = 0.0
    fee: float = 0.0
    trading_value: float = 0.0
    num_trades: int = 0
    bucket_volume: float = 0.0
    warmup_trades_seen: int = 0
    lab: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    @property
    def fill_rate(self):
        """The number a paper engine without a queue model cannot produce."""
        total = self.entries_filled + self.entries_expired + self.entries_cancelled
        return self.entries_filled / total if total else None

    @property
    def realized_fee_bps(self):
        return self.fee / self.trading_value * 10000 if self.trading_value else None


class SwarmBacktest:
    """Drives the production strategy objects against a simulated venue."""

    def __init__(self, config=None):
        self.config = config or BacktestConfig()
        self.alpha = AlphaEngine(min_buckets=VPIN_MIN_BUCKETS)
        self.swarm = GroktopusSwarm()
        self.planner = BarrierPlanner(horizon=self.config.horizon)
        self.sizer = PositionSizer()
        self.brake = DrawdownBrake()
        self.bars = MinuteBars()
        self.lab = SignalLab(self.config.lab_path, horizon=self.config.horizon)
        self.report = BacktestReport()
        self._filters = self.config.filters()
        self._sequence = 0
        self._trade_id = 0
        self._order_id = 0
        self._entry = None          # (order_id, side, submitted_at)
        self._holding = None        # (side, entry_price, barriers, opened_at)
        self._last_now = 0.0
        self._warmup = []
        self._calibrated = False

    # ------------------------------------------------------------------ feed
    def _ingest(self, hbt, book, now):
        self.alpha.on_book(book)
        trades = hbt.last_trades(0)
        for row in trades:
            price, quantity = float(row['px']), float(row['qty'])
            if price <= 0 or quantity <= 0:
                continue
            if self._calibrated:
                self._trade_id += 1
                self.alpha.on_trade(price, quantity, bool(row['ev'] & BUY_EVENT),
                                    now, self._trade_id)
            else:
                self._collect(price, quantity, bool(row['ev'] & BUY_EVENT), now)
        hbt.clear_last_trades(0)
        closed = self.bars.update(now, book.mid)
        if closed:
            timestamp, high, low, close = closed
            try:
                self.alpha.on_bar(timestamp, high, low, close)
            except ValueError:
                pass

    def _collect(self, price, quantity, aggressor, now):
        """Mirror the live warm-up: size the VPIN bucket first, then replay the tape.

        Feeding trades before calibration would fill buckets at the default volume
        and reproduce exactly the saturation artefact the live engine was fixed for.
        """
        self._warmup.append((price, quantity, aggressor, now))
        if len(self._warmup) < self.config.warmup_trades:
            return
        self.alpha.buckets.volume = calibrate_bucket_volume(
            [{'q': str(q), 'T': t * 1000} for _, q, _, t in self._warmup])
        self.report.bucket_volume = self.alpha.buckets.volume
        self.report.warmup_trades_seen = len(self._warmup)
        for replay_price, replay_qty, replay_side, replay_now in self._warmup:
            self._trade_id += 1
            self.alpha.on_trade(replay_price, replay_qty, replay_side, replay_now, self._trade_id)
        self._warmup.clear()
        self._calibrated = True

    # ----------------------------------------------------------------- orders
    def _live_order(self, hbt):
        if self._entry is None:
            return None
        return hbt.orders(0).get(self._entry[0])

    def _settle_entry(self, order):
        """Classify a finished entry. A resting fill keeps its own price tick."""
        if order.status == FILLED:
            self.report.entries_filled += 1
            if order.exec_price_tick == order.price_tick:
                self.report.maker_fills += 1
            else:
                # A post-only entry that executed by crossing is a modelling error,
                # and it silently costs 3 bps on every trade.
                self.report.taker_fills += 1
            return 'FILLED'
        if order.status == EXPIRED:
            self.report.entries_expired += 1
            return 'EXPIRED'
        if order.status == CANCELED:
            self.report.entries_cancelled += 1
            return 'CANCELED'
        return None

    def _submit_entry(self, hbt, book, side, quantity):
        self._order_id += 1
        price = book.bid if side == 'BUY' else book.ask
        submit = hbt.submit_buy_order if side == 'BUY' else hbt.submit_sell_order
        # GTX + LIMIT: post-only. Passing a side constant as order_type silently
        # produces a MARKET order, because BUY and MARKET are both 1.
        submit(0, self._order_id, price, quantity, GTX, LIMIT, False)
        self._entry = (self._order_id, side, self._last_now)
        self.report.entries_submitted += 1
        return price

    def _flatten(self, hbt, book, position):
        self._order_id += 1
        side = 'SELL' if position > 0 else 'BUY'
        submit = hbt.submit_sell_order if position > 0 else hbt.submit_buy_order
        price = book.bid if position > 0 else book.ask
        submit(0, self._order_id, price, abs(position), GTC, MARKET, False)
        self.report.exits += 1
        self._holding = None

    # -------------------------------------------------------------------- run
    def run(self, hbt):
        config = self.config
        interval = config.interval_ms * 1_000_000
        equity = config.equity
        while hbt.elapse(interval) == 0:
            self.report.ticks += 1
            now = hbt.current_timestamp / NS
            self._last_now = now
            depth = hbt.depth(0)
            self._sequence += 1
            book = build_book(config.symbol, depth, now, self._sequence)
            if book is None:
                continue
            self.report.books += 1
            self._ingest(hbt, book, now)

            state = hbt.state_values(0)
            position = hbt.position(0)
            used_notional = abs(position) * book.mid
            equity = config.equity + state.balance + position * book.mid - state.fee
            self.brake.update(max(equity, 1e-9))

            features = self.alpha.features()
            decision, votes = self.swarm.evaluate(
                features, book.mid,
                capacity_ok=used_notional < equity * .75 and equity > 0,
                risk_ok=not self.brake.halted,
                gateway_ready=True, exits_ready=True, cross_momentum=0.0,
                maker_fee=config.maker_fee, taker_fee=config.taker_fee,
                toxic_vpin=config.toxic_vpin, edge_multiple=config.edge_multiple)
            if features.ready:
                self.report.signals += 1
            if decision.allowed:
                self.report.authorized += 1

            # Counterfactual probes: every signal, gated or not, same as live.
            self.lab.resolve(config.symbol, book, now)
            self._observe(book, decision, votes, features, now)

            order = self._live_order(hbt)
            if order is not None:
                outcome = self._settle_entry(order)
                if outcome == 'FILLED':
                    entry = order.exec_price_tick * config.tick_size
                    side = self._entry[1]
                    plan = self._plan(side, entry, features.atr, now)
                    if plan:
                        self._holding = (side, entry, plan, now)
                    self._entry = None
                    hbt.clear_inactive_orders(0)
                elif outcome is not None:
                    self._entry = None
                    hbt.clear_inactive_orders(0)
                elif now - self._entry[2] > config.quote_ttl:
                    hbt.cancel(0, self._entry[0], False)

            if self._holding and position != 0:
                self._manage(hbt, book, position, now)
            elif position != 0 and self._holding is None:
                self._flatten(hbt, book, position)
            elif position == 0 and self._entry is None and decision.allowed:
                self._enter(hbt, book, decision, features, equity, used_notional, now)

        state = hbt.state_values(0)
        self.report.position = hbt.position(0)
        self.report.balance = state.balance
        self.report.fee = state.fee
        self.report.trading_value = state.trading_value
        self.report.num_trades = state.num_trades
        self.report.lab = self.lab.summary()
        self._audit()
        return self.report

    # ------------------------------------------------------------- internals
    def _plan(self, side, entry, atr, now):
        try:
            return self.planner.plan(side, entry, atr, self.config.tick_size, now)
        except ValueError:
            return None

    def _observe(self, book, decision, votes, features, now):
        price = book.bid if decision.side == 'BUY' else book.ask
        plan = self._plan(decision.side, price, features.atr, now)
        if plan is None:
            return
        self.lab.observe(self.config.symbol, features, book, decision, votes, plan, now,
                         directional_score(votes))

    def _enter(self, hbt, book, decision, features, equity, used_notional, now):
        price = book.bid if decision.side == 'BUY' else book.ask
        plan = self._plan(decision.side, price, features.atr, now)
        if plan is None:
            return
        size = self.sizer.size(equity, used_notional, price, plan.stop, self._filters,
                               bootstrap=self.config.bootstrap, available=equity,
                               roundtrip_fee=self.config.maker_fee + self.config.taker_fee)
        if size.quantity == '0':
            return
        self._submit_entry(hbt, book, decision.side, float(size.quantity))

    def _manage(self, hbt, book, position, now):
        side, entry, plan, opened = self._holding
        sign = 1 if side == 'BUY' else -1
        mark = book.bid if sign > 0 else book.ask
        updated = self.planner.update(side, entry, book.mid, self.config.tick_size,
                                      opened, now, plan, plan.stop)
        self._holding = (side, entry, updated, opened)
        if sign * (mark - updated.stop) <= 0:
            self.report.barrier_sl += 1
        elif sign * (mark - updated.take_profit) >= 0:
            self.report.barrier_tp += 1
        elif now >= updated.deadline:
            self.report.barrier_timeout += 1
        else:
            return
        self._flatten(hbt, book, position)

    def _audit(self):
        """Refuse to hand back numbers the setup cannot support."""
        report = self.report
        if report.taker_fills:
            report.warnings.append(
                f'{report.taker_fills} post-only entries executed by crossing; '
                'the maker assumption is broken and costs are understated')
        if report.books == 0:
            report.warnings.append('no usable book was built; check the data feed')
        elif not self._calibrated:
            report.warnings.append(
                f'VPIN bucket never calibrated: only {len(self._warmup)} of '
                f'{self.config.warmup_trades} warm-up trades seen. Use a longer feed.')
        elif report.signals == 0:
            report.warnings.append(
                'features never reached ready: needs 20 one-minute bars plus 10 VPIN '
                'buckets, so a useful run is hours, not minutes')
        if report.entries_submitted and report.fill_rate is not None and report.fill_rate < .05:
            report.warnings.append(
                f'fill rate {report.fill_rate:.1%}: post-only entries are rarely filled, '
                'so realized edge will be far below the probe estimate')
        if report.lab.get('probes', 0) < 200:
            report.warnings.append(
                f"only {report.lab.get('probes', 0)} probes resolved; below 200 the "
                'calibration is provisional')
