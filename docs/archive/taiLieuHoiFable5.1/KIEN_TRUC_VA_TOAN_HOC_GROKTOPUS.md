# 🏛️ KIẾN TRÚC HỆ THỐNG VÀ NỀN TẢNG TOÁN HỌC GROKTOPUS 2.0
### HỆ THỐNG GIAO DỊCH ĐA TÁC TỬ TẦNG SỐ CAO (HFT QUANT MULTI-AGENT SWARM)

---

## 1. TRIẾT LÝ HỆ THỐNG: KHÔNG PHẢI MỘT BOT, MÀ LÀ MỘT SÀN ĐẤU GIÁ ĐỒNG THUẬN TỰ TRỊ

Trong các bot giao dịch truyền thống, AI trực tiếp thay đổi số dư ví (`AI -> Wallet -> Trade`). Mô hình này dẫn tới thảm họa khi thị trường biến động cực đoan hoặc khi một mô hình AI hallucinate (ảo giác).

Ở **GrokTOPUS 2.0**, không một tác tử đơn lẻ nào được phép tự ý chạm vào tiền. Vốn chỉ có thể di chuyển khi đạt **Đại đa số đồng thuận (Supermajority Consensus)** của 8 tác tử chuyên biệt, được đóng dấu số bằng vé cryptographic một lần (**single-use AuthToken**) và được kiểm soát bởi các rào cản toán học nghiêm ngặt.

---

## 2. PHÂN NHIỆM 8 TÁC TỬ VÀ VAI TRÒ TOÁN HỌC

Hệ thống phân tách rạch ròi giữa **Tín hiệu Định hướng (Alpha)** và **Điều kiện Khả thi & Rủi ro (Gates)**:

### A. Nhóm Alpha Định Hướng (Directional Alpha Agents - Tổng trọng số = 60%)

1. **`NORO` (Fair-Value & VWAP Arbitrageur) - Trọng số: 20% (0.20)**
   - **Nhiệm vụ**: Dự báo xu hướng hồi quy giá trị hợp lý (Fair-Value Mean Reversion).
   - **Đầu vào**: Tỷ số chuẩn hóa VWAP Z-Score ($Z_{vwap}$) và độ biến thiên giá dài hạn.
   - **Công thức tín hiệu**:
     $$\text{signal}_{NORO} = \tanh\left(-\frac{Z_{vwap}}{2} + 0.25 \times \text{momentum}\right)$$
   - **Quy ước**: $> 0.05 \to \text{BUY}$, $< -0.05 \to \text{SELL}$, còn lại $\to \text{HOLD}$.

2. **`LUMEN` (Order Flow & Microstructure Sentiment) - Trọng số: 20% (0.20)**
   - **Nhiệm vụ**: Bắt sóng dòng tiền vi mô trong sổ lệnh (L2 Limit Order Book Imbalance & Order Flow).
   - **Đầu vào**: Order Book Imbalance ($OBI$), Order Flow Imbalance ($OFI$), và Cumulative Volume Delta ($CVD$).
   - **Công thức tín hiệu**:
     $$\text{signal}_{LUMEN} = \tanh(1.5 \times OBI + \text{flow} + 0.2 \times OFI)$$
   - **Ý nghĩa**: $OBI = \frac{\sum Bid - \sum Ask}{\sum Bid + \sum Ask}$ trên 5 bước giá sâu nhất.

3. **`TIDAL` (Volatility & Sweep Scanner) - Trọng số: 20% (0.20)**
   - **Nhiệm vụ**: Đo đạc xung lực chéo thị trường (Cross-sectional Momentum), quét thanh khoản (Liquidity Sweeps) và quá mua/quá bán RSI.
   - **Công thức tín hiệu**:
     $$\text{signal}_{TIDAL} = \tanh\left(0.6 \times \text{momentum} + 0.3 \times \text{cross\_mom} + 0.5 \times \text{sweep} + \frac{50 - RSI}{100}\right)$$

---

### B. Nhóm Cổng An Toàn & Điều Kiện Sàn (Suitability & Safety Gates - Tổng trọng số = 40%)

Các tác tử này không tự tạo ra hướng mua/bán độc lập mà đánh giá tính khả thi. Nếu thỏa mãn điều kiện, họ sẽ bỏ phiếu ủng hộ chiều đi của 3 tác tử Alpha với độ tin cậy 100% (1.0). Nếu vi phạm, họ lập tức **VETO (phủ quyết tuyệt đối)**:

4. **`ZEPHR` (Liquidity & Cost Gate) - Trọng số: 10% (0.10)**
   - **Nhiệm vụ**: Đảm bảo tỷ lệ Lợi thế kỳ vọng / Chi phí sàn ($\text{Edge} / \text{Cost}$) thỏa mãn ngưỡng yêu cầu.
   - **Mô hình Chi phí (GTX Maker Entry + Exit)**:
     $$\text{cost}_{bps} = (\text{maker\_fee} + \text{taker\_fee}) \times 10000 + \frac{\text{spread}_{bps}}{2} + \text{slippage}_{bps}$$
   - Lệnh GTX Maker chỉ trả phí Maker ($2\text{ bps}$), nhưng vòng thoát lệnh (Stop Loss taker) chịu phí Taker ($5\text{ bps}$). Tổng chi phí sàn là $\approx 8.0 - 10.0\text{ bps}$.

5. **`RUNE` (Supreme Risk Guardian) - Trọng số: 10% (0.10)**
   - **Nhiệm vụ**: Cổng an toàn rủi ro tối cao. Có quyền phủ quyết một chiều không thể đảo ngược.
   - **Điều kiện VETO**:
     - Phát hiện dòng tiền độc hại: $VPIN > \text{toxic\_vpin}$ (Easley et al. 2012).
     - Tài khoản drawdown vi phạm giới hạn.
     - Điểm đồng thuận toàn đàn rơi xuống dưới ngưỡng tối thiểu $\text{veto\_threshold}$.

6. **`OKAPI` (Capital Capacity & Kelly Sizer) - Trọng số: 8% (0.08)**
   - **Nhiệm vụ**: Kiểm soát tỷ lệ đòn bẩy danh mục không vượt quá 75% Margin Capacity.

7. **`MARIN` (Exit Readiness & Triple Barrier) - Trọng số: 7% (0.07)**
   - **Nhiệm vụ**: Kiểm tra tính sẵn sàng của bộ ba rào cản: Take Profit ($2.5 \times ATR$), Stop Loss ($1.5 \times ATR$), và Thời hạn nắm giữ ($H = 300s$).

8. **`VESKA` (Authenticated Execution Gateway) - Trọng số: 5% (0.05)**
   - **Nhiệm vụ**: Cổng bắn lệnh thực thi. Chỉ chấp nhận thực thi lệnh khi có vé mã hóa `AuthToken` hợp lệ, chưa hết hạn (TTL = 5s) và khớp chính xác mã băm SHA-256 của đơn vị lệnh (`Intent`).

---

## 3. CÔNG THỨC ĐỒNG THUẬN VÀ QUORUM GATING

Tổng trọng số của 8 tác tử:
$$\sum_{i=1}^{8} w_i = 0.20 + 0.20 + 0.20 + 0.10 + 0.10 + 0.08 + 0.07 + 0.05 = 1.00$$

Điểm đồng thuận cho chiều Mua ($BUY$) hoặc Chiều Bán ($SELL$):
$$\text{Score}(S) = \sum_{i \in \text{Agents}, \text{side}_i = S} w_i \times \text{confidence}_i$$

- **Quy tắc Phê duyệt (Authorized)**:
  $$\text{Authorized} \iff \left(\text{Score} \ge \text{Threshold}\right) \land \left(\neg \text{Any Veto}\right)$$
- **Quy tắc Phủ quyết (RUNE VETO)**:
  $$\text{Veto} \iff (\exists v \in \text{Votes}: v.\text{veto} = \text{True}) \lor (\text{Score} < \text{VetoThreshold})$$

---

## 4. BỘ BA RÀO CẢN ĐỘNG (DYNAMIC TRIPLE BARRIER)

Mỗi vị thế mở đều được bao bọc bởi cấu trúc 3 rào cản thời gian thực:
1. **Rào cản Chốt lời (Upper/Lower Take Profit)**:
   $$\text{TP} = \text{Entry} \pm \max(2.5 \times ATR_{1m}, 2 \times \text{tick})$$
2. **Rào cản Cắt lỗ trên sàn (On-Exchange Stop Loss)**:
   $$\text{SL} = \text{Entry} \mp \max(1.5 \times ATR_{1m}, 2 \times \text{tick})$$
3. **Rào cản Thời gian (Time Barrier)**:
   $$\text{Deadline} = \text{Timestamp} + 300\text{ seconds}$$
4. **Cơ chế Khóa Lời Tự Động (Breakeven Ratchet)**:
   Khi vị thế có lãi $\ge 0.25\%$, rào cản SL lập tức được kéo lên dời về giá vào lệnh cộng phí sàn (`Entry + Fees`), loại bỏ hoàn toàn rủi ro thua lỗ cho phần thời gian còn lại.
