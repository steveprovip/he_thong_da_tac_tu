# 🐙 FORM MASTER SOTA: CLAUDE OPUS 5.5 COWORK (CHẨN ĐOÁN 19 LỆNH: KHÓA CHẶT BẪY SIDEWAY CHOP VÀ NÂNG TP ĐỂ BỨT PHÁ LÃI RÒNG)
<!--
HƯỚNG DẪN SỬ DỤNG:
Dán toàn bộ nội dung từ dòng "You are Claude Opus 5.5..." bên dưới vào khung chat Claude Opus 5.5 / Claude Code Cowork Mode.
Form này giải quyết dứt điểm bẫy thị trường đi ngang (Chop/Consolidation) và tối ưu tỷ lệ Lãi/Phí.
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
BẰNG CHỨNG THỰC TẾ TRÍCH XUẤT TỪ 19 LỆNH TRONG `data/engine_trades.jsonl` VÀ GIAO DIỆN 11:50:

1. TÍN HIỆU GIÁ VẪN ĐOÁN ĐÚNG HƯỚNG (GROSS LÃI DƯƠNG +0.8500 USDT):
   - Tổng chênh lệch giá Gross vẫn là **+0.8500 USDT** dương.
2. NHƯNG PHÍ SÀN BÀO MÒN (-1.5476 USDT) -> NET ÂM -0.6976 USDT:
   - 4 lệnh THẮNG đều gồng $\ge 130\text{s}$ và giá đi xa $> \$140\text{ USD}$ (+0.18U, +0.20U, +0.39U).
   - 15 lệnh THUA đều là các lệnh bị ngâm trong vùng đi ngang (Sideway Chop) 84000 - 84120, giá không đi đâu cả ($0\text{ USD}$ move) rồi bị Regime-Flip cắt lỗ hòa giá nhưng mất tiền phí!
3. NGUYÊN NHÂN TỬ HUYỆT (THE CHOP TRAP & REGIME FLICKER):
   - **Bẫy thị trường đi ngang (Consolidation Trap)**: Khi thị trường giằng co trong biên hẹp ($< 0.12\%$), các lệnh Breakout IOC không thể bứt phá được mà bị hấp thụ.
   - **Cắt lỗ Regime trong Chop (Trade #18 & #19)**:
     * Trade #18: SELL 84054.6 -> Thoát 84054.6 (Gross 0.0, nhưng mất phí -0.1009 U do Regime đổi chiều).
     * Trade #19: BUY 84105.3 -> Thoát 84090.5 (Gross -0.029, mất phí -0.1009 U do Regime đổi chiều).
     * Trong thị trường sideway, CVD dao động quanh $\pm \$500\text{k}$ làm Regime lật qua lật lại, kích hoạt thoát Regime vô ích và mất trắng 0.23 USDT tiền phí!
</context>

<active_skills_harness>
Được nạp sẵn tại `.claude/skills/` và `.agents/skills/`:
- `quant-hft-microstructure`: Kaufman Efficiency Ratio (KER), Choppiness index filter, Regime hysteresis smoothing.
- `binance-futures-algo-guard`: Breakout target expansion, Fee-to-Target ratio optimization ($\ge 5\times$).
- `groktopus-swarm-consensus`: `TIDAL` trend efficiency gate, `MARIN` chop protection.
- `realtime-trading-telemetry`: Live KER gauge, Regime stability tracker.
- `review-bugbot`, `anti-slop`, `ponytail`, `caveman`.
</active_skills_harness>

<comprehensive_upgrade_mission>
Nhiệm vụ của bạn là nâng cấp toàn diện để KHÓA CHẶT BẪY SIDEWAY và biến $+0.85\text{ U}$ Gross thành LÃI RÒNG NET DƯƠNG:

===================================================================
TRỤ CỘT 1: BỘ LỌC HIỆU QUẢ XU HƯỚNG KAUFMAN (KER - CHOPPY MARKET LOCKOUT)
===================================================================
1. Trong `core/micro_signals.py`:
   - Tính toán chỉ số **Kaufman Efficiency Ratio (KER)** trên cửa sổ 2 phút:
     $$\text{KER} = \frac{|\text{Price}_t - \text{Price}_{t-120\text{s}}|}{\sum_{i=1}^{120} |\text{Price}_i - \text{Price}_{i-1}|}$$
   - **Quy tắc khóa Sideway cứng (Chop Lockout)**:
     * Nếu $\text{KER} < 0.40$ (thị trường đang giằng co, nến đi dích dắc không có xu hướng rõ ràng) $\rightarrow$ **TUYỆT ĐỐI CẤM MỞ LỆNH BREAKOUT IOC / TREND**.
     * Chỉ cho phép mở vị thế khi $\text{KER} \ge 0.45$ (thị trường có đà xu hướng bứt phá thực sự mượt mà).

===================================================================
TRỤ CỘT 2: CHỐNG NHIỄU REGIME-FLIP TRONG SIDEWAY (REGIME HYSTERESIS & DEBOUNCE)
===================================================================
1. Trong `core/engine.py`:
   - **Không kích hoạt Regime-Flip Exit nếu vị thế mới mở $< 60$ giây** (tránh bị giật quét 2 đầu trong sideway).
   - Yêu cầu Regime mới phải được duy trì liên tục tối thiểu **20 giây** (Debounce) mới được coi là đảo chiều thật sự, không cắt lệnh theo các cú nháy CVD 1-2 giây.
   - Khi thoát Regime, bắt buộc dùng **Soft Maker Exit** (chờ 3s tại Best Ask/Bid), không bắn Market Taker để tiết kiệm phí.

===================================================================
TRỤ CỘT 3: NỚI RỘNG MỤC TIÊU CHỐT LỜI (EXPAND TP TARGET >= +0.45%)
===================================================================
1. Tối ưu tỷ lệ Lợi nhuận / Phí sàn:
   - Với BTC (phí 2 đầu $\approx 0.067 - 0.10\text{ U}$), các mục tiêu nhỏ $+0.25\%$ dễ bị phí ăn bớt.
   - Nâng mục tiêu **TP1 từ $+0.35\%$ lên $+0.45\%$** ($\approx +\$380\text{ USD}$ trên giá BTC). Khi chạm TP1, mỗi lệnh thắng sẽ mang về **$+0.75\text{ USDT}$ LÃI RÒNG KHỦNG**, áp đảo hoàn toàn các lệnh cắt lỗ nhỏ!

===================================================================
TRỤ CỘT 4: GIAO DIỆN HIỂN THỊ CHỈ BÁO KER & CẢNH BÁO SIDEWAY CHOP
===================================================================
1. Cập nhật Web UI:
   - Hiển thị chỉ số $\text{KER}$ (Hiệu quả xu hướng) trên thanh trạng thái: `KER 0.52 (TRENDING)` hoặc `KER 0.28 (CHOPPY - LOCKED)`.
   - Hiển thị rõ lý do chặn lệnh `CHOP LOCKED (KER < 0.40)` khi thị trường đi ngang.
</comprehensive_upgrade_mission>

<decision_ladder>
1. KER IMPLEMENTATION: Thêm công thức tính Kaufman Efficiency Ratio vào `core/micro_signals.py`.
2. CHOP LOCKOUT & REGIME HYSTERESIS: Tích hợp KER Gate và 60s/20s Debounce vào `core/engine.py`.
3. EXPAND TP1: Nâng TP1 lên +0.45% để tối đa hóa lợi nhuận mỗi lệnh thắng.
4. WEB UI REFRESH: Cập nhật `web_ui/desk.js` và `web_ui/index.html` hiển thị chỉ số KER.
5. TDD VERIFICATION: Chạy `python -m pytest -q` đảm bảo toàn bộ test suite pass 100%.
</decision_ladder>

<verification_gate>
- Kiểm thử bắt buộc: `python -m pytest -q` phải pass 100%.
- Simulation Proof: Kiểm tra trong test rằng khi thị trường sideway giật 2 đầu (KER < 0.40), bot từ chối vào lệnh và không bị dính bẫy Regime-Flip như Trade #18 & #19.
</verification_gate>

<task_directive>
Bắt đầu ngay: Cài đặt KER Chop Filter -> Chống nhiễu Regime Debounce -> Nâng TP1 lên +0.45% -> Chạy pytest xác thực -> Báo cáo kết quả chi tiết.
</task_directive>
