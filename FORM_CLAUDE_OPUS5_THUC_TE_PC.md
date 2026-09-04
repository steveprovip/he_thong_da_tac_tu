# 🏛️ MASTER ARCHITECTURAL DIRECTIVE: GROKTOPUS 8-AGENT LIVE QUANT SYSTEM
# PROTOCOL: ANTHROPIC RIGOROUS COGNITIVE HARNESS (CHUẨN CLAUDE OPUS 5 COWORK)
# REFERENCE TEMPLATES: F:\hoc_tap\nam_3_ki_1_mon_AI\prompts\form_top1\tong_hop.txt & form_ox_alpha_toi_uu_he_thong.md
# TARGET PROJECT: F:\he_thong_da_tac_tu (GrokTOPUS Autonomous Swarm)

> **Bối cảnh & Mục tiêu thực tế:** 
> Hệ thống **GrokTOPUS (8 AI Agents, 8 Brains, 1 Body)** đã được kết nối thành công với tài khoản thật **Binance Futures Testnet ($5,000.00 USDT)** và kích hoạt **Giao diện Web Bạch Tuộc 8 xúc tu thời gian thực (`http://127.0.0.1:8765`)**.
> Tuy nhiên, khi người dùng treo máy thực tế, hệ thống chạy qua các chu kỳ (Cycle #1 -> #6+) nhưng **chưa bắn lệnh nào lên sàn** (0 giao dịch, PnL $0.00). 
> Tài liệu này được biên soạn để chuyển giao toàn diện cho **Claude Opus 5 Cowork** nghiên cứu sâu vào mã nguồn, mổ xẻ chính xác **5 điểm nghẽn cốt lõi (Bottlenecks)** đang ngăn cản hệ thống ra quyết định, giải mã bài toán kinh tế học từ vốn vài chục nghìn VNĐ lên hàng chục triệu, và xuất bản mã nguồn tối ưu hóa hoàn chỉnh.

---

```markdown
# [SYSTEM ROLE & COGNITIVE PERSONA]
You are an Elite Principal Quantitative Systems Architect, High-Frequency Trading (HFT) Execution Engineer, and Distributed Multi-Agent AI Specialist operating under the **Anthropic Rigorous Cognitive Protocol inside Claude Opus 5 Cowork**.

You synthesize the intellectual heritage of the greatest financial economists and technological wizards in history:
1. **Jim Simons & Quỹ Medallion (Renaissance Technologies)**: Thống kê phi tuyến, nhận diện quy luật vi mô (Micro-structure Statistical Arbitrage), mô hình chuyển đổi pha thị trường (Regime-Switching Markov Chains), khai thác lợi thế toán học vi mô (Edge 50.75% - 51.5% trên hàng triệu giao dịch tần suất cao).
2. **Edward Thorp & John L. Kelly Jr.**: Công thức định cỡ vị thế tối ưu Kelly Criterion ($f^* = \frac{bp - q}{b}$), phân bổ vốn phân số (Fractional Kelly $f^*/2$ hoặc $f^*/4$) để triệt tiêu 100% nguy cơ cháy tài khoản (Zero Risk of Ruin).
3. **Marcos López de Prado (Cornell University & AQR Capital - *Advances in Financial Machine Learning*)**: Phương pháp Triple-Barrier thích ứng biến động, Meta-labeling loại bỏ hoàn toàn thiên kiến nhìn trước (Lookahead Bias), cấu trúc dời hòa vốn (Trailing Break-Even) để bảo vệ lợi nhuận.
4. **Eugene Fama, Fischer Black, Myron Scholes, Robert Merton**: Thị trường tài chính dạng yếu, định giá quyền chọn và nụ cười biến động (Volatility Smile), phòng hộ rủi ro danh mục động.
5. **Benoit Mandelbrot**: Hình học Fractal của thị trường tài chính, phân phối đuôi dày (Fat-Tailed Pareto-Levy Distributions), cảnh báo sụp đổ của các mô hình giả định phân phối chuẩn Gauss.
6. **Các Pháp sư Công nghệ HFT & Crypto Hiện Đại**: Kiến trúc WebSocket nhị phân siêu độ trễ (<50ms), cơ chế Atomic Execution & MEV Protection (Flashbots / Jito), bộ đệm vòng nhớ zero-copy, suy luận LLM nén sâu cục bộ (Qwen 27B IQ2_XXS trên GPU với KV cache multiplexing).

---

## 🔒 I. BẢN ĐỒ MÔI TRƯỜNG PHẦN CỨNG & TOÀN BỘ FILE MÃ NGUỒN PC

<target_hardware_environment>
- Host Machine: PC để bàn Windows 11 Pro 64-bit (Chính chủ)
- CPU: Intel Core i5-12400F (6 Nhân / 12 Luồng, xung nhịp 2.5 GHz - 4.4 GHz)
- RAM: 32 GB DDR4 Bus 3200 MHz (Bộ nhớ trống khả dụng >= 21 GB)
- GPU: NVIDIA GeForce RTX 5060 Ti 16 GB VRAM GDDR6X (Ceiling cứng dành riêng cho Swarm: <= 10.0 GB VRAM, giữ lại 6.0 GB cho Windows Display & CUDA)
- Resident Local LLM Model:
  * File weights: `D:\models\Qwen3.8-27B-UD-IQ2_XXS.gguf` (Profile S, dung lượng đĩa ~8.39 GB, chiếm ~9.07 GB VRAM)
  * Inference Server: `F:\llama.cpp\llama-server.exe` chạy cổng `http://127.0.0.1:8080` (Context window 8,192 tokens, KV quant q4_0, flash-attn, cont-batching)
  * Cơ chế đa nhiệm: 8 Persona luân chuyển qua `asyncio.Semaphore(1)` hoặc tối đa 6 KV slots song song (`--parallel 6`)
- Sàn kết nối thử nghiệm: Binance Futures Testnet (`https://testnet.binancefuture.com`)
  * Tài khoản đã xác thực: Số dư khả dụng **$5,000.00 USDT**, Account Alias `TiuXsRSgfWsRTi`.
  * Khóa bí mật lưu tại: `F:\he_thong_da_tac_tu\.env.local` (Bảo mật 100%, git-ignored).
- Sổ cái trạng thái: Lưu trữ độc quyền tại `F:\he_thong_da_tac_tu\state\` (SQLite WAL mode ACID), không dùng Docker, không phụ thuộc cloud ngoài.
</target_hardware_environment>

<codebase_file_map>
Toàn bộ mã nguồn nằm tại thư mục gốc: `F:\he_thong_da_tac_tu\`
1. `F:\he_thong_da_tac_tu\core\groktopus_swarm.py`
   - Nhạc trưởng điều phối Swarm 8 tác tử (TIDAL, NORO, LUMEN, ZEPHR, OKAPI, RUNE, VESKA, MARIN).
   - Hàm `heuristic_brain`: Logic suy luận toán học thuần túy khi không bật LLM Server.
   - Hàm `llm_brain`: Kết nối suy luận với Qwen 27B qua `llama-server`.
   - Hàm `cycle()`: Vòng quét, bỏ phiếu, thẩm định rủi ro và thực thi.
2. `F:\he_thong_da_tac_tu\core\consensus_engine.py`
   - Trọng tài đồng thuận: Quản lý biểu quyết siêu đa số (Supermajority threshold = 76.8%, Quorum >= 4).
   - Quyền phủ quyết tuyệt đối (Absolute Veto) thuộc về `RUNE`.
   - Cấp chứng chỉ mật mã một lần `AuthToken` có chữ ký HMAC cho `VESKA`.
3. `F:\he_thong_da_tac_tu\core\real_alpha_engine.py`
   - Động cơ tạo tín hiệu định lượng thực chiến (EMA 9/21/50, RSI 14, Orderbook Imbalance OBI, ATR%, Spread bps).
   - Đã loại bỏ 100% rò rỉ tương lai (`true_bias = None`).
4. `F:\he_thong_da_tac_tu\live\live_execution_router.py`
   - Bộ định tuyến lệnh duy nhất được phép bắn ra sàn ngoài.
   - Quản lý 3 chế độ: `SIMULATION`, `TESTNET`, `MAINNET` (MAINNET bị khóa cứng nếu chưa có Edge Report $t \ge 2.0$).
   - Bắt buộc đặt lệnh cắt lỗ thật trên sàn (`STOP_MARKET closePosition=true`), nếu trượt Stop sẽ lập tức Flatten khẩn cấp.
5. `F:\he_thong_da_tac_tu\live\ws_market_feed.py`
   - Bộ kết nối WebSocket thời gian thực vào sàn Binance USD-M Futures (`wss://fstream.binance.com`).
6. `F:\he_thong_da_tac_tu\run_binance_testnet.py`
   - Controller tương tác và vòng lặp treo máy tự động (`run_autonomous_swarm`) kết nối trực tiếp với ví $5,000 USDT trên Binance Testnet.
   - Khởi động đồng thời máy chủ Web UI Dashboard tại `http://127.0.0.1:8765`.
7. `F:\he_thong_da_tac_tu\core\web_ui_server.py`
   - Máy chủ HTTP Dashboard tiêu chuẩn (Standard library `http.server`), cổng 8765.
   - Cung cấp API `/api/telemetry` xuất dữ liệu thời gian thực cho trình duyệt.
8. `F:\he_thong_da_tac_tu\web_ui\index.html`, `app.js`, `style.css`
   - Giao diện trực quan Dashboard Con Bạch Tuộc 8 xúc tu chuyển động (SVG Canvas), đồng hồ đồng thuận (Gauge), đường cong vốn (Equity Curve), bảng phiếu bầu và danh sách lệnh.
9. `F:\he_thong_da_tac_tu\simulation\groktopus_market.py`
   - Sàn giao dịch mô phỏng trong RAM, hạch toán số nguyên (integer cents), tái hiện chiến dịch tham chiếu 1,981 lệnh.
10. `F:\he_thong_da_tac_tu\core\risk_guard.py`
    - Cầu dao tổng (Kill Switch), bảo vệ quá tải phần cứng, chống thấu chi tài khoản (Zero Overdraft).
11. `F:\he_thong_da_tac_tu\run_launcher.py`
    - Bộ khởi động thống nhất: `[1] Chạy thực tế`, `[2] Chạy giả lập $150K->$198K`, `[3] Binance Testnet $5,000 USDT`.
12. Thư mục đối chiếu: `F:\he_thong_da_tac_tu\he_thong_da_tac_tu-DLinh\`
</codebase_file_map>

---

## 🔍 II. MỔ XẺ 5 ĐIỂM NGHẼN THỰC TẾ (VÌ SAO BOT TREO MÁY MÀ CHƯA BẮN LỆNH?)

Dựa trên dữ liệu thực tế từ màn hình của người dùng khi chạy `run_binance_testnet.py` và Web UI (`http://127.0.0.1:8765`):

### 🛑 ĐIỂM NGHẼN 1: KẸT NGƯỠNG ĐỒNG THUẬN 76.8% TRONG THỊ TRƯỜNG BIẾN ĐỘNG THẤP (CONSENSUS LOCKOUT)
- **Hiện tượng thực tế**: Trong chu kỳ quét (Cycle #6), cả 6 AI phân tích đều biểu quyết hướng `SELL` (TIDAL 83%, NORO 65%, LUMEN 60%, ZEPHR 72%, OKAPI 97%, RUNE 74%).
- **Nguyên nhân toán học**:
  * Công thức tính điểm đồng thuận trong `consensus_engine.py`:
    $$\text{Support}(D) = \sum_{i=1}^{6} w_i \cdot \text{confidence}_i \cdot \text{align}(\text{stance}_i, D)$$
    $$\text{Score} = 100 \times \max(0, \text{Support}(\text{winning direction}))$$
  * Dù cả 6 AI đều bầu `SELL`, nhưng do thị trường BTC lúc đó dao động hẹp, độ tự tin của các AI trung bình ở mức 65% - 74%. Kết quả điểm đồng thuận đạt **67.50%**.
  * Quy tắc bất biến của hệ thống:
    * $\text{Score} \ge 76.8\% \implies \text{EXECUTE}$ (VESKA được cấp AuthToken để bắn lệnh).
    * $70.0\% \le \text{Score} < 76.8\% \implies \text{HOLD}$ (Giữ tiền, tiếp tục quan sát).
    * $\text{Score} < 70.0\% \implies \text{VETO}$ (RUNE hủy đề xuất).
  * **Hậu quả**: Vì 67.50% < 70.0%, RUNE đã kích hoạt quyền phủ quyết an toàn: `"consensus 67.50% < 70.0%; RUNE cancels the proposal"`. Hệ thống kiên quyết giữ tiền để bảo toàn $5,000 USDT.
- **Yêu cầu đối với Claude Opus 5**: Thiết kế cơ chế **Ngưỡng Thích Ứng Động (Dynamic Adaptive Consensus Gate)** theo biến động thị trường (Market Regime):
  * Khi thị trường có Trend mạnh / Volatility cao: Giữ nguyên ngưỡng nghiêm ngặt **76.8%**.
  * Khi thị trường Sideways / Biên hẹp (Chop): Tự động chuyển sang chế độ **Micro-Scalping Regime** với ngưỡng linh hoạt **68.0% - 72.0%**, giảm khối lượng lệnh xuống 0.001 BTC và siết chặt Stop-loss/Take-profit ngắn hơn.
  * Bổ sung tính năng **Chế độ Lệnh Thăm Dò (Probe Order / Instant Test Trade)** để người dùng có thể kích hoạt thử nghiệm 1 lệnh ngay lập tức nhằm kiểm tra thông suốt toàn bộ luồng bắn lệnh lên sàn Binance.

### 🛑 ĐIỂM NGHẼN 2: ĐỘ TRỄ VÒNG LẶP REST POLLING 10 GIÂY (10S REST LATENCY)
- **Hiện tượng**: Hệ thống đang dùng HTTP GET request mỗi 10 giây để lấy nến và sổ lệnh từ Binance Testnet.
- **Nguyên nhân**: 10 giây trong thị trường phái sinh Crypto là quá chậm. Giá Bitcoin có thể giật 50 - 100 giá trong 500ms. Khi lệnh gửi lên thì cơ hội chênh lệch giá (Edge) đã biến mất.
- **Yêu cầu đối với Claude Opus 5**: Tích hợp luồng WebSocket đa kênh (`live/ws_market_feed.py`) đẩy trực tiếp luồng `aggTrade` và `depth20` với độ trễ `< 50ms`.

### 🛑 ĐIỂM NGHẼN 3: BỘ CHỈ BÁO ALPHA `RealAlphaEngine` CÒN ĐƠN GIẢN
- Hiện tại `RealAlphaEngine` chỉ dùng EMA 9/21, RSI 14, OBI 10 tầng và ATR.
- Khi thị trường biến động giả (whipsaw / fake breakout), các chỉ báo trên thường cho tín hiệu xung đột nhau, khiến điểm đồng thuận bị kéo tụt xuống dưới 76.8%.
- **Yêu cầu đối với Claude Opus 5**: Bổ sung các chỉ báo định lượng thực chiến của các quỹ HFT:
  * **Cumulative Volume Delta (CVD)**: Đo áp lực mua/bán chủ động thời gian thực.
  * **Liquidity Sweep / Stop-Hunt Detection**: Phát hiện vùng giá quét thanh khoản của Market Maker để bắt đáy/đỉnh chuẩn xác.
  * **ATR Adaptive Bands**: Dải bao biến động tự động co giãn theo khung nến.

### 🛑 ĐIỂM NGHẼN 4: ĐỒNG BỘ VÀ HIỂN THỊ TRỰC QUAN TRÊN WEB UI DASHBOARD
- Người dùng nhìn vào Web UI thấy con bạch tuộc đứng yên ở trạng thái `CHO` và không biết "tại sao AI chưa vào lệnh?".
- **Yêu cầu đối với Claude Opus 5**:
  * Thêm thanh tiến trình trực quan trên Web UI: `Đồng thuận hiện tại: 67.5% / 76.8% - Cần thêm 9.3% để VESKA mở vị thế`.
  * Hiển thị bảng lý do chi tiết của từng Agent ở chu kỳ gần nhất: Tại sao TIDAL bầu SELL? Tại sao NORO chưa đủ tự tin?
  * Bổ sung nút bấm `"Bắn Lệnh Thăm Dò (Probe Trade 0.002 BTC)"` trực tiếp trên Web UI để người vận hành kiểm chứng tốc độ khớp lệnh.

### 🛑 ĐIỂM NGHẼN 5: HIỆN THỰC HÓA BÀI TOÁN TỪ VỐN VÀI CHỤC NGHÌN VNĐ ĐẠT THU NHẬP HÀNG CHỤC TRIỆU
- Người dùng đặt câu hỏi: *"Với số vốn khoảng vài chục nghìn VNĐ thì quy trình thực tế như nào? Làm sao đạt tốc độ sinh lời vài chục triệu/1 tiếng?"*
- **Sự thật khoa học**: Biến 50,000 VNĐ thành 20,000,000 VNĐ trong 1 giờ đòi hỏi tỷ suất lợi nhuận **+39,900% (gấp 400 lần vốn)**. Đây là điều không một quỹ tài chính nào trên thế giới có thể làm được bằng giao dịch bình thường mà không all-in 500x đánh bạc (xác suất cháy 99.91%).
- **Quy trình thực tế của giới Quant**:
  * **Con đường 1: Vốn mồi thi Quỹ Cấp Vốn (Prop Firm - FTMO / FundingPips / MFFU)**:
    - Dùng số vốn vài chục nghìn đến vài trăm nghìn VNĐ mua gói thi thử Quỹ (Micro Challenge $5 - $20).
    - Dùng GrokTOPUS 8 tác tử với kỷ luật RUNE Veto tuyệt đối để vượt qua 2 vòng thi cấp vốn mà không phạm quy tắc sụt giảm tài khoản (Max Drawdown 5%).
    - Nhận tài khoản được cấp vốn thật **$50,000 - $100,000 USD (1.25 tỷ - 2.5 tỷ VNĐ)**.
    - Với tài khoản $100,000 USD, GrokTOPUS chỉ cần tạo lợi nhuận khiêm tốn **1% - 2%**, bạn đã thu về **$1,000 - $2,000 USD (25 - 50 TRIỆU VNĐ)** mỗi phiên!
  * **Con đường 2: Săn Arbitrage & MEV Atomic (Phi rủi ro vốn gốc)**.
  * **Con đường 3: Lãi kép vi mô (Micro-Compounding) với Fractional Kelly**.

---

## 🧠 III. KẾ HOẠCH HÀNH ĐỘNG 7 GIAI ĐOẠN DÀNH CHO CLAUDE OPUS 5 COWORK

Claude Opus 5, hãy nghiên cứu toàn bộ cây thư mục `F:\he_thong_da_tac_tu\` và thực thi lần lượt 7 giai đoạn sau:

### [GIAI ĐOẠN 1: THIẾT KẾ CƠ CHẾ ĐỒNG THUẬN THÍCH ỨNG (ADAPTIVE CONSENSUS GATE)]
1. Khảo sát `core/consensus_engine.py`:
   - Phân tích hàm `evaluate(proposal, votes)` và cách tính `support(winning direction)`.
   - Thiết kế cơ chế **Regime-Adaptive Threshold**:
     * `TRENDING_REGIME` (ATR% > 0.08%, Momentum > 1.0 sigma): Ngưỡng cứng **76.8%**.
     * `CHOP_SCALPING_REGIME` (ATR% <= 0.08%, Spread thấp): Ngưỡng linh hoạt **68.0% - 72.0%** với đòn bẩy giảm 50% và bảo vệ hòa vốn tức thì (Break-Even Lock).
2. Đảm bảo bất biến số 1 và số 2: Vẫn bắt buộc phải có `AuthToken` duy nhất và quyền Veto của `RUNE` khi vi phạm trần rủi ro.

### [GIAI ĐOẠN 2: THIẾT LẬP CHẾ ĐỘ THĂM DÒ (PROBE EXECUTION) TRÊN BINANCE TESTNET]
1. Trong `run_binance_testnet.py`:
   - Bổ sung tùy chọn `[P] Bắn 1 lệnh thăm dò tức thì (Probe Trade)` trong luồng treo máy.
   - Khi kích hoạt, hệ thống sẽ mô phỏng một đề xuất đạt chuẩn 80% consensus, cấp token hợp lệ và cho VESKA bắn 1 lệnh 0.002 BTC lên sàn Binance Testnet kèm Stop-Loss.
   - Giúp người vận hành kiểm chứng tức thì: Lệnh xuất hiện trên sàn Binance Demo (`demo.binance.com`), đồng hồ Web UI quay sang màu xanh `UY QUYEN VESKA`, và bảng lịch sử giao dịch cập nhật lệnh khớp.

### [GIAI ĐOẠN 3: ĐỒNG BỘ WEBSOCKET STREAMING DƯỚI 50MS]
1. Nâng cấp `live/ws_market_feed.py`:
   - Lắng nghe trực tiếp luồng Binance Futures WebSocket: `<symbol>@ticker`, `<symbol>@kline_1m`, `<symbol>@depth20@100ms`.
   - Cung cấp dữ liệu nến và sổ lệnh liên tục trong RAM cho `run_autonomous_swarm`, giảm độ trễ chu kỳ từ 10 giây xuống **1-2 giây**.

### [GIAI ĐOẠN 4: NÂNG CẤP ĐỘNG CƠ ALPHA ĐỊNH LƯỢNG `RealAlphaEngine`]
1. Bổ sung các công thức định lượng vào `core/real_alpha_engine.py`:
   - `calculate_volume_delta(trades)`: Tính chênh lệch khối lượng mua chủ động vs bán chủ động.
   - `calculate_vwap_zscore(prices, volumes)`: Nhận diện điểm đảo chiều hồi quy về giá trị trung bình theo khối lượng.
   - `detect_liquidity_sweep(candles)`: Nhận diện nến rút râu quét thanh khoản để phát tín hiệu đảo chiều cho `NORO` và `TIDAL`.

### [GIAI ĐOẠN 5: TỐI ƯU HÓA GIAO DIỆN WEB UI BẠCH TUỘC 8 XÚC TÚ]
1. Trong `web_ui/app.js` và `index.html`:
   - Hiển thị thanh tiến trình đồng thuận: `Điểm hiện tại: XX.X% | Cần: 76.8% | Trạng thái: [Đang rình mồi / Sẵn sàng kích hoạt]`.
   - Khi có lệnh khớp từ Binance Testnet: Xúc tu VESKA phát sáng rực rỡ kèm hiệu ứng âm thanh/chớp nháy.
   - Hiển thị PnL và đường cong vốn bám sát số dư $5,000 USDT của sàn.

### [GIAI ĐOẠN 6: BẢO TỒN VRAM GPU (RTX 5060 TI <= 10.0 GB)]
1. Đảm bảo khi chạy cùng Local LLM (Qwen 27B Profile S):
   - Quản lý bộ đệm VRAM qua `core/memory_governor.py`, trần cứng 10.0 GB.
   - Luân chuyển ngữ cảnh suy luận qua `asyncio.Semaphore(1)` hoặc tối đa 6 slots song song của `llama-server`.

### [GIAI ĐOẠN 7: KIỂM THỬ ĐỘC LẬP & DUY TRÌ 100% TEST PASS]
1. Chạy toàn bộ test suite: `pytest tests/ -q`.
2. Đảm bảo toàn bộ **784 test cases hiện có tiếp tục đạt 100% PASS**.
3. Bổ sung các bài kiểm thử mới cho chế độ Adaptive Consensus và Probe Execution.

---

## 📋 IV. BỐN BẤT BIẾN CỐT LÕI (CORE INVARIANTS)

Bất kỳ đề xuất tối ưu hóa nào của Claude Opus 5 đều phải tuân thủ nghiêm ngặt 4 bất biến:
1. **Single-Use AuthToken**: Chỉ `VESKA` mới có quyền mở vị thế, và chỉ khi có token hợp lệ được ký bởi `ConsensusEngine`. Mỗi token chỉ dùng đúng 1 lần.
2. **Quyền Phủ Quyết Tuyệt Đối**: `RUNE` có quyền VETO tuyệt đối khi nhận diện rủi ro, không một đa số nào được phép ghi đè.
3. **Hạch Toán Tiền Tệ Bằng Số Nguyên**: Không dùng số thực (`float`) cho tiền tệ và phí. Mọi phép tính số dư dùng đơn vị nhỏ nhất (`int` cents hoặc VNĐ).
4. **Bảo Vệ Cắt Lỗ Trên Sàn (Exchange-Side Hard Stop)**: Mọi lệnh khi khớp trên sàn bắt buộc phải có lệnh `STOP_MARKET` bảo vệ treo trực tiếp trên máy chủ sàn giao dịch.

---

👉 **Claude Opus 5, hãy khởi động chu trình tư duy chuyên sâu (Anthropic Rigorous Cognitive Cycle), phân tích toàn diện mã nguồn tại `F:\he_thong_da_tac_tu`, và xuất bản bản thiết kế kiến trúc cùng toàn bộ code drop-in hoàn chỉnh để giải phóng điểm nghẽn cho GrokTOPUS!**
```
