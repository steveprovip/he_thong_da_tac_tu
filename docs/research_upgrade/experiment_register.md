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

## Vòng (y) — 08/10/2026: định cỡ vị thế 10 %

| ID | Loại | Giả thuyết / thay đổi | Dữ liệu | Kết quả | Trạng thái / rollback |
|---|---|---|---|---|---|
| Y1 | sizing | 10 % notional tăng lợi nhuận mà không cháy | ledger 46 lệnh (SOL 15), Monte Carlo 10 000 × 200 lệnh | Không cháy (DD p99 1.2 %) nhưng PnL trung vị −19 U; SOL −1.98 bps/lệnh [−14.6, +9.1]; Kelly f* < 0 | **không áp dụng** |
| Y2 | sizing | 10 % vốn rủi ro tại stop | như Y1 | Cần 23x; P(DD ≥ 50 %) 95 % (null 75 %) | **REJECTED** |
| Y3 | thanh khoản | 4.35 SOL / 5 500 DOGE trượt giá | depth20 + aggTrades 30 phút demo/mainnet | Trượt 0 bps theo độ sâu hiển thị; khớp maker demo 3 s ≤ 10 % (cận trên) | ràng buộc là khớp, không phải impact |
| Y4 | sizing | Dynamic Conviction Sizing 3/5/10 % | code + test | score chưa hiệu chuẩn (≥ 0.95: −1.0 bps) | **shadow** (`cs_*` trên order row); `conviction_sizing='on'` |

## Vòng (z) — 08/10/2026: thất thoát phí khi thoát

| ID | Loại | Giả thuyết / thay đổi | Dữ liệu | Kết quả | Trạng thái / rollback |
|---|---|---|---|---|---|
| Z1 | thoát | Pre-stop maker (tier mềm ≥ stop sàn + 0.04 %) giảm phí taker | aggTrades 01–07/10, holdout 06–07, 859 vị thế, 1 lần chạy + 2 độ nhạy | +0.25 bps [0.12, 0.38] (xuyên) / +0.38 (chạm); ca ratchet-stop +4.7 / +7.0 bps | **bật**; `pre_stop_maker=False` |
| Z2 | thoát | TP1 50 % ở +0.30 % | như Z1 | +0.10 [0.05, 0.16] | bật demo; `tp1_scaled_pct=None` |
| Z3 | sizing | Trần rủi ro 5 U / notional $1 000 demo | ledger (vòng y) | Kỳ vọng mỗi lệnh chưa dương (−3.6 bps) → tăng size phóng đại kết quả | bật theo yêu cầu người dùng, kèm Z4 |
| Z4 | rủi ro | Cầu dao conviction: −5 U / 30 lệnh | test | VALIDATED (cơ học) | bật |

## Vòng (z2) — 08/10/2026: alpha gating & drawdown

| ID | Loại | Giả thuyết | Dữ liệu | Kết quả | Trạng thái |
|---|---|---|---|---|---|
| G1 | gate | CVD shield ±300 k / ±500 k giảm thua | ledger #50–55 + aggTrades replay 01–07/10 (holdout 06–07) | Ledger: thua lớn đều cùng chiều vĩ mô; replay trung tính (holdout −6.42 vs −6.56) | bật theo yêu cầu (vô hại) |
| G2 | gate | Đồng pha 3 tầng | như G1 | Xấu hơn: −7.27 vs −6.56 holdout, −7.16 vs −6.60 dev | shadow |
| G3 | sizing | Trần theo chế độ $170/$225/$315, cứng $350 | test | Giảm rủi ro (cơ học) | bật |
| G5 | thoát | Cắt sớm ≤ 45 s | như G1 | Không nhất quán (dev +0.09, holdout −0.51) | shadow |
| G6 | lỗi | Thoát maker tệ hơn stop sàn (#51), nhãn BE trên lệnh lỗ (#55) | ledger | Sửa + test | bật |

## Vòng (aa) — 08/10/2026: vòng nghiên cứu nhanh & chuyển khung thời gian

| ID | Loại | Giả thuyết | Dữ liệu | Kết quả | Trạng thái |
|---|---|---|---|---|---|
| AA1 | đánh giá | Ledger hiện tại đủ để kết luận edge | ledger 55 dòng, `tools/edge_check.py` | 9 hash code / 55 lệnh (~6 lệnh mỗi bản); toàn bộ −3.48 bps se 2.0; SPRT(−3 vs +2) = BAD, mSPRT vs 0 p 0.29 | quan sát |
| AA2 | horizon | Trend 1h/4h vượt phí | klines 1h + funding BTC/SOL/DOGE 2022-01..2026-09, `tools/factor_scan.py`, VAL 2025 + HOLDOUT 2026 | 1h median SR VAL −0.56 (7 % > 0); ENS 1h LS OOS −9.9 bps/RT; 4h ENS LS −7.3 bps/RT | bác bỏ (taker) |
| AA3 | horizon | Trend 1d vượt phí | như AA2; ENS 1d LS đăng ký trước | OOS SR +0.21, +24.6 bps/RT net, PSR 0.61; ENS_VT 1d LO SR +0.42 PSR 0.72; B&H SR −0.19; DSR(DEV, 96 cấu hình) ≤ 0.47 | giả thuyết có triển vọng, chưa xác nhận |
| AA4 | lọc | Funding 3 ngày > 0.03 %/8h chặn long | như AA2 | Không kích hoạt lần nào trong OOS | vô hiệu ở 2025–26 |
| AA5 | dữ liệu | Recorder L2 + aggTrades + forceOrder | smoke 20 s mainnet | Đủ stream sau khi tách route /public & /market; ~20 MB/h SOL (đầy đủ) | công cụ sẵn sàng, không chạy nền |

## Vòng (bb) — 09/10/2026: replay engine thật, A/B ghép cặp, làn trend 1D/4H

| ID | Loại | Giả thuyết | Dữ liệu | Kết quả | Trạng thái |
|---|---|---|---|---|---|
| BB1 | đánh giá | Bản `3ee16f29d2` đã cải thiện edge | ledger 23 lệnh của hash này | Hash chỉ thêm công cụ (engine không đổi); −2.29 bps se 3.3, net −1.58 U, SPRT UNDECIDED | bác bỏ diễn giải |
| BB2 | baseline | Engine hiện tại trên băng mainnet 01–07/10 | `tools/engine_replay.py` agg (sổ lệnh tổng hợp), 28 khúc 6 h | 108 vị thế, −5.41 bps/lệnh, WR 20 %, −8.74 U / 5 000 U | quan sát (công cụ ghép cặp) |
| BB3 | trend | Spec form 1D/4H (TSMOM 5/20/60 + Donchian 20, vol 20 %, funding overlay) | klines 1h + funding 2022-01..2026-09, `tools/trend_backtest.py` | 4H LS OOS SR +0.69, PSR 0.82, DSR 0.19; 1D LS OOS +0.03; PBO(100 cấu hình) 0.35 | giả thuyết → PAPER |
| BB4 | lỗi | `day_pnl` không reset qua nửa đêm | code + test | `RiskBook.roll()` trong gates() | sửa |
| AB-1 | trend | trend 4H vs 1D spec (TAKER, OOS 2025-01..2026-09): B={"bar": "4h"} vs A={} | 638 day net return | Δ +0.000283 CI95 [+4.678e-05, +0.0005208], DSR 0.33 | B better, NOT significant after deflation |
| AB-2 | trend | funding overlay on vs off (4H, TAKER, OOS): B={"bar": "4h"} vs A={"bar": "4h", "funding_hot": 1.0} | 638 day net return | Δ -9.524e-06 CI95 [-3.007e-05, +4.204e-06], DSR 0.00 | NO DIFFERENCE DETECTED |
| AB-3 | trend | vol target 25 vs 20 % (4H, TAKER, OOS): B={"bar": "4h", "vol_target": 0.25} vs A={"bar": "4h"} | 638 day net return | Δ +4.014e-05 CI95 [-0.0001286, +0.0002097], DSR 0.01 | NO DIFFERENCE DETECTED |
| AB-4 | micro | cvd_shield off vs on (agg Oct 1-7, 28 chunks): B=cvd_shield=off vs A= | 168 hour net PnL (U) | Δ +0 CI95 [+0, +0], DSR 0.01 | NO DIFFERENCE DETECTED |
| AB-5 | micro | early_invalidation on vs shadow (agg Oct 6-7): B=early_invalidation=on vs A= | 48 hour net PnL (U) | Δ +0.01471 CI95 [-0.001413, +0.03304], DSR 0.08 | NO DIFFERENCE DETECTED |

## Vòng (cc) — 09/10/2026: tự học, dreaming, nạp nóng, điều phối vốn

| ID | Loại | Giả thuyết | Dữ liệu | Kết quả | Trạng thái |
|---|---|---|---|---|---|
| CC1 | learner | Trọng số agent vào thẳng phiếu bầu cải thiện kết quả | replay liên tục 01–07/10 (agg) | Hạ trọng số → JEV fast-path → tier 10 %: notional $205 vs $145, −8.90 vs −8.51 U | bác bỏ → chỉ được phủ quyết |
| CC2 | autonomy | Learner (chỉ phủ quyết) + bandit + dreaming + router giảm lỗ | như CC1 | 88 vs 109 lệnh, −6.87 vs −8.51 U, bps/lệnh −5.31 vs −5.36; Δ +0.0097 U/h p 0.006, DSR 0.68 | demo 'on' (giảm lỗ do giao dịch ít đi, không có alpha) |
| CC3 | dreaming | Ledger thật có bẫy | 79 lệnh, dry-run thư mục tạm | CONVICTION: tier 10 % 18 lệnh −5.7 bps vs tier nhỏ −2.2 → trần $150 | chỉ thị tự động khi restart |
| CC4 | trend | Chandelier 3 ATR cho 4H LS | OOS 2025-01..2026-09 | Δ −2.4 bps/ngày [−7.8, +2.7] (tổng +18.7 % → +3.5 %) | mặc định tắt |
| CC5 | lỗi | Lệnh #76 NO_STOP −49 bps (−1.54 U) không ghi mã lỗi | ledger | `stop_reject` (loại + mã) + bẫy NO_STOP | sửa |
| AB-6 | trend | trend 4H chandelier 3 ATR vs none (TAKER, OOS): B={"bar": "4h", "trail_atr": 3.0} vs A={"bar": "4h"} | 638 day net return | Δ -0.0002378 CI95 [-0.0007781, +0.0002745], DSR 0.00 | NO DIFFERENCE DETECTED |
| AB-7 | micro | autonomy on v2 vs off (agg Oct 1-7, continuous week): B=autonomy=on v2 (continuous week) vs A=autonomy=off (continuous week) | 168 hour net PnL (U) | Δ +0.009712 CI95 [+0.004531, +0.01551], DSR 0.68 | B better, NOT significant after deflation |

## Vòng (dd) — 09/10/2026: định thời khớp lệnh & kích hoạt trend 4H

| ID | Loại | Giả thuyết | Dữ liệu | Kết quả | Trạng thái |
|---|---|---|---|---|---|
| DD1 | kiểm tra | "Gross dương 7/8, biên độ trend 150–300 bps" | ledger 8 lệnh gần nhất; 133 lệnh trend OOS | Gross 6/8 dương, trung vị +1.8 bps < phí 4; trend trung vị −59, TB +67 bps, p75 +41 | sửa tiền đề |
| DD2 | trend | Brackets form (TP1 1.35 %, SL 1.5 ATR, chandelier/Donchian mid) | 4H 2022–2026, OOS 2025-01..2026-09 | OOS SR +0.63 → −0.48 / −0.54; SL thảm hoạ 8 ATR +0.59 | brackets tắt, stop 8 ATR bật |
| DD3 | khớp lệnh | Maker / swarm-timed rẻ hơn taker cho làn trend | 261 sự kiện, aggTrades mainnet ±15/5 phút | MAKER −1.20 bps [−1.84, −0.48]; SWARM kém maker +0.87 [0.07, 1.64]; SR làn 0.59 → 0.60 | trend_exec='maker' |
| DD4 | vận hành | Tắt scalp vi mô độc lập, bật trend 4H trên demo | — | micro_standalone=False, trend_lane='active' ($150–300, stop 8 ATR), live không đổi | bật demo (DSR 0.19 — theo yêu cầu người dùng) |
| AB-8 | trend-exec | trend exec MAKER vs TAKER (through fill, 261 events): B=MAKER_through vs A=TAKER_through | 261 event, -cost bps | Δ +1.196 CI95 [+0.4801, +1.846], DSR 0.79 | B better, NOT significant after deflation |
| AB-9 | trend-exec | trend exec SWARM-timed vs plain MAKER (through, 261 events): B=SWARM_through vs A=MAKER_through | 261 event, -cost bps | Δ -0.8717 CI95 [-1.644, -0.06835], DSR 0.00 | B WORSE (significant after deflation) |
| AB-10 | trend | trend brackets of the 10-09 dd form vs signal exits (OOS): B=+TP1 1.35% + SL 1.5ATR + chandelier 3ATR vs A=4H LS signal exits | 638 day net return | Δ -0.0003032 CI95 [-0.0008768, +0.0002576], DSR 0.00 | NO DIFFERENCE DETECTED |
| EE1 | trend | Regime gate G6 (KER10>0.40 & ADX14>25 + vol>1.2×SMA20 + CVD) + chop exit + lock L1 + sizing 10 %/1 % | demo: trend_regime='trend_only', trend_confirm, trend_chop_exit, trend_lock_pct=2.0, capital_allocation_pct=0.10 | demo: trend_regime='off', trend_confirm=False, trend_chop_exit=False, trend_lock_pct=None, capital_allocation_pct=None, trend_notional_max=300 | DEV-selected; OOS SR 0.83 / DSR 0.30 (< 0.95): demo only, theo yêu cầu |
| AB-11 | trend | G6_G4+chop_exit/none/legacy vs V0_BASE/none/legacy (OOS 2025-01..2026-09) | 638 day net return | Δ +0.21 bps/day CI95 [-3.56, +4.06], SR +0.77, DD 7.6 %, DSR 0.27 (148 trials) | NO DIFFERENCE DETECTED |
| AB-12 | trend | G6_G4+chop_exit/L1_chand3.5@2%/legacy vs V0_BASE/none/legacy (OOS 2025-01..2026-09) | 638 day net return | Δ +0.03 bps/day CI95 [-4.38, +4.52], SR +0.92, DD 8.3 %, DSR 0.34 (148 trials) | NO DIFFERENCE DETECTED |
| AB-13 | trend | G4_trend+confirm/none/legacy vs V0_BASE/none/legacy (OOS 2025-01..2026-09) | 638 day net return | Δ +0.70 bps/day CI95 [-0.42, +1.80], SR +0.72, DD 11.1 %, DSR 0.25 (148 trials) | NO DIFFERENCE DETECTED |
| AB-14 | trend | G6_G4+chop_exit/L1_chand3.5@2%/S10 vs V0_BASE/none/S10 (OOS 2025-01..2026-09) | 638 day net return | Δ +0.07 bps/day CI95 [-3.04, +3.33], SR +0.83, DD 5.9 %, DSR 0.30 (148 trials) | NO DIFFERENCE DETECTED |
| FF1 | trend | Break-even stop at +2 % (P1) + conviction 1H/4H/1D size C3 (<= 30 %, <= 2 % risk) + BTC shield shadow | demo: trend_be_pct=0.3, trend_conv_alloc_pct=0.30, trend_conv_risk_pct=0.02, trend_btc_align='shadow', trend_notional_max=1500 | demo: trend_be_pct=None, trend_conv_alloc_pct=None, trend_btc_align='off', trend_notional_max=600 | OOS SR 1.06 / DSR 0.41 (< 0.95): demo only |
| FF2 | network | fetch_klines/fetch_funding timeout 12 s, retry 0.5/1/2 s, kline cache, spot-proxy cold start (no volume) | on (all profiles) | git revert | tests only; fapi.binance.vision / fapi1-3 not API mirrors |
| AB-15 | trend | A1 vs B0 = G6+L1+S10 (OOS 2025-01..2026-09) | 638 day net return | Δ -0.37 bps/day CI95 [-0.87, +0.10], SR +0.62 (B0 +0.83), DD 6.7 %, DSR 0.20 (159 trials) | NO DIFFERENCE DETECTED |
| AB-16 | trend | A2E vs B0 = G6+L1+S10 (OOS 2025-01..2026-09) | 638 day net return | Δ -0.05 bps/day CI95 [-0.48, +0.36], SR +0.78 (B0 +0.83), DD 6.0 %, DSR 0.27 (159 trials) | NO DIFFERENCE DETECTED |
| AB-17 | trend | P1 vs B0 = G6+L1+S10 (OOS 2025-01..2026-09) | 638 day net return | Δ -0.19 bps/day CI95 [-1.01, +0.51], SR +0.91 (B0 +0.83), DD 5.0 %, DSR 0.33 (159 trials) | NO DIFFERENCE DETECTED |
| AB-18 | trend | P2 vs B0 = G6+L1+S10 (OOS 2025-01..2026-09) | 638 day net return | Δ +0.11 bps/day CI95 [-0.69, +0.96], SR +0.84 (B0 +0.83), DD 6.2 %, DSR 0.29 (159 trials) | NO DIFFERENCE DETECTED |
| AB-19 | trend | C3 vs B0 = G6+L1+S10 (OOS 2025-01..2026-09) | 638 day net return | Δ +1.00 bps/day CI95 [-0.62, +2.91], SR +0.87 (B0 +0.83), DD 8.5 %, DSR 0.31 (159 trials) | NO DIFFERENCE DETECTED |
| AB-20 | trend | FINAL vs B0 = G6+L1+S10 (OOS 2025-01..2026-09) | 638 day net return | Δ +1.08 bps/day CI95 [-0.35, +2.70], SR +1.06 (B0 +0.83), DD 7.5 %, DSR 0.41 (159 trials) | NO DIFFERENCE DETECTED |
| HF1 | micro | Lane 2 sub-minute burst scalper (core/burst_lane.py), shadow on demo, active gated by config/burst_verdict.json | demo: burst_lane='shadow'; live 'off' | demo burst_lane='off' | replay 10-10: every variant negative -> SHADOW |
| HF2 | research | tools/burst_scalper_replay.py (aggTrades 09-01..10-08, SOL/DOGE USDT+USDC, through/touch fills, lat 0.25/0.5 s) + tools/burst_lane_xcheck.py (live lane code vs replay) | — | — | lane shadow = replay (SOL 168 vs 167 trades, -8.04 vs -7.85 bps) |
| HF3 | research | tools/fee_survey.py: USDC-M spreads + signed commissionRate (your account) | — | — | 0 % maker NOT confirmed (third-party: promo to 2026-02) |
