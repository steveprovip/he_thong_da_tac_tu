# 04 — Kế hoạch triển khai: H2-Regime + Sandbox 1000 kịch bản

ARCH-2026-10-24 · baseline: `main@91ad6ff`, `python -m pytest -q` = 1608 passed / 0 failed (126.7 s) · ước tính công sức là **ước tính**, chưa đo.

## 0. Nguyên tắc chung (mọi phase)

- **Một phase, một nhánh.** Nhánh `feat/h2-p<N>-<tên>` (worktree riêng nếu có phiên song song). Không commit hay push khi chưa có người duyệt.
- **TDD gate:** viết test trước. Phase xong khi `python -m pytest -q` xanh, không regression (CLAUDE.md §1).
- **Không khởi động lại engine, không đặt lệnh, không bật live, không tăng vốn/đòn bẩy** (CLAUDE.md §5).
- **Mặc định tắt:** mọi tính năng mới vào runtime đều sau cờ (`h2='off'`, playbook chỉ demo).
- **Hành vi không đổi:** phase nào chạm `core/` phải chứng minh replay cố định ra kết quả **giống hệt** trước–sau (`tools/engine_replay.py`, SOL 2026-09-08 00:00–06:00, so sánh `summary` và `positions`).
- **Đăng ký trước:** mỗi thí nghiệm (§3) có dòng trong `docs/research_upgrade/experiment_register.md` trước khi chạy. Mọi ứng viên đã probe → `trial_log.jsonl`.
- **Kết phiên:** `docs/harness/progress.md`, entry 6C trong `docs/harness/session-handoff.md` (validator exit 0), `engram save` + `engram sync`.
- **Bên thứ ba:** `third_party/harness2` (Apache-2.0) chỉ port phương pháp, giữ header bản quyền và ghi nguồn. `third_party/MiroFish` (AGPL-3.0) giữ nguyên bản, chạy tiến trình riêng, không import.

## 1. Các phase

### P0 — Khóa bất biến (C1) · ~1 ngày · không phụ thuộc

**Trạng thái 2026-10-24:**
- **P0a XONG** (commit `2398e92`): `core/invariants.py` + `tests/test_invariants_lock.py`.
- **P0b XONG:**
  - `Engine.__init__` từ chối quorum/token sai ở mọi mode, và từ chối Profile sai khi live đã arm; demo/replay chỉ phát `INVARIANT WARN`.
  - `core.runtime.arguments` chặn cứng hạ ngưỡng ở mainnet (D1).
  - Nhãn `consensus` + `jev_lift` có trên order log và ledger (D4).
  - 32 test.
- Hard gate của sandbox nối ở P3.

| Mục | Nội dung |
|---|---|
| Mới | `core/invariants.py`: hằng số (`SUPERMAJORITY = 0.768`, `VETO = 0.70`, `WEIGHTS_SHA256`, `ENTRY = 'gtx'`, `JEV_MIN_FLOOR = 8.0`, `TOKEN_TTL_S = 5.0`, `LIVE_EQUITY_CAP = 3.85`, `LIVE_MAX_LOSS = 0.05`) và hàm `check_consensus`, `check_token_authority`, `check_profile`, `check_runtime_args(args, mode)` |
| Mới | `tests/test_invariants_lock.py`: ghim trọng số và ngưỡng; τ của TokenAuthority đường engine = 0.768; mọi `PROFILES` có `entry == 'gtx'`, `super_edge False`, `super_cross ∈ {'off', 'shadow'}`; live 3.85 / 0.05 / autonomy off; `jev_min ≥ 8.0` và `jev_min/10 ≥ 0.768`. Thêm **test mô tả hành vi** ghim đúng tái hiện JEV FAST ở doc 01 §3.4, để mọi thay đổi ở đó phải là cố ý. |
| Sửa (cần D1/D2) | `core/runtime.py`: mainnet từ chối `--threshold < 0.768` hoặc `--veto-threshold < 0.70`; testnet/paper giữ override nghiên cứu (`tests/test_policy_lock.py::test_research_override_still_available` vẫn xanh). Đường production từ chối `regime='TREND'` khi `trend_floor < threshold`. |
| DoD | pytest ≥ 1608 + test mới, 0 failed; replay cố định giống hệt trước–sau |
| Rollback | xóa 2 file mới; revert `core/runtime.py` |

### P1 — RegimeEpisode và dữ liệu · ~2 ngày (+ 14 ngày ghi băng chạy nền) · phụ thuộc P0

| Mục | Nội dung |
|---|---|
| Mới | `core/regime_episode.py` (hàm thuần): `regime_key(features, terciles)`, `RegimeTracker` (W = 45 phút, dominance 60 %, dwell 30 phút, `UNKNOWN` không bao giờ chuyển) |
| Mới | `tools/regime_episodes_report.py`: cắt băng/ledger thành episode, thống kê theo ô |
| Sửa | ledger và mẫu funnel thêm trường `regime_cell`, `episode_id` (additive, sau cờ) |
| Vận hành (D5) | chủ sở hữu chạy `python tools/record_market.py` liên tục cho SOL/DOGE/BTC (WS công khai). `data/l2` cho thấy khoảng 364 MB/ngày chưa nén cho SOL; nên ghi ra ổ C: (NVMe) hoặc nén gz. |
| Test | `tests/test_regime_episode.py`: tính khóa đúng; `UNKNOWN` không chuyển; KER chập chờn quanh 0.38/0.42 không đẻ episode; id tất định; benchmark < 50 µs khấu hao mỗi tick |
| DoD | test xanh; báo cáo episode trên dữ liệu `data/l2` hiện có |
| Rollback | cờ off; xóa file mới |

### P2 — Bề mặt chỉnh sửa và mở rộng replay · ~2 ngày · phụ thuộc P0

| Mục | Nội dung |
|---|---|
| Mới | `research/h2/surface.py`: nạp `docs/architecture/h2_surface_v0.yaml` bằng loader chặt (từ chối enum không phải chuỗi), validate theo thứ tự ở doc 02 §4, trả thông báo lỗi dùng được ngay |
| Sửa | `tools/engine_replay.py`: thêm `sig_overrides` theo symbol và cờ `--sig-set SYMBOL.field=value` (additive); `tools/ab_bench.py` chuyển tiếp cờ này |
| Test | `tests/test_h2_surface.py`: 29/29 sửa trên field đóng băng bị từ chối; 84/84 lever chấp nhận giá trị baseline (KEEP_BASE hợp lệ); ràng buộc chéo; giới hạn 6 field; `'off'` không nháy bị từ chối; `policy_id` đổi khi và chỉ khi Profile hiệu lực đổi. `tests/test_engine_replay_sig_overrides.py`: override `ker_lock` đổi `chop_locked` một cách tất định; field lạ thì báo lỗi. |
| DoD | test xanh; replay không có `--sig-set` cho kết quả giống hệt; phần kiểm surface trong `docs/architecture/tools/check_arch_docs.py` đã chuyển thành test |
| Rollback | revert 2 tool; xóa `research/h2/surface.py` |

### P3 — Lõi sandbox: 650 kịch bản (H, B, F, X-perturb/splice/venue) · ~5 ngày · phụ thuộc P2, dữ liệu từ P1

| Mục | Nội dung |
|---|---|
| Mới | `research/sandbox/spec.py` (nạp suite, `suite_sha`, seed), `tape.py` (luồng sự kiện định dạng `rec` + SHA-256 cuộn), `venue.py` (`SandboxVenue`, doc 03 §5), `perturb.py` (X1, X5, X9, F3, F7, F8), `splice.py` (X4, X10, B), `faults.py` (F1, F2, F4, F5, F6 ở mức venue), `detectors.py` (9 detector cứng + 3 chỉ số theo dõi), `runner.py` (pool tiến trình, cache baseline, ghi kết quả trước marker), `scorecard.py` (cổng, gọi `core/fast_eval`), `verdict.py` |
| Mới | `tools/sandbox_run.py --suite docs/architecture/sandbox_suite_v0.yaml --stage P3 --candidate <overlay.yaml> --jobs 10` |
| Test | `tests/test_sandbox_venue.py` (queue, -5022 tại ack, khớp một phần, TRANSPORT không rõ), `tests/test_sandbox_suite.py` (tổng 1000, seed tất định, `suite_sha` ổn định), `tests/test_sandbox_detectors.py` (mỗi detector bắt được dòng vi phạm tổng hợp; **không** báo nhầm trên dòng JEV baseline), `tests/test_sandbox_scorecard.py` (toán cổng, quy tắc ba) |
| DoD | chạy baseline đủ 650; **đo throughput thật** và cập nhật doc 03 §6; 20 kịch bản ngẫu nhiên chạy lại cho cùng SHA; baseline 0 vi phạm cứng (nếu có thì mỗi vi phạm thành một ticket riêng, không che); có `config/sandbox_verdict.json` |
| Rollback | xóa `research/sandbox/`, `tools/sandbox_run.py`; runtime không bị đụng |

### P4 — Bộ máy H2 offline (tất định) · ~4 ngày · phụ thuộc P2, P3 (venue)

| Mục | Nội dung |
|---|---|
| Mới | `research/h2/{spec, evidence, proposers, recursion, ledger, selection, library, judge}.py`; `tools/h2_run.py --episode <id> --k 1 --mode parallel|sequential [--dry-run]` |
| Ghi chú | `ledger`/`selection`/`library`/`evidence` port từ `third_party/harness2/harness2/core/{ledger, selection, library, context}.py`, có header Apache-2.0. Không dùng `fcntl`; ghi tệp bằng tmp + `os.replace`. |
| Test | `tests/test_h2_ledger.py` (R = 4k, P = 3k, nhãn trùng), `tests/test_h2_selection.py` (xáo theo seed, base bị loại khỏi pool, k = 1 tầm thường), `tests/test_h2_evidence_tripwire.py` (có marker holdout/sandbox → báo lỗi), `tests/test_h2_library.py` (giữ nhân quả, NO_ENTRY, từ chối bài học chỉ dựa trên `confounded`, phân rã), `tests/test_h2_recursion_dry.py` (đầu-cuối với adapter giả và băng tổng hợp nhỏ: fallback khi đề xuất hỏng, KEEP_BASE) |
| DoD | `--dry-run` ra `summary.json` với R = 4, P = 3; khi có ≥ 1 episode thật thì chạy được đầu-cuối, judge J1 chờ băng holdout |
| Rollback | xóa `research/h2/` và tools; runtime không bị đụng |

### P5 — ABM, realism, adversary ABM, tường thuật → đủ 1000 · ~6–8 ngày · phụ thuộc P3, dữ liệu P1

| Mục | Nội dung |
|---|---|
| Mới | `research/sandbox/abm.py` (sổ lệnh giá–thời gian, agent, Hawkes), `calibrate.py` (theo ô), `realism.py` (cổng doc 03 §4.3), `adversaries_abm.py` (X2, X3, X7, X8), `narrative.py` (template + nạp cache), `mirofish_bridge.py` (HTTP tới dịch vụ MiroFish chạy riêng, `public_seed_check`, ánh xạ S(t)/E(t)) |
| Test | `tests/test_abm_lob.py` (ưu tiên, bảo toàn khối lượng), `tests/test_abm_realism_gate.py`, `tests/test_narrative_bridge.py` (từ chối seed chứa bí mật; template tất định; **không mạng trong test**) |
| DoD | báo cáo realism từng ô; baseline đủ 1000; family N chạy bằng template (thêm MiroFish nếu D7) |
| Rollback | cờ family A/X-abm/N tắt; suite quay về 650 (G1) |

### P6 — Tích hợp vào vòng thích ứng · ~3 ngày · phụ thuộc P1, P4, P3

| Mục | Nội dung |
|---|---|
| Mới | `core/playbook.py` (loader + validate lại + khóa verdict + hết hạn), `tools/h2_worker.py` (hàng đợi, 1 job/symbol), `tools/h2_approve.py` (terminal người thật: `GetConsoleMode` + cụm gõ có hash) |
| Sửa | `core/autonomy.py`: móc `RegimeTracker` trong `poll()`; tra playbook trong `refresh()` (chỉ demo, chỉ khi flat, thứ tự baseline → playbook → tunables → bandit → router); event `⚡ AI SELF-ADAPTED: playbook …`; `view()` thêm `regime`, `playbook`. `core/engine.py`: Profile thêm `h2: str = 'off'`. |
| Test | `tests/test_playbook_loader.py` (không bao giờ ở live; chỉ khi flat; field đóng băng bị từ chối; hết hạn → baseline; lệch khóa verdict → bỏ qua; tunables thắng playbook), `tests/test_h2_approve.py` (stdin không phải console → từ chối), `tests/test_autonomy_regime_hook.py` |
| DoD | replay 1 tuần với autonomy on + playbook mẫu: chỉ đổi khi flat, không bao giờ ở Profile live, có event telemetry; pytest xanh; với `h2='off'` thì replay giống hệt |
| Rollback | `h2='off'` (mặc định); revert `core/autonomy.py`, `core/engine.py` |

### P7 — Chu kỳ thật đầu tiên và quản trị · liên tục · phụ thuộc P1–P6 + ≥ 14 ngày băng

- Chạy H2 trên các episode đã ghi, J1 holdout, sandbox G1/G2. Viết báo cáo `docs/research_upgrade/report_h2_first_cycle_<ngày>.md`.
- **Luật dừng:** sau 20 episode không có ứng viên nào qua G2 + G3 thì H2 ở lại L0 (nghiên cứu), giống các lane hiện có.
- Track tùy chọn (D6): `ClaudeProposer` trong container; hoặc chạy harness2 nguyên bản (Linux, Python 3.12) qua một `BenchmarkAdapter` mỏng trỏ tới `tools/engine_replay.py`. So sánh ghép cặp với proposer tất định (E-H2-4).

## 2. Lịch

| Tuần | Việc | Song song |
|---|---|---|
| 1 | P0, P1, P2 | bắt đầu ghi băng (D5) |
| 2 | P3 | băng tích lũy |
| 3 | P4 | băng tích lũy |
| 4 | P5 | — |
| 5 | P6 | — |
| 6+ | P7 | — |

Tổng khoảng 23–27 ngày công. Đường găng là **dữ liệu** (≥ 14 ngày băng) và P5 (hiệu chuẩn ABM).

## 3. Thí nghiệm đăng ký trước

| ID | Câu hỏi | Đơn vị và dữ liệu | Bác bỏ khi |
|---|---|---|---|
| E-H2-1 | H2 (tất định, k = 1, parallel) có tốt hơn KEEP_BASE trên holdout tương lai? | Δnet ghép cặp theo episode (U, trung bình S seed) | sau 20 episode, CI95 của Δ trung bình chứa ≤ 0, hoặc DSR < 0.95 |
| E-H2-2 | parallel hay sequential (k = 2) | cùng episode, ghép cặp | chỉ báo cáo; không kết luận khi n < 20 |
| E-H2-3 | thư viện có giúp? (ô đã có entry PASS vs chưa có) | Δ của E-H2-1 chia theo nhóm | chỉ báo cáo khi n < 20 |
| E-H2-4 | proposer LLM so với tất định (nếu D6) | cùng episode | CI95 của chênh lệch chứa 0 → giữ tất định (rẻ hơn, tái lập được) |
| E-SBX-1 | ABM có thật không, theo ô? | stylized facts so với băng thật | trượt bất kỳ ngưỡng nào ở doc 03 §4.3 → ô chỉ tính độ bền |
| E-SBX-2 | Δ kinh tế trong sandbox (A) có báo trước Δ holdout? | Spearman ρ trên ≥ 20 ứng viên | ρ ≤ 0 → bỏ cổng kinh tế trên A |
| E-SBX-3 | verdict có ổn định theo mô hình queue? | 2 mô hình queue | < 90 % kịch bản cùng verdict → giữ mô hình bảo thủ hơn |
| E-C2 | lệnh "một nguồn" (N_eff < 1.2) có tệ hơn lệnh nhiều nguồn? | ledger demo shadow, n ≥ 100 mỗi nhóm | CI chồng nhau → không có luật veto |
| E-C3 | learner theo regime so với learner toàn cục | `tools/ab_bench.py` ≥ 2 tuần, ghép theo giờ | DSR < 0.95 hoặc CI95 thấp ≤ 0 |

## 4. Sổ rủi ro

| # | Rủi ro | Khả năng | Ảnh hưởng | Giảm thiểu | Dấu hiệu |
|---|---|---|---|---|---|
| R1 | Overfit / thiên lệch tìm kiếm | cao | cao | chọn mù, holdout tương lai, DSR theo mọi lần thử, ≤ 6 field | DSR thấp dù CI trong mẫu đẹp |
| R2 | Khoảng cách mô phỏng–thực tế | cao | TB | cổng realism, kinh tế chỉ từ băng thật, E-SBX-2 | ρ ≤ 0 |
| R3 | Trôi bất biến | thấp | rất cao | P0 + danh sách đóng băng + detector cứng | test ghim đỏ |
| R4 | Thiếu tính toán | TB | TB | cache baseline, sàng lọc 650, cửa sổ 90 phút | P3 đo < 0.02 sim-h/s |
| R5 | Lây AGPL | thấp | cao | MiroFish nguyên bản, tiến trình riêng, không import | review import |
| R6 | Lộ dữ liệu ra Zep/LLM | TB | cao | chỉ seed công khai, `public_seed_check`, mặc định template | seed bị từ chối |
| R7 | Proposer LLM: injection hoặc quyền | TB | cao | container không secret, đầu ra có cấu trúc, validator; không `--dangerously-skip-permissions` trên host | validator từ chối |
| R8 | Regime whipsaw làm playbook đổi liên tục | TB | TB | dwell/dominance, chỉ đổi khi flat, X10 | số lần đổi/giờ |
| R9 | Đầu độc thư viện | TB | TB | `confounded`, NO_ENTRY, trạng thái judge, phân rã | entry FAIL tăng |
| R10 | Lệch Python/OS (harness2 3.12, `fcntl`) | chắc chắn | thấp | port, không import | — |
| R11 | Quá tải người duyệt | TB | thấp | gom duyệt theo tuần, TTL 7 ngày | hàng đợi dài |
| R12 | Lỗ hổng dữ liệu băng | TB | TB | detector gap, judge = None | phủ băng < 95 % |

## 5. Quyết định cần chủ sở hữu

| ID | Câu hỏi | Khuyến nghị |
|---|---|---|
| D1 | `core/runtime.py` có từ chối hạ `--threshold`/`--veto-threshold` ở mainnet không? | **Có** (override nghiên cứu giữ cho testnet/paper) |
| D2 | Mặc định `trend_floor = 0.70` của `ConsensusEngine` (cố ý, theo tên test) | giữ mặc định, thêm kiểm tra ở đường production (P0) |
| D3 | Gộp `ConsensusEngine.record_outcome` (đường cũ) với `SwarmLearner`? | có, sau P6, ưu tiên thấp |
| D4 | Hiểu "RUNE veto < 70 %" thế nào? (a) như code hiện tại: veto thống kê bị JEV FAST thay được, veto gate tuyệt đối (sửa AGENTS.md cho khớp); (b) tuyệt đối: JEV chỉ nâng điểm khi raw ≥ 0.70 (đổi code, ít lệnh hơn). Kèm: tách JEV khỏi sizing (C4, treo từ 10-09). | quyết rõ một phương án; C4 chạy shadow trước |
| D5 | Bật ghi băng liên tục `tools/record_market.py` (dung lượng, WS công khai) | **có**, đây là đường găng |
| D6 | Cho phép proposer LLM (Claude) trong container, với trần chi phí? | mặc định **tắt**; chỉ bật cho E-H2-4 |
| D7 | Dùng MiroFish (Zep Cloud + LLM, chỉ seed công khai) hay chỉ template? | bắt đầu bằng template |
| D8 | TTL của playbook và người duyệt | 7 ngày; chủ sở hữu duyệt |
| D9 | Có cho live tự giảm rủi ro (mở rộng autonomy live chỉ với tunables risk-down)? | **không** ở giai đoạn này |
| D10 | Cổng sandbox lúc khởi động live: WARN hay ENFORCE? | WARN cho đến khi qua P7 |

**Đã chốt 2026-10-24** (chủ sở hữu):
- **D1:** chặn cứng ở mainnet. Đã nối trong P0b.
- **D4:** giữ JEV FAST như hiện tại. Mọi lệnh ghi `consensus` thô và `jev_lift` trên order log và ledger; event VESKA có nhãn `JEV-LIFT`; `tools/ledger_audit.py` có `by_jev_lift`.
- **D5:** ghi băng bằng `scripts/GHI_BANG_L2.bat` (gọi `tools/record_market.py`).
- **D7:** sandbox dùng template Monte Carlo nội bộ, không dùng MiroFish.

D2, D3, D6, D8, D9, D10 còn mở.

## 6. Định nghĩa hoàn thành (toàn chương trình)

1. P0–P6 hợp nhất, pytest xanh, replay với `h2='off'` giống hệt baseline.
2. Suite v0 đã đóng băng và có hash; baseline 1000 kịch bản: 0 vi phạm cứng, hoặc mỗi vi phạm có ticket.
3. ≥ 20 episode đã qua H2 + J1 + sandbox; báo cáo E-H2-1 trung thực, kể cả khi kết quả âm.
4. Không thay đổi nào chạm live nếu chưa có quyết định D-* và ủy quyền gõ phím.
