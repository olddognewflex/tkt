---
name: self-review
description: Adversarial self-review of changes before PR via tkt config.
trigger: manual
---

# Self-Review

Adversarial review of your own changes. Run AFTER implementation + tests pass, BEFORE opening a PR.

## Steps

1. Generate diff:
   ```shell
   // turbo
   git diff $(git merge-base HEAD origin/$(tkt cfg vcs.default_branch))...HEAD
   ```
2. Review as adversary across: security, types, errors, tests, style, breaking changes, performance, edge cases.
3. List findings: file+line, severity (blocker/warning/nit), description, suggested fix.
4. Fix all blockers and warnings.
5. Rebuild/re-test:
   ```shell
   // turbo
   tkt cfg build.build --pkg "<pkg>"
   // turbo
   tkt cfg build.test --pkg "<pkg>"
   // turbo
   tkt cfg build.typecheck
   ```
6. Loop until zero blockers and warnings. Max 3 passes.

## Output

- Passes completed
- Issues found/fixed by severity
- Remaining nits
- Confidence: high / medium / low

## Rules

- Toolchain comes from `tkt cfg`.
- Max 3 passes; escalate if still finding blockers.
