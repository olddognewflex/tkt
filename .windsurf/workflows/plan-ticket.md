# /plan-ticket

Produce a structured implementation plan from a triaged ticket. Planning happens BEFORE any code changes.

## Steps

1. Read the ticket:
   ```shell
   tkt view "$KEY" --json
   ```
2. Read existing code for each affected package (route handlers, types, schemas, tests, API specs).
   If the repo has a mex code graph (`.mex/graph.db` exists), map the ticket to code through it
   first: `mex graph scope "<summary>"`, `mex graph query where-defined <symbol>`, then
   `mex graph get <id> --detail source` for the 1-3 nodes that matter. Reword a poor scope match
   once, then fall back to reading files; treat source the graph returns as already read.
3. Classify the change: new endpoint, bug fix, new feature, refactor, or shared-lib change.
   With a code graph present, `mex impact <symbol-or-file>` on the main touchpoints shows the
   transitive blast radius — fold surprises into Risks.
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
