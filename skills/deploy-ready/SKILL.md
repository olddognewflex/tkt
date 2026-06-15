---
name: deploy-ready
description: 'Pick up tickets in the deploy_ready lane: annotate QA lane times, merge the PR, watch the staging workflow for the merge commit, gate manual production deploy, comment status. Provider-agnostic ticketing via tkt; VCS via gh.'
disable-model-invocation: true
---

# Deploy Ready

Resumes the SDLC once a human moves a ticket through `qa_ready → qa → deploy_ready`.
Ticketing via `tkt`; Git host via `gh`. Repo + workflow names from
`.sdlc/config.toml`.

```shell
REPO=$(tkt cfg vcs.repo)
DEFAULT_BRANCH=$(tkt cfg vcs.default_branch)
STAGING_WF=$(tkt cfg deploy.staging_workflow)
PROD_WF=$(tkt cfg deploy.production_workflow)
```

> ⚠️ **Deploy-contract note (project-specific):** whether a green workflow run means
> "code is live" depends on your pipeline. If your deploy workflow only builds/auths
> (stub), do **not** auto-transition tickets to `done` — leave them in `deploy_ready`
> for a human to confirm prod traffic. Set this expectation per project.

## Steps

### 1. Identify deploy_ready tickets

```shell
tkt list --query deploy_ready --json   # define [queries].deploy_ready in config
```

If multiple, take highest-priority + oldest. One at a time.

### 2. Annotate QA-lane times (retroactive, per lane)

For each already-exited QA lane, record entry→exit with `tkt lane-time` (which,
unlike `tkt worklog`, measures a closed interval rather than entry→now):

```shell
for ROLE in qa_ready qa deploy_ready; do
  tkt lane-time "$KEY" --role "$ROLE" --json 2>/dev/null || true
done
```

Then a summary comment (worklogs are canonical; comment is for skimmers):

```shell
tkt comment "$KEY" "QA lanes recorded (qa_ready / qa / deploy_ready). Proceeding to merge + staging."
```

### 3. Find the PR

```shell
gh pr list --repo "$REPO" --search "$KEY in:title,body" --state open \
  --json number,headRefName,mergeable,reviewDecision
```

Abort with a comment if no PR or not approved.

### 4. Merge

`--auto` returns when *enqueued*, not landed. Poll for `MERGED` before reading the
merge SHA:

```shell
gh pr merge "$PR" --repo "$REPO" --$(tkt cfg vcs.merge) --auto
for _ in $(seq 1 60); do
  STATE=$(gh pr view "$PR" --repo "$REPO" --json state --jq .state)
  [ "$STATE" = "MERGED" ] && break; sleep 10
done
[ "$STATE" != "MERGED" ] && { echo "PR did not merge within 10 min"; exit 1; }
```

### 5. Watch the staging workflow (match by merge commit)

Don't take the latest run — match the merge `headSha`:

```shell
MERGE_SHA=$(gh pr view "$PR" --repo "$REPO" --json mergeCommit --jq '.mergeCommit.oid')
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

On failure: read logs, fix forward if small, else escalate. Annotate the ticket.

### 6. Hand off to QE (if applicable) + comment

```shell
tkt comment "$KEY" "Staging workflow succeeded for $MERGE_SHA. Handing off to QE for smoke/regression (out-of-band)."
```

### 7. Present the production deploy gate

Production deploy is **manual**:

```shell
echo "Staging done. Run manually: gh workflow run $PROD_WF --repo $REPO --ref $DEFAULT_BRANCH [--field version=<tag>]"
tkt comment "$KEY" "Staging succeeded for $MERGE_SHA. Production deploy ready — manual human trigger required."
```

### 8. Monitor production deploy (once triggered)

```shell
gh run watch <prod-run-id> --repo "$REPO" --exit-status
```

### 9. Identify shipped tickets — transition per the deploy contract

```shell
git fetch origin "$DEFAULT_BRANCH" --tags
SHIPPED=$(git log <prev-prod-tag>..origin/"$DEFAULT_BRANCH" --format='%s %b' \
  | grep -oE '[A-Z]+-[0-9]+' | sort -u)
```

If your prod workflow truly deploys → transition each to `done`:

```shell
# for K in $SHIPPED; do tkt transition "$K" done; done
```

If it's a build/auth stub → comment and leave in `deploy_ready` for human Done:

```shell
for K in $SHIPPED; do
  tkt comment "$K" "Production workflow completed. Verify prod traffic, then transition to Done."
done
```

### 10. Post-deploy notes (only if notable)

Comment only on friction (CD retry, staging fix, runner outage, time ≫ median):

```shell
tkt comment "$KEY" "Deploy notes: <what slowed it down> | extra time: <Xh Ym>"
```

## Output

- Tickets transitioned (or left for human Done)
- Production run URL
- Any deploy friction
