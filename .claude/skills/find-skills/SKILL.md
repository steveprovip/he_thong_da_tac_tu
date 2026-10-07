---
name: find-skills
description: Discover, inspect, and load agent skills automatically from local workspaces and community registries (agenticskills.io, skills.sh, autoskills).
---

# Find & Auto Skills

References:
- https://github.com/midudev/autoskills
- https://agenticskills.io/skills/find-skills

## Capabilities
1. **Auto-Detection (`autoskills`)**:
   - Inspects tech stack (Python, Node, Rust, React, FastAPI, etc.) and project manifests.
   - Recommends and configures matching skills into `.claude/skills/`, `.codex/skills/`, and `.agents/skills/`.
2. **Registry Lookup (`find-skills`)**:
   - Locates domain-specific skills for quantitative finance, HFT, testing, and agent orchestration.
   - Loads skills on-demand without bloating context windows.
