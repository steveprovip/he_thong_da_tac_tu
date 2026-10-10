# Báo cáo nghiệm thu 10/10/2026 (d): đổi sàn và phí maker 0 %

**Phạm vi.**
- Replay offline trên dữ liệu thật của Binance USD-M:
  - kline 1m BTC/SOL/DOGE 2024-07 → 2026-09 cho làn intraday;
  - aggTrades SOL/DOGE USDT + USDC 2026-09-01 → 10-09 (152 symbol-ngày) cho làn burst.
- Khảo sát API công khai của MEXC và Hyperliquid, cùng tài liệu chính thức của 4 sàn.
- Code adapter đa sàn ở dạng **prototype không gửi lệnh**.
- Không khởi động lại engine, không gửi lệnh nào, không đổi live, không tăng vốn/đòn bẩy.
- Không đụng tới `data/engine_trades.jsonl` hay `data/trend_trades.jsonl`.

## 0. Kết luận

| Câu hỏi của form | Trả lời (đo trên hold-out) |
|---|---|
| Đổi sang sàn phí maker 0 % thì có lãi không? | **Không.** Không chiến lược nào có net hold-out dương có ý nghĩa ở bất kỳ biểu phí nào ≥ 0, kể cả **phí = 0 và trượt giá = 0**. |
| Chiến lược nào "lập tức chuyển sang lãi ròng vững chắc" khi maker = 0 %? | **Không có.** Ở phí 0/2 (MEXC web), tốt nhất là P15_rr25atr −0.68 bps [−7.14, +6.97] và R1 −1.63. Burst −3.0, MM −3.1. |
| MEXC "maker 0 % vĩnh viễn, taker 0.02 %" | **Đúng cho giao diện web/app, sai cho bot.** Lệnh đặt qua API có biểu phí riêng: **0.06 % / 0.08 %** từ 2026-06-01, theo thông báo MEXC ngày 2026-05-28, đắt hơn Binance. Với phí đó mọi chiến lược ≈ −13 đến −15 bps/lệnh. |
| Hyperliquid "maker −0.2 bps / taker 2.5 bps" | **Sai cho tài khoản nhỏ.** Bậc cơ bản là 1.5 / 4.5 bps. Rebate −0.2 bps cần > 1.5 % khối lượng maker 14 ngày của **toàn sàn**. |
| BNB giảm 10 % (Binance) | Có thật. Chỉ cải thiện +0.6 bps/lệnh: −7.09 → −6.42. |
| USDC-M maker 0 % (Binance) | Khuyến mãi gần nhất **đã hết hạn 2025-12-10**. Replay trên **chính sổ USDC** vẫn âm (−3.5 đến −5.8 bps). |
| P15 R:R ≥ 2.5 (TP 100 / SL 40 bps) — Sharpe > 0? | **Không.** P15_rr25 −6.39 bps, Sharpe −4.0. Bản theo ATR (P15_rr25atr) −5.48 bps, Sharpe −1.8. |

**Tiền đề của form không đúng với số đo.**
- Form nói gross edge "+0.5 đến +1.2 bps". Thực tế:
  - Gross hold-out intraday từ −0.25 đến +1.73 bps, **CI 95 % đều chứa 0**. Trên DEV phần lớn âm.
  - Gross burst/MM **âm có ý nghĩa**: −2.2 đến −3.1 bps với fill "xuyên giá". Đó là adverse selection.
- Phí không phải nguyên nhân duy nhất. Thiếu edge mới là nguyên nhân gốc. Phí chỉ quyết định độ lớn của khoản lỗ.

Mọi biến thể đều **SHADOW** theo quy tắc đăng ký trước (n ≥ 100, cận dưới CI hold-out > 0, tỷ lệ thắng > 50 %), ở cả 9 kịch bản phí.

## 1. Phương pháp (`tools/venue_fee_replay.py`)
- **Phí không đổi việc chọn lệnh.** Không quy tắc vào/ra nào đọc phí. Vì vậy mỗi replay chạy **một lần** rồi định giá lại từng lệnh **chính xác**:

  `net = gross − maker (vào lệnh, luôn post-only) − (1 − τᵢ)·maker − τᵢ·(taker + slip)`

  τᵢ = `taker_frac` = phần notional đóng bằng lệnh taker. Mỗi row giờ ghi trường này: 0.5 cho TP1 + stop hòa vốn, 1 cho SL/TIME, 0 cho TP maker.
- **Kiểm tra khớp:**
  - Kịch bản A khớp số liệu vòng (b)/(c) đến 2 chữ số. Ví dụ P15_tp35 −7.07, S1_SESSION −7.09.
  - Có test xác nhận `reprice()` = `net_bps` của lane.
- **Trượt giá:** 1 bp trên chân taker intraday, như replay gốc. Burst đã khớp taker ở print sau độ trễ, nên không cộng thêm.
- **Chọn biến thể và kiểm định:** DEV-best theo từng nhóm, từng kịch bản, báo cáo trên HOLD-OUT. CI là bootstrap theo ngày (intraday) hoặc theo giờ (burst). $/tháng tính với $150/lệnh trên ví $5 000.
- **Giới hạn:** fill là fill của Binance. Sàn khác có sổ lệnh, độ sâu, hàng đợi và basis riêng. Đây là **tác động của phí**, không phải replay trên sàn đó. Mục 6 bàn thêm.

## 2. Intraday — HOLD-OUT 2026-01 → 09, 3 coin gộp

Net bps/lệnh [CI 95 %]:

| Kịch bản phí (maker/taker bps) | S1_SESSION | R1_SESSION | P15_tp35 | P15_tp50 | P15_rr25 | P15_rr25atr |
|---|---|---|---|---|---|---|
| A Binance VIP0 2/5 | −7.09 [−8.33, −5.82] | −6.51 [−10.47, −2.31] | −7.07 [−10.83, −3.04] | −6.92 [−11.35, −2.26] | −6.39 [−10.06, −2.71] | −5.48 [−11.97, +2.20] |
| A2 Binance + BNB 1.8/4.5 | −6.42 [−7.66, −5.14] | −5.85 [−9.80, −1.65] | −6.46 [−10.21, −2.43] | −6.29 [−10.72, −1.64] | −5.76 [−9.43, −2.09] | −4.84 [−11.32, +2.83] |
| B MEXC web / form 0/2 | −2.18 [−3.42, −0.91] | −1.63 [−5.57, +2.56] | −2.36 [−6.11, +1.65] | −2.17 [−6.59, +2.47] | −1.64 [−5.29, +2.01] | −0.68 [−7.14, +6.97] |
| **B2 MEXC qua API 6/8** | −14.18 [−15.42, −12.91] | −13.63 [−17.57, −9.44] | −14.36 [−18.11, −10.35] | −14.17 [−18.59, −9.53] | −13.64 [−17.29, −9.99] | −12.68 [−19.14, −5.03] |
| C1 form −0.2/2.5 | −2.42 [−3.66, −1.14] | −1.85 [−5.80, +2.35] | −2.46 [−6.21, +1.57] | −2.29 [−6.72, +2.36] | −1.76 [−5.43, +1.91] | −0.84 [−7.32, +6.83] |
| C2 form 1.0/2.5 | −3.72 [−4.96, −2.45] | −3.19 [−7.12, +0.99] | −4.01 [−7.75, +0.00] | −3.79 [−8.21, +0.84] | −3.26 [−6.90, +0.38] | −2.28 [−8.72, +5.35] |
| D Hyperliquid bậc 0 1.5/4.5 | −6.09 [−7.33, −4.82] | −5.51 [−9.47, −1.31] | −6.07 [−9.83, −2.04] | −5.92 [−10.35, −1.26] | −5.39 [−9.06, −1.71] | −4.48 [−10.97, +3.20] |
| E Bybit VIP0 2/5.5 | −7.55 [−8.79, −6.27] | −6.95 [−10.92, −2.74] | −7.42 [−11.18, −3.39] | −7.29 [−11.73, −2.63] | −6.77 [−10.45, −3.07] | −5.88 [−12.38, +1.81] |
| **Z không phí, không trượt** | +0.56 [−0.66, +1.82] | +1.01 [−2.89, +5.13] | −0.25 [−3.97, +3.74] | +0.08 [−4.30, +4.65] | +0.62 [−2.95, +4.18] | +1.73 [−4.65, +9.28] |

Tỷ lệ thắng / Sharpe (năm hóa theo ngày) / $ mỗi tháng (@$150/lệnh, ví $5 000):

| Kịch bản | S1_SESSION | R1_SESSION | P15_tp35 | P15_tp50 | P15_rr25 | P15_rr25atr |
|---|---|---|---|---|---|---|
| A Binance VIP0 | 32 % / −12.1 / −55 | 34 % / −3.6 / −4 | 60 % / −4.1 / −16 | 52 % / −3.5 / −15 | 31 % / −4.0 / −15 | 34 % / −1.8 / −11 |
| A2 Binance + BNB | 33 % / −11.1 / −50 | 35 % / −3.3 / −3 | 60 % / −3.7 / −15 | 52 % / −3.1 / −13 | 31 % / −3.6 / −13 | 34 % / −1.6 / −10 |
| B MEXC web 0/2 | 36 % / −4.0 / −17 | 38 % / −0.9 / −1 | 60 % / −1.4 / −5 | 52 % / −1.1 / −5 | 31 % / −1.0 / −4 | 35 % / −0.2 / −1 |
| B2 MEXC API 6/8 | 28 % / −21.2 / −110 | 28 % / −6.9 / −8 | 60 % / −8.0 / −33 | 51 % / −7.0 / −30 | 30 % / −8.2 / −31 | 33 % / −4.2 / −25 |
| C1 −0.2/2.5 | 35 % / −4.4 / −19 | 38 % / −1.1 / −1 | 60 % / −1.4 / −6 | 52 % / −1.1 / −5 | 31 % / −1.1 / −4 | 35 % / −0.3 / −2 |
| C2 1.0/2.5 | 35 % / −6.7 / −29 | 37 % / −1.8 / −2 | 60 % / −2.3 / −9 | 52 % / −1.9 / −8 | 31 % / −2.0 / −7 | 35 % / −0.8 / −4 |
| D Hyperliquid | 33 % / −10.6 / −47 | 35 % / −3.1 / −3 | 60 % / −3.5 / −14 | 52 % / −3.0 / −13 | 31 % / −3.3 / −12 | 34 % / −1.5 / −9 |
| E Bybit | 32 % / −12.8 / −59 | 34 % / −3.8 / −4 | 60 % / −4.3 / −17 | 52 % / −3.6 / −15 | 31 % / −4.2 / −15 | 34 % / −2.0 / −12 |
| Z không phí | 37 % / +1.0 / +4 | 39 % / +0.6 / +1 | 60 % / −0.1 / −1 | 53 % / +0.0 / +0 | 32 % / +0.4 / +1 | 36 % / +0.6 / +3 |

**Ngay cả không phí**, lãi tốt nhất chỉ khoảng +$4/tháng, với Sharpe ≤ 1 và CI chứa 0.

## 3. Burst / market-making — HOLD-OUT 2026-09-21 → 10-09, SOL + DOGE USDT

Net bps/lệnh [CI 95 %]. Cột `xuyên` = fill thận trọng (mô hình chính). Cột `chạm` = fill lạc quan: coi như đứng đầu hàng đợi, nên là cận trên.

| Kịch bản phí | MOM15 xuyên | MR30 xuyên | MM xuyên | MR30 chạm | MM chạm |
|---|---|---|---|---|---|
| A Binance VIP0 2/5 | −7.69 [−7.86, −7.52] | −7.41 [−7.61, −7.21] | −7.14 [−7.19, −7.09] | −4.52 [−4.70, −4.35] | −4.20 [−4.26, −4.13] |
| A2 Binance + BNB | −7.14 [−7.32, −6.97] | −6.89 [−7.09, −6.68] | −6.74 [−6.78, −6.69] | −4.09 [−4.27, −3.92] | −3.80 [−3.86, −3.73] |
| B MEXC web 0/2 | −3.20 [−3.38, −3.04] | −3.00 [−3.20, −2.80] | −3.12 [−3.17, −3.07] | −0.44 [−0.61, −0.27] | −0.20 [−0.26, −0.13] |
| B2 MEXC API 6/8 | −15.20 [−15.38, −15.04] | −15.00 [−15.20, −14.80] | −15.12 [−15.17, −15.07] | −12.44 [−12.61, −12.27] | −12.20 [−12.26, −12.13] |
| C1 −0.2/2.5 | −3.14 [−3.32, −2.97] | −2.89 [−3.09, −2.68] | −2.74 [−2.78, −2.69] | −0.09 [−0.27, +0.08] | **+0.20 [+0.14, +0.27]** |
| C2 1.0/2.5 | −4.96 [−5.13, −4.80] | −4.80 [−4.99, −4.59] | −5.11 [−5.16, −5.06] | −2.40 [−2.57, −2.24] | −2.20 [−2.26, −2.13] |
| D Hyperliquid 1.5/4.5 | −6.69 [−6.86, −6.52] | −6.41 [−6.61, −6.21] | −6.14 [−6.19, −6.09] | −3.52 [−3.70, −3.35] | −3.20 [−3.26, −3.13] |
| E Bybit 2/5.5 | −7.93 [−8.11, −7.76] | −7.61 [−7.81, −7.41] | −7.15 [−7.20, −7.10] | −4.56 [−4.74, −4.38] | −4.20 [−4.26, −4.13] |
| **Z không phí** | −2.24 [−2.41, −2.08] | −2.19 [−2.38, −1.99] | −3.08 [−3.14, −3.03] | −0.28 [−0.44, −0.13] | −0.20 [−0.26, −0.13] |

- **Ô dương duy nhất của cả lưới** là MM "chạm" với rebate −0.2 bps. Nó cần đồng thời hai điều kiện không có thật với tài khoản nhỏ:
  - đứng **đầu** hàng đợi ở mọi lệnh;
  - đạt > 1.5 % khối lượng maker của toàn sàn Hyperliquid.

  Với fill thận trọng, cùng kịch bản đó ra −2.74 bps.
- Không ghi $/tháng cho burst. Replay vào 850–10 700 lệnh/ngày, con số $ ở quy mô đó không có ý nghĩa vận hành.
- **Bằng chứng trực tiếp về sàn maker 0 %:**
  - Vòng 10-10 đã replay trên **chính sổ USDC-M** (cặp từng có maker 0 %), với phí 0/4.
  - Kết quả: SOLUSDC MR30 −4.48, MM −3.50; DOGEUSDC MR30 −5.78, MM −4.96 bps.
  - Gross ở đó còn **âm hơn** USDT: −2.9 đến −4.7 bps. Ở sổ mỏng hơn, lệnh maker bị chọn bất lợi nặng hơn, và phần đó nuốt khoản phí tiết kiệm được.

## 4. Chứng minh toán học

Lấy trung bình công thức ở mục 1 trên các lệnh:

`E[net] = ḡ − (2 − τ)·m − τ·(t + s)`

trong đó ḡ = gross trung bình, τ = tỷ phần taker trung bình, m/t = phí maker/taker, s = trượt giá.

Hệ quả:
1. E[net] **giảm đơn điệu** theo m và t. Mọi biểu phí m ≥ 0, t ≥ 0 cho E[net] ≤ ḡ (kịch bản Z).
2. Vì vậy, nếu ḡ không dương có ý nghĩa thì **không biểu phí không âm nào** làm net dương có ý nghĩa.
3. Ở maker 0 %: E[net] = ḡ − τ·(t + s). Lãi khi và chỉ khi ḡ > τ·(t + s), tức taker hòa vốn **t\* = ḡ/τ − s**.

| Biến thể | ḡ DEV | ḡ HOLD-OUT [CI 95 %] | τ | net ở 0/2 (= ḡ − 3τ) | t\* hòa vốn khi maker 0 |
|---|---|---|---|---|---|
| S1_SESSION | −0.34 | +0.56 [−0.66, +1.82] | 0.91 | −2.18 | −0.39 (không thể) |
| R1_SESSION | −2.93 | +1.01 [−2.89, +5.13] | 0.88 | −1.63 | +0.15 bps |
| P15_tp35 | −1.24 | −0.25 [−3.97, +3.74] | 0.70 | −2.36 | không thể |
| P15_tp50 | −0.54 | +0.08 [−4.30, +4.65] | 0.75 | −2.17 | không thể |
| P15_rr25 | −0.65 | +0.62 [−2.95, +4.18] | 0.75 | −1.64 | không thể |
| P15_rr25atr | +3.56 | +1.73 [−4.65, +9.28] | 0.80 | −0.68 | +1.15 bps |
| Burst MR30 xuyên | −2.12 | −2.19 [−2.38, −1.99] | 0.41 | −3.00 | không thể |
| MM xuyên | −2.93 | −3.08 [−3.14, −3.03] | 0.02 | −3.12 | không thể (cần maker ≤ −1.6 bps) |
| MM chạm | +0.14 | −0.20 [−0.26, −0.13] | 0.00 | −0.20 | cần maker ≤ −0.10 bps (rebate) |

Ngay cả hai trường hợp có t\* dương (R1, P15_rr25atr) cũng cần taker ≤ 0.15–1.15 bps. Không sàn nào ở bảng mục 5 có mức đó. Hơn nữa CI của ḡ vẫn chứa 0.

**Kết luận:** giảm phí là điều kiện **cần** nhưng **không đủ**. Edge thô phải dương có ý nghĩa trước đã.

Một phép tính ngược: với τ ≈ 0.8 và phí 0/2, cần ḡ ≥ 2.4 bps chỉ để hòa, và ≥ 17.4 bps để đạt mục tiêu "+15 bps net" của các form trước.

## 5. Biểu phí và khả thi kỹ thuật đã kiểm chứng (10/10/2026)

| | Binance USD-M | MEXC futures | Hyperliquid perps | Bybit linear |
|---|---|---|---|---|
| Phí maker/taker (tài khoản nhỏ) | 2 / 5 bps (FAQ 2026-05-01); BNB −10 % → 1.8 / 4.5 | Web/app: **0 / 2 bps**. `contract/detail` đọc 7 lần lúc 11:52 UTC; một lần đọc lúc 11:45 cho BTC 1 / 4. **Qua API: 6 / 8 bps** từ 2026-06-01, "ưu tiên hơn mọi ưu đãi 0 phí và giảm giá MX" | 1.5 / 4.5 bps. Rebate cần ≥ 0.5 % maker share của toàn sàn. Stake HYPE giảm 5–40 %, referral giảm 4 % | 2 / 5.5 bps (nguồn thứ ba; trang chính thức trả 503) |
| Truy cập API đặt lệnh | Có (sàn hiện tại) | Mở cho người dùng đã KYC từ 2026-03-31, "có thể khác theo khu vực". Tài liệu cũ vẫn ghi "Under maintenance" | Có. Dùng agent wallet ký, không cần KYC | Có |
| Post-only | `timeInForce=GTX` | `type=2` (Post Only Maker; bị hủy **sau** khi ack nếu khớp ngay) | `tif=Alo` | `timeInForce=PostOnly` |
| Lệnh tối thiểu | MIN_NOTIONAL trong exchangeInfo (ví dụ docs 5 USDT; BTC 100 USDT chưa kiểm chứng) | 1 hợp đồng: BTC 0.0001 (≈ $8.3), SOL 0.1 (≈ $11), DOGE 100 (≈ $8.6) | 10 USD; size theo szDecimals (BTC 5, SOL 2, DOGE 0) | instruments-info (bị chặn địa lý, chưa kiểm chứng) |
| Giới hạn tốc độ | Weight 2 400/phút, ORDERS 1 200/phút | Đặt lệnh 20 req / 2 s (docs cũ) | 1 200 weight/phút/IP; mỗi địa chỉ 1 request cho mỗi 1 USDC đã giao dịch (+10 000 buffer) | 600 req / 5 s / IP |
| Stop phía sàn | algoOrder STOP_MARKET closePosition | plan order (trigger + market) | trigger `isMarket`, `tpsl=sl`, reduce-only | conditional / tpsl |
| Độ trễ public đo từ sandbox (n = 15) | HTTP 451 (chặn địa lý) | median 304 ms, p90 450 ms | median 298 ms, p90 426 ms | HTTP 403 (chặn địa lý) |
| Thanh khoản 24h (đọc 10/10) | Lớn nhất | Spread SOL 0.9 bps, DOGE 1.2 bps | BTC $1.22 tỷ, SOL $127 tr, **DOGE chỉ $5.6 tr** | — |

Ghi chú về nguồn:
- Độ trễ đo **từ máy cloud**, khác vùng với máy giao dịch, nên chỉ có giá trị tương đối. Muốn đo đúng thì chạy `python tools/venue_probe.py` trên máy Windows của bạn.
- Thông báo phí API của MEXC do agent nghiên cứu web tìm được. Fetch trực tiếp từ sandbox bị chặn (Access Denied). Bản thân thông báo cũng tự mâu thuẫn ở ví dụ BTC.
- **Trước mọi quyết định**, hãy đọc phí thật của tài khoản bằng API riêng của sàn.

## 6. Rủi ro khi đổi sàn (ngoài phí)
- **Fill khác.** Replay dùng sổ Binance. MEXC và Hyperliquid có hàng đợi, độ sâu và người tham gia khác. Bằng chứng USDC-M (mục 3) cho thấy sổ mỏng hơn thì adverse selection nặng hơn. DOGE trên Hyperliquid chỉ $5.6 tr/ngày.
- **Rủi ro đối tác và pháp lý:**
  - MEXC là sàn tập trung ngoài khơi. Hyperliquid là L1 riêng, rủi ro hợp đồng/bridge USDC.
  - Tháng 3/2026 báo chí đưa tin Bộ Tài chính Việt Nam dự thảo cấm công dân dùng sàn nước ngoài. **Chưa xác nhận đã có hiệu lực.**
- **Hạ tầng:** khóa API, chữ ký EIP-712 (Hyperliquid), cơ chế hủy post-only sau ack (MEXC), lệnh dừng khẩn cấp, đối soát ledger. Tất cả phải làm lại và kiểm thử trên testnet trước khi có đồng tiền nào.

## 7. Kiến trúc đa sàn (`core/venues/`)
- **`base.py`**:
  - Abstract class `BaseVenue` với 4 lời gọi async: `place_post_only`, `cancel_order`, `position_view`, `set_disaster_stop`.
  - Quy ước: side và qty theo đơn vị base; stop nhận side của **vị thế**; trường thiếu là `None` (UNKNOWN), không bao giờ là 0.
  - Lỗi: `PostOnlyRejected`, `MinSizeError` (ném ra **trước** khi gửi), `VenueDisabled`, `VenueError`.
  - `VENUE_SPECS` lưu các sự kiện ở mục 5 kèm trạng thái kiểm chứng.
- **`binance.py` — `BinanceAdapter`**: bọc venue hiện có (`limit_gtx`, `cancel`, `stop`, `position_view`), không có code đặt lệnh mới. Mã −5022 → `PostOnlyRejected`.
- **`mexc.py` — `MexcVenue`** (prototype):
  - Ký HMAC-SHA256 (`ApiKey`, `Request-Time`, `Signature`), đổi qty → số hợp đồng và chặn dưới 1 hợp đồng.
  - Post-only `type=2`, ánh xạ side 1/2/3/4, plan-order cho stop.
- **`hyperliquid.py` — `HyperliquidVenue`** (prototype):
  - Lệnh `Alo`; làm tròn giá theo 5 chữ số có nghĩa / (6 − szDecimals) số thập phân, theo hướng thụ động; tối thiểu 10 USD.
  - Stop là trigger reduce-only. Chữ ký do `signer` bên ngoài cung cấp (SDK chính thức); không có code quản lý khóa.
- **An toàn:**
  - Hai prototype **không gửi gì** khi không có `transport` (và `signer` với Hyperliquid); khi đó chúng ném `VenueDisabled`.
  - Không module nào trong `core/` hay `live/` import `core.venues`. Engine vẫn chạy Binance như cũ.
  - Khóa API, nếu sau này dùng, chỉ đọc từ `.env.local`.

## 8. Nhiệm vụ 3 — tối ưu trên Binance
- **BNB −10 %:** +0.6 bps/lệnh trên mọi chiến lược. Không đổi dấu kết quả. Muốn bật thì giữ BNB trong ví USDⓈ-M; không cần đổi code.
- **USDC-M:**
  - Khuyến mãi 0 % maker / 0.0275 % taker chạy 2025-09-11 → **2025-12-10**. Chưa thấy gia hạn; phí VIP0 hiện tại chưa kiểm chứng được.
  - Replay trên sổ USDC vẫn âm (mục 3). `tools/fee_survey.py --quote USDC` trên máy của bạn sẽ đọc phí thật.
- **R:R ≥ 2.5** (đăng ký trước: `P15_rr25` = TP +100 / SL −40 bps; `P15_rr25atr` = TP 2.5 / SL 1.0 ATR(15m); không TP1, không trailing, 180 phút):

  | Biến thể | Split | n (lệnh/ngày) | Thắng | Net bps [CI 95 %] | Gross | Sharpe |
  |---|---|---|---|---|---|---|
  | P15_rr25 | DEV | 2 839 (7.8) | 29 % | −7.6 [−10.5, −4.6] | −0.6 | −5.2 |
  | P15_rr25 | HO | 1 377 (5.0) | 31 % | −6.4 [−10.1, −2.7] | +0.6 | −4.0 |
  | P15_rr25atr | DEV | 2 182 (6.0) | 34 % | −3.6 [−9.2, +1.8] | +3.6 | −1.3 |
  | P15_rr25atr | HO | 1 193 (4.4) | 34 % | −5.5 [−12.0, +2.2] | +1.7 | −1.8 |

  - Phí chiếm "8 % của 100 bps" chỉ khi **thắng**. Trên hold-out chỉ 20–25 % lệnh chạm TP (342/1 377 và 236/1 193); phần còn lại là SL hoặc hết giờ (taker).
  - Sharpe **không vượt 0** trên Binance.
  - P15_rr25atr là DEV-best mới của nhóm P (gross DEV +3.6 bps), nhưng hold-out co lại còn +1.7 và CI chứa 0. Biến thể này được thêm vào làn shadow để thu bằng chứng forward, **không** bật.

## 9. Thay đổi code, test, rollback
**Code:**
- `core/intraday_lane.py`: thêm `P15_rr25`, `P15_rr25atr`; mỗi row có `taker_frac`.
- `tools/intraday_replay.py`, `tools/burst_scalper_replay.py`: ghi `taker_frac` (số liệu cũ không đổi).
- `tools/venue_fee_replay.py` (mới): định giá lại theo 9 kịch bản phí.
- `tools/venue_probe.py` (mới): độ trễ và spec từ API công khai, không cần khóa.
- `core/venues/` (mới).
- `core/engine.py`: biến thể shadow của demo thêm `P15_rr25atr`.
- `config/intraday_verdict.json` được ghi lại: mọi ô SHADOW, nhóm P = P15_rr25atr.

**Test:**
- `tests/test_venues.py` (9 test mới): hợp đồng 4 hàm, adapter Binance, payload/chữ ký MEXC, quy tắc giá/size Hyperliquid, chặn kích thước tối thiểu, không gửi khi thiếu transport, không bị nối vào engine.
- `tests/test_intraday_lane.py`: thêm test bracket R:R 2.5 và test `reprice` = `net_bps` của lane.
- Kết quả bộ test đầy đủ ghi ở `docs/harness/progress.md` (vòng 10-10 d).

**Dữ liệu:** `snapshot_2026-10-10d/` (JSON/TXT của các replay, phản hồi `contract/detail` của MEXC, kết quả probe, SHA256SUMS).

**Rollback:** `intraday_variants=('R1_SESSION', 'P15_tp50')`. `core/venues/` và hai tool mới không được gọi ở đâu nên có thể bỏ nguyên vẹn.

## 10. Khuyến nghị
1. **Không đổi sàn để cứu các chiến lược hiện có.**
   - MEXC qua API đắt hơn Binance.
   - Hyperliquid bậc cơ bản chỉ rẻ hơn khoảng 1 bp.
   - Ngay cả 0 phí thì chiến lược vẫn không có edge.
2. Nếu muốn khai thác phí thấp, điều kiện đầu vào là **một tín hiệu có gross ≥ 3 bps với CI > 0 trên hold-out Binance**. Chỉ khi đó mới đáng làm tiếp:
   - kiểm chứng phí tài khoản thật bằng API;
   - replay trên dữ liệu chính sàn đó (Hyperliquid có dữ liệu L2/trade công khai);
   - chạy testnet bằng adapter trong `core/venues/`.
3. Hướng có bằng chứng tốt nhất hiện nay vẫn là **làn trend 4H** (OOS SR 1.06). Ở đó phí 7 bps là nhỏ so với biên độ lệnh.
