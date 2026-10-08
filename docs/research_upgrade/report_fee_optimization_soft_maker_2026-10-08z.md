# Vòng (z) 08/10/2026 — Thất thoát phí: pre-stop maker exit, TP1 từng phần, trần demo 5 U / $1 000

Form: `prompts/FORM_CLAUDE_OPUS5.5_SOFT_MAKER_TRAILING_AND_FEE_OPTIMIZATION_2026-10-08.md`. Không khởi động lại
engine, không gửi lệnh, không đụng live. Ledger gốc không bị ghi (49 dòng).

## 1. Snapshot & mổ xẻ lệnh #49
`snapshot_2026-10-08z/` (SHA256SUMS). Lệnh #49 (code `72c9281`, conviction sizing `on` do người dùng bật):
BUY 2.04 SOL @ 113.47 (GTX, $231.5, bị cắt bởi trần rủi ro 1.0 U), MFE +0.154 %, đóng RATCHET 113.55 bằng
**STOP_MARKET** (`exit_mode: exchange`, 1 maker + 1 taker), gross +0.1632, phí 0.13895 = **6.0 bps** (demo: maker 2 +
taker **4**, cấu hình engine giả định taker 5), net **+0.0242 U**.

**Nguyên nhân gốc (tái hiện bằng test):** khi MFE ≥ 0.15 %, ratchet dời stop SÀN lên entry + 0.08 % (113.56), nhưng
mức thoát maker mềm BE chỉ ở entry + 0.06 % (113.54) — **nằm dưới stop sàn**. Giá quay đầu chạm stop taker trước,
MARIN không kịp đặt lệnh maker. Đây là xung đột thứ tự giữa ratchet (vòng r/w) và tier mềm, không phải thiếu cơ chế
maker (các lệnh #45–#48 đều thoát maker).

Số liệu form: "+0.117 U nếu thoát maker". Thực tế: maker ở đúng 113.55 → +0.0706 U (2.9×); maker ở mức pre-stop
113.61 → ≈ +0.19 U. Không có bảo đảm khớp: maker chỉ khớp nếu có người mua nâng giá trước khi giá rơi tới stop.

## 2. Replay chính sách thoát (`tools/replay_exit_policies.py`, `replay_exit_policies_2026-10-08z.json`)
aggTrades SOL/DOGE 01–07/10 (checksum Binance), cùng 2 910 vị thế (proxy TREND), dev 01–05, **holdout 06–07 đặt
trước**. Maker thoát tại touch, re-peg theo giá, không thấp hơn stop sàn, khớp khi có lệnh mua chủ động **chạm**
(touch) hoặc **xuyên** (through) giá; phí maker 2, taker 5 + 1 trượt (độ nhạy taker 4).

Holdout (n = 859), chênh lệch so với chính sách hiện tại, bps / vị thế [CI95]:
| Chính sách | Khớp xuyên (bảo thủ) | Khớp chạm | Taker 4 bps |
|---|---|---|---|
| PRE (pre-stop maker, gap 0.04 %) | **+0.25 [0.12, 0.38]** | +0.38 [0.24, 0.53] | +0.29 [0.16, 0.42] |
| TP30 (TP1 50 % ở +0.30 %) | +0.10 [0.05, 0.16] | +0.08 [0.04, 0.14] | +0.08 [0.03, 0.13] |
| PRE + TP30 | **+0.31 [0.17, 0.48]** | +0.43 [0.28, 0.60] | +0.34 [0.19, 0.49] |
Dev cùng dấu (PRE +0.31 [0.16, 0.46]). Riêng các ca giống #49 (ratchet rồi chạm stop, n = 234): hiện tại 0.0 bps →
PRE **+4.7** (xuyên; 138/234 thoát maker) / **+7.0** (chạm).

**Nhưng mọi chính sách vẫn âm tổng thể: −6.6 đến −7.2 bps / vị thế.** Tối ưu thoát lấy lại ~0.3 bps; phần lỗ nằm ở
lệnh vào (tín hiệu chưa có lợi thế sau phí). Hạn chế: không có book/queue, giá touch = print cuối, proxy tín hiệu.

## 3. Bản vá
| ID | Thay đổi | Demo / live | Rollback |
|---|---|---|---|
| Z1 | **Pre-stop maker exit**: khi stop sàn đã ở vùng lãi, tier mềm `PRESTOP` = stop + 0.04 % → giá quay đầu gặp lệnh post-only (re-peg tại touch) **trước** stop taker; stop sàn giữ nguyên làm lưới an toàn. | bật / bật | `pre_stop_maker=False` |
| Z2 | **TP1 từng phần**: 50 % maker TP1 ở +0.30 % (chỉ TREND AUTO, khi chia được size); runner giữ kế hoạch ATR + trail. `tp_plan` ghi TP ATR gốc. Cổng net RR vẫn đánh giá TP ATR (RR pha trộn 50/50 thấp hơn — ghi rõ). | bật / tắt | `tp1_scaled_pct=None` |
| Z3 | **Trần demo**: `portfolio_risk_usdt` 1.0 → **5.0 U**, trần notional conviction **$1 000** (`cs_max_notional`). Với ví 4 922 U, tier 10 % = $492 không còn bị cắt (rủi ro tại stop ≈ $2.1). Live giữ 1.0 U / 3.85 U / 0.05 U. | bật / không áp dụng | `portfolio_risk_usdt=1.0`, `cs_max_notional=None` |
| Z4 | **Cầu dao size**: 30 lệnh conviction gần nhất lỗ ròng ≥ 5 U → tạm dừng conviction sizing (về size thường) tự động, ghi `clip: breaker …`. | bật | `cs_breaker_usdt` lớn |

## 4. Kỳ vọng lợi nhuận — nói rõ
Form kỳ vọng "+$2.50 đến +$10 ròng mỗi lệnh" ở $1 000 notional = +25 đến +100 bps/lệnh. Ledger: thắng trung bình
+11.4 bps, cả ledger −3.6 bps/lệnh [−8.0, +0.7]. Ở $492 (tier 10 %) và −3.6 bps → khoảng **−$0.18 mỗi lệnh**; Z1+Z2
cộng ~+0.3 bps (≈ +$0.015). Size lớn hơn phóng đại cả lãi lẫn lỗ; cầu dao Z4 giới hạn thiệt hại demo ở ~5 U.

## 5. Kiểm thử
`python -m pytest -q` → **1285 passed** (trước 1272; +13 trong `tests/test_fee_optimization_soft_maker.py`; 2 test
spec cập nhật cho trần demo 5 U). `git diff --check` sạch. Fixture cũ cô lập tính năng mới.

## 6. Nguồn
Binance USD-M phí maker/taker xác nhận từ chính ledger demo (`fee_bps` 4.0 maker+maker, 6.0 maker+taker); Lehalle &
Mounjid 1610.00261 (lệnh maker khớp khi giá đi ngược — vì vậy maker thoát chỉ giúp khi giá còn bật lại); data.binance.vision
aggTrades (vòng x).
