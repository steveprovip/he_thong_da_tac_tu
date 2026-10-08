# Vòng (aa) 08/10/2026: vòng nghiên cứu nhanh và chuyển khung thời gian

Phạm vi: chỉ phân tích và công cụ offline. Không khởi động lại engine, không gửi lệnh, không đổi Profile, không bật chiến lược mới, không ghi vào ledger.

Báo cáo nghiên cứu sâu (có trích nguồn) nằm ở `docs/research_upgrade/research_alpha_after_fees_2026-10-08.md`.

## 1. Hai vấn đề người dùng nêu

1. **Mất nhiều thời gian mới biết một bản sửa tốt hay xấu.**
2. **Lãi, nếu có, rất thấp.** Ảnh chụp màn hình cho thấy WR 36.4 %, PF 0.46, Kelly f\* −0.25, gross +0.25 U và phí −0.90 U.

## 2. Vì sao vòng phản hồi chậm (quan sát từ ledger)

`python tools/edge_check.py --by code` trên 55 dòng ledger cho kết quả:

- Có **9 phiên bản code khác nhau trong 55 lệnh**, tức trung bình khoảng 6 lệnh mỗi phiên bản. 34 lệnh chưa có hash code.
- Độ lệch chuẩn mỗi lệnh khoảng 14.6 bps (SOL 19.7, BTC 8.9).
- Để phân biệt một edge −3 bps với +2 bps cần khoảng **93 lệnh** nếu cỡ mẫu cố định (power 80 %), hoặc trung bình khoảng **45 lệnh** nếu dùng SPRT.
- Không phiên bản nào chạy đủ số lệnh đó. Vì vậy "sửa xong chạy demo vài giờ" **không thể** trả lời được bản sửa tốt hay xấu, dù chờ lâu đến đâu, nếu code cứ đổi trước khi đủ mẫu.

Kết quả của toàn bộ ledger:

- Trung bình −3.48 bps/lệnh, se 2.0.
- SPRT (H0 −3 so với H1 +2) cho kết luận **BAD**: llr −3.42 vượt ngưỡng −2.94. Nghĩa là dữ liệu ủng hộ "≈ −3" hơn hẳn "≈ +2".
- mSPRT so với 0 cho p = 0.29, tức **chưa đủ** để khẳng định edge < 0 với độ tin cậy "nhìn liên tục".
- Theo symbol:
  - BTC: n = 28, −3.78 bps, SPRT BAD.
  - SOL: n = 24, −2.34 bps, chưa quyết.
  - DOGE: n = 3.

## 3. Vì sao lãi thấp (nghiên cứu, đã kiểm nguồn)

- **Biến động nhỏ hơn phí.** Trên mainnet, BTC biến động trung bình |Δ| khoảng 1.9 bps trong 10 giây và 5.4 bps trong 1 phút; SOL khoảng 3.9 và 10.9 bps. Phí khứ hồi là 4 bps (maker-maker) đến 10 bps (taker-taker).
- **Edge OFI công bố tốt nhất** chỉ khoảng +1 bp gross mỗi round-trip, đo ở độ trễ 2 ms từ AWS Tokyo. Bot này có độ trễ khoảng 300–700 ms.
- **Lệnh maker bị chọn bất lợi** khoảng −0.8 bp sau 5 giây trên BTC perp (233 nghìn lệnh live).
- **Market making ở VIP0** quy đổi ra khoảng −1.1 đến −2.1 bp mỗi lệnh. Chiến lược này chỉ có lãi khi được rebate.
- **Kết luận (có căn cứ, chưa phải định lý):** mức −3.6 bps/lệnh là hệ quả cấu trúc của khung giây cộng phí VIP0. Không gate, exit hay size nào sửa được kỳ vọng âm, và điều này khớp với các vòng v → z2, nơi mọi biến thể replay đều nằm trong khoảng −6.4 đến −7.3 bps.
- **Chỉ trend/momentum 4h–daily** có bằng chứng công bố là còn dương sau phí, ví dụ ensemble breakout daily trên top-20 coin có Sharpe net > 1.5 khi phí 10–50 bps.

## 4. Công cụ mới

| File | Việc | Thời gian chạy |
|---|---|---|
| `core/fast_eval.py` | Gồm: `required_n`/`required_pairs`, SPRT (Wald), mSPRT (anytime-valid), `paired_diff` (bootstrap theo khối), PSR/DSR, `ledger_bps`, `verdict`. Chỉ dùng stdlib vì máy không có scipy. | tức thì |
| `tools/edge_check.py` | Đọc ledger (chỉ đọc), nhóm theo `--by code\|symbol\|policy\|day`, cho kết luận GOOD/BAD/UNDECIDED và số lệnh còn cần. Có `--paired a.json b.json` cho A/B ghép cặp. | < 1 s |
| `tools/factor_scan.py` | Quét 96 cấu hình gồm TSMOM, Donchian, EMA-cross, ensemble, ensemble vol-target và ensemble lọc funding; mỗi loại chạy LS và LO, ở các khung 1h, 4h, 1d. Dữ liệu là klines 1h và funding của BTC/SOL/DOGE từ 2022-01 đến 2026-09 trên data.binance.vision. Có split cố định, walk-forward và DSR. | 20 s |
| `tools/record_market.py` | Ghi dữ liệu mainnet: `depth@100ms`, `bookTicker` (route /public), `aggTrade` và `forceOrder` (route /market). Có REST snapshot mỗi 10 phút, mỗi file gz là một giờ. Không dùng key, không gửi lệnh. | chạy nền |
| `tests/test_fast_research_loop.py` | 48 test | 4 s |

Smoke test recorder (20 s, máy người dùng):

- SOL có 5 227 bookTicker, 189 depth, 81 aggTrade và 1 snapshot.
- Dung lượng khoảng 20 MB/giờ cho SOL khi ghi đủ; `--light` bỏ bookTicker và giảm khoảng 10 lần.
- Route gốc `/stream` không trả `aggTrade` (đã thấy ở lần thử đầu). Đã sửa sang `/public` và `/market`, giống `core/micro_signals.py`.

## 5. Kết quả factor scan (dữ liệu thật, khoá trước khi chạy)

**Thiết lập:**
- Split: DEV 2022–2024, VAL 2025, HOLDOUT 2026-01 → 09.
- Danh mục gồm BTC/SOL/DOGE, mỗi đồng 1/3 notional.
- Chi phí TAKER là 5 bps + 1 bp trượt mỗi phía. MAKER là 2 bps, một giả định lạc quan vì coi mọi lệnh maker đều khớp.
- Có tính funding thật.

| Cấu hình | Ghi chú | OOS 2025-01 → 2026-09 (TAKER) | PSR>0 |
|---|---|---|---|
| Mua và giữ cả 3 đồng | benchmark | SR −0.19, −12.1 %/năm | — |
| **ENS 1d LS** | đăng ký trước theo literature | SR +0.21, +9.4 %/năm, DD 40 %, 38 RT/năm, **+24.6 bps/RT net** | 0.61 |
| ENS_VT 1d LO | hạng 2 ở DEV | SR +0.42, +8.5 %/năm, DD 23 %, 16 RT/năm, +52.5 bps/RT | 0.72 |
| ENS_VT 1d LS | — | SR +0.45, +15.5 %/năm, DD 28 % | 0.72 |
| ENS 4h LS | — | SR −0.35, −7.3 bps/RT | 0.33 |
| ENS 1h LS | — | SR −0.95, −9.9 bps/RT, 428 RT/năm | 0.11 |
| TSMOM 24 1h LS | — | SR −1.59, −11.5 bps/RT | 0.02 |
| Walk-forward (chọn lại mỗi quý, 24 tháng train) | 2024-01 → 2026-09 | SR +0.78, +30.9 %/năm, **DD 64 %** | 0.91 |

**Đọc kết quả một cách trung thực:**

1. **Khung 1h và 4h** thua phí ở OOS:
   - Median SR ở VAL: 1h −0.56 (chỉ 7 % cấu hình dương), 4h −0.41.
   - Ở HOLDOUT tốt hơn (4h +0.36), nhưng không ổn định giữa các năm.
2. **Khung 1d** là khung duy nhất mà cả cấu hình đăng ký trước lẫn các ensemble vol-target **dương ở cả VAL lẫn HOLDOUT** và thắng buy-and-hold. Lãi net mỗi round-trip là +25 đến +60 bps, gấp khoảng 4–10 lần chi phí.
3. **Chưa đủ mức có ý nghĩa thống kê.**
   - PSR chỉ 0.61–0.76 trên 638 ngày OOS. DSR ở DEV ≤ 0.47 sau 96 cấu hình.
   - Walk-forward đạt PSR 0.91 nhưng DD 64 % vì chọn Donchian LO trong 2024–25.
   - Đây là **giả thuyết có triển vọng (hypothesis)**, chưa phải đã xác nhận (validated).
4. **Bộ lọc funding** (trung bình 3 ngày > 0.03 %/8h) **không lần nào kích hoạt** trong OOS, nên ENS_FF trùng hệt ENS. Funding 2025–26 quá thấp để làm bộ lọc ở ngưỡng này.
5. **Hạn chế của kết quả:**
   - Chỉ có 3 đồng, được chọn sau khi đã biết chúng sống sót (survivorship).
   - Chỉ một lộ trình thị trường 2022–2026.
   - Lệnh daily thực tế vào sau close vài phút, nhưng mô hình giả định khớp đúng giá close.
   - Notional tối thiểu của Binance là 5 U (BTCUSDT 100 U). Trần live 3.85 U **không đủ** để mở lệnh, nên chiến lược này chỉ chạy được trên demo hoặc paper.

## 6. Quy trình mới: biết kết quả trong vài phút thay vì vài ngày

1. **Trước khi sửa:** chạy `python tools/edge_check.py --by code` và ghi lại trạng thái.
2. **Đánh giá offline bằng replay ghép cặp.** Chạy A và B trên **cùng** sự kiện rồi `edge_check.py --paired`. Với ρ ≈ 0.9 chỉ cần khoảng 20–26 cặp thay vì khoảng 250 lệnh mỗi nhánh.
   - Việc tiếp theo là cho các tool replay hiện có xuất danh sách bps của từng vị thế. Việc này **chưa làm**.
3. **Đăng ký trước khi chạy demo:**
   - Khai báo H0 −3 / H1 +2 bps.
   - **Không đổi code** cho tới khi SPRT ra quyết định, trung bình khoảng 45 lệnh với σ 15.
   - Mỗi hash code là một nhóm riêng.
4. **Ghi dữ liệu liên tục:** chạy `python tools/record_market.py --light` để có L2 và aggTrades của chính những ngày demo, phục vụ replay khớp lệnh maker sau này.
5. **Ý tưởng mới:** đưa vào `factor_scan.py` với split cố định, đếm số cấu hình đã thử (DSR) và giữ nguyên HOLDOUT.

## 7. Đề xuất ưu tiên (chưa bật gì)

| P | Việc | Lý do |
|---|---|---|
| P0 | Ngừng tinh chỉnh tham số khung giây. Nếu vẫn chạy demo thì đóng băng code trong khoảng 45–90 lệnh và để SPRT kết luận. | Tham số không đổi được kỳ vọng âm; 9 phiên bản trong 55 lệnh tạo ra nhiễu thay vì bằng chứng. |
| P1 | Một sleeve trend 1d chạy **paper/shadow**, gồm ENS 1d LS (đăng ký trước) và ENS_VT 1d LO, vào lệnh maker sau close UTC. | Đây là nguồn duy nhất dương ở OOS. Nhưng nó cần khoảng 6–12 tháng paper mới đủ bằng chứng, nên phải theo dõi bằng mSPRT. |
| P2 | Dùng tín hiệu khung giây như **bộ lọc thời điểm vào lệnh** cho sleeve 1d, không dùng làm tín hiệu vào. | Literature: OFI có ích để tối ưu khớp lệnh, không để chọn hướng. |
| P3 | Chạy recorder liên tục, dùng cho replay maker có queue model (hftbacktest/nautilus) ở vòng sau. | data.binance.vision không có L2 diff, và bookTicker dừng từ 2024-03-30. |

## 8. Kiểm chứng

- `python -m pytest -q` cho **1360 passed** (trên máy người dùng, 85 s).
- Recorder smoke test 20 s như mục 4.
- `edge_check` chạy trên ledger thật; tệp ledger không bị thay đổi, đã có test xác nhận byte-for-byte trên ledger tạm.
- Kết quả scan lưu ở `docs/research_upgrade/factor_scan_2026-10-08.json`.
