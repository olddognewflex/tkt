# Migrating edge-dev-platform to the pack

Roadmap for retiring the Jira-hardwired SDLC skill copies inside
`edge-dev-platform/.claude/skills/` and consuming this pack via `tkt sync-pack`
instead. The migration is deliberately deferred; this document is the plan of
record for when it lands (one PR, plus a short bake period on a branch).

## Canonical-source rule (in force NOW, before the migration)

As of the company import date, **this pack is canonical for the 15 shared
skills** (the 14 pipeline skills + `sync-skills`). Changes to shared skills must
be made here and flow out via `tkt sync-pack`; do **not** land pipeline-skill
changes in `edge-dev-platform/.claude/skills/` — they will be overwritten at
migration time and meanwhile create drift. Edge-only skills (list below) remain
owned by edge-dev-platform.

## Step 1 — Add `.sdlc/config.toml`

Commit a Jira-backed config at the repo root:

```toml
[ticketing]
provider = "jira"
project  = "DTB"

[jira]
board_id = 999            # DTB — Fusion Force Board
# auth via CONFLUENCE_SITE / CONFLUENCE_EMAIL / CONFLUENCE_API_TOKEN (acli-compatible env)

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

[issue_types]
full_sdlc   = ["Story", "Bug"]
deliverable = ["Task", "Sub-task", "Epic", "Spike"]

[timetracking]
provider = "tempo"        # TEMPO_API_TOKEN; non-billable default

[vcs]
provider   = "github"
repo       = "auctionedge/edge-dev-platform"
branch_fmt = "{type}/{key}_{slug}"    # FelipeFlow; keep < 40 chars
reviewers  = ["copilot"]              # + Code Owners via repo settings
merge      = "queue"                  # squash via merge queue

[build]
build     = "pnpm turbo build"
typecheck = "pnpm turbo typecheck"
lint      = "pnpm turbo lint"
test      = "pnpm turbo test -- --run"
# Go lanes: go build ./... / go test ./... resolved per-service; see docs/go/service-scaffold.md
```

Validate with `tkt doctor`. Note: consumer repos must **commit**
`.sdlc/config.toml` and `.sdlc/pack-manifest.json` — do not blanket-gitignore
`.sdlc/` (ignore only provider state dirs if the markdown backend is ever used).

## Step 2 — Install the pack

```sh
tkt sync-pack        # approved set: .claude/skills (14), .claude/agents/ticket-researcher.md,
                     # .github/prompts, .kiro/skills, AGENTS.md managed block, manifest
```

Edge supports every harness technically, so the full set is appropriate here:
`tkt sync-pack --all-harnesses`.

## Step 3 — Dispose of the 21 existing skills

**Delete 14** (replaced by pack copies; remove each skill dir AND its Tier-A/B
mirror copies in `.agents/skills`, `.kiro/skills`, `.github/prompts`,
`.gemini/commands`, `.cursor/*`, `.windsurf/workflows`, `.clinerules/workflows`,
`.continue/prompts`, `.augment/commands`, `.opencode/commands`):

`automated-sdlc`, `check-blockers`, `ci-fix`, `complete-deliverable`,
`deploy-preview`, `deploy-ready`, `hotfix-revert`, `open-pr`, `plan-ticket`,
`respond-to-review`, `resume-from-revise`, `select-ticket`, `self-review`,
`triage-ticket`

**Keep 6 Edge-only** (never ported; stay canonical in edge-dev-platform):

`add-service`, `debug`, `felipe-flow`, `grill-with-adrs`, `publish-docs`,
`review-pr`

**Keep `sync-skills`, rescoped**: pack skills now arrive pre-translated via
`sync-pack`, so edge's `sync-skills` shrinks to translating only the 6 Edge-only
skills. Update its inventory matrix accordingly. `scripts/sync-ai-support.mjs`
(Tier-D programmatic surfaces: Codex config, MCP mirrors, source manifest) is
untouched by this migration.

## Step 4 — Agents

Map `jira-researcher` → the pack's `ticket-researcher` (installed to
`.claude/agents/`). Delete `.claude/agents/jira-researcher.md`; keep
`code-reviewer`, `cdk-validator`, `secret-scanner`. Update any skill/doc
references.

## Step 5 — Verify

- `tkt doctor` green; `tkt view DTB-<n>` returns a real ticket.
- Run one full `select-ticket → triage-ticket` cycle against a scratch DTB
  ticket; confirm transitions + lane-time worklog land in Jira/Tempo.
- Re-run `tkt sync-pack` → `git status` clean (idempotent).
- `pnpm ai:check` still passes for the surfaces it owns.

## Risks / notes

- Lane-time semantics move from the inline Python changelog/Tempo helper to
  `tkt worklog` — compare one ticket's numbers before deleting the old skill.
- `deploy-preview` URL extraction and `deploy-ready` merge/deploy steps stay
  config-driven but Edge-shaped; review their PROJECT-SPECIFIC callouts after
  install.
- PR title/Jira-key conventions are enforced by edge CI, not the pack — the
  `[vcs]` config above must keep matching FelipeFlow.
