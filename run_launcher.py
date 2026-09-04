#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_launcher.py - Simplified Entrypoint for GrokTOPUS.
Handles exactly 2 modes:
  1. Live Mode: Real-time Public Market Feed + Local Qwen LLM + Web UI Dashboard
  2. Simulation Mode: 1,981-trade Paper Trading Campaign + Web UI Dashboard
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for p in (ROOT / "core", ROOT / "simulation", ROOT / "benchmarks"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from system_stabilizer import SystemStabilizer


def is_server_running(url: str = "http://127.0.0.1:8080/health") -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "GrokTOPUS-Launcher"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.status in (200, 404)  # Server is alive
    except Exception:
        return False


def start_llama_server_if_needed():
    if is_server_running():
        print("  ✅ Local Llama Server đang hoạt động tại http://127.0.0.1:8080")
        return

    server_exe = Path(r"F:\llama.cpp\llama-server.exe")
    model_path = Path(r"D:\models\Qwen3.8-27B-UD-IQ2_XXS.gguf")

    if not server_exe.exists() or not model_path.exists():
        print("  ⚠️ Không tìm thấy llama-server hoặc file model. Swarm sẽ dùng heuristic_brain.")
        return

    cmd = (
        f'start "Llama Server - Qwen 27B Profile S (6 Slots)" "{server_exe}" '
        f'-m "{model_path}" --ctx-size 24576 --parallel 6 --cache-type-k q4_0 --cache-type-v q4_0 '
        f'--n-gpu-layers 99 --threads 8 --host 127.0.0.1 --port 8080 --flash-attn --cont-batching'
    )
    print("  🚀 Đang tự động khởi động Llama Server (Batch 6 slot, Qwen 27B)...")
    os.system(cmd)
    print("  ⏳ Đang đợi model nạp vào VRAM (khoảng 3-5 giây)...")
    for _ in range(10):
        time.sleep(1.0)
        if is_server_running():
            print("  ✅ Llama Server đã sẵn sàng trong VRAM!")
            return
    print("  ℹ️ Llama Server đang khởi tạo ngầm.")


def run_live_mode():
    SystemStabilizer()
    print("=" * 76)
    print("      🐙 GROKTOPUS - CHẾ ĐỘ CHẠY THỰC TẾ (LIVE MODE)")
    print("============================================================================")
    print("  • Kiểm tra dữ liệu thị trường công khai thời gian thực...")
    try:
        from market_feed import MarketFeed
        feed = MarketFeed(venue="coinbase")
        prices = feed.fetch_all(["BTC-USD", "ETH-USD", "SOL-USD"])
        for sym, tick in prices.items():
            print(f"    - {sym}: ${tick.price_cents / 100:,.2f} (Coinbase Live)")
    except Exception as exc:
        print(f"    - Lưu ý feed: {exc}")

    print("\n  • Kiểm tra backend AI suy luận...")
    start_llama_server_if_needed()

    print("\n  • Đang khởi động Giao diện Web UI GrokTOPUS (Bạch tuộc 8 xúc tu)...")
    print("    Địa chỉ: http://127.0.0.1:8765")
    print("    Trình duyệt web sẽ tự động mở ra ngay bây giờ.")
    print("    (Nhấn Ctrl+C trong cửa sổ này để dừng hệ thống)")
    print("=" * 76)
    
    server_script = ROOT / "core" / "web_ui_server.py"
    subprocess.run([sys.executable, str(server_script), "--port", "8765", "--live"], cwd=str(ROOT))


def run_simulation_mode():
    SystemStabilizer()
    print("=" * 76)
    print("      🧪 GROKTOPUS - CHẾ ĐỘ CHẠY GIẢ LẬP (SIMULATION MODE)")
    print("============================================================================")
    print("  Chọn quy mô vốn thử nghiệm:")
    print("  [1] Vốn vi mô: 50,000 VNĐ  (Sàn Bybit / MEXC / Exness Cent micro-lot)")
    print("  [2] Vốn vi mô: 100,000 VNĐ (Chuẩn khuyến nghị vài chục nghìn VNĐ)")
    print("  [3] Vốn thể chế: $150,000 USD (Mô phỏng quỹ định lượng lớn)")
    print()
    try:
        cap_choice = input("👉 Chọn mức vốn (1, 2 hoặc 3 [Enter = 100,000 VNĐ]): ").strip()
    except (KeyboardInterrupt, EOFError):
        return

    server_script = ROOT / "core" / "web_ui_server.py"
    cmd = [sys.executable, str(server_script), "--port", "8765"]
    if cap_choice == "1":
        print("\n  • Khởi tạo Swarm với mức vốn: 50,000 VNĐ (Micro-Lots)...")
        cmd.extend(["--capital-vnd", "50000"])
    elif cap_choice == "3":
        print("\n  • Khởi tạo Swarm với mức vốn: $150,000 USD (Institutional)...")
    else:
        print("\n  • Khởi tạo Swarm với mức vốn: 100,000 VNĐ (Micro-Lots)...")
        cmd.extend(["--capital-vnd", "100000"])

    print("\n" + "=" * 76)
    print("  • Đang mở Web UI Dashboard mô phỏng tại http://127.0.0.1:8765 ...")
    print("    (Nhấn Ctrl+C trong cửa sổ này để dừng mô phỏng)")
    print("=" * 76)
    subprocess.run(cmd, cwd=str(ROOT))


def interactive_menu():
    while True:
        os.system("cls" if os.name == "nt" else "clear")
        print("==============================================================================")
        print("      🐙 GROKTOPUS - 8 AGENTS, 8 BRAINS, 1 BODY")
        print("==============================================================================")
        print()
        print("  [1] CHAY THUC TE  (Live Mode - Giá thật Coinbase, 8 Agent trên Model Local)")
        print("  [2] CHAY GIA LAP  (Simulation Mode - Paper Trading 1.981 lệnh $150K->$198K)")
        print("  [3] BINANCE TESTNET (Sàn Thật, Tiền Ảo $5,000 USDT - Lệnh & Stop-Loss Thật)")
        print("  [0] Thoát (Exit)")
        print()
        print("==============================================================================")
        try:
            choice = input("👉 Nhập lựa chọn của bạn (1, 2 hoặc 3): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nĐã thoát.")
            break

        if choice == "1":
            os.system("cls" if os.name == "nt" else "clear")
            run_live_mode()
            try:
                input("\nNhấn Enter để quay lại menu...")
            except (KeyboardInterrupt, EOFError):
                break
        elif choice == "2":
            os.system("cls" if os.name == "nt" else "clear")
            run_simulation_mode()
            try:
                input("\nNhấn Enter để quay lại menu...")
            except (KeyboardInterrupt, EOFError):
                break
        elif choice == "3":
            os.system("cls" if os.name == "nt" else "clear")
            import run_binance_testnet
            run_binance_testnet.main()
        elif choice == "0":
            print("\nTạm biệt!")
            sys.exit(0)
        else:
            print("\n❌ Lựa chọn không hợp lệ! Vui lòng chỉ nhập 1, 2 hoặc 3.")
            time.sleep(1.2)


def main():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="GrokTOPUS Simple Launcher")
    parser.add_argument("--live", action="store_true", help="Chạy thực tế (Live)")
    parser.add_argument("--sim", action="store_true", help="Chạy giả lập (Simulation)")
    parser.add_argument("mode", nargs="?", default=None, help="1 hoặc 2")
    args = parser.parse_args()

    if args.live or args.mode in ("1", "live"):
        run_live_mode()
    elif args.sim or args.mode in ("2", "sim"):
        run_simulation_mode()
    else:
        interactive_menu()


if __name__ == "__main__":
    main()

