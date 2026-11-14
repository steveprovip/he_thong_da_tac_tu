# FLS-1-CM + Polymarket benchmark — báo cáo 2026-11-14 (chỉ thị "recursive breakthrough")

Đăng ký trước: `docs/research_upgrade/plan_fls1_cm_history_2026_11.md` (SHA-256 LF `e62ac5a6…7c9e`, đóng băng
2026-11-13T17:20:58Z trước mọi kết quả). Dữ liệu ra: `data/fls1_cm_history.json`, `data/fls1_cm_sensitivity.json`,
`data/fls1_cm_boundary.json`, `data/fls1_cm_growth.json`, `data/pm15_benchmark.json`.

## 0. Kết luận
**Không có cấu hình nào cho net dương sau phí trên dữ liệu thanh lý thật.** FLS-1 (8 biến thể đăng ký trước): FAIL.
Vòng tìm kiếm 30 lượt: 0/31 cấu hình dương trên TRAIN. Điểm "biên" tốt nhất (cascade ≥ $150k, không E1) THẮNG
placebo +5.6 bps (LCB95 +0.37) nhưng vẫn **−3.8 bps/lệnh**. Tài khoản 5 U: trung vị 2.8-3.8 U sau 30 ngày.
Polymarket: mô hình giá trị hợp lý đã hiệu chỉnh tốt (lợi thế phải đến từ sổ lệnh chậm, không đo được trên lịch sử);
24 tín hiệu thật: −0.010 ± 0.092 USDC/share.

## 1. Dữ liệu (Giai đoạn 1)
- `data.binance.vision` **không có** `liquidationSnapshot` cho USD-M (danh sách `um/daily`: aggTrades, bookDepth,
  bookTicker, indexPriceKlines, klines, markPriceKlines, metrics, premiumIndexKlines, trades). Chỉ COIN-M có, từ
  2023-06-25 đến 2024-10-14. 1000PEPE không có hợp đồng COIN-M. => "30 ngày gần nhất USD-M" là không thể; đã dùng
  COIN-M làm **kiểm thử chuyển giao** (thị trường khác, năm khác).
- Tải về (`tools/fetch_cm_liquidation_tape.py`, SHA-256 từng file khớp `.CHECKSUM`):
  liquidationSnapshot SOL/DOGE/XRP/ETH/BTC 455/414/389/474/472 ngày (14 540 / 12 016 / 11 438 / 28 092 / 53 398 dòng
  sau khi bỏ dòng trùng lặp hoàn toàn); aggTrades các ngày có cascade (≥ $50k / 10 s): 157 / 93 / 20 / 342 / 428 ngày,
  đủ 24 file giờ mỗi ngày, 1.9 GB sau chuyển đổi. ETH 2024-04-18 không có trên kho.
- Thay thế đã khai báo trước: touch suy từ lệnh khớp (bid = lệnh bán chủ động gần nhất, ask = lệnh mua chủ động gần
  nhất), G1 dùng mất cân bằng dòng lệnh (TFI) ≥ 0.65 thay OFI L1, trễ phản ứng 250 ms, phí VIP0 2/5 bps + 1 bp trượt.

## 2. FLS-1, 8 biến thể (Giai đoạn 2) — 2 494 cascade ≥ $100k/5 s (BTC 1 683, ETH 627, SOL 148, DOGE 24, XRP 12)
TRAIN < 2024-04-01 (1 297 cascade), HOLDOUT 2024-04-01..10-14 (1 197).

| Biến thể | TRAIN n | TRAIN net bps | HOLDOUT n | HOLDOUT net bps | win HO | PF HO | vs placebo HO |
|---|---|---|---|---|---|---|---|
| k0.5 R1.5 G0 | 690 | −7.60 ± 0.92 | 640 | −10.22 ± 1.13 | 15.8 % | 0.30 | −1.36 |
| k0.5 R1.5 G1 | 342 | −9.82 ± 0.63 | 345 | −9.92 ± 0.80 | 10.7 % | 0.20 | −1.15 |
| **k0.5 R2 G0 (chọn)** | 689 | −6.83 ± 1.15 | 637 | **−10.83 ± 1.24** | 12.4 % | 0.30 | −1.62 (LCB −3.69) |
| k0.5 R2 G1 | 342 | −10.06 ± 0.68 | 345 | −9.44 ± 0.93 | 9.6 % | 0.25 | −0.34 |
| k1 R1.5 G0 | 442 | −8.02 ± 1.47 | 398 | −10.25 ± 1.21 | 18.3 % | 0.30 | −1.59 |
| k1 R1.5 G1 | 239 | −9.50 ± 1.07 | 243 | −10.86 ± 1.16 | 13.6 % | 0.18 | −2.04 |
| k1 R2 G0 | 442 | −8.06 ± 1.75 | 397 | −10.28 ± 1.31 | 15.9 % | 0.33 | −1.54 |
| k1 R2 G1 | 239 | −9.89 ± 1.18 | 243 | −11.18 ± 1.22 | 11.5 % | 0.19 | −2.71 |

Chấp nhận (HOLDOUT, biến thể chọn trên TRAIN): n ≥ 100 ✓, LCB95 > 0 ✗, thắng placebo ✗, ≥ nửa symbol dương ✗ ⇒ **FAIL**.
Hàng E0 (tham khảo, đúng mục 5 của FLS-1): −8.73 ± 1.70 bps, n 633, win 31.8 %. Markout sau khớp HOLDOUT (G0):
1 s −0.90, 5 s −1.14, 30 s −1.44 bps (bị chọn ngược). E1 thoát ~60 % số lệnh khớp.

## 3. Vòng tìm kiếm (Giai đoạn 3) — chỉ TRAIN, 30 lượt, seed 20261114
- Đường đi: −6.83 → −6.79 (TTL 73 s) → −6.77 → −6.08 (R 2.95) → −6.02 (time stop 23 min) → −4.78 (TTL 31 s) →
  −4.51 (TTL 22 s) → **−4.47** (time stop 20 min). Cấu hình cuối: $100k, 5 s, k 0.5, R 2.95, time stop 1 203 s,
  TTL 21.7 s, G0, E1. **0/31 cấu hình dương trên TRAIN.**
- HOLDOUT (1 lần đọc): **−8.38 ± 1.94 bps**, n 435, win 13.1 %, PF 0.47; vs placebo +0.84 (LCB −2.40);
  Deflated Sharpe (39 thử) ≈ 1·10⁻⁵.
- Bộ ngẫu nhiên không lần nào chọn `threshold` hay `e1` ⇒ quét bổ sung CHỈ TRAIN (11 cấu hình, tổng 50 thử):

| TRAIN | $50k | $75k | $100k | $150k | $250k |
|---|---|---|---|---|---|
| E1 | −6.59 ± 1.34 | −4.80 ± 1.80 | −4.47 ± 2.11 | −1.71 ± 3.12 | +1.40 ± 4.80 |
| E0 | −5.15 ± 1.68 | −2.61 ± 2.14 | −1.48 ± 2.79 | +1.88 ± 4.05 | +2.68 ± 6.44 |

  Cửa sổ 2 s / 10 s: −4.37 / −4.69. Vùng TRAIN > 0: ngưỡng ≥ $150k không E1 (không có ý nghĩa thống kê).
- **Kiểm "biên" trên HOLDOUT** (định trước: ≥ $150k, E0): **−3.79 ± 3.14 bps**, n 336, win 29.2 %, PF 0.82;
  **vs placebo +5.63 (LCB95 +0.37)**; DSR (50 thử) 0.0006. Theo symbol: BTC −6.15 (216), ETH −7.59 (98),
  SOL +42.2 (17), DOGE −36.6 (4), XRP +229 (1).
  ⇒ Cascade lớn có thông tin thời điểm thật (hồi giá sau thanh lý), nhưng không đủ trả phí + bị chọn ngược khi khớp maker.

## 4. Tăng trưởng 5 U (`tools/micro_growth_sim.py`, 2 000 đường, 0.05 U/lệnh, dừng phiên 2 thua, kill 0.15 U/ngày)
| Phân phối HOLDOUT | lệnh/ngày | mean R | 24 h | 7 ngày | 30 ngày | 196 ngày | P(≥ 15 U) |
|---|---|---|---|---|---|---|---|
| Tìm kiếm cuối | 2.22 | −0.67 | 4.90 | 4.45 | **2.77** | 0.97 (100 % < 1 U) | 0 |
| Biên ≥ $150k E0 | 1.71 | −0.47 | 4.88 | 4.69 | **3.79** | 0.96 (99.4 % < 1 U) | 0 |

## 5. Polymarket 15 phút (Giai đoạn 4) — `research/pm15_benchmark.py`
- Hiệu chỉnh `p_up` của module trên 1m Binance 22 tháng × BTC/ETH/SOL/XRP (766 041 điểm, kết quả = close phút 15 >
  open phút 1, proxy của TWAP Chainlink): Brier 0.127 vs 0.25 (skill 0.49). Lệch: dự báo 0.685 → thực 0.718,
  0.814 → 0.846, 0.184 → 0.157 (mô hình hơi thiếu tự tin ở giữa, quá tự tin ở hai đầu).
- Proxy xung lực 1 m (|move| ≥ 0.3 %): tỷ lệ trúng 67-76 % ≈ xác suất mô hình (SOL 10 m: 74.5 % vs 74.6 %): xung
  lực không thêm thông tin; ask hoà vốn sau phí 0.66-0.75.
- 24 tín hiệu thật đã tất toán (`data/pm15_signals.jsonl`, ask thật): 10 thắng, edge dự báo +0.083/share, thực tế
  **−0.010 ± 0.092/share** (tổng −0.25).
- "$0.10 → $1.00 = 10x": EV > 0 chỉ khi xác suất thật > 0.1063 (phí 0.07·a·(1−a)). 10x là payoff, không phải kỳ vọng.
- Đề xuất "dual-engine" (điều kiện, không vận hành): Polymarket chỉ có lợi khi ask < ngưỡng hoà vốn của p_up đã hiệu
  chỉnh, tức sổ lệnh chậm hơn Binance; cần ghi sổ Polymarket 1 s quanh xung lực Binance để đo; FLS-1 USD-M cần băng
  ghi trực tiếp (USD-M không có lịch sử). Cả hai chưa có bằng chứng dương.

## 6. Carry (đã có, không chạy lại)
XV2 (BN vs HL funding): HOLDOUT gốc +16.0 bps/ngày-28d (CI95 +10.9…+21.2, n 165) — lane duy nhất từng dương trên
holdout; tháng mới nhất n 1 (không kết luận), biến thể lỏng hơn −5.8. Trên 5 U: ≈ 0.008 U mỗi 28 ngày.

## 7. Tái lập
```
python -I tools/fetch_cm_liquidation_tape.py --symbols SOLUSD_PERP,DOGEUSD_PERP,XRPUSD_PERP,ETHUSD_PERP,BTCUSD_PERP --liq-only
python -I tools/fetch_cm_liquidation_tape.py --symbols <SYM> --no-book --days <YYYYMMDD,...>
python -m research.flash_liquidation_study --root data/recordings/historical/cm --symbols SOLUSD_PERP,DOGEUSD_PERP,XRPUSD_PERP,ETHUSD_PERP,BTCUSD_PERP --history-split 2024-04-01 --rounds 30 --out data/fls1_cm_history.json
python -m research.pm15_benchmark
```
