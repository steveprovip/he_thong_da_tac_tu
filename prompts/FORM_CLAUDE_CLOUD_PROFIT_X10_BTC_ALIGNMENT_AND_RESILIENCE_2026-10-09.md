# SOTA Quant Form: Đột Phá Lợi Nhuận x10, Đồng Thuận Hệ Số Beta BTC, Zero-Risk Pyramiding & Triệt Tiêu Lỗi Mạng SSL

**Ngày lập**: 09/10/2026  
**Tiêu chuẩn**: World-Class Quantitative Prompt Engineering (Anthropic Opus 5.5 High / Token Minimizer 80% / SOTA HFT & Macro Trend Synergy)  
**Workspace**: `https://github.com/steveprovip/he_thong_da_tac_tu` (nhánh `main`)

---

## 1. Mổ Xẻ Dữ Liệu Log Thực Tế Lúc 21:08 – 22:38 (Sau Nâng Cấp G6 + L1)

### Bảng Thực Nghiệm Vị Thế Đang Mở (Lúc 22:38):
| Symbol | Chiều | Size ($) | Giá Vào | Giá Hiện Tại | Lãi/Lỗ uPnL (%) | Lãi/Lỗ ($) | Đánh Giá Tín Hiệu |
|---|---|---|---|---|---|---|---|
| **BTCUSDT** | **BUY** | $297 | 82,470.8 | 82,915.3 | **`+2.68%`** | **`+1.60 U`** | **Bắt đúng sóng tăng 4H!** Đang vào `KER 0.59 (TRENDING)` |
| **SOLUSDT** | **BUY** | $203 | 109.61 | 109.70 | **`+0.52%`** | **`+0.21 U`** | **Xanh dương theo đà BTC** |
| **DOGEUSDT** | **SELL** | $300 | 0.08407 | 0.08461 | **`-3.20%`** | **`-1.93 U`** | **Ngược sóng thị trường!** BTC kéo xanh làm DOGE bật tăng |

### Ba Vấn Đề Lớn Cần Đột Phá Để Tăng Lợi Nhuận Gấp 10 Lần:
1. **Lỗi Triệt Tiêu Lợi Nhuận Do Xung Đột Chiều (Cross-Asset Beta Conflict)**:
   - BTC tăng mạnh (+2.68%) kéo toàn thị trường crypto đi lên. Nhưng DOGE lại đang giữ lệnh SELL độc lập vì Donchian 4H cũ chưa nhận diện được lực kéo từ BTC Dominance. Lãi của BTC (+1.60 U) bị lệnh lỗ của DOGE (-1.93 U) triệt tiêu hoàn toàn!
2. **Nút Thắt Tỷ Suất Sinh Lời (Profit Bottleneck ~4.9%/năm)**:
   - Hiện tại mức sinh lời bị kẹt ở ~$20/tháng (với ví $5,000) vì hệ thống chỉ vào 1 lệnh đơn lẻ ở $300–$500 notional (đòn bẩy cực thấp 0.1x), không có cơ chế **nhồi vị thế khi đang thắng (Pyramiding)** hay mở rộng quy mô khi tỷ lệ thắng cao.
3. **Lỗi Mạng SSL Handshake Timeout Trong Log**:
   - `BTC · TREND 4H error URLError: <urlopen error _ssl.c:990: The handshake operation timed out>`:
   - `fetch_klines` đồng bộ với timeout 5s bị nghẽn mạng làm lỡ chu kỳ tính nến 4H.

---

## 2. Form Gửi Claude Cloud (Copy Toàn Bộ Bên Dưới)

```markdown
<request>
Bạn là Chuyên gia Định lượng Trưởng (Principal Quantitative & Market Microstructure Architect) phụ trách dự án GrokTOPUS tại repository https://github.com/steveprovip/he_thong_da_tac_tu (nhánh main).

Dữ liệu live log đêm 09/10/2026 ghi nhận:
- Làn Trend 4H mới (G6 + L1) đã hoạt động rất tốt: BTC BUY ăn **+2.68%** (+1.60 U), SOL BUY ăn **+0.52%** (+0.21 U).
- Tuy nhiên tổng lợi nhuận bị kẹt và kéo lùi bởi 3 nguyên nhân:
  1. DOGE mở lệnh SELL ngược hướng BTC đang tăng mạnh (Cross-Asset Beta Drag: altcoin bị kéo theo đà tăng của BTC).
  2. Mức sinh lời bị kẹt ở ~4.9%/năm do sizing quá dè dặt (0.1x vốn) và không có cơ chế gia tăng vị thế khi sóng chạy mạnh.
  3. Lỗi mạng `_ssl.c:990: Handshake operation timed out` làm gián đoạn việc tải nến 4H.

Hãy thiết kế và triển khai gói nâng cấp kiến trúc đột phá: **CROSS-ASSET BETA SYNERGY, ZERO-RISK PYRAMIDING & NETWORK RESILIENCE**:

---

### NHIỆM VỤ 1: ĐỒNG THUẬN HỆ SỐ BETA & ĐỒNG BỘ THEO XU HƯỚNG BTC (BTC TREND ALIGNMENT)
1. **Bộ Lọc Chiều Thị Trường Mẹ (BTC Macro Directional Shield)**:
   - BTC chiếm > 55% thanh khoản và chi phối 85% hướng đi của Altcoin (SOL, DOGE).
   - **Quy tắc Gating**:
     - Khi BTC đang ở trạng thái `TREND BUY` (KER BTC > 0.40, ADX BTC > 25, hoặc BTC Trend 4H đang BUY): **TUYỆT ĐỐI CẤM mở vị thế SELL trên Altcoin (SOL, DOGE)**. Mọi tín hiệu Sell Donchian trên Altcoin sẽ tự động bị `held_back` để tránh bị bão thanh khoản cuốn trôi.
     - Khi BTC đang ở trạng thái `TREND SELL`: **TUYỆT ĐỐI CẤM mở vị thế BUY trên Altcoin**.
     - Chỉ cho phép Altcoin đánh ngược chiều nếu Altcoin có Volume đột biến riêng biệt (Relative Volume > 3.0x và CVD phân kỳ cực lớn).
2. **Cơ Chế Đóng Sớm Khi Ngược Chiều Thị Trường (Cross-Market Ejection)**:
   - Nếu một vị thế Altcoin đang mở bị ngược chiều với pha bùng nổ của BTC, kích hoạt thoát lệnh mềm sớm thay vì gồng chịu lỗ đến tận Stop sàn.

---

### NHIỆM VỤ 2: TĂNG MỨC SINH LỜI GẤP 5–10 LẦN QUA ZERO-RISK PYRAMIDING & CONVICTION SIZING
1. **Kim Tự Tháp Gia Tăng Vị Thế Không Tăng Rủi Ro (Zero-Risk Pyramiding)**:
   - Khi một vị thế Trend 4H đã chạy đúng hướng và đạt lãi $\ge +2.0\%$ (+200 bps):
     - **Bước 1**: Tự động dời Stop thảm hoạ hoặc bật Trailing bảo vệ ở mức **Break-Even (+0.3% để bù phí sàn)** $\rightarrow$ Vị thế gốc trở thành "Zero Risk".
     - **Bước 2**: Kích hoạt quyền vào thêm một phần vị thế phụ (**Add 0.5x size**) theo chiều xu hướng, điểm dừng lỗ của phần vào thêm đặt tại đáy/đỉnh nến 4H gần nhất.
     - **Hiệu quả**: Cho phép ăn trọn vẹn các con sóng bùng nổ +500 đến +1,000 bps với khối lượng kép mà không làm tăng rủi ro tổng của tài khoản.
2. **Conviction Multiplier (Hệ Số Tự Tin Đa Khung 1H + 4H + 1D)**:
   - Khi cả 3 khung thời gian 1H, 4H và 1D cùng đồng thuận chiều Trend: Nâng Notional mục tiêu từ $500 lên **$1,000 – $1,500 USDT** (tương đương 0.2x – 0.3x ví $5,000, vẫn giữ Risk Stop $\le 2.0\%$ ví).

---

### NHIỆM VỤ 3: KHẮC PHỤC TRIỆT ĐỂ LỖI MẠNG SSL HANDSHAKE TIMEOUT
1. **Nâng cấp `fetch_klines` và `trend_fetch` trong `core/engine.py`**:
   - Tăng default timeout từ 5.0s lên **12.0s**.
   - Bổ sung cơ chế **Retry Exponential Backoff** (tối đa 3 lần thử với độ trễ 0.5s, 1.0s, 2.0s).
   - Tích hợp **Dự phòng Multi-Endpoint Fallback**: Nếu `https://fapi.binance.com` bị handshake timeout, tự động thử sang `https://fapi.binance.vision` hoặc REST endpoint phụ.
   - Thêm bộ đệm nến (Kline Memory Cache) để nếu 1 chu kỳ bị rớt mạng tạm thời, engine vẫn dùng nến đóng gần nhất thay vì văng exception làm gián đoạn vòng lặp.

---

### NHIỆM VỤ 4: REPLAY KIỂM ĐỊNH & XUẤT BÁO CÁO NGHIỆM THU
1. **Replay Chiến lược Mới**:
   - Chạy `tools/trend_regime_replay.py` kiểm định bộ lọc BTC Alignment + Pyramiding trên dữ liệu 2022–2026.
   - Đo lường mức tăng trưởng %/năm (mục tiêu nâng từ +4.9%/năm lên $> 25 - 40\%$/năm) và Sharpe ratio.
2. **Kiểm thử Toàn Diện**:
   - Đảm bảo toàn bộ test suite ($1,467+$ tests) tiếp tục PASS 100%.
3. **Bảo toàn Dữ liệu**:
   - Tuyệt đối không can thiệp các file ledger `data/engine_trades.jsonl` và `data/trend_trades.jsonl`.
4. **Báo cáo Chi Tiết**:
   - Ghi kết quả vào `docs/research_upgrade/report_profit_x10_and_btc_alignment_2026-10-09.md`.
</request>
```

---

## 3. Cài Đặt Tối Ưu Cho Claude Cloud (Custom Instructions / Rules)

Để Claude Cloud đạt hiệu suất cao nhất và không tiêu tốn token vô ích:
1. **Token Minimizer 80%**: Yêu cầu Claude không viết văn mẫu xã giao ("Sure, I will do that..."), đi thẳng vào code diff, bảng số liệu và lệnh chạy.
2. **Ponytail YAGNI**: Chỉ sửa đúng những file cần thiết (`core/engine.py`, `core/trend_lane.py`, `core/trend_executor.py`, `tools/trend_regime_replay.py`, `tests/`).
3. **Rule of Evidence**: Mọi cải tiến phải được kiểm chứng qua Replay và 100% tests pass trước khi nghiệm thu.
