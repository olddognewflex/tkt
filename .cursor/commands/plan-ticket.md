# /plan-ticket

Produce a structured implementation plan from a triaged ticket. Planning happens BEFORE any code changes.

## Input

- Ticket key (e.g. from `triage-ticket`)
- Summary, acceptance criteria, affected packages

## Steps

### 1. Read the ticket

```shell
tkt view "$KEY" --json
```

Extract: `key`, `type` + `type_class`, `summary`, `acceptance`, affected packages.

### 2. Read existing code

For each affected package, read relevant entry points (route handlers, types, schemas, tests, API specs). Keep the package→file mapping in the project's own conventions doc.

### 3. Identify change scope

Classify: new endpoint, bug fix, new feature, refactor, or shared-lib change.

### 3a. Bug tickets: regression test first

If the ticket type is a bug (`tkt view "$KEY" --json | jq -r .type` matches bug/defect), the plan MUST list a failing regression test as change #1 — one that reproduces the reported behaviour and fails against current code. Name the test and the assertion in the Changes list, not just Test Strategy. No fix step precedes it. If the bug can't be reproduced in a test, say so explicitly under Risks and state why.

### 4. Produce the plan

```markdown
## Implementation Plan — <KEY>

### Summary
<one sentence>

### Changes
1. `<path>` — <what to add/change>
2. ...

### Test Strategy
- New tests: <describe>
- Modified tests: <describe>
- Regression: run the suite for affected packages
- Regression (bugs): <failing test written first, per §3a>

### Risks
- <anything that could go wrong / external schema or API deps>

### Out of Scope
- <things this ticket does NOT cover>

### Estimated Size
- ~<N> files, ~<N> lines
- If >400 lines: recommend splitting into <subtasks>
```

Build/test commands come from config:

```shell
tkt cfg build.build --pkg "<pkg>"
tkt cfg build.test --pkg "<pkg>"
tkt cfg build.typecheck
```

### 5. Validate against acceptance criteria

For each criterion, confirm the plan covers it. Note gaps.

### 6. Check for splitting

If estimated size > 400 lines:

```shell
tkt comment "$KEY" "Scope is large (~N lines). Recommend splitting into: 1) <sub>, 2) <sub>"
```

Pause and ask for confirmation before proceeding with a large ticket.

## Output

The implementation plan as structured markdown, ready to drive implementation.
