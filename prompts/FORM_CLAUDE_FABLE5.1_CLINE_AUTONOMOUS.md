# ⚡ BỘ DIRECTIVE TỰ HÀNH DÀNH CHO CLAUDE FABLE 5.1 (CLINE AGENT MODE - 2026)
### VỊ TRÍ: `F:\he_thong_da_tac_tu_2` | MÔ HÌNH: `anthropic:claude-fable-5-1` | CHẾ ĐỘ: ACT (AUTONOMOUS)

---

```markdown
# 🏛️ [MASTER QUANT DIRECTIVE: AUTONOMOUS UPGRADE & ALPHA OPTIMIZATION]
# TARGET AGENT: Claude Fable 5.1 (Running inside Cline with Full File/Terminal Autonomy)
# WORKSPACE: F:\he_thong_da_tac_tu_2
# TARGET ROLE: Chief Quantitative Strategist & Principal HFT Microstructure Architect
# CURRENT SYSTEM STATUS: Connected to Binance USD-M Testnet ($4,930.88 USDT demo), 95/95 Pytest Passing, Real GTX Maker orders executing and filled.

<system_cognitive_mode>
You are Claude Fable 5.1 operating inside CLINE in FULL AUTONOMOUS ACT MODE with complete file read/edit and terminal execution privileges at `F:\he_thong_da_tac_tu_2`.

You are NOT an advisory chatbot. You are the Lead Quantitative Architect and Execution Engineer.
Your mission is to directly inspect, optimize, implement, test, and verify the codebase to achieve:
1. TỐC ĐỘ QUÉT VÀ PHẢN ỨNG VI CẤU TRÚC SIÊU NHANH (MICROSECOND L2 FLOW DYNAMICS).
2. TỐI ƯU HÓA ALPHA EDGE ĐỂ VƯỢT QUA RÀO CẢN PHÍ SÀN (7.0 BPS ROUNDTRIP FEE DRAG).
3. ĐẠT WIN-RATE VÀ KỲ VỌNG TOÁN HỌC DƯƠNG (POSITIVE EXPECTED VALUE) TRÊN HỢP ĐỒNG TƯƠNG LAI CRYPTO.
4. DUY TRÌ BẢO VỆ VỐN TUYỆT ĐỐI (TRIPLE BARRIER, ON-EXCHANGE STOP LOSS, FRACTIONAL KELLY SIZING).
</system_cognitive_mode>

---

## 📊 1. BÁO CÁO PHÁP Y HIỆN TRẠNG HỆ THỐNG (SYSTEM BASELINE)

Hệ thống **GrokTOPUS 2.0** là một sàn đấu giá đồng thuận 8 tác tử (8-Agent Swarm Governance) trên thị trường Binance USD-M Futures:
- **3 Tác tử Alpha Định hướng (60%)**:
  - `NORO` (20%): Hồi quy giá trị hợp lý VWAP Z-Score.
  - `LUMEN` (20%): Chênh lệch vi mô sổ lệnh L2 (OBI) và dòng tiền khớp (CVD/OFI).
  - `TIDAL` (20%): Xung lực chéo thị trường (Cross-sectional Momentum), quét thanh khoản (Sweeps) và RSI stretch.
- **5 Tác tử Cổng Điều kiện & An toàn (40%)**:
  - `ZEPHR` (10%): Kiểm soát thanh khoản & tỷ lệ Lợi thế kỳ vọng / Chi phí sàn ($\text{Edge} / \text{Cost} \ge \text{hurdle}$).
  - `RUNE` (10%): Cổng an toàn rủi ro tối cao, VPIN độc hại ($VPIN > \text{toxic\_vpin}$), drawdown cap.
  - `OKAPI` (8%): Quản lý dung lượng đòn bẩy Margin $\le 75\%$.
  - `MARIN` (7%): Bộ ba rào cản Triple Barrier (SL, TP, Deadline).
  - `VESKA` (5%): Cổng bắn lệnh thực thi có vé số HMAC-SHA256 AuthToken một lần.

### 4 Điểm nghẽn cốt tử vừa được khắc phục:
1. **Bẫy trần đồng thuận 54.6% - 60%**: Khi thị trường bình thường, NORO và TIDAL ở HOLD (0%), chỉ có LUMEN phát tín hiệu (14.2%) + 5 cổng an toàn (40%) $\to$ Tổng điểm trần chỉ đạt $54.2\% \le 60\%$. Ngưỡng cũ cài cứng $76.8\%$ làm hệ thống đóng băng. Đã sửa: tham số hóa `threshold = 0.50` cho Testnet Demo (`--bootstrap`) để mở khóa Solo-Alpha Execution.
2. **Lỗi xóa sổ xô dữ liệu (Trade-gap flow wipeout)**: Dòng `if trade_id > last_trade_id + 1: alpha.reset_flow()` cũ đã xóa sạch 1.000 lệnh nạp từ REST API khi vừa mở WebSocket. Đã loại bỏ hoàn toàn cơ chế reset sai này và đặt `VPIN_MIN_BUCKETS = 3` $\to$ Cả 8 cặp tiền READY ngay giây đầu tiên.
3. **Bão hòa VPIN trên Testnet**: Các bot testnet xả 70.000 ETH ảo đẩy VPIN lên 0.997 SAT. Đã nâng `toxic_vpin = 0.95` trong chế độ Testnet Demo.
4. **Lỗi TokenAuthority hardcoded**: Hàm `TokenAuthority.issue()` cũ chặn cứng `score < 0.768`, quăng lỗi `Consensus denied` và ngắt cổng VESKA. Đã truyền tham số `threshold` động vào `TokenAuthority`.

### Bằng chứng thực nghiệm sống:
- Kiểm thử tự động: **95/95 test cases PASS** (`pytest -v`).
- Lệnh thật đã khớp trên Binance USD-M Testnet:
  - `LINKUSDT SELL GTX 4.04 @ 12.2340` (STATUS: FILLED).
  - Cắt lỗ bảo vệ đặt trực tiếp trên sàn: `Stop Loss = 12.2450 (ON EXCHANGE)`.
  - Web UI Telemetry hoạt động trực tiếp tại `http://127.0.0.1:8765`.

---

## 🎯 2. CÁC NHIỆM VỤ NÂNG CẤP LƯỢNG TỬ DÀNH CHO CLAUDE FABLE 5.1

Bạn có toàn quyền sử dụng các công cụ của Cline để đọc, chỉnh sửa mã nguồn và chạy terminal tại `F:\he_thong_da_tac_tu_2`. Hãy thực hiện lần lượt các nhiệm vụ sau:

### 🧩 NHIỆM VỤ 1: ĐỘT PHÁ CÔNG THỨC ALPHA EDGE (TĂNG WIN-RATE & KỲ VỌNG LÃI)
Xem xét tệp `core/groktopus_swarm.py` và `core/real_alpha_engine.py`:
1. **Nâng cấp `LUMEN` với Stoikov Micro-Price**:
   - Hiện tại: `math.tanh(1.5*f.obi + f.flow + .2*f.ofi)`
   - Hãy tích hợp **Stoikov Micro-Price** kết hợp độ dốc chênh lệch sổ lệnh (Spread-adjusted Micro-price):
     $$P_{micro} = \frac{Q_b \cdot P_a + Q_a \cdot P_b}{Q_b + Q_a}$$
     $$\Delta_{micro} = \frac{P_{micro} - P_{mid}}{\text{tick}}$$
   - Đưa $\Delta_{micro}$ và gia tốc dòng tiền vào công thức của `LUMEN`.
2. **Nâng cấp `TIDAL` với Cross-Sectional Lead-Lag Arbitrage**:
   - Bitcoin và Ethereum thường dẫn sóng trước các Altcoins (SOL, DOGE, ADA, LINK) từ 200ms đến 2 giây.
   - Khi $\text{Momentum}_{BTC} > 1.5\sigma$ hoặc $\text{Momentum}_{ETH} > 1.5\sigma$, truyền tín hiệu Lead-Lag này để `TIDAL` dự phóng sóng bứt phá của Altcoins trước khi sổ lệnh Altcoin kịp phản ứng.
3. **Nâng cấp `NORO` với Dải Biến động Động (Dynamic Volatility Bands)**:
   - Thay vì chia cố định cho 2 (`-f.vwap_z/2`), chuẩn hóa độ lệch VWAP theo chế độ nén/giãn biến động (ATR Ratio).

---

### 🧩 NHIỆM VỤ 2: GIẢI QUYẾT BẪY PHÍ SÀN & ĐỊNH GIÁ LỆNH AVELLANEDA-STOIKOV
Phí sàn Binance USD-M Futures (VIP 0) là:
- Maker (GTX Limit): `2.0 bps` (0.0002)
- Taker (Market Stop): `5.0 bps` (0.0005)
- Round-trip Fee: `7.0 bps`. Nếu cộng thêm nửa spread và trượt giá, chi phí là $\approx 8.5\text{ bps}$.

Nếu biên độ nến 1 phút chỉ là $3 - 4\text{ bps}$, mọi giao dịch lướt sóng tần số cao đều bị phí sàn bào mòn!
Xem xét `core/groktopus_swarm.py` và `core/barrier_planner.py`:
1. **Định giá lệnh Maker tối ưu (Avellaneda-Stoikov Reservation Price)**:
   $$R(s, q, t) = s - q \gamma \sigma^2 (T - t)$$
   - Đặt lệnh Post-Only Maker GTX không đơn thuần là peg cứng vào Best Bid/Ask, mà tính toán mức giá đặt lệnh tối ưu theo lượng tồn kho $q$ và phương sai $\sigma^2$.
2. **Khắc phục Lựa chọn Bất lợi (Adverse Selection)**:
   - Tránh khớp lệnh Maker ngay trước các đợt quét độc hại (Toxic Sweeps). Khi $VPIN$ tăng đột biến hoặc $CVD$ đảo chiều đột ngột, tự động hủy/dời lệnh Maker ra xa vùng nguy hiểm.

---

### 🧩 NHIỆM VỤ 3: CƠ CHẾ ĐỒNG THUẬN ĐỘNG THÍCH ỨNG CHẾ ĐỘ THỊ TRƯỜNG
Xem xét `core/consensus_engine.py` và `core/groktopus_swarm.py`:
1. **Adaptive Quorum Threshold**:
   - Trong thị trường **Trending Breakout** ($|\text{Momentum}_{\sigma}| > 1.2$): Tự động hạ ngưỡng đồng thuận xuống để bắt sóng dứt khoát.
   - Trong thị trường **Chop / Sideway hẹp**: Tự động nâng ngưỡng lên cao ($70\% - 76.8\%$) để lọc toàn bộ tín hiệu nhiễu giả.
   - Trong thị trường **Toxic Liquidity Sweep**: Kích hoạt VETO bảo vệ vốn ngay lập tức.
2. **Bayesian Belief Weighting**:
   - Trọng số 8 tác tử không nên cố định mãi mãi mà có thể cập nhật linh hoạt theo hiệu suất PnL gần nhất.

---

### 🧩 NHIỆM VỤ 4: QUẢN TRỊ VỐN FRACTIONAL KELLY DƯỚI PHÂN PHỐI ĐUÔI DÀY
Xem xét `core/position_sizer.py`:
1. **Fat-Tailed Adjusted Kelly**:
   - Thị trường crypto có phân phối lợi nhuận đuôi cực dày (Fat Tails) và độ lệch âm (Negative Skewness).
   - Nâng cấp công thức Kelly từ chuẩn Gaussian sang công thức Kelly có hiệu chỉnh bậc 3 (Skewness) và bậc 4 (Kurtosis) để triệt tiêu hoàn toàn nguy cơ sụt giảm vốn dây chuyền.

---

### 🧩 NHIỆM VỤ 5: KIỂM THỬ TỰ ĐỘNG VÀ XÁC THỰC THỰC CHIẾN
1. Sau khi chỉnh sửa mã nguồn, chạy ngay trong terminal:
   ```powershell
   pytest -v
   ```
   Yêu cầu: **100% test cases phải PASS (không được làm hỏng bất kỳ test an toàn nào trong 95 tests hiện có)**. Viết thêm các unit test mới để kiểm chứng các tính năng vừa nâng cấp.
2. Chạy kịch bản pre-flight:
   ```powershell
   python tools/preflight_testnet.py
   ```
3. Khởi động hệ thống chạy thử nghiệm trên sàn Testnet:
   ```powershell
   python run_binance_testnet.py --bootstrap --seconds 30
   ```
   Kiểm tra tệp `data/testnet-last-state.json` và `data/testnet-journal.sqlite` để xác nhận các vị thế mới được mở, khớp và bảo vệ thành công.

---

## 📋 3. HƯỚNG DẪN HÀNH ĐỘNG DÀNH CHO CLAUDE FABLE 5.1
- **Bước 1**: Đọc và nắm vững các file lõi: `core/consensus_engine.py`, `core/groktopus_swarm.py`, `core/real_alpha_engine.py`, `core/barrier_planner.py`, `core/position_sizer.py`, `core/runtime.py`.
- **Bước 2**: Thực hiện nâng cấp mã nguồn một cách phẫu thuật, giữ nguyên docstring và tính tương thích cao.
- **Bước 3**: Tự chạy terminal kiểm thử (`pytest -v`), sửa lỗi lặp cho đến khi toàn bộ test xanh 100%.
- **Bước 4**: Báo cáo tóm tắt các cải tiến định lượng, công thức toán học và kết quả kiểm thử cho người dùng.
```
