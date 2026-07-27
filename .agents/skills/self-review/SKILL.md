---
name: self-review
description: 'Adversarial self-review of changes before PR. Reviews diff, finds issues, fixes them, loops until clean. Build commands via tkt config; no ticketing.'
model_tier: standard
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

### 2. Review as adversary (→ `sdlc-reviewer`)

**Delegate to `sdlc-reviewer`**, passing the diff and the plan/ticket summary. The
point is separation: the reviewer is a fresh context that did not write the code,
so it judges the evidence rather than recalling the intent.

**Fallback** (no subagent support): run this pass yourself, but explicitly — re-read
the diff from scratch and argue against it before approving. Pretend it is a
stranger's PR. Do not rubber-stamp your own code.

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
suggested fix. Plus an overall verdict — PASS (zero blockers, zero warnings) skips
straight to the final check.

The reviewer produces findings only; the author context fixes them in step 4.

### 4. Fix blockers and warnings

Fix all blocker + warning items. Don't skip.

**Test gaps are not fixed here.** If the reviewer flags "new code without tests" or
"tests that assert nothing meaningful," do **not** write tests inline — a fix-pass
test written to close a finding tends to assert whatever the code already does.
Note the gap and hand it to `qa-author` (or the `qa-engineer` agent) after this
loop completes, which writes adversarial tests with red-before-green verification.

Format the flag as:

```
⚠️ TEST GAP: <file:line> — <description>
→ Recommend: invoke `qa-author` targeting <file/module> after self-review passes.
```

### 5. Rebuild and re-test (→ `sdlc-verifier`)

**Delegate to `sdlc-verifier`**, which runs the configured checks and returns a
binary PASS/FAIL with the exact commands and their output as evidence. It never
fixes anything — a FAIL comes back here.

**Fallback:** run them yourself.

```shell
eval "$(tkt cfg build.build --pkg "<pkg>")"
eval "$(tkt cfg build.test --pkg "<pkg>")"
eval "$(tkt cfg build.typecheck)"
```

### 5b. Run behavior specs — non-blocking (if configured)

If the repo binds a behavior-spec runner, run it as a **reported, non-blocking**
signal, mirroring the non-strict BDD posture: pending scenarios inform the author
but never block the flow. Skip cleanly when `build.bdd` is unset. See
[docs/behavior-specs.md](../../docs/behavior-specs.md).

```shell
# Only a missing key (tkt cfg exit 4 = NotFoundError) is a clean skip.
# Surface any other failure (e.g. exit 2 = bad/missing config) instead of
# silently swallowing it. On success stderr is empty, so 2>&1 is safe to eval.
out="$(tkt cfg build.bdd --pkg "<pkg>" 2>&1)"; rc=$?
if [ "$rc" -eq 0 ]; then
  eval "$out" || echo "bdd: behavior-spec runner exited non-zero (non-blocking)"
elif [ "$rc" -ne 4 ]; then
  echo "bdd: could not resolve build.bdd (tkt cfg exit $rc): $out" >&2
fi
```

### 6. Loop

Repeat until a pass yields **zero blockers and zero warnings**. Max 3 passes; if
still finding blockers, stop and flag for human review.

## Output

- Passes completed
- Issues found/fixed (count by severity)
- Remaining nits
- Confidence: high / medium / low
