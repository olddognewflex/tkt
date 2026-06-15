---
mode: agent
description: 'Short-circuit flow for deliverable ticket types via tkt.'
tools: ['terminal', 'file']
---

# Complete Deliverable

For tickets whose `type_class` is `deliverable` (Task, Sub-task, Epic, Chore, Spike). Produces the artifact, comments the link, logs lane time, and transitions to `done`. Ticketing via `tkt`.

## Steps

1. Read ticket `description` + `acceptance`. Classify deliverable: config change, ADR, design doc, spike, demo, POC, runbook, other.
2. Produce the artifact. Config changes may open a tiny PR with one self-review pass (no QA loop).
3. Record lane time and transition:
   ```shell
   WL=$(tkt worklog "$KEY" --from-role in_progress --note "Deliverable shipped" --json)
   tkt comment "$KEY" "Deliverable complete: <summary>. Link: <url>. Time: $(echo "$WL" | jq -r .human)"
   tkt transition "$KEY" done
   ```

## Rules

- Don't guess ambiguous deliverables; comment and stop.
- Sub-tasks: complete the sub-task only, not the parent.
- Blocked deliverables: `tkt transition "$KEY" blocked` with a comment.
- All ticketing via `tkt`.
