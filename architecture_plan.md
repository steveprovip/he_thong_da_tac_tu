# Autonomous Multi-Agent Engine — Architecture Plan

> Target machine: i5-12400F (6C/12T) · 32 GB DDR4 (~21 GB free) · RTX 5060 Ti 16 GB (14.77 GB free) · Windows 11 Pro
> Code root: `F:\he_thong_da_tac_tu\` (read/write) · Reference: `F:\KyThuatNenAIMoi\` (read-only)
> Written 2026-09-03. Supersedes the premises in `FORM_CLAUDE_OPUS5_COWORK_MULTI_AGENT.md` where they conflict with disk state — see §2.

---

## 1. What this system is

A single-GPU agent swarm. One quantized local model stays resident in VRAM; five agent roles are **personas multiplexed over that one model**, not five concurrent processes. An asyncio scheduler walks a task DAG, persists every state transition to SQLite in WAL mode, and routes each node to the persona that owns it. A Sentry process enforces budget and hardware ceilings and can atomically halt the swarm.

Money is simulated. There is no real-funds code path anywhere in this repository, by design (§7).

---

## 2. Corrections to the original directive

The directive was written against `BRAIN.md`; disk state on 2026-09-03 differs. Each correction below was confirmed by inspection, and the design follows the corrected fact.

**Only one of the three named models is present.** `F:\KyThuatNenAIMoi\model_AI\` holds `Qwen3.6-35B-A3B-UD-IQ1_M.gguf` (10.59 GB) and `s2-pro-q3_k.gguf` (2.82 GB, a Fish-Speech TTS model, not an LLM). Profile S's weights were subsequently confirmed by the operator at `D:\models\Qwen3.8-27B-UD-IQ2_XXS.gguf`, a path outside my sandbox and therefore unverifiable by me. Profile Q's IQ3_XXS weights remain absent. Consequence: the client resolves profiles at startup and **refuses to launch on a missing file** rather than failing mid-DAG.

**Profile S's file size disagrees with the documentation.** `BRAIN.md` §2 lists IQ2_XXS at 7.27 GB; the operator reports 8.39 GB. A 1.12 GB discrepancy moves the VRAM estimate from 7.96 GB to roughly 9.2 GB. The registry therefore treats every documented VRAM figure as a *claim* and recomputes from `os.stat()` at runtime, warning when the two diverge past 0.75 GB.

**Profile T's benchmark does not belong to Profile T's file.** The 87.4–95.9 tok/s figure in `BRAIN.md` §3 was measured on **IQ2_M** at ctx 327,680 over Vulkan with `ngram-mod`. The directive attributed it to **Q2_K_XL**. The file on disk is **IQ1_M**. Three different quantization tiers, one set of numbers, and none of them describes this file. Profile T is marked `UNBENCHMARKED_QUANT`; its throughput is unknown until measured, and 1-bit output quality is unproven for reasoning work.

**Profile S as "default worker" contradicts the reference document.** `BRAIN.md` QĐ-1 states plainly that Profile S is *"gần như vô dụng trên card này"* — it delivers the same throughput as Q, one quant tier worse, with 11.5× less context, stranding 6.8 GB of VRAM. Its ~52 tok/s also sits in `BRAIN.md` §7, the explicit "do not cite as fact" list. S is the default here **only** because it is the profile whose weights exist and it leaves headroom for a second small model. This is a weights-availability decision, not a performance one, and it is annotated as such in `config/profiles.json`.

**GroktoCrawl and Qdrant do not exist.** No directory, file, or configuration references either in either workspace; port 3002 refuses connections. The only match anywhere is `marketing-skills/tools/integrations/firecrawl.md`, a documentation page. Meanwhile `F:\KyThuatNenAIMoi\core\agent_memory.py` already implements SQLite FTS5 + vector + RRF with a documented embedding-backend fallback chain and 24/24 passing self-tests. Reusing it avoids duplicating proven code and avoids a Docker dependency. Scraping goes through a narrow `WebFetcher` interface so a real Firecrawl can be substituted later without touching agent code.

**The verification commands in the directive cannot run in my sandbox.** My shell is a 2-core / 3 GB Linux VM with no CUDA device (`/dev/nvidia*` does not exist) and no PowerShell. All pure-Python logic is unit-tested here; every VRAM and throughput assertion must be executed by the operator on Windows. `hardware_check.py` is built to **fail loudly** when no GPU is detectable rather than silently reporting success — a green run on a GPU-less machine would be worse than no run at all.

---

## 3. Memory footprint

Recomputed from real file sizes where available. VRAM ≈ weights + KV cache + ~0.55 GB CUDA context. That overhead constant is flagged in `BRAIN.md` §7 as an estimate; calibrate against `nvidia-smi`.

| | **S — SPEED** *(default)* | Q — QUALITY | T — TURBO MoE |
|---|---|---|---|
| Model | Qwen3.8-27B IQ2_XXS | Qwen3.8-27B IQ3_XXS | Qwen3.6-35B-A3B IQ1_M |
| Weights on disk | 8.39 GB *(operator-reported)* | 10.9 GB *(absent)* | **10.59 GB** *(verified)* |
| Context · KV | 8,192 · q4_0 | 94,208 · q4_0 | 32,768 · q8_0 |
| KV cache | ~0.13 GB | ~1.47 GB | ~1.0 GB *(est.)* |
| CUDA overhead | 0.55 GB | 0.55 GB | 0.55 GB |
| **VRAM total** | **~9.1 GB** | ~12.9 GB | ~12.1 GB |
| Headroom under 14.0 GB | **~4.9 GB** | ~1.1 GB | ~1.9 GB |
| Throughput | ~52 tok/s **inferred** | 50–55 tok/s **measured** | **unknown** |
| Weights present? | operator-confirmed | **no** | **yes** |

Two profiles cannot coexist: S+Q alone is ~22 GB against a 16 GB card. The ~4.9 GB that Profile S leaves free is the only slot available for a concurrent embedding model or Whisper — and it exists only because S is the smallest profile, which is precisely the tradeoff `BRAIN.md` QĐ-1 argues against taking.

Host RAM budget: Python processes and the SQLite WAL stay under ~1.5 GB; llama.cpp `mmap`s weights, so resident host RAM is page-cache backed and elastic. The 18 GB ceiling is enforced by the Sentry watchdog against the whole process tree, not estimated.

---

## 4. Persona multiplexing

One `llama-server` instance holds the model. Each agent role is a `(system_prompt, tool_allowlist, decoding_params)` triple; switching roles costs one prompt, not a model reload. This follows `BRAIN.md` QĐ-8: token throughput is an *inference budget*, and `--parallel 1` is mandatory when MTP is enabled, so concurrency at the model layer buys nothing and serialization is the honest design.

| Persona | Owns | Tools | Notes |
|---|---|---|---|
| **Orchestrator** | DAG decomposition, routing, policy | none (plans only) | Opus 5 Cowork, or local Q when present |
| **Researcher** | Web retrieval, extraction, indexing | `WebFetcher`, `AgentMemory` | stdlib HTTP + readability extraction |
| **Designer** | SVG/HTML/Tailwind/Canvas output | filesystem (write to `assets/`) | uses `canvas-design`, `frontend-design`, `brand-guidelines` skills |
| **Copywriter** | Landing copy, email sequences, CRO | `AgentMemory` (read) | uses `marketing-skills` (50 skills, v2.11.0, installed) |
| **Sentry** | Budget, hardware, kill switch | `RiskGuard` | runs on every transition, cannot be bypassed |

All five skill packs are installed under `F:\he_thong_da_tac_tu\.claude\skills\`: `anthropic-skills`, `brand-guidelines`, `canvas-design`, `frontend-design`, `marketing-skills`, `skill-creator`.

---

## 5. DAG execution flow

```
                        ┌──────────────────────┐
   high-level goal ───► │    Orchestrator      │
                        │  decompose → DAG     │
                        └──────────┬───────────┘
                                   │ nodes persisted (SQLite WAL)
                                   ▼
                        ┌──────────────────────┐
                        │  Scheduler (asyncio) │◄─── resume after crash
                        │  ready-set = deps ✓  │     from persisted state
                        └──────────┬───────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    ▼         per node            ▼
         ┌────────────────────┐          ┌────────────────────┐
         │ Sentry.pre_check() │──DENY───►│  node → BLOCKED    │
         │ budget · VRAM · RAM│          │  swarm halts       │
         │ kill-switch        │          └────────────────────┘
         └─────────┬──────────┘
                   │ ALLOW
                   ▼
         ┌────────────────────────────────────────┐
         │ persona = route(node.kind)             │
         │ LocalLLMClient.complete(persona, ctx)  │
         │   context assembled by AgentMemory     │
         │   (FTS5 + vector + RRF, not whole repo)│
         └─────────┬──────────────────────────────┘
                   │ result
                   ▼
         ┌────────────────────────────────────────┐
         │ Sentry.post_charge(actual_cost)        │
         │ node → DONE | FAILED(retry ≤ N)        │
         │ dependents re-evaluated                │
         └────────────────────────────────────────┘
```

Node states: `PENDING → READY → RUNNING → DONE | FAILED | BLOCKED | CANCELLED`. Every transition is a committed SQLite write, so a killed process resumes from the last durable state rather than restarting the DAG. Cycles are rejected at submission by topological sort. Failures retry with bounded backoff; exhausted retries mark dependents `BLOCKED` instead of running them on missing inputs.

Sentry checks run **before** each node (refuse if the projected cost would breach the cap) and charge **after** (record actual). Zero overdraft means the pre-check is a hard gate, not advisory.

---

## 6. Storage layout

```
F:\he_thong_da_tac_tu\
├── architecture_plan.md          this document
├── config\profiles.json          profile registry; VRAM claims vs. measured
├── core\
│   ├── local_llm_client.py       profile resolution, llama-server client, personas
│   ├── orchestrator.py           DAG scheduler + SQLite WAL persistence
│   ├── risk_guard.py             budget ledger, kill switch, hardware watchdog
│   ├── memory_bridge.py          adapter onto KyThuatNenAIMoi/core/agent_memory.py
│   └── web_fetcher.py            stdlib HTTP + extraction behind a swappable iface
├── simulation\sandbox_env.py     paper-run simulator + Monte Carlo
├── benchmarks\hardware_check.py  VRAM/RAM self-test under load
├── tests\                        pytest suite
├── state\engine.db               SQLite WAL (runtime)
├── logs\                         JSONL run logs (runtime)
└── assets\                       Designer output (runtime)
```

Reads from `F:\KyThuatNenAIMoi\` are read-only. No writes outside `F:\he_thong_da_tac_tu\`; no touching `C:`, `D:` (beyond reading the Profile S GGUF), or `E:`.

---

## 7. Financial safety

Simulation only. `sandbox_env.py` is a closed paper simulator — no payment SDK, no live market feed, no network egress for funds. The directive's 2-factor real-funds token is **deliberately unimplemented**; wiring an autonomous agent to real money is out of scope for this build.

Accounting invariants, enforced in `risk_guard.py`:

- All amounts are **integer minor units** (VND has no subunit, so 1 unit = 1 VND). No float ever touches a balance — `0.1 + 0.2 != 0.3` is not an acceptable failure mode in a ledger.
- **Zero overdraft.** A debit that would take the balance below zero is refused before it is applied, not compensated afterward.
- **Daily cap** evaluated on the operator's local calendar day.
- **Kill switch** is a filesystem sentinel plus an in-process flag: once tripped, every subsequent `pre_check` denies, and the state survives process restart.
- Every ledger mutation is an append-only row. Balance is derived from the sum, so the audit trail cannot silently disagree with the balance.

---

## 8. What must be verified on the host

I cannot execute any of these — no CUDA device, no PowerShell in my sandbox. Numbers below are unconfirmed until the operator runs them.

```powershell
# 1. Logic suite (no GPU needed) - should be fully green
python -m pytest F:\he_thong_da_tac_tu\tests\ -v

# 2. Confirm Profile S weights resolve and the real file size
python F:\he_thong_da_tac_tu\core\local_llm_client.py --verify-profiles

# 3. VRAM/RAM under load - REQUIRES the GPU; exits non-zero without nvidia-smi
python F:\he_thong_da_tac_tu\benchmarks\hardware_check.py --profile S --max-vram-gb 14.0

# 4. Budget and kill-switch assertions
python F:\he_thong_da_tac_tu\tests\test_risk_guard.py --budget-cap 10000

# 5. Actual throughput - replaces the INFERRED 52 tok/s with a measurement
python F:\KyThuatNenAIMoi\tools\probe_tokps.py
```

Open items, in priority order: measure Profile S throughput and retire the inferred figure; calibrate the 0.55 GB CUDA overhead constant against `nvidia-smi`; download IQ3_XXS and promote Profile Q to default per `BRAIN.md` QĐ-1; benchmark IQ1_M before trusting Profile T for anything.
