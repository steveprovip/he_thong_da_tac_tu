#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_menu.py - Interactive Console Dashboard & Launcher for Autonomous Multi-Agent Swarm.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

# Add core, simulation, benchmarks to sys.path
ROOT = Path(__file__).resolve().parent
for p in (ROOT / "core", ROOT / "simulation", ROOT / "benchmarks"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from system_stabilizer import SystemStabilizer, SafeAutoCleaner


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def print_banner():
    c_free = SystemStabilizer.get_disk_free_gb("C:\\")
    f_free = SystemStabilizer.get_disk_free_gb("F:\\")
    print("=" * 76)
    print("      🐙 GROKTOPUS - 8 AGENTS, 8 BRAINS, 1 BODY - CONSOLE DASHBOARD")
    print("           Target: RTX 5060 Ti 16GB | i5-12400F | 32GB RAM")
    print("=" * 76)
    print(f" [Disk Status] Drive C: {c_free:.2f} GB Free | Drive F: {f_free:.2f} GB Free")
    print("-" * 76)


def run_hardware_check():
    print("\n🔍 Đang chạy kiểm tra phần cứng & VRAM...")
    subprocess.run([sys.executable, "benchmarks/hardware_check.py", "--profile", "S"], cwd=str(ROOT))


def run_groktopus_web_ui():
    print("\n🐙 Đang bật Giao diện Web UI GrokTOPUS (bạch tuộc 8 xúc tu)...")
    print("   Địa chỉ: http://127.0.0.1:8765  (chỉ loopback, chỉ đọc, không đặt lệnh được)")
    print("   Trình duyệt sẽ tự mở. Nhấn Ctrl+C trong cửa sổ này để tắt máy chủ.")
    subprocess.run([sys.executable, "core/web_ui_server.py", "--port", "8765"], cwd=str(ROOT))


def run_groktopus_swarm_demo():
    print("\n🐙 Đang chạy Swarm GrokTOPUS 8 tác tử (quét → bỏ phiếu → VESKA → MARIN)...")
    subprocess.run([sys.executable, "core/groktopus_swarm.py", "--demo", "--cycles", "400"], cwd=str(ROOT))


def run_groktopus_campaign():
    print("\n📊 Đang chạy chiến dịch tham chiếu 1.981 lệnh trên sàn mô phỏng 8 cặp...")
    subprocess.run([sys.executable, "simulation/groktopus_market.py", "--demo"], cwd=str(ROOT))


def run_consensus_demo():
    print("\n🗳️ Đang minh hoạ cổng đồng thuận 76.8% với bốn kịch bản bỏ phiếu...")
    subprocess.run([sys.executable, "core/consensus_engine.py", "--demo"], cwd=str(ROOT))


def run_memory_governor():
    print("\n📐 Ngân sách bộ nhớ: cấu hình nào lọt trần, và lệch bao nhiêu nếu không...")
    subprocess.run([sys.executable, "core/memory_governor.py", "--budget", "TIGHT", "--report"], cwd=str(ROOT))
    print("\n--- Mốc 7.8 GB trong chỉ thị gốc ---")
    subprocess.run([sys.executable, "core/memory_governor.py", "--budget", "ULTRA", "--report"], cwd=str(ROOT))


def run_inference_planner():
    print("\n⚡ Kế hoạch thông lượng: 300 tok/s đơn luồng vs tổng gộp, batch vs speculative...")
    subprocess.run([sys.executable, "core/inference_planner.py", "--target", "300"], cwd=str(ROOT))


def run_throughput_bench():
    print("\n⏱️  Đo tốc độ THẬT (cần llama-server đang chạy)...")
    rc = subprocess.run(
        [sys.executable, "benchmarks/throughput_bench.py",
         "--mode", "serial", "--mode", "batched", "--slots", "6", "--calibrate"],
        cwd=str(ROOT),
    ).returncode
    if rc == 3:
        print("\n   → Chưa có server. Bật bằng chức năng 9 hoặc dùng dòng lệnh batch ở chức năng 16.")


def run_market_feed():
    print("\n📡 Dữ liệu thị trường công khai (chỉ đọc, không khoá API, có giới hạn nhịp)...")
    subprocess.run([sys.executable, "core/market_feed.py", "--venue", "coinbase"], cwd=str(ROOT))


def run_edge_report():
    print("\n📊 Đo edge out-of-sample (walk-forward, t-stat, hiệu chỉnh đa phép thử)...")
    print("   Đây là câu hỏi 'hệ thống này có edge thật không'. Kết quả có thể là KHÔNG.")
    subprocess.run([sys.executable, "benchmarks/edge_report.py",
                    "--folds", "6", "--cycles", "900", "--steps", "6000", "--write"], cwd=str(ROOT))


def run_router_preflight():
    print("\n🛫 Preflight bộ định tuyến lệnh (không gửi gì lên mạng)...")
    for mode in ("SIMULATION", "TESTNET", "MAINNET"):
        print(f"\n--- {mode} ---")
        subprocess.run([sys.executable, "live/live_execution_router.py",
                        "--mode", mode, "--preflight"], cwd=str(ROOT))


def run_orchestrator_demo():
    print("\n🐝 Đang chạy thử nghiệm Swarm DAG với 5 tác tử...")
    subprocess.run([sys.executable, "core/orchestrator.py", "--demo"], cwd=str(ROOT))


def run_risk_guard_status():
    print("\n🛡️ Đang truy vấn trạng thái Sentry Guard...")
    subprocess.run([sys.executable, "core/risk_guard.py", "--status"], cwd=str(ROOT))


def run_model_profiles_check():
    print("\n🧠 Đang kiểm tra danh sách Model AI GGUF nội bộ...")
    subprocess.run([sys.executable, "core/local_llm_client.py", "--verify-profiles"], cwd=str(ROOT))


def run_monte_carlo():
    print("\n📈 Đang chạy mô phỏng tài chính Monte Carlo (1000 lượt)...")
    subprocess.run([sys.executable, "simulation/sandbox_env.py", "--montecarlo", "--trials", "1000"], cwd=str(ROOT))


def run_paper_run_demo():
    print("\n💵 Đang chạy thử nghiệm Paper Run (Sandbox Mode)...")
    subprocess.run([sys.executable, "simulation/sandbox_env.py", "--demo"], cwd=str(ROOT))


def run_auto_clean():
    print("\n🧹 Đang quét và dọn dẹp các mục rác an toàn...")
    res = SafeAutoCleaner.clean_safe_artifacts()
    print(f" -> Đã giải phóng: {res['freed_mb']} MB")
    if res['cleaned_items']:
        for item in res['cleaned_items']:
            print(f"   • Đã xóa: {item}")
    else:
        print("   • Hệ thống đã sạch sẽ, không có file rác phát sinh.")


def run_pytest():
    print("\n🧪 Đang chạy toàn bộ bộ kiểm thử tự động (230 test cases)...")
    subprocess.run([sys.executable, "-m", "pytest", "tests/", "-v"], cwd=str(ROOT))


def run_llama_server():
    server_exe = Path(r"F:\llama.cpp\llama-server.exe")
    model_path = Path(r"D:\models\Qwen3.8-27B-UD-IQ2_XXS.gguf")
    if not server_exe.exists():
        print(f"❌ Không tìm thấy llama-server.exe tại {server_exe}")
        return
    if not model_path.exists():
        print(f"❌ Không tìm thấy model weights tại {model_path}")
        return

    cmd = (
        f'start "Llama Server - Qwen 27B IQ2_XXS" "{server_exe}" '
        f'-m "{model_path}" --ctx-size 8192 --cache-type-k q4_0 --cache-type-v q4_0 '
        f'--n-gpu-layers 99 --threads 8 --parallel 1 --host 127.0.0.1 --port 8080 --flash-attn'
    )
    print(f"\n🚀 Đang khởi động Llama Server trong cửa sổ riêng...")
    print(f"   Model: {model_path.name}")
    print(f"   VRAM dự kiến: ~9.07 GB (Phù hợp mức trần 10.0 GB)")
    print(f"   Endpoint: http://127.0.0.1:8080")
    os.system(cmd)
    print("✅ Đã mở cửa sổ Llama Server!")


def run_realtime_monitor():
    from core.realtime_monitor import live_loop
    live_loop(interval_s=1.5)


def main():
    SystemStabilizer()  # Set priority to below normal for smooth UI
    while True:
        clear_screen()
        print_banner()
        print(" [CHỨC NĂNG THỬ NGHIỆM & ĐIỀU KHIỂN]")
        print("  1. 🖥️  Kiểm tra phần cứng & Bộ nhớ VRAM (Hardware Check)")
        print("  2. 🐝  Chạy Swarm DAG đa tác tử mẫu (Multi-Agent DAG Demo)")
        print("  3. 🛡️  Kiểm tra trạng thái Sentry, Ngân sách, VRAM (Risk Guard Status)")
        print("  4. 🧠  Kiểm tra các Profile Model AI nội bộ (Verify Profiles S/Q/T)")
        print("  5. 💵  Chạy mô phỏng chiến dịch Paper Run (Sandbox Demo)")
        print("  6. 📈  Chạy phân tích rủi ro Monte Carlo (1000 trials)")
        print("  7. 🧹  Quét và dọn dẹp an toàn ổ đĩa ngay (Safe Auto-Cleaner)")
        print("  8. 🧪  Chạy kiểm thử toàn bộ hệ thống (Pytest All Suites)")
        print("  9. 🚀  Bật Local Llama Server cho Model Qwen 27B Profile S")
        print(" 10. 📡  Bật Giám sát Tài nguyên & Swarm Thời gian thực (Real-Time Monitor)")
        print("-" * 76)
        print(" [GROKTOPUS - 8 AGENTS, 8 BRAINS, 1 BODY]")
        print(" 11. 🐙  Bật Giao diện Web UI GrokTOPUS (bạch tuộc 8 xúc tu)")
        print(" 12. 🧭  Chạy Swarm GrokTOPUS 8 tác tử (400 vòng đầy đủ pipeline)")
        print(" 13. 📊  Chạy chiến dịch tham chiếu 1.981 lệnh ($150K → ~$198K)")
        print(" 14. 🗳️   Minh hoạ cổng đồng thuận 76.8% (4 kịch bản bỏ phiếu)")
        print("-" * 76)
        print(" [HIỆU NĂNG & DỮ LIỆU THẬT]")
        print(" 15. 📐  Ngân sách VRAM/RAM: cấu hình nào lọt trần 9.5 GB (Memory Governor)")
        print(" 16. ⚡  Kế hoạch thông lượng: batch 6 slot vs speculative (Inference Planner)")
        print(" 17. ⏱️   Đo tốc độ THẬT qua llama-server (Throughput Benchmark)")
        print(" 18. 📡  Xem giá công khai chỉ-đọc từ sàn (Public Market Feed)")
        print("-" * 76)
        print(" [TRUNG THỰC ĐỊNH LƯỢNG & THI HÀNH LỆNH]")
        print(" 19. 📊  Đo edge out-of-sample (Edge Report - có thể trả lời KHÔNG)")
        print(" 20. 🛫  Preflight router: điều kiện nào đang chặn MAINNET")
        print("  0. ❌  Thoát (Exit)")
        print("=" * 76)

        choice = input("👉 Nhập số chức năng muốn chạy (0-20): ").strip()
        if choice == "1":
            run_hardware_check()
        elif choice == "2":
            run_orchestrator_demo()
        elif choice == "3":
            run_risk_guard_status()
        elif choice == "4":
            run_model_profiles_check()
        elif choice == "5":
            run_paper_run_demo()
        elif choice == "6":
            run_monte_carlo()
        elif choice == "7":
            run_auto_clean()
        elif choice == "8":
            run_pytest()
        elif choice == "9":
            run_llama_server()
        elif choice == "10":
            run_realtime_monitor()
        elif choice == "11":
            run_groktopus_web_ui()
        elif choice == "12":
            run_groktopus_swarm_demo()
        elif choice == "13":
            run_groktopus_campaign()
        elif choice == "14":
            run_consensus_demo()
        elif choice == "15":
            run_memory_governor()
        elif choice == "16":
            run_inference_planner()
        elif choice == "17":
            run_throughput_bench()
        elif choice == "18":
            run_market_feed()
        elif choice == "19":
            run_edge_report()
        elif choice == "20":
            run_router_preflight()
        elif choice == "0":
            print("\nĐã thoát. Tạm biệt!")
            break
        else:
            print("\nLựa chọn không hợp lệ. Vui lòng chọn từ 0 đến 20.")
        
        input("\nẤn Enter để quay lại Menu...")


if __name__ == "__main__":
    main()
