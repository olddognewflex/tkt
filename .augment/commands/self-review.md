# /self-review

Adversarial review of your own changes. Run AFTER implementation + tests pass, BEFORE opening a PR.

## Steps

1. Generate diff: `git diff $(git merge-base HEAD origin/$(tkt cfg vcs.default_branch))...HEAD`.
2. Review as adversary across: security, types, errors, tests, style, breaking changes, performance, edge cases.
3. List findings: file+line, severity, description, suggested fix.
4. Fix all blockers and warnings.
5. Rebuild/re-test via `tkt cfg build.*`.
6. Loop until zero blockers and warnings. Max 3 passes.

## Rules

- Toolchain comes from `tkt cfg`.
- Max 3 passes; escalate if still finding blockers.
