"""One-click: snapshot -> pick highest-edge channel -> act -> exit within 15 minutes.

    python scripts/run_alpha_instant.py                     shadow (default, no orders)
    python scripts/run_alpha_instant.py --mode testnet      hands an eligible carry to core.carry_runtime
    python scripts/run_alpha_instant.py --snapshot-file f   offline replay of a saved snapshot

Phases (hard wall-clock cap --max-minutes <= 15, default 15):
  1. WARM-UP  REST snapshot of Binance (+ Bybit) funding / books / OI / history.
              No L2 recording. Typical 5-30 s.
  2. RANK     core.instant_alpha.rank_opportunities + JEV System-1 score.
              Executable only when gross > 3 x all-in cost (score >= 8.5).
  3. ACT      testnet + eligible carry + two-leg capital gate LIVE  ->  launch
              core.carry_runtime for the chosen symbols (it re-checks every
              gate itself and HOLDS the pair: carry earns over 7-30 days;
              closing at minute 15 would pay the full cost for no funding).
              Otherwise: LIQUIDATION_SHOCK shadow on !forceOrder@arr until the cap.
  4. EXIT     report data/instant/<utc>.json. Nothing found -> exit 2 at once.

Mainnet is never launched from here (RELEASES.md G2: --arm/--confirm are typed
by a human each session); the exact command is printed instead.
Exit codes: 0 acted/reported · 2 NO_OPPORTUNITY · 3 CAPITAL_GATE · 4 NETWORK.
"""
import argparse
import asyncio
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.funding_carry import BOOK_EQUITY_USDT, two_leg_capital_gate  # noqa: E402
from core.instant_alpha import (LiquidationWindow, ShockShadow, binance_snapshot,  # noqa: E402
                                bybit_snapshot, parse_force_order, rank_opportunities)
from core.jev_gatekeeper import JevGatekeeper  # noqa: E402

MAX_MINUTES = 15.0
WS_URL = 'wss://fstream.binance.com/stream?streams=!forceOrder@arr/!markPrice@arr@1s'


def arguments(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--mode', choices=('shadow', 'testnet', 'mainnet'), default='shadow')
    ap.add_argument('--max-minutes', type=float, default=MAX_MINUTES)
    ap.add_argument('--equity', type=float, default=BOOK_EQUITY_USDT,
                    help='Two-venue equity in USDT used by the capital gate')
    ap.add_argument('--notional', type=float, default=10.0, help='USDT per carry pair')
    ap.add_argument('--horizon-days', type=float, default=7.0)
    ap.add_argument('--no-bybit', action='store_true')
    ap.add_argument('--no-liquidation', action='store_true')
    ap.add_argument('--flush', action='store_true',
                    help='Micro-capital (< 10 USDT two-leg minimum): spend the remaining window on the '
                         'DOGE 1m flush shadow (research.flush_shadow, WebSocket) instead of the '
                         'liquidation-shock shadow. Shadow only, no orders.')
    ap.add_argument('--jev', action='store_true',
                    help='Second-opinion veto from JEV/TypeSafe System-1 (needs TYPESAFE_API_KEY); can only lower scores')
    ap.add_argument('--snapshot-file', type=Path, help='Offline: JSON {"binance": {...}, "bybit": {...}, "now": t}')
    ap.add_argument('--out', type=Path, default=ROOT / 'data' / 'instant')
    args = ap.parse_args(argv)
    if not 0 < args.max_minutes <= MAX_MINUTES:
        ap.error(f'--max-minutes must be in (0, {MAX_MINUTES:g}]')
    if not 7 <= args.horizon_days <= 30:
        ap.error('--horizon-days must be within 7-30 (carry needs settlements to repay costs)')
    if args.equity < 0 or args.notional <= 0:
        ap.error('invalid capital')
    return args


def snapshot(args):
    if args.snapshot_file:
        data = json.loads(args.snapshot_file.read_text(encoding='utf-8'))
        return data.get('binance', {}), data.get('bybit', {}), float(data.get('now', time.time()))
    binance = binance_snapshot()
    bybit = {}
    if not args.no_bybit:
        try:
            bybit = bybit_snapshot()
        except Exception as exc:          # Bybit is optional evidence, never a blocker
            print(f'[warm-up] Bybit unavailable: {type(exc).__name__}')
    return binance, bybit, time.time()


async def liquidation_shadow(deadline, report):
    import websockets
    window, shadow, marks = LiquidationWindow(), ShockShadow(), {}
    retries = 0
    while time.monotonic() < deadline and retries < 3:
        try:
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20, open_timeout=10) as ws:
                retries = 0
                while time.monotonic() < deadline:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=max(1.0, min(15.0, deadline - time.monotonic())))
                    except asyncio.TimeoutError:
                        continue
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue
                    stream = msg.get('stream', '')
                    now = time.time()
                    if 'markPrice' in stream:
                        for m in msg.get('data', []):
                            try:
                                marks[m['s']] = float(m['p'])
                            except (KeyError, TypeError, ValueError):
                                continue
                        for s, gross in shadow.resolve(marks, now):
                            print(f'  [shock] {s.symbol} fade {s.fade_side} -> gross {gross:+.2f} bps')
                    elif 'forceOrder' in stream:
                        ev = parse_force_order(msg)
                        if ev and ev[0]:
                            shock = window.add(*ev, mark=marks.get(ev[0]))
                            if shock:
                                shadow.open_shock(shock)
                                print(f'  [shock] {shock.symbol} {shock.notional_usdt/1e3:.0f}k USDT '
                                      f'{shock.liquidated_side}-liquidations -> shadow fade {shock.fade_side}')
                    if shadow.gate.verdict is not None and shadow.gate.verdict.stop:
                        break
        except (OSError, asyncio.TimeoutError, websockets.WebSocketException) as exc:
            # asyncio.TimeoutError is NOT an OSError on Python 3.10: a slow handshake
            # (open_timeout) used to escape here and kill the run with a traceback.
            retries += 1
            if time.monotonic() >= deadline or retries >= 3:
                print(f'  [shock] WebSocket stream closed: {type(exc).__name__}')
                break
            print(f'  [shock] reconnect ({retries}/3): {type(exc).__name__}')
            await asyncio.sleep(2)
    report['liquidation_shock'] = shadow.summary()


def main(argv=None):
    args = arguments(argv)
    t0 = time.monotonic()
    deadline = t0 + args.max_minutes * 60
    report = {'utc': datetime.now(timezone.utc).isoformat(), 'mode': args.mode,
              'max_minutes': args.max_minutes, 'equity_usdt': args.equity}
    gk = JevGatekeeper()
    if args.jev:
        from core.jev_typesafe_backend import load_client, make_backend
        client = load_client()
        if client is None:
            print('[jev] TypeSafe SDK/API key unavailable -> deterministic System-1 only')
            report['jev'] = 'UNAVAILABLE'
        else:
            gk = JevGatekeeper(backend=make_backend(client), budget_ns=2_000_000_000)
            report['jev'] = 'VETO_ONLY_2S_BUDGET'
    try:
        binance, bybit, now = snapshot(args)
    except Exception as exc:
        report.update(status='NETWORK_UNAVAILABLE', error=f'{type(exc).__name__}: {exc}')
        return finish(args, report, 4)
    report['warmup_seconds'] = round(time.monotonic() - t0, 2)
    ranked = rank_opportunities(binance, bybit, now=now, horizon_days=args.horizon_days, gatekeeper=gk)
    report['opportunities'] = [o.as_dict() for o in ranked[:25]]
    report['system1_latency'] = gk.latency()
    best = next((o for o in ranked if o.executable), None)
    print(f'[warm-up] {report["warmup_seconds"]}s · {len(binance)} Binance / {len(bybit)} Bybit symbols')
    for o in ranked[:10]:
        print(f'  {o.channel:17} {o.symbol:14} G {o.gross_bps:8.2f} C {o.cost_bps:6.2f} '
              f'G/C {o.ratio:5.2f} score {o.score:4.1f} {"EXEC" if o.executable else "----"} {o.reason}')
    if best is not None:
        report['selected'] = best.as_dict()
        gate = two_leg_capital_gate(args.equity, args.notional, args.mode)
        report['capital_gate'] = {'mode': gate.mode, 'allowed': gate.allowed, 'reason': gate.reason}
        symbols = [o.symbol for o in ranked if o.executable][:8]
        if args.mode == 'mainnet':
            report['status'] = 'MAINNET_REQUIRES_HUMAN'
            report['command'] = (f'python -m core.carry_runtime --carry --mode mainnet --symbols {" ".join(symbols)} '
                                 f'--notional {args.notional:g} --arm --confirm "I UNDERSTAND SPOT PERP MAINNET RISK"')
            print('[act] Mainnet khong tu dong. Lenh cho nguoi van hanh:\n  ' + report['command'])
            return finish(args, report, 0)
        if args.mode == 'testnet' and gate.allowed:
            report['status'] = 'CARRY_HANDOFF_TESTNET'
            finish(args, report, 0)
            cmd = [sys.executable, '-m', 'core.carry_runtime', '--carry', '--mode', 'testnet',
                   '--symbols', *symbols, '--notional', f'{args.notional:g}',
                   '--horizon-days', f'{args.horizon_days:g}']
            print('[act] ' + ' '.join(cmd))
            return subprocess.call(cmd, cwd=str(ROOT))
        if gate.mode == 'RESEARCH_ONLY':
            report['status'] = 'CAPITAL_GATE'
            print(f'[act] Capital gate: {gate.reason} -> plan recorded, no order')
            if args.no_liquidation:
                return finish(args, report, 3)
        else:
            report['status'] = 'SHADOW_PLAN'
            print(f'[act] SHADOW: {best.channel} {best.symbol} net {best.net_bps:.1f} bps over '
                  f'{args.horizon_days:g}d ({gate.reason})')
    else:
        report['status'] = 'NO_CARRY_OPPORTUNITY'
    if args.flush and not args.snapshot_file and time.monotonic() < deadline:
        from research.flush_shadow import run_shadow_loop
        secs = max(1, int(deadline - time.monotonic()))
        print(f'[flush] DOGE 1m flush shadow for {secs}s (single-leg, fits {args.equity:g} USDT)...')
        engine = None
        try:
            engine = asyncio.run(run_shadow_loop(seconds=secs))
        except KeyboardInterrupt:
            print('[flush] stopped by user')
        if engine is not None:
            report['flush_shadow'] = {k: {f: v for f, v in st.items() if f != 'name'}
                                      for k, st in ((n, b.get_stats()) for n, b in engine.shadow_branches.items())}
            report['flush_equity_usdt'] = round(engine.equity, 6)
        report['status'] = report.get('status', 'NO_CARRY_OPPORTUNITY') + '+FLUSH_SHADOW'
        return finish(args, report, 0)
    if not args.no_liquidation and not args.snapshot_file and time.monotonic() < deadline:
        print(f'[shock] Liquidation-shock shadow until the {args.max_minutes:g}-minute cap...')
        try:
            asyncio.run(liquidation_shadow(deadline, report))
        except KeyboardInterrupt:
            print('[shock] stopped by user')
    if best is None and not (report.get('liquidation_shock') or {}).get('live_permitted'):
        report['status'] = 'NO_OPPORTUNITY'
        return finish(args, report, 2)
    return finish(args, report, 0)


def finish(args, report, code):
    report['exit_code'] = code
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + f'-{args.mode}.json')
    path.write_text(json.dumps(report, indent=1, allow_nan=False, default=str), encoding='utf-8')
    print(f'[exit {code}] {report.get("status")} -> {path}')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
