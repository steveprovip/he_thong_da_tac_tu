# Báo cáo trước / sau — vòng (q), 07/10/2026

## Trước
- Ledger 34 dòng DEMO: net −1.22563407 U, PF 0.511, expectancy −0.0360 U/lệnh (CI95 block [−0.088, +0.017]), phí/gross 2.21, max DD −1.95 U. Dashboard báo gross 0.8885 (sai: gộp dòng TEST thiếu dữ liệu).
- Không ghi: lệnh không khớp/bị huỷ/bị từ chối, độ trễ từng giai đoạn, basis demo↔mainnet, markout, phiên bản policy/code. Lệnh bị KILL không vào ledger.
- Phiên 13:56–14:29 UTC: size SOL nâng từ 7 lệnh (0.45→1.06) + convex 1.5×.

## Sau (đã đo)
- Kế toán: gross/phí chỉ từ dòng có đủ dữ liệu (0.95817999 / 2.11414320), đếm dòng UNKNOWN; `tools/ledger_audit.py` tái tạo baseline có CI.
- Mỗi attempt vào lệnh → `data/engine_orders.jsonl`; ledger schema 2 có policy/code/basis/stage latency/markout; KILL ghi ledger.
- Sizing: không còn tăng size từ swarm score chưa hiệu chuẩn hoặc < 20 lệnh.
- Độ trễ xử lý (VM, mock feed, 200–2000 mẫu): `on_message(depth)` p50 0.008 ms; `features()` 0.30 / 0.63 ms (p50/p99); `signals+evaluate` 0.04 / 0.08 ms; `Engine.tick` không đặt lệnh 0.78 / 1.46 ms. So với ledger `latency_ms` p50 1286 ms → phần lớn là chờ khớp GTX + REST, không phải tính toán. Mạng (send→ack) sẽ có số khi chạy demo với Q3.
- Checks: `python -m pytest -q` 1052 passed, 0 skipped (trước vòng: 1033). Ledger thật không đổi (md5 427fd193…).

## Không tuyên bố
- Không có cải thiện net nào được chứng minh. Các patch là kế toán/đo lường/giảm rủi ro. Bằng chứng kinh tế cần E1–E3 trong experiment register.
