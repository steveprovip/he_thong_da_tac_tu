# 👑 FORM MASTER SOTA THẾ GIỚI: CLAUDE OPUS 5.5 MAX COWORK (ỨNG DỤNG LÝ THUYẾT ĐỊNH LƯỢNG HÀNG ĐẦU THẾ GIỚI: OFI CONCORDANCE, CARTEA-JAIMUNGAL DRIFT & CẤM ĐÁNH NGƯỢC MICRO-FLOW)
<!--
HƯỚNG DẪN SỬ DỤNG:
Dán toàn bộ nội dung từ dòng "You are Claude Opus 5.5..." bên dưới vào khung chat Claude Opus 5.5 / Claude Code Cowork Mode.
Form này tích hợp các công trình nghiên cứu kinh tế lượng hàng đầu thế giới (Cont-Stoikov 2014, Cartea-Jaimungal 2015, Lopez de Prado 2018) để triệt tiêu dứt điểm các lệnh đánh ngược dòng tiền siêu ngắn hạn.
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
BẰNG CHỨNG THỰC TẾ TỪ LỆNH AUTO SHORT LÚC 15:34 TRÊN GIAO DIỆN (COMMIT `e1a311d`, 934 TESTS PASS):

1. THỰC CHIẾN LỆNH AUTO SHORT:
   - Bot tự động mở SHORT 0.002 BTC tại giá `83,979.2`, SL trên sàn tại `84,105.2`, TP tại `83,664.2`.
   - Kết quả: Giá chạy ngược lên `84,036.6` (PnL -0.1148 U, -0.34% ROE).
2. NGUYÊN NHÂN TỬ HUYỆT (THE MICRO-FLOW CONCORDANCE TRAP):
   - Nhìn vào bảng Telemetry lúc mở lệnh:
     * Dòng tiền 3 phút $\text{CVD}_{3m} = -\$2.36\text{M}$ (quá khứ 3 phút phe bán xả mạnh $\rightarrow$ Phân loại Macro là DOWN).
     * Nhưng trong 60 giây và 15 giây gần nhất: Phe mua ồ ạt gom hàng $\text{CVD}_{60s} = +\$959.2\text{k}$ (DƯƠNG GẦN $1M) và $\text{OFI}_{L10} = +0.56$ (Sổ lệnh đang dồn phe mua).
   - 👉 LỖI NGHIÊM TRỌNG: Thuật toán dựa vào xu hướng cũ 3 phút để SHORT, đâm thẳng đầu vào cơn sóng mua gom $+1\text{M}\$$ đang đẩy giá tăng!
3. NGUYÊN LÝ ĐỊNH LƯỢNG TỪ CÁC CÔNG TRÌNH KINH TẾ LƯỢNG HÀNG ĐẦU THẾ GIỚI:
   - **Cont, Kukanov & Stoikov (2014) - "The Price Impact of Order Book Events"**: Vận tốc thay đổi giá tức thời $\Delta P_t$ tỷ lệ thuận $100\%$ với Order Flow Imbalance ($\text{OFI}_t$). Đánh ngược $\text{OFI}$ và $\text{CVD}_{60s}$ luôn có xác suất thua $> 75\%$.
   - **Cartea & Jaimungal (2015) - "Algorithmic and High-Frequency Trading"**: Cấm mở vị thế khi có sự phân kỳ giữa vi cấu trúc siêu ngắn hạn (Sub-minute flow) và xu hướng trung hạn (Drift Conflict).
</telemetry_audit_and_context>

<active_skills_harness>
Được tích hợp sẵn tại `.claude/skills/`, `.agents/skills/`, và workspace:
- `quant-hft-microstructure`: Multi-level OFI, Cont-Stoikov price impact, Absolute Flow Concordance, Avellaneda-Stoikov reservation pricing, Dynamic ATR bracket scaling.
- `binance-futures-algo-guard`: WebSocket multiplexing, Post-Only GTX maker routing, on-exchange Stop Loss ratchet.
- `groktopus-swarm-consensus`: Multi-Agent Swarm voting with strict Concordance Gating.
- `realtime-trading-telemetry`: Live OFI meter, Multi-Asset Radar.
- `review-bugbot`, `anti-slop`, `ponytail`, `caveman`.
</active_skills_harness>

<world_class_quantitative_mission>
Nhiệm vụ của bạn là hiện thực hóa 4 TRỤ CỘT ĐỘT PHÁ để TRIỆT TIÊU 100% CÁC LỆNH ĐÁNH NGƯỢC DÒNG TIỀN và tăng tỷ lệ thắng vượt bậc:

===================================================================
TRỤ CỘT 1: ĐỒNG THUẬN DÒNG TIỀN TUYỆT ĐỐI (TRIPLE CONCORDANCE FLOW GATING)
===================================================================
1. Trong `core/micro_signals.py` & `core/micro_swarm.py`:
   - Thiết lập **Quy tắc Đồng Thuận Ba Tầng (Triple Concordance Rule)**:
     * Để mở lệnh **BUY**:
       $$\text{CVD}_{15s} > 0 \quad \text{VÀ} \quad \text{CVD}_{60s} > +\$300\text{k} \quad \text{VÀ} \quad \text{OFI}_{10s} \ge +0.35$$
       (Nếu $\text{CVD}_{60s} < 0$ hoặc $\text{OFI} < 0$ $\rightarrow$ **TUYỆT ĐỐI CẤM BUY**).
     * Để mở lệnh **SELL**:
       $$\text{CVD}_{15s} < 0 \quad \text{VÀ} \quad \text{CVD}_{60s} < -\$300\text{k} \quad \text{VÀ} \quad \text{OFI}_{10s} \le -0.35$$
       (Nếu $\text{CVD}_{60s} > 0$ hoặc $\text{OFI} > 0$ $\rightarrow$ **TUYỆT ĐỐI CẤM SELL**).
   - **Triệt tiêu dứt điểm lỗi 15:34**: Không bao giờ mở lệnh SELL khi $\text{CVD}_{60s}$ đang dương ($+959\text{k}\$$) hoặc sổ lệnh $\text{OFI}$ đang nghiêng về phe mua!

===================================================================
TRỤ CỘT 2: KHÓA TRẠNG THÁI XUNG ĐỘT (DRIFT CONFLICT HARD LOCK)
===================================================================
1. Trong `core/engine.py`:
   - Khi hệ thống hiển thị trạng thái `DRIFT (60s/3m CONFLICT - WAIT)`:
     * Đây là trạng thái thị trường không đồng nhất (1 phút đánh nhau với 3 phút).
     * **KHÓA CỨNG $100\%$ CÁC LỆNH VÀO TỰ ĐỘNG (AUTO ENTRY)**.
     * Chỉ mở khóa giao dịch khi cả 60s và 3m cùng pha đồng nhất một chiều (Unidirectional Trend).

===================================================================
TRỤ CỘT 3: TỐI ƯU HÓA TỶ TRỌNG CƠ HỘI ĐA TÀI SẢN (SOL/DOGE SHARPE ROUTING)
===================================================================
1. Trong `core/engine.py`:
   - Trong ảnh lúc 15:34, BTC đang bị kẹt trong Drift Conflict, trong khi `SOL: TREND BUY (76%)` đang có sóng tăng cực đẹp.
   - Nâng cấp **Bộ điều phối khe cắm vị thế (Smart Asset Slot Selector)**:
     * Nếu tài sản A (BTC) đang ở trạng thái DRIFT / CHOP / CONFLICT $\rightarrow$ Ngay lập tức giải phóng quyền ưu tiên cho tài sản B (SOL/DOGE) có tín hiệu Trend sạch.
     * Không để tài sản yếu/nhiễu chiếm giữ slot của tài sản có sóng bứt phá rõ nét.

===================================================================
TRỤ CỘT 4: GIAO DIỆN HIỂN THỊ CONCORDANCE RADAR & CHỐT CHẶN XUNG ĐỘT
===================================================================
1. Cập nhật Web UI (`web_ui/desk.js`, `web_ui/index.html`):
   - Thêm chip trạng thái Đồng thuận: `FLOW: CONCORDANT (BULLISH)` hoặc `FLOW: CONFLICT (LOCKED)`.
</comprehensive_upgrade_mission>

<decision_ladder>
1. TRIPLE CONCORDANCE: Cài đặt quy tắc bắt buộc cùng chiều (CVD 15s + CVD 60s + OFI L10) vào `core/micro_signals.py`.
2. DRIFT HARD LOCK: Khóa 100% Auto Entry khi trạng thái là DRIFT CONFLICT trong `core/engine.py`.
3. MULTI-ASSET ROUTING: Tinh chỉnh bộ chọn tài sản ưu tiên cặp có sóng đồng thuận sạch nhất.
4. UI ENHANCEMENT: Cập nhật SSE telemetry và chip Concordance trên Web UI.
5. TDD VERIFICATION: Viết test suite chi tiết trong `tests/test_concordance_guard.py`, đảm bảo `python -m pytest -q` pass 100% (>= 950+ tests).
</decision_ladder>

<verification_gate>
- Toàn bộ test suite `python -m pytest -q` phải pass 100%.
- Bằng chứng kiểm thử:
  * Test tái hiện ca 15:34 (CVD 3m âm, nhưng CVD 60s +$959k và OFI +0.56) -> Bị chặn 100% không cho mở lệnh SELL.
  * Test trạng thái DRIFT CONFLICT -> Tự động khóa auto trade và chuyển slot ưu tiên sang SOL đang có TREND BUY.
</verification_gate>

<task_directive>
Bắt đầu ngay: Lập trình Triple Concordance Rule -> Khóa cứng Drift Conflict -> Tối ưu Smart Multi-Asset Routing -> Viết test xác thực -> Chạy pytest 100% -> Báo cáo chi tiết.
</task_directive>
