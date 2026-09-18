---
name: deploy-ready
description: Pick up deploy_ready tickets, merge PR, watch staging, gate prod deploy via tkt.
trigger: manual
---

# Deploy Ready

Pick up tickets in `deploy_ready`, annotate QA lane times, merge the PR, watch staging, gate manual production deploy. Ticketing via `tkt`; Git host via `gh`.

## Steps

1. Find deploy_ready tickets:
   ```shell
   // turbo
   tkt list --query deploy_ready --json
   ```
1.5. After-hours hold (before any merge/staging step). Optional `[schedule]` config table;
   a missing table (exit 4) disables the check:
   ```shell
   // turbo
   AH_LABEL=$(tkt cfg schedule.after_hours_label 2>/dev/null) || AH_LABEL=""
   # also: schedule.business_hours (HH:MM-HH:MM; start > end = overnight window),
   # schedule.timezone (IANA), schedule.days (lowercase `date +%a` names)
   ```
   If the ticket's labels contain `$AH_LABEL` and the current time is inside the
   business-hours window: comment the ticket, echo `HOLD`, and stop before
   merge/staging — under `tkt run`, report outcome `gate`. A re-run outside the
   window proceeds normally from the top.
2. Annotate QA-lane times:
   ```shell
   // turbo
   for ROLE in qa_ready qa deploy_ready; do
     tkt lane-time "$KEY" --role "$ROLE" --json 2>/dev/null || true
   done
   ```
3. Find the PR:
   ```shell
   // turbo
   gh pr list --repo $(tkt cfg vcs.repo) --search "$KEY in:title,body" --state open
   ```
4. Merge:
   ```shell
   // turbo
   gh pr merge "$PR" --repo $(tkt cfg vcs.repo) --$(tkt cfg vcs.merge) --auto
   ```
   Poll until `MERGED`.
5. Watch staging workflow matched by merge commit SHA.
6. Comment and hand off to QE.
7. Present production deploy gate (manual).
8. Monitor production deploy once triggered.
9. Transition to `done` only if prod workflow truly deploys; otherwise leave in `deploy_ready`.

## Rules

- Repo/workflow names from `tkt cfg`.
- Production deploy is manual.
- `tkt lane-time` is a no-op when time tracking is disabled.
