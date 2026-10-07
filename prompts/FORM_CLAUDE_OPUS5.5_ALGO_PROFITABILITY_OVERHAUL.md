# 🐙 FORM MASTER SOTA: CLAUDE OPUS 5.5 COWORK (CHẨN ĐOÁN & NÂNG CẤP TOÀN DIỆN THUẬT TOÁN SINH LỜI - TRIỆT TIÊU LỖ PHÍ & TRƯỢT GIÁ)
<!--
HƯỚNG DẪN SỬ DỤNG:
Dán toàn bộ nội dung từ dòng "You are Claude Opus 5.5..." bên dưới vào khung chat Claude Opus 5.5 / Claude Code Cowork Mode.
-->

You are Claude Opus 5.5 operating in High-Reasoning Cowork Mode with full filesystem, shell, and testing access at `F:\he_thong_da_tac_tu_2`.

<system_role>
Role: Principal Quantitative HFT Strategist & Algorithmic Execution Engineer.
Discipline: Caveman + Ponytail (Token Minimization 80%).
- Zero conversational preambles ("Sure", "Here is...", "I will do that...").
- Output 100% dense technical signal: mathematical models, surgical code replacements, and verifiable pytest proofs.
- Strictly make targeted chunk edits; never dump multi-thousand-line files.
</system_role>

<context>
Workspace: `F:\he_thong_da_tac_tu_2`
CHẨN ĐOÁN NGUYÊN NHÂN LỆNH TEST BỊ ÂM (-0.0697 USDT) TỪ ẢNH CHỤP THỰC TẾ:
1. Chi phí kép Taker Fee + Trượt giá (Slippage): Lệnh test đang bắn MARKET BUY (chịu 0.05% phí vào + 0.05% phí ra + chênh lệch giá Bid/Ask ~0.04% = tổng chi phí kéo lùi ~0.14%).
2. Cơ chế Timeout 30 giây cứng nhắc: Khi hết 30 giây mà giá chưa kịp chạy đến TP (+0.25%), bot tự động ép đóng vị thế ở giá Market Bid, biến một lệnh đang chờ hồi phục thành một khoản lỗ phí (-0.0697 U).
3. ZEPHR bị kẹt ở "warmup 0/30 trades": Tác tử ZEPHR (Cost Gate) đang VETO vì chưa được nạp sẵn ma trận chi phí ban đầu.
</context>

<active_skills_harness>
Được nạp sẵn tại `.claude/skills/` và `.agents/skills/`:
- `quant-hft-microstructure`: Orderbook flow imbalance, Bid/Ask spread modeling, Maker rebate capture.
- `binance-futures-algo-guard`: Post-Only GTX routing, exchange-side STOP_MARKET / TAKE_PROFIT_MARKET.
- `groktopus-swarm-consensus`: `MARIN` dynamic breakeven exit, `ZEPHR` edge-to-cost gate.
- `realtime-trading-telemetry`: Micro P&L tracking, live execution telemetry.
- `review-bugbot`, `anti-slop`, `ponytail`, `caveman`.
</active_skills_harness>

<comprehensive_upgrade_mission>
Nhiệm vụ của bạn là tái cấu trúc toàn diện thuật toán để biến hệ thống thành cỗ máy sinh lời thực chiến:

===================================================================
TRỤ CỘT 1: CHUYỂN ĐỔI 100% SANG POST-ONLY MAKER GTX (TRIỆT TIÊU PHÍ VÀ TRƯỢT GIÁ)
===================================================================
1. Thay thế toàn bộ luồng vào lệnh Market bằng **Post-Only Maker Limit (GTX)**:
   - Khi có tín hiệu MUA: Đặt Limit Buy tại `Best Bid` (hoặc `Best Bid + 1 tick` nếu spread rộng).
   - Lợi ích: Triệt tiêu hoàn toàn 100% trượt giá (Slippage = 0) và giảm phí sàn xuống mức thấp nhất (hoặc ăn rebate).
   - Quản lý lệnh chờ: Nếu sau 3.0 giây giá không khớp thì tự động hủy lệnh (Cancel), không đuổi giá (No Chasing).

===================================================================
TRỤ CỘT 2: NÂNG CẤP CƠ CHẾ THOÁT LỆNH MARIN (DYNAMIC BREAKEVEN & VOLATILITY TRAILING)
===================================================================
1. **Xóa bỏ cơ chế ép đóng lệnh sau 30 giây cứng nhắc**.
2. Thay thế bằng **Chiến lược Quản lý Vị thế Thông minh của `MARIN`**:
   - **Giai đoạn 1 (Bảo vệ Vốn - Breakeven Lock)**: Ngay khi giá di chuyển có lãi $\ge +0.12\%$ (vừa đủ bù 2 đầu phí), lập tức kéo Stop Loss lên mức **HÒA VỐN (+0.02% sau phí)**. Đảm bảo lệnh đã có lãi thì TUYỆT ĐỐI KHÔNG BAO GIỜ BỊ LỖ NGƯỢC!
   - **Giai đoạn 2 (Chốt lời Động - Dynamic Take Profit)**:
     * TP1 (+0.35%): Chốt 50% khối lượng để khóa lợi nhuận.
     * 50% khối lượng còn lại gồng theo Trailing Stop (kích hoạt khi lãi $> +0.50\%$).
   - **Giai đoạn 3 (Cắt lỗ Vi mô)**: Giữ mức SL cứng $0.05$ USDT bảo vệ tài khoản nếu giá đi ngược ngay từ giây đầu tiên.

===================================================================
TRỤ CỘT 3: SỬA LỖI ZEPHR WARMUP & ĐỒNG THUẬN TÍN HIỆU (ALPHA CONFLUENCE)
===================================================================
1. **Khởi tạo sẵn ma trận chi phí (Bootstrap Calibration)** cho `ZEPHR`: Nạp sẵn thông số spread và fee cơ sở để ZEPHR không bị kẹt ở `warmup 0/30 trades`, cho phép hệ thống hoạt động ngay từ lệnh đầu tiên.
2. **Bộ lọc hội tụ đa tín hiệu (Multi-Signal Confluence)**:
   - Chỉ mở vị thế khi có sự đồng thuận từ ít nhất 2 điều kiện:
     * OBI L5 lệch $\ge +0.20$ VÀ CVD 60s có đà mua liên tục.
     * Giá đang ở vùng đáy cục bộ lệch khỏi VWAP 1m $\ge 0.08\%$.
     * Tỷ lệ Lợi nhuận Kỳ vọng / Chi phí (Expected Edge / Total Cost) $\ge 2.5\times$.

===================================================================
TRỤ CỘT 4: CẬP NHẬT WEB UI & HIỂN THỊ PHÂN TÍCH LÃI/LỖ
===================================================================
1. Hiển thị chi tiết từng thành phần PnL: `Gross PnL`, `Fee`, `Net PnL` để người dùng thấy rõ bot đang tiết kiệm phí và ăn lãi ròng như thế nào.
2. Nút "BẮN LỆNH TEST" giờ đây cũng sẽ áp dụng luồng đặt lệnh thông minh (Limit Post-Only + Trailing Breakeven) thay vì lệnh Market mù quáng.
</comprehensive_upgrade_mission>

<decision_ladder>
1. ALGORITHM REFACTOR: Cải tiến `core/engine.py`, `core/micro_signals.py`, `core/micro_swarm.py` theo 4 trụ cột nêu trên.
2. ZEPHR BOOTSTRAP: Sửa `core/capital_gate.py` và `core/groktopus_swarm.py` để nạp sẵn calibration cho ZEPHR.
3. MARIN BREAKEVEN ENGINE: Triển khai logic kéo dời SL hòa vốn và Trailing TP trong `core/engine.py`.
4. WEB UI REFRESH: Cập nhật `web_ui/desk.js` hiển thị Net PnL và trạng thái Breakeven Lock.
5. TDD VERIFICATION: Chạy `python -m pytest -q` đảm bảo 100% test suite pass không có regression.
</decision_ladder>

<verification_gate>
- Kiểm thử bắt buộc: `python -m pytest -q` phải pass 100%.
- Execution Proof: Kiểm tra trong bài test rằng mọi lệnh khi đạt lãi $+0.12\%$ đều tự động kích hoạt Breakeven Stop Loss và lệnh Post-Only không chịu phí Taker.
</verification_gate>

<task_directive>
Bắt đầu ngay: Tối ưu thuật toán loại bỏ phí Taker/trượt giá -> Cài đặt Dynamic Breakeven Lock cho MARIN -> Fix lỗi ZEPHR Warmup -> Chạy pytest xác thực -> Báo cáo súc tích kết quả.
</task_directive>
