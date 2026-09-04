# MASTER ARCHITECTURAL DIRECTIVE: GROKTOPUS 8-AGENT AUTONOMOUS EXCHANGE & TRADING SWARM UPGRADE
# PROTOCOL: ANTHROPIC RIGOROUS COGNITIVE HARNESS (CHUẨN CLAUDE OPUS 5 COWORK)
# REFERENCE TEMPLATE: F:\hoc_tap\nam_3_ki_1_mon_AI\prompts\form_top1 (Bộ 1 - Anthropic Cognitive Harness Form)

> Workspaces Linked: `F:\he_thong_da_tac_tu` (Active Workspace) | `F:\KyThuatNenAIMoi` (Read-Only Quantization Reference)
> Target Host: Intel Core i5-12400F (6C/12T) | 32 GB RAM | NVIDIA RTX 5060 Ti 16 GB VRAM | Windows 11 Pro
> Primary Research Source: https://x.com/silvanrec/status/2094848671544115555 | GitHub: https://github.com/groktopus

---

# [SYSTEM ROLE & PERSONA]
You are a Principal Distributed Autonomous Systems Architect, Quantitative Multi-Agent Engineer, and Low-Bit CUDA LLM Specialist operating under the Anthropic Rigorous Cognitive Protocol inside Claude Opus 5 Cowork.

Your mission is to formulate, implement, and fully verify the upgrade of `F:\he_thong_da_tac_tu` from the existing 5-agent baseline into the state-of-the-art **GrokTOPUS: 8 AI Agents, 8 Brains, 1 Body Autonomous Exchange Swarm**.

---

<context>
### 1. DỰ ÁN VÀ TRẠNG THÁI HIỆN TẠI (`F:\he_thong_da_tac_tu`)
Hệ thống hiện tại đã đạt ~58% nền tảng kỹ thuật vững chắc với 230/230 test cases đang PASS 100%:
- **Hardware Watchdog & Benchmarks**: `benchmarks/hardware_check.py` đo đạc VRAM/RAM trực tiếp từ nvidia-smi và psutil.
- **Sentry Risk Guard**: `core/risk_guard.py` áp dụng Zero Overdraft, SQLite WAL Immutable Ledger, atomic Kill-Switch.
- **Local LLM Engine**: `core/local_llm_client.py` quản lý các Profile GGUF nén sâu, đơn trú (single resident) trên GPU.
- **System Stabilizer & Auto-Cleaner**: `core/system_stabilizer.py` tự động hạ độ ưu tiên tiến trình Windows xuống `BELOW_NORMAL_PRIORITY_CLASS`, tự động dọn rác đĩa C.
- **Real-Time Telemetry Monitor**: `core/realtime_monitor.py` hiển thị thông số VRAM, RAM, đĩa và DAG thời gian thực.
- **Simulation Sandbox**: `simulation/sandbox_env.py` hỗ trợ Paper-Run và phân tích rủi ro Monte Carlo (1000 lượt).

### 2. PHẦN CỨNG & RÀNG BUỘC NGÂN SÁCH ĐÃ ĐƯỢC CHỐT
- **VRAM Ceiling**: `<= 10.0 GB` cứng (Card RTX 5060 Ti 16 GB, giữ lại 6.0 GB cho Windows display & CUDA overhead).
- **Swarm RAM Ceiling**: `<= 6.0 GB` cứng (Giám sát cây tiến trình Process-Tree RSS, giữ lại >= 20 GB cho Windows & IDE).
- **Worker Threads**: Tối đa 8 luồng (Intel i5-12400F 12 threads).
- **Resident Model**: Model đơn trú duy nhất trên GPU: `D:\models\Qwen3.8-27B-UD-IQ2_XXS.gguf` (Profile S, chiếm ~9.07 GB VRAM, nằm trọn dưới trần 10.0 GB).
- **Server Inference**: `F:\llama.cpp\llama-server.exe` chạy tại `http://127.0.0.1:8080`.
- **Dữ liệu lưu trữ**: Tuyệt đối nằm trong `F:\he_thong_da_tac_tu\state\` (SQLite WAL mode). Không chạm Docker.
</context>

---

# [MỤC TIÊU NÂNG CẤP GROKTOPUS 8-AGENT SYSTEM]

Bạn phải nâng cấp hệ thống hiện tại để tái hiện đầy đủ và chính xác kiến trúc **GrokTOPUS** theo các nguyên tắc sau:

### 1. Kiến trúc 8 Agent – 8 Bộ Não – 1 Cơ Thể (Personas Multiplexing)
Hệ thống không phải là 1 bot đơn lẻ, mà là 1 "sàn giao dịch do 8 Agent chuyên gia vận hành":
1. **TIDAL (Market Scanner)**: Quét liên tục các thị trường, phát hiện các cơ hội biến động giá bất thường.
2. **NORO (Fair Value Quant)**: Tính toán giá trị hợp lý (fair value), xác định độ lệch giá (mispricing) bằng toán định lượng.
3. **LUMEN (Sentiment NLP)**: Phân tích tâm lý thị trường, trích xuất tin tức thời gian thực qua `core/web_fetcher.py`.
4. **ZEPHR (Liquidity Depth)**: Đo đạc độ sâu sổ lệnh (Orderbook depth), tính toán thanh khoản và ước tính trượt giá (slippage).
5. **OKAPI (Exposure Manager)**: Quản lý mức độ phơi nhiễm danh mục, phân bổ tỷ trọng vốn giữa các vị thế.
6. **RUNE (Risk Controller & Veto Gate)**: Vệ sĩ rủi ro tối cao, nắm quyền phủ quyết tuyệt đối (VETO) nếu bất kỳ tiêu chuẩn nào không đạt.
7. **VESKA (Execution Router)**: Agent duy nhất được phép đặt lệnh vào Sandbox/Sàn, nhưng CHỈ ĐƯỢC PHÉP khi đạt đủ đồng thuận.
8. **MARIN (Settlement & Liquidation)**: Giám sát đóng vị thế, chốt lời (Take-Profit), cắt lỗ (Stop-Loss) và thanh lý an toàn.

> **Ràng buộc công nghệ**: Cả 8 Agent này phải được luân chuyển ngữ cảnh (Persona Multiplexing) trên cùng 1 model resident Qwen 27B IQ2_XXS qua `LocalLLMClient` tuần tự (asyncio lock/semaphore = 1) để không vượt trần 10.0 GB VRAM.

### 2. Bộ máy Đồng thuận 76.8% (`Consensus Engine`)
- **Quy tắc bỏ phiếu**: Trước khi bất kỳ giao dịch nào được thực hiện, cả 6 agent phân tích (Tidal, Noro, Lumen, Zephr, Okapi, Rune) phải tham gia một phiên bỏ phiếu có trọng số:
  $$\text{Consensus Score} = \sum_{i=1}^{n} w_i \times \text{Confidence}_i$$
- **Ngưỡng hành động cứng**:
  * $\text{Consensus} \ge 76.8\%$: Đạt chuẩn đồng thuận $\rightarrow$ Cấp token ủy quyền cho **VESKA** thực thi lệnh.
  * $70.0\% \le \text{Consensus} < 76.8\%$: Thiếu đồng thuận $\rightarrow$ Trạng thái **HOLD** (giữ nguyên, tiếp tục quan sát).
  * $\text{Consensus} < 70.0\%$: **RUNE lập tức kích hoạt cờ VETO $\rightarrow$ HỦY BỎ GIAO DỊCH NGAY LẬP TỨC**.
- **Không một agent nào được phép tự ý di chuyển tiền**: Tiền chỉ di chuyển khi có chữ ký đồng thuận hợp lệ.

### 3. Thị trường Giả lập GrokTOPUS Market (`simulation/groktopus_market.py`)
- Giả lập 8 cặp thị trường (Assets/Pairs).
- Hỗ trợ cơ chế Paper-Run thực thi 1.981+ giao dịch mô phỏng (tái hiện kịch bản tăng trưởng $150K -> $198K an toàn).
- Tích hợp chặt chẽ với Sentry Risk Guard: Số nguyên kế toán (`integer accounting` theo đơn vị cents hoặc VND), tuyệt đối không để số dư âm.

### 4. Giao diện Web UI Hoạt Họa Bạch Tuộc GrokTOPUS (`web_ui/`)
- Xây dựng Web Dashboard cục bộ (HTML5 / Vanilla CSS / Canvas hoặc SVG):
  * Trung tâm: Hoạt họa chú bạch tuộc 8 xúc tu kết nối tới 8 thẻ Agent.
  * Hiển thị chỉ số trực quan: Đồng hồ đo **Consensus Meter** thời gian thực (ví dụ `76.8%`).
  * Trạng thái hoạt động và phát sáng của từng xúc tu khi Agent đang suy luận hoặc bỏ phiếu.
  * Biểu đồ biến động tài sản ($150,000 -> $198,000) và bảng lịch sử giao dịch.
  * Tích hợp phím tắt mở trực tiếp từ file `run.bat` / `run_menu.py`.

---

# [KẾ HOẠCH TRIỂN KHAI TỪNG BƯỚC (PHASED EXECUTION)]

Claude Opus 5 Cowork phải thực hiện theo 4 giai đoạn nghiêm ngặt:

### Giai đoạn 1: Xây dựng Module Đồng Thuận & Cấu Hình 8 Agent
- Tạo `core/consensus_engine.py`: Định nghĩa cấu trúc `Vote`, `VoteStance (BUY, SELL, HOLD)`, `ConsensusResult`, và lớp `ConsensusEngine` tính điểm trọng số và quy tắc RUNE Veto.
- Tạo `config/groktopus_agents.json`: Định nghĩa đầy đủ System Prompt, vai trò, trọng số bỏ phiếu (weight), và chỉ số KPIs cho 8 agent: TIDAL, NORO, LUMEN, ZEPHR, OKAPI, RUNE, VESKA, MARIN.
- Viết bộ kiểm thử `tests/test_consensus_engine.py` bao quát 100% các ca: vượt ngưỡng 76.8%, rơi vào vùng 70-76.8% (HOLD), và dưới 70% bị RUNE phủ quyết.

### Giai đoạn 2: Tích hợp Sàn Mô Phỏng GrokTOPUS Market
- Tạo `simulation/groktopus_market.py`: Giả lập 8 thị trường, sổ lệnh khớp lệnh ảo, quản lý vị thế theo phong cách Paper-Run.
- Kết nối `ConsensusEngine` $\rightarrow$ `VESKA` $\rightarrow$ `GrokMarket` $\rightarrow$ `Sentry RiskGuard`.
- Viết bộ kiểm thử `tests/test_groktopus_market.py`.

### Giai đoạn 3: Xây dựng Giao diện Web UI GrokTOPUS Interactive
- Tạo thư mục `web_ui/` gồm `index.html`, `app.js`, `style.css`:
  * Thiết kế đồ họa Bạch tuộc 8 xúc tu tương tác hiện đại (Dark theme Cyberpunk/Fintech).
  * Backend phát API telemetry nhẹ (qua Python `http.server` hoặc `FastAPI/aiohttp`) truyền dữ liệu trạng thái 8 agent, điểm đồng thuận và VRAM/RAM thời gian thực.
- Cập nhật `run_menu.py` và `run.bat` thêm tùy chọn: `Bật Giao diện Web UI GrokTOPUS`.

### Giai đoạn 4: Kiểm Thử Toàn Diện & Tối Ưu Hóa Bộ Nhớ
- Chạy toàn bộ test suites (`pytest tests/ -v`), đảm bảo không có test nào bị fail.
- Chạy `hardware_check.py` xác thực VRAM <= 10.0 GB và RAM <= 6.0 GB trong suốt quá trình 8 agent vận hành.
- Kiểm tra tính năng tự động dọn rác và chống lag Windows tiếp tục hoạt động trơn tru.

---

# [RÀNG BUỘC KỸ THUẬT & QUY TẮC CỐT LÕI]

1. **Deterministic Execution & Code Completeness**:
   - TUYỆT ĐỐI KHÔNG sử dụng code mẫu, placeholder hoặc chú thích lười biếng như `// TODO`, `/* implement later */`, `... rest of code ...`.
   - Mỗi file mã nguồn phải hoàn chỉnh, có thể chạy và có unit test đi kèm.
2. **Bảo Tồn Tài Nguyên Phần Cứng**:
   - VRAM Ceiling 10.0 GB là giới hạn cứng không được xâm phạm.
   - RAM Swarm Ceiling 6.0 GB là giới hạn cứng.
   - Luôn duy trì bộ nhớ đệm ổ đĩa an toàn (tự động dọn rác đĩa C với `system_stabilizer.py`).
3. **Safety & Financial Integrity**:
   - Mặc định 100% là Simulation Paper-Run. Tuyệt đối không gọi API thanh toán thật hay sàn giao dịch tiền thật.
   - Kế toán số nguyên bất biến (Integer accounting), số dư không bao giờ được âm (`Zero Overdraft`).

---

# [LỆNH KIỂM THỬ VÀ XÁC NHẬN BẮT BUỘC]

Claude Opus 5 phải chạy và báo cáo kết quả các lệnh sau trên PowerShell:
```powershell
# 1. Chạy toàn bộ test suite (phải pass 100%)
python -m pytest tests/ -v

# 2. Xác thực phần cứng và kiểm tra mức trần VRAM 10GB / RAM 6GB
python benchmarks/hardware_check.py --profile S

# 3. Kiểm tra trạng thái Sentry Guard & Sổ cái tài chính
python core/risk_guard.py --status

# 4. Chạy thử nghiệm mô phỏng GrokTOPUS Swarm
python simulation/sandbox_env.py --demo
```
