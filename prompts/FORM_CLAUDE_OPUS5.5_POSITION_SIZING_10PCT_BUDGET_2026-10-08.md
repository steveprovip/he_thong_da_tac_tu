# Form Claude Opus 5.5 High: Nghiên cứu Nâng giới hạn Vị thế lên 10% Ngân sách

Ngày lập: 08/10/2026. Workspace: `F:\he_thong_da_tac_tu_2`.
Tiêu đề phiên bản: **"có sinh lời, tuỳ thời điểm"**

---

## 1. Bối cảnh thực nghiệm: Đã có chuỗi sinh lời thực tế trên SOL

Nhật ký thực tế từ hệ thống (Lệnh #45 & #46 lúc 16:13 - 16:18):
1. **Lệnh #45 (SOL BUY 0.40)**: Vào 114.84, Ratchet Tầng 2 dời SL lên 114.99 (+0.13%), chốt lời REGIME ở 115.13 (+1.06% ROE) $\rightarrow$ **Net Lãi `+0.0976 U`**!
2. **Lệnh #46 (SOL SELL 0.13)**: Vào 114.89, chốt lời REGIME ở 114.80 $\rightarrow$ **Net Lãi `+0.0057 U`**!
3. **Hiệu suất trong ngày**: Tỷ lệ thắng đạt **5W / 9L (55.6% Win Rate)**, kéo Realized PnL ngày từ `-0.1718 U` về **`-0.0684 U`**, đưa SOL lũy kế lên **`+0.27 U` (PROFIT MAKER)**.
4. **Vấn đề đặt ra**: Hiện tại size lệnh còn nhỏ ($0.40\text{ SOL} \approx \$46\text{ USDT}$ giá trị vị thế, tức $< 1\%$ ngân sách $5,000\text{ USDT}$), khiến lãi tuyệt đối mỗi lệnh còn nhỏ ($+0.097\text{ U}$). Người dùng muốn nghiên cứu: **Nâng giới hạn giao dịch lên 10% tổng ngân sách có khả quan không?**

---

## 2. Lệnh giao việc cho Claude Opus 5.5 High (Sao chép phần dưới)

```markdown
<request>
Bạn là Chuyên gia Định lượng Cao cấp (Principal Quantitative Risk & Portfolio Manager) phụ trách dự án GrokTOPUS tại F:\he_thong_da_tac_tu_2.
Hệ thống vừa chứng minh năng lực sinh lời thực tế trên SOLUSDT (Winrate ngày 55.6%, Lệnh #45 ăn +1.06% ROE lãi ròng +0.0976 U nhờ Ratchet Tầng 2).

Hãy thực hiện nghiên cứu chuyên sâu về bài toán QUẢN TRỊ VỐN & ĐỊNH CỠ VỊ THẾ (Position Sizing & Capital Allocation) để trả lời câu hỏi:
"Nâng giới hạn mức giao dịch lên 10% tổng ngân sách có khả quan không và triển khai như thế nào để tối đa lợi nhuận mà không cháy tài khoản?"

### NỘI DUNG NGHIÊN CỨU & THIẾT KẾ:

1. **Phân biệt rạch ròi 2 định nghĩa về "10% Ngân sách"**:
   - **Phương án A: 10% Notional Exposure (Khối lượng vị thế = 10% Vốn)**:
     - Với vốn $5,000 USDT, vị thế tối đa = $500 USDT (tương đương ~4.35 SOL hoặc ~5,500 DOGE).
     - Khi Stop Loss ở mức 0.20% - 0.35% (theo ATR), số tiền rủi ro thực tế khi dính SL chỉ là:
       Risk = $500 x 0.35% = $1.75 USDT (chỉ chiếm 0.035% tài khoản!).
     - Khi ăn sóng TP1 +1.0% (như lệnh #45), lãi ròng thu về sẽ là **+$1.06 USDT / lệnh** (gấp 10 lần hiện tại).
   - **Phương án B: 10% Risk-at-Stop (Số tiền mất khi dính SL = 10% Vốn)**:
     - Mỗi lệnh dính SL mất $500 USDT. Theo lý thuyết Fractional Kelly (Edward Thorp 2007) và Ralph Vince Optimal F, mô hình này trong HFT sẽ dẫn tới xác suất cháy tài khoản (Risk of Ruin) > 45% sau chuỗi 5 lệnh thua liên tiếp.

2. **Mô phỏng Monte Carlo & Tối ưu Fractional Kelly (Quarter-Kelly)**:
   - Dựa trên 47 lệnh thực nghiệm trong `data/engine_trades.jsonl` (tách riêng hiệu suất SOL winrate 55.6%, RR net 1.94, Avg Win +0.095 U, Avg Loss -0.091 U):
   - Chạy 10,000 lần mô phỏng Monte Carlo để xác định:
     - Max Drawdown kỳ vọng ở các mức Notional: 2%, 5%, 10%, 15%.
     - Tỷ lệ Sharpe Ratio và Tốc độ tăng trưởng vốn kỳ vọng (Expected Growth Rate).

3. **Đánh giá Tác động Thanh khoản & Trượt giá (Market Impact & Queue Latency)**:
   - Khi tăng size SOL từ 0.40 SOL ($46 U) lên ~4.35 SOL ($500 U):
   - Đánh giá sổ lệnh Binance Futures: Khối lượng ở top 1–3 ticks có đủ hấp thụ 4.35 SOL maker mà không bị tăng thời gian chờ khớp hoặc bị trượt giá khi thoát lệnh không?

4. **Thiết kế Cơ chế Dynamic Conviction Sizing (Định cỡ theo độ tin cậy)**:
   - Thay vì cố định 10% cho mọi lệnh:
     - Tín hiệu Trend bình thường (Score 80-90%): Size 3% - 5% ngân sách (~1.5 - 2.0 SOL).
     - Tín hiệu Super Conviction (Score >= 95%, OFI >= 0.85): Size 10% ngân sách (~4.35 SOL / 5,500 DOGE).
     - Giữ nguyên cơ chế bảo vệ vốn: Portfolio Risk Cap, Ratchet Tầng 2 (Lock Entry + Fee Shield), và EJECT-FAST (120s).

### YÊU CẦU KIỂM CHỨNG & BÁO CÁO:
- Viết công cụ mô phỏng tại `tools/simulate_position_sizing.py`.
- Xuất báo cáo nghiên cứu chi tiết tại `docs/research_upgrade/report_position_sizing_10pct_budget_2026-10-08y.md`.
- Toàn bộ test suite phải pass 100% (mục tiêu >= 1,260 tests passing).
- Tuyệt đối không ghi đè lịch sử ledger gốc.
</request>
```

---

## 3. Nguồn nghiên cứu & Cơ sở toán học

1. **Edward O. Thorp (2007)**: *The Kelly Criterion in Blackjack, Sports Betting, and the Stock Market* - Ứng dụng Fractional Kelly trong giao dịch tần suất cao.
2. **Ralph Vince (1992)**: *The Mathematics of Money Management: Risk Analysis Techniques for Traders* - Optimal F và kiểm soát Risk of Ruin.
3. **Robert Almgren & Neil Chriss (2000)**: *Optimal Execution of Portfolio Transactions* - Mô hình tác động thanh khoản và trượt giá khi mở rộng quy mô vị thế.
