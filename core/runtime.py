"""Concurrent market ingestion with one serialized portfolio/execution decision loop."""
import argparse
import asyncio
from dataclasses import asdict
import json
import math
from pathlib import Path
import random
import time

from config.settings import ROOT, Settings
from core.barrier_planner import BarrierPlanner, Barriers
from core.consensus_engine import TokenAuthority, WEIGHTS
from core.groktopus_swarm import GroktopusSwarm
from core.models import Book, Filters, Intent, dec, quantize
from core.position_sizer import PositionSizer, DrawdownBrake
from core.real_alpha_engine import AlphaEngine, calibrate_bucket_volume, cross_sectional_momentum
from core.signal_lab import SignalLab
from core.web_ui_server import Telemetry, start_server
from live.binance_futures_client import BinanceFuturesClient, BinanceError
from live.execution_gateway import ExecutionGateway, Journal
from live.futures_streams import PartialBookTracker, consume_stream, stream_url, user_stream
from simulation.paper_engine import PaperEngine

SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'ADAUSDT', 'LINKUSDT']
DIRECTIONAL = ('NORO', 'LUMEN', 'TIDAL')
# Calibrated buckets fill slower, so report VPIN from a partial window once it is meaningful.
VPIN_MIN_BUCKETS = 5


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


class Runtime:
    def __init__(self, settings, args, telemetry):
        self.settings, self.args, self.telemetry = settings, args, telemetry
        self.symbols = args.symbols
        self.stop = asyncio.Event()
        self.alpha = {s: AlphaEngine(min_buckets=VPIN_MIN_BUCKETS) for s in self.symbols}
        self.edge_multiple = args.edge_multiple if getattr(args, 'edge_multiple', None) is not None else (1.0 if getattr(args, 'bootstrap', False) else 1.8)
        self.toxic_vpin = args.toxic_vpin if getattr(args, 'toxic_vpin', None) is not None else (0.80 if getattr(args, 'bootstrap', False) else 0.65)
        self.books, self.filters = {}, {}
        self.tracker = PartialBookTracker()
        self.authority = TokenAuthority()
        self.swarm = GroktopusSwarm()
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
        self.outcomes_path = ROOT/'data'/f'{settings.mode}-outcomes.json'
        self.outcomes_path.parent.mkdir(exist_ok=True)
        if self.outcomes_path.exists() and not args.offline:
            for value in json.loads(self.outcomes_path.read_text()):
                self.sizer.record(float(value))
        # Pure observer: records what the gates refused so the gates can be calibrated.
        self.lab = None if args.no_lab else SignalLab(
            ROOT/'data'/f'{settings.mode}-signal-lab.jsonl', horizon=args.horizon)

    def record_outcome(self, pnl):
        self.sizer.record(pnl)
        self.brake.update(self.equity, pnl)
        if not self.args.offline:
            temp = self.outcomes_path.with_suffix('.tmp')
            temp.write_text(json.dumps(list(self.sizer.outcomes)), encoding='utf-8')
            temp.replace(self.outcomes_path)

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
            if alpha.last_trade_id >= 0 and trade_id > alpha.last_trade_id+1:
                alpha.reset_flow()
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
            trades = await self.client.history(symbol)
            relevant = [t for t in trades if t['time'] >= int(tracked.opened*1000)]
            income = await self.client.request('GET', '/fapi/v1/income', {'symbol': symbol,
                'incomeType': 'FUNDING_FEE', 'startTime': int(tracked.opened*1000), 'limit': 1000}, signed=True, weight=30)
            net = sum(float(t['realizedPnl'])-float(t['commission']) for t in relevant)
            net += sum(float(i['income']) for i in income if i.get('asset') == 'USDT')
            if len(trades) == 200 or any(t.get('commissionAsset') != 'USDT' for t in relevant):
                self.gateway.halted = True
                self.telemetry.event('Trade accounting incomplete/non-USDT fee; Kelly update withheld')
                continue
            if relevant:
                self.record_outcome(net)
                self.session_pnl += net
                self.session_volume += sum(float(t['qty'])*float(t['price']) for t in relevant)
                self.session_fees += sum(float(t['commission']) for t in relevant)
                self.telemetry.event(f'{symbol}: closed net {net:+.4f} USDT')

    async def manage_positions(self, now):
        for symbol, order in list(self.active().items()):
            book = self.books.get(symbol)
            if not book or now-book.timestamp > 3:
                continue
            intent = order.intent
            opened = order.submitted if self.paper else order.opened
            if now-opened > self.args.quote_ttl:
                if self.paper:
                    self.paper.cancel(symbol)
                elif order.status in ('NEW', 'PARTIALLY_FILLED'):
                    async with self.gateway.lock:
                        await self.gateway._cancel_entry(order)
                        if order.filled and not order.stop_id:
                            await self.gateway._protect(order, order.current_stop)
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
            sign = 1 if intent.side == 'BUY' else -1
            exit_due = sign*(mark-update.stop) <= 0 or sign*(mark-update.take_profit) >= 0 or now >= update.deadline
            if exit_due:
                if self.paper:
                    result = self.paper.close(symbol, book)
                    if result:
                        self.record_outcome(result['net_pnl'])
                        self.telemetry.event(f"{symbol}: paper closed {result['net_pnl']:+.4f} USDT")
                else:
                    await self.gateway.exit(symbol)
                self.cooldowns[symbol] = now+30

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
        await self.manage_positions(now)
        features = {s: a.features() for s, a in self.alpha.items()}
        cross = cross_sectional_momentum({s: f.momentum for s, f in features.items() if f.ready})
        rows = []
        for symbol in self.symbols:
            f, book = features[symbol], self.books.get(symbol)
            fresh = book is not None and 0 <= now-book.timestamp < 2 and now-self.alpha[symbol].last_trade_time < 5
            if (not fresh or (f.vpin is not None and f.vpin > .65) or self.brake.halted) and symbol in self.active():
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
            decision, votes = self.swarm.evaluate(f, book.mid, capacity, risk_ok, ready, True, cross.get(symbol, 0),
                                                  toxic_vpin=self.toxic_vpin, edge_multiple=self.edge_multiple)
            self.last_decision[symbol] = decision
            if symbol == self.symbols[0]:
                self.last_votes = votes
            rows.append({'symbol': symbol, 'mid': book.mid, 'spread': book.spread_bps, 'obi': f.obi,
                         'vpin': f.vpin, 'saturation': f.vpin_saturation, 'score': decision.score,
                         'directional': directional_score(votes), 'side': decision.side,
                         'blockers': [v.agent for v in votes if v.veto],
                         'why': next((v.reason for v in votes if v.veto), ''),
                         'status': decision.reason if f.ready else 'WARMUP', 'age': max(0, now-book.timestamp)})
            if self.lab:
                self.lab.resolve(symbol, book, now)
                self.observe_signal(symbol, f, book, decision, votes, rows[-1]['directional'], now)
            if not decision.allowed or symbol in self.active() or now < self.cooldowns.get(symbol, 0):
                continue
            filters = self.filters[symbol]
            price = quantize(book.bid if decision.side == 'BUY' else book.ask, filters.tick, up=decision.side == 'SELL')
            plan = self.planner.plan(decision.side, float(price), f.atr, float(filters.tick), now)
            size = self.sizer.size(self.equity, self.used_margin, float(price), plan.stop, filters,
                                   bootstrap=self.settings.mode == 'paper' or (self.settings.mode == 'testnet' and self.args.bootstrap), available=self.available)
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
                self.barriers[symbol] = plan
                self.used_margin += size.notional
                self.available -= size.notional
                self.telemetry.event(f'{symbol} {intent.side} GTX {size.quantity} @ {price}')
        positions = [{'symbol': s, 'side': o.intent.side, 'filled': o.filled, 'entry': float(o.intent.price),
                      'stop': float(o.intent.stop) if self.paper else o.current_stop,
                      'protection': 'SIMULATED' if self.paper else 'ON EXCHANGE' if o.stop_id else 'PENDING FILL'} for s, o in self.active().items()]
        status = self.gateway.alert if self.gateway and self.gateway.alert else 'DRAWDOWN HALT' if self.brake.halted else 'MONITORING'
        if self.settings.mode == 'mainnet' and not self.settings.armed:
            status = 'ADVISORY · MAINNET NOT ARMED'
        self.telemetry.update(mode=self.settings.mode.upper()+(' · SYNTHETIC' if self.args.offline else ''),
            armed=self.settings.armed, status=status, wallet=self.wallet if self.wallet else None,
            equity=self.equity if self.equity else None, pnl=self.equity-self.initial_equity if self.initial_equity else None,
            volume=self.paper.volume if self.paper else self.session_volume, fees=self.paper.fees if self.paper else self.session_fees,
            score=max((d.score for d in self.last_decision.values()), default=0), agents=[asdict(v) for v in self.last_votes],
            markets=rows, positions=positions, kelly=self.sizer.kelly(), margin=self.used_margin,
            lab=self.lab.summary() if self.lab else None,
            authorized=self.authorized, auth_at=self.auth_at,
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
                start = time.monotonic()
                while not self.stop.is_set() and (not self.args.seconds or time.monotonic()-start < self.args.seconds):
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
                    await asyncio.sleep(.5)
            finally:
                self.stop.set()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if self.gateway:
                    try:
                        await asyncio.shield(self.gateway.cancel_entries())
                        await asyncio.shield(self.gateway.reconcile())
                    except Exception:
                        self.telemetry.event('Shutdown reconciliation failed; inspect venue orders and protection')
                if journal:
                    journal.close()
                self.telemetry.update(status='STOPPED · exchange stops retained for open positions' if self.gateway else 'STOPPED')
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
    parser.add_argument('--edge-multiple', type=float, default=None, help='Edge multiple hurdle (default 1.0 for bootstrap, 1.8 for live)')
    parser.add_argument('--toxic-vpin', type=float, default=None, help='Toxic VPIN veto threshold (default 0.80 for bootstrap, 0.65 for live)')
    parser.add_argument('--offline', action='store_true', help='Paper only: deterministic synthetic feed for diagnostics')
    parser.add_argument('--no-lab', action='store_true', help='Disable the counterfactual signal recorder')
    args = parser.parse_args(argv)
    args.symbols = list(dict.fromkeys(s.upper() for s in args.symbols))
    if not args.symbols or len(args.symbols) > 8 or any(not s.isalnum() for s in args.symbols):
        parser.error('Choose 1–8 alphanumeric symbols')
    if args.offline and mode != 'paper':
        parser.error('--offline is paper only')
    if args.bootstrap and mode == 'mainnet':
        parser.error('Mainnet requires measured outcomes; bootstrap is unavailable')
    if any(not math.isfinite(v) or v <= 0 for v in (args.horizon, args.quote_ttl, args.equity)) or args.seconds < 0 or not math.isfinite(args.seconds):
        parser.error('Invalid duration or equity')
    return args


def main(mode, argv=None):
    args = arguments(mode, argv)
    settings = Settings.load(mode, args.arm)
    telemetry = Telemetry()
    runtime = Runtime(settings, args, telemetry)
    server = None
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
        asyncio.run(runtime.run())
    except KeyboardInterrupt:
        print('Stopped. Review retained exchange protection for any open position.')
    except (BinanceError, ValueError, RuntimeError, PermissionError) as error:
        print(f'Startup failed: {error}')
        raise SystemExit(1) from None
    finally:
        if server:
            server.shutdown()
            server.server_close()
