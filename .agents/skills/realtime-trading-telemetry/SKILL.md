---
name: realtime-trading-telemetry
description: Real-time loopback telemetry streaming for trading dashboards, SVG micro-animations, real equity curve mapping, dynamic consensus meters, low-overhead HTTP polling and WebSocket broadcast without CPU/GPU lockup.
---

# Real-Time Trading Telemetry & Dashboard Engineering

## 1. System Topology
- Telemetry Server: Lightweight loopback http.server.ThreadingHTTPServer on port 8765.
- Telemetry Producer: Trading bot writes atomic snapshot to data/live_telemetry.json (tmp -> rename).
- Frontend: Vanilla JavaScript + SVG + Cyberpunk Fintech CSS (Zero heavy frameworks like React/Tailwind to minimize RAM footprint).

## 2. Frontend Dynamic Rendering Best Practices
1. Falsy Guard Bug Fix:
   - Never write score = consensus.score_pct || 78.5.
   - When score is 0.0, JavaScript treats 0 as false and locks to 78.5%.
   - Always write: score = (consensus.score_pct !== undefined) ? consensus.score_pct : 0.0.
2. SVG Balance History Mapping:
   - Map equity curve from real account balance array (,984 USDT).
   - Scale SVG viewBox dynamically based on [min(equity), max(equity)].
3. Octopus Arm Animation Sync:
   - Modulate tentacle wiggle frequency and glow pulse based on consensus score and market volatility.
