from dataclasses import dataclass, field
import os
import re
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


MICRO_CAPITAL_MODES = ('auto', 'on', 'off')


def micro_capital_env(env):
    """11-09 micro-capital flags from the environment, for the keys that are SET ({} when none is):
    MICRO_CAPITAL_MODE (auto | on | off) -> micro_capital, MICRO_CAPITAL_EQUITY_MAX (USDT, default 15) -> micro_equity_max,
    MICRO_IOC (off | shadow | on) -> micro_ioc, MICRO_CAPITAL_BANKROLL (USDT, the equity a forced run on a big wallet
    pretends to have, default 5) -> micro_bankroll, MICRO_CAPITAL_SYMBOLS (comma list of the tradable universe) ->
    micro_symbols.  A bad value raises, so a typo cannot silently leave the small-account guard off."""
    out = {}
    mode = str(env.get('MICRO_CAPITAL_MODE', '')).strip().lower()
    if mode:
        if mode not in MICRO_CAPITAL_MODES:
            raise ValueError('MICRO_CAPITAL_MODE must be auto, on or off')
        out['micro_capital'] = mode
    raw = str(env.get('MICRO_CAPITAL_EQUITY_MAX', '')).strip()
    if raw:
        try:
            limit = float(raw)
        except ValueError:
            raise ValueError('MICRO_CAPITAL_EQUITY_MAX must be a number') from None
        if not 0 < limit <= 1000:
            raise ValueError('MICRO_CAPITAL_EQUITY_MAX must be in (0, 1000] USDT')
        out['micro_equity_max'] = limit
    ioc = str(env.get('MICRO_IOC', '')).strip().lower()
    if ioc:
        if ioc not in ('off', 'shadow', 'on'):
            raise ValueError('MICRO_IOC must be off, shadow or on')
        out['micro_ioc'] = ioc
    policy = str(env.get('MICRO_EXIT_POLICY', '')).strip().lower()         # 11-10 chop-harvester exit policy
    if policy:
        if policy not in ('legacy', 'patient'):
            raise ValueError('MICRO_EXIT_POLICY must be legacy or patient')
        out['micro_exit_policy'] = policy
    harvester = str(env.get('MICRO_HARVESTER', '')).strip().lower()       # 11-09e chop lane of the dual mode
    if harvester:
        if harvester not in ('off', 'shadow', 'on'):
            raise ValueError('MICRO_HARVESTER must be off, shadow or on')
        if harvester == 'on':                 # 11-13: TP 0.15 / SL 0.15 needs 67.6 % wins; mainnet tape gave 45-47.5 %
            raise ValueError('MICRO_HARVESTER=on is retired (2026-11-13): the TP 0.15 / SL 0.15 % chop lane failed its '
                             'holdout (0 of 40 brackets positive); use shadow (markout logging) or off')
        out['micro_harvester'] = harvester
    gate = str(env.get('MICRO_NET_EV_GATE', '')).strip().lower()          # 11-13 net-expectancy entry gate
    if gate:
        if gate not in ('off', 'shadow', 'on'):
            raise ValueError('MICRO_NET_EV_GATE must be off, shadow or on')
        out['micro_net_ev_gate'] = gate
    runner = str(env.get('MICRO_SNIPER_RUNNER', '')).strip().lower()      # 11-10 (e) runner exits of high-conviction trend entries
    if runner:
        if runner not in ('off', 'on'):
            raise ValueError('MICRO_SNIPER_RUNNER must be off or on')
        out['micro_sniper_runner'] = runner
    banks = {k: str(env.get(k, '')).strip() for k in ('MICRO_CAPITAL_BANKROLL', 'MICRO_BANKROLL_DEMO')}   # 11-09f: same knob, 2 names
    for key, raw in banks.items():
        if raw:
            try:
                bank = float(raw)
            except ValueError:
                raise ValueError(f'{key} must be a number') from None
            if not 0 < bank <= 1000:
                raise ValueError(f'{key} must be in (0, 1000] USDT')
            if out.get('micro_bankroll', bank) != bank:
                raise ValueError('MICRO_CAPITAL_BANKROLL and MICRO_BANKROLL_DEMO disagree: set one')
            out['micro_bankroll'] = bank
    raw = str(env.get('MICRO_MAX_POSITIONS', '')).strip()           # 11-09f: concurrent micro positions (one per coin)
    if raw:
        if raw not in ('1', '2', '3'):
            raise ValueError('MICRO_MAX_POSITIONS must be 1, 2 or 3')
        out['micro_max_positions'] = int(raw)
    raw = str(env.get('MICRO_MAX_SAME_SIDE', '')).strip()           # 11-09g: same-side positions across the micro slots (0 = off)
    if raw:
        if raw not in ('0', '1', '2', '3'):
            raise ValueError('MICRO_MAX_SAME_SIDE must be 0, 1, 2 or 3')
        out['micro_max_same_side'] = int(raw)
    raw = str(env.get('MICRO_ACCEL_K', '')).strip()                 # 11-09f: lot growth per realized USDT (0 = off)
    if raw:
        try:
            k = float(raw)
        except ValueError:
            raise ValueError('MICRO_ACCEL_K must be a number') from None
        if not 0 <= k <= 10:
            raise ValueError('MICRO_ACCEL_K must be in [0, 10]')
        out['micro_accel_k'] = k
    raw = str(env.get('MICRO_CAPITAL_SYMBOLS', '')).strip().upper()
    if raw:
        syms = tuple(s.strip() for s in raw.split(',') if s.strip())
        if not syms or len(set(syms)) != len(syms) or not all(re.fullmatch(r'[A-Z0-9]{2,20}USDT', s) for s in syms):
            raise ValueError('MICRO_CAPITAL_SYMBOLS must be a comma list of distinct USDT symbols, e.g. DOGEUSDT,XRPUSDT')
        out['micro_symbols'] = syms
    raw = str(env.get('MICRO_OFI_MIN', '')).strip()                 # 11-11: dynamic OFI floor for micro trend entries
    if raw:
        try:
            ofi_floor = float(raw)
        except ValueError:
            raise ValueError('MICRO_OFI_MIN must be a number') from None
        if not 0.65 <= ofi_floor <= 1.0:      # 11-13 owner decision: the 0.65 floor may be raised, never loosened
            raise ValueError('MICRO_OFI_MIN must be in [0.65, 1]: loosening the OFI floor was rejected (2026-11-13)')
        out['micro_ofi_min'] = ofi_floor
    raw = str(env.get('MICRO_MIN_EDGE_BPS', '')).strip()            # 11-11: fee-gate edge floor for micro mode
    if raw:
        try:
            edge_floor = float(raw)
        except ValueError:
            raise ValueError('MICRO_MIN_EDGE_BPS must be a number') from None
        if not 0.0 <= edge_floor <= 50.0:
            raise ValueError('MICRO_MIN_EDGE_BPS must be in [0, 50]')
        out['scalp_min_edge_bps'] = edge_floor
    return out


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
    micro_capital_mode: str = 'auto'            # 11-09: auto = small-account rules while the wallet <= the limit below
    micro_capital_equity_max: float = 15.0

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
        micro = micro_capital_env(env)
        return cls(mode, rest, ws.removesuffix('/ws'), env.get(prefix+'API_KEY', ''),
                   env.get(prefix+'SECRET_KEY', ''), armed, host, int(env.get('WEB_UI_PORT', 8765)),
                   micro.get('micro_capital', 'auto'), micro.get('micro_equity_max', 15.0))
