# /self-review

Adversarial review of your own changes. Run AFTER implementation + tests pass, BEFORE opening a PR.

## Steps

1. Generate diff:
   ```shell
   git diff $(git merge-base HEAD origin/$(tkt cfg vcs.default_branch))...HEAD
   ```
2. Review as adversary across:
   - Security: injection, auth bypass, secrets, unvalidated input
   - Types: loose types, missing null checks
   - Errors: unhandled rejections, wrong status codes
   - Tests: new code without tests, meaningless assertions
   - Style: lint conventions, debug logging
   - Breaking: changed shared exports / API contracts
   - Performance: N+1 queries, unbounded loops
   - Edge cases: empty arrays, nulls, concurrency
3. List findings: file+line, severity (blocker/warning/nit), description, suggested fix.
4. Fix all blockers and warnings.
5. Rebuild/re-test via `tkt cfg build.*`.
6. Loop until zero blockers and warnings. Max 3 passes.

## Output

- Passes completed
- Issues found/fixed by severity
- Remaining nits
- Confidence: high / medium / low

## Rules

- Toolchain comes from `tkt cfg`.
- Max 3 passes; escalate if still finding blockers.
