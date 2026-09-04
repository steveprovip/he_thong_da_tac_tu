# GrokTOPUS — Multi-Agent Engineering & Development Protocol (AGENTS.md)

> This document defines the operational directives for any AI coding assistant collaborating on this repository.

## System Mission
Build, optimize, and maintain **GrokTOPUS**: an 8-Agent Quantitative Swarm Trading System operating under strict computational and financial safety bounds.

## Architectural Mandates
1. **Safety First:** The system operates in simulation/paper mode by default. All real execution paths require cryptographic tokens.
2. **Fail-Closed Design:** Any malformed input, missing vote, out-of-range confidence, or stale market feed must fail closed (cancel proposal or abort cycle).
3. **Determinism & Math:** Zero floating-point drift in accounting. All currency values are stored as 64-bit integers (`cents` or `VND`).
4. **Hardware Compliance:** Max VRAM $\le 10.0$ GB. When running on low-resource machines or laptops without dedicated GPUs, always use `heuristic_brain` (CPU-only, 0 VRAM, <200 MB RAM).

## Key Files & References
- Comprehensive Guide: [`AI_HANDOVER_GUIDE.md`](./AI_HANDOVER_GUIDE.md)
- User Documentation: [`README.md`](./README.md)
- Mathematical & Consensus Spec: [`GROKTOPUS.md`](./GROKTOPUS.md)
- Configuration: [`config/groktopus_agents.json`](./config/groktopus_agents.json)

## Verification Protocol
Before proposing or committing any code changes:
1. Run the test suite: `python -m pytest tests/ -q` (must be 100% passing).
2. Validate hardware/memory profile: `python benchmarks/hardware_check.py --profile S`.
3. Check risk guard status: `python core/risk_guard.py --status`.
