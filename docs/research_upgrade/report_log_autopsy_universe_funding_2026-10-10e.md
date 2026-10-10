# Báo cáo 10/10/2026 (e): giải phẫu log burst, Universe Screener 15m, kiến trúc Funding Arb

**Phạm vi.** Đọc log DEMO 19:19 → 22:12 (form `FORM_CLAUDE_OPUS5.5_LOCAL_LOG_AUTOPSY_MULTI_ASSET_ALPHA_2026-10-10.md`) và `data/micro_trades_shadow.jsonl` (chỉ đọc). Gọi API công khai Binance/Hyperliquid (không khóa, không lệnh). Thêm `core/universe.py` (không nối vào engine). Không khởi động lại engine, không đổi Profile, không đụng `data/engine_trades.jsonl` / `data/trend_trades.jsonl`.

## 0. Kết luận

| Câu hỏi | Trả lời |
|---|---|
| Burst thua do adverse selection? | **Đúng**, nhưng số liệu của form sai: **53 lệnh** (không phải 40), thắng 2 (3.8 %), tổng **−455.7 bps / −3.40 U** (không phải −376 / −2.8). |
| Giữ làn 1 active, làn 2 SHADOW? | **Giữ.** Không đổi gì: demo `trend_lane='active'`, `burst_lane='shadow'`, `config/burst_verdict.json` SHADOW. |
| Quét 30 coin cho P15_rr25atr để có "edge > 50 bps"? | **Chưa có edge để nhân lên.** HOLD-OUT P15_rr25atr net −5.48 bps [−11.97, +2.20], gross +1.73 CI chứa 0. "50–250 bps" là biên độ sóng, không phải kỳ vọng. Quét rộng chỉ là **đầu vào cho replay**; chỉ bật shadow trên mã nào qua cổng. |
| Funding arb "20–50 % APR, phi rủi ro"? | **Không đúng với dữ liệu.** 15 mã lớn trên Binance 166 ngày qua: 1.2–6.8 % APR. Spread Binance↔Hyperliquid: 2.4–5.1 % APR. Hoà vốn phí cần giữ ~3–4 tuần. Có rủi ro basis, chân lệnh, thanh lý, đối tác. |
| Test | 1 533 passed trước thay đổi; xem mục 5 cho số sau thay đổi. |

## 1. Giải phẫu log burst (nhiệm vụ 1)

`snapshot_2026-10-10e/burst_log_autopsy.json` — 53 dòng trong log khớp 53 row ledger cùng cửa sổ.

| | n | gross bps | net bps | ghi chú |
|---|---|---|---|---|
| Tất cả | 53 | −2.62 | −8.60 (bootstrap CI [−10.05, −7.01]) | phí trung bình 5.98 bps |
| TIME-TAKER | 35 | −2.67 | −9.67 | hết 60 s + 10 s maker → taker |
| MAKER-EXIT | 18 | −2.52 | −6.52 | **thoát time-stop bằng maker**, không phải stop giá xấu như form viết |
| TP / SL (±30 bps) | 0 | — | — | giá không đi 30 bps trong 60 s lần nào |
| Toàn bộ 211 lệnh burst shadow | 211 | −2.11 | −8.36 | win 4.7 % |

- Replay hold-out dự báo −7.17 (SOL) / −7.69 (DOGE) bps; forward shadow −8.6 [−10.05, −7.01]. **Replay đã dự báo đúng.**
- Cơ chế: TP/SL không bao giờ chạm → toàn bộ lỗ gross là markout sau khi khớp (−2.6 bps) = adverse selection. Phí chỉ cộng thêm ~6 bps.
- Thời gian giữ trung vị 70 s (form ghi 15–30 s).
- VPIN pre-emptive cancel (form mục 3C) **không làm**: không thuộc 3 nhiệm vụ, và với gross −2.6 trên mọi tín hiệu (`tfi` −1.72, `surge` −5.26) một bộ lọc hủy lệnh cần đảo dấu markout, không chỉ giảm nó.

## 2. Universe Screener cho làn 15m (nhiệm vụ 2)

### 2.1 `core/universe.py`
- `screen(listings, daily, as_of_ms, top_n=30, lookback_days=14, min_median_qv=50e6, min_range_bps=150, max_range_bps=1500, min_age_days=90)` → `(picked, reasons)`.
- **Point-in-time:** chỉ dùng nến ngày đã đóng trước `as_of_ms` → cùng hàm cho live (REST) và replay (data.binance.vision theo tháng, gồm cả mã đã delist → tránh survivorship).
- **Xếp hạng bằng trung vị quote volume 14 ngày**, không dùng ticker 24 h. Ngày 10/10, top-24h có MAGIC (range 77 %), US (72 %), LUMIA (53 %) — mã pump; trung vị + trần range loại chúng.
- Chỉ USDT perpetual `TRADING`, `underlyingType == 'COIN'`, tuổi ≥ 90 ngày. Thiếu dữ liệu → loại (UNKNOWN), không điền 0.
- `tests/test_universe.py` (7 test): xếp hạng, spike 1 ngày, trần range, không look-ahead, UNKNOWN, parser payload Binance, không nối vào `core/` `live/`.

### 2.2 Chạy trên dữ liệu thật 10/10 (`snapshot_2026-10-10e/universe_2026-10-10.json`)
525 USDT perp đang giao dịch; 154 mã ≥ $15 M/24 h được lấy nến ngày; 39 qua lọc, top 30:
BTC, ETH, SOL, ZEC, XRP, NEAR, HYPE, DOGE, SUI, PUMP, WLD, ENA, UNI, BNB, ADA, 1000PEPE, AVAX, TAO, LINK, ONDO, AAVE, FIL, ARB, LTC, TRUMP, SAND, BCH, PENGU, XLM, ZRO.

### 2.3 Vì sao chưa nối vào engine
- Ước lượng: 4.4 lệnh/ngày trên 3 mã → ~1.5/mã/ngày → 30 mã ≈ **44 lệnh/ngày**. Với net HO −5.48 bps × $150 ≈ **−$3.6/ngày (≈ −$110/tháng)** nếu active.
- 30 altcoin tương quan cao với BTC: 30 mã ≠ 30 cược độc lập.

### 2.4 Kế hoạch kiểm định (đăng ký trước, chưa chạy)
1. Universe theo tháng: `screen(as_of = ngày 1 mỗi tháng)` trên nến ngày 2024-06 → 2026-09.
2. Dữ liệu: kline 1m data.binance.vision cho hợp các universe tháng (~40–60 mã × 27 tháng, ước tính 2–4 GB) — **cần anh/chị cho phép tải**.
3. `tools/intraday_replay.py`: thêm `--universe <json tháng → mã>`, chỉ giữ lệnh có entry trong tháng mà mã thuộc universe. Biến thể **cố định** `P15_rr25atr`, không tune lại.
4. DEV 2025 / HOLD-OUT 2026-01..09, bootstrap theo ngày, báo cáo **gộp**; per-symbol chỉ để chẩn đoán (chọn mã thắng trên HO = data-mining).
5. Cổng giữ nguyên (n ≥ 100, cận dưới CI HO > 0, win > 50 %). **Cần anh/chị quyết:** điều kiện win > 50 % không thể đạt với R:R 2.5 (hòa vốn ở ~29 % win trước phí: 1 / 3.5) → đề xuất thay bằng cận dưới CI net > 0 cho riêng nhóm R:R ≥ 2.
6. Chỉ khi qua cổng: `intraday_symbols` = universe, shadow, giới hạn số vị thế đồng thời.

## 3. Funding Rate Arbitrage (nhiệm vụ 3)

### 3.1 Hiện trạng code (không viết mới)
- `core/funding_carry.py`: `plan_spot_perp` (APR ≥ 15 %, |basis| ≤ 1.5 %, chi phí = phí perp + 2 × phí spot + spread + trượt), `carry_votes`, `two_leg_capital_gate`.
- `core/carry_runtime.py`: `CarryRuntime`, `PairExecutionGateway`, `PairReconciler`, khóa tiến trình, câu xác nhận mainnet.
- Cổng vốn: ví thật 3.85 USDT < 10 USDT tối thiểu 2 chân → **RESEARCH_ONLY về cấu trúc**. Shadow `data/carry`: 0 chu kỳ. `carry-scan` 2026-09-10: 1/900 ứng viên.

### 3.2 Số đo (API công khai, 10/10)
`snapshot_2026-10-10e/funding_binance_166d.json`

| | Binance perp, 166 ngày | Binance ↔ Hyperliquid (short HL / long BN) |
|---|---|---|
| APR trung bình | BTC 4.6, ETH 3.5, SOL 1.2, XRP 1.4, DOGE 5.0, LINK 5.4, HYPE 6.8 % | BTC 2.4, ETH 4.3, SOL 3.4, DOGE 3.7, XRP 3.7, LINK 5.1 % |
| Chi phí vòng | ~30 bps (spot 10×2 + perp 2×2 + spread/trượt) | ~22 bps (4 lệnh maker 2/1.5 + spread/trượt) |
| Cửa sổ 7 ngày vượt chi phí | 0–3 % | 0–6 % (giá trị tuyệt đối, chọn chiều bằng hindsight → cận trên) |
| Hoà vốn | BTC ≈ 24 ngày | ≈ 3–4 tuần |

Ví dụ $2 000 notional mỗi chân, 3.4 % APR: ~$5.7/tháng funding − $4.4 phí vòng → ~$1.3 cho chu kỳ 1 tháng, trước rủi ro basis.

### 3.3 Kiến trúc đề xuất (theo pha, mỗi pha có cổng)
- **Pha 0 — offline, không khóa:** `tools/funding_spread_replay.py`: funding BN (`fapi/v1/fundingRate`) + HL (`info.fundingHistory`), basis MTM từ kline 1h hai sàn, phí theo `VENUE_SPECS`. Quy tắc vào đăng ký trước (spread trượt 3 ngày > chi phí / thời gian giữ), giữ tối thiểu N ngày. Cổng: HO net CI > 0 sau 4 lệnh + basis.
- **Pha 1 — mở rộng interface, chỉ đọc:** protocol riêng `CarryVenue` (không phình `BaseVenue`): `funding_view(symbol) -> {rate, next_ms, interval_h}`, `balances()`, `ioc(symbol, side, qty)` cho chân hedge (chân maker treo một mình = delta trần). Hyperliquid vẫn cần `signer` ngoài; không quản lý khóa trong repo.
- **Pha 2 — shadow:** `CarrySnapshot` cho cặp perp/perp → `carry_votes` + `PairReconciler` hiện có. Lệnh vẫn qua VESKA/AuthToken, RUNE veto.
- **Pha 3 — testnet** cả hai sàn trước khi có đồng tiền thật.

Rủi ro (không phải phi rủi ro): funding đổi dấu; basis giãn khi vào/ra; một chân khớp chân kia không (leg risk); thanh lý một chân khi giá chạy mạnh trong lúc chuyển USDC qua bridge (vài giờ); rủi ro sàn/L1; dự thảo cấm sàn nước ngoài tại VN (3/2026, chưa xác nhận hiệu lực); cổng vốn 2 chân.

## 4. Khuyến nghị
1. Giữ nguyên chế độ các làn. Burst giữ SHADOW — forward khớp replay, không có đường nào đổi dấu gross.
2. Bước tiếp theo đáng giá nhất: replay P15_rr25atr trên universe point-in-time (mục 2.4) — cần cho phép tải dữ liệu và quyết định về điều kiện win > 50 %.
3. Funding arb: chỉ làm Pha 0. Với 1–7 % APR và ví 3.85 USDT, đây là nghiên cứu, không phải nguồn "giao dịch nhiều".

## 5. Thay đổi, test, rollback
- Mới: `core/universe.py`, `tests/test_universe.py`, `snapshot_2026-10-10e/` (universe, funding, autopsy, SHA256SUMS), báo cáo này.
- Không đổi: engine, Profile, lanes, verdict, ledger.
- Test: trước thay đổi `python -m pytest -q` **1 533 passed**; sau thay đổi ghi ở `docs/harness/progress.md` (10-10 e).
- Rollback: xóa `core/universe.py` + `tests/test_universe.py` (không nơi nào import).
