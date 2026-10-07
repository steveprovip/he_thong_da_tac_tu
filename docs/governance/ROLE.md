# ROLE.md — GrokTOPUS với tư cách **Autonomous Funding-Carry Harvester**

> Trạng thái: **RESEARCH / SHADOW**. Vốn hiện tại E = **3.85 USDT** < ngưỡng
> hai chân **10.0 USDT**, nên mọi routine dưới đây chạy ở chế độ quan sát.
> Xem `RELEASES.md` để biết điều kiện mở khoá.

## 1. Vì sao đổi vai

Scalping 1 phút đã **chết trên chính bộ số của hệ thống**, không phải vì thiếu
alpha mà vì số học phí:

| Hạng mục | Đo được | Hệ quả |
|---|---|---|
| Phí Taker vòng | 5 bps | ~1.5 cent mỗi lần chạm SL trên 3.85 USDT |
| ATR quan sát | 52 bps | biên động thật |
| Trần SL mà vốn cho phép | 20.82 bps | **SL nằm bên trong nhiễu** |

ATR 52 bps > trần SL 20.82 bps nghĩa là stop bị quét bởi nhiễu thường, không
phải bởi tín hiệu sai. Không có ngưỡng đồng thuận nào sửa được điều đó. Cách
duy nhất còn lại là chuyển sang nguồn lợi nhuận **không phụ thuộc hướng giá**:
funding carry hai chân (spot mua — perp bán), nơi thu nhập đến từ khoản funding
mà phe short được trả, còn rủi ro hướng giá bị hedge triệt tiêu.

## 2. Bốn routine, tám agent

Không agent nào bị bỏ đi, không agent nào đổi quyền. Vai trò được ánh xạ sang
bài toán carry:

| Routine | Agent | Việc phải làm | Tiêu chí cứng |
|---|---|---|---|
| **R1 `funding-scan`** | TIDAL + NORO | Xếp hạng funding toàn thị trường | horizon 7–30 ngày · ≥ 21 lần settle · OI > 20M USDT · APR dự phóng > 15% |
| **R2 `envelope-size`** | OKAPI + RUNE | Dựng phong bì vốn | spot tiền mặt · isolated margin · L ≤ 2 · basis ≤ 1.5% · margin ratio ≤ 65% · **notional hai chân ≥ 10.0 USDT** |
| **R3 `stage-pair`** | ZEPHR + VESKA | Đặt lệnh maker Post-Only GTX | AuthToken TTL 5s · đối soát hai chân khi khớp một phần · net edge **G − C > 2C ⇔ G > 3C** |
| **R4 `reconcile-brief`** | LUMEN + MARIN | Chốt sổ hằng ngày 07:00 Asia/Saigon | `PnL = ΔV_spot + ΔV_perp + Σ funding − Σ fees` |

## 3. Những gì **không** đổi

Đây là các hằng số chính sách, không phải tham số hiệu năng:

- Ngưỡng thực thi **76.8%**, phủ quyết RUNE **70.0%** — `core/consensus_engine.py`.
- **VESKA là cổng xuất vốn duy nhất**; AuthToken bám theo intent, dùng một lần, TTL 5s.
- **Watchdog bảo vệ RUNE/MARIN chạy liên tục 30 giây**, độc lập hoàn toàn với
  nhịp routine. `--routine-period` là núm thông lượng; bảo vệ sàn thì không.
  `tests/test_routine_scheduler.py::test_routine_interval_never_touches_the_30s_protection_watchdog`
  khoá điều này lại.
- Trần vốn đơn chân `core/capital_gate.py` (B_hard 3.85, N_cap 0.5775) giữ nguyên.

## 4. Tính idempotent — điều kiện sống còn

Một nhân viên chạy không người trông có đúng một lỗi đáng sợ: **làm hai lần**.
Trên carry hai chân, một lệnh vào bị phát lại là một chân trần.

`core/routine_scheduler.py` khoá mỗi lần chạy bằng

```
H(mode, routine, symbol, period)  →  sha256      (định danh DÒNG công việc)
slot = H(key : window_index)                     (định danh MỘT lần chạy)
```

và một slot đi qua đúng một chiều:

```
PENDING → EXECUTING → RECONCILED → DONE
```

Không có cạnh `EXECUTING → PENDING`. Nếu tiến trình chết giữa chừng, slot ở lại
`EXECUTING`, **không** được claim lại, và `recover()` trả nó về để đối soát hai
chân trước khi làm bất cứ việc gì khác. Mọi chuyển trạng thái được ghi xuống đĩa
(temp + replace) *trước* khi trả về cho caller.

## 5. Ranh giới tự chủ

Được phép tự làm: quét, xếp hạng, tính phong bì, mô phỏng, đối soát, viết brief.
**Không** được tự làm: nâng đòn bẩy, hạ ngưỡng, bỏ qua veto, hay coi 3.85 USDT
là đủ cho hai chân. Những việc đó cần cổng người thật trong `RELEASES.md`.
