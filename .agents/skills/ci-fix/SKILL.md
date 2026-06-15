---
name: ci-fix
description: 'Monitor CI status on a PR, diagnose failures from logs, fix, and re-push. Loop until green. VCS via gh; build commands + ticket comments via tkt config.'
---

# CI Fix

Monitor CI on a PR. When checks fail, diagnose from logs, fix, push again, loop
until green. Git host operations use `gh`; toolchain + ticketing come from
`.sdlc/config.toml` via `tkt cfg` / `tkt`.

## Input

- PR number (`$PR`) and the ticket key (`$KEY`, for the stuck-escalation comment)

## Steps

### 1. Check / wait for CI

```shell
gh pr checks "$PR"                                  # one-shot read

# Wait until nothing is pending (stable bucket enum; don't text-grep).
until [ "$(gh pr checks "$PR" --json bucket --jq 'all(.[]; .bucket != "pending")')" = "true" ]; do
  sleep 15
done
gh pr checks "$PR"
```

All pass after settling → done. **Do not** poll with a text grep — `skipping`
rows and check names containing `pass` cause false early exits.

### 2. Identify failed checks

```shell
gh pr checks "$PR" --json name,state,link | jq '.[] | select(.state == "FAILURE")'
```

### 3. Get failure logs

```shell
gh run view <run-id> --log-failed | head -200
```

### 4. Diagnose

| Pattern | Likely cause | Fix |
| --- | --- | --- |
| `Type error` | type-check | fix type |
| `Test failed` | regression | fix code or test |
| `ECONNREFUSED` | service not up in CI | check CI service step |
| `Module not found` | bad import / missing ext | fix import path |
| `Lint error` | style | `eval "$(tkt cfg build.lint)" -- --fix` (if supported) |
| branch behind | stale | `git rebase origin/$(tkt cfg vcs.default_branch)` |

### 5. Apply the minimal fix

Fix only the CI failure. Do not refactor unrelated code.

### 6. Rebuild locally (config toolchain)

```shell
eval "$(tkt cfg build.build --pkg "<pkg>")"
eval "$(tkt cfg build.test --pkg "<pkg>")"
eval "$(tkt cfg build.typecheck)"
```

### 7. Push

```shell
git add -A
git commit -m "fix(<scope>): resolve CI failure — <brief>"
git push
```

### 8. Loop

Repeat from step 1. **Max 3 iterations.** If still failing:

```shell
gh pr comment "$PR" --body "CI failing after 3 fix attempts. Needs human investigation. Last failure: <summary>"
tkt comment "$KEY" "CI failing after 3 attempts on PR #$PR — needs human investigation."
```

### 9. Flaky tests

Non-deterministic failure (passes locally, random timeout): `gh run rerun <run-id> --failed`
**once**. If it fails again, treat as real.

## Output

- CI status: green / still failing
- Fixes applied (commits)
- If stuck: the unresolved failure
