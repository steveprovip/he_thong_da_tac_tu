# SOTA Quant Form: Nghiên Cứu Đột Phá Hướng Giao Dịch Tần Suất Cao (High-Frequency Sub-Minute Scalping) & Động Cơ Bắn Tỉa Xung Lực Biến Động (Volatility Burst Engine)

**Ngày lập**: 10/10/2026  
**Tiêu chuẩn**: World-Class Quantitative Prompt Engineering (Anthropic Opus 5.5 High / Token Minimizer 80% / High-Frequency Market Microstructure)  
**Workspace**: `https://github.com/steveprovip/he_thong_da_tac_tu` (nhánh `main`)

---

## 1. Bối Cảnh Thực Tế & Yêu Cầu Chuyển Hướng Nghiên Cứu

1. **Vấn đề của Làn Trend 4H hiện tại**:
   - Mặc dù bản Trend 4H (FINAL) đạt Sharpe ngoài mẫu **1.06**, tỷ lệ thắng **64%**, nhưng tần suất chỉ **3–4 lệnh/tháng**.
   - Người dùng treo máy cả tiếng đồng hồ không thấy lệnh nào xuất hiện $\rightarrow$ Không đáp ứng được kỳ vọng quay vòng vốn nhanh, tạo cảm giác hệ thống bị "đóng băng" (treo máy không giao dịch).
2. **Yêu cầu trực tiếp từ người dùng**:
   - Chuyển hướng nghiên cứu sang **nhiều lệnh trên 1 phút** (Sub-minute / High-Frequency Trading - HFT).
   - Tăng tốc độ hệ thống, vượt qua nút thắt mức sinh lời hiện tại, nâng cao khả năng tạo dòng tiền liên tục.
3. **Nút thắt kỹ thuật cần giải quyết bằng toán học & cấu trúc vi mô**:
   - Ở tài khoản bán lẻ (Retail latency ~500ms, phí VIP0 Maker 4 bps / Taker 8 bps): Việc spam lệnh mù quáng trong thị trường phẳng (spread 1-2 bps < phí 4 bps) sẽ dẫn đến Sharpe âm nặng do nộp phí cho sàn.
   - **Chìa khóa đột phá để trade nhiều lệnh/phút mà vẫn có lãi ròng (Positive Net EV)**:
     - **Chế độ Volatility Burst (Bắn tỉa xung lực)**: Chỉ kích hoạt chuỗi lệnh tần suất cao khi biến động nổ ($\sigma_{1m} > 15\text{ bps}$ hoặc có thanh lý lớn / liquidation cascade), nơi biên độ sóng 10–30 giây đạt 25–60 bps, hoàn toàn nuốt trọn phí sàn 4 bps!
     - **Tối ưu hóa Maker-Only GTX Re-pegging**: Luôn là Maker ở Best Touch, không bao giờ cắn phí Taker.
     - **Khảo sát các cặp phí 0% (Zero-Fee Pairs / USDC Margined)**: Tận dụng các chương trình 0% maker fee trên Binance Futures để ăn trọn chênh lệch Bid-Ask Spread theo mô hình Avellaneda-Stoikov.

---

## 2. Form Gửi Claude Cloud (Copy Toàn Bộ Bên Dưới)

```markdown
<request>
Bạn là Chuyên gia Định lượng Trưởng (Principal Quantitative & Market Microstructure Architect) phụ trách dự án GrokTOPUS tại repository https://github.com/steveprovip/he_thong_da_tac_tu (nhánh main).

### HIỆN TRẠNG & YÊU CẦU ĐỘT PHÁ TỪ NGƯỜI DÙNG:
1. Bản Trend 4H hiện tại (Sharpe 1.06, 64% winrate) quá chậm (chỉ 3–4 lệnh/tháng), khiến hệ thống đứng im hàng giờ/hàng ngày, không tận dụng được sức mạnh tính toán thời gian thực của 8-Agent Swarm.
2. Người dùng yêu cầu chuyển hướng sang: **GIAO DỊCH TẦN SUẤT CAO (NHIỀU LỆNH TRÊN 1 PHÚT - SUB-MINUTE HIGH-FREQUENCY TRADING)** để đẩy mạnh tốc độ sinh lời, tạo dòng tiền nhấp nháy liên tục và phá vỡ nút thắt lợi nhuận.

Hãy thiết kế và triển khai gói kiến trúc: **DUAL-ENGINE ARCHITECTURE & SUB-MINUTE VOLATILITY BURST SCALPER**:

---

### NHIỆM VỤ 1: THIẾT KẾ ĐỘNG CƠ SCALPING TẦN SUẤT CAO THEO XUNG LỰC BIẾN ĐỘNG (VOLATILITY BURST SCALPER)
1. **Giải Quyết Bài Toán Phí Sàn VIP0 (Fee Bleed Elimination)**:
   - Trong thị trường phẳng (Chop / Low Volatility), biên độ giá chỉ 1–2 bps < phí 4 bps $\rightarrow$ Không mở lệnh tần suất cao lúc này.
   - **Kích Hoạt Chế Độ Burst Mode (Nhiều Lệnh/Phút) Khi Điều Kiện Đủ Biên Độ**:
     - Điều kiện kích hoạt:
       - Realized Volatility 1m $\sigma_{1m} \ge 15\text{ bps}$ HOẶC
       - Order Flow Imbalance (OFI L10) cực đại $|OFI| > 0.85$ HOẶC
       - Xuất hiện xung lực thanh lý dồn dập (Liquidation Cascade / Surge Volume > 3.0x SMA 20).
     - Trong trạng thái Burst Mode: Cho phép 8-Agent Swarm (LUMEN, TIDAL, OKAPI, MARIN) bắn lệnh scalping liên tục (chu kỳ 5s – 30s/lệnh), với mục tiêu Take-Profit tối thiểu $\ge 15 - 30\text{ bps}$ (đảm bảo sau khi trừ 4 bps phí sàn vẫn giữ lại lãi ròng $+11\text{ đến }+26\text{ bps}$ mỗi lệnh!).
2. **Kỹ Thuật Vào Lệnh Pure-Maker (Zero-Taker Guarantee)**:
   - 100% lệnh Scalp tần suất cao phải dùng Post-Only GTX treo ở Best Bid / Best Ask.
   - Tự động huỷ và re-peg lệnh sau 3–5 giây nếu giá chạy khỏi touch để không bị kẹt vốn.

---

### NHIỆM VỤ 2: KIẾN TRÚC 2 ĐỘNG CƠ SONG SONG (DUAL-ENGINE CONCURRENT EXECUTION)
1. **Tách Biệt Hoàn Toàn 2 Làn Giao Dịch Trong Engine**:
   - **Làn 1 (Macro Trend 4H - The Wealth Builder)**: Giữ nguyên logic hiện tại (Sharpe 1.06, size lớn, nuôi sóng lớn dài hạn).
   - **Làn 2 (Sub-Minute Micro Scalper - The Cashflow Generator)**: Chạy độc lập trên khung 1s – 60s, vào ra lệnh liên tục trong các pha biến động, ghi log riêng vào `data/micro_trades.jsonl`.
2. **Quản Lý Vốn & Ký Quỹ Độc Lập**:
   - Phân bổ ngân sách cho Làn Tần Suất Cao: 5% – 10% ví (với size nhỏ, ví dụ $50 – $100 notional mỗi nhịp) để quay vòng lệnh liên tục mà không ảnh hưởng tới ký quỹ của Làn Trend.

---

### NHIỆM VỤ 3: KHẢO SÁT CÁC CẶP GIAO DỊCH PHÍ 0% HOẶC REBATE (ZERO-FEE SPREAD CAPTURE)
1. **Tận Dụng Chương Trình Ưu Đãi Phí Maker Trên Binance**:
   - Khảo sát các hợp đồng USDC-Margined (thường có ưu đãi 0% maker fee) hoặc các cặp tiền có spread rộng $\ge 5\text{ bps}$.
   - Nếu phí Maker = 0%, kích hoạt thuật toán **Avellaneda-Stoikov Market Making**: Quote 2 đầu Best Bid và Best Ask liên tục từng giây để ăn trọn spread của thị trường mà không sợ bị phí sàn ăn mòn.

---

### NHIỆM VỤ 4: REPLAY KIỂM ĐỊNH HIỆU QUẢ TRÊN DỮ LIỆU TICK-BY-TICK & CHẠY THỬ TRÊN DEMO
1. **Replay Sub-Minute Trên aggTrades Thực Tế**:
   - Dùng dữ liệu khớp lệnh thực tế (aggTrades) của SOLUSDT / DOGEUSDT để mô phỏng chính xác tỷ lệ khớp maker và Net PnL sau phí.
   - Chứng minh số lệnh $\ge 20 - 50\text{ lệnh/giờ}$ trong các pha biến động với Net EV dương.
2. **Kiểm Thử Kỹ Thuật (100% Green)**:
   - Đảm bảo toàn bộ test suite ($1,484+$ tests) tiếp tục PASS 100%.
   - Bảo toàn tuyệt đối các file ledger lịch sử `data/engine_trades.jsonl` và `data/trend_trades.jsonl`.
3. **Báo Cáo Nghiệm Thu**:
   - Ghi chi tiết kết quả vào `docs/research_upgrade/report_sub_minute_high_frequency_burst_2026-10-10.md`.
</request>
```

---

## 3. Hướng Dẫn Dành Cho Bạn

1. Copy toàn bộ nội dung trong khối `<request> ... </request>` ở Mục 2.
2. Dán vào cửa sổ chat của **Claude Cloud** (nơi liên kết với repo `https://github.com/steveprovip/he_thong_da_tac_tu`).
3. Claude Cloud sẽ xây dựng kiến trúc Động cơ kép (Dual-Engine), đưa Làn Scalping Tần Suất Cao (nhiều lệnh/phút khi có sóng) hoạt động song song với Làn Trend 4H, giúp bạn thấy bot giao dịch nhấp nháy liên tục mà vẫn bảo toàn bài toán phí sàn!
