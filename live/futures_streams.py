"""Split market/public/private streams with bounded reconnect and explicit gaps."""
import asyncio
import json
import random
import time
import websockets
from core.models import Book


def stream_url(base, category, streams):
    if category not in ('public', 'market', 'private'):
        raise ValueError('Unknown stream category')
    base = base.rstrip('/').removesuffix('/ws')
    if category == 'private':
        return base+'/private/ws/'+streams[0]
    return base+'/'+category+'/stream?streams='+'/'.join(streams)


class PartialBookTracker:
    def __init__(self):
        self.last = {}

    def reset(self):
        self.last.clear()

    def parse(self, event):
        symbol = event['s']
        sequence = int(event['u'])
        old = self.last.get(symbol)
        if old is not None and sequence <= old:
            return None, False
        gap = old is not None and int(event.get('pu', old)) != old
        bids = tuple(sorted(((float(p), float(q)) for p, q in event['b'] if float(q) > 0), reverse=True))
        asks = tuple(sorted((float(p), float(q)) for p, q in event['a'] if float(q) > 0))
        book = Book(symbol, bids, asks, event.get('T', event['E'])/1000, sequence)
        self.last[symbol] = sequence
        # Partial depth messages are complete top-N snapshots, not full-book deltas.
        return book, gap


async def consume_stream(url, callback, on_disconnect, stop, label):
    backoff = .5
    while not stop.is_set():
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=3, max_queue=128, max_size=2**20) as ws:
                backoff = .5
                await callback({'e': '_CONNECTED', 'category': label})
                while not stop.is_set():
                    raw = await asyncio.wait_for(ws.recv(), timeout=None if label == 'private' else 15)
                    event = json.loads(raw)
                    event = event.get('data', event)
                    if event.get('e') == 'listenKeyExpired':
                        raise ConnectionError('User stream expired')
                    await callback(event)
        except asyncio.CancelledError:
            raise
        except (OSError, ValueError, ConnectionError, asyncio.TimeoutError, websockets.exceptions.WebSocketException):
            await on_disconnect(label)
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff+random.random()*.2)
            except asyncio.TimeoutError:
                backoff = min(20, backoff*2)


async def user_stream(client, callback, on_disconnect, stop):
    async def heartbeat():
        while not stop.is_set():
            await asyncio.sleep(25*60)
            await client.keepalive()

    while not stop.is_set():
        tasks = []
        try:
            key = await client.listen_key()
            # A reconnect obtains a fresh listenKey and forces REST reconciliation.
            async def disconnected(label):
                await on_disconnect(label)
                raise ConnectionError('Renew user stream')
            tasks = [asyncio.create_task(heartbeat()), asyncio.create_task(consume_stream(
                stream_url(client.settings.ws, 'private', [key]), callback, disconnected, stop, 'private'))]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except asyncio.CancelledError:
            raise
        except Exception:
            await on_disconnect('private')
            await asyncio.sleep(2)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
