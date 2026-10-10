# Báo cáo nghiệm thu 10/10/2026 (b): intraday 5m/15m micro-swing và VWAP reversion

**Phạm vi.**
- Code, test và replay offline trên kline 1 phút thật của Binance USD-M. Không khởi động lại engine, không gửi lệnh nào.
- Không đụng tới `data/engine_trades.jsonl` hay `data/trend_trades.jsonl`.
- Làn 3 ghi vào file riêng:
  - `data/intraday_trades_shadow.jsonl` ở chế độ shadow;
  - `data/intraday_trades.jsonl` ở chế độ active.
- Live không đổi (`intraday_lane='off'`).

## 0. Kết luận

| Mục tiêu của form | Kết quả hold-out 2026-01 → 09 (3 coin gộp) |
|---|---|
| 5–15 lệnh/ngày | **Đạt**: S1 khoảng 17–21 lệnh/ngày, S2 khoảng 2 lệnh/ngày |
| Tỷ lệ thắng ≥ 55–65 % | **Không đạt**: S1 32 %, S2 27–28 % |
| Net > +15 bps/lệnh | **Không đạt**: S1 −7.1 đến −8.0 bps, S2 −7.5 đến −8.4 bps. Khoảng tin cậy 95 % nằm hoàn toàn dưới 0 |
| Sharpe intraday ≥ 1.2–1.8 | **Không đạt**: −4 đến −18 |
| Tiền với $150/lệnh trên ví $5 000 | S1_SESSION ≈ **−$1.8/ngày ≈ −$55/tháng**. S1_ROLL100 ≈ −$2.5/ngày |

**Vì sao không đạt:**
- **Phí quyết định dấu kết quả.** Gross trước phí ≈ 0 (−1.3 đến +0.6 bps) ở mọi biến thể. Phí round-trip là 8 bps khi cả hai chân maker, tới 12 bps khi thoát bằng taker.
- **Tỷ lệ thắng > 50 % mâu thuẫn với R:R ≥ 1.5 khi không có lợi thế.** Mục tiêu (TP) thường xa gấp 1.5–4 lần điểm dừng (SL), nên xác suất chạm TP trước SL theo random walk chỉ khoảng 20–34 %. Muốn thắng trên 55 % với R:R ≥ 1.5 thì cần gross edge vài chục bps mỗi lệnh. Đo được thì gần 0.

Theo quy tắc đã đăng ký trước, không (symbol, chiến lược) nào được bật active. Điều kiện để bật: CI hold-out > 0, tỷ lệ thắng > 50 % và ít nhất 100 lệnh.

Làn 3 đã có đủ code và chạy **shadow trên demo**. Shadow ở đây là chính quy tắc replay, áp lên từng nến 1m mainnet vừa đóng. Phần active bị khoá bằng `config/intraday_verdict.json`.

Chuyện 14 lệnh `BURST SHADOW` sáng 10/10: đó là **lệnh ảo** của làn 2 shadow, không phải tiền thật. Kết quả −7 bps/lệnh khớp đúng replay của vòng trước. Shadow đã làm đúng việc của nó: đo chiến lược mà không mất tiền.

## 1. Replay đã đăng ký trước (`tools/intraday_replay.py`)

**Dữ liệu và thiết lập:**
- Kline 1m của BTCUSDT, SOLUSDT, DOGEUSDT từ 2024-07 đến 2026-09: 1 183 680 nến mỗi coin, không có khoảng trống. Nguồn: data.binance.vision.
- Nến 5m/15m được ghép từ đủ các nến 1m. Mọi quyết định dùng nến đã đóng và có hiệu lực từ phút kế tiếp.
- DEV 2025 dùng để chọn biến thể. HOLD-OUT 2026-01 → 09 chỉ dùng để báo cáo.
- Phí VIP0: maker 2 bps; taker 5 bps + 1 bp trượt giá.

**S1, VWAP-band mean reversion** (tại mỗi lần nến 5m đóng):
- Vào lệnh khi giá chạm VWAP ± 2σ, CVD của phút cuối đi ngược hướng giá (dấu hiệu bên mua/bán đã cạn sức), và |độ dốc EMA20(5m)| < 0.08 %.
- Lệnh vào: post-only ở giá đóng nến 5m, sống 1 phút.
- TP về VWAP; SL = 1 ATR(5m); bỏ qua nếu R:R < 1.5.
- Hai cách tính VWAP: ROLL100 (100 nến 1m) và SESSION (neo 00:00 UTC).
- `vwap_5m` của engine là VWAP trượt **5 phút**. Dải ±2σ của nó chỉ rộng khoảng 5–15 bps, không thể cho mục tiêu 35–50 bps như form muốn. Vì vậy dải được tính trên 100 phút hoặc cả phiên.

**S2, 15m momentum continuation:**
- Điều kiện xu hướng: độ dốc EMA20(5m) ≥ 0.15 %, đo trên 3 nến (bản chính) hoặc 1 nến (bản theo đúng chữ form); ADX(15m) > 25; giá nằm đúng phía EMA20.
- Lệnh vào: post-only tại EMA20, đặt lại giá mỗi lần nến 5m đóng khi xu hướng còn.
- TP +60 hoặc +100 bps. SL ở đáy/đỉnh của 2 nến 5m gần nhất, kéo theo từng nến 5m mới.

**Chung cho cả hai:**
- Giữ tối đa 45 phút, hết giờ đóng bằng taker.
- Lệnh maker chỉ khớp khi giá **đi xuyên qua** giá đặt.
- Trong cùng một phút, giả định SL chạm trước TP.
- Trong phút khớp lệnh, chỉ SL được tính.

### Hold-out 2026, 3 coin gộp

| Biến thể | Lệnh (/ngày) | Thắng | Net bps [CI95] | Gross | Thắng TB / Thua TB | Sharpe | $/ngày | Giữ |
|---|---|---|---|---|---|---|---|---|
| **S1_SESSION** (DEV-best) | 4 704 (17.2) | 32.2 % | **−7.09 [−8.33, −5.82]** | +0.56 | +35.7 / −27.4 | −12.2 | −1.83 | 21 phút |
| S1_ROLL100 | 5 680 (20.8) | 32.4 % | −7.99 [−8.93, −7.00] | −0.87 | +29.2 / −25.8 | −17.6 | −2.49 | 17 phút |
| **S2_slope3_tp60** (DEV-best) | 620 (2.3) | 28.1 % | **−8.44 [−12.49, −4.60]** | −1.33 | +46.2 / −29.8 | −4.5 | −0.29 | 5 phút |
| S2_slope3_tp100 | 615 (2.3) | 26.8 % | −7.53 [−12.09, −2.84] | −0.01 | +52.8 / −29.7 | −3.7 | −0.25 | 6 phút |
| S2_slope1 (theo chữ form, ×2 TP) | 16 | 25–31 % | −6 đến −11 (n quá nhỏ) | | | | | |

- **Theo từng coin** (hold-out, DEV-best), tất cả đều âm với CI dưới 0:

| Coin | S1_SESSION | S2_slope3_tp60 |
|---|---|---|
| BTC | −7.8 bps | −13.5 bps |
| SOL | −7.4 bps | −9.7 bps |
| DOGE | −6.0 bps | −6.0 bps |

- Mọi ô theo quý từ 2025Q1 đến 2026Q3 đều âm.
- Giai đoạn DEV cho kết quả gần như hệt hold-out. Chi tiết: `snapshot_2026-10-10b/intraday_replay.txt`.
- **S2 thực chất là lệnh khoảng 5 phút.**
  - Lệnh vào là limit tại EMA20, nằm dưới giá. Vì vậy đáy của 2 nến 5m trước thường **cao hơn** giá vào, và 89–92 % lệnh phải dùng SL dự phòng 1 ATR.
  - Sau đó SL bị kéo lên đáy của chính nến khớp, rất sát giá vào.
  - Đây là hệ quả của spec trong form, không phải lỗi code. Một SL rộng hơn là giả thuyết khác, chưa được đăng ký trước.

## 2. Kiểm chứng độc lập và đối kháng (workflow 9 agent)

| Kiểm tra | Kết quả |
|---|---|
| **Viết lại độc lập** chỉ từ docstring, không đọc code | S1 khớp trong 0.35 % số lệnh và 0.03 bps. S2 khớp chính xác tới 1e-6 bps sau khi giải thích hết mọi chênh lệch |
| **Soi look-ahead** | **0 vi phạm.** Kiểm bằng 5 cách: tính lại mọi chỉ báo bằng code nhân quả riêng; cắt dữ liệu ở 5 điểm; ghi lại thời điểm mỗi giá trị được biết (36 275 lệnh); cố tình chèn look-ahead và đều bị bắt; chạy lại toàn bộ trên dữ liệu đã cắt |
| **Cận khớp lệnh và phí** | Ngay cả khi cộng mọi giả định lạc quan, S1 vẫn −5 đến −6.6 bps và S2 −2.5 đến −3 bps. Phí 0 cho gross ≈ 0. Không cận nào cho tỷ lệ thắng > 41 %. Nến 1m khớp với tick aggTrades ở 99.9 % số đỉnh/đáy phút |
| **Thống kê** | CI bootstrap theo ngày, Sharpe (gồm cả ngày không có lệnh), tỷ lệ thắng đều đúng. Kết luận âm vững theo từng coin và từng quý |

**Đã sửa theo kết quả kiểm chứng** (không đổi kết luận nào):
1. Vòng re-anchor của S2 có giới hạn 3 nến không có trong spec, nên cứ mỗi nến thứ 4 lại bị bỏ qua. Đã bỏ, replay giờ theo đúng spec. Số lệnh S2 tăng khoảng 20 %, net đổi ±0.6 bps.
2. Quy tắc verdict thiếu ngưỡng mẫu tối thiểu: một lệnh duy nhất đã có thể ra ACTIVE-ELIGIBLE. Đã thêm điều kiện ≥ 100 lệnh hold-out mỗi (symbol, chiến lược).
3. DD giờ tính từ 0, nên lỗ của ngày đầu tiên cũng được tính.
4. Quy tắc "không vào lệnh mới ở nến 5m mà phút cuối vừa đóng vị thế cũ" giờ được ghi rõ trong docstring. Lane và replay áp cùng quy tắc này.

**Hạn chế được nêu rõ:**
- Khoảng 60 % số lần S1 khớp sẽ bị **từ chối post-only** trên thị trường thật: lệnh đặt ở giá đóng nến 5m thường cắt qua sổ lệnh. Bỏ các lệnh đó đi thì tần suất giảm, nhưng net mỗi lệnh không khá hơn. Shadow vẫn dùng quy tắc của replay, nên ở điểm này shadow lạc quan hơn active.
- Kết luận "mọi biến thể đều âm" không áp được cho S2_slope1 vì chỉ có 16 lệnh hold-out. Nó chưa kiểm định được, chứ không phải đã chứng minh âm.

## 3. Khám phá thêm 5 họ chiến lược intraday (chỉ trên DEV 2025)

Mỗi agent đăng ký trước tối đa 4 biến thể, dùng cùng mô hình khớp/phí, và không được xem dữ liệu 2026. Holdout chỉ chạy cho biến thể DEV-best có net dương. **Không họ nào có biến thể như vậy.** Code và kết quả nằm trong `snapshot_2026-10-10b/families/`.

| Họ chiến lược | Nguồn tham khảo | Biến thể tốt nhất (DEV) | Thắng | Gross |
|---|---|---|---|---|
| Intraday time-series momentum (phiên, 4h z-score, noise-area breakout) | Gao–Han–Li–Zhou 2018; Zarattini–Aziz–Barbon | −6.6 bps [−20.7, +8.3] | 35–44 % | ≈ 0 (\|t\| ≤ 1.34) |
| Mean reversion khung lớn (Bollinger/VWAP 1 ngày, ADX 1h < 20, giữ tới 4 h) | — | −12.2 đến −18.7 bps | 36–42 % | −5 đến −11 |
| Opening-range breakout (00:00 / 08:00 / 13:30 UTC, volume ≥ 1.5×) | — | −11.2 bps | 38–43 % | ≈ 0 |
| Hiệu ứng giờ funding (00/08/16 UTC) | — | −7.6 bps | 35–40 % | +2–3 bps sau giờ funding, đúng dấu nhưng ≪ phí |
| Đánh ngược sốc volume (proxy cho chuỗi thanh lý) | Grossman–Miller 1988; Campbell–Grossman–Wang 1993; Nagel 2012 | −8.2 bps | 45–51 % (bản taker 51.4 % mà vẫn −11 bps) | ≈ −2 |

Tổng cộng 6 họ, 26 biến thể, 3 coin. Ở khung 5 phút đến 4 giờ, gross edge sau trượt giá đều ≈ 0. Không cách nào bù được 8–12 bps phí VIP0. Điều này khớp với tổng quan tài liệu ngày 08/10 (`research_alpha_after_fees_2026-10-08.md`): ở khung giây đến giờ, lợi thế nhỏ hơn phí retail một bậc độ lớn. Chỉ trend 4H–1D cho thấy edge sau phí.

## 4. Làn 3 trong engine (`core/intraday_lane.py`)

**Một nguồn logic duy nhất.** Chỉ báo, `s1_signal`, `s2_trend`, `s2_stop` nằm trong `core/intraday_lane.py`, và `tools/intraday_replay.py` import lại chúng. Test `test_shadow_lane_reproduces_the_replay_trade_by_trade` chứng minh lane shadow (chạy từng nến) cho **đúng từng lệnh** như replay, cho cả S1 và S2.

**Chế độ** (`Profile.intraday_lane`):
- **shadow:** mỗi khi một nến 1m mainnet đóng, engine lấy lại 1500 nến 1m qua REST (có retry, cache và spot proxy từ vòng ff), rồi áp đúng quy tắc replay. Kết quả ghi vào `data/intraday_trades_shadow.jsonl`, kèm event `INTRADAY SHADOW …`.
  - Nến không có volume (lấy từ spot proxy) bị coi là UNKNOWN: không phát tín hiệu.
- **active** (chỉ demo, khi verdict là ACTIVE-ELIGIBLE):
  - lệnh vào post-only GTX;
  - STOP_MARKET closePosition trên sàn đặt ngay sau khi khớp; nếu sàn từ chối stop thì đóng vị thế ngay;
  - TP post-only;
  - S2 kéo stop bằng cách huỷ rồi đặt lại;
  - hết 45 phút thì đóng bằng IOC;
  - ghi vào `data/intraday_trades.jsonl`.
- **Chung tài khoản với các làn khác:** Binance one-way mode chỉ có **một vị thế ròng mỗi symbol**.
  - Khi đang có lệnh hoặc vị thế active, lane đặt `e.busy` để làn trend chờ.
  - Lane không vào lệnh khi symbol đang có vị thế trend/vi mô, hoặc trong 10 phút sau mỗi lần nến 4H đóng.
  - Cầu dao lỗ ngày $25 (`intraday_daily_kill_usdt`).
- **Size:** $150/lệnh (`intraday_notional_usdt`), tối đa 4 % ví.
- **Biến thể:** `intraday_variants = ('S1_SESSION', 'S2_slope3_tp60')`, tức DEV-best của từng chiến lược.
- **Telemetry:** `snapshot['intraday_lane'][symbol]` (mode, lệnh chờ, vị thế, VWAP, slope3, ADX15, day_pnl, n).

**Review đối kháng của code lane:** xem mục 6.

## 5. Kiểm thử và lưu trữ

**Test:**
- `python -m pytest -q`: xem mục 6 (số cuối cùng sau khi sửa theo review).
- 13 test mới trong `tests/test_intraday_lane.py`:
  - resample, VWAP warm-up;
  - điều kiện S1/S2;
  - nến thiếu volume;
  - lane shadow: khớp TP, lệnh hết hạn, SL ngay trong phút khớp, hết giờ 45 phút;
  - khoá 4H và symbol bận;
  - **lane = replay từng lệnh**;
  - active bị chặn khi không có verdict và ở live;
  - lệnh vào active đặt stop trên sàn và TP;
  - profile production.

**Lưu trữ:**
- Snapshot `docs/research_upgrade/snapshot_2026-10-10b/`: JSON và log replay, kết quả 9 agent, code và kết quả DEV của 5 họ chiến lược, SHA256SUMS.
- `config/intraday_verdict.json`: mọi (symbol, chiến lược) đều `SHADOW`.
- `experiment_register.md` thêm ID1–ID3; `trial_log.jsonl` thêm 4 dòng hold-out (AB-21..24) và 5 dòng các họ chiến lược DEV.

**Rollback:** demo `intraday_lane='off'`.

## 6. Review code làn 3 và số test cuối cùng

(xem phần cập nhật bên dưới)
