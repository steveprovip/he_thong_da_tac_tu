# Báo cáo nghiệm thu (cc) 09/10/2026: tự học trực tuyến, Dreaming, nạp nóng, điều phối vốn

**Phạm vi.** Code và test trong repo, cộng replay offline.
- Không khởi động lại engine, không gửi lệnh, không bật live.
- Không ghi vào `data/engine_trades.jsonl` (có test kiểm tra từng byte).
- Mọi cơ chế tự trị chỉ chạy khi bạn tự khởi động lại `run.bat demo`.
- Profile live: `autonomy='off'`.

## 0. Tóm tắt trung thực

- **Có hệ tự vận hành.** Engine tự đổi trọng số agent, tự chọn tham số, tự khám nghiệm và tự siết rủi ro, nạp vào mà không cần restart, không cần hỏi Claude.
- **Tự học không tạo ra edge.**
  - Replay liên tục cả tuần 01–07/10 trên băng mainnet, bản cuối: engine vẫn lỗ khoảng −5.3 bps/lệnh. Chất lượng mỗi lệnh y như khi tắt autonomy.
  - Net tốt hơn +1.63 U/tuần (p 0.006). Lý do duy nhất là vào ít hơn 21 lệnh khi đang thua (mục 5). Sau khi phạt cho 69 lần thử, DSR = 0.68, chưa đạt ngưỡng 0.95.
  - Một bandit hay một hệ trọng số không biến một tín hiệu kỳ vọng âm thành dương. Với khoảng 2 lệnh/giờ và σ ≈ 15 bps, 20 lệnh không đủ để phân biệt agent giỏi với agent may mắn.
- **Phần có giá trị thật là tự động giảm rủi ro.** Dreaming phát hiện bẫy và tự siết size hoặc chuyển sang quan sát.
  - Chạy thử (dry-run) trên ledger thật hôm nay, nó tìm ra một bài học chưa ai thấy: **tier conviction 10 % lỗ nặng nhất** (18 lệnh, −5.7 bps, −2.21 U), tệ hơn các tier nhỏ (−2.2 bps). Chỉ thị tự sinh là trần $150.
- **Đã sửa một tác dụng phụ nguy hiểm, tìm ra nhờ replay.** Thiết kế ban đầu (trọng số đi thẳng vào phiếu bầu) **làm lệnh to hơn**: 66/88 lệnh bị đẩy lên tier 10 %:
  1. Hạ trọng số LUMEN/TIDAL đẩy điểm swarm xuống dưới 76.8 %.
  2. Đường JEV fast-path liền duyệt chính setup đó với score khoảng 0.98.
  3. Conviction sizing nhìn score 0.98 và cấp tier 10 %. Kết quả: notional trung bình $205 so với $145 khi tắt.

  Bản cuối: trọng số học được **chỉ có thể phủ quyết** một lệnh, không bao giờ thêm lệnh hay tăng size.

  Engine vẫn còn một điểm lạ từ trước, **chưa sửa** (cần quyết định của bạn): lệnh đi đường JEV mang score = JEV/10, nên mặc nhiên vào tier conviction cao nhất. Ledger thật có 6 lệnh JEV, −6.8 bps.
- **Ba yêu cầu của form không làm và có lý do:**
  1. Tắt tiếng hay đổi trọng số **ZEPHR**: đây là cổng dữ liệu/spread, không phải agent dự báo. Tắt nó là cho phép vào lệnh trên sổ lệnh cũ.
  2. **Tự mở rộng đòn bẩy** cho làn trend: rủi ro chỉ được tự động giảm (CLAUDE.md, `binance-futures-algo-guard`). Làn trend vẫn là paper với DSR 0.19.
  3. **Ngưỡng OFI 0.80**: thấp hơn mức 0.85 hiện tại, tức tăng tần suất size 10 %.

## 1. Nhiệm vụ 1: tự học trực tuyến

### 1a. `core/swarm_learner.py`: trọng số agent

**Agent được học:** chỉ ba agent **định hướng** là NORO, LUMEN và TIDAL. Theo `core/micro_swarm.py`, năm agent còn lại là cổng an toàn: ZEPHR (dữ liệu/spread), RUNE (veto), OKAPI (size), MARIN (thoát lệnh), VESKA (AuthToken). Các cổng này giữ nguyên.

**Dữ liệu:** mỗi lệnh lưu phiếu của từng agent alpha lúc vào lệnh, ở trường `agents` trong ledger.

**Cách tính, cửa sổ trượt 20 lệnh:**
- Hit-rate.
- **Brier** = mean((0.5 + 0.5·conf − đúng)²).
- **Edge đóng góp** = net bps trung bình của các lệnh agent bỏ phiếu cùng chiều.
- Trọng số = clip(1 + 2.4·((hits+5)/(n+10) − 0.5), 0.2, 1.8). Mẫu nhỏ được kéo về 1.0 nhờ shrinkage.
  - 20/20 đúng cho ra khoảng 1.8.
  - 0/20 đúng cho ra khoảng 0.2.
- **Mute 30 phút** khi agent bỏ phiếu cùng chiều với 3 lệnh lỗ liên tiếp.

**Áp dụng (bản cuối):**
- Quyết định gốc (score và size) giữ nguyên.
- Engine bỏ phiếu lại với trọng số học được. Nếu lần bỏ phiếu đó **không** cho phép lệnh, lệnh bị chặn với lý do `LEARNER VETO`.
- Trọng số > 1 không có tác dụng thực thi, chỉ hiển thị.

**Giới hạn đo được trong replay một tuần:**
- LUMEN và TIDAL luôn bỏ phiếu cùng nhau, nên có thống kê y hệt nhau (20 lệnh, hit 30 %, Brier 0.65, edge −8.1 bps).
- Không thể tách công/tội giữa hai agent này. NORO không bỏ phiếu lần nào.

### 1b. `core/param_bandit.py`: Thompson sampling theo chế độ

**Các arm:**

| Tham số | Giá trị | Ghi chú |
|---|---|---|
| `cs_super_ofi` | 0.85 / 0.90 / 0.95 | OFI cho tier 10 % (đây là "ngưỡng OFI 0.80–0.95" trong engine; trigger OFI của TIDAL là `ofi_min` 0.65 và không bị đụng). 0.80 bị loại vì tăng phơi nhiễm |
| `hurdle_min_hold_s` | 30 / 60 / 90 | |
| `tp1_scaled_pct` | 0.30 / 0.40 / 0.50 % | |

**Cách hoạt động:**
- Chế độ (regime) theo KER lúc chọn: CHOP < 0.30 ≤ MID < 0.40 ≤ TREND.
- Posterior Normal cho mỗi arm, với prior N(0, 10²) và σ = 15 bps.
- Reward = net bps của lệnh, sau phí.
- **Chống lặp:** combo nào thua 2 lần liên tiếp bị cấm trong 10 lần chọn kế tiếp của chế độ đó.
- Chỉ chọn và áp dụng tham số **khi đang flat**. Thoát lệnh không bao giờ đổi khi đang có vị thế.
- Arm đã chọn được ghi vào ledger (`arms`).

**Test:** với reward giả lập có arm tốt nhất, sau 400 lệnh bandit chọn đúng arm > 80 %. Với dữ liệu thật thì khác: khoảng 100 lệnh/tuần chia cho 3 chế độ × 3 arm × 3 tham số, nên **mỗi arm chỉ có vài lệnh mỗi tuần**. Bandit sẽ khám phá trong nhiều tuần.

## 2. Nhiệm vụ 2: `core/dreaming_daemon.py`

**Khi chạy:** sau mỗi 15 phút hoặc mỗi 3 lệnh đóng, bên trong Runner. Có thể chạy riêng bằng `python -m core.dreaming_daemon --once`.

**Khám nghiệm:** với mỗi lệnh, tính net, gross, phí bps, trượt giá so với signal mid (lệnh taker), và markout 1 s / 5 s / 30 s. Ledger chỉ được đọc.

**Bẫy và chỉ thị** (chỉ giảm rủi ro, hết hạn sau 30 phút):

| Bẫy | Điều kiện | Chỉ thị |
|---|---|---|
| CHURN | ≥ 5 lệnh, trung vị \|gross\| < 3 bps trong khi phí ≥ 3.5 bps | trần $100 + quan sát |
| STREAK | ≥ 4 lệnh lỗ liên tiếp | quan sát |
| ADVERSE | trung vị markout 5 s ≤ −3 bps | trần $100 |
| FEE_DRAG | phí > \|gross\| và net < 0 | trần $150 |
| NO_STOP | stop bị sàn từ chối, đóng lệnh market lỗ > 20 bps | trần $150 |
| CONVICTION | tier 10 % (≥ 10 lệnh) trung bình âm và tệ hơn các tier nhỏ | trần $150 |

**Trí nhớ:**
- Bài học nguyên tử được ghi vào `data/brain_learnings.jsonl` theo dạng: pattern → nguyên nhân → chuẩn mới, kèm số liệu.
- `config/dynamic_state.json` hợp nhất các bài học: count, first/last seen.
- Bài học gặp ≥ 3 lần được **nâng thành luật** (tự kích hoạt lại trên cửa sổ 3 lệnh).
- Bài học không gặp lại trong 24 h bị xoá.

**Kết quả trên dữ liệu:**
- **Dry-run trên ledger thật** (79 lệnh, thư mục tạm, ledger không đổi): phát hiện **CONVICTION**. Tier 10 % có 18 lệnh, −5.7 bps; các tier khác −2.2 bps. Chỉ thị: `cs_max_notional 150` trong 30 phút.
  - Bảng ledger theo tier: SWARM tier "score ≥ 0.95 & OFI" có 18 lệnh, notional $204, −5.67 bps, −2.21 U. Đây là nguồn lỗ lớn nhất. Hai tier score ≥ 0.8 / ≥ 0.9 có 14 lệnh, +1.9 bps.
- **Replay tuần (bản 1):** 113 bài học (FEE_DRAG 53, STREAK 45, CHURN 15); cả ba được nâng thành luật.
- **Phát hiện phụ:** lệnh #76 ngày 09/10 bị **stop từ chối, đóng market −49 bps, −1.54 U** (2.88 SOL, tier 10 %). Đây là một mình bằng gần toàn bộ lỗ đêm qua. Mã lỗi không được ghi lại, nên ledger nay có thêm `stop_reject` (chỉ loại lỗi và mã, không có văn bản lỗi của sàn), và dreaming có bẫy NO_STOP.

## 3. Nhiệm vụ 3: nạp nóng (`core/tunables.py`)

- `Runner.autonomy_loop` kiểm tra mtime của `config/live_tunables.json` mỗi **5 giây**.
- Nội dung được kiểm tra hợp lệ (whitelist + giới hạn) rồi áp vào các engine **ngay khi flat**. Không restart. Event `⚡ AI SELF-ADAPTED: …` xuất hiện trong luồng sự kiện Web UI, và telemetry có thêm `snapshot['autonomy']`.
- **An toàn:**
  - `cs_max_notional`, `portfolio_risk_usdt`, `daily_kill_usdt`, `max_trades_hour`, `max_positions` chỉ được ≤ baseline.
  - `cooldown_s`, `cs_super_ofi` chỉ được ≥ baseline.
  - `hurdle_min_hold_s` và `tp1_scaled_pct` nằm trong khoảng của form.
  - Đòn bẩy, mode, venue và các cổng RUNE/VESKA **không nạp nóng được**.
  - Trọng số chỉ áp cho NORO/LUMEN/TIDAL.
  - File hỏng: giữ cấu hình tốt gần nhất. Hết hạn (`expires_at`): quay về baseline.
- File runtime được thêm vào `.gitignore`.
- Có thể sửa tay file này; engine tự nạp.

## 4. Nhiệm vụ 4: điều phối vốn (`core/capital_router.py`)

| Chế độ | Điều kiện | Hành động |
|---|---|---|
| CHOP | mọi symbol giao dịch được có KER < 0.25, kéo dài ≥ 60 s | làn vi mô vào chế độ quan sát; thoát CHOP ngay khi điều kiện hết |
| BURST | KER > 0.5 và \|CVD 60 s\| > $1M × usd_scale | chỉ báo hiệu (`trend_trail`) |
| NORMAL | còn lại | không đổi |

- **CHOP gần như thừa.** Engine vốn đã khoá khi KER < 0.38. Bản đầu có độ trễ 60 s ở chiều thoát CHOP, khiến router *lén* chặn những lệnh vào ngay sau giai đoạn chop. Đó là một bộ lọc chưa kiểm định, nên đã bỏ.
- **Trailing cho làn trend.** Đã thêm `TrendConfig.trail_atr` (chandelier k×ATR14).
  - A/B 4H LS ngoài mẫu 2025-01→2026-09: trail 3 ATR làm tổng lợi nhuận ngoài mẫu (638 ngày) giảm từ +18.7 % xuống +3.5 % (Δ −2.4 bps/ngày, CI [−7.8, +2.7], p 0.39).
  - Không có ý nghĩa thống kê nhưng nghiêng về xấu. **Mặc định tắt**, router không bật nó.
- **Không tăng đòn bẩy, không chuyển tiền thật.** Làn trend là paper.

## 5. Kiểm chứng kinh tế: replay liên tục cả tuần, engine thật

**Điều kiện:**
- Băng mainnet 01–07/10, SOL+DOGE, nguồn `agg` (sổ lệnh tổng hợp).
- Một tiến trình liên tục (để learner và bandit tích luỹ qua cả tuần), mock balance 5 000 U.
- So sánh ghép cặp theo giờ (168 giờ).

| Biến thể | Lệnh | Net U | bps/lệnh | Notional TB | Ghi chú |
|---|---|---|---|---|---|
| autonomy **off** | 109 | −8.51 | −5.36 | $145 | đều đi đường SWARM, tier 3 % |
| autonomy on **v1** (trọng số vào phiếu) | 88 | −8.90 | −5.31 | **$205** | 66/88 lệnh đi đường JEV và nhận tier 10 %: lỗi đã sửa |
| autonomy on **v2** (chỉ được phủ quyết) | 88 | **−6.87** | −5.31 | $146 | bản cuối: size như off, ít lệnh hơn |

- v1 so với off: Δ −0.0023 U/giờ, CI [−0.022, +0.020], p 0.81. Không khác biệt.
- v2 so với off: Δ **+0.0097 U/giờ**, CI95 [+0.0045, +0.0155], p 0.006, ρ 0.92. Tổng cộng +1.63 U/tuần trên 5 000 U.
  - Sau khi phạt cho 69 lần thử, DSR = 0.68, nên **chưa đạt ngưỡng 0.95**. Kết luận AB-7: "B tốt hơn, chưa có ý nghĩa sau deflation".
  - **Lý do tốt hơn:** chất lượng mỗi lệnh *không đổi* (−5.31 so với −5.36 bps). Hệ bớt được 21 lệnh lỗ nhờ 71 chỉ thị dreaming (STREAK 45, FEE_DRAG 54, CHURN 15; cả ba được nâng thành luật), 38 lần mute agent, và các lần learner phủ quyết.
  - Nói cách khác, **autonomy giảm lỗ bằng cách giao dịch ít đi lúc đang thua**, không phải bằng cách tìm ra alpha.
- **Hạn chế:**
  - Sổ lệnh tổng hợp và fill khi giá đi xuyên qua.
  - Một tuần duy nhất. Bẫy CONVICTION và NO_STOP được thêm sau khi replay v2 đã chạy, nên không có trong con số này.

## 6. Kiểm chứng kỹ thuật

- `python -m pytest -q` trên máy người dùng: **1433 passed** (trước vòng này: 1396).
- `tests/test_autonomy_self_learning.py` có 37 test, gồm:
  - learner: trung tính khi chưa có dữ liệu, thưởng/phạt, shrinkage, mute 30 phút, cửa sổ 20 lệnh, warm-start không bị mute;
  - learner chỉ được phủ quyết, không thêm lệnh, không tăng size;
  - shadow chỉ đếm, không hành động;
  - bandit: không bao giờ hạ OFI dưới baseline, hội tụ về arm tốt, cấm combo thua 2 lần;
  - tunables: chặn trần/sàn, từ chối đòn bẩy và cổng RUNE/VESKA, live không có trần thì từ chối, mtime/file hỏng/hết hạn;
  - nạp nóng chỉ khi flat và tự khôi phục baseline;
  - dreaming: khám nghiệm, 6 loại bẫy, chỉ thị, bài học, nâng luật, xoá theo thời gian, bỏ dòng live, không ghi ledger;
  - router: hysteresis;
  - chandelier;
  - `.gitignore`.
- Replay chạy autonomy với state trong thư mục tạm, không đụng `config/` hay `data/` thật.

## 7. Vận hành

- **Bật:** demo đã có `autonomy='on'`. Khi bạn restart, xem các dòng `⚡ AI SELF-ADAPTED` trên Web UI.
- **Ghi đè tay:**
  - sửa `config/live_tunables.json`, ví dụ `{"profile": {"cs_max_notional": 150}, "observe": false, "expires_at": <epoch>}`;
  - xoá file để quay về baseline.
- **Tắt:** `--set autonomy=off`, hoặc đặt `autonomy='off'` trong Profile demo.
- **Kiểm định mọi thay đổi:** `python tools/ab_bench.py micro --a-set autonomy=off ...`. Chạy liên tục bằng `tools/engine_replay.py` nếu muốn learner tích luỹ.
