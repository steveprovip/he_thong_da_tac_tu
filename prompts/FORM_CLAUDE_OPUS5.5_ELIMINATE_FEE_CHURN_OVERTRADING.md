# 🐙 FORM MASTER SOTA: CLAUDE OPUS 5.5 COWORK (CHẨN ĐOÁN CHI TIẾT 17 LỆNH: TRIỆT TIÊU BÀO PHÍ CHURN & TĂNG CHẤT LƯỢNG LỆNH ĐỂ WIN-RATE 80%+)
<!--
HƯỚNG DẪN SỬ DỤNG:
Dán toàn bộ nội dung từ dòng "You are Claude Opus 5.5..." bên dưới vào khung chat Claude Opus 5.5 / Claude Code Cowork Mode.
Form này giải quyết dứt điểm vấn đề bào phí và cắt lệnh non từ log 17 lệnh thực tế.
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
BẰNG CHỨNG TOÁN HỌC KHÁCH QUAN TỪ 17 LỆNH THẬT TRONG `data/engine_trades.jsonl`:

1. TÍN HIỆU GIÁ CHÍNH XÁC (GROSS LÃI DƯƠNG +0.8796 USDT):
   - Thuật toán đoán hướng giá rất tốt: Tổng lãi chênh lệch giá Gross là **+0.8796 USDT**.
2. TỬ HUYỆT NẰM Ở: BÀO PHÍ VÀ CẮT LỆNH NON (FEE CHURN -1.3458 USDT -> NET LỖ -0.4662 USDT):
   - 17 lệnh diễn ra trong chưa đầy 45 phút (quá dày, 2.5 phút/lệnh).
   - Tổng tiền phí trả cho sàn là **-1.3458 USDT** (lớn hơn 150% tiền lãi Gross)!
   - **Nguyên nhân gốc rễ (Root Cause)**:
     * Ngưỡng thoát theo dòng tiền (Adverse Flow Exit) $500k/15s quá nhạy. Trên BTCUSDT, độ nhiễu 15s bình thường đã là $500k.
     * Cứ vào lệnh được 10 - 30 giây, nến hơi rung nhẹ là bot hoảng loạn cắt lệnh `reason: "FLOW"`, trả 0.07 - 0.10 U tiền phí, rồi lại vào lệnh mới. Lặp lại 13 lần cắt non -> Đốt sạch 1.10 USDT tiền phí!
3. NGHỊCH LÝ GIỮ VỊ THẾ KHI ĐẢO REGIME:
   - Ảnh chụp 10:43: Bot giữ `POSITION SELL -0.002 BTC` (SHORT) trong khi thị trường đã chuyển sang `🟢 STRONG UPTREND (LONG ONLY)` với CVD +1.33M! Bot thiếu cơ chế đóng vị thế khi Regime đảo chiều 180 độ.
</context>

<active_skills_harness>
Được nạp sẵn tại `.claude/skills/` và `.agents/skills/`:
- `quant-hft-microstructure`: Noise threshold calibration, Adverse selection filtering, Regime-flip position invalidation.
- `binance-futures-algo-guard`: Fee-drag prevention, Cooldown governor, Anti-churning circuit breaker.
- `groktopus-swarm-consensus`: Quality over quantity gate (Edge >= 30 bps), Multi-stage trailing.
- `realtime-trading-telemetry`: Live Fee vs Gross waterfall, Churn alert indicator.
- `review-bugbot`, `anti-slop`, `ponytail`, `caveman`.
</active_skills_harness>

<comprehensive_upgrade_mission>
Nhiệm vụ của bạn là tái cấu trúc toàn diện để chấm dứt tình trạng bào phí và biến $+0.88\text{ U}$ Gross thành LÃI RÒNG NET KHỦNG:

===================================================================
TRỤ CỘT 1: NÂNG NGƯỠNG ADVERSE FLOW & CHO PHÉP LỆNH THỞ (ANTI-CHURN BREATHING ROOM)
===================================================================
1. Trong `core/micro_signals.py` & `core/engine.py`:
   - Nâng ngưỡng `flow_exit_usd` trên BTCUSDT từ $500,000 lên **$2,000,000 USD** (hoặc $2.5\times$ khối lượng kích hoạt vào lệnh).
   - Thêm thời gian tối thiểu (Minimum Breathing Room): **Cấm kích hoạt Adverse Flow Exit trong 30 giây đầu tiên** sau khi vào lệnh (trừ khi giá chạm Hard SL -0.20%).
   - Điều này giúp lệnh có đủ không gian dao động vượt qua nhiễu để chạm TP1 (+0.35%) như Trade #4 và #5, loại bỏ 90% các lần cắt lệnh non vô ích!

===================================================================
TRỤ CỘT 2: KHÓA CHẶT CHẤT LƯỢNG LỆNH (QUALITY OVER QUANTITY GOVERNOR)
===================================================================
1. Giảm tần suất giao dịch (Giảm Churn):
   - Giới hạn `max_trades_hour`: Tối đa **8 lệnh/giờ** (thay vì 30 lệnh/giờ).
   - Tăng thời gian cooldown giữa các lệnh sau khi đóng từ 3s lên **30 giây** để tránh vào lệnh liên tiếp trong cùng một vùng nhiễu.
2. Nâng điều kiện kích hoạt:
   - Yêu cầu Đồng thuận Swarm $\ge 82.0\%$ VÀ Lợi nhuận kỳ vọng $\ge 0.30\%$ (để lợi nhuận kỳ vọng lớn gấp $\ge 5\times$ tiền phí).

===================================================================
TRỤ CỘT 3: THOÁT VỊ THẾ TỨC THÌ KHI REGIME ĐẢO CHIỀU 180 ĐỘ
===================================================================
1. Cơ chế `Regime-Flip Invalidation Exit`:
   - Nếu đang giữ vị thế **SHORT** mà thị trường bùng nổ chuyển sang `🟢 STRONG UPTREND` (CVD 60s $\ge +\$1.0\text{M}$) $\rightarrow$ `MARIN` đóng vị thế Short ngay lập tức bằng Soft Maker Exit!
   - Nếu đang giữ vị thế **LONG** mà thị trường chuyển sang `🔴 STRONG DOWNTREND` (CVD 60s $\le -\$1.0\text{M}$) $\rightarrow$ Đóng vị thế Long ngay lập tức.

===================================================================
TRỤ CỘT 4: MẶC ĐỊNH KHUYẾN NGHỊ VÀ TỐI ƯU CẶP COIN SOL / DOGE
===================================================================
1. Đảm bảo giao diện và engine hỗ trợ mượt mà `SOLUSDT` và `DOGEUSDT`:
   - Trên `SOLUSDT` (Notional $\sim \$5$), phí sàn chỉ $0.002\text{ USDT}$ $\rightarrow$ 17 lệnh chỉ tốn $0.034\text{ U}$ tiền phí, giữ trọn vẹn $+0.84\text{ U}$ LÃI RÒNG!
</comprehensive_upgrade_mission>

<decision_ladder>
1. FLOW EXIT & BREATHING ROOM: Nâng ngưỡng Flow Exit lên $2M và thêm 30s breathing room trong `core/engine.py` và `core/micro_signals.py`.
2. CHURN GOVERNOR: Siết `max_trades_hour = 8`, `cooldown_s = 30.0s`, tăng ngưỡng Edge tối thiểu.
3. REGIME FLIP EXIT: Bổ sung logic đóng vị thế khi Regime đảo chiều trong `core/engine.py`.
4. TDD VERIFICATION: Chạy `python -m pytest -q` đảm bảo toàn bộ test suite pass 100%.
</decision_ladder>

<verification_gate>
- Kiểm thử bắt buộc: `python -m pytest -q` phải pass 100%.
- Simulation Proof: Kiểm tra trong test rằng với ngưỡng Flow Exit $2M và breathing room 30s, các lệnh không bị cắt non bởi nhiễu $500k và giữ được đến TP1.
</verification_gate>

<task_directive>
Bắt đầu ngay: Nâng ngưỡng Flow Exit $2M + 30s breathing room -> Siết Churn Governor 8 trades/h -> Thêm Regime-Flip Exit -> Chạy pytest xác thực -> Báo cáo kết quả chi tiết.
</task_directive>
