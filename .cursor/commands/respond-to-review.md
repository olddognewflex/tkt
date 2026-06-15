# /respond-to-review

Read PR review comments, address each with code changes or replies, push fixes, loop until approved. Git host via `gh`; ticketing via `tkt`.

## Steps

1. Fetch review comments and decision:
   ```shell
   REPO=$(tkt cfg vcs.repo)
   OWNER=${REPO%/*}
   NAME=${REPO#*/}
   gh pr view "$PR" --repo "$REPO" --json reviews,reviewRequests,comments,reviewDecision
   gh api --paginate "repos/$OWNER/$NAME/pulls/$PR/comments?per_page=100"
   ```
2. Categorize: change request → fix; question → reply; suggestion → apply if valid; nit → apply; blocker → must fix.
3. Make changes and reply on threads.
4. Commit and push:
   ```shell
   git add -A && git commit -m "fix(<scope>): address review feedback" && git push
   ```
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
