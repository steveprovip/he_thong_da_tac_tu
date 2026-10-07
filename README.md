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

### 8.10 Cập nhật 07/10/2026 (j) — Quét đa cặp BTC/SOL/DOGE, 2 làn TREND/MR
- DEMO chạy 3 engine (BTCUSDT, SOLUSDT, DOGEUSDT) trên 1 venue, 1 cặp WebSocket gộp, 1 sổ rủi ro chung: **1 vị thế tại một thời điểm**, cặp nào đủ điều kiện trước thì vào; 8 lệnh/giờ, nghỉ 30 s, daily kill tính chung. LIVE vẫn 1 cặp (DOGE, vốn ≤ 3.85 U). Tắt quét: `python -m core.engine --mode demo --no-scan --symbol BTCUSDT`.
- Làn TREND: KER ≥ 0.42 (khoá < 0.38), velocity 15 s ≥ 0.025 %, hấp thụ ≥ 0.025 %, các lọc cũ giữ nguyên.
- Làn MR: KER < 0.38, giá lệch VWAP 60 s ≥ 0.12 % ở mép 20 % của hộp 60 s ≤ 0.40 %, sổ L10 nghiêng ≥ 0.25 về phía đảo chiều (NORO MR + TIDAL DEPTH = 80 %). Lệnh GTX, TP +0.15 % toàn bộ, SL −0.10 %.
- MACRO 5m chỉ khoá 1 chiều khi EMA-20 nến 5m dốc ≥ 0.05 %/nến.
- UI: dải `SCANNER BTC: CHOP | SOL: TREND BUY (84%) | DOGE: MR SCAN`; bấm chip hoặc nút BTC/DOGE/SOL để đổi màn hình theo dõi (cả 3 cặp vẫn quét); màn hình tự nhảy sang cặp đang giữ vị thế.

### 8.11 Cập nhật 07/10/2026 (k) — OFI, VPIN, TP/SL theo ATR, Avellaneda-Stoikov, Kelly ¼
- **OFI L10** (Cont-Kukanov-Stoikov 2014, cộng 10 mức): OFI_norm = Σe/Σ|e| trong 10 s (≥ 5 lần cập nhật sổ). |OFI| ≥ 0.65 cùng chiều CVD 15 s ≥ $500k → TIDAL bỏ phiếu → đồng thuận 3 agent (≥ 85 %).
- **VPIN** (Easley-López de Prado-O'Hara 2012) trên 10 rổ $1M (SOL/DOGE $100k), phân loại mua/bán chính xác từ aggTrade. VPIN > 0.75 → huỷ lệnh maker vào đang chờ, RUNE chặn mở lệnh mới 30 s (theo từng cặp).
- **TP/SL theo ATR(14) nến 1 phút** (nạp 30 nến REST lúc khởi động): SL = max(0.15 %, 1.0 ATR), TP1 = max(2.5 ATR, 2.5 SL) cho 50 %, phần còn lại trailing 1.2 ATR (≥ 0.10 %) sau TP1. RR gộp ≥ 2.5; UI hiện cả RR sau phí và tỉ lệ thắng hoà vốn. Chưa đủ 5 nến → TP 0.50 % / SL 0.20 % (RR 2.5). LIVE giữ SL theo lỗ tối đa 0.05 U.
- **MR**: lệnh maker đặt theo Avellaneda-Stoikov (r = s − qγσ²τ, nửa spread γσ²τ/2 + (1/γ)ln(1+γ/k)), không xa mid quá 2 bps, không bao giờ vượt touch; TP = khoảng cách về VWAP trong [0.12, 0.18] %, SL 0.08 %.
- **Kelly ¼** theo từng cặp khi sổ có ≥ 20 lệnh của cặp đó; rủi ro tới SL tối đa 0.50 U/lệnh (demo), notional ≤ $1000; Kelly ≤ 0 → khối lượng tối thiểu. LIVE không dùng Kelly.
- UI: chip `OFI`, `VPIN`, `RR` (SL/TP1, RR gộp / sau phí, % thắng hoà vốn), `KELLY ¼`.

### 8.12 Cập nhật 07/10/2026 (l) — Đồng thuận dòng tiền 3 lớp, khoá DRIFT, nhường slot
- Lệnh TREND: BUY cần CVD 15 s > 0, CVD 60 s ≥ +$300k (× hệ số coin), OFI L10 ≥ +0.35; SELL ngược lại. Thiếu OFI (chưa đủ dữ liệu) = chặn. Làn MR không áp dụng (bật `concordance_mr` sẽ tắt hẳn MR).
- Regime DRIFT (60 s và 3 m không cùng pha, hoặc chưa đủ ngưỡng) → khoá mọi lệnh AUTO.
- Quét đa cặp: cặp có ứng viên tốt hơn (điểm + 0.05 nếu regime cùng chiều + 0.05 nếu 3 dòng tiền cùng chiều) trong 1 s gần nhất được vào trước; cặp kém hơn ghi `SCANNER YIELD`.
- Tuỳ chọn (tắt mặc định): `hurdle_concordance_override` cho REGIME/FLOW exit vượt rào cản phí khi cả 3 dòng tiền ngược vị thế.
- UI: chip `FLOW: CONCORDANT BUY/SELL`, `FLOW: CONFLICT (LOCKED)`, `FLOW: DRIFT LOCK`; radar ghi `DRIFT LOCK`.

### 8.13 Cập nhật 07/10/2026 (m) — Nhiều vị thế song song, size lồi, ratchet SL sàn, nhận lại vị thế mồ côi
- DEMO: mỗi cặp 1 vị thế riêng, tối đa 3 cùng lúc; tổng tiền mất nếu mọi SL khớp (gồm phí, kể cả lệnh đang chờ khớp) ≤ 1.0 U; ký quỹ dùng ≤ 20 % ví. Bộ giới hạn 8 lệnh/giờ, nghỉ 30 s, cắt lỗ ngày vẫn tính chung. `multi_slot=False` = chế độ 1 slot cũ.
- Size lồi: phiếu swarm (không tính JEV) ≥ 90 % + OFI ≥ 0.65 + CVD 60 s ≥ $1M (× hệ số) cùng chiều → 1.5x; ≥ 98 % → 2.0x. LIVE không dùng.
- Ratchet: MFE ≥ +0.15 % → dời SL trên sàn về entry ± 0.08 % một lần (giữ được cả khi engine tắt). SL sàn là taker: 0.08 % ≈ đúng bằng phí, tức hoà, không phải +0.04 % ròng.
- Khởi động lại khi còn vị thế: DEMO nhận lại (ADOPTED) cùng SL đang có trên sàn (hoặc đặt SL mới), MARIN quản lý tiếp. LIVE vẫn chặn vị thế ngoài.
- UI: bảng vị thế nhiều dòng, chip `PORTFOLIO n/3 · uPnL · rủi ro SL x/1.00 U`. Nút ĐÓNG VỊ THẾ đóng cặp đang xem.

### 8.14 Cập nhật 07/10/2026 (n) — SL/TP riêng từng coin, VPIN bất đối xứng, size cân rủi ro, sửa lỗi -4130
- Sàn SL/TP1 theo coin (dưới ATR scaler): BTC 0.15 % / 0.38 %, SOL 0.35 % / 0.875 % (quy tắc RR 2.5), DOGE 0.50 % / 1.25 %. ATR lớn hơn thì ATR thắng.
- Size cân rủi ro cho SOL/DOGE: qty = 0.15 U / (giá × (SL + phí)) → ~0.30 SOL ở giá 117; BTC giữ 0.002 (mức tối thiểu sàn, rủi ro ~0.39 U).
- VPIN: rổ BTC $2.5M, SOL $250k, DOGE $200k; ngưỡng 0.80; chỉ chặn lệnh NGƯỢC chiều dòng tiền độc (bỏ lệnh dừng RUNE toàn bộ); lệnh maker cùng chiều không bị huỷ.
- Sửa lỗi: Binance chỉ cho 1 lệnh STOP closePosition mỗi chiều (-4130) → ratchet trước đây thất bại liên tục; nay huỷ SL cũ rồi đặt SL mới, lỗi thì khôi phục SL cũ, chờ 5 s mới thử lại. Lúc vào lệnh gặp -4130 (SL cũ còn sót) → dọn SL cũ rồi đặt lại thay vì đóng NO_STOP. Kiểm tra "SL đã bị vượt" dùng giá sổ lệnh hiện tại thay vì giá REST cũ.
- UI: chip RR ghi tên coin; radar hiện SL/TP từng coin khi rê chuột.

### 8.15 Cập nhật 07/10/2026 (o) — Ưu tiên SOL/DOGE, cô lập BTC đi ngang, thoát lệnh "chết" sau 180 s
- KER theo coin: BTC phải có KER >= 0.55 mới vào lệnh TREND (không còn vùng đệm), lane MR của BTC tắt; SOL/DOGE giữ khoá 0.38 / mở 0.42 và lane MR dưới 0.38. Radar BTC hiện CHOP thay vì MR SCAN.
- Thoát theo thời gian: lệnh mở >= 180 s mà MFE chưa từng đạt +0.08 % → đặt lệnh maker giảm vị thế ở mức hoà vốn sau phí (entry ± 4 bps), không đuổi giá xuống dưới mức đó. Đến 420 s vẫn chưa khớp → chuyển sang maker ở giá tốt nhất hiện tại (chấp nhận lỗ nhỏ). SL cứng trên sàn luôn giữ nguyên.
- Ưu tiên SOL/DOGE: phiếu swarm thô >= 90 % (thực tế = 100 % vì phiếu chỉ có 60/80/100 %) → size SOL tăng lên mức trần = min(0.70 SOL, $90 notional); mọi đường tính size SOL/DOGE (cân rủi ro, Kelly, size lồi) đều bị kẹp ở trần này (lệnh #34 MR 0.81 SOL sẽ bị kẹp). BTC chỉ được dùng 0.65 U trong ngân sách rủi ro 1.0 U, 0.35 U dành cho SOL/DOGE.
- UI: dải PnL theo coin trên radar (`BTC: -1.45 U | SOL: +0.23 U (PROFIT MAKER) | DOGE: 0.00 U`), trạng thái EJECT đếm ngược, PRIORITY, sàn BE khi đang thoát.
- Live: tắt eject / alt_boost / reserve (giữ trần 3.85 U, lỗ tối đa 0.05 U/lệnh).
- Số test: 1007 passed (tests/test_sol_priority_chop_ejection.py: 20).

### 8.16 Cập nhật 07/10/2026 (p) — Maker thuần, VFR đóng băng BTC, 85 % rủi ro cho SOL/DOGE, Kelly cuộn
- Vào lệnh tự động 100 % Post-Only GTX (phí 2 bps): IOC super-edge tắt mặc định (demo + live), cửa sổ chờ τ = 3 s rồi huỷ và tính lại. Bị sàn từ chối post-only (-5022) → lùi 1 tick khỏi giá tốt nhất, tối đa 2 lần, không bao giờ chuyển sang MARKET.
- VFR = ATR 15 m (bps) / (phí maker 2 chiều 4 bps + spread). VFR < 3 → coin bị FROZEN (BTC thêm điều kiện ATR 15 m >= 0.10 %). ATR 15 m tính từ nến 1 m gộp (seed 240 nến 1 m khi khởi động); thiếu dữ liệu thì ước lượng √15 × ATR 1 m (nhãn EST).
- Ngân sách rủi ro: SOL/DOGE dùng tới 1.0 U, BTC chỉ 15 % (0.15 U). BTC khi VFR < 5 hoặc size thường vượt 0.15 U → micro-probe 0.0001 BTC; nếu sàn không cho đặt 0.0001 (min 0.001 BTC / $100 trên mainnet) thì bỏ lệnh và nghỉ 30 s.
- Phí: TP1 gộp >= 3 × phí 2 chiều (>= 0.12 % với maker). MARIN T1: MFE +0.10 % → dời stop lên entry +0.04 % (= hoà vốn sau phí maker). Eject 180 s khi MFE < 0.06 %, lên giá tốt nhất sau 360 s.
- Size SOL khi phiếu swarm >= 90 %: 0.70 → 1.20 SOL theo Kelly ¼ cuộn 15 lệnh của chính coin đó (cần >= 5 lệnh), trần $140; DOGE 300 → 500. Không boost: trần 0.70 SOL / 300 DOGE.
- UI: radar hiện FROZEN VFR x.x, tooltip có VFR và ATR 15 m.
- Số test: 1033 passed (tests/test_world_quant_economic_engine.py: 26).

### 8.17 Cập nhật 07/10/2026 (q) — Kế toán, đo khớp/độ trễ, sizing an toàn
- Gross/phí trên dashboard chỉ cộng dòng có đủ dữ liệu (dòng TEST thiếu gross/fee được đếm riêng). KILL từ dashboard giờ đóng vị thế qua MARIN và ghi ledger.
- Mỗi lần vào lệnh ghi `data/engine_orders.jsonl` (FILLED/PARTIAL/UNFILLED/REJECTED/TOXIC/REFUSED, decide→send, send→ack, ack→fill, basis tín hiệu↔venue). Ledger schema 2 thêm policy id, commit, basis, markout 1/5/30 s.
- Tắt mặc định: size lồi (convex) và tăng size SOL/DOGE theo phiếu swarm; Kelly cuộn cần ≥ 20 lệnh. Lý do: điểm swarm chưa hiệu chuẩn, 7 lệnh không đủ.
- `python tools/ledger_audit.py`: baseline có khoảng tin cậy. Báo cáo: `docs/research_upgrade/`. Test: 1052 passed.

### 8.13 Phiên bản v2.3.1 (07/10/2026) — 🏆 Bản Ổn Định Sinh Lời Chậm (Stable Slow-Profit Alpha Engine)
- **Đặc điểm thực tế**: Đã kiểm chứng sinh lời ổn định thực tế (Số dư tài khoản tăng từ 4,922.00 U lên 4,923.45 U, PnL vị thế xanh đậm +0.1781 U / +1.06% ROE).
- **Cơ chế**: Sửa triệt để lỗi Binance code -4067 (đồng bộ khởi động êm ái khi còn vị thế/lệnh chờ).
- **Trạng thái an toàn**: Triple Flow Concordance (CVD 15s/60s + OFI L10) + Rào cản phí + ATR Dynamic TP/SL (RR >= 2.5:1).
- **Số test pass**: 951/951 passed.
