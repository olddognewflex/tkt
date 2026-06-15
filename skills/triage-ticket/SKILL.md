---
name: triage-ticket
description: 'Read a ticket, extract requirements, move it to In Progress, and start time tracking. Provider-agnostic via tkt.'
allowed-tools: [Bash, Read, Edit]
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
```

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
- Any blockers/unknowns identified
