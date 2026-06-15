---
name: open-pr
description: 'Commit changes, push the branch, open a PR, request reviewers, and transition the ticket to the review lane with lane-time annotation. Provider-agnostic ticketing via tkt; VCS via gh.'
compatibility:
  - claude
  - opencode
---

# Open PR

Commit staged changes, push the branch, open a pull request, and move the ticket
to its `review` lane. Ticketing via `tkt`; Git host via `gh`. All repo-specific
values (repo, branch format, reviewers, commit conventions, build commands) come
from `.sdlc/config.toml`.

## Input

- Feature branch with changes
- Ticket key (`$KEY`)
- Implementation summary

## Steps

### 1. Stage and commit

```shell
git add -A
git commit -m "<type>(<scope>): <description>"   # Conventional Commits
```

Scope = primary affected package. Imperative, lowercase, no period, < 72 chars.
Multiple logical changes → multiple commits.

### 2. Rebase on the default branch

```shell
git fetch origin
git rebase origin/main    # or your default branch
```

Resolve conflicts, then `git rebase --continue`.

### 3. Push

```shell
git push -u origin HEAD
```

### 4. Create the PR

Pull repo + reviewer config from `tkt`:

```shell
REPO=$(tkt cfg vcs.repo)
gh pr create --repo "$REPO" \
  --title "<type>(<scope>): <description>" \
  --body "## Summary

<one paragraph>

## Ticket

$(tkt view "$KEY" --json | jq -r .url)

## Changes
- <bullets>

## Testing
- <how tested>

## Checklist
- [x] Tests added/updated
- [x] Lint/typecheck pass"
```

### 5. Request reviewers

```shell
PR=<pr-number>
REPO=$(tkt cfg vcs.repo)
for R in $(tkt cfg vcs.reviewers --json | jq -r '.[]'); do
  gh api -X POST "repos/$REPO/pulls/$PR/requested_reviewers" -f "reviewers[]=$R" 2>/dev/null \
    || gh pr edit "$PR" --repo "$REPO" --add-reviewer "$R" 2>/dev/null \
    || echo "Note: reviewer '$R' not addable — skipping"
done
```

### 6. Transition the ticket (only if it has a review lane)

`full_sdlc` tickets (Story/Bug) go to the `review` lane immediately on PR open.
`deliverable` tickets (Task/Spike/…) have no review lane — leave them in
`in_progress`; `complete-deliverable` handles their final transition. Branch on
`type_class` (no need to enumerate live transitions):

```shell
CLASS=$(tkt view "$KEY" --json | jq -r .type_class)
PR_URL=$(gh pr view "$PR" --repo "$(tkt cfg vcs.repo)" --json url --jq .url)

if [ "$CLASS" = "full_sdlc" ]; then
  # Annotate time in in_progress, then move to review.
  WL=$(tkt worklog "$KEY" --from-role in_progress --note "PR opened: $PR_URL" --json)
  tkt transition "$KEY" review
  tkt comment "$KEY" "PR opened: $PR_URL. Review requested. Time in In Progress: \
$(echo "$WL" | jq -r .human) (worklog $(echo "$WL" | jq -r .worklog_id))."
else
  tkt comment "$KEY" "PR opened: $PR_URL. Review requested. (Deliverable type — staying in In Progress until merge + Done.)"
fi
```

`tkt worklog` is a no-op (returns an empty `worklog_id`) when the project's
`[timetracking].provider = "none"`, so this works unchanged on backends with no
time tracking — the comment just shows `(no time tracking)`.

## Output

- PR URL + number
- Whether reviewers were added or skipped
