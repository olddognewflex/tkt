# tkt — provider-agnostic ticketing CLI

`tkt` is the adapter shim that lets the SDLC skill pack run against any ticketing
backend. Skills call **semantic verbs** (`tkt view`, `tkt transition`, ...); `tkt`
reads `.sdlc/config.toml`, dispatches to the configured provider adapter, and
normalizes every backend into one JSON shape. Skills never touch `acli`/`gh`/REST.

This is a **standalone, portable package** — clone it once and point any project at
it. Nothing here is specific to a single repo or backend. Requires Python 3.11+
(uses stdlib `tomllib`); no third-party deps.

Status: core + `jira`, `markdown`, and `github` adapters, plus the **full** ported
SDLC skill pack — 13 skills + the ticket-researcher agent (see "Skill pack").
`linear`/`qi` adapters are the remaining Phase 3 work (just implement the same verb
contract; no skill changes).

## Install

```sh
git clone <this-repo> ~/Development/tkt
ln -s ~/Development/tkt/tkt /usr/local/bin/tkt   # put `tkt` on PATH (optional)
```

`tkt` runs from anywhere; it locates its own `core/`/`adapters/` relative to the
script, so the symlink target must resolve back to the repo (a symlink is fine).

## Use in a project

```sh
cd my-project
mkdir -p .sdlc
cp ~/Development/tkt/examples/config.markdown.toml .sdlc/config.toml   # or config.jira.toml
$EDITOR .sdlc/config.toml
tkt doctor

# Activate the skills (copy or symlink into the project's .claude/skills):
ln -s ~/Development/tkt/skills/* .claude/skills/
```

Config discovery order: `--config <path>` → `$TKT_CONFIG` → nearest `.sdlc/config.toml`
walking up from cwd. So `tkt` invoked anywhere inside `my-project` finds its config.

## Verb contract

Every adapter implements these. Read verbs accept `--json` (either side of the verb).

| Command | Does | Output |
| --- | --- | --- |
| `tkt whoami` | current user id | string |
| `tkt list --tier N` / `--query NAME` | run named query from `[queries]` | ticket list / `--json` array |
| `tkt view KEY` | one ticket | normalized ticket / `--json` |
| `tkt transition KEY ROLE` | move to lane mapped from ROLE | `KEY -> Lane` |
| `tkt comment KEY BODY` | post activity comment | confirmation |
| `tkt blockers KEY` | unresolved blockers only | list / `--json` |
| `tkt worklog KEY --from-role ROLE [--note T] [--billable]` | log time since entry into ROLE's lane → now | worklog / `--json`; no-op if `[timetracking].provider="none"` |
| `tkt lane-time KEY --role ROLE` | log time for an already-exited lane (entry→exit) | worklog / `--json` |
| `tkt create --type T --summary S [--priority P] [--assignee A] [--body B] [--project P]` | create a ticket | new key / `--json` ticket |
| `tkt link KEY --to OTHER --type T` | link KEY → OTHER (`T` = outward description: `blocks`, `is blocked by`, `Fixes`, …) | confirmation |
| `tkt lane ROLE` | resolve ROLE → provider lane name | string (config-only, no backend) |
| `tkt cfg DOTTED.KEY [--pkg X] [--ticket K] [--slug S]` | read a config value; substitutes `{pkg}`/`{key}`/`{key-lower}`/`{slug}` | string / `--json` |
| `tkt doctor` | validate auth + reachability + board model | checks; exit 1 if any fail |

`create`/`link` are optional verbs — adapters opt in (jira + markdown support them);
a backend without them returns a clear "not supported" error rather than failing to load.

Errors always go to stderr with a non-zero exit (2 config, 3 provider, 4 not-found,
64 usage) — skills can branch on exit code and never get a silent failure.

### Lane roles, not lane names

Skills reference **roles** (`in_progress`, `review`, `qa_ready`, `done`, `blocked`,
...). `[board.roles]` maps each role to the provider's literal lane string. This is
what lets the same skill drive an 8-lane Jira board and a 3-column markdown board:

```toml
[board.roles]
review = "PR Needs Review"   # Jira
# review = "In Review"        # a different board
```

In skills, prefer `$(tkt lane review)` when you need to print/commit the literal
name, and `tkt transition KEY review` to move.

## Normalized ticket shape (`tkt view --json`)

```json
{
  "key": "TKT-1", "type": "Story", "type_class": "full_sdlc",
  "summary": "...", "description": "...", "acceptance": ["..."],
  "status": "In Progress", "status_role": "in_progress",
  "assignee": "raymond", "priority": "Highest",
  "labels": ["..."], "components": ["..."],
  "blocked_by": [{"key": "TKT-2", "resolved": false}],
  "blocks": [], "transitions": ["..."], "url": "..."
}
```

`type_class` is resolved from `[issue_types]` (`full_sdlc` vs `deliverable`) — this is
what `automated-sdlc` Phase 1.5 branches on, now provider-independent.

## Providers

### jira
Ports the original acli + Jira REST + Tempo logic. Auth via env named in
`[ticketing].auth_env` (`CONFLUENCE_SITE/EMAIL/API_TOKEN`, plus `TEMPO_API_TOKEN`
for non-billable worklogs). `worklog`/`lane-time` page the full changelog and patch
Tempo exactly as the old `annotate_lane_time` helper did.

### github
Issues = tickets; board status from **Projects v2** (`board="projectv2"`, default) or
a `Status:` **label** convention (`board="labels"`). All access via the `gh` CLI:
`gh issue` (needs `repo` scope) and `gh project` (needs `project`/`read:project`
scope — run `gh auth refresh -s project,read:project` to enable projectv2 mode).

- **Key** = issue number. **type**/**priority** from label conventions
  (`type_label_prefix` / `priority_label_prefix`, or labels matching `[issue_types]`).
- **blocked_by/blocks** parsed from the body (`Blocked by #N` / `Blocks #N`);
  `resolved` = referenced issue closed.
- **Queries are GitHub-native**, not JQL: projectv2 → Projects filter syntax
  (`gh project item-list --query`); labels → issue search (`gh issue list --search`).
- **No time tracking** — `worklog`/`lane-time` are no-ops; set
  `[timetracking].provider = "none"`.

Validation status: labels mode is validated live (reads); projectv2 mode is built
against the documented `gh project` JSON and validate-after-scope-refresh.

### markdown
Zero-dependency local board. One `<KEY>.md` per ticket under `[markdown].board_dir`;
status transitions + worklogs recorded in JSONL sidecars under `[markdown].state_dir`
(machine-local, derived — the markdown stays human-canonical).

Ticket file format:

```markdown
---
type: Story
status: To Do
priority: Highest
assignee: raymond
labels: [api, auth]
blocked_by: [TKT-2]
blocks: []
---
# One-line summary

Description...

## Acceptance
- criterion one

## Comments
- 2026-06-02T10:00:00Z raymond: note
```

Frontmatter is a tiny YAML subset: `key: value`, `[a, b]` list literals, no nesting.
Queries use a tiny JQL subset (`field = "v" [AND ...] [ORDER BY field DIR]`,
`currentUser()`, `is EMPTY`) so the same `[queries]` strings read like Jira's.

## Skill pack

`skills/` holds the provider-agnostic SDLC skills (ported from the Jira-coupled
originals — every ticketing/board call now goes through `tkt`):

| Skill | Role |
| --- | --- |
| `select-ticket` | pick next ticket (tiered queries + blocker filter) |
| `triage-ticket` | read + move to `in_progress` |
| `plan-ticket` | structured implementation plan |
| `open-pr` | commit/push/PR/reviewers + `→ review` with lane-time |
| `ci-fix` | watch CI, diagnose, fix, loop until green |
| `self-review` | adversarial pre-PR review loop |
| `respond-to-review` | address review comments, `revise ↔ review` loop |
| `deploy-preview` | confirm preview env, post URL to ticket + PR |
| `complete-deliverable` | short-circuit `→ done` for deliverable types |
| `deploy-ready` | merge, watch staging, gate prod, QA lane-times |
| `resume-from-revise` | re-enter the loop after a human revise fix |
| `check-blockers` | classify blocked tickets, recommend unblocks |
| `hotfix-revert` | fast-track prod revert (uses `tkt create`/`link`) |
| `automated-sdlc` | orchestrator across all of the above |

`agents/ticket-researcher.md` is the read-only lookup subagent (provider-agnostic
port of the old `jira-researcher`).

To activate in a project, copy these into the project's `.claude/skills/` (and
`agents/`) — or symlink. They depend only on `tkt` + `.sdlc/config.toml`, so the
same skill set runs against any configured backend. Skills speak in **roles**
(`in_progress`, `review`, `done`, ...) and read toolchain/VCS/deploy settings via
`tkt cfg` (e.g. `tkt cfg build.test --pkg X`, `tkt cfg vcs.branch_fmt --ticket K
--slug s`), so nothing about Jira/pnpm/GitHub is baked into skill text.

VCS (PR/CI/merge via `gh`) and infra (preview/deploy) remain GitHub/cloud-shaped;
repo, branch formats, reviewers, and workflow names are all config-driven, and
genuinely infra-specific steps (e.g. preview-URL extraction) are clearly marked
PROJECT-SPECIFIC in the skill text.

## Architecture

```
core/
  cli.py       verb parsing + dispatch + output formatting
  config.py    .sdlc/config.toml loader; role<->lane, type routing, queries
  registry.py  provider name -> adapter (lazy import)
  schema.py    Ticket / Worklog / Check dataclasses + to_dict()
  errors.py    typed errors -> exit codes
adapters/
  base.py      the verb contract (ABC) — what a new provider must implement
  jira.py      jira adapter
  markdown.py  markdown adapter
  github.py    github adapter (Issues + Projects v2 / labels)
examples/      config.jira.toml, config.markdown.toml, config.github.toml
```

To add a provider: implement `adapters/base.Adapter`, register it in
`core/registry.py`, add an example config. No skill changes required.
