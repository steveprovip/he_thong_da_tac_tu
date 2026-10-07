# 🐙 FORM MASTER SOTA: CLAUDE OPUS 5.5 COWORK (TỐI ĐA HÓA WIN-RATE: CHẶN BẪY HẤP THỤ SỔ LỆNH, KHÓA LÃI BẢO VỆ PHÍ 2 TẦNG & MTF MACRO ALIGNMENT)
<!--
HƯỚNG DẪN SỬ DỤNG:
Dán toàn bộ nội dung từ dòng "You are Claude Opus 5.5..." bên dưới vào khung chat Claude Opus 5.5 / Claude Code Cowork Mode.
Form này triển khai 5 trụ cột lượng tử đột phá giúp đưa Win-Rate lên > 80% và chuyển hóa 100% Gross Profit thành Lãi Ròng Net thực tế.
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
BASELINE HIỆN TẠI (ĐÃ PASS 861/861 TESTS TẠI COMMIT `d71495e`):
- Đã có bộ lọc Kaufman Efficiency Ratio (KER < 0.40 khóa sideway, KER >= 0.45 mở).
- Đã có Regime Debounce (giữ tối thiểu 60s + 20s xác nhận đảo chiều liên tục).
- Đã nâng TP1 lên +0.45%.

PHÂN TÍCH TỬ HUYỆT CÒN LẠI ĐỂ ĐẠT WIN-RATE TỐI ĐA (>80%):
1. BẪY HẤP THỤ SỔ LỆNH (LIMIT ORDER ABSORPTION / DELTA DIVERGENCE):
   - Trong các lệnh thua cũ (#7, #8, #10, #15, #17), dòng tiền CVD nhảy vọt +$500k đến +$800k nhưng giá không vượt đỉnh (bị tường Limit Sell chặn hấp thụ toàn bộ lực mua).
   - Khi dòng tiền mua kiệt sức, giá đảo chiều ngay lập tức khiến lệnh bị kẹt.
2. THIẾU CƠ CHẾ RATCHET BẢO VỆ PHÍ (FEE-SHIELD BREAKEVEN AT +0.12% MFE):
   - Nhiều lệnh đã từng lãi xanh +0.04% đến +0.08% MFE nhưng do chưa chạm TP1 (+0.45%) nên bị quay đầu thành lỗ và chịu thêm phí.
   - Nếu có cơ chế kéo Stop Loss về `Entry + 0.04%` ngay khi MFE chạm +0.12%, toàn bộ các lệnh này sẽ được BẢO HIỂM 100% LÃI RÒNG DƯƠNG.
3. PHÍ TAKER BÀO MÒN (IOC TAKER FEE DRAG):
   - Lệnh IOC Taker tốn 4 bps vào + 4 bps ra = 8 bps ($0.136 U trên $170 notional).
   - Cần siết chặt điều kiện IOC Taker chỉ dành cho Super Momentum (CVD 10s >= $3.0M & KER >= 0.60), còn lại 100% phải dùng Pegged Post-Only Maker (2 bps).
4. XUNG ĐỘT XU HƯỚNG LỚN (MTF 5-MINUTE VWAP / EMA CONFLICT):
   - Các lệnh đánh ngược xu hướng 5m (ví dụ Short khi giá nằm trên VWAP 5m đang dốc lên) đều có xác suất thua cao.
</context>

<active_skills_harness>
Được nạp sẵn tại `.claude/skills/` và `.agents/skills/`:
- `quant-hft-microstructure`: Order Book Absorption detection, Price-Delta Concordance, 2-Stage Dynamic Breakeven Ratchet, Avellaneda-Stoikov spread capture.
- `binance-futures-algo-guard`: Post-Only GTX maker routing, on-exchange Stop ratchet, rate-limit management.
- `groktopus-swarm-consensus`: Multi-Agent Swarm voting with MTF trend bias.
- `realtime-trading-telemetry`: Live Absorption meter, MTF Trend bias telemetry.
- `review-bugbot`, `anti-slop`, `ponytail`, `caveman`.
</active_skills_harness>

<comprehensive_upgrade_mission>
Nhiệm vụ của bạn là hiện thực hóa 5 TRỤ CỘT LƯỢNG TỬ SOTA để nâng Win-rate và bảo vệ Lãi ròng:

===================================================================
TRỤ CỘT 1: BỘ LỌC BẪY HẤP THỤ SỔ LỆNH (ABSORPTION & PRICE-DELTA CONCORDANCE)
===================================================================
1. Trong `core/micro_signals.py`:
   - Tính toán chỉ số **Price-Delta Concordance Ratio (PDCR)** trên 15 giây:
     $$\text{PriceVelocity}_{15s} = \frac{P_t - P_{t-15s}}{P_{t-15s}}$$
   - **Quy tắc chặn bẫy hấp thụ (Absorption Trap Guard)**:
     * Nếu $\text{CVD}_{15s} \ge +\$500\text{k}$ (mua ồ ạt) nhưng $\text{PriceVelocity}_{15s} \le +0.02\%$ (giá không nhúc nhích vì dính tường bán Ask) $\rightarrow$ **CHẶN VÀO LỆNH BUY (Lý do: `ABSORPTION TRAP`)**.
     * Nếu $\text{CVD}_{15s} \le -\$500\text{k}$ (bán ồ ạt) nhưng $\text{PriceVelocity}_{15s} \ge -0.02\%$ (giá không rơi vì dính tường mua Bid) $\rightarrow$ **CHẶN VÀO LỆNH SELL (Lý do: `ABSORPTION TRAP`)**.
   - Chỉ cho phép vào lệnh khi lực mua/bán thực sự đẩy được giá di chuyển tương xứng ($\text{PriceVelocity} \ge 0.03\%$).

===================================================================
TRỤ CỘT 2: RATCHET BẢO HIỂM PHÍ 2 TẦNG (2-TIER FEE SHIELD & PROFIT LOCK)
===================================================================
1. Trong `core/engine.py`:
   - **Tầng 1 - Khóa hòa vốn bảo hiểm phí (Fee-Shield Ratchet tại +0.12% MFE)**:
     * Khi vị thế đạt mức lãi cao nhất (MFE) $\ge +0.12\%$ ($\approx +\$100\text{ USD}$ trên BTC):
     * Tự động dời Stop Loss về `Entry Price + 0.04%` (đối với BUY) hoặc `Entry Price - 0.04%` (đối với SELL).
     * Mức dời này bù đắp 100% phí Maker/Taker cả 2 đầu + dôi ra $+0.02\%$ lãi ròng. Vị thế một khi đã xanh là **VĨNH VIỄN KHÔNG THỂ LỖ**.
   - **Tầng 2 - Khóa lợi nhuận bỏ túi (Profit-Lock Ratchet tại +0.25% MFE)**:
     * Khi MFE $\ge +0.25\%$, dời Stop Loss về `Entry Price + 0.15%`. Đảm bảo bỏ túi chắc chắn $+0.11\%$ lãi ròng ngay cả khi giá quay đầu trước khi chạm TP1 (+0.45%).

===================================================================
TRỤ CỘT 3: ƯU TIÊN TUYỆT ĐỐI POST-ONLY MAKER (ZERO TAKER FEE DISCIPLINE)
===================================================================
1. Tiết kiệm 60% chi phí giao dịch:
   - Chỉ dùng lệnh IOC Taker khi thỏa mãn đồng thời 3 điều kiện siêu xung lực:
     * $\text{CVD}_{10s} \ge \$3.0\text{M}$
     * $\text{KER} \ge 0.60$ (xu hướng cực kỳ dốc và thẳng)
     * Điểm đồng thuận $\text{JEV} \ge 9.2$ hoặc Swarm $\ge 85\%$
   - Với tất cả các trường hợp còn lại: **BẮT BUỘC dùng Pegged Post-Only Maker (GTX)** tại Best Bid / Best Ask.
   - Thời gian chờ khớp Maker tối đa 5 giây; nếu không khớp thì hủy lệnh nhẹ nhàng, không nhảy sang Taker đuổi giá.

===================================================================
TRỤ CỘT 4: BỘ LỌC ĐỒNG THUẬN KHUNG LỚN 5 PHÚT (MTF 5-MINUTE MACRO VWAP BIAS)
===================================================================
1. Trong `core/micro_signals.py` & `core/engine.py`:
   - Duy trì đường rolling 5-minute VWAP và độ dốc EMA-20 của nến 5m.
   - **Luật cấm đánh ngược dòng (Macro Alignment Gate)**:
     * Nếu Giá $>$ VWAP 5m và EMA 5m dốc lên: **CHỈ MUA (LONG ONLY)**. Chặn 100% lệnh Short.
     * Nếu Giá $<$ VWAP 5m và EMA 5m dốc xuống: **CHỈ BÁN (SHORT ONLY)**. Chặn 100% lệnh Long.

===================================================================
TRỤ CỘT 5: GIAO DIỆN HIỂN THỊ TRẠNG THÁI HẤP THỤ & RATCHET SHIELD
===================================================================
1. Cập nhật Web UI (`web_ui/desk.js`, `web_ui/index.html`):
   - Thêm badge hiển thị trạng thái Hấp thụ: `ABSORPTION: CLEAR` (xanh) hoặc `ABSORPTION: TRAPPED` (đỏ).
   - Thêm chip trạng thái Ratchet: `SHIELD: ARMED (+0.12%)` khi lệnh đã được bảo hiểm hòa phí.
</comprehensive_upgrade_mission>

<decision_ladder>
1. ABSORPTION METRIC: Thêm Price-Delta Concordance và phát hiện tường chặn vào `core/micro_signals.py`.
2. 2-TIER RATCHET SHIELD: Cài đặt logic dời Stop Loss tại +0.12% MFE và +0.25% MFE vào `core/engine.py`.
3. STRICT MAKER ROUTING: Siết chặt ngưỡng IOC Taker và ưu tiên Pegged Maker trong `core/engine.py`.
4. MTF VWAP BIAS: Tích hợp bộ lọc xu hướng 5m vào tín hiệu swarm.
5. TELEMETRY & UI: Cập nhật SSE snapshot và Web UI hiển thị badge Absorption & Shield.
6. TDD SUITE: Viết test case mô phỏng đầy đủ cho cả 5 trụ cột trong `tests/test_winrate_maximizer.py`, đảm bảo `python -m pytest -q` pass 100%.
</decision_ladder>

<verification_gate>
- Toàn bộ test suite `python -m pytest -q` phải pass 100% (từ 861 test hiện tại nâng lên >= 875+ tests).
- Bằng chứng kiểm thử:
  * Test mô phỏng CVD tăng vọt nhưng giá đứng im -> Bị chặn bởi `ABSORPTION TRAP`.
  * Test mô phỏng lệnh đạt +0.12% MFE -> Stop Loss tự động kéo lên Entry + 0.04%, khi giá sập về Entry lệnh vẫn kết thúc với NET LÃI DƯƠNG.
  * Test mô phỏng lệnh chạm +0.25% MFE -> Khóa chắc +0.15% lợi nhuận.
</verification_gate>

<task_directive>
Bắt đầu ngay: Lập trình Bộ lọc Hấp thụ -> Ratchet Bảo hiểm phí 2 tầng -> Siết chặt Post-Only Maker -> Đồng thuận MTF 5m -> Viết test suite và chạy pytest xác thực 100% -> Báo cáo chi tiết.
</task_directive>
