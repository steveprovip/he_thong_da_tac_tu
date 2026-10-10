"""10-10 d multi-venue layer: BaseVenue contract, Binance wrapper, MEXC / Hyperliquid prototypes (offline only)."""
import asyncio
import hashlib
import hmac
import inspect
import json
from decimal import Decimal
from pathlib import Path

import pytest

from core.venues import BaseVenue, MinSizeError, PostOnlyRejected, VENUE_SPECS, VenueDisabled
from core.venues.binance import BinanceAdapter
from core.venues.hyperliquid import HyperliquidVenue, round_px, round_sz, wire
from core.venues.mexc import MexcVenue, mexc_symbol, sign

ROOT = Path(__file__).resolve().parents[1]
CALLS = ('place_post_only', 'cancel_order', 'position_view', 'set_disaster_stop')


def run(c):
    return asyncio.run(c)


def test_every_adapter_implements_the_four_async_calls():
    with pytest.raises(TypeError):
        BaseVenue()
    for cls in (BinanceAdapter, MexcVenue, HyperliquidVenue):
        for name in CALLS:
            assert inspect.iscoroutinefunction(getattr(cls, name)), (cls, name)
    assert set(VENUE_SPECS) >= {'binance', 'mexc', 'hyperliquid', 'bybit'}


def test_venues_are_not_wired_into_the_engine():
    """Prototype only: no trading module imports core.venues (the engine keeps its own Binance venue)."""
    for p in list((ROOT / 'core').glob('*.py')) + list((ROOT / 'live').glob('*.py')):
        assert 'core.venues' not in p.read_text(encoding='utf-8', errors='ignore'), p


# ---------------------------------------------------------------------------- Binance wrapper
class FakeBinance:
    def __init__(self, reject=False):
        self.calls, self.reject = [], reject

    async def limit_gtx(self, symbol, side, qty, price, cid):
        self.calls.append(('gtx', symbol, side, qty, price, cid))
        if self.reject:
            e = RuntimeError('would match')
            e.code = -5022
            raise e

    async def cancel(self, symbol, cid):
        self.calls.append(('cancel', symbol, cid))

    async def stop(self, symbol, side, stop, cid):
        self.calls.append(('stop', symbol, side, stop, cid))

    async def position_view(self, symbol):
        return {'amt': -0.5, 'entry': 100.0, 'mark': 99.0, 'upnl': 0.5, 'liq': 150.0, 'margin': 10.0}


def test_binance_adapter_delegates_and_maps_the_post_only_reject():
    v = FakeBinance()
    a = BinanceAdapter(v)
    ack = run(a.place_post_only('SOLUSDT', 'BUY', 0.5, 100.0, cid='x1'))
    assert ack == {'venue': 'binance', 'order_id': 'x1', 'status': 'NEW'} and v.calls[0] == ('gtx', 'SOLUSDT', 'BUY', '0.5', '100.0', 'x1')
    run(a.set_disaster_stop('SOLUSDT', 'BUY', 95.0, cid='s1'))
    assert v.calls[-1] == ('stop', 'SOLUSDT', 'SELL', '95.0', 's1')         # a long's stop sells
    run(a.cancel_order('SOLUSDT', 'x1'))
    assert run(a.position_view('SOLUSDT'))['amt'] == -0.5
    with pytest.raises(PostOnlyRejected):
        run(BinanceAdapter(FakeBinance(reject=True)).place_post_only('SOLUSDT', 'SELL', 0.5, 101.0))


# ---------------------------------------------------------------------------- MEXC prototype
def test_mexc_post_only_payload_contracts_and_signature():
    m = MexcVenue('KEY', 'SECRET', clock=lambda: 1_790_000_000.123)
    p = m.order_params('SOLUSDT', 'BUY', 0.55, 109.75)
    assert p == {'symbol': 'SOL_USDT', 'price': 109.75, 'vol': 5, 'side': 1, 'type': 2, 'openType': 2, 'leverage': 5}
    assert m.order_params('DOGEUSDT', 'SELL', 250, 0.0859)['vol'] == 2              # 100 DOGE per contract
    assert m.order_params('BTCUSDT', 'SELL', 0.001, 8e4, reduce_only=True)['side'] == 4   # close long
    assert m.order_params('BTCUSDT', 'BUY', 0.001, 8e4, reduce_only=True)['side'] == 2    # close short
    with pytest.raises(MinSizeError):
        m.order_params('DOGEUSDT', 'BUY', 99, 0.0859)                                   # < 1 contract
    method, url, h, body = m.request('POST', '/api/v1/private/order/submit', p)
    want = hmac.new(b'SECRET', ('KEY' + '1790000000123' + body).encode(), hashlib.sha256).hexdigest()
    assert h['Signature'] == want == sign('SECRET', 'KEY', '1790000000123', body) and h['Request-Time'] == '1790000000123'
    assert json.loads(body) == p and url.endswith('/order/submit')
    _, url, h, body = m.request('GET', '/api/v1/private/position/open_positions', {'symbol': 'SOL_USDT'})
    assert body is None and url.endswith('?symbol=SOL_USDT') and h['Signature'] == sign('SECRET', 'KEY', h['Request-Time'], 'symbol=SOL_USDT')
    assert mexc_symbol('DOGEUSDT') == 'DOGE_USDT'


def test_mexc_disaster_stop_and_no_send_without_transport():
    m = MexcVenue('K', 'S')
    s = m.stop_params('SOLUSDT', 'BUY', 104.0, 1.0)                     # long position -> sell when price <= 104
    assert (s['side'], s['triggerType'], s['orderType'], s['vol']) == (4, 2, 5, 10)
    s = m.stop_params('SOLUSDT', 'SELL', 115.0, 1.0)                    # short -> buy when price >= 115
    assert (s['side'], s['triggerType']) == (2, 1)
    for c in (m.place_post_only('SOLUSDT', 'BUY', 1.0, 100.0), m.cancel_order('SOLUSDT', 1),
              m.position_view('SOLUSDT'), m.set_disaster_stop('SOLUSDT', 'BUY', 95.0, qty=1.0)):
        with pytest.raises(VenueDisabled):
            run(c)


def test_mexc_with_a_fake_transport():
    sent = []

    async def transport(method, url, headers, body):
        sent.append((method, url, body))
        if 'open_positions' in url:
            return {'success': True, 'data': [{'positionType': 2, 'holdVol': 7, 'holdAvgPrice': 110.0, 'liquidatePrice': 150.0}]}
        return {'success': True, 'code': 0, 'data': 123}
    m = MexcVenue('K', 'S', transport=transport)
    assert run(m.place_post_only('SOLUSDT', 'SELL', 0.7, 110.0, cid='c1'))['order_id'] == 123
    assert json.loads(sent[0][2])['externalOid'] == 'c1'
    pv = run(m.position_view('SOLUSDT'))
    assert pv['amt'] == pytest.approx(-0.7) and pv['mark'] is None and pv['upnl'] is None   # unknown, not 0
    run(m.set_disaster_stop('SOLUSDT', 'SELL', 120.0))                  # qty read from the position
    assert json.loads(sent[-1][2])['vol'] == 7


# ---------------------------------------------------------------------------- Hyperliquid prototype
def test_hyperliquid_price_and_size_rules():
    assert round_px(82861.37, 5, 'BUY') == Decimal('82861') and round_px(82861.37, 5, 'SELL') == Decimal('82862')
    assert round_px(109.764, 2, 'BUY') == Decimal('109.76') and round_px(109.761, 2, 'SELL') == Decimal('109.77')
    assert round_px(0.0858973, 0, 'BUY') == Decimal('0.085897')        # 5 significant figures, 6 - 0 decimals
    assert round_px(123456.7, 5, 'SELL') == Decimal('123457')          # integers always valid
    assert round_sz(0.123456, 5) == Decimal('0.12345') and round_sz(117.9, 0) == Decimal('117')
    assert wire(Decimal('109.70')) == '109.7' and wire(Decimal('82861')) == '82861'


def test_hyperliquid_alo_order_min_notional_and_stop():
    h = HyperliquidVenue()
    o = h.order_wire('SOLUSDT', 'BUY', 0.5, 109.764)
    assert o == {'a': 5, 'b': True, 'p': '109.76', 's': '0.5', 'r': False, 't': {'limit': {'tif': 'Alo'}}}
    with pytest.raises(MinSizeError):
        h.order_wire('DOGEUSDT', 'BUY', 100, 0.0859)                    # 8.59 USD < 10 USD minimum
    s = h.stop_wire('SOLUSDT', 'BUY', 104.0, 0.5)                       # long -> reduce-only stop-market sell
    assert s['b'] is False and s['r'] is True and s['t'] == {'trigger': {'isMarket': True, 'triggerPx': '104', 'tpsl': 'sl'}}
    assert float(s['p']) < 104.0                                        # worst acceptable price below the trigger
    for c in (h.place_post_only('SOLUSDT', 'BUY', 0.5, 100.0), h.cancel_order('SOLUSDT', 1),
              h.position_view('SOLUSDT')):
        with pytest.raises(VenueDisabled):
            run(c)


def test_hyperliquid_with_a_fake_transport_and_signer():
    sent = []
    reply = {'status': 'ok', 'response': {'type': 'order', 'data': {'statuses': [{'resting': {'oid': 77}}]}}}

    async def transport(method, url, headers, body):
        sent.append((url, json.loads(body)))
        if url.endswith('/info'):
            return {'assetPositions': [{'position': {'coin': 'SOL', 'szi': '0.5', 'entryPx': '100.0',
                                                     'unrealizedPnl': '1.0', 'liquidationPx': None}}]}
        return reply
    h = HyperliquidVenue('0xabc', signer=lambda action, nonce: {'r': '0x1', 's': '0x2', 'v': 27}, transport=transport,
                         clock=lambda: 1_790_000_000.0)
    assert run(h.place_post_only('SOLUSDT', 'BUY', 0.5, 109.76))['order_id'] == 77
    url, body = sent[0]
    assert url.endswith('/exchange') and body['nonce'] == 1_790_000_000_000 and body['signature']['v'] == 27
    assert body['action']['orders'][0]['t'] == {'limit': {'tif': 'Alo'}}
    pv = run(h.position_view('SOLUSDT'))
    assert pv['amt'] == 0.5 and pv['liq'] is None
    reply['response']['data']['statuses'] = [{'error': 'Post only order would have immediately matched, bbo was 109.7@109.8'}]
    with pytest.raises(PostOnlyRejected):
        run(h.place_post_only('SOLUSDT', 'BUY', 0.5, 109.9))
