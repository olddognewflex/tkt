# /plan-ticket

Produce a structured implementation plan from a triaged ticket. Planning happens BEFORE any code changes.

## Steps

1. Read the ticket: `tkt view "$KEY" --json`.
2. Read existing code for affected packages.
3. Classify the change scope.
   - **Bug tickets:** plan MUST list a failing regression test (reproduces the bug, fails now) as change #1, named under Changes; if not reproducible, say why under Risks.
4. Produce a markdown plan: Summary, Changes, Test Strategy, Risks, Out of Scope, Estimated Size.
5. Validate each acceptance criterion is covered.
6. If >400 lines, comment and pause for confirmation.

Build/test commands come from `tkt cfg`.

## Rules

- Never hardcode the toolchain.
- All ticketing access goes through `tkt`.
