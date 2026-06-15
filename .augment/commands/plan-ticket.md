# /plan-ticket

Produce a structured implementation plan from a triaged ticket. Planning happens BEFORE any code changes.

## Steps

1. Read the ticket: `tkt view "$KEY" --json`.
2. Read existing code for affected packages.
3. Classify the change scope.
4. Produce a markdown plan: Summary, Changes, Test Strategy, Risks, Out of Scope, Estimated Size.
5. Validate each acceptance criterion is covered.
6. If >400 lines, comment and pause for confirmation.

Build/test commands come from `tkt cfg`.

## Rules

- Never hardcode the toolchain.
- All ticketing access goes through `tkt`.
