import asyncio
import json
import urllib.error
import urllib.request
import pytest
from config.settings import Settings
from core.runtime import Runtime, arguments
from core.web_ui_server import Telemetry, start_server


def test_unsafe_cli_options_rejected():
    with pytest.raises(SystemExit):
        arguments('mainnet',['--bootstrap'])
    with pytest.raises(SystemExit):
        arguments('testnet',['--offline'])


def test_loopback_static_and_api_no_path_traversal():
    telemetry=Telemetry()
    telemetry.update(score=0,equity=100)
    server=start_server(telemetry,port=0)
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        state=json.load(urllib.request.urlopen(base+'/api/state'))
        assert state['score']==0 and state['equity']==100
        assert b'GROKTOPUS' in urllib.request.urlopen(base+'/').read()
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(base+'/.env.local')
        assert error.value.code==404
        request=urllib.request.Request(base+'/api/state',headers={'Host':'evil.test'})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        assert error.value.code==403
    finally:
        server.shutdown()
        server.server_close()


async def test_offline_paper_runtime_runs_without_network(monkeypatch,tmp_path):
    import core.runtime as module
    from unittest.mock import AsyncMock
    monkeypatch.setattr(module,'ROOT',tmp_path)
    forbidden=AsyncMock(side_effect=AssertionError('Offline called exchange'))
    monkeypatch.setattr(module.BinanceFuturesClient,'request',forbidden)
    args=arguments('paper',['--offline','--seconds','1','--symbols','BTCUSDT'])
    telemetry=Telemetry()
    engine=Runtime(Settings.load('paper',env={}),args,telemetry)
    await engine.run()
    snapshot=telemetry.snapshot()
    assert snapshot['mode']=='PAPER · SYNTHETIC'
    assert snapshot['markets'][0]['symbol']=='BTCUSDT'
    assert snapshot['equity']==10000
    forbidden.assert_not_awaited()
