# Form tiếp tục sau ca DOGE SOFT-BE — 07/10/2026

Chọn **Opus 5.5 / High** trong Claude, cấp quyền thư mục `F:\he_thong_da_tac_tu_2`, rồi dán khối bên dưới. Form này tiếp tục công việc đã làm; không yêu cầu xây lại hệ thống. Chọn High trong giao diện, không chỉ viết High vào chat.

```text
<mission>
Tiếp tục nghiên cứu và nâng cấp GrokTOPUS ở F:\he_thong_da_tac_tu_2. Hệ thống vẫn có kỳ vọng âm. Hãy kiểm tra log mới nhất, tái hiện nguyên nhân mất tiền, sửa các lỗi xác nhận được rồi nghiên cứu cải tiến chiến lược có thể kiểm chứng. Mục tiêu là net EV sau mọi chi phí, kiểm soát tail risk và giảm độ trễ hữu ích. Không đánh đồng tần suất cao, consensus cao, maker fill hay test pass với khả năng sinh lời.

Tự thực hiện đọc, nghiên cứu, patch và kiểm thử trong phạm vi này; không kết thúc bằng lời hứa bước tiếp theo khi còn việc có thể làm. Không tự khởi chạy/restart bot, bật live, tăng vốn, đòn bẩy hoặc quyền API. Nếu Cowork thiếu công cụ sửa/test repo, lưu handoff cụ thể sang Claude Code, ghi rõ việc chưa chạy.
</mission>

<evidence>
Snapshot sau 23:02 ngày 07/10/2026: engine_trades.jsonl có 37 lệnh DEMO, 11 thắng, PF 0.511409, net -1.24659858 USDT. BTC 27 lệnh net -1.45314225; SOL 9 lệnh +0.25179298; DOGE 1 lệnh -0.04524931. Đây là số ledger, không phải chứng nhận P&L live hay hiệu quả của phiên bản mới. Tính lại từ log hiện tại, giữ UNKNOWN cho gross/fee thiếu.

Ca DOGE cần tái hiện đầu tiên: BUY 291 @0.08880, exit bình quân 0.08868, gross -0.03492, fee 0.01032931, net -0.04524931; reason SOFT-BE; be_locked=true; mbe_locked=true; ratchet=false; exit_reprices=6; hold 77.075s; MFE 0.140766%; basis entry -10.688 bps; send→ack 151.6ms; ack→fill 2992.1ms. UI ghi BREAKEVEN LOCKED nhưng stop sàn trong ảnh vẫn 0.08835, dưới entry của LONG.

Ảnh terminal cũng ghi SOL ADOPT SHORT 0.61 @116.01, nhiều RATCHET failed after cancel rồi restore stop 116.42; sau đó SOFT-PROFIT net +0.0234543. SOL BUY 0.25 @116.67 đóng EJECT-BE net chỉ +0.0008305. Dữ liệu mới có 12 entry-attempt rows: 6 UNFILLED, 4 REFUSED, 1 PARTIAL, 1 FILLED. Không lấy mẫu nhỏ này làm bằng chứng tăng sizing.
</evidence>

<work>
1. Đọc AGENTS.md, CLAUDE.md, git status, docs/harness/{feature_list.json,progress.md,session-handoff.md} và docs/research_upgrade/. Kiểm tra bản code/policy hiện tại. Vòng trước đã bổ sung schema 2, attempt log, stage latency, KILL ledger và tắt size boost; xác minh chúng thay vì làm lại. Outcome SOL từng UNKNOWN đã có dòng ADOPT mới; đối soát entry fee và các fills khi chuyển ownership, không tiếp tục coi vị thế đó chưa được đóng.

2. Đối soát engine_trades.jsonl, engine_orders.jsonl, telemetry và dữ liệu journal/fills sàn có thể đọc hợp lệ. Phân tầng DEMO/MOCK/LIVE, symbol, lane, policy/code, phiên và operator/adopt. Phân biệt net realized, unrealized, equity và known-cost net. Không đổ toàn log ra chat hoặc gửi log/secrets riêng tư lên web.

3. Trace ca DOGE qua manage/_manage_soft, start_soft_exit, work_soft_exit, move_stop, swap_stop, close/sync và UI. Code quan sát được đặt be_locked khi một mức soft-stop được tính; SOFT-BE gọi start_soft_exit mà không truyền floor; work_soft_exit chỉ giữ floor nếu có. Kiểm tra đây là policy thoát bám touch được thiết kế hay lỗi invariant. Tách trạng thái ARMING/TARGET, ORDER_ACKNOWLEDGED, PROTECTION_CONFIRMED, EXIT_PENDING, SETTLED. UI chỉ nêu bảo vệ sàn khi có xác nhận trạng thái/giá/qty hợp lệ; không gọi một soft target là hòa vốn đã được bảo đảm.

Không sửa bằng cách giữ floor vĩnh viễn: lệnh có thể không khớp trong khi giá tiếp tục giảm. So sánh trong replay: maker exit có deadline; controlled aggressive reduce-only exit sau deadline/risk breach; và stop sàn xác nhận được. Tính cả taker fee, slippage, missed fill và loss trong thời gian chờ. Hard risk exit phải ưu tiên hơn mục tiêu tiết kiệm phí. Không hứa stop-market hoặc breakeven ngăn mọi khoản lỗ.

4. Với RATCHET thất bại: ghi đầy đủ mã lỗi sàn đã sanitize, request/client ID, workingType, triggerPrice, giá mark/contract/touch tại thời điểm đặt, trạng thái cancel và remaining qty. Kiểm tra Binance conditional algo API, -4130/-2021, precision/tick, priceProtect và timeout theo tài liệu hiện hành; không đoán lỗi chỉ từ tên BinanceError. Tái hiện cancel→new-stop fail→restore, cancel timeout nhưng thực tế đã thành công, partial fill trong cancel, restore fail, reconnect/restart. Đo khoảng không có stop và kiểm tra hành động bù; không để exception bị nuốt rồi gắn nhãn được bảo vệ. KILL phải idempotent, settle đúng và reconcile position/order còn lại.

5. Nghiên cứu có mục đích từ nguồn gốc: Cont–Kukanov–Stoikov về OFI; Gould–Bonart về queue imbalance; Lehalle–Mounjid về adverse selection/latency; Avellaneda–Stoikov về quote/inventory; Andrew Lo về thay đổi regime; Bailey–López de Prado về selection bias/DSR/PBO. Chọn phương pháp phù hợp dữ liệu, không lấy danh tiếng làm bằng chứng lợi nhuận. Đọc prompts/NGUON_NANG_CAP_2026-10-07.md và phần nguồn cuối form này; tra thêm release/paper trong 90 ngày gần nhất nếu giải quyết đúng nút thắt.

Ưu tiên Binance/Anthropic chính thức, repo tác giả hftbacktest, NautilusTrader, anthropics/skills, Addy Osmani. Chỉ truy xuất file/issues/releases liên quan, ghi URL, ngày, tag/commit nếu đọc được, giả định và thử nghiệm bác bỏ. Không cào tràn lan, không chọn theo stars, không cài hàng loạt model/framework. Các preprint 26–29/09/2026 là ứng viên nghiên cứu, chưa được xác nhận hiệu quả trên repo. Tối đa 5 nguồn cho nút thắt đầu tiên rồi làm patch có giá trị.

6. Đánh giá economics sau sửa lifecycle: mainnet signal vs demo execution basis, stale book, queue reset khi re-peg, markout trên đúng venue, fill probability và realized cost. Edge_bps/consensus hiện là heuristic, cần hiệu chuẩn theo horizon/policy và dữ liệu chưa dùng tuning. Score 80% không phải xác suất thắng 80%. Consensus 0% khi HOLDING không tự chứng minh veto entry hỏng; kiểm tra semantics pha vòng đời.

Chọn tối đa 3 giả thuyết, làm từng patch và ablation. Dùng hftbacktest/bridge hiện có; không giả định chạm giá là khớp. Baseline OFI/logistic trước transformer/RL. Walk-forward, purge/embargo theo label horizon, holdout cuối, realistic queue/latency/cost stress và experiment register cho mọi cấu hình. Logs chỉ gồm fills không thể đánh giá cơ hội bị gate loại. Không coi 20 lệnh hay 100 fills là ngưỡng tự động chứng minh edge; kích thước mẫu cần dựa trên uncertainty/power/dependence. Không thay đồng loạt KER/VPIN/TP/SL/size theo vài giao dịch.

7. Regression tests phải tái hiện DOGE SOFT-BE lỗ, stop reject/restore, cancel-fill race, partial fills và accounting qua ADOPT/KILL. Giữ VESKA/AuthToken, veto RUNE và risk caps. Chạy checks theo AGENTS.md; ghi actual pass/fail/skip. Code test pass là bằng chứng hành vi, không phải hiệu quả kinh tế. Không sửa expectation chỉ để che policy lỗi. Với strategy chưa đủ bằng chứng, giữ disabled/shadow và ghi cụ thể data còn thiếu; không tự chạy lệnh để kiếm thêm mẫu.
</work>

<delivery>
Sửa nhỏ trong module hiện có; config mới mặc định an toàn và có rollback. Lưu timeline tái hiện, audit, source register, experiment register và before/after vào docs/research_upgrade/; cập nhật harness/handoff. Báo ngắn: lỗi xác nhận được → patch → test/benchmark → kinh tế đã biết/chưa biết → việc tiếp theo.

Giữ High nếu đã chọn. Dùng rg, script tổng hợp và đọc đoạn liên quan; chỉ nạp 1–3 skill cần thiết. Tái dùng checkpoint và nguồn đã kiểm tra. Không yêu cầu xuất chuỗi suy luận; chỉ cần quyết định, bằng chứng và giới hạn. Không mặc định đội subagent hoặc nhồi toàn bộ registry.

Bắt đầu ngay: đối soát log mới, tái hiện ca DOGE và RATCHET failure, chọn sửa đầu tiên có bằng chứng. Hoàn tất phần đã được yêu cầu và chạy checks; không dừng sau một bài tổng quan nghiên cứu.
</delivery>
```

## Nguồn được kiểm tra cho form này

- [Anthropic — Opus 5.5 prompting](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5): dùng capability thực, chỉ dẫn hoàn tất và context đúng việc; effort cao cần đo chất lượng/chi phí. Không có form công khai nào được chứng minh tối ưu tuyệt đối cho repo này.
- [Lehalle–Mounjid — Limit order placement, adverse selection and latency](https://arxiv.org/abs/1610.00261): nghiên cứu nền tảng, nộp 2016, revision 2018; dùng để thiết kế benchmark execution, không gọi đây là nghiên cứu mới.
- [Cont–Kukanov–Stoikov — OFI](https://arxiv.org/abs/1011.6402), [Gould–Bonart — queue imbalance](https://arxiv.org/abs/1512.03492): baseline dự báo vi cấu trúc; predictive accuracy không tự chuyển thành net profit crypto.
- [Bailey–López de Prado — DSR](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf): selection bias/multiple testing; cần số lần thử và dữ liệu phù hợp.
- [hftbacktest](https://github.com/nkaz001/hftbacktest), [fill-model limitations](https://hftbacktest.readthedocs.io/en/latest/order_fill.html): queue/latency replay hiện có; replay không tự mô hình hóa market impact của lệnh.
- [Spread-Gated Hawkes-Flocking](https://arxiv.org/abs/2609.36631), nộp **29/09/2026**: nghiên cứu placement mới; kiểm định trên INTC/MSFT và dùng execution probabilities cung cấp bên ngoài. Chưa chứng minh lợi nhuận Binance.
- [ReLOBGen](https://arxiv.org/abs/2609.35867), nộp **26/09/2026**: ứng viên mô phỏng message LOB; synthetic realism chưa thay thế holdout venue thật.
- Tham khảo các nguồn còn lại trong `prompts/NGUON_NANG_CAP_2026-10-07.md`; giữ phân biệt bài nền tảng, preprint mới và kết quả benchmark thực trên hệ thống.

## Phạm vi của lần viết form

Đã đọc log/code hiện tại và chuẩn bị chỉ dẫn cho Claude. Task này không sửa trading runtime, thay đổi vốn, kích hoạt giao dịch hoặc khẳng định đã cải thiện lợi nhuận. Bộ 37 dòng là snapshot; phí trường thiếu/entry fee vị thế ADOPT cần kiểm chứng riêng.

Kiểm tra sau thay đổi hướng dẫn: init.sh → **1052 project tests passed**, toolkit **15 passed, 1 skipped**; kiểm tra cấu trúc prompt, đồng nhất bản sao, số liệu snapshot và git diff --check pass. Các kiểm tra này không xác nhận đã sửa ca thua lỗ; đó là công việc Claude cần thực hiện theo form.
