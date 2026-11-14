# CHỈ THỊ GIAO VIỆC: CLAUDE FABLE 5.2 (xHIGH) - ĐỘT PHÁ NÉN AI 10X (552B XUỐNG 13.8 GB)
Thư mục làm việc: F:\KyThuatNenAIMoi

## 1. Mục tiêu tối thượng: Phá vỡ đáy nén 2-bit, nén gấp 10 lần
Hiện tại, rào cản nén vô hướng 2-bit (INT2/MXFP4) khiến mô hình cỡ lớn như DeepSeek V4.1 552B vẫn cần tối thiểu 138 GB đĩa cứng, vượt quá dung lượng ổ đĩa của máy cá nhân.
Mục tiêu của phiên nghiên cứu này: Nghiên cứu, thiết kế và hiện thực hóa thuật toán nén thế hệ mới đạt tỷ lệ nén gấp 10 lần (đưa footprint mô hình 552B từ 138 GB xuống khoảng 13.8 GB, tức đạt mức tương đương ~0.2 đến 0.25 bit trên mỗi tham số) mà vẫn giữ được năng lực suy luận cốt lõi.

Học hỏi từ các thành tựu mới nhất của cộng đồng (điển hình như llamAmpere v0.6.3 và Strata v0.2.11 chạy MoE 125B đạt 120 tok/s trên card 24GB): phối hợp giữa nén vector sâu, gộp chuyên gia và bộ đệm tầng.

## 2. Các hướng tiếp cận toán học bắt buộc khảo sát và lập trình thử nghiệm

1. Lượng tử hóa Vector đa tầng (Residual Vector Quantization - RVQ / AQLM Sub-1-bit):
   - Không nén từng trọng số riêng lẻ (bị chặn bởi giới hạn Shannon). Nén theo khối vector 64 hoặc 128 phần tử dùng chung một chỉ mục từ điển (Codebook).
   - Một chỉ số 16-bit đại diện cho 64 tham số đạt mức nén: 16 / 64 = 0.25 bit/tham số.
   - Viết module thử nghiệm trong `quilt/` để đo độ lệch tái tạo ma trận (Reconstruction MSE và Cosine Similarity).

2. Phân rã giá trị suy biến kết hợp gộp chuyên gia (SVD Low-Rank + MoE Expert Merging):
   - DeepSeek V4.1 có hàng trăm chuyên gia nhưng độ tương đồng không gian (Cosine Similarity) giữa nhiều cặp chuyên gia rất cao.
   - Áp dụng thuật toán gom cụm hình học (Geometric Clustering): Gộp các chuyên gia có hành vi tương đồng và chỉ giữ lại phần dư (Residual) dạng ma trận hạng thấp (Low-Rank SVD).
   - Đánh giá khả năng giảm 256/512 chuyên gia xuống còn 32 đến 48 chuyên gia tổng hợp mà không làm sập Perplexity.

3. Phân bổ độ chính xác thích ứng theo tầng (Layer-Adaptive Dynamic Precision):
   - Giữ nguyên các tầng nhạy cảm (Attention q/k/v, Router, Down-projection) ở 4-bit / FP8 (chiếm khoảng 2 đến 3 GB).
   - Nén cực đoan 0.2-bit cho toàn bộ các ma trận FFN Up/Gate của các chuyên gia nằm ở các tầng sâu.

4. Tối ưu kiến trúc runtime theo phong cách llamAmpere:
   - Nghiên cứu cơ chế Static Cache và n-gram draft cache của llamAmpere để tối ưu hóa việc nạp trang bộ nhớ, giúp việc giải mã vector nén không làm nghẽn băng thông PCIe.

## 3. Các nhiệm vụ thực thi cụ thể

Nhiệm vụ 1: Lập trình nguyên mẫu thuật toán nén 0.25-bit trong thư mục `quilt/`
- Tạo script thử nghiệm (ví dụ `quilt/rvq_compressor.py` hoặc mở rộng `quilt/tcq.py`).
- Chạy thử nén một ma trận trọng số thực tế lấy từ mô hình Qwen3/DeepSeek với các kích thước vector khối 32, 64, 128.
- Đo đạc: Tỷ lệ nén thực tế (GB trước và sau nén), sai số khôi phục (Frobenius norm error), và tốc độ giải nén trong C/CUDA.

Nhiệm vụ 2: Thiết kế thuật toán gộp chuyên gia (MoE Pruning / Merging)
- Xây dựng thuật toán đo ma trận tương quan giữa các chuyên gia.
- Viết thử nghiệm gộp một khối MoE 64 chuyên gia về 16 chuyên gia và đo mức suy hao độ chính xác.

Nhiệm vụ 3: Cập nhật công cụ chuyển đổi `tools/qk_convert.py`
- Bổ sung định dạng đóng gói siêu nén (ví dụ codec `--codec rvq10x` hoặc `--codec svd_merge`).
- Đảm bảo cấu trúc file sau khi đóng gói tương thích với đường đọc của `qk_store` và `qk_cache` trong QK Engine.

Nhiệm vụ 4: Kiểm thử và xuất báo cáo toán học
- Chạy toàn bộ bài test hồi quy để đảm bảo không làm gãy các tính năng đã chạy tốt của QK Engine.
- Lập bảng so sánh lý thuyết và thực nghiệm: Kích thước tệp (GB), Tỷ lệ nén (lần), Mức tiêu thụ VRAM, Sai số tái tạo ma trận.
- Cập nhật chi tiết kết quả vào `progress.md` và `session-handoff.md`.

## 4. Kỷ luật thực hiện
- Tuyệt đối không xóa hay làm hỏng các tệp runtime GPU vừa chạy đạt 86 tok/s ở phiên trước (`qk_model.c`, `qk_model_cuda.cu`, `qk_infer.c`).
- Mọi thuật toán đề xuất phải đi kèm mã nguồn Python/C thực thi và số liệu đo đạc thực nghiệm cụ thể.
- Báo cáo đi thẳng vào giải pháp toán học, kết quả benchmark và các đoạn mã cốt lõi.

