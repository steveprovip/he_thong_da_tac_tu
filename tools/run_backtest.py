"""Run the swarm through hftbacktest with queue position and latency modelled.

    python tools/run_backtest.py --synthetic --minutes 30
    python tools/run_backtest.py --data data/btcusdt_20260901.npz --symbol BTCUSDT

The synthetic feed proves the pipeline offline; only real Binance USDⓈ-M data
produces a number worth acting on, and the report says which one it ran on.
"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from hftbacktest import BacktestAsset, HashMapMarketDepthBacktest

from config.settings import ROOT
from research.bridge import BacktestConfig, SwarmBacktest
from research.synthetic import synthetic_feed

MS = 1_000_000

QUEUE_MODELS = {
    'power3': lambda asset: asset.power_prob_queue_model(3.0),
    'power2': lambda asset: asset.power_prob_queue_model(2.0),
    'log': lambda asset: asset.log_prob_queue_model(),
    'riskadverse': lambda asset: asset.risk_adverse_queue_model(),
}


def build_asset(data, config, queue_model, feed_latency_ms, order_latency_ms, latency_file=None):
    asset = BacktestAsset().data([data]).linear_asset(1.0)
    asset = QUEUE_MODELS[queue_model](asset)
    if latency_file:
        # Latency measured on the operator's own machine beats any guess.
        asset = asset.intp_order_latency([np.load(latency_file)])
    else:
        asset = asset.constant_latency(int(feed_latency_ms * MS), int(order_latency_ms * MS))
    return (asset
            .trading_value_fee_model(config.maker_fee, config.taker_fee)
            .tick_size(config.tick_size)
            .lot_size(config.lot_size)
            # Without capacity the exchange keeps no trade ticks, and the flow
            # features (CVD, VPIN, aggressor imbalance) silently see nothing.
            .last_trades_capacity(config.last_trades_capacity)
            .no_partial_fill_exchange())


def show(report, source, config, queue_model):
    def line(label, value, width=34):
        print(f'  {label:<{width}} {value}')

    print(f'\nSWARM BACKTEST · {source}')
    print(f'  queue model {queue_model} · maker {config.maker_fee*10000:.1f}bps / '
          f'taker {config.taker_fee*10000:.1f}bps · cycle {config.interval_ms} ms\n')

    print('1 / FEED AND WARM-UP')
    line('cycles', f'{report.ticks:,}')
    line('usable books', f'{report.books:,}')
    line('VPIN bucket calibrated from', f'{report.warmup_trades_seen:,} trades'
         if report.warmup_trades_seen else 'not reached')
    line('  bucket volume', f'{report.bucket_volume:.4f}' if report.bucket_volume else '—')
    line('signals with features ready', f'{report.signals:,}')

    print('\n2 / GATES')
    line('consensus authorized', f'{report.authorized:,}')

    print('\n3 / POST-ONLY EXECUTION')
    line('entries submitted', f'{report.entries_submitted:,}')
    line('  filled', f'{report.entries_filled:,}  (maker {report.maker_fills:,} · '
                     f'taker {report.taker_fills:,})')
    line('  expired (would have crossed)', f'{report.entries_expired:,}')
    line('  cancelled on quote TTL', f'{report.entries_cancelled:,}')
    rate = report.fill_rate
    line('FILL RATE', f'{rate:.1%}' if rate is not None else 'n/a')

    print('\n4 / BARRIERS AND PnL')
    line('take-profit / stop / timeout',
         f'{report.barrier_tp} / {report.barrier_sl} / {report.barrier_timeout}')
    line('venue trades', f'{report.num_trades:,}')
    line('traded value', f'{report.trading_value:,.2f} USDT')
    line('fees paid', f'{report.fee:,.4f} USDT')
    realized = report.realized_fee_bps
    line('realized fee rate', f'{realized:.2f} bps' if realized is not None else 'n/a')
    line('balance change', f'{report.balance:+,.4f} USDT')
    line('open position at end', f'{report.position:+.4f}')

    print('\n5 / COUNTERFACTUAL PROBES')
    lab = report.lab or {}
    if lab.get('probes'):
        line('resolved', f"{lab['probes']:,}  (open {lab.get('open', 0)})")
        line('take-profit share', f"{lab['tp']:.1%}")
        line('mean net', f"{lab['net_bps']:+.2f} bps")
    else:
        line('resolved', '0 — nothing reached a barrier')

    if report.warnings:
        print('\n6 / WARNINGS')
        for warning in report.warnings:
            print(f'  ! {warning}')
    print()


def main():
    parser = argparse.ArgumentParser(description='Backtest the swarm with queue and latency models')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--synthetic', action='store_true', help='Offline generated feed (proves plumbing only)')
    source.add_argument('--data', help='hftbacktest .npz event file')
    parser.add_argument('--symbol', default='BTCUSDT')
    parser.add_argument('--minutes', type=float, default=30, help='Synthetic feed length')
    parser.add_argument('--tick-size', type=float, default=0.10)
    parser.add_argument('--lot-size', type=float, default=0.001)
    parser.add_argument('--min-notional', type=float, default=100.0)
    parser.add_argument('--equity', type=float, default=4930.88)
    parser.add_argument('--queue-model', default='power3', choices=sorted(QUEUE_MODELS))
    parser.add_argument('--feed-latency-ms', type=float, default=5.0)
    parser.add_argument('--order-latency-ms', type=float, default=5.0)
    parser.add_argument('--latency-file', help='.npz of measured order latency (overrides constants)')
    parser.add_argument('--interval-ms', type=int, default=500)
    parser.add_argument('--edge-multiple', type=float, default=1.8)
    parser.add_argument('--lab', default=None, help='Probe JSONL path (default data/backtest-signal-lab.jsonl)')
    args = parser.parse_args()

    lab_path = args.lab or str(ROOT/'data'/'backtest-signal-lab.jsonl')
    Path(lab_path).parent.mkdir(exist_ok=True)
    config = BacktestConfig(symbol=args.symbol, tick_size=args.tick_size, lot_size=args.lot_size,
                            min_notional=args.min_notional, equity=args.equity,
                            interval_ms=args.interval_ms, lab_path=lab_path,
                            edge_multiple=args.edge_multiple)

    if args.synthetic:
        data = synthetic_feed(seconds=args.minutes*60, tick_size=config.tick_size,
                              lot_size=config.lot_size)
        label = f'SYNTHETIC FEED — plumbing only, not a result ({args.minutes:g} min)'
    else:
        data = np.load(args.data)
        data = data[data.files[0]] if hasattr(data, 'files') else data
        label = f'{args.data} · {args.symbol}'

    asset = build_asset(data, config, args.queue_model,
                        args.feed_latency_ms, args.order_latency_ms, args.latency_file)
    hbt = HashMapMarketDepthBacktest([asset])
    try:
        report = SwarmBacktest(config).run(hbt)
    finally:
        hbt.close()
    show(report, label, config, args.queue_model)
    print(f'Probes written to {lab_path}')
    print(f'Calibrate with:  python tools/calibrate_edge.py --path {lab_path}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
