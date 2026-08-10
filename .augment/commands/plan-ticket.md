# /plan-ticket

Produce a structured implementation plan from a triaged ticket. Planning happens BEFORE any code changes.

## Steps

1. Read the ticket: `tkt view "$KEY" --json`.
2. Read existing code for affected packages. If the repo has a mex code graph (`.mex/graph.db` exists), map the ticket to code first with `mex graph scope "<summary>"` and `mex graph query where-defined <symbol>`, expanding only the 1-3 nodes that matter via `mex graph get <id> --detail source`; fall back to reading files if scope misses.
3. Classify the change scope. With a code graph present, `mex impact <symbol-or-file>` on the main touchpoints shows the transitive blast radius — fold surprises into Risks.
4. Produce a markdown plan: Summary, Changes, Test Strategy, Risks, Out of Scope, Estimated Size.
5. Validate each acceptance criterion is covered.
6. If >400 lines, comment and pause for confirmation.

Build/test commands come from `tkt cfg`.

## Rules

- Never hardcode the toolchain.
- All ticketing access goes through `tkt`.
