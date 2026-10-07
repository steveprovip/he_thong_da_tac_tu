# CHẨN ĐOÁN & TỐI ƯU VÒNG LẶP ĐÀO DATA — 2026-09-24

Tái lập: `python phan_tich/nghen_edge.py` · `python phan_tich/markout_l2.py data/l2/*/2026090[89].jsonl data/l2/*/20260910.jsonl --toxicity 0.7` · `python phan_tich/nghiem_thu_vong_lap.py` (ghi `artifacts/nghiem_thu_vong_lap.json`).
Chi phí all-in: maker 2 + taker 5 + spread/slippage 1.5 = **8.5 bps** (`core/early_stop.COST_ALL_IN_BPS`). Ngưỡng gross OOS: **9 bps**.

## 1. Kết luận một dòng

Không có biến thể nào đã đo vượt 9 bps gross OOS. Vòng lặp tồn tại vì **không có luật dừng**: mọi collector chạy đến Ctrl+C, nên mỗi kết quả âm lại được đọc thành "cần thêm data". Sửa lỗi chính là dừng đúng lúc và đứng ngoài thị trường. Không có bộ lọc nào nới ra mà có lãi.

## 2. Bản đồ điểm nghẽn (file:dòng tại HEAD 116a07b)

| # | Vị trí | Cơ chế | Hệ quả |
|---|---|---|---|
| L1 | `research/l2_recorder.py:84` `--seconds 39600` + `run_launcher.py:17` `L2_DEFAULT_HOURS = 11` | Ghi 11 giờ, không trần byte, không kiểm tra ý nghĩa thống kê | 3.04 GB `data/l2` trong 6.6 giờ đồng hồ, 4 symbol |
| L2 | `research/flush_shadow.py:970` `while True` với `seconds=None` | Chỉ thoát khi kill 5 cent; có LCB95 nhưng không dùng để dừng | Shadow chạy vô hạn |
| L3 | `core/runtime.py:829` + `core/signal_lab.py` | Lab mở probe ở mọi chu kỳ, `--seconds 0` = đến Ctrl+C, không kiểm định tuần tự | 6795 probe / 55.9 h, trong khi câu trả lời đã có sau 200 probe |
| L4 | `core/runtime.py:633` | Probe được mở cả khi book **stale** (`fresh=False`) | Giá entry không ai giao dịch được; RUNE veto do feed đứt bị đếm như ý kiến rủi ro |
| L5 | `phan_tich/carry_scan.py:135` `--loop` | 15 phút × 7 ngày, không dừng khi không có ứng viên | Polling không đổi được kết luận |
| E1 | `core/groktopus_swarm.py:44-60` | Edge = ATR × directional, **không phải edge đã hiện thực**; cost 7 + spread/2 + 1 | 75 probe qua cổng: gross **−2.17 bps** (tệ hơn nhóm bị chặn: +1.00) |
| E2 | `core/runtime.py:660` `maker_quote(..., inventory=0.0)` | A-S neo theo mid với q=0 nên luôn trả về best bid/ask (27/27 trường hợp, nghen_edge MỤC 6) | Maker luôn đứng ở touch, là mức giá bị thị trường chạy một chiều quét qua |
| E3 | `core/groktopus_swarm.py:70` `RUNE = risk_ok and liquid` | RUNE lặp lại đúng điều kiện của ZEPHR, và `Decision.reason` luôn là `'RUNE VETO'` | Telemetry không phân biệt được cổng phí với vi phạm rủi ro. Kết quả thực thi thì không đổi (ZEPHR vẫn veto) |

**RUNE VETO không phải thủ phạm.** Nhóm bị chặn có gross +1.00 bps, sau 8.5 bps thì net −7.5. Nới cổng ra chỉ làm những lệnh lỗ này thành lệnh thật. Đây là mắt xích "nới filter → lỗ" trong vòng lặp.

## 3. Bốn chỉ số thực chứng

| | Chỉ số | Giá trị | Nguồn |
|---|---|---|---|
| **P1** | Adverse selection sau fill maker (mainnet L2, fill kiểu trade-through, TTL 5s) | 1s **−0.98** · 5s **−1.12** · 30s **−1.15** bps (TRAIN 0908, n=1905) · OOS 0909-10: −0.76 / −0.83 / −1.12 (n=432) | `artifacts/markout_l2/` |
| P1' | Fill so với probe cùng thời điểm (testnet) | −1.24 bps, SE 2.31, n=39 (không có ý nghĩa thống kê) | nghen_edge MỤC 8 |
| P1'' | Gross của lệnh đã khớp so với gross probe không điều kiện | −6.6 so với +0.96 bps → chọn lọc bất lợi ≈ **−7.6 bps** | MỤC 3 + 4 |
| **P2** | Tỷ lệ khớp maker | Live testnet **7.3%** (99/1362, TTL 10s) · L2 touch 40.2% TRAIN / 28.8% OOS | MỤC 8, markout_l2 |
| **P3** | Net EV sau 8.5 bps | Live **−12.61 bps** @6 (≈ −15.1 @8.5), −6.73 USDT / 102 lệnh · Lab −7.54 · Lọc F4 OOS **−4.11** · L2 maker **−9.62** (OOS) | nghiem_thu |
| **P4** | Chi phí đào data rác | L2 3.04 GB, cần ~0.19 GB cho kết luận → **93.7% rác** · Lab 6595/6795 probe sau điểm quyết định → **97.1% rác** (55.9 h so với 1.8 h) | nghiem_thu |

P4 không có số token trong repo. Proxy dùng ở đây là byte và giờ collector chạy.

## 4. Can thiệp

| File | Việc |
|---|---|
| `core/early_stop.py` (mới) | `EarlyStopGate`: Welford + Wilson 95% + SPRT Wald (H0 μ=0 so với H1 μ=2 bps, α=β=5%). Chỉ ra phán quyết khi n ≥ 200. Có `DataBudget` (mẫu/giây/byte), `directory_bytes`, `replay`. Trạng thái: `REGIME_UNPROFITABLE` · `EDGE_CONFIRMED` · `BUDGET_EXHAUSTED` |
| `core/regime_permit.py` (mới) | Slow-Brain → Fast-Hands permit, **chỉ chặn**: verdict slow-brain → CARRY (G > 3C) → SCALP (spread 2-8, ATR 6-20, gross OOS ≥ 9 và LCB95 > 8.5, gate không phải UNPROFITABLE). Thiếu bằng chứng thì `STAND_ASIDE` |
| `core/groktopus_swarm.py` | Thêm tham số `permit=`: bị từ chối thì RUNE veto với lý do `SLOW_BRAIN ...`. `None` giữ nguyên hành vi cũ |
| `core/consensus_engine.py` | `Decision.blockers` + lý do `RUNE VETO [ZEPHR,RUNE]`. Chỉ thêm quy trách nhiệm; 76.8% / 70.0% / trọng số không đổi |
| `core/barrier_planner.py` | `microstructure_quote`: reservation = microprice − qγσ²T, đứng ngoài khi queue imbalance ngược phía > toxicity; `queue_imbalance`, `top_of_book` |
| `core/signal_lab.py` | `gate=`: gate có phán quyết cuối thì lab ngừng mở probe; `summary()['gate']` |
| `core/runtime.py` | Gate + budget (`--lab-budget 2000`), phiên tự kết thúc khi `EARLY STOP`, permit bật mặc định (`--no-permit` để tắt), bỏ probe khi book stale, `--quote-model touch|micro`, `--quote-toxicity` |
| `research/l2_recorder.py` | Mặc định 3600s; ngân sách toàn cục `--max-mb 500` tính cả phiên cũ. Hết ngân sách → exit 3 `DATA_BUDGET_EXHAUSTED`. Vượt ngân sách phải dùng `--override-budget` |
| `research/flush_shadow.py` | `run_shadow_loop(gate=)`: dừng theo SPRT/Wilson trên net bps của lệnh đóng; ngân sách 12 giờ khi không có `--seconds` |
| `phan_tich/carry_scan.py` | Dừng khi vô vọng: 96 lượt rỗng liên tiếp (Wilson UCB 3.85%/lượt) → dừng |
| `phan_tich/slow_brain_contract.py` | `verdict_from_rating()`: rating TradingAgents v0.4.0 → verdict v2. Hold → CHOP, REVIEW → veto. `TradingAgents-main/` giữ sạch như upstream |
| `run_launcher.py` | `L2_DEFAULT_HOURS` 11 → 1 |
| `phan_tich/markout_l2.py`, `phan_tich/nghiem_thu_vong_lap.py` (mới) | Đo P1/P2 trên L2 và bảng nghiệm thu trước/sau |
| `tests/test_early_stop_and_permit.py` (mới) | 33 test · toàn bộ suite **576 passed, 0 failed** (baseline 543) |

## 5. Microprice/A-S có sửa được adverse selection không? **Không đủ.**

Ngưỡng toxicity được chọn trên TRAIN (0908, 4 symbol) rồi đóng băng cho OOS:

| | Fill% | m1s | m5s | m30s | Net @8.5 |
|---|---|---|---|---|---|
| touch TRAIN | 40.2 | −0.98 | −1.12 | −1.15 | −9.65 |
| micro 0.7 TRAIN | 31.9 | −0.98 | −1.16 | −1.22 | −9.72 |
| touch OOS | 28.8 | −0.76 | −0.83 | −1.12 | −9.62 |
| micro 0.7 OOS | 20.3 | −0.80 | −0.69 | −0.73 | −9.23 |

Trên OOS, micro cải thiện markout 30s khoảng 0.4 bps nhưng trên TRAIN thì kém hơn. Kết quả không ổn định và vẫn cách ngưỡng 8.5 bps khoảng 9 bps. Với spread 1 tick (≈1 bps trên SOL/BNB/LINK, 0.01 bps trên BTC) thì không có spread để thu. Vì vậy runtime giữ mặc định `--quote-model touch`, còn `micro` là tuỳ chọn. Thứ thực sự chặn được việc bị bào mòn là **permit**: không có bằng chứng ≥ 9 bps thì không đặt lệnh.

## 6. Nghiệm thu trước và sau

| | TRƯỚC | SAU |
|---|---|---|
| Probe lab | 6795 (55.9 h) | **200** (1.8 h) → `REGIME_UNPROFITABLE` (UCB95 −8.12 bps) · **−97.1%** |
| L2 | 3.04 GB, recorder 11 h | Từ chối ghi (vượt 500 MB); cần ~0.19 GB cho kết luận · **−93.7%** |
| Shadow / carry scan | `while True` / 7 ngày | Gate 200 lệnh + trần 12 h / dừng sau 96 lượt rỗng |
| Maker fill an toàn | 7.3% live, net −12.61 bps | Permit `STAND_ASIDE` → 0 lệnh scalp |
| Net EV sau 8.5 bps | Live −6.73 USDT; lọc F4 −4.11 bps; qua cổng −10.67 bps | **0** (không giao dịch). Nếu gate min=30 chạy trên chuỗi live: dừng ở lệnh 30, lỗ −3.08 thay vì −6.73 USDT (**giảm 54%**) |

Net không chuyển sang dương vì không tìm được edge dương để khai thác. Thay đổi làm được là ngừng lỗ vì phí. Nguồn lợi nhuận còn lại khả thi là funding carry (`ROLE.md`, `core/carry_runtime.py`), và permit đã cho phép khi G > 3C.

## 7. Điều kiện để mở lại việc thu data

Chỉ mở khi có **giả thuyết mới đã viết ra**, trong đó dự đoán gross ≥ 9 bps kèm cơ chế. Ghi tối đa 200 tín hiệu và để `EarlyStopGate` quyết định. Nếu kết quả là `REGIME_UNPROFITABLE` thì đóng giả thuyết lại, không lặp lại vòng thu data trên cùng giả thuyết đó.
