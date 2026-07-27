---
inclusion: fileMatch
fileMatchPattern: '.sdlc/config.toml'
---

# .sdlc/config.toml — Schema Reference

When editing `.sdlc/config.toml`, follow this schema. All sections are optional
except `[ticketing]` and `[vcs]`.

## `[ticketing]`

```toml
[ticketing]
provider = "jira"          # "jira" | "github" | "linear" | "markdown"
project  = "PROJ"          # project key (Jira/Linear) or unused (GitHub)
board_id = "123"           # Jira board ID (optional)
auth_env = ["VAR1"]        # env vars required for auth (validated by `tkt doctor`)
```

### GitHub-specific: `[github]`

```toml
[github]
board          = "projectv2"          # "projectv2" | "labels"
repo           = "org/repo"
project_owner  = "@me"                # login, org, or "@me"
project_number = 1                    # Projects v2 number
status_field   = "Status"             # single-select field name
priority_label_prefix = "Priority: "  # label prefix for priority extraction
type_label_prefix     = ""            # "" matches [issue_types] names directly
status_label_prefix   = "Status: "    # labels mode only
```

## `[board.roles]`

Maps canonical roles to the provider's literal lane/status names (case-sensitive):

```toml
[board.roles]
backlog      = "Backlog"
todo         = "To Do"
in_progress  = "In Progress"
review       = "PR Needs Review"
qa_ready     = "Ready for QA"
qa           = "QA"
deploy_ready = "Ready for Deploy"
done         = "Done"
revise       = "Revise"
blocked      = "Blocked"
cancelled    = "Cancelled"
```

Not every role is required — boards without a lane (e.g. no `qa_ready`) simply omit
that role. Type routing and ownership keep tickets on the correct flow.

## `[board.ownership]`

Declares which transitions are agent-driven vs human-gated:

```toml
[board.ownership]
"todo->in_progress"   = "agent"
"in_progress->review" = "agent"
"review->qa_ready"    = "agent"
"qa_ready->qa"        = "human"
"deploy_ready->done"  = "human"
```

## `[issue_types]`

Determines `type_class` routing:

```toml
[issue_types]
full_sdlc   = ["Story", "Bug"]                              # full PR/review/deploy pipeline
deliverable = ["Task", "Sub-task", "Chore", "Epic", "Spike"] # short-circuit to done
```

## `[queries]`

Named queries for ticket selection. Syntax is provider-native (JQL for Jira,
Projects filter for GitHub projectv2, search syntax for GitHub labels mode):

```toml
[queries]
tier1 = '...'          # highest priority, assigned to me
tier2 = '...'          # any priority, assigned to me
tier3 = '...'          # highest priority, unassigned
tier4 = '...'          # any priority, unassigned
tier5 = '...'          # backlog (promotion candidates)
blocked = '...'        # blocked tickets, assigned to me
blocked_team = '...'   # all blocked tickets
deploy_ready = '...'   # tickets ready for deploy
```

Tiers 1-2 auto-select; tiers 3-5 recommend only.

## `[vcs]`

```toml
[vcs]
provider       = "github"                          # "github" | "gitlab"
repo           = "org/repo"                        # full repo identifier
default_branch = "main"                            # target branch for PRs
branch_fmt     = "feature/{key-lower}-{slug}"      # template: {key}, {key-lower}, {slug}
hotfix_fmt     = "hotfix/{key-lower}-{slug}"       # hotfix branch template
reviewers      = ["user1", "bot[bot]"]             # default PR reviewers
merge          = "squash"                          # "squash" | "merge" | "rebase"
```

## `[build]`

```toml
[build]
build     = "make build"       # build command; {pkg} placeholder for monorepo filter
test      = "make test"        # test command; {pkg} placeholder
typecheck = "tsc --noEmit"     # type check command (use "true" to skip)
lint      = "eslint ."         # lint command (use "true" to skip)
bdd       = "npm run bdd"      # optional behavior-spec runner; see docs/behavior-specs.md
```

`bdd` is the only optional key here. When unset, `tkt cfg build.bdd` exits `4` and
the caller skips cleanly; any other non-zero exit is a real error to surface.

## `[models]` (optional)

```toml
[models]                       # tier -> model; resolved via `tkt cfg models.<tier>`
cheap    = "claude-haiku-4-5"
standard = "claude-sonnet-5"
deep     = "claude-opus-5"
```

Skills declare `model_tier: cheap|standard|deep` in frontmatter; this table maps
it. Omit the whole table to run everything on the harness default. Unrelated to
`[queries].tierN`, which selects *tickets*.

## `[run]` (optional)

```toml
[run]
harness_cmd        = "claude -p {prompt} --permission-mode acceptEdits"
max_iterations     = 30        # total phase invocations before halting
max_phase_attempts = 3         # failed attempts on one phase -> blocked
invocation_timeout = 3600      # seconds per harness invocation
```

`harness_cmd` is required for `tkt run` and is the only key with no default.

## `[deploy]`

```toml
[deploy]
preview_workflow    = "deploy-preview.yml"
staging_workflow    = "deploy-staging.yml"
production_workflow = "deploy-production.yml"
```

## `[timetracking]`

```toml
[timetracking]
provider = "none"        # "none" | "jira-worklog" | "tempo"
billable = false         # whether worklogs default to billable
auth_env = []            # env vars for time tracking auth
```

When `provider = "none"`, `tkt worklog` and `tkt lane-time` are safe
no-ops — they return empty results without error.

## `[docs]` (optional)

```toml
[docs]
provider = "confluence"
space    = "TEAM"
```

## Validation

After editing, run `tkt doctor` to validate the config:
- Auth env vars are set
- Provider is reachable
- Board roles resolve to real lanes/statuses
- Queries parse without error
