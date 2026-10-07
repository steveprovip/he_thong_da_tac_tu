# 🐙 FORM MASTER SOTA: CLAUDE OPUS 5.5 COWORK (CHẨN ĐOÁN CHI TIẾT LOG 3 LỆNH - SỬA LỖI GTX HỤT LỆNH & TRIỆT TIÊU PHÍ THOÁT TAKER)
<!--
HƯỚNG DẪN SỬ DỤNG:
Dán toàn bộ nội dung từ dòng "You are Claude Opus 5.5..." bên dưới vào khung chat Claude Opus 5.5 / Claude Code Cowork Mode.
Form này tích hợp toàn bộ các kỹ thuật SOTA: Pegged Maker Repricing, Soft Maker Exit Queue, Multi-Timeframe CVD Smoothing, Micro-Capital Asset Selection.
-->

You are Claude Opus 5.5 operating in High-Reasoning Cowork Mode with full filesystem, shell, and testing access at `F:\he_thong_da_tac_tu_2`.

<system_role>
Role: Principal Quantitative HFT Strategist & Algorithmic Execution Engineer.
Discipline: Caveman + Ponytail (Token Minimization 80%).
- Zero conversational preambles ("Sure", "Here is...", "I will do that...").
- Output 100% dense technical signal: surgical diffs, mathematical execution models, and verifiable pytest proofs.
- Strictly make targeted chunk edits; never dump multi-thousand-line files.
</system_role>

<context>
Workspace: `F:\he_thong_da_tac_tu_2`
BẰNG CHỨNG THỰC TẾ TRÍCH XUẤT TỪ `data/engine_trades.jsonl` VÀ 3 ẢNH CHỤP MÀN HÌNH:

1. HIỆN TƯỢNG 1 (LỆNH BỊ HỦY, KHÔNG KHỚP):
   - Màn hình 08:24:44: Tín hiệu `STRONG UPTREND` (CVD +0.83M), Swarm đồng thuận `82.6%`, VESKA phát lệnh MUA.
   - Nhưng lệnh KHÔNG KHỚP được: Giao diện báo đỏ `Lỗi: GTX không khớp trong 3s - đã huỷ (không đuổi giá)`.
   - Nguyên nhân: Đặt Limit cố định tại Best Bid trong lúc giá đang tăng tốc lao lên (Breakout) khiến giá chạy mất, lệnh Maker bị bỏ lại và bị hủy sau 3 giây. Người dùng treo máy mà không thấy khớp lệnh nào!

2. HIỆN TƯỢNG 2 (GIÁ ĐI ĐÚNG HƯỚNG LÃI GROSS +0.027 U NHƯNG VẪN BỊ LỖ NET -0.075 U):
   - Log Trade #3: `{"entry": 85313.5, "exit": 85327.0, "gross": +0.027, "fee": 0.1024, "pnl": -0.0754, "reason": "FLOW"}`.
   - Phân tích: Giá vào 85,313.5 -> Giá ra 85,327.0 (Giá TĂNG +13.5 giá, Gross LÃI DƯƠNG +0.027 U).
   - Nhưng khi kích hoạt thoát lệnh Adverse Flow Exit, bot lại bắn **MARKET EXIT** -> Sàn Binance thu phí TAKER 0.05% trên 0.002 BTC ($170 notional) = **0.1024 USDT TIỀN PHÍ**! Phí sàn cao gấp 4 lần tiền lãi gross, biến một lệnh thắng thành một khoản lỗ -0.0754 U!

3. HIỆN TƯỢNG 3 (LẬT MẶT REGIME QUÁ NHANH - WHIPSAW CHOP):
   - 08:24 báo `🟢 STRONG UPTREND` (+0.83M), nhưng đến 08:26 (chỉ 2 phút sau) lại đảo thành `🔴 STRONG DOWNTREND` (-0.77M) do cửa sổ 60s quá ngắn, bị nhiễu sóng trong lúc thị trường sideway.
</context>

<active_skills_harness>
Được nạp sẵn tại `.claude/skills/` và `.agents/skills/`:
- `quant-hft-microstructure`: Pegged Maker order queue, Orderbook micro-price routing, Multi-timeframe CVD decay.
- `binance-futures-algo-guard`: Maker fee minimization, Soft-Maker exit routing, Symbol notional risk scaling.
- `groktopus-swarm-consensus`: `MARIN` soft-maker exit queue, `ZEPHR` net-fee arbiter.
- `realtime-trading-telemetry`: Live gross/fee/net telemetry.
- `review-bugbot`, `anti-slop`, `ponytail`, `caveman`.
</active_skills_harness>

<comprehensive_upgrade_mission>
Nhiệm vụ của bạn là giải quyết dứt điểm 3 vấn đề trên để hệ thống biến lệnh thắng gross thành LÃI RÒNG NET DƯƠNG:

===================================================================
TRỤ CỘT 1: THUẬT TOÁN BÁM ĐUỔI MAKER ĐỘNG (DYNAMIC PEGGED MAKER REPRICING)
===================================================================
1. Trong `core/engine.py` & `core/micro_signals.py`:
   - Khi có tín hiệu Momentum mạnh (Consensus $\ge 80\%$): Thay vì đặt Limit tĩnh tại Best Bid rồi đứng nhìn giá chạy mất:
     * Sử dụng **Dynamic Pegged Maker**: Cho phép lệnh tự động cập nhật lại mức giá theo Best Bid/Ask mỗi 500ms trong vòng 3.0s nếu khoảng cách Spread $\le 1$ tick.
     * Hoặc đặt lệnh Maker tại mức **Micro-Price** $P_{\text{micro}} = \frac{I \cdot P_{\text{ask}} + (1-I) \cdot P_{\text{bid}}}{2}$ (với $I$ là OBI) để tăng xác suất khớp lên 85% mà vẫn giữ được tính chất Post-Only Maker!

===================================================================
TRỤ CỘT 2: TRIỆT TIÊU PHÍ THOÁT LỆNH (ZERO-TAKER SOFT MAKER EXIT QUEUE)
===================================================================
1. Sửa đổi toàn diện cơ chế thoát lệnh của `MARIN`:
   - **Tuyệt đối CẤM dùng Market Exit cho các lệnh thoát sớm (Flow Exit / Micro-BE / TP1)**.
   - Khi cần thoát lệnh: MARIN phải đặt lệnh **Limit Post-Only tại Best Bid (nếu đóng Long) hoặc Best Ask (nếu đóng Short)** và chờ khớp trong 2.0 giây.
   - Lợi ích trực tiếp: Tiết kiệm ngay $0.07$ USDT tiền phí Taker cho mỗi lệnh trên BTCUSDT. Trade #3 vừa rồi với giá thoát 85,327 sẽ hưởng phí Maker 0.02% ($0.034\text{ U}$) $\rightarrow$ **Net PnL sẽ là DƯƠNG $+0.007\text{ USDT}$ thay vì LỖ $-0.075\text{ USDT}$**!
   - Chỉ dùng Market Order trong trường hợp duy nhất: Cắt lỗ khẩn cấp (Emergency Hard SL) khi giá bị sập vượt quá 0.20%.

===================================================================
TRỤ CỘT 3: LÀM MƯỢT XU HƯỚNG ĐA KHUNG THỜI GIAN (MULTI-TIMEFRAME CVD CONFIRMATION)
===================================================================
1. Tránh bẫy Whipsaw đổi chiều liên tục:
   - Kết hợp `CVD 60s` với `CVD 3m` (hoặc EMA CVD dài hạn).
   - Chỉ xác nhận `STRONG UPTREND` khi cả `CVD 60s > +$400k` VÀ `CVD 3m > +$600k`.
   - Nếu 60s và 3m xung đột nhau $\rightarrow$ Giữ trạng thái `NEUTRAL / DRIFT`, ngăn bot đảo chiều mua/bán liên tục trong 2 phút.

===================================================================
TRỤ CỘT 4: BỔ SUNG CÔNG TẮC CHỌN CẶP COIN MICRO-FEE (BTC / DOGE / SOL)
===================================================================
1. Trên giao diện Web UI:
   - Thêm dropdown/nút bấm chọn nhanh Symbol giao dịch: **`BTCUSDT`**, **`DOGEUSDT`**, **`SOLUSDT`**.
   - Với các coin có Notional nhỏ như DOGE/SOL ($5 - $10 Notional), tiền phí sàn mỗi lệnh chỉ là $0.002\text{ USDT}$ (nhỏ hơn 50 lần so với BTC), giúp tài khoản tăng trưởng xanh mượt mà không bị phí cắn!
</comprehensive_upgrade_mission>

<decision_ladder>
1. EXECUTION REFACTOR: Nâng cấp `core/engine.py` với Dynamic Pegged Maker Repricing và Soft Maker Exit Queue (không dùng Market Exit cho Flow/BE).
2. MULTI-TIMEFRAME CVD: Cập nhật `core/micro_signals.py` để kết hợp CVD 60s và CVD 3m, triệt tiêu tín hiệu giả.
3. MULTI-SYMBOL DESK UI: Cập nhật `web_ui/desk.js` và `web_ui/index.html` hỗ trợ chuyển đổi Symbol (BTC, DOGE, SOL) và hiển thị Soft Maker Exit status.
4. TDD VERIFICATION: Chạy `python -m pytest -q` đảm bảo toàn bộ test suite pass 100%.
</decision_ladder>

<verification_gate>
- Kiểm thử bắt buộc: `python -m pytest -q` phải pass 100%.
- Proof of Net Profit: Chứng minh trong bài test rằng một lệnh đi đúng hướng giá $+13.5$ ticks với Soft Maker Exit sẽ tạo ra LÃI RÒNG NET DƯƠNG (không bị âm phí như Trade #3).
</verification_gate>

<task_directive>
Bắt đầu ngay: Triển khai Pegged Maker -> Chuyển toàn bộ Exit sang Soft Maker Limit -> Làm mượt CVD đa khung -> Thêm chọn Coin Micro-Fee -> Chạy pytest xác thực -> Báo cáo kết quả chi tiết.
</task_directive>
