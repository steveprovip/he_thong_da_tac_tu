# Báo cáo nghiệm thu (ff) 09/10/2026: đồng thuận BTC, pyramiding, conviction sizing và chống lỗi mạng cho làn trend 4H

**Phạm vi.**
- Code, test và replay offline. Không khởi động lại engine, không gửi lệnh nào.
- Không đụng tới `data/engine_trades.jsonl` hay `data/trend_trades.jsonl`. Hai file này chỉ có trên máy bạn; executor chỉ append và thêm 2 trường mới `conv`, `be` vào mỗi dòng.
- Thay đổi có hiệu lực khi bạn tự restart `run.bat demo`. **Live không đổi.** Code vẫn từ chối chạy làn trend ngoài demo.

## 0. Tóm tắt

| | B0 = G6 + L1 + size 10 % (đang chạy) | **FINAL** (demo mới) |
|---|---|---|
| Thành phần | — | B0 + dời stop về hoà vốn ở +2 % (P1) + size theo độ đồng thuận 1H/4H/1D (C3) |
| Sharpe ngoài mẫu 2025-01 → 2026-09 | +0.83 | **+1.06** |
| Sharpe DEV 2022–24 | +1.27 | +1.45 |
| %/năm ngoài mẫu | +4.9 % | **+8.9 %** |
| Max DD ngoài mẫu | 5.9 % | 7.5 % |
| Sharpe riêng 2025 / 2026 | +0.23 / +1.35 | +0.54 / +1.52 |
| DSR (159 lần thử) | 0.29 | 0.41 |
| Lệnh ngoài mẫu: thắng, trung vị / TB bps | 47 %, −30 / +169 | **64 %, +18** / +155 |
| Size trung bình / tối đa | 7.5 % / 10 % ví | 12.3 % / 30 % ví |

**Kết luận thẳng:**
- **"Lợi nhuận ×5–10, lên 25–40 %/năm" không đạt bằng alpha.**
  - Gói này nâng từ 4.9 % lên 8.9 %/năm (×1.8), phần lớn nhờ size lớn hơn khi 3 khung đồng thuận.
  - Sharpe ngoài mẫu lần đầu vượt 1.0 (1.06).
  - Muốn 25–40 %/năm chỉ còn cách **nhân đòn bẩy**, xem mục 2c: ×3 cho 27 %/năm với DD 22 %; ×5 cho 44 %/năm với DD 37 %.
- **Chưa phân biệt được với may mắn.** Chênh lợi nhuận ngày so với B0 là +1.08 bps/ngày, khoảng tin cậy 95 % [−0.35, +2.70]. DSR 0.41, còn xa ngưỡng 0.95 cho tiền thật.
- **BTC shield (Nhiệm vụ 1) làm kết quả tệ hơn ở cả 4 biến thể** → chỉ chạy chế độ `shadow` (ghi log, không chặn).
- **Phần "vào thêm 0.5x" của pyramiding thua quy tắc** → không bật. Chỉ dùng bước 1 (dời stop về hoà vốn).

## 1. Kiểm tra lại tiền đề của form

| Tiền đề | Kiểm tra | Kết luận |
|---|---|---|
| "BTC BUY ăn +2.68 %" | Giá vào 82,470.8 → 82,915.3 = **+0.54 %**; $297 × 0.54 % = $1.60 | +2.68 % là **ROE ở đòn bẩy 5x**, không phải giá chạy. Ngưỡng +2 % (lock, break-even) tính theo giá, nên lệnh này còn xa ngưỡng |
| "SOL +0.52 %", "DOGE −3.20 %" | SOL 109.61 → 109.70 = +0.08 % giá; DOGE 0.08407 → 0.08461 = −0.64 % cho lệnh short | Cũng là ROE ×5. Tổng uPnL lúc 22:38 khoảng −0.12 U |
| Size $297 / $203 / $300 | Size 10 % (vòng ee) với ví $5 000 sẽ ra khoảng $500 / $300 / $280 | Ba lệnh này khớp với kẹp cũ $150–300 của vòng (dd). **Có thể chúng được mở trước khi restart với code (ee).** Không kiểm tra được vì `data/trend_active.json` không có trong repo |
| "DOGE SELL ngược BTC kéo lợi nhuận về 0" | Ba vị thế mở trong một đêm | Đúng về kế toán đêm đó, nhưng **chưa phải bằng chứng thống kê**. Replay 21 tháng ở mục 2a cho kết quả ngược lại |
| "Lỗi mạng làm lỡ nến 4H" | `_ssl.c:990` khi gọi `fapi.binance.com` với timeout 5 s, không retry | Đúng. Đã sửa ở mục 4 |

## 2. Replay (`python tools/trend_regime_replay.py --stage ff --data <kline 1h + funding>`)

**Thiết lập:**
- Thời gian chạy khoảng 3.5 phút. Dữ liệu và mô hình giống vòng (ee): nến 4H dựng từ kline 1h 2022-01 → 2026-09, phí taker 6 bps/phía, có funding, stop thảm hoạ 8 ATR kiểm tra trong nến.
- Thêm nến 1D (dựng từ 1h) và 1h gốc để tính độ đồng thuận 3 khung.
- Biến thể và quy tắc chọn được ghi trong docstring `FF_DOC` **trước** lần chạy đầu tiên.
- FINAL ghép các thành phần được quy tắc chấp nhận. Nó được thêm sau lần chạy đầu vì FULL/FULL3 chứa thành phần bị loại; tính thêm 1 lần thử.
- DSR dùng phương sai Sharpe của 148 lần thử trước (snapshot ee) cộng các lần thử vòng này, tổng 159.

| Biến thể | SR DEV | SR OOS | %/năm OOS | DD OOS | SR 2025 / 2026 | DSR | Lệnh | Thắng | Trung vị / TB bps | P&L lệnh alt, % ví | Chênh so với B0 (bps/ngày, CI95) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| B0 | +1.27 | +0.83 | +4.9 | 5.9 % | +0.23 / +1.35 | 0.29 | 72 | 47 % | −30 / +169 | +8.1 | — |
| A1 shield theo dấu BTC | +1.28 | +0.62 | +3.6 | 6.7 % | −0.34 / +1.35 | 0.20 | 72 | 44 % | −120 / +105 | +5.8 | −0.37 [−0.87, +0.10] |
| A2 shield chỉ khi BTC TREND | +1.27 | +0.72 | +4.3 | 6.3 % | −0.11 / +1.41 | 0.24 | 74 | 46 % | −66 / +127 | +7.0 | −0.18 [−0.50, +0.13] |
| A1E = A1 + đóng sớm | +1.24 | +0.59 | +3.4 | 7.2 % | −0.45 / +1.40 | 0.19 | 74 | 43 % | −124 / +89 | +5.5 | −0.41 [−1.02, +0.15] |
| A2E = A2 + đóng sớm | +1.20 | +0.78 | +4.7 | 6.0 % | +0.01 / +1.45 | 0.27 | 77 | 47 % | −38 / +136 | +7.7 | −0.05 [−0.48, +0.36] |
| **P1** dời stop về hoà vốn | +1.33 | +0.91 | +4.2 | 5.0 % | +0.37 / +1.39 | 0.33 | 70 | **64 %** | **+18** / +155 | +6.2 | −0.19 [−1.01, +0.51] |
| P2 = P1 + vào thêm 0.5x | +1.25 | +0.84 | +5.3 | 6.2 % | +0.21 / +1.37 | 0.29 | 70 | 34 % | −110 / +186 | +7.5 | +0.11 [−0.69, +0.96] |
| C2 đồng thuận → 20 % / 2 % | +1.34 | +0.87 | +8.3 | 7.7 % | +0.32 / +1.36 | 0.31 | 72 | 47 % | −30 / +169 | +11.7 | +0.91 [−0.49, +2.55] |
| **C3** đồng thuận → 30 % / 2 % | +1.41 | +0.87 | +8.6 | 8.5 % | +0.27 / +1.39 | 0.31 | 72 | 47 % | −30 / +169 | +11.9 | +1.00 [−0.62, +2.91] |
| FULL = A1 + P2 + C2 | +1.31 | +0.85 | +9.5 | 9.4 % | +0.16 / +1.44 | 0.30 | 70 | 33 % | −113 / +151 | +10.8 | +1.26 [−1.07, +4.03] |
| FULL3 = A1 + P2 + C3 | +1.39 | +0.87 | +10.2 | 10.2 % | +0.12 / +1.50 | 0.31 | 70 | 33 % | −113 / +151 | +11.0 | +1.46 [−1.07, +4.52] |
| **FINAL = P1 + C3** | **+1.45** | **+1.06** | **+8.9** | 7.5 % | +0.54 / +1.52 | **0.41** | 70 | 64 % | +18 / +155 | +10.7 | +1.08 [−0.35, +2.70] |

### 2a. Nhiệm vụ 1: BTC shield thua

- **Quy tắc:** chọn shield có Sharpe DEV cao nhất là A1. A1 có Sharpe ngoài mẫu 0.62, thấp hơn B0 (0.83), nên **bị loại**. Ba biến thể còn lại cũng đều kém B0 ngoài mẫu.
- **Lý do: các lệnh alt bị chặn thực ra có lãi.** Tổng P&L của lệnh SOL/DOGE ngoài mẫu giảm từ +8.1 % ví (B0) xuống +5.8 % (A1). Phần bị chặn đóng góp khoảng +2.3 % ví.
  - Lý do có thể: tín hiệu alt đã gồm TSMOM 5/20/60 ngày, Donchian và cổng G6 (TREND + volume + CVD). Khi một alt vẫn qua được cổng đó dù BTC đi ngược chiều, đó thường là sức mạnh hoặc yếu riêng của coin đó.
  - Đóng sớm khi BTC bùng nổ ngược chiều (A1E/A2E) cũng làm Sharpe năm 2025 giảm (−0.45 / +0.01 so với +0.23).
- **Đã làm trong code:**
  - `BtcAlign`, `align_entry`, `btc_context` có trong `core/trend_lane.py`.
  - Executor có `Profile.trend_btc_align = 'off' | 'shadow' | 'sign' | 'trend'` và `trend_btc_eject`.
  - Ngoại lệ "alt có lực riêng": khối lượng > 3.0 × SMA20 và tỉ lệ CVD (CVD / khối lượng) ≥ 10 % cùng chiều alt. Ngưỡng cố định trước khi chạy.
  - Không có dữ liệu BTC (UNKNOWN) thì không mở lệnh alt.
- **Demo:** `trend_btc_align='shadow'`. Engine ghi `BTC shield (shadow) would hold back SELL: against BTC BUY (TREND)` nhưng vẫn vào lệnh. Sau vài chục lệnh có thể đối chiếu bằng dữ liệu thật trước khi chuyển sang `'trend'` hoặc `'sign'`.

### 2b. Nhiệm vụ 2: pyramiding và conviction

**P1, dời stop về hoà vốn: được chấp nhận.**
- Khi một nến 4H đóng có lãi ≥ +2 %, stop thảm hoạ trên sàn dời từ 8 ATR về giá vào ±0.3 %. Thứ tự: huỷ stop cũ rồi đặt stop mới, vì Binance chỉ cho một stop `closePosition` mỗi chiều.
  - Nếu sàn từ chối stop mới, engine đặt lại mức cũ. Nếu cũng không được, engine đóng vị thế (`STOP-LOST`). Vị thế không bao giờ nằm trên sàn mà không có stop.
- Kết quả: tỉ lệ thắng 47 % → 64 %, trung vị lệnh −30 → **+18 bps**, DD 5.9 % → 5.0 %. Đổi lại mất một ít %/năm (4.9 → 4.2): 18 lệnh dừng ở hoà vốn rồi giá chạy tiếp.
- **"Zero risk" không tuyệt đối:**
  - Nến mở gap xuyên stop có thể khớp tệ hơn.
  - Stop được đặt lúc nến đóng; giữa hai lần kiểm tra, vị thế vẫn còn stop 8 ATR.
  - Không hứa chắc được hoà vốn.

**P2, vào thêm 0.5x: bị loại.**
- Sharpe DEV 1.25 thấp hơn 1.27 của B0. Tỉ lệ thắng tụt xuống 34 %: phần vào thêm có stop mềm ở đáy nến 4H và hay bị quét.
- Phần này có trong backtest (`Pyramid(add_frac=0.5)`) nhưng **không đưa vào executor**. Lý do thứ hai: venue hiện chỉ có stop `closePosition`, chưa có stop theo khối lượng cho riêng phần vào thêm.

**C3, conviction: được chấp nhận.**
- Định nghĩa đồng thuận: TSMOM 1H trên 24/72/168 giờ, tín hiệu 4H và tín hiệu 1D (spec 1D LS đã đăng ký) cùng dấu. Chỉ dùng nến đã đóng.
- Khi đồng thuận, size = min(30 % ví, 2 % ví / khoảng cách tới stop 8 ATR), tối đa $1 500. Khi không đồng thuận, giữ min(10 %, 1 %).
- Khoảng 50 % số nến 4H ngoài mẫu có đồng thuận; 37/70 lệnh dùng size conviction.
- Với ATR trung vị, trần rủi ro 2 % là ràng buộc thực tế, không phải trần 30 %:

| Coin | Size khi đồng thuận |
|---|---|
| BTC | 2 % / 9.3 % = 21.5 % → **≈ $1 075** |
| SOL | 12.1 % → **≈ $606** |
| DOGE | 11.3 % → **≈ $565** |

  $1 500 chỉ đạt được khi 8 ATR ≤ 6.7 %.
- Thiếu nến 1H hoặc 1D (lỗi mạng) → coi như không đồng thuận, size thường. Không bao giờ dùng size lớn khi thiếu dữ liệu.

**Rủi ro tổng:**
- Mỗi vị thế lỗ tối đa 2 % ví ở stop 8 ATR.
- Ba vị thế conviction cùng lúc → khoảng 6 % ví, và notional tối đa khoảng 0.9x ví.
- Sau khi dời stop về hoà vốn, rủi ro của vị thế đó về khoảng 0.
- Đòn bẩy cài trên sàn (5x) không đổi.

### 2c. Muốn 25–40 %/năm phải trả giá bằng DD

Nhân lợi nhuận ngày của cấu hình với hệ số k (OOS, không tính lãi kép, không mô hình margin hay thanh lý):

| k | B0 %/năm | B0 DD | FINAL %/năm | FINAL DD |
|---|---|---|---|---|
| 1 | 4.9 | 5.9 % | 8.9 | 7.5 % |
| 2 | 9.8 | 11.8 % | 17.7 | 14.9 % |
| 3 | 14.8 | 17.7 % | **26.6** | **22.4 %** |
| 5 | 24.6 | 29.5 % | **44.3** | **37.3 %** |
| 8 | 39.4 | 47.2 % | 70.9 | 59.7 % |

- Sharpe không đổi theo k. Size không tạo ra lợi thế, chỉ phóng to cả lãi lẫn DD.
- Mức 25–40 %/năm tương ứng k ≈ 3–4.5 với FINAL, tức DD lịch sử 22–34 % ví, và trần rủi ro mỗi lệnh phải lên khoảng 6–9 %.
- Điều đó vi phạm giới hạn 2 % của chính form. Vì vậy **tôi không tăng k**. Nếu muốn, đó là quyết định của bạn, và chỉ nên làm sau khi demo cho thấy kết quả thật khớp với replay.

## 3. Thay đổi code

| File | Nội dung |
|---|---|
| `core/trend_lane.py` | `BtcAlign`, `btc_context`, `align_entry`, `Pyramid`, `H1_LOOKBACKS`, `h1_sign`, `conviction_series`; `regime_info` thêm `cvd_share`; `backtest_brackets(align, pyramid, conv, conv_sizing)` (lý do thoát `BE`, `BTC-EJECT`) |
| `core/trend_executor.py` | BTC context và `align_ok` cho alt; `conviction()` từ nến 1H/1D; `size(conv)`; `break_even()` dời stop sàn; `exit_check` có `BTC-EJECT`; dòng trade có `conv`, `be` |
| `core/engine.py` | Profile `trend_btc_align`, `trend_btc_eject`, `trend_be_pct`, `trend_pyr_arm_pct`, `trend_conv_alloc_pct`, `trend_conv_risk_pct`. Demo: P1 + C3 + shield shadow, `trend_notional_max=1500`. `trend_active_loop` tải thêm nến 1h/1d khi bật conviction. Lớp REST chống lỗi (mục 4) |
| `tools/trend_regime_replay.py` | `--stage ff` |
| `tests/test_trend_btc_alignment_pyramid.py` | 17 test (mục 5) |

## 4. Nhiệm vụ 3: lỗi mạng `_ssl.c:990`

- **Retry và timeout:** `rest_json` thử mỗi host 3 lần, timeout 12 s, nghỉ 0.5 s rồi 1 s giữa các lần.
  - Có retry: SSL handshake, DNS, timeout, reset, 429/418/5xx.
  - Không retry: 4xx dứt khoát như 451 (chặn địa lý) hay 400.
  - `fetch_klines`, `fetch_funding`, `fetch_klines_5m` đều dùng timeout 12 s.
- **Kiểm tra endpoint dự phòng (ngày 10-09):**
  - `fapi.binance.vision`: proxy từ chối kết nối (connect_rejected), nên không xác minh được host này có tồn tại không. Tôi không thấy nó trong tài liệu nào.
  - `fapi1/2/3.binance.com`: trả 302 về `www.binance.com/en`, tức không phải API mirror.
  - Vì vậy `REST_HOSTS` chỉ có `https://fapi.binance.com`. Có thể thêm host khi tìm được mirror chính thức.
- **Kline Memory Cache:** gọi lỗi → trả về nến đã đóng lần gần nhất của cùng (symbol, interval, limit). Executor thấy `bar_t` cũ thì không làm gì, nên không có quyết định sai. Vòng lặp không còn văng exception.
- **Khởi động lạnh, chưa có cache:** dùng kline **spot** từ `data-api.binance.vision/api/v3/klines` (đã kiểm tra: trả cùng định dạng).
  - Giá spot rất gần perp, nhưng khối lượng là của spot. Vì vậy các dòng này **bỏ volume/CVD**: cổng xác nhận coi là UNKNOWN và không mở lệnh mới.
  - Lệnh thoát theo giá (tín hiệu, CHOP, lock) vẫn hoạt động.
- `KLINE_SOURCE[(symbol, interval)]` ghi lại nguồn đã dùng: `fapi`, `cache` hoặc `spot-proxy`.
- **Chưa kiểm tra trên mạng của bạn.** Test chỉ dùng HTTP giả. Từ cloud, `fapi.binance.com` trả 451 (chặn địa lý), nên không chạy thử end-to-end được.

## 5. Kiểm định

**Test:**
- `python -m pytest -q` trong cloud cho kết quả **1454 passed, 28 skipped, 2 failed**.
- Hai test fail đã fail sẵn trước vòng này: chúng cần file local không có trong repo (signal-lab testnet trong `data/` và `tools/check_models_live.py`).
- Trên máy bạn dự kiến 1467 + 17 = **1484 passed**. Thời gian chạy suite không đổi (khoảng 2 phút).

17 test mới gồm:
- shield (ngoại lệ, chế độ `trend`/`sign`, UNKNOWN, BTC đi ngang);
- backtest có shield; stop hoà vốn có lãi; phần vào thêm được ghi nhận;
- `conviction_series` và conviction sizing trong backtest;
- executor: shield chặn thật / shadow chỉ ghi log; `BTC-EJECT` chỉ áp cho alt;
- dời stop hoà vốn đúng một lần; stop bị từ chối thì khôi phục mức cũ;
- size conviction và trần rủi ro; conviction từ nến 1H/1D;
- `rest_json`: retry, backoff, 451 → host kế tiếp;
- cache rồi spot-proxy (không có volume); timeout 12 s;
- profile production.

Test các vòng (dd)/(ee) cố định cấu hình cũ trong fixture.

**Lưu trữ:**
- Snapshot `docs/research_upgrade/snapshot_2026-10-09ff/` (JSON, log dạng txt, rows_add, SHA256SUMS).
- `experiment_register.md` thêm FF1, FF2, AB-15..20; `trial_log.jsonl` thêm 6 dòng.

**Hạn chế:**
- Replay theo nến 4H, không mô hình hàng đợi khớp lệnh. Chi phí maker/taker lấy từ vòng (dd).
- Tín hiệu 1D dựng từ kline 1h theo ngày UTC.
- FINAL là phép ghép sau lần chạy đầu (đã tính thêm 1 lần thử).
- Tất cả kết quả đều chưa có ý nghĩa thống kê sau khi trừ số lần thử.

**Rollback** (Profile demo trong `core/engine.py`):
```
trend_be_pct=None, trend_conv_alloc_pct=None, trend_btc_align='off', trend_notional_max=600.0
```
Lớp mạng gỡ bằng `git revert`.

## 6. Sau khi restart demo

**Event cần theo dõi:**
- `BREAK-EVEN: exchange stop …`
- `CONVICTION 1H+4H+1D` trong dòng OPEN
- `BTC shield (shadow) would hold back …`
- `TREND 1h/1d fetch failed …`

**Dữ liệu cần theo dõi:**
- các trường `conv`, `be` trong `data/trend_trades.jsonl`;
- nguồn nến trong `KLINE_SOURCE`.

**Kỳ vọng:**
- Vẫn khoảng 3–4 lệnh/tháng.
- Size $280–1 075 tuỳ coin và độ đồng thuận.
- Khoảng +8.9 %/năm ≈ **$37/tháng** trên ví $5 000 nếu tương lai giống 2025–26. Phương sai lớn; tháng lỗ là bình thường.

**Lệnh DOGE short đang mở:**
- Shadow không đóng lệnh này. Nó vẫn được bảo vệ bởi stop 8 ATR, thoát khi CHOP và lock.
- Muốn đóng tay thì làm trên sàn. Executor sẽ thấy vị thế đã phẳng và ghi lý do `DISASTER-STOP`.
