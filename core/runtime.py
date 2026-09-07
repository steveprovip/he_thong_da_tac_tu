"""Concurrent market ingestion with one serialized portfolio/execution decision loop."""
import argparse
import asyncio
from dataclasses import asdict, replace
import json
import math
from pathlib import Path
import random
import signal
import threading
import time
from collections import Counter

from config.settings import ROOT, Settings
from core.barrier_planner import BarrierPlanner, Barriers, maker_quote
from core.consensus_engine import TokenAuthority, WEIGHTS
from core.funding_carry import CarryConfig, FundingCarry
from core.groktopus_swarm import GroktopusSwarm, adverse_selection
from core.models import Book, Filters, Intent, dec, quantize
from core.position_sizer import PositionSizer, DrawdownBrake
from core.outcome_ledger import OutcomeLedger
from core.real_alpha_engine import (AlphaEngine, calibrate_bucket_volume,
                                    cross_sectional_momentum, lead_lag_signal)
from core.signal_lab import SignalLab
from core.web_ui_server import Telemetry, start_server
from live.binance_futures_client import BinanceFuturesClient, BinanceError
from live.execution_gateway import ExecutionGateway, Journal
from live.flatten import flatten_venue
from live.futures_streams import PartialBookTracker, consume_stream, stream_url, user_stream
from simulation.paper_engine import PaperEngine

SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'ADAUSDT', 'LINKUSDT']
DIRECTIONAL = ('NORO', 'LUMEN', 'TIDAL')
# Calibrated buckets fill slower, so report VPIN from a partial window once it is meaningful.
VPIN_MIN_BUCKETS = 10


def directional_score(votes):
    """Conviction of the three alpha agents alone, with the gate weight stripped out.

    The headline consensus adds 0.40 of gate weight to every authorized trade, so a
    0.768 headline only implies 0.613 mean directional conviction. This is the number
    to read when asking whether the swarm actually had an opinion."""
    weights = {v.agent: WEIGHTS[v.agent] for v in votes if v.agent in DIRECTIONAL}
    total = math.fsum(weights.values())
    if not total:
        return 0.0
    sides = {side: math.fsum(weights[v.agent]*v.confidence for v in votes
                             if v.agent in weights and v.side == side) for side in ('BUY', 'SELL')}
    return max(sides.values())/total


def console_stop_requested(sigint_event=None, keyboard=None):
    """True when the operator asked for an immediate safe stop from the terminal.

    Two channels: Ctrl+C (the SIGINT handler sets a threading.Event) and the
    'Q' hotkey read non-blockingly from the console. Any other keystroke is
    drained and ignored so stray typing cannot stop the system.
    """
    if sigint_event is not None and sigint_event.is_set():
        return True
    if keyboard is not None:
        return keyboard() in ('q', 'Q')
    try:
        import msvcrt
    except ImportError:
        return False
    try:
        while msvcrt.kbhit():
            if msvcrt.getwch() in ('q', 'Q'):
                return True
    except OSError:
        return False
    return False


def atr_bps_value(atr, mid):
    """ATR expressed in basis points of price; None when inputs are unusable."""
    try:
        atr, mid = float(atr), float(mid)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(atr) or not math.isfinite(mid) or mid <= 0:
        return None
    return atr/mid*1e4


def volatility_block(atr_bps, floor=0.0, ceiling=0.0):
    """Entry volatility band: 'LOW_VOLATILITY', 'HIGH_VOLATILITY' or '' to allow.

    The floor skips quiet-hour entries whose expected move cannot pay the
    roundtrip. The ceiling drops the measured-negative tail: across 5178 recorded
    lab probes, ATR >= 20 bps returned -4.03 bps gross (95% CI [-7.44, -0.66])
    against +0.84 bps for the whole sample.

    An unmeasurable ATR fails whichever side is configured -- not knowing the
    expected move is not evidence that the move is tradeable. Either bound at 0
    disables that side of the band.
    """
    if floor > 0 and (atr_bps is None or atr_bps < floor):
        return 'LOW_VOLATILITY'
    if ceiling > 0 and (atr_bps is None or atr_bps > ceiling):
        return 'HIGH_VOLATILITY'
    return ''


def exit_reason(side, mark, stop, take_profit, now, deadline):
    """Which triple-barrier leg fired first: 'STOP', 'TP', 'DEADLINE' or ''."""
    sign = 1 if side == 'BUY' else -1
    if sign*(mark-stop) <= 0:
        return 'STOP'
    if sign*(mark-take_profit) >= 0:
        return 'TP'
    if now >= deadline:
        return 'DEADLINE'
    return ''


class Runtime:
    def __init__(self, settings, args, telemetry):
        self.settings, self.args, self.telemetry = settings, args, telemetry
        self.symbols = args.symbols
        self.stop = asyncio.Event()
        self.alpha = {s: AlphaEngine(min_buckets=VPIN_MIN_BUCKETS) for s in self.symbols}
        # Two-tier policy (explicit owner decision): the demo venue trades
        # actively so the pipeline can be observed end-to-end; mainnet keeps the
        # survival gates. CLI flags always override either tier.
        self.simulated = settings.mode == 'testnet'
        tier = (dict(edge=0.6, vpin=0.65, threshold=0.55, veto=0.45) if self.simulated
                else dict(edge=1.8, vpin=0.65, threshold=0.768, veto=0.70))
        # Bootstrap changes sample-size handling only; it never weakens risk gates.
        self.simulated_flow = settings.mode == 'testnet' and bool(getattr(args, 'bootstrap', False))
        self._vpin_override = getattr(args, 'toxic_vpin', None)
        self.vpin_spike = 0.12
        self.edge_multiple = args.edge_multiple if getattr(args, 'edge_multiple', None) is not None else tier['edge']
        self.toxic_vpin = args.toxic_vpin if self._vpin_override is not None else tier['vpin']
        self.threshold = args.threshold if getattr(args, 'threshold', None) is not None else tier['threshold']
        self.veto_threshold = args.veto_threshold if getattr(args, 'veto_threshold', None) is not None else tier['veto']
        self.trend_floor = self.threshold
        self.bootstrap_fraction = getattr(args, 'bootstrap_fraction', None) or 0.01
        self.maker_exit_grace = float(getattr(args, 'maker_exit_grace', 8.0) or 0.0)
        self.min_atr_bps = float(getattr(args, 'min_atr_bps', 0.0) or 0.0)
        self.max_atr_bps = float(getattr(args, 'max_atr_bps', 0.0) or 0.0)
        self.min_conviction = float(getattr(args, 'min_conviction', 0.0) or 0.0)
        self.books, self.filters = {}, {}
        self.tracker = PartialBookTracker()
        self.swarm = GroktopusSwarm(threshold=self.threshold, veto_threshold=self.veto_threshold,
                                    trend_floor=self.trend_floor)
        # Authorization and adaptive quorum share one policy object; a token cannot
        # bypass a regime-specific threshold selected by the consensus engine.
        self.authority = TokenAuthority(threshold=self.threshold, policy=self.swarm.consensus)
        self.sizer = PositionSizer()
        self.brake = DrawdownBrake()
        self.planner = BarrierPlanner(horizon=args.horizon)
        self.paper = PaperEngine(args.equity) if settings.mode == 'paper' else None
        self.gateway = self.client = None
        self.wallet = self.equity = args.equity if self.paper else 0.0
        self.available = self.equity
        self.used_margin = 0.0
        self.initial_equity = None
        self.account_at = 0.0
        self.private_ready = False
        self.barriers = {}
        self.requote_counts = {}
        self.last_decision = {}
        self.last_votes = []
        self.cooldowns = {}
        self.session_volume = self.session_fees = self.session_pnl = 0.0
        self.authorized = 0
        self.auth_at = None
        self.order_votes = {}
        self.block_counts = Counter()
        self.cycles = 0
        source = f'paper-{getattr(args, "market_data", "testnet")}' if self.paper else settings.mode
        self.outcomes_path = ROOT/'data'/f'{source}-settled-v2.json'
        self.outcomes_path.parent.mkdir(exist_ok=True)
        self.ledger = OutcomeLedger(self.outcomes_path, source)
        self._mission_start = len(self.ledger.records)
        if not args.offline:
            for row in self.ledger.records:
                self.sizer.record(row['net_return'])
        if (ROOT/'data'/f'{settings.mode}-outcomes.json').exists():
            self.telemetry.event('Legacy Kelly floats excluded: units unverified; use audited settled-v2 ledger')
        # Pure observer: records what the gates refused so the gates can be calibrated.
        self.lab = None if args.no_lab else SignalLab(
            ROOT/'data'/f'{source}-signal-lab-v2.jsonl', horizon=args.horizon, source=source)
        # Counterfactual assumed-fill probes are descriptive evidence only.
        self.lab_warmup = None
        # Retain funding observations without manufacturing a consensus decision.
        self.carry = FundingCarry(CarryConfig(edge_multiple=self.edge_multiple))
        self.carry_enabled = False
        self.carry_scan_at = 0.0
        self.carry_tag = None

    def mission_summary(self):
        """End-of-session data-collection report: what was measured, what is missing.

        Lets a timed 15-minute run answer, in one file, whether the learning loop
        advanced: settled outcomes for Kelly, lab probes for the edge verdict."""
        lab = self.lab.summary() if self.lab else {}
        return {
            'mode': self.settings.mode,
            'planned_seconds': self.args.seconds,
            'mission_target': int(getattr(self.args, 'mission_target', 0) or 0),
            'collected_this_session': max(0, len(self.ledger.records) - self._mission_start),
            'settled_outcomes': len(self.ledger.records),
            'kelly_target': self.sizer.min_samples,
            'kelly_stop_target': self.sizer.stop_samples,
            'kelly_ready': self.sizer.kelly() is not None,
            'edge_verdict': self.sizer.edge_verdict(),
            'edge_ci_net_return': list(self.sizer.edge_interval()) if len(self.sizer.outcomes) >= 2 else None,
            'session_pnl_usdt': round(self.session_pnl, 6),
            'session_fees_usdt': round(self.session_fees, 6),
            'authorized_entries': self.authorized,
            'maker_exits': dict(self.gateway.maker_exit_stats) if self.gateway else {},
            'min_atr_bps': self.min_atr_bps,
            'max_atr_bps': self.max_atr_bps,
            'min_conviction': self.min_conviction,
            'lab': lab,
        }

    def vpin_gate(self, features):
        """Two-tier VPIN gate.

        Live: absolute threshold — saturation is never permission to bypass it.
        Simulated demo venue: the gate extends above the symbol's own rolling
        baseline, so venue-flow saturation (an artifact of synthetic wash bots,
        not real toxicity) is not a permanent veto, while a genuine spike
        (jump >= .15) still vetoes through adverse_selection.
        """
        if self.simulated and self._vpin_override is None:
            base = getattr(features, 'vpin_baseline', None)
            if base is not None and math.isfinite(base):
                return min(1.0, max(self.toxic_vpin, base + self.vpin_spike))
        return self.toxic_vpin

    def withhold_settlement(self, reason, symbol='', trade_id=''):
        """Journal a close that produced no ledger row, with the reason.

        Silence here is how five of twenty-three closed round trips went
        missing from the edge estimate: telemetry is ephemeral, so afterwards
        nothing could tell a complete ledger from a lossy one. The rows that go
        missing are the messy partial-fill ones, so the survivors are a biased
        sample and the mean reads optimistic. Journalling the reason makes the
        POSITION_CLOSED <-> ledger invariant checkable after the fact
        (tools/audit_execution.reconciliation_gaps).
        """
        self.telemetry.event(f'{symbol or "?"}: settlement withheld ({reason})')
        if self.gateway is not None:
            self.gateway.journal.write('SETTLE_WITHHELD',
                                       {'symbol': symbol, 'client_id': trade_id, 'reason': reason})

    def record_outcome(self, pnl, votes=None, side=None, notional=None, symbol='', trade_id=''):
        if notional is None or not math.isfinite(notional) or notional <= 0:
            self.withhold_settlement('missing entry notional', symbol, trade_id)
            return
        if not self.args.offline and not self.ledger.append(pnl, notional, time.time(), symbol, trade_id):
            return
        self.sizer.record(pnl, notional=notional)
        if votes and side:
            self.swarm.consensus.record_outcome(votes, side, pnl)
        self.brake.update(self.equity, pnl)

    async def disconnected(self, label):
        self.telemetry.event(f'{label} disconnected; entries paused, flow warm-up reset')
        if label == 'private':
            self.private_ready = False
        else:
            self.books.clear()
            self.tracker.reset()
            for engine in self.alpha.values():
                engine.reset_flow()
        if self.gateway:
            await self.gateway.cancel_entries()
        if self.paper:
            for symbol in list(self.paper.orders):
                self.paper.cancel(symbol)

    async def event(self, event):
        kind = event.get('e')
        if kind == '_CONNECTED':
            if event['category'] == 'private':
                await self.gateway.reconcile()
                self.private_ready = True
            return
        if kind in ('ORDER_TRADE_UPDATE', 'ACCOUNT_UPDATE', 'ALGO_UPDATE'):
            if self.gateway:
                await self.gateway.on_user_event(event)
            if kind == 'ACCOUNT_UPDATE':
                self.account_at = 0
            return
        symbol = event.get('s')
        if symbol not in self.alpha or event.get('st', 1) != 1:
            return
        alpha = self.alpha[symbol]
        now = time.time()+self.client.offset_ms/1000
        event_time = event.get('E', int(now*1000))/1000
        if now-event_time > 3 or event_time-now > 2:
            return
        if kind == 'depthUpdate':
            book, gap = self.tracker.parse(event)
            if book is None:
                return
            if gap:
                alpha.reset_flow()
                self.telemetry.event(f'{symbol}: depth sequence gap; flow warm-up restarted')
            self.books[symbol] = book
            alpha.on_book(book)
        elif kind == 'aggTrade':
            timestamp = event['T']/1000
            price, quantity, trade_id = float(event['p']), float(event['q']), int(event['a'])
            alpha.on_trade(price, quantity, not event['m'], timestamp, trade_id)
            if self.paper:
                self.paper.trade(symbol, price, quantity, not event['m'], timestamp, trade_id)
        elif kind == 'kline' and event['k']['x']:
            k = event['k']
            alpha.on_bar(k['T']/1000, float(k['h']), float(k['l']), float(k['c']))

    async def initialize(self):
        await self.client.sync_time()
        info = await self.client.exchange_info()
        contracts = {s['symbol']: s for s in info['symbols'] if s['status'] == 'TRADING' and s.get('quoteAsset') == 'USDT' and s.get('contractType') == 'PERPETUAL'}
        for symbol in self.symbols:
            if symbol not in contracts:
                raise ValueError(f'Unavailable USDT perpetual: {symbol}')
            self.filters[symbol] = Filters.from_exchange(contracts[symbol])
            # Use the venue tick, not the observed spread, for micro-price units.
            self.alpha[symbol].tick_size = float(self.filters[symbol].tick)
            candles = await self.client.request('GET', '/fapi/v1/klines', {'symbol': symbol, 'interval': '1m', 'limit': 120}, weight=2)
            for k in candles:
                if k[6] < time.time()*1000+self.client.offset_ms:
                    self.alpha[symbol].on_bar(k[6]/1000, float(k[2]), float(k[3]), float(k[4]))
            trades = await self.client.request('GET', '/fapi/v1/aggTrades', {'symbol': symbol, 'limit': 1000}, weight=20)
            self.alpha[symbol].buckets.volume = calibrate_bucket_volume(trades)
            for trade in trades:
                self.alpha[symbol].on_trade(float(trade['p']), float(trade['q']), not trade['m'], trade['T']/1000, trade['a'])
        if not self.paper and self.settings.api_key:
            await self.refresh_account()
        if self.gateway:
            mode = await self.client.request('GET', '/fapi/v1/positionSide/dual', signed=True, weight=30)
            if mode['dualSidePosition']:
                raise RuntimeError('One-way position mode required; change it manually while flat')
            # Do not silently take ownership of manual or previous-process exposure.
            for symbol in self.symbols:
                positions = await self.client.positions(symbol)
                if any(float(p['positionAmt']) != 0 for p in positions) or await self.client.open_orders(symbol) or await self.client.open_algos(symbol):
                    raise RuntimeError(f'{symbol}: existing exposure/orders; reconcile on venue before starting this session')
                fees = await self.client.request('GET', '/fapi/v1/commissionRate', {'symbol': symbol}, signed=True, weight=20)
                if float(fees['makerCommissionRate']) > .0002 or float(fees['takerCommissionRate']) > .0005:
                    raise RuntimeError(f'{symbol}: commission exceeds configured cost model')
        self.telemetry.event('Market warm-up loaded; waiting for fresh streams')

    async def refresh_account(self):
        account = await self.client.account()
        self.wallet = float(account['totalWalletBalance'])
        self.equity = float(account['totalMarginBalance'])
        self.available = float(account['availableBalance'])
        self.used_margin = float(account['totalInitialMargin'])
        self.account_at = time.monotonic()
        if self.initial_equity is None:
            self.initial_equity = self.equity
        return account

    def active(self):
        return self.paper.orders if self.paper else self.gateway.active if self.gateway else {}

    async def settle_exchange(self, closed):
        for symbol, tracked in closed.items():
            trades, complete = await self.client.history_since(symbol, int(tracked.opened*1000))
            relevant = [t for t in trades if t['time'] >= int(tracked.opened*1000)]
            income = await self.client.request('GET', '/fapi/v1/income', {'symbol': symbol,
                'incomeType': 'FUNDING_FEE', 'startTime': int(tracked.opened*1000), 'limit': 1000}, signed=True, weight=30)
            net = sum(float(t['realizedPnl'])-float(t['commission']) for t in relevant)
            net += sum(float(i['income']) for i in income if i.get('asset') == 'USDT')
            if not complete or any(t.get('commissionAsset') != 'USDT' for t in relevant):
                # Kelly update is withheld (fail-closed accounting), but trading
                # continues: a truncated history page must not halt the venue.
                self.withhold_settlement('incomplete history or non-USDT fee', symbol, tracked.client_id)
                continue
            if not relevant:
                self.withhold_settlement('no fills in settlement window', symbol, tracked.client_id)
            if relevant:
                vote_record = self.order_votes.pop(symbol, None)
                notional = sum(abs(float(t['qty']) * float(t['price'])) for t in relevant if t['side'] == tracked.intent.side)
                self.record_outcome(net, *(vote_record or (None, None)), notional=notional or None,
                                    symbol=symbol, trade_id=tracked.client_id)
                self.session_pnl += net
                self.session_volume += sum(float(t['qty'])*float(t['price']) for t in relevant)
                self.session_fees += sum(float(t['commission']) for t in relevant)
                self.telemetry.event(f'{symbol}: closed net {net:+.4f} USDT')

    async def manage_positions(self, now, features=None):
        features = features or {s: a.features() for s, a in self.alpha.items()}
        if not self.paper and self.gateway is not None and self.gateway.maker_exits:
            await self.gateway.sweep_maker_exits(now, books=self.books)
        for symbol, order in list(self.active().items()):
            book = self.books.get(symbol)
            if not book or now-book.timestamp > 3:
                continue
            intent = order.intent
            # Cancel only the unfilled maker leg on a toxic sweep/reversal. Filled
            # inventory is never abandoned: the exchange stop remains authoritative.
            if not order.filled and adverse_selection(features.get(symbol), intent.side,
                                                      self.vpin_gate(features.get(symbol))):
                if self.paper:
                    self.paper.cancel(symbol)
                elif order.status in ('NEW', 'PARTIALLY_FILLED'):
                    async with self.gateway.lock:
                        await self.gateway._cancel_entry(order)
                        if order.filled and not order.stop_id:
                            await self.gateway._protect(order, order.current_stop)
                        if not order.filled and order.status in ('CANCELED', 'EXPIRED', 'REJECTED'):
                            self.gateway.active.pop(symbol, None)
                continue
            opened = order.submitted if self.paper else order.opened
            if now-opened > self.args.quote_ttl:
                if self.paper:
                    self.paper.cancel(symbol)
                elif order.status in ('NEW', 'PARTIALLY_FILLED'):
                    async with self.gateway.lock:
                        await self.gateway._cancel_entry(order)
                        if order.filled and not order.stop_id:
                            await self.gateway._protect(order, order.current_stop)
                        if not order.filled and order.status in ('CANCELED', 'EXPIRED', 'REJECTED'):
                            self.gateway.active.pop(symbol, None)
            if not order.filled:
                continue
            initial = self.barriers.get(symbol, Barriers(float(intent.stop), float(intent.take_profit), opened+intent.horizon))
            entry = float(intent.price) if self.paper else order.average or float(intent.price)
            current_stop = initial.stop if self.paper else order.current_stop
            update = self.planner.update(intent.side, entry, book.mid, float(self.filters[symbol].tick), opened, now, initial, current_stop)
            if self.paper:
                self.barriers[symbol] = Barriers(update.stop, initial.take_profit, initial.deadline)
            elif update.stop != current_stop:
                await self.gateway.ratchet(symbol, update.stop)
            mark = book.bid if intent.side == 'BUY' else book.ask
            reason = exit_reason(intent.side, mark, update.stop, update.take_profit, now, update.deadline)
            if reason:
                if self.paper:
                    filled_notional = abs((float(order.intent.quantity)-order.remaining) * entry)
                    result = self.paper.close(symbol, book)
                    if result:
                        vote_record = self.order_votes.pop(symbol, None)
                        self.record_outcome(result['net_pnl'], *(vote_record or (None, None)),
                                            notional=filled_notional or None, symbol=symbol)
                        self.telemetry.event(f"{symbol}: paper closed {result['net_pnl']:+.4f} USDT")
                else:
                    # TP/deadline exits park a passive reduce-only peg first (maker
                    # 2 bps instead of taker 5 bps); a STOP always exits via market
                    # immediately: protection before cost.
                    maker_peg = None
                    if reason != 'STOP' and self.maker_exit_grace > 0:
                        maker_peg = book.ask if intent.side == 'BUY' else book.bid
                    await self.gateway.exit(symbol, maker_peg=maker_peg, grace=self.maker_exit_grace)
                self.cooldowns[symbol] = now+30

    async def maybe_carry(self, now, features):
        """Read-only funding diagnostics; no synthetic votes or order authorization."""
        if self.args.no_carry or self.args.offline or now < self.carry_scan_at:
            return
        self.carry_scan_at = now + self.carry.config.scan_interval
        self.carry.update(await self.client.premium_index(), now)

    def observe_signal(self, symbol, features, book, decision, votes, conviction, now):
        """Hand the lab the plan the runtime would have made, gated or not."""
        filters = self.filters.get(symbol)
        tick = float(filters.tick) if filters else max(book.mid*1e-6, 1e-12)
        price = book.bid if decision.side == 'BUY' else book.ask
        try:
            plan = self.planner.plan(decision.side, price, features.atr, tick, now)
        except ValueError:
            return None
        return self.lab.observe(symbol, features, book, decision, votes, plan, now, conviction)

    async def cycle(self):
        self.cycles += 1
        now = time.time()+self.client.offset_ms/1000
        if self.paper:
            self.wallet = self.paper.cash
            self.equity = self.paper.equity(self.books)
            self.available = self.equity
            self.used_margin = sum(float(o.intent.quantity)*float(o.intent.price) for o in self.paper.orders.values())
            self.initial_equity = self.paper.initial
        elif self.settings.api_key and time.monotonic()-self.account_at >= 3:
            before = dict(self.active())
            if self.gateway:
                await self.gateway.reconcile()
            await self.refresh_account()
            await self.settle_exchange({s: o for s, o in before.items() if s not in self.active() and o.filled})
        self.brake.update(self.equity)
        features = {s: a.features() for s, a in self.alpha.items()}
        cross = cross_sectional_momentum({s: f.momentum for s, f in features.items() if f.ready})
        lead_lag = {s: lead_lag_signal(s, features, now) for s in self.symbols}
        await self.manage_positions(now, features)
        rows = []
        for symbol in self.symbols:
            f, book = features[symbol], self.books.get(symbol)
            gate = self.vpin_gate(f)
            fresh = book is not None and 0 <= now-book.timestamp < 2 and now-self.alpha[symbol].last_trade_time < 5
            if (not fresh or (f.vpin is not None and f.vpin > gate) or self.brake.halted) and symbol in self.active():
                if self.paper:
                    self.paper.cancel(symbol)
                else:
                    async with self.gateway.lock:
                        order = self.gateway.active.get(symbol)
                        if order:
                            await self.gateway._cancel_entry(order)
                            if order.filled and not order.stop_id:
                                await self.gateway._protect(order, order.current_stop)
            if not book:
                rows.append({'symbol': symbol, 'status': 'WAITING', 'score': 0})
                continue
            risk_ok = fresh and not self.brake.halted and (self.paper is not None or (time.monotonic()-self.account_at < 10 and self.private_ready))
            capacity = self.used_margin < self.equity*.75 and self.equity > 0
            ready = self.paper is not None or (self.gateway is not None and not self.gateway.halted)
            cross_signal = cross.get(symbol, 0.0)
            symbol_lead_lag = lead_lag.get(symbol, 0.0)
            toxic_sweep = bool(f.sweep) and (f.vpin is not None and f.vpin > gate or
                                             f.vpin_jump >= .10 or f.flow_reversal)
            regime = ('TOXIC' if (f.vpin is not None and f.vpin > gate) or toxic_sweep else
                      'TREND' if max(abs(f.momentum), abs(cross_signal)) > 1.2 or abs(symbol_lead_lag) > 0 else
                       'CHOP')
            decision, votes = self.swarm.evaluate(f, book.mid, capacity, risk_ok, ready, True, cross_signal,
                                                  toxic_vpin=gate, edge_multiple=self.edge_multiple,
                                                  lead_lag=symbol_lead_lag, regime=regime)
            self.last_decision[symbol] = decision
            if symbol == self.symbols[0]:
                self.last_votes = votes
            rows.append({'symbol': symbol, 'mid': book.mid, 'spread': book.spread_bps, 'obi': f.obi,
                         'micro_price': f.micro_price, 'micro_delta_ticks': f.micro_delta_ticks,
                         'flow_acceleration': f.flow_acceleration, 'atr_ratio': f.atr_ratio,
                         'lead_lag': symbol_lead_lag, 'vpin': f.vpin,
                         'vpin_gate': gate, 'vpin_baseline': f.vpin_baseline,
                         'saturation': f.vpin_saturation, 'score': decision.score,
                         'threshold': decision.threshold, 'regime': decision.regime,
                         'directional': directional_score(votes), 'side': decision.side,
                         'blockers': [v.agent for v in votes if v.veto],
                         'why': next((v.reason for v in votes if v.veto), ''),
                         'status': decision.reason if f.ready else 'WARMUP', 'age': max(0, now-book.timestamp)})
            rows[-1].update(self.swarm.diagnostics)
            reasons = []
            if not fresh:
                reasons.append('STALE_DATA')
            if not f.ready:
                reasons.append('FEATURE_WARMUP')
            rows[-1]['atr_bps'] = atr_bps_value(f.atr, book.mid)
            volatility = volatility_block(rows[-1]['atr_bps'], self.min_atr_bps, self.max_atr_bps)
            if volatility:
                reasons.append(volatility)
            if self.min_conviction > 0 and rows[-1]['directional'] < self.min_conviction:
                reasons.append('LOW_CONVICTION')
            if f.vpin is not None and f.vpin > gate:
                reasons.append('VPIN_HIGH')
            if self.swarm.diagnostics['edge_ratio'] < self.edge_multiple:
                reasons.append('EDGE_BELOW_COST')
            if decision.score < decision.threshold:
                reasons.append('LOW_CONSENSUS')
            if adverse_selection(f, decision.side, gate):
                reasons.append('ADVERSE_SELECTION')
            if self.sizer.kelly() is None and not (self.paper or self.args.bootstrap):
                reasons.append('KELLY_WARMUP')
            if self.sizer.kelly() == 0:
                reasons.append('NONPOSITIVE_KELLY')
            if not capacity:
                reasons.append('CAPITAL_LIMIT')
            if not risk_ok:
                reasons.append('RISK_OR_FEED_GATE')
            rows[-1]['reasons'] = reasons
            rows[-1]['vpin_buckets'] = len(self.alpha[symbol].buckets.imbalances)
            self.block_counts.update(reasons)
            if self.lab:
                self.lab.resolve(symbol, book, now)
                self.observe_signal(symbol, f, book, decision, votes, rows[-1]['directional'], now)
            if not decision.allowed or symbol in self.active() or now < self.cooldowns.get(symbol, 0):
                continue
            # Volatility band is an ENTRY gate only; the lab already recorded the
            # signal above, so excluding a band costs no evidence.
            volatility = volatility_block(rows[-1].get('atr_bps'), self.min_atr_bps, self.max_atr_bps)
            if volatility:
                rows[-1]['status'] = volatility
                continue
            # Conviction of the three alpha agents with the 0.40 gate weight
            # stripped out, so this reads the swarm's actual directional opinion
            # rather than the headline score. Across 5178 recorded probes the
            # top conviction quintile returned +1.34 bps gross against -0.16 for
            # the bottom (Q5-Q1 = +1.51, t = 3.03). Default 0 disables the gate.
            if self.min_conviction > 0 and rows[-1]['directional'] < self.min_conviction:
                rows[-1]['status'] = 'LOW_CONVICTION'
                continue
            filters = self.filters[symbol]
            try:
                price = maker_quote(book, decision.side, float(filters.tick), inventory=0.0,
                                    risk_aversion=.1, variance_per_second=max(0., f.variance_per_second),
                                    remaining_seconds=self.args.horizon)
            except ValueError:
                rows[-1]['status'] = 'INVALID_MAKER_QUOTE'
                continue
            plan = self.planner.plan(decision.side, float(price), f.atr, float(filters.tick), now)
            size = self.sizer.size(self.equity, self.used_margin, float(price), plan.stop, filters,
                                   bootstrap=self.settings.mode == 'paper' or (self.settings.mode == 'testnet' and self.args.bootstrap), available=self.available,
                                   bootstrap_fraction=self.bootstrap_fraction)
            if size.quantity == '0':
                rows[-1]['status'] = size.reason
                continue
            intent = Intent(symbol, decision.side, size.quantity, format(price, 'f'),
                            format(quantize(plan.stop, filters.tick), 'f'),
                            format(quantize(plan.take_profit, filters.tick), 'f'), self.args.horizon, self.settings.mode)
            token = self.authority.issue(intent, decision)
            self.authorized += 1
            self.auth_at = time.time()
            if self.paper:
                self.authority.consume(token, intent)
                self.paper.submit(intent, book, now)
                result = 'PAPER'
            else:
                result = await self.gateway.submit(intent, token)
            if result == 'REQUOTE':
                self.requote_counts[symbol] = self.requote_counts.get(symbol, 0)+1
                self.cooldowns[symbol] = now+(.5 if self.requote_counts[symbol] < 3 else 30)
                if self.requote_counts[symbol] >= 3:
                    self.requote_counts[symbol] = 0
            elif result != 'REJECTED':
                self.order_votes[symbol] = (votes, decision.side)
                self.barriers[symbol] = plan
                self.used_margin += size.notional
                self.available -= size.notional
                self.telemetry.event(f'{symbol} {intent.side} GTX {size.quantity} @ {price}')
        # Funding opportunities remain research-only until a hedged strategy and
        # independently validated authorization policy exist.
        positions = [{'symbol': s, 'side': o.intent.side, 'filled': o.filled, 'entry': float(o.intent.price),
                      'stop': float(o.intent.stop) if self.paper else o.current_stop,
                      'protection': 'SIMULATED' if self.paper else 'ON EXCHANGE' if o.stop_id else 'PENDING FILL'} for s, o in self.active().items()]
        status = self.gateway.alert if self.gateway and self.gateway.alert else 'DRAWDOWN HALT' if self.brake.halted else 'MONITORING'
        if status == 'MONITORING' and rows and all(r.get('reasons') or r['status'] == 'WAITING' for r in rows):
            status = 'NO ENTRY · ' + ' / '.join(k for k, _ in self.block_counts.most_common(3))
        if self.settings.mode == 'mainnet' and not self.settings.armed:
            status = 'ADVISORY · MAINNET NOT ARMED'
        mission_target = int(getattr(self.args, 'mission_target', 0) or 0)
        if mission_target:
            status = f'{status} · MISSION +{len(self.ledger.records)-self._mission_start}/{mission_target} SETTLED'
        self.telemetry.update(mode=self.settings.mode.upper()+(' · SYNTHETIC' if self.args.offline else ''),
            armed=self.settings.armed, status=status, wallet=self.wallet if self.wallet else None,
            equity=self.equity if self.equity else None, pnl=self.equity-self.initial_equity if self.initial_equity else None,
            volume=self.paper.volume if self.paper else self.session_volume, fees=self.paper.fees if self.paper else self.session_fees,
            score=max((d.score for d in self.last_decision.values()), default=0), agents=[asdict(v) for v in self.last_votes],
            markets=rows, positions=positions, kelly=self.sizer.kelly(), margin=self.used_margin,
            lab=self.lab.summary() if self.lab else None,
            authorized=self.authorized, auth_at=self.auth_at, lab_warmup=self.lab_warmup,
            threshold=max((d.threshold for d in self.last_decision.values()), default=self.threshold),
            veto_threshold=self.veto_threshold, toxic_vpin=self.toxic_vpin,
            market_data=getattr(self.args, 'market_data', self.settings.mode),
            edge_multiple=self.edge_multiple,
            policy='SIMULATED' if self.simulated else 'STRICT', outcome_samples=len(self.sizer.outcomes),
            diagnostics={'cycles': self.cycles, 'blocked': dict(self.block_counts)},
            funding=self.carry.snapshot(),
            drawdown=1-self.equity/self.brake.peak if self.brake.peak else 0, updated_at=time.time())

    async def synthetic_feed(self):
        rng = random.Random(76)
        self.client.offset_ms = 0
        for index, symbol in enumerate(self.symbols):
            self.filters[symbol] = Filters(*map(dec, ['.01', '.01', '.01', '10000', '5']))
            self.alpha[symbol] = AlphaEngine(2, 5)
            for i in range(120):
                p = 100+index*10+math.sin(i*.2)
                self.alpha[symbol].on_bar(time.time()-60*(120-i), p+1, p-1, p)
        i = 0
        while not self.stop.is_set():
            now = time.time()
            for index, symbol in enumerate(self.symbols):
                p = round(100+index*10+math.sin(i*.05), 2)
                b = [[str(round(p-j*.01, 2)), str(2+rng.random())] for j in range(5)]
                a = [[str(round(p+.02+j*.01, 2)), str(2+rng.random())] for j in range(5)]
                await self.event({'e': 'depthUpdate', 's': symbol, 'b': b, 'a': a, 'u': i+1, 'pu': i, 'E': int(now*1000), 'T': int(now*1000)})
                await self.event({'e': 'aggTrade', 's': symbol, 'p': str(p), 'q': '1', 'm': i % 2 == 0, 'a': i, 'E': int(now*1000), 'T': int(now*1000)})
            i += 1
            await asyncio.sleep(.1)

    async def run(self):
        tasks = []
        journal = Journal(ROOT/'data'/f'{self.settings.mode}-journal.sqlite') if self.settings.armed else None
        async with BinanceFuturesClient(self.settings) as client:
            self.client = client
            if journal:
                self.gateway = ExecutionGateway(client, self.authority, journal)
            try:
                if self.args.offline:
                    tasks.append(asyncio.create_task(self.synthetic_feed()))
                else:
                    await self.initialize()
                    public = [s.lower()+'@depth5@100ms' for s in self.symbols]
                    market = [s.lower()+suffix for s in self.symbols for suffix in ('@aggTrade', '@kline_1m')]
                    tasks += [asyncio.create_task(consume_stream(stream_url(self.settings.ws, category, streams), self.event, self.disconnected, self.stop, category))
                              for category, streams in (('public', public), ('market', market))]
                    if self.gateway:
                        tasks.append(asyncio.create_task(user_stream(client, self.event, self.disconnected, self.stop)))
                user_shutdown = False
                mission_target = int(getattr(self.args, 'mission_target', 0) or 0)
                mission_start = len(self.ledger.records)
                start = time.monotonic()
                while not self.stop.is_set() and (not self.args.seconds or time.monotonic()-start < self.args.seconds):
                    if getattr(self.telemetry, 'shutdown_requested', False) or self.telemetry.snapshot().get('shutdown'):
                        user_shutdown = True
                        self.telemetry.event('KILL SWITCH accepted: cancelling orders and flattening everything')
                        break
                    if console_stop_requested(getattr(self, 'sigint', None)):
                        user_shutdown = True
                        self.telemetry.event('CONSOLE STOP accepted (Q/Ctrl+C): cancelling orders and flattening everything')
                        break
                    for task in tasks:
                        if task.done():
                            task.result()
                    try:
                        await self.cycle()
                    except (BinanceError, RuntimeError, PermissionError) as error:
                        if self.gateway:
                            self.gateway.halted = True
                            self.gateway.alert = str(error)
                        self.telemetry.event(str(error))
                        self.telemetry.update(status='HALTED · '+str(error))
                    if mission_target and len(self.ledger.records) - mission_start >= mission_target:
                        self.telemetry.event(f'MISSION TARGET reached: +{mission_target} settled outcomes this session; ending early')
                        break
                    await asyncio.sleep(.5)
            finally:
                self.stop.set()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                flatten_on_exit = bool(getattr(self.args, 'flatten_on_exit', False))
                if self.gateway:
                    flattened = False
                    try:
                        await asyncio.shield(self.gateway.cancel_entries())
                        if user_shutdown or flatten_on_exit:
                            await asyncio.shield(self.gateway.full_shutdown())
                            await asyncio.shield(flatten_venue(client, self.symbols, journal))
                            flattened = True
                        await asyncio.shield(self.gateway.reconcile())
                    except Exception:
                        self.telemetry.event('Shutdown reconciliation failed; inspect venue orders and protection')
                if self.args.seconds and not user_shutdown:
                    report = self.mission_summary()
                    (ROOT/'data'/f'{self.settings.mode}-mission-report.json').write_text(
                        json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
                    lab = report.get('lab') or {}
                    self.telemetry.event('MISSION REPORT: settled {0}/{1} kelly={2} · lab {3} probes mean {4} bps'.format(
                        report['settled_outcomes'], report['kelly_target'], report['kelly_ready'],
                        lab.get('probes', 0), round(lab.get('net_bps', 0.0), 2)))
                if journal:
                    journal.close()
                status = 'STOPPED'
                if self.gateway:
                    status = ('STOPPED · KILL SWITCH: venue flat, no working orders' if user_shutdown
                              else 'STOPPED · MISSION COMPLETE: venue flat, report written' if flatten_on_exit
                              else 'STOPPED · exchange stops retained for open positions')
                self.telemetry.update(status=status)
                path = ROOT/'data'/f'{self.settings.mode}-last-state.json'
                path.write_text(json.dumps(self.telemetry.snapshot(), indent=2, allow_nan=False), encoding='utf-8')


def arguments(mode, argv=None):
    parser = argparse.ArgumentParser(description=f'GROKTOPUS {mode}: fixed quorum, GTX entries, protected exits')
    parser.add_argument('--symbols', nargs='+', default=SYMBOLS)
    parser.add_argument('--seconds', type=float, default=0, help='Run duration after startup; 0 runs until Ctrl+C')
    parser.add_argument('--horizon', type=float, default=300)
    parser.add_argument('--quote-ttl', type=float, default=10)
    parser.add_argument('--equity', type=float, default=10000, help='Paper starting equity')
    parser.add_argument('--port', type=int)
    parser.add_argument('--no-ui', action='store_true')
    parser.add_argument('--arm', action='store_true', help='Explicitly enable Mainnet mutations')
    parser.add_argument('--bootstrap', action='store_true', help='Testnet only: small uncalibrated sizing until 20 settled outcomes')
    parser.add_argument('--bootstrap-fraction', type=float, default=None,
                        help='Bootstrap sizing as a fraction of equity per trade (demo observation knob; default .01, cap .25)')
    parser.add_argument('--market-data', choices=('mainnet', 'testnet'), default=None,
                        help='Paper feed source; default mainnet. Exchange execution must use its own venue.')
    parser.add_argument('--edge-multiple', type=float, default=None,
                        help='Edge/cost screening hurdle; default 1.8 live, 0.6 simulated demo')
    parser.add_argument('--toxic-vpin', type=float, default=None,
                        help='Absolute VPIN veto floor; default .65 everywhere (simulated demo extends it above the rolling baseline)')
    parser.add_argument('--threshold', type=float, default=None,
                        help='Consensus threshold; default .768 live, .55 simulated demo')
    parser.add_argument('--veto-threshold', type=float, default=None,
                        help='Consensus veto threshold; default .70 live, .45 simulated demo')
    parser.add_argument('--offline', action='store_true', help='Paper only: deterministic synthetic feed for diagnostics')
    parser.add_argument('--no-lab', action='store_true', help='Disable the counterfactual signal recorder')
    parser.add_argument('--seed-lab', dest='seed_lab', action='store_true', default=None,
                        help='Deprecated: assumed fills cannot seed execution sizing')
    parser.add_argument('--no-seed-lab', dest='seed_lab', action='store_false')
    parser.add_argument('--no-carry', action='store_true',
                        help='Disable the funding-carry scanner')
    parser.add_argument('--flatten-on-exit', action='store_true',
                        help='Timed data mission: when --seconds elapses, close every position and cancel every order before exiting')
    parser.add_argument('--mission-target', type=int, default=0,
                        help='Stop early once this many NEW settled outcomes are collected this session (0 = run full --seconds)')
    parser.add_argument('--maker-exit-grace', type=float, default=8.0,
                        help='Seconds a passive reduce-only exit peg is parked before market fallback; 0 disables maker exits')
    parser.add_argument('--min-atr-bps', type=float, default=0.0,
                        help='Skip entries when 1m ATR is below this many basis points of price; 0 disables the filter')
    parser.add_argument('--max-atr-bps', type=float, default=0.0,
                        help='Skip entries when 1m ATR is above this many basis points of price; 0 disables the ceiling')
    parser.add_argument('--min-conviction', type=float, default=0.0,
                        help='Skip entries when alpha-agent directional conviction is below this (0-1); 0 disables the gate')
    args = parser.parse_args(argv)
    args.symbols = list(dict.fromkeys(s.upper() for s in args.symbols))
    if not args.symbols or len(args.symbols) > 8 or any(not s.isalnum() for s in args.symbols):
        parser.error('Choose 1–8 alphanumeric symbols')
    if args.offline and mode != 'paper':
        parser.error('--offline is paper only')
    if args.bootstrap and mode == 'mainnet':
        parser.error('Mainnet requires measured outcomes; bootstrap is unavailable')
    if args.seed_lab:
        parser.error('Assumed-fill lab probes cannot seed execution sizing; use audited settled outcomes')
    args.seed_lab = False
    args.market_data = args.market_data or ('mainnet' if mode in ('paper', 'mainnet') else 'testnet')
    if mode != 'paper' and args.market_data != mode:
        parser.error('Execution prices must come from the execution venue; compare mainnet using paper mode')
    policy_values = (args.edge_multiple, args.toxic_vpin, args.threshold, args.veto_threshold)
    if any(v is not None and not math.isfinite(v) for v in policy_values):
        parser.error('Policy values must be finite')
    if args.edge_multiple is not None and args.edge_multiple <= 0:
        parser.error('--edge-multiple must be positive')
    if args.bootstrap_fraction is not None and (not math.isfinite(args.bootstrap_fraction) or not 0 < args.bootstrap_fraction <= .25):
        parser.error('--bootstrap-fraction must be in (0, 0.25]')
    if not 0 <= args.mission_target <= 1000:
        parser.error('--mission-target must be within [0, 1000]')
    for name, value in (('--min-atr-bps', args.min_atr_bps), ('--max-atr-bps', args.max_atr_bps)):
        if not math.isfinite(value) or value < 0:
            parser.error(f'{name} must be a finite, non-negative number')
    if 0 < args.max_atr_bps <= args.min_atr_bps:
        parser.error('--max-atr-bps must exceed --min-atr-bps')
    if not math.isfinite(args.min_conviction) or not 0 <= args.min_conviction <= 1:
        parser.error('--min-conviction must be within [0, 1]')
    if args.toxic_vpin is not None and not 0 < args.toxic_vpin <= 1:
        parser.error('--toxic-vpin must be in (0, 1]')
    if args.threshold is not None and not 0 < args.threshold <= 1:
        parser.error('--threshold must be in (0, 1]')
    if args.veto_threshold is not None and not 0 < args.veto_threshold <= 1:
        parser.error('--veto-threshold must be in (0, 1]')
    if (args.threshold is not None and args.veto_threshold is not None and
            args.veto_threshold > args.threshold):
        parser.error('--veto-threshold cannot exceed --threshold')
    if any(not math.isfinite(v) or v <= 0 for v in (args.horizon, args.quote_ttl, args.equity)) or args.seconds < 0 or not math.isfinite(args.seconds):
        parser.error('Invalid duration or equity')
    if not math.isfinite(args.maker_exit_grace) or args.maker_exit_grace < 0:
        parser.error('--maker-exit-grace must be a non-negative number')
    if not math.isfinite(args.min_atr_bps) or args.min_atr_bps < 0:
        parser.error('--min-atr-bps must be a non-negative number')
    return args


def main(mode, argv=None):
    args = arguments(mode, argv)
    settings = Settings.load(mode, args.arm)
    if mode == 'paper' and args.market_data == 'mainnet':
        settings = replace(Settings.load('mainnet', env={}), mode='paper', api_key='', secret='', armed=False)
    telemetry = Telemetry()
    runtime = Runtime(settings, args, telemetry)
    server = None
    # Terminal stop channels: Ctrl+C is converted into a cooperative flag so the
    # run loop exits through the SAME safe path as the kill switch (cancel every
    # order, flatten every position, remove protection) instead of dying mid-order.
    sigint = threading.Event()

    def _on_sigint(signum, frame):
        sigint.set()

    previous_sigint = None
    try:
        runtime.sigint = sigint
        previous_sigint = signal.signal(signal.SIGINT, _on_sigint)
    except ValueError:
        previous_sigint = None  # signal handlers only work in the main thread
    try:
        if not args.no_ui:
            port = args.port or settings.port
            server = start_server(telemetry, settings.host, port)
            print(f'Dashboard: http://{settings.host}:{port}')
        print(f'Mode: {mode}; armed={settings.armed}; symbols={",".join(args.symbols)}')
        if mode == 'mainnet' and settings.armed:
            # Arming can also come from .env.local, so name the source out loud.
            source = '--arm on the command line' if args.arm else 'ARM_LIVE_EXECUTION in .env.local'
            print('!! MAINNET ARMED via ' + source + ': real orders will move real funds.')
        print('Stop safely: press Q in this window or Ctrl+C - orders are cancelled,')
        print('             positions closed and exchange stops removed before exit.')
        asyncio.run(runtime.run())
    except KeyboardInterrupt:
        print('Stopped. Review retained exchange protection for any open position.')
    except (BinanceError, ValueError, RuntimeError, PermissionError) as error:
        print(f'Startup failed: {error}')
        raise SystemExit(1) from None
    finally:
        if previous_sigint is not None:
            signal.signal(signal.SIGINT, previous_sigint)
        if server:
            server.shutdown()
            server.server_close()
