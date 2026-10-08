# Vòng (x) 08/10/2026 — "Chop deadlock": adaptive KER và bracket MR, trước / sau

Form: `prompts/FORM_CLAUDE_OPUS5.5_RESOLVE_CHOP_DEADLOCK_2026-10-08.md`. Không khởi động lại engine, không gửi lệnh,
không bật live, không đổi size. Ledger gốc không bị ghi (44 dòng).

## 1. Snapshot
`snapshot_2026-10-08x/` lúc 08:44:01 UTC (15:44 Asia/Saigon), `SHA256SUMS.txt`. Engine đang chạy code `4bf6fbb` (có
funnel vòng v). Dữ liệu replay: aggTrades USD-M công khai data.binance.vision SOLUSDT/DOGEUSDT 01–07/10/2026,
checksum khớp file `.CHECKSUM` của Binance (`aggtrades_SHA256SUMS_2026-10-01_07.txt`).

## 2. Kiểm tra giả thuyết "deadlock" bằng dữ liệu
**Funnel runtime** (telemetry `scanner.funnel`, mẫu 1 Hz từ lúc khởi động lại, ≈ 23 phút/symbol):
| | SOL | DOGE |
|---|---|---|
| Mẫu (giây) | 1 379 | 1 424 |
| Swarm chưa cho phép (SWARM) | 1 100 | 1 331 |
| Swarm cho phép, churn chặn (CHURN) | 279 | 93 |
| KER chặn (bất kỳ vị trí) | 1 046 | 1 170 |
| **KER là blocker DUY NHẤT** | **3** | **1** |
| EDGE (< 30 bps) chặn | 1 373 | 1 423 |
| SCORE / VELOCITY / FLOW chặn | 1 185 / 1 033 / 1 011 | 1 374 / 1 164 / 1 253 |
| Mẫu làn MR | **0** | **0** |
| NET RR chặn | **0** | **0** |
Blocker đầu lớn nhất ("OTHER" 779/1 120) là "SCANNING · no trigger": các agent alpha nghiêng một phía nhưng không có
trigger tổng hợp — nay đặt tên `NO_TRIGGER`.

Kết luận:
- KER < 0.38 chiếm **81 %** thời gian (replay 7 ngày) — đúng như form quan sát.
- Nhưng **không có deadlock MR/net RR trong phiên này**: tín hiệu MR (|VWAP 60 s| ≥ 0.12 %, biên 20 % của range ≤ 0.40 %)
  không xuất hiện lần nào; nhãn radar "MR SCAN" chỉ là trạng thái chờ, không phải lệnh MR bị net RR chặn.
- Khi KER chặn, gần như luôn có gate khác cùng chặn (EDGE, SCORE, VELOCITY, FLOW). Bỏ hẳn KER chỉ mở được ~3 giây
  trong 23 phút cho SOL.
- Công thức của form `max(0.15, ker_lock − 0.20 × (score − 0.90))` chỉ hạ ngưỡng **tối đa 0.02** (0.38 → 0.362 ở score
  0.99): KER 0.05–0.30 vẫn khoá.

## 3. Replay hai đòn bẩy (`tools/replay_chop_lanes.py`, `replay_chop_lanes_2026-10-08x.json`)
Dev = 01–05/10, **holdout = 06–07/10 (đặt trước, không tune)**. Vào post-only, khớp khi print xuyên giá (cons) / chạm
giá (opt); TP maker 2 bps, SL taker 5 + 1 trượt, vào maker 2; thoát taker sau 300 s; 1 vị thế/symbol/làn, nghỉ 30 s.

**A. Làn MR (tín hiệu đúng như engine), holdout, fill bảo thủ:**
| Bracket | Net RR thật | Lệnh khớp | Thắng | Net / lệnh khớp [CI95] |
|---|---|---|---|---|
| Hiện tại (TP clamp 0.12–0.18, SL 0.08) | ≤ 0.875 | 101 | 32.7 % | −7.43 [−9.97, −5.76] |
| Form 0.22 / 0.10 | 1.00 | 91 | 30.8 % | −6.55 [−9.94, −3.28] |
| Form 0.24 / 0.11 | 1.05 | 90 | 30.0 % | −6.77 [−10.51, −2.88] |
| Form 0.26 / 0.12 | 1.10 | 90 | 28.9 % | −7.23 [−11.00, −3.24] |
| **0.29 / 0.10 (RR 1.389)** | **1.39** | 90 | 26.7 % | **−7.03** [−10.76, −3.42] |
| 0.31 / 0.12 (RR 1.35) | 1.35 | 89 | 28.1 % | −7.01 [−11.49, −2.04] |
Dev và mô hình opt cùng dấu (−5.9 đến −8.6 bps). RR cao hơn → tỉ lệ thắng giảm tương ứng (cần 42 % để hoà ở RR 1.39,
đạt 27 %). **Không bracket nào dương.**

**B. TREND trong chop (proxy adaptive KER: |CVD vel 10 s| ≥ 3 000 USD/s, vận tốc giá 15 s ≥ 0.025 % cùng chiều,
CVD 60 s cùng dấu), holdout, bảo thủ:**
| Dải KER | Lệnh khớp | Thắng | Net / lệnh khớp [CI95] |
|---|---|---|---|
| ≥ 0.42 (đang được phép) | 574 | 25.3 % | −7.97 [−9.28, −6.80] |
| 0.362–0.42 (công thức form mở) | 492 | 25.4 % | −8.01 [−9.42, −6.63] |
| 0.15–0.362 | 714 | 23.2 % | −7.92 [−9.09, −6.86] |
| < 0.15 | 621 | 24.5 % | −8.17 [−9.58, −6.79] |
Dải KER không làm khác kết quả: tín hiệu dòng tiền ngắn hạn này không có lợi thế sau phí ở bất kỳ dải nào. Proxy
không có phiếu swarm/OFI; vòng w đã đo trực tiếp 54 lệnh swarm ≥ 0.95 thật: biến động 5 phút trung bình −0.3 bps.

## 4. Sai số số học trong form
`(0.24 − 0.04) / (0.11 + 0.04) = 1.33` dùng phí 0.04 % cho cả hai phía. Stop là STOP_MARKET (taker): lỗ = SL + vào
maker 0.02 + taker 0.05 + trượt 0.01 = SL + 0.08 %. RR thật = 0.20 / 0.19 = **1.05**; cả dải 0.22–0.26 / 0.10–0.12
cho 1.00–1.10 < 1.30 → vẫn bị gate chặn. TP nhỏ nhất đạt 1.35 với SL 0.10 là **0.29 %** (với SL 0.12: 0.31 %); MR
vào ở |dev| ≥ 0.12 % nên TP 0.29 % là vượt qua VWAP thêm ~0.17 %, không còn là "hồi về VWAP".

## 5. Bản vá
| ID | Thay đổi | Mặc định demo / live | Bật thật |
|---|---|---|---|
| X1 | `Engine.adaptive_ker()` đúng công thức form; điều kiện score ≥ 0.95, CVD vel 10 s ≥ 3 000 USD/s cùng chiều, confluence 3/3; chỉ làn TREND. Shadow: KER vẫn chặn, ghi `SHADOW_KER` (≤ 1 dòng/20 s/symbol) kèm `other_blockers` — cho biết nếu mở KER thì lệnh có thật sự đi tới submit không. | **shadow** / off | `adaptive_ker='on'` |
| X2 | `mr_bracket='fixed'` → TP `mr_tp_fixed` 0.29 % / SL `mr_sl_fixed` 0.10 % (net RR 1.389 ≥ 1.35); ma trận khả thi phản ánh bracket; test chứng minh làn MR đi qua mọi gate tới khớp lệnh. | **vwap** (cũ; MR bị NET RR chặn) / không áp dụng (live dùng SL ngân sách) | `mr_bracket='fixed'` |
| X3 | Funnel: `NO_TRIGGER` thay "OTHER"; `combos_after_swarm` = tổ hợp blocker của các mẫu swarm đã cho phép (biết chính xác gate nào cùng chặn). | bật (chỉ đo) | — |

Ma trận khả thi: với `adaptive_ker='on'` + `mr_bracket='fixed'` cả TREND (1.94–2.34) và MR (1.389) đều khả thi và có
đường tới submit (test). Mặc định giữ shadow/vwap vì §3 cho thấy mở thêm lệnh ở cả hai làn đều lỗ ≈ 7–8 bps mỗi lệnh.

## 6. Kiểm thử
`python -m pytest -q` → **1249 passed** (trước 1222; +27 trong `tests/test_chop_deadlock_resolution.py`). `git diff
--check` sạch. Fixture cũ cô lập `adaptive_ker='off'`.

## 7. Rollback
`adaptive_ker='off'`; `mr_bracket='vwap'` (mặc định); X3 chỉ là bộ đếm.

## 8. Giới hạn và bước tiếp
- Replay không có book/queue/spread, không có phiếu swarm, mainnet ≠ demo, 7 ngày.
- Sau khi người dùng khởi động lại: `SHADOW_KER` + `combos_after_swarm` cho biết bao nhiêu lần adaptive KER thật sự
  dẫn tới submit; chỉ đề xuất bật khi markout sau phí của các dòng đó dương với CI95 > 0.
- Nút thắt thật là lợi thế tín hiệu (EDGE/SCORE chặn > 95 % thời gian; các tín hiệu proxy âm sau phí), không phải
  một khoá logic.
