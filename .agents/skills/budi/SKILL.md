---
name: budi
description: "Local-first AI agent token and cost analytics tool tracking spend across Cursor, Claude Code, Codex, and Copilot. Use when checking token usage, cost acceleration, session vitals, budget guardrails, or running /budi."
---

# Budi AI Token & Cost Analytics

Budi is a local-first cost tracking daemon that tails agent transcript logs directly on disk without intercepting network traffic or leaking prompts.

## Quick CLI Usage (Windows & Cross-Platform)

- **View Live Session Vitals & Context Bloat**:
  ```powershell
  & "$env:LOCALAPPDATA\budi\bin\budi.exe" sessions current
  ```

- **View 30-Day Cost & Token Breakdown**:
  ```powershell
  & "$env:LOCALAPPDATA\budi\bin\budi.exe" stats -p 30d
  ```

- **View Today / 7-Day Stats**:
  ```powershell
  & "$env:LOCALAPPDATA\budi\bin\budi.exe" stats -p 1d
  & "$env:LOCALAPPDATA\budi\bin\budi.exe" stats -p 7d
  ```

- **Health Check & Parser Status**:
  ```powershell
  & "$env:LOCALAPPDATA\budi\bin\budi.exe" doctor
  ```

- **Backfill Historical Transcripts**:
  ```powershell
  & "$env:LOCALAPPDATA\budi\bin\budi.exe" db import
  ```

## Monitored Agents
- **Cursor**: Tails project agent transcripts under `~/.cursor/projects`
- **Claude Code**: Tails project session logs under `~/.claude/projects`
- **OpenAI Codex**: Tails logs under `~/.codex/sessions`
- **GitHub Copilot / VS Code**: Tails logs under workspaceStorage and session-state
- **Google Antigravity / IDE**: Monitored via transcript logs under conversation brain paths
