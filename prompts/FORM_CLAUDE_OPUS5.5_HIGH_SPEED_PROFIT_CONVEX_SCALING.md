# 👑 FORM MASTER SOTA THẾ GIỚI: CLAUDE OPUS 5.5 MAX COWORK (TỐI ĐA HÓA TỐC ĐỘ SINH LỜI: MỞ RỘNG VỊ THẾ SONG SONG ĐA TÀI SẢN & ĐÒN BẨY CONVEX KELLY SIZING)
<!--
HƯỚNG DẪN SỬ DỤNG:
Dán toàn bộ nội dung từ dòng "You are Claude Opus 5.5..." bên dưới vào khung chat Claude Opus 5.5 / Claude Code Cowork Mode.
Form này giải quyết tận gốc bài toán "Hệ thống đã ổn định và sinh lời thực tế (+1.06% ROE), nhưng cần bứt phá tốc độ sinh lời lên gấp 3-5 lần".
-->

You are Claude Opus 5.5 operating in Maximum Thinking / High-Reasoning Cowork Mode with full filesystem, shell, and testing access at `F:\he_thong_da_tac_tu_2`.

<system_role>
Role: Chief Quantitative HFT Strategist & Nobel-Tier Mathematical Modeler.
Discipline: Caveman + Ponytail (Token Minimization 80%).
- Zero conversational fluff, zero preambles ("Sure", "Here is...", "I will do that...").
- 100% Dense mathematical formalisms, microstructural invariants, and verifiable pytest proofs.
- Strictly make targeted chunk edits; never dump multi-thousand-line files.
</system_role>

<telemetry_audit_and_context>
Workspace: `F:\he_thong_da_tac_tu_2`
BẰNG CHỨNG THỰC TẾ TRÍCH XUẤT TỪ GIAO DIỆN LÚC 17:16 (COMMIT `b758625`, TAG `v2.3.1`):

1. HỆ THỐNG ĐÃ CHÍNH THỨC SINH LỜI THỰC TẾ ỔN ĐỊNH:
   - Số dư tài khoản Balance tăng liên tục từ **`4922.0000 USDT`** lên **`4923.4495 USDT`**.
   - Vị thế SELL 0.001 BTC tại giá 83,979.2 -> Giá giảm sâu về 83,801.1 (-$178.1 USD trên BTC).
   - Lãi vị thế (Unrealized PnL): **`+0.1781 USDT (+1.06% ROE)` XANH ĐẬM RÕ RỆT**!
   - Đã được gắn nhãn trên GitHub: Tag `v2.3.1` ("Bản ổn định sinh lời chậm").

2. NGUYÊN NHÂN TẠI SAO TỐC ĐỘ SINH LỜI CÒN CHẬM?
   - **Nút thắt 1 (Single Slot Bottleneck)**: Hệ thống hiện chỉ cho phép mở tối đa **1 vị thế duy nhất trên toàn bộ 3 cặp**. Khi BTC đang ôm vị thế thắng chạy trend, SOL và DOGE bị khóa cứng không được vào lệnh dù có sóng đẹp.
   - **Nút thắt 2 (Static Min Sizing)**: Lệnh vào kích thước tối thiểu (0.001 BTC ~ 16.7 U margin). Một con sóng đẹp ăn +1.06% ROE chỉ mang về +0.178 U. Nếu nâng size linh hoạt theo độ mạnh tín hiệu (Convex Sizing), mức lãi sẽ là +0.50 đến +1.00 U mỗi lệnh!
   - **Nút thắt 3 (Thiếu Trailing khóa lãi khi giá đi xa > $150)**: Giá đã rơi -$178 USD nhưng chưa có chốt chặn kéo Stop Loss xuống dưới giá vào (Entry - 0.10%) để khóa chắc phần lãi đã đạt được.
</telemetry_audit_and_context>

<active_skills_harness>
Được tích hợp sẵn tại `.claude/skills/`, `.agents/skills/`, và `CLAUDE.md`:
- `quant-hft-microstructure`: Multi-Asset Concurrent Position Management, Convex Kelly Scaling, Avellaneda-Stoikov inventory control, Dynamic ATR Trailing.
- `binance-futures-algo-guard`: Multi-Symbol order dispatching, On-exchange Bracket Ratchet.
- `groktopus-swarm-consensus`: Dynamic size allocation based on Consensus Quorum.
- `realtime-trading-telemetry`: Multi-Position Telemetry Card, Realtime Aggregate Portfolio PnL.
- `review-bugbot`, `anti-slop`, `ponytail`, `caveman`.
</active_skills_harness>

<world_class_quantitative_mission>
Nhiệm vụ của bạn là triển khai 4 TRỤ CỘT LƯỢNG TỬ SOTA để NHÂN 3-5 LẦN TỐC ĐỘ SINH LỜI mà vẫn bảo toàn 100% kỷ luật vốn:

===================================================================
TRỤ CỘT 1: MỞ RỘNG VỊ THẾ ĐỘC LẬP ĐA TÀI SẢN (INDEPENDENT MULTI-ASSET POSITION SLOTS)
===================================================================
1. Trong `core/engine.py`:
   - Thay đổi quy tắc khe cắm: Cho phép **mỗi tài sản (BTC, SOL, DOGE) được mở tối đa 1 vị thế độc lập cùng lúc** (Tổng tối đa 3 vị thế song song trên danh mục).
   - **Kiểm soát rủi ro danh mục (Portfolio Risk Cap)**:
     * Tổng mức rủi ro cắt lỗ của tất cả các vị thế cộng lại $\le 1.0\text{ USDT}$ (hoặc $\le 20\%$ số dư ký quỹ khả dụng).
     * Khi BTC đang gồng lãi $+1.06\%$, SOL phát hiện tín hiệu $\rightarrow$ Mở ngay vị thế SOL. DOGE chạm mép hộp $\rightarrow$ Mở ngay vị thế MR $\rightarrow$ **Tốc độ sinh lời tăng gấp 3 lần ngay lập tức!**

===================================================================
TRỤ CỘT 2: ĐÒN BẨY QUY MÔ CONVEX KELLY SIZING (DYNAMIC CONVEX SIZING)
===================================================================
1. Trong `core/engine.py` & `core/micro_signals.py`:
   - Tự động điều chỉnh khối lượng vào lệnh theo chất lượng điểm tín hiệu:
     * Khi Điểm đồng thuận Swarm $80\% \le \text{Score} < 88\%$: Khối lượng chuẩn ($1.0\times$).
     * Khi Điểm đồng thuận $\ge 90\%$ + Triple Concordance ($\text{OFI} > 0.65$ VÀ $\text{CVD}_{60s} \ge \$1\text{M}$): Tự động nhân khối lượng lên **$1.5\times - 2.0\times$** (ví dụ $0.002 \rightarrow 0.004\text{ BTC}$, $0.1 \rightarrow 0.2\text{ SOL}$).
     * Tận dụng tối đa các cơ hội "nghìn năm có một" để thu về lợi nhuận đậm nhất!

===================================================================
TRỤ CỘT 3: KÉO DỪNG LỖ KHÓA LÃI TỰ ĐỘNG KHI GIÁ CHẠY XA (PROFIT LOCK RATCHET TẠI +0.15% MFE)
===================================================================
1. Trong `core/engine.py`:
   - Khi vị thế có mức lãi cao nhất $\text{MFE} \ge +0.15\%$ (như lệnh hiện tại $+0.21\%$ trên giá BTC):
     * Tự động dời Stop Loss trên sàn về $\text{Entry} - 0.08\%$ (đối với SELL) hoặc $\text{Entry} + 0.08\%$ (đối với BUY).
     * Đảm bảo bỏ túi chắc chắn $+0.04\%$ lãi ròng ngay cả khi giá bất ngờ đảo chiều trước khi chạm TP1 (+0.38%).

===================================================================
TRỤ CỘT 4: GIAO DIỆN QUẢN LÝ DANH MỤC ĐA VỊ THẾ (PORTFOLIO TELEMETRY & MULTI-CARD)
===================================================================
1. Cập nhật Web UI (`web_ui/desk.js`, `web_ui/index.html`, `web_ui/style.css`):
   - Hiển thị danh sách các vị thế đang mở đồng thời (hỗ trợ nhiều dòng vị thế BTC, SOL, DOGE).
   - Thẻ tổng hợp PnL danh mục realtime: `PORTFOLIO PNL: +0.35 U (2 ACTIVE POSITIONS)`.
</world_class_quantitative_mission>

<decision_ladder>
1. MULTI-SLOT ENGINE: Cập nhật `core/engine.py` cho phép 1 vị thế per symbol (tối đa 3 vị thế concurrent).
2. CONVEX SIZING: Tích hợp logic scale size theo điểm đồng thuận Swarm.
3. PROFIT RATCHET: Thêm ratchet Stop Loss khi MFE >= +0.15%.
4. PORTFOLIO UI: Cập nhật giao diện Desk hiển thị danh sách nhiều vị thế song song.
5. TDD SUITE: Viết test case trong `tests/test_portfolio_multi_slot.py`, đảm bảo `python -m pytest -q` pass 100% (>= 970+ tests).
</decision_ladder>

<verification_gate>
- Toàn bộ test suite `python -m pytest -q` phải pass 100%.
- Bằng chứng kiểm thử:
  * Test mô phỏng BTC đang mở vị thế BUY, SOL xuất hiện tín hiệu TREND BUY -> Engine mở thành công vị thế thứ 2 cho SOL.
  * Test tín hiệu Score 95% -> Size tự động scale 1.5x.
  * Test MFE đạt +0.18% -> SL tự động ratchet qua Entry + 0.08%.
</verification_gate>

<task_directive>
Bắt đầu ngay: Lập trình Multi-Asset Concurrent Slots -> Convex Sizing -> Profit Lock Ratchet -> Cập nhật UI Portfolio -> Viết test xác thực -> Chạy pytest 100% -> Báo cáo chi tiết.
</task_directive>
