# Vòng (y) 08/10/2026 — Nâng vị thế lên 10 % ngân sách có khả quan không?

Form: `prompts/FORM_CLAUDE_OPUS5.5_POSITION_SIZING_10PCT_BUDGET_2026-10-08.md`. Nghiên cứu + công cụ + cơ chế; không
khởi động lại engine, không gửi lệnh, không đổi size đang chạy, không bật live. Ledger gốc không bị ghi.

## 1. Số liệu tính lại (snapshot `snapshot_2026-10-08y/`, SHA256SUMS)
| Mục | Form | Ledger thực tế |
|---|---|---|
| Số lệnh | 47 | **46** dòng |
| Hôm nay (Asia/Saigon) | 5W / 9L, win 55.6 % | **9 lệnh, 5 thắng / 4 thua** (55.6 %), net −0.0684 U |
| SOL | win 55.6 %, RR net 1.94 | 15 lệnh, 9 thắng (60 %), net **+0.266 U** |
| SOL tính theo % notional | — | **trung bình −1.98 bps/lệnh**, CI95 [−14.6, +9.1], P(trung bình ≤ 0) = 61 % |
| Thắng / thua trung bình SOL | +0.095 / −0.091 U | **+11.4 bps / −22.0 bps** → RR thực hiện 0.52 |
| Toàn ledger | — | −3.6 bps/lệnh [−8.0, +0.7], thắng 34.8 % |
"RR net 1.94" là RR **kế hoạch** của bracket (dashboard), không phải RR đã thực hiện. SOL lãi theo USDT nhưng âm theo
bps: lãi đến từ việc các lệnh thắng sớm có size lớn hơn, không phải từ lợi thế mỗi đồng vốn. Lệnh #45: +21.2 bps trên
$45.9 notional (= +1.06 % ROE ở đòn bẩy 5x).

## 2. Hai định nghĩa "10 %"
- **A — 10 % notional** ($500 ≈ 4.35 SOL ở $115): rủi ro tại stop = $500 × (0.35 % + 0.08 % phí) ≈ **$2.15 = 0.043 %
  vốn**. Lệnh như #45 sẽ là +$1.06. Rủi ro cháy tài khoản ≈ 0. Nhưng **vượt `portfolio_risk_usdt` = 1.0 U hiện tại**
  (RUNE sẽ từ chối/cắt) và trần notional $140 của ALT_SIZE.
- **B — 10 % vốn mất tại stop**: notional = 0.10 / 0.0043 = **23× vốn** → cần đòn bẩy 23x (tài khoản đặt 5x). 5 stop
  liên tiếp còn 0.9⁵ = 59 % vốn. Không khả thi và không chấp nhận được.

## 3. Monte Carlo (`tools/simulate_position_sizing.py`, 10 000 đường × 200 lệnh, vốn 5 000 U)
Lợi nhuận mỗi lệnh = PnL / notional (đã gồm phí), nhân với tỉ lệ notional/vốn. `sizing_montecarlo_2026-10-08y.json`.

SOL (n = 15), bootstrap iid:
| Notional / vốn | PnL trung vị 200 lệnh | P(lỗ) | Max DD p95 | P(DD ≥ 50 %) |
|---|---|---|---|---|
| 0.9 % (hiện tại ~$46) | −1.75 U | 89 % | 0.09 % | 0 |
| 2 % | −3.89 U | 89 % | 0.21 % | 0 |
| 5 % | −9.72 U | 89 % | 0.51 % | 0 |
| **10 %** | **−19.4 U** | 89 % | **1.03 %** | 0 |
| 15 % | −29.2 U | 89 % | 1.54 % | 0 |
| B (10 % rủi ro) | −3 520 U | 94 % | 93.5 % | **94.6 %** |
Các kịch bản khác ở 10 %: block bootstrap (giữ chuỗi) −19.1 U; **null** (bỏ lợi thế, giữ độ phân tán) +0.3 U,
P(lỗ) 49 %, DD p95 0.72 %; **stress** (−2 bps/lệnh khi size lớn) −39.3 U, P(lỗ) 99 %. Toàn ledger 10 %: −35.5 U.
Biên theo CI của trung bình SOL ở 10 % / 200 lệnh: +9.1 bps → **+$91**; −14.6 bps → **−$146**.

Kết luận: (1) ở 10 % notional, **rủi ro cháy không phải vấn đề** — DD p99 chỉ ~1.2 %; (2) nhưng size **nhân kỳ vọng**,
không tạo ra kỳ vọng: với mẫu hiện tại kỳ vọng âm hoặc chưa phân biệt được với 0; Kelly f* = mean/var **< 0** cho
SOL và toàn ledger → Kelly (kể cả Quarter-Kelly) khuyên size **0**, không phải 10 %. Sharpe mỗi lệnh (−0.08 SOL) không
đổi theo size. (3) Phương án B dẫn tới cháy tài khoản trong mọi kịch bản kể cả null (75 %).

## 4. Thanh khoản & trượt giá (`liquidity_depth_2026-10-08y.json`, `liquidity_takerflow_2026-10-08y.json`)
**Độ sâu hiển thị** (8 ảnh depth20, ~16:5x): trượt khi taker 4.35 SOL / 5 500 DOGE = **0 bps** trên cả demo và mainnet
(touch demo SOL 543–6 740 SOL, mainnet 717–1 145 SOL). Spread demo DOGE **9–11 bps** (mainnet 1.1). Tác động giá
(Almgren–Chriss) không phải ràng buộc ở cỡ này.

**Khả năng khớp maker** (cận trên, bỏ qua hàng đợi phía trước; 30 phút aggTrades): P(khối lượng taker đối ứng trong 3 s
≥ size)
| | 0.4 SOL | 1.5 | 2.0 | 4.35 SOL | 300 DOGE | 2 000 | 5 500 |
|---|---|---|---|---|---|---|---|
| Demo | 33 % | 14 % | 13 % | **10 %** | 29 % | 16 % | 15 % |
| Mainnet | 87 % | 78 % | 75 % | 66 % | 71 % | 48 % | 38 % |
Trên demo, size lớn → khớp một phần nhiều hơn (đã thấy 0.49/0.80, 0.13/0.80 hôm nay), không trượt giá. Hàng đợi demo
(hàng trăm–nghìn SOL phía trước) làm con số thật thấp hơn nhiều.

## 5. Dynamic Conviction Sizing (đã triển khai, **shadow**)
`Engine.conviction_size()`: SOL/DOGE, lệnh TREND AUTO:
| Tín hiệu | Notional / ví |
|---|---|
| score 0.80–0.90 | 3 % |
| score 0.90–0.95 (hoặc ≥ 0.95 mà OFI < 0.85) | 5 % |
| score ≥ 0.95 và OFI ≥ 0.85 cùng chiều | 10 % |
Size bị **cắt** (không bị từ chối) theo ngân sách rủi ro còn trống (`portfolio_risk_usdt`, gồm lệnh đang mở) và trần
margin (`portfolio_margin_frac`); không nâng trần nào. Với trần 1.0 U hiện tại, 10 % SOL bị cắt còn ≈ $232 (~2 SOL).
Ratchet 2, EJECT-FAST, RUNE, stop-side giữ nguyên; MR/SCALP không đổi size.
- `conviction_sizing='shadow'` (mặc định demo): size không đổi; order row ghi `cs_tier/cs_frac/cs_qty/cs_notional/cs_clip`,
  ledger ghi `conviction` → đánh giá ngoài mẫu. `'on'`: dùng size này. Live: `off`.

Vì sao shadow: score chưa phân biệt kết quả — ledger alt score ≥ 0.95: 11 lệnh, −1.0 bps; 0.80–0.95: 5 lệnh, −10.2 bps;
vòng w: 54 attempt score ≥ 0.95 → giá 5 phút trung bình −0.3 bps. Định cỡ theo độ tin cậy chỉ có lợi khi độ tin cậy
được hiệu chuẩn.

## 6. Khuyến nghị
1. **Không nâng lên 10 %** khi kỳ vọng mỗi lệnh chưa dương có ý nghĩa. Ở mẫu này 10 % chỉ phóng đại khoản lỗ kỳ vọng
   (−$19 / 200 lệnh) dù rủi ro cháy rất thấp.
2. Nếu vẫn muốn tăng trên **demo** để thu dữ liệu nhanh hơn: dùng phương án A, bước nhỏ (2 % → 5 %), bật
   `conviction_sizing='on'` và nâng `portfolio_risk_usdt` có chủ đích (ví dụ 2.5 U cho 10 % SOL), đặt trước tiêu chí
   dừng: sau ≥ 50 lệnh cùng policy, nếu CI95 của bps/lệnh không > 0 thì quay về mức cũ. Không bao giờ dùng phương án B.
3. Live giữ nguyên ≤ 3.85 U vốn, ≤ 0.05 U lỗ/lệnh.

## 7. Kiểm thử
`python -m pytest -q` → **1272 passed** (trước 1249; +23 trong `tests/test_position_sizing_10pct.py`). `git diff --check`
sạch. Fixture cũ cô lập `conviction_sizing='off'`.

## 8. Nguồn
- Thorp, *The Kelly Criterion in Blackjack, Sports Betting, and the Stock Market* (2007, Handbook of Asset and Liability
  Management): f* = μ/σ² (liên tục); ước lượng μ sai → over-betting; dùng phân số Kelly.
- Vince, *The Mathematics of Money Management* (1992): Optimal f và rủi ro cháy theo chuỗi thua.
- Almgren & Chriss, *Optimal Execution of Portfolio Transactions* (J. Risk, 2000): chi phí tác động tăng theo size so
  với thanh khoản — ở 4.35 SOL độ sâu hiển thị đủ, ràng buộc là khả năng khớp maker.
Giới hạn: 15 lệnh SOL qua nhiều phiên bản policy; Monte Carlo giả định lợi nhuận/notional không đổi theo size và các
lệnh độc lập; ảnh depth/aggTrades là 30 phút buổi chiều 08/10.

## 9. Rollback
`conviction_sizing='off'` (hoặc giữ shadow). Không có tham số size/trần nào đổi.
