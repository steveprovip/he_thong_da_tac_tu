# 👑 FORM MASTER SOTA THẾ GIỚI: CLAUDE OPUS 5.5 MAX COWORK (PHÂN TÍCH 34 LỆNH: SOL ĂN LÃI ĐẬM +0.33U, CÔ LẬP SIDEWAY BTC & ĐỘT PHÁ LỢI NHUẬN DANH MỤC)
<!--
HƯỚNG DẪN SỬ DỤNG:
Dán toàn bộ nội dung từ dòng "You are Claude Opus 5.5..." bên dưới vào khung chat Claude Opus 5.5 / Claude Code Cowork Mode.
Form này giải mã sự thật định lượng từ 34 lệnh: SOL mang lại lãi ròng khủng (+0.33 USDT), trong khi BTC bị kẹt trong hộp giằng co $100 làm kéo tụt tài khoản.
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
BẰNG CHỨNG ĐỊNH LƯỢNG RÕ RÀNG TỪ SỔ LỆNH DATA/ENGINE_TRADES.JSONL (34 LỆNH THỰC TẾ):

1. SOL LÀ CỖ MÁY IN TIỀN THỰC SỰ (3 LỆNH THẮNG ĐẬM LIÊN TIẾP):
   - Trade #27 (SOL SELL 0.54 SOL): LÃI RÒNG NET `+0.1024 USDT` (MFE +0.20%, Ratchet ON).
   - Trade #29 (SOL SELL 0.27 SOL): LÃI RÒNG NET `+0.0189 USDT`.
   - Trade #32 (SOL SELL 0.45 SOL): LÃI RÒNG NET `+0.2085 USDT (SIÊU THẮNG LỚN!)` (Giá giảm từ 116.89 về 116.38, MFE +0.44%, Profit Lock ON).
   - 👉 TỔNG LÃI RÒNG TỪ SOL: **`+0.3298 USDT`**! Phí trên SOL cực thấp (chỉ $0.021 U trên $52 notional).

2. TỬ HUYỆT DUY NHẤT: BTC BỊ KẸT TRONG HỘP SIDEWAY $100 (83480 - 83600):
   - 3 lệnh thua của BTC (#28, #30, #31) bị ngâm trong hộp dao động hẹp $0.14\%$ suốt 5-10 phút (hold 600s, 493s, 297s), làm mất `-0.7920 USDT`.
   - Trong khi SOL chạy trend cực mượt thì BTC bị dính bẫy sideway giật 2 đầu làm bào mòn toàn bộ phần lãi của SOL.
</telemetry_audit_and_context>

<active_skills_harness>
Được tích hợp sẵn tại `.claude/skills/`, `.agents/skills/`, và `CLAUDE.md`:
- `quant-hft-microstructure`: Asset Relative Sharpe Allocation, Cross-Asset Momentum Dispersion, Time-Decay Consolidation Ejection.
- `binance-futures-algo-guard`: Multi-Asset Capital Routing, High-Beta Priority Execution.
- `groktopus-swarm-consensus`: Swarm Consensus with Asset-Specific Thresholds.
- `realtime-trading-telemetry`: SSE Telemetry, Multi-Asset PnL Decomposition.
- `review-bugbot`, `anti-slop`, `ponytail`, `caveman`.
</active_skills_harness>

<world_class_quantitative_mission>
Nhiệm vụ của bạn là triển khai 4 TRỤ CỘT LƯỢNG TỬ SOTA để CÔ LẬP RỦI RO BTC VÀ TỐI ĐA HÓA CỖ MÁY IN TIỀN SOL/DOGE:

===================================================================
TRỤ CỘT 1: ƯU TIÊN PHÂN BỔ VỐN CHO ALTCOIN HIGH-BETA (SOL/DOGE FIRST ROUTING)
===================================================================
1. Trong `core/engine.py`:
   - Phân cấp ưu tiên tài sản dựa trên tỷ lệ Lợi nhuận/Phí thực tế:
     * **SOL/DOGE**: Ưu tiên số 1 khi có tín hiệu Trend sạch. Nâng trần quy mô vị thế SOL lên $0.60 - 0.80\text{ SOL}$ ($\approx \$70 - \$90\text{ notional}$) trên các setup điểm cao $\ge 90\%$.
     * Mỗi lệnh thắng trên SOL như Trade #32 sẽ mang về **$+0.40\text{ đến }+0.60\text{ USDT}$**!

===================================================================
TRỤ CỘT 2: SIẾT CHẶT ĐIỀU KIỆN VÀO LỆNH TRÊN BTC (BTC CHOP ISOLATION: KER >= 0.55)
===================================================================
1. Trong `core/micro_signals.py`:
   - Phân tách ngưỡng KER theo từng coin:
     * **SOL/DOGE**: $\text{KER} \ge 0.42$ (dễ bắt sóng vì altcoin có đà chạy mạnh).
     * **BTC**: Nâng ngưỡng lên **$\text{KER} \ge 0.55$** (chỉ cho phép mở lệnh BTC khi xu hướng thực sự rõ nét và dốc thẳng, loại bỏ $100\%$ các cú lừa giằng co trong hộp $\$100$).
   - Khi BTC có $\text{KER} < 0.55$, hệ thống **KHÓA BTC VÀ CHUYỂN TOÀN BỘ VỐN CHO SOL/DOGE**.

===================================================================
TRỤ CỘT 3: THOÁT SỚM KHI VỊ THẾ BỊ NGÂM KHÔNG CHẠY (TIME-DECAY CHOP EJECTION TẠI 180s)
===================================================================
1. Trong `core/engine.py`:
   - Nhìn vào 3 lệnh thua BTC: giữ $600\text{s}, 493\text{s}, 297\text{s}$ mà giá không chịu chạy.
   - **Quy tắc thoát ngâm (Time-Decay Ejection)**:
     * Nếu vị thế mở quá **180 giây (3 phút)** mà mức $\text{MFE} < +0.08\%$ (giá lình xình đi ngang quanh entry):
     * Tự động kích hoạt **Soft Maker Ejection** đóng lệnh ở mức hòa vốn hoặc lỗ nhỏ $0.02\%$.
     * Không bao giờ để một vị thế sideway bị ngâm $10$ phút rồi quay đầu dính full SL $-0.20\%$!

===================================================================
TRỤ CỘT 4: GIAO DIỆN HIỂN THỊ TỶ TRỌNG PNL TỪNG COIN (ASSET PNL DECOMPOSITION)
===================================================================
1. Cập nhật Web UI:
   - Hiển thị PnL riêng biệt của từng coin trên radar: `BTC: -0.79 U` | `SOL: +0.33 U (PROFIT MAKER)` | `DOGE: 0.00 U`.
</world_class_quantitative_mission>

<decision_ladder>
1. ASSET SPECIFIC KER: Cài đặt KER >= 0.55 cho BTC và KER >= 0.42 cho SOL/DOGE trong `core/micro_signals.py`.
2. TIME DECAY EJECTION: Cài đặt logic đóng vị thế sau 180s nếu MFE < 0.08% vào `core/engine.py`.
3. SOL SCALING: Nâng size tối đa cho SOL lên 0.70 SOL khi điểm >= 90%.
4. UI ASSET BREAKDOWN: Cập nhật Desk UI hiển thị PnL từng coin.
5. TDD SUITE: Viết test case trong `tests/test_sol_priority_chop_ejection.py`, đảm bảo `python -m pytest -q` pass 100% (>= 1000+ tests).
</decision_ladder>

<verification_gate>
- Toàn bộ test suite `python -m pytest -q` phải pass 100% (>= 1000+ tests).
- Test mô phỏng BTC trong hộp sideway (KER 0.48) bị chặn, trong khi SOL (KER 0.45) được vào lệnh.
- Test vị thế bị ngâm 180s với MFE 0.03% -> Kích hoạt Ejection đóng ở hòa vốn.
</verification_gate>

<task_directive>
Bắt đầu ngay: Asset-Specific KER -> Time-Decay Ejection 180s -> SOL Priority Scaling -> Viết test xác thực -> Chạy pytest 100% -> Báo cáo chi tiết.
</task_directive>
