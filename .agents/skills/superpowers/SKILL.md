---
name: superpowers
description: Kenton Varda & Obra software development superpower framework enforcing strict SDLC gates: Brainstorming -> Spec -> Plan -> TDD -> Review -> Plan Execution.
---

# Superpowers: Enforced Engineering Process Framework

## Overview
Superpowers enforces a disciplined, non-bypassable software development lifecycle (SDLC) for AI coding agents to eliminate hallucinated features, unverified code, and premature implementation.

## The 6 Mandatory Process Gates
1. **Brainstorming & Requirements**:
   - Clarify edge cases, non-functional requirements, and architectural boundaries before writing any code.
2. **Specification (Spec)**:
   - Formulate unambiguous input/output contracts, API specifications, and data schemas.
3. **Execution Plan**:
   - Deconstruct complex tasks into atomic, sequentially ordered, verifiable phases.
4. **Test-Driven Development (TDD)**:
   - Write failing unit/integration tests that define the contract before writing production code.
5. **Implementation**:
   - Implement only what is required to make tests pass (prevent scope creep).
6. **Code Review & Verification**:
   - Review diffs for regressions, performance overhead, security vulnerabilities, and code cleanliness.

## Operational Invariants
- Never write production code without an explicit specification and test coverage.
- Never mark a task complete until all tests pass in a clean environment.
