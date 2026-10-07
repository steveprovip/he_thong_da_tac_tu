---
name: agent-memory-dreaming
description: Lamis Mukta (Anthropic) self-prompting memory and out-of-band dreaming architecture for compounding agent intelligence across Claude, Codex, and Antigravity.
---

# AGENT MEMORY & DREAMING SYSTEM (LAMIS MUKTA ARCHITECTURE)

Based on Anthropic research by Lamis Mukta: *"Stop prompting the model manually; build a system that prompts itself."*

## 1. The 4-Layer Memory Architecture
1. **Layer 1: Static Rules (`CLAUDE.md`, `AGENTS.md`, `rules/`)**:
   - Immutable constraints, token minimization policies, output formatting, safety rails.
2. **Layer 2: Modular Skills (`skills/`)**:
   - Reusable, on-demand operational workflows loaded dynamically to preserve context.
3. **Layer 3: File-system & MCP Memory Tool (Dynamic State)**:
   - Persistent read/write stores (`~/.claude/memory/`, `~/.codex/memories/`, `~/.gemini/config/memory/`).
   - Automatically tracks project-specific findings, environment quirks, and user preferences.
4. **Layer 4: Dreaming Mechanism (Asynchronous Self-Reflection)**:
   - Out-of-band batch synthesis process that inspects past transcripts, consolidates lessons, prunes redundant data, and updates high-tier memory.

---

## 2. In-Session Self-Prompting Protocols

### A. Pre-Task Recall (Cold Start)
Before starting any multi-step task or debugging session:
1. Inspect local `learnings.md` and relevant project rules.
2. Check if a similar issue or preference has already been resolved in past sessions.
3. Inject the retrieved solution into the active reasoning loop without re-asking the user.

### B. Reactive Self-Learning (On Correction or Bug Resolution)
Whenever the user corrects your code, or when a non-trivial bug is fixed:
1. Do not merely apologize or apply a one-off fix.
2. Formulate a 1-2 line atomic learning: `[Pattern / Context] -> [Root Cause / Mistake] -> [Permanent Standard]`.
3. Append or update the learning in the persistent memory store (`learnings.md`).

---

## 3. The Dreaming & Reflection Engine (Out-of-Band Process)

The Dreaming Engine executes asynchronously (via CLI command or scheduled task) to prevent context bloat:

```bash
python ~/.gemini/scripts/dreaming_engine.py
```

### What Dreaming Does:
1. **Transcript Aggregation**: Reads session logs and execution traces from `.claude/`, `.codex/`, and `.gemini/`.
2. **Pattern Synthesis**: Groups repeated actions, recurring commands, and frequent errors.
3. **Memory Pruning (Anti-Slop / Ponytail)**: Strips obsolete notes, merges duplicates, and enforces token-dense formatting.
4. **Rule Promotion**: If a pattern occurs > 3 times, promotes it into the core `CLAUDE.md` / `AGENTS.md` / `rules/` file.

---

## 4. Storage Locations Across Agents

| Agent | Memory Store | Skills Directory | Core Instruction |
| :--- | :--- | :--- | :--- |
| **Claude Code / Desktop** | `~/.claude/memory/learnings.md` | `~/.claude/skills/` | `~/.claude/CLAUDE.md` |
| **OpenAI Codex** | `~/.codex/memories/learnings.md` | `~/.codex/skills/` | `~/.codex/AGENTS.md` |
| **Antigravity** | `~/.gemini/config/memory/learnings.md` | `~/.gemini/config/skills/` | `~/.gemini/config/rules/` |
