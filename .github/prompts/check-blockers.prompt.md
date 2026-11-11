---
mode: agent
description: 'Review blocked tickets, classify blockers, and recommend unblock actions via tkt.'
tools: ['terminal']
---

# Check Blockers

Scans blocked tickets, summarizes why each is stuck, recommends next steps. All ticketing via `tkt`.

## Steps

1. Query blocked tickets: `tkt list --query blocked --json` (or `blocked_team` for `--team`).
2. Classify each blocker from recent comments + links (`tkt view "$K" --json`):
   - internal dep, external dep, spec gap, access gap, stale, undocumented
3. For internal deps, check blocker status recursively with `tkt blockers` + `tkt view`.
4. Build a report grouped by recommended action.
5. List auto-unblock candidates but do **not** transition without confirmation.
6. Optionally nudge stale blockers with `tkt comment`.
7. Optional, when `[queries].containers` is defined (`tkt cfg queries.containers` exits 4 when not): list it, and for each `container` run `tkt children KEY --json`; report those whose children are all `closed` as ready to close. A human closes them; never transition.

## Output

Markdown report grouped by action type: ready to unblock / needs ping / external / stale / containers ready to close.

## Rules

- Default scope: current user's blocked tickets.
- Never auto-transition.
- All ticketing via `tkt`.
