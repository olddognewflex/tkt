---
name: qa-flake-triage
description: 'Classify flaky test root causes and recommend fixes. Never papers over flakes with sleep or skip — finds the actual race condition, shared state, or timing issue.'
model_tier: standard
---

# QA Flake Triage — Flaky Test Root-Cause Classification

Diagnose non-deterministic test failures. Classify the flake, identify root cause,
and propose a real fix — never `sleep()`, never `.skip()`.

## Input

- Test file path
- Failure logs (from CI or local — `gh run view <id> --log-failed` if available)
- Optionally: recent CI history showing the pattern

## Steps

### 1. Read the failing test

Read the test code. Understand:
- What it exercises
- What assertions it makes
- What setup/teardown it performs
- What external resources it touches

### 2. Read failure logs

Gather evidence:
- Error messages and stack traces
- Timing information (how long did it take?)
- Which specific assertion failed?
- What was the actual vs expected value?

If CI history is available:

```shell
# Recent runs of this test
gh run list --workflow=<workflow> --limit=20 --json conclusion,startedAt,databaseId
```

### 3. Classify the flake

| Class | Indicators | Root cause |
| --- | --- | --- |
| **Race condition** | Passes with delay, fails under load; timing-dependent assertions; async operations without proper synchronization | Missing await, no mutex, optimistic read |
| **Shared state** | Fails only when run after specific other test; passes in isolation (`-run TestX`); order-dependent | Tests mutating shared DB/file/global without cleanup |
| **Non-deterministic ordering** | Sorted assertions fail on ties; map iteration order; DB results without ORDER BY | Unstable sort, relying on insertion order |
| **Clock sensitivity** | Fails near midnight, DST transitions, or second boundaries; token expiry assertions | Using `Date.now()` / `time.Now()` without mocking |
| **External dependency** | Fails when network/service is slow or unavailable; timeout errors; connection refused | Real HTTP calls in tests, no retry/circuit-breaker |
| **Resource exhaustion** | Fails later in the suite; connection pool errors; "too many open files" | Leaked connections, file handles, goroutines |
| **Platform-specific** | Fails on CI but not local (or vice versa); OS, locale, timezone differences | Hardcoded paths, locale assumptions, Docker differences |

### 4. Gather supporting evidence

For the classified category, look for corroborating signals:

**Race condition:**
- Are there `async` operations without `await`?
- Is there shared mutable state accessed from multiple execution paths?
- Does the test use `setTimeout`/`time.Sleep` for synchronization?

**Shared state:**
- Is there a `beforeAll` without corresponding `afterAll` cleanup?
- Do tests share a database table without per-test transactions?
- Are there global variables mutated in tests?

**Non-deterministic ordering:**
- Does the assertion use strict equality on an array?
- Is the data source a Map/object with non-guaranteed key order?
- Is the DB query missing `ORDER BY`?

**Clock sensitivity:**
- Does the test compare timestamps with exact equality?
- Is there a "created X seconds ago" assertion?
- Does the test rely on `Date.now()` being stable across execution?

**External dependency:**
- Are there real HTTP calls (not mocked)?
- Is there a database/service startup race on CI?
- Are there DNS lookups that could timeout?

**Resource exhaustion:**
- Is the test suite run with connection pool limits?
- Are streams/readers properly closed in tests?
- Does `go test -race` detect goroutine leaks?

**Platform-specific:**
- Are file paths hardcoded with `/` or `\`?
- Does the test assume a specific timezone?
- Are there locale-dependent string comparisons?

### 5. Propose a fix

The fix must be a **real solution**, not a workaround:

| Class | Correct fix | FORBIDDEN fix |
| --- | --- | --- |
| Race condition | Add proper synchronization (mutex, channel, await) | `sleep(5000)` |
| Shared state | Per-test isolation (transaction, temp dir, fresh instance) | Running tests sequentially |
| Non-deterministic ordering | Remove order dependency from assertion, add stable sort | `.skip()` |
| Clock sensitivity | Mock time, use relative comparisons | Widening tolerance to minutes |
| External dependency | Mock the external service, use test containers | Adding retry loops with backoff |
| Resource exhaustion | Fix the leak (close connections, cancel contexts) | Increasing pool size |
| Platform-specific | Use path.join, normalize TZ, use t.TempDir() | Skipping on specific platforms |

### 6. Confidence assessment

| Level | Meaning |
| --- | --- |
| **High** | Root cause identified with strong evidence (reproducer exists) |
| **Medium** | Classification is likely, evidence is circumstantial |
| **Low** | Multiple possible causes, need more data to confirm |

## Output

```markdown
# Flake Triage — <test name>

## Classification: <class>
**Confidence:** High / Medium / Low

## Evidence
- <observation 1>
- <observation 2>
- <CI history pattern>

## Root Cause
<explanation of why this test is non-deterministic>

## Proposed Fix
```<language>
// Before (flaky)
<current code>

// After (stable)
<fixed code>
```

## Verification
- Run `<command>` N times to confirm stability
- Expected: 0 failures in N runs

## If Fix Doesn't Work
- Alternative hypothesis: <other possible cause>
- Additional investigation: <what to check next>
```

## Guardrails

- **Never paper over the flake.** `sleep()`, `.skip()`, widening tolerances,
  increasing timeouts — all forbidden. These hide the problem.
- **Never weaken assertions.** If the assertion is correct, the code or test
  setup is wrong — fix that.
- **Read-only ticketing.** Use `tkt view` and `tkt cfg` only.
- **May modify test files.** This skill can write fixes to test files (same
  scope as `qa-author`: `**/*.test.*`, `**/*.spec.*`, `**/__tests__/**`,
  `**/test/**`).
- **Never modify implementation code.** If the implementation has a race
  condition, document it and recommend a fix — don't apply it.
- **No git mutations.** Never commit or push.
