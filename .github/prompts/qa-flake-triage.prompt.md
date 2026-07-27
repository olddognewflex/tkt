---
mode: agent
description: 'Classify a flaky test root cause and propose a real fix — never sleep, never skip.'
tools: ['search/codebase', 'terminal', 'file']
---

# QA Flake Triage

Diagnose a non-deterministic test failure. Classify it, find the root cause, propose a real fix.

## Steps

1. Read the failing test: what it exercises, what it asserts, its setup/teardown, what external resources it touches.
2. Gather failure evidence — error and stack trace, timing, which assertion failed, actual vs expected. CI history if available:
   ```shell
   gh run view <id> --log-failed
   gh run list --workflow=<workflow> --limit=20 --json conclusion,startedAt,databaseId
   ```
3. Classify:
   - **Race condition** — passes with delay, fails under load; async without proper synchronization
   - **Shared state** — passes in isolation, fails after a specific other test; order-dependent
   - **Non-deterministic ordering** — assertions on ties, map iteration order, query missing `ORDER BY`
   - **Clock sensitivity** — fails near midnight, DST, or second boundaries; unmocked `Date.now()`/`time.Now()`
   - **External dependency** — timeouts, connection refused, real network calls in tests
   - **Resource exhaustion** — fails later in the suite; pool errors, leaked handles or goroutines
   - **Platform-specific** — CI vs local; hardcoded paths, locale or timezone assumptions
4. Corroborate the classification with specific signals in the code before committing to it.
5. Propose the **real** fix, never the workaround:

   | Class | Correct fix | Forbidden |
   |---|---|---|
   | Race | proper synchronization (mutex, channel, await) | `sleep(5000)` |
   | Shared state | per-test isolation (transaction, temp dir, fresh instance) | forcing sequential runs |
   | Ordering | remove order dependency, add stable sort | `.skip()` |
   | Clock | mock time, use relative comparisons | widening tolerance |
   | External dep | mock the service, use test containers | retry loops |
   | Resource | fix the leak (close, cancel contexts) | raising pool size |
   | Platform | `path.join`, normalized TZ, `t.TempDir()` | skipping on a platform |

6. State confidence: high (reproducer exists), medium (circumstantial), low (needs more data).

## Output

Classification + confidence, evidence, root cause, before/after fix diff, a verification command with expected 0 failures over N runs, and an alternative hypothesis if the fix doesn't hold.

## Rules

- Never paper over the flake — no `sleep()`, `.skip()`, widened tolerances, or raised timeouts.
- Never weaken a correct assertion; fix the code or the setup instead.
- May modify test files only. Never modify implementation code — document the race and recommend the fix.
- Read-only ticketing. No commits or pushes.
