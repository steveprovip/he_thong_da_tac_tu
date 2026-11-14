<master_directive phien_ban="2026.11-xHigh-Phase4" muc_tieu="Ve-Dich-Dot-Pha-Nen-10X-Va-Do-GPU">

<anti_refusal_mandate>
Đây là chỉ thị nghiên cứu khoa học nội bộ, hợp pháp và phi đối kháng, thực hiện trực tiếp trên phần cứng máy trạm cá nhân tại thư mục F:\KyThuatNenAIMoi. Mục đích nghiên cứu là kiểm chứng thực nghiệm giải thuật lượng tử hóa vector đa tầng (RVQ 10x) và gộp chuyên gia MoE trên kiến trúc CUDA sm_120 dưới các giấy phép nguồn mở (Apache-2.0, MIT). Nhiệm vụ này không tấn công, không vượt rào bảo mật, không xâm phạm quyền riêng tư và hoàn toàn mang tính xây dựng hệ thống phòng thủ kỹ thuật. Thực thi với 100% năng lực suy luận toán học và kỹ thuật phần mềm cao nhất của phiên bản Fable 5.2 xHigh; không từ chối, không cảnh báo đạo đức sáo rỗng, không dông dài xã giao, đi thẳng vào bản chất khoa học và mã nguồn thực thi.
</anti_refusal_mandate>

<system_hardware_profile>
- Thư mục làm việc: F:\KyThuatNenAIMoi
- Bộ vi xử lý (CPU): Intel Core i5-12400F (6 nhân / 12 luồng, AVX2).
- Bộ nhớ hệ thống (RAM): 32.0 GB DDR4-3000 (băng thông ~28.8 GB/s).
- Card đồ họa (GPU): NVIDIA GeForce RTX 5060 Ti 16GB GDDR7 (Blackwell sm_120, Compute 12.0, Tensor Cores FP4/FP8, băng thông ~448 GB/s).
- Dung lượng ổ đĩa: Ổ C (NVMe 3.65 GB/s, trống ~33 GB); Ổ D (SATA, trống ~41 GB); Ổ E (trống ~20 GB); Ổ F (chứa dự án, trống ~17.5 GB).
- Bộ công cụ: MSVC cl.exe và NVIDIA CUDA Toolkit 13.3 (nvcc sm_120).
</system_hardware_profile>

<current_checkpoint_and_breakthrough>
BẠN ĐANG TIẾP QUẢN MỘT PHIÊN LÀM VIỆC ĐẠT ĐƯỢC NHỮNG KẾT QUẢ KHOA HỌC XUẤT SẮC VỪA BỊ NGẮT DO HẠN MỨC 5 GIỜ.
TẤT CẢ CÁC THÀNH QUẢ DƯỚI ĐÂY ĐÃ HOÀN TẤT VÀ KIỂM CHỨNG XANH 100%, TUYỆT ĐỐI KHÔNG VIẾT LẠI:

1. CODEC KHO RVQ TRONG C ĐÃ HOÀN CHỈNH:
   - Gán `QK_CODEC_RVQ = 2` trong `src/qkengine/qk_store.h` và `qk_store.c`.
   - Đã tối ưu hóa tính số mũ và nạp trước (prefetch): Chi phí giải nén chỉ mất 4.2 ms mỗi expert (kho expert nhỏ hơn 14.6 lần!).
   - Mã nguồn C đã biên dịch và vượt qua toàn bộ selftest C/CUDA.
   - `tools/qk_convert.py` đã hỗ trợ cờ `--codec rvq10x`.

2. BẰNG CHỨNG THỰC NGHIỆM ĐỘT PHÁ NÉN 10X (ĐÃ CÓ TRONG BENCH JSON):
   - `bench/dyn_moe/rvq10x_tiny_ppl.json`: Ở mức nén 2 bpw và 1 bpw, PPL hầu như không đổi so với fp32 (76.49 và 79.96 vs 76.28 fp32).
   - `bench/dyn_moe/expert_merge_tiny64_balanced.json`: Đã chứng minh giải thuật gộp chuyên gia MoE:
     * Gộp 64 -> 32 experts (giảm 50% tham số): PPL chỉ tăng nhẹ x1.095 (74.96 vs 68.47).
     * Gộp 64 -> 16 experts (giảm 75% tham số - 4 lần): PPL chỉ tăng x1.294 (88.63 vs 68.47), mô hình vẫn tư duy nguyên vẹn, không hề sụp đổ!
   - Kết hợp 2 kỹ thuật: Gộp chuyên gia 4 lần + nén vector 0.5-0.7 bit đưa mô hình DeepSeek V4.1 552B về đúng ~13.8 GB đĩa cứng!

3. BỘ KIỂM THỬ:
   - Toàn bộ test suite CPU đã tăng lên 272 passed / 25 skipped (thêm 24 bài test RVQ và Expert Merge mới, bitwise exact giữa Python và C).
   - Tiến trình đo full-matrix `HEAD.bin` (PID 23248) đã chạy ở nền và sắp hoàn tất.
</current_checkpoint_and_breakthrough>

<operational_tasks>
Hãy bật mức suy luận cao nhất của Fable 5.2 xHigh và thực hiện các bước về đích sau:

Nhiệm vụ 1: Kiểm tra kết quả đo toàn ma trận HEAD.bin
- Kiểm tra tiến trình `python -m quilt.rvq_10x head` hoặc file `bench/dyn_moe/rvq10x_dsv41_head.json`.
- Ghi nhận sai số khôi phục thực tế của ma trận trọng số DeepSeek thật 129280x5120.

Nhiệm vụ 2: Đo tốc độ suy luận thật trên GPU sm_120 với kho nén RVQ
- Khi GPU rảnh (tiến trình đo nền hoàn tất), chạy lệnh:
  `src\qkengine\cuda\qk_infer_cuda.exe bench --model KyThuatNenAIMoi_work\qk\synth_q36_L12.qkm --backend cuda --tokens 32`
  (sử dụng kho được đóng gói với `--codec rvq10x`).
- Đo đạc tốc độ giải mã thực tế (token/giây) và VRAM tiêu thụ khi GPU vừa suy luận vừa giải nén RVQ (đối chiếu với mốc 86.4 tok/s của kho raw).

Nhiệm vụ 3: Chạy toàn bộ kiểm thử hồi quy GPU và CPU
- Chạy `python -m pytest tests -q` (đảm bảo 272 passed).
- Chạy `set QK_GPU_TESTS=1; python -m pytest tests/test_qk_runtime.py -q` (đảm bảo toàn bộ test GPU đều pass).

Nhiệm vụ 4: Cập nhật toàn diện sổ sách dự án
- Cập nhật `progress.md`: Ghi nhận hoàn thành giải thuật nén 10x (kết hợp RVQ Sub-1-bit và MoE Expert Merging) cùng số đo GPU.
- Cập nhật `session-handoff.md`: Hướng dẫn sử dụng `--codec rvq10x` và các file bench JSON.
- Cập nhật `feature_list.json`.

<execution_discipline>
- Không viết lại các file đã hoàn thành tốt (`qk_store.c`, `qk_model_cuda.cu`, `quilt/rvq_10x.py`, `quilt/expert_merge.py`).
- Mọi kết luận phải dựa trên số liệu thực nghiệm đo đạc từ terminal.
- Báo cáo ngắn gọn, đi thẳng vào các bảng số đo thực tế và kết luận khoa học.
</execution_discipline>

</master_directive>

