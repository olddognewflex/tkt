# QA Engineer Suite — Design Document

**Status:** Draft / Brainstorm
**Author:** Raymond Doran
**Date:** 2025-07-24

---

## 1. Problem Statement

The existing SDLC pack has skills that nibble at QA:

| Existing skill | What it does for testing | What it does NOT do |
|---|---|---|
| `self-review` | Adversarial review of diff (security, types, edge cases) | Doesn't write tests, doesn't produce a test plan, doesn't evaluate test adequacy |
| `plan-ticket` | Produces a "Test Strategy" section in the plan | Single paragraph, no matrix, no adversarial thinking |
| `ci-fix` | Fixes failing CI / flake rerun | Doesn't triage *why* tests are flaky, doesn't improve coverage |
| `sdlc-reviewer` | Flags "new code without tests" | Doesn't say *which* tests are missing or write them |

**Result:** If a user says "write tests for this" or "review this PR from a QA perspective," no single skill owns the request. Three skills half-fire, none of them wins.

**The gap:** A dedicated QA engineer persona that:
1. Forces the pessimist hat — breaks things rather than proving they work
2. Produces concrete test artifacts (files, plans, risk tables) — not essays
3. Treats acceptance criteria as the source of truth, not the implementation

---

## 2. Design Principle: Adversarial-First

An LLM asked to "write tests" will cheerfully produce a beautiful happy-path suite that proves the code does what the code does. That's tautology with a test runner, not QA.

**This suite's reason to exist is forcing the pessimist hat on.**

Every skill in this suite operates under the assumption that the code is wrong until proven otherwise. The happy path is already tested by the developer — we exist to find what they missed.

---

## 3. Scope & Boundaries

### What the QA suite owns

| Concern | Deliverable |
|---|---|
| **Test Strategy** | `TEST-PLAN.md` — requirements → test matrix mapping |
| **Test Authoring** | Test files (unit, integration, e2e) focused on edge cases and failure modes |
| **Adversarial Analysis** | Risk table: what can break, how badly, likelihood |
| **Test Suite Critique** | Coverage gaps, assertion quality, false-confidence detection |
| **Flake Triage** | Root-cause classification of non-deterministic failures |
| **Bug Reporting** | Structured reproduction steps from a failing scenario |

### What existing skills keep

| Skill | Keeps | Does NOT expand into |
|---|---|---|
| `self-review` | Pre-PR diff review (code quality lens) | Writing tests, producing test plans |
| `plan-ticket` | Implementation plan with a "Test Strategy" *summary* | Detailed test matrices (delegates to QA suite) |
| `ci-fix` | Fix CI failures, rerun flakes once | Deep flake analysis (delegates to QA suite) |
| `sdlc-reviewer` | Code review findings | Test adequacy assessment |

### Boundary rules

1. `self-review` may flag "missing tests" as a WARNING — but it does NOT write them. It says "invoke QA authoring."
2. `plan-ticket` produces a one-paragraph test strategy — the QA suite expands it into a full matrix when invoked.
3. `ci-fix` handles "make CI green." The QA suite handles "why does this test keep going red/green randomly?"
4. The QA suite never transitions tickets or opens PRs. It produces artifacts and recommendations.

---

## 4. Architecture: Skills + Sub-Agent

### Why a sub-agent, not just skills

**Author-reviewer separation is real.** The instance that wrote the code is emotionally invested in it being correct. A fresh context that only sees the diff, the requirements, and the existing test suite is meaningfully harsher.

The QA sub-agent (`qa-engineer`) is a read-only adversarial context — same trust model as `sdlc-reviewer`, extended to test analysis and authoring.

### Suite structure

```
skills/
  qa-strategy/SKILL.md          # Test plan from spec/requirements
  qa-author/SKILL.md            # Write adversarial tests (files)
  qa-critique/SKILL.md          # Evaluate existing test suite quality
  qa-flake-triage/SKILL.md      # Classify and fix flaky tests
  qa-bug-report/SKILL.md        # Structured repro from a failure

agents/
  qa-engineer.md                # Sub-agent: adversarial analysis + routing

skills/qa-author/references/
  nestjs.md
  react.md
  go.md
  cdk.md
  htmx-lambda.md
```

### Trigger phrases → Skill routing

| User says | Skill invoked |
|---|---|
| "review this PR before I merge" | `qa-critique` (test adequacy) + `qa-strategy` (gap analysis) |
| "generate tests from this spec" | `qa-strategy` (plan) → `qa-author` (files) |
| "write tests for this" | `qa-author` (with adversarial checklist) |
| "why is this test flaky?" | `qa-flake-triage` |
| "what could break here?" | `qa-engineer` agent (adversarial analysis → risk table) |
| "file a bug for this" | `qa-bug-report` |

---

## 5. The Break-It Checklist

This is the meat. Every QA skill walks this list. It cannot hand-wave.

### 5.1 Boundary Analysis
- **0, 1, n, n+1** — every numeric input
- **Empty** — empty array, empty string, empty object, `null`, `undefined`
- **Single-element** — collections with exactly one item (different code path than n)
- **Max** — at configured limits, at integer overflow, at payload size limits
- **Negative** — negative numbers, negative indexes, counts below zero

### 5.2 Null / Undefined / Missing
- `null` vs `undefined` vs empty string (TS lies constantly — `Partial<T>` doesn't mean what you think)
- Missing key vs key-present-with-null-value
- Optional chaining paths that can produce `undefined` mid-chain
- JSON.parse of responses that omit fields

### 5.3 Authorization
- **Can user A read user B's thing?** Every endpoint. Every query. Every mutation.
- Horizontal privilege escalation (same role, different tenant/org)
- Vertical privilege escalation (lower role accessing higher-role resources)
- Resource enumeration via sequential IDs
- Token expiry mid-operation
- Permission changes between authentication and resource access

### 5.4 Concurrency
- Two writes, same row, same millisecond
- Read-then-write races (TOCTOU)
- Concurrent list + delete (item disappears mid-iteration)
- Connection pool exhaustion under parallel requests
- Deadlock potential in multi-table operations

### 5.5 Idempotency & Retries
- Client sends it twice — what happens?
- Network timeout → retry → original request already succeeded
- Partial retry after partial success
- Queue consumers processing the same message twice

### 5.6 Partial Failure
- DB write succeeds, external API call doesn't — state consistency?
- Multi-step transaction: step 3 of 5 fails — what's left behind?
- S3 upload succeeds, metadata write fails
- Webhook delivery succeeds, local state update doesn't

### 5.7 Time
- Timezones: UTC vs local, server vs client
- DST transitions (the 2 AM that happens twice, the 2 AM that doesn't exist)
- Clock skew between services
- Token/session expiry at exact boundary
- Cron jobs that fire during a deploy
- "Created 0 seconds ago" edge (Date.now() comparison)

### 5.8 Pagination & Ordering
- Unstable sort with ties (insertion order varies by DB engine)
- Cursor drift when items are inserted/deleted mid-pagination
- Page size = 0, page = -1, page beyond total
- First page vs last page vs empty result set
- Concurrent writes during pagination (phantom reads)

### 5.9 Encoding & Input
- Unicode: emoji, combining characters, zero-width joiners
- RTL text in LTR contexts
- 10k-character inputs (above typical validation, below hard limit)
- Script injection in every user-writable field
- SQL injection (parameterized queries should make this impossible — verify)
- Path traversal in filenames
- Null bytes in strings
- Multi-byte characters at truncation boundaries

### 5.10 Performance & Data Volume
- N+1 queries (the existing tkt tooling detects these — the QA skill should know to reach for it)
- Over-fetching: requesting 50 fields when you need 3
- Large result sets without streaming/pagination
- Missing database indexes on filtered/sorted columns
- Unbounded loops or recursion
- Memory accumulation in long-running processes

### 5.11 Error Propagation
- Is the error message safe to show the user? (no stack traces, no internal paths)
- Does the error response match the documented schema?
- Are error codes/statuses correct? (not everything is 500)
- Retry-after headers on rate limits
- Graceful degradation vs hard failure

---

## 6. Forbidden Cheats

These are explicitly forbidden in every QA skill. Agents "fix" failing tests in ways that would get a human fired:

| Cheat | Why it's forbidden |
|---|---|
| `.skip()` or delete a failing test to get green | Deleting evidence is not fixing |
| Weaken an assertion to match observed (wrong) output | You just encoded the bug as a spec |
| Add `sleep(5000)` / `waitFor(5000)` to paper over a flake | You've hidden a race condition behind a prayer |
| Mock the unit under test | You're testing your mock, not your code |
| Assert on `expect(result).toBeDefined()` alone | That proves nothing except it didn't throw |
| Catch-all `try/catch` that swallows errors in tests | You've made the test unfailable |
| Test that passes regardless of implementation | Tautological — delete it |

### The Red-Before-Green Rule

**A new test MUST fail first against the unfixed code.**

If you write a test and it passes immediately, you've proven nothing. Either:
1. The bug doesn't exist (remove the test, investigate further)
2. The test doesn't actually exercise the failure path (fix the test)
3. The assertion is too weak (strengthen it)

This is the single highest-leverage constraint in the entire suite.

---

## 7. Skill Specifications

### 7.1 `qa-strategy` — Test Plan from Spec

**Trigger:** "generate tests from this spec," "what should we test for this ticket?"

**Input:** Ticket key (via `tkt view KEY --json`) or a spec/requirements document.

**Process:**
1. Extract acceptance criteria
2. For each criterion, enumerate: happy path, sad paths, edge cases (using the Break-It Checklist)
3. Map each test case to a priority (P0 = blocks release, P1 = should fix before ship, P2 = nice to have)
4. Flag any untested requirement (acceptance criterion with no test mapped to it)
5. Flag any untestable requirement (vague, subjective, or missing definition of done)

**Output:** `TEST-PLAN.md` (or equivalent structured artifact)

```markdown
# Test Plan — <KEY>: <summary>

## Requirements Coverage Matrix

| # | Requirement (from AC) | Test cases | Priority | Status |
|---|---|---|---|---|
| 1 | User can reset password via email | TC-1.1, TC-1.2, TC-1.3, TC-1.4 | P0 | Planned |
| 2 | Reset link expires after 1 hour | TC-2.1, TC-2.2 | P0 | Planned |

## Test Cases

### TC-1.1: Happy path — valid email, link sent
- **Type:** Integration
- **Priority:** P0
- **Steps:** ...
- **Expected:** ...

### TC-1.2: Email not found — should not reveal account existence
- **Type:** Integration
- **Priority:** P0
- **Steps:** ...
- **Expected:** Same response shape/timing as success (no oracle)

### TC-1.3: Email with unicode/punycode domain
...

## Untested Requirements
- AC #5 ("should feel fast") — no measurable criterion. Recommend: define latency SLO.

## Risk Table

| Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|
| Token collision in reset link | Critical | Low | Use crypto-random 256-bit token |
| Race: two resets same user | Medium | Medium | Last-write-wins with DB constraint |
```

### 7.2 `qa-author` — Write Adversarial Tests

**Trigger:** "write tests for this," "add tests for <file/feature>"

**Input:** File path or diff. Optionally a TEST-PLAN.md from `qa-strategy`.

**Process:**
1. Read the implementation
2. Read existing tests for the same module
3. Walk the Break-It Checklist against the implementation
4. Write test cases — prioritizing failure modes over happy paths
5. Verify each new test **fails** against a known-bad state (Red-Before-Green rule)
6. Group: unit → integration → e2e

**Output:** Test files in the project's testing convention. No essays — code.

**Stack dispatch:** Check the file extension and import patterns, then load the appropriate reference:
- `.ts` + `@nestjs/*` → `references/nestjs.md`
- `.tsx` + `react` → `references/react.md`
- `.go` → `references/go.md`
- `.ts` + `aws-cdk-lib` → `references/cdk.md`
- `.ts` + `hx-` attributes in template strings → `references/htmx-lambda.md`

### 7.3 `qa-critique` — Test Suite Quality Assessment

**Trigger:** "review this PR before I merge" (from QA perspective), "are these tests good enough?"

**Input:** Diff or file paths of test files. Optionally the implementation they test.

**Process:**
1. Read the test files
2. Read the implementation under test
3. Evaluate against:
   - **Coverage gaps:** Which branches/paths have no test?
   - **Assertion quality:** Are assertions specific enough? Would they catch a regression?
   - **False confidence:** Tests that pass regardless of implementation correctness
   - **Missing scenarios:** Walk the Break-It Checklist — what's not exercised?
   - **Test isolation:** Shared mutable state, test ordering dependencies
   - **Maintenance burden:** Overly brittle tests that break on refactors
4. Produce a scored assessment

**Output:** Critique report

```markdown
# Test Suite Critique — <file/module>

## Score: 6/10

## Coverage Gaps (Critical)
- [ ] No test for unauthorized access to GET /users/:id with another user's token
- [ ] No test for empty result set on paginated endpoint
- [ ] No test for concurrent write conflict

## Weak Assertions
- `user.spec.ts:45` — `expect(result).toBeDefined()` proves nothing
- `order.spec.ts:112` — catches error but doesn't assert the error *type*

## False Confidence
- `payment.spec.ts:78` — mocks the payment gateway AND the validation layer; tests only the orchestration glue, which is 3 lines

## Missing from Break-It Checklist
- [ ] Boundary: max pagination size
- [ ] Auth: horizontal escalation
- [ ] Time: token expiry at exact second
- [ ] Encoding: unicode in user-supplied names

## Recommendations (prioritized)
1. (P0) Add authz tests — every endpoint, user A accessing user B's resources
2. (P0) Replace `toBeDefined()` assertions with specific value/shape checks
3. (P1) Add boundary tests for pagination
4. (P2) Consider property-based testing for the validation layer
```

### 7.4 `qa-flake-triage` — Flaky Test Root-Cause Classification

**Trigger:** "why is this test flaky?" "this test keeps failing randomly"

**Input:** Test file + failure logs (from CI or local)

**Process:**
1. Read the test
2. Read recent failure logs (CI history if available via `gh run view`)
3. Classify the flake:

| Class | Indicators | Fix pattern |
|---|---|---|
| **Race condition** | Passes with delay, fails under load | Add proper synchronization, not sleep |
| **Shared state** | Fails only when run after specific other test | Isolate — teardown/setup per test |
| **Non-deterministic ordering** | Fails on sorted assertions with ties | Make sort stable or assert without order |
| **Clock sensitivity** | Fails near midnight, DST, or second boundaries | Mock time, use relative comparisons |
| **External dependency** | Fails when network/service is slow | Mock or use test containers |
| **Resource exhaustion** | Fails later in the suite | Connection/file handle leak |
| **Platform-specific** | Fails on CI but not local (or vice versa) | OS, timezone, locale, file system differences |

4. Propose a fix (not `sleep`, not `.skip`)

**Output:** Classification + fix recommendation + confidence level

### 7.5 `qa-bug-report` — Structured Reproduction

**Trigger:** "file a bug for this," "this is broken, document it"

**Input:** A failing scenario (test output, error log, user report, or observed behavior)

**Process:**
1. Identify the failure
2. Determine minimal reproduction steps
3. Classify severity and likely root cause
4. Format as a structured bug report suitable for ticket creation

**Output:**

```markdown
## Bug Report

**Summary:** <one line>
**Severity:** Critical / High / Medium / Low
**Component:** <package/module>
**Reproducibility:** Always / Intermittent / Rare

### Steps to Reproduce
1. ...
2. ...
3. ...

### Expected Behavior
...

### Actual Behavior
...

### Environment
- Branch: ...
- Commit: ...
- Relevant config: ...

### Root Cause (suspected)
...

### Suggested Fix
...
```

Optionally invoke `tkt create --type Bug --summary "..." --description "..."` if the user confirms.

---

## 8. Sub-Agent: `qa-engineer`

### Identity

```markdown
---
name: qa-engineer
description: Adversarial QA analysis — finds what the developer missed. Read + write test files only.
tools: [Read, Write, Bash]
model: sonnet
---
```

### Trust model

- **Read:** Any file in the repo
- **Write:** Only `**/*.test.*`, `**/*.spec.*`, `**/__tests__/**`, `**/TEST-PLAN.md`, `**/test/**`
- **Bash (read-only):** `git diff`, `git log`, `tkt view`, `tkt cfg`, test runners (read output only)
- **Bash (write):** Test runner execution only (to verify red-before-green)
- **NEVER:** `tkt transition`, `tkt comment`, `git commit`, `git push`, file edits outside test directories

### Persona

You are a pessimist who has seen production burn. You assume:
- The developer tested the happy path and nothing else
- Every type annotation is a lie until a runtime check proves it
- Every "this can't happen" comment is a prediction, not a fact
- The existing test suite has gaps the developer doesn't know about

Your job is NOT to prove the code works. Your job is to find where it doesn't.

### Routing

The `qa-engineer` agent acts as the primary entry point for QA requests and routes to the appropriate skill:

```
User request → qa-engineer
  ├── "what could break?" → adversarial analysis (inline) → risk table
  ├── "generate tests from spec" → qa-strategy → qa-author
  ├── "write tests" → qa-author
  ├── "review tests" / "are these tests good enough?" → qa-critique
  ├── "flaky test" → qa-flake-triage
  └── "file a bug" → qa-bug-report
```

---

## 9. Stack-Specific References

Progressive disclosure: SKILL.md stays as a router. Details pushed into `references/` so you're not burning context on Go table tests when debugging a React component.

### `references/nestjs.md`
- `Test.createTestingModule` patterns
- Provider overrides for unit isolation
- Supertest for e2e (assert response shape, status, headers)
- Transactional test DB (setup/teardown per test)
- Guard/interceptor testing in isolation

### `references/react.md`
- Testing Library philosophy: test behavior, not implementation
- `userEvent` over `fireEvent` (simulates real interaction)
- Never query by class name or test-id-less selectors
- Async act() patterns for state updates
- Accessibility assertions (`toBeAccessible`, role queries)

### `references/go.md`
- Table-driven tests (subtests with `t.Run`)
- `t.Parallel()` for concurrency safety
- No testify religion — stdlib `testing` + `cmp` is fine
- Interface mocks via manual struct, not framework magic
- `httptest.NewServer` for HTTP handler testing

### `references/cdk.md`
- `Template.fromStack()` assertions
- Snapshot tests are a diff alarm, not a test (acknowledge their limits)
- Resource property assertions for security (IAM, SGs, encryption)
- `Aspects` for cross-stack policy validation

### `references/htmx-lambda.md`
- Responses are HTML fragments — assert with cheerio/jsdom, not JSON shape
- Test `hx-*` attributes and swap targets explicitly
- Assert `HX-Trigger` response headers for event-driven UI updates
- Template rendering assertions (is the data bound correctly?)
- Lambda handler integration tests with API Gateway event shapes
- Most internet testing guidance has no idea this pattern exists — reference the actual request/response cycle

---

## 10. Integration with Existing Pipeline

### Where QA skills plug into `automated-sdlc`

```
Phase 2 (Plan)
  └── qa-strategy can expand the "Test Strategy" section into a full TEST-PLAN.md
      (optional — triggered by user or by plan-ticket detecting complex AC)

Phase 3 (Implement)
  └── Developer/executor writes implementation + basic tests
  
Phase 4 (Self-Review)
  └── sdlc-reviewer may flag "missing tests" → recommends invoking qa-author
  
[NEW] Phase 4.5 (QA Analysis) — OPTIONAL, human-triggered
  └── qa-engineer runs adversarial analysis against the diff
  └── qa-critique evaluates the test suite
  └── qa-author fills coverage gaps
  └── Produces risk table for human reviewer
  
Phase 5+ (PR, CI, Review...)
  └── qa-flake-triage activates if ci-fix encounters non-deterministic failures
```

### Standalone invocations (outside the pipeline)

The QA suite is equally useful outside the SDLC pipeline:
- "Review this PR before I merge" → `qa-critique` + `qa-strategy` (gap analysis)
- "Write tests for this module" → `qa-author`
- "This test keeps flaking" → `qa-flake-triage`

---

## 11. Output Artifacts

Every skill produces a concrete deliverable — no 2000-word essays about testing philosophy.

| Skill | Primary artifact | Secondary |
|---|---|---|
| `qa-strategy` | `TEST-PLAN.md` (requirements → test matrix) | Risk table |
| `qa-author` | Test files (`.test.ts`, `.spec.ts`, `_test.go`, etc.) | — |
| `qa-critique` | Critique report (structured markdown) | Scored assessment |
| `qa-flake-triage` | Classification + fix recommendation | — |
| `qa-bug-report` | Structured bug report | Optionally creates ticket |

---

## 12. Evaluation Strategy

### Known-bug fixture repo

Plant bugs in a fixture and verify the suite finds them:

| Bug type | Location | Expected detection |
|---|---|---|
| Off-by-one in pagination | `listUsers` returns n-1 items on last page | `qa-author` boundary tests |
| Authz hole | `GET /users/:id` doesn't check ownership | `qa-author` or `qa-critique` flags missing authz test |
| N+1 query | `getOrders` fires one query per order for items | `qa-critique` flags performance, `qa-author` writes load assertion |
| Flaky test | Shared DB state between parallel tests | `qa-flake-triage` classifies as shared-state |
| Weak assertion | `expect(response).toBeDefined()` on a critical endpoint | `qa-critique` flags false confidence |
| Race condition | Counter increment without lock | `qa-author` concurrency test |

### Eval criteria

- **Detection rate:** Does the skill find the planted bug? (binary)
- **False positive rate:** Does it flag non-issues? (< 20% acceptable)
- **Artifact quality:** Is the output usable without human rewriting? (qualitative)
- **Red-before-green compliance:** Does the authored test actually fail on buggy code? (binary)

---

## 13. Open Questions

1. **Should `qa-author` auto-commit its test files?** Current inclination: no. It writes them, the user/executor reviews and commits. Same as how `sdlc-reviewer` doesn't fix.

2. **How deep should `qa-critique` go on coverage?** Options:
   - (a) AST-level branch coverage analysis (expensive, precise)
   - (b) Heuristic based on reading test + implementation (cheaper, less precise)
   - (c) Run coverage tool and analyze the report (requires toolchain support)
   
   Recommendation: (b) by default, (c) when coverage tooling is configured via `tkt cfg build.coverage`.

3. **Should the QA suite have its own orchestrator skill?** Or does `qa-engineer` agent handle routing?
   
   Recommendation: `qa-engineer` agent is the entry point. No separate orchestrator skill — avoids another layer.

4. **Integration with BDD / behavior specs?** The existing `behavior-specs.md` practice uses Gherkin-style scenarios. Should `qa-strategy` produce Gherkin as an optional output format?
   
   Recommendation: Yes, optional. If `build.bdd` is configured, `qa-strategy` can output `.feature` files alongside `TEST-PLAN.md`.

5. **Model tier for each skill?**
   
   | Skill | Recommended tier | Rationale |
   |---|---|---|
   | `qa-strategy` | opus | Requires deep requirement analysis |
   | `qa-author` | sonnet | Implementation work, benefits from speed |
   | `qa-critique` | opus | Adversarial analysis is where models are weakest |
   | `qa-flake-triage` | sonnet | Pattern matching against known categories |
   | `qa-bug-report` | haiku | Structured formatting of known information |
   | `qa-engineer` (agent) | opus | Routing + adversarial judgment calls |

---

## 14. Next Steps

- [ ] Write `skills/qa-strategy/SKILL.md` (canonical)
- [ ] Write `skills/qa-author/SKILL.md` (canonical)
- [ ] Write `skills/qa-critique/SKILL.md` (canonical)
- [ ] Write `skills/qa-flake-triage/SKILL.md` (canonical)
- [ ] Write `skills/qa-bug-report/SKILL.md` (canonical)
- [ ] Write `agents/qa-engineer.md` (sub-agent definition)
- [ ] Write stack-specific references (`references/*.md`)
- [ ] Create fixture repo for evals
- [ ] Sync to all harness formats via `sync-skills`
- [ ] Update `automated-sdlc/SKILL.md` to reference Phase 4.5
- [ ] Update `self-review/SKILL.md` to delegate test-gap findings to QA suite
- [ ] Update `ci-fix/SKILL.md` to delegate flake analysis to `qa-flake-triage`
