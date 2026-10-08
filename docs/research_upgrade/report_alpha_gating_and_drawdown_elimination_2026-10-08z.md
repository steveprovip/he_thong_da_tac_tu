# Vòng (z2) 08/10/2026 — Alpha gating, CVD shield, sizing theo chế độ: nghiệm thu

Yêu cầu: gói "ALPHA GATING, DIRECTIONAL CVD SHIELD & ADAPTIVE SIZING HARMONIZATION". Không khởi động lại engine,
không gửi lệnh, không đổi live ngoài các mặc định an toàn. Ledger gốc không bị ghi (55 dòng).

## 1. Khám nghiệm đợt sụt (snapshot `snapshot_2026-10-08z2/`, SHA256SUMS)
Ledger #49–#55 (sau khi bật conviction sizing): **gross +0.1721, phí 0.7267, net −0.5546 U** (−3.4 bps/notional).
Phí > lãi gộp: 7 lệnh notional $1 627 × ~4.5 bps. Số liệu dashboard của người dùng (gross +0.2527 / phí −0.8973 /
net −0.6446) gồm khoảng thời gian khác của phiên; cùng kết luận.

**CVD mainnet tại thời điểm vào lệnh** (aggTrades công khai, `ledger_entry_cvd_2026-10-08z2.json`):
| # | Chiều | Net U | CVD 15 s / 60 s / 3 m (k USD) | Shield | 3 tầng CVD |
|---|---|---|---|---|---|
| 50 | SELL | +0.0018 | −199 / −164 / **+523** | chặn | không |
| 51 | SELL | −0.2053 | −1 577 / −2 690 / −2 099 | cho qua | cùng pha |
| 52 | SELL | +0.1479 | −436 / −633 / −671 | cho qua | cùng pha |
| 53 | SELL | −0.2290 | −6 948 / −7 077 / −7 586 | cho qua | cùng pha |
| 54 | SELL | −0.0099 | −1 199 / **+416** / −5 916 | chặn | không |
| 55 | BUY | −0.2843 | +737 / +1 036 / +1 331 | cho qua | cùng pha |
**Ba lệnh thua lớn (#51, #53, #55) đều ĐÚNG chiều dòng tiền vĩ mô** — không phải "bắt dao rơi". Chúng vào sau khi
dòng tiền đã chạy (CVD −7 M ở #53) và trên demo có basis lớn (#51 +19 → +37 bps, #55 −26 bps so với mainnet).
Shield chỉ chặn #50 và #54 (tổng +0.008 U ⇒ không cứu được đợt sụt).

Hai lỗi thật tìm thấy trong ledger:
- **#55 "SOFT-BE" nhưng giá ra 111.90 < giá vào 111.92** (gross −0.0878): nhãn BE trên lệnh lỗ thô.
- **#51 "SOFT-PRESTOP" mua lại ở 113.26 trong khi stop sàn (ratchet) ở 113.08**: book demo nhảy (touch 113.26, mark
  113.10) và lệnh maker thoát được đặt tệ hơn chính stop sàn.

## 2. Replay (`tools/replay_alpha_gating.py`, `replay_alpha_gating_2026-10-08z2.json`)
aggTrades SOL/DOGE 01–07/10, holdout 06–07 (đặt trước), lối thoát = MARIN hiện tại (pre-stop + TP1 0.30 %).
| Biến thể | Dev bps/vị thế | Holdout bps/vị thế [CI95] |
|---|---|---|
| BASE (proxy hiện tại) | −6.60 | −6.56 [−7.36, −5.65] |
| SHIELD (CVD 60 s ±300 k / 3 m ±500 k) | −6.82 | −6.42 [−7.34, −5.50] |
| ALIGN3 (CVD 3 m, 60 s, 15 s cùng chiều) | **−7.16** | **−7.27** [−8.04, −6.48] |
| EARLY (cắt sớm ≤ 45 s khi CVD ngược ≥ 50 k/s) | −6.50 | −7.07 [−7.88, −6.20] |
| ALIGN3 + EARLY | −6.86 | −6.99 |
SHIELD trung tính (không phân biệt được); ALIGN3 **xấu hơn ở cả hai tập** (chờ đủ 3 tầng = vào muộn, lúc dòng
tiền sắp cạn); EARLY không nhất quán. OBI (tầng 3) không có trong aggTrades → chưa kiểm được. Không biến thể nào
đưa net/vị thế về dương: vấn đề là lợi thế của tín hiệu vào, không phải bộ lọc chiều.

## 3. Bản vá
| ID | Yêu cầu | Triển khai | Mặc định demo / live |
|---|---|---|---|
| G1 | Nhiệm vụ 1.1 | `cvd_shield`: cấm BUY khi CVD 60 s < −300 k hoặc CVD 3 m < −500 k USD (SELL đối xứng), mọi lane; blocker `CVD_SHIELD` trong funnel. STRONG DOWN/UPTREND đã bị swarm `REGIME BAN` chặn sẵn. | **on** / on (vô hại) |
| G2 | Nhiệm vụ 1.2 | `align3`: CVD 3 m + 60 s (vĩ mô), CVD 15 s + OFI L10 (vi mô), OBI (book) cùng chiều; ghi `align3` lên vị thế/ledger. Engine đã có `concordance_gate` (CVD 15 s + 60 s + OFI) bật sẵn. | **shadow** (replay xấu hơn) / off |
| G3 | Nhiệm vụ 2.1 | Trần size theo chế độ trong conviction sizing: chop (KER < 0.30 hoặc dòng tiền không đồng pha) **$170** (~1.5 SOL), giữa $225, trend (KER ≥ 0.40 & đồng pha) **$315** (~2.8 SOL); **trần cứng $350** (`cs_max_notional`, trước $1 000). | bật / live không có conviction sizing |
| G4 | Nhiệm vụ 2.2 | TP1 50 % = **+0.35 %** khi notional ≥ $225 (≥ ~2 SOL), +0.30 % dưới đó. | bật / off |
| G5 | Nhiệm vụ 3.1 | `early_invalidation`: trong 45 s đầu, CVD vel 10 s ≥ 50 k USD/s ngược hoặc OFI ≤ −0.30 ngược → maker exit tại touch, bỏ qua fee hurdle; stop sàn giữ nguyên. Ghi `early_invalid` lên ledger. | **shadow** (replay không nhất quán) / off |
| G6 | Nhiệm vụ 3.2 | (a) Lệnh thoát maker **không bao giờ đặt tệ hơn stop sàn** (sửa #51). (b) SOFT-BE / SOFT-LOCK / SOFT-PRESTOP / SOFT-PROFIT / EJECT-BE có **gross < 0** → nhãn `…-LOSS` (sửa #55). BE thật đã là entry × (1 + phí vào + maker ra + 0.02 %). | bật / bật |

Không làm được như yêu cầu nguyên văn:
- "Cắt lỗ êm ≤ −0.05 %": lệnh maker không đảm bảo giá; nếu giá chạy nhanh hơn, stop sàn là chốt chặn.
- "Phí không vượt 0.05 U" ở 1.0–1.5 SOL: $170 × 4 bps (maker 2 chiều) = **0.068 U**; muốn ≤ 0.05 U cần ≤ $125.
- "Luôn ≥ +$0.50 – 1.20 ròng mỗi lệnh thắng": chỉ đúng khi chạm TP1 (+0.35 % × $225–315 − 4 bps = $0.70–1.0); phần
  lớn lệnh thắng thoát sớm hơn qua tier mềm.

## 4. Kiểm thử
`python -m pytest -q` → **1313 passed** (trước 1285; +28 trong `tests/test_alpha_gating_drawdown.py`; 6 test spec cũ
cập nhật cho nhãn `-LOSS`, trần $350 và trần theo chế độ). `git diff --check` sạch. Fixture cũ cô lập tính năng mới.

## 5. Rollback
`cvd_shield='off'`, `align3='off'`, `early_invalidation='off'`, `cs_max_notional=1000`, `cs_*_notional` lớn,
`tp1_scaled_big_pct=0.30`; G6 revert 2 đoạn trong `work_soft_exit` / `_settle`.

## 6. Kết luận
Đợt sụt đến từ phí + tín hiệu vào muộn trên demo có basis lớn, không phải từ việc ngược dòng tiền vĩ mô. Gói này
giảm kích thước rủi ro (trần $350 thay $1 000, $170 khi chop), sửa 2 lỗi thoát lệnh có thật, thêm shield theo yêu cầu
(trung tính); các bộ lọc chưa có bằng chứng (ALIGN3, EARLY) chạy shadow để thu dữ liệu demo. Chưa có thay đổi nào
được chứng minh đưa net EV về dương.
