# Vòng (v) 08/10/2026 — Funnel, tính khả thi của lane, GTX không khớp, lợi nhuận ròng sau phí

Form: `prompts/FORM_CLAUDE_KHOP_LENH_NET_EV_2026-10-08.md`. Phạm vi: điều tra + bản vá + kiểm chứng offline.
Không khởi chạy/restart engine, không gửi lệnh, không bật live, không đổi size/đòn bẩy, không bật chiến lược mới.
Engine demo đang chạy vẫn dùng code cũ (đã nạp trước khi sửa) cho tới khi người dùng tự khởi động lại.

## 1. Snapshot (nhất quán, không sửa file gốc)
| Mục | Giá trị |
|---|---|
| Code | HEAD `df6ed6e` + bản vá chưa commit của vòng này |
| Snapshot 1 | `snapshot_2026-10-08v/` (13:05 Asia/Saigon): orders 404 dòng, ledger 41 dòng |
| Snapshot 2 (dùng cho số liệu dưới đây) | `snapshot_2026-10-08v/latest/` lúc 06:25 UTC = 13:25 Asia/Saigon, `SHA256SUMS.txt` |
| Ledger | 41 dòng, sha256 `d86279d3…` (không đổi từ 05:07:50 UTC), net ghi nhận **−1.32906026 U**, 13 thắng |
| Orders | 480 dòng = 403 SHADOW_SCALP + 48 UNFILLED + 23 REFUSED + 3 PARTIAL + 3 FILLED |
| Mode / policy phiên hiện tại | demo, SOL `a417601a8a`, DOGE `556a902e45` (từ 12:28) |

Báo cáo tái lập: `python tools/execution_funnel_report.py --orders docs/research_upgrade/snapshot_2026-10-08v/latest/engine_orders.jsonl --ledger docs/research_upgrade/snapshot_2026-10-08v/latest/engine_trades.jsonl --since 1791437237 --json docs/research_upgrade/execution_funnel_2026-10-08v.json`.

## 2. Vì sao ít lệnh — kết luận có bằng chứng
1. **Phiên hiện tại không đói tín hiệu; nút thắt là khớp lệnh trên demo.** Từ 12:27:17 tới 13:23 (0.91 h): **11 attempt GTX thật** tới sàn (DOGE 6, SOL 5), **0 khớp**; 267 dòng SHADOW_SCALP (không phải lệnh). Toàn bộ log: 54 attempt gửi sàn, 6 khớp (3 FILLED + 3 PARTIAL) = **11.1 %**.
2. **Queue/thanh khoản demo.** 17 attempt có `touch_qty_last`. SOL/DOGE: 4 lần lệnh của mình gần như là toàn bộ touch (`ALONE`: SOL 0.80 tại touch 0.80/0.85, DOGE 300/500) mà vẫn không ai khớp trong 3 s → thiếu taker trên book testnet, không phải xếp hàng; 11 lần đứng sau khối lớn (DOGE 6.8M–140M, SOL 135–675), không khớp. BTC: 1 ALONE khớp, 1 BEHIND không khớp. `touch_qty_last` là khối lượng tổng hợp, có thể gồm lệnh của mình; không phải queue-ahead.
3. **Basis demo↔mainnet**: SOL p05/p50/p95 = −10.9/−4.3/+18.9 bps, DOGE −15.1/+5.1/+13.0 bps → tín hiệu mainnet đặt giá trên một book khác.
4. **BTC**: observe-only (cấu hình chủ động, vòng u) → 0 entry là đúng thiết kế; 23 REFUSED cũ là BTC micro-probe dưới sàn sàn.
5. **MR lane**: bật nhưng **bất khả thi** (rr_net tối đa 0.875 < 1.30). **Live**: rr 0.474 < 1.30 → mọi lệnh live bị từ chối. Cả hai là vấn đề cấu hình/khả thi, không sửa được bằng phiếu đồng thuận.
6. **Scanner focus**: decide_loop chạy 0.2 s cho mọi engine trên WS feed, gate đọc feed → focus BTC **không chặn quyết định** SOL/DOGE. Nhưng nhịp REST (book 3 s / sync 4 s) bị gắn với symbol UI đang xem → đã tách (mục 4).
7. Funnel đầy đủ theo blocker (mọi blocker, không chỉ blocker đầu) chưa có số liệu runtime: instrumentation mới chỉ thu khi engine được khởi động lại.

## 3. Ma trận khả thi lane (config thật, `Engine.lane_feasibility()`)
| Mode · symbol | TREND rr_net (min–max) | MR rr_net max | SCALP | Gate |
|---|---|---|---|---|
| demo SOL | 1.942–2.338 → khả thi | 0.875 → **bất khả thi** | SHADOW, rr 0.20 (hoà vốn cần thắng 83 %) | 1.30 |
| demo DOGE | 2.086–2.385 → khả thi | 0.875 → **bất khả thi** | SHADOW | 1.30 |
| demo BTC | OBSERVER (1.478–2.278) | OBSERVER | OBSERVER | — |
| live DOGE @0.0874 | 0.474 → **bất khả thi** (SL 0.890 % từ ngân sách 0.05 U ở 59 DOGE) | 0.113 | OFF | 1.30 |

Fixture chứng minh lane TREND có đường tới submit với **gate production demo** (VFR, NET RR, KER, flow, velocity, score, edge): `test_trend_lane_has_a_path_through_every_production_gate`, `test_trend_lane_reaches_submit_and_fill_with_production_execution_checks`.

Lựa chọn cho MR (chưa áp dụng — không hạ gate, không nâng TP tuỳ ý):
| Phương án | rr_net | Điều kiện để xét |
|---|---|---|
| Giữ nguyên (MR bị NET RR chặn) | ≤ 0.875 | hiện trạng, an toàn |
| TP ≥ 0.248 % với SL 0.08 % | ≥ 1.30 | cần replay tỉ lệ hồi về VWAP ≥ 0.25 %: **không làm được** với bookTicker (không có trade/VWAP) |
| Hạ gate riêng MR về ~0.9 | 0.875 | chỉ khi win-rate MR ≥ 53 % trên holdout; lịch sử 0/2 → không có cơ sở |
| Tắt MR tường minh | — | làm rõ funnel; khuyến nghị nếu không thu được dữ liệu trade |

Live: SL do ngân sách 0.05 U sinh ra 0.89 % > TP 0.50 % → muốn khả thi phải đổi bracket live hoặc ngân sách — **quyết định của người dùng**, không thay đổi ở vòng này.

## 4. Bản vá (code)
| ID | File | Thay đổi | Loại |
|---|---|---|---|
| V1 | `core/exec_funnel.py` (mới), `core/engine.py` | `churn_checks()` đánh giá **mọi** churn gate (không early-return), `churn_gate()` trả blocker đầu như cũ (thông điệp giữ nguyên). `Funnel` 1 Hz/engine: stage (HARD/SWARM/CHURN/YIELD/OBSERVER/SUBMIT), blocker đầu / mọi blocker / blocker duy nhất theo lane, outcome từ order log. Phân loại hard / cost / statistical / config. | đo, không đổi quyết định |
| V2 | `core/engine.py` | `lane_feasibility()` + `scanner_view()` publish `funnel`, `feasibility` và bộ đếm `intents/fills/partial/cancels/refused/shadow/fill_rate/trades_per_hour` (phạm vi phiên). | đo |
| V3 | `web_ui/app.js` | Hiển thị "—" khi chưa biết, không còn `undefined`; không ép 0. Nguyên nhân: UI đọc `scanner.intents/fills/cancels` của `research/micro_scanner.py`, engine không bao giờ publish các khoá đó. | bug schema |
| V4 | `core/engine.py` Runner | Nhịp REST theo **quyền giao dịch**, không theo UI focus: observer phẳng (3.0, 4.0 s); engine được phép giao dịch, phẳng, không focus (2.0, 3.0); focus (1.0, 1.2); có vị thế/lệnh (0.3, 1.2); khi engine khác đang có vị thế/lệnh, engine phẳng còn lại về (3.0, 4.0) để giữ ngân sách weight. Focus mặc định = symbol đầu tiên được phép giao dịch (SOL), không phải BTC observer. Ước tính weight ≤ lịch cũ trong mọi trạng thái (test). | lịch chạy |
| V5 | `core/engine.py` `_execute` | **Bug tái hiện được**: exception sau khi đặt GTX (POST không chắc chắn, lỗi REST khi poll) trả về mà lệnh entry vẫn NEW trên sàn → có thể khớp muộn thành vị thế "EXTERNAL". Nay `cancel_orphan_entry()` huỷ (benign nếu không tồn tại), đọc khối lượng đã khớp cộng dồn; phần đã khớp để `sync()` → ADOPT đặt stop. Không bao giờ đặt lại lệnh. Hai test fail trên code cũ, pass sau vá. | lifecycle |
| V6 | `tools/replay_gtx_placement.py`, `tools/execution_funnel_report.py` | Replay đặt lệnh trên mainnet bookTicker; báo cáo funnel/timeline/latency/P&L tái lập. | offline |

Không đổi: VESKA/AuthToken, RUNE, OKAPI, giới hạn exposure, stop-side protection, size, TTL, peg, net_rr_min, shadow scalper.

Kiểm toán race cancel/replace (form §B): peg huỷ → đọc lệnh → nếu đã khớp một phần thì **giữ phần đó, không đặt lại full qty** (test `test_partial_fill_racing_a_peg_cancel_is_kept_and_never_reposted_full`: 1 lần POST, vị thế 0.001, stop đặt trên phần khớp). Không tái hiện được lỗi "re-post full qty" → không gọi đó là bug. Hạn chế còn lại: phần chưa khớp không được đặt tiếp (bỏ cơ hội, an toàn). Tài liệu Binance: Cancel Order trả `executedQty`; Modify Order "reordered in the match queue" → sửa giá không giữ ưu tiên; code dùng huỷ + đặt mới (cũng mất ưu tiên).

## 5. GTX: replay đặt lệnh trên mọi cơ hội (offline, mainnet)
`python tools/replay_gtx_placement.py --every 15` → `replay_gtx_placement_2026-10-08v.json`. Dữ liệu SOL/LINK/BNB 08–10/09 (không có DOGE, không dùng BTC 1.6 GB). Dev = 09–10/09 (≈ 2.9 h/symbol), **holdout = 08/09 (3.94 h/symbol), đặt trước, không tune**. ACK 0.15 s; GTX bị từ chối nếu cắt qua book tại ACK. Net_30 = side × (mid(t0+30 s) − giá khớp) − phí vào − 2 bps thoát maker chung; lệnh không khớp = 0. Ba mô hình fill: `clear` (chỉ khi mức giá bị quét — cận dưới), `prop` (queue tuyến tính kiểu ProbQueue), `front` (mọi giảm khối lượng ở trước mình — cận trên).

Holdout 08/09, tín hiệu proxy momentum (|Δmid 15 s| ≥ 3 bps), n = 899 cơ hội:
| Policy | Fill (clear / front) | Chờ p50 | IS bps | mk5 bps | **Net_30 / cơ hội** (clear) [CI95] | Δ vs PEG_T3 (clear) [CI95] |
|---|---|---|---|---|---|---|
| SKIP | 0 | — | — | — | **0.000** | +1.870 [1.515, 2.176] |
| JOIN_T3 | 0.320 / 0.516 | 1.3 s | −0.23 | −1.44 | −1.643 [−1.927, −1.316] | +0.227 [0.112, 0.373] |
| PEG_T3 (hiện tại) | 0.365 / 0.467 | 1.5 s | +0.21 | −1.38 | −1.870 [−2.176, −1.515] | 0 |
| JOIN_T5 | 0.423 / 0.641 | 2.0 s | −0.24 | −1.48 | −2.264 | −0.394 [−0.658, −0.162] |
| JOIN_T10 | 0.570 / 0.741 | 2.6 s | −0.25 | −1.49 | −3.081 | −1.211 [−1.454, −0.987] |
| IMPROVE_T3 | 0.322 | 1.3 s | −0.23 | −1.43 | −1.645 | +0.225 [0.111, 0.372] |
| IOC (shadow) | 1.000 | 0.15 s | +1.29 | −1.29 | −8.495 [−8.883, −8.013] | −6.625 |

Kết luận (VALIDATED trong giới hạn dữ liệu này; dev cho cùng hướng):
- **Mọi cách đặt lệnh đều âm sau phí trên mọi cơ hội**, cả với proxy momentum lẫn hướng ngẫu nhiên; mỗi lần khớp ≈ −5 bps (2 vào + 2 ra + chọn ngược ≈ −1 bps markout). SKIP (WAIT) trội hơn.
- **Tăng fill-rate làm net xấu hơn**: TTL 5/10 s và IOC đều tệ hơn TTL 3 s; lệnh khớp muộn là lệnh bị chọn ngược (Lehalle–Mounjid). Không kéo dài TTL, không bật IOC.
- Re-peg (PEG_T3) khớp nhiều hơn JOIN_T3 nhưng IS dương (đuổi giá) → kém hơn 0.23 bps/cơ hội với mô hình clear/prop; mô hình front: −0.08 [−0.28, 0.16] (không phân biệt). **Ứng viên**: `peg=False`. Chưa đổi mặc định: lợi ích nhỏ so với phí, đo trên book mainnet chứ không phải demo.
- IMPROVE 1 tick ≡ JOIN vì spread mainnet SOL/BNB/LINK gần như luôn 1 tick; trên demo spread rộng (SOL ~13 bps) thì khác — chưa đo được.
- Nút thắt kinh tế là **lợi thế tín hiệu < ~5 bps/khớp**, không phải cơ chế khớp. Mainnet replay khớp ~32 % trong 3 s so với demo 11 % → demo đánh giá thấp khả năng khớp nhưng cũng không đại diện chi phí chọn ngược.

Giới hạn: không có trade prints / order id → không biết queue thật; proxy tín hiệu không phải swarm thật; không DOGE; mainnet tháng 9 ≠ demo tháng 10; ACK 0.15 s cố định; thoát tại mid + 2 bps là lạc quan (thoát taker ≈ 5 bps + nửa spread).

## 6. Độ trễ (54 attempt gửi sàn)
| Chặng | p50 | p95 | p99 |
|---|---|---|---|
| decide→send (gồm 1 REST bookTicker demo trước khi báo giá) | 97.5 ms | 105.0 | 107.1 |
| send→ACK REST | 145.3 ms | 193.1 | 210.7 |
| ACK→thấy khớp (polling, n = 6) | 3 130 ms | 3 420 | 3 455 |
| tổng | 3 590 ms | 3 808 | 4 006 |
Thời gian chủ yếu là chờ hàng đợi (TTL 3 s), không phải tính toán. Thời điểm fill là lúc polling thấy, không phải `T` của sàn (chưa có user stream). Không tối ưu CPU vì không có profiling cho thấy nút thắt.

## 7. P&L theo lát (ledger 41 dòng; số nhỏ, không kết luận chiến lược)
| Lát | n | thắng | net | gross | phí |
|---|---|---|---|---|---|
| BTC | 28 | 6 | −1.4999 | +0.5766 | 2.0069 |
| SOL | 10 | 6 | +0.2522 | +0.3764 | 0.1477 |
| DOGE | 3 | 1 | −0.0813 | −0.0471 | 0.0343 |
| TREND (SWARM+JEV) | 37 | 12 | −1.2375 | +0.8975 | 2.1351 |
| MR | 2 | 0 | −0.0453 | +0.0084 | 0.0537 |
| entry GTX | 27 | 6 | −1.5527 | −0.4287 | 1.1240 |
| entry IOC | 12 | 6 | +0.2699 | +1.3346 | 1.0647 |
Gross TREND dương nhưng phí lớn hơn gross. 2 dòng có chi phí UNKNOWN (ADOPT; OPERATOR/TIME) được đếm riêng, không cộng phí = 0. Lát IOC/GTX lẫn lộn giai đoạn và policy khác nhau → không suy ra "IOC tốt hơn" (replay §5 nói ngược lại cho lệnh vào).

## 8. Hard guards vs statistical gates
- **Hard** (giữ, không nới vì ít lệnh): ZEPHR (feed stale / spread), RUNE (halt, lỗ ngày, vị thế, slot, cooldown, trần lệnh/giờ), OKAPI (filter sàn, vốn), MARIN, VESKA (AuthToken), portfolio risk/margin, stop-side protection.
- **Cost** (số học phí): VFR, NET RR.
- **Statistical** (cần ablation + holdout trước khi đổi): VPIN, DRIFT, KER, MACRO, ABSORB, FLOW, MR flow, MR score, VELOCITY, SCORE, EDGE, swarm WEAK/CONFLUENCE/WALL/REGIME.
- **Config**: OBSERVER, scanner YIELD.
Ablation từng statistical gate cần đúng tín hiệu swarm + trade prints → chưa làm được offline; funnel V1 sẽ cho tần suất "blocker duy nhất" của mỗi gate để chọn gate nào đáng ablate trước.

## 9. Kiểm thử
`python -m pytest -q` → **1188 passed** (trước 1158; +30 trong `tests/test_execution_funnel_feasibility.py`). `git diff --check` sạch. Ledger 41 dòng, mtime 05:07:50 UTC không đổi. Test xác nhận phần mềm, không xác nhận lợi nhuận.

## 10. Rollback
- V1/V2/V6: chỉ đo; bỏ bằng revert `core/exec_funnel.py` + các lời gọi (`funnel_sample`, `funnel.outcome`).
- V3: revert `web_ui/app.js` đoạn lab-tag.
- V4: khôi phục `Runner.idle()` cũ (`e is not self.engine`) và `sleep(3.0/1.0)`, `sleep(4.0/period)`.
- V5: bỏ lời gọi `cancel_orphan_entry` trong `except` (không khuyến nghị: tái hiện lệnh mồ côi).

## 11. Dữ liệu cần thu thụ động tiếp (không cần bật gì thêm)
1. Sau khi người dùng khởi động lại demo: `scanner.funnel` (blocker đầu / mọi blocker / duy nhất theo lane) ≥ 2 h → chọn 1–2 statistical gate để ablate.
2. Order log: ≥ 200 attempt dưới một policy; ghi thêm `T` từ user data stream để tách thời gian chờ hàng đợi khỏi polling.
3. Ghi trade prints (aggTrade) cùng bookTicker mainnet cho SOL/DOGE để replay queue (RiskAverse/ProbQueue) và MR (VWAP) đúng nghĩa.
