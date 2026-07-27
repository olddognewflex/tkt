---
name: qa-critique
description: 'Evaluate existing test suite quality: coverage gaps, assertion strength, false confidence detection, Break-It Checklist compliance. Produces a scored assessment.'
model_tier: deep
---

# QA Critique — Test Suite Quality Assessment

Evaluate an existing test suite for adequacy. Finds coverage gaps, weak
assertions, false confidence, and missing adversarial scenarios. Produces a
scored assessment with prioritized recommendations.

This skill does NOT write tests — it identifies what's missing so `qa-author`
can fill the gaps.

## Input

- Test file paths (or a diff containing test changes)
- Optionally: the implementation files they test

## Steps

### 1. Read the test files

Read all test files in scope. Understand:
- What modules/functions are under test
- What scenarios are exercised
- What assertions are made
- How tests are structured (setup/teardown, isolation)

### 2. Read the implementation under test

Read the implementation code. Identify:
- All branches and code paths
- External dependencies and failure modes
- Authorization checks (or their absence)
- Error handling paths
- Edge cases in business logic

### 3. Evaluate coverage gaps

For each public function / endpoint / handler in the implementation:
- Is it tested at all?
- Which branches have no test?
- Which error paths are unexercised?

### 4. Evaluate assertion quality

For each assertion in the test suite:

| Quality level | Example | Verdict |
| --- | --- | --- |
| **Strong** | `expect(result.amount).toBe(150.00)` | Good |
| **Adequate** | `expect(result).toMatchObject({...})` | Acceptable |
| **Weak** | `expect(result).toBeDefined()` | Flag |
| **Meaningless** | `expect(true).toBe(true)` | Critical flag |
| **Tautological** | Tests that pass regardless of implementation | Critical flag |

### 5. Detect false confidence

Tests that give a feeling of safety without providing it:

- **Mock-heavy tests:** If the mock covers 80% of the code path, you're testing
  the remaining 20% plus your mock setup. The integration gap is where bugs hide.
- **Overly specific mocks:** Mock returns exactly what the code expects — proves
  only that the code works when given perfect inputs.
- **Assertion on mock calls only:** Verifying a function was called doesn't verify
  the *result* is correct.
- **Try/catch swallowing:** Test catches errors without asserting on them.

### 6. Walk the Break-It Checklist

Check which dimensions from the checklist are exercised:

| Dimension | Exercised? | Missing scenarios |
| --- | --- | --- |
| Boundary (0, 1, n, n+1, max, negative, empty) | | |
| Null / Undefined / Missing | | |
| Authorization (horizontal, vertical, expiry) | | |
| Concurrency (races, TOCTOU, pool exhaustion) | | |
| Idempotency (double-submit, retry) | | |
| Partial failure (DB + API mismatch) | | |
| Time (timezone, DST, expiry boundary) | | |
| Pagination (unstable sort, cursor drift) | | |
| Encoding (unicode, injection, path traversal) | | |
| Performance (N+1, unbounded, missing index) | | |
| Error propagation (safe messages, correct codes) | | |

### 7. Evaluate test isolation

- Shared mutable state between tests?
- Test ordering dependencies?
- Global setup that hides individual test requirements?
- Tests that only pass when run in a specific order?

### 8. Evaluate maintenance burden

- Overly brittle tests that break on refactors (testing implementation details)?
- Snapshot tests without targeted assertions?
- Deep mock hierarchies that mirror implementation structure?

### 9. Score and produce report

Score on a 1–10 scale:

| Score | Meaning |
| --- | --- |
| 9–10 | Adversarial-quality suite, covers failure modes |
| 7–8 | Good coverage, some gaps in edge cases |
| 5–6 | Happy-path coverage, missing adversarial scenarios |
| 3–4 | Sparse, weak assertions, significant gaps |
| 1–2 | Cosmetic tests only, false confidence |

## Output

```markdown
# Test Suite Critique — <file/module>

## Score: N/10

## Coverage Gaps (Critical)
- [ ] <missing test scenario>
- [ ] <untested branch/path>

## Weak Assertions
- `<file>:<line>` — <what's wrong with the assertion>

## False Confidence
- `<file>:<line>` — <why this test provides false safety>

## Missing from Break-It Checklist
- [ ] <dimension>: <specific missing scenario>

## Test Isolation Issues
- <shared state, ordering dependencies>

## Maintenance Concerns
- <brittle tests, over-mocking>

## Recommendations (prioritized)
1. (P0) <critical gap to fill>
2. (P0) <security test to add>
3. (P1) <important edge case>
4. (P2) <nice-to-have improvement>

## Dimensions Assessed

| Dimension | Coverage | Notes |
|---|---|---|
| Boundary | Partial | Missing n+1 for pagination |
| Authorization | None | No authz tests at all |
| ... | ... | ... |
```

## Guardrails

- **Read-only.** Do not modify any files.
- **No ticket operations.** Never `tkt transition`, `tkt comment`,
  `tkt create`.
- **No git mutations.** Never commit, push, or modify history.
- **Concrete over vague.** Every finding must reference a specific file:line and
  explain what's wrong and what should be there instead.
- **No false positives from ignorance.** If you can't determine whether a test is
  adequate without reading more context, say so rather than guessing.
