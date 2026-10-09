# Form Claude Cloud / Opus 5.5 High: Nghiên Cứu Đột Phá Tình Trạng Lỗ, Lọc Regime Tránh Whipsaw & Nâng Quy Mô Giao Dịch Lên 10% Vốn

**Ngày lập**: 09/10/2026  
**Workspace**: `F:\he_thong_da_tac_tu_2`  
**Mục tiêu**: Giải quyết dứt điểm tình trạng whipsaw khi thị trường đi ngang, nâng cao Sharpe/DSR của Làn Trend 4H, và nâng quy mô phân bổ vị thế lên **10% vốn tài khoản** theo yêu cầu trực tiếp từ người dùng.

---

## 1. Hiện Trạng Thực Tế & Dữ Liệu Sau Khi Kích Hoạt Làn Trend 4H Demo (17:49 - 18:07)

1. **Làn Scalp Vi mô độc lập đã tắt thành công**:
   - Chuyển sang `OBSERVER` / `CHOP`, không còn mở lệnh vi mô rác gây bào mòn phí (Fee Bleed).
   - Realized PnL cũ dừng ở `-2.4539 U`, không tăng thêm lệnh lỗ vi mô nào.
2. **Làn Trend 4H đã vào 3 lệnh Demo bằng Maker (`core/trend_executor.py`)**:
   - BTC: BUY 0.0036 BTC ($297 notional) @ 82,470.8 — Disaster Stop sàn 74,955.5 (8 ATR).
   - SOL: BUY 1.85 SOL ($203 notional) @ 109.61 — Disaster Stop sàn 91.98 (8 ATR).
   - DOGE: SELL 3563 DOGE ($300 notional) @ 0.08407 — Disaster Stop sàn 0.1022 (8 ATR).
   - uPnL tạm tính đang dao động `-0.7732 U` do rung lắc giá ngắn hạn.
3. **Vấn đề cốt lõi cần đột phá (Từ số liệu Replay 2025–2026)**:
   - Bản Trend 4H Donchian + TSMOM hiện tại có **DSR = 0.19** (chưa có ý nghĩa thống kê), **Winrate chỉ 34%**, **Trung vị lệnh âm -59 bps**.
   - Khi thị trường đi ngang (Chop), Donchian 120 sinh tín hiệu trễ ở đỉnh/đáy nến 4H dẫn đến hiện tượng **Whipsaw (mua đỉnh bán đáy liên tục)**, khiến tài khoản bị bào mòn trước khi bắt được một con sóng lớn.
   - Quy mô lệnh hiện tại bị kẹp cố định ở mức nhỏ ($150 - $300), chưa tương xứng với quy mô vốn và chưa tối ưu hoá theo tăng trưởng vốn.

---

## 2. Nội Dung Form Dành Cho Claude Cloud (Copy Toàn Bộ Bên Dưới)

```markdown
<request>
Bạn là Chuyên gia Định lượng Trưởng (Principal Quantitative & Market Microstructure Architect) phụ trách dự án GrokTOPUS tại repository https://github.com/steveprovip/he_thong_da_tac_tu (nhánh main).

Dữ liệu thực nghiệm ngày 09/10/2026 cho thấy:
- Làn vi mô đã tắt standalone scalping thành công để dừng bào phí VIP0.
- Làn Trend 4H (`core/trend_executor.py`) đã kích hoạt trên Demo nhưng bộc lộ điểm yếu: DSR chỉ 0.19, Winrate 34%, trung vị lệnh âm -59 bps do bị dính Whipsaw khi thị trường đi ngang (Chop/False Breakout).
- Người dùng yêu cầu: (1) Đột phá thuật toán để thoát khỏi tình trạng lỗ dập dình, và (2) Nâng quy mô giao dịch lên khoảng **10% vốn tài khoản**.

Hãy thiết kế và triển khai gói nâng cấp kiến trúc: **ALPHA REGIME BREAKTHROUGH & 10% CAPITAL ALLOCATION OVERHAUL**:

---

### NHIỆM VỤ 1: ĐỘT PHÁ BỘ LỌC REGIME ĐA KHUNG (MULTI-TIMEFRAME REGIME GATING)
1. **Khắc phục Whipsaw của Donchian 4H / TSMOM**:
   - Tích hợp bộ lọc chế độ thị trường Macro (Regime Filter) trước khi cho phép Làn Trend 4H mở lệnh:
     - Sử dụng **Kaufman Efficiency Ratio 4H (KER 4H)** kết hợp **ADX(14) 4H** và **CVD Momentum 4H**:
       - **Trending Mode (KER > 0.40, ADX > 25)**: Cho phép Làn Trend 4H mở/đảo vị thế theo Donchian + TSMOM để bắt sóng lớn.
       - **Chop / Mean-Reverting Mode (KER < 0.25, ADX < 20)**: Tuyệt đối KHÔNG mở lệnh Breakout Donchian (tránh mua đỉnh bán đáy). Thay vào đó giữ trạng thái Cash/Observer 100% hoặc kích hoạt cơ chế Mean-Reversion biên độ rộng.
2. **Lọc False Breakout bằng Volume & CVD Skew**:
   - Chỉ xác nhận breakout nến 4H khi có sự đồng thuận của khối lượng (Volume > 1.2x SMA 20) và Cumulative Volume Delta (CVD) cùng chiều với hướng bứt phá.

---

### NHIỆM VỤ 2: NÂNG QUY MÔ GIAO DỊCH LÊN 10% VỐN (10% CAPITAL SIZING & DYNAMIC FRACTIONAL KELLY)
1. **Cấu hình Sizing 10% Vốn**:
   - Cập nhật cơ chế phân bổ vốn trong `core/trend_executor.py` và `core/engine.py`:
     - Bổ sung tham số cấu hình: `capital_allocation_pct = 0.10` (10% vốn tài khoản cho mỗi lệnh/vị thế).
     - Với tài khoản Demo ($5,000 USDT), quy mô vị thế mục tiêu (target notional) nâng lên **~$500 USDT** (hoặc margin 10% = $500 với leverage 5x = notional $2,500 tuỳ theo cấu hình an toàn).
2. **Dynamic Volatility Adjuster (Fractional Kelly 0.25x - 0.5x)**:
   - Điều chỉnh size linh hoạt theo độ biến động (ATR 4H):
     - Khi ATR thấp (thị trường nén biến động trước khi nổ trend): Tăng size lên tới trần 10% vốn.
     - Khi ATR quá cao (thị trường hỗn loạn/biến động cực đại): Giảm size tương ứng để giữ rủi ro mỗi lệnh $\le 1.0\%$ vốn tài khoản.

---

### NHIỆM VỤ 3: TỐI ƯU HOÁ THOÁT LỆNH & KHOÁ LỢI NHUẬN (DYNAMIC PROFIT LOCK)
1. **Không dùng TP/SL chặt kiểu vi mô (tránh cắt cụt sóng)**:
   - Duy trì Hard Disaster Stop 8×ATR trên sàn để phòng hộ khi sập kết nối.
2. **Kích hoạt Dynamic Chandelier Trailing khi Lãi Lớn**:
   - Khi vị thế đạt mức lãi $\ge +2.0\%$ (+200 bps): Tự động kích hoạt cơ chế Trailing Stop mềm (theo Chandelier 3.5 ATR hoặc Donchian Mid-band) để bảo vệ phần lãi đã có, không để một vị thế lãi lớn bị quay đầu thành lỗ khi trend đảo chiều đột ngột.

---

### NHIỆM VỤ 4: REPLAY KIỂM ĐỊNH & BẢO ĐẢM AN TOÀN
1. **Backtest / Replay So Sánh**:
   - Sử dụng `tools/trend_execution_replay.py` chạy trên toàn bộ dữ liệu 2025–2026.
   - So sánh trước và sau khi có Regime Filter: Đo lường mức cải thiện Sharpe (mục tiêu $> 1.0$), DSR (mục tiêu $> 0.80$), và giảm Maximum Drawdown.
2. **Kiểm thử 100% Xanh**:
   - Đảm bảo toàn bộ test suite ($1,452+$ tests) tiếp tục PASS 100%.
3. **Bảo toàn Dữ liệu**:
   - Giữ nguyên vẹn lịch sử trong `data/engine_trades.jsonl` và `data/trend_trades.jsonl`.
4. **Báo cáo Nghiệm thu**:
   - Ghi chi tiết kết quả vào `docs/research_upgrade/report_regime_breakthrough_and_10pct_sizing_2026-10-09.md`.
</request>
```

---

## 3. Hướng Dẫn Sử Dụng Trên Claude Cloud

1. **Mở phiên Claude Cloud** tại giao diện liên kết với repo `https://github.com/steveprovip/he_thong_da_tac_tu` (nhánh `main`).
2. **Copy toàn bộ nội dung trong khối `<request> ... </request>`** ở Mục 2.
3. **Dán vào Claude Cloud** để Claude Cloud tự động kích hoạt $100 Cloud Credits, đọc codebase trên GitHub và thực hiện toàn bộ quá trình nghiên cứu, viết code, chạy kiểm thử và xuất báo cáo.
