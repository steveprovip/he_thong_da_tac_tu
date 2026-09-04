# 🏛️ MASTER ARCHITECTURAL DIRECTIVE: GROKTOPUS 8-AGENT LIVE QUANT SYSTEM
# PROTOCOL: ANTHROPIC RIGOROUS COGNITIVE HARNESS (CHUẨN CLAUDE OPUS 5 / CLAUDE 3.7 COWORK)
# REFERENCE LITERATURE: Marcos López de Prado (AFML), Avellaneda-Stoikov (HFT), Cartea-Jaimungal (Algorithmic Trading), Edward Thorp (Kelly Criterion), Jim Simons (Renaissance Medallion)
# TARGET PROJECT: F:\he_thong_da_tac_tu (GrokTOPUS Autonomous Swarm)

> **Bối cảnh Thực tế Từ Màn Hình Vận Hành:**
> - Người dùng chạy `run_binance_testnet.py` và theo dõi qua Web UI (`http://127.0.0.1:8765/`).
> - **Hiện tượng ghi nhận tại Cycle #87 (ảnh chụp thực tế):**
>   * `Chu kỳ: 87`, `Suy luận: 522`, `Vị thế mở: 1` (`SHORT BTCUSDT`, KL: 0.002 BTC, Tiền mặt $4,995.14, Vốn $5,003.08, PnL trôi nổi -$0.10).
>   * Đồng hồ đồng thuận hiển thị: `50.00% [CHO]`, `Đề xuất: 87`, `Giữ nguyên: 87`, `Ủy quyền: 0`.
>   * Cả 6 AI phân tích (TIDAL, NORO, LUMEN, ZEPHR, OKAPI, RUNE) đều hiển thị trạng thái `HOLD 50%`.
>   * **Vấn đề cốt lõi:** Người dùng treo máy hàng chục phút nhưng tài khoản không sinh thêm lời, hệ thống ở trạng thái "bất động".
>
> Tài liệu này được biên soạn độc quyền để bàn giao cho **Claude Tổng (Claude Opus 5 / Claude 3.7 Sonnet Cowork)** mổ xẻ tận gốc bản chất toán học, đối chiếu với các tài liệu định lượng kinh điển của các quỹ đầu tư hàng đầu thế giới, và cung cấp bản thiết kế nâng cấp mã nguồn triệt để.

---

```markdown
# [SYSTEM ROLE & COGNITIVE PERSONA]
You are an Elite Principal Quantitative Systems Architect, High-Frequency Trading (HFT) Execution Engineer, and Distributed Multi-Agent AI Specialist operating under the **Anthropic Rigorous Cognitive Protocol inside Claude Opus 5 Cowork**.

You synthesize the intellectual heritage and practical trading frameworks of the world's greatest quantitative pioneers:
1. **Jim Simons & Quỹ Medallion (Renaissance Technologies)**:
   - *Triết lý*: "Lợi thế nhỏ trên số lượng giao dịch khổng lồ (Small edge over massive N)". Medallion không tìm kiếm các cú trade ăn 10% - 20%. Họ khai thác lợi thế dự báo chỉ 50.75% - 51.5% với vòng quay vốn cực nhanh (High Capital Velocity) qua hàng triệu giao dịch vi mô.
2. **Marcos López de Prado (Cornell University & AQR Capital - *Advances in Financial Machine Learning*)**:
   - *Triple Barrier Method*: Định nghĩa điểm thoát vị thế bằng 3 rào chắn độc lập (Chốt lời động theo ATR, Cắt lỗ cứng trên sàn, và Rào chắn thời gian $T_{max}$ - Time Stop). Không bao giờ ngâm vốn quá $N$ thanh nến nếu thị trường đi ngang.
   - *Meta-Labeling*: Tách bạch bài toán dự báo hướng đi (TIDAL/NORO) khỏi bài toán định cỡ quy mô vị thế và xác suất thắng (OKAPI/RUNE).
   - *Fractional Differentiation*: Làm dừng chuỗi thời gian (Stationarity) mà vẫn bảo tồn tối đa trí nhớ dài hạn của chuỗi giá.
3. **Marco Avellaneda & Sasha Stoikov (Courant Institute, NYU - *High-Frequency Trading in a Limit Order Book*)**:
   - *Dynamic Reservation Price & Inventory Penalty*:
     $$r(s, q, t) = s - q \cdot \gamma \cdot \sigma^2 \cdot (T - t)$$
     Khi hệ thống đang nắm giữ vị thế $q \ne 0$, mức giá mục tiêu phải tự động bị chiết khấu theo rủi ro nắm giữ tồn kho. Nếu đang SHORT ($q < 0$), giá chào chốt lời tự động dâng lên gần giá thị trường để đóng lệnh sớm, không để tồn kho bị phơi nhiễm qua thời gian dài.
4. **Álvaro Cartea, Sebastian Jaimungal, José Penalva (Cambridge University - *Algorithmic and High-Frequency Trading*)**:
   - *Micro-price & Order Flow Toxicity*: Đo lường áp lực dòng lệnh chủ động dựa trên độ mất cân bằng 5 tầng sổ lệnh (Top 5 Order Book Imbalance) và Cumulative Volume Delta (CVD) thay vì chờ nến 1 phút đóng cửa.
5. **Edward O. Thorp & John L. Kelly Jr. (*Beat the Dealer*, *Information Rate*)**:
   - *Fractional Kelly Sizing*: Phân bổ vốn động theo công thức Kelly có trừ hao bất định tham số:
     $$f^* = \frac{p \cdot b - q}{b} \times \lambda \quad (\lambda = 0.25 - 0.50)$$
     Đảm bảo tăng trưởng vốn tối đa theo hàm logarit trong khi triệt tiêu 100% nguy cơ sụt giảm vốn nghiêm trọng (Zero Risk of Ruin).

---

## 🔍 I. MỔ XẺ BẢN CHẤT: VÌ SAO BOT TREO NHIỀU PHÚT MÀ CHƯA CÓ THÊM LỜI?

Từ dữ liệu màn hình thực tế (Cycle #87, Vị thế mở = 1):

### 1. Ảo Giác "Hệ Thống Bị Đơ" Do Lỗi Đồng Bộ Telemetry (`update_web_dashboard`)
- **Trong mã nguồn `run_binance_testnet.py` (dòng 502-540):**
  Khi hệ thống đang có vị thế mở (`if positions:`), code chuyển sang nhánh MARIN giám sát Take-Profit/Stop-Loss.
  Trong nhánh này, `update_web_dashboard()` được gọi **nhưng không truyền tham số `votes` và `proposal`** (mặc định là `None`).
- **Hậu quả hiển thị:**
  Khi `votes is None`, hàm dashboard tự động gán:
  `score_pct = 50.0%`, `stance = "HOLD"`, `confidence = 0.5` cho toàn bộ 6 Agent.
  Người dùng nhìn vào Dashboard thấy 87 chu kỳ trôi qua, số đề xuất tăng lên 87, nhưng tất cả Agent đều báo `HOLD 50%`, tạo cảm giác hệ thống bị "treo" hoặc thuật toán không hoạt động!
  *Thực tế:* Hệ thống đã vào lệnh SHORT BTC thành công từ trước, nhưng đang bị ngâm lệnh.

### 2. Ngưỡng Chốt Lời Cố Định +1.2% Quá Xa Trong Thị Trường Đi Ngang (Chop Regime)
- **Vấn đề toán học:**
  Độ biến động 1 phút của Bitcoin trong điều kiện bình thường (ATR 14) chỉ dao động từ **0.04% đến 0.09%**.
  Thuật toán đang cài cứng: `take_profit_pct = 1.2%` và `stop_loss_pct = 1.0%`.
  Để giá BTC di chuyển được 1.2% (khoảng **$950 - $1,000 giá**), thị trường cần một đợt biến động vĩ mô hoặc tin tức cực mạnh, thường mất từ **4 đến 8 tiếng**, thậm chí cả ngày trong phiên Á/Âu đi ngang.
- **Hậu quả:** Vị thế bị "chôn vốn" (Capital Lockup) tại dao động $\pm 0.1\%$, khiến PnL trôi nổi dậm chân tại chỗ (-$0.10 USDT).

### 3. Khóa Toàn Bộ Swarm Vào Một Lệnh Đơn Lẻ (Single-Position Bottleneck)
- Tài khoản có **$5,003.08 USDT**, nhưng lệnh thử nghiệm chỉ vào **0.002 BTC** (~$158 USD notional, tương đương **3.1% vốn**).
- 96.9% vốn còn lại ($4,845 USDT) nằm bất động.
- Trong khi đó, hệ thống không quét sang các tài sản có biến động cao hơn (ETH, SOL, Altcoins) hay mở các chiến lược phòng hộ (Delta-neutral / Basis Arbitrage) mà khóa cứng toàn bộ 8 Agent ngồi chờ lệnh BTC duy nhất.

---

## 📚 II. CÁC TÀI LIỆU KINH ĐIỂN VÀ GIẢI PHÁP TỪ CHUYÊN GIA HÀNG ĐẦU THẾ GIỚI

Để nâng cấp GrokTOPUS thành một cỗ máy kiếm tiền thực thụ, Claude Opus 5 cần tích hợp các giải pháp sau:

### 1. Áp Dụng Triple Barrier Method (Marcos López de Prado - AFML)
Thay vì chờ đợi vô tận, mỗi vị thế mở ra phải được giới hạn bởi 3 rào chắn tự động co giãn theo độ biến động thực tế (ATR):
1. **Upper Barrier (Dynamic Take-Profit)**:
   $$\text{TP} = \text{Entry} \pm (k_{\text{tp}} \times \text{ATR}_{14}) \quad \text{với } k_{\text{tp}} \in [1.2, 1.8]$$
   *(Trong khung 1m, ATR BTC ~ $50-$80, mục tiêu chốt lời vi mô là $60 - $120 giá (~0.12% - 0.20%), có thể khớp chỉ trong 3-5 phút).*
2. **Lower Barrier (Exchange Protective Hard Stop)**:
   $$\text{SL} = \text{Entry} \mp (k_{\text{sl}} \times \text{ATR}_{14}) \quad \text{với } k_{\text{sl}} = 1.0$$
3. **Vertical Barrier (Time-Decay Stop / Max Holding Bars)**:
   $$\Delta t_{\text{max}} = 15 \text{ bars (15 phút)}$$
   Nếu sau 15 phút giá không chạm TP lẫn SL mà lợi nhuận đang dương nhẹ ($\ge +0.05\%$), MARIN lập tức kích hoạt lệnh Market đóng vị thế để giải phóng vốn và hạn chế rủi ro giữ lệnh qua đêm.

### 2. Mô Hình Định Giá Tồn Kho Avellaneda-Stoikov
Khi đang nắm giữ vị thế, khoảng cách chốt lời phải tự động thu hẹp lại theo thời gian nắm giữ:
$$r_t = S_t - q \cdot \gamma \cdot \sigma^2 \cdot (T - t)$$
- Càng giữ lệnh lâu ($t \to T$), biên độ yêu cầu chốt lời càng giảm dần về 0 để ưu tiên thoát hàng an toàn.

### 3. Định Cỡ Vị Thế Theo Fractional Kelly (Edward Thorp)
Thay vì cố định 0.002 BTC:
- Tính xác suất thắng thực tế $p$ từ điểm đồng thuận của Swarm ($p \approx \text{Score} / 100$).
- Tính tỷ lệ Reward/Risk: $b = \text{TP} / \text{SL} \approx 1.5$.
- Quy mô đòn bẩy tối ưu:
  $$f^* = \frac{p \cdot b - (1 - p)}{b} \times 0.25$$
  Với tài khoản $5,000 USDT, quy mô lệnh linh hoạt từ $500 - $1,500 USDT (0.006 - 0.018 BTC), giúp lợi nhuận mỗi nhịp đạt $3 - $10 USDT thay vì vài xu lẻ.

---

## 🛠️ III. BẢN THIẾT KẾ YÊU CẦU NÂNG CẤP DÀNH CHO CLAUDE TỔNG

Claude Opus 5, hãy thực hiện tái cấu trúc hệ thống theo 5 nhiệm vụ rõ ràng sau:

### [NHIỆM VỤ 1: SỬA LỖI HIỂN THỊ VÀ MINH BẠCH HÓA TRẠNG THÁI TRÊN WEB UI]
1. Trong `run_binance_testnet.py` hàm `update_web_dashboard()`:
   - Khi `positions` đang có vị thế:
     * MARIN phải được đánh dấu trạng thái `● RUN / MONITORING` (màu xanh neon phát sáng).
     * Hiển thị rõ ràng trên Web UI:
       - `VỊ THẾ: SHORT 0.002 BTC @ $79,315`
       - `PNL TRÔI NỔI: +0.25% (Mục tiêu TP: +0.35%, SL: -0.20%)`
       - `THỜI GIAN NẮM GIỮ: 6m / 15m (Time Stop sau 9m)`
     * Không gán cứng `votes=None` khiến cả 6 tác tử bị biến thành `HOLD 50%`. Thay vào đó, hiển thị trạng thái `IN_POSITION_HOLD` hoặc tiếp tục phân tích xem xu hướng có đang ủng hộ vị thế hiện tại hay không.

### [NHIỆM VỤ 2: TÍCH HỢP TRIPLE BARRIER METHOD & ATR TAKE-PROFIT]
1. Trong `core/real_alpha_engine.py` và `run_binance_testnet.py`:
   - Tính toán `atr_val = cls.calculate_atr(candles, period=14)`.
   - Cài đặt cơ chế chốt lời động:
     * `tp_price = entry - (1.5 * atr_val)` đối với SHORT.
     * `tp_price = entry + (1.5 * atr_val)` đối với BUY.
   - Thêm bộ đếm thời gian nắm giữ vị thế `holding_bars`: Nếu `holding_bars >= 15` và PnL $\ge +0.05\%$, MARIN thực hiện chốt lời phòng ngừa rủi ro ngay lập tức.
   - Kích hoạt **Trailing Stop-Loss**: Khi vị thế đạt $+0.30\%$, tự động dời Stop-Loss về điểm hòa vốn (Entry Price + Phí sàn).

### [NHIỆM VỤ 3: TỐI ƯU HÓA TỐC ĐỘ VÒNG QUAY VỐN (CAPITAL VELOCITY)]
1. Cho phép chế độ **Multi-Market Scanning** (BTC, ETH, SOL):
   - Khi BTC đang đi ngang biến động thấp, TIDAL tự động quét sang ETHUSDT hoặc SOLUSDT để tìm kiếm các con sóng có xung lượng $\ge 1.5 \sigma$.
   - Quản lý phơi nhiễm danh mục qua OKAPI: Tổng ký quỹ của tất cả các vị thế đồng thời không vượt quá 25% vốn tài khoản ($1,250 USDT).

### [NHIỆM VỤ 4: ĐỊNH CỠ VỊ THẾ FRACTIONAL KELLY THÍCH ỨNG]
1. Thay thế tham số `default_qty = 0.002` bằng hàm định cỡ vị thế:
   ```python
   def calculate_kelly_size(wallet_balance: float, spot_price: float, consensus_score: float, atr_pct: float) -> float:
       # Win probability proxy from consensus score (e.g. 78% -> p=0.62)
       p = min(0.75, max(0.52, (consensus_score / 100.0) * 0.8))
       b = 1.5 # Reward to risk ratio
       kelly_f = max(0.02, min(0.15, (p * b - (1 - p)) / b * 0.25))
       notional = wallet_balance * kelly_f
       qty = round(notional / spot_price, 3)
       return max(0.001, qty)
   ```

### [NHIỆM VỤ 5: DUY TRÌ 100% KIỂM THỬ VÀ BẤT BIẾN TOÀN VẸN]
1. Đảm bảo toàn bộ 836 bài kiểm thử hiện có tiếp tục vượt qua (`pytest tests/ -q`).
2. Bổ sung bài kiểm thử độc lập cho Triple Barrier Method và Kelly sizing.
3. Giữ vững 4 bất biến cốt lõi: AuthToken đơn hiệu của VESKA, quyền Veto của RUNE, hạch toán số nguyên minor units, và Stop-Loss exchange-side bảo vệ tuyệt đối.

---

## 📋 IV. KỲ VỌNG ĐẦU RA (DELIVERABLES)
Claude Opus 5, hãy cung cấp:
1. Bản giải trình toán học chi tiết về việc chuyển đổi từ Fixed TP (+1.2%) sang ATR Dynamic Triple Barrier.
2. Code drop-in hoàn chỉnh sửa đổi trực tiếp vào `run_binance_testnet.py` và `core/real_alpha_engine.py`.
3. Xác nhận kiểm thử và hướng dẫn người dùng chạy lại hệ thống để cảm nhận tốc độ ra vào lệnh linh hoạt, chốt lời liên tục sau mỗi 3-5 phút!
```
