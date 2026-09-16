# DOGE Flush: phán quyết và đặc tả Regime-Gated Shadow

Ngày: 15/09/2026. Đầu ra nghiên cứu để kỹ sư triển khai; tài liệu này không thay đổi code giao dịch hoặc cấp quyền Mainnet.

## 1. Phán quyết

- Vốn 3,85 USDT: chỉ tiếp tục Shadow có kiểm soát; chưa có bằng chứng cho phép scalping bằng tiền thật. Tần suất giao dịch là kết quả của bộ lọc, không phải mục tiêu phải đạt.
- Spot–Perp carry không đủ vốn tại 3,85 USDT. Với hai chân cùng notional 5 USDT, vốn lý thuyết là 10 USDT ở Futures 1x hoặc 7,5 USDT ở 2x, chưa tính phí, buffer và filters từng symbol. Không xem 10 USDT là mức bảo đảm vận hành an toàn.
- Khi đủ vốn dự phòng, carry delta-neutral phù hợp hơn với ưu tiên giảm rủi ro định hướng; lợi nhuận tuyệt đối rất nhỏ, funding có thể đảo dấu. Basis, isolated liquidation, ADL và mất một chân vẫn có thể gây lỗ.
- Không thể suy ra xác suất sống sót của hai phương án từ ba giao dịch. Không có bộ lọc EMA/ADX nào loại bỏ chắc chắn mọi cú rơi.

| Đối soát | Kết quả |
|---|---|
| Baseline TP net | 39,67 − 4 = 35,67 bps |
| Baseline SL net | −20,44 − 9 = −29,44 bps |
| p hòa vốn, chỉ TP/SL | 29,44 / (35,67 + 29,44) = 45,22% |
| 2 TP / 1 SL | Trung bình 13,9667 bps; tổng khoảng +0,02108 USDT nếu mỗi lệnh N=5,03 |
| Độ tin cậy của 2/3 thắng | Wilson 95% khoảng 20,8–93,9%, với giả định Bernoulli độc lập; chưa chứng minh edge |
| Một SL, N=5,03 | Khoảng −0,014808 USDT; ba SL khoảng −0,044425 |
| Equity 3,8032 | Lỗ 0,0468 < 0,05 USDT; riêng con số này chưa chứng minh kill đã chạm |
| Tỷ trọng chi phí SL | 9/20,44 = 44,0% khoảng SL giá; 9/29,44 = 30,6% tổng lỗ |

Carry minh họa, không phải dự báo: N=5 USDT mỗi chân, tổng vốn E=10 USDT, funding APR không đổi 15–40%, giữ 7 ngày, basis không đổi. Futures maker vào/taker ra 7 bps; Spot 7,5–10 bps mỗi chiều: tổng vòng hai chân 22–27 bps trên N. Funding gross 0,01438–0,03836 USDT; trừ phí còn khoảng 0,0009–0,0274 USDT, tương đương 0,009–0,274% E. Chưa trừ slippage, borrowing hoặc sai lệch hedge. Thời gian hòa phí khoảng 2,0–6,6 ngày. Funding APR trên N không phải APR trên tổng vốn E.

## 2. Sai lệch cần sửa trước khi tối ưu

Đã kiểm tra phiên bản hiện tại của `research/flush_shadow.py` và chạy 16 tests Flush đang có:

1. `update_cycle()` trả HALTED trước khi quản lý/đóng vị thế khi kill kích hoạt. Reproduction trong bộ nhớ: vẫn còn position, không có trade settled. HALTED không đồng nghĩa FLAT.
2. `get_liquidation_equity()` dự phòng 7 bps commission nhưng thiếu 2 bps slippage thoát thị trường. Quy ước cash hiện tại khác quy ước cash mới ở mục 8; không trừ entry fee hai lần.
3. Khi primary BE đóng sớm, entry tiếp theo có thể gọi `open_position()` lên mọi branch, ghi đè position baseline chưa đóng. Không được dùng kết quả so sánh bị nhiễm này để chọn chiến lược.
4. Signal hiện tại dùng volume spike OR bounce, không có regime; OI được tải nhưng chưa tham gia quyết định. Consensus/agent values trong telemetry đang là số cố định, không phải bằng chứng đồng thuận thật.
5. REST lịch sử DOGE, cửa sổ 14:30–15:00 UTC ngày 15/09 (=21:30–22:00 Việt Nam), cho ATR14 SMA 1m min/median/max khoảng 12,40/25,31/31,82 bps, khác báo cáo 80–150 bps. Phải đối chiếu timestamp, phương pháp ATR và đơn vị; không dùng range cả phiên thay ATR. Đây là truy vấn độc lập, chưa liên kết với journal của phiên người dùng.

## 3. Hợp đồng dữ liệu và bất biến

- Không tải/ghi L2 hoặc tick stream. Bootstrap REST Klines: 250 nến 1m + 200 nến 15m, exchangeInfo; budget toàn batch <60 giây, quá hạn thì DATA_INVALID. Cache exchangeInfo và kiểm tra lại định kỳ.
- Dùng closeTime và server time để chọn nến đã đóng; không mặc định phần tử cuối luôn là nến mở. Refresh nến 1m khi đóng, 15m khi đóng; bookTicker REST 2 giây/lần chỉ giữ bộ nhớ. Chỉ lưu nến đóng, quyết định và fill/exit journal.
- BookTicker thiếu, crossed, hoặc age >5 giây: cấm entry. Không bỏ qua lỗi rồi dùng dữ liệu cũ như dữ liệu mới. Stale khi đang có vị thế phải chuyển trạng thái quản lý lỗi, không giả lập đã thoát.
- A là ATR14 Wilder 1m tại thời điểm signal, đơn vị giá; a=10000*A/entry là bps. Seed bằng SMA14 TR, sau đó ATR_t=(13*ATR_previous+TR_t)/14. Baseline SMA cũ phải gắn nhãn riêng.
- EMA20/50, ADX14 và DI dùng nến 15m đóng, Wilder smoothing cho ADX. Không dùng nến 15m chưa đóng cho quyết định lịch sử.
- 2x isolated tối đa, một position/symbol/portfolio, không averaging, không tăng size sau lỗ. Khớp quantity/price bằng Decimal và filters thực, không hardcode DOGE.
- Giữ nguyên TokenAuthority, 8-agent, quorum 76,8% và veto hiện hữu 70%; không bịa điểm hoặc thay missing signal bằng điểm thuận lợi. Missing input khiến gate cần input đó không được cấp quyền.
- Mặc định SHADOW; spec không thêm live arming. Quản lý thoát khẩn cấp không bị chặn bởi gate cấp quyền mở lệnh.

## 4. Regime: tham số giả thuyết khóa trước kiểm thử

Đặt slope=(EMA20_t−EMA20_(t−3))/(3*ATR15_t), dùng nến đóng.

```text
DOWN = C15 < EMA20 < EMA50 AND slope <= -0.10
       AND ADX14 >= 25 AND DI_minus > DI_plus
UP   = C15 > EMA20 > EMA50 AND slope >= +0.10
       AND ADX14 >= 25 AND DI_plus > DI_minus
RANGE = ADX14 <= 20 AND abs(slope) <= 0.05
        AND abs(EMA20-EMA50) <= 0.5*ATR15
otherwise = TRANSITION

FAST_DOWN = C1_t < min(L1[t-20:t]) - 0.2*A_previous
            OR (C1_t-C1_(t-3)) <= -1.5*A_previous
```

Slice `[t-20:t]` loại nến t. FAST_DOWN luôn hủy pending Long, cấm Long mới. DOWN cho phép tìm Short retest; RANGE cho phép tìm Long reclaim; UP/TRANSITION không mở chiến lược mới trong phiên bản này. Không tự đảo Long sang Short khi stop.

Sau FAST_DOWN/DOWN, chỉ mở lại Long khi có hai nến 15m đóng liên tiếp thỏa RANGE và không FAST_DOWN trong ba nến 1m gần nhất. Hysteresis chủ ý bỏ lỡ một số nhịp hồi. Nếu bộ lọc bỏ quá nhiều lệnh, ghi nhận kết quả, không tự hạ ngưỡng.

## 5. Entry hai chiều

### Long mean reversion: chỉ RANGE, không FAST_DOWN

Tại nến signal t, support S=min(L[t−20:t]), Vref=median(V[t−20:t]); dùng A_previous trước nến signal cho độ lớn cú flush.

```text
flush = L_t <= S - 0.1*A_previous
reclaim = C_t > S AND (C_t-L_t)/(H_t-L_t) >= 0.60
volume = V_t >= 1.5*Vref
candidate = flush AND reclaim AND volume
confirm tại t+1 = C_(t+1) > H_t AND L_(t+1) >= L_t
```

H=L thì signal false. Nếu không confirm ngay t+1, bỏ event. Sau confirm mới đặt GTX BUY tại bid làm tròn xuống tick; chỉ đặt khi |entry−S| <= 0,5*A_signal, spread <=3 bps và reward room đủ. Không đuổi giá. TTL60 giây, event_id=(symbol,signal_closeTime,LONG) chỉ dùng một lần.

### Short momentum: chỉ DOWN đã xác nhận, breakout rồi retest

```text
S = min(L[t-20:t])
break = C_t < S - 0.2*A_previous
        AND (C_t-L_t)/(H_t-L_t) <= 0.30
        AND V_t >= 1.5*median(V[t-20:t])
trong tối đa 3 nến sau break, tìm retest r:
    S-0.1*A_signal <= H_r <= S+0.3*A_signal
    AND C_r < S-0.1*A_signal AND C_r < O_r
```

Sau retest đóng, đặt GTX SELL tại ask làm tròn lên tick nếu |entry−S| <=0,5*A_signal, spread <=3 bps và reward room đủ. Không retest thì không giao dịch. TTL60 giây. Hủy pending khi DOWN mất hiệu lực hoặc giá vượt structural stop. Không diễn giải mọi FAST_DOWN thành quyền Short.

## 6. SL, TP, trailing và khả năng chịu lỗ

Tất cả mức tính từ entry dự kiến, kiểm tra lại sau fill; A_signal đóng băng cho position, không nới SL bằng ATR tăng sau entry.

```text
Long structural_stop = min(L_signal,L_confirm) - 0.1*A_signal
Short structural_stop = max(H_retest,S) + 0.1*A_signal
s_structure = abs(entry-structural_stop)/entry*10000
s = max(s_structure, 0.8*a, 3*tick_bps)
T_long = max(25, 1.5*s)       # gross bps
T_short = max(25, 2.0*s)
```

Stop đặt xa entry theo s: Long làm tròn xuống, Short làm tròn lên; TP làm tròn theo giá maker hợp lệ rồi tính lại payoff. Reward room Long là khoảng cách tới mức thấp hơn trong EMA20 1m và đỉnh20 nến trước signal, chỉ nếu mức đó trên entry. Reward room Short là khoảng cách tới low thấp nhất của 60 nến đóng trước breakout, chỉ nếu dưới entry. Không đủ dữ liệu/mức ở sai phía hoặc room < T thì bỏ lệnh. Đây là quy tắc bảo thủ có thể làm không phát sinh trade.

Điều kiện payoff sơ bộ: `(T−4)/(s+9) >=1.2`. Điều này không chứng minh EV dương; EV còn phụ thuộc xác suất và phân phối fill/exit. ZEPHR không được xem target danh nghĩa là alpha đã đo.

### Budget trước entry

```text
floor = initial_equity - 0.05
remaining = max(0, liquidation_equity-floor)
risk_allowed = min(0.015, 0.004*liquidation_equity,
                   max(0,remaining-0.005))
q_min = ceil_to_step(max(minQty, minNotional/entry))
R = q_min*entry*(s + 2 + 5 + slip_stress_bps)/10000
BLOCK nếu R > risk_allowed, quantity invalid,
      hoặc initial_margin + entry_fee + R > available_equity
```

`slip_stress_bps=max(2, p99_slippage_đã_đối_soát)`; khi chưa có fills thật, 2 bps chỉ là giả định, thêm stress scenario 5 và 10 bps. Sau rounding phải tính R lại. Không thu hẹp structural SL hoặc tăng leverage để ép vượt gate.

Với N=5,03 và risk_allowed=0,015: s tối đa 20,82 bps. Nếu ATR=80–150 bps, s>=64–120 khiến một SL tốn khoảng 0,0367–0,0649 USDT: BLOCK. Ngay ATR khoảng25 bps, s>=20 đã gần hết budget; nhiều structural setup vẫn phải bỏ.

### Breakeven/trailing

Với market stop, b=2+5+2+1=10 bps gross chỉ bảo vệ khoảng +1 bps net trong giả định slippage2; không phải bảo đảm giá khớp.

```text
b = ceil_to_bps_tick(2+5+slip_stress_bps+1)
trigger = max(15, b + max(3*tick_bps, 0.3*a))
distance = max(3*tick_bps, 0.5*a)
MFE = max(MFE, favorable_executable_return_bps)
if MFE >= trigger:
    desired_protection = max(previous_protection, b, MFE-distance)
    chỉ nâng stop nếu còn cách executable bid/ask >=1 tick
```

Long nâng stop; Short hạ stop. Kiểm tra stop đang hiệu lực trước, sau đó mới cập nhật trailing áp dụng từ observation kế tiếp. Không dùng high/low cả nến để vừa nâng stop vừa giả định khớp stop trước đó. Không nới stop, không reset MFE. TP remains cố định sau fill. Timeout10 nến1m cho Long,15 cho Short, thoát market và tính taker/slippage.

Hai SL liên tiếp: cooldown30 phút và yêu cầu regime hợp lệ mới sau cooldown. Counter theo portfolio; BE/timeout có PnL<=0 không được gắn WIN. Kill là latch vĩnh viễn cho run; không tự reset sau cooldown.

## 7. State machine và vòng điều khiển

```text
FLAT -> PENDING_ENTRY -> OPEN -> EXIT_PENDING -> FLAT
bất kỳ trạng thái -> KILL_PENDING -> HALTED_FLAT
thoát lỗi -> UNWINDING_ERROR, giữ kill latch và tiếp tục quản lý

on_observation(data):
    reconcile_existing_orders_and_fills()
    update_cash_and_liquidation_equity()
    if loss_from_run_initial >= 0.05:
        persist_kill_latch()
    if kill_latched:
        cancel_pending_entries()
        request_reduce_only_market_exit_for_remaining_qty()
        reconcile_until_flat_or_report_unwinding_error()
        return  # chỉ HALTED_FLAT khi đã xác nhận quantity=0

    manage_existing_stop_TP_timeout()  # luôn trước xét entry
    update_trailing_for_next_observation()
    if stale_or_invalid_data: block_new_entry(); return
    if not new_closed_1m_bar: return
    update_indicators_and_regime_without_lookahead()
    invalidate_pending_entries_if_regime_changed()
    if position_or_pending_or_cooldown: return
    if FAST_DOWN: prohibit_long()
    if DOWN: candidate = confirmed_short_retest()
    elif RANGE and long_reenabled: candidate = confirmed_long_reclaim()
    else: return
    if candidate and filters_and_risk_pass and authentic_swarm_authorizes:
        place_shadow_GTX(candidate)
```

Kill threshold là tín hiệu bắt đầu cưỡng chế thoát, không bảo đảm mức lỗ tối đa chính xác5cent khi gap/disconnection. State phải lưu initial equity, cash, orders, position, kill reason/timestamp; restart đối soát trước, không tự xóa loss. Shadow phải settle exit tại giá mô phỏng bảo thủ, không chỉ đổi nhãn.

Mỗi branch có ledger, order và position riêng. So sánh exit-policy bằng common-entry cohorts: chỉ mở cohort mới khi mọi branch đã flat; tuyệt đối không ghi đè branch đang mở. So sánh strategy/regime riêng phải có portfolio độc lập và báo cả coverage, không ép cùng fills giả tạo.

## 8. Hạch toán chính xác

Ký hiệu d=+1 Long, −1 Short; quantity q, entry E, exit X; fee rate dạng thập phân, không phải bps:

`PnL = d*q*(X−E) − q*E*f_entry − q*X*f_exit + funding_cashflow`.

- Entry fill: cash trừ entry commission một lần. Exit: cash cộng gross PnL, trừ exit commission; funding hạch toán khi phát sinh. Fees lấy actual role khi có fill thật, không gán mọi exit là maker.
- TP maker: f_entry=f_exit=0,0002. Stop/timeout/kill: f_entry=0,0002, f_exit=0,0005.
- Mô phỏng SL Long: X=floor_tick(min(bid,stop)*(1−slippage/10000)); Short: X=ceil_tick(max(ask,stop)*(1+slippage/10000)). Timeout/kill dùng executable quote, không dùng stop chưa chạm để tạo giá xấu giả.
- Slippage đã đi vào X: không trừ thêm lần nữa trong fees.
- Liquidation equity = cash + d*q*(X_liq−E) − q*X_liq*f_taker. Entry fee đã nằm trong cash, không trừ lại. Theo d dùng bid/ask và slippage stress cho X_liq.
- BE net có thể âm khi gap lớn hơn cushion. Báo measured PnL, không ghi cứng +1 bps cho mọi PROTECT.

Với payoff hai kết quả gần đúng:

`EV_bps=p*(T−4)−(1−p)*(s+9)`;
`p_BE=(s+9)/(T+s+5)`.

Nếu T=25,s=20,44: p_BE=29,44/50,44=58,37%. Nếu toàn bộ exits đều maker4bps thì 24,44/45,44=53,79%, nhưng không phù hợp stop market đang dùng.

Với nhiều exit types: `EV = sum_k p_k * mean_net_bps_k`; phải bao gồm TP, SL, trailing, timeout, kill. `EV_USDT/hour = filled_trades/hour * mean(PnL_USDT/trade)` với cùng cửa sổ, gồm cả giờ không trade; variable notional dùng PnL từng trade. Không nhân riêng p với một TP cố định sau khi bật trailing.

## 9. Giới hạn fill simulation và thiết kế đánh giá

- REST bookTicker2s không biết queue hoặc đường giá giữa polls. Shadow fills không phải bằng chứng maker thực tế đã khớp.
- Không fill ngay observation đặt lệnh. Chạy ít nhất hai kịch bản: touch optimistic và trade-through >=1 tick ở hai observations kế tiếp bảo thủ. Gắn nhãn giả định, không gọi đây là fill chắc chắn.
- Nếu dùng nến1m và cả stop/TP nằm trong cùng nến, đánh dấu ambiguous và dùng SL-first cho kết quả bảo thủ. Không sử dụng high/low trước entry trong cùng nến để thoát position vừa mở.
- Ghi journal theo event: regime, closed-bar timestamps, ATR đơn vị giá/bps, filters, veto reason, entry/exit role, fees, slippage, MFE/MAE quan sát, exit reason, equity, hypothesis_id. Không ghi stream tick.
- Premature stopout: sau BE, theo dõi original TP/SL tới deadline ban đầu của position; không cấp thêm2h từ lúc BE. Báo `P(original TP before original SL | BE exit)` và ambiguous share. Win rate tăng chưa chắc EV tăng.
- Khóa baseline và một candidate trước holdout; không tối ưu hàng chục cấu hình rồi chỉ công bố winner. Chia theo thời gian, purge tối thiểu holding horizon, không trộn future bars vào indicators.
- Mốc review đề xuất: >=200 fills OOS trên >=20 ngày, >=30 fills cho mỗi regime được tuyên bố hỗ trợ. Đây là điều kiện review, không là chứng minh thống kê đủ mạnh tự động.
- Báo CI95 bằng block bootstrap theo ngày (2.000 resamples) cho net/hour và mean net/trade, gồm những ngày không trade. Chỉ xem xét triển khai tiếp khi lower bound >0 trong kịch bản phí/fill bảo thủ và stress hợp lý; nếu CI chứa0: chưa chứng minh, không tự tăng vốn.
- Shadow có kill độc lập từng portfolio, không hồi sinh bằng reset equity. Holdout nghiên cứu dài hạn có thể đánh giá nhiều phiên vốn độc lập nhưng phải công bố tỷ lệ phiên chạm floor và không ghép chúng thành một đường vốn sống sót giả.

## 10. Checklist file cho Claude

| File | Thay đổi được đề xuất |
|---|---|
| research/flush_shadow.py | Ghép regime/risk/ledger; sửa kill settlement, branches độc lập, dùng side-aware exits; giữ baseline có nhãn |
| research/flush_regime.py (mới) | Pure functions ATR/EMA/ADX, trend gates, reclaim/retest, không I/O |
| research/flush_risk.py (mới) | Filters/rounding, structural risk, remaining loss budget, exact ledger helpers |
| tests/test_flush_shadow.py; tests/test_flush_shadow_invariants.py | Giữ regression hợp lệ; bổ sung bug reproductions, không sửa kỳ vọng để che lỗi |
| tests/test_flush_regime.py; tests/test_flush_risk.py (mới) | Fixtures deterministic, không mạng/không fake alpha |

Không sửa carry runtime, không thay đổi threshold/global TokenAuthority. Tích hợp auth qua API hiện hữu sau khi kỹ sư đọc interfaces thực; không giả định tên method chưa kiểm tra.

### Acceptance tests bắt buộc

1. ATR/Wilder và ADX/DI khớp fixture tính độc lập; đúng bps, zero-range an toàn.
2. Loại nến chưa đóng theo closeTime; không future15m leakage.
3. DOWN/FAST_DOWN không thể sinh Long kể cả volume/bounce lớn.
4. Hysteresis cần đủ hai nến15m; transition chặn cả entry mới.
5. Long cần đồng thời flush/reclaim/volume/confirm; OR cũ không còn.
6. Short thiếu retest không entry; retest quá ba nến hết hiệu lực.
7. Pending bị hủy khi invalidation; event không vào lại nhiều lần.
8. Decimal quantity/price đáp ứng filters; rounding phải tính lại risk.
9. ATR80bps, N5,03, risk0,015 phải BLOCK; không co SL để lách.
10. Equity3,8032 flat chưa chạm kill0,05 nhưng risk budget entry phải bằng0 với reserve0,005.
11. Kill có position tạo exit/settlement rồi mới HALTED_FLAT.
12. Exit failure không báo FLAT; restart giữ latch và quản lý unwind.
13. Cash trừ entry fee đúng một lần; taker exit đúng5bps.
14. Slippage chỉ tính qua exit price; gap không được khớp giả tại stop.
15. Long/Short ledger đối xứng về dấu và executable side.
16. Liquidation equity có exit fee/slippage, không double entry fee.
17. Trailing chỉ siết stop; áp dụng observation tiếp theo; gap BE có thể âm.
18. Timeout/kill không được gán maker commission.
19. Một branch BE đóng không ghi đè baseline còn mở.
20. Fill không xảy ra từ quote trước đặt lệnh; intrabar ambiguity xử lý bảo thủ.
21. Stale/missing dữ liệu không tạo entry hoặc fake fill.
22. Quorum/veto/auth thật không bị bypass; missing vote không thành84%.
23. Hai SL kích cooldown nhưng không reset kill/global initial equity.
24. Không module nào tạo L2/tick stream files hoặc gửi Mainnet order.

Chạy toàn bộ `python -m pytest -q` sau triển khai; yêu cầu mọi regression hợp lệ pass và các test mới kiểm chứng hành vi thật. Test hiện tại16 Flush pass không chứng minh các invariants mới đã đúng.

## Nguồn và phạm vi chứng cứ

- Binance USD-M fee calculation: https://www.binance.com/en/support/faq/detail/360033544231
- Binance Spot fee calculation: https://www.binance.com/en/support/faq/detail/e85d6e703b874674840122196b89780a
- Binance Funding mechanics: https://www.binance.com/en/support/faq/detail/360033525031
- Binance exchange filters: https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information
- Historical kline API đã dùng để đối soát ATR: https://fapi.binance.com/fapi/v1/klines ; symbol DOGEUSDT, interval1m, cửa sổ được ghi ở mục2.

Fee tier/discount/filter phải đọc lại từ tài khoản/sàn khi triển khai. Các ngưỡng EMA/ADX/ATR và risk cap trong tài liệu là thiết kế thử nghiệm có thể bị bác bỏ; không phải kết quả đã chứng minh sinh lời.
