# 🐙 FORM MASTER SOTA: CLAUDE OPUS 5.5 COWORK (CHẨN ĐOÁN 22 LỆNH: KHÓA CHẶT BẪY THOÁT NON HÒA PHÍ & BỨT PHÁ LÃI RÒNG NET)
<!--
HƯỚNG DẪN SỬ DỤNG:
Dán toàn bộ nội dung từ dòng "You are Claude Opus 5.5..." bên dưới vào khung chat Claude Opus 5.5 / Claude Code Cowork Mode.
Form này giải quyết tận gốc nguyên nhân 22 lệnh: Dự đoán đúng 100% hướng giá nhưng bị thoát non khi chưa đủ bù phí sàn!
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
BẰNG CHỨNG THỰC TẾ TRÍCH XUẤT TỪ 22 LỆNH TRONG `data/engine_trades.jsonl` (CẬP NHẬT 13:03):

1. TÍN HIỆU ĐOÁN HƯỚNG GIÁ CHÍNH XÁC 100% Ở CẢ 3 LỆNH MỚI NHẤT (#20, #21, #22):
   - Trade #20 (SELL): Vào 84,176.0 -> Ra 84,171.9 (Gross DƯƠNG +0.0082 U).
   - Trade #21 (BUY): Vào 84,188.7 -> Ra 84,269.8 (Gross DƯƠNG +0.1622 U, NET LÃI +0.0948 U THẮNG LỚN!).
   - Trade #22 (BUY): Vào 84,272.7 -> Ra 84,276.8 (Gross DƯƠNG +0.0078 U).
   - 👉 TỔNG GROSS PNL TĂNG VỌT LÊN **+1.0282 USDT (DƯƠNG VỮNG CHẮC)**!

2. 100% LỆNH MỚI ĐÃ VÀO BẰNG MAKER GTX (TIẾT KIỆM 35% PHÍ):
   - Phí mỗi lệnh giảm từ $0.101 U xuống $0.064 - $0.067 U.

3. TỬ HUYỆT DUY NHẤT CÒN LẠI: "BẪY THOÁT NON KHI CHƯA QUA RÀO CẢN PHÍ (PREMATURE FEE-TRAP EXITS)":
   - Ở Trade #20 và #22: Giá đi đúng hướng được +$4.1 USD (+0.005%), Gross đang dương (+0.008 U).
   - Nhưng bộ lọc FLOW EXIT và REGIME EXIT bị nhạy quá mức, kích hoạt thoát lệnh ngay tại mức +0.005% (mức này chưa đủ bù 0.04% phí maker 2 đầu = $34 USD trên BTC).
   - HẬU QUẢ: Thoát lệnh khi đang lãi +0.008 U gross khiến người dùng phải trả $0.067 U tiền phí và bị ép thành LỖ RÒNG NET -0.059 U!
</context>

<active_skills_harness>
Được nạp sẵn tại `.claude/skills/` và `.agents/skills/`:
- `quant-hft-microstructure`: Fee-Hurdle Exit Gating, Dynamic Trailing Thresholds, ATR-scaled barriers.
- `binance-futures-algo-guard`: Maker fee arithmetic, on-exchange protective stop.
- `groktopus-swarm-consensus`: Multi-Agent Swarm with trend-holding discipline.
- `realtime-trading-telemetry`: Live Fee-Hurdle meter, Profit expansion status.
- `review-bugbot`, `anti-slop`, `ponytail`, `caveman`.
</active_skills_harness>

<comprehensive_upgrade_mission>
Nhiệm vụ của bạn là cài đặt 4 TRỤ CỘT ĐỘT PHÁ để chấm dứt tình trạng thoát non và biến +1.028 USDT Gross thành LÃI RÒNG NET KHỦNG:

===================================================================
TRỤ CỘT 1: RÀO CẢN PHÍ CHO LỆNH THOÁT (FEE-HURDLE EXIT GATING)
===================================================================
1. Trong `core/engine.py`:
   - Phí Maker 2 đầu của BTC là $0.04\%$ ($\approx \$34\text{ USD}$ trên giá $84,000$).
   - **Quy tắc chặn thoát non (Fee-Hurdle Gate)**:
     * Khi vị thế đang có lãi nhẹ ($0 < \text{Gross} < +0.06\%$, tức giá mới chạy $+1\$$ đến $+\$30\$$):
     * **TUYỆT ĐỐI KHÔNG ĐƯỢC KÍCH HOẠT FLOW EXIT HOẶC REGIME EXIT**.
     * Lý do: Cắt lệnh ở vùng này đồng nghĩa với việc tự nguyện nộp $0.067\text{ U}$ tiền phí để nhận về $-0.05\text{ U}$ lỗ net!
   - Chỉ cho phép Flow/Regime Exit khi:
     a) Lợi nhuận đã vượt rào cản phí: $\text{Gross} \ge +0.08\%$ (đã chắc chắn có LÃI RÒNG NET DƯƠNG sau phí), HOẶC
     b) Giá đảo chiều thực sự rơi vào vùng âm lỗ: $\text{Gross} \le -0.08\%$.
   - Khi giá đang lình xình $+0.005\%$ đến $+0.05\%$, HÃY ĐỂ LỆNH CHẠY (Hold) để sóng giá phát triển lên $+0.25\% - +0.45\%$ như Trade #21!

===================================================================
TRỤ CỘT 2: TĂNG THỜI GIAN NUÔI LỆNH TỐI THIỂU (MINIMUM HOLDING TIME >= 90s CHO LỆNH DƯƠNG)
===================================================================
1. Trong `core/engine.py`:
   - Các lệnh thắng lớn (#4, #5, #6, #21) đều được gồng từ $130\text{s} - 250\text{s}$.
   - Nếu vị thế đang có lãi $\text{Gross} \ge 0$, cấm thoát Flow/Regime trong **90 giây đầu tiên** để tránh bị nhiễu micro-flow giật 1-2 nhịp làm mất vị thế đẹp.

===================================================================
TRỤ CỘT 3: NÂNG CHẤT LƯỢNG ĐIỂM VÀO LỆNH (KER >= 0.48 & CONFIRMATION VELOCITY)
===================================================================
1. Trong `core/micro_signals.py`:
   - Nâng ngưỡng mở lệnh $\text{KER}$ từ $0.45$ lên **$\ge 0.48$**.
   - Yêu cầu $\text{PriceVelocity}_{15s} \ge +0.04\%$ (đối với BUY) hoặc $\le -0.04\%$ (đối với SELL) để đảm bảo chỉ vào lệnh khi sóng đẩy thực sự mạnh mẽ, tránh vào ở đuôi sóng.

===================================================================
TRỤ CỘT 4: GIAO DIỆN HIỂN THỊ CHIP FEE-HURDLE & HOLDING DISCIPLINE
===================================================================
1. Cập nhật Web UI (`web_ui/desk.js`, `web_ui/index.html`):
   - Thêm trạng thái trên chip vị thế: `HOLDING: FEE HURDLE (+0.03% < +0.08%)` hoặc `PROFIT: UNLOCKED`.
</comprehensive_upgrade_mission>

<decision_ladder>
1. FEE-HURDLE EXIT GATING: Cài đặt logic chặn thoát non trong vùng 0% đến +0.06% vào `core/engine.py`.
2. HOLDING DISCIPLINE: Áp dụng 90s minimum hold cho vị thế có lãi nhẹ.
3. KER & VELOCITY TUNING: Cập nhật ngưỡng KER >= 0.48 và PriceVelocity >= 0.04% trong `core/micro_signals.py`.
4. UI TELEMETRY: Hiển thị trạng thái Fee-Hurdle trên Desk UI.
5. TDD VERIFICATION: Viết test case trong `tests/test_fee_hurdle.py`, chạy `python -m pytest -q` pass 100%.
</decision_ladder>

<verification_gate>
- Test suite `python -m pytest -q` phải pass 100% (>= 890+ tests).
- Test chứng minh: Vị thế BUY tại 84,000, giá nhích lên 84,005 (+0.006%), dòng tiền CVD có cú giật nhẹ -> Engine KHÔNG ĐƯỢC THOÁT, tiếp tục giữ lệnh cho đến khi đạt >= +0.08% hoặc chạm Stop Loss.
</verification_gate>

<task_directive>
Bắt đầu ngay: Lập trình Fee-Hurdle Exit Gating -> Nâng KER/Velocity -> Viết test xác thực -> Chạy pytest 100% -> Báo cáo chi tiết.
</task_directive>
