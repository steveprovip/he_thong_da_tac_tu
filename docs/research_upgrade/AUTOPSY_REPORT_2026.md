# AUTOPSY REPORT 2026-11-13 — tiền lỗ đi đâu?

Công cụ: `python tools/autopsy_2026.py` (chỉ đọc; ~35 s; ghi `data/autopsy_2026.json`). Seed 20261113.
Nguồn: `data/engine_trades.jsonl` (133 lệnh đóng, 131 AUTO, Demo, 2026-10-06 → 11-10), `data/engine_orders.jsonl`
(8 738 dòng funnel), `data/micro_trades_shadow.jsonl` (812 lệnh ảo BURST), `docs/research_upgrade/trial_log.jsonl`
(45 thử nghiệm), và băng aggTrades MAINNET trùng thời gian (`data/ticks` 10-01..10-14 BTC/SOL/DOGE/BNB,
`data/aggtrades_20261110` 10-12..11-08 DOGE/XRP/PEPE, `data/recordings` 11-10 02-12 UTC).

Bot và recorder dừng từ 2026-11-10 19:28 (giờ máy). Commit `6a621f8` (11-11, nới `MICRO_OFI_MIN=0.45`,
`MICRO_MIN_EDGE_BPS=7`) **chưa có một lệnh nào** trong ledger: hiệu quả của nó là UNKNOWN.

## 0. Kết luận một dòng
Lỗ = **phí trên một edge gộp bằng 0**. Gross trung bình **+0.62 ± 1.15 bps/lệnh** (không khác 0), phí **4.57 bps/lệnh**,
net **−3.95 ± 1.16 bps/lệnh**. Phí = **129 %** khoản lỗ ròng (gross dương +1.20 U, phí 5.29 U, net −4.09 U).
Adverse selection, stop "nhiễu" và over-filtering **không** giải thích được khoản lỗ trên dữ liệu này.

## 1. Bảng phân rã (131 lệnh AUTO; cộng tính, chính xác trên ledger)

`net = gross − fee`; trên 96 dòng có markout: `gross = fill_markout(30 s) + body`.

| Thành phần | USDT | bps/lệnh (mean ± SE) | % của lỗ ròng 4.092 U |
|---|---|---|---|
| Gross (tín hiệu + thoát lệnh, trước phí) | **+1.197** | +0.62 ± 1.15 | −29 % (đóng góp **lời**) |
| Phí tổng | **−5.290** | 4.57 ± 0.08 | **129 %** |
| ├ maker (2.0 bps/leg trên Demo) | −3.871 | | 95 % (73 % của phí) |
| └ taker (4.0 bps/leg trên Demo) | −1.418 | | 35 % (27 % của phí) |
| Fill markout 30 s (spread capture + adverse selection), 96 dòng | **+1.227** | +1.26 ± 0.53 | **0 %** (là khoản **lời**) |
| Body (sau 30 s → exit), 96 dòng | −0.331 | | 8 % |

Theo chiến dịch:

| Nhóm | n | thắng | net U | gross U | phí U | net bps | gross bps |
|---|---|---|---|---|---|---|---|
| swarm Demo-size (notional trung vị 65 U) | 110 | 37 | −3.983 | +1.244 | 5.228 | −3.65 ± 1.23 | +0.90 ± 1.22 |
| micro (Nov, notional trung vị 5.1 U) | 21 | 12 | −0.109 | −0.047 | 0.062 | −5.51 ± 3.37 | −0.85 ± 3.31 |

Phí thực Demo, đo trên mọi lớp (maker_fills, taker_fills): **maker 2.0 bps, taker 4.0 bps / leg** (lớp (1,1) = 3.00 bps,
(0,2) = 4.00 bps). Code giả định taker 5 bps (VIP0 mainnet): mainnet đắt hơn Demo 1 bp mỗi leg taker.

## 2. Trả lời 4 câu hỏi của chỉ thị

| Nguyên nhân | Đo bằng | Kết quả | % lỗ |
|---|---|---|---|
| **Phí (maker/taker drag)** | ledger fee, tách bằng 2/4 bps | 5.29 U = 4.57 bps/lệnh; 73 % maker / 27 % taker | **129 %** |
| **Adverse selection** (giá quét xuyên GTX) | markout 30 s từ giá khớp | +1.26 ± 0.53 bps (t = 2.4, **dương**) | **0 %** trên lệnh đã khớp |
| **Stop sớm do nhiễu** | biến động mainnet cùng chiều lệnh 5/15/30 phút SAU exit, so với placebo (23 600 thời điểm ngẫu nhiên, cùng symbol/ngày/chiều) | 81 exit sớm: +0.5 / +3.3 / +4.2 bps (SE 1.9 / 3.6 / 4.3) vs placebo +0.2 / +0.6 / +1.4; USDT +1.5 / +5.2 / +0.6, CI90 bootstrap [−2.4, 5.7] / [−2.1, 13.4] / [−6.6, 7.8]. 6 SL có băng: 30 phút sau stop −3.3 ± 12.7 bps | **không chứng minh được** (CI chứa 0 ở mọi horizon; không loại trừ được tới ~5 U) |
| **Over-filtering / deadlock** | 747 tín hiệu bị từ chối/shadow (khử trùng 5 phút) có băng: bracket micro SL 0.22 % / TP 0.46 % maker-in, 30 phút | **−5.02 ± 0.94 bps/tín hiệu**; di chuyển 60 phút cùng chiều +3.3 ± 2.0 bps < phí khứ hồi | **0 %** — bộ lọc **tránh** lỗ ~5 bps/tín hiệu |

Chi tiết bộ lọc (khử trùng): SHADOW_KER (CHOP lock) 704 → −4.96 ± 0.97 bps; REGIME GATE 12 → −9.5 ± 4.5;
MICRO ALIGN (OFI < 0.65) 10 → −15.6 ± 9.6 bps, di chuyển 60 phút −36 ± 21 bps (các tín hiệu này đi NGƯỢC):
đây là bằng chứng (n nhỏ) chống lại việc hạ `MICRO_OFI_MIN` xuống 0.45 ở commit 6a621f8.

Ghi chú về adverse selection: trên lệnh ĐÃ khớp, markout 30 s dương. Chọn lọc ngược thể hiện ở chỗ khác: 440 lệnh
GTX AUTO → 96 khớp (21.8 %); trên băng mainnet 27 ngày (Phase 2, 11-10 d) lệnh khớp của harvester có TP-trước-SL
45-47.5 % (cần 67.6 %), 16 đặc trưng AUC 0.49-0.51: khớp hay không không mang thông tin hướng.

## 3. Bằng chứng phụ

- Lane BURST shadow (812 lệnh ảo, phí 2/5 bps): gross **−2.32 ± 0.30** bps, net **−7.80 ± 0.31** bps, win 14.7 %.
- trial_log, intraday holdout (Binance VIP0): mean_diff **−5.48 … −8.65 bps/lệnh**, win 26.8-59.9 % (chỉ thị ghi
  31-38.9 %: đúng cho 4 dòng 10-10 c/d; P15_tp35 có win 59.9 % mà vẫn −7.07 bps). Không một họ chiến lược nào dương.
- SMC event study 11-06 (`data/smc_backtest_*.json`, 6 tháng 1m BTC/ETH/SOL): BOS / CHoCH / sweep / FVG pull ở
  5/15/60 phút: gross |≤ 4| bps, net −4.5 … −11.7 bps sau 8.5 bps phí.
- Kiểm tra replay: lợi nhuận mainnet entry→exit trừ gross ledger Demo = −3.2 ± 1.6 bps, sai số tuyệt đối trung vị
  5.9 bps/lệnh (basis Demo vs mainnet): kết luận cấp < 6 bps/lệnh từ Demo là nhiễu.
- 37 / 131 lệnh không có băng mainnet (10-24, 11-09, 11-10 trước 02 UTC): UNKNOWN, không tính là 0.

## 4. Hệ quả cho thiết kế (đầu vào Giai đoạn 2)

1. Không thay đổi TP/SL/BE/phí nào biến một gross 0 thành net dương: trên giá driftless, EV của mọi bracket là
   `−(c_win·SL + c_loss·TP)/(TP + SL)` (= −(4 SL + 8 TP)/(TP + SL) bps với maker-in, maker TP, taker+slip SL).
   R:R 1.5-2.0 chỉ đổi win rate hoà vốn, không tạo edge. Cần **edge hướng** (p_win > SL/(TP+SL) + biên phí).
2. Cổng đúng là **net expectancy có bằng chứng**: chỉ vào lệnh khi LCB95 của net bps/lệnh của lane > 0 trên ≥ 30
   lệnh (thật hoặc ảo). Không có bằng chứng → giá coi là driftless → EV = −phí → từ chối.
3. Ngắt mạch thua liên tiếp làm giảm số lệnh trên edge 0, tức giảm phí: thuận chiều, không tạo edge.
4. Mục tiêu "5 U → +10 U/giờ" = +200 %/giờ. Với trần lỗ 0.05 U/lệnh, R:R 2, mỗi lệnh thắng tối đa ~+0.09 U sau phí:
   cần ≥ 111 lệnh thắng LIÊN TIẾP mỗi giờ. Không tồn tại trên dữ liệu này (xem `tools/micro_growth_sim.py`, Phase 4).

## 5. Tái lập
```
python tools/autopsy_2026.py            # in bảng tóm tắt, ghi data/autopsy_2026.json
python -m pytest -q tests/test_autopsy_2026.py
```

## 6. Giai đoạn 2-4: kết quả (thiết kế đăng ký trước trong `docs/harness/plan_alpha_reengineering_2026_11_13.md`)

### 6.1 SMC Fibo-discount + liquidity sweep + CVD absorption (R:R 1.5 / 2.0, BE fee-shield) — **FAIL, không nối vào engine**
`python -m research.smc_fibo_backtest` (83 s): 1m klines Binance USD-M, 11 symbol, TRAIN 2024-12..2025-12, HOLDOUT
2026-01..09, phí VIP0 maker 2 / taker 5 bps + 1 bp slip, khớp maker chỉ khi giá xuyên qua 1 tick, SL+TP cùng nến = SL.

| HOLDOUT | n | win | net bps/lệnh | gross bps | so với placebo (thời điểm ngẫu nhiên) | symbol > 0 |
|---|---|---|---|---|---|---|
| R1.5 BE off | 186 | 31.2 % | −13.1 ± 3.0 | −7.1 | −4.0 | 0/11 |
| R1.5 BE on | 186 | 30.6 % | −10.4 ± 2.8 | −4.2 | −2.0 | 1/11 |
| **R2 BE off (chọn trên TRAIN)** | 187 | 26.2 % | **−14.1 ± 3.3** (LCB95 −19.5) | −7.9 | −4.7 (CI95 [−11.8, +2.4]) | 1/11 |
| R2 BE on | 187 | 26.7 % | −10.8 ± 2.8 | −4.4 | −3.2 | 3/11 |

TRAIN (cùng biến thể): −3.1 ± 4.6 bps, n 222 (không khác 0). Placebo HOLDOUT: −9.4 ± 1.6 bps (= EV driftless −6.7 bps
+ ~3 bps chọn lọc ngược của lệnh maker chỉ khớp khi giá xuyên qua). Sharpe ngày (năm hoá) HOLDOUT −4.9; MDD 99 R.
Tài khoản 5 U, rủi ro 0.05 U/lệnh, ≤ 3 vị thế, dừng phiên sau 2 thua, kill 0.15 U/ngày: **−2.99 U sau 273 ngày
(−0.00046 U/giờ)**, MDD 3.02 U. Kelly học từ bằng chứng (kể cả lệnh ảo): 0 lệnh, tài khoản giữ nguyên.
Tiêu chí đăng ký trước: n ≥ 100 ✓, LCB95 > 0 ✗, thắng placebo ✗, ≥ 6/11 symbol ✗ → **REJECT**.
OFI L10 không kiểm được trên klines (không có depth); 11 giờ recording 11-10 quá ngắn cho setup H1/H4: UNTESTED.

### 6.2 Tăng trưởng 5 U (`python tools/micro_growth_sim.py`, 2 000 đường)
| Câu hỏi | Kết quả |
|---|---|
| +10 U/giờ ở rủi ro 0.05 U/lệnh, R 1.9 sau phí | cần 105 lệnh/giờ nếu **thắng 100 %**; 270/giờ ở win 60 %; 444/giờ ở win 50 % (profile live: tối đa 6 lệnh/giờ) |
| 5 → 15 U ở 3 %/lệnh, 6 lệnh/giờ | 14.7 h (win 50 %) … 8.7 h (win 60 %) — với edge KHÔNG tồn tại trên dữ liệu (holdout win 26 % ở 2R, hoà vốn ~36 %) |
| Bootstrap ledger 131 lệnh (−0.48 R/lệnh) | trung vị 4.34 U sau 7 ngày, −0.004 U/giờ |
| Bootstrap SMC-Fibo holdout (−0.51 R/lệnh) | trung vị 2.52 U sau 30 ngày, −0.0034 U/giờ |
| Giả định p 0.55, R 1.9, 6/giờ, 0.05 U | trung vị 11.2 U sau 30 ngày, P(≥ 15 U) 3.7 %; dừng phiên 2 thua cắt số lệnh/ngày còn ~1/6 |

### 6.3 Đã sửa trong code (đều siết rủi ro, không nới)
- Cổng net expectancy (`core/net_expectancy.py`, `Engine.net_ev_verdict`, `MICRO_NET_EV_GATE=on` trong `run.bat micro`):
  bằng chứng lane micro hiện tại = 21 lệnh thật (−5.5 ± 3.4 bps, < 30) + 0 lệnh ảo → `NET EV UNPROVEN` → Demo micro
  **không gửi lệnh**, chỉ chạy lệnh ảo cho tới khi lane có LCB95 > 0 trên ≥ 30 lệnh (cả ledger 131 lệnh: `NEGATIVE`).
- Dừng phiên sau 2 lệnh micro AUTO thua liên tiếp trong ngày (`micro_session_losses` 2), đọc từ ledger.
- `MICRO_HARVESTER=on` (TP 0.15 / SL 0.15) bị từ chối; shadow vẫn ghi markout.
- Knife shield 11-11 (adverse ≥ 25 k) chỉ áp cho micro: commit `6a621f8` làm hỏng `tests/test_anti_churn.py` (non-micro).
- `fractional_kelly_risk` / `confluence_scale` (Kelly 1/4 trên cận dưới posterior Beta, trần 0.05 U và 3 % vốn).
