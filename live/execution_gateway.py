"""VESKA: journal before submission; protect partial fills; never retry unknown POST."""
import asyncio
from dataclasses import asdict, dataclass
import json
import sqlite3
import time
import uuid
from core.models import Intent, dec
from live.binance_futures_client import BinanceError
from live.binance_spot_client import received_coins, gross_buy_quantity
from core.models import PairIntent, quantize


class Journal:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, timestamp REAL, kind TEXT, payload TEXT)')
        self.db.commit()

    def write(self, kind, payload):
        self.db.execute('INSERT INTO events(timestamp,kind,payload) VALUES(?,?,?)',
                        (time.time(), kind, json.dumps(payload, sort_keys=True)))
        self.db.commit()

    def close(self):
        self.db.close()


@dataclass
class TrackedOrder:
    intent: Intent
    client_id: str
    opened: float
    filled: float = 0.0
    average: float = 0.0
    status: str = 'SUBMITTING'
    stop_id: str = ''
    current_stop: float = 0.0
    closing: bool = False


class ExecutionGateway:
    def __init__(self, client, authority, journal):
        self.client, self.authority, self.journal = client, authority, journal
        self.active = {}
        self.lock = asyncio.Lock()
        self.halted = False
        self.alert = ''
        self.maker_exits = {}
        self.maker_exit_stats = {'placed': 0, 'fallback': 0, 'filled': 0}

    async def submit(self, intent, token, snapshot_id='', slow_brain=None):
        """Submit one intent. snapshot lineage is journal-only, never gates here.

        slow_brain: optional dict {'snapshot_id', 'baseline', 'would_block',
        'reason'} recorded into ENTRY_INTENT for replay. Shadow only — the
        live decision path is unchanged by this change.
        """
        async with self.lock:
            if self.halted or not self.client.settings.armed or intent.mode != self.client.settings.mode:
                raise PermissionError('Gateway unavailable or advisory')
            if intent.symbol in self.active:
                raise PermissionError('One active intent per symbol')
            self.authority.consume(token, intent)
            client_id = 'gt-'+uuid.uuid4().hex[:28]
            order = TrackedOrder(intent, client_id, time.time(), current_stop=float(intent.stop))
            lineage = {'snapshot_id': snapshot_id or '',
                       'baseline': (slow_brain or {}).get('baseline', ''),
                       'would_block': bool((slow_brain or {}).get('would_block', False)),
                       'reason': (slow_brain or {}).get('reason', '')}
            self.journal.write('ENTRY_INTENT', {'client_id': client_id, **asdict(intent), **lineage})
            self.active[intent.symbol] = order
            try:
                response = await self.client.place_entry(intent, client_id)
            except BinanceError as error:
                if not error.uncertain:
                    self.active.pop(intent.symbol)
                    self.journal.write('ENTRY_REJECTED', {'client_id': client_id, 'code': error.code})
                    return 'REQUOTE' if error.code == -5022 else 'REJECTED'
                self.halted = True
                self.alert = 'Unknown entry result; reconcile by client ID'
                response = await self.client.order(intent.symbol, client_id)
            await self._apply(order, response)
            return client_id

    async def _apply(self, order, response):
        filled = float(response.get('executedQty', response.get('z', 0)))
        order.filled = max(order.filled, filled)
        average = float(response.get('avgPrice', response.get('ap', 0)))
        if average > 0:
            order.average = average
        order.status = response.get('status', response.get('X', order.status))
        if order.filled > 0 and not order.stop_id:
            await self._protect(order, order.current_stop)
        if order.status in ('CANCELED', 'EXPIRED', 'EXPIRED_IN_MATCH', 'REJECTED') and order.filled == 0:
            self.active.pop(order.intent.symbol, None)
        self.journal.write('ORDER_STATE', {'client_id': order.client_id, 'filled': order.filled, 'status': order.status, 'stop_id': order.stop_id})

    async def _protect(self, order, stop):
        stop_id = 'gs-'+uuid.uuid4().hex[:28]
        side = 'SELL' if order.intent.side == 'BUY' else 'BUY'
        old_id = order.stop_id
        self.journal.write('STOP_INTENT', {'client_id': stop_id, 'symbol': order.intent.symbol, 'stop': stop})
        try:
            try:
                result = await self.client.protect(order.intent.symbol, side, stop, stop_id)
            except BinanceError as error:
                if error.code == -4509:
                    # Demo venue rejects closePosition algo stops while the
                    # position is not yet registered. Transient: leave the
                    # order unstop-covered; the reconcile loop (~3s) retries
                    # as soon as the position exists.
                    self.journal.write('STOP_DEFERRED',
                                       {'client_id': stop_id, 'symbol': order.intent.symbol,
                                        'code': error.code})
                    return
                if not error.uncertain:
                    raise
                result = await self.client.algo(stop_id)
            if result.get('algoStatus', 'NEW') not in ('NEW', 'WORKING', 'ACCEPTED'):
                raise RuntimeError('Stop not active')
            order.stop_id, order.current_stop = stop_id, stop
            self.journal.write('STOP_ACK', {'client_id': stop_id, 'symbol': order.intent.symbol})
            # Ratchet without a gap: new protective stop is acknowledged first.
            if old_id:
                # The new stop is already acknowledged, so protection is live.
                # A stale ratchet cancel (venue already deleted the old algo)
                # must never discard the new protective stop.
                try:
                    await self.client.cancel_algo(old_id)
                except BinanceError as error:
                    if error.uncertain or error.code not in (-2011, -2013):
                        self.journal.write('STOP_OLD_CANCEL_FAILED',
                                           {'old': old_id, 'code': getattr(error, 'code', None)})
        except Exception:
            self.halted = True
            self.alert = 'Protective stop failure; cancel entry and flatten residual exposure'
            await self._cancel_entry(order)
            await self._flatten(order)
            raise RuntimeError(self.alert) from None

    async def on_user_event(self, event):
        async with self.lock:
            if event.get('e') == 'ORDER_TRADE_UPDATE':
                data = event['o']
                order = self.active.get(data['s'])
                if order and data.get('c') == order.client_id:
                    await self._apply(order, data)
            elif event.get('e') == 'ALGO_UPDATE':
                data = event.get('o', {})
                if data.get('X') in ('REJECTED', 'EXPIRED', 'CANCELED'):
                    stop_id = data.get('caid') or data.get('clientAlgoId')
                    order = next((o for o in self.active.values()
                                  if o.stop_id and o.stop_id == stop_id), None)
                    if order:
                        # A deliberate cancel (exit/ratchet/shutdown) also emits
                        # CANCELED; only a venue refusal is a protection failure.
                        # Either way the reconcile loop re-covers open positions.
                        order.stop_id = ''
                        if data.get('X') != 'CANCELED':
                            # A closedPosition algo auto-EXPIRES when the position
                            # it protects closed naturally — that is bookkeeping,
                            # not a protection failure. Halt only while exposure
                            # is actually uncovered.
                            positions = await self.client.positions(order.intent.symbol)
                            if any(float(p['positionAmt']) != 0 for p in positions):
                                self.halted = True
                                self.alert = 'Protective algo inactive; REST reconciliation required'

    async def _cancel_entry(self, order):
        if order.status in ('NEW', 'PARTIALLY_FILLED', 'SUBMITTING'):
            # Venue state can trail the cancel ack by a heartbeat; confirm
            # twice before declaring a protection failure, but never silently
            # give up: an uncancelled entry keeps unprotected exposure.
            for _ in range(2):
                try:
                    result = await self.client.cancel(order.intent.symbol, order.client_id)
                except BinanceError:
                    result = await self.client.order(order.intent.symbol, order.client_id)
                # Cancel acknowledgment can include a fill racing with cancellation.
                order.filled = max(order.filled, float(result.get('executedQty', 0)))
                order.status = result.get('status', order.status)
                if order.status not in ('NEW', 'PARTIALLY_FILLED'):
                    return
                await asyncio.sleep(0.25)
            self.halted = True
            raise RuntimeError(f'Entry cancellation not confirmed: '
                               f'{order.intent.symbol} {order.client_id} still {order.status}')

    async def _flatten(self, order):
        positions = await self.client.positions(order.intent.symbol)
        for position in positions:
            amount = dec(position['positionAmt'])
            if amount:
                client_id = 'gx-'+uuid.uuid4().hex[:28]
                self.journal.write('EXIT_INTENT', {'symbol': order.intent.symbol, 'client_id': client_id, 'quantity': str(abs(amount))})
                try:
                    await self.client.reduce_position(order.intent.symbol, 'SELL' if amount > 0 else 'BUY', abs(amount), client_id)
                except BinanceError as error:
                    if error.code == -2022 and not error.uncertain:
                        # Position closed underneath us (stop or peg filled
                        # between the snapshot and this order). Nothing left
                        # to reduce; reconcile owns the books.
                        continue
                    if error.uncertain:
                        self.halted = True
                        self.alert = 'Unknown exit; inspect venue position before retry'
                        await self.client.order(order.intent.symbol, client_id)
                    else:
                        raise
        order.closing = True

    async def _maker_exit(self, order, peg, grace):
        """Post-only reduce exit: join the passive side so closing costs 2 bps.

        Returns True when the peg is parked (sweep + reconcile own it from here),
        False when the venue refused it (would cross) and a market flatten must run."""
        positions = await self.client.positions(order.intent.symbol)
        amount = sum(dec(p['positionAmt']) for p in positions)
        if not amount:
            return True  # already flat; reconcile closes the books
        side = 'SELL' if amount > 0 else 'BUY'
        client_id = 'gm-'+uuid.uuid4().hex[:28]
        self.journal.write('MAKER_EXIT_INTENT', {'symbol': order.intent.symbol,
                                                 'client_id': client_id, 'price': str(peg),
                                                 'quantity': str(abs(amount))})
        try:
            await self.client.reduce_maker(order.intent.symbol, side, abs(amount), peg, client_id)
        except BinanceError as error:
            if error.code == -5022 and not error.uncertain:
                return False  # post-only would cross: immediate market fallback
            if error.code == -2022 and not error.uncertain:
                # ReduceOnly refused because the position is already gone: the
                # venue stop filled while the peg was being parked. Benign race;
                # the flatten below finds nothing to do and reconcile closes
                # the books. Previously this raised into the telemetry banner.
                return False
            if error.uncertain:
                self.halted = True
                self.alert = 'Unknown maker-exit result; inspect venue position before retry'
            raise
        order.closing = True
        self.maker_exits[order.intent.symbol] = {
            'client_id': client_id, 'placed_at': time.time(),
            'deadline': time.time()+grace, 'stop': order.current_stop,
            'position_side': order.intent.side}
        self.maker_exit_stats['placed'] += 1
        return True

    async def sweep_maker_exits(self, now, books=None):
        """Fallback sweep: a parked maker exit that aged out, or whose symbol
        traded back through its stop, is cancelled and market-flattened."""
        for symbol, pending in list(self.maker_exits.items()):
            positions = await self.client.positions(symbol)
            if not any(float(p['positionAmt']) != 0 for p in positions):
                # Fully filled: reconcile owns the POSITION_CLOSED bookkeeping.
                self.maker_exits.pop(symbol, None)
                self.maker_exit_stats['filled'] += 1
                continue
            book = (books or {}).get(symbol)
            sign = 1 if pending['position_side'] == 'BUY' else -1
            crossed = bool(pending['stop']) and book is not None and \
                sign*(book.mid-pending['stop']) <= 0
            if now < pending['deadline'] and not crossed:
                continue
            try:
                await self.client.cancel(symbol, pending['client_id'])
            except BinanceError:
                await self.client.order(symbol, pending['client_id'])
            positions = await self.client.positions(symbol)
            order = self.active.get(symbol)
            if any(float(p['positionAmt']) != 0 for p in positions) and order:
                await self._flatten(order)
                self.maker_exit_stats['fallback'] += 1
            else:
                # Flat after the cancel: the peg actually filled while the
                # cancel was in flight, so the exit cost 2 bps, not 4+spread.
                # Count it where P2 looks -- a filled peg booked as fallback
                # biases the fill-rate estimate downward.
                self.maker_exit_stats['filled'] += 1
            self.maker_exits.pop(symbol, None)

    async def exit(self, symbol, maker_peg=None, grace=8.0):
        async with self.lock:
            order = self.active.get(symbol)
            if order and not order.closing:
                await self._cancel_entry(order)
                if maker_peg and grace > 0 and await self._maker_exit(order, maker_peg, grace):
                    return
                if maker_peg:
                    self.maker_exit_stats['fallback'] += 1
                await self._flatten(order)

    async def ratchet(self, symbol, stop):
        async with self.lock:
            order = self.active.get(symbol)
            if order and order.filled and not order.closing and stop != order.current_stop:
                improved = stop > order.current_stop if order.intent.side == 'BUY' else stop < order.current_stop
                if improved:
                    await self._protect(order, stop)

    async def reconcile(self):
        async with self.lock:
            for symbol, order in list(self.active.items()):
                response = await self.client.order(symbol, order.client_id)
                await self._apply(order, response)
                if symbol not in self.active:
                    continue
                positions = await self.client.positions(symbol)
                amount = sum(abs(float(p['positionAmt'])) for p in positions)
                if amount > 0 and not order.closing:
                    algos = await self.client.open_algos(symbol)
                    if not any(a.get('clientAlgoId') == order.stop_id for a in algos):
                        order.stop_id = ''
                        await self._protect(order, order.current_stop)
                if order.filled and not amount:
                    await self._cancel_entry(order)
                    # Recheck after cancel so a racing partial fill cannot be orphaned.
                    positions = await self.client.positions(symbol)
                    if any(float(p['positionAmt']) != 0 for p in positions):
                        await self._flatten(order)
                        continue
                    if order.stop_id:
                        try:
                            await self.client.cancel_algo(order.stop_id)
                        except BinanceError as error:
                            if error.uncertain or error.code not in (-2011, -2013):
                                raise
                    self.active.pop(symbol, None)
                    self.journal.write('POSITION_CLOSED', {'symbol': symbol, 'client_id': order.client_id})
            # A protection halt is justified only while exposure is uncovered.
            # Once reconcile has verified every tracked fill is stop-covered
            # (or being closed), the halt is cleared and trading may resume.
            if self.halted and self.alert.startswith('Protective'):
                uncovered = [o.intent.symbol for o in self.active.values()
                             if o.filled > 0 and not o.stop_id and not o.closing]
                if not uncovered:
                    self.halted = False
                    self.alert = ''
                    self.journal.write('HALT_CLEARED', {'verified': 'reconcile coverage'})

    async def cancel_entries(self):
        async with self.lock:
            for order in list(self.active.values()):
                await self._cancel_entry(order)
                if order.filled and not order.stop_id:
                    await self._protect(order, order.current_stop)
                if not order.filled:
                    self.active.pop(order.intent.symbol, None)

    async def full_shutdown(self):
        """User-initiated stop: cancel entries, flatten fills, remove protection.

        Unlike the natural-exit path (which retains on-exchange stops for open
        positions), a full shutdown leaves the account completely flat with no
        working orders of any kind.
        """
        for symbol, pending in list(self.maker_exits.items()):
            try:
                await self.client.cancel(symbol, pending['client_id'])
            except BinanceError:
                await self.client.order(symbol, pending['client_id'])
            self.maker_exits.pop(symbol, None)
        for symbol in list(self.active):
            await self.exit(symbol)
        async with self.lock:
            for symbol, order in list(self.active.items()):
                if order.stop_id:
                    try:
                        await self.client.cancel_algo(order.stop_id)
                    except BinanceError as error:
                        if error.uncertain or error.code not in (-2011, -2013):
                            raise
                    order.stop_id = ''
                self.active.pop(symbol, None)
                self.journal.write('POSITION_CLOSED',
                                   {'symbol': symbol, 'client_id': order.client_id, 'reason': 'shutdown'})


@dataclass
class PairState:
    pair_id: str
    intent: PairIntent
    state: str = 'PENDING_LEG1'
    perp_filled: str = '0'
    perp_closed: str = '0'
    spot_owned: str = '0'
    entry_status: str = 'NEW'
    stop_id: str = ''
    opened: float = 0.0
    error: str = ''
    hedge_dispatch_ms: float = 0.0
    pending_spot: str = ''
    closed_at: float = 0.0
    pending_spot_sell: str = ''

    @property
    def short_quantity(self):
        return dec(self.perp_filled) - dec(self.perp_closed)


class PairExecutionGateway:
    """VESKA pair coordinator. Own fills only; unknown POSTs never retried.

    Recovery reconstructs owned inventory from order IDs and closes it before
    accepting new entries. Journals contain execution/account data only.
    """
    TERMINAL = {'FILLED', 'CANCELED', 'EXPIRED', 'EXPIRED_IN_MATCH', 'REJECTED'}

    def __init__(self, futures, spot, authority, journal, hedge_deadline=5.0):
        self.futures, self.spot = futures, spot
        self.authority, self.journal = authority, journal
        self.pairs = {}
        self.lock = asyncio.Lock()
        self.halted = False
        self.hedge_deadline = min(5.0, hedge_deadline)
        if self.hedge_deadline <= 0:
            raise ValueError('Invalid hedge deadline')

    def save(self, pair):
        self.journal.write('PAIR_STATE', asdict(pair))

    def check_armed(self, intent):
        if intent.mode == 'shadow' or any(not c.settings.armed or c.settings.mode != intent.mode
                                         for c in (self.futures, self.spot)):
            raise PermissionError('Both carry venues must be armed and match the intent')

    async def operation(self, pair, role, call, client=None, symbol=None, client_id=None):
        client_id = client_id or 'cp-' + uuid.uuid4().hex[:28]
        self.journal.write('PAIR_ORDER_INTENT', {'pair_id': pair.pair_id, 'role': role,
                           'client_id': client_id, 'symbol': symbol})
        try:
            result = await call(client_id)
        except BinanceError as error:
            if not error.uncertain:
                self.journal.write('PAIR_ORDER_REJECTED', {'client_id': client_id, 'code': error.code})
                raise
            # An unknown POST may have filled. Query this ID, never send another.
            try:
                result = await client.order(symbol, client_id)
            except Exception:
                self.halted = True
                pair.error = 'UNKNOWN_ORDER:' + client_id
                self.save(pair)
                raise RuntimeError(pair.error) from None
        self.journal.write('PAIR_ORDER_ACK', {'client_id': client_id, 'result': result})
        return result

    async def settle_spot_order(self, pair, row, client_id):
        if row.get('status') not in self.TERMINAL:
            row = await self.spot.cancel(pair.intent.spot_symbol, client_id)
            if row.get('status') not in self.TERMINAL:
                raise RuntimeError('Spot cancellation not confirmed')
        return await self.spot.fill_details(pair.intent.spot_symbol, row)

    async def submit(self, intent, token):
        async with self.lock:
            self.check_armed(intent)
            if self.halted or any(p.state != 'CLOSED' and p.intent.perp_symbol == intent.perp_symbol
                                  for p in self.pairs.values()):
                raise PermissionError('Carry halted or pair already active')
            self.authority.consume_pair(token, intent)
            pair = PairState('cp-' + uuid.uuid4().hex[:28], intent, opened=time.time())
            self.pairs[pair.pair_id] = pair
            self.save(pair)
            try:
                row = await self.operation(pair, 'PERP_ENTRY', lambda cid: self.futures.request(
                    'POST', '/fapi/v1/order', {'symbol': intent.perp_symbol, 'side': 'SELL',
                    'type': 'LIMIT', 'timeInForce': 'GTX', 'quantity': intent.quantity,
                    'price': intent.perp_price, 'positionSide': 'BOTH',
                    'newClientOrderId': cid}, signed=True), self.futures, intent.perp_symbol, pair.pair_id)
                await self.apply_entry(pair, row)
            except (Exception, asyncio.CancelledError) as error:
                pair.error = type(error).__name__
                self.save(pair)
                await self.unwind_locked(pair, 'ENTRY_FAILURE')
                raise
            return pair

    async def on_user_event(self, event):
        if event.get('e') != 'ORDER_TRADE_UPDATE':
            return
        row = event['o']
        async with self.lock:
            pair = self.pairs.get(row.get('c'))
            if pair and pair.state != 'CLOSED':
                await self.apply_entry(pair, row)
            elif row.get('x') == 'TRADE':
                # Stops, manual reductions, and ADL can remove one leg.
                for pair in self.pairs.values():
                    if pair.state != 'CLOSED' and pair.intent.perp_symbol == row.get('s'):
                        await self.reconcile_pair(pair)

    async def apply_entry(self, pair, row):
        received_at = time.monotonic()
        previous = dec(pair.perp_filled)
        cumulative = dec(row.get('executedQty', row.get('z', 0)))
        pair.perp_filled = str(max(previous, cumulative))
        pair.entry_status = row.get('status', row.get('X', pair.entry_status))
        self.save(pair)
        if cumulative > previous:
            try:
                await asyncio.wait_for(self.hedge(pair, received_at), self.hedge_deadline)
                if not pair.stop_id:
                    sid = 'cs-' + uuid.uuid4().hex[:28]
                    # Persist the ID first so recovery can find/cancel an unknown stop.
                    pair.stop_id = sid
                    self.save(pair)
                    try:
                        response = await self.futures.protect(pair.intent.perp_symbol, 'BUY', pair.intent.stop, sid)
                    except BinanceError as error:
                        if not error.uncertain:
                            raise
                        response = await self.futures.algo(sid)
                    if response.get('algoStatus', 'NEW') not in ('NEW', 'WORKING', 'ACCEPTED'):
                        raise RuntimeError('Pair protection rejected')
            except Exception as error:
                self.halted = True
                pair.error = 'HEDGE_OR_STOP:' + type(error).__name__
                self.save(pair)
                await self.unwind_locked(pair, pair.error)
                return
        if pair.short_quantity > 0:
            delta = pair.short_quantity - dec(pair.spot_owned)
            if delta != 0:
                self.halted = True
                await self.unwind_locked(pair, 'DELTA_MISMATCH')
                return
            pair.state = 'FULLY_HEDGED' if pair.entry_status in self.TERMINAL else 'PARTIAL_HEDGED'
        elif pair.entry_status in self.TERMINAL:
            pair.state = 'CLOSED'
            pair.closed_at = time.time()
        self.save(pair)

    async def hedge(self, pair, received_at):
        intent = pair.intent
        # Initial market order plus at most three bounded IOC attempts.
        for attempt in range(4):
            missing = pair.short_quantity - dec(pair.spot_owned)
            if missing <= 0:
                break
            quantity = gross_buy_quantity(missing, intent.spot_fee_rate, intent.fee_in_bnb, intent.spot_step)
            if quantity * dec(intent.spot_price) < dec(intent.spot_min_notional):
                raise RuntimeError('Partial fill below spot minimum notional')
            price = None
            if attempt:
                top = await self.spot.book_ticker(intent.spot_symbol)
                # IOC bounded at the executable REST ask; no unbounded chasing.
                price = top['askPrice']
            # Record the request before transmitting; fsync latency is measured.
            pair.pending_spot = 'cb-' + uuid.uuid4().hex[:28]
            self.save(pair)
            try:
                async def dispatch(cid):
                    if not attempt:
                        pair.hedge_dispatch_ms = (time.monotonic() - received_at) * 1000
                    return await self.spot.create_order(intent.spot_symbol, 'BUY', quantity, cid, price=price)
                row = await self.operation(pair, 'SPOT_BUY', dispatch, self.spot, intent.spot_symbol,
                    pair.pending_spot)
                row = await self.settle_spot_order(pair, row, pair.pending_spot)
                pair.spot_owned = str(dec(pair.spot_owned) + received_coins(row, intent.base_asset))
                pair.pending_spot = ''
                self.save(pair)
            except BinanceError as error:
                if error.uncertain:
                    raise
                pair.pending_spot = ''
                self.save(pair)
                continue
        # A base commission can leave dust or rounding can produce extra coins.
        # Exact neutrality is required: if grids prevent it, unwind, never mask it.
        if pair.short_quantity != dec(pair.spot_owned):
            raise RuntimeError('Unhedged residual after bounded hedge attempts')
        self.journal.write('PAIR_HEDGE_LATENCY', {'pair_id': pair.pair_id,
                           'dispatch_ms': pair.hedge_dispatch_ms,
                           'within_100ms': pair.hedge_dispatch_ms < 100})

    async def reconcile_pair(self, pair):
        rows = await self.futures.positions(pair.intent.perp_symbol)
        position = sum((dec(r['positionAmt']) for r in rows), dec(0))
        if position > 0 or -position > pair.short_quantity:
            self.halted = True
            raise RuntimeError('Unattributed futures exposure; manual reconciliation required')
        if -position != pair.short_quantity:
            pair.perp_closed = str(dec(pair.perp_filled) + position)
            self.save(pair)
            await self.unwind_locked(pair, 'EXTERNAL_REDUCTION_OR_ADL')

    async def unwind(self, pair_id, reason):
        async with self.lock:
            await self.unwind_locked(self.pairs[pair_id], reason)

    async def unwind_locked(self, pair, reason):
        if pair.state == 'CLOSED':
            return
        pair.state = 'UNWINDING'
        pair.error = reason
        self.save(pair)
        try:
            # Cancel first, and use its cumulative fill to catch cancel/fill races.
            try:
                row = await self.futures.cancel(pair.intent.perp_symbol, pair.pair_id)
            except BinanceError as error:
                if error.code == -2011 and dec(pair.perp_filled) == 0:
                    try:
                        row = await self.futures.order(pair.intent.perp_symbol, pair.pair_id)
                    except BinanceError as lookup:
                        if lookup.code != -2013:
                            raise
                        row = {'status': 'REJECTED', 'executedQty': '0'}
                else:
                    row = await self.futures.order(pair.intent.perp_symbol, pair.pair_id)
            if row.get('status') not in self.TERMINAL:
                raise RuntimeError('Entry cancellation not confirmed')
            pair.perp_filled = str(max(dec(pair.perp_filled), dec(row.get('executedQty', 0))))
            pair.entry_status = row['status']
            self.save(pair)
            # Repeated emergency attempts must use current exposure; a previous
            # reduce-only POST may have succeeded before a lost response.
            positions = await self.futures.positions(pair.intent.perp_symbol)
            actual = -sum((dec(p['positionAmt']) for p in positions), dec(0))
            if actual < 0 or actual > pair.short_quantity:
                raise RuntimeError('Unattributed exposure during unwind')
            pair.perp_closed = str(dec(pair.perp_filled) - actual)
            self.save(pair)
            if pair.short_quantity > 0:
                cid = 'cx-' + uuid.uuid4().hex[:28]
                row = await self.operation(pair, 'PERP_CLOSE', lambda ident: self.futures.reduce_position(
                    pair.intent.perp_symbol, 'BUY', pair.short_quantity, ident),
                    self.futures, pair.intent.perp_symbol, cid)
                if 'executedQty' not in row or row.get('status') != 'FILLED':
                    row = await self.futures.order(pair.intent.perp_symbol, cid)
                pair.perp_closed = str(dec(pair.perp_closed) + dec(row.get('executedQty', 0)))
                self.save(pair)
                if pair.short_quantity != 0:
                    raise RuntimeError('Futures emergency close incomplete')
            if pair.pending_spot:
                # Timeout may mean a successful buy with a lost response.
                try:
                    row = await self.spot.order(pair.intent.spot_symbol, pair.pending_spot)
                except BinanceError as error:
                    if error.code != -2013:
                        raise
                    # A single not-found is not proof that an in-flight POST failed.
                    raise RuntimeError('Unknown spot hedge; reconciliation required') from None
                row = await self.settle_spot_order(pair, row, pair.pending_spot)
                pair.spot_owned = str(dec(pair.spot_owned) + received_coins(row, pair.intent.base_asset))
                pair.pending_spot = ''
                self.save(pair)
            if dec(pair.spot_owned) > 0:
                quantity = quantize(pair.spot_owned, pair.intent.spot_step)
                if quantity != dec(pair.spot_owned):
                    raise RuntimeError('Spot dust requires explicit reconciliation')
                if pair.pending_spot_sell:
                    row = await self.spot.order(pair.intent.spot_symbol, pair.pending_spot_sell)
                else:
                    pair.pending_spot_sell = 'ce-' + uuid.uuid4().hex[:28]
                    self.save(pair)
                    try:
                        row = await self.operation(pair, 'SPOT_SELL', lambda cid: self.spot.create_order(
                            pair.intent.spot_symbol, 'SELL', quantity, cid), self.spot, pair.intent.spot_symbol,
                            pair.pending_spot_sell)
                    except BinanceError as error:
                        if not error.uncertain:
                            pair.pending_spot_sell = ''
                            self.save(pair)
                        raise
                row = await self.settle_spot_order(pair, row, pair.pending_spot_sell)
                base_fee = sum((dec(f['commission']) for f in row.get('fills', [])
                                if f['commissionAsset'] == pair.intent.base_asset), dec(0))
                pair.spot_owned = str(dec(pair.spot_owned) - dec(row.get('executedQty', 0)) - base_fee)
                pair.pending_spot_sell = ''
                self.save(pair)
                if dec(pair.spot_owned) != 0:
                    raise RuntimeError('Spot close incomplete')
            if pair.stop_id:
                try:
                    await self.futures.cancel_algo(pair.stop_id)
                except BinanceError as error:
                    if error.uncertain or error.code not in (-2011, -2013):
                        raise
                pair.stop_id = ''
            pair.state = 'CLOSED'
            pair.closed_at = time.time()
            self.save(pair)
        except Exception:
            self.halted = True
            self.save(pair)
            raise

    async def recover(self):
        """Recover post-crash inventory, including POST-before-ACK crash windows."""
        events = [(kind, json.loads(payload)) for kind, payload in self.journal.db.execute(
            'SELECT kind,payload FROM events ORDER BY id')]
        operations, rejected = [], set()
        for kind, data in events:
            if kind == 'PAIR_STATE':
                data['intent'] = PairIntent(**data['intent'])
                self.pairs[data['pair_id']] = PairState(**data)
            elif kind == 'PAIR_ORDER_INTENT':
                operations.append(data)
            elif kind == 'PAIR_ORDER_REJECTED':
                rejected.add(data['client_id'])
        for pair in self.pairs.values():
            if pair.state == 'CLOSED':
                continue
            self.check_armed(pair.intent)
            pair.perp_filled = pair.perp_closed = pair.spot_owned = '0'
            for op in operations:
                if op['pair_id'] != pair.pair_id or op['client_id'] in rejected:
                    continue
                client = self.spot if op['role'].startswith('SPOT') else self.futures
                # Missing/expired exchange history is a hard block, not zero fills.
                row = await client.order(op['symbol'], op['client_id'])
                q = dec(row.get('executedQty', 0))
                if op['role'] == 'PERP_ENTRY':
                    pair.perp_filled = str(q)
                elif op['role'] == 'PERP_CLOSE':
                    pair.perp_closed = str(dec(pair.perp_closed) + q)
                else:
                    row = await self.spot.fill_details(op['symbol'], row)
                    net = received_coins(row, pair.intent.base_asset)
                    if op['role'] == 'SPOT_BUY':
                        pair.spot_owned = str(dec(pair.spot_owned) + net)
                    else:
                        pair.spot_owned = str(dec(pair.spot_owned) - (2 * q - net))
            rows = await self.futures.positions(pair.intent.perp_symbol)
            actual = -sum((dec(r['positionAmt']) for r in rows), dec(0))
            if actual < 0 or actual > pair.short_quantity:
                raise RuntimeError('Recovery found unattributed futures exposure')
            pair.perp_closed = str(dec(pair.perp_filled) - actual)
            pair.pending_spot = ''  # reconstructed from the complete operation list
            pair.pending_spot_sell = ''
            self.save(pair)
            await self.unwind_locked(pair, 'RESTART_RECOVERY')

    async def shutdown(self):
        self.halted = True
        failures = []
        for pair in list(self.pairs.values()):
            if pair.state != 'CLOSED':
                try:
                    await self.unwind(pair.pair_id, 'SHUTDOWN')
                except Exception as error:
                    failures.append((pair.pair_id, type(error).__name__))
        if failures:
            raise RuntimeError('Unresolved pair exits: ' + str(failures))
