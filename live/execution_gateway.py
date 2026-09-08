"""VESKA: journal before submission; protect partial fills; never retry unknown POST."""
import asyncio
from dataclasses import asdict, dataclass
import json
import sqlite3
import time
import uuid
from core.models import Intent, dec
from live.binance_futures_client import BinanceError


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

    async def submit(self, intent, token):
        async with self.lock:
            if self.halted or not self.client.settings.armed or intent.mode != self.client.settings.mode:
                raise PermissionError('Gateway unavailable or advisory')
            if intent.symbol in self.active:
                raise PermissionError('One active intent per symbol')
            self.authority.consume(token, intent)
            client_id = 'gt-'+uuid.uuid4().hex[:28]
            order = TrackedOrder(intent, client_id, time.time(), current_stop=float(intent.stop))
            self.journal.write('ENTRY_INTENT', {'client_id': client_id, **asdict(intent)})
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
