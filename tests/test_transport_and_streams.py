import hashlib
import hmac
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock
import pytest
from config.settings import Settings
from core.models import Intent
from live.binance_futures_client import BinanceFuturesClient, BinanceError
from live.futures_streams import PartialBookTracker, stream_url


def test_environment_allowlist_and_arming():
    assert not Settings.load('mainnet',env={}).armed
    assert Settings.load('mainnet',arm=True,env={}).armed
    assert Settings.load('mainnet',env={'ARM_LIVE_EXECUTION':'True'}).armed
    with pytest.raises(ValueError):
        Settings.load('testnet',env={'BINANCE_TESTNET_BASE_URL':'https://fapi.binance.com'})
    with pytest.raises(ValueError):
        Settings.load('testnet',env={'BINANCE_TESTNET_BASE_URL':'https://testnet.binancefuture.com@evil.test'})


async def test_gtx_and_algo_contract():
    client=BinanceFuturesClient(Settings.load('testnet',env={}))
    client.request=AsyncMock(return_value={})
    intent=Intent('X','BUY','1','100','99','103',300,'testnet')
    await client.place_entry(intent,'entry-id')
    args=client.request.call_args.args
    assert args[1]=='/fapi/v1/order'
    assert args[2]['timeInForce']=='GTX' and args[2]['type']=='LIMIT'
    await client.protect('X','SELL','99','stop-id')
    args=client.request.call_args.args
    assert args[1]=='/fapi/v1/algoOrder'
    assert args[2]['triggerPrice']=='99' and args[2]['closePosition']=='true'
    assert 'quantity' not in args[2] and 'stopPrice' not in args[2]


def test_hmac_matches_reference():
    settings=Settings.load('testnet',env={'BINANCE_TESTNET_SECRET_KEY':'secret'})
    client=BinanceFuturesClient(settings)
    query=client.signed_query({'symbol':'BTCUSDT','timestamp':123})
    expected=hmac.new(b'secret',b'symbol=BTCUSDT&timestamp=123',hashlib.sha256).hexdigest()
    assert query.endswith('signature='+expected)
    assert 'secret' not in repr(settings)


def test_split_urls_and_gap_snapshot_replacement():
    base='wss://fstream.binance.com/ws'
    assert stream_url(base,'public',['btcusdt@depth5@100ms'])=='wss://fstream.binance.com/public/stream?streams=btcusdt@depth5@100ms'
    assert '/private/ws/key' in stream_url(base,'private',['key'])
    tracker=PartialBookTracker()
    event={'s':'X','u':10,'pu':9,'E':1000,'b':[['100','1']],'a':[['101','2']]}
    assert not tracker.parse(event)[1]
    assert tracker.parse(event)[0] is None
    event.update(u=12,pu=11,b=[['99','3']])
    book,gap=tracker.parse(event)
    assert gap and book.bids==((99.,3.),)


class Response:
    status=503
    headers={}
    async def __aenter__(self):
        return self
    async def __aexit__(self,*args):
        return False
    async def json(self,**kwargs):
        return {'code':-1000,'msg':'sensitive message must not leak'}


async def test_uncertain_post_error_is_sanitized():
    class Session:
        def request(self,*args,**kwargs):
            return Response()
    client=BinanceFuturesClient(Settings.load('testnet',env={}),Session())
    with pytest.raises(BinanceError) as exc:
        await client.request('POST','/fapi/v1/order')
    assert exc.value.uncertain and 'sensitive' not in str(exc.value)
