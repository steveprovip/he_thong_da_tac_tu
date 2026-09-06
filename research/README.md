# Tầng nghiên cứu — đo edge trước khi tin vào nó

## Tầng này trả lời câu hỏi gì

`simulation/paper_engine.py` trả lời được: *nếu lệnh của tôi khớp thì chuyện gì xảy ra*.
Nó **không** trả lời được: *lệnh của tôi có khớp không* — vì nó không có mô hình vị trí
hàng đợi và không có độ trễ.

Với một chiến lược Post-Only Maker, câu hỏi thứ hai quyết định tất cả. Tầng này chạy
**đúng** `AlphaEngine`, `GroktopusSwarm`, `BarrierPlanner`, `PositionSizer` của bản live
bên trong `hftbacktest`, nơi có mô hình hàng đợi và độ trễ thật.

Ba con số nó tạo ra mà hệ thống cũ không tạo được:

| Con số | Ý nghĩa |
|---|---|
| **Fill rate** | Bao nhiêu % lệnh GTX thực sự khớp, bao nhiêu % hết hạn vì sẽ cross |
| **Maker/taker split** | Lệnh vào có thật sự được tính phí maker 2 bps không |
| **Realized fee rate** | Phí thực tế trên toàn bộ vòng đời lệnh, không phải phí mô hình |

Probe được ghi qua chính `core.signal_lab.SignalLab`, **cùng schema JSONL với bản live**,
nên `tools/calibrate_edge.py` đọc được cả hai và bạn so sánh backtest với live trực tiếp.

## Cài đặt

```powershell
pip install hftbacktest numba numpy
```

## Chạy thử ngay (không cần dữ liệu, không cần mạng)

```powershell
python tools/run_backtest.py --synthetic --minutes 90
```

Feed tổng hợp chỉ để chứng minh đường ống chạy được. Mọi PnL trên đó là **giả tạo** —
báo cáo tự đóng dấu `SYNTHETIC` vì lý do đó. Đừng đọc nó như một kết quả.

> Cần tối thiểu ~21 phút dữ liệu thì `features.ready` mới bật, vì cần 20 nến 1 phút
> cộng 10 bucket VPIN. Một lần chạy có ý nghĩa phải tính bằng **giờ**, không phải phút.

## Đường dữ liệu thật

### Đường A — nhanh, độ trung thực thấp hơn

Tải dữ liệu lịch sử công khai của Binance tại <https://www.binance.com/en/landing/data>
(bộ `bookDepth` và `aggTrades` của USDⓈ-M futures), rồi:

```python
from hftbacktest.data.utils import binancehistmktdata
data = binancehistmktdata.convert(
    depth_filename='BTCUSDT-bookDepth-2026-09-01.csv',
    trades_filename='BTCUSDT-aggTrades-2026-09-01.csv',
    output_filename='data/btcusdt_20260901.npz',
    feed_latency=0.0,     # xem cảnh báo bên dưới
)
```

> ⚠️ **Thư viện tự cảnh báo**: dữ liệu công khai này **không có local timestamp**, nên
> không mang thông tin độ trễ feed. Backtest sẽ nghĩ bạn thấy sổ lệnh ngay lập tức, trong
> khi thực tế bạn thấy nó muộn vài mili-giây. Với chiến lược maker, sai lệch đó làm
> **fill rate cao hơn thực tế**. Dùng đường A để có con số đầu tiên, đừng dùng để quyết
> định vào tiền thật.

### Đường B — chậm hơn, đúng hơn

Chạy collector của hftbacktest (Rust, target `binancefuturesum`) trên chính máy sẽ chạy bot,
để ghi lại feed **kèm local timestamp thật của bạn**:

```powershell
cargo run --release --bin collector -- --exchange binancefuturesum --symbols btcusdt
```

rồi chuyển đổi bằng `hftbacktest.data.utils.binancefutures.convert(...)`. Đây là dữ liệu
duy nhất phản ánh đúng đường mạng của bạn tới sàn.

### Mô hình độ trễ

Mặc định dùng hằng số:

```powershell
python tools/run_backtest.py --data data/btcusdt_20260901.npz --feed-latency-ms 5 --order-latency-ms 5
```

Khi đã ghi được độ trễ đặt lệnh thật, dùng `IntpOrderLatency` — nội suy từ số đo thật
thay vì đoán:

```powershell
python tools/run_backtest.py --data data/btcusdt.npz --latency-file data/order_latency.npz
```

## Đọc kết quả

```powershell
python tools/run_backtest.py --data data/btcusdt_20260901.npz --symbol BTCUSDT
python tools/calibrate_edge.py --path data/backtest-signal-lab.jsonl
```

Thứ tự đọc:

1. **Cảnh báo trước tiên.** Báo cáo tự từ chối những con số mà thiết lập không đỡ nổi:
   chưa hiệu chỉnh bucket VPIN, feed quá ngắn, fill rate quá thấp, hay lệnh post-only bị
   khớp kiểu taker.
2. **Fill rate.** Nếu chỉ 20% lệnh GTX khớp thì edge thực tế thấp hơn nhiều so với ước
   lượng từ probe, vì probe giả định lệnh luôn khớp.
3. **Maker/taker split.** Cột `taker` ở mục 3 phải bằng **0**. Khác 0 nghĩa là giả định
   maker đã vỡ và chi phí đang bị tính thiếu 3 bps.
4. **Khoảng tin cậy trong `calibrate_edge.py`.** Chỉ nới ngưỡng khi khoảng tin cậy nằm
   **trọn** trên 0. Trung bình dương với khoảng tin cậy vắt qua 0 là nhiễu.

## Cái bẫy đã suýt lọt

`BUY = 1` và `MARKET = 1` có cùng giá trị trong hftbacktest. Truyền nhầm hằng số phía
(side) vào chỗ `order_type` sẽ biến lệnh post-only thành **lệnh thị trường**, bị tính phí
taker 5 bps thay vì maker 2 bps — trên một chiến lược mà toàn bộ ngân sách chi phí chỉ
khoảng 8,75 bps. PnL vẫn chạy, không có lỗi nào được ném ra.

`tests/test_research_bridge.py::test_post_only_entry_rests_and_is_charged_the_maker_fee`
khoá lại cái bẫy đó: nó khẳng định lệnh vào phải nằm sổ (`exec_price_tick == price_tick`)
và phí phải đúng bằng maker rate.

## Giới hạn trung thực

- Đường A không có độ trễ feed → fill rate lạc quan hơn thực tế.
- Backtest chưa mô phỏng lớp an toàn của `ExecutionGateway` (stop trên sàn, ratchet,
  reconcile). Nó đo **alpha**, không đo gateway — gateway đã có 19 bài kiểm thử bơm lỗi riêng.
- Feed tổng hợp không mang edge nào cả. Nếu `calibrate_edge.py` báo có edge trên feed tổng
  hợp thì công cụ hỏng, không phải chiến lược tốt.
