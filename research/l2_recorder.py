"""L2 recorder — ghi bookTicker + depth@100ms từ mainnet public WS (không key).

Lời giải Option 4 cho blocker A4: dữ liệu công khai của Binance (data.binance.vision)
chỉ có bookDepth per-percent không tái tạo được L2, nên cách duy nhất để có
L2 đúng với local-timestamp là TỰ GHI khi bot đang chạy phiên. Recorder này
chạy ĐỘC LẬP với bot: chỉ đọc stream công khai, không key, không gửi lệnh,
không đụng core/ hay live/.

Mỗi dòng JSONL: {"lt": <epoch float local>, "s": <symbol>, "ch": "bookTicker"|
"depth", ...payload}. bookTicker cho top-of-book (bid/ask chính xác tới từng
update); depth@100ms cho 10 level đầu. Hai kênh ghép sau này theo "lt" gần
nhất cho backtest có queue model đáng tin.

Chạy:  python research/l2_recorder.py --symbols SOLUSDT,BTCUSDT --seconds 39600
Mặc định ghi vào data/l2/<symbol>/<YYYYMMDD>.jsonl, rotate theo ngày,
reconnect với backoff khi mất mạng. Ctrl+C dừng sạch.
"""
import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import websockets

WS_URL = 'wss://fstream.binance.com/stream?streams={streams}'
ROOT = Path(__file__).resolve().parents[1] / 'data' / 'l2'
BACKOFF_START, BACKOFF_MAX = 1.0, 60.0


def out_path(symbol):
    day = datetime.now(timezone.utc).strftime('%Y%m%d')
    folder = ROOT / symbol.upper()
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f'{day}.jsonl'


async def record(symbols, seconds, stats):
    streams = '/'.join(f'{s.lower()}@bookTicker' for s in symbols
                       ) + '/' + '/'.join(f'{s.lower()}@depth@100ms' for s in symbols)
    url = WS_URL.format(streams=streams)
    deadline = time.monotonic() + seconds
    backoff = BACKOFF_START
    files = {}
    counts = {s: 0 for s in symbols}
    try:
        while time.monotonic() < deadline:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    backoff = BACKOFF_START
                    stats('connected')
                    while time.monotonic() < deadline:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        event = json.loads(raw)
                        channel, payload = event.get('stream', ''), event.get('data', {})
                        symbol = (payload.get('s') or '').upper()
                        if not symbol:
                            continue
                        path = out_path(symbol)
                        if files.get(symbol) != path:
                            if files.get(symbol):
                                files[symbol].close()
                            files[symbol] = open(path, 'a', encoding='utf-8')
                        record = {'lt': time.time(), 'pc': time.perf_counter(),
                                  's': symbol, 'ch': 'bookTicker' if 'bookTicker' in channel else 'depth'}
                        record.update(payload)
                        files[symbol].write(json.dumps(record, separators=(',', ':')) + '\n')
                        counts[symbol] = counts.get(symbol, 0) + 1
            except (asyncio.TimeoutError, ConnectionError, OSError, websockets.WebSocketException) as exc:
                stats(f'reconnect: {type(exc).__name__}: {exc}')
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)
    finally:
        for handle in files.values():
            handle.close()
        for symbol in symbols:
            print(f'  {symbol}: {counts.get(symbol, 0)} events -> {ROOT / symbol.upper()}')


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--symbols', default='SOLUSDT,BTCUSDT')
    parser.add_argument('--seconds', type=float, default=39600.0)
    args = parser.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    print(f'L2 recorder: {symbols} trong {args.seconds/3600:.1f}h -> {ROOT}')
    stats = lambda msg: print(f'  [{datetime.now().strftime("%H:%M:%S")}] {msg}', flush=True)
    try:
        asyncio.run(record(symbols, args.seconds, stats))
    except KeyboardInterrupt:
        print('\nDung sach theo yeu cau.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
