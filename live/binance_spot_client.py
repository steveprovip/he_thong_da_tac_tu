"""Spot REST and authenticated account WebSocket; no market stream recorder."""
import asyncio
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import websockets

from core.models import dec, quantize
from live.binance_futures_client import BinanceFuturesClient, BinanceError


class BinanceSpotClient(BinanceFuturesClient):
    async def request(self, method, path, params=None, **kwargs):
        if method != 'GET' and not self.settings.armed:
            raise PermissionError('Spot mutations require an armed execution mode')
        return await super().request(method, path, params, **kwargs)

    async def sync_time(self):
        start = time.time() * 1000
        row = await self.request('GET', '/api/v3/time')
        self.offset_ms = row['serverTime'] - (start + time.time() * 1000) / 2

    async def exchange_info(self):
        return await self.request('GET', '/api/v3/exchangeInfo', weight=20)

    async def ticker(self, symbol):
        return await self.request('GET', '/api/v3/ticker/price', {'symbol': symbol}, weight=2)

    async def book_ticker(self, symbol):
        return await self.request('GET', '/api/v3/ticker/bookTicker', {'symbol': symbol}, weight=2)

    async def account(self):
        return await self.request('GET', '/api/v3/account', signed=True, weight=20)

    async def balances(self):
        return {r['asset']: dec(r['free']) for r in (await self.account())['balances']}

    async def commission(self, symbol):
        return await self.request('GET', '/api/v3/account/commission', {'symbol': symbol}, signed=True, weight=20)

    async def my_trades(self, symbol, order_id=None, start_ms=None, from_id=None):
        params = {'symbol': symbol, 'limit': 1000}
        if order_id is not None:
            params['orderId'] = order_id
        if start_ms is not None:
            params['startTime'] = int(start_ms)
        if from_id is not None:
            params['fromId'] = int(from_id)
        return await self.request('GET', '/api/v3/myTrades', params, signed=True, weight=20)

    async def order(self, symbol, client_id):
        return await self.request('GET', '/api/v3/order',
                                  {'symbol': symbol, 'origClientOrderId': client_id}, signed=True, weight=4)

    async def cancel(self, symbol, client_id):
        return await self.request('DELETE', '/api/v3/order',
                                  {'symbol': symbol, 'origClientOrderId': client_id}, signed=True)

    async def create_order(self, symbol, side, quantity, client_id, *, price=None):
        params = {'symbol': symbol, 'side': side, 'quantity': str(quantity),
                  'newClientOrderId': client_id, 'newOrderRespType': 'FULL',
                  'type': 'MARKET' if price is None else 'LIMIT'}
        if price is not None:
            params.update(price=str(price), timeInForce='IOC')
        return await self.request('POST', '/api/v3/order', params, signed=True)

    async def fill_details(self, symbol, response):
        if dec(response.get('executedQty', 0)) > 0 and not response.get('fills'):
            trades = await self.my_trades(symbol, response['orderId'])
            if sum((dec(t['qty']) for t in trades), dec(0)) != dec(response['executedQty']):
                raise RuntimeError('Spot trade history incomplete')
            return dict(response, fills=trades)
        return response

    async def user_events(self, callback, ready, disconnected, stop):
        """HMAC subscription, memory only; reconnection is fail-closed upstream."""
        while not stop.is_set():
            try:
                async with websockets.connect(self.settings.ws, ping_interval=20,
                                              close_timeout=2, max_queue=128) as ws:
                    params = {'apiKey': self.settings.api_key,
                              'timestamp': int(time.time() * 1000 + self.offset_ms)}
                    payload = urlencode(sorted(params.items()))
                    params['signature'] = hmac.new(self.settings.secret.encode(), payload.encode(),
                                                   hashlib.sha256).hexdigest()
                    await ws.send(json.dumps({'id': 'carry-user',
                                              'method': 'userDataStream.subscribe.signature', 'params': params}))
                    ack = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    if ack.get('status') != 200:
                        raise BinanceError('SPOT_WS_SUBSCRIPTION')
                    ready.set()
                    while not stop.is_set():
                        event = json.loads(await ws.recv())
                        if 'event' in event:
                            await callback(event['event'])
            except asyncio.CancelledError:
                raise
            except (OSError, ValueError, RuntimeError, asyncio.TimeoutError,
                    websockets.exceptions.WebSocketException):
                ready.clear()
                await disconnected('spot')
                await asyncio.sleep(1)


def received_coins(response, base_asset):
    """Use actual commission asset, including BNB-disabled/fallback fills."""
    gross = dec(response.get('executedQty', 0))
    fills = response.get('fills', [])
    if gross > 0 and sum((dec(f['qty']) for f in fills), dec(0)) != gross:
        raise ValueError('Missing or incomplete spot commissions')
    fee = sum((dec(f['commission']) for f in fills if f['commissionAsset'] == base_asset), dec(0))
    if fee < 0 or fee > gross:
        raise ValueError('Invalid base commission')
    return gross - fee


def gross_buy_quantity(net_quantity, fee_rate, fee_in_bnb, step):
    fee = dec(0) if fee_in_bnb else dec(fee_rate)
    if not 0 <= fee < 1:
        raise ValueError('Invalid spot commission rate')
    return quantize(dec(net_quantity) / (1 - fee), step, up=True)
