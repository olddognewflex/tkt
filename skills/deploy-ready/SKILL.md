---
name: deploy-ready
description: 'Pick up tickets in the deploy_ready lane: annotate QA lane times, merge the PR, watch the staging workflow for the merge commit, gate manual production deploy, comment status. Provider-agnostic ticketing via tkt; VCS via gh.'
disable-model-invocation: true
model_tier: standard
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

When invoked for one specific ticket (for example P10 under `tkt run KEY`), set
`RUN_KEY` to it so nothing else can be picked; a held `RUN_KEY` then ends in `HOLD`
rather than a different ticket being deployed inside its run.

```shell
tkt list --query deploy_ready --json > /tmp/tkt_deploy_ready.json   # define [queries].deploy_ready in config
if [ -n "${RUN_KEY:-}" ]; then
  jq --arg k "$RUN_KEY" '[.[] | select(.key == $k)]' /tmp/tkt_deploy_ready.json > /tmp/tkt_deploy_ready.tmp \
    && mv /tmp/tkt_deploy_ready.tmp /tmp/tkt_deploy_ready.json
  if [ "$(jq 'length' /tmp/tkt_deploy_ready.json)" -eq 0 ]; then
    echo "NOTHING: $RUN_KEY is not in deploy_ready; nothing to deploy for this run."
  fi
fi
```

Step 1.5 then drops held tickets, and you take the highest-priority + oldest of
what remains. One at a time.

### 1.5 After-hours hold (before any merge/staging step)

Optional, driven by the `[schedule]` config table. A ticket carrying the
after-hours label must not enter the deploy pipeline during business hours. The
hold sits **here**, before merge and staging, so the skill stays re-runnable from
the top and nothing ships to an auto-deploying default branch inside the window.

`tkt schedule --json` owns the window maths (timezone, days, overnight windows),
so every harness shell gets the same answer. It exits 2 when `[schedule]` is
misconfigured; the snippet then fails closed and holds labeled tickets, loudly.

```shell
if SCHED=$(tkt schedule --json); then
  AH_LABEL=$(echo "$SCHED" | jq -r '.label // ""')
  IN_WINDOW=$(echo "$SCHED" | jq -r '.in_window')
else
  echo "WARNING: [schedule] is misconfigured (see above); holding after-hours tickets" >&2
  AH_LABEL=$(tkt cfg schedule.after_hours_label 2>/dev/null) || AH_LABEL=""
  IN_WINDOW=true
fi

if [ -n "$AH_LABEL" ] && [ "$IN_WINDOW" = "true" ]; then
  HELD=$(jq -r --arg l "$AH_LABEL" \
    '[.[] | select((.labels // []) | index($l)) | .key] | join(" ")' /tmp/tkt_deploy_ready.json)
  jq --arg l "$AH_LABEL" '[.[] | select((.labels // []) | index($l) | not)]' \
    /tmp/tkt_deploy_ready.json > /tmp/tkt_deploy_eligible.json
else
  HELD=""
  cp /tmp/tkt_deploy_ready.json /tmp/tkt_deploy_eligible.json
fi
if [ -n "$HELD" ]; then echo "HELD (after-hours, in business hours): $HELD"; fi
if [ "$(jq 'length' /tmp/tkt_deploy_eligible.json)" -eq 0 ] && [ -n "$HELD" ]; then
  # One key per line: zsh does not word-split an unquoted "$HELD" in `for`.
  jq -r --arg l "$AH_LABEL" '.[] | select((.labels // []) | index($l)) | .key' \
    /tmp/tkt_deploy_ready.json | while read -r H; do
    tkt comment "$H" "After-hours ticket: deploy held during business hours. Re-run deploy-ready outside the window."
  done
  echo "HOLD: no eligible ticket; the rest are after-hours ($HELD). Stopping before merge/staging."
fi
```

Pick `KEY` from `/tmp/tkt_deploy_eligible.json` and continue at Step 2. A held
ticket simply waits: it does not block the unlabeled tickets behind it.

On `HOLD`, **stop the skill here**: do not merge, do not watch staging. Under
`tkt run`, report outcome `gate` (human gate reached, stop the run). A human or
scheduler re-runs this skill outside the window and it proceeds normally from
the top. Held tickets are commented only when nothing else can proceed; a repeat
run inside the window comments again, which is harmless, and there is no
comment-read verb to dedupe with. No `[schedule]`, or outside the window, means
nothing is held.

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

Production deploy is **manual**, and it ships everything merged since the last
release, not just `$KEY`. An after-hours ticket merged out of hours waits in
`deploy_ready` until prod confirms it, so during business hours warn whenever
one is there. That can over-warn for a held, still-unmerged ticket, which is the
safe side for a manual gate, and it needs no commit-message key convention.

```shell
if SCHED=$(tkt schedule --json); then
  AH_LABEL=$(echo "$SCHED" | jq -r '.label // ""'); IN_WINDOW=$(echo "$SCHED" | jq -r '.in_window')
else
  echo "WARNING: [schedule] is misconfigured (see above); treating now as business hours" >&2
  AH_LABEL=$(tkt cfg schedule.after_hours_label 2>/dev/null) || AH_LABEL=""; IN_WINDOW=true
fi
PENDING_AH=""
if [ -n "$AH_LABEL" ] && [ "$IN_WINDOW" = "true" ]; then
  if DR=$(tkt list --query deploy_ready --json); then
    PENDING_AH=$(echo "$DR" | jq -r --arg l "$AH_LABEL" \
      '[.[] | select((.labels // []) | index($l)) | .key] | join(" ")')
  else
    PENDING_AH="unknown, the list failed"    # fail closed: warn
  fi
fi
if [ -n "$PENDING_AH" ]; then
  echo "WAIT: after-hours tickets are in deploy_ready ($PENDING_AH). If any is merged, this prod deploy ships it; trigger only outside business hours."
fi
```

Then present the gate:

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
# echo "$SHIPPED" | while read -r K; do [ -n "$K" ] || continue; tkt transition "$K" done; tkt edit "$K" --agent-status done; done
```

If it's a build/auth stub → comment and leave in `deploy_ready` for human Done:

```shell
echo "$SHIPPED" | while read -r K; do    # zsh does not word-split "$SHIPPED"
  [ -n "$K" ] || continue                  # empty SHIPPED still yields one empty line
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
