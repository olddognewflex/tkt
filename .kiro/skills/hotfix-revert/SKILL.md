---
name: hotfix-revert
description: 'Revert a bad change in production, create a highest-priority hotfix ticket, and fast-track through PR → staging → prod with a single human signoff. Skips most automation gates. Provider-agnostic ticketing via tkt.'
disable-model-invocation: true
---

# Hotfix Revert

Fast lane for production issues. Reverts a problematic change, opens a hotfix
ticket at highest priority, runs an accelerated pipeline. Ticketing via `tkt`
(incl. `tkt create` / `tkt link`); Git host via `gh`.

```shell
REPO=$(tkt cfg vcs.repo)
DEFAULT_BRANCH=$(tkt cfg vcs.default_branch)
STAGING_WF=$(tkt cfg deploy.staging_workflow)
PROD_WF=$(tkt cfg deploy.production_workflow)
```

## When to use

- A prod deploy caused a regression and the bad commit/PR is known
- A merged PR must be backed out before the next planned deploy

## Skips vs. standard SDLC

Ticket selection (created here), plan/self-review loops (one pass), QA gate
(skipped), multi-cycle review (one signoff), QE staging (out-of-band).

## Steps

### 1. Identify the change to revert

Required input: a **merged PR number**. (Commit SHA / ticket key → resolve to a PR
first with `gh pr list --repo "$REPO" --search "<x>"`.)

```shell
TARGET_SHA=$(gh pr view <n> --repo "$REPO" --json mergeCommit --jq '.mergeCommit.oid')
TARGET_TICKET=$(gh pr view <n> --repo "$REPO" --json body --jq '.body' | grep -oE '[A-Z]+-[0-9]+' | head -1)
```

### 2. Create the hotfix ticket

```shell
HOTFIX_KEY=$(tkt create --type Bug \
  --summary "HOTFIX: revert <original-summary> (reverts $TARGET_TICKET)" \
  --priority Highest --assignee "$(tkt whoami)" \
  --body "Hotfix for $TARGET_TICKET. Reverting $TARGET_SHA. Fast-track: single signoff, skip QA.")

# Link hotfix → original. "Fixes" is the outward description (HOTFIX Fixes TARGET).
tkt link "$HOTFIX_KEY" --to "$TARGET_TICKET" --type "Fixes"
tkt transition "$HOTFIX_KEY" in_progress
```

### 3. Create the revert branch

For squash-merged history use `git revert <sha>` (no `-m`; `-m 1` is only for true
two-parent merge commits):

```shell
SLUG="revert-${TARGET_SHA:0:8}"
git fetch origin
git checkout -b "$(tkt cfg vcs.hotfix_fmt --ticket "$HOTFIX_KEY" --slug "$SLUG")" "origin/$DEFAULT_BRANCH"
git revert --no-edit "$TARGET_SHA"
```

Conflicts → resolve minimally, no unrelated changes.

### 4. Single self-review pass

Invoke `self-review` **once** (no loop). The revert is mechanical.

### 5. Open the hotfix PR

```shell
git push -u origin HEAD
HOTFIX_URL=$(tkt view "$HOTFIX_KEY" --json | jq -r .url)
gh pr create --repo "$REPO" --title "hotfix($HOTFIX_KEY): revert $TARGET_TICKET" --label hotfix \
  --body "## Hotfix
Reverts $TARGET_SHA (from $TARGET_TICKET). Ticket: $HOTFIX_URL
Production incident — bypassing standard QA per hotfix policy. Single signoff."
tkt transition "$HOTFIX_KEY" review
```

### 6. Request human signoff (skip the bot loop)

```shell
gh pr edit <pr> --repo "$REPO" --add-reviewer <oncall-handle>
```

Wait for **one** approval. Don't loop on feedback — if significant changes are
requested, abort hotfix and escalate.

### 7. Monitor CI (required checks only)

```shell
gh pr checks <pr> --repo "$REPO" --watch --required
```

Required check fails → fix forward minimally or abort.

### 8. Merge

```shell
gh pr merge <pr> --repo "$REPO" --$(tkt cfg vcs.merge) --auto
for _ in $(seq 1 60); do
  STATE=$(gh pr view <pr> --repo "$REPO" --json state --jq .state)
  [ "$STATE" = "MERGED" ] && break; sleep 10
done
[ "$STATE" != "MERGED" ] && { echo "hotfix PR did not merge within 10 min"; exit 1; }
```

### 9. Watch staging (match merge commit)

```shell
MERGE_SHA=$(gh pr view <pr> --repo "$REPO" --json mergeCommit --jq '.mergeCommit.oid')
RUN_ID=""
for _ in $(seq 1 30); do
  RUN_ID=$(gh run list --workflow="$STAGING_WF" --repo "$REPO" --branch="$DEFAULT_BRANCH" \
    --limit=10 --json databaseId,headSha \
    | jq -r --arg sha "$MERGE_SHA" '.[] | select(.headSha==$sha) | .databaseId' | head -1)
  [ -n "$RUN_ID" ] && break; sleep 10
done
[ -z "$RUN_ID" ] && { echo "no staging run for $MERGE_SHA"; exit 1; }
gh run watch "$RUN_ID" --repo "$REPO" --exit-status
```

Staging fails → comment on the ticket, escalate, do not proceed to prod.

### 10. Production deploy (single signoff)

```shell
echo "Staging green. Run: gh workflow run $PROD_WF --repo $REPO --ref $DEFAULT_BRANCH --field version=hotfix-$HOTFIX_KEY"
# After the human triggers it:
gh run watch <prod-run-id> --repo "$REPO" --exit-status
```

### 11. Comment on both tickets — transition per deploy contract

```shell
WL=$(tkt worklog "$HOTFIX_KEY" --from-role in_progress --note "Hotfix run complete — awaiting verification" --json)
tkt comment "$HOTFIX_KEY" "Production workflow completed. Verify the revert serves prod traffic, then transition to Done. \
Agent lane time: $(echo "$WL" | jq -r .human) (worklog $(echo "$WL" | jq -r .worklog_id))."
tkt comment "$TARGET_TICKET" "Reverted by $HOTFIX_KEY due to a production regression. Redo the work with the regression addressed before re-attempting."
# If your prod workflow truly deploys: tkt transition "$HOTFIX_KEY" done
```

## Output

- Hotfix ticket key, PR URL, prod deploy URL, total elapsed time
