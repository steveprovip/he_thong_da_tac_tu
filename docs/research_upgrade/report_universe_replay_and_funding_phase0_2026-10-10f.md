# Báo cáo 10/10/2026 (f): cổng R:R ≥ 2, replay P15 trên universe, Funding Arb Pha 0

**Phạm vi.** Ba quyết định của anh/chị sau báo cáo (e). Replay offline trên dữ liệu công khai: kline 1m data.binance.vision (504 file tháng, 818 MB, SHA256 khớp `.CHECKSUM` của sàn); funding + nến 4h của Binance và Hyperliquid. Không khởi động lại engine, không gửi lệnh, không đổi Profile/verdict, không đụng ledger.

## 0. Kết luận

| Việc | Kết quả |
|---|---|
| 1. Cổng R:R ≥ 2 | Đã áp dụng: biến thể bracket cố định có R:R kế hoạch ≥ 2 → **n ≥ 100 và cận dưới CI95 (bootstrap theo ngày) > 0**, bỏ điều kiện win > 50 %. Các biến thể khác giữ nguyên. |
| 2. P15_rr25atr trên universe top 20 | **Không có edge, nay âm có ý nghĩa.** HOLD-OUT 52 mã: n 11 673 (42.8 lệnh/ngày), net **−6.1 bps [−10.9, −1.2]**, gross +1.1. Lõi 12 mã: −6.6 [−12.4, −1.0]. 0/28 mã đủ mẫu có cận dưới CI > 0. 208/208 verdict = SHADOW. |
| 3. Funding Pha 0 | **Spot/perp Binance: âm** (phí spot 10 bps). **Perp/perp Binance↔Hyperliquid: qua cổng** — DEV-best `HEDGE\|H28\|k2`, HOLD-OUT n 153, net **+14.6 bps [+9.2, +19.9]** mỗi vòng 28 ngày. Nhưng chỉ ≈ **1.9 %/năm trên notional**: có ý nghĩa thống kê, nhỏ về kinh tế. |
| Test | 1 540 → **1 552 passed** (thêm 12). |

## 1. Cổng đánh giá (`tools/intraday_replay.py`)
- `planned_rr(var)` = TP/SL của bracket cố định (`tp_bps/sl_bps` hoặc `tp_atr/sl_atr`); `None` cho TP1/trailing/band.
- `eligible(h, var)`: n ≥ 100 và `ci95[0] > 0`; thêm win > 50 % **chỉ khi** R:R < 2 hoặc không xác định (burst, S1/S2/R1, P15_tp35/50).
- `tools/venue_fee_replay.py` dùng cùng hàm. Chuỗi `_rule` của `config/intraday_verdict.json` cập nhật khi chạy lại với `--write-verdict` (lần này **không** ghi verdict).

## 2. Replay P15_rr25atr trên universe point-in-time

**Universe** (`tools/universe_months.py`): `core.universe.screen` tại 00:00 UTC ngày 1 mỗi tháng 2025-01 → 2026-09, top 20 theo trung vị volume 14 ngày, trên nến ngày của 525 USDT perp đang niêm yết. Hợp 52 mã; 12 mã có mặt ≥ 19/21 tháng. Mỗi lệnh chỉ được tính nếu tháng tín hiệu có mã đó trong universe (`--universe`).

**Kiểm tra dữ liệu:** chạy BTC/SOL/DOGE không lọc universe → P15_rr25atr DEV n 2 182 / −3.6, HO n 1 193 / −5.5 [−12.0, +2.2], **khớp đúng** báo cáo (d).

| P15 HOLD-OUT 2026-01..09 | n (lệnh/ngày) | thắng | net bps [CI95 ngày] | gross | Sharpe |
|---|---|---|---|---|---|
| 3 mã (báo cáo d) | 1 193 (4.4) | 34 % | −5.5 [−12.0, +2.2] | +1.7 | −1.8 |
| Lõi 12 mã | 5 648 (20.7) | 34 % | −6.6 [−12.4, −1.0] | +0.6 | −2.7 |
| **Universe 52 mã** | **11 673 (42.8)** | 34 % | **−6.1 [−10.9, −1.2]** | +1.1 | −3.0 |
| P15_rr25 (52 mã, DEV-best nhóm P) | 15 481 (56.7) | 29 % | −8.1 [−9.9, −6.4] | −1.2 | −10.9 |

- Mở rộng universe làm **tăng tần suất gấp ~10 lần**, đúng như mục tiêu "giao dịch nhiều". Nhưng nó **thu hẹp CI quanh một giá trị âm**: −6.1 bps × 42.8 lệnh × $150 ≈ **−$3.9/ngày** nếu active.
- Mọi biến thể khác (S1, S2, R1, P5, P15_tp35/50) trên 52 mã đều âm với CI < 0.
- Per-symbol: 28 mã có n ≥ 100 trên HO, **0** mã có cận dưới CI > 0; 4 mã có trung bình > 0 (INJ +19.6 n 115, PUMP +6.5…), mọi CI chứa 0. Chọn mã theo HO là data-mining nên không dùng.
- **Hạn chế:** survivorship — mã đã delist không có trong exchangeInfo. Top 20 theo volume chủ yếu là coin lớn, và kết quả đã âm, nên sai lệch này không đổi kết luận.

**Kết luận làn 15m:** tín hiệu pull-back P15 không có edge trên Binance ở 1 coin, 3 coin hay 52 coin. Giữ SHADOW; **không** nối `core/universe.py` vào engine.

## 3. Funding Arb Pha 0 (`tools/funding_spread_replay.py`)

Quy tắc đăng ký trước, ghi trong docstring trước lần chạy đầu:
- Cặp **XV**: short perp có funding trailing 72h cao hơn, long perp sàn kia. Cặp **SP**: long spot + short perp Binance, chỉ khi funding > 0.
- Vào lệnh khi carry kỳ vọng = S/3 ngày × H ≥ k × chi phí vòng. H ∈ {7, 14, 28} ngày, k ∈ {1, 2}.
- P&L = funding nhận − funding trả + basis (giá mở nến 4h tại T và T+H, mỗi chân theo sàn của nó) − phí.
- **HEDGE** (chính): mỗi lần mở/đóng có một chân maker, một chân taker + 1 bp. **MAKER**: cả 4 lệnh maker (cận trên).
- 18 coin, DEV 2025 (exit trước 2026), HOLD-OUT 2026-01..09. Thiếu dữ liệu = không vào lệnh.

| DEV-best (HEDGE) | HOLD-OUT n | net bps/vòng [CI ngày] [CI tháng] | thắng | funding | basis (sd) | chi phí | APR/notional |
|---|---|---|---|---|---|---|---|
| **XV H28 k2** | 153 (18 coin) | **+14.6 [+9.2, +19.9] [+9.7, +20.4]** | 71 % | +29.3 | +0.3 (7) | 15.0 | **+1.9 %** |
| SP H28 k2 | 87 | −2.7 [−11.5, +4.4] | 55 % | +22.7 | +0.6 (6) | 26.0 | −0.3 % |

**Kiểm tra độ bền của XV** (HOLD-OUT, cùng tập lệnh):
- 9/9 tháng dương (+3.4 đến +46.3); bỏ bất kỳ 1 coin nào vẫn ≥ +12.4.
- Stress taker cả 4 chân + trượt 3 bps (chi phí 25 bps): **+7.5 [+1.8, +13.0]**. Hòa vốn khi chi phí vòng ≈ 29.6 bps.
- Nguồn edge: 79 % lệnh là short Hyperliquid / long Binance. Funding HL cao hơn Binance một cách cấu trúc (khớp mục 3.2 báo cáo e). Edge nằm ở altcoin: TAO +50.9, WLD +50.1, ZEC +44.6, PEPE +34.8, AAVE +34.7. Coin lớn ≈ 0: BTC −2.3, BNB −8.2, ETH +5.1.
- Tất cả 6 biến thể HEDGE XV có H ≥ 14 đều dương trên HO, nên kết quả không phụ thuộc vào một tham số may mắn.

**Kinh tế — đây là điểm quyết định:**
- +14.6 bps / 28 ngày ≈ **1.9 %/năm trên notional**. Vốn cần cho $1 notional: 2/L (margin hai sàn). L = 2 → 1.9 %/năm trên vốn; L = 3 → ~2.9 %. Thấp hơn lãi USDC/T-bill không rủi ro.
- Ví demo $5 000 ở L = 2 → ~$8/tháng. Ví thật 3.85 USDT: `two_leg_capital_gate` chặn (Binance 5 + Hyperliquid 10 USD tối thiểu mỗi chân).
- **Rủi ro chưa mô hình hóa:**
  - Thanh lý một chân. Giữ 28 ngày; TAO/WLD/ZEC có thể đi ±50 %. Ở L = 2, chân short bị thanh lý trước khi kịp chuyển USDC qua bridge (vài giờ).
  - ADL, rủi ro L1/bridge của Hyperliquid, Binance đổi chu kỳ funding.
  - Độ sâu sổ HL của alt nhỏ hơn nhiều, nên trượt giá thật có thể > 3 bps.
  - Dự thảo cấm sàn nước ngoài tại VN.
- Giá 4h là mark tham chiếu, không phải fill thật.

**Verdict (cổng đã duyệt):** XV `HEDGE|H28|k2` = **ELIGIBLE cho Pha 1–2** (interface chỉ đọc + shadow). Không phải cho tiền thật. SP = RESEARCH_ONLY (phí spot VIP0 làm âm; trùng với `carry_runtime` hiện có, nên giữ nguyên ở shadow).

## 4. Khuyến nghị
1. Làn 15m: dừng hướng P15. Hơn 11 000 lệnh hold-out âm có ý nghĩa; mở rộng universe không cứu được.
2. Funding XV: nếu muốn tiếp tục, bước rẻ nhất là **shadow forward 2–3 tháng**, chỉ đọc public API, không khóa. Ghi tín hiệu + funding thực nhận lý thuyết theo đúng quy tắc H28 k2. Mục đích là đối chiếu với replay trước khi viết `CarryVenue`. Nếu sau shadow mà vẫn muốn chạy thật, cần vốn ≥ vài trăm USD ở mỗi sàn, L ≤ 2, và chỉ coin lớn có độ sâu HL đủ (BTC/ETH/SOL lại gần như không có edge — đó là mâu thuẫn chính).
3. Giữ nguyên mọi chế độ làn hiện tại.

## 5. Thay đổi, test, rollback
- Code:
  - `tools/intraday_replay.py`: `planned_rr`, `eligible`, `in_universe`, `--universe`, `--workers`.
  - `tools/venue_fee_replay.py`: dùng `ir.eligible`; bỏ `MIN_N` không còn dùng.
  - Mới: `tools/universe_months.py`, `tools/fetch_klines_1m.py`, `tools/funding_spread_replay.py`.
- Test mới: `tests/test_verdict_gate.py` (4), `tests/test_funding_spread_replay.py` (8). Bộ đầy đủ: xem `docs/harness/progress.md` (10-10 f).
- Dữ liệu (gitignored):
  - `data/klines_1m/` (504 zip + .sha256)
  - `data/funding_cache/` (90 JSON)
  - `data/universe_daily.json`, `data/universe_months.json`
- Snapshot `snapshot_2026-10-10f/`: universe theo tháng, JSON/TXT replay, `klines_1m_SHA256SUMS.txt`, SHA256SUMS.
- Rollback: `git revert`. Không có module runtime nào import các tool mới.
