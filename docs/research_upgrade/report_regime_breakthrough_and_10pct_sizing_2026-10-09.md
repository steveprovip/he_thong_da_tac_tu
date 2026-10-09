# Báo cáo nghiệm thu (ee) 09/10/2026: bộ lọc regime đa khung, khoá lợi nhuận và sizing 10 % vốn cho làn trend 4H

**Phạm vi.**
- Code, test và replay offline. Không khởi động lại engine, không gửi lệnh nào.
- Không ghi vào `data/engine_trades.jsonl` hay `data/trend_trades.jsonl` (hai file chỉ có trên máy bạn, không nằm trong repo). Executor chỉ **append**; mỗi dòng trend mới có thêm 2 trường `regime_entry`, `best`.
- Thay đổi có hiệu lực khi bạn tự restart `run.bat demo`. **Live không đổi** (`trend_lane='off'`, `capital_allocation_pct=None`). Code vẫn từ chối chạy làn trend ngoài demo.
- Không đổi đòn bẩy. $500 notional trên ví $5 000 tương đương 0.1x.

## 0. Tóm tắt

| | Trước (V0, đang chạy) | Sau (G6 + L1 + S10, demo mới) |
|---|---|---|
| Sharpe ngoài mẫu 2025-01 → 2026-09 | +0.59 (size cũ) / +0.43 (size 10 %) | **+0.83** (size 10 %) / +0.92 (size cũ) |
| Sharpe DEV 2022–24 | +0.52 / +0.63 | **+1.27** / +1.14 |
| Max DD ngoài mẫu | 13.3 % / 9.6 % | **5.9 %** / 8.3 % |
| Riêng 2025 (năm đi ngang) | SR −0.07 / −0.32 | SR **+0.23** / +0.16 |
| DSR ngoài mẫu (148 lần thử) | 0.20 / 0.15 | **0.30** / 0.34 |
| Lệnh ngoài mẫu | 133, thắng 34 %, trung vị −59 bps, TB +45 | 72, thắng 47 %, trung vị **−30**, TB **+169** |
| Lãi kỳ vọng (ví $5 000) | — | +4.9 %/năm ≈ **$20/tháng**, phương sai lớn |

**Mục tiêu của form đạt được đến đâu:**
- **Max DD giảm: đạt.** Ngoài mẫu từ 9.6–13.3 % xuống 5.9–8.3 %.
- **Sharpe > 1.0 ngoài mẫu: chưa đạt.** Đạt 0.83–0.92. Riêng 2026 là 1.35–1.47, nhưng năm 2025 kéo xuống.
- **DSR > 0.80: chưa đạt, còn xa.** Đạt 0.30–0.34.
- **Chưa phân biệt được với may mắn.** Chênh lợi nhuận ngày so với V0 (ghép cặp) là +0.07 bps/ngày, CI95 [−3.0, +3.3]. Phần cải thiện chủ yếu đến từ **giảm biến động và DD**: bớt lệnh whipsaw, đứng ngoài khi đi ngang. Lợi nhuận trung bình không tăng có ý nghĩa.

Vì vậy cấu hình mới chỉ bật trên demo, theo yêu cầu trực tiếp của bạn, giống vòng (dd). Live vẫn tắt. Ngưỡng của chính dự án cho tiền thật là DSR > 0.95.

## 1. Nhiệm vụ 1: bộ lọc regime (`core/trend_lane.RegimeGate`)

**Chỉ báo** (tính trên nến 4H đã đóng):

| Thành phần | Định nghĩa | Nguồn số liệu |
|---|---|---|
| KER | Kaufman ER 10 nến = \|c_i − c_{i−10}\| / Σ\|Δc\| | close 4H |
| ADX | Wilder ADX(14) | high/low/close 4H |
| Khối lượng | quote volume nến / SMA20 của 20 nến trước | kline `r[7]` |
| CVD nến | taker-buy quote − taker-sell quote = 2·`r[10]` − `r[7]` | kline mainnet (REST + data.binance.vision) |

**Regime:**
- **TREND:** KER > 0.40 và ADX > 25.
- **CHOP:** KER < 0.25 và ADX < 20.
- **MIXED:** các trường hợp còn lại.

Ngưỡng lấy từ form. Cửa sổ dùng mặc định sách giáo khoa (ER 10, ADX 14, SMA 20), cố định **trước** khi chạy replay, không tinh chỉnh.

**Xác nhận breakout:** khối lượng > 1.2 × SMA20 **và** CVD của nến cùng chiều lệnh. Thiếu volume/CVD được coi là **UNKNOWN**: không vào lệnh, không bao giờ coi là 0 rồi cho qua.

**Trong OOS, tỉ lệ nến 4H theo regime** (cả 3 coin gần giống nhau): TREND 20 %, MIXED 64 %, CHOP 15 %.

### 1a. Replay (`tools/trend_regime_replay.py`, đăng ký trước trong docstring)

**Thiết lập:**
- Mô hình backtest giống executor demo: `backtest_brackets`, size cố định lúc vào, thoát theo tín hiệu, stop thảm hoạ 8 ATR kiểm tra trong nến, taker 6 bps/phía, có funding.
- Dữ liệu: kline 1h → 4H của BTC/SOL/DOGE, 2022-01 → 2026-09, tải từ data.binance.vision (342 file).
- **Quy tắc chọn:** biến thể có Sharpe DEV cao nhất. Nó thay V0 chỉ khi SR ngoài mẫu ≥ V0 và chênh lệch ghép cặp không âm có ý nghĩa.

Bảng dưới dùng size cũ (|weight|), không khoá lợi nhuận.

| Biến thể | SR DEV | SR OOS | %/năm OOS | DD OOS | Lệnh OOS | Thắng | Trung vị / TB bps |
|---|---|---|---|---|---|---|---|
| V0 cơ sở | +0.52 | +0.59 | +9.3 | 13.3 % | 133 | 34 % | −59 / +45 |
| G1 chỉ TREND | +0.64 | +0.63 | +10.5 | 13.9 % | 73 | 41 % | −50 / +113 |
| G2 cấm CHOP | +0.51 | +0.59 | +9.5 | 13.3 % | 124 | 35 % | −59 / +49 |
| G3 chỉ xác nhận Vol+CVD | +0.67 | +0.59 | +9.5 | 12.5 % | 88 | 35 % | −88 / +92 |
| G4 = G1 + G3 (đúng form) | +0.45 | +0.72 | +11.9 | 11.1 % | 57 | 42 % | −64 / +181 |
| G5 = G2 + G3 | +0.67 | +0.61 | +9.7 | 12.5 % | 84 | 36 % | −107 / +102 |
| **G6 = G4 + thoát khi CHOP** | **+0.95** | **+0.77** | +10.1 | **7.6 %** | 114 | 47 % | −66 / +155 |
| G7 = G2 + thoát khi CHOP | +0.70 | +0.44 | +6.1 | 12.1 % | 581 | 45 % | −18 / +28 |

**G6 được chọn theo DEV và qua kiểm tra OOS.**
- Lợi ích tập trung ở năm whipsaw 2025: SR từ −0.07 lên +0.30, DD từ 13.3 % xuống 5.1 %.
- Năm 2026 đi trend rõ: SR gần như giữ nguyên (1.19 → 1.23), nhưng %/năm giảm (23.0 → 19.2) vì đứng ngoài nhiều hơn.
- G7 (thoát khi CHOP nhưng không đòi TREND để vào lại) ra vào liên tục, 581 lệnh, là biến thể tệ nhất. Thoát khi CHOP chỉ có tác dụng khi điều kiện vào lại đủ khắt khe.

**Độ nhạy của G6** (chỉ để kiểm tra độ bền, không dùng để chọn; `snapshot_2026-10-09ee/g6_sensitivity.json`):
- Đổi ER 8/14, KER trend 0.35/0.45, ADX 20/15 hoặc 30/25, vol 1.0×/1.5×, KER chop 0.20 → SR OOS 0.49–0.80, DD 5.8–13 %.
- Biên dưới là ER 14 (OOS 0.49, năm 2025 âm). Kết quả không nằm trên một "mũi dao" tham số, nhưng cũng không bền tuyệt đối.

**Mean-reversion biên rộng trong CHOP: không làm.** Form cho chọn "Cash/Observer **hoặc** MR". Chưa có bằng chứng nào cho MR 4H sau phí, nên làn trend chỉ đứng ngoài (observer).

### 1b. Cách executor áp dụng (`core/trend_executor.py`)

- `targets()` tính regime, KER, ADX, tỉ lệ volume, CVD và `gate_ok`/`gate_why` cho nến 4H vừa đóng. Telemetry `trend_lane['4h_active'].targets` hiển thị các giá trị này.
- **Vào lệnh:** cổng chặn → event `TREND 4H · SOLUSDT BUY signal held back: regime MIXED (needs TREND) -> observer`. Lệnh chờ nến sau, miễn tín hiệu còn cùng chiều.
- **Thoát theo tín hiệu không bao giờ bị cổng chặn.** Nến đóng ở regime CHOP → đóng lệnh với lý do `CHOP`.

## 2. Nhiệm vụ 2: sizing 10 % vốn (`core/trend_lane.Sizing`, `TrendExecutor.size`)

**Công thức:**
```
notional = min(capital_allocation_pct, trend_risk_pct / (trend_disaster_atr × ATR14_4h / giá)) × wallet,
           và ≤ trend_notional_max
demo: 0.10, 0.01, 8 ATR, $600
```

**Cách công thức vận hành:**
- **ATR thấp** (thị trường nén): trần 10 % ràng buộc → $500.
- **ATR cao:** size co lại để lỗ khi chạm stop thảm hoạ ≤ 1 % ví ($50). Nến gap xuyên stop vẫn có thể lỗ hơn mức này.
- Ví dụ với ATR trung vị (vòng dd):

| Coin | 8 ATR | Size |
|---|---|---|
| BTC | ≈ 9.3 % | trần 10 % → **$500** |
| SOL | ≈ 16.5 % | 6.1 % → **≈ $303** |
| DOGE | ≈ 17.7 % | 5.6 % → **≈ $282** |

- Trung bình ngoài mẫu là 7.5 % ví, khoảng $377 mỗi lệnh. Mức cũ là $150–300.
- ATR không xác định → size 0, không vào lệnh.

**Không chọn phương án "margin 10 % × 5x = notional $2 500".** Với stop 8 ATR (9–18 %), mỗi lệnh sẽ rủi ro $230–440, tức 4.6–8.8 % ví. Điều này vi phạm chính yêu cầu "rủi ro mỗi lệnh ≤ 1 %".

**Đối chiếu Kelly** (`kelly_f` = mean/var của lợi nhuận trên notional, DEV):

| Làn | Kelly đầy đủ | 0.25× Kelly | 0.5× Kelly |
|---|---|---|---|
| V0 | 0.32 | 0.08 | 0.16 |
| G6 + L1 | 0.91 | 0.23 | 0.45 |

- Với làn mới, 10 % thấp hơn 0.25× Kelly, nên nằm ở phía an toàn của dải 0.25–0.5× form yêu cầu.
- Với làn cũ, 10 % đã vượt 0.25× Kelly.
- Kelly này ước lượng từ backtest với 3 coin tương quan, nên chỉ để tham khảo.

**Rủi ro tổng:** cả 3 coin cùng giữ lệnh → tối đa 30 % notional và khoảng 3 % ví nếu cả ba cùng chạm stop thảm hoạ.

**Size không tạo ra lợi thế.** Sharpe theo size 10 % của V0 là +0.43, thấp hơn size cũ (+0.59). Lý do: size mới dồn vốn vào BTC, giảm SOL/DOGE. Chỉ khi có bộ lọc thì size 10 % mới có SR 0.83–0.84.

## 3. Nhiệm vụ 3: khoá lợi nhuận động

- **Giữ nguyên:** stop thảm hoạ STOP_MARKET trên sàn ở 8 × ATR(14, 4H), không đổi theo trailing. Không có TP/SL chặt.
- **Soft profit lock** (`Profile.trend_lock_pct=2.0`):
  - Kích hoạt khi giá đóng 4H tốt nhất kể từ lúc vào đạt ≥ +2 % so với giá vào.
  - Từ đó, nến 4H đóng vượt `best ∓ 3.5 × ATR` (chandelier) → đóng lệnh ngay, lý do `PROFIT-LOCK`.
  - Sau đó không vào lại cùng chiều cho tới khi tín hiệu đổi dấu.
  - Có thể đổi sang Donchian mid 20 ngày bằng `trend_lock_trail='donchian_mid'`.
- **Replay trên G6** (quy tắc: chỉ giữ lock nếu không làm giảm SR cả DEV lẫn OOS):

| | SR DEV | SR OOS | DD OOS | Lệnh | Trung vị / TB bps | Cách thoát |
|---|---|---|---|---|---|---|
| G6 không lock | +0.95 | +0.77 | 7.6 % | 114 | −66 / +155 | CHOP 88, SIGNAL 22, SL 4 |
| **G6 + L1 chandelier 3.5 ATR @ +2 %** | **+1.14** | **+0.92** | 8.3 % | 72 | **−30** / +169 | LOCK 36, CHOP 24, SIGNAL 10, SL 2 |
| G6 + L2 Donchian mid @ +2 % | +0.92 | +0.84 | 7.5 % | 85 | −29 / +170 | — |

- L1 qua quy tắc, L2 không qua (DEV giảm).
- Với size 10 %, L1 có SR DEV 0.71 → 1.27 và OOS 0.84 → 0.83 (ngang nhau).
- Trên làn V0 không có bộ lọc, L1 làm giảm SR DEV (0.52 → 0.42). Vì vậy kết luận "trailing cắt sóng" của vòng (dd), vốn dùng trailing 3 ATR không chờ +2 %, không mâu thuẫn với kết quả này.
- **Không hứa chốt được lãi.** Lock là mềm (engine kiểm tra lúc nến đóng). Nếu engine tắt, chỉ còn stop thảm hoạ 8 ATR trên sàn.

## 4. Nhiệm vụ 4: kiểm định và an toàn

- **Replay:**
  - `python tools/trend_regime_replay.py --data <dir kline 1h + funding> --json out.json` chạy 64 biến thể trong khoảng 13 phút.
  - Kết quả lưu ở `docs/research_upgrade/snapshot_2026-10-09ee/` (JSON replay, độ nhạy, SHA256SUMS).
  - A/B ghi ở `experiment_register.md` (EE1, AB-11..14) và `trial_log.jsonl`.
- **Về `tools/trend_execution_replay.py` form chỉ định:** công cụ này đo chi phí khớp lệnh trên aggTrades quanh mỗi lần đóng nến, không đo tác dụng của bộ lọc regime. Câu hỏi regime được trả lời bằng replay theo nến ở trên, cùng mô hình với executor. Không chạy lại chi phí khớp: kết quả maker −1.2 bps so với taker vẫn áp dụng cho từng lệnh, và số lệnh giảm (133 → 72) nên tổng phí còn giảm thêm.
- **Lưu ý con số mới:** trung bình lệnh của V0 ở đây là +45 bps, khác +67 bps của báo cáo (dd). Lần này tính trên đúng mô hình sự kiện có stop 8 ATR; báo cáo (dd) dùng backtest theo band. Trung vị −59 khớp nhau.
- **Test:**
  - `python -m pytest -q` trong cloud cho kết quả **1437 passed, 28 skipped, 2 failed**.
  - Hai test fail giống hệt trên code gốc trước vòng này (đã kiểm tra bằng `git stash`). Chúng cần file local không có trong repo: signal-lab testnet trong `data/` và `tools/check_models_live.py`.
  - Trên máy bạn dự kiến 1452 + 15 test mới = 1467 passed.
  - 15 test mới trong `tests/test_trend_regime_breakthrough.py`:
    - chỉ báo KER và ADX;
    - phân loại regime; UNKNOWN volume không cho vào lệnh;
    - cổng TREND + volume + CVD;
    - sizing 10 % và trần rủi ro 1 %;
    - backtest có gate, lock và sizing;
    - executor giữ lệnh chờ xác nhận rồi mới mở;
    - thoát khi CHOP ghi dòng trade; `exit_check` chandelier và Donchian;
    - `trend_fetch`/`klines_to_bars` mang theo volume;
    - profile production (demo bật, live không đổi).
  - Test của vòng (dd) cố định cấu hình (dd) trong fixture.
- **Rollback** (Profile demo trong `core/engine.py`): `trend_regime='off', trend_confirm=False, trend_chop_exit=False, trend_lock_pct=None, capital_allocation_pct=None, trend_notional_max=300.0`.

## 5. Kỳ vọng sau khi restart demo

- **Tần suất:** khoảng 3–4 lệnh trend/tháng cho cả 3 coin (trước khoảng 6). Phần lớn thời gian ở chế độ observer: TREND chỉ chiếm khoảng 20 % số nến.
- **Size:** $280–500 mỗi lệnh.
- **Vị thế đang mở từ vòng (dd)** (trong `data/trend_active.json`, nếu có): khi restart sẽ chịu luôn thoát CHOP và lock. Mốc `best` tính từ giá vào.
- **Theo dõi:**
  - event `TREND 4H · … held back: …`, `CLOSE … CHOP`, `CLOSE … PROFIT-LOCK`;
  - trường `regime_entry` trong `data/trend_trades.jsonl`.
- **Đánh giá:** vài chục lệnh chưa nói lên gì. Cần khoảng 100 lệnh (khoảng 2 năm ở tần suất này) mới có kết luận thống kê bằng `tools/edge_check.py` hoặc mSPRT. Tháng lỗ là bình thường.
