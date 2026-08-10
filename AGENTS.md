# AGENTS.md — tkt Project Guide

This repo is **`tkt`**, a provider-agnostic ticketing CLI plus a portable SDLC
skill pack. The pack's skills speak semantic verbs (`tkt view`, `tkt transition`,
...) and never touch a concrete backend. `tkt` reads `.sdlc/config.toml`,
dispatches to the configured provider adapter, and normalizes every backend into
one JSON ticket shape.

This file is the harness-facing contract: how to drive `tkt` and how to edit the
skills without breaking portability. For this repo's internals (`core/`,
`adapters/`, how to add a provider), see `CLAUDE.md`.

Use this file as the root fallback when your harness does not have a dedicated
per-tool folder (Cursor rules, Windsurf rules, OpenCode instructions, etc. are in
separate harness-specific files).

## Core portability rules

1. **No backend specifics in workflow logic.**
   - Use the semantic verbs below — never call `acli`, `gh issue`, Jira REST,
     Linear GraphQL, or any other backend API directly for ticketing.

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

6. **Optional verbs are opt-in per adapter.**
   - `create`, `apply`, `edit`, and `link` raise a "not supported"
     `ProviderError` on backends that don't implement them. Branch on the exit
     code (3) rather than assuming availability.

## Verb contract (quick reference)

| Command | Purpose |
|---------|---------|
| `tkt whoami` | current user id |
| `tkt list --tier N` \| `--query NAME` | run named query from config (one is required) |
| `tkt view KEY --json` | normalized ticket |
| `tkt transition KEY ROLE` | move ticket to role's lane |
| `tkt comment KEY BODY` | post activity comment |
| `tkt blockers KEY --json` | unresolved blockers only |
| `tkt worklog KEY --from-role ROLE [--note T] [--billable]` | log time since entry → now |
| `tkt lane-time [KEY] --role ROLE [--keys K1:role,K2:role] [--read-only]` | log time for a closed lane interval; batch via `--keys` |
| `tkt create --type T --summary S [--priority P] [--assignee A] [--body B] [--project P]` | create a ticket (adapter-opt-in) |
| `tkt apply [KEY] --file PATH` \| `--new --file PATH` \| `--template` | create/update from a full ticket markdown doc (`-` reads stdin) |
| `tkt edit KEY [--summary/--body/--priority/--assignee/--add-label/--remove-label/--due/--scheduled/--completed/--agent-status]` | field-level update |
| `tkt link KEY --to OTHER --type T` | link tickets (adapter-opt-in) |
| `tkt lane ROLE` | resolve role → provider lane name |
| `tkt cfg DOTTED.KEY [--pkg/--ticket/--slug]` | read config + template substitution |
| `tkt cfg priorities` | backend-aware priority list, highest-first |
| `tkt init --provider P [--dir D] [--link-skills] [--sample] [--force] [--no-detect-build]` | scaffold `.sdlc/` (seeds `[build]` from the project's package manager) |
| `tkt sync-pack [HARNESS...] [--dir D] [--all-harnesses] [--list-harnesses] [--check]` | install the pack into a consumer repo as committed copies |
| `tkt run [KEY] [--status] [--stop] [--max-iterations N] [--dry-run]` | external loop driver: one pipeline phase per harness invocation |
| `tkt doctor` | validate auth + reachability + board model + pack sync |

`--json` works on either side of the verb.

### Edit semantics

`tkt edit` distinguishes "not supplied" from "clear":

- Omit a flag → field unchanged.
- Pass `""` → field cleared (e.g. `--assignee ""`).
- `--due` / `--scheduled` / `--completed` take `YYYY-MM-DD`.
- `--agent-status` takes one of `idle`, `processing`, `waiting`, `done`,
  `blocked` (or `""` to clear). Invalid values are rejected up front so a typo
  can't write a state the board's badge mapping won't recognize.

### Exit codes

Branch on these — errors always go to stderr with a non-zero exit:

| Code | Meaning |
|------|---------|
| 2 | config error |
| 3 | provider error (includes "verb not supported by this adapter") |
| 4 | not found |
| 64 | usage error |

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
| `qa-strategy` | test plan from acceptance criteria (Break-It Checklist) |
| `qa-author` | write adversarial tests, red-before-green enforced |
| `qa-critique` | score an existing suite for coverage + assertion strength |
| `qa-flake-triage` | classify a flaky test's root cause; never papers over it |
| `qa-bug-report` | structured bug report with a minimal reproduction |
| `sync-skills` | translate the canonical skills into each harness format |

### Editing skills

- **No backend specifics in skill text.** Speak in roles, and read all
  toolchain/VCS/deploy settings through `tkt cfg`. Never hardcode Jira/pnpm/
  GitHub/repo names.
- VCS (PR/CI/merge via `gh`) and infra (preview/deploy) are still GitHub/
  cloud-shaped, but repo/branch/reviewer/workflow values come from config;
  genuinely infra-specific steps are marked `PROJECT-SPECIFIC` in the skill text.
- Editing a skill's frontmatter `description` changes the `sync-pack` managed
  block in consumer repos — it is generated from the first sentence.

## Distributing the pack

`tkt sync-pack` installs the pack into a consumer repo as **committed copies**,
not symlinks: cloud harnesses and CI only see tracked files, so the symlinks
`tkt init --link-skills` writes are invisible to them. It only writes paths it
records in `.sdlc/pack-manifest.json`, never deletes, and is idempotent (a second
run with an unchanged pack leaves `git status` clean).

In a consumer repo it also maintains a generated block inside that repo's
`AGENTS.md`, delimited by markers — content outside the markers is preserved
verbatim. `tkt sync-pack --check` reports missing/locally-modified/out-of-date
pack files and exits 1; `tkt doctor` folds the same check in.

### Harness selection

A bare `tkt sync-pack` installs the default harnesses (`claude`, `copilot`,
`kiro`). Name others to add them to an already-synced project:

```sh
tkt sync-pack cursor              # add Cursor to this repo
tkt sync-pack cursor windsurf     # several at once
tkt sync-pack --all-harnesses     # every known harness
tkt sync-pack --list-harnesses    # names, target dirs, what's installed here
```

Selection is **additive and sticky**: the resulting set is recorded under
`harnesses` in `.sdlc/pack-manifest.json`, so a later bare `tkt sync-pack` keeps
every added harness up to date rather than reverting to the default three.
Removing a harness means dropping it from that list by hand — `sync-pack` never
deletes files.

The defaults apply only when nothing is named **and** nothing is recorded. So in
a repo that was set up with `tkt init --link-skills` (symlinked `.claude/`, no
manifest), `tkt sync-pack cursor` installs `.cursor/` and the `AGENTS.md` block
and nothing else — it will not drop committed copies on top of the symlinks.

This file — the pack repo's own `AGENTS.md` — has no managed block and is
hand-maintained.

## Meta

- `agents/` holds the subagent catalog, shipped to `.claude/agents/` by
  `sync-pack`. Each is trust-scoped so the pipeline never self-approves:

  | Agent | Trust | Used by |
  | --- | --- | --- |
  | `ticket-researcher` | read-only | ad-hoc ticket lookup |
  | `sdlc-planner` | read-only | plan phase |
  | `sdlc-executor` | file read/write + build/test, no VCS push, no transitions | implement phase |
  | `sdlc-reviewer` | read-only | self-review, review pass |
  | `sdlc-verifier` | read + build/test only | self-review, check pass |
  | `qa-engineer` | read + write tests + test runner | QA phase (P4.5) |

  Every phase that delegates also states an inline fallback, so harnesses without
  subagent support run the pipeline unchanged.
- `docs/install.md` — installing `tkt` and the pack.
- `docs/markdown-ticketing.md` — the markdown provider's on-disk format.
- `docs/model-routing.md` — the `model_tier` frontmatter key and the `[models]`
  tier→model mapping skills resolve via `tkt cfg models.<tier>`.
- `docs/behavior-specs.md` — the BDD practice behind the optional `build.bdd` key.
- `docs/qa-suite-design.md` — how the five `qa-*` skills and `qa-engineer` fit
  together, and the Break-It Checklist they share.
- `docs/extraction-history.md` — how this pack was extracted from its origin repo.
- `scripts/` — `company-import.sh` (bulk import) and `smoke-sync-pack.sh`
  (sync-pack smoke test).
- This repo is pure stdlib Python 3.11+; no build step.

## When in doubt

- Run `tkt doctor` to validate a project's config.
- Run `tkt view KEY --json` to see the normalized shape.
- Config keys are not self-documenting: consult `.sdlc/config.toml` or
  `examples/config.<provider>.toml`.
