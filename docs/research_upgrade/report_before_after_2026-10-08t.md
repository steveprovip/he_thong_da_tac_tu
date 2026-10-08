# Báo cáo trước / sau — vòng (t), 08/10/2026

## Trước
- Phiên 03:26–04:12 UTC: 0 lệnh khớp. SOL bị đóng băng VFR 1.9 do spread book demo 13 bps; mọi tín hiệu BTC bị từ chối vì probe 0.0001 BTC dưới min $50; 3/3 lệnh GTX không khớp trong 3 s.

## Sau
- VFR theo spread feed tín hiệu → SOL với ATR15m 0.32 % có VFR ≈ 6.6 (không đóng băng).
- BTC probe ở size nhỏ nhất sàn nhận (0.0006 BTC trên test mock demo spec, rủi ro ≤ 0.15 U).
- DRIFT ACCEL probe 0.5× (demo).
- Chop scalper: đã cài đầy đủ (`on` chạy được trên mock) nhưng mặc định **shadow**; mỗi cơ hội được ghi vào order log để đối chiếu.
- Checks: `python -m pytest -q` **1134 passed, 0 skipped** (trước 1102). Ledger 40 dòng không bị ghi.

## Kinh tế
- Scalper theo form: replay mainnet −6.3 đến −7.5 bps/lệnh sau phí, CI < 0 ở cả in-sample và holdout. Bật nó sẽ tăng tần suất nhưng tăng lỗ.
- T1/T2 tăng số cơ hội được phép vào lệnh nhưng không có bằng chứng các lệnh đó có lãi. T3 chưa đánh giá được.
