---
name: plan-ticket
description: 'Analyze a triaged ticket and produce a structured implementation plan before coding begins. Provider-agnostic via tkt. Invoke as slash command, not automatically.'
disable-model-invocation: true
---

# Plan Ticket

Produce a structured implementation plan from a triaged ticket. Planning happens
BEFORE any code changes. Ticketing access via `tkt`.

## Steps

1. Read the ticket: `tkt view "$KEY" --json`.
2. Read existing code for affected packages.
3. Classify: new endpoint, bug fix, new feature, refactor, or shared-lib change.
4. Produce a markdown plan:
   - Summary (one sentence)
   - Changes (numbered `path — what`)
   - Test Strategy
   - Risks
   - Out of Scope
   - Estimated Size (~N files, ~N lines)
5. Validate each acceptance criterion is covered.
6. If >400 lines, comment and pause for confirmation.

Build/test commands come from config:
```shell
tkt cfg build.build --pkg "<pkg>"
tkt cfg build.test --pkg "<pkg>"
tkt cfg build.typecheck
```

## Output

Structured markdown plan, ready to drive implementation.
