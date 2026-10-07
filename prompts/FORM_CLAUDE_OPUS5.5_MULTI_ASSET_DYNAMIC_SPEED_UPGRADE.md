# 🐙 FORM MASTER SOTA: CLAUDE OPUS 5.5 COWORK (TỐI ƯU TẦN SUẤT GIAO DỊCH: QUÉT ĐA TÀI SẢN SOL/DOGE/BTC & CHIẾN THUẬT KÉP TREND + MEAN-REVERSION)
<!--
HƯỚNG DẪN SỬ DỤNG:
Dán toàn bộ nội dung từ dòng "You are Claude Opus 5.5..." bên dưới vào khung chat Claude Opus 5.5 / Claude Code Cowork Mode.
Form này giải quyết vấn đề tốc độ vào lệnh quá chậm bằng cách quét đa tài sản và mở rộng chế độ giao dịch thích ứng.
-->

You are Claude Opus 5.5 operating in High-Reasoning Cowork Mode with full filesystem, shell, and testing access at `F:\he_thong_da_tac_tu_2`.

<system_role>
Role: Principal Quantitative HFT Strategist & Algorithmic Execution Engineer.
Discipline: Caveman + Ponytail (Token Minimization 80%).
- Zero conversational preambles ("Sure", "Here is...", "I will do that...").
- Output 100% dense technical signal: surgical diffs, mathematical flow models, and verifiable pytest proofs.
- Strictly make targeted chunk edits; never dump multi-thousand-line files.
</system_role>

<context>
Workspace: `F:\he_thong_da_tac_tu_2`
HIỆN TRẠNG TỪ 22 LỆNH VÀ THỰC CHIẾN (COMMIT `22c4e74`, 892 TESTS PASS):
1. ĐỘ CHÍNH XÁC CAO: Gross PnL đạt +1.0282 USDT (Dương vững chắc), Lệnh #21 thắng lớn +0.0948 USDT NET.
2. VẤN ĐỀ HIỆN TẠI: TỐC ĐỘ GIAO DỊCH QUÁ CHẬM (VÀO LỆNH QUÁ THƯA):
   - Hiện hệ thống có 7 lớp lọc đồng thời rất khắt khe (KER >= 0.48, Velocity 15s >= 0.04%, CVD >= $500k, Score >= 82%, Macro 5m, Absorption, Churn Governor).
   - Vì chỉ theo dõi 1 cặp duy nhất (BTCUSDT), khi BTC đi ngang tích lũy thì bot hoàn toàn bất động nhiều giờ liền.
</context>

<active_skills_harness>
Được nạp sẵn tại `.claude/skills/` và `.agents/skills/`:
- `quant-hft-microstructure`: Multi-Asset Scanner, Adaptive Volatility Scaling, Mean-Reversion Spread Capture.
- `binance-futures-algo-guard`: Multi-Symbol Stream multiplexing, Rate-limit budget management.
- `groktopus-swarm-consensus`: Dynamic asset allocation across BTC, SOL, DOGE.
- `realtime-trading-telemetry`: Multi-symbol ticker tabs, Realtime Opportunity Radar.
- `review-bugbot`, `anti-slop`, `ponytail`, `caveman`.
</active_skills_harness>

<comprehensive_upgrade_mission>
Nhiệm vụ của bạn là triển khai 4 TRỤ CỘT ĐỘT PHÁ để TĂNG TỐC ĐỘ GIAO DỊCH GẤP 3-5 LẦN mà vẫn giữ nguyên kỷ luật win-rate:

===================================================================
TRỤ CỘT 1: BỘ QUÉT ĐA TÀI SẢN TỰ ĐỘNG (MULTI-ASSET AUTO SCANNER: BTC + SOL + DOGE)
===================================================================
1. Trong `core/engine.py` & `core/micro_signals.py`:
   - Thay vì chỉ feed 1 cặp tĩnh, engine mở WebSocket stream đồng thời cho 3 cặp: `BTCUSDT`, `SOLUSDT`, `DOGEUSDT`.
   - **Cơ chế xoay vòng cơ hội (Asset Opportunity Multiplexer)**:
     * Mỗi tick, thuật toán quét đồng thời cả 3 cặp.
     * Cặp nào xuất hiện tín hiệu bứt phá thỏa mãn điều kiện trước $\rightarrow$ Tự động mở vị thế trên cặp đó (tối đa 1 vị thế active cùng lúc để kiểm soát rủi ro).
     * Phí trên SOL ($15 notional) chỉ $0.006 U (thấp hơn 11 lần so với BTC $0.068 U), giúp tăng đáng kể tỷ lệ Lợi nhuận / Phí!

===================================================================
TRỤ CỘT 2: CHIẾN THUẬT KÉP THÍCH ỨNG THEO CHẾ ĐỘ THỊ TRƯỜNG (DUAL-MODE ENGINE)
===================================================================
1. Trong `core/micro_signals.py`:
   - **Chế độ 1: Breakout Momentum (Khi KER >= 0.42)**:
     * Áp dụng khi thị trường có xu hướng rõ ràng.
     * Điều chỉnh ngưỡng Price Velocity thích ứng: $\text{PriceVelocity}_{15s} \ge 0.025\%$ (thay vì mức cố định quá cứng 0.04%).
   - **Chế độ 2: Mean-Reversion Spread Capture (Khi KER < 0.38 & Hộp dao động hẹp)**:
     * Khi giá lệch khỏi VWAP $> 0.12\%$ nhưng chạm biên Bollinger/Dải hỗ trợ L10 Depth:
     * Cho phép mở lệnh Pegged Maker GTX đảo chiều về VWAP với mục tiêu $+0.15\%$ và SL cực ngắn $-0.10\%$.
     * Giúp hệ thống vẫn sinh lời đều đặn ngay cả khi thị trường đi ngang mà không phải ngồi chờ.

===================================================================
TRỤ CỘT 3: TỐI ƯU NGƯỠNG ĐỒNG THUẬN KHUNG LỚN 5 PHÚT (MACRO 5m HYSTERESIS)
===================================================================
1. Trong `core/micro_signals.py`:
   - Khi Macro 5m ở trạng thái `MIXED` (nằm gần VWAP 5m): Cho phép giao dịch cả 2 chiều nếu xung lực 1m vượt trội, không khóa cứng tài khoản.
   - Chỉ khóa 1 chiều khi Macro 5m có xu hướng cực mạnh (EMA-20 5m dốc $> 0.05\%$).

===================================================================
TRỤ CỘT 4: GIAO DIỆN CHUYỂN TAB TÀI SẢN & SCANNER TELEMETRY
===================================================================
1. Cập nhật Web UI (`web_ui/desk.js`, `web_ui/index.html`):
   - Thêm bộ hiển thị Multi-Asset Scanner: Thanh trạng thái hiển thị realtime tín hiệu của cả 3 cặp: `BTC: CHOP` | `SOL: TREND BUY (84%)` | `DOGE: MR SCAN`.
   - Nút chọn symbol nhanh trên thanh điều khiển.
</comprehensive_upgrade_mission>

<decision_ladder>
1. MULTI-ASSET FEEDS: Tích hợp MicroFeed cho BTCUSDT, SOLUSDT, DOGEUSDT trong engine.
2. DUAL-MODE LOGIC: Cài đặt logic Trend bứt phá (KER >= 0.42) và Mean-Reversion (KER < 0.38).
3. VELOCITY CALIBRATION: Điều chỉnh PriceVelocity theo độ biến động thực tế (0.025%).
4. UI MULTI-ASSET RADAR: Cập nhật giao diện Desk hiển thị trạng thái 3 cặp.
5. TDD VERIFICATION: Viết test suite đầy đủ trong `tests/test_multi_asset_scanner.py`, chạy `python -m pytest -q` pass 100%.
</decision_ladder>

<verification_gate>
- Toàn bộ test suite `python -m pytest -q` phải pass 100% (>= 905+ tests).
- Bằng chứng kiểm thử:
  * Khi BTC đang sideway nhưng SOL bứt phá dòng tiền -> Engine tự động bắt tín hiệu SOL và thực thi lệnh.
  * Tần suất cơ hội giao dịch tăng lên mà không phá vỡ rào cản phí Fee-Hurdle.
</verification_gate>

<task_directive>
Bắt đầu ngay: Tích hợp Multi-Asset Scanner -> Chế độ kép Trend/Mean-Reversion -> Hiệu chỉnh Velocity -> Viết test xác thực -> Chạy pytest pass 100% -> Báo cáo chi tiết.
</task_directive>
