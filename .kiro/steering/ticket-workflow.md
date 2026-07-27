---
inclusion: manual
---

# Ticket Workflow Reference

Detailed reference for ticket lifecycle, type routing, lane-time annotation, and
transition ownership. Include this context (`#ticket-workflow`) when working on
tickets through the SDLC pipeline.

## Board Flow

```
backlog → todo → in_progress → review → qa_ready → qa → deploy_ready → done
```

Side roles (can be entered from multiple points):
- `blocked` — external dependency or unclear spec
- `revise` — QA failure or reviewer changes-requested
- `cancelled` — ticket abandoned

## Type-Class Routing

Check a ticket's `type_class` to determine the correct flow:

```shell
CLASS=$(tkt view "$KEY" --json | jq -r .type_class)
```

### `full_sdlc` (Story, Bug)

Full pipeline:
1. `select-ticket` — pick from tiered queries
2. `triage-ticket` — read requirements, transition `todo → in_progress`
3. `plan-ticket` — structured implementation plan
4. Implement + test (using `tkt cfg build.*` commands)
5. `self-review` — adversarial pre-PR review loop
6. `open-pr` — push, open PR, request reviewers, transition `in_progress → review`
7. `ci-fix` — watch CI, fix failures, loop until green
8. `respond-to-review` — address reviewer comments, loop until approved
9. `deploy-preview` — confirm preview URL, comment on ticket
10. Promote to `qa_ready` (human QA gate — agent stops here)
11. `deploy-ready` — merge, watch staging, gate production

### `deliverable` (Task, Sub-task, Chore, Epic, Spike)

Short-circuit:
1. `select-ticket` / `triage-ticket` (same as above)
2. Produce the deliverable (artifact, document, config, etc.)
3. `complete-deliverable` — comment artifact link, transition straight to `done`

No PR, no review loop, no deploy pipeline.

## Lane-Time Annotation

Every agent-driven transition out of a lane records elapsed time:

```shell
# Log time and get human-readable result
WL=$(tkt worklog "$KEY" --from-role in_progress --note "PR opened" --json)

# Post comment with the time
tkt comment "$KEY" "PR opened. Time in In Progress: \
$(echo "$WL" | jq -r .human) (worklog $(echo "$WL" | jq -r .worklog_id))."
```

### When to annotate

| Transition | Note context |
|------------|--------------|
| `in_progress → review` | "PR opened" |
| `review → qa_ready` | "Approved — promoting to QA" |
| `deploy_ready → done` | "Deployed to production" |
| `revise → in_progress` | "Revise fixes applied" |
| `in_progress → blocked` | "Blocked — \<reason\>" |

### No-op behavior

When `[timetracking].provider = "none"`, `tkt worklog` returns:
```json
{"human": "(no time tracking)", "worklog_id": "", "seconds": 0}
```

Comments should still read naturally — embed the `.human` value as-is.

## Transition Ownership

Transitions are gated by ownership declared in `.sdlc/config.toml`:

| Transition | Default owner |
|------------|---------------|
| `backlog → todo` | Human (refinement) |
| `todo → in_progress` | **Agent** (after select + triage) |
| `in_progress → review` | **Agent** (on PR open) |
| `review → revise` | Reviewer or `respond-to-review` |
| `revise → in_progress` | **Agent** (`resume-from-revise`) |
| `review → qa_ready` | **Agent** (promotion gate) |
| `qa_ready → qa` | **Human** (QA begins testing) |
| `qa → revise` / `qa → deploy_ready` | **Human** (QA verdict) |
| `deploy_ready → done` | **Human** (deploy confirmation) |

The agent must never perform a human-gated transition. Check `[board.ownership]`
in config if unsure.

## Blocker Handling

```shell
# Check for unresolved blockers
BLOCKERS=$(tkt blockers "$KEY" --json)
COUNT=$(echo "$BLOCKERS" | jq 'length')

# If blocked, transition and comment
if [ "$COUNT" -gt 0 ]; then
  WL=$(tkt worklog "$KEY" --from-role in_progress --note "Blocked" --json)
  tkt transition "$KEY" blocked
  tkt comment "$KEY" "BLOCKED: <description>. \
Waiting on: <who>. Need: <what unblocks>. \
Time in In Progress: $(echo "$WL" | jq -r .human)."
fi
```

Run `check-blockers` periodically to classify and recommend unblock actions.

## Decision Points

| Condition | Action |
|-----------|--------|
| Tests fail after 3 fix attempts | Stop, transition to `blocked`, request human help |
| CI flake (infra, not code) | `gh run rerun <id> --failed`; don't count against budget |
| Reviewer requests changes | Loop via `respond-to-review` until approved |
| Merge conflicts | Rebase on default branch, resolve, re-push |
| Ticket unclear / spec gap | Transition to `blocked` with clarifying comment |
| Scope > 400 LOC | Stop, recommend splitting via `plan-ticket` |

## Abort Conditions

Stop and comment on the ticket when:
- 3+ non-converging review cycles
- Infrastructure CI failure (not code-related)
- Scope exceeds ~400 lines (split needed)
- External blocker unresolved > 24h
