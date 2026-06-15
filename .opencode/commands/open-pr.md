---
description: Commit, push, open a PR, request reviewers, and move the ticket to review via tkt
---

Open a PR for ticket: $ARGUMENTS

1. Stage and commit: `git add -A && git commit -m "<type>(<scope>): <description>"`.
2. Rebase on default branch: `git fetch origin && git rebase origin/$(tkt cfg vcs.default_branch)`.
3. Push: `git push -u origin HEAD`.
4. Create PR with `gh pr create --repo $(tkt cfg vcs.repo) --title "..." --body "..."`.
5. Request reviewers from `tkt cfg vcs.reviewers --json`.
6. If `type_class == full_sdlc`: annotate with `tkt worklog "$KEY" --from-role in_progress --note "PR opened: $PR_URL"` then `tkt transition "$KEY" review`.
7. If `deliverable`: comment and stay in `in_progress`.

Repo/reviewers from `tkt cfg`. Conventional Commits. `tkt worklog` is a no-op when time tracking is disabled.
