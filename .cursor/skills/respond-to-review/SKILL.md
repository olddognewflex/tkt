---
name: respond-to-review
description: 'Read PR review comments, address each with code changes or replies, push fixes, loop until approved. VCS via gh; ticketing via tkt. Invoke as slash command, not automatically.'
disable-model-invocation: true
---

# Respond to Review

Read PR review comments, address each, push fixes, loop until approved. Git host
via `gh`; all ticketing via `tkt`.

## Steps

1. Fetch review comments and decision with `gh pr view` and `gh api`.
2. Categorize: change request → fix; question → reply; suggestion → apply if valid; nit → apply; blocker → must fix.
3. Make changes and reply on threads.
4. Commit and push: `git add -A && git commit -m "fix(<scope>): address review feedback" && git push`.
5. Re-request reviewers from `tkt cfg vcs.reviewers --json`.
6. Move ticket `revise → in_progress → review`, annotating lane time with `tkt worklog`.
7. Loop until `reviewDecision == APPROVED` and no unresolved comments. Max 5 cycles.

## Output

- Review status: approved / changes requested / stuck
- Comments addressed (count)
- Outstanding items

## Rules

- `tkt worklog` is a no-op when time tracking is disabled.
- Never argue with repeated bot nits; justify "won't fix" and resolve.
- All ticketing access goes through `tkt`.
