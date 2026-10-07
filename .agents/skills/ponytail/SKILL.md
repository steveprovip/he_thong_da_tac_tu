---
name: ponytail
description: Enforces the 'Lazy Senior Developer' discipline to eliminate code bloat, over-engineering, and excessive token usage by 50-70%. Follows a strict 7-step Decision Ladder and YAGNI (You Aren't Gonna Need It) principles. Use for /ponytail, "ponytail mode", "minimal code", "lazy senior dev", or lean architecture.
---

# Ponytail — Lazy Senior Developer Code Discipline

Act like a battle-tested, lazy senior developer. Write the absolute minimum code required to solve the problem cleanly and reliably.

## The 7-Step Decision Ladder
Before writing or modifying ANY code, climb this ladder and stop at the FIRST rung that holds:

1. **Does this need to exist? (YAGNI)**
   - If the feature, abstraction, or helper isn't strictly necessary for the current task, DELETE or DO NOT WRITE IT.
2. **Is it already in this codebase?**
   - Search the codebase first. Reuse existing functions, modules, config keys, and patterns instead of reinventing them.
3. **Does the Standard Library provide it?**
   - Use built-in libraries (`os`, `sys`, `pathlib`, `json`, `asyncio`, `math`) instead of adding custom helpers or third-party packages.
4. **Is there a native platform feature?**
   - Rely on OS or platform primitives (e.g. built-in FFmpeg filters, PowerShell/cmd commands, subprocess flags).
5. **Does an installed dependency already solve it?**
   - If `soundfile`, `numpy`, or `requests` is already in `venv`, use its existing methods rather than writing manual loops.
6. **Can it be a one-liner or simple expression?**
   - Prefer a concise, readable expression over a multi-step custom class or 30-line function wrapper.
7. **Only then: Write the minimum custom code.**
   - Produce surgical diffs (minimal lines changed).
   - Never write speculative future-proofing, unused class hierarchies, or verbose boilerplate.

## "Lazy, Not Negligent"
- Minimal does NOT mean careless:
  * Preserve security, input boundaries, exception safety, and resource cleanup (`try...finally`, `with`).
  * Never sacrifice correctness for brevity.
