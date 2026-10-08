# Vòng (w) 08/10/2026 — Super-Alpha crossing, TTL động, ratchet 2, chịu lỗi mạng: trước / sau

Form: `prompts/FORM_CLAUDE_OPUS5.5_BREAKTHROUGH_ALPHA_EXECUTION_2026-10-08.md`. Không khởi động lại engine, không gửi lệnh,
không bật live, không đổi size/đòn bẩy. Ledger gốc không bị ghi (44 dòng).

## 1. Snapshot
`snapshot_2026-10-08w/` lúc 07:56:39 UTC (14:56 Asia/Saigon), `SHA256SUMS.txt`. Engine đang chạy code `79ed9c0`
(đã có funnel/orphan của vòng v). Orders 927 dòng = 836 SHADOW_SCALP + 58 UNFILLED + 23 REFUSED + 6 PARTIAL + 3 FILLED
+ 1 ERROR. Attempt SOL/DOGE thật: 66, trong đó 54 có vote ≥ 0.95 (tất cả edge_bps ≥ 30).

## 2. Kiểm chứng giả thuyết của form trên chính các attempt thật
Công cụ: `tools/counterfactual_super_alpha.py` (aggTrades công khai của **demo-fapi** — nơi P&L demo xảy ra — và
**mainnet**; không key, không lệnh). Kết quả: `counterfactual_super_alpha_2026-10-08w.json`.

**Biên độ thực sau tín hiệu vote ≥ 0.95 (mainnet, 54 attempt, 5 phút):** MFE trung vị 8.8 bps, p90 23.2, lớn nhất
28.3 bps; **không lần nào đạt 50 bps**. Giá sau 5 phút: trung bình −0.27 bps, 52 % cùng chiều (tung đồng xu).
Lệnh 14:42:38 SOL SELL: MFE 16.5 bps, +14.7 bps sau 5 phút — một lần tốt, không phải "sóng 50–100 bps".
→ Giả định "lợi nhuận kỳ vọng +50 đến +80 bps" **không được dữ liệu ủng hộ**. edge_bps là heuristic chưa hiệu chuẩn
(dashboard ghi "EDGE PROXY UNCALIBRATED").

**Net mỗi cơ hội (bps, cùng size, bracket SL 0.35/0.50 %, TP 0.88/1.25 %, thoát sau 300 s; vote ≥ 0.95, n = 54):**
| Chính sách | Demo: khớp · net/cơ hội [CI95] | Mainnet: khớp · net/cơ hội [CI95] |
|---|---|---|
| GTX 3 s (hiện tại) | 2 · **+0.12** [0.00, 0.54] | 38 · −3.99 [−8.72, 1.06] |
| GTX 10 s | 15 · −1.21 [−3.92, 0.47] | 45 · −5.86 [−10.84, −0.48] |
| GTX 20 s | 18 · −1.52 [−4.18, 0.23] | 49 · −6.22 [−11.31, −0.87] |
| TTL động 15 s (huỷ khi ngược/rời 5 bps) | 17 · −1.43 [−4.10, 0.35] | 47 · −5.73 [−10.61, −0.46] |
| **Crossing ngay (IOC)** | 50 · **−6.80** [−12.59, −1.67] | 54 · **−12.74** [−18.32, −7.02] |
| **GTX 2 s → crossing** | 52 · **−7.08** [−12.87, −1.71] | 53 · **−9.64** [−15.69, −3.73] |

Tập con có size super (0.80 SOL / 500 DOGE ⇒ OFI ≥ 0.80), n = 17: crossing 2 s demo −3.17 [−11.54, 3.68], mainnet
−7.80 [−17.52, −1.64]. Markout sau khi vào taker âm ở 30/60/300 s. Không lần nào chạm SL/TP trong 300 s → thoát TIME.

Kết luận: **khớp chắc chắn bằng taker biến "không giao dịch" (≈ 0) thành lỗ ≈ 7–13 bps mỗi cơ hội**; TTL dài/động
cũng làm net xấu hơn (khớp chủ yếu khi giá đi ngược). Trùng với replay vòng v trên book tháng 9. Phí taker USD-M
trong cấu hình là 5 bps (+1 bps trượt), không phải 2–4 bps như form; vào taker + thoát maker = 8 bps.

Giới hạn: không có lịch sử book demo (độ sâu cho 0.80 SOL / 500 DOGE không biết → trượt thực có thể lớn hơn); OFI
tại thời điểm attempt không được log (dùng size super làm proxy); thoát của engine (FLOW/REGIME/soft/EJECT) không mô
phỏng; 66 attempt chồng thời gian; mẫu nhỏ.

## 3. Bản vá
| ID | Thay đổi | Mặc định demo / live | Rollback |
|---|---|---|---|
| W1 | **Super-Alpha crossing fallback** (`cross_check`, `cross_remaining`): sau `super_cross_after_s` = 2 s GTX chưa khớp, kiểm lại trên feature MỚI: SOL/DOGE, vote ≥ 0.95, OFI ≥ 0.85 cùng chiều, edge ≥ 30 bps, VPIN không độc, touch demo còn mới, RR sau phí taker ≥ 1.50 → huỷ GTX, đọc khối lượng đã khớp, **chỉ IOC phần còn lại** với LIMIT trần touch ± 3 bps (không bao giờ MARKET). Trung bình giá theo khối lượng, phí vào hỗn hợp, `entry_type` GTX+IOC / IOC, order log `crossed` / `cross_check`. MR/SCALP không bao giờ crossing. | **shadow** (ghi `SHADOW_CROSS` với giá taker dự kiến, không gửi lệnh) / off | `super_cross='off'`; bật thật: `super_cross='on'` |
| W2 | **TTL động**: quá TTL 3 s, gia hạn từng 1 s (tối đa 15 s kể từ lúc đặt) khi score ≥ 0.90, OFI ≥ 0.50 cùng chiều, touch chưa rời > 0.05 %; huỷ ngay khi mid đi ngược ≥ 3 bps hoặc OFI tắt. Vote không chạy lại được trong vòng lệnh (engine giữ lock) → dùng score của quyết định. | **shadow** (ghi `SHADOW_DYN`, vẫn huỷ ở 3 s) / off | `dyn_ttl='off'` / `'on'` |
| W3 | **Ratchet tầng 2**: MFE ≥ +0.25 % → stop SÀN tới entry ± (phí vào của chính lệnh + 5 taker + 1 trượt + 0.05 %) = 0.13 % (vào maker) / 0.17 % (vào IOC). Tầng 1 (MFE 0.15 %) nay fee-true: 0.08 % maker / 0.12 % IOC (trước cố định 0.08 % → lỗ phí nếu vào taker). | bật / tắt | `ratchet2=False` |
| W4 | **Chịu lỗi mạng** (BinanceVenue): −1008 = thất bại chắc chắn → gửi lại cùng clientOrderId sau 0.2/0.4/0.8 s (tối đa 3); kết quả không rõ (timeout/transport/5xx khác) → tra clientOrderId trước, chỉ gửi lại cùng id khi sàn báo không tồn tại và là GTX; **IOC không bao giờ gửi lại khi không rõ** (IOC đã khớp không còn "open", gửi lại có thể khớp 2 lần). Cancel/query retry 5xx. Orphan có cancel thất bại → `self.orphans`, thử lại mỗi `sync()`, tối đa 20 lần. | bật | revert các override `_post/cancel/order/ioc` |

Lệnh 98 % không còn "bị huỷ mù": mỗi attempt ghi rõ `cross_check` (OK hoặc lý do), `SHADOW_CROSS` lưu giá taker dự kiến,
`SHADOW_DYN` lưu lần TTL động sẽ gia hạn — đủ để đối chiếu với aggTrades demo sau 1–2 ngày bằng chính công cụ W.

Về yêu cầu "100 % lệnh đóng đều có lãi ròng dương": **không thể đảm bảo**. Ratchet 2 chỉ áp cho lệnh đã đạt +0.25 %
(4/54 attempt có MFE ≥ 25 bps trong 5 phút); stop-market có thể trượt/gap; lệnh không bao giờ đạt +0.25 % không bị
ảnh hưởng. Mức +0.05 % trên giá vào của form **thấp hơn phí** (vào maker + stop taker = 8 bps) nên đã đổi thành
phí thật + 0.05 %. Engine đã có soft PROFIT LOCK ở +0.25 % → +0.15 % (vòng trước).

## 4. Vì sao mặc định là shadow cho W1/W2
Bật crossing trên demo với các tham số form: theo §2 kỳ vọng âm có CI loại 0 trên demo và mainnet → số dư sẽ giảm,
trái mục tiêu "sinh lời ròng sau phí". Shadow thu được giá taker dự kiến của **chính** tín hiệu và cổng mới (OFI
≥ 0.85, RR ≥ 1.5) — điều counterfactual hiện tại chưa đo được. Bật/tắt là một tham số profile; live luôn off.

## 5. Nguồn
- Binance USD-M General Info (sửa 07/10/2026): 503 "Unknown error…" = trạng thái KHÔNG RÕ (kiểm tra trước khi gửi lại);
  503 "Service Unavailable" và −1008 = thất bại 100 %, retry backoff 200→400→800 ms, tối đa 3–5; reduce-only /
  close-position / cancel được miễn −1008. Error codes: −1007 timeout "execution status unknown".
- Binance New Order: `newClientOrderId` duy nhất **giữa các lệnh đang mở** → gửi lại cùng id chỉ an toàn cho lệnh còn mở.
- "Equilibrium Fast Trading" là của **Biais, Foucault & Moinas** (SSRN 2024360; JFE), không phải Salanié; nội dung:
  đầu tư tốc độ tạo chọn ngược và đầu tư quá mức — không phải quy tắc "alpha/phí → vào taker".
- Cont & Kukanov 1210.1625: chia limit/market tối ưu khi biết xác suất khớp và chi phí — ở đây đo trực tiếp bằng counterfactual.
- Lehalle & Mounjid 1610.00261: lệnh limit khớp nhiều hơn khi giá sắp đi ngược → TTL dài kém (khớp với §2).

## 6. Kiểm thử
`python -m pytest -q` → **1222 passed** (trước 1188; +34 trong `tests/test_breakthrough_alpha_execution.py`).
`git diff --check` sạch. Fixture cũ cô lập tính năng mới (`super_cross='off'`, `dyn_ttl='off'`, `ratchet2=False`).

## 7. Rollback
`super_cross='off'`, `dyn_ttl='off'`, `ratchet2=False` trong `PROFILES['demo']`; W4 revert các method BinanceVenue.

## 8. Việc tiếp theo (dữ liệu thụ động)
1. Sau khi người dùng khởi động lại: thu `SHADOW_CROSS` / `SHADOW_DYN` ≥ 30 dòng, chạy lại
   `tools/counterfactual_super_alpha.py` (thêm cột giá dự kiến) → chỉ đề xuất `super_cross='on'` khi net/cơ hội trên
   demo VÀ mainnet có CI95 > 0.
2. Log OFI tại thời điểm attempt (đã có trong `SHADOW_CROSS`) để kiểm cổng OFI ≥ 0.85 riêng.
3. Lõi vấn đề là tín hiệu: vote 98 % chưa dự báo được biến động 5 phút (52 % cùng chiều). Hiệu chuẩn score/OFI →
   markout trên holdout trước khi tìm thêm cách khớp lệnh.
