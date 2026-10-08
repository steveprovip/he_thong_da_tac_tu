# Báo cáo trước / sau — vòng (u), 08/10/2026

## Trước
- BTC vẫn được vào lệnh (probe size tối thiểu từ vòng t): #41 −0.0468 U.
- SOL size thường ~0.30 (parity 0.15 U), DOGE ~290; super 0.50 SOL / 450 DOGE.
- Eject: 180 s, MFE < 0.06 %, BE floor rồi touch sau 360 s.

## Sau
- BTC observe-only: không lệnh nào (AUTO, MANUAL, scalper); radar "OBSERVER"; vị thế BTC còn sót vẫn được MARIN quản lý và đóng.
- SOL ≥ 0.40, DOGE ≥ 300 (trend thường); super 0.80 SOL / 500 DOGE; probe DRIFT = 0.5× sau floor.
- EJECT-FAST: 120 s khi MFE < 0.08 % và vận tốc 15 s yếu.
- Checks: `python -m pytest -q` **1158 passed, 0 skipped** (trước 1134). Ledger 41 dòng không bị ghi.

## Kinh tế
- Bỏ BTC khỏi 41 lệnh cũ = +0.1709 U (cùng mẫu, nhìn lại). Tăng size SOL/DOGE nhân cả lãi lẫn lỗ; 10 lệnh SOL không đủ để biết kỳ vọng dương.
- Eject 120 s: replay trung tính về cơ học.
