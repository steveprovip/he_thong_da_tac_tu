"""Async REST transport. Signed URLs and credentials are never logged."""
import asyncio
from collections import deque
import hashlib
import hmac
import time
from urllib.parse import urlencode
import aiohttp


class BinanceError(RuntimeError):
    def __init__(self, code, status=0, uncertain=False):
        self.code, self.status, self.uncertain = code, status, uncertain
        super().__init__(f'Binance code={code}, HTTP={status}, execution_uncertain={uncertain}')


class RateBudget:
    def __init__(self, limit=1200):
        self.limit = limit
        self.requests = deque()
        self.orders = deque()
        self.blocked_until = 0.0
        self.lock = asyncio.Lock()

    async def acquire(self, weight, order=False):
        async with self.lock:
            while True:
                now = time.monotonic()
                while self.requests and self.requests[0][0] <= now-60:
                    self.requests.popleft()
                while self.orders and self.orders[0] <= now-60:
                    self.orders.popleft()
                if now < self.blocked_until:
                    await asyncio.sleep(min(1, self.blocked_until-now))
                    continue
                if sum(w for _, w in self.requests)+weight > self.limit or (order and (len(self.orders) >= 100 or sum(t > now-10 for t in self.orders) >= 20)):
                    await asyncio.sleep(.25)
                    continue
                self.requests.append((now, weight))
                if order:
                    self.orders.append(now)
                return


class BinanceFuturesClient:
    def __init__(self, settings, session=None):
        self.settings = settings
        self.session = session
        self.owns_session = session is None
        self.offset_ms = 0
        self.budget = RateBudget()

    async def __aenter__(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10), trust_env=False)
        return self

    async def __aexit__(self, *args):
        if self.owns_session and self.session:
            await self.session.close()

    def signed_query(self, params):
        query = urlencode(params)
        signature = hmac.new(self.settings.secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        return query+'&signature='+signature

    async def request(self, method, path, params=None, signed=False, key=False, weight=1):
        mutation = method != 'GET'
        if mutation and self.settings.mode == 'mainnet' and not self.settings.armed:
            raise PermissionError('Mainnet is advisory; no mutation permitted')
        if (signed or key) and not self.settings.api_key:
            raise PermissionError('Missing API key')
        if signed and not self.settings.secret:
            raise PermissionError('Missing API secret')
        await self.budget.acquire(weight, order=mutation and 'order' in path.lower())
        data = dict(params or {})
        if signed:
            data.update(timestamp=int(time.time()*1000+self.offset_ms), recvWindow=5000)
        query = self.signed_query(data) if signed else urlencode(data)
        url = self.settings.rest+path+('?' + query if query else '')
        headers = {'X-MBX-APIKEY': self.settings.api_key} if signed or key else {}
        try:
            async with self.session.request(method, url, headers=headers, allow_redirects=False) as response:
                try:
                    body = await response.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError):
                    raise BinanceError('NON_JSON', response.status, mutation) from None
                used = int(response.headers.get('X-MBX-USED-WEIGHT-1M', '0'))
                if used >= 1000:
                    self.budget.blocked_until = time.monotonic()+max(1, 60-time.time()%60)
                if response.status in (418, 429):
                    self.budget.blocked_until = time.monotonic()+float(response.headers.get('Retry-After', 60))
                if response.status >= 300 or (isinstance(body, dict) and isinstance(body.get('code'), int) and body['code'] < 0):
                    code = body.get('code', 'HTTP_ERROR') if isinstance(body, dict) else 'HTTP_ERROR'
                    raise BinanceError(code, response.status, mutation and response.status >= 500)
                return body
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise BinanceError('TRANSPORT', 0, mutation) from None

    async def sync_time(self):
        start = time.time()*1000
        data = await self.request('GET', '/fapi/v1/time')
        self.offset_ms = data['serverTime']-(start+time.time()*1000)/2

    async def exchange_info(self):
        return await self.request('GET', '/fapi/v1/exchangeInfo')

    async def depth(self, symbol):
        return await self.request('GET', '/fapi/v1/depth', {'symbol': symbol, 'limit': 5}, weight=2)

    async def account(self):
        return await self.request('GET', '/fapi/v3/account', signed=True, weight=5)

    async def positions(self, symbol=None):
        return await self.request('GET', '/fapi/v3/positionRisk', {'symbol': symbol} if symbol else {}, signed=True, weight=5)

    async def open_orders(self, symbol):
        return await self.request('GET', '/fapi/v1/openOrders', {'symbol': symbol}, signed=True)

    async def open_algos(self, symbol):
        return await self.request('GET', '/fapi/v1/openAlgoOrders', {'symbol': symbol}, signed=True)

    async def order(self, symbol, client_id):
        return await self.request('GET', '/fapi/v1/order', {'symbol': symbol, 'origClientOrderId': client_id}, signed=True)

    async def cancel(self, symbol, client_id):
        return await self.request('DELETE', '/fapi/v1/order', {'symbol': symbol, 'origClientOrderId': client_id}, signed=True)

    async def place_entry(self, intent, client_id):
        return await self.request('POST', '/fapi/v1/order', {'symbol': intent.symbol, 'side': intent.side,
            'type': 'LIMIT', 'timeInForce': 'GTX', 'positionSide': 'BOTH', 'quantity': intent.quantity,
            'price': intent.price, 'newClientOrderId': client_id}, signed=True)

    async def protect(self, symbol, side, stop, client_id):
        return await self.request('POST', '/fapi/v1/algoOrder', {'symbol': symbol, 'side': side,
            'type': 'STOP_MARKET', 'algoType': 'CONDITIONAL', 'triggerPrice': str(stop),
            'closePosition': 'true', 'workingType': 'CONTRACT_PRICE', 'positionSide': 'BOTH',
            'clientAlgoId': client_id}, signed=True)

    async def algo(self, client_id):
        return await self.request('GET', '/fapi/v1/algoOrder', {'clientAlgoId': client_id}, signed=True)

    async def cancel_algo(self, client_id):
        return await self.request('DELETE', '/fapi/v1/algoOrder', {'clientAlgoId': client_id}, signed=True)

    async def reduce_position(self, symbol, side, quantity, client_id):
        return await self.request('POST', '/fapi/v1/order', {'symbol': symbol, 'side': side,
            'type': 'MARKET', 'quantity': str(quantity), 'reduceOnly': 'true', 'positionSide': 'BOTH',
            'newClientOrderId': client_id}, signed=True)

    async def listen_key(self):
        return (await self.request('POST', '/fapi/v1/listenKey', key=True))['listenKey']

    async def keepalive(self):
        return await self.request('PUT', '/fapi/v1/listenKey', key=True)

    async def history(self, symbol):
        return await self.request('GET', '/fapi/v1/userTrades', {'symbol': symbol, 'limit': 200}, signed=True, weight=5)
