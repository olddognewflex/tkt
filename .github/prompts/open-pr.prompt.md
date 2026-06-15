---
mode: agent
description: 'Commit, push, open a PR, request reviewers, and move the ticket to review via tkt.'
tools: ['terminal']
---

# Open PR

Commit staged changes, push the branch, open a PR, request reviewers, and move the ticket to `review`. Ticketing via `tkt`; Git host via `gh`.

## Steps

1. Stage and commit: `git add -A && git commit -m "<type>(<scope>): <description>"`.
2. Rebase on default branch: `git fetch origin && git rebase origin/$(tkt cfg vcs.default_branch)`.
3. Push: `git push -u origin HEAD`.
4. Create PR using `gh pr create --repo $(tkt cfg vcs.repo)`.
5. Request reviewers from `tkt cfg vcs.reviewers --json`.
6. If `type_class == full_sdlc`: annotate time with `tkt worklog "$KEY" --from-role in_progress --note "PR opened: $PR_URL"` then `tkt transition "$KEY" review`.
7. If `deliverable`: comment and stay in `in_progress`.

## Output

- PR URL + number
- Reviewer status

## Rules

- Repo/reviewers from `tkt cfg`.
- Conventional Commits.
- `tkt worklog` is a no-op when time tracking is disabled.
