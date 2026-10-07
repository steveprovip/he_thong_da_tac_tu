# Form tiếp tục — dùng trong phiên đang làm hoặc sau checkpoint

```text
Tiếp tục nâng cấp GrokTOPUS tại F:\he_thong_da_tac_tu_2 theo prompts/FORM_CLAUDE_NANG_CAP_2026-10-07.md. Đọc checkpoint trong docs/harness/progress.md và session-handoff.md; kiểm tra git status và log mới phát sinh kể từ mốc đã xử lý, không nghiên cứu lại toàn repo.

Đối soát schema/P&L/phí theo venue, phiên bản, symbol, lane và route; UNKNOWN không phải 0. Báo thay đổi so với baseline và kiểm tra xem patch trước có cải thiện net EV, fill, adverse selection hoặc latency thực đo hay chỉ khiến bot ít giao dịch hơn. Mẫu quá nhỏ thì giữ kết luận chưa đủ bằng chứng.

Chọn đúng một nút thắt còn lại. Tra tài liệu gốc/release mới liên quan, ghi URL + ngày + tag/commit nếu đọc được, rồi sửa nhỏ, làm test hồi quy/ablation và chạy checks bắt buộc. Không tăng sizing/đòn bẩy, không bật live hoặc restart engine. Cập nhật experiment register, checkpoint, rollback và kết quả trước/sau. Tiếp tục làm đến khi có patch được kiểm chứng hoặc một giới hạn dữ liệu/công cụ cụ thể cần bàn giao; không chỉ đưa danh sách ý tưởng.

Trả lời ngắn: mới quan sát gì → đã làm gì → bằng chứng → bước tiếp theo. Không đổ toàn log, không tải lại tất cả skill, không lặp nguồn/test cũ nếu không có lý do mới.
```
