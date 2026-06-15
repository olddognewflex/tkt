---
name: self-review
description: 'Adversarial self-review of changes before PR. Reviews diff, finds issues, fixes them, loops until clean. Build commands via tkt config; no ticketing.'
compatibility:
  - claude
  - opencode
---

# Self-Review

Adversarial review of your own changes. Run AFTER implementation + tests pass,
BEFORE opening a PR. Loops until no blockers remain. Toolchain comes from
`.sdlc/config.toml` via `tkt cfg`; this skill has no ticketing coupling.

## Steps

### 1. Generate the diff

```shell
git diff $(git merge-base HEAD origin/$(tkt cfg vcs.default_branch))...HEAD
```

### 2. Review as adversary

Hostile-reviewer mindset. Check every changed file for:

| Category | Look for |
| --- | --- |
| **Security** | injection, auth bypass, secrets in code, unvalidated input |
| **Types** | unjustified loose types, missing null checks, unguarded casts |
| **Errors** | unhandled rejections, missing error responses, wrong status codes |
| **Tests** | new code without tests; tests that assert nothing meaningful |
| **Style** | project lint conventions; debug logging left in |
| **Breaking** | changed shared exports / existing API contracts |
| **Performance** | N+1 queries, missing indexes, unbounded loops, large payloads |
| **Edge cases** | empty arrays, nulls, concurrency, expired tokens |

Project-specific rules (import conventions, error types, logging) belong in the
project's own conventions doc — read them and apply.

### 3. List findings

Per issue: file+line, severity (**blocker** / **warning** / **nit**), description,
suggested fix.

### 4. Fix blockers and warnings

Fix all blocker + warning items. Don't skip.

### 5. Rebuild and re-test (config toolchain)

```shell
eval "$(tkt cfg build.build --pkg "<pkg>")"
eval "$(tkt cfg build.test --pkg "<pkg>")"
eval "$(tkt cfg build.typecheck)"
```

### 6. Loop

Repeat until a pass yields **zero blockers and zero warnings**. Max 3 passes; if
still finding blockers, stop and flag for human review.

## Output

- Passes completed
- Issues found/fixed (count by severity)
- Remaining nits
- Confidence: high / medium / low
