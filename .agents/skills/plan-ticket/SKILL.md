---
name: plan-ticket
description: 'Analyze a triaged ticket and produce a structured implementation plan before coding begins. Provider-agnostic via tkt.'
---

# Plan Ticket

Produce a structured implementation plan from a triaged ticket. Planning happens
BEFORE any code changes. Ticketing access via `tkt`.

## Input

From `triage-ticket`: ticket key, summary, acceptance criteria, affected packages.
Re-read anytime with `tkt view "$KEY" --json`.

## Steps

### 1. Read existing code

For each affected package, read the relevant entry points (route handlers, types,
schemas, tests, API specs). Keep this mapping in the project's own conventions
doc — the skill is repo-agnostic.

### 2. Identify change scope

Classify the work: new endpoint, bug fix, new feature (cross-cutting), refactor,
or shared-lib change. Note the typical files touched for each.

### 3. Produce the plan

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

### Risks
- <anything that could go wrong / external schema or API deps>

### Out of Scope
- <things this ticket does NOT cover>

### Estimated Size
- ~<N> files, ~<N> lines
- If >400 lines: recommend splitting into <subtasks>
```

Build/test commands come from config — don't hardcode the toolchain:

```shell
tkt cfg build.build --pkg "<pkg>"     # e.g. pnpm turbo build --filter=<pkg>
tkt cfg build.test  --pkg "<pkg>"
tkt cfg build.typecheck
```

### 4. Validate plan against acceptance criteria

For each criterion in the ticket's `acceptance`, confirm the plan covers it.
Note any gap.

### 5. Check if the ticket needs splitting

If estimated size > 400 lines:

```shell
tkt comment "$KEY" "Scope is large (~N lines). Recommend splitting into: 1) <sub>, 2) <sub>"
```

Pause and ask for confirmation before proceeding with a large ticket.

## Output

The implementation plan as structured markdown, ready to drive implementation.
