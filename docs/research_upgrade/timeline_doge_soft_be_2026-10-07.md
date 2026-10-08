# Tái hiện DOGE #37 SOFT-BE và RATCHET SOL — 07/10/2026 (UTC; Asia/Saigon = +7)

Nguồn: `data/engine_trades.jsonl` #35–#37, `data/engine_orders.jsonl` (12 dòng), sự kiện trong `data/telemetry_state.json` (persist 16:02 UTC), code `f6ca5a7` (policy DOGE `ad6d902372`). DEMO, venue BINANCE DEMO (USD-M testnet book).

## DOGE BUY 291 @0.08880 (AUTO, SWARM 80 %)
| UTC | Sự kiện | Ghi chú |
|---|---|---|
| 16:01:04 | GTX BUY 291 @0.08879 UNFILLED 3 s → huỷ | order log: ack 138 ms |
| 16:01:09 | Re-peg 0.08880 → FILLED; SL sàn 0.08835, TP1 0.08991 | ack 151.6 ms, ack→fill 2992 ms, basis −10.7 bps (demo dưới mainnet) |
| 16:01:17 | T1 MICRO-LOCK +0.113 % → mức thoát 0.08884 | chỉ là mục tiêu phần mềm, `mbe_locked=True` |
| 16:01:18 | T2 FEE-SHIELD +0.141 % → mức thoát 0.08886 | `be_locked=True`, **SL trên sàn vẫn 0.08835**; ratchet cần MFE ≥ 0.15 % → không chạy |
| 16:01:42 | mid ≤ 0.08886 → SOFT EXIT SOFT-BE: post-only tại touch, re-peg 0.5 s, không market | không có floor, không deadline |
| 16:01:42–16:02:26 | giá giảm; lệnh bán bám ask đi xuống, 6 lần re-peg | không ai mua lại ở các mức trên |
| 16:02:26 | khớp maker trung bình 0.08868 | gross −0.03492, fee 0.01033, net **−0.04525** U (−13.5 bps gross, −17.5 bps net) |

Nguyên nhân xác nhận được (OBSERVED, tái hiện chính xác trong `tests/test_soft_exit_lifecycle.py::test_doge_37_reproduced_without_the_bound`, net −0.04524931):
1. `be_locked` được đặt khi một **mục tiêu mềm** được tính, không phải khi sàn có stop. UI/agent row ghi "BREAKEVEN LOCKED" trong khi stop sàn 0.08835 < entry. → lỗi ngữ nghĩa/báo cáo, đã sửa.
2. Lệnh thoát SOFT-* là maker bám touch không giới hạn: đây là **thiết kế** (tiết kiệm phí), không phải lỗi invariant. Có thể biến "hoà vốn" thành lỗ tới mức hard SL. Replay cho thấy chặn nó tốn tiền trung bình (xem experiment register R2), nên mặc định giữ hành vi cũ + ghi shadow.

## SOL SHORT 0.61 @116.01 (vị thế phiên 14:26 bị KILL 14:29, ADOPT lại)
| UTC | Sự kiện |
|---|---|
| 14:29:02 | Dashboard KILL (code cũ 46dcdf9): vị thế **không được đóng** (lý do UNKNOWN, không có stdout log) |
| 15:14:46 | Khởi động lại → MARIN ADOPT SHORT 0.61 @116.01, stop 116.42 (giữ), MFE +0.220 % |
| 15:14:46 / :52 / :57 | RATCHET (stop BUY → entry −0.08 % = 115.91) thất bại 3 lần "failed after cancel (BinanceError)" → khôi phục 116.42. **Mã lỗi bị bỏ** (code cũ chỉ ghi tên lớp). |
| 15:14:50 | T2b PROFIT LOCK → mức thoát 115.83 |
| 15:15:08 | SOFT-PROFIT khớp maker, net ghi +0.0234543 U |

- Mỗi lần thất bại: huỷ stop cũ → đặt mới lỗi → đặt lại cũ ⇒ có một khoảng **không có stop** (không đo được ở code cũ).
- HYPOTHESIS cho mã lỗi: −2021 "Order would immediately trigger" ([error codes](https://developers.binance.com/docs/derivatives/usds-margined-futures/error-code)) vì stop dùng `workingType=CONTRACT_PRICE` (giá khớp cuối của testnet) trong khi MFE/book lấy từ book của venue. Chưa kiểm chứng — từ nay mã, bước, trigger, bid/ask venue, mark và thời gian không có stop được ghi vào `stop_events`.
- Kế toán ADOPT: phí ghi 0.0141457 = đúng **một** chiều maker (0.61×116.01×2 bps) ⇒ phí vào lệnh nhiều khả năng bị bỏ do cửa sổ fill bắt đầu sau lúc khớp vào. Net đúng ước tính ≈ +0.0093 U. Dòng #35 được `ledger_audit` đánh dấu `cost_incomplete`.
