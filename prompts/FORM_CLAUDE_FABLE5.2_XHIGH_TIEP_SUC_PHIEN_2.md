# CHỈ THỊ GIAO VIỆC: CLAUDE FABLE 5.2 (xHIGH) - PHIÊN TIẾP SỨC
Thư mục làm việc: F:\KyThuatNenAIMoi

## 1. Bối cảnh và Mục tiêu tối thượng
Bạn đang tiếp quản dự án F:\KyThuatNenAIMoi ở trạng thái vừa hoàn thành một bước đột phá lớn và bị ngắt do hết hạn mức 5 giờ.
Mục tiêu phiên này: Hoàn thiện suy luận suy đoán (Speculative Decoding) và đóng gói mô hình thật chạy trực tiếp trên GPU sm_120 (RTX 5060 Ti 16GB, 32GB RAM).
Tạm dừng hoàn toàn thư mục AI Linh (E:\AI_Linh_vtuber...), chỉ tập trung 100% vào F:\KyThuatNenAIMoi.

## 2. Những việc đã xong và số liệu đã kiểm chứng
Đừng làm lại những việc sau đây vì mã nguồn đã chạy ổn định và vượt toàn bộ bài test:
- Lõi sinh token C thuần (qk_model.c, qk_model.h): Đầy đủ Attention GQA, RoPE, RMSNorm, KV Cache, SwiGLU, Router MoE nối qk_store và qk_cache.
- Bộ mã hóa từ C (qk_tok.c, qk_tok.h) và bảng tra cứu Unicode (qk_unicode_tab.h).
- CLI suy luận (qk_infer.c) và bộ chuyển đổi Safetensors (tools/qk_convert.py).
- Backend GPU Blackwell sm_120 (cuda/qk_model_cuda.cu) đã được liên kết và biên dịch thành công file: cuda\qk_infer_cuda.exe.
- Bộ kiểm thử: 247 bài test pytest đều đạt (tests/test_qk_runtime.py đạt 19/19 bài). Hai bài selftest C và Infer đều đạt 100%, không rò rỉ bộ nhớ.
- Số đo thực tế đo trực tiếp trên GPU RTX 5060 Ti (với mô hình MoE 12 lớp synth_q36_L12.qkm):
  * Tốc độ giải mã: 86.38 token/giây (vượt xa mục tiêu 50 token/giây).
  * Bộ nhớ VRAM chiếm dụng: 2.206 GB (rất nhẹ so với giới hạn 8 GB).
  * Dung lượng tệp mô hình: 1.08 GB.

## 3. Các nhiệm vụ cụ thể cần làm tiếp
Hãy bật mức suy luận cao nhất và thực hiện tuần tự các việc sau:

1. Đo đạc suy luận suy đoán (Speculative Decoding / DFlash2):
   - Chạy thử tính năng suy đoán với cờ `--spec ngram` và `--spec model` trên file `cuda\qk_infer_cuda.exe`.
   - Đo tốc độ giải mã (token/giây) và tỷ lệ chấp nhận token (alpha) khi đặt gamma = 4 và gamma = 6.
   - Lưu kết quả đo vào tệp: `bench/dyn_moe/qk_infer_cuda_speculative_bench.json`.

2. Kiểm chứng đóng gói trọng số mô hình thật:
   - Dùng script `tools/qk_convert.py` để đóng gói thử một mô hình thật dạng mở (cỡ 12B–14B hoặc MoE 30B–35B có sẵn trong thư mục đệm/staging) sang cặp tệp `.qkm` và `.qkx`.
   - Kiểm tra lệnh `cuda\qk_infer_cuda.exe generate` với đầu vào câu hỏi tự nhiên để xác nhận mô hình sinh văn bản chính xác.

3. Kiểm tra an toàn bộ nhớ trên GPU (Leak check):
   - Chạy lệnh kiểm tra rò rỉ: `.\cuda\qk_infer_cuda.exe leakcheck --model KyThuatNenAIMoi_work\qk\synth_q36_L12.qkm --backend cuda`.
   - Đảm bảo sau 100 vòng mở, chạy và đóng mô hình, VRAM được giải phóng sạch sẽ 100%.

4. Cập nhật sổ sách dự án:
   - Ghi lại cột mốc đạt 86.4 token/giây trên GPU vào `progress.md` và `session-handoff.md`.
   - Cập nhật danh sách tính năng trong `feature_list.json`.

## 4. Kỷ luật thực hiện
- Không viết lại các tệp mã nguồn C/CUDA đang hoạt động tốt.
- Mọi kết luận đều phải dựa trên số đo thực tế từ terminal, không phỏng đoán.
- Báo cáo kết quả trực tiếp, ngắn gọn, đi thẳng vào số liệu kỹ thuật.
