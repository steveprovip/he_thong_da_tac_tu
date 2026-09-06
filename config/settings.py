from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def read_env(path=ROOT / '.env.local'):
    values = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    values.update(os.environ)
    return values


@dataclass(frozen=True)
class Settings:
    mode: str
    rest: str
    ws: str
    api_key: str = field(default='', repr=False)
    secret: str = field(default='', repr=False)
    armed: bool = False
    host: str = '127.0.0.1'
    port: int = 8765

    @classmethod
    def load(cls, mode='testnet', arm=False, env=None):
        if mode not in ('mainnet', 'testnet', 'paper'):
            raise ValueError('Unknown mode')
        env = read_env() if env is None else env
        venue = 'MAINNET' if mode == 'mainnet' else 'TESTNET'
        prefix = f'BINANCE_{venue}_'
        rest = env.get(prefix + 'BASE_URL', 'https://fapi.binance.com' if mode == 'mainnet' else 'https://demo-fapi.binance.com').rstrip('/')
        ws = env.get(prefix + 'WS_URL', 'wss://fstream.binance.com' if mode == 'mainnet' else 'wss://demo-fstream.binance.com').rstrip('/')
        allowed_rest = {'fapi.binance.com'} if mode == 'mainnet' else {'testnet.binancefuture.com', 'demo-fapi.binance.com'}
        allowed_ws = {'fstream.binance.com'} if mode == 'mainnet' else {'stream.binancefuture.com', 'demo-fstream.binance.com'}
        for address, scheme, hosts in ((rest, 'https', allowed_rest), (ws, 'wss', allowed_ws)):
            parts = urlsplit(address)
            if parts.scheme != scheme or parts.hostname not in hosts or parts.username or parts.password or parts.port or parts.query or parts.fragment:
                raise ValueError('Endpoint does not match selected environment')
        if urlsplit(rest).path or urlsplit(ws).path not in ('', '/ws'):
            raise ValueError('Expected Binance base endpoint')
        host = env.get('WEB_UI_HOST', '127.0.0.1')
        if host not in ('127.0.0.1', 'localhost'):
            raise ValueError('Telemetry must bind to loopback')
        armed = mode == 'testnet' or (mode == 'mainnet' and (arm or env.get('ARM_LIVE_EXECUTION', '').lower() == 'true'))
        return cls(mode, rest, ws.removesuffix('/ws'), env.get(prefix+'API_KEY', ''),
                   env.get(prefix+'SECRET_KEY', ''), armed, host, int(env.get('WEB_UI_PORT', 8765)))
