# AGENTS.md — tkt Project Guide

This repo is **`tkt`**, a provider-agnostic ticketing CLI plus a portable SDLC
skill pack. The pack's skills speak semantic verbs (`tkt view`, `tkt transition`,
...) and never touch a concrete backend. `tkt` reads `.sdlc/config.toml`,
dispatches to the configured provider adapter, and normalizes every backend into
one JSON ticket shape.

Use this file as the root fallback when your harness does not have a dedicated
per-tool folder (Cursor rules, Windsurf rules, OpenCode instructions, etc. are in
separate harness-specific files).

## Core portability rules

1. **No backend specifics in workflow logic.**
   - Use `tkt view`, `tkt transition`, `tkt comment`, `tkt blockers`,
     `tkt worklog`, `tkt lane-time`, `tkt list`, `tkt create`, `tkt link`.
   - Never call `acli`, `gh issue`, Jira REST, Linear GraphQL, or any other
     backend API directly for ticketing.

2. **No repo/toolchain hardcoding.**
   - Read values through `tkt cfg`:
     - `tkt cfg vcs.repo`
     - `tkt cfg vcs.default_branch`
     - `tkt cfg vcs.merge`
     - `tkt cfg vcs.reviewers --json`
     - `tkt cfg vcs.branch_fmt --ticket K --slug s`
     - `tkt cfg build.build --pkg X`
     - `tkt cfg build.test --pkg X`
     - `tkt cfg build.typecheck`
     - `tkt cfg deploy.staging_workflow`
     - `tkt cfg deploy.production_workflow`
   - Never hardcode repo names, branch names, workflow names, or build commands.

3. **Speak in roles, not lane names.**
   - Canonical roles: `backlog`, `todo`, `in_progress`, `review`, `qa_ready`,
     `qa`, `deploy_ready`, `done`, plus side roles `revise`, `blocked`,
     `cancelled`.
   - Use `tkt transition KEY review` to move.
   - Use `tkt lane review` only when you need the provider's literal lane string.

4. **Respect `type_class` routing.**
   - `full_sdlc` (Story/Bug): runs select → triage → plan → implement →
     self-review → open-pr → respond-to-review → deploy-ready.
   - `deliverable` (Task/Epic/Spike/Chore): short-circuits via
     `complete-deliverable` straight to `done`.

5. **`tkt worklog` / `tkt lane-time` are safe no-ops.**
   - When `[timetracking].provider = "none"`, these return empty worklogs.
   - Comments should still read naturally (e.g. "Time in In Progress: (no time
     tracking)").

## Verb contract (quick reference)

| Command | Purpose |
|---------|---------|
| `tkt whoami` | current user id |
| `tkt list --tier N` / `--query NAME` | run named query from config |
| `tkt view KEY --json` | normalized ticket |
| `tkt transition KEY ROLE` | move ticket to role's lane |
| `tkt comment KEY BODY` | post activity comment |
| `tkt blockers KEY --json` | unresolved blockers only |
| `tkt worklog KEY --from-role ROLE [--note T]` | log time since entry → now |
| `tkt lane-time KEY --role ROLE` | log time for closed lane interval |
| `tkt create --type T --summary S ...` | create a ticket (adapter-opt-in) |
| `tkt link KEY --to OTHER --type T` | link tickets (adapter-opt-in) |
| `tkt lane ROLE` | resolve role → provider lane name |
| `tkt cfg DOTTED.KEY ...` | read config + template substitution |
| `tkt doctor` | validate auth + reachability + board model |

## SDLC skill pack

Canonical source: `skills/<name>/SKILL.md`. Harness-specific translations live in
per-tool folders (`.opencode/commands/`, `.cursor/commands/`, `.cursor/skills/`,
`.continue/prompts/`, `.windsurf/workflows/`, `.clinerules/workflows/`,
`.augment/commands/`, `.agents/workflows/`, `.agents/skills/`, `.kiro/skills/`,
`.github/prompts/`, `.gemini/commands/`).

| Skill | Purpose |
|-------|---------|
| `select-ticket` | pick next ticket (tiered queries + blocker filter) |
| `triage-ticket` | read ticket + move to `in_progress` |
| `plan-ticket` | structured implementation plan before coding |
| `open-pr` | commit/push/PR/reviewers + `→ review` with lane-time |
| `self-review` | adversarial pre-PR review loop |
| `respond-to-review` | address review comments, `revise ↔ review` loop |
| `deploy-ready` | merge, watch staging, gate prod, QA lane-times |
| `automated-sdlc` | orchestrator across all of the above |
| `check-blockers` | classify blocked tickets, recommend unblocks |
| `ci-fix` | watch CI, diagnose, fix, loop until green |
| `complete-deliverable` | short-circuit `→ done` for deliverable types |
| `deploy-preview` | confirm preview env, post URL to ticket + PR |
| `hotfix-revert` | fast-track prod revert |
| `resume-from-revise` | re-enter the loop after a human revise fix |

## Meta

- `skills/sync-skills/SKILL.md` documents how to translate the canonical skills
  into each harness format.
- `agents/ticket-researcher.md` is the read-only lookup subagent.
- This repo is pure stdlib Python 3.11+; no build step.

## When in doubt

- Run `tkt doctor` to validate a project's config.
- Run `tkt view KEY --json` to see the normalized shape.
- Run `tkt cfg <dotted.key> --help` is not supported; consult
  `.sdlc/config.toml` or `examples/config.<provider>.toml`.
