"""Read-only, bounded loopback telemetry; no credentials or trading controls.

Endpoints (127.0.0.1 only):
  GET  /api/state   engine telemetry (in-process, else fresh data/telemetry_state.json, else :8766)
  GET  /api/fast    fast lane (data/fast_state.json)      GET /api/radar   alpha radar
  GET  /api/stream  Server-Sent Events: {"state":..,"fast":..} pushed on change (<=4 Hz)
  POST /api/shutdown {"confirm":"SHUTDOWN"}  kill switch
  POST /api/force_test_trade {"side":"BUY"}   demo only (engine process: python -m core.engine)
  POST /api/switch_mode {"mode":"demo"|"live","confirm":"LIVE"}   live needs run.bat live arming
  POST /api/close_position {}                 reduce-only market close of the engine symbol
  Control POSTs require Content-Type: application/json and a loopback Origin (CSRF guard).
Files are published atomically by writers (core.atomic_io), read with retry here.
"""
import argparse
import hashlib
import json
from pathlib import Path
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
from urllib.parse import urlsplit

if __package__ in (None, ''):                      # `python core/web_ui_server.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.atomic_io import atomic_write_json, read_json  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / 'data' / 'telemetry_state.json'
STATE_FRESH_SECONDS = 5.0
CONTROL_ROUTES = {'/api/force_test_trade': 'force_test_trade', '/api/switch_mode': 'switch_mode',
                  '/api/switch_symbol': 'switch_symbol',
                  '/api/close_position': 'close_position'}
STREAM_HZ = 4.0
STREAM_MAX_S = 300.0          # browser EventSource reconnects; bounds handler threads
_proxy_down_until = [0.0]
CSP = ("default-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
       "script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _read_state_file():
    data = read_json(STATE_FILE)
    return data if isinstance(data, dict) else None


class Telemetry:
    def __init__(self):
        self.lock = threading.Lock()
        self.history = deque(maxlen=600)
        self.events = deque(maxlen=50)
        self.state = {'mode': 'OFFLINE', 'status': 'Waiting for engine', 'equity': None,
                      'wallet': None, 'pnl': None, 'volume': 0, 'fees': 0, 'score': 0,
                      'agents': [], 'markets': [], 'positions': [], 'armed': False,
                      'authorized': 0, 'auth_at': None, 'execute_gate': .768, 'veto_gate': .70,
                      'lab': None, 'shutdown': False, 'shutdown_reason': ''}

    def request_shutdown(self, reason='user'):
        """Kill switch: the engine polls this flag and flattens before exiting."""
        with self.lock:
            self.state['shutdown'] = True
            self.state['shutdown_reason'] = str(reason)[:120]

    def update(self, **kwargs):
        with self.lock:
            self.state.update(kwargs)
            if kwargs.get('equity') is not None:
                self.history.append({'t': time.time(), 'value': kwargs['equity']})

    def event(self, message):
        with self.lock:
            self.events.appendleft({'t': time.time(), 'message': str(message)[:240]})

    def snapshot(self):
        with self.lock:
            return {**self.state, 'timestamp': time.time(), 'history': list(self.history), 'events': list(self.events)}

    def persist(self, path=None) -> bool:
        """Atomic publish for out-of-process dashboards (Windows-safe replace)."""
        snap = self.snapshot()
        snap['updated_at'] = snap['timestamp']
        return atomic_write_json(path or STATE_FILE, snap)


def state_payload(telemetry) -> bytes:
    snap = telemetry.snapshot()
    payload = None
    if snap.get('status') == 'Waiting for engine' and snap.get('equity') is None:
        # Standalone dashboard. A FRESH engine file wins outright; only a
        # stale/missing one pays for the :8766 probe, and a refused probe
        # is not retried for 5 s. On Windows a connect to a closed local
        # port burns the whole 0.3 s timeout, which throttled every poll.
        file_data = _read_state_file()
        fresh = file_data is not None and time.time() - _num(file_data.get('updated_at')) < STATE_FRESH_SECONDS
        if not fresh and time.time() >= _proxy_down_until[0]:
            try:
                import urllib.request
                with urllib.request.urlopen('http://127.0.0.1:8766/api/state', timeout=0.3) as resp:
                    payload = resp.read()
            except Exception:
                _proxy_down_until[0] = time.time() + 5.0
        if payload is None and file_data is not None:
            file_data.setdefault('shutdown', snap.get('shutdown', False))
            file_data.setdefault('shutdown_reason', snap.get('shutdown_reason', ''))
            try:
                payload = json.dumps(file_data, allow_nan=False).encode()
            except ValueError:
                payload = None
    if payload is None:
        payload = json.dumps(snap, allow_nan=False).encode()
    return payload


def file_payload(path: Path, empty: bytes) -> bytes:
    try:
        payload = path.read_bytes()
        json.loads(payload)
        return payload
    except (OSError, ValueError):
        return empty


def make_server(telemetry, host='127.0.0.1', port=8765, shutdown_file=None, radar_file=None, fast_file=None,
                stream_max_s=STREAM_MAX_S, controller=None):
    fast_path = Path(fast_file) if fast_file is not None else ROOT / 'data' / 'fast_state.json'
    radar_path = Path(radar_file) if radar_file is not None else ROOT / 'data' / 'alpha_radar.json'
    if host not in ('127.0.0.1', 'localhost'):
        raise ValueError('Loopback only')
    assets = {'/': ('index.html', 'text/html; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8'),
              '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
              '/terminal.js': ('terminal.js', 'text/javascript; charset=utf-8'),
              '/desk.js': ('desk.js', 'text/javascript; charset=utf-8')}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_GET(self):
            if self.headers.get('Host', '').split(':')[0] not in ('127.0.0.1', 'localhost'):
                self.send_error(403)
                return
            path = self.path.split('?')[0]
            if path == '/api/state':
                payload, content_type = state_payload(telemetry), 'application/json'
            elif path == '/api/fast':
                # Fast lane snapshot written atomically by research.fast_runner (1 s cadence).
                payload, content_type = file_payload(fast_path, b'{"status": "no fast lane yet"}'), 'application/json'
            elif path == '/api/radar':
                # Alpha/Event radar snapshot written atomically by research.radar_runner.
                payload, content_type = file_payload(radar_path, b'{"status": "no radar yet"}'), 'application/json'
            elif path == '/api/stream':
                self._stream()
                return
            elif path in assets:
                file, content_type = assets[path]
                payload = (ROOT/'web_ui'/file).read_bytes()
            elif path == '/health':
                payload, content_type = b'{"ok":true}', 'application/json'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', CSP)
            self.end_headers()
            self.wfile.write(payload)

        def _control(self, method, raw_body):
            body = None
            if 'application/json' in (self.headers.get('Content-Type') or '') and raw_body and len(raw_body) <= 1024:
                try:
                    body = json.loads(raw_body)
                except ValueError:
                    body = None
            if not isinstance(body, dict):
                status, out = 400, {'ok': False, 'error': 'JSON body required (Content-Type: application/json)'}
            elif controller is None:
                status, out = 503, {'ok': False, 'error': 'engine không chạy trong tiến trình này - dùng run.bat demo'}
            else:
                out = getattr(controller, method)(body)
                status = 200 if out.get('ok') else 409
                telemetry.event(f'{method}: ' + ('OK' if out.get('ok') else str(out.get('error'))))
            payload = json.dumps(out, ensure_ascii=False, allow_nan=False, default=str).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(payload)

        def _stream(self):
            """SSE push: one `tick` per change, `hb` liveness event every 2 s, closes after STREAM_MAX_S."""
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.close_connection = True          # body ends at close (no Content-Length)
            self.end_headers()
            last, beat, t0 = None, 0.0, time.monotonic()
            try:
                self.wfile.write(b'retry: 1500\n\n')
                self.wfile.flush()
                while time.monotonic() - t0 < stream_max_s:
                    body = (b'{"state":' + state_payload(telemetry) + b',"fast":'
                            + file_payload(fast_path, b'null') + b'}')
                    digest = hashlib.blake2b(body, digest_size=16).digest()
                    now = time.monotonic()
                    if digest != last:
                        self.wfile.write(b'event: tick\ndata: ' + body + b'\n\n')
                        self.wfile.flush()
                        last, beat = digest, now
                    elif now - beat >= 2.0:                # liveness event: client keeps SSE as source
                        self.wfile.write(b'event: hb\ndata: {}\n\n')
                        self.wfile.flush()
                        beat = now
                    time.sleep(1.0 / STREAM_HZ)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                return

        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
                raw_body = self.rfile.read(length) if 0 < length <= 65536 else b''
            except Exception:
                raw_body = b''
            if self.headers.get('Host', '').split(':')[0] not in ('127.0.0.1', 'localhost'):
                self.send_error(403)
                return
            path = self.path.split('?')[0]
            origin = self.headers.get('Origin')
            if origin and urlsplit(origin).hostname not in ('127.0.0.1', 'localhost'):
                self.send_error(403)                       # cross-site POST (CSRF) refused
                return
            if path in CONTROL_ROUTES:
                self._control(CONTROL_ROUTES[path], raw_body)
                return
            if path != '/api/shutdown':
                self.send_error(404)
                return
            body = {}
            if raw_body and len(raw_body) <= 1024:
                try:
                    body = json.loads(raw_body)
                except ValueError:
                    body = {}
            if not isinstance(body, dict) or body.get('confirm') != 'SHUTDOWN':
                payload = b'{"error": "confirm token required: {\\"confirm\\": \\"SHUTDOWN\\"}"}'
                status = 400
            else:
                telemetry.request_shutdown('dashboard kill switch')
                if shutdown_file is not None:
                    # Out-of-process engines (research.micro_scanner) poll this file's mtime.
                    try:
                        Path(shutdown_file).parent.mkdir(parents=True, exist_ok=True)
                        Path(shutdown_file).write_text(json.dumps({'t': time.time(), 'reason': 'dashboard'}), encoding='utf-8')
                    except OSError:
                        pass
                telemetry.event('KILL SWITCH: full shutdown requested; flattening venue')
                payload = b'{"ok": true, "shutdown": true}'
                status = 200
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def start_server(telemetry, host='127.0.0.1', port=8765):
    server = make_server(telemetry, host, port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Standalone read-only dashboard preview')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    print(f'GROKTOPUS telemetry -> http://127.0.0.1:{args.port}  (SSE /api/stream · Ctrl+C to stop)', flush=True)
    make_server(Telemetry(), port=args.port, shutdown_file=ROOT / 'data' / 'shutdown_request.json').serve_forever()
