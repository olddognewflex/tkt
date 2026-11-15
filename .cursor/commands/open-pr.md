# /open-pr

Commit staged changes, push the branch, open a PR, request reviewers, and move the ticket to `review`. Ticketing via `tkt`; Git host via `gh`.

## Steps

1. Stage and commit:
   ```shell
   git add -A
   git commit -m "<type>(<scope>): <description>"
   ```
2. Rebase on default branch:
   ```shell
   git fetch origin
   git rebase origin/$(tkt cfg vcs.default_branch)
   ```
3. Push:
   ```shell
   git push -u origin HEAD
   ```
4. Create PR:
   ```shell
   REPO=$(tkt cfg vcs.repo)
   gh pr create --repo "$REPO" --title "..." --body "..."
   ```
   Record the branch and the PR URL (`$PR_URL`, printed by `gh pr create`) on the ticket: `tkt edit "$KEY" --add-branch "$BRANCH" --add-pr "$PR_URL"`, where `BRANCH=$(git symbolic-ref -q --short HEAD)`; on a detached HEAD (`BRANCH` empty) leave out `--add-branch` so the PR is still recorded. Non-fatal: exit 3 means the backend does not store branches/PRs; report any other failure.
5. Request reviewers from `tkt cfg vcs.reviewers --json`.
6. Transition based on `type_class`:
   - `full_sdlc`: `tkt worklog "$KEY" --from-role in_progress --note "PR opened: $PR_URL"` then `tkt transition "$KEY" review`
   - `deliverable`: comment and stay in `in_progress`

## Output

- PR URL + number
- Reviewer status

## Rules

- Repo/reviewers from `tkt cfg`.
- Conventional Commits.
- `tkt worklog` is a no-op when time tracking is disabled.
