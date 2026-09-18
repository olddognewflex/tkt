# /deploy-ready

Pick up tickets in `deploy_ready`, annotate QA lane times, merge the PR, watch the staging workflow, gate manual production deploy, and comment status. Ticketing via `tkt`; Git host via `gh`.

## Steps

1. Find deploy_ready tickets:
   ```shell
   tkt list --query deploy_ready --json
   ```
1.5. After-hours hold (before any merge/staging step). Optional `[schedule]` config
   table; `tkt schedule` owns the window maths (exit 2 = misconfigured: fail closed and hold):
   ```shell
   SCHED=$(tkt schedule --json)   # {"label": ..., "in_window": true|false, ...}
   ```
   Under `tkt run KEY` consider only KEY. During business hours (`in_window` true), drop tickets whose labels contain
   `label` and take the next eligible ticket; held tickets wait without blocking
   others. Only when every deploy_ready ticket is held: comment each, echo `HOLD`,
   and stop before merge/staging — under `tkt run`, report outcome `gate`. A
   re-run outside the window proceeds normally from the top.
2. Annotate QA-lane times retroactively:
   ```shell
   for ROLE in qa_ready qa deploy_ready; do
     tkt lane-time "$KEY" --role "$ROLE" --json 2>/dev/null || true
   done
   ```
3. Find the PR:
   ```shell
   gh pr list --repo $(tkt cfg vcs.repo) --search "$KEY in:title,body" --state open
   ```
4. Merge:
   ```shell
   gh pr merge "$PR" --repo $(tkt cfg vcs.repo) --$(tkt cfg vcs.merge) --auto
   ```
   Poll until `MERGED`.
5. Watch staging workflow matched by merge commit SHA.
6. Comment and hand off to QE.
7. Present production deploy gate (manual); if `tkt schedule` reports business hours and any deploy_ready ticket carries the after-hours label, warn to trigger it only outside the window.
8. Monitor production deploy once triggered.
9. Transition to `done` only if prod workflow truly deploys; otherwise leave in `deploy_ready`.

## Output

- Tickets transitioned (or left for human Done)
- Production run URL
- Any deploy friction

## Rules

- Repo/workflow names from `tkt cfg`.
- Production deploy is manual; never auto-deploy to prod.
- `tkt lane-time` is a no-op when time tracking is disabled.
