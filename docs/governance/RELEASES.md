# RELEASES.md — Cổng phê duyệt của con người

Tài liệu này định nghĩa **cái gì mở khoá cái gì**. Không có mục nào ở đây được
mở bằng suy luận của AI, bằng một phiên chạy đẹp, hay bằng một bài test xanh.
Mỗi cổng cần một con người bấm, và mỗi cổng đều **thu hồi được**.

## G0 — SHADOW / RESEARCH *(đang ở đây)*

- **Trạng thái:** mặc định, không cần phê duyệt.
- **Được phép:** quét, xếp hạng, tính phong bì, replay off-policy, viết brief.
- **Không được phép:** mọi lệnh thật, trên mọi sàn.
- **Cơ chế:** `--mode shadow`, và `two_leg_capital_gate` trả `SHADOW`.

## G1 — TESTNET có vốn

- **Điều kiện cứng (máy tự kiểm, không thương lượng):**
  - Equity đo được trên **cả hai** venue ≥ **10.0 USDT**.
  - `--notional` mỗi cặp ≥ **10.0 USDT** và ≤ equity.
  - `python -m pytest -q` xanh toàn bộ.
- **Điều kiện mềm (người đánh giá):** ít nhất 7 ngày R1–R4 chạy liên tục ở G0
  không có `ROUTINE_RECOVERY`, và brief R4 `status = RECONCILED` mọi ngày.
- **Ai mở:** chủ sở hữu repo.
- **Cách mở:** `--mode testnet`. Không cần cờ xác nhận.

## G2 — MAINNET

- **Điều kiện cứng:** tất cả của G1, cộng:
  - `--arm`
  - `--confirm "I UNDERSTAND SPOT PERP MAINNET RISK"`
  - Khoá API **tách rời** cho Spot và Futures, đều đã bật quyền giao dịch.
  - Futures ở chế độ **one-way, single-asset, ISOLATED**, đòn bẩy ≤ 2.
  - Không có vị thế/lệnh mở nào chưa được quy trách nhiệm trên các symbol đích.
- **Ai mở:** chủ sở hữu repo, bằng tay, mỗi phiên.
- **Không tự động hoá được:** hai cờ trên **phải** gõ tay. Không đọc từ `.env`,
  không nhớ giữa các phiên, không suy ra từ `ARM_LIVE_EXECUTION`.

## Cổng thu hồi (máy tự bấm, không cần người)

Hệ thống tự hạ cấp về G0 và `halted = True` khi:

| Sự kiện | Nguồn |
|---|---|
| Equity tụt dưới 10.0 USDT | `CarryRuntime.enforce_capital_gate` |
| Margin ratio > 65% và không nạp bù được trong trần 25% notional | `watchdog_once` |
| Stop phía sàn không còn ACTIVE | `watchdog_once` |
| Ví spot không khớp (`wallet_status != RECONCILED`) | `reconcile_accounts` |
| Slot routine còn `EXECUTING` sau khởi động lại | `recover_routines` |
| Ba kỳ funding âm liên tiếp · basis > 1.5% · quá horizon | `scan` |

## Những thứ **không** phải là cổng

Nói rõ để không ai phải tranh luận lại:

- **Nâng đòn bẩy để chạm notional tối thiểu.** Đòn bẩy nhân lỗ mỗi tick, không
  thu nhỏ notional sàn khớp. Đây là cách lách, không phải cách mở khoá.
- **Hạ 76.8% / 70.0%.** Hai số này là hằng số chính sách; `tests/test_policy_lock.py`
  ghim chúng.
- **Giãn nhịp watchdog 30 giây** để tiết kiệm rate limit.
- **Một chuỗi ngày lãi trong shadow.** Shadow giả định lệnh maker luôn khớp;
  nó không chứng minh được khả năng khớp lệnh.

## Nhật ký phát hành

| Ngày | Cổng | Ai | Ghi chú |
|---|---|---|---|
| 2026-09-21 | G0 | — | Chuyển vai sang funding-carry; R1–R4 + `routine_scheduler` vào hệ thống. E = 3.85 USDT, khoá ở RESEARCH_ONLY. |
