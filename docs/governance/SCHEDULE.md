# SCHEDULE.md — Thời khoá biểu R1–R4

Nguồn sự thật là `core/routine_scheduler.py::ROUTINES`. Bảng này là bản đọc
được của cùng một cấu hình; nếu hai bên lệch nhau thì **mã đúng, tài liệu sai**.

## 1. Bảng nhịp

| | Routine | Chu kỳ (mặc định) | Chu kỳ cho phép | Cửa sổ ân hạn | Neo | Phạm vi khoá |
|---|---|---|---|---|---|---|
| R1 | `funding-scan` | 1h | 1h · 4h · 8h | 900s | 00:00 UTC | `*` (toàn thị trường) |
| R2 | `envelope-size` | 1h | 1h · 4h · 8h | 600s | 00:00 UTC | mỗi symbol |
| R3 | `stage-pair` | 1h | 1h · 4h · 8h | **300s** | 00:00 UTC | mỗi symbol |
| R4 | `reconcile-brief` | 24h | — | 3600s | **07:00 Asia/Saigon** | `*` |

Đổi nhịp R1–R3 bằng `--routine-period {3600,14400,28800}`. R4 không có núm:
nó là giờ của con người.

## 2. Vì sao neo như vậy

- **R1–R3 neo theo UTC** vì Binance settle funding trên biên UTC (00/04/08/12/16/20),
  không theo giờ địa phương. Neo theo giờ Sài Gòn sẽ làm cửa sổ quét lệch khỏi
  chính lúc funding được chốt.
- **R4 neo theo Asia/Saigon +07:00** (cố định, không DST từ 1975). 07:00 giờ Sài
  Gòn = **00:00 UTC chẵn**, nên phép tính cửa sổ không cần cơ sở dữ liệu múi giờ
  lúc chạy — một chi tiết nhỏ nhưng là khác biệt giữa "chạy được trên Windows
  không có tzdata" và "không".

## 3. Vì sao cửa sổ ân hạn ngắn dần R1 → R3

Cửa sổ là thời gian slot còn mở sau khi nó bắt đầu. Quá hạn thì **bỏ qua, không
chạy trễ**:

- R1 900s — một bảng xếp hạng funding 15 phút tuổi vẫn còn dùng được.
- R2 600s — số dư và margin ratio trôi nhanh hơn funding.
- R3 **300s** — AuthToken chỉ sống 5 giây và giá maker là giá của lúc quét. Một
  lệnh vào đặt theo snapshot 50 phút trước **không phải là giao dịch đã được
  phê duyệt**, nó là một giao dịch khác.

## 4. Một ngày điển hình (giờ Sài Gòn, chu kỳ 1h)

```
07:00  R4 reconcile-brief   chốt sổ 24h vừa qua, ghi <mode>-brief-YYYY-MM-DD.json
07:00  R1 funding-scan      quét (cửa sổ tới 07:15)
07:00  R2 envelope-size     mỗi symbol lọt R1 (cửa sổ tới 07:10)
07:00  R3 stage-pair        đặt maker GTX (cửa sổ tới 07:05)
08:00  R1 · R2 · R3         lặp lại, window_index +1
...
mỗi 30 giây, không phụ thuộc bảng trên:
       watchdog RUNE/MARIN  margin ratio, stop còn sống, đối soát hai chân
```

## 5. Trạng thái bền vững

Ghi tại `data/carry/<mode>-routines.json` (`--routine-state` để đổi chỗ). File
gắn với **một mode**: mở file của `testnet` ở chế độ `mainnet` là lỗi, không
phải cảnh báo. Slot đã `DONE` bị cắt bớt khi vượt `max_slots`; slot **chưa**
`DONE` thì không bao giờ bị cắt — đó đúng là dòng mà nếu mất đi, một lần khởi
động lại sẽ vào lại cặp đang dở.

## 6. Lệnh chạy

```bat
python -m core.carry_runtime --carry --mode shadow  --symbols BTCUSDT ETHUSDT SOLUSDT
python -m core.carry_runtime --carry --mode testnet --routine-period 28800
```

Kiểm tra nhịp mà không chạy vốn:

```python
from core.routine_scheduler import RoutineScheduler
RoutineScheduler('data/carry/shadow-routines.json', mode='shadow').snapshot()
```
