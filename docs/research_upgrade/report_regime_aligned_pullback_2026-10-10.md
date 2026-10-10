# Báo cáo nghiệm thu 10/10/2026 (c): pullback thuận regime và swing intraday 15m/1h

**Phạm vi.**
- Code, test và replay offline trên kline 1 phút thật của Binance USD-M (BTCUSDT, SOLUSDT, DOGEUSDT; 2024-07 → 2026-09).
- Không khởi động lại engine, không gửi lệnh nào, không đổi live, không tăng vốn/đòn bẩy.
- Không đụng tới `data/engine_trades.jsonl` hay `data/trend_trades.jsonl`.
- Làn 3 vẫn **chỉ shadow** (`intraday_lane='shadow'` trên demo, `'off'` trên live). Lệnh ảo ghi vào `data/intraday_trades_shadow.jsonl`.

## 0. Kết luận

| Mục tiêu của form | Kết quả hold-out 2026-01 → 09 (3 coin gộp) |
|---|---|
| Tỷ lệ thắng 60–68 % | **Đạt một phần**: P15_tp35 thắng 59.9 % (DEV 63.4 %). P15_tp50 51.7 %. R1 (chỉ fade trong CHOP) 34.1 % |
| Net EV > +15 bps/lệnh | **Không đạt**: mọi biến thể −6.5 đến −8.7 bps/lệnh. Khoảng tin cậy 95 % nằm hoàn toàn dưới 0 |
| Sharpe ≥ 1.3–2.0 | **Không đạt**: −3.5 đến −13 |
| Swing 60–120 bps, giữ 30 phút – 3 giờ | Cơ chế đã có: TP1 50 % ở +35/+50 bps, phần còn lại về hòa vốn rồi trailing chandelier 2.5 ATR. Thời gian giữ trung vị 46–61 phút |
| `intraday_close_margin_s` 5 → 10 s | **Đã làm** |
| 100 % test | 1 493 passed. Còn 2 lỗi có từ trước, do máy cloud thiếu file local (mục 6) |

**Vì sao tỷ lệ thắng tăng mà vẫn lỗ:**
- **TP1 + hòa vốn chỉ đổi hình dạng phân phối, không tạo ra lợi thế.**
  - P15_tp35 thắng 60 %, nhưng lệnh thắng trung bình chỉ +29.6 bps còn lệnh thua trung bình −61.7 bps.
  - Muốn hòa thì cần thắng 61.7 / (29.6 + 61.7) = **67.6 %**. Thực tế là 59.9 %.
- **Gross trước phí ≈ 0** ở mọi biến thể (−1.4 đến +1.0 bps). Phí khứ hồi 7–8 bps quyết định dấu kết quả. Điều này giống hệt vòng (b).
- **Giả thuyết "lỗ do fade ngược xu hướng" bị bác bỏ** (mục 3).
  - Các tín hiệu S1 ngược xu hướng (n ≈ 6 000) lỗ −7.3 / −8.2 bps.
  - Tín hiệu trong CHOP lỗ −9.2 bps; trong MIXED lỗ −7.2 bps.
  - Chặn fade ngược xu hướng không lấy đi phần lỗ đặc biệt nào.

Theo quy tắc đã đăng ký trước, mọi (symbol, nhóm) đều **SHADOW**. Điều kiện để bật active là n ≥ 100, cận dưới CI hold-out > 0 và tỷ lệ thắng > 50 %. Kết quả ghi trong `config/intraday_verdict.json`.

Làn 3 shadow từ nay ghi hai biến thể mới thay cho S1/S2 (đã đo đủ ở vòng b): **R1_SESSION** và **P15_tp50**, là DEV-best của mỗi nhóm.

## 1. Đã làm gì

### Task 1 — cổng regime (`core/intraday_lane.py`, `regime5`)
Regime được xét tại mỗi lần nến 5m đóng, chỉ dùng nến đã đóng. Ngưỡng lấy đúng theo form và đăng ký trước lần chạy đầu tiên:

| Regime | Điều kiện |
|---|---|
| **UP** | (KER10(5m) > 0.40 **và** 10 nến đi lên) **hoặc** độ dốc EMA20(5m) > +0.10 % |
| **DOWN** | Đối xứng với UP |
| **CHOP** | KER10(5m) < 0.25 **và** ADX14(15m) < 20 |
| **MIXED** | Các trường hợp còn lại |

Áp vào chiến lược:
- **R1** = S1 (VWAP-band mean reversion, VWAP phiên) chỉ được vào lệnh khi regime là CHOP. Trong UP không bao giờ SELL ở dải trên; trong DOWN không bao giờ BUY ở dải dưới.
- **P5**: pullback 5m thuận regime. Trong UP:
  - đáy nến 5m chạm max(EMA20(5m), VWAP phiên);
  - giá đóng nến trở lại trên EMA20;
  - CVD của phút cuối > 0 (bên bán đã cạn).

  Khi đủ cả ba: BUY post-only tại giá đóng, lệnh sống 1 phút. DOWN đối xứng.

### Task 2 — swing 15m/1h (`P15`) và quản lý lệnh nhiều chân
- **Xu hướng 1h**: EMA20(1h) tăng ≥ 0.10 % qua nến 1h đã đóng gần nhất, **và** giá đóng nến 15m > EMA20(15m).
- **Pullback 15m**:
  - đáy nến 15m chạm max(EMA20(15m), VWAP phiên);
  - giá đóng trở lại trên EMA20(15m);
  - CVD của nến 15m > 0.

  Khi đủ: BUY post-only tại giá đóng 15m, lệnh sống 5 phút. DOWN đối xứng.
- **Quản lý lệnh** (P5 và P15):
  - Stop ban đầu: 1.5 ATR(5m) cho P5, 1.0 ATR(15m) cho P15.
  - **TP1**: chốt 50 % ở +35 hoặc +50 bps bằng maker.
  - Từ phút kế tiếp, phần còn lại có **stop = giá vào** (hòa vốn).
  - **TP2**: chandelier 2.5 ATR tính từ giá đóng tốt nhất, cập nhật mỗi khi nến 5m/15m đóng.
  - Rào thời gian 180 phút.
- **Ghi sổ**: mỗi lệnh ghi cả giao dịch trên toàn notional, gồm chân TP1 cộng phần còn lại. Thắng = net > 0. Nhãn thoát có dạng `TP1+SL`, `TP1+TIME`, …
- Tỷ lệ thắng của lệnh có TP1 rồi về hòa vốn là đúng nghĩa: net vẫn dương sau phí. Ví dụ TP1 +35 → net ≈ +11.5 bps.

### Task 3 — `intraday_close_margin_s` 5.0 → 10.0 s
Một nến 1m chỉ được nhận sau 10 giây kể từ lúc đóng, tính theo **đồng hồ server Binance**. Mục đích là tránh cảnh báo lệch đồng hồ trên Windows. Đổi tại `core/engine.py` (Profile) và có test.

### Task 4 — replay
- **Một nguồn sự thật duy nhất.** Với R1/P5/P15, `tools/intraday_replay.py` gọi `run_lane`, tức là chạy **chính engine bar-by-bar của làn shadow** (`IntradayLane._bar`) trên dữ liệu lịch sử. Rows được thu qua `sink`, không ghi ra ledger. Shadow và replay vì thế không thể lệch nhau.
- **Thiết lập** giữ nguyên như vòng (b):
  - DEV 2025 dùng để chọn biến thể; HOLD-OUT 2026-01 → 09 chỉ dùng để báo cáo.
  - Maker 2 bps; taker 5 bps + 1 bp trượt giá.
  - Lệnh limit chỉ khớp khi giá **đi xuyên** qua giá đặt.
  - Trong phút khớp lệnh chỉ tính stop; trong cùng một phút, stop được xét trước target.
  - CI là bootstrap theo cụm ngày.
- Thời gian chạy 31 s cho 3 coin × 12 biến thể.

## 2. Kết quả (3 coin gộp)

| Biến thể | Split | n (lệnh/ngày) | Thắng | Net bps [CI 95 %] | Gross | TB thắng / thua | Sharpe | $/ngày @$150 | Giữ trung vị |
|---|---|---|---|---|---|---|---|---|---|
| S1_SESSION (vòng b, đối chứng) | HO | 4 704 (17.2) | 32.2 % | −7.09 [−8.33, −5.82] | +0.56 | +35.7 / −27.4 | −12.2 | −1.83 | 21 phút |
| R1_SESSION | DEV | 365 (1.0) | 31.0 % | −10.47 [−14.37, −6.38] | −2.93 | +35.2 / −31.0 | −4.3 | −0.16 | 18 phút |
| R1_SESSION | **HO** | 337 (1.2) | 34.1 % | **−6.51 [−10.47, −2.31]** | +1.01 | +32.8 / −26.9 | −3.6 | −0.12 | 23 phút |
| P5_tp35 | DEV | 7 127 (19.5) | 50.5 % | −8.17 [−9.92, −6.20] | −1.17 | +30.5 / −47.7 | −8.7 | −2.39 | 33 phút |
| P5_tp35 | HO | 5 246 (19.2) | 46.9 % | −8.08 [−9.42, −6.62] | −1.00 | +27.2 / −39.2 | −13.0 | −2.33 | 41 phút |
| P5_tp50 | HO | 5 027 (18.4) | 38.9 % | −8.65 [−10.28, −6.97] | −1.37 | +39.9 / −39.6 | −11.8 | −2.39 | 49 phút |
| P15_tp35 | DEV | 2 615 (7.2) | **63.4 %** | −7.98 [−10.74, −5.27] | −1.24 | +28.9 / −71.9 | −5.6 | −0.86 | 36 phút |
| P15_tp35 | **HO** | 1 373 (5.0) | **59.9 %** | **−7.07 [−10.83, −3.04]** | −0.25 | +29.6 / −61.7 | −4.1 | −0.53 | 46 phút |
| P15_tp50 (DEV-best P) | DEV | 2 468 (6.8) | 56.2 % | −7.43 [−10.76, −4.04] | −0.54 | +43.1 / −72.2 | −4.3 | −0.75 | 48 phút |
| P15_tp50 | **HO** | 1 289 (4.7) | 51.7 % | **−6.92 [−11.35, −2.26]** | +0.08 | +43.8 / −61.1 | −3.5 | −0.49 | 61 phút |

Phân bố cách thoát của P15_tp35 trên HO:

| Cách thoát | Số lệnh |
|---|---|
| SL | 522 |
| TP1+SL | 606 (TP1 rồi về hòa vốn, hoặc dính trailing) |
| TP1+TIME | 206 |
| TIME | 39 |

Như vậy TP1 được chạm ở 59 % số lệnh.

### Theo coin (hold-out, biến thể DEV-best; `config/intraday_verdict.json`)

| Coin | R1_SESSION | P15_tp50 |
|---|---|---|
| BTCUSDT | n 96, thắng 29 %, −10.17 bps [−14.83, −5.49] | n 241, thắng 43 %, −7.53 [−15.62, +0.59] |
| SOLUSDT | n 125, thắng 34 %, −5.41 [−11.85, +2.05] | n 539, thắng 53 %, −6.59 [−12.22, −0.82] |
| DOGEUSDT | n 116, thắng 38 %, −4.67 [−12.44, +4.27] | n 509, thắng 54 %, −6.97 [−12.96, −0.97] |

Không ô nào có cận dưới CI > 0, nên cả 3 coin đều **SHADOW**.

P15_tp35 theo coin trên HO:

| Coin | Thắng | Net bps |
|---|---|---|
| BTC | 53 % | −5.2 [−12.4, +1.7] |
| SOL | 61 % | −6.9 |
| DOGE | 62 % | −8.1 |

## 3. Chẩn đoán: fade ngược xu hướng có phải nguồn lỗ? (`snapshot_2026-10-10c/regime_diag.txt`)
Bảng dưới phân loại các tín hiệu S1_SESSION giai đoạn 2025-01 → 2026-09 (3 coin gộp) theo regime 5m tại thời điểm tín hiệu:

| Regime / chiều | n | Thắng | Net trung bình |
|---|---|---|---|
| UP, **ngược** xu hướng (SELL ở dải trên) | 3 027 | 31.4 % | −7.33 bps |
| DOWN, **ngược** xu hướng (BUY ở dải dưới) | 3 034 | 31.1 % | −8.20 bps |
| CHOP | 505 | 31.1 % | −9.15 bps |
| MIXED | 4 072 | 33.5 % | −7.18 bps |
| UP/DOWN thuận xu hướng | 11 | — | quá ít |

- Fade trong CHOP **không tốt hơn** fade ngược xu hướng. Vì vậy R1 chỉ giảm số lệnh (−93 %), không cải thiện EV/lệnh có ý nghĩa: −6.5 so với −7.1 bps, CI chồng lên nhau.
- Các lệnh SHORT BTC 16:08–16:32 mà form nêu là đúng loại lệnh R1 chặn. Nhưng nhìn trên 20 tháng, loại lệnh đó không lỗ hơn phần còn lại.

## 4. An toàn / bất biến
- Làn 3 không có đường đặt lệnh thật; `'active'` bị từ chối theo thiết kế. Live vẫn `intraday_lane='off'`.
- VESKA/AuthToken, quyền veto của RUNE, trần rủi ro, cầu dao lỗ ngày (`intraday_daily_kill_usdt`) và `would_block` đều giữ nguyên.
- Không bật scalper replay âm, không tăng size hay đòn bẩy.
- Dữ liệu thiếu → `UNKNOWN`, không bao giờ coi là 0. Một lỗ hổng dữ liệu đóng các lệnh ảo với nhãn UNKNOWN.
- Hai ledger `data/engine_trades.jsonl` và `data/trend_trades.jsonl` không bị đụng tới.

## 5. Test
`tests/test_intraday_lane.py`: 24 test (19 cũ + 5 mới).

| Test mới | Kiểm tra gì |
|---|---|
| `test_regime5_up_down_chop_mixed` | Phân loại regime |
| `test_r1_blocks_a_band_fade_outside_chop` | R1 không fade ngoài CHOP |
| `test_p15_tp1_half_then_break_even_books_the_whole_trade` | `TP1+SL`, net = 0.5·35 − phí từng chân |
| `test_p15_trailing_chandelier_after_tp1` | Trailing chandelier sau TP1 |
| `test_replay_runs_the_lane_engine_for_the_new_variants` | Replay = engine của làn |

Ngoài ra:
- Test production đã kiểm thêm margin 10 s và hai biến thể mặc định.
- Test thanh thời gian cũ nay ghim rõ biến thể S1, vì mặc định của Profile đã đổi.

## 6. Kiểm tra đã chạy
- `python -m pytest -q`: **1 493 passed, 28 skipped, 2 failed**. Hai lỗi có từ trước, do môi trường cloud thiếu file local; giống hệt các vòng (ee) → (b):
  - `test_policy_replay.py::test_engine_reads_the_recorded_testnet_probes`: thiếu signal lab `data/` testnet.
  - `test_upgrade_2026_10.py::test_credential_file_still_ignored_after_move`: thiếu `tools/check_models_live.py`.

  Trên máy Windows có đủ file, hai test này PASS (commit `c32234c`: 1 518 tests).
- Replay: `python tools/intraday_replay.py --data <thư mục SYMBOL-1m-YYYY-MM.zip> --json … --write-verdict` cho kết quả ở `snapshot_2026-10-10c/`, có SHA256SUMS.

## 7. Rollback
- Đưa biến thể shadow về như cũ: `intraday_variants=('S1_SESSION', 'S2_slope3_tp60')`.
- Margin: `intraday_close_margin_s=5.0`.
- Tắt làn: demo `intraday_lane='off'`.

Các biến thể mới chỉ là thêm vào; S1/S2 vẫn chạy được nguyên trạng.

## 8. Giới hạn và bước tiếp theo
- **Kết luận giới hạn trong phạm vi đã đo.** Chỉ đúng với 3 coin, kline 1m (không có sổ lệnh), phí VIP0 và quy tắc khớp bảo thủ. Có fill tốt hơn (chạm thay vì xuyên) thì vòng (b) cho thấy vẫn âm.
- **Không có gì để bật.** Làn shadow sẽ thu R1/P15 trên dữ liệu mainnet mới. Chạy lại `tools/intraday_replay.py --write-verdict` khi đã có thêm ≥ 3 tháng dữ liệu.
- **Tỷ lệ thắng không phải mục tiêu đúng.** Con số này có thể đẩy lên tùy ý bằng TP gần và stop xa, mà EV không đổi. Mục tiêu nên là **gross edge trước phí ≥ 10 bps** trên hold-out. Đến nay chưa họ intraday 5m/15m nào (10 họ, 40+ biến thể) vượt được 0 một cách có ý nghĩa.
- **Hướng có cơ sở hơn là giảm phí.** Ví dụ 0 % maker USDC (chưa xác nhận), hoặc khung dài hơn nơi edge lớn hơn phí, như trend 4H đã có OOS SR 1.06. Thêm bộ lọc vào cùng một tín hiệu không phải hướng đi.
