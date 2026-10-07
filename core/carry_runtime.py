"""Independent funding harvester. REST snapshots, private fills, no L2 dependency."""
import argparse
import asyncio
from contextlib import AsyncExitStack
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import signal
import time

from config.settings import ROOT, Settings, read_env
from core.consensus_engine import TokenAuthority
from core.funding_carry import (CarrySnapshot, plan_spot_perp, carry_votes,
                               isolated_margin_ratio, two_leg_capital_gate,
                               TWO_LEG_MIN_NOTIONAL_USDT)
from core.models import PairIntent, dec, quantize
from core.outcome_ledger import daily_brief
from core.routine_scheduler import EXECUTING, RECONCILED, RoutineScheduler
from live.binance_futures_client import BinanceFuturesClient, BinanceError
from live.binance_spot_client import BinanceSpotClient
from live.execution_gateway import PairExecutionGateway, Journal
from live.futures_streams import user_stream
from phan_tich.carry_lab import PairReconciler, wallet_totals, wallet_reconciliation, weekly_report


CONFIRMATION = 'I UNDERSTAND SPOT PERP MAINNET RISK'


class CarryProcessLock:
    """OS-released lock prevents two local carry runtimes sharing an account mode."""
    def __init__(self, path):
        self.path, self.file = path, None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b'0')
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError('Another carry runtime already owns this mode') from None
        return self

    def __exit__(self, *args):
        self.file.close()  # Closing releases the OS lock, including after crashes.


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--carry', action='store_true')
    parser.add_argument('--mode', choices=('shadow', 'testnet', 'mainnet'), default='shadow')
    parser.add_argument('--symbols', nargs='+', default=['BTCUSDT', 'ETHUSDT', 'SOLUSDT'])
    parser.add_argument('--seconds', type=float, default=0, help='0 runs until Ctrl+C')
    parser.add_argument('--notional', type=float, default=100, help='Maximum USDT per pair')
    parser.add_argument('--leverage', type=int, choices=(1, 2), default=1)
    parser.add_argument('--horizon-days', type=float, default=7)
    parser.add_argument('--profit-target-bps', type=float, default=100)
    parser.add_argument('--arm', action='store_true')
    parser.add_argument('--confirm', default='')
    parser.add_argument('--journal', type=Path)
    # Routine cadence for R1/R2/R3. R4 is a wall-clock daily brief and is not
    # tunable here. Nothing about the 30s RUNE/MARIN watchdog reads this value:
    # protection cadence is not a throughput knob.
    parser.add_argument('--routine-period', type=float, default=3600.0,
                        choices=(3600.0, 14400.0, 28800.0))
    parser.add_argument('--routine-state', type=Path)
    args = parser.parse_args(argv)
    args.symbols = list(dict.fromkeys(s.upper() for s in args.symbols))
    if not 1 <= len(args.symbols) <= 8 or any(not s.isalnum() or not s.endswith('USDT') for s in args.symbols):
        parser.error('Choose 1–8 USDT symbols')
    if (not all(math.isfinite(v) for v in (args.seconds, args.notional, args.horizon_days, args.profit_target_bps))
            or args.seconds < 0 or args.notional <= 0 or not 7 <= args.horizon_days <= 30
            or args.profit_target_bps <= 0):
        parser.error('Invalid carry duration/capital/target')
    if args.mode == 'mainnet' and (not args.arm or args.confirm != CONFIRMATION):
        parser.error('Mainnet requires --arm and --confirm "' + CONFIRMATION + '"')
    if args.mode == 'shadow' and args.arm:
        parser.error('Shadow cannot be armed')
    args.journal = args.journal or ROOT / 'data' / 'carry' / (args.mode + '.sqlite')
    args.routine_state = args.routine_state or ROOT / 'data' / 'carry' / (args.mode + '-routines.json')
    return args


def venue_settings(args, env=None):
    env = read_env() if env is None else env
    mode = args.mode
    # Ignore ARM_LIVE_EXECUTION: the pair CLI requires an explicit double opt-in.
    armed = mode == 'testnet' or (mode == 'mainnet' and args.arm and args.confirm == CONFIRMATION)
    futures = Settings.load('testnet' if mode == 'testnet' else 'mainnet', env=env)
    futures = replace(futures, mode=mode, armed=armed,
                      api_key=futures.api_key if armed else '', secret=futures.secret if armed else '')
    prefix = 'BINANCE_SPOT_' + ('TESTNET_' if mode == 'testnet' else 'MAINNET_')
    spot = Settings(mode, 'https://testnet.binance.vision' if mode == 'testnet' else 'https://api.binance.com',
                    'wss://ws-api.testnet.binance.vision/ws-api/v3' if mode == 'testnet'
                    else 'wss://ws-api.binance.com:443/ws-api/v3',
                    env.get(prefix + 'API_KEY', '') if armed else '',
                    env.get(prefix + 'SECRET_KEY', '') if armed else '', armed)
    return futures, spot


def common_step(*steps):
    values = [dec(s) for s in steps if dec(s) > 0]
    places = max(-v.as_tuple().exponent for v in values)
    scale = 10 ** places
    return dec(math.lcm(*(int(v * scale) for v in values))) / scale


def normalized_funding(history, interval):
    normalized = []
    for previous, current in zip(history, history[1:]):
        gap_ms = int(current['fundingTime']) - int(previous['fundingTime'])
        hours = min((1, 4, 8), key=lambda h: abs(gap_ms - h * 3600000))
        # Published settlement timestamps have millisecond jitter. Missing
        # whole periods remain errors; timestamp tolerance is only one second.
        if abs(gap_ms - hours * 3600000) > 1000:
            raise ValueError('Funding history has an unsupported interval or gap')
        normalized.append(float(current['fundingRate']) * interval / hours)
    return tuple(normalized)


def exchange_filters(row):
    fs = {f['filterType']: f for f in row['filters']}
    lot = fs['LOT_SIZE']
    market = fs.get('MARKET_LOT_SIZE', lot)
    step = common_step(lot['stepSize'], market['stepSize'])
    notional = fs.get('NOTIONAL', fs.get('MIN_NOTIONAL', {}))
    return {'step': step, 'tick': dec(fs['PRICE_FILTER']['tickSize']),
            'min_qty': max(dec(lot['minQty']), dec(market['minQty'])),
            'max_qty': min(dec(lot['maxQty']), dec(market['maxQty'])) if dec(market['maxQty']) else dec(lot['maxQty']),
            'min_notional': dec(notional.get('minNotional', notional.get('notional', '5')))}


def commission_bps(row, side='buyer'):
    """BNB discount applies only to standard commission, not tax/special."""
    discount = row.get('discount', {})
    enabled = (discount.get('enabledForAccount') is True and discount.get('enabledForSymbol') is True
               and discount.get('discountAsset') == 'BNB')
    standard = row['standardCommission']
    base = dec(standard['taker']) + dec(standard.get(side, 0))
    if enabled:
        base *= dec(discount['discount'])
    extra = sum((dec(row.get(k, {}).get('taker', 0)) + dec(row.get(k, {}).get(side, 0))
                 for k in ('taxCommission', 'specialCommission')), dec(0))
    # Conservative retail floor: never budget less than the verified 7.5bps
    # BNB baseline, even if a symbol's current API discount is more generous.
    return max(7.5 if enabled else 10.0, float((base + extra) * 10000)), enabled


def futures_equity(account):
    """Futures-side book value, preferring margin balance over free cash."""
    for key in ('totalMarginBalance', 'totalWalletBalance', 'availableBalance'):
        try:
            return float(dec(account[key]))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            continue
    return 0.0


async def futures_account(client):
    """V3 balances omit permissions/leverage; join their documented endpoints."""
    account, config, symbols = await asyncio.gather(
        client.account(), client.request('GET', '/fapi/v1/accountConfig', signed=True, weight=5),
        client.request('GET', '/fapi/v1/symbolConfig', signed=True, weight=5))
    by_symbol = {r['symbol']: r for r in symbols}
    positions = [dict(p, isolated=by_symbol.get(p['symbol'], {}).get('marginType') == 'ISOLATED',
                      leverage=by_symbol.get(p['symbol'], {}).get('leverage', 99))
                 for p in account.get('positions', [])]
    return dict(account, positions=positions, canTrade=config.get('canTrade') is True,
                symbol_config=by_symbol, dualSidePosition=config.get('dualSidePosition'),
                multiAssetsMargin=config.get('multiAssetsMargin'))


class CarryRuntime:
    def __init__(self, args, futures, spot, journal):
        self.args, self.futures, self.spot, self.journal = args, futures, spot, journal
        self.gateway = PairExecutionGateway(futures, spot, TokenAuthority(), journal)
        self.stop = asyncio.Event()
        self.futures_ready, self.spot_ready = asyncio.Event(), asyncio.Event()
        self.market = {}
        self.semaphore = asyncio.Semaphore(6)
        self.margin_topups = {}
        self.funding_seen = {}
        self.negative_periods = {}
        self.start_ms = int(time.time() * 1000)
        self.latest_accounts = None
        self.reconciler = PairReconciler(futures, spot, journal)
        self.reconciled = {}
        self.owned_spot_trades = {}
        self.wallet_before = None
        self.wallet_status = 'NOT_CHECKED'
        # Idempotent R1-R4 coordinator. It decides whether a firing has already
        # happened; it never decides whether a trade is safe.
        self.scheduler = RoutineScheduler(args.routine_state, mode=args.mode)
        self.staged_leases = {}
        self.capital = None
        self.research_only = False

    def report(self, kind, **data):
        print(json.dumps({'event': kind, 'mode': self.args.mode, **data}, ensure_ascii=False,
                         allow_nan=False), flush=True)

    # --------------------------------------------------------- capital + routines
    def execution_enabled(self):
        """Live two-leg staging needs a non-shadow mode AND a funded book.

        Both halves matter. `--mode shadow` is the operator's choice; the capital
        gate is the book's arithmetic, and on E = 3.85 USDT it is never satisfied.
        """
        return self.args.mode != 'shadow' and not self.research_only

    def enforce_capital_gate(self, equity_usdt, notional_usdt=None):
        """Force RESEARCH_ONLY when the book cannot fund two compliant legs."""
        gate = two_leg_capital_gate(equity_usdt, notional_usdt, self.args.mode)
        was_research = self.research_only
        self.capital = gate
        self.research_only = not gate.allowed and self.args.mode != 'shadow'
        if self.research_only and not was_research:
            detail = {'mode': gate.mode, 'reason': gate.reason,
                      'equity_usdt': gate.equity_usdt if math.isfinite(gate.equity_usdt) else None,
                      'notional_usdt': gate.notional_usdt if math.isfinite(gate.notional_usdt) else None,
                      'two_leg_min_usdt': TWO_LEG_MIN_NOTIONAL_USDT}
            self.report('CAPITAL_GATE_RESEARCH_ONLY', **detail)
            self.journal.write('CAPITAL_GATE_RESEARCH_ONLY', detail)
        return gate

    def routine_period(self, name):
        """R1-R3 follow --routine-period; R4 keeps its own daily cadence."""
        return None if name == 'reconcile-brief' else self.args.routine_period

    def routine_slot(self, name, symbol, now):
        """Claim one routine slot. None means already handled, or window shut."""
        period = self.routine_period(name)
        lease = self.scheduler.claim(name, symbol, now, period=period)
        if lease is None:
            self.report('ROUTINE_SKIPPED', routine=name, symbol=symbol,
                        state=self.scheduler.state(name, symbol, now, period),
                        window_open=self.scheduler.window_open(name, now, period))
        return lease

    def recover_routines(self):
        """Surface slots a crash left mid-flight; they are never re-claimed."""
        stranded = self.scheduler.recover()
        for lease in stranded:
            self.report('ROUTINE_RECOVERY', routine=lease.routine, symbol=lease.symbol,
                        window=lease.window_index, state=lease.state)
            self.journal.write('ROUTINE_RECOVERY', {'slot': lease.slot, 'key': lease.key,
                               'routine': lease.routine, 'symbol': lease.symbol,
                               'window_index': lease.window_index, 'state': lease.state})
        if stranded and self.args.mode != 'shadow':
            # A half-open pair is exactly what an EXECUTING slot means here.
            self.gateway.halted = True
        return stranded

    def settle_routines(self):
        """Walk staged leases to RECONCILED then DONE once their pair reconciles."""
        for pair_id, lease in list(self.staged_leases.items()):
            report = self.reconciled.get(pair_id)
            if not report or report.get('status') != 'RECONCILED':
                continue
            now = time.time()
            if lease.state == EXECUTING:
                lease = self.scheduler.mark_reconciled(lease, now)
            if lease.state == RECONCILED:
                lease = self.scheduler.mark_done(lease, now)
            self.staged_leases.pop(pair_id, None)
            self.report('ROUTINE_DONE', routine=lease.routine, symbol=lease.symbol,
                        window=lease.window_index)

    def publish_daily_brief(self, now=None):
        """R4 at 07:00 Asia/Saigon. Idempotent: at most one brief per business day."""
        now = time.time() if now is None else now
        lease = self.routine_slot('reconcile-brief', '*', now)
        if lease is None:
            return None
        lease = self.scheduler.mark_executing(lease, now)
        legs = []
        for pair_id, report in self.reconciled.items():
            pair = self.gateway.pairs.get(pair_id)
            if report.get('status') != 'RECONCILED' or pair is None or not pair.closed_at:
                continue
            legs.append({'symbol': pair.intent.perp_symbol,
                         'delta_spot_usdt': float(dec(report['spot_pnl_usdt'])),
                         'delta_perp_usdt': float(dec(report['perp_pnl_usdt'])),
                         'funding_usdt': float(dec(report['funding_usdt'])),
                         'fees_usdt': float(dec(report['fee_usdt'])),
                         'closed_at': float(pair.closed_at)})
        brief = daily_brief(legs, now)
        brief['wallet_status'] = self.wallet_status
        self.journal.write('CARRY_DAILY_BRIEF', brief)
        self.report('DAILY_BRIEF', label=brief['label'], legs=brief['legs'],
                    net_pnl_usdt=brief['net_pnl_usdt'], funding_usdt=brief['funding_usdt'],
                    fees_usdt=brief['fees_usdt'], status=brief['status'])
        path = self.args.journal.with_name('%s-brief-%s.json' % (self.args.mode, brief['label']))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(brief, indent=2, allow_nan=False), encoding='utf-8')
        lease = self.scheduler.mark_reconciled(lease, now)
        self.scheduler.mark_done(lease, now)
        return brief

    async def initialize(self):
        self.recover_routines()
        await asyncio.gather(self.futures.sync_time(), self.spot.sync_time())
        f, s = await asyncio.gather(self.futures.exchange_info(), self.spot.exchange_info())
        self.futures_info = {r['symbol']: r for r in f['symbols']}
        self.spot_info = {r['symbol']: r for r in s['symbols']}
        for symbol in self.args.symbols:
            if (symbol not in self.futures_info or symbol not in self.spot_info
                    or self.futures_info[symbol]['status'] != 'TRADING'
                    or self.futures_info[symbol].get('contractType') != 'PERPETUAL'
                    or self.spot_info[symbol]['status'] != 'TRADING'):
                raise ValueError('Unavailable matching spot/perpetual: ' + symbol)
        if self.args.mode != 'shadow':
            if any(not c.settings.api_key or not c.settings.secret for c in (self.futures, self.spot)):
                raise PermissionError('Separate Spot and Futures credentials are required')
            side, multi = await asyncio.gather(
                self.futures.request('GET', '/fapi/v1/positionSide/dual', signed=True),
                self.futures.request('GET', '/fapi/v1/multiAssetsMargin', signed=True))
            if side['dualSidePosition'] or multi['multiAssetsMargin']:
                raise PermissionError('Carry requires one-way, single-asset futures mode')
            await self.gateway.recover()
            for symbol in self.args.symbols:
                rows, orders = await asyncio.gather(self.futures.positions(symbol), self.futures.open_orders(symbol))
                if any(dec(r['positionAmt']) != 0 for r in rows) or orders:
                    raise PermissionError('Existing unattributed futures exposure on ' + symbol)
                try:
                    await self.futures.request('POST', '/fapi/v1/marginType',
                                               {'symbol': symbol, 'marginType': 'ISOLATED'}, signed=True)
                except BinanceError as error:
                    if error.code != -4046:
                        raise
                result = await self.futures.request('POST', '/fapi/v1/leverage',
                                                   {'symbol': symbol, 'leverage': self.args.leverage}, signed=True)
                if int(result['leverage']) != self.args.leverage:
                    raise RuntimeError('Futures leverage not confirmed')
            self.wallet_before = wallet_totals(await self.spot.account())
            self.start_ms = int(time.time() * 1000)
            self.journal.write('CARRY_WALLET_BASELINE', {'start_ms': self.start_ms,
                               'balances': {k: str(v) for k, v in self.wallet_before.items()}})

    async def symbol_snapshot(self, symbol, premium, interval):
        async with self.semaphore:
            spot, perp, oi, history = await asyncio.gather(
                self.spot.book_ticker(symbol),
                self.futures.request('GET', '/fapi/v1/ticker/bookTicker', {'symbol': symbol}, weight=2),
                self.futures.request('GET', '/fapi/v1/openInterest', {'symbol': symbol}),
                self.futures.request('GET', '/fapi/v1/fundingRate', {'symbol': symbol, 'limit': 90}))
            now = time.time() + self.futures.offset_ms / 1000
            observed_at = min(now, int(premium['time']) / 1000, int(perp['time']) / 1000)
            settled = [r for r in history if int(r['fundingTime']) / 1000 <= now]
            normalized = normalized_funding(settled, interval)
            self.market[symbol] = {'spot': spot, 'perp': perp, 'history': settled}
            return CarrySnapshot(symbol, float(spot['bidPrice']), float(spot['askPrice']),
                                 float(perp['bidPrice']), float(perp['askPrice']),
                                 float(premium['lastFundingRate']), interval, int(premium['nextFundingTime']) / 1000,
                                 float(oi['openInterest']) * float(premium['markPrice']), observed_at,
                                 tuple(normalized))

    async def collect(self):
        started = time.monotonic()
        premium, info = await asyncio.gather(self.futures.premium_index(),
                                             self.futures.request('GET', '/fapi/v1/fundingInfo'))
        rates = {r['symbol']: r for r in premium}
        intervals = {r['symbol']: int(r['fundingIntervalHours']) for r in info}
        # fundingInfo lists adjustments; unlisted contracts have the default 8h.
        snapshots = await asyncio.gather(*(self.symbol_snapshot(s, rates[s], intervals.get(s, 8))
                                          for s in self.args.symbols))
        self.report('REST_BATCH', seconds=round(time.monotonic() - started, 3), symbols=len(snapshots))
        return snapshots

    async def account_snapshot(self, snapshot):
        spot_account, future_account, fee, bnb, future_fee = await asyncio.gather(
            self.spot.account(), futures_account(self.futures), self.spot.commission(snapshot.symbol),
            self.spot.ticker('BNBUSDT'), self.futures.request('GET', '/fapi/v1/commissionRate',
                {'symbol': snapshot.symbol}, signed=True, weight=20))
        balances = {r['asset']: dec(r['free']) for r in spot_account['balances']}
        buy_fee, buy_bnb = commission_bps(fee)
        sell_fee, sell_bnb = commission_bps(fee, 'seller')
        reserve = dec(self.args.notional * len(self.args.symbols) * (buy_fee + sell_fee) / 10000) * 2
        bnb_ok = buy_bnb and sell_bnb and balances.get('BNB', dec(0)) * dec(bnb['price']) >= reserve
        positions = [p for p in future_account.get('positions', []) if dec(p.get('positionAmt', 0)) != 0]
        ratio = max((isolated_margin_ratio(p) for p in positions), default=0)
        active_notional = sum(float(p.intent.quantity) * float(p.intent.perp_price)
                              for p in self.gateway.pairs.values() if p.state != 'CLOSED')
        reserved_spot = sum((max(dec(0), dec(p.intent.quantity) - dec(p.spot_owned)) * dec(p.intent.spot_price)
                             for p in self.gateway.pairs.values() if p.state != 'CLOSED'), dec(0))
        capacity = (balances.get('USDT', dec(0)) - reserved_spot * dec('1.02') >= dec(self.args.notional * 1.02)
                    and dec(future_account['availableBalance']) >= dec(self.args.notional / self.args.leverage * 1.1)
                    and active_notional + self.args.notional <= self.args.notional * len(self.args.symbols) + .001)
        verified = (spot_account.get('canTrade') is True and future_account.get('canTrade') is True
                    and future_account['dualSidePosition'] is False and future_account['multiAssetsMargin'] is False
                    and future_account['symbol_config'].get(snapshot.symbol, {}).get('marginType') == 'ISOLATED'
                    and int(future_account['symbol_config'].get(snapshot.symbol, {}).get('leverage', 99)) in (1, 2)
                    and all(p.get('isolated') is True and int(p.get('leverage', 99)) <= 2 for p in positions))
        self.latest_accounts = (spot_account, future_account)
        # Whole book across BOTH venues, never one side of it.
        self.enforce_capital_gate(float(balances.get('USDT', dec(0))) + futures_equity(future_account),
                                  self.args.notional)
        return replace(snapshot, spot_fee_bps=max(buy_fee, sell_fee), bnb_ok=bnb_ok,
                       accounts_verified=verified, capacity_ok=capacity, margin_ratio=ratio,
                       futures_fee_bps=max(6, float(dec(future_fee['makerCommissionRate']) + dec(future_fee['takerCommissionRate'])) * 10000),
                       futures_taker_fee_bps=max(4, float(future_fee['takerCommissionRate']) * 10000))

    def make_intent(self, snapshot, plan):
        sf = exchange_filters(self.spot_info[snapshot.symbol])
        ff = exchange_filters(self.futures_info[snapshot.symbol])
        step = common_step(sf['step'], ff['step'])
        price = quantize(snapshot.perp_ask, ff['tick'], up=True)
        q = quantize(dec(self.args.notional) / price, step)
        if (q < max(sf['min_qty'], ff['min_qty']) or q > min(sf['max_qty'], ff['max_qty'])
                or q * dec(snapshot.spot_bid) < sf['min_notional'] or q * price < ff['min_notional']):
            raise ValueError('Capital cap does not meet both venues minimum lot/notional')
        return PairIntent(snapshot.symbol, snapshot.symbol, self.spot_info[snapshot.symbol]['baseAsset'],
                          str(q), str(price), str(snapshot.spot_ask), str(quantize(price * dec('1.30'), ff['tick'], up=True)),
                          str(sf['step']), str(ff['step']), str(sf['min_notional']), plan.horizon_days * 86400,
                          self.args.mode, self.args.leverage, str(snapshot.spot_fee_bps / 10000), True,
                          str(snapshot.futures_taker_fee_bps))

    def count_negative_settlements(self, pair, history):
        seen = self.funding_seen.get(pair.pair_id, int(pair.opened * 1000))
        count = self.negative_periods.get(pair.pair_id, 0)
        for row in sorted(history, key=lambda r: int(r['fundingTime'])):
            if int(row['fundingTime']) > seen:
                count = count + 1 if dec(row['fundingRate']) < 0 else 0
                seen = int(row['fundingTime'])
        self.funding_seen[pair.pair_id] = seen
        self.negative_periods[pair.pair_id] = count
        return count

    async def scan(self):
        snapshots = await asyncio.wait_for(self.collect(), timeout=50)
        scan_lease = self.routine_slot('funding-scan', '*',
                                       time.time() + self.futures.offset_ms / 1000)
        if scan_lease is not None:
            scan_lease = self.scheduler.mark_executing(scan_lease)
        for snapshot in snapshots:
            if self.args.mode != 'shadow':
                snapshot = await asyncio.wait_for(self.account_snapshot(snapshot), timeout=8)
            now = time.time() + self.futures.offset_ms / 1000
            plan, reason = plan_spot_perp(snapshot, now, self.args.horizon_days)
            decision, votes = carry_votes(snapshot, plan,
                gateway_ready=self.futures_ready.is_set() and self.spot_ready.is_set() and not self.gateway.halted,
                exits_ready=self.futures_ready.is_set() and self.spot_ready.is_set(), now=now)
            self.report('PAIR_SNAPSHOT', symbol=snapshot.symbol, spot=snapshot.spot_ask, perp=snapshot.perp_bid,
                        basis_bps=round(snapshot.basis_fraction * 10000, 3), funding_bps=snapshot.rate * 10000,
                        interval_hours=snapshot.interval_hours, plan=reason,
                        net_bps=round(plan.net_bps, 3) if plan else None, allowed=decision.allowed,
                        votes={v.agent: v.reason for v in votes if v.side == 'HOLD'})
            active = next((p for p in self.gateway.pairs.values()
                           if p.intent.perp_symbol == snapshot.symbol and p.state != 'CLOSED'), None)
            if active:
                history = self.market[snapshot.symbol]['history']
                if self.count_negative_settlements(active, history) >= 3:
                    await self.gateway.unwind(active.pair_id, 'THREE_NEGATIVE_SETTLEMENTS')
                elif abs(snapshot.basis_fraction) > .015:
                    await self.gateway.unwind(active.pair_id, 'BASIS_LIMIT')
                elif now - active.opened >= active.intent.horizon:
                    await self.gateway.unwind(active.pair_id, 'HORIZON')
                elif now - active.opened > 60 and active.entry_status not in self.gateway.TERMINAL:
                    await self.gateway.unwind(active.pair_id, 'ENTRY_TTL')
            elif decision.allowed and self.execution_enabled():
                # R2 sizes the envelope, R3 stages the pair. Both are claimed
                # before anything leaves the process, so a restart mid-submit
                # finds the slot taken instead of sending a second entry.
                envelope = self.routine_slot('envelope-size', snapshot.symbol, now)
                if envelope is None:
                    continue
                envelope = self.scheduler.mark_executing(envelope, now)
                stage = self.routine_slot('stage-pair', snapshot.symbol, now)
                if stage is None:
                    self.scheduler.mark_done(self.scheduler.mark_reconciled(envelope, now), now)
                    continue
                stage = self.scheduler.mark_executing(stage, now)
                self.scheduler.mark_done(self.scheduler.mark_reconciled(envelope, now), now)
                pair = self.make_intent(snapshot, plan)
                self.journal.write('PAIR_VOTES', {'intent': pair.__dict__, 'routine_slot': stage.slot,
                                   'votes': [v.__dict__ for v in votes], 'score': decision.score})
                token = self.gateway.authority.issue_pair(pair, decision)
                state = await self.gateway.submit(pair, token)
                self.staged_leases[getattr(state, 'pair_id', pair.perp_symbol)] = stage
        if scan_lease is not None:
            self.scheduler.mark_done(self.scheduler.mark_reconciled(scan_lease))
        if self.args.mode != 'shadow':
            await self.reconcile_accounts()

    async def reconcile_accounts(self):
        for pair in list(self.gateway.pairs.values()):
            old = self.reconciled.get(pair.pair_id, {})
            if (pair.state == 'CLOSED' and old.get('status') == 'RECONCILED'
                    and old.get('reported_at', 0) > pair.closed_at + 120):
                continue
            top = self.market.get(pair.intent.spot_symbol)
            if top is None:
                spot, perp = await asyncio.gather(self.spot.book_ticker(pair.intent.spot_symbol),
                    self.futures.request('GET', '/fapi/v1/ticker/bookTicker', {'symbol': pair.intent.perp_symbol}, weight=2))
                top = {'spot': spot, 'perp': perp}
            report, trades = await self.reconciler.pair_report(pair, top['spot']['bidPrice'], top['perp']['askPrice'],
                end_ms=int(pair.closed_at * 1000) if pair.closed_at else None)
            self.reconciled[pair.pair_id] = report
            self.owned_spot_trades[pair.pair_id] = trades
            self.report('PAIR_PNL', **report)
        before_seq = self.journal.db.execute('SELECT MAX(id) FROM events').fetchone()[0]
        current = wallet_totals(await self.spot.account())
        after_seq = self.journal.db.execute('SELECT MAX(id) FROM events').fetchone()[0]
        if before_seq != after_seq:
            self.report('WALLET_RETRY', reason='Execution changed during wallet snapshot')
            return
        trades = [t for rows in self.owned_spot_trades.values() for t in rows if int(t['time']) >= self.start_ms]
        wallet = wallet_reconciliation(self.wallet_before, current, trades,
                    {p.intent.spot_symbol: p.intent.base_asset for p in self.gateway.pairs.values()})
        self.wallet_status = wallet['status']
        self.journal.write('CARRY_WALLET_RECONCILIATION', wallet)
        self.report('WALLET_RECONCILIATION', **wallet)
        if wallet['status'] != 'RECONCILED':
            self.gateway.halted = True
        for pair_id, report in self.reconciled.items():
            report['wallet_status'] = wallet['status']
            self.journal.write('PAIR_RECONCILIATION', report)
            pair = self.gateway.pairs[pair_id]
            if report['status'] == 'RECONCILED' and wallet['status'] == 'RECONCILED' and not report['closed']:
                notional = dec(pair.intent.quantity) * dec(pair.intent.perp_price)
                if dec(report['net_usdt']) / notional * 10000 >= dec(self.args.profit_target_bps):
                    await self.gateway.unwind(pair_id, 'NET_PROFIT_TARGET')
        self.settle_routines()
        self.publish_daily_brief()
        self.write_weekly_report()

    def write_weekly_report(self):
        rows = []
        for _, payload in self.journal.db.execute("SELECT kind,payload FROM events WHERE kind='PAIR_RECONCILIATION' ORDER BY id"):
            row = json.loads(payload)
            rows.append(row)
        latest = {r['pair_id']: r for r in rows}
        for row in latest.values():
            if row.get('wallet_status') != 'RECONCILED':
                row = latest[row['pair_id']] = dict(row, status='UNMATCHED')
        path = self.args.journal.with_name(self.args.mode + '-weekly.md')
        path.write_text(weekly_report(latest.values()) + '\nSpot wallet check: ' + self.wallet_status + '\n', encoding='utf-8')

    async def watchdog_once(self):
        if self.args.mode == 'shadow':
            self.report('WATCHDOG', status='SHADOW_NO_PRIVATE_ACCOUNT')
            return
        account = await futures_account(self.futures)
        positions = {p['symbol']: p for p in account.get('positions', []) if dec(p.get('positionAmt', 0)) != 0}
        available = dec(account['availableBalance'])
        for pair in self.gateway.pairs.values():
            if pair.state == 'CLOSED':
                continue
            if pair.state == 'UNWINDING':
                await self.gateway.unwind(pair.pair_id, 'RETRY_UNWIND_RECONCILED')
                continue
            async with self.gateway.lock:
                if pair.entry_status not in self.gateway.TERMINAL:
                    row = await self.futures.order(pair.intent.perp_symbol, pair.pair_id)
                    await self.gateway.apply_entry(pair, row)
                if pair.short_quantity > 0:
                    await self.gateway.reconcile_pair(pair)
                    if pair.state != 'CLOSED' and pair.stop_id:
                        algo = await self.futures.algo(pair.stop_id)
                        if algo.get('algoStatus') not in ('NEW', 'WORKING', 'ACCEPTED'):
                            self.gateway.halted = True
                            await self.gateway.unwind_locked(pair, 'EXCHANGE_STOP_INACTIVE')
            if pair.state == 'CLOSED':
                continue
            position = positions.get(pair.intent.perp_symbol)
            if not position:
                continue
            ratio = isolated_margin_ratio(position)
            if position.get('isolated') is not True or int(position.get('leverage', 99)) > 2:
                ratio = math.inf
            if ratio > .65:
                self.report('MARGIN_ALERT', pair=pair.pair_id, ratio=ratio if math.isfinite(ratio) else None)
                # Rebalance only from free futures cash, with a 25% notional lifetime cap.
                used = self.margin_topups.get(pair.pair_id, dec(0))
                cap = dec(pair.intent.quantity) * dec(pair.intent.perp_price) * dec('.25') - used
                needed = dec(0)
                if math.isfinite(ratio):
                    needed = (dec(position['maintMargin']) / dec('.50') - dec(position['isolatedWallet'])
                              - dec(position.get('unrealizedProfit', position.get('unRealizedProfit', 0))))
                if 0 < needed <= min(available, cap):
                    # Unknown transfer outcomes are never retried; verify margin instead.
                    self.journal.write('PAIR_MARGIN_INTENT', {'pair_id': pair.pair_id, 'amount': str(needed)})
                    self.margin_topups[pair.pair_id] = used + needed
                    try:
                        await self.futures.request('POST', '/fapi/v1/positionMargin',
                            {'symbol': pair.intent.perp_symbol, 'amount': str(needed), 'type': 1,
                             'positionSide': 'BOTH'}, signed=True)
                    except BinanceError as error:
                        self.report('MARGIN_REBALANCE_ERROR', code=str(error.code))
                    fresh = await futures_account(self.futures)
                    updated = next((p for p in fresh['positions'] if p['symbol'] == pair.intent.perp_symbol), {})
                    if isolated_margin_ratio(updated) <= .65:
                        available -= needed
                        self.report('MARGIN_REBALANCED', pair=pair.pair_id, added=str(needed))
                        continue
                self.gateway.halted = True
                await self.gateway.unwind(pair.pair_id, 'MARGIN_RATIO_OVER_65_PERCENT')

    async def disconnected(self, label):
        self.futures_ready.clear() if label == 'private' else self.spot_ready.clear()
        self.gateway.halted = True
        self.report('STREAM_DISCONNECTED', stream=label)
        await self.gateway.shutdown()

    async def futures_event(self, event):
        if event.get('e') == '_CONNECTED':
            self.futures_ready.set()
        else:
            await self.gateway.on_user_event(event)

    async def spot_event(self, event):
        if event.get('e') in ('executionReport', 'outboundAccountPosition', 'balanceUpdate'):
            # Persist execution identity only, never market ticks or credentials.
            self.journal.write('SPOT_ACCOUNT_EVENT', {k: event[k] for k in ('e', 'E', 's', 'i', 'c', 'X') if k in event})

    async def watchdog(self):
        while not self.stop.is_set():
            try:
                # Transport reads have finite timeouts; don't cancel mutations.
                await self.watchdog_once()
            except Exception as error:
                self.gateway.halted = True
                self.report('WATCHDOG_FAILURE', error=type(error).__name__)
                await self.gateway.shutdown()
            try:
                await asyncio.wait_for(self.stop.wait(), 30)
            except asyncio.TimeoutError:
                continue

    async def run(self):
        await asyncio.wait_for(self.initialize(), timeout=50)
        tasks = [asyncio.create_task(self.watchdog())]
        if self.args.mode != 'shadow':
            tasks.extend([asyncio.create_task(user_stream(self.futures, self.futures_event, self.disconnected, self.stop)),
                          asyncio.create_task(self.spot.user_events(self.spot_event, self.spot_ready, self.disconnected, self.stop))])
        started = time.monotonic()
        try:
            while not self.stop.is_set():
                cycle = time.monotonic()
                try:
                    # Only reads have batch timeouts. Never cancel an order POST
                    # because a scanning deadline expired.
                    await self.scan()
                except (BinanceError, ValueError, RuntimeError, KeyError, asyncio.TimeoutError, PermissionError) as error:
                    self.report('SCAN_BLOCKED', error=type(error).__name__, detail=str(error))
                    if self.args.mode != 'shadow':
                        self.gateway.halted = True
                for task in tasks:
                    if task.done():
                        task.result()
                        raise RuntimeError('Carry background task stopped')
                remaining = self.args.seconds - (time.monotonic() - started) if self.args.seconds else math.inf
                if remaining <= 0:
                    break
                wait = min(max(0, 60 - (time.monotonic() - cycle)), remaining)
                try:
                    await asyncio.wait_for(self.stop.wait(), wait)
                except asyncio.TimeoutError:
                    if self.args.seconds and time.monotonic() - started >= self.args.seconds:
                        break
        finally:
            self.stop.set()
            try:
                if self.args.mode != 'shadow':
                    await self.gateway.shutdown()
                    await self.reconcile_accounts()
                else:
                    self.write_weekly_report()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            self.report('CARRY_STOPPED', seconds=round(time.monotonic() - started, 2),
                        pairs=len(self.gateway.pairs), halted=self.gateway.halted)


async def run(args):
    futures_settings, spot_settings = venue_settings(args)
    args.journal.parent.mkdir(parents=True, exist_ok=True)
    journal = Journal(args.journal)
    try:
        async with AsyncExitStack() as stack:
            futures = await stack.enter_async_context(BinanceFuturesClient(futures_settings))
            spot = await stack.enter_async_context(BinanceSpotClient(spot_settings))
            runtime = CarryRuntime(args, futures, spot, journal)
            loop = asyncio.get_running_loop()
            previous = signal.signal(signal.SIGINT, lambda *_: loop.call_soon_threadsafe(runtime.stop.set))
            try:
                await runtime.run()
            finally:
                signal.signal(signal.SIGINT, previous)
    finally:
        journal.close()


def main(argv=None):
    args = arguments(argv)
    try:
        with CarryProcessLock(ROOT / 'data' / 'carry' / (args.mode + '.lock')):
            asyncio.run(run(args))
    except (ValueError, RuntimeError, PermissionError, BinanceError, asyncio.TimeoutError) as error:
        print(json.dumps({'event': 'CARRY_FAILED', 'error': type(error).__name__, 'detail': str(error)}), flush=True)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
