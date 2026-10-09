# Báo cáo nghiệm thu (dd) 09/10/2026: định thời khớp lệnh và kích hoạt làn trend 4H

**Phạm vi.**
- Code, test và replay offline. Không khởi động lại engine, không gửi lệnh nào trong vòng này.
- Không ghi vào `data/engine_trades.jsonl`.
- Thay đổi chỉ có hiệu lực khi bạn tự restart `run.bat demo`.
- Live không đổi (`trend_lane='off'`, micro bật). Ngoài ra, code từ chối chạy làn trend nếu profile không phải demo hoặc venue là mainnet.

## 0. Kiểm tra lại các tiền đề của form

| Tiền đề | Kiểm tra | Kết luận |
|---|---|---|
| "Gross luôn dương 7/8 lệnh, biên độ 2–4 bps" | 8 lệnh gần nhất (code 4eb99f8): gross +10.0, 0.0, +6.3, +1.8, +1.8, +2.4, +1.8, −4.2 bps; phí 4.0 bps mỗi lệnh | Gần đúng: 6/8 dương, trung vị +1.8 bps < phí 4 bps. Đây là phí ăn mòn mạn tính. Mẫu 8 lệnh còn nhỏ |
| "Trend 4H có biên độ sóng 150–300 bps" | Phân phối lệnh ngoài mẫu 2025-01 → 2026-09, 133 lệnh | **Không đúng với lệnh điển hình**: trung vị −59 bps, trung bình +67 bps, p25 −211, p75 +41, tỉ lệ thắng 34 %. Lãi đến từ một số ít sóng lớn |
| "Sharpe ngoài mẫu +0.69, +10.7 %/năm" | Đó là bản backtest có band tái cân bằng. Bản thật thi hành (size cố định, thoát theo tín hiệu, có stop thảm hoạ 8 ATR) | SR +0.59, +9.3 %/năm, PSR 0.79. **DSR 0.19 (vòng bb): chưa có ý nghĩa thống kê** |
| "Hard SL 1.5 ATR ≈ −1 %" | ATR(14) 4H trung vị: BTC 1.16 %, SOL 2.06 %, DOGE 2.21 % | 1.5 ATR ≈ **1.7–3.3 %**, không phải 1 % |

Mâu thuẫn với quy tắc của chính form vòng (bb): chỉ chiến lược có DSR > 0.95 mới lên thực nghiệm, nhưng làn trend có DSR 0.19. Vòng này vẫn bật làn trend **trên demo** (tiền giả) theo yêu cầu trực tiếp của bạn, với trần notional $150–300. Live vẫn tắt.

## 1. Nhiệm vụ 3 làm trước: replay kiểm định

### 1a. Brackets theo form (TP1 + SL + trailing) làm hỏng làn trend

**Dữ liệu và phương pháp:**
- Backtest theo sự kiện `tl.backtest_brackets`.
- Bar 4H của BTC/SOL/DOGE. Ngoài mẫu (OOS) 2025-01 → 2026-09, giai đoạn phát triển (DEV) 2022–24.
- Taker 6 bps, maker 2 bps.
- Kiểm tra trong cùng một bar theo thứ tự bất lợi: stop trước, TP sau.

| Biến thể | SR ngoài mẫu | %/năm | DD | SR DEV | Thắng | Trung vị / trung bình bps | Giữ trung vị |
|---|---|---|---|---|---|---|---|
| **E0 thoát theo tín hiệu** | **+0.63** | +10.0 | 12.7 % | +0.50 | 34 % | −59 / +67 | 48 h |
| E1 form: TP1 1.35 % + SL 1.5 ATR + chandelier 3 ATR | **−0.48** | −1.1 | 5.3 % | +0.40 | 37 % | −43 / −15 | 20 h |
| E2 form: TP1 1.35 % + SL 1.5 ATR + Donchian mid | −0.54 | −1.2 | 5.6 % | +0.25 | 36 % | −36 / −13 | 8 h |
| E3 chỉ SL 1.5 ATR | +0.29 | +2.2 | 9.3 % | +0.33 | 20 % | −123 / −57 | 20 h |
| E4 chỉ TP1 1.35 % | +0.68 | +5.7 | 7.5 % | +0.61 | 46 % | −17 / +43 | 48 h |
| SL thảm hoạ 8 ATR | +0.59 | +9.3 | 13.3 % | | | | |
| SL 5 ATR / 12 ATR | +0.30 / +0.53 | | | | | | |

**Kết luận:**
- Trend-following sống nhờ vài sóng dài. Stop chặt và trailing cắt chính những sóng đó.
- AB-10: E1 so với E0 là −3.0 bps/ngày, CI [−8.8, +2.6].
- **Mặc định: thoát theo tín hiệu, cộng stop thảm hoạ 8 ATR trên sàn** (để bảo vệ khi engine tắt). TP1, SL và trailing có trong code nhưng tắt.

### 1b. Định thời khớp lệnh: taker, maker, hay swarm định thời (`tools/trend_execution_replay.py`)

**Dữ liệu:**
- 261 lần làn trend thực sự đổi vị thế (vào/ra lệnh) ngoài mẫu: BTC 112, SOL 108, DOGE 41.
- Mỗi lần dùng aggTrades mainnet thật từ 15 phút trước đến 5 phút sau lúc đóng nến 4H; 154 file ngày trên data.binance.vision.
- Giá tham chiếu là giá đóng nến. Chi phí = độ lệch giá khớp + phí (maker 2, taker 5 bps).
- Tính năng swarm dùng chính `MicroFeed` của engine.
- Sổ lệnh dựng lại từ phía của lệnh chủ động, không mô hình hàng đợi. Có hai mô hình khớp:
  - "xuyên giá" (bảo thủ): chỉ khớp khi giá giao dịch đi qua giá đặt;
  - "chạm giá" (lạc quan): khớp ngay khi giá giao dịch chạm giá đặt.

| Chính sách | Chi phí TB | Trung vị | p90 | Khớp maker | Chờ trung vị | So với TAKER (ghép cặp) | SR làn ngoài mẫu |
|---|---|---|---|---|---|---|---|
| TAKER | +5.30 | +5.00 | +6.18 | 0 % | 0 s | — | +0.592 |
| **MAKER** (xuyên giá) | **+4.10** | +2.00 | +10.46 | 100 % | 6 s | **−1.20 bps [−1.84, −0.48], p 0.0003** | +0.600 |
| MAKER (chạm giá) | +2.19 | +2.00 | +2.00 | 100 % | 1 s | −3.10 [−3.37, −2.80] | +0.612 |
| SWARM định thời (xuyên giá) | +4.97 | +5.00 | +9.42 | 29 % | 3 s | −0.32 [−1.03, +0.34] | +0.595 |
| SWARM định thời (chạm giá) | +4.16 | +5.00 | +8.03 | 40 % | 1 s | −1.14 | +0.600 |

Đối chiếu: giả định cũ 6 bps/phía cho SR +0.587.

**Kết luận trung thực:**
- **Maker đơn giản thắng taker** khoảng 1.2 bps mỗi lần (mô hình bảo thủ) đến 3.1 bps (lạc quan).
  - Kết quả đều cho cả lệnh vào (−1.08) và lệnh ra (−1.31). BTC −1.2, SOL −1.7; DOGE +0.2 vì spread của DOGE tính theo tick lớn.
  - AB-8: DSR 0.79 sau khi tính 87 lần thử, nên chưa đạt ngưỡng 0.95.
- **Định thời bằng swarm (đợi nhịp hồi theo CVD 15 s, bỏ qua khi VPIN độc) KÉM hơn maker đơn giản** 0.87 bps [0.07, 1.64] (AB-9).
  - Lý do: ở 185/261 lần, dòng tiền 60 s đang chạy cùng chiều, nên nó khớp taker ngay để không bị lỡ. Vào đúng lúc đóng nến 4H, đợi nhịp hồi là đợi một con sóng đang chạy đi.
  - Chính sách `swarm` vẫn có trong code nhưng **mặc định dùng `maker`**.
- **Lợi ích thật nhỏ hơn form kỳ vọng.**
  - Tiết kiệm 1–3 bps mỗi phía chỉ đưa SR làn từ 0.59 lên 0.60–0.61 (+0.1–0.3 %/năm).
  - Ước lượng tiền: khoảng 6 lệnh/tháng × $225 × 2.4 bps (vào + ra) ≈ **$0.3/tháng**. Phần thắng chính vẫn là có hay không có xu hướng.
- Không chạy được "engine_replay 2 năm" như form gợi ý: engine Python mất khoảng 60 giờ cho một năm. Replay theo cửa sổ sự kiện với chính `MicroFeed` của engine là công cụ đúng cho câu hỏi này.

## 2. Nhiệm vụ 1: swarm thành bộ định thời, tắt scalp độc lập

- **`Profile.micro_standalone`** (demo `False`, live `True`): engine vi mô thành observer, không mở vị thế độc lập.
  - Các tính năng (CVD, VPIN, OFI…), swarm, telemetry và funnel vẫn chạy.
  - Lý do: gross trung vị +1.8 bps nhỏ hơn phí 4 bps (mục 0).
- **Swarm làm bộ định thời** cho làn trend nằm trong `TrendExecutor.execute`. Chính sách `swarm` gồm:
  - chỉ đặt GTX ở touch của sàn demo khi CVD 15 s chạy ngược chiều mình và VPIN không độc;
  - khớp IOC ngay khi CVD 60 s vượt ngưỡng regime theo chiều mình.
  - Theo replay, chính sách này kém maker đơn giản, nên mặc định `trend_exec='maker'`: đặt GTX ở touch ngay, đặt lại giá mỗi 30 s.
- Mọi chính sách đều có lưới an toàn: sau `trend_exec_window_s` (300 s), phần chưa khớp được gửi IOC với dải bảo vệ 15 bps. Tín hiệu không bao giờ bị bỏ lỡ trong im lặng.

## 3. Nhiệm vụ 2: làn trend 4H giao dịch thật trên demo (`core/trend_executor.py`)

**Cấu hình:**
- `trend_lane='active'` trên demo. Mỗi 60 s, engine lấy nến 4H đã đóng và funding (REST công khai).
- Tín hiệu là spec 4H LS đã kiểm định (TSMOM 30/120/360 bar, Donchian 120, vol target 20 %/năm).

**Cách giao dịch:**
- **Chỉ đổi theo dấu:** vào, thoát, đảo chiều; mỗi nến 4H mới chỉ xử lý một lần. Size cố định lúc vào lệnh, không tái cân bằng để khỏi tốn phí. Backtest band so với size cố định chênh −0.2 bps/ngày, CI [−1.8, +1.4].
- **Size** = |trọng số| × ví, kẹp trong **[$150, $300]**.
- **Thoát:** theo tín hiệu, cộng **stop thảm hoạ STOP_MARKET trên sàn ở 8 × ATR(14, 4H)**. Nếu sàn từ chối stop, engine đóng lệnh ngay.
- Sau khi bị stop, không vào lại cùng chiều cho tới khi tín hiệu đổi.
- TP1 / SL 1.5 ATR / trailing có thể bật (`trend_tp1_pct`… qua `core/trend_lane.Brackets`) nhưng mặc định tắt (mục 1a).

**Tách biệt với làn vi mô:**
- `Engine.trend_owned` ngăn engine vi mô adopt vị thế trend.
- Trong lúc đặt lệnh, `e.busy` chặn làn vi mô.
- Nếu sàn có vị thế không phải của làn trend, làn trend không vào lệnh.

**State và ghi nhận:**
- Trạng thái lưu ở `data/trend_active.json`; khôi phục sau restart, đối chiếu với vị thế thật trên sàn.
- Lệnh trend ghi ở **`data/trend_trades.jsonl`**, không ghi vào ledger vi mô. Phí/PnL lấy từ `realized_parts` của sàn.
- Event `TREND 4H · OPEN/CLOSE …` xuất hiện trên Web UI; telemetry có `trend_lane['4h_active']`.

**Ràng buộc an toàn:**
- Chỉ chạy demo. Code từ chối profile không phải demo hoặc venue mainnet.
- Không đổi đòn bẩy.
- Cầu dao lỗ ngày của làn vi mô không tính PnL trend (lần này ghi nhận, chưa gộp).

**Lane paper:** làn paper 1D vẫn chạy song song. Khung 4H paper được thay bằng 4H thật.

## 4. Kiểm chứng

- `python -m pytest -q` trên máy người dùng: **1452 passed** (trước vòng này: 1433).
- `tests/test_trend_activation_and_timing.py` có 19 test, gồm:
  - profile production;
  - observer khi micro tắt;
  - chặn live và mainnet;
  - size bị kẹp;
  - mở lệnh kèm stop thảm hoạ, đảo chiều kèm dòng ghi trong `trend_trades`;
  - stop sàn khớp thì settle và chặn vào lại cùng chiều;
  - maker khớp ở touch, maker hết giờ chuyển IOC;
  - swarm khớp ngay khi dòng tiền chạy cùng chiều, swarm đặt lệnh khi có nhịp hồi;
  - engine không adopt vị thế trend;
  - vị thế lạ trên sàn thì không vào;
  - backtest brackets: stop trước TP, TP1 chịu phí maker;
  - các hàm mô phỏng khớp lệnh và `MicroFeed` dùng trong replay.
- **Test vi mô cũ:** khoảng 200 test vi mô giả định micro bật. `tests/conftest.py` cho các test này thấy demo với micro bật và trend paper. Test nào kiểm tra cấu hình thật thì gắn `@pytest.mark.production_profile`.
- `tools/engine_replay.py` mặc định bật micro (đó là thứ nó kiểm định). Có thể đổi bằng `--set`.

## 5. Kỳ vọng và theo dõi

- **Tần suất:** khoảng 6 lệnh trend/tháng cho 3 coin. Mỗi lệnh giữ khoảng 2 ngày (trung vị). Phần lớn lệnh lỗ nhỏ; lãi đến từ vài sóng lớn.
- **Tiền:** với $150–300 mỗi lệnh, trung bình +67 bps → khoảng **+$1.5/lệnh, ~$9/tháng** nếu tương lai giống 2025–26. Độ lệch rất lớn: tháng âm là bình thường.
- **Cần kiên nhẫn:** khoảng 100 lệnh, tức nhiều tháng, mới đánh giá được bằng mSPRT hay `edge_check` trên `trend_trades.jsonl`.
- **Muốn quay lại hành vi cũ:** đặt `trend_lane='paper'` và `micro_standalone=True` trong Profile demo (`core/engine.py`).
