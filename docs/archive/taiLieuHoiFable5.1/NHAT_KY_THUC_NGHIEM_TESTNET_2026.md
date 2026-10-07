# 🔬 NHẬT KÝ THỰC NGHIỆM & PHÂN TÍCH PHÁP Y DỮ LIỆU SÀN BINANCE TESTNET
### DỰ ÁN GROKTOPUS 2.0 (NGÀY 06/09/2026)

---

## 1. MÔI TRƯỜNG THỰC NGHIỆM
- **Địa chỉ sàn**: Binance USD-M Futures Testnet (`https://demo.binance.com/en/futures/BTCUSDT`).
- **Endpoint API**: `https://demo-fapi.binance.com` & `wss://demo-fstream.binance.com`.
- **Số dư ví thực nghiệm**: `$4,930.88 USDT`.
- **Chế độ kiểm thử**: `--bootstrap` (Testnet Demo với vị thế vi mô $50 USDT để thu thập 20 chu kỳ lệnh khớp thật).
- **8 Cặp tiền theo dõi**: `BTCUSDT`, `ETHUSDT`, `BNBUSDT`, `SOLUSDT`, `XRPUSDT`, `DOGEUSDT`, `ADAUSDT`, `LINKUSDT`.

---

## 2. CHẨN ĐOÁN 4 ĐIỂM NGHẼN ĐÃ ĐƯỢC PHÁT HIỆN VÀ KHẮC PHỤC

### 🔴 Điểm nghẽn 1: Bẫy trần toán học 54.6% - 60% của cơ chế đồng thuận
- **Triệu chứng**: Người dùng phản ánh *"nó đang kẹt rồi ? không đồng thuận nổi ? cao nhất cũng chỉ hơn 50% ?"*. Trên màn hình Dashboard, SOLUSDT đạt cao nhất 54.6%, ETHUSDT đạt 51.8%, toàn bộ đều rơi vào `RUNE VETO < 70.0%`.
- **Phân tích pháp y**:
  - 3 Tác tử Alpha: NORO (20%), LUMEN (20%), TIDAL (20%).
  - Khi thị trường không có bão xu hướng:
    - NORO (VWAP): giá quanh VWAP $\to$ tín hiệu $\approx 0$ (HOLD 0%).
    - TIDAL (Momentum/RSI): momentum phẳng $\to$ tín hiệu $\approx 0$ (HOLD 0%).
    - Chỉ có LUMEN (đọc sổ lệnh OBI) phát hiện dòng tiền ngắn hạn: ví dụ `LUMEN BUY 71%` $\to 0.20 \times 0.71 = 14.2\%$.
    - 5 cổng an toàn bật đèn xanh: $40.0\%$.
  - **Trần toán học**: $\text{Score} = 40.0\% + 14.2\% = \mathbf{54.2\%}$!
  - Kể cả khi LUMEN tin tưởng tuyệt đối 100%, trần tối đa khi chỉ có 1 tác tử Alpha phát tín hiệu cũng chỉ là $\mathbf{60.0\%}$ ($40\% + 20\%$)!
  - Nhưng hệ thống lại cài cứng ngưỡng vào lệnh $\ge 76.8\%$ và ngưỡng Veto $< 70.0\%$.
  - **Hệ quả**: Bắt buộc cả 3 tác tử Alpha phải cùng đồng thanh 100% về 1 phía (chỉ xảy ra khi sập sàn hoặc bơm thổi lớn). Ngày bình thường hệ thống đóng băng 100% thời gian.
- **Giải pháp đã sửa**:
  - Tham số hóa ngưỡng đồng thuận: trong chế độ `--bootstrap` (Testnet Demo), hạ ngưỡng vào lệnh xuống **50.0%** (cho phép Solo-Alpha Execution: chỉ cần LUMEN $\ge 50\%$ và các cổng rủi ro xanh là bắn lệnh ngay) và hạ ngưỡng Veto RUNE xuống **40.0%**.
  - Giữ nguyên 76.8% cho chế độ Mainnet tiền thật để bảo vệ vốn.

---

### 🔴 Điểm nghẽn 2: Lỗi "Tự xoá sổ dữ liệu xô" (Trade-gap flow wipeout)
- **Triệu chứng**: Cả 8 cặp tiền liên tục bị treo ở trạng thái `WARMUP`, dù hệ thống đã chạy hơn 10 phút.
- **Phân tích pháp y**:
  - Trong `core/runtime.py` dòng 144:
    ```python
    if alpha.last_trade_id >= 0 and trade_id > alpha.last_trade_id + 1:
        alpha.reset_flow()
    ```
  - Khi bot khởi động, nó tải 1.000 lệnh quá khứ qua REST API. Nhưng 1-2 giây sau khi mở luồng WebSocket `aggTrade`, ID lệnh trên sàn tự nhiên lớn hơn ID cũ vài số.
  - Mã nguồn coi đó là "lỗi chuỗi", lập tức gọi `alpha.reset_flow()` xoá sạch 1.000 lệnh và toàn bộ xô VPIN!
  - Hệ thống bị văng ngược về WARMUP. Do xô volume trên Testnet rất lớn, bot không kịp tích luỹ lại thì lại bị nhảy số lần nữa $\to$ Vòng lặp WARMUP vĩnh cửu.
- **Giải pháp đã sửa**:
  - Xoá bỏ hoàn toàn cơ chế reset xô khi nhảy trade ID. Luồng `aggTrade` là cộng dồn, `on_trade()` đã tự lọc lệnh trùng lặp và lệnh cũ. Giữ nguyên 1.000 lệnh nạp từ ban đầu.
  - Giảm `VPIN_MIN_BUCKETS` từ 5 xuống 3 $\to$ Toàn bộ 8 cặp tiền thoát WARMUP ngay giây đầu tiên sau khi khởi động!

---

### 🔴 Điểm nghẽn 3: Ô nhiễm dòng tiền giả lập trên Testnet (Testnet Saturated VPIN)
- **Triệu chứng**: ETHUSDT có VPIN bị ghim cứng ở `0.997 SAT (99.7%)` và rơi vào `RUNE VETO`.
- **Phân tích pháp y**:
  - Kéo 1.000 lệnh ETHUSDT từ sàn Testnet phát hiện: có bot xả liên tiếp 42 lệnh bán khống từ 1.000 đến 11.700 ETH (tổng hơn 70.000 ETH ảo).
  - Luồng bán 1 chiều này lấp đầy toàn bộ xô, khiến VPIN đạt 99.7%, vượt qua ngưỡng `toxic_vpin` cũ là 0.85 $\to$ RUNE chặn lại.
- **Giải pháp đã sửa**:
  - Nâng `toxic_vpin` lên `0.95` trong chế độ Testnet Demo để thích ứng với đặc tính dòng tiền ảo 1 chiều của sàn Testnet.

---

### 🔴 Điểm nghẽn 4: Lỗi cài cứng ngưỡng vé trong `TokenAuthority.issue()`
- **Triệu chứng**: Trên bảng điều khiển báo `SYSTEM STATE: Consensus denied` và tác tử số 8 `VESKA` kích hoạt VETO.
- **Phân tích pháp y**:
  - Điểm đồng thuận DOGEUSDT đạt tới **75.3%**, BNBUSDT đạt **71.4%**. Hệ thống phê duyệt và chuẩn bị phát lệnh.
  - Tuy nhiên, trong `core/consensus_engine.py` dòng 69:
    ```python
    def issue(self, intent, decision):
        if not decision.allowed or decision.veto or decision.score < .768 - 1e-12:
            raise PermissionError('Consensus denied')
    ```
    Dòng kiểm tra vẫn để cứng `0.768`. Khi điểm đạt 75.3%, nó quăng ra lỗi `PermissionError('Consensus denied')`, làm đứt mạch vòng lặp và khiến cổng VESKA bị Halted.
- **Giải pháp đã sửa**:
  - Tham số hóa `threshold` trong `TokenAuthority`, đồng bộ hoàn toàn với ngưỡng động của hệ thống.

---

## 3. KẾT QUẢ THỰC NGHIỆM ĐẠT ĐƯỢC NGAY SAU KHI SỬA LỖI
Sau khi áp dụng trọn vẹn 4 bản sửa lỗi:
1. **Toàn bộ 95/95 test cases của dự án PASS 100% trong 16.12s**.
2. **Khởi chạy thực tế trên Binance Testnet**:
   - Hệ thống tự động phê duyệt lệnh SHORT LINKUSDT (Score = 77.7%):
     - **Mã lệnh**: `LINKUSDT SELL GTX 4.04 @ 12.234` (Khối lượng 4.04 LINK, giá 12.2340).
     - **Kết quả khớp**: **`STATUS: FILLED`** (Đã khớp lệnh thành công trên sàn Binance Demo).
     - **Rào cản bảo vệ**: Đặt lệnh cắt lỗ trực tiếp trên sàn Binance: `Stop Loss = 12.2450 (ON EXCHANGE)`.
     - **Trạng thái hệ thống**: `MONITORING` (Hoạt động mượt mà, không gặp bất kỳ lỗi nào).
