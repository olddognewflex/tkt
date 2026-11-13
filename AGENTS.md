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
     - `tkt cfg route <phase> --json` resolves a phase to `{phase, harness,
       model, tier, cmd, source}`; exit 4 = nothing configured, proceed on
       the session default. An empty `cmd` means run it on the current
       harness with `model`.
     - `tkt schedule --json` for after-hours handling (optional `[schedule]`
       table: `after_hours_label`, `business_hours`, `timezone`, `days`). Never
       compute the window in shell; `in_window` is false and `enabled` false
       when the table is absent, and exit 2 means it is misconfigured (fail closed).
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
   - `deliverable` (Task/Sub-task/Spike/Chore): short-circuits via
     `complete-deliverable` straight to `done`.
   - `container` (Epic): groups other tickets and is never worked.
     `select-ticket` never selects one, and never selects a ticket with open
     children; `triage-ticket` and Phase 1.5 stop on one; a human closes it.
     `tkt view` adds a `rollup` (children closed / total, `ready_to_close`)
     and `tkt children KEY --json` lists each child's state. Every ticket
     carries `closed`: a closed role or the backend's own terminal state
     (a closed GitHub issue, Jira's done category), one rule for parents
     and children.
   - Those lists are the example configs' defaults; `container` defaults to
     `["Epic"]` when absent, `container = []` turns it off, and a type in
     several lists resolves container > full_sdlc > deliverable.
     `[issue_types]` decides
     which types fall in each set, and a project can move one, such as
     Sub-task, so branch on `type_class`, not the type name.

5. **`tkt worklog` / `tkt lane-time` are safe no-ops.**
   - When `[timetracking].provider = "none"`, these return empty worklogs.
   - Comments should still read naturally (e.g. "Time in In Progress: (no time
     tracking)").

6. **Optional verbs are opt-in per adapter.**
   - `create`, `apply`, `edit`, `link`, and `activity` raise a "not supported"
     `ProviderError` on backends that don't implement them. Branch on the exit
     code (3) rather than assuming availability.

## Verb contract (quick reference)

| Command | Purpose |
|---------|---------|
| `tkt whoami` | current user id |
| `tkt list --tier N` \| `--query NAME` `[--all-projects]` | run named query from config (one is required); `--all-projects` drops project scoping |
| `tkt view KEY --json` | normalized ticket |
| `tkt activity --query NAME --since ISO [--until ISO] --json` | comments + field changes on the query's tickets in `[since, until)`; each event has a stable `evidence_id` to cite (adapter-opt-in: jira) |
| `tkt transition KEY ROLE` | move ticket to role's lane (format-gated from `review` on; see below) |
| `tkt comment KEY BODY` | post activity comment (format-gated) |
| `tkt blockers KEY --json` | unresolved blockers only |
| `tkt children KEY --json` | each direct child's `status`, `status_role` and `closed` |
| `tkt worklog KEY --from-role ROLE [--note T] [--billable\|--no-billable]` | log time since entry → now |
| `tkt lane-time [KEY] --role ROLE [--keys K1:role,K2:role] [--read-only]` | log time for a closed lane interval; batch via `--keys` |
| `tkt create --type T --summary S [--priority P] [--assignee A] [--body B] [--project P]` | create a ticket (adapter-opt-in) |
| `tkt apply [KEY] --file PATH` \| `--new --file PATH` \| `--template` | create/update from a full ticket markdown doc (`-` reads stdin) |
| `tkt edit KEY [--summary/--body/--priority/--assignee/--add-label/--remove-label/--due/--scheduled/--completed/--agent-status]` | field-level update; `--body` replaces the whole body, like `create --body` (comments kept) |
| `tkt link KEY --to OTHER --type T` | link tickets (adapter-opt-in) |
| `tkt lane ROLE` | resolve role → provider lane name |
| `tkt cfg DOTTED.KEY [--pkg/--ticket/--slug]` | read config + template substitution |
| `tkt schedule [--at ISO]` | is it business hours now, per `[schedule]` (config + clock, no backend) |
| `tkt cfg priorities` | backend-aware priority list, highest-first |
| `tkt cfg route PHASE` | resolve a phase to harness + model via `[routes]`/`[harnesses]`/`[models]`/`[run]` (config-only) |
| `tkt init --provider P [--dir D] [--link-skills] [--sample] [--force] [--no-detect-build] [--no-detect-repo] [--project KEY] [--board-dir PATH] [--no-inherit]` | scaffold `.sdlc/` (seeds `[build]` from the project's package manager and `[vcs].repo` from the git origin remote; markdown joins a parent dir's shared board, key derived from the dir name) |
| `tkt sync-pack [HARNESS...] [--dir D] [--all-harnesses] [--list-harnesses] [--check] [--prune]` | install the pack into a consumer repo as committed copies |
| `tkt run [KEY] [--status] [--stop] [--max-iterations N] [--dry-run]` | external loop driver: one pipeline phase per harness invocation |
| `tkt agents [--stale-after N] [--dir D] [--enrich] [--all]` | state of every run at once (local read; no backend without `--enrich`) |
| `tkt doctor` | validate auth + reachability + board model + pack sync |

`--json` works on either side of the verb. `tkt --version` / `-V` prints the tkt build
(release number plus checkout commit) and exits 0; quote it when reporting a tkt bug.

### Edit semantics

`tkt edit` distinguishes "not supplied" from "clear":

- Omit a flag → field unchanged.
- Pass `""` → field cleared (e.g. `--assignee ""`).
- `--due` / `--scheduled` / `--completed` take `YYYY-MM-DD`.
- `--agent-status` takes one of `idle`, `processing`, `waiting`, `done`,
  `blocked` (or `""` to clear). Invalid values are rejected up front so a typo
  can't write a state the board's badge mapping won't recognize. The adapter
  stamps `agent_status_at` whenever the state actually changes; re-asserting
  the same state leaves the stamp alone.

### Ticket identity fields

`tkt view --json` (and, partly, `list --json`) also carry `project` / `project_name`
(key and display name), `assignee_id`, `reporter` / `reporter_id` (display
name, else email, and account id) and `created` / `updated` (canonical UTC,
`...T06:28:36.123Z`). Only jira fills them today; every other backend leaves
`""` / `null`, so treat an empty value as "unknown", not as a fact. A
timestamp jira can't parse is `null` here, never an error (unlike
`tkt activity`). On jira's acli fallback, `list` fills only the people
fields; `view` always fills all of them. To match people across tickets, compare account ids, not
names. `tkt whoami` prints a name or email, not an account id.

### Activity semantics

`tkt activity` reports what happened on a named query's tickets in the
half-open window `[since, until)`:

- `--since` / `--until` take an ISO-8601 date or datetime. A value without an
  offset is **UTC** (a bare date is its UTC midnight), not local time.
  `--until` defaults to now; `since` must be before `until` (else exit 64).
- Events are sorted by `timestamp` (canonical UTC, `...T06:28:36.123Z`), then
  `evidence_id`. `kind` is `comment` (with `body`) or `change` (with `field`,
  `from`, `to`). Quote `evidence_id` when a skill cites what it saw.
- An unparseable backend timestamp is exit 3, never a silently dropped event.
- Tickets are chosen by the named query evaluated against their **current**
  state, not their state during the window: a ticket that has since left the
  query's status (e.g. moved from "In Progress" to "Done") is not scanned.
  For a retrospective, use a broader query (e.g. one without a status filter).

### Query scopes

Named queries (`[queries]`) are scoped to the configured project by default.
An optional `[query_scopes]` table marks individual queries (by name or
`tierN`) as `"global"` so they span projects, e.g. "everything assigned to me
across the org":

```toml
[query_scopes]
mine_all = "global"    # any key not listed is "project"
```

Only the Jira adapter applies it today; other backends ignore it. A key
that is not a defined query, or a value other than `"project"` or `"global"`,
is a config error (exit 2) on the next Jira query, and `tkt doctor` reports it
on every backend.

`tkt list --all-projects` forces the global scope for one call, whatever
`[query_scopes]` says: every ticket on a shared markdown board (no key-prefix
filter) or every project on Jira; other backends accept it and ignore it. It
is a view for humans only -- skills that act on the first result (e.g.
`select-ticket`) must not pass it, or one repo picks up another's ticket.

### Format gates

`tkt transition` into `review`, `qa_ready`, `qa`, `deploy_ready` or `done`
checks the ticket body's format first, and `tkt comment` checks the comment.
Findings print to stderr as `tkt: warn: ...`; `--json` stdout is unchanged.
Under `[enforcement].artifact_format = "strict"` a finding is exit 5 and the
ticket does not move (or the comment is not posted): fix the body and retry.
`tkt: warn: hierarchy: ...` lines are advisory and never block. A gate that
cannot judge (no node, Jira bodies, a backend error) lets the verb through.

### Exit codes

Branch on these — errors always go to stderr with a non-zero exit:

| Code | Meaning |
|------|---------|
| 2 | config error |
| 3 | provider error (includes "verb not supported by this adapter") |
| 4 | not found |
| 5 | format gate refused a transition or comment (`[enforcement].artifact_format = "strict"`) |
| 64 | usage error |

## SDLC skill pack

Canonical source: `skills/<name>/SKILL.md`. Harness-specific translations live in
per-tool folders (`.opencode/commands/`, `.cursor/commands/`, `.cursor/skills/`,
`.continue/prompts/`, `.windsurf/workflows/`, `.clinerules/workflows/`,
`.augment/commands/`, `.agents/workflows/`, `.github/prompts/`,
`.gemini/commands/`). `.agents/skills/` and `.kiro/skills/` are not stored:
`sync-pack` derives them byte-for-byte from `skills/` at sync time.

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
- Pipe JSON into `jq` with `printf '%s\n' "$VAR" | jq`, never `echo "$VAR"`:
  zsh's and dash's `echo` expand the `\n` escapes inside JSON strings and
  hand `jq` invalid JSON. Inside a TOML `"""` string use `printf '%s'`, since
  TOML would turn `\n` into a real newline. `tests/test_no_echo_jq.py`
  enforces both.

## Distributing the pack

`tkt sync-pack` installs the pack into a consumer repo as **committed copies**,
not symlinks: cloud harnesses and CI only see tracked files, so the symlinks
`tkt init --link-skills` writes are invisible to them. It only writes paths it
records in `.sdlc/pack-manifest.json`, deletes only on `--prune`, and is
idempotent (a second run with an unchanged pack leaves `git status` clean).
The one untracked write: before overwriting a file the pack has never written
whose content differs (say, a consumer's own
`.github/prompts/select-ticket.prompt.md`), it copies it to
`<name>.pre-sdlc.bak` beside it and prints the backup path. An existing backup
is never overwritten (the next is `<name>.pre-sdlc.1.bak`); backups are never in
the manifest, never pruned, and never shipped from a pack tree. If the copy
fails, or the file is unreadable, it is left untouched and the sync exits 1.
No pack file is ever written through a symlink out of the repo: a path that
resolves outside the consumer tree (a dangling or outside symlink, or a
symlinked directory above it) is skipped with a
`skipped <path>: resolves outside the repo` note, its manifest record kept,
and the sync still exits 0. A link to the very pack file it would copy (the
`--link-skills` case, run from that checkout) is already current and left
alone silently. Symlinks that stay inside the repo are followed. The same
note covers an `AGENTS.md` that resolves outside the repo (not read or
written; `AGENTS.md -> CLAUDE.md` still works), and a
`.sdlc/pack-manifest.json` that resolves outside (a symlinked `.sdlc`)
refuses the whole sync, `--check` included, with exit 64 before any write.

In a consumer repo it also maintains a generated block inside that repo's
`AGENTS.md`, delimited by markers — content outside the markers is preserved
verbatim. `tkt sync-pack --check` reports missing/locally-modified/out-of-date/
not-executable pack files and exits 1. Executable pack files are installed
executable, and a lost bit is repaired on re-sync. `tkt doctor` does **not** run
this check: it only reports whether the pack checkout has moved since the last
sync (`pack_commit`), as a non-fatal hint.

### Base layer

Every sync installs the base layer, whatever harnesses are selected:

| Pack source | Consumer dest |
| --- | --- |
| `skills/` (derived mirror) | `.agents/skills/` |
| `.agents/workflows/` | `.agents/workflows/` |
| `standards/` | `docs/sdlc/` |
| `enforcement/` (minus `*.test.mjs`) | `.sdlc/enforcement/` |

Codex needs no surface of its own: it reads the base layer plus `AGENTS.md`.
The manifest records these dest dirs under `base_layer`, and `--prune` never
removes them. `standards/` ships once the pack has one; until then that row
is a no-op. The checker ships executable, for consumer CI and hooks; `tkt`'s own
format gates always run the copy in `tkt`'s checkout.

### Harness selection

A bare `tkt sync-pack` installs the base layer plus the default harnesses
(`claude`, `copilot`, `kiro`). Harness selection only adds surfaces on top of
the base layer; it can never remove it. `agents` is accepted for old
manifests and scripts, and on its own means "base layer only". Name others
to add them to an already-synced project:

```sh
tkt sync-pack cursor              # add Cursor to this repo
tkt sync-pack cursor windsurf     # several at once
tkt sync-pack --all-harnesses     # every known harness
tkt sync-pack --list-harnesses    # names, target dirs, what's installed here
```

Selection is **additive and sticky**: the resulting set is recorded under
`harnesses` in `.sdlc/pack-manifest.json`, so a later bare `tkt sync-pack` keeps
every added harness up to date rather than reverting to the default three.
To remove a harness, drop it from that list, then run `tkt sync-pack --prune`.

The defaults apply only when nothing is named **and** nothing is recorded. So in
a repo that was set up with `tkt init --link-skills` (symlinked `.claude/`, no
manifest), `tkt sync-pack cursor` installs the base layer, `.cursor/` and the
`AGENTS.md` block — it will not drop committed copies on top of the symlinks.

### Your own skills

A skill the consumer writes under `.claude/skills/<name>/SKILL.md` is copied,
whole dir and byte for byte, into every verbatim skill surface the selection
installs: `.agents/skills/` always, plus `.kiro/skills/` and `.cursor/skills/`
when those harnesses are selected. The surface list is derived from the
harness registry (any dest named `<dir>/skills`), so a harness that later
gains one picks local skills up unchanged. Translated surfaces
(`.github/prompts/`, the command dirs) never get a copy: a rewrite is not a
copy. Rules:

- A name the pack owns (one it ships, `sync-skills`, or one a previous sync
  wrote and did not record as local) is never mirrored; the pack's copy
  wins, and a consumer dir it shadows is reported. If the pack starts
  shipping a name you already use, sync warns.
- A dir without a `SKILL.md` and a hidden dir are ignored. Within a skill,
  `.git`, `__pycache__`, `.DS_Store`, `.pre-sdlc.bak` backups, gitignored
  files and anything that resolves outside the repo (a `tkt init
  --link-skills` symlink, say) are left out, and a copy dir that resolves
  outside the repo is never written.
- The copies are recorded in the manifest like pack files, and the names
  under `local_skills`, so `--check` flags a copy older than its source. A
  copy dir sync-pack has never written into that holds a file not identical
  to the source's is your own harness-specific variant: it is kept whole
  (`kept <dir>: holds files sync-pack did not write`). Once sync-pack owns a
  copy dir, an extra file you add there is left alone and mirroring goes on.
  A copy dir that is a symlink to the source needs nothing.
- A skill whose `SKILL.md` git ignores is private and never mirrored. If git
  cannot list ignored files in a work tree, no local skill is mirrored that
  run (a warning says so) rather than risk publishing one. A skill, skills
  dir or file that could not be read or judged is not treated as removed:
  its existing copies are kept as they are, so `--prune` never deletes them
  on a transient failure. A copy path that resolves outside the repo (a
  dangling symlink, say) is never written through.
- Remove the skill and all its copies are retired, the `.agents/skills/` one
  included: `--prune` deletes them if unchanged. Base-layer protection
  covers files the pack still needs, not copies of a source that is gone.

### Pruning

A file a previous sync wrote that the current selection no longer ships (a
dropped harness, a skill the pack removed) is **retired**. A plain sync leaves
it in place, records it under `retired` in the manifest, and prints a note.
`tkt sync-pack --prune` deletes retired files; `--check --prune` lists them,
writes nothing, and exits 1 if any would go. Guarantees:

- Only paths recorded in the previous manifest (`files` or `retired`) are
  candidates, never an arbitrary consumer file, and only inside a dir the
  pack writes into (`.claude/`, `.kiro/skills/`, ...). Anything else, and any
  path with a `.git` segment, is skipped as `not a pack path`.
- The base layer, `AGENTS.md` and the manifest itself are never pruned. A
  base-layer file the pack stops shipping stays on disk and is tracked under
  `base_retired`, so re-shipping it later does not warn that it pre-existed.
  An old manifest's `base_layer` entries count only when they name a pack
  dir, so a tampered list cannot point the sync outside the repo.
- A manifest path that is absolute, not in the canonical form the pack writes
  (`./x`, `a//b`, `..`, a backslash), or that goes through a symlinked dir
  (inside or outside the tree) is skipped and reported, so a corrupted or
  tampered manifest cannot reach other files.
- A retired path that is the same file as a live one (a case-only rename on a
  case-insensitive disk, a hard link, a base-layer dir linked to another
  surface, or a link you added under a base-layer dir) is skipped, not
  deleted. Links under base-layer dirs are followed while they stay inside
  the repo. If part of the base layer cannot be read, prune deletes nothing
  and reports each candidate as `kept (could not verify)`.
- A file whose content changed since the pack wrote it is kept and reported
  (`kept (locally modified)`), and stays retired. So is a file that is now a
  symlink, or one prune cannot read. A file that cannot be deleted is warned
  about, counted in the summary (`could NOT be pruned`), and stays retired.
- Parent dirs left empty are removed, up to but never including the repo root.

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
- This repo is pure stdlib Python 3.11+; no build step. The exception is
  `enforcement/` (stdlib-only Node checkers, CI-tested). `tkt` runs without
  Node; the format gates just stay inactive.

## When in doubt

- Run `tkt doctor` to validate a project's config.
- Run `tkt view KEY --json` to see the normalized shape.
- Config keys are not self-documenting: consult `.sdlc/config.toml` or
  `examples/config.<provider>.toml`.
