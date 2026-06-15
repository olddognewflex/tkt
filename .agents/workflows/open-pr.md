---
name: open-pr
description: Commit, push, open a PR, request reviewers, and move the ticket to review via tkt.
trigger: manual
---

# Open PR

Commit staged changes, push the branch, open a PR, request reviewers, and move the ticket to `review`. Ticketing via `tkt`; Git host via `gh`.

## Steps

1. Stage and commit:
   ```shell
   // turbo
   git add -A && git commit -m "<type>(<scope>): <description>"
   ```
2. Rebase on default branch:
   ```shell
   // turbo
   git fetch origin && git rebase origin/$(tkt cfg vcs.default_branch)
   ```
3. Push:
   ```shell
   // turbo
   git push -u origin HEAD
   ```
4. Create PR:
   ```shell
   // turbo
   REPO=$(tkt cfg vcs.repo)
   gh pr create --repo "$REPO" --title "..." --body "..."
   ```
5. Request reviewers from `tkt cfg vcs.reviewers --json`.
6. If `type_class == full_sdlc`: annotate with `tkt worklog` then `tkt transition "$KEY" review`.
7. If `deliverable`: comment and stay in `in_progress`.

## Rules

- Repo/reviewers from `tkt cfg`.
- Conventional Commits.
- `tkt worklog` is a no-op when time tracking is disabled.
