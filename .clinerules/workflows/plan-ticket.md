# /plan-ticket

Produce a structured implementation plan from a triaged ticket. Planning happens BEFORE any code changes.

## Steps

1. Read the ticket:
   ```shell
   tkt view "$KEY" --json
   ```
2. Read existing code for each affected package (route handlers, types, schemas, tests, API specs).
3. Classify the change: new endpoint, bug fix, new feature, refactor, or shared-lib change.
4. Produce a markdown plan:
   - Summary (one sentence)
   - Changes (numbered `path — what`)
   - Test Strategy (new / modified / regression)
   - Risks (including external schema/API deps)
   - Out of Scope
   - Estimated Size (~N files, ~N lines)
5. Validate each acceptance criterion is covered.
6. If >400 lines, comment on the ticket and pause for confirmation.

Build/test commands come from config:
```shell
tkt cfg build.build --pkg "<pkg>"
tkt cfg build.test --pkg "<pkg>"
tkt cfg build.typecheck
```

## Output

Structured markdown plan, ready to drive implementation.

## Rules

- Never hardcode the toolchain.
- All ticketing access goes through `tkt`.
