---
mode: agent
description: 'Generate a structured test plan from ticket requirements or a spec via tkt.'
tools: ['search/codebase', 'terminal', 'file']
---

# QA Strategy

Map requirements to concrete test cases. Prioritize failure modes over happy paths — assume the code is wrong until proven otherwise. This produces the plan that `qa-author` implements; it does NOT write test files.

## Steps

1. Extract acceptance criteria:
   ```shell
   tkt view "$KEY" --json | jq -r '.acceptance[]'
   ```
   If the input is a spec document instead, extract testable assertions from its text.
2. For each criterion, walk the Break-It Checklist and enumerate cases across: boundary (0, 1, n, n+1, max, negative, empty), null/missing, authorization (horizontal, vertical, expiry), concurrency (TOCTOU, pool exhaustion), idempotency, partial failure, time (timezone, DST, expiry boundary), pagination (unstable sort, cursor drift), encoding (unicode, injection, path traversal), performance (N+1, unbounded), error propagation.
3. Skip dimensions that don't apply — but state which you considered and dismissed.
4. Assign priority: P0 blocks release (security, data loss, core broken), P1 fix before ship, P2 nice to have.
5. Flag **untested** criteria (no case mapped) and **untestable** ones (vague or no definition of done — recommend how to make them testable).
6. Produce a risk table: what breaks, severity, likelihood, mitigation.
7. If BDD is configured (`tkt cfg build.bdd` exits 0), also emit `.feature` files in Gherkin.

## Output

`TEST-PLAN.md` with: requirements coverage matrix, test cases (each with type, priority, dimension, preconditions, steps, expected, and how to verify it fails against buggy code), untested/untestable requirements, risk table, and dimensions considered.

## Rules

- Read-only ticketing — `tkt view` and `tkt cfg` only. Never transition, comment, or create.
- No code changes. This produces a plan, not tests.
- No happy-path padding. Focus on what the developer will miss.
- Every case needs specific inputs and expected outputs. "Test that it handles errors" is not a test case.
