---
mode: agent
description: 'Confirm preview environment for a PR, extract URL, and post to ticket + PR via tkt.'
tools: ['terminal']
---

# Deploy Preview

Confirm an ephemeral preview environment for a PR, extract the service URL, and link it back to the ticket. Git host via `gh`; ticketing via `tkt`.

## Steps

1. Verify preview deployment triggered (`gh pr checks` or `gh workflow run "$PREVIEW_WF"`).
2. Wait for deployment to finish.
3. Extract preview URL — **project-specific** (stack output, workflow log, deterministic pattern).
4. Verify health with `curl -sf <url>/health` (project-specific).
5. Comment URL on ticket: `tkt comment "$KEY" "Preview environment: <url> ..."`.
6. Comment URL on PR: `gh pr comment "$PR" --repo "$REPO" --body "## Preview Environment
🔗 <url>
✅ Health: passing"`.
7. Do **not** transition the ticket.

## Output

- Preview URL, health status, ticket + PR updated.

## Rules

- URL extraction is project-specific; replace with your own method.
- Always pass `$PR` explicitly to `gh pr comment`.
- All ticketing via `tkt`.
