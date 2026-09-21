---
name: triage-ticket
description: 'Read a ticket, extract requirements, move it to In Progress, and start time tracking. Provider-agnostic via tkt.'
allowed-tools: [Bash, Read, Edit]
model_tier: cheap
---

# Triage Ticket

Read a ticket, extract actionable requirements, transition to the `in_progress`
lane, and start time tracking. All access via `tkt`; backend/board from
`.sdlc/config.toml`.

## Prerequisites

- `tkt doctor` passes.

## Lanes

Skills speak in **roles**, not literal lane names. The default board roles:

```
backlog → todo → in_progress → review → qa_ready → qa → deploy_ready → done
```

Side roles: `revise`, `blocked`, `cancelled`. Resolve a role to the provider's
actual lane string with `tkt lane <role>` when you need to print it.

## Input

- Ticket key (e.g. from `select-ticket`). If none provided, invoke `select-ticket`
  first rather than asking.

## Steps

### 1. Read the ticket

```shell
tkt view "$KEY" --json > /tmp/tkt_ticket.json
```

Extract from the normalized shape:

- `type` + `type_class` — `full_sdlc` (Story/Bug) vs `deliverable` (Task/Epic/…);
  drives Phase 1.5 routing in `automated-sdlc`.
- `summary`, `description`, `acceptance`
- `labels`, `components`, `priority`
- `blocked_by` (unresolved entries mean you should not have been routed here)

### 2. Identify affected packages

Map ticket content (summary/description/labels/components) to your repo's
packages. Keep a project-specific keyword→package table in the project's own docs
or `CLAUDE.md`; this skill stays generic. Example shape:

| Keyword | Package |
| --- | --- |
| auth, JWT, token | `@scope/auth` |
| cart, hold | `@scope/cart-service` |

### 3. Move to In Progress

```shell
tkt transition "$KEY" in_progress
tkt edit "$KEY" --agent-status processing
```

### 3b. Create the working branch

Branch as soon as the ticket is in progress — before any file is touched — so
work cannot start on the default branch. Slug from the summary (lowercase,
hyphen-separated, a few words). Idempotent: re-running triage on a ticket
already branched is a no-op.

```shell
BRANCH=$(tkt cfg vcs.branch_fmt --ticket "$KEY" --slug "<slug-from-summary>")
if [ "$(git rev-parse --abbrev-ref HEAD)" != "$BRANCH" ]; then
  git checkout -b "$BRANCH" 2>/dev/null || git checkout "$BRANCH"
fi
```

This runs for every ticket type. A ticket later routed to
`complete-deliverable`, or stopped by an external blocker, leaves an unused
local branch — cheaper than discovering at commit time that the work landed on
the default branch.

Uncommitted changes carry across `git checkout -b`, so a run that already
started editing is still recoverable by branching at this point.

### 4. Start time tracking

Entry time is recorded implicitly by the provider's history/changelog; `tkt
worklog ... --from-role in_progress` later computes elapsed time from this entry.
Nothing to do here beyond the transition.

### 5. Add a work-start comment

```shell
tkt comment "$KEY" "Starting work. Affected packages: <pkg1>, <pkg2>"
```

## Output

Provide to the next skill:

- Ticket key
- `type` + `type_class` (so the orchestrator routes full SDLC vs deliverable)
- Summary
- Acceptance criteria (bullet list)
- Affected packages
- The working branch name (created in step 3b)
- Any blockers/unknowns identified
