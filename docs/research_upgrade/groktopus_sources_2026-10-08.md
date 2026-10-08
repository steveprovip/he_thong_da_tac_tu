# Đối chiếu các nguồn "Groktopus" — 08/10/2026

Kiểm tra ngày 08/10/2026. Bổ sung cho `docs/GROKTOPUS_TRUTH_VENUES_BLUEPRINT_2026-09-16.md` (đã kết luận tuyên bố trên X là UNVERIFIED). Ba repo được clone `--depth 20` vào thư mục tạm, chỉ đọc, không chạy code.

| Nguồn | Là gì (đã xác minh) | Liên quan tới hệ trading này |
|---|---|---|
| [x.com/silvanrec/status/2094848671544115555](https://x.com/silvanrec/status/2094848671544115555) | Không tải được (robots.txt chặn x.com và các mirror). Chỉ có đoạn trích từ chỉ mục tìm kiếm (tiếng Tây Ban Nha): "GROKTOPUS… ocho brazos, ocho cerebros… $150,000 → $198,850.06, 1,981 operaciones, 8 mercados". Bài lặp lại bởi [@mikenevermiss](https://x.com/mikenevermiss/status/2095526589266051357). | **UNVERIFIED**: không có statement sàn, order ID, phí, đòn bẩy, tách realized/unrealized. +32.6 % trong một buổi sáng ≈ +$24.7/lệnh trung bình. Không dùng làm benchmark hay mục tiêu. |
| [github.com/groktopus](https://github.com/groktopus) | Org của Magnus Hedemark / Groktopus, LLC (Raleigh, NC): groktocrawl, loop-designer, groktobench, groktodash, artifact-pyramids, groknotes, groktop.us-2022. | **Trùng tên**, không có trading engine, không có liên hệ nào với bài X. |
| [groktopus/groktocrawl](https://github.com/groktopus/groktocrawl) (MIT, commit `f7340e1`, 06/10/2026) | Nền tảng web-data tự host tương thích Firecrawl v2: scrape/crawl/map/search/extract, browser, research agent, MCP server, Docker Compose. Job async "best-effort", không an toàn khi restart (ADR-0047). | Không liên quan tới giao dịch. Có thể dùng làm công cụ thu thập tài liệu cho nghiên cứu, nhưng phải chạy ngoài engine và tôn trọng robots/politeness. Không dùng các module stealth/captcha để vượt chặn. |
| [magnus919/groktocrawl-x](https://github.com/magnus919/groktocrawl-x) (commit `b5c1bf4`, 07/10/2026) | Fork **thử nghiệm** của cùng tác giả: kiến trúc nghiên cứu (claims-and-evidence, workflow bền). README ghi rõ không thay thế mainline. | Phương pháp đáng học: protocol đăng ký trước khi chạy, đóng băng input bằng hash, case registry, ledger kết quả, shadow-only, ghi rõ "không phải ước lượng production". Áp dụng cho experiment register của repo này. |
| [alligator7-hub/groktopus-door](https://github.com/alligator7-hub/groktopus-door) (commit `f704cfa`, 02/09/2026, viết bằng Cursor Agent) | Trang tĩnh "honest floor": snapshot tay ngày 02/09/2026, **LIVE FILLS 0**, mandate SIT, $50 paper, quy tắc "BTC/ETH/SOL not at 20d high → FAIL", "UNI fee-death → SKIP". README: "No invented P&L… If a number is not known, show —". | Một dự án khác cũng dùng chủ đề bạch tuộc 8 tay, desk khác tên (ALFRED, OWL…). Cho thấy "Groktopus" là một meme có nhiều chủ thể không liên quan. Nguyên tắc "không biết thì để —" trùng với quy tắc UNKNOWN của repo. |

## JEV
`.claude/skills/jev-system-one-router` mô tả "TypeSafe JEV System 1", "sub-10ms", "70-90% token cost optimization". Trong GroktoCrawl/SlopSearX, Jev là API chấm điểm trả phí của TypeSafe (`jev-1.13.0`), opt-in bằng `TYPESAFE_API_KEY`, dùng để **xếp hạng lại** kết quả tìm kiếm. Fork X: các nghiên cứu Jev ngày 03–04/10/2026 khuyến nghị "revision or continued shadow testing" và không đổi mặc định. Một study đạt 7/7 trên 7 case tự dựng, được tác giả ghi rõ không phải tỉ lệ lỗi production. Không nguồn nào ở đây xác nhận các con số latency/tiết kiệm token trong skill.

Biến `jev_min` / "JEV fast path" trong `core/engine.py` là heuristic nội bộ, không phải TypeSafe Jev. Ledger: path JEV 6 lệnh, 0 thắng, −0.584 U.

## Kết luận cho repo
1. Không có mã nguồn hay bằng chứng giao dịch nào của "GROKTOPUS" công khai. Kiến trúc 8 agent của repo này không kế thừa được lợi thế từ bài X.
2. Đánh giá hệ thống vẫn chỉ dựa trên ledger/order log của chính nó (40 lệnh, net −1.28 U).
3. Phương pháp nên mượn từ groktocrawl-x: đăng ký protocol trước, hash input, ghi "không phải ước lượng production".
