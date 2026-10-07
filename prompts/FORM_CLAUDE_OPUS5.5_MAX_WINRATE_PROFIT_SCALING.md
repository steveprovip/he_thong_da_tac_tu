# 🐙 FORM MASTER SOTA: CLAUDE OPUS 5.5 COWORK (TỐI ĐA HÓA WIN-RATE 85%+ & ỔN ĐỊNH LỢI NHUẬN RÒNG SAU CHUỖI LỆNH THẮNG +0.398 U & +0.183 U)
<!--
HƯỚNG DẪN SỬ DỤNG:
Dán toàn bộ nội dung từ dòng "You are Claude Opus 5.5..." bên dưới vào khung chat Claude Opus 5.5 / Claude Code Cowork Mode.
Form này tích hợp toàn bộ các kỹ thuật SOTA: Micro-Order Flow Acceleration, Depth L10 Imbalance, Volatility Contraction Squeeze, Multi-Stage Dynamic Trailing.
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
KẾT QUẢ ĐỘT PHÁ THỰC TẾ TRÍCH XUẤT TỪ `data/engine_trades.jsonl` VÀ GIAO DIỆN:
1. ĐỘNG CƠ ĐÃ SINH LỜI THẬT & LẬT NGƯỢC THẾ TRẬN THÀNH LÃI RÒNG NET DƯƠNG:
   - Trade #4: `BUY 0.002 BTC @ 83,913.9` -> `Exit 84,163.3` -> **LÃI RÒNG +0.3982 USDT (+1.18%)** (TP1+FLOW maker exit, 183s).
   - Trade #5: `BUY 0.002 BTC @ 83,890.8` -> `Exit 84,032.5` -> **LÃI RÒNG +0.1827 USDT (+0.54%)** (TP1+FLOW maker exit, 130s).
   - Tổng kết 5 lệnh: **NET PROFIT LÃI DƯƠNG +0.3258 USDT (Số dư tài khoản: 4924.6207 USDT)**.
2. VẤN ĐỀ CẦN TỐI ƯU TIẾP THEO:
   - Dù đã có lãi thực sự, lợi nhuận vẫn còn chịu biến động tùy theo nhịp sóng thị trường.
   - Mục tiêu: Nâng tỷ lệ thắng (Win-Rate) từ ~60% lên **85%+** và tối ưu hóa điểm chốt lời động để tối đa hóa lợi nhuận ròng trên mỗi chu kỳ sóng.
</context>

<active_skills_harness>
Được nạp sẵn tại `.claude/skills/` và `.agents/skills/`:
- `quant-hft-microstructure`: Flow velocity $d(\text{CVD})/dt$, Depth L10 book wall absorption, Volatility squeeze filter.
- `binance-futures-algo-guard`: Multi-stage trailing runner, ATR dynamic TP/SL brackets.
- `groktopus-swarm-consensus`: `TIDAL` volatility squeeze confirmation, `OKAPI` dynamic profit sizing.
- `realtime-trading-telemetry`: Live Win-Rate tracker, Net PnL equity waterfall.
- `review-bugbot`, `anti-slop`, `ponytail`, `caveman`.
</active_skills_harness>

<comprehensive_upgrade_mission>
Nhiệm vụ của bạn là nâng cấp toàn diện hệ thống để tối đa hóa Win-Rate và độ ổn định dòng tiền theo 4 trụ cột công nghệ HFT 2026:

===================================================================
TRỤ CỘT 1: GIA TỐC DÒNG TIỀN & ĐỘ SÂU SỔ LỆNH L10 (FLOW ACCELERATION & DEPTH WALLS)
===================================================================
1. Trong `core/micro_signals.py`:
   - Bổ sung chỉ số **Gia Tốc Dòng Tiền (CVD Acceleration $d\text{CVD}/dt$)**:
     * Đo tốc độ bơm tiền trong 10 giây so với 60 giây. Khi $d\text{CVD}/dt > +50,000\text{ USD/s}$ $\rightarrow$ Xác nhận lực mua bùng nổ của tổ chức (Smart Momentum Surge).
   - Bổ sung **Độ Sâu Sổ Lệnh L10 (Depth Imbalance Wall)**:
     * Kiểm tra tổng khối lượng Bid L10 vs Ask L10. Khi $\text{Depth L10 Imbalance} \ge +0.35$ (tường mua dày gấp 2 lần tường bán) $\rightarrow$ Củng cố xác suất thắng của lệnh LONG lên trên 85%!

===================================================================
TRỤ CỘT 2: BỘ LỌC NÉN BIẾN ĐỘNG (VOLATILITY SQUEEZE REGIME FILTER)
===================================================================
1. Khảo sát chu kỳ thị trường:
   - Các lệnh Breakout IOC có tỷ lệ thắng cao nhất khi giá bứt phá sau một giai đoạn **Nén Biến Động (Volatility Contraction)**.
   - Thêm bộ lọc `squeeze_active`: Khi dải nến 1m co hẹp (Spread/Range co lại $\le 0.08\%$) rồi bùng nổ vượt đỉnh/đáy với CVD lớn $\rightarrow$ Tăng điểm ưu tiên đồng thuận Swarm lên 9.5/10!

===================================================================
TRỤ CỘT 3: CHỐT LỜI ĐỘNG ĐA TẦNG (MULTI-STAGE VOLATILITY TRAILING TP)
===================================================================
1. Nâng cấp cơ chế chốt lời của `MARIN` trong `core/engine.py`:
   - **Tầng 1 (Khóa Vốn Vi Mô - Micro-Lock)**: Lãi $+0.08\%$ $\rightarrow$ Dời SL lên `Entry + 0.03%` (khống chế lỗ $\le 0.05\%$).
   - **Tầng 2 (Hòa Vốn Thực Sự - Full Breakeven)**: Lãi $+0.12\%$ $\rightarrow$ Dời SL lên `Entry + Phí + 0.02% Net`.
   - **Tầng 3 (Chốt 50% TP1)**: Lãi $+0.35\%$ $\rightarrow$ Khớp lệnh Maker bán 50% khối lượng để bỏ túi lợi nhuận chắc chắn (như Trade #4 và #5).
   - **Tầng 4 (Gồng Runner Đỉnh Cao - Tight Trailing $0.15\%$)**: Với 50% khối lượng còn lại, khi lãi đạt $\ge +0.70\%$, kéo khoảng cách Trailing sát nốt đỉnh chỉ $0.15\%$ (thay vì $0.20\%$) để vắt kiệt từng giọt lợi nhuận của con sóng lớn!

===================================================================
TRỤ CỘT 4: GIAO DIỆN THỐNG KÊ CHI TIẾT & WIN-RATE RADAR
===================================================================
1. Cập nhật Web UI:
   - Thống kê tỷ lệ Win-Rate thời gian thực (ví dụ: `Win Rate: 66.7% · 2W / 1L · Avg Win: +0.29U · Avg Loss: -0.07U · Profit Factor: 2.85`).
   - Hiển thị chỉ báo gia tốc dòng tiền CVD Velocity trên đồng hồ đo áp lực.
</comprehensive_upgrade_mission>

<decision_ladder>
1. SIGNAL & FLOW REFACTOR: Thêm CVD Velocity và Depth L10 Wall vào `core/micro_signals.py`.
2. SQUEEZE & SWARM ENGINE: Tích hợp Volatility Squeeze Gate vào `core/micro_swarm.py`.
3. MULTI-STAGE TRAILING: Nâng cấp 4 tầng chốt lời trong `core/engine.py`.
4. WEB UI REFRESH: Cập nhật `web_ui/desk.js` và `web_ui/index.html` hiển thị Win Rate, Profit Factor và CVD Velocity.
5. TDD VERIFICATION: Chạy `python -m pytest -q` đảm bảo toàn bộ test suite pass 100%.
</decision_ladder>

<verification_gate>
- Kiểm thử bắt buộc: `python -m pytest -q` phải pass 100%.
- Simulation Proof: Kiểm tra trong bài test rằng các đợt bùng nổ có CVD Velocity cao kết hợp Multi-Stage Trailing giúp Profit Factor đạt $\ge 2.5\times$.
</verification_gate>

<task_directive>
Bắt đầu ngay: Cài đặt CVD Velocity & Depth L10 -> Nâng cấp 4 tầng Trailing chốt lời -> Cập nhật Web UI thống kê Profit Factor -> Chạy pytest xác thực -> Báo cáo kết quả chi tiết.
</task_directive>
