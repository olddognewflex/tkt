---
name: complete-deliverable
description: 'Short-circuit flow for non-PR ticket types (Task, Sub-task, Epic, Chore, Spike). Produces the deliverable, comments with the artifact link, logs lane time, and transitions straight to Done. Provider-agnostic via tkt.'
---

# Complete Deliverable

For tickets whose `type_class` is `deliverable` — they produce something other
than merged feature code (config change, ADR/decision doc, design doc, demo/Loom,
POC, spike notes, runbook). Their lanes are just `todo → in_progress → done`
(+ `blocked`, `cancelled`). Ticketing via `tkt`.

## When the orchestrator routes here

`automated-sdlc` checks `tkt view "$KEY" --json | jq -r .type_class` after triage.
`deliverable` → this skill instead of Plan → Implement → PR → Review.

## Input

- Ticket key (already in `in_progress` from `triage-ticket`)

## Steps

### 1. Determine the deliverable

Read `description` + `acceptance`. Classify into: config change, ADR/decision,
design doc, spike/research, demo/video, POC, runbook, or other — each resolves to
an artifact URL. If the expected deliverable is ambiguous, comment a clarifying
question and stop. Don't guess.

### 2. Do the work

Execute it. A config change may still open a tiny PR (CI + one self-review pass,
**no QA loop**). Docs/spikes produce a published page or a file in the repo.

### 3. Record lane time and transition to Done

```shell
WL=$(tkt worklog "$KEY" --from-role in_progress --note "Deliverable shipped — <summary>" --json)
tkt comment "$KEY" "Deliverable complete: <summary>.
Link: <url>
Time in In Progress: $(echo "$WL" | jq -r .human) (worklog $(echo "$WL" | jq -r .worklog_id))."
tkt transition "$KEY" done
```

## Edge cases

- **Config change needing a PR** (workflow files, shared tsconfig, infra constants,
  the skill pack itself): open a small PR, CI runs, skip the QA loop, merge after a
  single self-review. Then comment + `tkt transition "$KEY" done`. Do not route to
  `qa_ready`. `open-pr` auto-detects the missing review lane (via `type_class`) and
  leaves the ticket in `in_progress` until this step.
- **Sub-task under a parent Story**: complete and mark the sub-task `done`. Do not
  transition the parent.
- **Spike with no concrete next step**: comment findings + a recommended follow-up
  ticket, mark `done`. The follow-up is a separate Story.
- **Blocked deliverable**: `tkt worklog "$KEY" --from-role in_progress` then
  `tkt transition "$KEY" blocked` with a blocker comment (see `automated-sdlc` →
  External blocker handling).

## Output

- Deliverable link
- Time spent
- Confirmation the Done transition succeeded
