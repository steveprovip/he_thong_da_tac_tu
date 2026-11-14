import gzip
import http.server
import json
import threading
import zipfile

import pytest

from tools import download_public_l2 as dl


def _zip_csv(path, name, header, rows):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        lines = [header, *rows]
        archive.writestr(name, "\n".join(lines) + "\n")
    return path


def test_archive_url_matches_binance_vision_layout():
    assert dl.archive_url("aggTrades", "BTCUSDT", "2026-09-01") == (
        "https://data.binance.vision/data/futures/um/daily/aggTrades/BTCUSDT/"
        "BTCUSDT-aggTrades-2026-09-01.zip"
    )
    assert dl.archive_url("bookDepth", "SOLUSDT", "2026-09-14").endswith(
        "/bookDepth/SOLUSDT/SOLUSDT-bookDepth-2026-09-14.zip"
    )


def test_checksum_parser_accepts_binance_sha256_manifest():
    digest = "a" * 64
    assert dl.parse_checksum(f"{digest}  BTCUSDT-aggTrades-2026-09-01.zip\n") == digest
    with pytest.raises(dl.DownloadError):
        dl.parse_checksum("not-a-checksum")


def test_f_drive_guard_rejects_c_and_resolves_f():
    with pytest.raises(dl.DownloadError, match="outside F:"):
        dl.ensure_f_drive("C:/market-data")
    assert dl.ensure_f_drive("F:/market-data").drive.upper() == "F:"


def test_convert_aggtrades_to_compressed_jsonl(tmp_path):
    archive = _zip_csv(
        tmp_path / "trades.zip",
        "BTCUSDT-aggTrades-2026-09-01.csv",
        "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker",
        [
            "1,100.0,5.0,1,1,1788220800003,false",
            "2,100.1,3.0,2,2,1788220801000,true",
        ],
    )
    output = tmp_path / "ticks.jsonl.gz"

    stats = dl.convert_archive(archive, output, "aggTrades", "BTCUSDT", "2026-09-01")
    with gzip.open(output, "rt", encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle]

    assert stats["output_rows"] == stats["source_rows"] == 2
    assert records[0]["ch"] == "aggTrade"
    assert records[0]["s"] == "BTCUSDT"
    assert records[0]["m"] is False
    assert records[1]["m"] is True
    assert records[0]["source_time_unit"] == "ms"
    assert records[0]["ts_ns"] < records[1]["ts_ns"]


def test_convert_depth_keeps_percentage_bands_and_groups_snapshots(tmp_path):
    archive = _zip_csv(
        tmp_path / "depth.zip",
        "BTCUSDT-bookDepth-2026-09-01.csv",
        "timestamp,percentage,depth,notional",
        [
            "2026-09-01 00:00:04,-0.20,1000.0,100000.0",
            "2026-09-01 00:00:04,0.20,1000.0,100200.0",
            "2026-09-01 00:01:04,-0.20,1100.0,110000.0",
        ],
    )
    output = tmp_path / "depth.jsonl.gz"

    stats = dl.convert_archive(archive, output, "bookDepth", "BTCUSDT", "2026-09-01")
    with gzip.open(output, "rt", encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle]

    assert stats["output_rows"] == 2
    assert stats["source_rows"] == 3
    assert records[0]["ch"] == "bookDepthBands"
    assert [band["percentage"] for band in records[0]["levels"]] == [-0.2, 0.2]
    assert "price" not in records[0]["levels"][0]


def test_convert_rejects_timestamps_outside_the_archive_day(tmp_path):
    archive = _zip_csv(
        tmp_path / "trades.zip",
        "trades.csv",
        "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker",
        ["1,100.0,5.0,1,1,1788307200000,false"],
    )
    with pytest.raises(dl.DownloadError, match="outside requested UTC day"):
        dl.convert_archive(archive, tmp_path / "bad.jsonl.gz", "aggTrades", "BTCUSDT", "2026-09-01")


def test_depth_audit_flags_flat_ask_bands(tmp_path):
    output = tmp_path / "flat.jsonl.gz"
    with gzip.open(output, "wt", encoding="utf-8") as handle:
        for idx in range(100):
            handle.write(json.dumps({"levels": [{"percentage": 0.2, "depth": 10.0, "notional": 100.0}]}) + "\n")

    audit = dl.audit_depth_jsonl(output)
    assert audit["quality_flags"] == ["flat_band:+0.20%"]
    assert audit["band_diagnostics"]["+0.20%"]["observations"] == 100


def test_download_resumes_with_http_range(tmp_path):
    payload = b"verified public market-data archive bytes"
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            range_value = self.headers.get("Range")
            requests.append(range_value)
            start = int(range_value.partition("=")[2].partition("-")[0]) if range_value else 0
            body = payload[start:]
            self.send_response(206 if range_value else 200)
            self.send_header("Content-Length", str(len(body)))
            if range_value:
                self.send_header("Content-Range", f"bytes {start}-{len(payload)-1}/{len(payload)}")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    part = tmp_path / "resume.zip.part"
    part.write_bytes(payload[:9])
    try:
        dl.download_resumable(
            f"http://127.0.0.1:{server.server_port}/archive.zip",
            part,
            len(payload),
            tmp_path,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert requests == [f"bytes=9-"]
    assert part.read_bytes() == payload
