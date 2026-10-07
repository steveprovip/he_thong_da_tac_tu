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
