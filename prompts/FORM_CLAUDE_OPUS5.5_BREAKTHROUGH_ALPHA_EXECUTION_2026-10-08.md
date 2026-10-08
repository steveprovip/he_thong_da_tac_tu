# Form Claude Opus 5.5 High: Đột phá khớp lệnh Alpha & Tối đa hoá lợi nhuận ròng

Ngày lập: 08/10/2026. Workspace: `F:\he_thong_da_tac_tu_2`.

---

## 1. Bối cảnh thực nghiệm & Bằng chứng từ Terminal (14:40 - 14:53 Asia/Saigon)

Từ log terminal thực tế của phiên giao dịch:
1. **Swarm bắt được tín hiệu Alpha siêu mạnh (98% Conviction)**:
   - `[14:42:35] SOL - ALPHA CONCENTRATION SOL vote 98.5% · OFI -0.89 -> size 0.40 -> 0.80 SOL`
   - `[14:50:55] SOL - ALPHA CONCENTRATION SOL vote 98.1% · OFI -0.87 -> size 0.40 -> 0.80 SOL`
2. **Nút thắt chí tử: Lệnh Maker GTX bị huỷ sau 3 giây (Starvation)**:
   - `[14:42:38] SOL - GTX SELL 0.80 @ 115.52 not filled in 3s -> cancelled`
   - `[14:50:58] SOL - GTX SELL 0.80 @ 115.14 not filled in 3s -> cancelled`
   - Trên sàn Binance Demo, không có Market Takers đối ứng chủ động cắn lệnh chờ. Lệnh Maker GTX đặt ở giá touch 3s bị huỷ khiến bot bỏ lỡ hoàn toàn con sóng rơi 50–100 bps của SOL.
3. **Hệ quả**: Số dư tài khoản bị "đóng băng" ở `4922.6314`, treo cả ngày không sinh lời đáng kể vì cơ chế đặt lệnh quá cứng nhắc.
4. **Lỗi mạng sàn Demo**: `Binance code=-1008, HTTP=503` lúc 14:44:12 cần cơ chế retry/reconcile tự động, không làm gián đoạn vòng lặp quét.

---

## 2. Lệnh giao việc cho Claude Opus 5.5 High (Sao chép phần dưới)

```markdown
<request>
Bạn là Chuyên gia Định lượng Cao cấp (Principal Quantitative HFT Architect) phụ trách dự án GrokTOPUS tại F:\he_thong_da_tac_tu_2. 
Hệ thống hiện tại đang gặp điểm nghẽn nghiêm trọng: Swarm phát hiện cơ hội Alpha cực mạnh (vote > 98%, OFI > 0.85, Edge > 35 bps), nhưng 100% lệnh GTX bị hủy sau 3 giây vì thiếu taker đối ứng trên sàn Demo, khiến bot treo cả ngày không sinh lời.

Hãy nghiên cứu, thiết kế và triển khai bản vá ĐỘT PHÁ KHỚP LỆNH & TỐI ĐA LỢI NHUẬN RÒNG (Breakthrough Execution & Alpha Capture) đáp ứng các tiêu chuẩn sau:

### NHIỆM VỤ CỐT LÕI:

1. **Cơ chế Super-Alpha Crossing / Fallback (Chụp sóng dứt khoát)**:
   - Dựa trên nghiên cứu của Cont & Kukanov (2014) và Biais-Foucault-Salanié (2014) về "Alpha-to-Cost Ratio": Khi tín hiệu đạt độ tin cậy tuyệt đối (Swarm Vote >= 95%, |OFI| >= 0.85, Expected Edge >= 30 bps), lợi nhuận kỳ vọng (+50 đến +80 bps) vượt xa phí Taker (2.0 - 4.0 bps).
   - Thiết kế cơ chế: Nếu GTX không khớp sau 2s hoặc trong chế độ Super-Conviction, cho phép lệnh Crossing Limit / IOC Taker để khớp ngay 100% khối lượng, không để lỡ sóng Alpha.
   - Thêm cổng kiểm soát: Chỉ kích hoạt cho SOL và DOGE, giới hạn trần trượt giá (Slippage Cap <= 3.0 bps), và yêu cầu Net R:R sau phí Taker vẫn >= 1.50.

2. **TTL Động theo Tuổi thọ Tín hiệu (Dynamic Signal-Decay TTL)**:
   - Thay vì huỷ cứng sau 3.0 giây, mở rộng TTL lên 10–20 giây nếu chỉ số OFI và Swarm Vote vẫn duy trì >= 90% cùng hướng và giá chưa chạy quá 0.05%.
   - Nếu giá bắt đầu đi ngược (Adverse Selection), lập tức huỷ lệnh bảo toàn vốn.

3. **Cải tiến Quản lý Lợi nhuận Ròng (Dynamic Profit Scaling)**:
   - Tối ưu hoá TP1 và Trailing Ratchet cho các lệnh Super Size (0.80 SOL / 500 DOGE): Khi giá chạy đúng hướng được +0.25%, lập tức dời Stop Loss lên trên giá vào + Phí sàn (Lock Entry + 0.05% Fee Shield) để đảm bảo 100% lệnh đóng đều có lãi ròng dương.

4. **Khắc phục triệt để lỗi mạng sàn Demo (HTTP 503 / Binance Code -1008)**:
   - Tăng cường khả năng chịu lỗi của VESKA: Tự động retry với exponential backoff khi gặp HTTP 503/502, mượt mà reconcile vị thế mà không crash loop.

### YÊU CẦU KIỂM CHỨNG & CHẤT LƯỢNG:
- Toàn bộ test suite phải pass 100% (mục tiêu >= 1,200 tests passing).
- Tuyệt đối không sửa/ghi đè lịch sử ledger `data/engine_trades.jsonl`.
- Giữ nguyên kiến trúc 2-chế độ nghiêm ngặt (Demo vs Live).
- Viết báo cáo Before/After chi tiết tại `docs/research_upgrade/report_breakthrough_alpha_execution_2026-10-08w.md`.
</request>
```

---

## 3. Nguồn nghiên cứu & Cơ sở toán học

1. **Rama Cont & Arseniy Kukanov (2014)**: *Optimal Order Placement in Limit Order Markets* (arXiv:1210.1625) - Tối ưu hoá xác suất khớp lệnh và cân bằng giữa phí Maker/Taker với chi phí bỏ lỡ cơ hội (Opportunity Cost).
2. **Bruno Biais, Thierry Foucault, Pierre Salanié (2014)**: *Equilibrium Fast Trading* (Journal of Financial Economics) - Giá trị của tốc độ và việc chủ động khớp thanh khoản khi tín hiệu Alpha vượt trội phí giao dịch.
3. **Charles-Albert Lehalle & Othmane Mounjid (2018)**: *Limit Order Strategic Placement with Adverse Selection Risk* (arXiv:1610.00261).
4. **hftbacktest Execution Model**: https://hftbacktest.readthedocs.io/en/latest/order_fill.html - Mô phỏng hàng đợi, TTL và crossing orders.
