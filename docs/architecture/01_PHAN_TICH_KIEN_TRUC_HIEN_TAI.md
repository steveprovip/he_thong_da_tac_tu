# 01 — Phân tích kỹ thuật kiến trúc hiện tại (Groktopus 2.0)

ARCH-2026-10-24 · nhánh `docs/h2-regime-sandbox-arch` · gốc `main@91ad6ff` · chỉ đọc và đo, **không đổi runtime, không đặt lệnh**.

## 0. Phạm vi và cách làm

- **Đã đọc:** `CLAUDE.md`, `AGENTS.md`; `core/{consensus_engine, micro_swarm, groktopus_swarm, autonomy, swarm_learner, param_bandit, tunables, dreaming_daemon, capital_router, regime_permit, fast_eval, early_stop}.py`; `core/engine.py`, `core/runtime.py`, `core/micro_signals.py` theo vùng (rg); `tools/{engine_replay, ab_bench}.py`; `docs/harness/*`; báo cáo `docs/research_upgrade/report_autonomous_self_learning_and_dreaming_2026-10-09.md`; `third_party/harness2` (README, `docs/EXPERIMENTS.md`, toàn bộ `harness2/core/*` liên quan); `third_party/MiroFish` (README, `backend/app/config.py`, `simulation_config_generator.py`, `scripts/run_parallel_simulation.py`).
- **Đã đo trên máy này** (12 CPU logic, Python 3.10.11, ổ F: là SATA):
  - `python -m pytest -q` → **1608 passed, 0 failed, 126.7 s** (baseline của phiên).
  - `python tools/engine_replay.py --source agg --symbols SOLUSDT --start 2026-09-08T00:00 --end 2026-09-08T06:00 --warmup-min 60 --quiet` → 93,003 message, **74.8 s wall = 0.08 sim-h/s** (0 vị thế; nguồn `agg` dùng sổ lệnh tổng hợp).
- **Đã tái hiện trên code thật:** đường JEV FAST (§3.4) và bộ kiểm tra spec `h2_surface_v0.yaml` (84 lever, 29 field đóng băng, 0 lỗi).
- Skill và prompt cũ chỉ là giả thuyết (CLAUDE.md §5). Ví dụ: skill `groktopus-swarm-consensus` ghi ngưỡng TREND 72% và một engine GPU Qwen. Đường engine 2-mode không có cả hai.

## 1. Hai đường chạy

```
Đường chính 2-mode  (run.bat demo|live  ->  python -m core.engine)
  WS mainnet công khai -> micro_signals.MicroFeed.features
  -> micro_swarm.evaluate (8 phiếu, regime NORMAL => tau = 0.768)
  -> JEV FAST / CONFLUENCE >= 2 / WALL / REGIME BAN          core/micro_swarm.py:96-111
  -> Autonomy.demote (LEARNER VETO, chỉ được bớt lệnh)        core/engine.py:1527-1530
  -> churn gates -> scanner yield -> observer                 core/engine.py:1534-1551
  -> TokenAuthority (HMAC-SHA256, 5 s, một lần)               core/engine.py:1026
  -> VESKA: LIMIT GTX post-only -> STOP_MARKET sàn + TP reduce-only GTX -> MARIN

Đường cũ core.runtime  (scripts/run_concurrent_{paper,live}.py, run_binance_testnet.py, run_launcher --carry)
  -> GroktopusSwarm.evaluate (tự suy regime TOXIC/TREND/CHOP)  core/groktopus_swarm.py:43-47
  -> TokenAuthority(policy=consensus)                          core/runtime.py:188
  (không có JEV FAST; trend_floor = threshold                  core/runtime.py:171)
```

## 2. Tám agent

| Agent | Loại | Trọng số | Phiếu trên đường engine | Quyền veto | Được học? |
|---|---|---|---|---|---|
| NORO | alpha: fair value, trend, VWAP MR | 0.20 | strength của trigger TREND/VWAP/MR | không | có, chỉ để hạ (demote) |
| LUMEN | alpha: OBI + CVD flow | 0.20 | FLOW/SURGE | không | có, chỉ để hạ |
| TIDAL | alpha: liq, squeeze, depth, OFI | 0.20 | SQUEEZE/LIQ/DEPTH/OFI | không | có, chỉ để hạ |
| ZEPHR | gate: dữ liệu tươi, spread, chi phí | 0.10 | cùng phía @1.0 hoặc HOLD + veto | cứng | không |
| RUNE | gate: ngân sách rủi ro, kill, tần suất | 0.10 | như trên | cứng | không |
| OKAPI | gate: size, vốn | 0.08 | như trên | cứng | không |
| MARIN | gate: thoát lệnh đã sẵn | 0.07 | như trên | cứng | không |
| VESKA | gate: gateway sẵn; chuyển tiền duy nhất | 0.05 | như trên | cứng | không |

Nguồn: `core/consensus_engine.py:12-13`, `core/micro_swarm.py:26-32`, `core/engine.py:1498`, `core/swarm_learner.py:1-18`, `core/tunables.py:73-81`.

## 3. Toán đồng thuận

### 3.1 Công thức (`core/consensus_engine.py:54-103`)

- score(s) = Σᵢ wᵢ · cᵢ · [vᵢ = s], với s ∈ {BUY, SELL}. Phía thắng = argmax.
- hard = (regime TOXIC) ∨ (có phiếu veto=True) ∨ (RUNE bỏ HOLD).
- veto = hard ∨ score < 0.70. allowed = ¬veto ∧ score ≥ τ(regime).
- τ(regime): NORMAL = 0.768; TOXIC = 0.768 (kèm veto cứng); CHOP = max(0.768, trend_floor); TREND = max(0.70, min(0.768, trend_floor)).
- `ConsensusEngine()` mặc định trend_floor = 0.70, nên **τ(TREND) = 0.70** (kiểm trong phiên này). `core/runtime.py:171` đặt trend_floor = threshold, nên trên đường cũ τ(TREND) = 0.768. Đường engine luôn gọi regime NORMAL.

### 3.2 Giải phẫu định lượng

- Khối gate = 0.10 + 0.10 + 0.08 + 0.07 + 0.05 = **0.40**. Khi năm gate xanh, mỗi lệnh được cộng sẵn 0.40.
- Muốn chạm 0.768, phần alpha phải góp ≥ 0.368, tức Σ cᵢ (cùng phía) ≥ 1.84: hai trigger mạnh ≥ 0.92, hoặc ba trigger trung bình ≥ 0.613.
- Dải [0.70, 0.768): không lệnh, không veto (nhãn `WEAK`/`BELOW`).
- score < 0.70 ⇔ Σ cᵢ < 1.5: **veto thống kê**. Một alpha đơn lẻ tối đa 0.40 + 0.20 = 0.60, nên luôn rơi vào veto thống kê nếu chỉ đi đường SWARM.
- "76.8%" không phải 76.8% đồng thuận giữa các agent dự báo. Chính code ghi rằng headline 0.768 chỉ tương đương conviction định hướng trung bình 0.613 (`core/runtime.py:44-56`). CLAUDE.md cũng nói consensus không phải xác suất đã hiệu chuẩn.

### 3.3 Các lớp lọc sau đồng thuận (đường engine)

1. **JEV FAST** (`core/micro_swarm.py:96-99`): nếu chưa allowed, có phía, năm gate xanh và jev ≥ `jev_min` (8.0 ⇔ edge > 2.54 × cost) thì score := max(score, jev/10), allowed = True, veto = False.
2. **CONFLUENCE** ≥ `min_confluence` (2) trong C1..C6 (`:100-104`). C3_EDGE (edge/cost ≥ 2.5) được tính, và gần như luôn đúng khi JEV ≥ 8.
3. **WALL** ngược phía (`:105-108`); **REGIME BAN** theo CVD (`:109-111`).
4. **LEARNER VETO**: bỏ phiếu lại với trọng số học được, chỉ được chặn (`core/autonomy.py:106-127`).
5. churn gates, scanner yield, observer (`core/engine.py:1534-1551`).

### 3.4 Tái hiện trên code thật (phiên này)

```python
from core import micro_swarm as ms
sig = {'FLOW': {'side': 'BUY', 'strength': 1.0, 'edge_bps': 60.0, 'why': 't'}}   # một alpha duy nhất
d, votes, diag = ms.evaluate(sig, ms.Gates(cost_bps=6.0), jev_min=8.0)
# -> raw consensus 0.60 | path JEV | allowed True | veto False | score 0.998 | 'JEV FAST 10.0/10'
# jev_min=None -> allowed False | veto True | score 0.60 | 'WEAK 60.0% < 76.8% · JEV 10.0'
# TokenAuthority().issue(intent, d) cấp token, consume lần hai -> 'Unknown or replayed token'
# Gates(rune=(False, ...)) -> allowed False | veto True | path None | 'RUNE VETO [RUNE]'
```

Kết luận: **veto cứng của gate là tuyệt đối**. **Veto thống kê < 0.70 thì không**: đường JEV FAST thay nó bằng bằng chứng edge/cost, đúng như thiết kế 2026-10-07 (docstring `core/micro_swarm.py:9-11`). Ngưỡng 0.768 vẫn được TokenAuthority kiểm, nhưng trên điểm đã nâng.

### 3.5 TokenAuthority và VESKA (`core/consensus_engine.py:106-155`)

- `issue`: cần allowed, ¬veto, score ≥ τ, decision.threshold ≥ τ, cùng phía với intent. Sau đó HMAC-SHA256(key 32 byte ngẫu nhiên, nonce + sha256(intent)), hạn 5 s.
- `consume`: pop nonce (một lần), kiểm hạn, digest và `compare_digest`.
- Đường engine: `TokenAuthority()` không có policy, nên τ cố định 0.768 (`core/engine.py:1026`). Lệnh cặp (carry) luôn đòi 0.768 (`:145-149`).

### 3.6 Post-Only GTX

- `Profile.entry = 'gtx'` ở cả demo và live (`core/engine.py:163`). Lệnh cắt sổ bị từ chối với -5022 (`:920-923` cho MockVenue), lùi tối đa `gtx_backoff_ticks` = 2.
- Đường taker khi vào lệnh: `super_edge` (False từ 10-07p, `:190`) và `super_cross` (`'shadow'` ở demo, `'off'` ở live; `:2238`).
- Thoát lệnh: SL sàn là STOP_MARKET (taker theo thiết kế), TP là GTX reduce-only. Thoát mềm được phép leo thang ra MARKET khi `soft_exit_escalate` (đang False).

## 4. Vòng thích ứng hiện có

| Thành phần | Nhịp | Phạm vi | Quyền | Bằng chứng |
|---|---|---|---|---|
| `SwarmLearner` (20 lệnh, mute sau 3 lệnh thua) | mỗi lệnh đóng | demo (`autonomy='on'`) | chỉ veto (demote) | 10-09: LUMEN/TIDAL có thống kê y hệt nhau, NORO không bỏ phiếu lần nào |
| `ParamBandit` (Thompson, bucket KER) | khi flat, sau mỗi settle | demo | 3 tham số trong arm | không tạo edge |
| `TunableWatcher` (`config/live_tunables.json`) | 5 s | demo | chỉ giảm rủi ro, 9 field | `core/tunables.py:28-38` |
| Dreaming (15 phút hoặc 3 lệnh) | out-of-band | demo | chỉ giảm rủi ro + observer, hết hạn 30 phút | 10-09: tìm ra tier conviction 10 % lỗ nặng nhất |
| Capital router NORMAL/CHOP/BURST | 5 s, giữ 60 s, thoát CHOP 180 s, nguội BURST 300 s | demo | phân bổ scalp | `core/autonomy.py:184-222` |
| Slow-brain permit | đường cũ | — | chỉ chặn | `core/regime_permit.py` |

- **Live không có thành phần tự thích ứng nào.** `PROFILES['live']` giữ `autonomy='off'` (`core/engine.py:577-583`) và `Autonomy.refresh` thoát sớm khi khác `'on'` (`core/autonomy.py:82`).
- Báo cáo 10-09: tự học không tạo edge (khoảng −5.3 bps/lệnh). Net tốt hơn chỉ vì vào ít lệnh hơn khi đang thua; DSR 0.68 sau 69 lần thử. Báo cáo cũng ghi một tác dụng phụ đã sửa: hạ trọng số làm đường JEV duyệt lệnh và đẩy size lên tier 10 %.

## 5. Bản đồ "regime" đang có

| Khái niệm | Nhịp | Nguồn | Ai dùng |
|---|---|---|---|
| VPIN TOXIC | bucket khối lượng | `micro_signals.toxicity` (`:323`) | gate, `mark_toxic` |
| CVD regime UP/DOWN/NEUTRAL/DRIFT | 60 s / 3 m | `micro_signals.regime` (`:658-675`) | `allow` phía lệnh |
| CHOP LOCK (KER 0.38/0.42, BTC 0.55) | 120 s, hysteresis | `micro_signals.chop_locked` (`:139-146`), `KER_GATES` (`:340`) | chặn TREND lane |
| Router NORMAL/CHOP/BURST | 5 s + hysteresis | `capital_router.py:22-26`, `autonomy.route` | scalp alloc, burst lane |
| Bucket bandit CHOP/MID/TREND | lúc chọn arm | `param_bandit.regime_of` (`:32-35`) | bandit |
| Macro bias 5 m | 5 m | `micro_signals.macro_bias` (`:183`) | gate |
| RegimeGate KER/ADX, Hurst tri-state | 1 h / 4 h | `trend_lane.py:511-640` | trend lane |

**Khoảng trống:** chuyển regime chưa phải sự kiện hạng nhất. Có ≥ 7 khái niệm regime với nhịp khác nhau, ledger không có `episode_id`. Vì vậy không quy được hiệu suất về từng giai đoạn regime, mà Harness² cần đúng điều đó để lập thư viện theo regime.

## 6. Hạ tầng kiểm chứng sẵn có

- `tools/engine_replay.py`: chạy **đúng code production** trên băng lịch sử với đồng hồ ảo, không mạng, không đụng ledger thật.
  - Giới hạn tự khai: nguồn `agg` dựng sổ lệnh tổng hợp; không có queue position, latency hay reject.
  - Chỉ override được field của `Profile` (`:362-365`), không override được `SignalConfig`.
- `tools/ab_bench.py`: A/B ghép cặp trên cùng sự kiện. DSR bị phạt theo tổng số lần thử đã ghi (`:261`).
- `core/fast_eval.py`: SPRT, mSPRT, `paired_diff` (block bootstrap), PSR, DSR, `pbo_cscv` (`:81-241`).
- Mẫu file verdict: `config/burst_verdict.json`, quy tắc "ACTIVE-ELIGIBLE iff HOLDOUT CI95 low > 0".
- **Dữ liệu hiện có:**
  - `data/recordings/`: trống.
  - `data/l2/`: SOL 3 ngày (2026-09-08..10), cùng BTC, BNB, LINK.
  - `data/replay_cache/agg_zips/`: 1 ngày SOL.
  - Đây là nút thắt cho cả Harness² lẫn sandbox.

## 7. harness2 cục bộ: cơ chế đã xác minh từ code

| Cơ chế | Nơi trong `third_party/harness2/harness2/core/` | Ý nghĩa cho Groktopus |
|---|---|---|
| Hai cấp đệ quy: Propose–Probe–Compose theo task và thư viện kinh nghiệm theo domain | `recursion.py`, `schedule.py`, `library.py` | task = một episode regime; domain = ô regime |
| Hai chiều đề xuất INV (cách làm việc) và DOM (kiến thức người làm nghề) | `prompts.py:347-407` | INV = VESKA/MARIN thực thi; DOM = ngưỡng tín hiệu, gate |
| Parallel: 2k ứng viên rồi k tổng hợp độc lập. Sequential: k vòng (INV, DOM) → compose | `recursion.py:343-449`, `:452-566` | hai chế độ ngân sách |
| Ngân sách R = 4k rollout, P = 3k stage; kiểm đồng nhất và nhãn trùng | `ledger.py:26-33`, `:89-113` | ghi đủ mọi lần thử để phạt DSR |
| Đề xuất hỏng được thử lại 1 lần, sau đó kế thừa harness hiện tại | `recursion.py:36`, `:180-251` | KEEP_BASE an toàn |
| Bề mặt sửa có allowlist và validate | `tree.py:92-110`, `:336-390` | `h2_surface_v0.yaml` |
| Chọn mù: ID ẩn danh xáo theo seed, loại ứng viên không giao, lỗi selector thì fallback | `selection.py:71-76`, `:114-153`, `:237-252` | chọn theo scorecard, không theo holdout |
| Chỉ chấm sau khi đã chọn; judge lỗi = None, không phải 0 | `judge.py:15-19` | holdout tương lai + sandbox |
| Tripwire chặn "đáp án" lọt vào ngữ cảnh người đề xuất | `context.py:399-518` | cấm số holdout/sandbox trong evidence |
| Thư viện chỉ chứa task trước đó; experience gồm cue/lesson/evidence/confidence; verdict helped/hurt/no effect/confounded; cho phép NO_ENTRY | `schedule.py:699-701`, `library.py:101-152`, `selection.py:454-505`, `prompts.py:1105-1160` | thư viện playbook theo regime, nhân quả |
| KEEP_BASE là đáp án hợp lệ | `prompts.py:333-335` | không ép thay đổi |

- **Kết quả bài báo** (README): tối đa +11.5 điểm trên WorkBuddy-Bench, +4.5 trên JobBench, +11.8 điểm phần trăm partial pass rate trên LAB, với GLM-5 và Gemini 3.5 Flash. Đây là công việc văn phòng, pháp lý và code của agent LLM. **Áp sang tham số giao dịch là giả thuyết.**
- **Giới hạn trên máy này:**
  - Đòi Python ≥ 3.12 (`pyproject.toml`), trong khi dự án chạy 3.10.11.
  - `schedule.py:294` dùng `fcntl`, chỉ chạy trên Linux.
  - Executor là OpenCode/Codex. Proposer kênh `claude_cli` chạy `claude --dangerously-skip-permissions -p` (`proposer.py:213-217`): **không được chạy trên host có `.env.local` chứa khóa Binance.**

## 8. MiroFish cục bộ

- Giấy phép **AGPL-3.0** (`LICENSE`, `backend/pyproject.toml`).
- Năm bước (README): dựng graph → dựng môi trường và persona → mô phỏng với tiêm biến động → báo cáo → tương tác.
- **Bắt buộc dịch vụ ngoài:** LLM theo chuẩn OpenAI và **Zep Cloud**; `ZEP_API_URL` tự host bị từ chối (`backend/app/config.py:67-72`). Mọi seed đều rời máy.
- **Không tái lập được và tốn kém:**
  - Mỗi round chọn agent hoạt động bằng `random` không seed (`scripts/run_parallel_simulation.py:1063-1080`).
  - Mỗi agent hoạt động tốn một lần gọi LLM (`:1253`), đồng thời tối đa 30 (`:1159`).
  - Mặc định 72 giờ mô phỏng × 60 phút/round (`simulation_config_generator.py:88-91`); README khuyên chạy dưới 40 round vì tốn.
- **Đáng mượn** (dưới dạng ý tưởng, không chép code):
  - `AgentActivityConfig`: activity_level, posts/comments per hour, active_hours, response_delay, sentiment_bias, stance, influence_weight (`:52-81`).
  - `EventConfig`: initial_posts, scheduled_events, hot_topics, narrative_direction (`:114-127`).
  - `PlatformConfig`: recency/popularity/relevance, viral_threshold, echo_chamber_strength (`:130-144`).
- **Hệ quả:** MiroFish mô phỏng **mạng xã hội**, không phải sổ lệnh. Chỉ hợp làm bộ sinh "cú sốc tường thuật" từ seed công khai, chạy cô lập và được cache. Không hợp làm Monte Carlo 1000 kịch bản.

## 9. Phát hiện

| ID | Mức | Phát hiện | Bằng chứng | Xử lý đề xuất |
|---|---|---|---|---|
| F1 | Cao | Veto < 70% không tuyệt đối trên đường engine: JEV FAST thay veto thống kê khi năm gate xanh. AGENTS.md lại ghi "Absolute Veto if consensus < 70%". | §3.4 | Chủ sở hữu chốt cách hiểu (D4). Sandbox theo dõi `jev_override_of_statistical_veto`, chưa chặn. |
| F2 | Cao | `core.runtime` nhận `--threshold`/`--veto-threshold` thấp hơn kể cả mainnet; CLI chỉ kiểm khoảng (0, 1]. Test cố ý giữ override cho testnet. | `core/runtime.py:1025-1031`, `tests/test_policy_lock.py` | Invariant lock P0: từ chối ở mainnet (D1). |
| F3 | TB | `ConsensusEngine()` mặc định trend_floor = 0.70 ⇒ τ(TREND) = 0.70. Đây là chủ ý: test tên `test_trend_floor_parameter_preserves_default_adaptive_quorum` giữ quorum thích ứng làm mặc định của class. Các caller production đều đặt 0.768 (`runtime.py:171`, `funding_carry.py:302`). Caller mới truyền regime TREND mà quên trend_floor sẽ chạy ở 70 %. `Profile.peg_min_score` lặp lại 0.768. | `core/consensus_engine.py:41`, `tests/test_quant_risk.py:170-174`, kiểm trong phiên | D2: giữ mặc định nhưng thêm kiểm tra trên đường production (C1). |
| F4 | TB | Đồng thuận không phải bằng chứng độc lập: 0.40 của gate cộng vào mọi lệnh; LUMEN và TIDAL đồng phiếu; JEV FAST chỉ cần một agent dự báo. | §3.2–3.4, báo cáo 10-09 | C2: đo N_eff (chỉ telemetry). |
| F5 | TB | Hai bộ học riêng (`ConsensusEngine.record_outcome` trên đường cũ, `SwarmLearner` trên đường engine), đều không theo regime: mang hiệu suất cũ qua lúc chuyển regime. | `consensus_engine.py:66-85`, `swarm_learner.py:33-41` | C3 + D3. |
| F6 | TB | Chuyển regime chưa là sự kiện hạng nhất; không có `episode_id`. | §5 | `RegimeEpisode` (doc 02 §3). |
| F7 | TB | Replay không override được `SignalConfig`, nên không probe được lever DOM. | `tools/engine_replay.py:362-365` | P2 thêm `--sig-set`. |
| F8 | TB | MockVenue khớp trade-through, không queue, ack tức thì, không reject ngoài -5022: kinh tế maker GTX trong replay lạc quan. | `core/engine.py:879-966` | `SandboxVenue` (doc 03 §5). |
| F9 | TB | Dữ liệu replay mỏng (§6). | `ls data/*` | Ghi băng ≥ 14 ngày (D5). |
| F10 | Thấp | harness2 không chạy native (Python 3.12, `fcntl`) và proposer bỏ qua quyền. | §7 | Port phương pháp; LLM chỉ chạy trong container không secret. |
| F11 | Thấp | MiroFish: AGPL, Zep Cloud, LLM, RNG không seed. | §8 | Cô lập, seed công khai, cache. |
| F12 | Thấp | Tài liệu lệch code: skill ghi TREND 72 % và Qwen GPU; AGENTS.md ghi "606+ tests" (thực tế 1608). | §0 | Tài liệu dẫn đến code. |

## 10. Sổ bất biến

| # | Bất biến (đúng như code đang thực thi) | Thực thi tại | Test đang ghim | Lỗ hổng |
|---|---|---|---|---|
| I1 | Không lệnh nào thiếu token HMAC một lần, cấp cho Decision có score ≥ 0.768, threshold ≥ 0.768, allowed, ¬veto, cùng phía | `consensus_engine.py:121-143`, `engine.py:1026` | `test_consensus_engine.py`, `test_quant_risk.py`, `test_two_mode_engine.py:161`, `test_policy_lock.py` | CLI đường cũ hạ được τ (F2) |
| I2 | Veto cứng của gate chặn tuyệt đối, JEV không vượt được | `consensus_engine.py:96-98`, `micro_swarm.py:94-96` | `test_groktopus_swarm.py`, `test_flow_toxicity_and_gates.py` | — |
| I3 | score < 0.70 là veto | `consensus_engine.py:97` | `test_consensus_engine.py` | JEV FAST thay thế (F1) |
| I4 | Trọng số cố định; gate không bị học hay mute; trọng số học được chỉ để chặn | `consensus_engine.py:49-52`, `tunables.py:73-81`, `autonomy.py:106-127` | `test_autonomy_self_learning.py:97`, `:169` | — |
| I5 | Entry Post-Only GTX; IOC chỉ qua `super_edge`/`super_cross` (off/shadow) | `engine.py:163`, `:190`, `:920-923`, `:2238` | `test_two_mode_engine.py:289`, `:300`, `:309`, `:391`, `:763` | `super_cross='on'` là đường taker |
| I6 | Live micro-capital: equity_cap 3.85 U, max_loss 0.05 U, autonomy off, phải gõ LIVE | `engine.py:5-8`, `:577-583` | `test_two_mode_engine.py:188`, `:207`, `:945` | — |
| I7 | Tunables chỉ giảm rủi ro, trong whitelist | `tunables.py:12-16`, `:28-38` | `test_autonomy_self_learning.py:169` | — |
| I8 | Đường cũ: trend_floor = threshold | `runtime.py:171` | `test_policy_lock.py` | mặc định của class là 0.70 (F3) |

Mọi thiết kế ở doc 02 và 03 phải giữ nguyên I1–I8 **đúng như code hiện tại**. Chỉ chủ sở hữu được đổi cách hiểu I3 (D4).
