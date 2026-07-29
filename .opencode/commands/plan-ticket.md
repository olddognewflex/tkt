---
description: Produce a structured implementation plan from a triaged ticket
---

Produce a structured implementation plan for ticket: $ARGUMENTS

Planning happens BEFORE any code changes.

1. Read the ticket with `tkt view "$KEY" --json` and extract key, summary, acceptance criteria, and affected packages.
2. Read existing code for each affected package (route handlers, types, schemas, tests, API specs). Keep the package→file mapping in the project's own conventions doc.
3. Classify the change: new endpoint, bug fix, new feature, refactor, or shared-lib change.
   - **Bug tickets:** the plan MUST list a failing regression test as change #1 — one that reproduces the reported behaviour and fails against current code. Name the test and assertion under Changes, not just Test Strategy; no fix step precedes it. If it can't be reproduced in a test, say so under Risks with the reason.
4. Produce a markdown plan with these sections:
   - Summary (one sentence)
   - Changes (numbered `path — what`)
   - Test Strategy (new / modified / regression)
   - Risks (including external schema/API deps)
   - Out of Scope
   - Estimated Size (~N files, ~N lines)
5. Validate each acceptance criterion is covered by the plan. Note any gap.
6. If estimated size > 400 lines, comment on the ticket recommending sub-tasks, then pause for confirmation before proceeding.

Build/test commands come from config — never hardcode the toolchain:

- `tkt cfg build.build --pkg "<pkg>"`
- `tkt cfg build.test --pkg "<pkg>"`
- `tkt cfg build.typecheck`

Output the plan as structured markdown, ready to drive implementation.
