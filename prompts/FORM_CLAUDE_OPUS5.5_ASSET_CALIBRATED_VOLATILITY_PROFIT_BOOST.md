# 👑 FORM MASTER SOTA THẾ GIỚI: CLAUDE OPUS 5.5 MAX COWORK (HIỆU CHỈNH ĐỘ BIẾN ĐỘNG THEO TỪNG TÀI SẢN SOL/DOGE/BTC, NỚI RỘNG VÙNG THỞ SL & TĂNG TỐC ĐỘ SINH LỜI)
<!--
HƯỚNG DẪN SỬ DỤNG:
Dán toàn bộ nội dung từ dòng "You are Claude Opus 5.5..." bên dưới vào khung chat Claude Opus 5.5 / Claude Code Cowork Mode.
Form này giải quyết tận gốc nguyên nhân SOL bị quét Stop Loss do dùng chung khung biến động của BTC, và mở rộng tốc độ sinh lời trên toàn danh mục.
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
BẰNG CHỨNG THỰC TẾ TRÍCH XUẤT TỪ SỔ LỆNH DATA/ENGINE_TRADES.JSONL (COMMIT `ebb2553`, TAG `v2.4.0`):

1. CHIẾN THẮNG LỚN TRÊN BTC:
   - Trade #24 (BTCUSDT BUY): Vào 83,674.9 -> Ra 83,769.4 (Giá chạy +$94.5 USD đúng hướng!).
   - Gross: `+0.0945 U`, Phí: `-0.0335 U` -> **NET PNL LÃI +0.0610 USDT THẮNG ĐẬM** (Size mult 2.0x, OFI +0.84).
   - TỔNG GROSS PNL TĂNG VỌT LÊN **+1.0973 USDT**, WIN RATE TĂNG LÊN **24.0% (6 THẮNG)**!
2. NGUYÊN NHÂN SOL BỊ QUÉT SL OAN (#25 & #26):
   - SOL là altcoin có beta và độ biến động tự nhiên cao hơn BTC gấp 3 lần.
   - Độ biến động nhiễu 1 phút bình thường của SOL là $0.20\% - 0.30\%$ ($\approx \$0.25 - \$0.35$).
   - Việc áp dụng mức SL sàn tối thiểu $0.15\%$ ($\approx \$0.18$) khiến các lệnh SOL (#25 BUY tại 117.82, #26 SELL tại 117.12) bị quét râu nến trong 45-60s trước khi sóng kịp chạy!
3. VPIN BỊ NHẠY QUÁ MỨC (40 LẦN PAUSE / 45 PHÚT):
   - Rổ volume $1M trên BTC và $100k trên SOL quá nhỏ, khiến VPIN thường xuyên vượt 0.75 giả trong các phiên giao dịch bình thường, đóng băng hệ thống không cần thiết.
</telemetry_audit_and_context>

<active_skills_harness>
Được tích hợp sẵn tại `.claude/skills/`, `.agents/skills/`, và `CLAUDE.md`:
- `quant-hft-microstructure`: Asset-Specific Volatility Calibration (Bollerslev 1986, Nelson 1991 GARCH), VPIN Volume Bucket Tuning (Easley et al. 2012), Directional Asymmetric Toxicity Veto.
- `binance-futures-algo-guard`: Dynamic Multi-Symbol Bracket Scaling, Zero-Fee Maker Pegging.
- `groktopus-swarm-consensus`: Multi-Asset Swarm Execution.
- `realtime-trading-telemetry`: SSE Telemetry, Multi-Asset Radar.
- `review-bugbot`, `anti-slop`, `ponytail`, `caveman`.
</active_skills_harness>

<world_class_quantitative_mission>
Nhiệm vụ của bạn là hiện thực hóa 4 TRỤ CỘT LƯỢNG TỬ SOTA để TỐI ƯU HÓA BIÊN ĐỘ TỪNG TÀI SẢN VÀ BỨT PHÁ LỢI NHUẬN:

===================================================================
TRỤ CỘT 1: KHUNG BIẾN ĐỘNG RIÊNG CHO TỪNG COIN (ASSET-SPECIFIC ATR CALIBRATION)
===================================================================
1. Trong `core/micro_signals.py` & `core/engine.py`:
   - Phân tách ngưỡng dừng lỗ và chốt lời theo đặc tính dao động của từng coin:
     * **BTCUSDT (Low Volatility)**: $\text{SL}_{\text{min}} = 0.15\%$, $\text{TP1}_{\text{min}} = 0.38\%$ (hoặc $2.5\times\text{ATR}$).
     * **SOLUSDT (Medium/High Volatility)**: $\text{SL}_{\text{min}} = 0.35\%$ ($\approx \$0.40$ trên giá $117$), $\text{TP1}_{\text{min}} = 0.85\%$ (hoặc $2.5\times\text{ATR}$).
     * **DOGEUSDT (Extreme Volatility)**: $\text{SL}_{\text{min}} = 0.50\%$, $\text{TP1}_{\text{min}} = 1.25\%$ (hoặc $2.5\times\text{ATR}$).
   - Cho phép SOL và DOGE có đủ "vùng thở" tự nhiên, không bị quét râu nến 1-2 tick, giúp bắt trọn con sóng $+1.0\% - +2.0\%$ (mang về $+0.15\text{ đến }+0.30\text{ U}$ mỗi lệnh với mức phí chỉ $0.005\text{ U}$!).

===================================================================
TRỤ CỘT 2: TINH CHỈNH BỘ LỌC ĐỘ ĐỘC HẠI VPIN (ASYMMETRIC TOXICITY VETO)
===================================================================
1. Trong `core/micro_signals.py`:
   - Nâng kích thước rổ Volume Bucket để tránh nhiễu:
     * BTC: $\$2.5\text{M}$ (thay vì $\$1\text{M}$).
     * SOL: $\$250\text{k}$ (thay vì $\$100\text{k}$).
     * DOGE: $\$200\text{k}$.
   - **Cơ chế phủ quyết bất đối xứng (Asymmetric Toxicity Veto)**:
     * Khi $\text{VPIN} > 0.80$, chỉ chặn lệnh nếu dòng tiền độc hại đang **NGƯỢC CHIỀU** với lệnh dự kiến vào.
     * Nếu dòng tiền lớn cùng chiều với lệnh bứt phá (Informed Momentum Push), KHÔNG ĐƯỢC CHẶN.
     * Giảm $80\%$ số lần pause giả, giải phóng tần suất giao dịch!

===================================================================
TRỤ CỘT 3: TỐI ƯU HÓA TỶ TRỌNG KÝ QUÝ CHO SOL/DOGE (EQUAL RISK CAPITAL ALLOCATION)
===================================================================
1. Trong `core/engine.py`:
   - Hiện SOL chỉ vào $0.08\text{ SOL}$ ($\approx \$9.4\text{ notional}$, rủi ro SL chỉ $0.03\text{ U}$).
   - Nâng size cơ sở của SOL lên $0.25 - 0.35\text{ SOL}$ ($\approx \$30 - \$40\text{ notional}$, mức rủi ro SL $0.12\text{ U}$ tương đương với BTC $0.001$).
   - Khi SOL thắng $+1.0\%$, lợi nhuận ròng sẽ đạt **$+0.35\text{ USDT}$** (áp đảo hoàn toàn phí $0.008\text{ U}$)!

===================================================================
TRỤ CỘT 4: GIAO DIỆN HIỂN THỊ ASSET BRACKETS & VPIN ASYMMETRIC STATUS
===================================================================
1. Cập nhật Web UI:
   - Hiển thị rõ mức SL/TP riêng của từng coin khi chuyển tab: `SOL ATR SL 0.35% · TP1 0.85%` | `BTC ATR SL 0.15% · TP1 0.38%`.
</world_class_quantitative_mission>

<decision_ladder>
1. ASSET BRACKET CALIBRATION: Cài đặt cấu hình SL/TP min riêng cho BTC (0.15%), SOL (0.35%), DOGE (0.50%) vào `core/micro_signals.py`.
2. ASYMMETRIC VPIN: Tăng kích thước bucket và áp dụng luật phủ quyết bất đối xứng theo chiều flow.
3. EQUAL RISK SIZING: Chuẩn hóa notional của SOL/DOGE để rủi ro tương đương BTC (~$35 notional).
4. TDD SUITE: Viết test case trong `tests/test_asset_calibrated_brackets.py`, đảm bảo `python -m pytest -q` pass 100% (>= 985+ tests).
</decision_ladder>

<verification_gate>
- Toàn bộ test suite `python -m pytest -q` phải pass 100%.
- Test mô phỏng lệnh SOL với SL 0.35% không bị quét bởi nhịp giật 0.20%, sau đó giá chạy lên +0.85% chạm TP1 và mang về lãi ròng đậm.
- Test VPIN 0.82 cùng chiều lệnh bứt phá -> Được phép vào lệnh.
</verification_gate>

<task_directive>
Bắt đầu ngay: Lập trình Asset-Specific Brackets -> Asymmetric VPIN -> Equal Risk Sizing -> Viết test xác thực -> Chạy pytest 100% -> Báo cáo chi tiết.
</task_directive>
