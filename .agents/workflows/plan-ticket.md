---
name: plan-ticket
description: Analyze a triaged ticket and produce a structured implementation plan via tkt.
trigger: manual
---

# Plan Ticket

Produce a structured implementation plan from a triaged ticket. Planning happens BEFORE any code changes.

## Steps

1. Read the ticket:
   ```shell
   // turbo
   tkt view "$KEY" --json
   ```
2. Read existing code for affected packages.
3. Classify the change scope.
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
// turbo
tkt cfg build.build --pkg "<pkg>"
// turbo
tkt cfg build.test --pkg "<pkg>"
// turbo
tkt cfg build.typecheck
```

## Rules

- Never hardcode the toolchain.
- All ticketing access goes through `tkt`.
