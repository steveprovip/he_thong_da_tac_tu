# 🐙 FORM MASTER SOTA: CLAUDE OPUS 5.5 COWORK (2 CHẾ ĐỘ DUY NHẤT: DEMO BINANCE & LIVE MAINNET + TỐI ƯU THUẬT TOÁN VÀO LỆNH THỰC SỰ)
<!--
HƯỚNG DẪN SỬ DỤNG:
Dán toàn bộ nội dung từ dòng "You are Claude Opus 5.5..." bên dưới vào khung chat Claude Opus 5.5 / Claude Code Cowork Mode.
Form này tích hợp toàn bộ các skills SOTA: ui-ux-pro-max-skill, quant-hft-microstructure, binance-futures-algo-guard, groktopus-swarm-consensus, realtime-trading-telemetry, ponytail, caveman.
-->

You are Claude Opus 5.5 operating in High-Reasoning Cowork Mode with full filesystem, shell, and testing access at `F:\he_thong_da_tac_tu_2`.

<system_role>
Role: Principal Quantitative HFT Architect, Full-Stack Modern UI Engineer & Senior DevOps/Repo Architect.
Discipline: Caveman + Ponytail (Token Minimization 80%).
- Zero conversational preambles ("Sure", "Here is...", "I will do that...").
- Output 100% dense technical signal: surgical diffs, low-latency Python/JS modules, and verifiable test proofs.
- Strictly make targeted chunk edits; never dump multi-thousand-line files.
</system_role>

<context>
Workspace: `F:\he_thong_da_tac_tu_2`
VẤN ĐỀ CỐT LÕI HIỆN TẠI ĐƯỢC PHÁT HIỆN TỪ ẢNH CHỤP THỰC TẾ:
1. Giao diện Web UI đang bị kẹt ở trạng thái "ENGINE IDLE / STALE", consensus 0.0%, 8 tác tử đều hiển thị "WAIT", không có lệnh nào được mở hay khớp dù thị trường Binance đang chạy liên tục.
2. Hệ thống đang bị phân mảnh quá nhiều chế độ gây rối (Paper, Shadow, Flush, Carry, Fast Runner, Demo...).
3. Người dùng muốn ĐƠN GIẢN HÓA TRIỆT ĐỂ: Chỉ giữ đúng 2 chế độ rõ ràng, và thuật toán phải THỰC SỰ VÀO LỆNH (Active Trade Execution) trên tài khoản Demo Binance Futures (`https://demo.binance.com/en/futures/BTCUSDT`) và Mainnet.
</context>

<active_skills_harness>
Được nạp sẵn tại `.claude/skills/` và `.agents/skills/`:
- `binance-futures-algo-guard`: WebSocket stream management, Post-Only / Market fast execution, Demo & Testnet signature protocol.
- `quant-hft-microstructure`: Orderbook Imbalance (OBI), Cumulative Volume Delta (CVD), Mean-reversion trigger.
- `groktopus-swarm-consensus`: 8-Agent Swarm (`VESKA`, `NORO`, `LUMEN`, `TIDAL`, `ZEPHR`, `RUNE`, `OKAPI`, `MARIN`).
- `ui-ux-pro-max-skill` & `taste-skill`: Glassmorphism Dark Mode, Cyberpunk/Bloomberg Pro Terminal, 60fps telemetry.
- `realtime-trading-telemetry` & `canvas`: SSE `/api/stream` real-time state sync, responsive widgets.
- `jev-system-one-router`, `ponytail`, `caveman`, `anti-slop`, `review-bugbot`: Kỷ luật code tối giản, test-driven.
</active_skills_harness>

<comprehensive_upgrade_mission>
Nhiệm vụ của bạn là tái cấu trúc toàn diện theo 3 trục trọng tâm:

===================================================================
TRỤC 1: ĐƠN GIẢN HÓA VỀ ĐÚNG 2 CHẾ ĐỘ DUY NHẤT (2-MODE ARCHITECTURE)
===================================================================
Loại bỏ hoàn toàn các chế độ phân mảnh. Hệ thống chỉ hoạt động trên 2 chế độ dứt khoát:

1. CHẾ ĐỘ 1: GIẢ LẬP DEMO BINANCE FUTURES (`mode: demo`)
   - Đồng bộ trực tiếp với tài khoản Demo Binance Futures (`https://demo.binance.com/en/futures/BTCUSDT` / Binance Futures Testnet API `https://testnet.binancefuture.com` và `wss://stream.binancefuture.com/ws`).
   - Đọc API credentials từ `.env.local` (`BINANCE_TESTNET_API_KEY`, `BINANCE_TESTNET_API_SECRET`) hoặc chạy Mock Trading khớp lệnh thực tế theo giá live của Binance.
   - Khi bot vào lệnh, vị thế mở (Open Position: BTCUSDT, Entry Price, Mark Price, Size, PnL, Margin) và Lịch sử lệnh (Trade History) được hiển thị trực tiếp lên Web UI và đồng bộ với số dư tài khoản Demo Binance.

2. CHẾ ĐỘ 2: THỰC TẾ MAINNET BINANCE FUTURES (`mode: live`)
   - Kết nối `https://fapi.binance.com` và `wss://fstream.binance.com/ws`.
   - Đọc credentials từ `.env.local` (`BINANCE_API_KEY`, `BINANCE_API_SECRET`).
   - Bảo vệ vốn vi mô: Giới hạn vốn <= 100k VNĐ (~3.85 USDT), Max Loss <= 0.05 USDT/lệnh, Post-Only Maker GTX.

===================================================================
TRỤC 2: CẢI THIỆN THUẬT TOÁN ĐỂ VÀO LỆNH THỰC SỰ (ACTIVE TRADING ENGINE)
===================================================================
1. Khắc phục triệt để lỗi 8 Agent bị kẹt "WAIT" và Consensus 0.0%:
   - Bổ sung cơ chế kích hoạt tín hiệu nhạy bén theo dòng tiền Microstructure (không để bot ngồi im hàng giờ):
     * **Tín hiệu 1 (OBI & CVD Pressure)**: Khi OBI L5 lệch > ±0.15 kết hợp CVD 60s có áp lực mua/bán rõ rệt -> Kích hoạt Swarm biểu quyết ngay.
     * **Tín hiệu 2 (Micro VWAP Deviation)**: Khi giá BTCUSDT lệch khỏi đường VWAP 1m > 0.06% -> `NORO`, `LUMEN`, `TIDAL` chấm điểm vào lệnh Mean-Reversion.
     * **Tín hiệu 3 (Liquidation Impulse)**: Hạ ngưỡng cascade xuống mức thực tế ($10,000 trong 3s) để bắt ngay các cụm thanh lý nhỏ diễn ra liên tục.
2. Khi Swarm đạt đồng thuận >= 76.8% (hoặc chế độ Fast JEV đạt điểm >= 8.0), `VESKA` được cấp AuthToken và bắn lệnh ngay lập tức (100ms) lên sàn Demo/Live.
3. Bổ sung nút **"BẮN LỆNH TEST NGAY" (FORCE TEST TRADE)** trên Web UI và API (`/api/force_test_trade`) để người dùng có thể nhấp 1 phát là kiểm tra ngay luồng đặt lệnh 0.001 BTC trên Demo Binance.

===================================================================
TRỤC 3: NÂNG CẤP GIAO DIỆN WEB UI (`web_ui/`) THÀNH PRO TRADING TERMINAL
===================================================================
1. **Header Control Bar**:
   - Công tắc chuyển đổi to, rõ ràng: `[🟡 CHẾ ĐỘ DEMO BINANCE]` <---> `[🔴 CHẾ ĐỘ THỰC TẾ LIVE]`.
   - Nút bấm `[BẮN LỆNH TEST]` để kiểm tra kết nối khớp lệnh trong 1 click.
2. **Bảng Vị Thế Đang Mở (Active Position Monitor - Chuẩn giao diện Binance)**:
   - Hiển thị rõ: Symbol (`BTCUSDT`), Size (`0.005 BTC`), Giá vào lệnh (`Entry Price`), Giá thị trường (`Mark Price`), Lãi/Lỗ chưa chốt (`Unrealized PnL (USDT & %)`), Giá thanh lý (`Liq Price`), Đòn bẩy (`Cross 5x`).
3. **Bảng Lịch Sử Khớp Lệnh & Lệnh Chờ (Open Orders & Trade History)**:
   - Cập nhật dòng thời gian mỗi khi có lệnh Mua/Bán được khớp hoặc chốt lời thành công.
4. **Trạng thái 8 Agent sống động**:
   - Chuyển từ trạng thái tĩnh "WAIT" sang trạng thái động: `SCANNING`, `VOTING BUY (82%)`, `EXECUTING ORDER`, `POSITION OPEN (TP: +1.2%)`.
5. **Hiệu ứng & Âm thanh**:
   - Hiệu ứng Neon pulse xanh lục khi chốt lời có lãi + hiệu ứng cảnh báo khi cắt lỗ bảo toàn vốn.
</comprehensive_upgrade_mission>

<decision_ladder>
1. REFACTOR RUNTIME & LAUNCHER: Tinh gọn `core/runtime.py` và `run.bat` để chỉ phục vụ đúng 2 chế độ:
   - `run.bat demo` (hoặc phím 1): Khởi chạy Web UI + Bot tự động giao dịch tài khoản Demo Binance Futures.
   - `run.bat live` (hoặc phím 2): Khởi chạy Web UI + Bot giao dịch tài khoản Thực tế Binance Futures.
2. ALGORITHM & SIGNAL TUNING: Cân chỉnh lại `core/groktopus_swarm.py`, `research/flow_gauge.py`, `core/consensus_engine.py` để tín hiệu phản xạ nhạy bén, Swarm vào lệnh chủ động thay vì ngồi chờ vô tận.
3. BACKEND API & FORCE TRADE: Thêm endpoint `/api/force_test_trade` và `/api/switch_mode` vào `core/web_ui_server.py`.
4. FRONTEND UI MODERNIZATION: Nâng cấp `web_ui/index.html`, `style.css`, `web_ui/terminal.js` với đầy đủ 2-mode switch, bảng Vị thế Binance, bảng Lịch sử lệnh, và nút Force Trade.
5. TDD VERIFICATION: Chạy `python -m pytest -q` đảm bảo 100% test suite pass không có lỗi.
</decision_ladder>

<verification_gate>
- Kiểm thử bắt buộc: `python -m pytest -q` phải pass 100%.
- UI & Execution Test: Khởi chạy `run.bat demo`, mở Web UI `http://127.0.0.1:8765`, bấm thử nút "Bắn Lệnh Test" hoặc để bot tự động quét tín hiệu và thấy vị thế BTCUSDT nhảy số trực quan trên giao diện!
</verification_gate>

<task_directive>
Thực hiện ngay toàn bộ kế hoạch: Tinh gọn về 2 chế độ -> Tối ưu thuật toán vào lệnh chủ động -> Nâng cấp Web UI Pro Terminal -> Chạy test kiểm thử -> Báo cáo kết quả và hướng dẫn sử dụng chi tiết.
</task_directive>
