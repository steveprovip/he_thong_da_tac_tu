"""Check and download Binance public data files via raw TLS (bypasses proxy blocking urllib).

Usage:
    python tools/_check_and_download.py --check-only
    python tools/_check_and_download.py --download --out-dir data/raw
"""
import argparse
import socket
import ssl
import sys
import time
from pathlib import Path

HOST = "data.binance.vision"
BASE = "/data/futures/um/daily"

FILES = [
    ("aggTrades", "SOLUSDT", "2026-09-05"),
    ("bookDepth",  "SOLUSDT", "2026-09-05"),
    ("aggTrades", "SOLUSDT", "2026-09-06"),
    ("bookDepth",  "SOLUSDT", "2026-09-06"),
    ("aggTrades", "BTCUSDT", "2026-09-05"),
    ("bookDepth",  "BTCUSDT", "2026-09-05"),
]


def _path(kind, symbol, date):
    return f"{BASE}/{kind}/{symbol}/{symbol}-{kind}-{date}.zip"


def _tls_connect(host, timeout=15):
    ctx = ssl.create_default_context()
    raw = socket.create_connection((host, 443), timeout=timeout)
    raw.settimeout(timeout)
    return ctx.wrap_socket(raw, server_hostname=host)


def head(host, path):
    tls = _tls_connect(host)
    req = f"HEAD {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
    tls.sendall(req.encode())
    resp = b""
    while True:
        chunk = tls.recv(4096)
        if not chunk:
            break
        resp += chunk
        if b"\r\n\r\n" in resp:
            break
    tls.close()
    lines = resp.decode(errors="replace").split("\r\n")
    status_code = int(lines[0].split()[1]) if lines else 0
    cl = next(
        (l.split(":", 1)[1].strip() for l in lines if l.lower().startswith("content-length")),
        "0",
    )
    return status_code, int(cl)


def download(host, path, dest, chunk=65536):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tls = _tls_connect(host, timeout=120)
    req = f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
    tls.sendall(req.encode())
    header_buf = b""
    while b"\r\n\r\n" not in header_buf:
        header_buf += tls.recv(4096)
    header_raw, body_start = header_buf.split(b"\r\n\r\n", 1)
    lines = header_raw.decode(errors="replace").split("\r\n")
    status_code = int(lines[0].split()[1])
    if status_code != 200:
        tls.close()
        raise RuntimeError(f"HTTP {status_code} for {path}")
    cl_str = next(
        (l.split(":", 1)[1].strip() for l in lines if l.lower().startswith("content-length")),
        "0",
    )
    cl = int(cl_str)
    received = len(body_start)
    t0 = time.time()
    with open(dest, "wb") as fh:
        fh.write(body_start)
        while received < cl:
            data = tls.recv(chunk)
            if not data:
                break
            fh.write(data)
            received += len(data)
    tls.close()
    elapsed = time.time() - t0
    return received, elapsed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--out-dir", default="data/raw")
    args = ap.parse_args()

    out = Path(args.out_dir)
    print(f"Host: {HOST}")
    print(f"Files to process: {len(FILES)}\n")

    for kind, symbol, date in FILES:
        path = _path(kind, symbol, date)
        fname = path.split("/")[-1]
        dest = out / fname
        try:
            code, size_bytes = head(HOST, path)
            size_kb = size_bytes // 1024
            already = dest.exists()
            tag = "[already downloaded]" if already else ""
            print(f"  HTTP {code}  {size_kb:>7} KB  {fname}  {tag}")
            if args.download and not already and code == 200:
                print(f"    Downloading -> {dest} ...", end=" ", flush=True)
                received, elapsed = download(HOST, path, dest)
                speed = received // 1024 // max(1, int(elapsed))
                print(f"{received//1024} KB in {elapsed:.1f}s ({speed} KB/s)")
        except Exception as exc:
            print(f"  ERROR  {fname}: {exc}")

    print("\nDone.")


if __name__ == "__main__":
    sys.exit(main())
