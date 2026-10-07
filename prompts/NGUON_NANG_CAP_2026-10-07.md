# Nguồn và hướng áp dụng cho GrokTOPUS

Kiểm tra web ngày 07/10/2026. Đây là danh sách nguồn được chọn theo nút thắt trong repo, không phải bảng xếp hạng “tốt nhất thế giới”. Không có benchmark công khai nào đã chứng minh một form duy nhất tối ưu cho hệ thống này. Form được thiết kế để đo chất lượng công việc, net EV, latency và lượng context dùng thực tế.

## Prompt và cách sử dụng Claude

| Nguồn gốc | Điều áp dụng |
|---|---|
| [Prompting Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5) | Model đã có hướng dẫn riêng. Medium là mặc định; giữ High theo yêu cầu phiên này và đánh giá bằng số đo. Không hứa giảm 80% token; không yêu cầu xuất chuỗi suy luận. |
| [Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices) | Mục tiêu, context, constraints và output rõ; XML phân ranh dữ liệu với lệnh. |
| [Context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) | Context có chọn lọc và truy xuất đúng lúc; tránh nhồi toàn registry/log. |
| [Harness cho task dài](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) | Checkpoint, trạng thái công việc và kiểm chứng cho phiên tiếp theo. |
| [Cowork best practices](https://claude.com/resources/guides/claude-cowork-product-guide/best-practices-and-tips) | Đưa đầu vào cụ thể; chuyển công việc production code sang Claude Code khi cần. |
| [Customize Cowork](https://academy.claude.com/tutorials/customize-claude-cowork) | Phân biệt Global/Project Instructions, Skills và Connectors. Không giả định CLAUDE.md là setting Cowork. |

## Nhà nghiên cứu và phương pháp phù hợp

Chọn theo chuyên môn thị trường vi mô và thống kê, không lấy danh tiếng thay bằng chứng trên crypto.

| Tác giả, tài liệu | Thử nghiệm sát hệ thống | Giới hạn |
|---|---|---|
| Cont, Kukanov, Stoikov — [The Price Impact of Order Book Events](https://arxiv.org/abs/1011.6402), bản arXiv 2010–2011, bài journal 2014 | Hiệu chuẩn OFI theo độ sâu book, horizon, symbol; so sánh với OBI/CVD và heuristic edge_bps. | Dữ liệu nghiên cứu là cổ phiếu Mỹ; cần tái kiểm chứng trên Binance. |
| Gould, Bonart — [Queue Imbalance](https://arxiv.org/abs/1512.03492), 2015 | Logistic baseline dự báo hướng thay đổi mid-price; đánh giá calibration và net kết quả sau khớp. | Dự báo một tick không tự trả được phí round trip. |
| Avellaneda, Stoikov — [High-frequency trading in a limit order book](https://math.nyu.edu/inmemoriam/avellaneda/HighFrequencyTrading.pdf), 2008 | Kiểm tra quote theo inventory/volatility; hiệu chuẩn tham số bằng fill data thay vì hằng số tùy ý. | Mô hình market making có giả định riêng; không tự chuyển thành alpha directional. |
| Andrew W. Lo — [Adaptive Markets tại MIT](https://alo.mit.edu/research-page/reconciling-efficient-markets-with-behavioral-finance-the-adaptive-markets-hypothesis/) | Theo dõi suy giảm lợi thế và tính ổn định theo regime; thiết kế cơ chế dừng/đánh giá lại. | Là khung lý thuyết; không đưa ra threshold KER hay win rate bảo đảm. |
| Bailey, López de Prado — [Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf), 2014 | Ghi số cấu hình đã thử và xem xét selection bias khi đánh giá chiến lược. | Cần đủ dữ liệu và đáp ứng giả định; 34 lệnh không phải chứng nhận. |
| Bailey, Borwein, López de Prado, Zhu — [Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf) | Experiment register, kiểm tra overfit khi so sánh nhiều chiến lược/cấu hình. | Không thay thế holdout theo thời gian hoặc xử lý leakage. |

## Công nghệ và nghiên cứu gần đây

| Nguồn gốc | Quyết định áp dụng |
|---|---|
| [hftbacktest](https://github.com/nkaz001/hftbacktest), [giới hạn fill model](https://hftbacktest.readthedocs.io/en/latest/order_fill.html) | Repo đã có dependency và bridge/tests. Ưu tiên mở rộng replay queue/latency này. Replay không tự mô phỏng tác động của lệnh lên thị trường; fill khi lấy thanh khoản cần đặc biệt thận trọng. |
| [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) | Tham khảo kiến trúc event-driven và đối chiếu khả năng backtest; chỉ làm thử nghiệm cô lập nếu stack hiện tại thiếu tính năng. README hiện phân biệt stable v1 và tài liệu/development v2; không lấy API mới rồi cài bản stable không tương ứng, không nâng engine live để chạy theo phiên bản mới. |
| [TLOB](https://arxiv.org/abs/2502.15757), 2025 | Ứng viên nghiên cứu LOB sau baseline đơn giản. Chính bài báo bàn về việc predictability suy giảm khi tính chi phí. Chỉ thử khi có dataset, pipeline không leakage và benchmark net/latency. |
| Hyoeun Lee, Kiseop Lee — [Spread-Gated Hawkes-Flocking](https://arxiv.org/abs/2609.36631), nộp 29/09/2026 | Preprint mới về động lực best bid/ask và đặt limit order. Có thể nghiên cứu quote/placement sau baseline; bài dùng INTC/MSFT và xác suất khớp được cung cấp bên ngoài. Fit AIC/BIC tốt hơn không chứng minh net profit trên Binance. |
| [ReLOBGen](https://arxiv.org/abs/2609.35867), nộp 26/09/2026 | Preprint về sinh message order book có thể replay; ứng viên cho stress test/synthetic simulation khi có nhu cầu đã xác định. Không thay thế dữ liệu venue thật hoặc làm bằng chứng alpha. Chưa kiểm chứng độc lập trong repo này. |
| [PredictionMarketBench](https://arxiv.org/abs/2602.00133), 2026 | Preprint benchmark replay cho prediction markets; tham khảo nếu khảo sát Polymarket. Không phải bằng chứng lợi nhuận Binance hoặc xác nhận peer review. |
| [Binance Python connector](https://github.com/binance/binance-connector-python), [issue conditional order #545](https://github.com/binance/binance-connector-python/issues/545) | Kiểm tra sự khớp giữa SDK và conditional algo API. Repo local đã dùng `/fapi/v1/algoOrder`; kiểm chứng các trường hợp lỗi/race, không giả định endpoint còn thiếu rồi viết lại. |
| [Binance Skills Hub](https://github.com/binance/binance-skills-hub) | Tra reference/API mapping chính chủ; đọc và đánh giá trước dùng. Không tự cấp quyền trade hoặc nhập tất cả skill. |
| [Anthropic Skills](https://github.com/anthropics/skills), [Addy Osmani](https://github.com/addyosmani/agent-skills), [Superpowers](https://github.com/obra/superpowers) | Chọn thành phần kiểm tra code, verification và handoff phù hợp. Repo công khai không chứng minh mọi workflow cần dùng, hoặc sẽ giảm token trong task này. |

Ba đường dẫn Binance API cũ được mở trong lần tra cứu này đã trả trang API tổng quát, nên không coi chúng là bằng chứng đã xác nhận schema/fee hiện hành. Claude phải đi tới mục API tương ứng trên [cổng tài liệu Binance](https://developers.binance.com/) và kiểm tra response/account commission + fills trong môi trường được phép trước thay đổi. Không hard-code rate limit/phí từ skill cũ. Tag/commit/release chính xác chưa được chốt ở đây; cần chốt khi chọn dependency cho một patch cụ thể.

## Hướng nghiên cứu đầu tiên từ repo

1. Schema và đối soát: dòng TEST thiếu gross/fee/route; `_ui_engine_state.json` là snapshot MOCK cũ, khác telemetry DEMO hiện tại. Chưa được trộn hai loại dữ liệu.
2. Cost/fill: fees đã ghi 2.11414320 U lớn hơn gross của 33 dòng có trường gross, 0.95817999 U. Đo fill probability và adverse selection; GTX không đồng nghĩa giao dịch có lợi thế hoặc round trip luôn maker.
3. Calibration: `core/micro_signals.py` tính nhiều edge_bps bằng công thức heuristic; phải hiệu chuẩn trên outcome chưa dùng trong tuning. Consensus dùng chung feature không phải tập phiếu độc lập.
4. Sizing: `alt_kelly_min_n=5`; `kelly_qty()` trả None với f<=0 và chú thích cho phép quay về min/profile qty. Cần trace đường execute và kiểm chứng no-edge gate trước gọi đây là bug. Lựa chọn policy NO EDGE phải rõ, không ngầm giao dịch chỉ vì đạt minimum của sàn.
5. Độ trễ: ledger `latency_ms` có thể gồm thời gian chờ khớp GTX; cần tách stage timestamps trước benchmark.

Vòng nghiên cứu đầu tiên nên giải quyết một trong các mục này và cập nhật nguồn theo câu hỏi cụ thể. Hai preprint cuối tháng 9/2026 ở trên đáp ứng tiêu chí gần đây, nhưng chưa được benchmark tại repo. “Mới nhất” là điều kiện tìm kiếm, không phải lý do mặc định triển khai.
