"""KILL SWITCH: stop the bot and leave the venue completely flat.

Two phases, both idempotent -- safe to run twice:
  1. If the dashboard answers on 127.0.0.1:<port>, POST /api/shutdown so the
     engine performs its own graceful full flatten, then wait for it to exit.
  2. Connect to the venue REST and sweep whatever remains: cancel every
     working order and algo, close every position reduce-only.

Usage:
  python tools/shutdown_system.py                        # testnet
  python tools/shutdown_system.py --mode mainnet --arm   # live (explicit)
"""
import argparse
import asyncio
from pathlib import Path
import json
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import Settings
from live.binance_futures_client import BinanceFuturesClient
from live.flatten import flatten_venue


def health_ok(host, port):
    try:
        with urllib.request.urlopen(f'http://{host}:{port}/health', timeout=2) as response:
            return response.status == 200
    except OSError:
        return False
    except Exception:
        return False


def request_ui_shutdown(host, port):
    request = urllib.request.Request(
        f'http://{host}:{port}/api/shutdown',
        data=json.dumps({'confirm': 'SHUTDOWN'}).encode(),
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def wait_until_stopped(host, port, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not health_ok(host, port):
            return True
        time.sleep(1)
    return False


async def flatten(settings, journal_path):
    journal = None
    if journal_path.exists():
        from live.execution_gateway import Journal
        journal = Journal(journal_path)
    try:
        async with BinanceFuturesClient(settings) as client:
            return await flatten_venue(client, journal=journal)
    finally:
        if journal:
            journal.close()


def main():
    parser = argparse.ArgumentParser(description='Full-system shutdown: stop bot, flatten venue')
    parser.add_argument('--mode', choices=('testnet', 'mainnet', 'paper'), default='testnet')
    parser.add_argument('--arm', action='store_true',
                        help='Required for mainnet (flatten is risk-reducing but still a live mutation)')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if args.mode == 'mainnet' and not args.arm:
        parser.error('Mainnet flatten requires --arm')

    report = {'ui_shutdown': None, 'ui_stopped': None, 'venue_flatten': [], 'errors': []}
    if health_ok(args.host, args.port):
        try:
            report['ui_shutdown'] = request_ui_shutdown(args.host, args.port)
        except Exception as error:
            report['errors'].append(f'UI shutdown request: {error}')
        report['ui_stopped'] = wait_until_stopped(args.host, args.port)
        if report['ui_stopped'] is False:
            report['errors'].append('Engine did not exit in time; venue sweep still runs')
    else:
        report['ui_shutdown'] = 'dashboard not running'

    settings = Settings.load(args.mode, args.arm)
    try:
        report['venue_flatten'] = asyncio.run(flatten(settings, Path('data') / f'{args.mode}-journal.sqlite'))
    except Exception as error:
        report['errors'].append(f'Venue flatten: {error}')

    print(json.dumps(report, indent=2, default=str))
    return 1 if report['errors'] else 0


if __name__ == '__main__':
    sys.exit(main())