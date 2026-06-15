---
mode: agent
description: 'Monitor CI on a PR, diagnose failures, fix, and loop until green via tkt.'
tools: ['terminal']
---

# CI Fix

Monitor CI on a PR. When checks fail, diagnose from logs, fix, push again, loop until green. Git host via `gh`; build + ticketing via `tkt cfg` / `tkt`.

## Steps

1. Wait for CI to settle (no pending checks) using `gh pr checks "$PR" --json bucket`.
2. Identify failed checks.
3. Get logs with `gh run view <run-id> --log-failed`.
4. Diagnose: type error, test failure, service down, bad import, lint, stale branch.
5. Apply the minimal fix only.
6. Rebuild/re-test via `tkt cfg build.*`.
7. Commit and push: `git commit -m "fix(<scope>): resolve CI failure — <brief>" && git push`.
8. Loop. Max 3 iterations; escalate if still failing.

## Output

- CI status: green / still failing
- Fixes applied
- Escalation note if stuck

## Rules

- Toolchain from `tkt cfg`.
- Rerun flaky failures once; treat second failure as real.
- All ticketing via `tkt`.
