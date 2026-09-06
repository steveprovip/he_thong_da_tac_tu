"""Offline pre-flight for the testnet session: no network, no credentials printed.

Answers the questions a live run would otherwise answer the expensive way:
does sizing clear the venue minimum at the current wallet, do the consensus
gates sit where the policy says, does every planned entry carry a stop, and is
mainnet still advisory. Run it before `run_binance_testnet.py`.
"""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import Settings
from core.barrier_planner import BarrierPlanner
from core.consensus_engine import ConsensusEngine, TokenAuthority, Vote, WEIGHTS
from core.models import Filters, Intent, dec
from core.position_sizer import PositionSizer

# BTCUSDT USDⓈ-M perpetual filters; MIN_NOTIONAL differs between venues.
PROFILES = {'testnet': dict(tick='0.10', step='0.001', min_qty='0.001', max_qty='1000', min_notional='100'),
            'mainnet': dict(tick='0.10', step='0.001', min_qty='0.001', max_qty='1000', min_notional='5')}


def filters(profile):
    p = PROFILES[profile]
    return Filters(dec(p['tick']), dec(p['step']), dec(p['min_qty']), dec(p['max_qty']), dec(p['min_notional']))


def check(label, ok, detail=''):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")
    return ok


def main(equity, price, atr):
    failures = 0
    print(f'GROKTOPUS pre-flight · equity={equity:,.2f} USDT · BTCUSDT ref={price:,.2f} · ATR={atr:.2f}\n')

    print('1 / ENVIRONMENT')
    testnet = Settings.load('testnet')
    failures += not check('Testnet endpoints allowlisted', testnet.rest.endswith(('binancefuture.com', 'binance.com')),
                          f'{testnet.rest} · {testnet.ws}')
    failures += not check('Testnet credentials present', bool(testnet.api_key and testnet.secret),
                          f'key {len(testnet.api_key)} chars, secret {len(testnet.secret)} chars (values never printed)')
    failures += not check('Testnet armed for virtual orders', testnet.armed)
    failures += not check('Mainnet advisory without --arm', not Settings.load('mainnet').armed)
    failures += not check('Mainnet arms only on explicit flag', Settings.load('mainnet', arm=True).armed)
    failures += not check('Telemetry bound to loopback', testnet.host in ('127.0.0.1', 'localhost'),
                          f'{testnet.host}:{testnet.port}')

    print('\n2 / SIZING (no SIZE_ZERO at this wallet)')
    sizer = PositionSizer()
    planner = BarrierPlanner()
    plan = planner.plan('BUY', price, atr, float(filters('testnet').tick), 0.0)
    for profile in ('testnet', 'mainnet'):
        f = filters(profile)
        cold = sizer.size(equity, 0.0, price, plan.stop, f, bootstrap=False)
        boot = sizer.size(equity, 0.0, price, plan.stop, f, bootstrap=True, available=equity)
        failures += not check(f'{profile}: uncalibrated run refuses to trade', cold.quantity == '0',
                              cold.reason)
        ok = boot.quantity != '0'
        detail = f'{boot.quantity} BTC ≈ {boot.notional:,.2f} USDT' if ok else boot.reason
        if profile == 'testnet':
            failures += not check(f'{profile}: --bootstrap produces a live order size', ok, detail)
        else:
            check(f'{profile}: --bootstrap size (informational)', True, detail)

    print('\n3 / PROTECTION (no NO_STOP intent can be built)')
    f = filters('testnet')
    size = sizer.size(equity, 0.0, price, plan.stop, f, bootstrap=True, available=equity)
    intent = Intent('BTCUSDT', 'BUY', size.quantity, format(dec(price), 'f'),
                    format(dec(plan.stop), 'f'), format(dec(plan.take_profit), 'f'), 300.0, 'testnet')
    failures += not check('Long bracket ordered stop < entry < take-profit',
                          float(intent.stop) < float(intent.price) < float(intent.take_profit),
                          f'{intent.stop} < {intent.price} < {intent.take_profit}')
    short = planner.plan('SELL', price, atr, float(f.tick), 0.0)
    failures += not check('Short bracket ordered take-profit < entry < stop',
                          short.take_profit < price < short.stop,
                          f'{short.take_profit} < {price} < {short.stop}')
    try:
        Intent('BTCUSDT', 'BUY', size.quantity, format(dec(price), 'f'), '0', format(dec(plan.take_profit), 'f'), 300.0, 'testnet')
        failures += not check('Stopless intent is rejected at construction', False, 'accepted a zero stop')
    except ValueError:
        failures += not check('Stopless intent is rejected at construction', True)

    print('\n4 / CONSENSUS GATES')
    engine = ConsensusEngine()
    unanimous = engine.evaluate([Vote(n, 'BUY', 1, 'preflight') for n in WEIGHTS])
    failures += not check('Unanimous conviction authorizes', unanimous.allowed, f'score={unanimous.score:.3f}')
    vetoed = engine.evaluate([Vote(n, 'BUY', 1, 'preflight', veto=n == 'RUNE') for n in WEIGHTS])
    failures += not check('RUNE veto blocks a 100% book', vetoed.veto and not vetoed.allowed, vetoed.reason)
    # 0.60·0.58 + 0.40 = 0.748: past RUNE's 70% floor, short of the 76.8% gate.
    weak = engine.evaluate([Vote(n, 'BUY', .58 if n in ('NORO', 'LUMEN', 'TIDAL') else 1, 'preflight') for n in WEIGHTS])
    failures += not check('Score in the 70–76.8% band is withheld, not vetoed',
                          not weak.allowed and not weak.veto, f'score={weak.score:.3f} · {weak.reason}')
    authority = TokenAuthority()
    try:
        authority.issue(intent, weak)
        failures += not check('AuthToken refused below the gate', False, 'token issued below 76.8%')
    except PermissionError:
        failures += not check('AuthToken refused below the gate', True)
    token = authority.issue(intent, unanimous)
    authority.consume(token, intent)
    try:
        authority.consume(token, intent)
        failures += not check('AuthToken is single-use', False, 'replay accepted')
    except PermissionError:
        failures += not check('AuthToken is single-use', True)

    print(f"\n{'PRE-FLIGHT CLEAN' if not failures else f'{failures} CHECK(S) FAILED'}")
    return 1 if failures else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Offline testnet pre-flight')
    parser.add_argument('--equity', type=float, default=4930.88)
    parser.add_argument('--price', type=float, default=68000.0)
    parser.add_argument('--atr', type=float, default=95.0)
    raise SystemExit(main(*vars(parser.parse_args()).values()))
