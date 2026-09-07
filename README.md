# 🐙 GROKTOPUS 2.0: Autonomous 8-Agent Quantitative Swarm
Hệ thống nghiên cứu giao dịch có kiểm soát thực thi. Chưa có bằng chứng lợi nhuận sau phí hoặc xác minh tuyên bố hiệu suất ban đầu.

**Bản sửa 06/09/2026:** xem [chẩn đoán, số liệu đối soát, nguồn nghiên cứu và lệnh chạy](docs/CHAN_DOAN_2026-09-06.md). Paper mặc định đọc dữ liệu Mainnet; Kelly chỉ đọc ledger có đơn vị đã xác minh. `--bootstrap` không còn tự hạ ngưỡng rủi ro; Signal Lab không cấp vốn hoặc phiếu đồng thuận.

## 1. Giới thiệu tổng quan
Hầu hết mọi người xây dựng bot theo mô hình: `AI -> Ví -> Mua/Bán`.
GrokTOPUS không phải là một bot, mà là một sàn giao dịch do 8 AI Agent chuyên biệt vận hành:
- **VESKA**: Thực thi lệnh (Cổng xuất vốn duy nhất, bắt buộc có AuthToken mật mã)
- **NORO**: Tính toán giá trị hợp lý (Fair Value Drift & VWAP Z-score)
- **LUMEN**: Phân tích tâm lý thị trường (Orderbook Imbalance OBI & CVD proxy)
- **TIDAL**: Quét thị trường (Cross-sectional momentum, độ dãn RSI, Liquidity Sweeps)
- **ZEPHR**: Phân tích thanh khoản (Spread, slippage, tỷ lệ Edge-to-Cost > 1.8x)
- **RUNE**: Kiểm soát rủi ro tối cao (Phủ quyết tuyệt đối nếu đồng thuận < 70%)
- **OKAPI**: Quản lý phơi nhiễm (Quy mô Kelly trượt, trần sử dụng ký quỹ 75%)
- **MARIN**: Thanh lý giao dịch & giám sát vị thế (Dynamic Triple Barrier, Breakeven Lock, Avellaneda-Stoikov decay)

## 2. Quy tắc Đồng thuận
- **Ngưỡng thực thi giao dịch**: 76.8% đồng thuận có trọng số.
- **Quyền phủ quyết RUNE**: Dưới 70.0% hoặc vi phạm quy tắc an toàn -> Bác bỏ tức thì.

## 3. Chế độ Vận hành
Hệ thống hỗ trợ 2 chế độ độc lập:
1. **Chế độ Thử nghiệm (Simulation / Binance Futures Testnet)**:
   - Sàn trực quan: `https://demo.binance.com/en/futures/BTCUSDT`
   - Endpoint REST: `https://testnet.binancefuture.com`
   - Endpoint WebSocket: `wss://stream.binancefuture.com/ws`
   - Khóa API Testnet đã được nạp sẵn trong `.env.local`.
2. **Chế độ Thực tế (Binance USD-M Futures Mainnet)**:
   - Endpoint REST: `https://fapi.binance.com`
   - Endpoint WebSocket: `wss://fstream.binance.com/ws`
   - Lệnh Maker Post-Only (`GTX`) + Lệnh Stop-Loss sàn (`STOP_MARKET`).

## 4. Giao diện Giám sát Trực quan (Web UI Telemetry)
- Máy chủ loopback port `8765` (`http://127.0.0.1:8765`).
- Hoạt họa SVG bạch tuộc 8 xúc tu Cyberpunk, đồng hồ đo đồng thuận động, đường cong tài sản thực tế.

## 5. Bắt đầu với GPT-6 Astra
Xem chi tiết chỉ thị nghiên cứu và kiến thiết toàn diện từ đầu tại:
[`FORM_GPT6_ASTRA_GROKTOPUS_REBUILD_MASTER.md`](./FORM_GPT6_ASTRA_GROKTOPUS_REBUILD_MASTER.md)
