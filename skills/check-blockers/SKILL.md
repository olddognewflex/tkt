---
name: check-blockers
description: 'Review tickets in the blocked lane, classify each blocker, and recommend unblock actions. Ad-hoc or standup-driven. Provider-agnostic via tkt.'
model: claude-haiku-4-5
compatibility:
  - claude
  - opencode
---

# Check Blockers

Scans blocked tickets, summarizes why each is stuck, recommends next steps. All
ticketing via `tkt`.

## When to use

- Standup prep — a "blocked items" rollup
- Ad-hoc triage of stuck work
- After a dependency is reported unblocked

## Scope

Default to the current user's blocked tickets. Define the queries in config:

```toml
[queries]
blocked      = '... status = blocked AND assignee = currentUser() ...'   # --mine
blocked_team = '... status = blocked ...'                                 # --team
```

## Steps

### 1. Query blocked tickets

```shell
tkt list --query blocked --json        # or blocked_team for --team
```

### 2. Classify each blocker

Read recent comments + links (`tkt view "$K" --json`) to determine type:

| Signal | Type | Action |
| --- | --- | --- |
| unresolved `blocked_by` link → open ticket | internal dep | check blocker status; ping owner |
| "waiting on <team>" / external system | external dep | verify in their channel; direct ask |
| "design needed" / "spec missing" | spec gap | schedule a design sync |
| "credentials" / "access" / "token" | access gap | IT/security request |
| no update > 7 days | stale | ping blocker owner |
| no blocker comment | undocumented | add a blocker comment first |

### 3. For internal-dep blockers, check the blocker recursively

```shell
for B in $(tkt blockers "$K" --json | jq -r '.[].key'); do
  BSTATE=$(tkt view "$B" --json | jq -r .status_role)
  echo "$K blocked by $B (status: $BSTATE)"
done
```

- blocker is `done`/`deploy_ready` → recommend **unblock now** (move `$K` to `todo`/`in_progress`)
- blocker is `in_progress`/`review` → ETA-ping its assignee
- blocker is itself `blocked` → flag chain blocker, recommend escalation

### 4. Build the report

```
<KEY> | <priority> | <summary>
  Blocker: <type>
  Detail: <one line>
  Recommended next step: <action>
```

Group by recommended action for batch responses.

### 5. Auto-unblock candidates (confirm before transitioning)

List tickets whose blocker is verifiably resolved. Do **not** transition
automatically:

```
Auto-unblock candidates (confirm first):
  <KEY> — blocked by <B> which is now Done. Suggest: tkt transition <KEY> in_progress
```

### 6. Optional nudge on stale blockers (after human confirms)

```shell
tkt comment "$K" "Blocker check: blocked <N> days. Blocker <B> status: <status>. Next: <action>."
```

## Output

A markdown report grouped by action type (ready to unblock / needs ping / external
/ stale).

## Scheduling

```
/schedule weekday 9am /check-blockers --mine
```
