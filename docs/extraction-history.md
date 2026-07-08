# Portable SDLC Skill Pack — Plan

> Historical extraction plan/coupling inventory; kept for reference.


Goal: take the `automated-sdlc` skill family (currently hardwired to Jira + Tempo +
GitHub + pnpm/turbo) and make it **plug into any project** with a configurable
ticketing system and kanban board. Backends to support: **Jira, GitHub Issues+Projects,
Linear, qi vault, plain markdown board**. Packaging: **adapter CLI shim + config**.

---

## 1. Coupling inventory (what is hardwired today)

Scanned all 22 skills + 4 agents. Hardcoded provider assumptions:

| Coupling | Where | Count |
| --- | --- | --- |
| `acli jira workitem` (view/transition/comment/search/auth) | 12 skills + jira-researcher | 56 calls |
| `curl` → Jira REST (`/rest/api/3/...`), JQL, changelog pagination | select-ticket, deploy-ready, check-blockers, annotate helper | many |
| Tempo REST (`api.tempo.io/4`), billable logic | automated-sdlc, deploy-ready | 8 |
| Auth env: `CONFLUENCE_SITE/EMAIL/API_TOKEN`, `CONFLUENCE_SPACE_ID`, `TEMPO_API_TOKEN` | everywhere | 50+ |
| Project key `DTB`, board id `999` | select-ticket, automated-sdlc | — |
| Lane names: `Backlog→To Do→In Progress→PR Needs Review→Ready for QA→QA→Ready for Deploy→Done` + `Revise/Blocked/Cancelled` | all pipeline skills | — |
| Issue types `Story/Bug/Task/Sub-task/Chore/Epic/Spike` → routing | automated-sdlc P1.5, triage, complete-deliverable | — |
| VCS: `gh`, repo `auctionedge/edge-dev-platform`, Copilot reviewer, FelipeFlow branches | open-pr, deploy-ready, ci-fix | — |
| Build/test: `pnpm turbo build/test/typecheck/lint` | automated-sdlc, plan, resume | 17 |
| Keyword→package map | triage-ticket | — |
| Docs: Confluence pages | publish-docs, grill-with-adrs | — |

Root problem: skills mix **process logic** (what step comes next) with **provider
calls** (how to talk to Jira). Abstraction = split those two.

---

## 2. Target architecture

```
 skill (.md, provider-agnostic process logic)
        │  calls semantic verbs only
        ▼
   tkt  CLI shim  ──reads──►  .sdlc/config.toml   (provider + board model)
        │  dispatches by config.provider
        ▼
 adapters:  jira | github | linear | qi | markdown   (each implements the verb contract)
        │  emits normalized JSON
        ▼
   real backend (acli/curl, gh+GraphQL, Linear GQL, qi CLI, local md file)
```

Three things move out of the skills:

1. **`tkt` adapter CLI** — one provider-agnostic verb surface. Skills never touch
   `acli`/`curl`/Tempo again. Each provider implements the same verbs, returns the
   same normalized JSON shape.
2. **`.sdlc/config.toml`** — single source of truth: provider, project/board ids,
   auth env var names, lane-role mapping, type routing, build commands, VCS repo,
   time-tracking + docs providers.
3. **Lane *roles*** — skills reference roles (`in_progress`, `review`, `qa_ready`,
   `done`), not literal lane names. Config maps role → provider lane string. This is
   what lets the same skill drive an 8-lane Jira board and a 3-column markdown board.

### 2.1 Normalized ticket JSON (the contract every adapter returns)

```json
{
  "key": "DTB-123",
  "type": "Story",
  "type_class": "full_sdlc | deliverable",   // resolved from config routing
  "summary": "...",
  "description": "...",
  "acceptance": ["...", "..."],
  "status": "In Progress",
  "status_role": "in_progress",
  "assignee": "raymond",
  "priority": "Highest",
  "labels": ["..."],
  "components": ["..."],
  "blocked_by": [{"key": "DTB-100", "resolved": false}],
  "blocks": ["DTB-130"],
  "transitions": ["PR Needs Review", "Blocked"],   // available next lanes
  "url": "https://..."
}
```

Skills read this shape regardless of backend. Adapters translate provider payloads into it.

### 2.2 Verb contract (`tkt`)

Derived from the 56 `acli` calls + curl blocks. Minimal set that covers every skill:

| Verb | Replaces | Notes |
| --- | --- | --- |
| `tkt whoami` | `acli jira auth status`, `currentUser()` | normalized user id |
| `tkt list --tier N` / `tkt list --query <name>` | JQL tier curls | named queries live in config, not in skills |
| `tkt view <key> [--json]` | `acli jira workitem view` | returns normalized JSON above |
| `tkt transition <key> <role>` | `acli ... transition --status` | role→lane resolved; rejects illegal moves per board model |
| `tkt comment <key> <body>` | `acli ... comment create` | — |
| `tkt blockers <key>` | issuelinks parsing | returns unresolved blockers only |
| `tkt worklog <key> --from-role <role> [--note] [--billable]` | `annotate_lane_time` python | encapsulates changelog pagination + Tempo billable; **no-op** if timetracking=none |
| `tkt lane-time <key> --role <role>` | deploy-ready retro loop | entry→exit deltas for already-exited lanes |
| `tkt lane <role>` | literal lane strings in prose | prints provider lane name (for echo/commits) |
| `tkt doctor` | scattered prereq checks | validates auth + board model reachable |

All verbs: exit non-zero + stderr message on failure; never silent.

### 2.3 Config schema (`.sdlc/config.toml`)

```toml
[ticketing]
provider   = "jira"              # jira | github | linear | qi | markdown
project    = "DTB"
board_id   = "999"
auth_env   = ["CONFLUENCE_SITE", "CONFLUENCE_EMAIL", "CONFLUENCE_API_TOKEN"]

[board.roles]                    # role -> provider lane name
backlog     = "Backlog"
todo        = "To Do"
in_progress = "In Progress"
review      = "PR Needs Review"
qa_ready    = "Ready for QA"
qa          = "QA"
deploy_ready= "Ready for Deploy"
done        = "Done"
revise      = "Revise"
blocked     = "Blocked"
cancelled   = "Cancelled"

[board.ownership]                # role transition -> agent | human (drives gates)
"todo->in_progress"      = "agent"
"review->qa_ready"       = "agent"
"qa_ready->qa"           = "human"
# ...

[issue_types]
full_sdlc   = ["Story", "Bug"]
deliverable = ["Task", "Sub-task", "Chore", "Epic", "Spike"]

[queries]                        # named, per-provider; skills call by name
tier1 = 'status="To Do" AND priority=Highest AND assignee=currentUser() ORDER BY created ASC'
tier2 = 'status="To Do" AND assignee=currentUser() ORDER BY priority DESC, created ASC'
# ...

[vcs]
provider     = "github"
repo         = "auctionedge/edge-dev-platform"
branch_fmt   = "feature/{key-lower}-{slug}"
reviewers    = ["copilot-pull-request-reviewer[bot]"]
merge        = "squash"

[build]
build     = "pnpm turbo build --filter={pkg}"
test      = "pnpm --filter {pkg} test -- --run"
typecheck = "pnpm turbo typecheck"
lint      = "pnpm turbo lint"

[timetracking]
provider = "tempo"               # none | jira-worklog | tempo
billable = false
auth_env = ["TEMPO_API_TOKEN"]

[docs]
provider = "confluence"          # none | confluence | markdown
space    = "..."
```

A new project = drop in skill pack + `tkt`, write this file, set env. Done.

---

## 3. Per-provider notes (the hard parts)

| Provider | Tickets | Board lanes | Time tracking | Notes |
| --- | --- | --- | --- | --- |
| **jira** | acli + REST (port existing code verbatim) | status field | Tempo/worklog (existing) | regression baseline — behavior must stay identical |
| **github** | Issues via `gh`/REST | **Projects v2 = GraphQL**, status = single-select field option ids | none → `worklog` becomes a comment | board mapping is the main effort; needs project node id + field/option ids in config |
| **linear** | GraphQL API | workflow states (native role-like model) | none (or estimate field) | cleanest lane model; states have types (started/completed) that map to roles well |
| ~~**qi**~~ | _dropped — qi has no issue system to adapt; use the `markdown` backend for a local board instead_ | | | |
| **markdown** | a `BOARD.md` kanban file | `## Lane` headings, tasks move between them | local `.sdlc/worklog.jsonl` | zero deps; the portable default + abstraction proof |

Cross-cutting:
- **Time tracking is optional.** On github/linear/markdown, `tkt worklog` degrades to a
  comment + local log. Tempo billable logic stays jira-only.
- **Queries aren't portable.** JQL → GraphQL filters → markdown scans differ, so they
  live in config as named queries; skills only reference `tier1`/`tier2`.
- **Blockers differ** (Jira links vs GitHub "blocked by" vs Linear relations vs md
  checklist) — normalized behind `tkt blockers`.

---

## 4. Phased rollout (start: `automated-sdlc`)

### Phase 0 — Foundation
- Lock the normalized JSON schema + verb contract (§2.1/2.2).
- Write `.sdlc/config.toml` schema + example configs for all 5 providers.
- Build `tkt` core (config load + dispatch) and **two** adapters:
  - `jira` (port existing acli/curl/Tempo logic — exact behavior, regression anchor)
  - `markdown` (proves the abstraction with zero external deps)
- `tkt doctor` for both.

### Phase 1 — Port `automated-sdlc` + direct sub-skills to verbs
Order (leaf → root): `select-ticket` → `triage-ticket` → `plan-ticket` → `open-pr` →
`complete-deliverable` → `automated-sdlc` orchestrator.
Per skill: swap provider calls for `tkt`, lane literals for roles, build cmds for
`[build]`, repo for `[vcs]`. **Acceptance: identical behavior on the live DTB Jira board.**

### Phase 2 — Remaining pipeline skills
`ci-fix`, `self-review`, `respond-to-review`, `deploy-preview`, `deploy-ready`,
`resume-from-revise`, `check-blockers`, `hotfix-revert`, `jira-researcher` agent →
`ticket-researcher`. Fold deploy-ready's retro changelog loop into `tkt lane-time`.

### Phase 3 — Remaining adapters
`github` (Issues + Projects v2 / labels) and `linear` (GraphQL) — done. No skill
changes; each only implements the verb contract. (`qi` dropped — no issue system.)
Validate each with `tkt doctor` + a dry run.

### Phase 4 — Distribution + validation  ✅
- `tkt init` scaffolder: `--provider` (prompts on a TTY), writes `.sdlc/config.toml`
  from the matching example, `--link-skills` symlinks the pack into `.claude/`,
  `--sample` drops a markdown starter ticket. Refuses to clobber without `--force`.
- End-to-end validated on a freshly-scaffolded **markdown** project: init → doctor →
  select → triage → route → open-pr (worklog) → done. (github labels mode reads are
  live-validated; projectv2 + mutations need scope/consent.)
- Docs: README Install / Use-in-a-project / verb contract / per-provider notes.

---

## 5. Plug-into-a-new-project flow (end state)

1. Copy `skills/` pack + `tkt` adapter into the project's `.claude/` (or a shared plugin).
2. `sdlc init` → choose provider, answer prompts → writes `.sdlc/config.toml`.
3. Export auth env vars named in `config.ticketing.auth_env`.
4. `tkt doctor` → green.
5. Invoke `automated-sdlc`. Same skills, new backend.

---

## 6. Risks / open questions

- **GitHub Projects v2** board control is GraphQL-only and needs project/field/option
  node ids cached in config — biggest single implementation cost.
- **`tkt` language**: shell wrapper vs Python vs a small Go binary (qi is Go — could
  ship `tkt` as a qi subcommand for the qi provider). Recommend Python (matches existing
  helper code, no build step) unless you want it folded into qi.
- **Lane ownership / gates**: encoding agent-vs-human transitions in config (§2.3
  `[board.ownership]`) lets the orchestrator stay generic — confirm that's wanted vs
  keeping gates in skill prose.
- **Where the pack lives**: per-repo `.claude/` copy (drifts) vs a shared OMC-style
  plugin (single source, versioned). Recommend plugin.
```
