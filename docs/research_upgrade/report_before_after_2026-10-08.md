# Báo cáo trước / sau — vòng (r), 08/10/2026

## Trước
- DOGE #37: `be_locked=True`, UI "BREAKEVEN LOCKED", nhưng stop sàn 0.08835 < entry 0.08880; thoát maker bám giá 6 lần → net −0.04525 U.
- RATCHET SOL thất bại 3 lần, chỉ ghi "BinanceError"; không biết mã, không đo thời gian không có stop, retry mỗi 5 s vô hạn.
- ADOPT: phí vào có thể không vào ledger (#35 chỉ có 1 chiều phí).
- KILL 14:29 không đóng vị thế (nguyên nhân UNKNOWN); KILL không kiểm tra lại.

## Sau (đo được)
- Tái hiện chính xác #37 trên mock (net −0.04524931). Cùng đường giá với bounded chase bật: −0.0149 U (taker). Mặc định vẫn shadow vì replay (R2) cho thấy bounded chase tốn trung bình 0.4–1.1 bps/lần thoát (holdout 672 sự kiện), dù đuôi lỗ tốt hơn (worst −15.5 → −8.5 bps).
- Báo cáo bảo vệ đúng: "BE TARGET (soft) · CHƯA BẢO VỆ (SL sàn x)" cho tới khi stop sàn qua entry.
- Stop API: mã + nghĩa chuẩn, bối cảnh, `naked_ms`; back-off 5 → 10 s, bỏ sau 3 lần.
- ADOPT đối soát fill tạo vị thế; KILL idempotent + reconcile.
- `ledger_audit`: 37 dòng, net −1.24659858, PF 0.511; net theo chi phí đầy đủ (bỏ TEST, ADOPT thiếu phí) −1.20038202.
- Checks: `python -m pytest -q` **1070 passed, 0 skipped** (trước 1052). Ledger thật không bị ghi.

## Chưa biết
- Mã lỗi thật của RATCHET demo (giả thuyết −2021 / CONTRACT_PRICE) — sẽ có ở lần chạy demo tới.
- Bounded chase trên DOGE/venue demo: `shadow_escalate` trong ledger cho phép so counterfactual.
- Không có thay đổi nào chứng minh tăng net EV.
