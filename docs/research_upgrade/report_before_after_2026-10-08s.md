# Báo cáo trước / sau — vòng (s), 08/10/2026

## Trước
- Làn MR được mua/bán ngược dòng tiền: #39 MR BUY DOGE ở OFI −0.929, rr_net 0.634 → SL 50 s, −0.04001 U (100 % lỗ của phiên).
- Không có gate theo reward:risk sau phí: MR luôn có rr_net 0.63–0.88.
- Order log không cho biết lệnh GTX hết hạn vì giá chạy đi hay vì xếp hàng.

## Sau
- `msig.mr_flow_veto` + `mr_flow_gate`: #39 bị chặn ("MR FLOW BUY vs OFI −0.93"); với OFI trung tính nó vẫn bị chặn bởi net RR ("NET RR 0.63 < 1.3, hoà vốn 61 %").
- `net_rr_min=1.30` ở churn gate và trên plan đã sizing trong execute (`plan_rr`). Làn MR không thể đạt 1.30 với TP ≤ 0.18 % / SL 0.08 % → làn MR thực tế tắt. Profile live hiện tại có rr 0.47 → bị từ chối.
- Re-peg: đã re-quote trong < 1.5 s (test); order log thêm `touch_last`/`touch_qty_last`.
- Super-conviction: vote ≥ 0.95 + OFI ≥ 0.80 → 0.50 SOL / 450 DOGE ở demo (test: 0.50 SOL, risk < 0.85 U). Live tắt.
- Checks: `python -m pytest -q` **1102 passed, 0 skipped** (trước 1070). Ledger 40 dòng không bị ghi.

## Kinh tế
- Phản thực tế trên ledger: S1/S2 chặn đúng 2 lệnh MR (tiết kiệm 0.0453 U trên 40 lệnh) và không chặn lệnh trend nào. Đây là đếm lại trên mẫu đã dùng để thiết kế gate — không phải ngoài mẫu.
- S4 tăng rủi ro mỗi lệnh (tới ~0.25 U) dựa trên 4 lệnh lịch sử; chưa có bằng chứng ngoài mẫu.
