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
2. Read existing code for affected packages. If the repo has a mex code graph
   (`.mex/graph.db` exists), map the ticket to code through it first: `mex graph scope
   "<summary>"`, `mex graph query where-defined <symbol>`, then `mex graph get <id>
   --detail source` for the 1-3 nodes that matter. Reword a poor scope match once, then
   fall back to reading files; treat source the graph returns as already read.
3. Classify: new endpoint, bug fix, new feature, refactor, or shared-lib change. With a
   code graph present, `mex impact <symbol-or-file>` on the main touchpoints shows the
   transitive blast radius — fold surprises into Risks.
4. Read existing code for affected packages.
5. Classify: new endpoint, bug fix, new feature, refactor, or shared-lib change.
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
tkt cfg build.build --pkg "<pkg>"
tkt cfg build.test --pkg "<pkg>"
tkt cfg build.typecheck
```

## Output

Structured markdown plan, ready to drive implementation.
