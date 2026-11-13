---
name: respond-to-review
description: 'Read PR review comments, address each with code changes or replies, push fixes, loop until approved. VCS via gh; ticketing via tkt. Invoke as slash command, not automatically.'
disable-model-invocation: true
---

# Respond to Review

Read PR review comments, address each, push fixes, loop until approved. Git host
via `gh`; all ticketing via `tkt`.

Input: PR number (`$PR`, e.g. from `gh pr view --json number --jq .number`) and ticket key (`$KEY`).

## Steps

1. Fetch review comments and decision with `gh pr view` and `gh api`.
2. Categorize: change request → fix; question → reply; suggestion → apply if valid; nit → apply; blocker → must fix.
3. Make changes. Post answers and fix summaries on the comment's own thread: `gh api "repos/$(tkt cfg vcs.repo)/pulls/$PR/comments/<root-comment-id>/replies" -f body=...` (`gh pr comment` only for a question with no thread).
4. Commit and push (skip the commit when nothing changed, e.g. a round that only answered questions, and just push): `git add -A && git commit -m "fix(<scope>): address review feedback" && git push`.
5. Re-request reviewers from `tkt cfg vcs.reviewers --json`.
6. Move ticket `revise → in_progress → review`, annotating lane time with `tkt worklog`.
7. Loop until `reviewDecision == APPROVED`, no unresolved bot comments, and no unresolved human thread except one left open on purpose (a partial fix, an out-of-scope or ambiguous item, or a justified won't-fix) that the reviewer hasn't replied to since. Max 5 cycles.

## Output

- Review status: approved / changes requested / stuck
- Comments addressed (count)
- Outstanding items

## Rules

- `tkt worklog` is a no-op when time tracking is disabled.
- Resolve a thread once its fix is in the pushed diff, or once you have answered its question on the thread (an answer changes no file: no diff check).
- Never argue with repeated bot nits; justify "won't fix" on the thread and resolve it.
- A human reviewer's comment you won't fix: justify on the thread, leave it open.
- A change you only partly made: say on the thread what remains (a note, not a fix summary), leave it open.
- All ticketing access goes through `tkt`.
