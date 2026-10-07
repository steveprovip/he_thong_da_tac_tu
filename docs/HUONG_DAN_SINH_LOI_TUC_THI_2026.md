# HƯỚNG DẪN "ẤN CHẠY" 2026 — một lệnh, ≤ 15 phút, chỉ vào khi G > 3C

> Nói thẳng trước: **không có phần mềm nào bảo đảm "bấm chạy là sinh lời".**
> Những gì hệ thống làm được là: trong ≤ 15 phút đo xem thị trường *lúc này*
> có kênh nào gross > 3 lần chi phí không. Nếu có, nó hành động trong các cổng
> an toàn. Nếu không, nó thoát ngay với mã 2. Nó không đào data và không nới
> filter. Với vốn hiện tại **3.85 USDT**, mọi kênh đều nằm dưới mức notional
> tối thiểu của sàn, nên kết quả thật sẽ là `CAPITAL_GATE` (exit 3).

## 1. Một lệnh

```bat
CHAY_SINH_LOI_NGAY.bat              :: shadow — không đặt lệnh
CHAY_SINH_LOI_NGAY.bat testnet      :: testnet — giao carry cho core.carry_runtime nếu đủ cổng
python run_alpha_instant.py --horizon-days 30 --equity 50 --notional 20   :: ví dụ khi có vốn
python run_alpha_instant.py --jev   :: thêm JEV/TypeSafe làm veto thứ hai (cần TYPESAFE_API_KEY)
```

| Pha | Thời gian | Việc |
|---|---|---|
| Warm-up | 5–30 s (REST) | Binance: premiumIndex, fundingInfo, 24hr, bookTicker perp + spot, 21 kỳ funding và OI cho top-20 · Bybit: tickers + funding interval. **Không ghi L2** |
| Rank | < 1 ms | `core/instant_alpha.rank_opportunities` + System-1 score. Kênh chỉ `EXEC` khi score ≥ 8.5 ⇔ G/C ≥ 3 |
| Act | — | testnet + có kênh EXEC + `two_leg_capital_gate` LIVE → `python -m core.carry_runtime --carry --mode testnet --symbols …` (**giữ cặp**, không có `--seconds`). Mainnet: chỉ in lệnh, không bao giờ tự chạy (RELEASES.md G2) |
| Shock | đến trần 15 phút | Shadow `!forceOrder@arr` ≥ 500k USDT/60 s, fade, chốt theo mark sau 5 phút. Chỉ được chạy live khi `EarlyStopGate` = `EDGE_CONFIRMED` **và** gross trung bình ≥ 25 bps |
| Exit | — | `data/instant/<utc>-<mode>.json` · 0 hành động · 2 không có cơ hội · 3 capital gate · 4 mạng |

**Vì sao carry không bị cắt ở phút 15:** carry lấy lợi nhuận từ các kỳ settle trong 7–30 ngày. Đóng cặp ở phút 15 nghĩa là trả đủ chi phí C mà không nhận được đồng funding nào, tức là lỗ chắc chắn. Trần 15 phút áp cho pha *quyết định*: sau 15 phút, hoặc đã có vị thế carry, hoặc chương trình tự thoát.

## 2. Số học carry (Binance VIP0)

C = 2 × 10 (spot) + 2 (perp maker vào) + 5 (perp taker ra) + spread hai chân + 2 slippage ≈ **30 bps**.
G = min(funding hiện tại, trung bình 21 kỳ) × số kỳ settle trong horizon.

| Horizon | Số kỳ 8h | Funding tối thiểu để G > 3C | Tương đương APR |
|---|---|---|---|
| 7 ngày | 21 | **4.3 bps/8h** | ~47% |
| 14 ngày | 42 | 2.1 bps/8h | ~23% |
| 30 ngày | 90 | **1.0 bps/8h** | ~11% |

Mức "0.05–0.2%/ngày" nêu trong directive tương đương 1.7–6.7 bps/8h, chỉ gặp khi thị trường nóng. Funding âm cần short spot (vay margin), nên được liệt kê `NEEDS_SPOT_SHORT` và không thực thi. Funding chênh Binance–Bybit được liệt kê `RESEARCH_ONLY`: snapshot không có lịch sử của khoản chênh lệch, và cần tài khoản Bybit.
Với 10 USDT notional ở horizon 30 ngày và 1 bps/8h: net = 90 − 30 = 60 bps ≈ **0.06 USDT/tháng**. Muốn kiếm được tiền có ý nghĩa thì cần vốn, không cần thêm thuật toán.

## 3. JEV System 1

| | Kết quả |
|---|---|
| SDK thật | `F:\JEV_AI\typesafe-sdk-python`: `TypeSafeClient.system_one(state, questions={Noul/Choice/Score})`, gọi HTTP tới `https://api.typesafe.ai`, cần `TYPESAFE_API_KEY`. `client.evaluate(...)` trong README_TONG_QUAN.md **không có** trong source |
| Latency đo (`python tools/bench_system1.py`) | Swarm 8 agent `evaluate()` p50 **22.9 µs**, p99 79 µs. System-1 cục bộ: adverse p50 **2.0 µs** / p99 5.0 µs; score p50 **1.5 µs** / p99 3.3 µs. **0 token LLM mỗi quyết định**, trước và sau |
| JEV qua mạng | Bị giới hạn bởi RTT (từ châu Á khoảng 100–300 ms), nên không dùng được trong đường nóng 10 ms. `JevGatekeeper` bỏ qua mọi câu trả lời quá 10 ms hoặc bị lỗi. `--jev` chỉ dùng JEV làm **veto** một lần cho mỗi ứng viên, budget 2 s |
| Lọc lệnh xấu (L2 mainnet, gate độ sâu, toxicity 0.7) | Chặn 13.6% quote OOS (204/1500). Markout 30s của fill còn lại: OOS −0.73 so với −1.12 bps; TRAIN −1.22 so với −1.15. **Không ổn định**, và cả hai vẫn cách ngưỡng 8.5 bps khoảng 9 bps |

Tiền đề "giảm từ vài giây xuống < 10 ms, bớt 90% token" không đúng với repo này: đường quyết định vốn đã là Python tất định, chạy cỡ micro-giây và không gọi LLM. Nút thắt là edge, không phải tốc độ ra quyết định.

## 4. Ba thị trường ngoài crypto (`research/alternative_alphas/`)

| Thị trường | Module | Chi phí thật | Edge có ở đâu | Khả thi với 3.85 USDT |
|---|---|---|---|---|
| Prediction markets | `prediction_markets.py` | Polymarket: taker `C·rate·p(1−p)`, rate 0–0.07 theo nhóm, **maker 0**. Kalshi: taker `ceil(0.07·C·P(1−P))`, maker 0.0175 | Rổ multi-outcome (negRisk) có tổng ask < 1 − phí (không rủi ro, nhưng vốn bị khoá tới khi resolve). Mispricing theo mô hình: cần q đã hiệu chỉnh bằng Brier. YES+NO nhị phân trên Polymarket dùng chung một book nên complement arb ≈ 0 | **KHÔNG — ĐÍNH CHÍNH 2026-09-24:** danh sách chặn của Polymarket không có Việt Nam, nhưng Cục A05 (Bộ Công an) đã xác định Polymarket là nền tảng cờ bạc và chặn truy cập tại Việt Nam. Không dùng kênh này từ Việt Nam. Xem `docs/VON_NHO_100K_2026.md` |
| ETF vs E-mini basis | `etf_basis.py` | Phí hợp đồng, phí ETF, spread, phí vay khi reverse | Desk định chế với chi phí vốn dưới 1 bp; phần lệch bán lẻ nhìn thấy thường nằm trong dải chi phí của chính mình | **Không**: 1 MES ≈ 5 × index ≈ 30k USD/chân. Nối ai-hedge-fund: `verdict_from_agent_signals` (định dạng `{'signal','confidence'}` theo `hedge_fund/llm/contract.py`) → verdict v2, chỉ chặn |
| CEX–DEX / DEX–DEX | `cex_dex.py` | LP fee 5–100 bps, taker CEX, gas, flash premium, tip cho builder | CEX–DEX **không nguyên tử** (chân CEX off-chain → rủi ro tồn kho và độ trễ). DEX–DEX nguyên tử là sân của MEV searcher, phần lớn lợi nhuận trả cho builder | **Không**: `breakeven_notional` cho thấy gas nuốt hết gap ở notional nhỏ. Không deploy contract |

Nguồn phí (tra 2026-09-24): [Polymarket fees](https://docs.polymarket.com/trading/fees) · [Polymarket geographic restrictions](https://help.polymarket.com/en/articles/13364163-geographic-restrictions) · [Kalshi fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf).

## 5. Kiểm thử

`python -m pytest -q` → **600 passed** (576 trước đó + 24 trong `tests/test_instant_alpha.py`).
Môi trường phát triển không truy cập được Binance/Bybit/Polymarket (proxy chặn), nên toàn bộ luồng được kiểm bằng snapshot bơm vào (`--snapshot-file`). Lần chạy thật đầu tiên phải là **shadow trên máy của anh**.
