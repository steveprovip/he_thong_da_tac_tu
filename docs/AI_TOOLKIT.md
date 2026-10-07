# Bộ công cụ AI đã tích hợp — 04/10/2026

Dự án: GrokTOPUS: hệ đa tác tử giao dịch. Mã dự án cho tìm kiếm tài liệu: `swarm`.

Kho chung: `F:\AI_Agent_Toolkit`. Mở `MO_BO_CONG_CU.cmd` trong thư mục này.

Claude Code, Codex và Antigravity có ba MCP dùng chung: `local-project-docs`, `dbx`, `notebooklm`. Khởi động lại phiên agent để nạp cấu hình mới. Tìm tài liệu local dùng chỉ mục đọc của README và docs; cần chạy `CAP_NHAT_TAI_LIEU.cmd` sau khi thay đổi tài liệu. Tài liệu và mã nguồn không tự gửi lên cloud.

Kiểm thử dự án từ thư mục gốc:
```
python -m pytest -q
```
Bằng chứng nền: 756 passed. Kết quả đầy đủ lưu ở báo cáo của phiên tích hợp.

## Thành phần phù hợp

- Harness: hướng dẫn ngắn, đọc tài liệu đúng lúc, kiểm thử trước khi báo xong; dùng trạng thái/handoff sẵn có. Tham khảo `sources/learn-harness-engineering` và `sources/claude-code-best-practice`.
- DBX: quản lý SQLite/SQL/Redis/Mongo từ một app. MCP đã cài; kết nối thật cần cấu hình trong DBX và allowlist đọc. Không tự khởi động database hoặc đổi schema.
- NotebookLM: connector đã cài; đăng nhập Google một lần bằng `DANG_NHAP_NOTEBOOKLM.cmd`. Không phải giải pháp không dùng token: truy vấn, câu trả lời và tool vẫn tiêu thụ context của agent.
- Tìm tài liệu local: dùng MCP `local-project-docs` hoặc script `tools/local_docs.py --search "câu tìm" --project swarm`.
- Agent Orchestrator/Munder: app giám sát CLI; dùng workspace/worktree riêng khi có nhiều tác tử sửa cùng dự án. Không tự chạy đội agent có phí.
- Agentic RAG: môi trường `env-rag`, cấu hình tại `apps/rag-local`; launcher `CHAY_RAG_LOCAL.cmd` dùng endpoint local 127.0.0.1:8018. Mô hình local phải được bật riêng; hỏi đáp thực chỉ được xác nhận khi endpoint hoạt động.
- AgentOps: SDK trong môi trường riêng; mẫu opt-in có tại `tools/agentops_optional.py`. Không bật cloud telemetry mặc định.
- MAI-Transcribe-2: mẫu API có ở `tools/mai_transcribe.py`, cần Azure Speech key và `--send`; phù hợp làm transcript/đánh giá âm thanh cho music, linh, rvc. Không thay RVC/TTS hoặc đường audio realtime.
- JEV/Clef/SpikingBrain: nguồn/model card ở toolkit; chưa thay engine hiện tại bằng mô hình chưa kiểm chứng trên máy.

Giữ bí mật trong cấu hình hiện tại; không sao chép chúng vào docs, notebook hoặc kho tham khảo. Không thay các ngưỡng giao dịch, model hay thiết lập GPU đang dùng chỉ vì benchmark trong bài Facebook.

## Kiểm chứng tích hợp cuối phiên

756 passed; init.sh hoàn tất.

Ba MCP handshake thành công; tra cứu local và liệt kê kết nối DBX thành công. Bộ test adapter: 15 passed, 1 skipped. RAG: HTTP 200, embedding CPU và truy xuất Qdrant qua; hỏi đáp với LLM thật chưa kiểm tra vì endpoint local chưa chạy.
