---
name: jev-system-one-router
description: TypeSafe JEV System 1 fast judgment engine for dynamic model routing (/jv on, /jv off), 10ms skill gating across 110+ skills, and 70-90% token cost optimization.
---

# JEV + CLAUDE CODE / CODEX / ANTIGRAVITY OPTIMIZER (SYSTEM 1 ARCHITECTURE)

Based on TypeSafe AI (founded by Diogo Almeida - ex-OpenAI/ChatGPT).

## 1. Core Operating Philosophy
- **System 1 (JEV)**: Fast reflex, instant classification, structured typed output (`Boolean`, `Choice`, `Score`). Sub-10ms latency, zero generation tokens.
- **System 2 (Frontier LLM / Claude / Codex / Gemini)**: Deep reasoning, architecture, code synthesis.

---

## 2. Capabilities & Commands

### A. Dynamic Model Routing (`/jv route`)
Evaluates prompt complexity to prevent using expensive Frontier models for trivial tasks:
- **Haiku / Flash ($0.80/M)**: File search, grep, regex, linting, git status, typo fixes, small scripts.
- **Sonnet / Flash High ($3.00/M)**: Feature implementation, refactoring, unit tests, bug fixing.
- **Opus / Pro ($15.00/M)**: Multi-file architectural redesign, crypto protocols, formal verification.

### B. Fast Skill Gate (10ms Selection)
Instead of dumping 40,000+ tokens of 110+ skill descriptions into the LLM system prompt:
1. JEV scans skill keywords in milliseconds.
2. Selects top 1-2 candidate skills.
3. Injects only the matching skills into the context.

### C. CLI Commands & Toggles
```bash
# Run benchmark & token savings report
python F:/JEV_AI/jev-claude-optimizer/jev_cli.py --benchmark

# Test dynamic model route for a prompt
python F:/JEV_AI/jev-claude-optimizer/jev_cli.py --route "Viết test case cho hàm đăng nhập"

# Test 10ms skill selector
python F:/JEV_AI/jev-claude-optimizer/jev_cli.py --skill "Thiết lập bot Binance Futures"

# Toggle router on/off
python F:/JEV_AI/jev-claude-optimizer/jev_cli.py --toggle on
```

---

## 3. Storage & Integration Paths

- **Optimizer Toolkit**: `F:\JEV_AI\jev-claude-optimizer\`
- **Awesome Jev Catalog**: `F:\JEV_AI\awesome-jev-projects\`
- **AI Hedge Fund**: `F:\JEV_AI\ai-hedge-fund\`
- **TypeSafe SDKs**: `F:\JEV_AI\typesafe-sdk-python\`, `F:\JEV_AI\typesafe-sdk-js\`
