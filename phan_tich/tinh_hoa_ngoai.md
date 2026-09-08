# Tinh hoa ngoại — đã chắt lọc từ các dự án đa-agent đầu tư (ngày 09/09/2026)

Nguồn đã tra cứu và đối chiếu. Mục đích: tham khảo kiến trúc, KHÔNG sao chép
paradigm. GrokTOPUS là deterministic microstructure (0.5s/cycle); các nguồn
dưới đây là LLM fundamental/news (khung ngày). So sánh trực diện sẽ sai lề.

## Nguồn

| Nguồn | Link | Trạng thái |
|---|---|---|
| GuruAgents (CIKM 2025 workshop) | https://arxiv.org/abs/2510.01664 | Paper học thuật: 5 agent persona, Buffett-agent 42.2% CAGR NASDAQ-100 Q4'23–Q2'25, deterministic reasoning pipeline |
| AlphaGBM/investment-masters (635★, MIT) | https://github.com/AlphaGBM/investment-masters | 15 profile bậc thầy dạng .md; "methodology extraction, not roleplay"; SKILL.md template |
| virattt/ai-hedge-fund (63k★) | https://github.com/virattt/ai-hedge-fund | Gốc của dòng "investor personas"; VISION: agent = pluggable, backtestable alpha model với equity-curve riêng |
| TauricResearch/TradingAgents (103k★) | https://github.com/TauricResearch/TradingAgents | Bull/bear debate trước kết luận; README thừa nhận non-deterministic |
| App "Augur" 18 masters (bài TiniX) | KHÔNG tìm thấy repo công khai | Chỉ UI trình diễn; không có gì cho microstructure |

## Tinh hoa 1 — Persona → behavior đo được (GuruAgents)

Paper chứng minh: prompt-encoded persona sinh hành vi ĐẶC TRƯNG, tái lập
được, và khác nhau giữa các persona một cách thống kê. => Hướng đi đúng cho
GrokTOPUS: equity-curve RIÊNG từng agent (NORO/LUMEN/TIDAL), chấm verdict
KTC95 từng agent, cấp trọng số theo thành tích qua cơ chế record_outcome
(consensus_engine.py:62-81, Beta posterior, ngân sách alpha 60% bất biến).
Điều kiện tiên quyết: probe phải ghi đủ phiếu alpha (hiện thiếu — xem
dissent_score.py).

## Tinh hoa 2 — "Methodology extraction, not roleplay" (investment-masters)

Mỗi agent phải có tài liệu 4 tầng: (1) nguồn gốc quy tắc — file:dòng code,
(2) core principle — vì sao signal này có ý nghĩa vi mô, (3) risk control —
điều kiện bị veto, (4) quy về quy tắc đo được. Mọi claim phải truy nguồn.
=> Đã áp: phan_tich/agent_methodology.md — 8 agent viết theo khung này,
trích code thật. Cơ hội: agent nào không viết nổi tầng (2) = agent vô dụng,
ứng viên giảm trọng số.

## Tinh hoa 3 — 5 nguyên tắc hội tụ (investment-masters) đối chiếu GrokTOPUS

| Nguyên tắc của họ | GrokTOPUS đã có | Ghi chú |
|---|---|---|
| Systems over intuition | 8 agent hàm deterministic, không LLM | Đã đạt |
| Risk management over stock picking | VPIN gate, Kelly 3 trạng thái, DrawdownBrake | Đã đạt |
| Clear thesis + willing to be wrong | Pass/fail viết trước khi chạy phiên; rollback duyệt trước | Đã đạt |
| Cycle awareness | Volatility band 6–20 ATR; cửa sổ phiên | Đã đạt |
| Long-term > short-term | n=60/120 trước khi kết luận; cấm cherry-pick | Đã đạt |

Kết luận đối chiếu: kiến trúc GrokTOPUS đã nằm sẵn ở chuẩn mà các dự án
LLM đang cố tiến tới. Không cần thay đổi kiến trúc — chỉ thiếu DỮ LIỆU
để chứng minh từng agent.

## Tinh hoa 4 — Dissent map (TradingAgents, phiên bản đo được cho HFT)

"Cho nhiều góc nhìn cạnh tranh trước khi kết luận" — bản đo được ở khung
0.5s là mức phân tán phiếu. Hai lớp:
- Gate dissent (ĐO ĐƯỢC NGAY): khoảng cách giữa conviction alpha (60% trọng
  số) và headline score — phản ánh mức các gate nghi ngờ alpha. Script:
  phan_tich/dissent_score.py
- Alpha dissent (CẦN schema mới): entropy 3 phiếu alpha. Chỉ tính được sau
  khi probe ghi alpha_votes. dissent_score.py tự nhận diện schema mới.

### Kết quả đo lần đầu (2,188 probe, dissent_score.py, 09/09/2026)

| Bucket gate-dissent | n | gross mean | KTC95 |
|---|---|---|---|
| thấp (<0.15) | 22 | −0.844 | [−2.960, +1.437] |
| giữa | 2068 | +1.130 | [+0.549, +1.732] |
| cao (>0.30) | 98 | **−4.932** | **[−8.954, −0.600]** |

Bucket cao — nơi điểm headline được nâng bởi gate trong khi alpha yếu —
có gross ÂM với KTC95 loại trừ 0: dissent proxy CÓ sức phân loại thật,
mịn hơn phân biệt nhị phân gated/non-gated trước đây (+1.70 vs +0.84).

CAVEAT (đọc trước khi tin): thang `consensus − 0.60·conviction` KHÔNG
sạch — conviction trong probe là tổng raw 3 giá trị tanh (∈[−3,3]) trong
khi confidence mỗi phiếu bị min(1,|v|) cắt (groktopus_swarm.py:62-63),
nên bucket là proxy trộn cả scale mismatch. Trước khi dùng dissent làm
gate, phải: (a) tách riêng nhóm gated=True để loại nhiễu "bị chặn sẵn",
(b) ghi alpha_votes chuẩn để dissent thuần alpha. Cả hai chờ schema 3.

## KHÔNG lấy — và lý do
## KHÔNG lấy — và lý do

| Hấp dẫn nhưng từ chối | Lý do |
|---|---|
| Vòng tranh luận LLM bull/bear | Ở khung 0.5s: bất khả về latency, đốt chi phí, non-deterministic — tự hủy lợi thế "đo được" của hệ |
| Kết nối tầng nhân tin / tin đồn xã hội | Nguồn nhiễu độc; VPIN/flow data sạch hơn nhiều bậc |
| UI "18 bậc thầy chấm /10" | Trình diễn, không phải edge; 42.2% CAGR của paper là quarterly equity, không dịch chuyển nguyên vẹn sang crypto 0.5s |
| Nhét LLM vào consensus | Phá tính auditability — giá trị cốt lõi của hệ |
