# 📋 GROKTOPUS 2.0 — AI HANDOVER & OPERATOR RUNBOOK

> Cập nhật: 2026-09-06 · Phase 4 (tầng nghiên cứu) tiếp nối Phase 3.

## 1. Tổng quan Dự án
- **Workspace**: `F:\he_thong_da_tac_tu_2`
- **Mục tiêu**: Siêu hệ thống giao dịch đa tác tử GrokTOPUS — 8 Agent (VESKA, NORO, LUMEN, TIDAL, ZEPHR, RUNE, OKAPI, MARIN), một cổng thực thi duy nhất.
- **Trạng thái**: Phase 1 hoàn tất bởi GPT-6 Astra (38 files, 41/41 tests, Testnet $4.930,88 USDT).
  Phase 2: Web UI Observatory, bộ bơm lỗi 19 bài, pre-flight offline.
  Phase 3: sửa suy thoái VPIN, sửa double-count chi phí, thêm Signal Lab đo edge.
  Phase 4: **95/95 tests pass** — thêm tầng nghiên cứu `research/` chạy swarm trong hftbacktest.

## 2. Kiến trúc & Quy chuẩn An toàn
1. **VESKA** là Agent duy nhất chạm sàn. Mọi lệnh cần `AuthToken` HMAC-SHA256 gắn với đúng nội dung Intent, dùng một lần, TTL 5 giây (`core/consensus_engine.py::TokenAuthority`).
2. **Đồng thuận**: ngưỡng thực thi **76,8%**; dưới **70,0%** hoặc có bất kỳ veto nào → RUNE chặn tuyệt đối. Trọng số cố định trong `WEIGHTS` (tổng = 1,00).
3. **Chế độ**:
   - `paper` — khớp lệnh mô phỏng FIFO trên sổ lệnh L2 thật (`--offline` dùng feed tổng hợp, không cần mạng).
   - `testnet` — lệnh thật trên Binance Futures Testnet, luôn `armed`.
   - `mainnet` — **advisory theo mặc định**; xem mục 5.
4. **Bảo vệ vị thế**: vào lệnh Post-Only `GTX`; mọi phần khớp được che ngay bằng `STOP_MARKET` `closePosition=true` trên sàn. Ratchet luôn *đặt stop mới → chờ NEW/WORKING → mới hủy stop cũ* (không bao giờ có khoảng trống).

## 3. Những gì thay đổi trong phiên này

| Tệp | Thay đổi |
|---|---|
| `web_ui/index.html` | Thêm dải ticker 8 thị trường; defs SVG cho hào quang lõi VESKA + filter neon; equity trace có vùng gradient, con trỏ và tooltip; trạng thái lõi hiển thị ở tiêu đề panel 01 |
| `web_ui/app.js` | Viết lại renderer: xúc tu sống (xung điện chạy dọc), tile ticker tái sử dụng DOM, biểu đồ equity mượt + hover, mọi trạng thái màu đều kèm nhãn chữ |
| `web_ui/style.css` | Nối thêm ~110 dòng: token `--buy/--sell/--veto/--auth`, keyframes `impulse`/`alarm`/`breathe`, lưới ticker responsive, meter OBI phân kỳ và VPIN, sửa rò rỉ CSS `header/footer` toàn cục vào tile |
| `core/web_ui_server.py` | Telemetry state thêm `authorized`, `auth_at`, `execute_gate`, `veto_gate` |
| `core/runtime.py` | Đếm AuthToken phát hành + mốc thời gian, đẩy vào telemetry; cảnh báo lớn khi Mainnet được arm, nêu rõ nguồn arm |
| `tests/test_fault_injection.py` | **Mới** — 19 bài bơm lỗi (xem mục 4) |
| `tools/preflight_testnet.py` | **Mới** — kiểm tra offline: endpoint, sizing, bracket, cổng đồng thuận, vòng đời AuthToken |
| `run.bat`, `run_launcher.py` | Menu 9 mục, có pre-flight và chạy offline; sửa số test 41 → 60 |

Bản gốc của 3 tệp Web UI được giữ lại làm điểm rollback: `web_ui/*.bak` (dự án chưa có git).

## 4. Bộ kiểm thử — 60/60 PASS

```
41 bài Phase 1  +  19 bài bơm lỗi mới  =  60 passed
```

`tests/test_fault_injection.py` phủ 5 nhóm sự cố:

- **A · Mất kết nối (6 bài)** — rớt stream public xóa sổ lệnh, reset warm-up dòng tiền và hủy lệnh chờ; rớt stream private chỉ tạm dừng vào lệnh cho tới khi `reconcile()` xong; **stop trên sàn không bao giờ bị hủy khi mất kết nối**; tự reconnect và resubscribe sau gián đoạn; `listenKeyExpired` buộc làm mới; đứt số thứ tự depth khởi động lại warm-up nhưng vẫn nhận top-of-book mới.
- **B · Khớp một phần (2 bài)** — mở 0,010 BTC khớp 0,003 BTC: stop `closePosition` che đúng 0,003, phần 0,007 bị hủy không bao giờ được che; hủy theo quote-TTL vẫn giữ nguyên stop.
- **C · Sàn từ chối Stop (4 bài)** — từ chối stop sau khi khớp → hủy lệnh + phanh khẩn cấp Market `reduceOnly` ngay; `algoStatus` không phải NEW/WORKING/ACCEPTED bị coi là thất bại; kết quả không chắc chắn được tra cứu chứ không gửi lại; sự kiện `ALGO_UPDATE` REJECTED làm treo gateway.
- **D · Đua lệnh Ratchet (5 bài)** — thứ tự bắt buộc `place → confirm → cancel`; ratchet hỏng thì stop cũ vẫn còn nguyên; không bao giờ nới lỏng bảo vệ; không ratchet lệnh chưa khớp hoặc đang đóng; phía SELL chỉ siết xuống.
- **E · Két an toàn (2 bài)** — token bị sửa hoặc phát lại không bao giờ tới sàn; Mainnet chưa arm chạy hết phiên mà **không tạo gateway, không tạo journal, không chạm venue**.

## 5. Rà soát két an toàn Mainnet

Bốn lớp khóa độc lập, mỗi lớp đủ để chặn một lệnh thật:

1. `Settings.load('mainnet')` → `armed=False` nếu không có `--arm`.
2. `Runtime.run()` chỉ tạo `Journal` + `ExecutionGateway` khi `settings.armed` — chưa arm thì **không tồn tại đường gửi lệnh**.
3. `BinanceFuturesClient.request()` ném `PermissionError` cho mọi method khác GET khi mainnet chưa arm.
4. `ExecutionGateway.submit()` từ chối nếu `not armed` hoặc `intent.mode` lệch chế độ.

Ngoài ra `initialize()` bắt buộc `dualSidePosition == False` (One-Way Mode), từ chối khởi động nếu còn vị thế/lệnh cũ trên sàn, và từ chối nếu phí vượt mô hình chi phí.

> ⚠️ **Điểm cần biết**: `armed` có **hai** đường vào — cờ `--arm`, **và** biến `ARM_LIVE_EXECUTION=true` trong `.env.local`. Hiện tại `.env.local` đặt `False` nên an toàn. Hành vi này có test bảo vệ nên không bị đổi; thay vào đó `main()` nay in cảnh báo lớn nêu đúng nguồn đã arm. **Giữ `ARM_LIVE_EXECUTION=False` để `--arm` là con đường duy nhất.**

## 6. Bảng lệnh

```powershell
# Kiểm tra offline trước mỗi phiên (không cần mạng)
python tools/preflight_testnet.py

# Toàn bộ 60 bài kiểm thử
python -m pytest -v

# Kiểm tra kết nối + số dư ví Testnet
python tools/check_connection.py

# Paper trading trên sổ lệnh L2 thật
python run_concurrent_paper.py

# Paper offline bằng feed tổng hợp (không cần mạng)
python run_concurrent_paper.py --offline --seconds 25

# Binance Testnet, lệnh vi mô bootstrap
python run_binance_testnet.py --bootstrap --seconds 20

# Dashboard độc lập
python core/web_ui_server.py          # http://127.0.0.1:8765

# Mainnet: quan sát (KHÔNG đặt lệnh)
python run_concurrent_live.py

# Mainnet: đặt lệnh thật — chỉ chạy khi đã hiểu rõ rủi ro
python run_concurrent_live.py --arm

# Menu một chạm
run.bat
```

## 7. Việc còn lại cho người vận hành

Môi trường cloud của Claude bị chặn egress tới `testnet.binancefuture.com`, nên hai bước sau **phải chạy trên máy Windows**:

1. `python tools/check_connection.py` — xác nhận lại số dư ví và hai stream depth/aggTrade.
2. `python run_binance_testnet.py --bootstrap --seconds 20` — vòng lặp Testnet thật, sau đó soi `data/testnet-journal.sqlite` và `data/testnet-last-state.json`.

Mọi thứ khác trong lộ trình đã được chạy và xác minh offline.


---

# 📊 PHASE 3 — CHẨN ĐOÁN "0 FILLS / 100% RUNE VETO"

## 8. Ba chẩn đoán, ba kết luận khác nhau

### ✅ Suy thoái VPIN — XÁC NHẬN, đã sửa
Cho tape aggTrade đuôi nặng (lognormal σ=1,2–2,2, có sweep-run) chạy qua chính `VolumeBuckets`:

| Quy tắc bucket | trades/bucket | VPIN | % bucket một chiều |
|---|---:|---:|---:|
| Cũ: `sum(1000)/100` | ~10 | 0,73 – 0,96 | > 40% |
| Mới: `calibrate_bucket_volume` | ~300 | 0,26 | 0% |

Bucket cũ nhỏ hơn ~30 lần so với cần thiết → VPIN đo **cỡ mẫu chứ không đo độc tính**.
`core/real_alpha_engine.py::calibrate_bucket_volume` ràng buộc bucket phải trải 15 giây tape
**và** chứa ≥ 25 lệnh cỡ p95. Thêm `VolumeBuckets.saturation` — tỉ lệ bucket một chiều; gần 1,0
nghĩa là VPIN vô nghĩa, dashboard hiện `SAT` màu đỏ. `min_buckets=10` cho phép báo VPIN từ
cửa sổ một phần (~2,5 phút tape) thay vì chờ đủ 50 bucket.

### 🟡 Mô hình chi phí ZEPHR — ĐÚNG MỘT NỬA
Double-count có thật: lệnh GTX nằm trong sổ **kiếm** spread chứ không trả. Đã sửa thành
`maker + taker + spread/2 + slippage` → 10,5 bps xuống 8,75 bps ở spread 1,5 bps.

Nhưng ngưỡng 1,8× **không** bất khả thi — nó là bộ lọc chế độ biến động:

| ATR/giá | edge (conviction 0,60) | ratio |
|---:|---:|---:|
| 5 bps | 3,0 bps | 0,29 |
| 20 bps | 12,0 bps | 1,14 |
| 40 bps | 24,0 bps | **2,29 ✅** |

Ba ngưỡng `slippage_bps`, `toxic_vpin`, `edge_multiple` nay là tham số của
`GroktopusSwarm.evaluate()`, chỉnh được mà không phải sửa logic.

### ❌ "Pha loãng đồng thuận" — NGƯỢC LẠI
0,40 trọng số của 5 agent cổng là **trợ cấp**, không phải pha loãng:

```
Với cả 5 cổng xanh, bộ ba định hướng chỉ cần 0,613 (không phải 0,768).
Kiểm chứng: conviction 0,61 → 76,60% BỊ CHẶN · 0,62 → 77,20% AUTHORIZED
```

Bỏ phiếu phân tầng sẽ đòi bộ ba đạt 0,768 → **chặt hơn**, không lỏng hơn. Con số 27,6–43,8%
trên dashboard là do ZEPHR+RUNE bị đưa về 0 trọng số (hệ quả của suy thoái VPIN), không phải
do pha loãng. Đã giữ nguyên luật bỏ phiếu, chỉ bổ sung hiển thị: cột `DIRECTIONAL` và
`BLOCKED BY` trong bảng vi cấu trúc.

## 9. Signal Lab — đo trước, chỉnh sau

`core/signal_lab.py` mở một probe phản thực cho **mọi** tín hiệu định hướng, kể cả tín hiệu bị
ZEPHR/RUNE chặn, lập cùng bộ rào chắn mà planner thật sẽ lập, rồi giải quyết bằng các sổ lệnh
về sau. Đây là quan sát viên thuần tuý — không giữ tham chiếu tới gateway (có test bảo vệ).

Lý do tồn tại: hệ thống chỉ từng quan sát kết quả của những lệnh mà cổng **đã** chấp nhận —
đúng cái mẫu không thể dùng để biết cổng có hiệu chuẩn đúng hay không.

Ghi ra `data/{mode}-signal-lab.jsonl`, mỗi dòng một probe đã đóng: entry/stop/TP, conviction,
edge/cost dự báo, VPIN, có bị chặn không và ai chặn, kết cục TP/SL/TIMEOUT, MFE/MAE, gross/net bps.

**Giới hạn đã ghi rõ trong docstring**: giả định lệnh vào luôn khớp ở giá maker, rào chắn giải
quyết theo top-of-book, chi phí là mô hình chứ không phải phí thật. Cả ba thiên lệch đều làm
chiến lược **trông đẹp hơn thực tế** — hướng an toàn cho một quyết định nới phanh.

Tắt bằng `--no-lab`.

`tools/calibrate_edge.py` đọc file đó và trả lời bằng khoảng tin cậy chứ không phải khẳng định:
tỉ lệ chạm TP trước SL so với p hoà vốn (1,5/4,0 = 0,375), net edge có phân biệt được với 0 không
(bootstrap CI 95%), **cổng ZEPHR có thật sự tách được lệnh tốt khỏi lệnh xấu không**, và mức
`edge_multiple` nào dữ liệu thực sự biện minh được. Dưới 200 probe nó tự nói là chưa đủ.

## 10. ⚠️ Hai điều cần bạn để mắt

1. **`run_launcher.py` menu `[1] CHẠY THỰC TẾ` gọi `run_concurrent_live.py --arm`.**
   Hiện tại vô hại vì `BINANCE_MAINNET_API_KEY` và `SECRET_KEY` trong `.env.local` đang **rỗng** —
   lệnh sẽ dừng ở `PermissionError: Missing API key`. Nhưng ngay khi bạn dán khoá Mainnet vào,
   phím `1` sẽ đặt lệnh thật bằng tiền thật, không có bước xác nhận thứ hai, trên một chiến lược
   mà Signal Lab **chưa** chứng minh có edge. Tôi để nguyên file vì bạn đã commit nó có chủ ý.
   Nếu muốn, tôi thêm bước gõ xác nhận trước khi chạy nhánh đó.

2. **Chưa có bằng chứng nào cho thấy tín hiệu có edge dương.** Sửa VPIN gỡ được một lỗi đo lường
   thật, không phải một lời hứa lợi nhuận. Hãy chạy Signal Lab đủ lâu rồi đọc
   `tools/calibrate_edge.py` trước khi nới bất kỳ ngưỡng nào.


---

# 🔬 PHASE 4 — TẦNG NGHIÊN CỨU (`research/`)

## 11. Vì sao cần

Khảo sát 12 repo trong `mau_chatloctinhhoa` cho kết luận: chỉ 4 repo chạm được bài toán,
và 6 repo (TradingAgents, FinMem, StockAgent, swarm-trader, FinRobot, FinGPT) là tác tử LLM
trên **nến ngày** của cổ phiếu — lệch 172.800 lần so với vòng lặp 0,5 giây của hệ thống này.

Repo đáng giá nhất là **hftbacktest** (MIT), và nó không dùng để xây lại hệ thống — nó trả
lời câu hỏi mà `simulation/paper_engine.py` không trả lời nổi: **lệnh GTX có khớp không.**

## 12. Thành phần

| Tệp | Vai trò |
|---|---|
| `research/book_adapter.py` | Dựng đúng `core.models.Book` từ market depth của hftbacktest, kèm `MinuteBars` sinh nến 1 phút |
| `research/bridge.py` | `SwarmBacktest` — chạy **đúng** AlphaEngine / GroktopusSwarm / BarrierPlanner / PositionSizer của bản live bên trong sàn mô phỏng |
| `research/synthetic.py` | Feed tổng hợp để kiểm thử offline; báo cáo tự đóng dấu SYNTHETIC |
| `tools/run_backtest.py` | CLI, chọn queue model và mô hình độ trễ |
| `tests/test_research_bridge.py` | 11 bài, gồm bài khoá cái bẫy phí maker |

Probe ghi qua chính `core.signal_lab.SignalLab` → **cùng schema JSONL với bản live** →
`tools/calibrate_edge.py` đọc được cả hai, so sánh backtest với live trực tiếp.

## 13. Ba con số mới

Chạy thử 90 phút trên feed tổng hợp (số liệu vô nghĩa, chỉ chứng minh đường ống):

```
entries submitted   24
  filled            13  (maker 13 · taker 0)
  expired           11        <- sẽ cross nên bị sàn từ chối
FILL RATE           54.2%     <- PaperEngine không tạo được con số này
realized fee rate   3.50 bps  <- maker vào 2bps + taker ra 5bps
```

## 14. Cái bẫy đã suýt lọt

`BUY = 1` và `MARKET = 1` **cùng giá trị** trong hftbacktest. Truyền nhầm hằng số phía vào
chỗ `order_type` biến lệnh post-only thành lệnh thị trường, bị tính 5 bps thay vì 2 bps —
trên chiến lược có tổng ngân sách chi phí ~8,75 bps. Không có lỗi nào được ném ra, PnL vẫn
chạy. Test `test_post_only_entry_rests_and_is_charged_the_maker_fee` khoá lại vĩnh viễn.

## 15. Giới hạn cần nhớ

- Dữ liệu lịch sử **công khai** của Binance không có local timestamp → thiếu độ trễ feed →
  **fill rate lạc quan hơn thực tế**. Muốn số đáng tin phải chạy collector `binancefuturesum`
  trên chính máy chạy bot.
- Cần tối thiểu ~21 phút dữ liệu mới bật `features.ready`; một lần chạy có ý nghĩa tính bằng giờ.
- Tầng này đo **alpha**, không đo gateway. Gateway đã có 19 bài bơm lỗi riêng.

Chi tiết đầy đủ: `research/README.md`.

## 16. Hàng đợi việc còn lại

Đã thống nhất làm tuần tự sau khi có số đo thật:

1. Chống look-ahead / point-in-time theo cách TradingAgents v0.4.0 làm.
2. Bộ `protections` kiểu freqtrade — stoploss guard, khoá cặp không sinh lời (**đọc ý tưởng
   rồi tự viết, freqtrade là GPL-3.0**).
3. Lớp nhận diện chế độ thị trường chạy theo giờ, tách hẳn khỏi vòng lặp nóng.
