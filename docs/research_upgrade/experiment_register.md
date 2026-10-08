# Experiment register

Quy ước: mỗi dòng = một cấu hình/patch đã thử hoặc dự kiến. Bằng chứng kinh tế chỉ tính trên dữ liệu ngoài mẫu theo thời gian, cùng `policy` id, có chi phí thật. Test pytest chỉ chứng minh hành vi phần mềm.

## Lịch sử chỉnh ngưỡng trên cùng mẫu (≤ 34 lệnh) — chưa từng kiểm định ngoài mẫu
Các vòng 10-07 (f)…(p) đã đổi ≥ 40 ngưỡng (KER 0.40/0.45→0.38/0.42→BTC 0.55; velocity 0.04→0.025; TP1 0.45→0.50; MR TP/SL; VPIN; ATR RR 2.5; Kelly; convex; asset brackets; eject 180 s; VFR 3/5; 85 % alt share; IOC off; τ 3 s…). Số cấu hình ~ ≥ 15 phiên bản policy. Không có holdout → mọi cải thiện từ các vòng đó là **HYPOTHESIS**.

## Vòng (q) — 07/10/2026

| ID | Loại | Thay đổi | Mặc định | Rollback | Trạng thái |
|---|---|---|---|---|---|
| Q1 | kế toán | `session.gross/fees` và `stats()` chỉ cộng dòng có cả gross và fee; đếm `rows_unknown_cost` | luôn bật | revert commit | VALIDATED (test + tái tạo 0.95817999 / 2.1141432) |
| Q2 | kế toán | `kill_switch()` đóng vị thế đang theo dõi qua `close('KILL')` → ghi ledger | luôn bật | revert | VALIDATED (test mock) |
| Q3 | instrumentation | `engine_orders.jsonl`: mỗi lần vào lệnh (FILLED/PARTIAL/UNFILLED/REJECTED/TOXIC/REFUSED), decide→send, send→ack, ack→fill, basis, policy, code | `order_log=True` | `order_log=False` | VALIDATED (phần mềm) |
| Q4 | instrumentation | Ledger schema 2: `policy`, `code`, `signal_mid`, `venue_mid`, `basis_bps`, `basis_exit_bps`, `ack_ms`, `fill_wait_ms`, `markout_bps` 1/5/30 s | luôn bật (cộng thêm trường) | revert | VALIDATED (phần mềm) |
| Q5 | sizing | `convex_sizing` False, `alt_size_boost` False, `alt_kelly_min_n` 20 | an toàn (giảm size) | đặt lại True / 5 | VALIDATED (test); không tuyên bố lợi nhuận |
| Q6 | đo | Benchmark xử lý quyết định (VM, mock) | — | — | OBSERVED: tick p50 0.78 ms, p99 1.46 ms |

## Dự kiến (chưa chạy) — mỗi cái có tiêu chí bác bỏ
| ID | Câu hỏi | Dữ liệu cần | Bác bỏ khi |
|---|---|---|---|
| E1 | Basis demo↔mainnet có làm lệch P&L demo? | ≥ 200 attempt trong order log | \|basis\| median < 2 bps và markout venue ≈ markout mainnet |
| E2 | Adverse selection của GTX entry | markout 1/5/30 s của ≥ 100 lệnh khớp cùng policy | markout 5 s trung bình ≥ 0 (không bị chọn ngược) |
| E3 | edge_bps có dự báo outcome? | edge_bps + markout 30 s/net của ≥ 200 attempt; chia theo thời gian, holdout 30 % cuối | hệ số/AUC trên holdout không khác ngẫu nhiên (CI chứa 0 / 0.5) |
| E4 | GTX vs IOC trên cùng cơ hội | replay hftbacktest với latency từ Q3 + L2 ghi lại | chênh net/attempt không vượt CI khi stress phí +2 bps, latency ×2 |
| E5 | Gate NO EDGE (f* ≤ 0 → không vào) | quyết định chính sách + shadow để tránh khoá chết | — |

## Vòng (r) — 08/10/2026: vòng đời lệnh thoát / stop

| ID | Loại | Thay đổi | Mặc định | Rollback | Trạng thái |
|---|---|---|---|---|---|
| R1 | ngữ nghĩa | `stop_covers_entry`, `protection` (EXCHANGE STOP >= ENTRY / SOFT TARGET ONLY / HARD SL ONLY); MARIN ghi "BE TARGET (soft)" thay vì "BREAKEVEN LOCKED" khi stop sàn chưa qua entry; ledger `stop_covered_entry` | luôn bật | revert | VALIDATED (test) |
| R2 | thoát lệnh | bounded chase: SOFT-*/FLOW/REGIME maker exit → reduce-only MARKET khi giá qua mốc ≥ 0.03 % hoặc ≥ 5 s mà vẫn ở/qua mốc | **OFF (shadow)**: ghi `shadow_escalate` | `soft_exit_escalate=True` | REJECTED cho bật mặc định (replay dưới) |
| R3 | stop API | `err_txt` (mã + nghĩa chuẩn, không chép text sàn); `stop_events` (bước, mã, trigger, bid/ask venue, mark, mid tín hiệu, naked_ms); back-off ×2, bỏ sau 3 lần (`ratchet_max_fail`) | luôn bật | `ratchet_max_fail` lớn | VALIDATED (test) |
| R4 | kế toán | ADOPT tìm fill tạo vị thế (`entry_start_ms`) → phí vào có trong ledger; không đối soát được → `cost_complete=False` | luôn bật | revert | VALIDATED (test) |
| R5 | KILL | `kill_switch` idempotent + reconcile từng symbol (flatten lại 1 lần, event NOT FLAT) | luôn bật | revert | VALIDATED (test) |

### R2 — replay (tools/replay_soft_exit.py), dữ liệu mainnet bookTicker
Câu hỏi: khi BE (MFE ≥ 0.12 %) đã arm và giá quay về mốc entry+0.06 %, chính sách nào cho net tốt hơn? Sự kiện tổng hợp: long+short tại mid mỗi 20 s; phí maker 2 / taker 5 bps; SL 0.35 %. Không có dữ liệu trade → hai mô hình fill maker: bảo thủ (chỉ khi giá xuyên qua lệnh) và lạc quan (thêm: đứng đầu touch ≥ 1 s). Không phải DOGE, không phải venue demo.

In-sample (SOL/LINK/BNB 09–10/09/2026, 164 sự kiện): A chase mean −0.02 / +0.79 bps (bảo thủ / lạc quan), p5 −6.1 / −2.8, worst −16.1; C exchange stop mean −1.79, p5 −3.6, worst −9.9. Lưới (mọi cấu hình đều được ghi, không chọn lọc) — chênh B − A theo cặp, CI95 block bootstrap:

| slip, deadline | B−A bảo thủ (bps) | B−A lạc quan (bps) | p5 A → B (bảo thủ) | worst A → B | số lần escalate (bảo thủ / lạc quan) |
|---|---|---|---|---|---|
| slip=0.03 dl=5.0 | -1.14 [-1.771, -0.711] | -0.36 [-0.885, -0.002] | -6.09 → -4.91 | -16.12 → -13.26 | 92 / 32 |
| slip=0.03 dl=30.0 | -0.60 [-1.246, -0.138] | -0.36 [-0.885, -0.002] | -6.09 → -4.91 | -16.12 → -13.26 | 60 / 32 |
| slip=0.03 dl=inf | -0.50 [-1.06, -0.09] | -0.36 [-0.885, -0.002] | -6.09 → -4.91 | -16.12 → -13.26 | 56 / 32 |
| slip=0.06 dl=5.0 | -1.10 [-1.695, -0.729] | +0.03 [-0.118, 0.117] | -6.09 → -7.42 | -16.12 → -14.94 | 76 / 8 |
| slip=0.06 dl=30.0 | -0.42 [-0.97, -0.118] | +0.03 [-0.118, 0.117] | -6.09 → -7.49 | -16.12 → -14.94 | 32 / 8 |
| slip=0.06 dl=inf | -0.33 [-0.837, -0.058] | +0.03 [-0.118, 0.117] | -6.09 → -7.49 | -16.12 → -14.94 | 28 / 8 |
| slip=0.1 dl=5.0 | -1.28 [-1.773, -0.877] | -0.14 [-0.327, 0.0] | -6.09 → -7.49 | -16.12 → -19.95 | 75 / 6 |
| slip=0.1 dl=30.0 | -0.24 [-0.483, -0.021] | -0.12 [-0.304, 0.0] | -6.09 → -6.09 | -16.12 → -19.95 | 11 / 5 |
| slip=0.1 dl=inf | -0.14 [-0.329, 0.0] | -0.12 [-0.304, 0.0] | -6.09 → -6.09 | -16.12 → -19.95 | 6 / 5 |
| slip=inf dl=5.0 | -1.17 [-1.71, -0.803] | -0.02 [-0.07, 0.0] | -6.09 → -7.49 | -16.12 → -16.12 | 70 / 1 |
| slip=inf dl=30.0 | -0.10 [-0.291, 0.007] | +0.00 [0.0, 0.0] | -6.09 → -6.09 | -16.12 → -16.12 | 5 / 0 |

Holdout chưa dùng (SOL+LINK 08/09/2026, 672 sự kiện, cấu hình 0.03 %/5 s cố định trước): B − A = −1.06 bps [−1.41, −0.61] (bảo thủ), −0.42 [−0.58, −0.25] (lạc quan); worst −15.5 → −8.5; p5 bảo thủ −7.4 → −5.0 nhưng lạc quan −3.6 → −5.0. C stop: mean −1.50, p5 −2.65, worst −8.5.

Kết luận: chặn chase **giảm đuôi lỗ nhưng tốn 0.4–1.1 bps/lần thoát trung bình** ở cả in-sample và holdout; không cấu hình nào cải thiện net trung bình. Theo tiêu chí chấp nhận → giữ shadow. Quyết định đổi mean lấy tail là của người dùng (chi phí đã đo ở trên). Ca DOGE (−17.5 bps net) nằm ở đuôi, tệ hơn worst của A trong replay (DOGE tick = 1.1 bps, book demo).

## Vòng (s) — 08/10/2026: MR anti-fade, net RR, re-peg, super-conviction size

| ID | Loại | Thay đổi | Mặc định | Rollback | Bằng chứng / trạng thái |
|---|---|---|---|---|---|
| S1 | gate | MR không fade dòng tiền: cấm MR BUY khi OFI < −0.30 hoặc CVD60 < −$100k×scale (SELL đối xứng); OFI chưa có → cấm | bật | `mr_flow_gate=False` | Chặn #39 (OFI −0.93) và #34 (SELL, OFI +0.89). Chỉ 2 lệnh MR trong lịch sử (cả 2 lỗ, tổng −0.0453 U) → giảm rủi ro, không phải bằng chứng alpha. |
| S2 | gate | Net RR (bracket_plan rr_net, phí thật) ≥ 1.30 ở churn gate; kiểm lại trên plan đã sizing trong execute (cả live) | bật | `net_rr_min=0` | Không chặn lệnh SWARM nào trong lịch sử (min 1.46). MR tối đa 0.875 → **làn MR tắt hẳn** với bracket hiện tại. **Live**: TP 0.50 % vs SL ~0.89 % từ ngân sách 0.05 U → rr 0.47 → mọi lệnh live bị từ chối. |
| S3 | đo | Fast re-peg: đã có (0.5 s, trần 0.15 %); thêm `touch_last`, `touch_qty_last` vào order log | — | — | 25/32 UNFILLED không re-peg → timeout do hàng đợi, không do drift. Không đổi tham số. |
| S4 | sizing | Vote ≥ 0.95 VÀ OFI ≥ 0.80 cùng chiều → SOL 0.50 / DOGE 450 (trần $140, risk/margin portfolio vẫn áp) | bật demo, tắt live | `super_size=False` | **Chưa có bằng chứng**: 4 lệnh alt lịch sử thoả quy tắc (#26, #27, #38, #40) net +0.0443 U; quy tắc được đặt sau khi thấy #40 → selection bias. Ghi `super_size` trong ledger để đánh giá ngoài mẫu. |

## Vòng (t) — 08/10/2026: đói cơ hội

| ID | Loại | Thay đổi | Mặc định | Rollback | Bằng chứng / trạng thái |
|---|---|---|---|---|---|
| T1 | đo (bug) | VFR lấy spread của feed tín hiệu (thị trường đo ATR), không lấy book testnet | luôn bật | revert | VALIDATED (test). Bỏ đóng băng giả SOL. |
| T2 | sizing (bug) | BTC probe = size nhỏ nhất venue chấp nhận nếu rủi ro ≤ 15 % ngân sách | bật | `btc_probe_min_tradable=False` | VALIDATED (test). BTC demo giao dịch được lại ở 0.0006 BTC. |
| T3 | gate | DRIFT ACCEL: bỏ DRIFT lock cho phía CVD60 khi \|CVD60\| ≥ 3×\|CVD3m\| và ≥ $2M×scale, size 0.5× | bật demo, tắt live | `drift_accel=False` | **Chưa có bằng chứng** (L2 ghi lại không có trade/CVD). Ledger ghi `drift_probe` để đánh giá. |
| T4 | chiến lược | Chop micro-scalper: KER < 0.40, spread ≤ 1.5 bps, phía theo OFI, GTX tại touch, TP 0.08 % maker, SL 0.12 %, BE sau 90 s, size 0.5× | **shadow** (ghi `SHADOW_SCALP`, không đặt lệnh); live off | `chop_scalper='on'` | **REJECTED để bật**: replay (tools/replay_chop_scalper.py) in-sample 09–10/09 SOL/LINK/BNB: −7.46 bps/lệnh [−9.53, −5.73] (fill bảo thủ), −6.82 [−8.53, −5.69] (lạc quan); holdout 08/09 SOL+LINK: −7.11 [−7.77, −6.29] / −6.31 [−6.69, −5.89]; tỉ lệ thắng 14–26 %. Bracket có RR sau phí 0.20 (cần thắng 83 %). Replay test cả hai phía, không có bộ lọc OFI (không có dữ liệu trade). |

## Vòng (u) — 08/10/2026: cách ly BTC, size SOL/DOGE, eject 120 s

| ID | Loại | Thay đổi | Mặc định | Rollback | Bằng chứng / trạng thái |
|---|---|---|---|---|---|
| U1 | phạm vi | BTC observe-only: feed, swarm, telemetry chạy; không vào lệnh (kể cả MANUAL), không shadow scalper; vị thế có sẵn vẫn được quản lý/đóng | bật (demo) | `observe_only=()` | Giảm rủi ro, không cần bằng chứng lợi nhuận. BTC AUTO −1.43 U / 27 lệnh qua nhiều phiên bản. |
| U2 | sizing | SOL ≥ 0.40 / DOGE ≥ 300 cho lệnh trend thường; super size 0.80 SOL / 500 DOGE (từ 0.50 / 450) | bật demo, tắt live | `alt_size_floor=False`, SUPER_SIZE cũ | **Chưa có bằng chứng**: SOL 10 lệnh, CI chứa 0. Rủi ro tối đa ~0.40 U (SOL) + ~0.26 U (DOGE) < 1.0 U. |
| U3 | thoát lệnh | ≥ 120 s, MFE < 0.08 %, \|vận tốc 15 s\| < 0.025 % → maker exit tại touch (EJECT-FAST); không áp cho SCALP/TP1 đã khớp | bật demo, tắt live | `eject_fast=False` | Replay (tools/replay_eject.py, vào lệnh ngẫu nhiên, mainnet 09–10/09, 611 vị thế): B − A = −0.05 bps [−1.20, +1.73] (bảo thủ), +0.60 [−0.60, +2.36] (lạc quan) → **không khác biệt đo được** về cơ học thoát. Giá trị thật phụ thuộc lệnh chậm có kỳ vọng âm hay không — chưa đủ dữ liệu (1 lệnh alt thoả điều kiện: #36). |

## Vòng (v) — 08/10/2026: funnel, khả thi lane, GTX, net/cơ hội

| ID | Loại | Giả thuyết / thay đổi | Dữ liệu · split | Tiêu chí | Kết quả | Trạng thái / rollback |
|---|---|---|---|---|---|---|
| V1 | đo | Đếm mọi blocker (không chỉ blocker đầu) 1 Hz/engine, theo lane; phân loại hard/cost/statistical/config | runtime (chưa có) | — | Code + test; chưa có số liệu runtime | bật (chỉ đo); revert `exec_funnel` |
| V2 | khả thi | Lane có đầu vào hợp lệ qua hết gate? | config thật | rr_net max ≥ 1.30 | TREND demo SOL 1.94–2.34, DOGE 2.09–2.39 ✓; MR max 0.875 ✗; live 0.474 ✗; SCALP shadow (gate không áp, rr 0.20) | báo cáo, không đổi gate |
| V3 | bug | Dashboard `undefined`: producer (engine) ≠ schema UI (micro_scanner) | code | test schema | VALIDATED (test) | revert app.js |
| V4 | lịch | UI focus làm chậm REST của SOL/DOGE khi BTC observer được xem | code | weight ≤ lịch cũ; quyết định không phụ thuộc focus | Quyết định không phụ thuộc focus (decide 0.2 s, WS). REST: SOL/DOGE 3/4 s → 2/3 s khi BTC focus; ước tính weight ≤ lịch cũ ở mọi trạng thái | bật; revert idle() |
| V5 | bug lifecycle | Exception sau POST GTX để lệnh mồ côi | test tái hiện | lệnh không còn NEW; fill muộn được ADOPT + stop | 2 test fail trên code cũ, pass sau vá | bật; revert lời gọi |
| V6 | replay | Cách đặt lệnh nào tốt nhất theo net/cơ hội (không theo fill-rate)? 8 policy × 3 fill model × 2 bộ cơ hội | mainnet bookTicker SOL/LINK/BNB; dev 09–10/09, **holdout 08/09 đặt trước**; 1 lần chạy, không tune | Δ net30/cơ hội vs PEG_T3, CI95 block bootstrap ghép cặp loại 0 trên clear+prop | Mọi policy < 0; SKIP > mọi policy (+1.87 [1.52, 2.18]); TTL 5/10 s, IOC tệ hơn; JOIN_T3 > PEG_T3 +0.23 [0.11, 0.37] (clear), front không phân biệt | **WAIT**. Ứng viên `peg=False` chưa áp dụng (book mainnet ≠ demo, lợi ích ≪ phí) |
| V7 | sizing | Floor/super size SOL/DOGE (vòng u) có cải thiện không? | ledger | — | Không đánh giá được: 0 lệnh SOL/DOGE khớp dưới policy u/v (11 attempt 0 khớp) | giữ nguyên, không tăng thêm |

Số cấu hình đã thử ở V6: 8 policy × 3 mô hình × 2 bộ cơ hội, một lần chạy; không có tham số nào được chọn trên holdout.

## Vòng (w) — 08/10/2026: Super-Alpha crossing, TTL động, ratchet 2, retry mạng

| ID | Loại | Giả thuyết / thay đổi | Dữ liệu | Tiêu chí | Kết quả | Trạng thái / rollback |
|---|---|---|---|---|---|---|
| W1 | thực thi | Vote ≥ 0.95 → crossing taker sau 2 s GTX bắt sóng 50–80 bps, net > 0 | 54 attempt thật vote ≥ 0.95 (SOL/DOGE 07–08/10), aggTrades demo + mainnet, 1 lần chạy, không tune | net/cơ hội CI95 > 0 trên demo và mainnet | Demo −7.08 [−12.87, −1.71], mainnet −9.64 [−15.69, −3.73]; MFE 5 phút max 28 bps, 0/54 ≥ 50 bps | **REJECTED để bật**; code + shadow (`SHADOW_CROSS`); `super_cross='on'` để bật |
| W2 | thực thi | TTL động (≤ 15 s) tăng net | như W1 | như W1 | Demo −1.43 [−4.10, 0.35] vs GTX 3 s +0.12; mainnet −5.73 vs −3.99 | shadow (`SHADOW_DYN`) |
| W3 | thoát | Ratchet 2 ở MFE 0.25 % → stop fee-true + 0.05 %; tầng 1 fee-true theo phí vào | test | stop-out tại giá stop ≥ +5 bps net | VALIDATED (test, cơ học); kinh tế chưa đo (4/54 đạt MFE 25 bps) | bật demo; `ratchet2=False` |
| W4 | hạ tầng | −1008/503 không làm mất lệnh hay tạo lệnh trùng | fake client | gửi lại có giới hạn, IOC không gửi lại khi không rõ | VALIDATED (test) | bật |

## Vòng (x) — 08/10/2026: chop deadlock

| ID | Loại | Giả thuyết / thay đổi | Dữ liệu | Tiêu chí | Kết quả | Trạng thái / rollback |
|---|---|---|---|---|---|---|
| X1 | gate | Adaptive KER (công thức form) mở TREND trong chop có lời | funnel runtime 23 phút; replay aggTrades SOL+DOGE 01–05/10 dev, 06–07/10 holdout, 1 lần chạy | net/lệnh CI95 > 0 trên holdout | KER là blocker duy nhất 3/1 379 mẫu SOL; công thức hạ ngưỡng ≤ 0.02; proxy dải 0.362–0.42: −8.01 bps [−9.42, −6.63], mọi dải KER âm như nhau | **shadow** (`SHADOW_KER`); `adaptive_ker='on'` |
| X2 | bracket | MR TP 0.22–0.26 / SL 0.10–0.12 đạt RR ≥ 1.35 và có lời | như X1 | như X1 | RR thật 1.00–1.10 (form tính sai phí stop); 0.29/0.10 (RR 1.39): −7.03 bps [−10.76, −3.42], thắng 27 % < 42 % hoà vốn; mọi biến thể âm | code `mr_bracket='fixed'`, mặc định `vwap` |
| X3 | đo | Tên blocker & tổ hợp sau swarm | runtime | — | `NO_TRIGGER` là blocker đầu lớn nhất | bật |
