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

## 5. Tài liệu Nghiên cứu & Định hướng Đột phá 2026
- **Tổng hợp Đối soát & Con đường Đột phá (16/09/2026)**: [`docs/TONG_HOP_NGHIEN_CUU_VA_CON_DUONG_DOT_PHA_2026.md`](./docs/TONG_HOP_NGHIEN_CUU_VA_CON_DUONG_DOT_PHA_2026.md)
- **Giải mã GrokTOPUS & Blueprint 6 Sàn Giao dịch**: [`docs/GROKTOPUS_TRUTH_VENUES_BLUEPRINT_2026-09-16.md`](./docs/GROKTOPUS_TRUTH_VENUES_BLUEPRINT_2026-09-16.md)
- **Đặc tả Khả thi Atomic Arbitrage Vốn Nhỏ (<100k VNĐ)**: [`docs/ATOMIC_4USD_FEASIBILITY_2026-09-16.md`](./docs/ATOMIC_4USD_FEASIBILITY_2026-09-16.md)
- **Đặc tả Regime-Gated Shadow (DOGE Flush)**: [`docs/FLUSH_REGIME_SPEC_2026-09-15.md`](./docs/FLUSH_REGIME_SPEC_2026-09-15.md)

## 6. Trạng thái Kiểm thử Hồi quy
- Kiểm thử tự động: `python -m pytest -q` (hoặc `run.bat test`) $\to$ **834 passed in ~45s** (07/10/2026).
- Bất biến an toàn: Quorum 76.8%, Veto RUNE 70.0%, TokenAuthority HMAC-SHA256 được bảo vệ tuyệt đối.

## 7. Cấu trúc thư mục & Khởi chạy (06/10/2026)
```
run.bat        Master launcher: menu | run.bat test | ui | ui-off | liq | pm15
scripts/       Toàn bộ .bat/.cmd + entrypoint python (run_launcher.py, run_alpha_instant.py, run_concurrent_*.py)
prompts/       FORM_*.md (brief cho agent)
core/ research/ live/ simulation/ phan_tich/ config/    package Python
web_ui/        Pro Terminal (index.html, style.css, app.js, terminal.js)
tests/  tools/  docs/{governance,harness,archive}/  third_party/  data/ artifacts/
```
- **Pro Terminal** `http://127.0.0.1:8765`: SSE `/api/stream` (fallback polling), 5 widget: Micro P&L, OBI/CVD gauge, 8-Agent Matrix, Liquidation Cascade Stream, Polymarket 15m Radar.
- **Flash Liquidation Sniper** `python -m research.fast_liquidation_sniper --mode shadow`: cascade ≥ $50k/5s → JEV recovery score (µs) → Post-Only GTX bắt râu, TP 0.8–1.5% theo điểm, SL ≤ 0.05 U.
- **Polymarket 15m Fast-Arb** `python -m research.polymarket_15m_fast_arb --mode shadow`: trigger IMPULSE (Binance ≥ ±0.3%/8s & share < $0.45 & edge sau phí ≥ 0) + FAIR (edge ≥ 5pp). Chỉ SHADOW + ledger tự chấm điểm; chưa có ví/lệnh thật.

## 8. HAI CHẾ ĐỘ DUY NHẤT (06/10/2026) — `run.bat`
| Lệnh | Sàn | Mặc định | Bảo vệ |
|---|---|---|---|
| `run.bat demo` (phím 1) | Binance **Demo** Futures (`BINANCE_TESTNET_*`); thiếu key → **MOCK** khớp theo giá live | BTCUSDT · khối lượng tối thiểu (0.001–0.002 BTC) · Cross 5x · MARKET · TP +0.25% / SL −0.20% · giữ tối đa 120 s | kill 5 U/ngày, 30 lệnh/giờ, nghỉ 5 phút sau 3 lệnh lỗ |
| `run.bat live` (phím 2, phải **gõ LIVE**) | Binance **Mainnet** (`BINANCE_MAINNET_*` hoặc `BINANCE_API_KEY/SECRET`) | DOGEUSDT · Cross 5x · **Post-Only GTX** · TP +1.0% · SL tính từ lỗ tối đa | ký quỹ ≤ 3.85 U, lỗ ≤ 0.05 U/lệnh (gồm phí), kill 0.15 U/ngày, 6 lệnh/giờ |

Luồng: WS mainnet công khai (`aggTrade`, `depth5@100ms`, `markPrice@1s`, `forceOrder`) → 3 tín hiệu (S1 OBI+CVD, S2 lệch VWAP 1m > 0.06 %, S3 thanh lý ≥ $10k/3 s) → 8 agent bỏ phiếu (≥ 76.8 % hoặc JEV ≥ 8.0, mọi cổng xanh) → AuthToken HMAC dùng 1 lần → VESKA vào lệnh → STOP_MARKET trên sàn + TP reduce-only GTX → MARIN đóng lệnh khi hết thời gian giữ. Lịch sử: `data/engine_trades.jsonl`.
Web UI `http://127.0.0.1:8765`: công tắc DEMO ⟷ LIVE, **BẮN LỆNH TEST** (chỉ DEMO), **ĐÓNG VỊ THẾ**, bảng vị thế kiểu Binance, lệnh chờ, lịch sử khớp lệnh, âm thanh.
BTCUSDT có min notional $100 nên **không** chạy được ở LIVE với vốn 3.85 U / lỗ 0.05 U — OKAPI chặn bằng `CAPITAL GATE`. Các launcher cũ nằm ở `scripts/legacy/`.

### 8.1 Cập nhật 07/10/2026 — Maker-only + MARIN breakeven
- **Vào lệnh 100 % Post-Only GTX** tại best bid/ask (nhích 1 tick khi spread ≥ 2 tick), chờ 3 s, không khớp thì huỷ, không đuổi giá. MARKET chỉ còn dùng cho lệnh đóng khẩn cấp reduce-only. Nút BẮN LỆNH TEST cũng đi luồng này.
- **MARIN**: bỏ đóng lệnh theo thời gian. SL cứng (demo 0.20 %, live tính từ lỗ tối đa 0.05 U) → khi lãi ≥ +0.12 % dời stop về hoà vốn = giá vào + phí maker/taker + trượt + 0.02 % → TP1 +0.35 % chốt 50 % (maker) → phần còn lại trailing cách đỉnh 0.20 % khi lãi > +0.50 %. Stop mới luôn đặt TRƯỚC rồi mới huỷ stop cũ.
- **Hội tụ ≥ 2/3**: C1 OBI L5 ≥ 0.20 + CVD 60s ≥ 0.20 + CVD 10s cùng chiều · C2 lệch VWAP 1m ≥ 0.08 % tại đáy/đỉnh 60 s · C3 edge/chi phí ≥ 2.5x.
- **ZEPHR**: ma trận chi phí khởi tạo sẵn (maker 2 bps, taker 5 bps, spread mặc định theo symbol), không còn chờ 30 trade. Nguyên nhân thật của "warmup 0/30": Binance tách WebSocket (thông báo 2026-03-06, URL cũ tắt 2026-04-23) → aggTrade/markPrice/forceOrder chỉ còn ở `wss://fstream.binance.com/market/...`, depth ở `/public/...`. Engine và fast lane đã dùng 2 socket.
- Lịch sử lệnh có Gross / Phí / Net và số lần khớp maker/taker.

### 8.2 Cập nhật 07/10/2026 (b) — Thuận dòng tiền, 2 chiều
- **Cổng regime CVD (cứng)**: CVD 60s ≤ −$500k hoặc cvd_norm ≤ −0.15 → `DOWN` chỉ SHORT; ≥ +$500k / +0.15 → `UP` chỉ LONG; |CVD| ≤ $200k → `NEUTRAL` (2 chiều, cho phép mean-reversion VWAP); còn lại `DRIFT` (2 chiều, cấm mean-reversion). Ngưỡng USD nhân hệ số theo coin (BTC 1.0, ETH 0.5, còn lại 0.1). Áp dụng cho cả lệnh tự động lẫn nút test.
- **S4 TREND**: CVD ≤ −$300k + OBI ≤ −0.15 + giá dưới VWAP → SHORT GTX tại best ask (đối xứng cho LONG). NORO bỏ phiếu TREND, chỉ dùng VWAP mean-reversion khi NEUTRAL.
- **MARIN**: thoát theo dòng tiền khi có ≥ $500k lệnh chủ động ngược chiều trong 15 s kể từ lúc vào lệnh (thử maker tại giá tốt nhất 1 s rồi MARKET). Micro-BE +0.08 % → stop về giá vào +0.03 % (lỗ tối đa ≈ phí 0.05 %); BE thật +0.12 % → net +0.02 %.
- UI: huy hiệu regime ở header + thanh điều khiển, nút ⚡ BẮN LONG TEST / ⚡ BẮN SHORT TEST (chiều bị cấm tự khoá).

### 8.3 Cập nhật 07/10/2026 (c) — Pegged maker, thoát lệnh maker, CVD đa khung, chọn coin
- **Vào lệnh pegged maker** (đồng thuận ≥ 80 %): lệnh GTX tự đặt lại theo best bid/ask mỗi 0.5 s trong 3 s, không đuổi quá 0.05 % so với giá đặt đầu; spread ≥ 2 tick thì đặt theo micro-price `I·ask + (1−I)·bid`, I = bid_qty/(bid_qty+ask_qty).
- **Thoát lệnh maker** (`soft_exits=True`): FLOW / BE / TRAIL không còn dùng MARKET mà là lệnh reduce-only post-only tại giá tốt nhất (đóng long = bán ở best ask), tự đặt lại mỗi 0.5 s khi giá dịch chuyển. Chỉ hard SL trên sàn (STOP_MARKET −0.20 %) mới trả phí taker. BE mềm: +0.08 % → mức thoát = giá vào + 2×maker + 0.02 % = +0.06 %.
- **CVD đa khung**: UP cần CVD 60s ≥ +$400k **và** CVD 3m ≥ +$600k (DOWN đối xứng); lệch nhau → DRIFT (đứng ngoài, không TREND, không mean-reversion).
- **Chọn coin**: BTC / DOGE / SOL trên giao diện (`/api/switch_symbol`, chỉ đổi được khi không có vị thế); DOGE/SOL tự lấy khối lượng nhỏ nhất (~$5).

### 8.4 Cập nhật 07/10/2026 (d) — Không bỏ lỡ sóng bứt phá
- **Pegged maker nới rộng**: chờ khớp 6 s, bám giá tối đa 0.15 %, áp dụng từ 76.8 %; spread ≥ 2 tick thì mua ở ask − 1 tick / bán ở bid + 1 tick (vẫn post-only).
- **IOC taker khi siêu dòng tiền**: đồng thuận swarm gốc ≥ 85 % **hoặc** (JEV ≥ 9.0 **và** |CVD 60s| ≥ $3M × hệ số coin) → VESKA gửi LIMIT IOC tại giá tốt nhất ± 0.03 % (trượt có giới hạn, không phải MARKET mù). Sau khi khớp: SL sàn + TP1 maker như cũ; BE mềm kích hoạt ở +0.12 % với mức thoát = giá vào + taker + maker + 0.02 % (+0.09 %).
- **Không kẹt trạng thái**: lệnh không khớp (GTX hết 6 s hoặc IOC hết thanh khoản) → về SCANNING, quét lại sau 1 s.

### 8.5 Cập nhật 07/10/2026 (e) — Velocity, L10 wall, squeeze, 4 tầng chốt lời, sổ lệnh của sàn demo
- **Sửa lỗi gốc ở DEMO**: sàn demo có sổ lệnh/giá riêng, KHÔNG phải bản sao mainnet. Trước đây giá đặt lệnh và MARIN đọc giá mainnet → GTX bị từ chối "post-only", và ở lệnh #4/#5 TP1 khớp +0.35 % trên demo trong khi MFE ghi 0 % (BE không bao giờ kích hoạt). Nay giá đặt/thoát lệnh và MARIN dùng `bookTicker` của chính sàn demo (0.3 s khi có vị thế); tín hiệu dòng tiền vẫn lấy từ mainnet.
- **S5 SURGE**: tốc độ CVD 10 s ≥ $50k/s và ≥ 2× nhịp 60 s. **WALL**: mất cân bằng sổ L10 ≥ ±0.35 (tính confluence, chặn vào lệnh đâm vào tường ngược). **S6 SQUEEZE**: hộp giá 2 phút ≤ 0.08 % rồi phá vỡ cùng SURGE → TIDAL bỏ phiếu 100 % → đồng thuận ≥ 95 % → IOC.
- **MARIN 4 tầng (mềm, thoát maker)**: +0.08 % → mức thoát entry +0.03 % · +0.12 % → hoà phí +0.02 % net · TP1 50 % ở +0.35 % · runner trailing 0.20 % từ +0.50 %, siết 0.15 % từ +0.70 %.
- UI: Win Rate · W/L · Avg Win/Loss · Profit Factor · Expectancy; CVD velocity trên đồng hồ T2.

### 8.6 Cập nhật 07/10/2026 (f) — Chống churn phí
- Sổ lệnh thật (17 lệnh demo): 15 lần thoát FLOW, 13 lần chỉ có $0.50–0.97M flow ngược (< $2M); 14 lệnh trong 1 giờ cao điểm.
- **Flow exit**: ngưỡng $2M (× hệ số coin; SOL/DOGE $200k), hoặc 2.5× flow 15 s lúc vào lệnh (trần 3× = $6M). **30 s thở** đầu tiên không có flow exit; SL cứng −0.20 % trên sàn vẫn luôn chạy.
- **Churn governor**: tối đa 8 lệnh/giờ · nghỉ 30 s sau mỗi lệnh đóng · lệnh AUTO cần điểm swarm ≥ 82 % (điểm thô hoặc JEV/10) **và** edge kỳ vọng ≥ 30 bps. Lệnh TEST không qua bộ lọc này.
- **Regime-flip exit**: SHORT khi CVD 60 s ≥ +$1M, LONG khi ≤ −$1M → MARIN thoát maker ngay (kể cả trong 30 s thở).
- Đổi coin không đổi % phí: phí và lãi gộp cùng co theo notional, nên net % như nhau; SOL/DOGE chỉ làm số USDT lời/lỗ nhỏ lại.

### 8.7 Cập nhật 07/10/2026 (g) — Khoá sideway (KER), chống nhiễu regime, TP1 +0.45 %
- **KER 2 phút** = |P_t − P_{t−120s}| / Σ|ΔP|, lấy mẫu giá khớp cuối mỗi 5 s (bỏ nhiễu bid/ask). KER < 0.40 → **CHOP LOCKED** (cấm mọi lệnh AUTO); chỉ mở lại khi KER ≥ 0.45; dưới 60 s dữ liệu = khoá.
- **Regime-flip exit**: không thoát trong 60 s đầu; CVD lật phải giữ liên tục 20 s mới thoát (maker, không market).
- **TP1 +0.45 %** cho 50 % khối lượng. 0.002 BTC @84k: TP1 = 0.378 U gộp; nếu runner về hoà phí, cả lệnh ≈ +0.36 U net (không phải +0.75 U).
- UI: chip `KER 0.52 (TRENDING)` / `KER 0.28 (CHOPPY - LOCKED)` trên header và badge regime; ô QUYẾT ĐỊNH ghi `CHOP LOCKED KER …`.

### 8.8 Cập nhật 07/10/2026 (h) — Bẫy hấp thụ, khiên phí 2 tầng, maker nghiêm, macro 5 phút
- **ABSORPTION TRAP**: |CVD 15 s| ≥ $500k (× hệ số coin) mà giá không đi ≥ 0.03 % cùng chiều → cấm vào lệnh chiều đó.
- **Khiên phí**: MFE +0.12 % → mức thoát ≥ entry + 0.04 % (thực tế dùng hoà phí thật entry + phí 2 đầu + 0.02 % nếu cao hơn: GTX +0.06 %, IOC +0.09 %). MFE +0.25 % → khoá entry + 0.15 % (≈ +0.11 % net với GTX). Đây là lệnh thoát maker: giá rơi một mạch qua mức khoá thì vẫn có thể lỗ nhỏ (SL cứng −0.20 % chặn đáy).
- **IOC** chỉ khi CVD 10 s cùng chiều ≥ $3M **và** KER ≥ 0.60 **và** (JEV ≥ 9.2 hoặc swarm ≥ 85 %); còn lại pegged GTX 5 s, không đuổi taker.
- **MACRO 5m**: giá > VWAP 5 phút và EMA-20 nến 5 phút dốc lên → chỉ LONG; ngược lại → chỉ SHORT. EMA được nạp 40 nến 5m từ REST công khai của Binance lúc khởi động; VWAP 5 phút cần 4 phút dữ liệu (trước đó = WARMUP, không chặn).
- UI: chip `ABSORPTION`, `MACRO 5m`, `SHIELD` cạnh Win Rate.

### 8.9 Cập nhật 07/10/2026 (i) — Rào cản phí khi thoát FLOW/REGIME
- FLOW / REGIME exit chỉ chạy khi gross ≥ +0.08 % hoặc ≤ −0.08 %; lệnh đang xanh (gross ≥ 0) không bị FLOW/REGIME cắt trong 90 s đầu. SL cứng −0.20 % và các tầng MARIN (LOCK/BE/PROFIT/TP1/TRAIL) không bị chặn.
- Mỗi lần thoát bị giữ được ghi vào sổ lệnh (`hurdle_blocks`, `hurdle_first_px`, `hurdle_first_pct`) để so sánh "giữ" với "thoát ngay".
- Vào lệnh: KER mở khoá ở ≥ 0.48 (khoá < 0.40); giá 15 s phải đi ≥ 0.04 % cùng chiều lệnh.
- UI: chip `HOLDING: FEE HURDLE (+0.03% < +0.08%)` / `PROFIT: UNLOCKED` / `EXIT: ARMED`.
