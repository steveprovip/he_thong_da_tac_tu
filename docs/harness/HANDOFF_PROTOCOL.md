# Chuẩn bàn giao ngữ cảnh không thất thoát (6 thành phần, 6 bước)

Áp dụng mỗi khi kết thúc một phiên hoặc một giai đoạn, và khi chuyển việc giữa các agent (Claude Code, Codex, Antigravity, Cursor).
Template: [templates/handoff_6c.md](templates/handoff_6c.md). Entry mới được **thêm vào cuối** `docs/harness/session-handoff.md`, tiêu đề `## Handoff 6C — <ngày> (<tag>)`.
Bản gốc trong hub: `F:\1tr_AI_agent\.agents\specs\08-handoff-protocol.md`.

## 6 thành phần bắt buộc (thiếu một mục là validator báo fail)
| # | Thành phần | Phải chứa |
|---|---|---|
| 1 | Mục tiêu (Goal) | DoD và kết quả kỳ vọng |
| 2 | Ràng buộc (Constraints) | File/DB bị cấm, quy chuẩn, phạm vi, quyền push/publish/chi tiêu |
| 3 | Trạng thái thực tế (Real State) | Branch + commit + đường dẫn file. Validator đòi có ít nhất một mốc cụ thể |
| 4 | Bằng chứng (Evidence) | Lệnh kèm kết quả đo thật (số test, exit code). Validator đòi có lệnh/verdict và con số |
| 5 | Bước tiếp theo (Next Steps) | Backlog còn lại theo thứ tự ưu tiên |
| 6 | Quyết định & Bài học (Decisions & Lessons) | Phương án đã chốt, phương án đã loại và lý do, lỗi đã gặp |

## 6 bước theo đúng thứ tự
1. **Dừng agent đang làm.** Không bàn giao khi code còn đang được sinh dở.
2. **Khóa phạm vi.** Ghi rõ việc nào in-scope, việc nào out-of-scope.
3. **Chụp trạng thái:** `git status -s`, `git diff --stat`, `git rev-parse --short HEAD`.
4. **Phân loại tiến độ.** "Xong" chỉ khi đã có bằng chứng test pass; còn lại là "đang chờ".
5. **Lọc quyết định.** Bỏ phần trò chuyện, giữ kiến trúc và lý do chọn.
6. **Xuất đúng một entry** theo template, rồi validate (phải exit 0):
   ```
   F:\1tr_AI_agent\.venv\Scripts\python F:\1tr_AI_agent\tools\harness\check_handoff.py docs\harness\session-handoff.md --entry "Handoff 6C — <ngày>"
   ```
   Có thể kiểm template bằng `--template` (chỉ kiểm heading).

## Trí nhớ Engram (đi kèm mỗi lần bàn giao)
- Binary: `F:\1tr_AI_agent\runtime\engram\bin\engram.exe`, build từ `third_party/engram@4d7f2a2` bằng `F:\1tr_AI_agent\tools\harness\build_engram.sh`.
- Dữ liệu dùng chung, phân vùng theo project: `ENGRAM_DATA_DIR=F:\1tr_AI_agent\runtime\engram\data`. Project của repo này: `he_thong_da_tac_tu_2` (`.engram/config.json`).
- Bên giao: `engram save "<tiêu đề>" "<tóm tắt 6C>" --project he_thong_da_tac_tu_2`, rồi `engram sync` để xuất ra `.engram/` (commit cùng code khi người dùng cho phép).
- Bên nhận: `engram search "<chủ đề>" --project he_thong_da_tac_tu_2`, đọc entry 6C mới nhất, chạy `./init.sh` trước khi sửa.
- MCP: server `engram` khai báo trong `.mcp.json` của repo.

## Backup dữ liệu
- SQLite (`data/testnet-journal.sqlite`) do `F:\1tr_AI_agent\tools\harness\sqlite_backup.py` lo, cấu hình ở `F:\1tr_AI_agent\config\backup_sqlite.json`. Mỗi lần backup: online backup, integrity_check, restore thử, manifest SHA-256, giữ 7 bản.
- Bản backup nằm cùng ổ vật lý với dữ liệu (SATA Kingmax). Nó chống hỏng logic và thao tác nhầm, **không** chống hỏng ổ.
- Databasus (`third_party/databasus@9ee25ed`) chỉ hỗ trợ PostgreSQL/MySQL/MariaDB/MongoDB. Chỉ bật khi đã triển khai Paymenter (MariaDB) hoặc Engram Cloud (Postgres): `docker compose -f third_party/databasus/docker-compose.yml up -d`, bind 127.0.0.1, và cần người dùng duyệt.

## Lệnh tắt (skill `agent-context-handoff`)
`/context-handoff giao --output docs/harness/session-handoff.md` · `/context-handoff nhan --input docs/harness/session-handoff.md`
