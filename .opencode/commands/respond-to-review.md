---
description: Read PR review comments, address each, push fixes, and loop until approved via tkt
---

Respond to PR review feedback for ticket: $ARGUMENTS

1. Fetch review comments and decision with `gh pr view` and `gh api`.
2. Categorize each comment: change request → fix; question → reply; suggestion → apply if valid; nit → apply; blocker → must fix.
3. Make changes and reply on threads.
4. Commit and push: `git add -A && git commit -m "fix(<scope>): address review feedback" && git push`.
5. Re-request reviewers from `tkt cfg vcs.reviewers --json`.
6. Move ticket `revise → in_progress → review`, annotating lane time with `tkt worklog`.
7. Loop until `reviewDecision == APPROVED` and no unresolved comments. Max 5 cycles.

If stuck after 5 cycles, comment and escalate to human sync.

`tkt worklog` is a no-op when time tracking is disabled.
