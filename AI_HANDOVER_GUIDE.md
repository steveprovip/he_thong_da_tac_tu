# 📋 GROKTOPUS 2.0 — AI HANDOVER & OPERATOR RUNBOOK

> Cập nhật: 2026-09-06 · Bàn giao Phase 2 (Claude Opus 5, Cowork) tiếp nối Phase 1 (GPT-6 Astra).

## 1. Tổng quan Dự án
- **Workspace**: `F:\he_thong_da_tac_tu_2`
- **Mục tiêu**: Siêu hệ thống giao dịch đa tác tử GrokTOPUS — 8 Agent (VESKA, NORO, LUMEN, TIDAL, ZEPHR, RUNE, OKAPI, MARIN), một cổng thực thi duy nhất.
- **Trạng thái**: Phase 1 hoàn tất bởi GPT-6 Astra (38 files, 41/41 tests, Testnet $4.930,88 USDT).
  Phase 2 hoàn tất: **60/60 tests pass**, Web UI Observatory nâng cấp, bộ bơm lỗi 19 bài, pre-flight offline.

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
