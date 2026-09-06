# 📚 HƯỚNG DẪN SỬ DỤNG BỘ TÀI LIỆU HỎI CLAUDE FABLE 5.1

Thư mục này chứa toàn bộ hồ sơ kỹ thuật, mã nguồn lõi và biểu mẫu câu hỏi chuyên sâu được chuẩn hóa dành riêng cho **Claude Fable 5.1** (chế độ **Reasoning: Max**, Context: **1M tokens**).

---

## 📂 Danh sách các tài liệu trong thư mục:

1. **`PROMPT_MASTER_HOI_FABLE_5.1.md`** ⭐ *(QUAN TRỌNG NHẤT)*
   - Biểu mẫu câu hỏi tổng thể (Master Prompt) được thiết kế đặc biệt cho Claude Fable 5.1.
   - Tóm tắt đầy đủ kiến trúc đàn 8 tác tử, nhật ký thực nghiệm, các lỗi đã giải quyết và 5 bài toán lượng tử lớn cần Fable 5.1 nghiên cứu đột phá.
   - **Cách dùng**: Mở tệp này, copy toàn bộ nội dung và dán thẳng vào ô chat của Claude Fable 5.1 Playground.

2. **`KIEN_TRUC_VA_TOAN_HOC_GROKTOPUS.md`**
   - Mô tả chi tiết lý thuyết cấu trúc vi mô thị trường (Market Microstructure), công thức đồng thuận Condorcet của 8 tác tử, cơ chế bảo vệ vốn Fractional Kelly, Avellaneda-Stoikov Triple Barrier và TokenAuthority.

3. **`NHAT_KY_THUC_NGHIEM_TESTNET_2026.md`**
   - Tổng hợp toàn bộ số liệu pháp y từ Dashboard và sàn Binance USD-M Testnet:
     - Hiện tượng bão hòa VPIN trên Testnet.
     - Phân tích bằng chứng vì sao trước đó kẹt ở 54.6% và bị VESKA veto.
     - Nhật ký lệnh thật đầu tiên vừa khớp trên Binance Testnet (`LINKUSDT SHORT 4.04 LINK`).

4. **`MA_NGUON_COT_LOI_CORE.md`**
   - Bản sao mã nguồn đầy đủ của 6 tệp trọng yếu trong thư mục `core/` để Claude Fable 5.1 đọc trực tiếp trong bộ nhớ 1M tokens mà không cần đoán mã.

---

## 🚀 Các bước copy hỏi Claude Fable 5.1:

1. Mở trình duyệt tại tab: `platform.experientiallabs.ai/playground?model=claude-fable-5.1`
2. Kiểm tra thông số cột bên phải:
   - **Model**: `Claude Fable 5.1`
   - **Reasoning effort**: Chọn **`Max`** (như trong ảnh của bạn để mô hình suy nghĩ sâu nhất).
   - **Max response tokens**: `128,000` (hoặc model default).
3. Mở tệp **`PROMPT_MASTER_HOI_FABLE_5.1.md`**, copy toàn bộ nội dung (Ctrl+A -> Ctrl+C).
4. Dán vào khung chat (Ctrl+V) và ấn **Send**.
5. Nhận kết quả tư vấn, các công thức toán cải tiến Alpha Edge và bản nâng cấp mã nguồn từ Claude Fable 5.1.
