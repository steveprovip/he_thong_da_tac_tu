# Groktopus 2.0 — Harness² theo regime và Sandbox đối kháng 1000 kịch bản (ARCH-2026-10-24)

**Trạng thái:** THIẾT KẾ. Không đổi runtime, không đặt lệnh. Nhánh `docs/h2-regime-sandbox-arch`, gốc `main@91ad6ff`.

**P0a (2026-10-24):** đã thêm `core/invariants.py` và `tests/test_invariants_lock.py`. Hai file này ghim I1–I8 và hành vi JEV FAST hiện tại; chưa được import ở đường runtime nào.

## Tóm tắt

- **Đồng thuận hôm nay.** Hai lớp veto có độ mạnh khác nhau:
  - Veto của gate (ZEPHR, RUNE, OKAPI, MARIN, VESKA) là tuyệt đối.
  - Veto thống kê (score < 0.70) bị đường JEV FAST thay thế khi cả năm gate xanh. Đã tái hiện trên code thật: chỉ một alpha, consensus thô 0.60, vẫn được cấp token.
  - Ngưỡng 0.768 do TokenAuthority giữ, nhưng áp lên điểm đã nâng. Khối gate cộng sẵn 0.40 vào mọi lệnh, nên "76.8 %" không phải mức đồng thuận độc lập giữa các agent dự báo.
- **H2-Regime.** Áp phương pháp Harness² (Propose–Probe–Compose + thư viện kinh nghiệm) lên một **bề mặt tham số có biên**: 84 lever, 29 field đóng băng, các hằng số đồng thuận khóa cứng.
  - "Task" = một giai đoạn regime đã xác nhận.
  - "Domain" = ô regime (vol × trend × tox).
  - Chọn mù trong cửa sổ probe, chấm trên **holdout tương lai** chỉ sau khi đã commit.
- **Sandbox AMS.** Lấy quy trình 5 bước của MiroFish, thay mạng xã hội LLM bằng thị trường tất định. Engine chạy đúng code production qua `engine_replay` + `SandboxVenue` (có queue và latency).
  - 1000 kịch bản: H 300, B 150, A 200, X 250, F 50, N 50.
  - MiroFish thật chỉ sinh "cú sốc tường thuật" từ seed công khai, chạy cô lập và được cache. Lý do: AGPL, Zep Cloud, RNG không seed, mỗi agent một lần gọi LLM.
- **Không hứa lợi nhuận.** Báo cáo 10-09 cho thấy tự học không tạo edge. Hai cơ chế này là kỷ luật tìm kiếm và cổng an toàn. Mọi thứ chạm live đều phải có chủ sở hữu ủy quyền.

## Thứ tự đọc

| # | Tài liệu | Nội dung |
|---|---|---|
| 1 | [01_PHAN_TICH_KIEN_TRUC_HIEN_TAI.md](01_PHAN_TICH_KIEN_TRUC_HIEN_TAI.md) | kiến trúc, toán đồng thuận, vòng thích ứng, harness2/MiroFish cục bộ, 12 phát hiện, sổ bất biến I1–I8 |
| 2 | [02_THIET_KE_HARNESS2_REGIME.md](02_THIET_KE_HARNESS2_REGIME.md) | module H2-Regime: RegimeEpisode, bề mặt sửa, đệ quy hai cấp, thăng cấp L0–L2, nâng cấp đồng thuận C1–C5 |
| 3 | [03_THIET_KE_SANDBOX_MIROFISH_1000.md](03_THIET_KE_SANDBOX_MIROFISH_1000.md) | sandbox: ánh xạ MiroFish, 1000 kịch bản, 10 adversary, SandboxVenue, cổng G0–G5 |
| 4 | [04_KE_HOACH_TRIEN_KHAI.md](04_KE_HOACH_TRIEN_KHAI.md) | P0–P7: file, test, DoD, rollback; thí nghiệm đăng ký trước; rủi ro; quyết định D1–D10 |
| — | [h2_surface_v0.yaml](h2_surface_v0.yaml) | bề mặt sửa máy đọc được (đã kiểm với code) |
| — | [sandbox_suite_v0.yaml](sandbox_suite_v0.yaml) | suite 1000 kịch bản máy đọc được (đã kiểm tổng) |

## Bất biến giữ nguyên (doc 01 §10)

- I1: Token HMAC một lần, hạn 5 s, chỉ cho Decision có score ≥ 0.768, threshold ≥ 0.768, allowed và không veto.
- I2: Veto của gate là tuyệt đối.
- I3: score < 0.70 là veto. Đường JEV FAST thay thế được (chờ D4).
- I4: Trọng số cố định; gate không bị học; trọng số học được chỉ dùng để chặn.
- I5: Vào lệnh bằng Post-Only GTX.
- I6: Live: 3.85 U / 0.05 U, autonomy off, phải gõ LIVE.
- I7: Tunables chỉ giảm rủi ro.
- I8: Đường cũ: trend_floor = threshold.

## Bằng chứng của phiên thiết kế

- `python -m pytest -q` → 1608 passed, 0 failed, 126.7 s (trước khi viết tài liệu).
- `tools/engine_replay.py` SOL 2026-09-08 00:00–06:00 (`agg`) → 93,003 message trong 74.8 s, tức 0.08 sim-h/s. Đây là cơ sở cho mọi ước tính ngân sách.
- Kiểm spec: 84 lever, 29 field đóng băng, baseline khớp `PROFILES` và `config_for()`, suite tổng 1000 (P3 650 / P5 350), 0 lỗi. Phát hiện và sửa: PyYAML đọc `off` không nháy thành `False`.
- Tái hiện JEV FAST, việc cấp/tiêu token và veto cứng của RUNE trên code thật (doc 01 §3.4).
- Kiểm lại bất cứ lúc nào (spec so với code, tổng suite, 85 tham chiếu `file:line`): `python docs/architecture/tools/check_arch_docs.py` → `OK: 0 errors`.

## Quyết định cần chủ sở hữu trước khi code

D1 (mainnet từ chối ngưỡng thấp), D4 (cách hiểu veto < 70 % và JEV sizing), D5 (ghi băng liên tục, đường găng), D7 (MiroFish hay template). Danh sách đủ ở doc 04 §5.
