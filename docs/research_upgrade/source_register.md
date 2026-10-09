# Source register — 07/10/2026 (q)

Ngày kiểm tra: 07/10/2026. Chỉ giữ nguồn trả lời nút thắt hiện tại (đo khớp/độ trễ/đại diện của demo). Nguồn nền khác: `prompts/NGUON_NANG_CAP_2026-10-07.md`.

| # | URL gốc (phát hành / sửa đổi) | Kết luận dùng được · giới hạn | Vấn đề trong code | Thử nghiệm bác bỏ được |
|---|---|---|---|---|
| 1 | https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info (sửa 07/10/2026) | REST testnet USD-M = `https://demo-fapi.binance.com`, WS `wss://demo-fstream.binance.com`. Trang New Order lại ghi `testnet.binancefuture.com` → tài liệu không thống nhất. | Runner lấy tín hiệu từ WS mainnet, khớp lệnh ở demo-fapi. | Log `basis_bps` mỗi lệnh; nếu \|basis\| median < 2 bps trên ≥ 200 lệnh/attempt thì bác bỏ giả thuyết "basis làm nhiễu P&L demo". |
| 2 | https://developers.binance.com/docs/binance-spot-api-docs/demo-mode/general-info (sửa 07/10/2026; trang Spot) | "Testnet's prices and order books are independent from the live exchange"; demo mode "similar", "realistic market data is not equal to real". Áp sang futures là suy luận (HYPOTHESIS). | Kết quả demo đang được dùng như bằng chứng chiến lược. | So `markout_bps` theo venue mid với markout theo mainnet mid cùng thời điểm; chênh có hệ thống → demo không đại diện. |
| 3 | https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/user-data-streams (sửa 07/10/2026); https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/New-Order | ORDER_TRADE_UPDATE có `E` (event) và `T` (transaction time, matching engine); New Order trả `updateTime`. Schema trường con `o` không render được khi kiểm tra → chưa xác nhận tên `l/L/m/n`. | Fill hiện phát hiện bằng polling REST 0.1 s → `fill_wait_ms` có sai số ≤ 0.1 s + 1 RTT. | Bước sau: ghi `T` từ user stream; nếu chênh (poll − T) p50 > 150 ms thì chuyển phát hiện fill sang user stream. |
| 4 | https://hftbacktest.readthedocs.io/en/latest/order_fill.html; https://hftbacktest.readthedocs.io/en/latest/latency_models.html (bản "latest", © 2024; repo đã có bridge) | Không mô phỏng market impact; fill ở cùng giá chỉ khi đầu hàng đợi; queue model ước lượng từ MBP. Latency tách feed / order entry / order response; mô hình tốt nhất dựng từ `req_ts, exch_ts, resp_ts`. | `latency_ms` gộp mọi giai đoạn; replay hiện không có latency đo thực. | Dùng `ack_ms` và `fill_wait_ms` từ order log làm IntpOrderLatency; so tỉ lệ khớp replay với tỉ lệ FILLED thực trong order log. |
| 5 | Bailey & López de Prado, Deflated Sharpe Ratio (2014) https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf | Phải ghi số cấu hình đã thử; 34 lệnh không đủ cho DSR/PBO. | 10+ vòng chỉnh ngưỡng trên cùng ~34 lệnh, không register. | Experiment register (file kế bên); DSR chỉ khi có ≥ vài trăm lệnh cùng policy id. |

## Bổ sung 08/10/2026 (r) — nút thắt: vòng đời lệnh thoát/stop
| # | URL gốc (ngày) | Kết luận dùng được · giới hạn | Vấn đề trong code | Thử nghiệm bác bỏ được |
|---|---|---|---|---|
| 6 | https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/New-Algo-Order (không ghi ngày sửa) | `closePosition` không đi cùng `quantity`/`reduceOnly`; `workingType` mặc định CONTRACT_PRICE; `priceProtect` mặc định false. Không có câu nào nói "chỉ một closePosition stop mỗi chiều" — đó là quan sát (−4130) của chính ledger. | Stop dùng closePosition + CONTRACT_PRICE; dời stop phải huỷ trước → có khoảng không bảo vệ. | `stop_events.naked_ms` p95 > 500 ms trên ≥ 20 lần dời → xét stop reduceOnly+quantity (đặt mới trước, huỷ sau) — cần thử trên demo vì tài liệu không xác nhận cùng tồn tại (−2022 có thể xảy ra). |
| 7 | https://developers.binance.com/docs/derivatives/usds-margined-futures/error-code (sửa 07/10/2026) | −2021 "Order would immediately trigger"; −5022 GTX reject; −2011/−2013 cancel/không tồn tại; −4045 max stop; −4137/−4138 ràng buộc closePosition. **−4130 không có trong trang**. | Code cũ chỉ ghi tên lớp lỗi. | Nếu `stop_events` cho thấy mã ≠ −2021 ở đa số lần RATCHET hỏng thì bác bỏ giả thuyết CONTRACT_PRICE. |
| 8 | Lehalle & Mounjid, https://arxiv.org/abs/1610.00261 (v1 02/10/2016, v4 15/03/2018; bài nền tảng) | Lệnh limit bị chọn ngược: dễ khớp khi giá sắp đi ngược; độ trễ làm giảm giá trị của tín hiệu imbalance vì không kịp huỷ/đặt lại. | Thoát maker bám touch sau khi giá đã quay đầu = đúng tình huống bị chọn ngược; re-peg 0.5 s. | Replay R2: so chase / bounded / stop trên cùng sự kiện; markout sau thoát trên demo. |
| 9 | hftbacktest fill model (đã ghi #4) | Không mô phỏng impact; fill phụ thuộc mô hình hàng đợi. | Replay R2 không có dữ liệu trade → hai mô hình fill (bảo thủ / lạc quan) làm khoảng. | — |

## Bổ sung 08/10/2026 (v) — nút thắt: GTX không khớp, funnel, lợi nhuận/cơ hội
Ngày truy cập: 08/10/2026. Kết quả cổ phiếu Mỹ/NYSE/Nasdaq không phải alpha crypto sẵn có; chỉ dùng làm giả thuyết và khung đo.

| # | Nguồn (công bố / sửa) | Kết luận dùng được · giới hạn | Vấn đề trong code | Thử nghiệm bác bỏ được |
|---|---|---|---|---|
| 10 | Binance USD-M Trade REST (trang gộp Modify/New/Cancel/Query Order; không ghi ngày sửa) https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/Modify-Order | Modify: chỉ LIMIT; "modified orders will be reordered in the match queue"; GTX bị huỷ nếu giá mới khớp ngay. New Order: `newClientOrderId` duy nhất **giữa các lệnh đang mở**; trang không định nghĩa GTX/−5022. Cancel trả `executedQty`/`cumQty`. Query: lệnh CANCELED/EXPIRED không có fill quá 3 ngày không còn tìm được. | Peg = huỷ + đặt mới (mất ưu tiên như modify). Exception sau POST để lệnh mồ côi (V5). | Không dùng modify để "giữ queue". Nếu `executedQty` của cancel ≠ query sau cancel trên ≥ 20 lần huỷ có fill → dùng giá trị cancel làm nguồn chính. |
| 11 | Binance USD-M General Info (sửa 07/10/2026) https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info | Giới hạn theo IP; con số REQUEST_WEIGHT lấy từ `exchangeInfo.rateLimits`; header `X-MBX-USED-WEIGHT-(n)(unit)`; 429 → 418 ban 2 phút–3 ngày. | Client tự phanh ở 1 000 weight/phút; nhịp REST cũ phụ thuộc UI focus. | Ghi `X-MBX-USED-WEIGHT-1M` p95 mỗi phút sau V4; nếu > 900 thì giảm nhịp engine phẳng. |
| 12 | Cont & Kukanov, Optimal order placement in limit order markets, https://arxiv.org/abs/1210.1625 (v1 05/10/2012, v4 23/11/2014) | Chia limit/market là bài toán tối ưu lồi theo queue, order flow, phí; lời giải phụ thuộc xác suất khớp ước lượng được. | TTL/peg chọn theo kinh nghiệm, không theo xác suất khớp × chi phí. | Replay V6 so JOIN/PEG/TTL/IOC/SKIP trên mọi cơ hội. |
| 13 | Lehalle & Mounjid, https://arxiv.org/abs/1610.00261 (đã ghi #8) | Lệnh limit dễ khớp khi giá sắp đi ngược; chờ lâu hơn tăng khớp nhưng tăng chọn ngược; độ trễ làm mất lợi thế huỷ/đặt lại. | TTL dài / IOC tăng fill. | Replay V6: TTL 5/10 s và IOC đều có net/cơ hội thấp hơn TTL 3 s (holdout). |
| 14 | Cont, Kukanov & Stoikov, https://arxiv.org/abs/1011.6402 (v1 29/11/2010, v3 13/04/2011; J. Fin. Econometrics) · Gould & Bonart, https://arxiv.org/abs/1512.03492 (11/12/2015) | OFI ở best quote giải thích biến động ngắn hạn (tuyến tính, dốc ∝ 1/độ sâu); queue imbalance dự báo hướng tick kế tiếp, mạnh hơn ở cổ phiếu tick lớn. | `ofi_10s` dùng như tín hiệu, chưa hiệu chuẩn theo horizon trên crypto. | Cần trade prints + depth ghi đồng thời; hiệu chuẩn OFI→markout 1/5/30 s trên holdout trước khi đổi ngưỡng. |
| 15 | hftbacktest (MIT; ~1 038 commit) https://hftbacktest.readthedocs.io/en/latest/order_fill.html | Queue model: RiskAverse (chỉ trade đẩy hàng), ProbQueue (power/log, f(0)=0, f(1)=1); không mô phỏng impact. | Replay V6 không có trade → `clear` (còn bảo thủ hơn RiskAverse), `prop` (ProbQueue tuyến tính), `front` (cận trên). | Khi có aggTrade: chạy hftbacktest RiskAverse + PowerProb(n=2,3) trên cùng cơ hội; nếu xếp hạng policy đổi so với V6 thì V6 không đủ tin. |
| 16 | Lee & Lee, Spread-Gated Hawkes-Flocking, https://arxiv.org/abs/2609.36631 (v1 29/09/2026, preprint, chưa thấy phản biện) | Mô hình best quote Hawkes có kích thích chéo phụ thuộc spread; size lệnh tối ưu dựa vào xác suất khớp **cho từ bên ngoài**. | — | Không prototype: không giải quyết nút thắt (cần xác suất khớp, chính là thứ thiếu). |
| 17 | ReLOBGen, https://arxiv.org/abs/2609.35867 | **Không kiểm tra được** (proxy trả HTTP 429). Không dùng. | — | — |

## Bổ sung 08/10/2026 (w)
| # | Nguồn | Kết luận dùng được · giới hạn | Áp dụng |
|---|---|---|---|
| 18 | Binance USD-M General Info, mục HTTP 503 (sửa 07/10/2026) https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info | 503 "Unknown error" = trạng thái không rõ, kiểm tra trước khi gửi lại; 503 "Service Unavailable" và −1008 = thất bại, retry backoff 200/400/800 ms (3–5 lần); cancel / reduce-only miễn −1008. | W4 |
| 19 | Biais, Foucault & Moinas, Equilibrium Fast Trading (SSRN 2024360; JFE) | Đầu tư công nghệ nhanh tạo chọn ngược và dư thừa đầu tư; không phải quy tắc khớp taker theo alpha/phí. Form ghi nhầm "Salanié". | Chỉ bối cảnh |

## Bổ sung 08/10/2026 (x)
| # | Nguồn | Kết luận dùng được · giới hạn | Áp dụng |
|---|---|---|---|
| 20 | Binance public data, USD-M daily aggTrades https://data.binance.vision/data/futures/um/daily/aggTrades/ (SOLUSDT, DOGEUSDT 2026-10-01..07, checksum khớp) | Mọi print có giá/khối lượng/phía taker → tính KER, VWAP 60 s, CVD giống engine; không có book/queue. | Replay X1/X2 |
| 21 | Kaufman, Efficiency Ratio (Smarter Trading, 1995; định nghĩa đã dùng trong `msig.ker`) | ER = \|ΔP\| / Σ\|Δp\|; chỉ đo độ thẳng của đường giá, không dự báo hướng. | Ngưỡng KER là gate thống kê, cần holdout |

## Bổ sung 08/10/2026 (y)
| # | Nguồn | Kết luận dùng được · giới hạn | Áp dụng |
|---|---|---|---|
| 22 | Thorp (2007), Kelly criterion (Handbook of Asset and Liability Management) | f* = μ/σ²; μ ước lượng sai → cược quá mức; phân số Kelly giảm rủi ro | Y1: f* < 0 trên ledger |
| 23 | Vince (1992), The Mathematics of Money Management | Optimal f, chuỗi thua và rủi ro cháy | Y2 |
| 24 | Almgren & Chriss (2000), Optimal Execution of Portfolio Transactions, J. Risk | Tác động tạm thời/vĩnh viễn theo size/độ sâu | Y3 |
| 25 | Binance USD-M public `/fapi/v1/depth`, `/fapi/v1/aggTrades` (demo-fapi + fapi, 08/10 ~16:5x) | Độ sâu hiển thị & dòng taker 30 phút | Y3 |

## Bổ sung 08/10/2026 (aa) — chi tiết & URL trong `research_alpha_after_fees_2026-10-08.md`
| # | Nguồn | Kết luận dùng được · giới hạn | Áp dụng |
|---|---|---|---|
| 26 | Albers et al. (arXiv 2502.18625), vi cấu trúc Binance BTC perp | Taker theo imbalance ≈ +1 bp gross/RT ở độ trễ ~2 ms; âm sau phí taker 1.5 bps | AA1: khung giây không đủ edge |
| 27 | Nghiên cứu lệnh maker live BTC perp (233 k lệnh; ghi chú `microstructure_ofi_mm.md`) | Fill maker bị chọn bất lợi ~−0.8 bp sau 5 s | AA1 |
| 28 | Zarattini, Pagani & Barbon (2025), trend-following ensemble crypto | Breakout daily top-20, Sharpe net > 1.5 với phí 10–50 bps (bảng chi tiết sau paywall) | AA3 tiền định |
| 29 | Bailey & López de Prado (2012, 2014) PSR / Deflated Sharpe | Phạt số cấu hình đã thử | `core/fast_eval.py` |
| 30 | Wald (1945) SPRT; Johari et al. (2017) mixture SPRT | Dừng sớm có kiểm soát sai lầm | `core/fast_eval.py`, `tools/edge_check.py` |
| 31 | BIS WP "Crypto carry" (2023); He, Manela, Ross & von Wachter "Fundamentals of Perpetual Futures" | Carry/funding dự báo rủi ro 1–3 tháng, không dự báo hướng ngắn hạn | AA4 |
| 32 | data.binance.vision USD-M monthly klines 1h + fundingRate (BTC/SOL/DOGE 2022-01..2026-09) | OHLCV + taker buy volume; bookTicker USD-M dừng sau 2024-03-30 | AA2–AA4 |

## Bổ sung 09/10/2026 (bb)
| # | Nguồn | Kết luận dùng được · giới hạn | Áp dụng |
|---|---|---|---|
| 33 | Bailey, Borwein, López de Prado & Zhu (2014/2017), "The Probability of Backtest Overfitting", J. Computational Finance | CSCV: PBO = tỉ lệ split mà cấu hình tốt nhất IS rơi nửa dưới OOS | `core/fast_eval.pbo_cscv` |
| 34 | Moskowitz, Ooi & Pedersen (2012), "Time Series Momentum", JFE | TSMOM đa lookback + vol scaling | `core/trend_lane.py` |
| 35 | Python asyncio event loop (CPython `BaseEventLoop._run_once`, `_clock_resolution`) | Loop thời gian ảo: thời gian tương đối, bỏ timer huỷ ở đầu heap | `tools/engine_replay.py` |
| 36 | data.binance.vision USD-M daily aggTrades SOLUSDT 2026-09-08 + `data/l2` (bookTicker + depth diff đã ghi) | Nguồn `l2` sổ lệnh thật cho replay | BB2 |

## Bổ sung 09/10/2026 (cc)
| # | Nguồn | Kết luận dùng được · giới hạn | Áp dụng |
|---|---|---|---|
| 37 | Brier (1950), "Verification of forecasts expressed in terms of probability", Monthly Weather Review | Điểm Brier cho dự báo xác suất | `core/swarm_learner.py` |
| 38 | Thompson (1933); Russo et al. (2018), "A Tutorial on Thompson Sampling" | Lấy mẫu posterior cho multi-armed bandit | `core/param_bandit.py` |
| 39 | Repo skill `agent-memory-dreaming` (bài học nguyên tử, hợp nhất, nâng luật > 3 lần) và `groktopus-swarm-consensus` (3 agent định hướng, 5 cổng an toàn) | Kiến trúc dreaming; chỉ học trọng số NORO/LUMEN/TIDAL | `core/dreaming_daemon.py`, `core/autonomy.py` |
