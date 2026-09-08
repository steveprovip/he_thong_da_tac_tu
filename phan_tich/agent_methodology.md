# Methodology profile — 8 agent GrokTOPUS (khung 4 tầng của investment-masters)

Mỗi agent trả lời 4 tầng: (1) nguồn gốc — file:dòng, (2) principle — vì sao
signal có ý nghĩa vi mô, (3) risk control — điều kiện bị chặn, (4) quy tắc
đo được. Trích từ core/groktopus_swarm.py + core/consensus_engine.py.
Trọng số hiện tại (consensus_engine.py:12-13): alpha 60% / gate 40%.
Kiểm định của tầng (2) chưa có — sẽ đến từ equity-curve từng agent khi
probe ghi đủ phiếu (dissent_score.py tự nhận diện).

## NORO — mean-reversion (weight .20)
1. Nguồn: groktopus_swarm.py:44 — `tanh(-vwap_z/(2·max(atr_ratio,.25)) + .25·momentum)`
2. Principle: giá lệch khỏi VWAP quá xa có xu hướng kéo về; atr_ratio ở mẫu
   số tự động nới lỏng tín hiệu khi volatility cao (tránh bắt dao rơi trong
   biến động bất thường). Momentum .25 làm mượt chống fake-revert.
3. Risk: bị veto bởi toàn bộ gate (ZEPHR edge/cost, RUNE toxicity); tự vô
   hiệu khi features invalid (groktopus_swarm.py:48-49).
4. Đo được: gross theo bucket conviction — hiện Q1 −0.16 vs Q5 +1.34 bps
   (conviction là tổng 3 alpha, chưa tách riêng NORO).

## LUMEN — order-flow imbalance (weight .20)
1. Nguồn: groktopus_swarm.py:45 — `tanh(1.5·obi + flow + .2·ofi + .35·micro_delta_ticks + .25·flow_acceleration)`
2. Principle: mất cân bằng sổ lệnh + dòng lệnh chủ động dự báo áp lực giá
   ngắn hạn; micro_delta_ticks và flow_acceleration bắt gia tốc dòng.
3. Risk: như trên; nhạy nhất với VPIN (dòng độc hại làm obi/flow đảo dấu).
4. Đo được: by_alpha hiện n≈100 (−3.4 bps) — nhóm mẫu lớn nhất, ứng viên
   đầu tiên có verdict KTC95 khi dữ liệu đủ.

## TIDAL — momentum/trend (weight .20)
1. Nguồn: groktopus_swarm.py:46 — `tanh(.6·momentum + .3·cross_momentum + .5·sweep + .5·lead_lag + (50−rsi)/100)`
2. Principle: đuổi xu hướng có xác nhận đa nguồn (cross-symbol momentum,
   lead-lag BTC/ETH, sweep có tổ chức, RSI lệch 50).
3. Risk: regime TREND hạ ngưỡng quorum (consensus_engine.py:55 — trend_floor
   .70) nhưng sweep + flow_reversal vẫn bị adverse_selection chặn.
4. Đo được: by_alpha n≈13 — quá nhỏ, chưa kết luận.

## ZEPHR — liquidity/edge gate (weight .10, VETO)
1. Nguồn: groktopus_swarm.py:61 — `edge_bps ≥ edge_multiple × cost_bps`
2. Principle: chỉ giao dịch khi edge ước tính (proxy ATR×conviction,
   UNCALIBRATED — groktopus_swarm.py:59) vượt chi phí với bội số an toàn
   1.8× (mainnet) — phí maker+taker + nửa spread + slippage (:55).
3. Risk: chính nó là risk control; cost model đã đo thật 6.00 bps.
4. Đo được: block_counts['EDGE_BELOW_COST'] — tỷ lệ chặn theo phiên.

## RUNE — toxicity gate (weight .10, VETO)
1. Nguồn: groktopus_swarm.py:60 + adverse_selection() :6-21
2. Principle: không quote vào hàng đang bị quét — VPIN > .65, vpin_jump ≥ .15,
   flow_reversal ngược hướng, hoặc sweep kèm bất kỳ dấu hiệu độc nào.
3. Risk: bản thân nó; single veto chặn toàn bộ lệnh.
4. Đo được: block_counts['VPIN_HIGH'/'ADVERSE_SELECTION']; RUNE VETO đã
   chặn đúng LINK VPIN .951 (kiểm chứng phiên 07/09).

## OKAPI — capacity (weight .08, VETO)
1. Nguồn: groktopus_swarm.py:70 — capacity_ok
2. Principle: không vượt biên độ vốn; bootstrap 1% ≪ giới hạn margin 75%.
3. Risk: bản thân nó. 4. Đo được: block_counts['CAPITAL_LIMIT'].

## MARIN — exit readiness (weight .07, VETO)
1. Nguồn: groktopus_swarm.py:70 — exits_ready
2. Principle: không vào lệnh nếu không có kế hoạch thoát (barrier đã plan).
3. Risk: bản thân nó. 4. Đo được: block_counts tương ứng.

## VESKA — gateway readiness (weight .05, VETO)
1. Nguồn: groktopus_swarm.py:70 — gateway_ready
2. Principle: không vào lệnh khi kênh thực thi không chắc chắn (feed mới,
   gateway reconcile lỗi).
3. Risk: bản thân nó. 4. Đo được: block_counts['RISK_OR_FEED_GATE'].

## Ghi chú trung thực
- Điểm 0.768 headline KHÔNG phải conviction hướng: với gate toàn PASS,
  0.768 ≈ 0.615 conviction alpha (đã kiểm chứng: runtime.py:582-589 comment).
- Việc tách đóng góp RIÊNG của NORO/LUMEN/TIDAL chờ alpha_votes trong probe.
- Tài liệu này mô tả code NGAY THỜI ĐIỂM viết; nếu code đổi, đổi cả đây.
