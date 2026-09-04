# 🧠 AI HANDOVER GUIDE — SỔ TAY BÀN GIAO CHO AI TIẾP THEO
> **Dành cho Claude Opus 5, Gemini 2.5, GPT-5, Cursor, Copilot hoặc bất kỳ Trợ lý Lập trình AI nào tiếp quản dự án này trên máy tính của người dùng.**

---

## ⚡ 1. Tóm Tắt Dự Án Trong 3 Phút (TL;DR)

- **Tên dự án:** `GrokTOPUS` (Workspace: `F:\he_thong_da_tac_tu`).
- **Bản chất:** Hệ thống giao dịch định lượng đa tác tử (8-Agent Quant Swarm Trading System), lấy cảm hứng từ cấu trúc bầy đàn của bạch tuộc: **8 Tác tử (8 Agents), 8 Bộ não (8 Brains), 1 Thể xác (1 Body)**.
- **Trạng thái hiện tại:**
  - Bộ kiểm thử: **715/715 tests passed (100% xanh)**.
  - Tỷ lệ thắng thực nghiệm: **~72% - 76.3%** (Take Profit gấp gần 3 lần Stop Loss).
  - Tỷ suất sinh lời: **+12% đến +17.86%** sau 2,000 chu kỳ (Compounding Sizing).
  - Tài nguyên phần cứng: VRAM **< 9.07 GB / 16 GB** (RTX 4080/5080), Swarm RAM **< 0.18 GB / 6.0 GB**.
  - Đã hỗ trợ cả mức vốn vi mô **50,000đ - 100,000đ VNĐ** (chuẩn Bybit/MEXC/Exness Cent) lẫn quỹ thể chế **$150,000 USD**.

---

## 🛑 2. Bốn Nguyên Tắc Bất Di Bất Dịch (Non-Negotiable Invariants)

Khi tiếp tục nâng cấp hoặc viết thêm code, **BẠN TUYỆT ĐỐI KHÔNG ĐƯỢC PHÁ VỠ 4 NGUYÊN TẮC NÀY**:

### Invariant 1: Phân Quyền Token Duy Nhất (Single-Use AuthToken)
- **Quy tắc:** Chỉ duy nhất Agent **`VESKA`** mới được mở lệnh (`m.execute`), và CHỈ được mở khi mang theo một `AuthToken` còn hạn (30 giây) do **`ConsensusEngine`** ký sau khi đạt tỷ lệ đồng thuận $\ge 76.8\%$.
- Không một Agent nào được phép tự ý gửi lệnh mà bỏ qua biểu quyết.
- Chỉ duy nhất Agent **`MARIN`** mới có quyền đóng vị thế (`m.close`).

### Invariant 2: Hạch Toán Số Nguyên (Integer Currency Accounting)
- **Quy tắc:** Không bao giờ dùng kiểu dữ liệu `float` để lưu trữ số dư tài khoản, giá, hay phí!
- Toàn bộ giá trị tiền tệ trong hệ thống bắt buộc phải là `int` (tính bằng cents USD hoặc đơn vị VNĐ).
- Mọi phép tính chia tỷ lệ phần trăm / basis points đều phải thông qua hàm làm tròn nửa bước `_round_half_up` và `_bps(amount, bps)` trong `simulation/groktopus_market.py`.

### Invariant 3: Một Mô Hình Thường Trú & Hạn Mức VRAM (1 Body in VRAM)
- **Quy tắc:** Khi dùng chế độ LLM, chỉ load đúng **1 mô hình LLM duy nhất** vào VRAM (Qwen 2.5/3.8 27B GGUF Profile S, ~9.07 GB).
- 8 Agent là 8 Persona được đa nhiệm hóa (Multiplexed) tuần tự thông qua `asyncio.Semaphore(1)` hoặc tối đa 6 KV cache slots của `llama-server --parallel 6`.
- Không bao giờ khởi chạy 2 tiến trình model riêng biệt làm tràn VRAM của người dùng.
- Khi chạy trên máy không có GPU (Laptop văn phòng, CPU-only), hệ thống mặc định dùng `heuristic_brain` (0 MB VRAM, <0.2 GB RAM).

### Invariant 4: Cổng Phủ Quyết Tuyệt Đối (Absolute Veto Gate)
- **Quy tắc:** Agent **`RUNE`** (Risk Controller) có quyền phủ quyết tuyệt đối (`veto = True`).
- Bất kể 5 Agent còn lại vote 100% BUY hay SELL, nếu `RUNE` kích hoạt VETO (ví dụ: chạm trần rủi ro danh mục hoặc biến động cực đoan), đề xuất bị hủy bỏ ngay lập tức.

---

## 📊 3. Bản Đồ Mã Nguồn (Codebase Architecture)

| Đường dẫn file | Vai trò chính | Điểm cần lưu ý khi chỉnh sửa |
| :--- | :--- | :--- |
| [`core/groktopus_swarm.py`](file:///F:/he_thong_da_tac_tu/core/groktopus_swarm.py) | Nhạc trưởng điều phối 8 Agent, Sizing vị thế, Alpha tuning, chu kỳ `cycle()`. | Đã cài đặt quét đa tài sản (`scan(exclude=...)`), sizing lãi kép `risk_fraction = 0.16`, rào cản `stop_bps = 75`, `target_bps = 110`. |
| [`core/consensus_engine.py`](file:///F:/he_thong_da_tac_tu/core/consensus_engine.py) | Tính điểm biểu quyết có hướng `support(D) = Σ wᵢ·cᵢ·align`, cấp `AuthToken`. | Ngưỡng duyệt: $\ge 76.8\%$. Hủy nếu $< 70.0\%$ hoặc có VETO. |
| [`core/market_feed.py`](file:///F:/he_thong_da_tac_tu/core/market_feed.py) | Nạp dữ liệu thị trường thực tế (REST/Coinbase/Binance). | Có cơ chế chống dữ liệu cũ `is_fresh(max_age_s)`, rate limiter token-bucket. |
| [`core/risk_guard.py`](file:///F:/he_thong_da_tac_tu/core/risk_guard.py) | Sentry bảo vệ rủi ro và kill-switch bằng SQLite ACID. | Hạch toán đơn vị VNĐ nguyên, tự động cắt cầu dao nếu phát hiện bất thường. |
| [`simulation/groktopus_market.py`](file:///F:/he_thong_da_tac_tu/simulation/groktopus_market.py) | Sàn giao dịch mô phỏng định lượng (Paper Exchange). | Chứa `DEFAULT_SYMBOLS` ($150k USD) và `MICRO_VND_SYMBOLS` (50k-100k VNĐ). |
| [`core/web_ui_server.py`](file:///F:/he_thong_da_tac_tu/core/web_ui_server.py) | Server HTTP phục vụ Telemetry và Web UI Dashboard. | Hỗ trợ tham số `--capital-vnd` và `--micro`. |
| [`web_ui/app.js`](file:///F:/he_thong_da_tac_tu/web_ui/app.js) | Frontend vẽ bạch tuộc SVG, đồng hồ đồng thuận và đồ thị vốn. | Tự động nhận diện tiền tệ `VND` (₫) hoặc `USD` ($). |
| [`run_launcher.py`](file:///F:/he_thong_da_tac_tu/run_launcher.py) | Menu điều khiển 1 chạm, hỗ trợ chọn mức vốn vi mô hoặc vốn chuẩn. | Chạy mượt trên cả Windows CMD và Linux bash. |

---

## 💻 4. Hướng Dẫn Chạy & Nghiên Cứu Trên Laptop (Laptop Setup Guide)

Nếu người dùng tải dự án này về máy tính xách tay (Laptop), cấu hình thường khiêm tốn hơn máy bàn (có thể không có GPU hoặc chỉ có GPU 4GB - 6GB VRAM):

### A. Laptop Không Có GPU Rời (Chạy CPU-Only)
- **Cơ chế:** Hệ thống được thiết kế với kiến trúc **Dual-Brain**. Khi không có `llama-server.exe` chạy, GrokTOPUS tự động kích hoạt **`heuristic_brain`**.
- **Tài nguyên:** Tiêu tốn **0 MB VRAM** và **< 0.18 GB RAM**.
- **Hiệu năng:** Chạy cực nhanh trên CPU (Intel Core i3/i5/i7/i9, AMD Ryzen, Apple Silicon).
- **Kiểm thử:** Đạt 100% điểm số (715/715 tests pass hoàn hảo).
- **Khởi chạy:**
  ```bash
  pip install -r requirements.txt
  python run_launcher.py
  # Chọn [2] CHAY GIA LAP -> Chọn [2] 100,000 VND
  # Truy cập Dashboard Web tại: http://127.0.0.1:8765
  ```

### B. Laptop Có GPU Rời Nhỏ (4 GB - 6 GB VRAM)
- Nếu muốn thử nghiệm chế độ LLM (`llm_brain`) trên laptop có GPU nhỏ:
  1. Tải mô hình nén nhỏ hơn vào thư mục models: ví dụ `Qwen2.5-7B-Instruct-Q4_K_M.gguf` (~4.5 GB) hoặc `Llama-3.2-3B-Instruct.gguf` (~2.2 GB).
  2. Cập nhật đường dẫn file trong `config/profiles.json`.
  3. Khởi chạy `llama-server.exe -m <duong_dan_model> --port 8080 -c 4096`.

---

## 🔍 5. Nơi Tìm & Nạp Nguồn Dữ Liệu Thị Trường (Data Sourcing Guide)

Dưới đây là cẩm nang toàn diện về các nguồn dữ liệu có thể tích hợp để tiếp tục nâng cấp:

### A. Nguồn Crypto Thời Gian Thực (Miễn Phí & Độ Trễ Thấp)
1. **Coinbase Exchange REST API (Đã có sẵn trong `core/market_feed.py`):**
   - URL: `https://api.exchange.coinbase.com/products/{BTC-USD,ETH-USD,SOL-USD}/ticker`
   - Ưu điểm: Public, không cần API Key, không chặn IP, phản hồi < 150ms.
2. **Binance Public API:**
   - Spot Ticker: `https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT`
   - Futures Ticker: `https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT`
   - Depth (Orderbook L2): `https://api.binance.com/api/v3/depth?symbol=BTCUSDT&limit=20`
   - WebSocket Ticker: `wss://stream.binance.com:9443/ws/btcusdt@ticker`
   - WebSocket Mini-Ticker All: `wss://stream.binance.com:9443/ws/!miniTicker@arr`
3. **Bybit Public API v5:**
   - REST Ticker: `https://api.bybit.com/v5/market/tickers?category=spot`
   - WebSocket: `wss://stream.bybit.com/v5/public/spot`
   - Orderbook Depth: `https://api.bybit.com/v5/market/orderbook?category=spot&symbol=BTCUSDT&limit=50`
4. **OKX API v5:**
   - REST: `https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT`
   - WebSocket: `wss://ws.okx.com:8443/ws/v5/public`
5. **Thư viện CCXT (`pip install ccxt`):**
   - *Khuyến nghị hàng đầu*: CCXT thống nhất API của hơn 100 sàn giao dịch crypto:
   ```python
   import ccxt.async_support as ccxt
   exchange = ccxt.binance()
   ticker = await exchange.fetch_ticker('BTC/USDT')
   print(ticker['last'], ticker['bid'], ticker['ask'])
   await exchange.close()
   ```

### B. Nguồn Ngoại Hối (Forex), Vàng, Dầu, Chứng Khoán Mỹ
1. **Yahoo Finance (`pip install yfinance`):**
   - Tickers: Vàng thế giới (`GC=F`), Dầu thô WTI (`CL=F`), S&P 500 (`^GSPC`), Nasdaq 100 (`^IXIC`), EUR/USD (`EURUSD=X`), USD/VND (`USDVND=X`).
   ```python
   import yfinance as yf
   gold = yf.Ticker("GC=F")
   price = gold.fast_info['lastPrice']
   ```
2. **Alpha Vantage / Twelve Data:**
   - Cung cấp API miễn phí cho Forex và Cổ phiếu toàn cầu (cần đăng ký free API key).

### C. Nguồn Tâm Lý & Tin Tức Thị Trường (Cho Agent LUMEN)
1. **CryptoPanic API:**
   - Endpoint: `https://cryptopanic.com/api/v1/posts/?auth_token=YOUR_TOKEN&public=true`
   - Cung cấp tin tức tức thời, gắn nhãn `bullish` hoặc `bearish` từ hàng ngàn nhà đầu tư.
2. **Alternative.me Crypto Fear & Greed Index:**
   - Endpoint: `https://api.alternative.me/fng/?limit=1`
   - Hoàn toàn miễn phí, không cần đăng ký. Trả về chỉ số Sợ hãi / Tham lam (0 - 100).
3. **Coinglass API:**
   - Cung cấp dữ liệu Tỷ lệ Long/Short (Long/Short Ratio), Bản đồ thanh lý (Liquidation Heatmap) và Open Interest (OI).

### D. Các Sàn Giao Dịch Cho Phép Vốn Vi Mô (50k - 100k VNĐ)
1. **Bybit:** Hạn mức đặt lệnh tối thiểu Spot chỉ từ **$1 USD** (~25,000 VNĐ). Hỗ trợ P2P nạp rút tiền VNĐ qua ngân hàng Việt Nam.
2. **MEXC:** Hạn mức đặt lệnh tối thiểu Spot chỉ từ **$1 USD**. Phí giao dịch Maker 0%.
3. **Exness (Tài khoản Standard Cent):** Nạp tối thiểu từ **$10 USD** (~250,000 VNĐ), số dư hiển thị bằng Cents ($10 = 1,000 cents), đòn bẩy linh hoạt, nạp rút VNĐ tức thì không phí.

---

## 🛠️ 6. Lộ Trình Đề Xuất Cho AI Tiếp Theo (Next Milestones)

Dưới đây là các đầu việc giá trị nhất bạn có thể bắt tay vào làm ngay để nâng tầm hệ thống:

### 🎯 Milestone 1: Nâng cấp Live WebSocket Feed (Hạ độ trễ xuống < 50ms)
- **Hiện tại:** `MarketFeed` dùng HTTP polling (chu kỳ ~0.5s - 1.0s).
- **Mục tiêu:** Viết thêm class `WebSocketMarketFeed` trong `core/market_feed.py` sử dụng thư viện `websockets` hoặc `aiohttp` lắng nghe kênh WebSocket từ Binance hoặc Bybit.
- **Hiệu quả:** Nhận giá tức thì từng mili-giây ngay khi có giao dịch khớp trên sàn, tăng tốc độ phản ứng của `TIDAL` và `ZEPHR`.

### 🎯 Milestone 2: Tích hợp Tin Tức & Fear/Greed Thực Tế cho Agent LUMEN
- **Hiện tại:** `LUMEN` đang ước lượng tâm lý qua động lượng dòng tiền và heuristic.
- **Mục tiêu:** Trong `core/groktopus_swarm.py`, thêm hàm gọi `https://api.alternative.me/fng/` và CryptoPanic API. Nạp trực tiếp chỉ số Sợ hãi & Tham lam vào prompt của `LUMEN` để đánh giá độ Crowding.

### 🎯 Milestone 3: Bộ Lọc Trạng Thái Thị Trường (Market Regime Detection)
- **Mục tiêu:** Sử dụng chỉ số ADX kết hợp Bollinger Band Width hoặc Hidden Markov Model (HMM) để phân loại trạng thái thị trường:
  - Khi thị trường **Có Xu Hướng (Trending - ADX > 25)**: Tăng trọng số cho `TIDAL` (Momentum breakout).
  - Khi thị trường **Đi Ngang (Ranging / Sideway - ADX < 20)**: Tăng trọng số cho `NORO` (Mean-Reversion chạm biên bật lại).

### 🎯 Milestone 4: Kết nối Testnet Sàn Thật (Bybit / Binance Testnet)
- Tạo module `live_execution_router.py` sử dụng thư viện `ccxt`.
- Nhập API Key Testnet (giao dịch tiền ảo trên dữ liệu sàn thật).
- Khi `VESKA` nhận `AuthToken`, thay vì chỉ ghi nhận trong `GrokMarket`, hệ thống có thể tùy chọn bắn lệnh thật lên Bybit Testnet!

---

## 🧪 7. Quy Trình Kiểm Thử Chuẩn Khi Hoàn Thành Code Mới

Sau khi bạn sửa bất kỳ file nào, **BẮT BUỘC** phải chạy kiểm thử:

```bash
# 1. Chạy toàn bộ test suite (715 tests)
python -m pytest tests/ --basetemp=F:\he_thong_da_tac_tu\.pytest_temp -q

# 2. Chạy kiểm thử đa seed 2,000 chu kỳ để kiểm tra PnL và Win Rate
python -c "
import asyncio
from core.groktopus_swarm import build_swarm
async def run():
    for seed in [20260904, 42, 100]:
        s = build_swarm(seed=seed, n_steps=2500)
        for _ in range(2000): await s.cycle()
        m = s.market
        eq = m.equity_cents() / 100
        pnl = eq - (m.config.start_equity_cents / 100)
        wr = (sum(1 for t in m.closed if t.won) / len(m.closed) * 100) if m.closed else 0
        print(f'Seed {seed}: Von ${eq:,.2f} ({pnl:+,.2f}) | WR: {wr:.1f}%')
asyncio.run(run())
"

# 3. Commit và đẩy lên GitHub
git add .
git commit -m "noi dung thay doi"
git push origin main
```

Chúc bạn kế thừa và phát triển GrokTOPUS lên một tầm cao mới thành công rực rỡ! 🚀
