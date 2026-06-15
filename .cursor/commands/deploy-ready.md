# /deploy-ready

Pick up tickets in `deploy_ready`, annotate QA lane times, merge the PR, watch the staging workflow, gate manual production deploy, and comment status. Ticketing via `tkt`; Git host via `gh`.

## Steps

1. Find deploy_ready tickets:
   ```shell
   tkt list --query deploy_ready --json
   ```
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
7. Present production deploy gate (manual).
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
