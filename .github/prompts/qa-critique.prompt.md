---
mode: agent
description: 'Score an existing test suite for coverage gaps, assertion strength, and false confidence.'
tools: ['search/codebase', 'terminal', 'file']
---

# QA Critique

Evaluate an existing test suite for adequacy. Identifies what's missing so `qa-author` can fill it. Does NOT write tests.

## Steps

1. Read the test files in scope: what's under test, what scenarios run, what's asserted, how isolation is handled.
2. Read the implementation: every branch, external dependency, authorization check (or its absence), error path.
3. **Coverage gaps** — per public function/endpoint/handler: tested at all? which branches have no test? which error paths are unexercised?
4. **Assertion quality** — strong (`expect(result.amount).toBe(150.00)`) → adequate (`toMatchObject`) → weak (`toBeDefined()` alone, flag) → meaningless (`expect(true).toBe(true)`, critical) → tautological (passes regardless of implementation, critical).
5. **False confidence** — mock-heavy tests where the mock covers most of the path; mocks returning exactly what the code expects; assertions on mock calls rather than results; try/catch that swallows without asserting.
6. **Break-It Checklist** — mark each dimension exercised or missing: boundary, null/missing, authorization, concurrency, idempotency, partial failure, time, pagination, encoding, performance, error propagation.
7. **Isolation** — shared mutable state, ordering dependencies, global setup hiding per-test requirements.
8. **Maintenance burden** — brittle tests coupled to implementation details, snapshots without targeted assertions, deep mock hierarchies.
9. Score 1–10: 9–10 adversarial quality · 7–8 good with edge-case gaps · 5–6 happy-path only · 3–4 sparse and weak · 1–2 cosmetic, false confidence.

## Output

Markdown critique with the score, coverage gaps, weak assertions (file:line), false-confidence findings, missing Break-It dimensions, isolation and maintenance concerns, and prioritized P0/P1/P2 recommendations.

## Rules

- Read-only. Modify nothing.
- No ticket operations, no git mutations.
- Every finding cites a specific file:line and says what should be there instead.
- If you can't judge adequacy without more context, say so rather than guessing.
