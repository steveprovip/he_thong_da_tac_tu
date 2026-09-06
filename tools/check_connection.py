"""Read-only testnet diagnostics; prints no authentication material."""
import asyncio
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import websockets
from config.settings import ROOT, Settings
from live.binance_futures_client import BinanceFuturesClient, BinanceError
from live.futures_streams import stream_url


async def check():
    settings = Settings.load('testnet')
    result = {'environment': 'testnet', 'rest': settings.rest, 'ws': settings.ws}
    async with BinanceFuturesClient(settings) as client:
        await client.sync_time()
        result['time_offset_ms'] = round(client.offset_ms)
        info = await client.exchange_info()
        result['trading_symbols'] = sum(s['status'] == 'TRADING' for s in info['symbols'])
        try:
            account = await client.account()
            result['account'] = {k: account.get(k) for k in ('totalWalletBalance', 'totalMarginBalance', 'availableBalance', 'canTrade')}
            positions = await client.positions()
            result['open_positions'] = [{k:p[k] for k in ('symbol', 'positionAmt', 'entryPrice')} for p in positions if float(p['positionAmt'])]
            result['recent_btc_trade_count'] = len(await client.history('BTCUSDT'))
        except (BinanceError, PermissionError) as error:
            result['account_error'] = str(error)
        for category, stream in [('public', 'btcusdt@depth5@100ms'), ('market', 'btcusdt@aggTrade')]:
            try:
                async with websockets.connect(stream_url(settings.ws, category, [stream]), open_timeout=10, close_timeout=2) as ws:
                    event = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    result[category+'_stream'] = event.get('data', event).get('e')
            except Exception as error:
                result[category+'_stream_error'] = type(error).__name__
    target = ROOT/'data'/'connection-check.json'
    target.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    asyncio.run(check())
