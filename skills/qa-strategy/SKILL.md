---
name: qa-strategy
description: 'Generate a structured test plan from ticket requirements or a spec document. Maps acceptance criteria to test cases with priority, risk analysis, and coverage gaps. Provider-agnostic via tkt.'
model_tier: deep
---

# QA Strategy — Test Plan from Spec

Produce a structured test plan that maps requirements to concrete test cases.
Prioritizes failure modes over happy paths. Operates under the assumption that
the code is wrong until proven otherwise.

This skill does NOT write test files — it produces the plan that `qa-author`
executes against.

## Input

- Ticket key (`$KEY`) — requirements read via `tkt view "$KEY" --json`
- OR a spec/requirements document path

## Steps

### 1. Extract acceptance criteria

```shell
tkt view "$KEY" --json | jq -r '.acceptance[]'
```

If input is a document rather than a ticket, extract testable assertions from
the spec text.

### 2. Walk the Break-It Checklist per criterion

For each acceptance criterion, enumerate test cases across these dimensions:

| Dimension | What to enumerate |
| --- | --- |
| **Boundary** | 0, 1, n, n+1, max, negative, empty, single-element |
| **Null/Missing** | null vs undefined vs empty, missing keys, optional chaining traps |
| **Authorization** | Horizontal escalation, vertical escalation, token expiry, resource enumeration |
| **Concurrency** | Two writes same row, TOCTOU, pool exhaustion, deadlocks |
| **Idempotency** | Double-submit, retry after timeout, partial retry |
| **Partial failure** | DB write + API call mismatch, multi-step rollback |
| **Time** | Timezones, DST, clock skew, expiry at boundary |
| **Pagination** | Unstable sort, cursor drift, page=0, page beyond total |
| **Encoding** | Unicode, RTL, 10k-char inputs, injection, path traversal, null bytes |
| **Performance** | N+1 queries, over-fetching, unbounded loops, missing indexes |
| **Error propagation** | Safe error messages, correct status codes, retry-after headers |

Not every dimension applies to every criterion. Skip irrelevant ones — but
explicitly state which were considered and dismissed.

### 3. Assign priority

| Priority | Meaning |
| --- | --- |
| P0 | Blocks release — security, data loss, core functionality broken |
| P1 | Should fix before ship — important edge cases, degraded UX |
| P2 | Nice to have — unlikely scenarios, cosmetic |

### 4. Flag untested and untestable requirements

- **Untested:** Acceptance criterion with no test case mapped to it
- **Untestable:** Vague, subjective, or missing definition of done (e.g. "should
  feel fast" — recommend: define latency SLO)

### 5. Produce risk table

For each identified risk:
- What can break
- Severity (Critical / High / Medium / Low)
- Likelihood (High / Medium / Low)
- Mitigation (what the implementation should do)

### 6. Optional: BDD output

If BDD is configured (`tkt cfg build.bdd` resolves without error), produce
`.feature` files in Gherkin format alongside `TEST-PLAN.md`.

## Output

`TEST-PLAN.md` in the following structure:

```markdown
# Test Plan — <KEY>: <summary>

## Requirements Coverage Matrix

| # | Requirement (from AC) | Test cases | Priority | Status |
|---|---|---|---|---|
| 1 | <requirement text> | TC-1.1, TC-1.2, TC-1.3 | P0 | Planned |

## Test Cases

### TC-1.1: <descriptive name>
- **Type:** Unit / Integration / E2E
- **Priority:** P0 / P1 / P2
- **Dimension:** Boundary / Auth / Concurrency / ...
- **Preconditions:** ...
- **Steps:** ...
- **Expected:** ...
- **Red-before-green:** How to verify this fails against buggy code

### TC-1.2: ...

## Untested Requirements
- AC #N (<text>) — <reason it has no test>

## Untestable Requirements
- AC #N (<text>) — <why it's untestable> — <recommendation>

## Risk Table

| Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|
| <what can break> | Critical | Medium | <what to do> |

## Checklist Dimensions Considered

| Dimension | Applicable | Test cases |
|---|---|---|
| Boundary | Yes | TC-1.2, TC-1.3 |
| Authorization | No (no user-facing auth in this feature) | — |
| ... | ... | ... |
```

## Guardrails

- **Read-only ticketing.** Use `tkt view` and `tkt cfg` only. Never
  transition, comment, or create tickets.
- **No code changes.** This skill produces a plan document, not code.
- **No happy-path padding.** Do not enumerate obvious happy-path tests that any
  developer would write. Focus on what they'll miss.
- **Concrete over vague.** Every test case must have specific inputs and expected
  outputs. "Test that it handles errors" is not a test case.
