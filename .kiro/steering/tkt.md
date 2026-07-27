# tkt — Core Rules

This project uses **tkt**, a provider-agnostic ticketing CLI. All ticketing,
board transitions, and project config are accessed through `tkt` commands —
never through a backend API directly.

## Portability Rules

1. **No backend specifics in workflow logic.**
   Use `tkt view`, `tkt transition`, `tkt comment`, `tkt blockers`,
   `tkt worklog`, `tkt lane-time`, `tkt list`, `tkt create`, `tkt link`.
   Never call `acli`, `gh issue`, Jira REST, Linear GraphQL, or any other
   backend API directly for ticketing operations.

2. **No repo/toolchain hardcoding.**
   Read all values through `tkt cfg`:
   - `tkt cfg vcs.repo` — repository identifier
   - `tkt cfg vcs.default_branch` — main branch name
   - `tkt cfg vcs.merge` — merge strategy (squash, merge, rebase)
   - `tkt cfg vcs.reviewers --json` — PR reviewer list
   - `tkt cfg vcs.branch_fmt --ticket KEY --slug s` — branch name template
   - `tkt cfg build.build --pkg X` — build command
   - `tkt cfg build.test --pkg X` — test command
   - `tkt cfg build.typecheck` — type checking command
   - `tkt cfg deploy.staging_workflow` — staging deploy workflow name
   - `tkt cfg deploy.production_workflow` — production deploy workflow name

3. **Speak in roles, not lane names.**
   Canonical roles: `backlog`, `todo`, `in_progress`, `review`, `qa_ready`,
   `qa`, `deploy_ready`, `done`, plus side roles `revise`, `blocked`, `cancelled`.
   Use `tkt transition KEY review` to move tickets.
   Use `tkt lane review` only when the provider's literal lane string is needed.

4. **Respect `type_class` routing.**
   - `full_sdlc` (Story/Bug): full pipeline — select, triage, plan, implement, self-review, open-pr, respond-to-review, deploy-ready.
   - `deliverable` (Task/Epic/Spike/Chore): short-circuits via `complete-deliverable` straight to `done`.

5. **`tkt worklog` / `tkt lane-time` are safe no-ops.**
   When `[timetracking].provider = "none"`, these return empty worklogs.
   Comments should still read naturally (e.g. "Time in In Progress: (no time tracking)").

## Verb Contract

| Command | Purpose |
|---------|---------|
| `tkt whoami` | current user id |
| `tkt list --tier N` | run tiered query from config |
| `tkt list --query NAME` | run named query from config |
| `tkt view KEY --json` | normalized ticket JSON |
| `tkt transition KEY ROLE` | move ticket to role's lane |
| `tkt comment KEY BODY` | post activity comment |
| `tkt blockers KEY --json` | unresolved blockers only |
| `tkt worklog KEY --from-role ROLE [--note T]` | log time since lane entry |
| `tkt lane-time KEY --role ROLE` | log time for closed lane interval |
| `tkt create --type T --summary S ...` | create a ticket |
| `tkt link KEY --to OTHER --type T` | link tickets |
| `tkt lane ROLE` | resolve role to provider lane name |
| `tkt apply [KEY] --file PATH` | create/update from a full ticket markdown doc |
| `tkt edit KEY [--summary/--body/--agent-status/...]` | field-level update |
| `tkt cfg DOTTED.KEY ...` | read config with template substitution |
| `tkt init --provider P [...]` | scaffold `.sdlc/` in a project |
| `tkt sync-pack [--dir D] [--check]` | install the pack as committed copies |
| `tkt run [KEY] [--status/--stop/--dry-run]` | external loop driver, one phase per invocation |
| `tkt doctor` | validate auth + reachability + board model + pack sync |

Exit codes are the branch points: `4` is `NotFoundError` (an unset optional config
key — a clean skip), `2` is a usage/config error (a real problem). Never treat a
non-zero exit as interchangeable.

## Validation

When starting work or troubleshooting, run `tkt doctor` to confirm:
- Ticketing auth is valid
- Board model is reachable
- Role-to-lane mappings resolve correctly

## Configuration

The project's SDLC configuration lives in `.sdlc/config.toml`. This file defines:
- Ticketing provider and board structure
- Role-to-lane mappings
- Query tiers for ticket selection
- VCS settings (repo, branch format, reviewers, merge strategy)
- Build/test/lint commands, plus the optional `build.bdd` behavior-spec runner
- Deploy workflow names
- Time tracking provider
- `[models]` — the tier→model mapping skills resolve via `tkt cfg models.<tier>`
- `[run]` — harness command and caps for the `tkt run` loop driver
