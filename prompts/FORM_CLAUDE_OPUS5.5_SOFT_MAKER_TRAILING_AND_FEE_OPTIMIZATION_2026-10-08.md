# Form Claude Opus 5.5 High: Tối ưu hóa Chốt lời Maker & Loại bỏ Thất thoát Phí sàn (Fee Drag Elimination)

Ngày lập: 08/10/2026. Workspace: `F:\he_thong_da_tac_tu_2`.

---

## 1. Mổ xẻ Chi tiết Ca Lệnh Thực tế #49 (SOL BUY 2.04 lúc 18:26)

Dữ liệu thực nghiệm từ ledger `data/engine_trades.jsonl`:
- **Vào lệnh**: BUY 2.04 SOL @ 113.47 (GTX Maker).
- **Giá chạy đỉnh (MFE)**: Lên tới 113.60 (+0.154% ROE).
- **Lãi gộp (Gross Profit)**: **`+0.1632 USDT`**.
- **Đóng lệnh**: Chạm Stop Ratchet tại 113.55 (STOP_MARKET trên sàn).
- **Phí sàn bị trừ**: **`-0.1390 USDT`** (Entry Maker 2 bps + Exit Taker 5 bps = 6.0 bps)!
- **Lãi ròng thực nhận (Net Profit)**: **`+0.0242 USDT`** (Phí sàn nuốt chửng 85% lợi nhuận thô!).
- **Hiện tượng trên UI**: Lúc lệnh đang chạy, người dùng thấy Balance đỉnh là `4922.9261` (lãi tạm tính +0.24 U), khi đóng lệnh bằng Stop Taker thì số dư thực tế về `4922.7374`, gây cảm giác "vừa lãi xong lại tụt".

---

## 2. Lệnh giao việc cho Claude Opus 5.5 High (Sao chép phần dưới)

```markdown
<request>
Bạn là Chuyên gia Định lượng Cao cấp (Principal Quantitative HFT & Execution Architect) phụ trách dự án GrokTOPUS tại F:\he_thong_da_tac_tu_2.
Vừa qua, hệ thống đã kích hoạt thành công Dynamic Conviction Sizing (Size 2.04 SOL ~ $231.5 U notional) và chuỗi 7/12 lệnh thắng (58.3% Winrate).
Tuy nhiên, xuất hiện NÚT THẮT THẤT THOÁT PHÍ NGHIÊM TRỌNG:
- Lệnh #49 lãi gộp +0.1632 U, nhưng lệnh đóng bằng Stop Market Taker đã bị sàn thu phí tới -0.1390 U (85% lãi gộp), khiến lãi ròng teo tóp chỉ còn +0.0242 U.

Hãy nghiên cứu, thiết kế và triển khai bản vá TỐI ƯU HÓA CHỐT LỜI MAKER & TRIỆT TIÊU THẤT THOÁT PHÍ (Soft Maker Trailing & Fee Drag Elimination) với các nhiệm vụ:

### 1. Cơ chế Chốt lời Chủ động bằng Maker (Soft Maker Trailing / Pre-Stop Maker Exit):
- Khi vị thế có lãi (MFE >= +0.12% - +0.20%) và đạt điểm kích hoạt Ratchet / Fee-Shield:
  - Thay vì chỉ để lệnh nằm chờ giá quay đầu chạm Stop Market Taker trên sàn, MARIN chủ động đặt lệnh **Maker Limit / Post-Only GTX** tại giá đỉnh (Best Ask / Bid) để chốt lời êm dịu (Soft Maker Exit).
  - So sánh phí: Thoát Maker chỉ tốn 2.0 bps (tiết kiệm ngay 60% chi phí so với Taker 5.0 bps). Cùng lệnh #49, nếu thoát Maker thì lãi ròng thu về sẽ là **+0.117 U (GẤP 5 LẦN hiện tại!)**.
  - Lệnh Stop Market trên sàn vẫn được giữ nguyên ở mức an toàn làm chốt chặn bảo vệ cuối cùng (Hard Safety Net).

2. **Chốt lời Từng phần Động (Dynamic Scaled TP1)**:
   - Thay vì để TP1 cố định quá xa (+0.88%), áp dụng TP1 đa tầng:
     - Chốt 50% khối lượng tại mốc **+0.25% - +0.35% (Maker)** ngay khi động lượng chậm lại.
     - 50% khối lượng còn lại thả runner bám theo Trailing Stop.

3. **Mở rộng Giới hạn Vị thế An toàn (Portfolio Risk Cap 5.0 U / Max Notional $1,000 U)**:
   - Nâng trần Notional lên $1,000 U (~8.5 - 10 SOL) trên Demo để khi kết hợp với Soft Maker Exit, mỗi lệnh chốt lời mang về **+$2.50 đến +$10.00 USDT ròng**.

### YÊU CẦU KIỂM CHỨNG & CHẤT LƯỢNG:
- Toàn bộ test suite phải pass 100% (mục tiêu >= 1,280 tests passing).
- Tuyệt đối không ghi đè lịch sử ledger `data/engine_trades.jsonl`.
- Viết báo cáo Before/After tại `docs/research_upgrade/report_fee_optimization_soft_maker_2026-10-08z.md`.
</request>
```
