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
2. Read existing code for affected packages. If the repo has a mex code graph
   (`.mex/graph.db` exists), map the ticket to code through it first:
   ```shell
   mex graph scope "<summary>"
   mex graph query where-defined <symbol>
   mex graph get <id> --detail source
   ```
   Reword a poor scope match once, then fall back to reading files; treat source the
   graph returns as already read.
3. Classify the change scope. With a code graph present, `mex impact <symbol-or-file>`
   on the main touchpoints shows the transitive blast radius — fold surprises into Risks.
4. Read existing code for affected packages.
5. Classify the change scope.
   - **Bug tickets:** the plan MUST list a failing regression test as change #1 — one that reproduces the reported behaviour and fails against current code. Name the test and assertion under Changes, not just Test Strategy; no fix step precedes it. If it can't be reproduced in a test, say so under Risks with the reason.
6. Produce a markdown plan:
   - Summary (one sentence)
   - Changes (numbered `path — what`)
   - Test Strategy
   - Risks
   - Out of Scope
   - Estimated Size (~N files, ~N lines)
7. Validate each acceptance criterion is covered.
8. If >400 lines, comment and pause for confirmation.

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
