# Báo cáo nghiệm thu (bb) 09/10/2026: replay harness tốc độ cao, A/B ghép cặp, làn trend 1D/4H

**Phạm vi.** Đây là công việc offline cộng với một làn trend chỉ chạy PAPER.

Những điều KHÔNG làm trong vòng này:
- không khởi động lại engine;
- không gửi lệnh;
- không bật live;
- không tăng vốn hay đòn bẩy;
- không ghi vào `data/engine_trades.jsonl` (có test kiểm tra từng byte).

Một thay đổi hành vi nhỏ trong engine là bản sửa đồng hồ lỗ ngày (mục 6).

## 0. Ba tiền đề của form cần sửa trước

1. **Bản code `3ee16f29d2` không phải là thứ "chặn đà sụt giảm".**
   - Commit này chỉ thêm công cụ nghiên cứu. Giữa `c5c8be7` và `3ee16f2`, trong `core/` chỉ có `core/fast_eval.py` mới (đây là file thống kê, engine không import). Logic giao dịch y hệt bản trước.
   - Ledger lúc viết báo cáo có 23 lệnh mang hash này: trung bình −2.29 bps/lệnh, se 3.3, net −1.58 U.
     - SPRT: UNDECIDED. Cần khoảng 52 lệnh (SPRT) hoặc 107 lệnh (cỡ mẫu cố định).
     - Chuỗi 19 lệnh "hòa vốn" nằm trong vùng nhiễu ±5.5 bps.
   - Toàn bộ ledger: 78 lệnh, −3.13 bps, SPRT = BAD.
2. **"4H và 1D đã chứng minh +24.6 → +52.5 bps" là không đúng.**
   - Hai con số này đến từ cấu hình **1D** (ENS 1d LS / ENS_VT 1d LO) ở vòng (aa). Khung 4h trong vòng đó **thua phí** (ENS 4h LS −7.3 bps/RT).
   - PSR chỉ đạt 0.61–0.72, chưa có ý nghĩa thống kê. Đây là giả thuyết có triển vọng, chưa được chứng minh.
3. **"Replay toàn bộ engine một năm trong 15–30 giây" không khả thi với engine hiện tại.**
   - Engine Python quyết định mỗi 0.2 s, tức khoảng 157 triệu tick/năm/symbol. Tốc độ đo được ở mục 1.
   - Mục tiêu "dưới 1 phút" đạt được theo hai cách:
     - (a) làn trend: 4 năm dữ liệu replay trong 1–9 s;
     - (b) A/B vi mô trên **cửa sổ ngắn chọn trước**, chạy song song: vài phút thay vì 24 h demo.

## 1. Nhiệm vụ 1: `tools/engine_replay.py`, replay chính engine thật

**Chạy đúng mã production, không sửa:**
- `Engine` gồm swarm consensus, churn gates, scanner slot/alpha concentration, conviction sizing, ATR brackets, MARIN soft/hard exits, ratchet và TP1.
- `MicroFeed` và `MockVenue` (phí maker/taker theo Profile; lệnh chờ khớp khi giá đi xuyên qua; stop khớp taker cộng trượt giá).
- Hai vòng lặp `Runner.decide_loop` và `Runner.sync_loop`, dùng lại qua shim.

**Chỉ thay ba thứ:**
- **Thời gian ảo:** `time.time`, `time.monotonic`, `datetime.now` và loop asyncio đều đọc `VClock`. Loop nhảy thẳng tới timer kế tiếp, nên tick 0.2 s chỉ tốn CPU, không tốn thời gian thực.
- **Feed:** thông điệp lịch sử theo thứ tự thời gian, gom theo lô 50 ms.
- **Không mạng:** specs lấy từ `FALLBACK_SPECS`; không REST, không WebSocket, không key.

**Nguồn dữ liệu:**

| Nguồn | Sổ lệnh | Độ trung thực |
|---|---|---|
| `agg` (aggTrades data.binance.vision) | tổng hợp từ phía taker, 1 tick, size phẳng | gần đúng: OBI/OFI/WALL suy biến; logic dòng tiền (CVD, VWAP, KER, ATR) và exits vẫn thật |
| `l2` (`data/l2`, bookTicker + diff depth đã ghi) | touch thật, các mức gần touch dựng lại từ diff | tốt; hiện chỉ có SOL 08–10/09, khoảng 6.6 h |
| `rec` (`tools/record_market.py`) | snapshot + diff depth@100ms | tốt nhất; cần bật recorder |

**Không mô phỏng được:**
- sổ lệnh và basis riêng của sàn demo (±20–37 bps so với mainnet);
- vị trí trong hàng đợi;
- độ trễ, ack và reject.

Vì vậy kết quả dùng như **công cụ so sánh ghép cặp A và B**, không phải dự báo PnL tuyệt đối.

**Đầu ra:** JSON gồm summary, từng vị thế (`bps` ròng theo notional), PnL ròng theo giờ (U) căn theo cửa sổ, và tốc độ. Có thể dùng trực tiếp cho `edge_check.py --paired`, `ab_bench.py` và `core/fast_eval.py`.

**Tốc độ đo thật:**
- Cloud, 1 lõi:
  - SOL `agg`: khoảng 0.09–0.12 giờ mô phỏng mỗi giây, tức ~360× thời gian thực.
  - SOL+DOGE: khoảng 0.08 symbol-giờ mỗi giây mỗi lõi. Benchmark lớn chạy 784 symbol-giờ (cả 2 nhánh) trong 4 639 s với 2 tiến trình.
- Máy người dùng, `l2` SOL 3.9 h: 94 s, đã gồm đọc file 364 MB.
- Một năm SOL+DOGE trên 1 lõi mất khoảng 60 giờ. **Không phải 15–30 s.**

**Tối ưu đã làm (kết quả giống hệt bản cũ, có test):**
- `ker()` và `realized_sigma_bps()` duyệt từ mới về cũ và dừng ở mép cửa sổ (`newest_first=True`).
- Mức lợi nhỏ (khoảng 5–10 %), vì phần lớn CPU nằm ở `features()` mỗi tick.

**Hai lỗi đã sửa trong lúc dựng harness (ghi lại cho người bảo trì):**
- Thời gian epoch dạng float không đủ độ phân giải 1 ns của asyncio, nên loop dùng thời gian tương đối.
- Timer đã huỷ nằm ở đầu heap khiến `select()` ngủ thật.

**Baseline do harness đo: engine hiện tại trên dòng giao dịch mainnet 01–07/10.**
- Điều kiện: nguồn `agg`, 28 khúc 6 h, warm-up 60 phút, mock balance 5 000 U.
- Kết quả:
  - 108 vị thế;
  - net −8.74 U;
  - trung bình **−5.41 bps/lệnh** (sd 8.3);
  - WR 20 %.
- Cùng dấu với ledger demo (−3.1 bps). WR thấp hơn demo vì fill model "xuyên giá", sổ lệnh tổng hợp và khác biệt sàn. Không nên so trực tiếp con số tuyệt đối.
- Chạy `l2` SOL ngày 08/09 (sổ lệnh thật): 3 vị thế, −13.4 bps. Mẫu này quá nhỏ để kết luận.

## 2. Nhiệm vụ 2: làn trend 1D/4H (`core/trend_lane.py`)

**Đặc tả theo form, cố định TRƯỚC khi chạy:**
- Tín hiệu: TSMOM 5/20/60 bar, cộng Donchian 20, trộn 50/50.
- Vol targeting 20 %/năm cho mỗi sleeve, trần 1.5×.
- Overlay funding: giảm một nửa long khi funding trung bình 3 ngày > 0.025 %/8h, và ngược lại cho short.
- Band tái cân bằng 5 %.
- Khung 4h dùng cùng chân trời lịch (nhân 6 số bar).
- Chi phí: TAKER 6 bps/phía, MAKER 2 bps/phía.

**Dữ liệu và phương pháp:**
- Klines 1h và funding BTC/SOL/DOGE từ 2022-01 đến 2026-09.
- Split: DEV 2022–24, VAL 2025, HOLDOUT 2026-01→09.
- Mỗi spec chạy hết 4 năm trong 1.2 s (1D) hoặc 8.5 s (4H).

| Spec (TAKER) | DEV SR | VAL SR | HOLDOUT SR | OOS SR / năm / DD | PSR | DSR (100 thử) | Lệnh OOS: hold trung vị, thắng, trung vị / trung bình bps |
|---|---|---|---|---|---|---|---|
| 1D LS | +0.43 | −0.47 | +0.59 | +0.03 / +0.4 % / 13 % | 0.51 | 0.04 | 288 h, 30 %, −225 / −17 |
| 1D LO | +1.14 | −0.40 | +0.14 | −0.14 / −1.3 % / 13 % | 0.42 | 0.02 | 216 h, 27 %, −223 / −130 |
| **4H LS** | +0.93 | +0.03 | **+1.39** | **+0.69 / +10.7 % / 11.7 %** | **0.82** | 0.19 | 48 h, 35 %, −65 / +126 |
| 4H LO | +1.41 | −0.20 | +1.07 | +0.39 / +3.8 % / 12 % | 0.70 | 0.10 | 36 h, 35 %, −78 / 0 |

Với chi phí MAKER, kết quả tốt hơn khoảng 0.03–0.07 SR.

**Đọc kết quả một cách trung thực:**
- **4H LS** là cấu hình duy nhất có OOS dương rõ.
  - Mỗi lệnh giữ khoảng 2 ngày. 53 % số lệnh có |lãi/lỗ| ≥ 150 bps, nên phí 4–6 bps đúng là "phần phụ".
  - Tỉ lệ thắng chỉ 35 % và trung vị là lỗ. Lãi đến từ một số ít lệnh lớn, đúng hình dạng của trend-following.
- **Không spec nào đạt DSR > 0.95.**
- **PBO** (CSCV, 10 khối) trên cả 100 chuỗi lợi nhuận ngày (96 cấu hình factor-scan + 4 spec) là **0.35**. Nghĩa là trong 35 % các lần chia, cấu hình tốt nhất in-sample lại rơi xuống nửa dưới ở out-of-sample.
- Theo đúng quy tắc của form ("chỉ DSR > 0.95 mới lên thực nghiệm"), làn trend **chỉ được chạy PAPER**.
- Spec 1D theo form yếu hơn ENS 1d (lookback 5/10/20/40/80) của vòng (aa). Thêm Donchian và vol 20 % không cải thiện kết quả.

**Tích hợp vào engine:**
- Profile mới: `trend_lane` ('off' | 'paper'), `trend_bars=('1d','4h')`, `trend_equity`, `trend_every_s=900`.
- Demo dùng `'paper'`, live dùng `'off'`.
- `Runner.trend_loop()` chạy song song với các làn vi mô:
  - cứ 15 phút lấy klines và funding công khai từ mainnet;
  - tái cân bằng một `PaperBook` cho mỗi khung, trên **bar đã đóng** và mỗi bar chỉ một lần;
  - ghi `data/trend_paper_<bar>.json/.jsonl` (đã nằm trong `.gitignore`);
  - in event `TREND … PAPER … (no order sent)`;
  - đưa trạng thái vào telemetry ở `snapshot['trend_lane']`.
- **Không gọi venue.** Có test kiểm tra không có lời gọi nào tới venue.
- Làn này chỉ chạy khi người dùng tự khởi động lại engine.

## 3. Nhiệm vụ 3: A/B ghép cặp (`tools/ab_bench.py`) và chống overfit

**Chế độ `micro`:**
- A và B chạy `engine_replay` trên cùng các khúc thời gian, mỗi khúc là một tiến trình riêng, song song theo `--jobs`.
- Biến thể đặt bằng `--a-set/--b-set` (override Profile), hoặc `--a-code/--b-code` (hai bản code, ví dụ git worktree của commit cũ).
- Đơn vị ghép cặp là PnL ròng mỗi giờ, kể cả giờ không có lệnh.

**Chế độ `trend`:** ghép cặp theo lợi nhuận ngày, chạy vài giây.

**Báo cáo đầu ra gồm:**
- hiệu trung bình và CI95 bootstrap theo khối;
- t, p, ρ;
- hiệu suất so với A/B không ghép cặp;
- số đơn vị cần để đạt power 80 %;
- PSR/DSR của chuỗi hiệu. Số lần thử N = số dòng trong `experiment_register.md` + số dòng `trial_log.jsonl` + `--prior-trials`.

**Đăng ký thử nghiệm:** `--register` ghi `docs/research_upgrade/trial_log.jsonl` và thêm một dòng `AB-n` vào register. PBO có trong `fast_eval.pbo_cscv`.

| ID | So sánh | Đơn vị | Δ (B−A), CI95 | DSR | Kết luận |
|---|---|---|---|---|---|
| AB-1 | trend 4H vs 1D spec (TAKER, OOS) | 638 ngày | +2.8 bps/ngày [+0.5, +5.2], p 0.022, ρ 0.92 (12× hiệu quả) | 0.33 | B tốt hơn nhưng **không** còn ý nghĩa sau khi phạt 160 lần thử |
| AB-2 | overlay funding bật vs tắt (4H) | 638 ngày | −0.1 bps/ngày [−0.3, +0.04] | 0.00 | không khác biệt: overlay gần như không kích hoạt |
| AB-3 | vol target 25 % vs 20 % (4H) | 638 ngày | +0.4 bps/ngày [−1.3, +2.1] | 0.01 | không khác biệt (chỉ đổi đòn bẩy) |
| AB-4 | `cvd_shield` off vs on (vi mô, 01–07/10, 28 khúc) | 168 giờ | **0**: 108/108 vị thế y hệt | — | shield **không bao giờ chặn** lệnh nào trên băng mainnet: ngưỡng ±300k/500k USD thô bị các gate dòng tiền khác làm thừa. Bản thân shield chỉ dùng CVD từ trade nên kết luận không phụ thuộc sổ lệnh tổng hợp |
| AB-5 | `early_invalidation` on vs shadow (vi mô, 06–07/10, 8 khúc) | 48 giờ | +0.0147 U/giờ [−0.0014, +0.0330], p 0.23, ρ 0.78 (4.6× hiệu quả) | 0.08 | **không khác biệt có ý nghĩa**. B −2.93 U so với A −3.64 U, tức −4.77 so với −5.92 bps/lệnh. Cần khoảng 262 giờ (~11 ngày replay) để kết luận |

**3b. Chi tiết AB-5.**
- Cùng số vị thế (41) ở mỗi nhánh. Cắt sớm giảm lỗ trung bình 1.15 bps/lệnh, nhưng CI95 vẫn chứa 0.
- Lưu ý: trên nguồn `agg`, nhánh OFI L10 của quy tắc cắt sớm bị suy biến (sổ lệnh tổng hợp), nên chỉ trigger "CVD velocity 50k USD/s" được thử.
- Kết luận: tiếp tục để `shadow`. Nếu muốn quyết, chạy AB-5 trên 01–07/10 (khoảng 75 phút) hoặc trên nguồn `rec` khi đã có dữ liệu ghi.

## 4. Một ý tưởng vi mô mới mất bao lâu để đánh giá

| Cách | Thời gian | Ghi chú |
|---|---|---|
| Demo thời gian thực | khoảng 25–50 h cho 50–100 lệnh | kèm nhiễu sàn demo |
| `ab_bench micro`, 2 ngày SOL+DOGE (8 khúc × 2 nhánh, 2 lõi) | khoảng 20 phút | 16 khúc, cặp theo giờ |
| `ab_bench micro`, 2–4 khúc 1 h nhắm sự kiện | khoảng 1–2 phút | để soi lỗi logic, không đủ cho kết luận kinh tế |
| `ab_bench trend` | 10–20 s | 4 năm, 638 ngày OOS |

## 5. Lệnh sử dụng

```
# replay một cửa sổ (aggTrades cần nằm trong --data)
python tools/engine_replay.py --source agg --data data/replay_cache/agg_zips --symbols SOLUSDT,DOGEUSDT \
    --start 2026-10-06T00:00 --end 2026-10-06T06:00 --warmup-min 60 --out run.json
# A/B tham số hoặc hai bản code, có đăng ký thử nghiệm
python tools/ab_bench.py micro --data data/replay_cache/agg_zips --days 2026-10-06..2026-10-07 --chunk-h 6 \
    --b-set early_invalidation=on --register --name "..."
git worktree add ../gt_old c5c8be7 && python tools/ab_bench.py micro ... --a-code ../gt_old
# làn trend, PBO, DSR
python tools/trend_backtest.py --data <klines+funding zips>
python tools/ab_bench.py trend --data <zips> --a-cfg '{}' --b-cfg '{"bar":"4h"}' --prior-trials 100 --register
# replay sổ lệnh thật trong tương lai
python tools/record_market.py --light          # chạy nền liên tục, rồi dùng --source rec
```

## 6. Sửa lỗi engine: đồng hồ lỗ ngày

- **Lỗi:** `day_pnl` chỉ được dựng lại khi khởi động. Engine chạy qua nửa đêm sẽ mang lỗ của hôm qua vào cầu dao −5 U của hôm nay. Ví dụ: −4.9 U lúc 23:59 khiến ngày mới chỉ còn 0.1 U trước khi bị khoá.
- **Sửa:** thêm `RiskBook.roll(today)`, được gọi trong `gates()`. Khi sang ngày mới (giờ địa phương), engine đặt lại meter và ghi event.
- Có 2 test cho bản sửa này.

## 7. Kiểm chứng

- `python -m pytest -q` trên máy người dùng: **1396 passed** (trước vòng này: 1360).
- File test mới `tests/test_replay_harness_and_trend_lane.py` có 36 test, gồm:
  - thời gian ảo không tốn thời gian thực, và đồng hồ, `datetime`, event loop được trả lại sau replay;
  - replay chạy engine thật, xác định (deterministic), không đụng ledger thật;
  - `ker`/sigma nhanh cho kết quả giống hệt bản cũ;
  - A/B verdict, deflation, trial log;
  - PBO phân biệt nhiễu với kỹ năng;
  - các thành phần trend không nhìn tương lai, vol target, overlay funding, band, phí và độ trễ, dấu funding, PaperBook;
  - `Runner.trend_loop` chỉ PAPER;
  - đồng hồ lỗ ngày qua nửa đêm.
- Có một lỗi phát hiện khi chạy trên Python 3.10 của máy người dùng: replay để lại event loop là `None`, làm hỏng `test_spread_band`. Đã sửa bằng cách khôi phục loop của bên gọi.
- Ledger thật: không ghi.

## 8. Đề xuất (chưa bật gì ngoài paper)

1. Khởi động lại demo khi bạn muốn để làn trend PAPER chạy. Theo dõi bằng mSPRT trên lợi nhuận ngày. Cần nhiều tháng mới đủ bằng chứng; không chuyển sang lệnh thật khi DSR < 0.95.
2. Mọi thay đổi vi mô từ nay đi qua `ab_bench micro --register` trước khi lên demo. Nếu không có khác biệt ghép cặp thì không bật.
3. Chạy `record_market.py --light` liên tục để có nguồn `rec` (sổ lệnh thật) cho replay maker.
4. Xem lại CVD shield: hiện nó vô dụng trên băng mainnet. Nếu giữ thì cần ngưỡng theo `usd_scale` của từng coin, và kiểm lại bằng `ab_bench`.
