---
description: Adversarial self-review of changes before PR via tkt config
---

Run an adversarial self-review of the current branch's changes: $ARGUMENTS

1. Generate the diff: `git diff $(git merge-base HEAD origin/$(tkt cfg vcs.default_branch))...HEAD`.
2. Review as adversary across: security, types, errors, tests, style, breaking changes, performance, edge cases.
3. List findings with file+line, severity (blocker/warning/nit), description, suggested fix.
4. Fix all blockers and warnings.
5. Rebuild/re-test via `tkt cfg build.build --pkg "<pkg>"`, `tkt cfg build.test --pkg "<pkg>"`, `tkt cfg build.typecheck`.
6. Loop until zero blockers and warnings. Max 3 passes; escalate if still finding blockers.

Output: passes completed, issues by severity, remaining nits, confidence.
