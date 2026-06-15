---
name: deploy-preview
description: 'Confirm the preview environment for a PR, extract its URL, and post it back to the ticket and PR. Provider-agnostic ticketing via tkt; VCS via gh. URL extraction is project-specific.'
---

# Deploy Preview

Confirm an ephemeral preview environment for a PR, extract the service URL, and
link it back to the ticket. Git host via `gh`; ticketing via `tkt`. The
URL-extraction step is **project-specific** (depends on your hosting) — the rest
is portable.

## Input

- PR number (`$PR`), ticket key (`$KEY`)

```shell
REPO=$(tkt cfg vcs.repo)
PREVIEW_WF=$(tkt cfg deploy.preview_workflow 2>/dev/null || echo "")
```

## Steps

### 1. Verify the preview deployment triggered

```shell
gh pr checks "$PR" --repo "$REPO" | grep -i preview || true
# If a dedicated workflow exists and didn't auto-run:
[ -n "$PREVIEW_WF" ] && gh workflow run "$PREVIEW_WF" --repo "$REPO" -f pr_number="$PR"
```

### 2. Wait for deployment

```shell
[ -n "$PREVIEW_WF" ] && gh run list --workflow="$PREVIEW_WF" --repo "$REPO" \
  --branch="$(git branch --show-current)" --limit=1 --json status,databaseId
```

### 3. Extract the preview URL — PROJECT-SPECIFIC

This depends on where previews live. Replace with your project's method. Examples:

```shell
# AWS CloudFormation/CDK stack output:
# aws cloudformation describe-stacks --stack-name <stack-pr-$PR> \
#   --query "Stacks[0].Outputs[?OutputKey=='ServiceUrl'].OutputValue" --output text --region <region>

# Or: a URL the preview workflow prints / a deterministic pattern:
# PREVIEW_URL="https://pr-$PR.preview.example.com"
```

### 4. Verify health (project-specific)

```shell
curl -sf "<preview-url>/health" || echo "preview health check failed"
```

### 5. Post the URL to the ticket

```shell
tkt comment "$KEY" "Preview environment: <preview-url> (PR #$PR). Health: passing."
```

### 6. Post the URL to the PR

Always pass `$PR` explicitly (don't let `gh` infer from the current branch):

```shell
gh pr comment "$PR" --repo "$REPO" --body "## Preview Environment
🔗 <preview-url>
✅ Health: passing
Destroyed when this PR closes/merges."
```

### 7. Do NOT transition the ticket

`deploy-preview` posts the URL only. Standalone → transitioning is the caller's
choice. From `automated-sdlc` → Phase 9 owns the `qa_ready` transition after the
URL is confirmed; transitioning here would jump the gate.

## Output

- Preview URL, health status, ticket + PR updated
