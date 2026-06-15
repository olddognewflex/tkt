# /deploy-ready

Pick up tickets in `deploy_ready`, annotate QA lane times, merge the PR, watch staging, gate manual production deploy. Ticketing via `tkt`; Git host via `gh`.

## Steps

1. Find deploy_ready tickets: `tkt list --query deploy_ready --json`.
2. Annotate QA-lane times: `tkt lane-time "$KEY" --role qa_ready`, `qa`, `deploy_ready`.
3. Find the PR: `gh pr list --repo $(tkt cfg vcs.repo) --search "$KEY in:title,body"`.
4. Merge and poll until `MERGED`.
5. Watch staging workflow matched by merge commit SHA.
6. Comment and hand off to QE.
7. Present production deploy gate (manual).
8. Monitor production deploy once triggered.
9. Transition to `done` only if prod workflow truly deploys.

## Rules

- Repo/workflow names from `tkt cfg`.
- Production deploy is manual.
- `tkt lane-time` is a no-op when time tracking is disabled.
