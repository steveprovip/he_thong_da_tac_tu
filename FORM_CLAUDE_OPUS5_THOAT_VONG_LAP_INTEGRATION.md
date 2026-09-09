# 🌌 FORM CLAUDE OPUS 5 COWORK — THOÁT VÒNG LẶP TREO → LỖ → THU DỮ LIỆU → LẶP LẠI
### Tích hợp GROKTOPUS 2.0 (`F:\he_thong_da_tac_tu_2`) × TradingAgents v0.4.0 (`F:\he_thong_da_tac_tu_2\TradingAgents-main`)
### Mục tiêu: SINH LỜI NHANH MÀ AN TOÀN — có bằng chứng, không hạ cổng để giả vờ có lãi

> Nguồn kỹ thuật form này (đã tra cứu):
> - Anthropic Docs — Prompting best practices (XML tags, role, clarity, examples, thinking, agentic systems)
> - Anthropic Docs — Prompting Claude Opus 5 (verbosity, progress updates, task scope/over-verification, subagent control, self-correction, effort low/medium/high/xhigh, thinking-disabled artifacts)
> - Anthropic Docs — Prompt engineering overview (define success + evals trước khi tune prompt)
> - GitHub TauricResearch/TradingAgents v0.4.0 + CHANGELOG (point-in-time fix, decision-log memory, checkpoint resume, trader price grounding)
> - Local evidence: `docs/CHAN_DOAN_2026-09-06.md`, `phan_tich/nghen_edge.py`, `phan_tich/pool_phien.py`, `research/README.md`, `run.bat`, 4 FORM cũ trong workspace
> - Cách dùng Claude Desktop tối đa: chọn Folder = `F:\he_thong_da_tac_tu_2`, model Opus 5, Cowork mode, effort High (mặc định) → xhigh chỉ khi refactor đa-file khó

---

## 📋 HƯỚNG DẪN DÁN VÀO CLAUDE DESKTOP (2 PHÚT — ĐỌC TRƯỚC KHI COPY KHỐI DƯỚI)

1. Mở Claude Desktop → chuyển model **Opus 5** → bật **Cowork**.
2. **Add Project / Folder**: chọn `F:\he_thong_da_tac_tu_2` (nó sẽ tự thấy `TradingAgents-main/` bên trong, `core/`, `research/`, `tools/`, `data/`, `docs/`).
3. Copy TOÀN BỘ khối trong ```markdown dưới đây → dán vào chat → Enter.
4. Để Opus 5 tự chạy: đọc code → chạy pytest → chạy backtest/signal-lab → sửa phẫu thuật → verify. Đừng ngắt giữa chừng, đừng bắt nó hỏi confirm từng bước nhỏ.
5. Khi xong, yêu cầu nó xuất bảng nghiệm thu P1-P3 + audit (mục 🏁).

```markdown
# 🏛️ [CLAUDE OPUS 5 COWORK — THOÁT VÒNG LẶP & TÍCH HỢP GROKTOPUS × TRADINGAGENTS DIRECTIVE]
# TARGET WORKSPACE: F:\he_thong_da_tac_tu_2
# TARGET ROLE: Chief Quantitative Strategist + Principal HFT Microstructure Architect + Multi-Agent Systems Integrator
# MODEL: Claude Opus 5 (Cowork, thinking ENABLED, effort=high mặc định)

<system_cognitive_mode>
You are Claude Opus 5 operating in Claude Desktop COWORK MODE with full local file + terminal privileges at `F:\he_thong_da_tac_tu_2`.

You have world-class mastery of:
1. HFT microstructure: OBI, OFI, CVD, VPIN (Easley/Lopez de Prado/O'Hara), Avellaneda-Stoikov reservation price R(s,q,t) = s - q*gamma*sigma^2*(T-t), queue-position fill modelling, adverse selection.
2. Quant risk & sizing: Fractional Kelly under fat tails (Busseti-Ryu-Boyd 2016), Triple Barrier (Lopez de Prado), maker/taker fee microstructure (Binance USD-M: maker 2bps, taker 5bps, round-trip 6-7bps + spread/slippage ≈ 8-8.75bps).
3. Multi-agent LLM systems: LangGraph orchestration, bull/bear debate, risk team, Trader price grounding, Portfolio Manager, persistent decision-log memory with point-in-time guard, checkpoint resume.
4. Rigorous empirics: no lookahead, no survivorship bias, paired fill-vs-probe analysis, bootstrap CI, Wilson interval, pre-registered frozen filters, time-block OOS.

You are NOT a passive chatbot. You are the autonomous Co-Worker who owns the outcome.
</system_cognitive_mode>

<cowork_operational_protocol>
1. Autonomous loop: plan internally → inspect code → run commands → edit surgically → re-run tests → report EMPIRICAL numbers. Do NOT ask for trivial step-by-step confirmations.
2. Complete task first, then report. You received the FULL spec upfront — run end-to-end before asking anything.
3. Zero placeholder invariant: 100% complete syntactically-valid code. Zero `# TODO`, zero `...`, zero omitted functions.
4. Codebase continuity: DO NOT wipe/rebuild working modules. GrokTOPUS has 157 tests passing; TradingAgents-main is upstream v0.4.0 — respect both, integrate by ADAPTER, not rewrite.
5. Test-driven verification: every change must keep `python -m pytest -q` at 100% PASS. Add new tests for new logic.
6. Financial safety (HARD): Mainnet is ADVISORY-ONLY unless user explicitly types XAC NHAN/CONFIRM. Never place real orders to prove activity. Testnet uses `--bootstrap` sizing ONLY, policy thresholds NEVER lowered to fake fills.
7. Progress updates: brief user-facing notes at phase boundaries only (started / forensic done / integration done / verified). No narration of every tool call. No `double-check / re-verify` theatre — your built-in self-correction already covers it.
8. Code review honesty: report EVERYTHING you find, then filter in a separate pass. Never hide low-severity issues to look clean, never inflate results. If a fix only repairs measurement (not alpha), say so explicitly.
9. Thinking: keep thinking ENABLED. Control cost with effort levels (low/medium for reads, high default, xhigh only for hard multi-file refactors) — never disable thinking. Do not emit internal XML tags in visible output.
</cowork_operational_protocol>

<success_criteria>
DONE = all of the following are TRUE with evidence, not claims:
(a) `python -m pytest -q` → 157+ tests PASS (no safety test broken).
(b) Vòng lặp treo→lỗ→thu data→lặp lại BỊ PHÁ VỠ: có exit criteria định lượng — khi nào DỪNG thu data mù, khi nào KILL strategy, khi nào mới được tăng size. Không còn "chạy 11 giờ để xem sao".
(c) Tích hợp GrokTOPUS × TradingAgents chạy được end-to-end trên paper/testnet: slow-brain (regime+debate+memory) GATE fast-hands (microstructure swarm), có log chứng minh gate đã chặn/mở đúng.
(d) 3 số P1-P3 được báo cáo trung thực: P1 adverse selection (fill-probe paired, bps ± SE), P2 p_fill maker-exit (Wilson 95%), P3 frozen-filter F4 KTC95 có nằm trọn trên 0 hay không. Nếu chưa dương → verdict NO-GO + kế hoạch thu thập tiếp theo, KHÔNG hạ cổng.
(e) `AI_HANDOVER_GUIDE.md` + `docs/CHAN_DOAN_*.md` được cập nhật với số mới, lệnh tái lập, giới hạn trung thực.
</success_criteria>

---

## 📸 PHẦN I: BẰNG CHỨNG PHÁP Y — VÒNG LẶP HIỆN TẠI (đọc trước khi sửa)

<forensic_evidence>
Hệ thống GrokTOPUS 2.0 tại `F:\he_thong_da_tac_tu_2` chạy sạch nhưng kẹt vòng lặp:
`treo máy (0 fills / RUNE VETO) → nới lỏng để có fills → chịu lỗ phí → thu thập dữ liệu → lặp lại mà không có exit criteria`.

Số liệu đối soát thật (nguồn duy nhất được tin — `docs/CHAN_DOAN_2026-09-06.md` + tái lập bằng `python phan_tich/nghen_edge.py` + `python tools/audit_execution.py`):

| Chỉ tiêu | Kết quả |
|---|---:|
| ENTRY_INTENT trong journal | 90 |
| Lệnh mở có fill đối soát được qua `/fapi/v1/userTrades` | 7 |
| Giao dịch khép kín | 7 |
| Thắng / thua sau phí | 2 / 5 |
| PnL trước phí | -0.00908003 USDT |
| Phí thực trả | 0.17332082 USDT |
| PnL ròng | -0.18240085 USDT |
| Định luật phí (đã chứng minh) | entry 100% maker 2bps + exit 100% taker 4bps → round-trip ≈ 6.00bps; +spread/slippage ≈ 8–8.75bps |
| Signal Lab v1 (n=4231 probe, assumed-fill, overlapping, multi-config) | mean net -8.13bps; 13 mẫu approved mean net -5.06bps — KHÔNG phải 4231 trades độc lập |
| Edge proxy hiện tại (`|mean(direction)|×ATR/price`) | BTC/ETH ≈ 0.08–0.52bps vs cost ≈ 8bps → cổng ZEPHR chặn là ĐÚNG, không phải bug |
| VPIN Testnet ≈ 1.0 | chưa phân biệt được tape một chiều vs bucket sai vs wash — cấm gọi bừa là wash rồi bỏ qua |
| Kelly | mới 7 mẫu Testnet verified (< 20 tối thiểu) → WARMUP là đúng; `--bootstrap` chỉ cho sizing thăm dò, KHÔNG đổi cổng rủi ro |
| Tests | 157 tests PASS (`python -m pytest -q`) — gồm chống replay token, partial-fill, stop-reject, ledger đơn vị, VPIN volume-conservation |
| run.bat hiện tại | phiên dài 11 giờ (`--seconds 39600`) + L2 recorder + auto audit + pool P1-P3 — nhưng CHƯA có quy tắc DỪNG/KILL |

Kết luận pháp y (bắt buộc thừa nhận trước khi code):
- Sửa đo lường (VPIN, microprice, Kelly units, Signal Lab v2) mới chỉ sửa THƯỚC ĐO, chưa tạo ra ALPHA.
- Ép NORO/TIDAL cùng chiều, hạ threshold 76.8%→50%, nâng toxic_vpin→0.95 để có fills = GIẢ VỜ CÓ LÃI. Cấm tuyệt đối.
- Muốn sinh lời nhanh mà an toàn: hoặc TĂNG edge thực (fill-aware, regime-filtered) lên trên 8bps, hoặc GIẢM cost thực (maker-exit p_fill cao), hoặc DỪNG đúng lúc. Không có đường thứ tư.
</forensic_evidence>

<first_job>
1. Đọc `docs/CHAN_DOAN_2026-09-06.md`, `AI_HANDOVER_GUIDE.md` (Phase 3+4), `research/README.md`.
2. Chạy tái lập (chỉ đọc, không ghi):
```powershell
Set-Location 'F:\he_thong_da_tac_tu_2'
python -m pytest -q
python tools/audit_execution.py
python phan_tich/nghen_edge.py
python phan_tich/pool_phien.py --since 0
```
## P2-HUONG 1+2: FILL-AWARE EDGE & MAKER-EXIT (core plumbing)

<research_mission>
Nhiem vu KHONG phai "trade nhieu hon". Nhiem vu la CHUNG MINH: edge o dau, cost bao nhieu, fill bao nhieu, KHI NAO DUNG.
</research_mission>

### HUONG 1 - FILL-AWARE EDGE (probe dang gia dinh khop 100%)
- Van de: Signal Lab tinh PnL nhu maker luon khop. Thuc te research do fill GTX ~54.2% (synthetic), live chi 7/90 intents doi soat duoc.
- File: `core/signal_lab.py`, `research/bridge.py`, `tools/calibrate_edge.py`.
- Viec lam: (1) moi probe mang `p_fill` + bao cao `edge_fill_adjusted = mean_gross x p_fill - fee`; (2) tach 3 schema `assumed_fill` vs `queue_modelled` vs `live_verified`; (3) probe thieu p_fill -> calibrate_edge tu choi (fail loudly).
- Kill: KTC95 cua edge_fill_adjusted vat qua 0 sau >=200 probe queue-modelled -> KILL luot spread mong.

### HUONG 2 - MAKER-EXIT P_FILL (giam cost 6bps -> 4bps)
- File: `core/barrier_planner.py`, `core/runtime.py`, `phan_tich/pool_phien.py` (P2).
- Viec lam: (1) do P2 chuan MAKER_EXIT_INTENT -> filled/fallback, grace 8s + Wilson 95%; (2) toi uu grace theo tung coin; (3) test fallback taker PHAI co stop.
- Kill: p_fill maker-exit < 15% (upper Wilson < 0.15, n>=50) -> KILL maker-exit, chap nhan taker-exit.
3. Nếu bất kỳ con số nào lệch khỏi bảng trên → DỪNG, báo cáo lệch trước, không code tiếp.
## P2-HUONG 3+4: REGIME GATE & CARRY (dung ha nguong bua bai)

### HUONG 3 - REGIME-GATED CONSENSUS (giu 76.8% / veto 70%)
- CAM TUYET DOI ha threshold, nang toxic_vpin, ha edge-cost de co fills.
- File: `core/consensus_engine.py`, `core/groktopus_swarm.py`.
- Viec lam: them lop REGIME chay theo GIO (tach vong nong 0.5s): trending/chop/toxic. Chi cho intents khi trending + spread 2-8bps + ATR 6-20bps (ke thua F4 trong pool_phien.py). Regime KHONG doc future (hoc TradingAgents #1275/#1220: UTC half-open window). Chi duoc NANG nguong khi chop, cam ha duoi 70%.
- Kill: P3 F4 KTC95 duoi <= 0 sau >=300 resolved probes -> KILL directional day-trading.

### HUONG 4 - CARRY/FUNDING EDGE (edge cham ma chac)
- File `core/funding_carry.py` hien la nghien cuu, KHONG co duong auto-trade (giu nguyen cam).
- Viec lam offline: funding history + basis vs phi taker 1 lan. Co hedge (spot/futures) TRUOC khi viet execution. Khong hedge -> khong code.
- Kill: net carry < 2x cost -> KILL.
## P2-HUONG 5: SLOW-BRAIN x FAST-HANDS (tich hop TradingAgents)

<integration_architecture>
GrokTOPUS = fast-hands (0.5s microstructure, mu macro). TradingAgents v0.4.0 = slow-brain (phut-gio, LLM debate) nhung chay nen NGAY co phieu - cam truc tiep vao vong nong = chay latency + lookahead. Kien truc BAT BUOC (adapter, khong rewrite):

TradingAgents (slow-brain, cron theo GIO, offline) -> regime verdict + veto + conviction prior (file JSON)
        v (guard as_of_ts < intent_ts, KHONG import vao core/runtime.py)
GrokTOPUS swarm (fast-hands 0.5s) -> intents, bi GATE boi verdict
        v
RUNE veto (giu nguyen) -> VESKA execution (giu AuthToken HMAC TTL 5s)
</integration_architecture>

<integration_tasks>
1. Doc truoc: `TradingAgents-main/tradingagents/graph/trading_graph.py`, `propagation.py`, `reflection.py`, `checkpointer.py`, `default_config.py`, `agents/` (analysts/researchers/trader/risk_mgmt/managers), `cli/main.py`.
2. Copy NGUYEN LY (tu viet, khong copy code): point-in-time lesson guard (#1251), debate-open-no-fabrication (#1176), trader-price-grounding (#1167), checkpoint resume (#1249), decision-log memory (moi settled trade ghi raw+net PnL, lan sau chi thay lessons resolved truoc trade date).
3. Viet `research/ta_bridge.py` (adapter mong): doc verdict JSON (symbol, regime, conviction_prior, veto_flag, as_of_ts) + guard lookahead. Slow-brain dung LLM re (quick_think scan gio + deep_think khi regime doi), cau hinh qua TRADINGAGENTS_* env, khong hardcode key.
4. Tests: verdict-from-future bi tu choi; veto=true chan 100% intents; verdict stale >2h -> default CHOP (chan).
5. Kill: sau 1 tuan paper, gated-intents khong cai thien P3 vs ungated (KTC95 overlap) -> KILL integration, giu GrokTOPUS thuan.
</integration_tasks>

## PHAN III: KE HOACH THUC THI 5 GIAI DOAN (tu chay, khong hoi vat)

<phase_1_forensic>
`python -m pytest -q` + `tools/audit_execution.py` + `phan_tich/nghen_edge.py` + `pool_phien.py --since 0`. So voi bang Phan I. Lech -> DUNG, bao cao.
</phase_1_forensic>

<phase_2_plumbing>
Fill-aware probe + P2 maker-exit + regime gate + ta_bridge skeleton. Moi cai kem test moi. Gi nguyen 76.8/70/0.65/1.8x.
</phase_2_plumbing>

<phase_3_backtest_first>
```powershell
pip install hftbacktest numba numpy
python tools/run_backtest.py --synthetic --minutes 90
python tools/calibrate_edge.py --path data/backtest-signal-lab.jsonl
```
Doc theo research/README: canh bao -> fill rate -> maker/taker split (taker phai = 0) -> KTC. Chi tiep tuc khi queue-modelled edge_fill_adjusted KTC95 tren 0 HOAN TOAN.
</phase_3_backtest_first>

<phase_4_paper_gate>
```powershell
python run_concurrent_paper.py --market-data mainnet --port 8766
python phan_tich/pool_phien.py --since 0
```
Toi thieu 1 phien co y nghia (gio, khong phai phut). P1/P2/P3 phai co so, khong duoc "chay 11 gio xem sao" ma khong nguong.
</phase_4_paper_gate>

<phase_5_testnet_probe>
Chi khi P3 PASS paper: `python run_binance_testnet.py --bootstrap --no-carry` (size tham do, policy giu nguyen). Sau phien: audit + pool P1-P3. Mainnet van ADVISORY.
</phase_5_testnet_probe>

## PHAN IV: EXIT CRITERIA - MAY CAT VONG LAP (bat buoc viet vao run.bat + handover)

<exit_rules>
- DUNG thu data mu: bat ky Huong nao dat Kill-criteria -> DUNG huong do trong 24h, ghi ly do vao docs, khong chay lai voi tham so cu.
- KILL strategy: ledger KTC95 upper < 0 sau >=20 settled VERIFIED trades -> ve paper-only, cam testnet 1 tuan.
- MOI duoc tang size: P3 F4 KTC duoi > 0 tren >=300 resolved probes + P1 adverse selection SE < 1.5bps + P2 p_fill co Wilson duoi > 0.15. Thieu 1 dieu kien -> giu bootstrap 1% equity.
- CAM: ha threshold / nang toxic_vpin / ha edge-cost / seed gia / tao phieu gia / dung vote confidence thay xac suat Kelly (loi cu CHAN_DOAN da cam).
- Mainnet: chi khi 3 dieu kien tren + user go XAC NHAN. Khong co "sinh loi nhanh" bang cach bo qua buoc nay.
</exit_rules>

## PHAN V: LENH COWORK NHANH (cho Opus 5 paste vao terminal)

<quick_commands>
```powershell
Set-Location 'F:\he_thong_da_tac_tu_2'
python -m pytest -q
python tools/audit_execution.py
python phan_tich/nghen_edge.py
python phan_tich/pool_phien.py --since 0
python tools/run_backtest.py --synthetic --minutes 90
python tools/calibrate_edge.py --path data/backtest-signal-lab.jsonl
python run_concurrent_paper.py --market-data mainnet --port 8766
python run_binance_testnet.py --bootstrap --no-carry
python tools/_diag_blocks.py
```
</quick_commands>

## BANG NGHIEM THU (Opus 5 phai xuat khi xong)

<acceptance_report>
1. Pytest: ___/___ PASS (ke ca test moi cho p_fill guard, future-verdict reject, veto-chan-100%).
2. Tai lap phap y: audit ___ trades, PnL rong ___, phi ___, co khop bang Phan I khong.
3. P1 adverse selection: mean ___ bps, SE ___, n=___. P2 p_fill: ___ (Wilson ___-___). P3 F4: n=___, mean gross ___, KTC ___-___, verdict GO / NO-GO.
4. Tich hop TA bridge: verdict file mau, stale-guard PASS/FAIL, veto-chan PASS/FAIL, gated vs ungated P3 so sanh.
5. Exit criteria da viet vao run.bat + AI_HANDOVER_GUIDE + docs/CHAN_DOAN moi: CO / CHUA.
6. Tuyen bo trung thuc 1 cau: sua do luong hay tim duoc alpha? Neu chi sua thuoc -> ghi ro CHUA CO EDGE.
</acceptance_report>

```

