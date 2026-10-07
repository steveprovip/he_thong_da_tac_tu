# 🐙 FORM MASTER SOTA: CLAUDE OPUS 5.5 COWORK (CHẨN ĐOÁN LOG THỰC TẾ & NÂNG CẤP ĐỘNG CƠ FLOW-TREND 2 CHIỀU - CHỐNG BẮT DAO RƠI)
<!--
HƯỚNG DẪN SỬ DỤNG:
Dán toàn bộ nội dung từ dòng "You are Claude Opus 5.5..." bên dưới vào khung chat Claude Opus 5.5 / Claude Code Cowork Mode.
Form này tích hợp toàn bộ các kỹ thuật SOTA: quant-hft-microstructure, binance-futures-algo-guard, groktopus-swarm-consensus, flow toxicity VPIN, adverse selection gating.
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
CHẨN ĐOÁN GỐC RỄ TỪ LOG VÀ ẢNH CHỤP THỰC TẾ (PHÁT HIỆN LỖI NGHIÊM TRỌNG):
1. Bẫy Bắt Dao Rơi (Catching a Falling Knife):
   - Ảnh chụp thực tế cho thấy: Bot mở lệnh BUY BTCUSDT tại `85,488.7`, trong khi **CVD 60s đang BÁN THÁO -$3.44 TRIỆU USD** và Đồng hồ Áp lực chỉ `-25 SELL PRESSURE`!
   - Nguyên nhân: Tín hiệu Mean-Reversion (S2 VWAP) mù quáng phát tín hiệu BUY chỉ vì giá nằm dưới VWAP, hoàn toàn bỏ qua việc cá voi đang xả $3.44M.
2. Thiếu Chiến Lược Giao Dịch 2 Chiều (2-Way Trading):
   - Khi thị trường xả mạnh, bot lẽ ra phải vào lệnh **SHORT / SELL** để ăn sóng giảm, nhưng lại cố chấp BUY bắt đáy.
3. Kẹt Vị Thế Quá Lâu (238 giây) & Không Có Cơ Chế Cắt Lỗ Dòng Tiền (Flow Invalidation Exit):
   - MFE (Độ lệch lãi tối đa) chỉ đạt +0.04%, không chạm ngưỡng BE (+0.12%), khiến bot gồng lỗ 238s chờ dính Stop Loss.
   - Thiếu cơ chế: Nếu dòng tiền tiếp tục xả mạnh sau khi vào lệnh thì phải thoát ngay lập tức ở 1-2 tick thay vì gồng chết.
</context>

<active_skills_harness>
Được nạp sẵn tại `.claude/skills/` và `.agents/skills/`:
- `quant-hft-microstructure`: CVD flow imbalance, Orderbook toxic flow detection (VPIN/Adverse Selection), Hawkes momentum.
- `binance-futures-algo-guard`: 2-way Short/Long execution, Post-Only Maker GTX routing.
- `groktopus-swarm-consensus`: `LUMEN` flow gate, `NORO` trend alignment, `MARIN` dynamic flow exit.
- `realtime-trading-telemetry`: Live CVD gauge, Net P&L waterfall.
- `review-bugbot`, `anti-slop`, `ponytail`, `caveman`.
</active_skills_harness>

<comprehensive_upgrade_mission>
Nhiệm vụ của bạn là tái cấu trúc toàn diện logic tín hiệu và quản lý vị thế để hệ thống giao dịch THUẬN DÒNG TIỀN (Trend & Flow Following) thay vì bắt dao rơi:

===================================================================
TRỤ CỘT 1: CỔNG LỌC DÒNG TIỀN CỨNG (CVD FLOW-REGIME FILTER)
===================================================================
1. Trong `core/micro_signals.py` & `core/micro_swarm.py`:
   - **Quy tắc cấm cản tàu (Hard Regime Gate)**:
     * Nếu `CVD 60s < -$500,000 USD` (hoặc `cvd_norm < -0.15`): **TUYỆT ĐỐI CẤM MỞ LỆNH BUY** dưới bất kỳ hình thức nào. Tín hiệu Mean-Reversion S2 bị vô hiệu hóa khi dòng tiền đang xả ồ ạt.
     * Nếu `CVD 60s > +$500,000 USD` (hoặc `cvd_norm > +0.15`): **TUYỆT ĐỐI CẤM MỞ LỆNH SELL/SHORT**.
   - Chỉ cho phép Mean-Reversion khi thị trường đi ngang (CVD trung tính trong khoảng $\pm \$200,000$).

===================================================================
TRỤ CỘT 2: KÍCH HOẠT GIAO DỊCH 2 CHIỀU ĐỘNG (ADAPTIVE LONG & SHORT)
===================================================================
1. **Động cơ Bắn Lệnh SHORT / SELL Thuận Xu Hướng**:
   - Khi phát hiện: `CVD 60s < -$300,000` + `OBI L5 < -0.15` + Giá thủng VWAP 1m $\rightarrow$ Kích hoạt ngay tín hiệu **MỞ VỊ THẾ SHORT** (Post-Only Limit tại Best Ask) để kiếm lợi nhuận từ các đợt cá voi xả hàng!
2. **Động cơ Bắn Lệnh LONG / BUY Thuận Xu Hướng**:
   - Khi phát hiện: `CVD 60s > +$300,000` + `OBI L5 > +0.15` + Giá bứt phá trên VWAP 1m $\rightarrow$ Kích hoạt tín hiệu **MỞ VỊ THẾ LONG** (Post-Only Limit tại Best Bid).

===================================================================
TRỤ CỘT 3: THOÁT LỆNH DÒNG TIỀN ĐỘNG & HẠ NGƯỠNG BREAKEVEN (MARIN MICRO-BE)
===================================================================
1. **Cơ chế Cắt Lỗ Dòng Tiền (Adverse Flow Invalidation Exit)**:
   - Nếu đang giữ vị thế BUY, mà trong 15s tiếp theo CVD xả thêm $> \$500,000$ (áp lực bán gia tăng) $\rightarrow$ `MARIN` lập tức đóng lệnh ngay tại giá hòa vốn hoặc lỗ nhẹ 1 tick (không chờ tới 200s để dính full SL).
2. **Hạ Ngưỡng Kéo Dời Hòa Vốn (Micro-BE)**:
   - Với mức phí Maker 2 đầu chỉ là $0.04\%$, hạ ngưỡng kích hoạt Breakeven từ $+0.12\%$ xuống **$+0.07\%$ - $+0.08\%$**.
   - Ngay khi giá chạy lãi $+0.08\%$, kéo Stop Loss về **Hòa Vốn $+0.02\%$ Net** ngay lập tức để bảo toàn 100% tài khoản!

===================================================================
TRỤ CỘT 4: GIAO DIỆN HIỂN THỊ TRẠNG THÁI LONG/SHORT & CẢNH BÁO CVD
===================================================================
1. Trên Header & Desk UI:
   - Hiển thị rõ trạng thái Regime: `🔴 STRONG DOWNTREND (SHORT ONLY)` hoặc `🟢 STRONG UPTREND (LONG ONLY)` hoặc `⚪ NEUTRAL (2-WAY)`.
   - Bổ sung nút **`⚡ BẮN SHORT TEST`** bên cạnh nút **`⚡ BẮN LONG TEST`** để người dùng có thể thử nghiệm cả 2 chiều.
</comprehensive_upgrade_mission>

<decision_ladder>
1. SIGNAL & FLOW REFACTOR: Sửa `core/micro_signals.py` để bổ sung CVD Regime Gate và logic Short thuận xu hướng.
2. SWARM & INGESTION: Sửa `core/micro_swarm.py` và `core/engine.py` để hỗ trợ mở và quản trị vị thế SHORT hoàn chỉnh (TP/SL/BE đảo chiều chính xác).
3. MARIN FLOW EXIT: Thêm logic Adverse Flow Invalidation và hạ ngưỡng BE xuống +0.08% trong `core/engine.py`.
4. WEB UI UPDATE: Cập nhật `web_ui/desk.js` và `web_ui/index.html` hiển thị Regime và nút Short Test.
5. TDD VERIFICATION: Chạy `python -m pytest -q` đảm bảo toàn bộ 801+ test suite pass 100%.
</decision_ladder>

<verification_gate>
- Kiểm thử bắt buộc: `python -m pytest -q` phải pass 100%.
- Kiểm tra bài test: Đảm bảo khi CVD < -$500k thì bot từ chối lệnh BUY và tự động chuyển sang luồng SHORT.
</verification_gate>

<task_directive>
Bắt đầu ngay: Cài đặt CVD Flow Gate -> Kích hoạt Short thuận dòng tiền -> Cài đặt Adverse Flow Exit & Micro-BE +0.08% -> Chạy pytest xác thực -> Báo cáo kết quả chi tiết.
</task_directive>
