# /open-pr

Commit staged changes, push the branch, open a PR, request reviewers, and move the ticket to `review`. Ticketing via `tkt`; Git host via `gh`.

## Steps

1. Stage and commit: `git add -A && git commit -m "<type>(<scope>): <description>"`.
2. Rebase on default branch.
3. Push: `git push -u origin HEAD`.
4. Create PR, capturing the URL `gh pr create` prints: `PR_URL=$(gh pr create --repo "$(tkt cfg vcs.repo)" --title "..." --body "...")`. Record the branch and the PR URL (`$PR_URL`, captured above) on the ticket: `tkt edit "$KEY" --add-branch "$BRANCH" --add-pr "$PR_URL"`, where `BRANCH=$(git symbolic-ref -q --short HEAD)`; on a detached HEAD (`BRANCH` empty) leave out `--add-branch` so the PR is still recorded. Non-fatal: exit 3 means the backend does not store branches/PRs; report any other failure.
5. Request reviewers from `tkt cfg vcs.reviewers --json`.
6. If `full_sdlc`: `tkt worklog` then `tkt transition "$KEY" review`.
7. If `deliverable`: comment and stay in `in_progress`.

## Rules

- Repo/reviewers from `tkt cfg`.
- Conventional Commits.
