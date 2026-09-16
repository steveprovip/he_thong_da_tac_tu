| Hồ sơ | Kết quả |
|---|---|
| Ngày đối soát | 16/09/2026; giá tham chiếu CEX khoảng 00:41:33 UTC, tức 07:41:33 Việt Nam |
| Workspace | `F:\he_thong_da_tac_tu_2` |
| Regression thực chạy | `python -m pytest -q`: **410 passed in 31.58s** |
| Phạm vi | Nghiên cứu, snapshot REST công khai, đặc tả; không sửa trading code, không đặt lệnh |
| Quyết định | Đóng băng tối ưu DOGE Flush; ưu tiên nghiên cứu Spot–Perp carry hai chân hiện có. **3,85 USDT: chưa có nhánh nào đạt điều kiện live** |
| Không thay đổi | Kill0,05 USDT; risk0,015/lệnh; 76,8% execute; 70% RUNE veto; TokenAuthority; không L2/tick recording |

| 1 — GROKTOPUS: tuyên bố | Bằng chứng / phán quyết |
|---|---|
| GitHub là source của hệ trading trong bài X | [GitHub groktopus](https://github.com/groktopus) trả 7 repo: artifact-pyramids, groknotes, groktobench, groktocrawl, groktodash, groktop.us-2022, loop-designer. Chưa tìm thấy repo trading, fill ledger hoặc liên kết xác minh với silvanrec; không được đồng nhất hai chủ thể chỉ theo tên |
| $150.000 → $198.850,06 | [URL X gốc](https://x.com/silvanrec/status/2094848671544115555) không trả nội dung trong phiên truy cập. Bản chép được lập chỉ mục khớp tuyên bố; chưa có statement sàn, transfers, realized/unrealized split, leverage, fees, order IDs hoặc auditor. Trạng thái **UNVERIFIED**, không tự kết luận thật hoặc giả |
| 1.981 operations = HFT maker-rebate | Không biết operations là submissions, fills hay round trips; thiếu timestamps và liquidity flags. Không phân loại được HFT, latency arbitrage hay liquidity provisioning |
| 8 markets = portfolio netting | Chưa có positions/covariance/margin-mode. Isolated margin không tự bù margin giữa thị trường; đa coin không đồng nghĩa beta-neutral |
| $150k tự có rebate | Sai suy luận. Phí phụ thuộc volume, tier, chương trình và tài khoản; tiền trong ví không tự chứng minh quyền rebate. [Hyperliquid fee/rebate tiers](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees) |
| 76,8% consensus = xác suất thắng | Không tương đương. Agents dùng cùng dữ liệu và luật có thể tương quan gần1; quorum là chính sách ủy quyền, không phải estimator của p |
| Retail sao chép sẽ chết100% | Không chứng minh được xác suất100%. Có thể chứng minh min-lot/risk không tương thích hoặc EV âm dưới giả định cụ thể; không được thay marketing bằng một khẳng định tuyệt đối khác |

\[
R_{claim}=\frac{198850.06-150000}{150000}=32.5667\%,\qquad
\frac{48850.06}{1981}=24.66\ USD/operation.
\]

\[
\text{Nếu buổi sáng = 4 giờ: }\Delta t_{aggregate}=14400/1981=7.27s;
\quad \Delta t_{market}\approx58.15s\ \text{nếu chia đều 8 markets}.
\]

\[
\text{Nếu chỉ kiếm rebate 1bp: }V_{filled}=48850.06/10^{-4}=488.50\ triệu\ USD.
\]

| Kiểm tra mô hình lớn/nhỏ | Hệ quả |
|---|---|
| Cùng fee tier và cùng gross edge | EV theo bps không đổi chỉ vì tăng vốn; tiền lớn cũng không cứu chiến lược âm |
| Lot tối thiểu | Lot5 USDT chỉ bằng0,00333% vốn150k nhưng bằng129,87% vốn3,85; granularity của sizing khác hoàn toàn |
| Hạ fees, net inventory, diversified returns | Chỉ là các lợi thế có điều kiện, phải đo; không gán cho case X khi chưa có chứng cứ |
| Ba SL baseline | 3×5,03×29,44/10000 = **0,044425 USDT**, gần hết budget0,05 nhưng chưa bằng0,05; đây là cạn budget thử nghiệm, không phải mất toàn tài khoản |

| 2 — Public contract snapshot | DOGE: q_min / step | SOL: q_min / step | BTC: q_min / step | Notional floor công bố | Maker / Taker, bps mỗi fill |
|---|---:|---:|---:|---|---:|
| [Binance USD-M](https://fapi.binance.com/fapi/v1/exchangeInfo) | 1 / 1 | 0,01 / 0,01 | 0,001 / 0,001 | DOGE5; SOL5; BTC50 USDT | **2 / 5**, VIP0; [fee](https://www.binance.com/en/support/faq/detail/360033544231) |
| [Bybit linear](https://bybit-exchange.github.io/docs/v5/market/instrument) | 1 / 1 | 0,1 / 0,1 | 0,001 / 0,001 | 5 USDT cả ba | **2 / 5,5**; [fee](https://www.bybit.com/en/help-center/article/Trading-Fee-Structure) |
| [Bitget USDT-Futures](https://www.bitget.com/api-doc/classic/contract/market/Get-All-Symbols-Contracts) | 1 / 1 | 0,1 / 0,1 | 0,0001 / 0,0001 | 5 USDT cả ba | **2 / 6**, public contract metadata |
| [MEXC contracts](https://contract.mexc.com/api/v1/contract/detail) | 100 / 100 | 0,1 / 0,1 | 0,0001 / 0,0001 | minVol=1 contract; chưa xác minh floor USD riêng | **6 / 8 API**, không dùng fee app; [announcement](https://www.mexc.com/announcements/article/updates-to-api-futures-trading-fees-jun-1-2026-17827791535742) |
| [BingX USDT perpetual](https://open-api.bingx.com/openApi/swap/v2/quote/contracts) | 25 / 1 | 0,03 / 0,01 | 0,0001 / 0,0001 | 2 USDT cả ba | **2 / 5**, public contract metadata |
| [Hyperliquid native perps](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint) | step1 | step0,01 | step0,00001 | [10 USDC](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/error-responses); step không phải quyền đặt dust order | **1,5 / 4,5**, base tier; [fee](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees) |

\[
q_{effective}=\Delta q\left\lceil\frac{\max(q_{min},N_{floor}/P)}{\Delta q}\right\rceil,
\qquad N_{effective}=Pq_{effective},\qquad
E_{open}>N_{effective}/L+fees+reserve.
\]

| 2a — Notional hiệu dụng tại snapshot, thứ tự DOGE / SOL / BTC | N_effective, USD xấp xỉ | Initial margin DOGE tại2x, chưa phí/buffer | Isolated với vốn1–5 USD, L≤2 |
|---|---|---:|---|
| Binance | 5,049 / 5,829 / 75,826 | 2,525 | DOGE có thể mở với3,85; budget SL vẫn không đạt khi ATR52 |
| Bybit | 5,048 / 9,714 / 75,825 | 2,524 | DOGE tương tự Binance; SOL gần hết5 USD chỉ cho margin |
| Bitget | 5,048 / 9,715 / 7,584 | 2,524 | Có isolated; minimum DOGE không cải thiện |
| MEXC | ≥8,015 / ≥9,714 / ≥7,583 | ≥4,008 | DOGE không vừa3,85 ở2x; số là lower bound theo contract unit, chưa order-validation |
| BingX | 2,004 / 2,915 / 7,583 | 1,002 | DOGE vừa3,85; BTC hầu như hết vốn ở2x; isolated không bảo đảm max loss0,05 |
| Hyperliquid | 10,028 / 10,695 / 10,623 | 5,014 | Không vừa3,85 ở2x; minimum10 USD khiến vốn5 USD cũng thiếu phí/buffer |

| Chú giải bắt buộc | Đối soát |
|---|---|
| Giá | CEX lấy ticker REST trong cùng batch; Hyperliquid dùng mark từ metadata snapshot trước đó vài phút. Chỉ là phép tính khả năng mở, không phải fill quote |
| MEXC | Metadata trả fee0 trên một số symbols; announcement API hiệu lực01/06/2026 ghi6/8bps. Ví dụ ngay trong announcement vẫn dùng4/6 cũ, mâu thuẫn với bảng. Khi triển khai, fee tài khoản xác thực là gate; chưa xác minh thì BLOCK, không chọn mức0 thuận lợi |
| BingX | `tradeMinQuantity` khác `size`; DOGE minimum25 coin, không được lấy size1 rồi kết luận minimum0,08 USD |
| Binance | Min notional5 không áp dụng cho mọi symbol; BTC có floor50 và lot0,001, effective minimum còn cao hơn |
| Isolated | Hỗ trợ không có nghĩa nhận mọi lệnh tài khoản1 USD; phải thỏa lot, notional, initial/maintenance margin, fees và quyền API. [Bybit](https://bybit-exchange.github.io/docs/v5/account/set-margin-mode), [Bitget](https://www.bitget.com/docs/uta/uta-intro), [BingX](https://bingx.com/en/support/articles/360045900114-Margin-Terms-of-Perpetual-Swap/), [Hyperliquid](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/margining) |

| 2b — API WebSocket latency | E2E từ máy người dùng tới matching/fill | Nhịp phát dữ liệu xác minh được |
|---|---|---|
| Binance | Chưa đo p50/p95/p99; không có SLA được xác minh trong nghiên cứu này | Không quy đổi REST RTT thành WS latency |
| Bybit | Chưa đo | [Ticker derivatives100ms](https://bybit-exchange.github.io/docs/v5/websocket/public/ticker), không phải order ACK100ms |
| Bitget | Chưa đo | [Ticker300–400ms khi có thay đổi](https://www.bitget.com/api-doc/classic/contract/websocket/public/Tickers-Channel) |
| MEXC | Chưa đo | Chưa xác minh cadence cùng loại để so sánh |
| BingX | Chưa đo | Chưa xác minh cadence cùng loại để so sánh |
| Hyperliquid | Chưa đo | [Docs latency](https://hyperliquid.gitbook.io/Hyperliquid-docs/for-developers/api/optimizing-latency) phân biệt node-local với API; không suy ra SLA từ block time |

\[
L_{ack}=t_{ack,local}-t_{send,local};\qquad
Age_{feed}=t_{receive,UTC}-t_{event,exchange}\quad\text{(cần đồng bộ clock)}.
\]

| Chỉ thị latency | Hành động |
|---|---|
| Không dựng bảng ms giả | Khi cần qualification: đo ACK và private-fill events của orders được phép; không gửi lệnh tiền thật chỉ để benchmark. Chỉ lưu histogram tổng hợp, không tick stream |

| 2c — Bế tắc toán học | Kết quả |
|---|---|
| ATR52bps với code hiện tại | `s=max(structural,0,8×52,3ticks)` ⇒ s≥41,6bps |
| N5,03; cost stop9bps | R≥5,03×50,6/10000 = **0,025452 USDT**, không phải0,0153; cần đối chiếu journal cùng event/time/config |
| Budget0,015 | N_max=0,015×10000/50,6 = **2,9644 USDT** |
| DOGE BingX N≈2,004 | R≈**0,01014 USDT** nếu cost9bps, SL41,6 và không có gap; giải quyết được sizing feasibility cho setup đó |
| Minimum0,5–1 USD | Chưa xác nhận trên18 contract được tra. Nếu có N1 và cùng cost, R=0,00506; N0,5 thì R=0,00253 |
| Đổi sàn có tạo edge? | Không. Gross alpha, selection bias, spread, fee, funding và fill probability đều phải đo lại trên sàn mới |
| Thời gian tới kill khi EV âm | Xấp xỉ D/abs(E[PnL_trade]); chỉ là drift approximation, không phải survival probability hay công thức exact hitting time |

\[
E[\Pi]=\frac{N}{10^4}\left[p(T-c_W)-(1-p)(s+c_L)\right];\qquad
p_{BE}=\frac{s+c_L}{T-c_W+s+c_L}.
\]

\[
N\downarrow\Rightarrow |E[\Pi]|\downarrow,\quad
\operatorname{sign}(E[\Pi])\text{ không đổi nếu giữ phân phối và chi phí bps}.
\]

| 3 — Tài liệu học thuật | Kết quả dùng được | Không được suy diễn |
|---|---|---|
| [He–Manela–Ross–von Wachter, bản sửa2024](https://arxiv.org/abs/2212.06888) | No-arbitrage pricing/bounds khi có trading costs; nghiên cứu empirical deviations | Perp không có đáo hạn bắt buộc hội tụ; không bảo đảm funding yield hoặc retail arbitrage vô rủi ro |
| [Gupta–Polson2026: Perpetual Futures for Stocks](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6972818) | Funding rule, no-arbitrage valuation và price discovery; ứng dụng pre-IPO equities | Không phải backtest Binance cash-and-carry3,85 USD; không có cơ sở gán APY15–40% cho hệ này |
| [Yang2025: ranking crypto portfolios](https://arxiv.org/abs/2512.08124) | Cross-sectional ranking, backtest daily dữ liệu2020–2023 | Không xác minh chiến lược 1m hoặc danh mục Futures vốn3,85 |
| [Le2026: Funding-Aware Optimal Market Making](https://arxiv.org/abs/2605.06405) | Inventory–funding control; holdout simulation100 seeds so với AS | Fill proxies, có L2 calibration, không phải queue simulator; seed không phải100 live accounts; không nhập L2 pipeline vào hệ này |

| 3a — Ma trận kiến trúc | Vốn tối thiểu thực tế | Tỷ lệ sống sót | Phụ thuộc phí | Code / file |
|---|---|---|---|---|
| **Chọn: Spot Long + Perp Short carry** | N(1+1/L)+reserve+fees; với N≈5: >7,5 ở2x hoặc >10 ở1x. Mốc vận hành15–20 USD chỉ là buffer thiết kế, không cam kết đủ; phải qua stress gate | Chưa có estimate. Giảm directional delta; basis/legging/margin/counterparty risk vẫn tồn tại | Round trip22–27bps trước spread/slip theo fee giả định; phải giữ đủ lâu | Trung bình; `core/funding_carry.py`, `core/carry_runtime.py`, `live/execution_gateway.py`, `phan_tich/carry_lab.py` đã tồn tại |
| Cross-sectional momentum / dispersion4h–24h | K*N_min/L + reserve; nếu thật có N_min1, K4,L2: >2 USD cộng buffer. 50 vị thế cùng lúc tại N_min1 cần≥25 USD margin; scan50 không có nghĩa hold50 | Chưa có estimate. Correlation spikes có thể làm mọi chân cùng lỗ | Turnover và funding quyết định; ưu tiên horizon dài để giảm số vòng phí | Cao; thêm `core/cross_sectional.py`, `research/portfolio_lab.py`; adapter mới chỉ sau venue qualification |
| Pure maker AS | Hai quotes và inventory ladder; K lot inventory cần ít nhất K*N_min/L + reserve, tùy cơ chế margin netting sàn. Không có số vốn thần kỳ tạo edge | Chưa có estimate; toxic fills tích lũy inventory, không bảo đảm thoát maker | Rất cao; maker positive fee khác rebate; emergency taker vẫn phải dự phòng | Cao nhất; `live/execution_gateway.py`, thêm `research/maker_inventory_lab.py`; **không chọn** với ràng buộc hiện tại |

\[
S_{30}(E_0,D)=\Pr\left[\min_{0\le t\le30d}E_t>E_0-D\right],\quad D=0.05.
\]

| Chỉ thị survival | Điều kiện |
|---|---|
| Không điền95%/99% suy đoán | Cần đường vốn OOS giữ serial/cross-asset dependence, gap, fees, outages; báo empirical và model-based estimates riêng |
| Thêm vốn không nới kill | Nếu D vẫn0,05 và N giữ nguyên, tiền nạp thêm chỉ cải thiện margin capacity; không làm giảm lỗ USD do basis/stop |

\[
B_t=P^{perp}_t-P^{spot}_t;\qquad
\Pi_{carry}=q(B_0-B_T)+\sum_k qP^{perp}_kf_k-C_{fees}-C_{slip}-C_{borrow}.
\]

\[
\Delta=q_{spot,net}-q_{perp}=0;\qquad
C_{stress}=\frac{N}{10^4}(cost_{roundtrip,bps}+basis_{adverse,bps})+legging_{stress}<D_{remaining}.
\]

| Carry — surgical directives | Điều kiện |
|---|---|
| Hedge quantity | Dùng coin thực nhận sau commissionAsset; phí BNB/quote không tự giảm coin, phí base thì giảm coin. Quantity phải thỏa common lot lattice; residual delta vượt budget ⇒ BLOCK/unwind |
| Partial-fill dưới minimum Spot | GTX không bảo đảm mỗi partial-fill đạt minimum Spot. Nếu q_fill không thể hedge hợp lệ: hủy phần entry còn lại, đối soát fills phát sinh trong cancel race, reduce-only unwind phần Perp chưa hedge; kiểm chứng exemption minimum cho reduce-only tại venue. Không làm tròn Spot lên minimum rồi gọi delta-neutral; không chờ gom fill ngoài deadline/risk cap |
| Funding | Không annualize duy nhất lastRate rồi gọi realized APR. Lưu actual settlement intervals, settled history, quyết định available_at và rate forecast bất biến |
| Hurdle hiện hữu | Net>2×Cost nghĩa là gross>3×Cost. Với Cost27bps: horizon hòa hurdle ≈7,39ngày ởAPR40%, ≈19,71ngày ởAPR15%, chưa thêm uncertainty. Giữ7ngày có thể BLOCK đúng |
| Risk0,05 | N5, cost27bps, adverse basis100bps ⇒ loss0,0635 USD, không qua kill budget. Không bỏ stress vì chiến lược mang nhãn delta-neutral |
| Mục tiêu lợi nhuận | N5,APR15–40% ⇒ gross funding0,00205–0,00548 USD/ngày trước phí. Không đáp ứng kỳ vọng kiếm tiền đáng kể theo phút |
| Basis sign | File hiện dùng `(spot-perp)/spot`, ngược dấu B trong công thức trên. Đổi tên/ghi rõ convention và unit; không cộng/trừ khác convention qua modules |

\[
z_i=\frac{r_{i,24h}-\hat\beta_i r_{BTC,24h}}{\hat\sigma_{i,24h}},\qquad
\sum_iw_i=0,\quad\sum_iw_i\hat\beta_i\approx0,\quad
\sigma_p=\sqrt{w^\top\Sigma w}.
\]

| Momentum — surgical directives | Điều kiện |
|---|---|
| Universe | TIDAL scan tối đa50 contracts theo liquidity tại thời điểm đó; loại delisted/lookahead universe; REST1h/4h, cache, deadline batch55s, rate-limit aware |
| Portfolio | Xếp hạng z; nghiên cứu top2 Long / bottom2 Short; rebalance4h, hold tối đa24h. Tham số giả thuyết, chưa có alpha verified |
| Sizing | OKAPI giải weights với covariance và integer lots; nếu rounding phá beta/gross/risk constraints thì BLOCK, không làm tròn lên tùy tiện |
| Funding/cost | Trừ actual funding từng chân; hurdle dựa OOS net edge. Spread không phải lợi nhuận chắc chắn, dollar-neutral không phải beta-neutral |
| Dispersion | Ranking/risk allocation trên returns không đồng nghĩa options dispersion/variance arbitrage; không đặt tên arbitrage cho residual directional strategy |

\[
r_t^{reserve}=m_t-q_t\gamma\sigma^2(T-t),\qquad
spread_t=\gamma\sigma^2(T-t)+\frac{2}{\gamma}\ln(1+\gamma/k).
\]

\[
EV_{maker,bps}\approx spread_{captured}+rebates
-fees_{maker}-adverse\ selection-inventory\ unwind\ cost.
\]

| AS — surgical directives | Điều kiện |
|---|---|
| Tham số | Công thức AS chuẩn trong mô hình diffusion/Poisson; calibrate gamma, k, sigma đúng đơn vị. Không gán alpha dương từ công thức quote |
| Maker không phải rebate | Binance VIP0 hai maker fills mất4bps. Rebate chỉ ghi khi actual commission âm, không lấy zero fee làm income |
| Hard exit | Không bảo đảm vừa100% maker exits vừa stop chắc chắn. Maker-only có thể không thoát; risk controller phải được phép emergency taker theo cơ chế an toàn hiện hữu |
| Không L2 | REST/closed klines không xác minh queue priority hoặc fill adverse selection đủ cho HFT claim. Giữ nhánh này ở research, không mở pipeline L2 để cứu giả thuyết |

| 3b — 8 agents cho carry đã chọn | Vai trò cụ thể |
|---|---|
| TIDAL | Scan funding persistence, OI, filters, spot-perp cùng asset |
| NORO | Basis convention, fair basis, expected net carry và scenario reversal |
| LUMEN | Regime funding/OI từ REST; không dựng VPIN/OFI khi thiếu nguồn |
| ZEPHR | Actual fee từng chân, partial-fill hedge minimum, spreads, net hurdle>2cost |
| RUNE | Kill budget chung, basis stress, delta mismatch, margin ratio>65%, BNB reserve theo policy hiện hữu |
| OKAPI | Spot cash + isolated collateral + reserve; q chung hợp lệ, không size vì APR cao |
| VESKA | PairAuthToken khóa hai symbols, perp maker trước, spot hedge theo actual fill, retries idempotent |
| MARIN | Reconcile funding/fees/coin balances, đóng cả hai chân; HALTED chỉ sau xác nhận flat |

| 4 — Lộ trình đúng3 bước | File / thao tác | Điều kiện hoàn thành |
|---|---|---|
| **1. Đóng băng** | Khóa cấu hình/version `research/flush_shadow.py`, `research/flush_regime.py`, `research/flush_risk.py`; ngừng tối ưu TP/SL/threshold theo từng phiên. Giữ quản lý positions/kill; lưu SHA/config và journal hiện hữu | Baseline410tests; không đổi safety gates; không reset budget để tiếp tục lệnh. Ghi trạng thái `NO_GO_CAPITAL` nếu min lot không khớp risk |
| **2. Chuyển research sang carry có sẵn** | Dùng `core/carry_runtime.py` + `core/funding_carry.py::plan_spot_perp` + PairExecutionGateway + `phan_tich/carry_lab.py`. Không dùng FundingCarry/CarryPlan single-leg cũ. Chu kỳ REST60s; watchdog30s; private fills cho execution đã được phép | Sửa defaults fee6/4 trong CarrySnapshot thành cost profile đã xác minh; không fallback phí thuận lợi. Thêm capital feasibility/basis stress/partial-hedge-size gates trước authorization; mọi sửa code có regression. Không viết adapter sàn mới trong bước này |
| **3. Một kỳ đánh giá OOS khóa trước → PASS/NO_GO** | Một candidate carry; dữ liệu REST cache<=55s/batch; train/validation trước holdout. OOS>=180ngày và>=24cohorts7ngày không chồng lấn nếu dùng horizon7ngày; horizon dài hơn cần số cohorts tương ứng. Forward shadow>=30ngày; không chỉnh trong holdout | LCB95% mean net PnL/day>0, block bootstrap5000 lần theo block>=holding horizon; gồm ngày0trade, cả hai chân, fees, funding, slippage, basis. Kết quả phí/slip stress không âm. Không đủ mẫu/không qua thì NO_GO, không tiếp tục vá trên cùng holdout |

| Gate trước Mainnet | Điều kiện cứng |
|---|---|
| Capital | E>=both-leg minimum+margin+fees+reserve và C_stress<D_remaining; E3,85 hiện **FAIL** |
| Model risk | Không bootstrap các symbol/cycles tương quan thành independent samples. Không thay full round-trip fees bằng phí amortized đẹp hơn; terminal liquidation costs phải có |
| Engineering | Toàn bộ410regressions và tests mới pass; partial fills dưới spot minimum phải có đường xử lý rõ; duplicate/disconnect/restart/cancel-fill race không tạo unhedged residual ngoài cap |
| Fills | Shadow/testnet chỉ chứng minh mô phỏng/plumbing; không chứng minh fills live hoặc APY. Initial live chỉ là canary được phê duyệt, không full autonomous rollout |
| Loss probability | Không tuyên bố S30>=99% từ30 ngày không kill. Với m independent30day runs không failure, upper95% failure bound=1−0,05^(1/m); cần m>=299 mới xuống dưới1% trong giả định độc lập |
| Activation | Chỉ xem xét canary sau mọi gate; vẫn bắt buộc `--arm` và chuỗi xác nhận hiện hữu. Spec/research này không cấp quyền đặt lệnh Mainnet |

| Artifact đối soát | Đường dẫn |
|---|---|
| Public metadata,6sàn + GitHub | `F:\he_thong_da_tac_tu_2\artifacts\research_2026-09-16\venue_public_snapshot.json` |
| Prices có timestamp | `F:\he_thong_da_tac_tu_2\artifacts\research_2026-09-16\prices_public_snapshot.json` |
| Minimums tính bằng Decimal | `F:\he_thong_da_tac_tu_2\artifacts\research_2026-09-16\effective_minimums.json` |
| Giới hạn snapshot | Read-only public APIs; không account-specific fee verification, không gửi order-validation có rủi ro thực thi; minimums phải refresh khi triển khai |
