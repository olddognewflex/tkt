---
description: Pick up deploy_ready tickets, merge PR, watch staging, gate prod deploy via tkt
---

Deploy the ticket: $ARGUMENTS

1. Find deploy_ready tickets: `tkt list --query deploy_ready --json`.
2. Annotate QA-lane times: `tkt lane-time "$KEY" --role qa_ready`, `qa`, `deploy_ready`.
3. Find the PR: `gh pr list --repo $(tkt cfg vcs.repo) --search "$KEY in:title,body"`.
4. Merge: `gh pr merge "$PR" --repo $(tkt cfg vcs.repo) --$(tkt cfg vcs.merge) --auto`. Poll until `MERGED`.
5. Watch staging workflow matched by merge commit SHA.
6. Comment and hand off to QE.
7. Present production deploy gate (manual).
8. Monitor production deploy once triggered.
9. Transition to `done` only if prod workflow truly deploys; otherwise leave in `deploy_ready`.

Repo/workflow names from `tkt cfg`. Production deploy is manual. `tkt lane-time` is a no-op when time tracking is disabled.
