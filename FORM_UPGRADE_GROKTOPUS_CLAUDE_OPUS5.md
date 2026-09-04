# MASTER ARCHITECTURAL DIRECTIVE: NÂNG CẤP HỆ THỐNG ĐA TÁC TỬ LÊN KIẾN TRÚC GROKTOPUS (8 AGENTS - 76.8% CONSENSUS)
# THIẾT KẾ CHO: CLAUDE OPUS 5 COWORK & CLAUDE CODE AUTONOMOUS LOOP
# TIÊU CHUẨN PROTOCOL: ANTHROPIC RIGOROUS COGNITIVE PROTOCOL (FORM TOP 1 - BỘ 1 & BỘ 5)

> **Workspace thực thi:** `F:\he_thong_da_tac_tu`  
> **Workspace tham chiếu lượng tử:** `F:\KyThuatNenAIMoi`  
> **Nguồn nghiên cứu công nghệ:**  
> - X (Silvanrec): `https://x.com/silvanrec/status/2094848671544115555`  
> - GitHub GrokTOPUS: `https://github.com/groktopus`  
> - Video & Thảo luận cộng đồng: Hệ sinh thái 8 AI Agents biến $150K -> $198K qua 1.981 lệnh mô phỏng.  
> **Phần cứng mục tiêu:** Intel Core i5-12400F (6C/12T) | 32 GB RAM | NVIDIA RTX 5060 Ti 16 GB VRAM | Windows 11 Pro  

---

## [SYSTEM ROLE & PERSONA]
You are a Principal Distributed Systems Architect, Senior Quantitative AI Engineer, and High-Performance CUDA Systems Specialist operating under the Anthropic Rigorous Cognitive Protocol inside Claude Opus 5 Cowork.

Your mission is to upgrade the current multi-agent swarm in `F:\he_thong_da_tac_tu` into the complete **GrokTOPUS Autonomous Multi-Agent Trading & Consensus Engine**, turning 8 specialized AI agents into a single unified cognitive organism ("8 bộ não, 1 cơ thể") operating on strict mathematical consensus and zero-overdraft financial guardrails.

---

## 1. HARDWARE ANCHORS & HARD CONSTRAINTS (NGHIÊM NGẶT)

Hệ thống bắt buộc phải tuân thủ triệt để các giới hạn phần cứng đã thiết lập để không gây giật lag máy:

| Thành phần | Thông số máy thật | Trần giới hạn hoạt động (Ceiling) | Ghi chú vận hành |
|---|---|---|---|
| **GPU VRAM** | RTX 5060 Ti 16 GB (15.93 GB) | **Mức trần VRAM: `<= 10.0 GB`** | Giữ lại `>= 5.9 GB` làm bộ đệm màn hình & hệ điều hành. |
| **System RAM** | 32 GB DDR4 (21 GB free) | **Mức trần Swarm RAM: `<= 6.0 GB`** | Giữ lại `>= 14.0 GB` cho Windows 11 và IDE. |
| **CPU Threads**| i5-12400F (6C / 12T) | Worker Threads: `<= 8 threads` | Đặt Process Priority `BELOW_NORMAL_PRIORITY_CLASS`. |
| **Storage** | Ổ C: (SSD) & Ổ F: (Dự án) | Ổ C: duy trì trống `>= 25.0 GB` | Kích hoạt `SafeAutoCleaner` tự xóa rác temp khi chạy test. |
| **Inference** | Local llama.cpp (`F:\llama.cpp\llama-server.exe`) | **Model Resident duy nhất:** Profile S | `D:\models\Qwen3.8-27B-UD-IQ2_XXS.gguf` (~9.07 GB VRAM). |

> **Quy tắc Vàng về VRAM:** 16 GB VRAM không thể chứa cùng lúc 8 model AI riêng biệt. Claude Opus 5 **BẮT BUỘC** áp dụng kiến trúc **Single-Resident Persona Multiplexing**: 8 vai trò tác tử là 8 system prompt/nhiệm vụ khác nhau nhưng cùng luân phiên suy luận qua duy nhất một tiến trình `llama-server` Profile S đang thường trực trong GPU, được tuần tự hóa qua `asyncio.Semaphore(1)`.

---

## 2. HIỆN TRẠNG HỆ THỐNG HIỆN TẠI (BASELINE ~58% GROKTOPUS)

Trước khi bắt đầu, Claude Opus 5 cần nắm rõ các module cốt lõi đã chạy ổn định và đạt 230/230 test cases:
1. `core/risk_guard.py`: Sentry Guard kiểm soát VRAM 10.0 GB, RAM 6.0 GB, Zero Overdraft, SQLite WAL Immutable Ledger.
2. `core/system_stabilizer.py`: Cơ chế hạ mức ưu tiên Windows chống đơ chuột, tự dọn dẹp an toàn đĩa C.
3. `core/orchestrator.py`: Asyncio DAG Scheduler lưu trạng thái bền vững vào SQLite WAL.
4. `core/local_llm_client.py`: Multiplexer kết nối llama-server Profile S (Qwen 27B IQ2_XXS).
5. `core/realtime_monitor.py`: Dashboard giám sát thời gian thực VRAM, RAM, Sentry và tiến độ DAG.
6. `simulation/sandbox_env.py`: Paper-run simulator và Monte Carlo 1,000 trials.
7. `run.bat` & `run_menu.py`: Bảng điều khiển tương tác 10 chức năng.

---

## 3. MỤC TIÊU NÂNG CẤP GROKTOPUS TOÀN DIỆN (LÊN 100%)

Claude Opus 5 cần triển khai tiếp 4 giai đoạn cụ thể:

### GIAI ĐOẠN 1: BỘ 8 AGENT PERSONAS CHUYÊN BIỆT (GROKTOPUS BRAINS)
Tạo file cấu hình [`config/groktopus_agents.json`](file:///F:/he_thong_da_tac_tu/config/groktopus_agents.json) và module [`core/groktopus_agents.py`](file:///F:/he_thong_da_tac_tu/core/groktopus_agents.py) định nghĩa 8 bộ não với nhiệm vụ chuyên sâu:
1. 🔭 **TIDAL (Market Scanner):** Quét phát hiện cơ hội, nhận diện mô hình giá và biến động bất thường.
2. 📐 **NORO (Fair Value Engine):** Tính toán giá trị hợp lý (Fair Value / Quant pricing), phát hiện định giá sai.
3. 📰 **LUMEN (Sentiment NLP):** Phân tích tâm lý thị trường qua tin tức, mạng xã hội (tích hợp `core/web_fetcher.py`).
4. 💧 **ZEPHR (Liquidity & Orderbook):** Phân tích độ sâu sổ lệnh, tính toán trượt giá (Slippage) và thanh khoản.
5. ⚖️ **OKAPI (Exposure Manager):** Quản lý mức độ phơi nhiễm danh mục, tính toán phân bổ vốn an toàn.
6. 🛡️ **RUNE (Risk Controller & Veto):** Người gác cổng rủi ro cuối cùng, nắm quyền **Phủ Quyết Tuyệt Đối (VETO)**.
7. ⚡ **VESKA (Execution Router):** Tác tử thực thi lệnh (chỉ được kích hoạt khi có đủ đồng thuận).
8. 🏁 **MARIN (Settlement & Liquidation):** Quản lý thanh lý vị thế, Take-Profit, Stop-Loss bảo toàn vốn.

### GIAI ĐOẠN 2: BỘ MÁY ĐỒNG THUẬN 76.8% (`core/consensus_engine.py`)
Xây dựng thuật toán biểu quyết theo trọng số mô phỏng chính xác cơ chế của GrokTOPUS:
- **Cấu trúc Vote:** Mỗi agent phân tích (Tidal, Noro, Lumen, Zephr, Okapi, Rune) xuất ra một phiếu bầu:
  $$\text{Vote} = \{\text{agent\_id}, \text{action} \in [\text{BUY}, \text{SELL}, \text{HOLD}], \text{confidence} \in [0.0, 1.0], \text{weight}, \text{reasoning}\}$$
- **Quy tắc Đồng thuận:**
  $$\text{Consensus Score} = \frac{\sum (\text{weight}_i \times \text{confidence}_i \times \mathbb{I}(\text{action}_i == \text{action}^*))}{\sum \text{weight}_i}$$
- **2 Ngưỡng Kích Hoạt Tối Thượng:**
  1. **Ngưỡng hành động:** $\text{Consensus} \ge 76.8\%$ mới cấp phép cho `VESKA` đưa lệnh vào thực thi.
  2. **Quyền Phủ Quyết của RUNE:** Nếu $\text{Consensus} < 70.0\%$, agent `RUNE` kích hoạt cờ **VETO** $\rightarrow$ Hủy bỏ cơ hội giao dịch ngay lập tức, chuyển trạng thái sang `REJECTED_BY_RUNE`.
  3. Nếu $70.0\% \le \text{Consensus} < 76.8\%$: Trạng thái `HOLD` (Chờ tín hiệu rõ ràng hơn).

### GIAI ĐOẠN 3: SÀN GIAO DỊCH MÔ PHỎNG NÂNG CAO (`simulation/groktopus_market.py`)
- Mở rộng môi trường Paper-Run Sandbox để mô phỏng 8 cặp thị trường (Asset pairs).
- Cho phép chạy chuỗi giao dịch liên tục (1,981 chu kỳ giao dịch) như bài học của Silvanrec:
  - Vốn khởi điểm: $150,000 USD (hoặc quy đổi VND trong ledger).
  - Mục tiêu lợi nhuận: Đạt $198,000 USD (+32%) thông qua kỷ luật đồng thuận 76.8%.
  - Tích hợp ghi sổ cái bất biến SQLite WAL với mã hash chứng thực cho từng lệnh.
  - Tuyệt đối giữ an toàn: Mọi giao dịch chỉ diễn ra trong Sandbox ảo, **không kết nối API tiền thật**.

### GIAI ĐOẠN 4: GIAO DIỆN WEB UI HOẠT HỌA BẠCH TUỘC GROKTOPUS (`web_ui/`)
Xây dựng giao diện Web Dashboard cục bộ hiện đại theo đúng phong cách thiết kế trong video X/Facebook:
- Đồ họa trung tâm: **Con Bạch tuộc GrokTOPUS với 8 xúc tu kết nối trực tiếp tới 8 card tác tử**.
- Xúc tu phát sáng động (pulse animation) khi từng agent phát biểu trong phiên đồng thuận.
- **Đồng hồ Consensus Meter khổng lồ** hiển thị mức % đồng thuận thời gian thực (với vạch đỏ 70% và vạch xanh 76.8%).
- Log hiển thị lý luận của từng agent (Noro tính giá bao nhiêu, Zephr đánh giá thanh khoản ra sao).
- Tích hợp một phím bấm trong `run_menu.py` và `run.bat` để mở Web UI trên trình duyệt một cách mượt mà.

---

## 4. BỘ TIÊU CHUẨN KIỂM THỬ & CHẤP THUẬN (ACCEPTANCE CRITERIA)

Claude Opus 5 phải kiểm định mọi bước triển khai qua các lệnh PowerShell sau:
1. **Kiểm tra phần cứng & VRAM 10GB / RAM 6GB:**
   ```powershell
   python benchmarks\hardware_check.py --profile S
   # Phải đạt: verdict: READY (exit 0)
   ```
2. **Kiểm thử đơn vị bộ máy đồng thuận:**
   ```powershell
   python -m pytest tests\test_consensus_engine.py -v
   # Phải kiểm tra: Đồng thuận >= 76.8% được PASS, < 70% bị RUNE VETO.
   ```
3. **Chạy mô phỏng sàn giao dịch 8 Agent GrokTOPUS:**
   ```powershell
   python simulation\groktopus_market.py --trials 1981 --start-balance 150000
   ```
4. **Kiểm tra toàn bộ hệ thống:**
   ```powershell
   python -m pytest tests\ -v
   # Toàn bộ test cases mới và cũ phải PASS 100%.
   ```

---

## 5. CHỈ THỊ THI CÔNG DÀNH CHO CLAUDE OPUS 5 COWORK
- **Quy tắc code hoàn chỉnh:** Tuyệt đối không để placeholder (`// TODO`, `pass`, viết lửng lơ). Mọi hàm phải có typing, docstring và xử lý ngoại lệ đầy đủ.
- **Bảo vệ tài nguyên:** Không tạo file rác khổng lồ lên ổ C:. Sử dụng `SafeAutoCleaner` và SQLite WAL.
- **Thực thi tuần tự:** Bắt đầu bằng Giai đoạn 1 (8 Agent Personas) $\rightarrow$ Giai đoạn 2 (Consensus Engine) $\rightarrow$ Giai đoạn 3 (Simulation Market) $\rightarrow$ Giai đoạn 4 (Web UI Octopus Dashboard).
