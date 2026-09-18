# /deploy-ready

Pick up tickets in `deploy_ready`, annotate QA lane times, merge the PR, watch staging, gate manual production deploy. Ticketing via `tkt`; Git host via `gh`.

## Steps

1. Find deploy_ready tickets: `tkt list --query deploy_ready --json`.
1.5. After-hours hold: `tkt schedule --json` reports `label` and `in_window` (exit 2 = misconfigured: fail closed and hold). Under `tkt run KEY` consider only KEY. During business hours drop tickets carrying the label and take the next eligible ticket; held tickets wait without blocking others. Only when every deploy_ready ticket is held: comment each, echo `HOLD`, stop before merge/staging (under `tkt run`, outcome `gate`). Re-run outside the window proceeds normally.
2. Annotate QA-lane times: `tkt lane-time "$KEY" --role qa_ready`, `qa`, `deploy_ready`.
3. Find the PR: `gh pr list --repo $(tkt cfg vcs.repo) --search "$KEY in:title,body"`.
4. Merge and poll until `MERGED`.
5. Watch staging workflow matched by merge commit SHA.
6. Comment and hand off to QE.
7. Present production deploy gate (manual); if `tkt schedule` reports business hours and any deploy_ready ticket carries the after-hours label, warn to trigger it only outside the window.
8. Monitor production deploy once triggered.
9. Transition to `done` only if prod workflow truly deploys.

## Rules

- Repo/workflow names from `tkt cfg`.
- Production deploy is manual.
- `tkt lane-time` is a no-op when time tracking is disabled.
