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
                if not error.uncertain:
                    raise
                result = await self.client.algo(stop_id)
            if result.get('algoStatus', 'NEW') not in ('NEW', 'WORKING', 'ACCEPTED'):
                raise RuntimeError('Stop not active')
            order.stop_id, order.current_stop = stop_id, stop
            self.journal.write('STOP_ACK', {'client_id': stop_id, 'symbol': order.intent.symbol})
            # Ratchet without a gap: new protective stop is acknowledged first.
            if old_id:
                await self.client.cancel_algo(old_id)
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
                    for order in list(self.active.values()):
                        if data.get('caid') == order.stop_id or data.get('clientAlgoId') == order.stop_id:
                            self.halted = True
                            self.alert = 'Protective algo inactive; REST reconciliation required'

    async def _cancel_entry(self, order):
        if order.status in ('NEW', 'PARTIALLY_FILLED', 'SUBMITTING'):
            try:
                result = await self.client.cancel(order.intent.symbol, order.client_id)
            except BinanceError:
                result = await self.client.order(order.intent.symbol, order.client_id)
            # Cancel acknowledgment can include a fill racing with cancellation.
            order.filled = max(order.filled, float(result.get('executedQty', 0)))
            order.status = result.get('status', order.status)
            if order.status in ('NEW', 'PARTIALLY_FILLED'):
                self.halted = True
                raise RuntimeError('Entry cancellation not confirmed')

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
                    if error.uncertain:
                        self.halted = True
                        self.alert = 'Unknown exit; inspect venue position before retry'
                        await self.client.order(order.intent.symbol, client_id)
                    else:
                        raise
        order.closing = True

    async def exit(self, symbol):
        async with self.lock:
            order = self.active.get(symbol)
            if order and not order.closing:
                await self._cancel_entry(order)
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

    async def cancel_entries(self):
        async with self.lock:
            for order in list(self.active.values()):
                await self._cancel_entry(order)
                if order.filled and not order.stop_id:
                    await self._protect(order, order.current_stop)
                if not order.filled:
                    self.active.pop(order.intent.symbol, None)
