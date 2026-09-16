| Kết luận | Phạm vi |
|---|---|
| Không có trilemma chuẩn cấm mọi giao dịch arbitrage dưới4USD | Có thể tạo ví dụ toán học một giao dịch dương sau phí; chưa chứng minh thị trường hiện có cơ hội executable lặp lại từng phút |
| Phương án ưu tiên trong miền giao dịch | Same-chain atomic USDC cycle trên Base, chỉ nghiên cứu read-only/fork trước; flashLoanSimple nếu được enable, không tạo nợ tồn tại sau transaction |
| Không đáp ứng100% hiện tại | Chưa đo inclusion probability/competition/net profit; revert có gas cost; native gas inventory và stablecoin có rủi ro riêng |
| Nới điều kiện có ý nghĩa | Bỏ bắt buộc có lợi nhuận mỗi phút; giữ vòng thực thi ngắn khi có cơ hội. Nếu zero loss bao gồm gas, native-token drawdown và mọi rủi ro tín dụng: NO_GO |
| Trạng thái | Chỉ tài liệu nghiên cứu và kế hoạch; không tạo trading client, không deploy contract, không gửi giao dịch |

\[
\Pi_{success}=Qe_{gross}-Q\sum_j f_j-I(Q)-F_{flash}(Q)-G_s-Tip.
\]

\[
E[\Pi]=p_s\Pi_{success}-p_fG_f-K_{RPC,AI,setup};\quad p_s+p_f\le1.
\]

| Ký hiệu | Định nghĩa |
|---|---|
| Q | Notional đưa qua route; có thể là vốn flash loan, không phải equity của người chạy |
| p_s | Xác suất transaction thực sự được included và thành công |
| p_f | Xác suất transaction được included nhưng thất bại và vẫn mất fee |
| 1−p_s−p_f | Không được included; không có chain fee nếu thực sự chưa lên chain, nhưng vẫn có chi phí tìm kiếm |
| I(Q) | Price impact/slippage; nếu executable quote đã bao gồm thì không trừ lần hai |
| K | Phân bổ chi phí RPC, inference, deployment, approval, onboarding; phân biệt sunk và marginal costs |

\[
Q\le3.85-G_{reserve}-C_{setup}\quad\text{nếu tự cấp vốn};
\quad Q\text{ không bị equity cap khi flash loan hợp lệ}.
\]

| Phản ví dụ số học, không phải cơ hội đã quan sát | Giá trị |
|---|---:|
| Q; gross edge; pool fees; impact | 3USD;30bps;10bps;2bps |
| Gas success | 0,003USD |
| Net success | 3×(30−10−2)/10000−0,003 = **0,0024USD** |
| Giả định p_s0,75; p_f0,25; gas failure0,002 | EV=**0,0013USD/attempt** trước K |
| Điều chưa biết | Có route đó, có thanh khoản, có giữ được giá đến execution và có thắng ordering auction hay không |

| Bảo vệ | Chứng minh / giới hạn |
|---|---|
| Atomicity | Trong một transaction, mọi swap và repay cùng thành công hoặc state changes revert. Nằm cùng block nhưng là các transaction riêng không tự atomic |
| Profit guard | Cuối transaction, kiểm tra balance asset gốc sau repay >= balance trước + minProfit; minProfit phải bao phủ conservative external fee budget để success có net dương |
| Failed transaction | Nếu p_f>0 và G_f>0 thì tồn tại trạng thái PnL=−G_f<0; không thể gọi vốn bảo toàn tuyệt đối |
| Dump−50% | Không giữ volatile inventory qua transaction tránh directional dump loss trên principal, giả định contract/token đúng; gas token dự trữ g vẫn có thể mất0,5g USD |
| Stablecoin | Bảo toàn số token không bảo đảm giáUSD nếu depeg, freeze hoặc tổn thất issuer/bridge |
| Cạnh tranh | Flash loan giảm rào cản vốn cho cả đối thủ; không tạo private order flow hoặc inclusion priority |

| Miền thị trường | Khả thi với3,85USD |
|---|---|
| Binance triangle FDUSDUSDT/BTCFDUSD/BTCUSDT | REST16/09 cho minNotional5 cả ba; vốn3USD không đặt được. Một leg0% không làm cả vòng miễn phí; maker sequential không atomic |
| DEX triangle | Có thể không có floor5USD nhưng ba swap mất phí/impact; phải ghép một transaction quay về asset gốc, không bridge giữa chains |
| Polymarket complete set | Ba active markets lấy mẫu có orderMinSize5. Nếu askYes+askNo=0,98 thì5sets tốn4,90 trước fees. Với vốn3,85,5sets cần tổng giá<=0,77 trước fees, không được giả định mispricing23% tồn tại |
| Polymarket single side + NLP | Có thể mua một phía với vốn nhỏ; outcome có thể về0. Không delta-neutral |
| Kalshi | API cập nhật20/08/2026 hỗ trợ granularity0,01contract; không phải lời hứa mọi tài khoản/order được chấp nhận với mọi notional. Cent rounding và fees đáng kể ở tiny size; đọc market/account constraints |
| Azuro | minBet phụ thuộc selection/token/chain/account; API trả minBet hoặc undefined. Cashout cần availability/quote, không phải quyền merge complete set cố định1USD |
| Maker grid fee0 | Inventory sau fill vẫn có delta; zero commission không xóa adverse selection hoặc depeg |
| AI paid microtasks | Không chịu directional market inventory; có thể vốn gần0 nếu máy/model sẵn, nhưng cần người mua và có payout/rejection risk. Đây là bán dịch vụ, không arbitrage; thời gian làm vài phút không bảo đảm payout mỗi phút |
| MEV backrun | Có thể atomic, flash-funded; tự chịu gas/tip/infrastructure. Không tự thỏa repeatable positive EV với3,85USD |

\[
\Pi_{triangle}=Q\left[\prod_{j=1}^{3}r_j(1-f_j)-1\right]-G;
\quad r_j\text{ phải là conversion rates executable đúng size/direction}.
\]

\[
Y_T+N_T=1;\quad\Pi_{complete\ set}=q(1-a_Y-a_N)-fees-G.
\]

| Prediction-market chi phí | Đối soát |
|---|---|
| Polymarket fee hiện công bố | fee=C×feeRate×p×(1−p); crypto rate0,07, tùy market. p0,5 ⇒ fee0,0175/share, tức3,5% giá mua0,50; không phải7bps |
| Ví dụ crypto5shares mỗi phía tại0,49 | Gross gap0,10USD; fees hai phía0,17493 ⇒ net−0,07493 trước gas, nếu rate0,07 áp dụng và số shares net đủ merge |
| Atomic acquisition | Hai FOK orders riêng không bảo đảm all-or-none cho cả cặp. Merge atomic không làm bước mua hai chân trước đó atomic |
| Kalshi cùng single book | askNo=1−bidYes ⇒ askYes+askNo=1+spreadYes>=1 trong book không crossed; midpoint mismatch không phải executable arbitrage |
| Azuro mọi outcomes | Với decimal odds o_i, cover hết outcomes cùng payoutV cần chi phí V×Σ(1/o_i). Arbitrage chỉ khi tổng này<1 sau fees và có khả năng khóa mọi legs; overround thông thường làm tổng>1 |

| Gas / setup | Đối soát |
|---|---|
| Base | Minimum base fee0,005gwei;200k gas,ETH2000USD ⇒ execution0,002USD; thêmL1 security và priority. Không có mặc định mọi swap<0,001USD |
| Arbitrum | Phí child-chain execution + parent-chain data; phải estimate đúng calldata/route, không lấy phí transfer làm phí arb |
| Solana | 5000lamports/signature;SOL100USD ⇒0,0005USD base fee/signature. Thêm priority, tip, account rent và route fees; failed included tx vẫn trả fee |
| Jito | Minimum bundle tip1000lamports không phải winning auction price; bundle non-inclusion khác on-chain failed transaction |
| Flash loans | AaveV3 fee khởi tạo0,05%, governance có thể sửa; query FLASHLOAN_PREMIUM_TOTAL và reserve enable trên deployment cụ thể |
| Capital nhỏ | Gas0,001/0,005/0,01USD trên Q3 tương ứng3,33/16,67/33,33bps |
| High turnover | 1440attempt/ngày×0,005USD=7,20USD/ngày trước gross profit; không thể cưỡng ép tần suất trên equity3,85 |

| Kế hoạch Python — đúng3 bước | File đề xuất / nội dung | PASS / NO_GO |
|---|---|---|
| 1. Cost feasibility, bounded read-only | Thêm `research/atomic_cost_model.py`, `research/atomic_shadow.py`; httpx/web3.py, Decimal/int. Allowlist native USDC/WETH và hai DEX cùng Base; kiểm chứng chainId, contract code/address, decimals, fee tiers. Batch RPC pin cùng block, deadline55s, concurrency/rate limits; không L2 orderbook/tick recorder | All-in setup+onboarding+gas reserve<=3,85; nếu flash loan chưa enable hoặc cần API/deployment vượt budget: NO_GO. Không hardcode gas0,001 hoặc3,85USD lànotional flashloan |
| 2. Route+executor simulation | Heuristic graph search, exact-size quotes, eth_call/estimateGas tại block đã biết. Local fork và Solidity executor tối thiểu; Python chỉ orchestration, không thể biến3HTTPswap thành atomic. Callback xác thực lender, repay toàn bộ, balance-delta guard, deadline, allowlist, nonce | Tests: dump50%, một leg revert, repay premium, gas spike, stale quote, wrong chain/decimals, duplicate, partial state, native inventory, failurecost. Stable principal không giảm ngoài modeled fee; không leftover debt hoặc volatile inventory |
| 3. Forward Shadow7ngày với config khóa | Chỉ lưu route decisions, block hash, quotes/fees và kết quả simulation. Đánh giá route ở block tiếp theo/độ trễ measured, không đếm repeats cùng state thành nhiều opportunities. Báo observed route survival và giả định inclusion riêng | Ít nhất100independent opportunity episodes và lower95% mean net theo day blocks>0 sau costs conservative; nếu thiếu hoặc fail: NO_GO. Đây chỉ gate xem xét canary, không proof live inclusion/EV. Shadow stop nếu cumulative modeled debit>=0,05; không tự reset |

| Vai trò8agents | Phân công |
|---|---|
| TIDAL | Pool/route discovery trong allowlist |
| NORO | Exact output, loan premium, balance-delta profitability |
| LUMEN | Offline đọc ABI/docs/protocol changes, kiểm chứng nguồn; không sentiment để quyết định arb |
| ZEPHR | GasL1/L2, priority/tip, fee tiers, stale-state buffer |
| RUNE | Principal preservation, external fee cap, contract/chain/token allowlist, kill0,05 |
| OKAPI | Chọn Q tối ưu sau impact; phân biệt borrowed notional với own gas budget |
| VESKA | Sau này chỉ signer với authorization bound chainId/to/calldataHash/value/maxFee/minProfit/deadline/nonce; HMAC nội bộ không thay Ethereum signature |
| MARIN | Receipt/state reconciliation, gas thật, stable delta, no residual debt/inventory |

| Chỉ thị kiến trúc | Điều kiện |
|---|---|
| Quorum | Giữ76,8%/70% và TokenAuthority; missing/false input không được nâng score để bắt cơ hội |
| Critical path | Heuristic/int math đã kiểm thử; LLM ngoài transaction path. Tính marginal AI cost, không gọi paid8LLMs cho edge vài phần nghìnUSD |
| Execution | Không mainnet trong thử nghiệm. Có guard trong contract, không chỉ assert trong Python. Không sandwich/front-run để ép giá người khác; chỉ cycle/backrun hợp lệ |
| Nới vốn | Tăng vốn chỉ tăng số attempts/setup capacity, không tự tạo alpha hoặc thắng ordering. Không có con số vốn khiến dự án lập tức có lợi thế cạnh tranh |
| Edge cạnh tranh cần đo | Exact simulation accuracy, ít failed sends, niche route coverage, inclusion access; không phải sốagents |

| Nguồn chính thức | Link |
|---|---|
| Binance filter API | https://api.binance.com/api/v3/exchangeInfo |
| Binance FDUSD fee update2026 | https://www.binance.com/en/support/announcement/detail/4856a6d4e4014d4e8a5a29ec5fb44857 |
| Polymarket market metadata | https://docs.polymarket.com/api-reference/markets/get-clob-market-info |
| Polymarket fees | https://docs.polymarket.com/trading/fees |
| Polymarket split/merge | https://docs.polymarket.com/concepts/positions-tokens |
| Kalshi fractional quantities | https://docs.kalshi.com/getting_started/fixed_point_migration |
| Kalshi fee rounding | https://docs.kalshi.com/getting_started/fee_rounding |
| Azuro minimum | https://gem.azuro.org/hub/apps/sdk/data-hooks/useBetCalculation |
| Azuro cashout | https://gem.azuro.org/hub/apps/guides/cashout/use-cashout |
| Base fees | https://docs.base.org/specifications/transactions/network-fees |
| Arbitrum fees | https://docs.arbitrum.io/how-arbitrum-works/deep-dives/gas-and-fees |
| Solana fees | https://solana.com/docs/core/fees/fee-structure |
| Jito bundles | https://docs.jito.wtf/lowlatencytxnsend/ |
| Aave flash loan | https://aave.com/docs/aave-v3/guides/flash-loans |
| Flashbots bundle semantics | https://docs.flashbots.net/flashbots-mev-share/searchers/understanding-bundles |
