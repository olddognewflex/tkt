---
name: resume-from-revise
description: 'Resume the SDLC after a human dev fixed a revise-state ticket. Annotates revise time, re-runs CI/review automation, promotes back to qa_ready. Provider-agnostic via tkt.'
---

# Resume From Revise

Picks up tickets that landed in `revise` after a failed QA round and a human dev
fix. Re-enters the automation loop. Ticketing via `tkt`; build via `tkt cfg`.

Invoke as `/resume-from-revise <KEY>` or `/resume-from-revise` (infer from branch).

## Steps

### 1. Resolve the ticket

If no arg, infer from branch (`feature/<key-lower>-...` → `<KEY>`).

```shell
tkt view "$KEY" --json | jq -r .status_role   # must be "revise"
```

If not `revise`, stop and ask.

### 2. Annotate revise time

```shell
WL=$(tkt worklog "$KEY" --from-role revise --note "Resuming from revise — human fix complete" --json)
tkt comment "$KEY" "Resuming from revise — fix complete. Time in Revise: \
$(echo "$WL" | jq -r .human) (worklog $(echo "$WL" | jq -r .worklog_id))."
```

### 3. Transition back to in_progress

```shell
tkt transition "$KEY" in_progress
```

The agent owns the work again while it pushes/re-reviews.

### 4. Verify local state (config toolchain)

```shell
git status
eval "$(tkt cfg build.build --pkg "<pkg>")"
eval "$(tkt cfg build.test --pkg "<pkg>")"
```

If tests fail: stop and surface. Do not push broken code.

### 5. Push

```shell
git push
```

### 6. Re-run automation subset

In sequence: `ci-fix` → `respond-to-review` → `deploy-preview`. Each annotates its
own added time via `tkt worklog` on exit.

### 7. Promote back to qa_ready

Verify approval first (same canonical criterion as `respond-to-review`):

```shell
REPO=$(tkt cfg vcs.repo)
DECISION=$(gh pr view "$PR" --repo "$REPO" --json reviewDecision --jq .reviewDecision)
[ "$DECISION" != "APPROVED" ] && { echo "PR not approved (decision=${DECISION:-pending})"; exit 1; }

# Annotate the in_progress portion of this revise cycle.
WL1=$(tkt worklog "$KEY" --from-role in_progress --note "Revise cycle — in_progress portion" --json)

# respond-to-review may already have moved it to review; only transition if not.
CUR=$(tkt view "$KEY" --json | jq -r .status_role)
[ "$CUR" != "review" ] && tkt transition "$KEY" review
WL2=$(tkt worklog "$KEY" --from-role review --note "Revise cycle — re-review portion" --json)

tkt transition "$KEY" qa_ready
tkt comment "$KEY" "Re-review complete and approved. Back in qa_ready — preview updated: <url>. \
In Progress (revise): $(echo "$WL1" | jq -r .human) (worklog $(echo "$WL1" | jq -r .worklog_id)) | \
review (re-review): $(echo "$WL2" | jq -r .human) (worklog $(echo "$WL2" | jq -r .worklog_id))."
```

QA picks it back up.

## Output

- PR URL, preview URL
- Total time added by the revise cycle
