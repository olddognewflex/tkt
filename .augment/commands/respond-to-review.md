# /respond-to-review

Read PR review comments, address each with code changes or replies, push fixes, loop until approved. Git host via `gh`; ticketing via `tkt`.

## Steps

1. Fetch review comments and decision with `gh pr view` and `gh api`.
2. Categorize: change request → fix; question → reply; suggestion → apply if valid; nit → apply.
3. Make changes and reply on threads.
4. Commit and push.
5. Re-request reviewers from `tkt cfg vcs.reviewers --json`.
6. Move ticket `revise → in_progress → review`, annotating lane time with `tkt worklog`.
7. Loop until approved and no unresolved comments. Max 5 cycles.

## Rules

- `tkt worklog` is a no-op when time tracking is disabled.
- All ticketing access goes through `tkt`.
