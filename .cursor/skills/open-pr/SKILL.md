---
name: open-pr
description: 'Commit changes, push the branch, open a PR, request reviewers, and transition the ticket to the review lane. Provider-agnostic via tkt; VCS via gh. Invoke as slash command, not automatically.'
disable-model-invocation: true
---

# Open PR

Commit staged changes, push the branch, open a pull request, and move the ticket
to its `review` lane. Ticketing via `tkt`; Git host via `gh`. Repo-specific values
from `.sdlc/config.toml` via `tkt cfg`.

## Steps

1. Stage and commit: `git add -A && git commit -m "<type>(<scope>): <description>"`.
2. Rebase on default branch: `git fetch origin && git rebase origin/$(tkt cfg vcs.default_branch)`.
3. Push: `git push -u origin HEAD`.
4. Create PR with `gh pr create --repo $(tkt cfg vcs.repo)`.
5. Request reviewers from `tkt cfg vcs.reviewers --json`.
6. If `type_class == full_sdlc`: annotate with `tkt worklog` then `tkt transition "$KEY" review`.
7. If `deliverable`: comment and stay in `in_progress`.

## Output

- PR URL + number
- Reviewer status

## Rules

- Repo/reviewers from `tkt cfg`.
- Conventional Commits.
- `tkt worklog` is a no-op when time tracking is disabled.
