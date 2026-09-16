# 🌌 FORM GPT-6 ASTRA MEDIUM × CODEX — THOÁT VÒNG LẶP & CHẮT LỌC TRADINGAGENTS
### Target: `F:\he_thong_da_tac_tu_2` + tinh hoa `mau\TradingAgents-main`
### Model: GPT-6 Astra Medium trong Codex — tối ưu token, sửa phẫu thuật, có verify

> Nguồn form này (đã tra cứu 09/09/2026):
> - Codex Prompting Guide (`developers.openai.com/codex/prompting.md`): Goal → Context → Output → Boundaries, bắt đầu bằng kết quả cần, chỉ attach nguồn làm đổi kết quả, verification cụ thể.
> - AGENTS.md Guide: chỉ dẫn ngắn, scope theo thư mục, task breakdown + verification, Code Review Rules.
> - Codex workflow: `$plan` local → revise plan → Implement Milestone 1 → `/review` → sửa → rerun.
> - TradingAgents v0.4.0: point-in-time fix, decision-log memory, checkpoint per-ticker SQLite, Trader price grounding, debate bull/bear + risk 3 chiều, DEFAULT_CONFIG env-override.
> - Local: `docs/CHAN_DOAN_2026-09-06.md` (90 INTENT → 7 fills → 2W/5L net -0.18 USDT), `phan_tich/nghen_edge.py`, `pool_phien.py`, `ev_fill_adjusted.py`, `slow_brain_contract.py`, `research/README.md`.

---
## 📋 CÁCH DÙNG CODEX 2 PHÚT

1. VS Code → Open Folder = `F:\he_thong_da_tac_tu_2`.
2. Codex sidebar → model **GPT-6 Astra Medium**.
3. Copy khối ```text ở PHẦN 2 bên dưới → paste → Enter.
4. Bắt nó chạy `$plan` trước → bạn duyệt → mới cho implement từng Milestone.
5. Cuối mỗi Milestone: `python -m pytest -q` + `/review` rồi mới tiếp.
---
## PHAN 2: KHOI COPY-PASTE SANG CODEX (tu day)

```text
Goal: Pha vong lap vo han (bat -> cao data mu -> treo 0 fills -> noi cong -> lo phi -> sua -> bat tiep) bang cach chat loc TradingAgents-main thanh SLOW-brain gating cho GROKTOPUS FAST-hands. Khong rebuild tu dau. Sua phau thuat + co exit criteria.

Context (trust this, only open files in Steps):
- Workspace F:\he_thong_da_tac_tu_2 = GROKTOPUS 8-agent (VESKA/NORO/LUMEN/TIDAL/ZEPHR/RUNE/OKAPI/MARIN), gate 76.8% execute / 70% veto, VPIN 0.65, edge/cost 1.8x. 157 tests pass.
- Reality: 90 ENTRY_INTENT -> 7 fills verified via userTrades -> 2W/5L net -0.18 USDT, fee 0.17. Fee maker-in 2bps + taker-out 4bps = 6bps + spread/slip ~8bps. Edge proxy 0.08-0.52bps << 8bps => ZEPHR block CORRECT, do not loosen.
- SignalLab v1 n=4231 mean -8.13bps (assumed-fill, overlapping, useless). v2 separated. hftbacktest fill ~54% synthetic, taker must = 0.
- Loop: treo -> noi cong -> lo phi -> thu data mu khong exit criteria -> lap. Daily-candle LLM alpha != 0.5s crypto edge.
- mau/TradingAgents-main v0.4.0: analysts/{market,news,sentiment,fundamentals,social} -> bull/bear debate (rounds=1) -> trader price-grounded -> risk/{aggressive,conservative,neutral} -> managers/{research,portfolio}. Memory + checkpoint SQLite + PIT guard + DEFAULT_CONFIG env-override.
- Reuse: phan_tich/slow_brain_contract.py (BLOCK-only, stale/future->block), phan_tich/ev_fill_adjusted.py (EV=p_fill*(gross-cost), fee(q)=6-2q).

Boundaries (NEVER violate, Code Review Rules):
- DO NOT lower 76.8/70/0.65/1.8x, DO NOT seed fake fills/votes, DO NOT use vote confidence as Kelly prob, DO NOT invent price (ground to verified snapshot).
- Verdict SLOW-brain ONLY BLOCK, never force BUY/SELL, never override ZEPHR/RUNE. Stale/malformed/future -> block. data_cutoff<=as_of<=generated<intent<expires.
- Surgical line-range edits only. Zero TODO/placeholder. Every new heuristic needs test in tests/.
- No mainnet real-money without user typing XAC NHAN. Default advisory/paper/testnet-bootstrap only.
- Token saving: concise, tables only when asked, no re-explain OBI/VPIN/Kelly basics.

Steps (plan first, then Milestones, verify after each):
M0-plan: Run $plan. Output migration plan exact files per milestone + rollback. Wait approve before code.
M1-forensic: Run python -m pytest -q + python tools/audit_execution.py + python phan_tich/nghen_edge.py + python phan_tich/pool_phien.py --since 0. Compare Context numbers. Mismatch -> STOP report. No code yet.
M2-distill: Read mau/TradingAgents-main/tradingagents/{agents/analysts,agents/researchers,agents/trader/trader.py,agents/risk_mgmt,agents/managers,graph/trading_graph.py,graph/reflection.py,default_config.py} + CHANGELOG.md. Table 1 row/module: keep-as-SLOW / adapt / reject-for-HFT + reason. Max 10 rows. No code yet.
M3-plumbing: Minimal ta_bridge hourly JSON verdict (schema_version,symbol,regime{TREND,CHOP,TOXIC},veto,conviction,as_of_ts,generated_at,expires_at,data_cutoff,model_version) reuse slow_brain_contract.validate_verdict + fill-aware guard reuse ev_fill_adjusted.ev_per_intent. Add 3 tests: stale-block, future-block, veto-chan-100%-block. Keep hot-loop except one gating if.
M4-measure: pip install hftbacktest numba numpy; python tools/run_backtest.py --synthetic --minutes 90; python tools/calibrate_edge.py --path data/backtest-signal-lab.jsonl. Order: warnings -> fill-rate -> taker must 0 -> KTC95. Continue only if queue-modelled edge_fill_adjusted KTC95 fully >0.
M5-paper: python run_concurrent_paper.py --market-data mainnet --port 8766 (>=1 hour); python phan_tich/pool_phien.py --since 0. Report P1/P2/P3 numbers.
M6-testnet: Only if P3 PASS paper: python run_binance_testnet.py --bootstrap --no-carry (probe size, policy frozen). Then audit+pool. Mainnet ADVISORY.

Output STRICT:
| # | Ket luan 1 dong | Bang chung/so | File:Function sua | Spec doi pseudo 2 dong | Test them | Kill-criteria |
+ 3 bullets: (a) Lam truoc 24h (b) Kill ngay (c) Can bao nhieu probes/trades (dung ev_fill_adjusted.sample_size_*).
+ Acceptance: pytest ___/___ PASS, audit ___ trades PnL ___ fee ___, P1 mean ___ SE ___ n=___, P2 p_fill ___ Wilson ___-___, P3 F4 n=___ mean ___ KTC ___-___ GO/NO-GO, honest 1 cau: fixed measurement or found alpha? Neu chi sua thuoc -> CHUA CO EDGE.

Verification (paste output):
Set-Location 'F:\he_thong_da_tac_tu_2'; python -m pytest -q; python tools/audit_execution.py; python phan_tich/pool_phien.py --since 0; /review Focus on lookahead, fee BUYvsMARKET trap, stale-verdict, fake-fill
Exit criteria (write into run.bat + AI_HANDOVER_GUIDE.md + docs/CHAN_DOAN new file):
- Any direction hits Kill -> STOP 24h, log reason, do not rerun same params.
- KILL strategy: ledger KTC95 upper<0 after >=20 settled VERIFIED trades -> paper-only, ban testnet 1 week.
- Scale-up ONLY if P3 F4 KTC lower>0 on >=300 probes + P1 SE<1.5bps + P2 Wilson lower>0.15. Missing 1 -> keep bootstrap 1pct equity.
```
