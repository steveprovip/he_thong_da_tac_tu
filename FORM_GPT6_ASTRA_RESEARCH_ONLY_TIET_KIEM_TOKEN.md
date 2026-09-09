# FORM GPT-6 ASTRA — NGHIÊN CỨU THUẦN TÚY, TIẾT KIỆM TOKEN (Cline làm code)
> File này để paste sang GPT-6 Astra. Nó CHỈ nghiên cứu + trả spec. Không xuất code full, không sửa file. Cline (tôi) sẽ実装.

## Cách dùng để đỡ tốn token
1. Chỉ paste KHỐI COPY 1 lần duy nhất. Đừng attach cả folder — chỉ attach 5 file khi nó hỏi.
2. Dặn nó trả lời <1500 tokens, dạng bảng. Hết là dừng.
3. Mỗi câu hỏi là 1 chat mới, không hỏi dồn. Xong việc 1 → quay về Cline để tôi làm → mới hỏi tiếp.
4. Không bảo nó viết code, viết test, viết lại file. Cấm `full code`.
5. Không bảo nó giải thích cơ bản (OBI/VPIN/Kelly là gì). Nó biết rồi.

---
## KHỐI COPY-PASTE SANG GPT-6 ASTRA (từ đây)
```text
Role: Principal Quant Researcher. Mode: RESEARCH ONLY, NO CODE.
Constraint: Answer <1500 tokens, Vietnamese, tables only, no full code, no file rewrite.

Context (do not re-scan, trust this):
- Workspace F:\he_thong_da_tac_tu_2 = GROKTOPUS 8-agent live (VESKA/NORO/LUMEN/TIDAL/ZEPHR/RUNE/OKAPI/MARIN), 76.8% execute, 70% veto, 157 tests pass.
- Reality: 90 ENTRY_INTENT -> 7 fills verified via userTrades -> 2W/5L, net -0.18 USDT, fee 0.17 USDT. Fee law: maker-in 2bps + taker-out 4bps = 6bps round-trip, +spread/slip ~8bps.
- Edge proxy |mean(dir)|*ATR/price = 0.08-0.52bps << 8bps cost => ZEPHR block is CORRECT.
- SignalLab v1 n=4231 mean net -8.13bps (assumed-fill, overlapping, useless). v2 separated. research/ hftbacktest fill-rate ~54% synthetic.
- Loop to break: treo (0 fills) -> nới cổng -> lỗ phí -> thu data mù -> lặp, no exit criteria.
- Other repo F:\he_thong_da_tac_tu_2\TradingAgents-main = TauricResearch v0.4.0 LangGraph (analysts/bull-bear/trader/risk/portfolio, memory, checkpoint). Daily-candle stock, mismatch 0.5s crypto loop.

Task (research only, rank 1-5 by EV/safety):
Q1: Why microstructure edge < cost? Is fill-aware edge (edge*p_fill-fee) viable, or must kill scalping?
Q2: Maker-exit p_fill path 6bps->4bps: what grace/TTL per symbol maximizes Wilson lower bound?
Q3: Regime-gate (hourly trending/chop/toxic, spread 2-8bps, ATR 6-20bps) vs lowering 76.8%: which breaks loop without fake fills?
Q4: TradingAgents integration as SLOW-brain (hourly JSON verdict: regime/veto/conviction, as_of_ts<intent_ts) gating FAST-hands swarm: minimal adapter spec?
Q5: Funding/carry as slow-safe profit vs scalping: condition to allow?

Output format STRICT (for Cline to implement, no code):
| # | Kết luận (1 dòng) | Bằng chứng/số | File:Function cần sửa | Spec đổi (pseudo 2 dòng) | Test cần thêm | Kill-criteria |
|---|-------------------|---------------|----------------------|--------------------------|---------------|---------------|
+ 3 bullet: (a) Làm trước 24h (b) Kill ngay (c) Cần bao nhiêu probe/trades để kết luận.
Do NOT output code. Do NOT explain basics.
```
---
