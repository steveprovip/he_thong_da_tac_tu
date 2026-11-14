#!/usr/bin/env python3
"""Download Binance USD-M public ticks and depth bands without writing off F:.

Example:
    python tools/download_public_l2.py --start-date 2026-09-01 --days 14

Outputs are gzip JSONL under data/ticks/<SYMBOL>/ and data/public_depth/<SYMBOL>/.
Binance's public bookDepth archive contains cumulative percentage bands, not exact
price-level L2 updates. The output deliberately preserves those bands as bands.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://data.binance.vision/data/futures/um/daily/{kind}/{symbol}/{symbol}-{kind}-{day}.zip"
DEFAULT_SYMBOLS = ("BTCUSDT", "SOLUSDT", "DOGEUSDT", "BNBUSDT")
SUPPORTED_TYPES = ("aggTrades", "bookDepth")
SHA256_RE = re.compile(r"(?i)\b([a-f0-9]{64})\b")
NANOSECONDS = 1_000_000_000


class DownloadError(RuntimeError):
    """An archive was unavailable, invalid, or could not be safely stored."""


@dataclass(frozen=True)
class RemoteArchive:
    kind: str
    symbol: str
    day: str
    url: str
    size_bytes: int
    sha256: str


def archive_url(kind: str, symbol: str, day: str) -> str:
    if kind not in SUPPORTED_TYPES and kind != "bookTicker":
        raise ValueError(f"unsupported Binance public data type: {kind}")
    if not re.fullmatch(r"[A-Z0-9]{3,20}", symbol):
        raise ValueError(f"invalid symbol: {symbol!r}")
    dt.date.fromisoformat(day)
    return BASE_URL.format(kind=kind, symbol=symbol, day=day)


def ensure_f_drive(path: Path, expected_drive: str = "F:") -> Path:
    """Resolve and reject any destination not rooted on the configured drive."""
    resolved = Path(path).expanduser().resolve(strict=False)
    if resolved.drive.upper() != expected_drive.upper():
        raise DownloadError(
            f"refusing to write outside {expected_drive}: resolved destination is {resolved}"
        )
    return resolved


def ensure_under_root(root: Path, path: Path) -> Path:
    root = ensure_f_drive(root)
    resolved = ensure_f_drive(path)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise DownloadError(f"refusing path outside data root {root}: {resolved}") from exc
    return resolved


def parse_checksum(text: str) -> str:
    match = SHA256_RE.search(text)
    if not match:
        raise DownloadError("Binance .CHECKSUM did not contain a SHA-256 digest")
    return match.group(1).lower()


def _request(url: str, method: str = "GET", headers: dict | None = None):
    req = urllib.request.Request(
        url,
        method=method,
        headers={"User-Agent": "factory-quant-opt-public-data/1.0", **(headers or {})},
    )
    return urllib.request.urlopen(req, timeout=45)


def _head(url: str) -> tuple[int, dict]:
    try:
        with _request(url, method="HEAD") as response:
            return response.status, dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()) if exc.headers else {}


def probe_bookticker(symbol: str, day: str) -> dict:
    """Probe the historical BBO path; 404 is reported as source availability, not fatal."""
    url = archive_url("bookTicker", symbol, day)
    status, headers = _head(url)
    return {
        "symbol": symbol,
        "day": day,
        "url": url,
        "http_status": status,
        "size_bytes": int(headers.get("Content-Length", "0") or 0),
    }


def preflight_archive(kind: str, symbol: str, day: str) -> RemoteArchive:
    url = archive_url(kind, symbol, day)
    status, headers = _head(url)
    if status != 200:
        raise DownloadError(f"HTTP {status} for {url}")
    try:
        size = int(headers.get("Content-Length", "0"))
    except ValueError as exc:
        raise DownloadError(f"invalid Content-Length for {url}") from exc
    if size <= 0:
        raise DownloadError(f"missing or empty archive at {url}")
    checksum_url = f"{url}.CHECKSUM"
    try:
        with _request(checksum_url) as response:
            digest = parse_checksum(response.read(4096).decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise DownloadError(f"HTTP {exc.code} for checksum {checksum_url}") from exc
    return RemoteArchive(kind, symbol, day, url, size, digest)


def _timestamp_ns(value: str) -> tuple[int, int | str, str]:
    """Return UTC ns, original timestamp, and the original timestamp unit."""
    value = value.strip()
    if value.isdigit():
        raw = int(value)
        if raw >= 10**14:  # Binance microsecond timestamp
            return raw * 1_000, raw, "us"
        if raw >= 10**11:  # Binance millisecond timestamp
            return raw * 1_000_000, raw, "ms"
        return raw * NANOSECONDS, raw, "s"
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    parsed = parsed.astimezone(dt.timezone.utc)
    ns = int(parsed.timestamp() * NANOSECONDS)
    return ns, value, "iso8601"


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in ("true", "1"):
        return True
    if normalized in ("false", "0"):
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


def normalize_trade(row: dict[str, str], symbol: str) -> dict:
    ts_value = row.get("transact_time") or row.get("timestamp") or row.get("time")
    if ts_value is None:
        raise ValueError("aggTrades row has no transact_time/timestamp/time")
    ts_ns, source_ts, source_unit = _timestamp_ns(ts_value)
    return {
        "ch": "aggTrade",
        "s": symbol,
        "ts_ns": ts_ns,
        "source_timestamp": source_ts,
        "source_time_unit": source_unit,
        "a": int(row["agg_trade_id"]),
        "p": float(row["price"]),
        "q": float(row["quantity"]),
        "f": int(row["first_trade_id"]),
        "l": int(row["last_trade_id"]),
        "m": _parse_bool(row["is_buyer_maker"]),
    }


def normalize_depth_band(row: dict[str, str]) -> tuple[int, dict]:
    ts_ns, source_ts, source_unit = _timestamp_ns(row["timestamp"])
    percentage = float(row["percentage"])
    depth = float(row["depth"])
    notional = float(row["notional"])
    if not -5.000001 <= percentage <= 5.000001:
        raise ValueError(f"bookDepth percentage outside [-5, 5]: {percentage}")
    if depth < 0 or notional < 0:
        raise ValueError("negative bookDepth quantity/notional")
    return ts_ns, {
        "percentage": percentage,
        "depth": depth,
        "notional": notional,
        "source_timestamp": source_ts,
        "source_time_unit": source_unit,
    }


def _rows_from_zip(archive: zipfile.ZipFile, member: str) -> Iterable[dict[str, str]]:
    with archive.open(member, "r") as binary:
        text = io.TextIOWrapper(binary, encoding="utf-8-sig", newline="")
        reader = csv.DictReader(text)
        if not reader.fieldnames:
            raise DownloadError(f"CSV has no header: {member}")
        reader.fieldnames = [name.strip() for name in reader.fieldnames]
        yield from reader


def audit_depth_jsonl(path: Path) -> dict:
    """Count repeated percentage-band payloads that can reveal frozen archive fields."""
    observations: dict[str, int] = {}
    values: dict[str, set[tuple[float, float]]] = {}
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            snapshot = json.loads(line)
            for band in snapshot.get("levels", []):
                label = f"{float(band['percentage']):+.2f}%"
                observations[label] = observations.get(label, 0) + 1
                values.setdefault(label, set()).add((float(band["depth"]), float(band["notional"])))
    diagnostics = {
        label: {"observations": observations[label], "unique_depth_notional": len(seen)}
        for label, seen in sorted(values.items())
    }
    flags = [
        f"flat_band:{label}"
        for label, stats in diagnostics.items()
        if label.startswith("+") and stats["observations"] >= 100 and stats["unique_depth_notional"] <= 5
    ]
    return {"band_diagnostics": diagnostics, "quality_flags": flags}


def convert_archive(archive_path: Path, output_path: Path, kind: str, symbol: str, day: str) -> dict:
    """Stream a verified ZIP CSV into gzip JSONL without extracting it to disk."""
    if kind not in SUPPORTED_TYPES:
        raise ValueError(f"unsupported conversion type: {kind}")
    archive_path = ensure_f_drive(archive_path)
    output_path = ensure_f_drive(output_path)
    start_ns = int(dt.datetime.combine(dt.date.fromisoformat(day), dt.time(), dt.timezone.utc).timestamp() * NANOSECONDS)
    end_ns = start_ns + 86_400 * NANOSECONDS
    output_part = output_path.with_name(output_path.name + ".part")
    members_found = []
    band_group: list[dict] = []
    group_ts = None
    rows = 0
    source_rows = 0
    min_ns = None
    max_ns = None
    previous_ns = None

    def write_record(text_stream, record: dict, timestamp_ns: int):
        if not start_ns <= timestamp_ns < end_ns:
            raise DownloadError(f"timestamp outside requested UTC day {day}: {timestamp_ns}")
        text_stream.write(json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n")

    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            members = [n for n in zf.namelist() if n.lower().endswith(".csv") and not n.endswith("/")]
            if not members:
                raise DownloadError(f"ZIP contains no CSV: {archive_path}")
            with output_part.open("wb") as raw_output:
                with gzip.GzipFile(fileobj=raw_output, mode="wb", mtime=0) as compressed:
                    with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as text_output:
                        for member in members:
                            members_found.append(member)
                            for row in _rows_from_zip(zf, member):
                                if not row or None in row:
                                    continue
                                if kind == "aggTrades":
                                    record = normalize_trade(row, symbol)
                                    timestamp_ns = int(record["ts_ns"])
                                    if previous_ns is not None and timestamp_ns < previous_ns:
                                        raise DownloadError(f"non-monotonic aggTrades timestamps in {member}")
                                    write_record(text_output, record, timestamp_ns)
                                    rows += 1
                                    source_rows += 1
                                else:
                                    timestamp_ns, band = normalize_depth_band(row)
                                    if previous_ns is not None and timestamp_ns < previous_ns:
                                        raise DownloadError(f"non-monotonic bookDepth timestamps in {member}")
                                    if group_ts is not None and timestamp_ns != group_ts:
                                        write_record(text_output, {
                                            "ch": "bookDepthBands", "s": symbol,
                                            "ts_ns": group_ts, "levels": band_group,
                                        }, group_ts)
                                        rows += 1
                                        band_group = []
                                    group_ts = timestamp_ns
                                    band_group.append(band)
                                    source_rows += 1
                                previous_ns = timestamp_ns
                                min_ns = timestamp_ns if min_ns is None else min(min_ns, timestamp_ns)
                                max_ns = timestamp_ns if max_ns is None else max(max_ns, timestamp_ns)
                        if kind == "bookDepth" and group_ts is not None:
                            write_record(text_output, {
                                "ch": "bookDepthBands", "s": symbol,
                                "ts_ns": group_ts, "levels": band_group,
                            }, group_ts)
                            rows += 1
        if not rows or min_ns is None or max_ns is None:
            raise DownloadError(f"archive produced no {kind} records: {archive_path}")
        diagnostics = audit_depth_jsonl(output_part) if kind == "bookDepth" else {}
        os.replace(output_part, output_path)
        return {
            "kind": kind,
            "symbol": symbol,
            "day": day,
            "output_rows": rows,
            "source_rows": source_rows,
            "min_ts_ns": min_ns,
            "max_ts_ns": max_ns,
            "zip_members": members_found,
            "output_bytes": output_path.stat().st_size,
            **diagnostics,
        }
    except Exception:
        output_part.unlink(missing_ok=True)
        raise


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, document: dict, root: Path) -> None:
    path = ensure_under_root(root, path)
    temporary = ensure_under_root(root, path.with_name(path.name + ".part"))
    temporary.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _paths(root: Path, job: RemoteArchive) -> tuple[Path, Path, Path]:
    folder = "ticks" if job.kind == "aggTrades" else "public_depth"
    output = root / folder / job.symbol / f"{job.day.replace('-', '')}.jsonl.gz"
    meta = output.with_name(output.name + ".meta.json")
    cache = root / ".public_l2_cache" / job.kind / job.symbol
    archive = cache / f"{job.symbol}-{job.kind}-{job.day}.zip"
    return (ensure_under_root(root, output), ensure_under_root(root, meta),
            ensure_under_root(root, archive))


def _is_complete(output: Path, meta_path: Path, job: RemoteArchive, root: Path) -> dict | None:
    if not output.exists() and not meta_path.exists():
        return None
    if not output.exists() or not meta_path.exists():
        raise DownloadError(f"incomplete existing output; inspect or use --replace-existing: {output}")
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DownloadError(f"invalid sidecar metadata; inspect or use --replace-existing: {meta_path}") from exc
    if metadata.get("archive_sha256") != job.sha256:
        raise DownloadError(f"source checksum changed; inspect or use --replace-existing: {output}")
    if sha256_file(output) != metadata.get("output_sha256"):
        raise DownloadError(f"existing output checksum failed; inspect or use --replace-existing: {output}")
    if job.kind == "bookDepth":
        diagnostics = audit_depth_jsonl(output)
        if any(metadata.get(key) != value for key, value in diagnostics.items()):
            metadata = {**metadata, **diagnostics}
            _write_json_atomic(meta_path, metadata, root)
    return {**metadata, "status": "skipped"}


def download_resumable(url: str, part_path: Path, expected_size: int, root: Path,
                      timeout: int = 120, retries: int = 5) -> None:
    """Stream to an F-drive .part file and resume with HTTP Range after interruptions."""
    part_path = ensure_under_root(root, part_path)
    part_path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        size = part_path.stat().st_size if part_path.exists() else 0
        if size > expected_size:
            part_path.unlink()
            size = 0
        if size == expected_size:
            return
        headers = {"Accept-Encoding": "identity"}
        if size:
            headers["Range"] = f"bytes={size}-"
        try:
            with _request(url, headers=headers) as response:
                status = response.status
                response_headers = {key.lower(): value for key, value in response.headers.items()}
                if size and status == 206:
                    content_range = response_headers.get("content-range", "")
                    if not content_range.startswith(f"bytes {size}-"):
                        part_path.unlink(missing_ok=True)
                        raise DownloadError(f"invalid Content-Range while resuming {url}: {content_range}")
                    mode = "ab"
                elif status == 200:
                    size = 0
                    mode = "wb"
                else:
                    raise DownloadError(f"HTTP {status} downloading {url}")
                with part_path.open(mode) as out:
                    for chunk in iter(lambda: response.read(1024 * 1024), b""):
                        out.write(chunk)
                    out.flush()
                    os.fsync(out.fileno())
            actual = part_path.stat().st_size
            if actual == expected_size:
                return
            if actual > expected_size:
                part_path.unlink(missing_ok=True)
                raise DownloadError(f"download size {actual} exceeds expected {expected_size}: {url}")
            raise OSError(f"incomplete response: {actual}/{expected_size} bytes for {url}")
        except DownloadError:
            raise
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            if attempt + 1 == retries:
                raise DownloadError(f"download failed after {retries} attempts: {url}: {exc}") from exc
            time.sleep(min(2 ** attempt, 8))


def download_one(job: RemoteArchive, root: Path, keep_archives: bool = False,
                 replace_existing: bool = False) -> dict:
    output, meta_path, archive_path = _paths(root, job)
    existing = _is_complete(output, meta_path, job, root)
    if existing:
        return existing
    if (output.exists() or meta_path.exists()) and not replace_existing:
        raise DownloadError(f"refusing to overwrite existing data: {output}; pass --replace-existing")

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = archive_path.with_name(archive_path.name + ".part")
    archive_path = ensure_under_root(root, archive_path)
    part_path = ensure_under_root(root, part_path)

    archive_ok = archive_path.exists() and archive_path.stat().st_size == job.size_bytes
    if archive_ok and sha256_file(archive_path) != job.sha256:
        archive_path.unlink()
        archive_ok = False
    if not archive_ok:
        for digest_attempt in range(2):
            download_resumable(job.url, part_path, job.size_bytes, root)
            if sha256_file(part_path) == job.sha256:
                os.replace(part_path, archive_path)
                archive_ok = True
                break
            part_path.unlink(missing_ok=True)
        if not archive_ok:
            raise DownloadError(f"SHA-256 mismatch after retry: {job.url}")

    output.parent.mkdir(parents=True, exist_ok=True)
    converted = convert_archive(archive_path, output, job.kind, job.symbol, job.day)
    output_digest = sha256_file(output)
    metadata = {
        **converted,
        "status": "downloaded",
        "source_url": job.url,
        "checksum_url": f"{job.url}.CHECKSUM",
        "archive_sha256": job.sha256,
        "archive_bytes": job.size_bytes,
        "output_sha256": output_digest,
        "output_path": str(output.relative_to(root)),
    }
    _write_json_atomic(meta_path, metadata, root)
    if not keep_archives:
        archive_path.unlink(missing_ok=True)
    return metadata


def _dates(start_date: str, days: int) -> list[str]:
    if days < 1 or days > 366:
        raise ValueError("--days must be between 1 and 366")
    start = dt.date.fromisoformat(start_date)
    return [(start + dt.timedelta(days=offset)).isoformat() for offset in range(days)]


async def _bounded_call(semaphore: asyncio.Semaphore, func, *args):
    async with semaphore:
        return await asyncio.to_thread(func, *args)


async def _preflight(jobs: list[tuple[str, str, str]], concurrency: int):
    semaphore = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(*(
        _bounded_call(semaphore, preflight_archive, kind, symbol, day)
        for kind, symbol, day in jobs
    ), return_exceptions=True)
    errors = [value for value in results if isinstance(value, BaseException)]
    if errors:
        details = "\n".join(f"  - {type(error).__name__}: {error}" for error in errors[:12])
        raise DownloadError(f"archive preflight failed ({len(errors)} missing/invalid):\n{details}")
    return [value for value in results if isinstance(value, RemoteArchive)]


async def _probe_bookticker(symbol_days: list[tuple[str, str]], concurrency: int):
    semaphore = asyncio.Semaphore(concurrency)
    return await asyncio.gather(*(
        _bounded_call(semaphore, probe_bookticker, symbol, day)
        for symbol, day in symbol_days
    ))


def _audit_summary(probes: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for probe in probes:
        key = str(probe["http_status"])
        counts[key] = counts.get(key, 0) + 1
    return {"probes": len(probes), "status_counts": counts, "details": probes}


async def run(args) -> int:
    days = _dates(args.start_date, args.days)
    symbols = [symbol.upper() for symbol in args.symbols]
    if len(set(symbols)) != len(symbols):
        raise DownloadError("duplicate symbols are not allowed")
    selected_types = args.types
    jobs = [(kind, symbol, day) for kind in selected_types for symbol in symbols for day in days]
    probe_jobs = [(symbol, day) for symbol in symbols for day in days]

    archives = await _preflight(jobs, args.concurrency)
    bookticker = await _probe_bookticker(probe_jobs, args.concurrency)
    bookticker_summary = _audit_summary(bookticker)
    archive_bytes = sum(item.size_bytes for item in archives)

    print(f"Preflight: {len(archives)} archives available; compressed bytes={archive_bytes:,}")
    print(f"Historical bookTicker: {bookticker_summary['status_counts']}")
    if args.audit_only:
        print(json.dumps({
            "archive_count": len(archives),
            "archive_bytes": archive_bytes,
            "archive_types": selected_types,
            "bookTicker": {key: value for key, value in bookticker_summary.items() if key != "details"},
        }, indent=2))
        return 0

    root = ensure_f_drive(Path(args.root))
    root.mkdir(parents=True, exist_ok=True)
    root = ensure_f_drive(root)
    required = int(archive_bytes * args.space_factor) + int(args.reserve_gb * 1024**3)
    free = shutil.disk_usage(root).free
    if free < required:
        raise DownloadError(
            f"F: space guard refused run: free={free:,} bytes, estimated={required:,} bytes "
            f"(factor={args.space_factor:g}, reserve={args.reserve_gb:g} GiB)"
        )
    print(f"F: space guard passed: free={free:,} bytes, estimated need={required:,} bytes")

    semaphore = asyncio.Semaphore(args.concurrency)
    tasks = [
        _bounded_call(semaphore, download_one, item, root, args.keep_archives, args.replace_existing)
        for item in archives
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    errors = [value for value in results if isinstance(value, BaseException)]
    if errors:
        details = "\n".join(f"  - {type(error).__name__}: {error}" for error in errors[:12])
        raise DownloadError(f"download/conversion failed ({len(errors)} jobs):\n{details}")

    entries = sorted(results, key=lambda row: (row["kind"], row["symbol"], row["day"]))
    manifest = {
        "schema": "binance_public_market_data/1",
        "source": "Binance Data Vision, USD-M Futures daily archives",
        "window_utc": {"start_inclusive": days[0], "end_exclusive": (dt.date.fromisoformat(days[-1]) + dt.timedelta(days=1)).isoformat()},
        "symbols": symbols,
        "types": selected_types,
        "bookTicker_availability": bookticker_summary,
        "bookDepth_limitation": "Cumulative percentage bands, not exact price-level L2 updates or queue history.",
        "entries": entries,
    }
    _write_json_atomic(root / "public_l2_manifest.json", manifest, root)
    downloaded = sum(row["status"] == "downloaded" for row in entries)
    skipped = len(entries) - downloaded
    for kind in selected_types:
        group = [row for row in entries if row["kind"] == kind]
        print(f"{kind}: {len(group)} daily files, {sum(row['output_rows'] for row in group):,} records, "
              f"{sum(row['output_bytes'] for row in group):,} gzip bytes")
    depth_flags = sum(len(row.get("quality_flags", [])) for row in entries if row["kind"] == "bookDepth")
    if depth_flags:
        print(f"bookDepth quality flags: {depth_flags} flat-band alerts; inspect manifest band_diagnostics")
    print(f"Done: downloaded={downloaded}, skipped={skipped}; manifest={root / 'public_l2_manifest.json'}")
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT / "data"), help="data root; must resolve on F:")
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--start-date", default="2026-09-01", help="UTC start date, inclusive")
    parser.add_argument("--days", type=int, default=14, help="number of consecutive UTC days")
    parser.add_argument("--types", nargs="+", choices=SUPPORTED_TYPES, default=list(SUPPORTED_TYPES))
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--space-factor", type=float, default=4.0,
                        help="conservative multiplier for compressed archive bytes")
    parser.add_argument("--reserve-gb", type=float, default=1.0,
                        help="minimum free F: space to preserve after the run")
    parser.add_argument("--keep-archives", action="store_true", help="retain verified ZIPs under data/.public_l2_cache")
    parser.add_argument("--replace-existing", action="store_true", help="replace mismatched or unverified outputs")
    parser.add_argument("--audit-only", action="store_true", help="probe archive paths/checksums without writing data")
    args = parser.parse_args(argv)
    if args.days < 1 or args.days > 366 or args.concurrency < 1 or args.concurrency > 16:
        parser.error("--days must be 1..366 and --concurrency must be 1..16")
    if args.space_factor < 1.0 or args.reserve_gb < 0:
        parser.error("--space-factor must be >= 1 and --reserve-gb must be >= 0")
    try:
        dt.date.fromisoformat(args.start_date)
    except ValueError as exc:
        parser.error(f"invalid --start-date: {exc}")
    if len(set(s.upper() for s in args.symbols)) != len(args.symbols):
        parser.error("--symbols must be unique")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.audit_only:
        try:
            ensure_f_drive(Path(args.root))
        except DownloadError as exc:
            print(f"F: disk guard: {exc}", file=sys.stderr)
            return 2
    try:
        return asyncio.run(run(args))
    except (DownloadError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
