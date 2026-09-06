"""Read-only, bounded loopback telemetry; no credentials or trading controls."""
import argparse
import json
from pathlib import Path
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]


class Telemetry:
    def __init__(self):
        self.lock = threading.Lock()
        self.history = deque(maxlen=600)
        self.events = deque(maxlen=50)
        self.state = {'mode': 'OFFLINE', 'status': 'Waiting for engine', 'equity': None,
                      'wallet': None, 'pnl': None, 'volume': 0, 'fees': 0, 'score': 0,
                      'agents': [], 'markets': [], 'positions': [], 'armed': False,
                      'authorized': 0, 'auth_at': None, 'execute_gate': .768, 'veto_gate': .70,
                      'lab': None}

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


def make_server(telemetry, host='127.0.0.1', port=8765):
    if host not in ('127.0.0.1', 'localhost'):
        raise ValueError('Loopback only')
    assets = {'/': ('index.html', 'text/html; charset=utf-8'), '/style.css': ('style.css', 'text/css'), '/app.js': ('app.js', 'text/javascript')}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_GET(self):
            if self.headers.get('Host', '').split(':')[0] not in ('127.0.0.1', 'localhost'):
                self.send_error(403)
                return
            path = self.path.split('?')[0]
            if path == '/api/state':
                payload = json.dumps(telemetry.snapshot(), allow_nan=False).encode()
                content_type = 'application/json'
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
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
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
    make_server(Telemetry(), port=args.port).serve_forever()
