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

Chạy:  python research/l2_recorder.py --symbols SOLUSDT,BTCUSDT --seconds 3600
Mặc định ghi vào data/l2/<symbol>/<YYYYMMDD>.jsonl, rotate theo ngày,
reconnect với backoff khi mất mạng. Ctrl+C dừng sạch.

NGÂN SÁCH DỮ LIỆU (core/early_stop.DataBudget): toàn bộ data/l2 (kể cả các phiên
cũ) không được vượt --max-mb (mặc định 500 MB). Đã có 2.9 GB và
phan_tich/markout_l2.py đã trả lời câu hỏi mà dữ liệu này dùng để hỏi
(markout maker -0.7..-1.2 bps, không có gì vượt 9 bps), nên recorder từ chối
chạy cho tới khi có --override-budget kèm một giả thuyết mới.
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
DEFAULT_SECONDS = 3600.0
DEFAULT_MAX_MB = 500.0


def remaining_budget_bytes(max_mb, root=ROOT):
    """Bytes still allowed under the global L2 budget (prior sessions count)."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from core.early_stop import directory_bytes
    return int(max_mb * 1e6) - directory_bytes(str(root))


def out_path(symbol):
    day = datetime.now(timezone.utc).strftime('%Y%m%d')
    folder = ROOT / symbol.upper()
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f'{day}.jsonl'


async def record(symbols, seconds, stats, max_bytes=0):
    streams = '/'.join(f'{s.lower()}@bookTicker' for s in symbols
                       ) + '/' + '/'.join(f'{s.lower()}@depth@100ms' for s in symbols)
    url = WS_URL.format(streams=streams)
    deadline = time.monotonic() + seconds
    backoff = BACKOFF_START
    files = {}
    counts = {s: 0 for s in symbols}
    written = 0
    try:
        while time.monotonic() < deadline and not (max_bytes and written >= max_bytes):
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    backoff = BACKOFF_START
                    stats('connected')
                    while time.monotonic() < deadline:
                        if max_bytes and written >= max_bytes:
                            stats(f'BUDGET_EXHAUSTED: {written} bytes >= {max_bytes}; recorder stops')
                            break
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
                        line = json.dumps(record, separators=(',', ':')) + '\n'
                        files[symbol].write(line)
                        written += len(line)
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--symbols', default='SOLUSDT,BTCUSDT')
    parser.add_argument('--seconds', type=float, default=DEFAULT_SECONDS)
    parser.add_argument('--max-mb', type=float, default=DEFAULT_MAX_MB,
                        help='Global budget for data/l2 including previous sessions')
    parser.add_argument('--override-budget', action='store_true',
                        help='Record past the budget (requires a written hypothesis; see docs/CHAN_DOAN_TOI_UU_VONG_LAP.md)')
    args = parser.parse_args(argv)
    symbols = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    remaining = remaining_budget_bytes(args.max_mb)
    if remaining <= 0 and not args.override_budget:
        print(f'DATA_BUDGET_EXHAUSTED: data/l2 da vuot {args.max_mb:.0f} MB. '
              'Khong ghi them. Dung --override-budget neu co gia thuyet moi.')
        return 3
    max_bytes = 0 if args.override_budget else remaining
    print(f'L2 recorder: {symbols} trong {args.seconds/3600:.1f}h, ngan sach {max_bytes/1e6:.0f} MB -> {ROOT}')
    stats = lambda msg: print(f'  [{datetime.now().strftime("%H:%M:%S")}] {msg}', flush=True)
    try:
        asyncio.run(record(symbols, args.seconds, stats, max_bytes))
    except KeyboardInterrupt:
        print('\nDung sach theo yeu cau.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
