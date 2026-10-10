# Báo cáo nghiệm thu 10/10/2026: kiến trúc 2 động cơ và làn scalper sub-minute theo xung biến động

**Phạm vi.**
- Code, test và replay offline trên aggTrades thật của Binance. Không khởi động lại engine, không gửi lệnh nào.
- Không đụng tới `data/engine_trades.jsonl` hay `data/trend_trades.jsonl`.
- Làn mới ghi vào file riêng:
  - `data/micro_trades_shadow.jsonl` ở chế độ shadow;
  - `data/micro_trades.jsonl` ở chế độ active.
- Live không đổi (`burst_lane='off'`).

## 0. Kết luận

| Câu hỏi của form | Kết quả đo được |
|---|---|
| Có đạt 20–50 lệnh/giờ trong pha biến động không? | **Có.** 90–200 lệnh mỗi giờ burst, tức 18–28 lệnh/giờ tính trung bình cả ngày, trên SOL/DOGE |
| Net EV có dương sau phí không? | **Không, ở mọi biến thể.** Hold-out SOLUSDT/DOGEUSDT: −7.2 đến −7.9 bps/lệnh, khoảng tin cậy 95 % hẹp, toàn bộ dưới 0. Ngay cả mô hình khớp lạc quan cũng còn −4 đến −5 bps |
| TP 15–30 bps có bù được phí 4 bps không? | **Không.** TP 30 bps chỉ khớp ở 1–5 % số lệnh trong 60 giây. Phần lớn lệnh thoát bằng time stop |
| Pure-maker có tránh được phí taker không? | Lệnh vào luôn là maker. Nhưng lệnh maker chỉ khớp khi giá chạy **xuyên qua** mình (bị chọn ngược): **gross đã âm −1.8 đến −3.9 bps trước khi trừ phí** |
| USDC-M phí maker 0 % + Avellaneda–Stoikov có ăn được spread không? | **Không.** Market making ở phí 0 % vẫn âm (−2.2 đến −5.0 bps mỗi vòng) vì bị chọn ngược. Ngoài ra phí 0 % hiện **chưa được xác nhận** (mục 3) |
| Tiền thật nếu bật trên ví $5 000, $75 mỗi lệnh | SOL + DOGE ≈ **−$62/ngày ≈ −$1 850/tháng (−37 % ví/tháng)** theo mô hình khớp bảo thủ |

Theo quy tắc đã đăng ký trước, làn chỉ được đặt lệnh thật khi khoảng tin cậy hold-out nằm hoàn toàn trên 0. Không symbol nào đạt.

Kết quả: làn 2 đã có đủ code, chạy ở chế độ **shadow trên demo** (giao dịch ảo trên giá mainnet thật, theo đúng luật replay). Chế độ active bị chặn bằng `config/burst_verdict.json`. Đây cũng là quy tắc trong CLAUDE.md: không bật scalper có replay âm.

## 1. Replay (`tools/burst_scalper_replay.py`)

**Dữ liệu:**
- aggTrades USD-M từ data.binance.vision cho SOLUSDT, DOGEUSDT, SOLUSDC, DOGEUSDC.
- 2026-09-01 → 10-08: 38 ngày × 4 cặp = 152 symbol-ngày. File ngày 10-09 chưa được phát hành.
- DEV (chọn biến thể): 09-01 → 09-20. HOLD-OUT (báo cáo): 09-21 → 10-08.
- Toàn bộ chạy mất 104 giây trên 4 nhân.

**Burst** (đăng ký trước, ngưỡng lấy từ form) khi một trong ba điều kiện đúng:
- σ_1m ≥ 15 bps;
- |TFI 10 s| > 0.85: mất cân bằng lệnh chủ động, cần ít nhất 20 lệnh khớp. Đây là proxy cho OFI L10, vì lịch sử độ sâu sổ lệnh không công khai;
- volume 1 phút > 3 × trung bình 20 phút trước. Đây là proxy cho chuỗi thanh lý, vì lịch sử forceOrder không công khai.

**Chính sách:**
- **Tín hiệu:**
  - MOM: theo dấu CVD 10 s.
  - MR: đánh ngược chiều giá 10 s.
- **Vào lệnh:** post-only ở touch của mình, đặt lại giá mỗi 4 s khi burst còn kéo dài.
- **Thoát:**
  - TP post-only ở ±15 hoặc ±30 bps;
  - SL bằng TP, khớp bằng lệnh taker;
  - time stop 60 s, sau đó maker ở touch thêm 10 s, rồi taker.
- **MM:** quote cả hai đầu touch khi phẳng; khi giữ 1 lot chỉ quote phía giảm vị thế; stop tồn kho 60 s.
- **Độ trễ:** 0.25 s cho mọi lệnh đặt và huỷ (kiểm tra thêm mức 0.5 s).

**Mô hình khớp:**
- "xuyên giá" (chính): chỉ khớp khi có lệnh khớp đi qua giá của mình, tức mình đứng cuối hàng chờ.
- "chạm giá" (cận lạc quan): khớp ngay khi có lệnh khớp chạm giá.
- Không mô hình hàng chờ. Sổ lệnh được dựng lại từ phía của lệnh chủ động.

**Phí:**
- USDT VIP0: maker 2 / taker 5 bps.
- USDC: maker 0 / taker 4 bps. Đây là kịch bản; mức phí này chưa xác nhận.

### Hold-out, biến thể tốt nhất theo DEV và các biến thể tham chiếu

| Cặp | Burst (% thời gian) | Biến thể | Lệnh/giờ (cả ngày / giờ burst) | Net bps/lệnh [CI95] | Gross bps | Thắng | $/ngày ở $75 |
|---|---|---|---|---|---|---|---|
| SOLUSDT | 20 % | **MR30** xuyên giá (DEV-best) | 24.9 / 128 | **−7.17 [−7.36, −6.97]** | −1.86 | 19 % | −32 |
| | | MR30 chạm giá | 37.1 / 190 | −4.08 [−4.23, −3.95] | +0.03 | 27 % | −27 |
| | | MOM15 xuyên giá | 20.9 / 108 | −7.39 [−7.61, −7.17] | −1.98 | 21 % | −28 |
| | | MM xuyên giá | 226 / 1 163 | −6.86 | −2.79 | 0 % | −279 |
| DOGEUSDT | 18 % | **MR30** xuyên giá (DEV-best) | 21.3 / 107 | **−7.69 [−7.99, −7.35]** | −2.57 | 26 % | −30 |
| | | MR30 chạm giá | 27.9 / 140 | −5.09 [−5.39, −4.80] | −0.69 | 32 % | −26 |
| | | MM xuyên giá | 246 / 1 236 | −7.40 | −3.35 | 0 % | −328 |
| SOLUSDC (maker 0 %) | 10 % | **MOM30** xuyên giá (DEV-best) | 7.5 / 83 | **−3.98 [−4.48, −3.53]** | −2.44 | 34 % | −5 |
| | | MM xuyên giá / chạm giá | 163 / 1 809 | −3.50 / −2.23 | −3.35 / −2.18 | 13 / 25 % | −103 / −91 |
| DOGEUSDC (maker 0 %) | 15 % | **MOM30** xuyên giá (DEV-best) | 11.8 / 69 | **−5.52 [−5.99, −5.00]** | −3.73 | 35 % | −12 |
| | | MM xuyên giá / chạm giá | 134 / 779 | −4.96 / −3.63 | −4.70 / −3.50 | 15 / 24 % | −120 / −111 |

- Đủ 14 biến thể × 2 giai đoạn × 4 cặp có trong `snapshot_2026-10-10a/burst_replay.txt`.
- DEV và hold-out cho kết quả gần như giống nhau. Độ trễ 0.5 s làm tệ thêm khoảng 0.1–0.3 bps.

**Vì sao âm** (con số lấy từ chính replay này):
1. **Lệnh maker bị chọn ngược.**
   - Ở mô hình xuyên giá, gross trước phí đã âm (−1.8 đến −3.9 bps). Lệnh treo ở bid chỉ khớp khi người bán đủ mạnh để xuyên qua mức đó, và giá thường đi tiếp theo hướng ấy.
   - Ở mô hình chạm giá, gross khoảng 0: không có lợi thế nào để trả phí 4 bps.
2. **TP 15–30 bps quá xa so với 60 giây.**
   - TP 30 bps chỉ khớp 1–5 % số lệnh, TP 15 bps khoảng 10–20 %. Phần còn lại thoát bằng time stop (maker hoặc taker) và stop.
   - σ_1m ≥ 15 bps không có nghĩa là giá đi 15–30 bps **theo hướng có lợi** trong 60 giây.
3. **Phí 0 % cũng không cứu được.** USDC-M có gross âm hơn (sổ lệnh mỏng hơn). Market making ở phí 0 % vẫn mất 2.2–5.0 bps mỗi vòng.

Điều này khớp với tổng quan tài liệu ngày 08/10 (`research_alpha_after_fees_2026-10-08.md`):
- OFI khung giây và market making ở VIP0 có gross edge khoảng 0.5–1.5 bps;
- hftbacktest với độ trễ khoảng 500 ms cho Sharpe âm sâu.

## 2. Kiến trúc 2 động cơ (code)

**`core/burst_lane.py`, `BurstDetector` + `BurstLane`:** chạy độc lập với làn trend 4H, vòng `Runner.burst_loop` mỗi 0.25 s.
- **Burst:** cùng định nghĩa với replay (σ_1m, TFI 10 s, surge). OFI L10 thật từ depth được hiển thị song song (`ofi_l10`) nhưng không dùng làm điều kiện kích hoạt, để shadow kiểm chứng đúng chính sách đã replay.
- **Khớp lệnh:**
  - Lệnh vào post-only GTX ở touch, đặt lại giá mỗi 4 s.
  - TP post-only; SL là IOC taker với dải bảo vệ 15 bps.
  - Time stop 60 s, sau đó maker 10 s, rồi taker.
  - Lệnh bị khớp một phần trước khi huỷ được nhận vào vị thế. Nếu IOC thoát không khớp thì thử lại ở vòng sau.
- **Chế độ** (`Profile.burst_lane`):
  - `off`;
  - `shadow`: lệnh ảo, khớp theo luật "xuyên giá" trên giá khớp mainnet thật; ghi `data/micro_trades_shadow.jsonl`; không gọi venue;
  - `active`: lệnh GTX/IOC thật trên **demo**, ghi `data/micro_trades.jsonl`. Chỉ được bật khi `config/burst_verdict.json` ghi `ACTIVE-ELIGIBLE` cho đúng symbol. File này do `tools/burst_scalper_replay.py --write-verdict` sinh ra; hiện cả 4 cặp là `SHADOW`. Ở live, active bị từ chối.
- **Chung tài khoản với làn trend:** Binance one-way mode chỉ có **một vị thế ròng mỗi symbol**, nên hai làn không thể giữ hai vị thế độc lập trên cùng một coin.
  - Làn burst chỉ vào khi symbol trống: không có vị thế trend, không có vị thế/lệnh vi mô (`trend_owned`, `pos`, `busy`).
  - Làn burst không vào trong 10 phút sau mỗi lần nến 4H đóng (`burst_trend_quiet_s`), để làn trend luôn thấy symbol trống khi đến lượt.
  - Muốn hai làn thật sự song song trên cùng coin thì cần Hedge Mode hoặc sub-account riêng. Việc đó ngoài phạm vi vòng này.
- **Vốn:**
  - Mỗi lệnh min($75, 5 % ví) (`burst_notional_usdt`, `burst_budget_pct`).
  - Chỉ chạy SOLUSDT/DOGEUSDT (`burst_symbols`), mỗi symbol tối đa 1 vị thế.
  - Cầu dao lỗ ngày $10 (`burst_daily_kill_usdt`) tính riêng cho làn này.
  - Ký quỹ ở 5x khoảng $15 mỗi vị thế, không ảnh hưởng làn trend.
- **Telemetry:** `snapshot['burst_lane'][symbol]` (burst, why, σ_1m, TFI, OFI L10, surge, pos, day_pnl, n). Event `BURST SHADOW SELL SOLUSDT TP +25.9 bps (+0.194 U)`.
- **Kiểm tra chéo** (`tools/burst_lane_xcheck.py`): cho **chính code lane live** chạy shadow trên 6 giờ aggTrades thật, so với replay trên cùng dữ liệu:

| Cặp / ngày | Replay: lệnh, bps/lệnh | Lane shadow: lệnh, bps/lệnh |
|---|---|---|
| SOLUSDT 09-25 | 167, −7.85 | 168, −8.04 |
| DOGEUSDT 09-25 | 87, −6.01 | 99, −5.98 |
| SOLUSDC 10-03 | 26, −5.85 | 25, −4.37 |

  Kết quả shadow trên demo vì vậy là phép đo trực tiếp của chính sách đã replay.

## 3. Nhiệm vụ 3: cặp phí 0 % (`tools/fee_survey.py`)

- **Tìm kiếm web (10/10):** không có thông báo chính thức nào của Binance về chương trình maker 0 % cho USDC-M đang chạy. Một nguồn bên thứ ba (Coinperps) ghi chương trình maker 0 % trên USDC-M **kéo dài đến tháng 2/2026**; chương trình giảm 10 % hồi 2024 cũng đã kết thúc. → **Chưa xác nhận.** Replay dùng maker 0 % chỉ như một kịch bản.
- **`python tools/fee_survey.py --quote USDC`** (chạy trên máy bạn; từ cloud thì `fapi.binance.com` bị chặn địa lý 451):
  - liệt kê perpetual USDC-M, spread touch, volume 24 h;
  - lấy **phí maker/taker thật của tài khoản bạn** qua `GET /fapi/v1/commissionRate` có ký (key mainnet trong `.env.local`, chỉ đọc).
  - Cặp có maker = 0 được đánh dấu `<- 0 % maker`. Thiếu key thì ghi UNKNOWN, không bao giờ giả định 0.
- **Ngay cả khi maker = 0 được xác nhận,** replay cho thấy MM ở touch vẫn âm, nên **không bật Avellaneda–Stoikov**. `micro_signals.as_quote` (AS 2008) vẫn có sẵn nếu sau này có rebate âm (VIP cao hoặc chương trình market maker).

## 4. Kiểm định

**Test:**
- `python -m pytest -q` trong cloud cho kết quả **1469 passed, 28 skipped, 2 failed**.
- Hai test fail đã fail sẵn trước vòng này: chúng cần file local không có trong repo (signal-lab testnet trong `data/` và `tools/check_models_live.py`).
- Trên máy bạn dự kiến 1484 + 15 = **1499 passed**.

15 test mới trong `tests/test_burst_lane.py`:
- detector: σ burst, TFI, surge cần đủ 21 phút, OFI chỉ báo cáo;
- shadow: MR có TP ghi vào ledger shadow mà không gọi venue; SL trả phí taker; time stop maker rồi taker;
- không vào lệnh khi trend giữ symbol, trong cửa sổ 4H, hoặc sau cầu dao;
- active bị từ chối khi không có verdict hoặc verdict SHADOW, và ở live; active có verdict thì đặt GTX thật ≤ $75 trên MockVenue;
- `print_hooks` của feed;
- profile production;
- hàm features/fill/simulate của replay;
- fee survey chỉ đánh dấu maker 0 % khi endpoint có ký trả về 0.

**Lưu trữ:**
- Snapshot `docs/research_upgrade/snapshot_2026-10-10a/` (JSON tóm tắt, log, kiểm tra chéo, SHA256SUMS).
- `config/burst_verdict.json`.
- `experiment_register.md` thêm HF1–HF3; `trial_log.jsonl` thêm 4 dòng.

**Hạn chế:**
- Không có sổ lệnh lịch sử: touch dựng từ lệnh khớp, OFI/thanh lý dùng proxy, không mô hình hàng chờ. Mô hình "xuyên giá" là cận dưới thực tế, "chạm giá" là cận trên.
- Thời gian đặt/huỷ lệnh thực tế trên demo có thể dài hơn 0.25 s.
- Sổ lệnh demo khác mainnet (spread DOGE trên demo 9–11 bps, vòng 08/10).

**Rollback:** demo `burst_lane='off'`. Phần còn lại không ảnh hưởng gì khi lane tắt.

## 5. Sau khi restart demo

- Làn trend 4H giữ nguyên cấu hình của vòng 09/10 (ff).
- Làn 2 chạy shadow trên SOL/DOGE:
  - event `BURST SHADOW …` trên Web UI;
  - từng lệnh ảo ghi trong `data/micro_trades_shadow.jsonl`;
  - trạng thái trong telemetry `burst_lane`.
- **Kỳ vọng:** khoảng 20–25 lệnh ảo/giờ mỗi coin, dồn vào các pha biến động. Theo replay, khoảng −7 bps mỗi lệnh. Nếu vài nghìn lệnh shadow ra trung bình dương với khoảng tin cậy trên 0, đó là bằng chứng mới. Khi đó chạy lại replay trên aggTrades mới với `--write-verdict` rồi mới bật active.
- **Hướng có cơ sở hơn để tăng tần suất mà không đốt phí:**
  - chỉ dùng tín hiệu vi mô làm **bộ định thời** cho làn trend (đã làm ở vòng dd: maker rẻ hơn taker 1.2 bps);
  - hoặc tìm một biến động lớn có thể dự báo ở khung phút–giờ (ví dụ phục hồi sau chuỗi thanh lý thật). Cần dữ liệu forceOrder tự ghi bằng `tools/record_market.py`.
