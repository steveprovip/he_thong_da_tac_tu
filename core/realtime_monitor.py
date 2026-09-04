#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
realtime_monitor.py - Real-time Live Telemetry & Monitoring Dashboard.

Polls hardware metrics (RTX 5060 Ti VRAM, host RAM, disk free) and SQLite state
(Sentry ledger, DAG node execution progress) every 1-2 seconds with zero lock overhead.
"""

from __future__ import annotations

import datetime
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

# Add core to sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "core") not in sys.path:
    sys.path.insert(0, str(ROOT / "core"))

from risk_guard import (
    DEFAULT_DB as GUARD_DB,
    read_ram_gb,
    read_vram_gb,
    read_process_tree_ram_gb,
)
from system_stabilizer import SystemStabilizer

ENGINE_DB = ROOT / "state" / "engine.db"


def make_bar(used: float, ceiling: float, length: int = 20) -> str:
    """Render a visual ASCII progress bar."""
    if ceiling <= 0:
        return "[" + " " * length + "]"
    ratio = min(max(used / ceiling, 0.0), 1.0)
    filled = int(round(ratio * length))
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}] {used:.2f}/{ceiling:.2f} GB ({int(ratio * 100)}%)"


def get_guard_metrics() -> dict:
    metrics = {"balance": 0, "spent_total": 0, "killed": False}
    if GUARD_DB.exists():
        try:
            conn = sqlite3.connect(f"file:{GUARD_DB}?mode=ro", uri=True, timeout=1.0)
            cur = conn.cursor()
            row = cur.execute("SELECT COALESCE(SUM(delta_minor), 0) FROM ledger").fetchone()
            if row:
                metrics["balance"] = row[0]
            row_spent = cur.execute("SELECT COALESCE(-SUM(delta_minor), 0) FROM ledger WHERE delta_minor < 0").fetchone()
            if row_spent:
                metrics["spent_total"] = row_spent[0]
            row_kill = cur.execute("SELECT value FROM guard_state WHERE key='kill_switch'").fetchone()
            if row_kill and row_kill[0] == "1":
                metrics["killed"] = True
            conn.close()
        except Exception:
            pass
    return metrics


def get_engine_metrics() -> dict:
    metrics = {"active_run": None, "nodes_done": 0, "nodes_running": 0, "nodes_blocked": 0, "nodes_pending": 0}
    if ENGINE_DB.exists():
        try:
            conn = sqlite3.connect(f"file:{ENGINE_DB}?mode=ro", uri=True, timeout=1.0)
            cur = conn.cursor()
            row = cur.execute("SELECT run_id, status FROM runs ORDER BY id DESC LIMIT 1").fetchone()
            if row:
                metrics["active_run"] = f"{row[0][:8]} ({row[1]})"
                run_id = row[0]
                rows = cur.execute("SELECT state, count(*) FROM nodes WHERE run_id=? GROUP BY state", (run_id,)).fetchall()
                for st, cnt in rows:
                    if st == "DONE":
                        metrics["nodes_done"] = cnt
                    elif st == "RUNNING":
                        metrics["nodes_running"] = cnt
                    elif st == "BLOCKED":
                        metrics["nodes_blocked"] = cnt
                    elif st in ("PENDING", "READY"):
                        metrics["nodes_pending"] += cnt
            conn.close()
        except Exception:
            pass
    return metrics


def render_dashboard(vram_ceiling: float = 10.0, ram_ceiling: float = 6.0):
    vram_used, vram_total, _ = read_vram_gb()
    vram_u = vram_used if vram_used is not None else 0.0
    vram_t = vram_total if vram_total is not None else 16.0

    sys_ram_used, sys_ram_total, _ = read_ram_gb()
    sys_ram_u = sys_ram_used if sys_ram_used is not None else 0.0
    sys_ram_t = sys_ram_total if sys_ram_total is not None else 32.0
    swarm_ram = read_process_tree_ram_gb()

    c_free = SystemStabilizer.get_disk_free_gb("C:\\")
    f_free = SystemStabilizer.get_disk_free_gb("F:\\")

    guard = get_guard_metrics()
    engine = get_engine_metrics()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    os.system("cls" if os.name == "nt" else "clear")
    print("=" * 76)
    print(f"   📡 REAL-TIME MULTI-AGENT SWARM MONITOR   |  {now_str}")
    print("=" * 76)

    # 1. Hardware Resource Telemetry
    print(" [1. THÔNG SỐ TÀI NGUYÊN THỜI GIAN THỰC]")
    vram_bar = make_bar(vram_u, vram_ceiling)
    print(f"  • GPU VRAM (Trần 10 GB) : {vram_bar}")
    print(f"    (Dung lượng card thật : {vram_u:.2f} / {vram_t:.2f} GB)")

    swarm_ram_bar = make_bar(swarm_ram, ram_ceiling)
    print(f"  • Swarm RAM (Trần 6 GB) : {swarm_ram_bar}")
    print(f"    (Hệ thống Windows     : Đang dùng {sys_ram_u:.2f} GB / Tổng {sys_ram_t:.2f} GB)")

    c_alert = "⚠️ SẮP ĐẦY" if c_free < 10.0 else "✅ Tốt"
    f_alert = "⚠️ SẮP ĐẦY" if f_free < 10.0 else "✅ Tốt"
    print(f"  • Ổ C: Trống {c_free:.2f} GB [{c_alert}]  |  Ổ F: Trống {f_free:.2f} GB [{f_alert}]")
    print("-" * 76)

    # 2. Financial & Sentry Status
    print(" [2. TRẠNG THÁI SENTRY & NGÂN SÁCH CHIẾN DỊCH]")
    kill_text = "🚨 ĐÃ BẬT (DỪNG KHẨN CẤP)" if guard["killed"] else "🟢 BÌNH THƯỜNG (HOẠT ĐỘNG)"
    print(f"  • Công tắc Kill-Switch : {kill_text}")
    print(f"  • Số dư ngân sách ảo   : {guard['balance']:,} VND (Đã tiêu: {guard['spent_total']:,} VND)")
    print("-" * 76)

    # 3. DAG Execution Stream
    print(" [3. TIẾN ĐỘ THI HÀNH SWARM DAG]")
    if engine["active_run"]:
        print(f"  • Phiên chạy gần nhất : Run ID {engine['active_run']}")
        print(f"  • Trạng thái Node     : DONE: {engine['nodes_done']} | RUNNING: {engine['nodes_running']} | PENDING: {engine['nodes_pending']} | BLOCKED: {engine['nodes_blocked']}")
    else:
        print("  • Chưa có phiên chạy DAG nào đang active.")
    print("=" * 76)
    print(" [Nhấn Ctrl+C để dừng giám sát và quay về Menu chính]")


def live_loop(interval_s: float = 1.5):
    SystemStabilizer()
    try:
        while True:
            render_dashboard()
            time.sleep(interval_s)
    except KeyboardInterrupt:
        print("\n\nĐã dừng giám sát thời gian thực.")


if __name__ == "__main__":
    live_loop()
