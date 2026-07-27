---
name: respond-to-review
description: 'Read PR review comments, address each with code changes or replies, push fixes, auto-resolve addressed threads with fix summaries, loop until approved. VCS via gh; ticketing (revise/review transitions + lane time) via tkt.'
model_tier: standard
---

# Respond to Review

Read PR review comments (bot + human), address each, push fixes, auto-resolve the
threads you actually addressed, loop until approved. Git host via `gh`; all
ticketing via `tkt`. Repo/reviewers from `.sdlc/config.toml`.

## Input

- PR number (`$PR`)
- Ticket key (`$KEY`)

```shell
REPO=$(tkt cfg vcs.repo); OWNER=${REPO%/*}; NAME=${REPO#*/}
```

## Ticket state during this skill

A "changes requested" review puts the ticket in the `revise` lane. While
addressing feedback, move it back to `in_progress`; after pushing fixes and
re-requesting review, move to `review`. Both are agent-driven → annotate lane time
with `tkt worklog`:

```shell
# Exiting revise → in_progress:
WL=$(tkt worklog "$KEY" --from-role revise --note "Addressing review feedback" --json)
tkt transition "$KEY" in_progress
tkt edit "$KEY" --agent-status processing
tkt comment "$KEY" "Addressing review feedback. Time in Revise: \
$(echo "$WL" | jq -r .human) (worklog $(echo "$WL" | jq -r .worklog_id))."
```

(Skip the revise annotation if the ticket wasn't in `revise` — e.g. first review
round straight from `review`.)

## Steps

### 1. Fetch review comments (bot + humans)

```shell
gh pr view "$PR" --repo "$REPO" --json reviews,reviewRequests,comments,reviewDecision

# Inline comments — always paginate.
gh api --paginate "repos/$OWNER/$NAME/pulls/$PR/comments?per_page=100" \
  --jq '.[] | {id, path, line, body, user: .user.login, in_reply_to_id}'
```

Identify automated-reviewer comments by `user.login` (e.g. `*copilot*[bot]`, or
whatever your `vcs.reviewers` lists). Track unresolved ones separately.

Thread state — fetch thread IDs, resolution status, file path, and comment bodies
(paginate via `pageInfo.hasNextPage`). The thread ID and path are what step 6 needs
to resolve, so collect them now:

```shell
CURSOR=null
while : ; do
  RESP=$(gh api graphql -f query='
    query($owner:String!, $repo:String!, $pr:Int!, $after:String) {
      repository(owner:$owner, name:$repo) { pullRequest(number:$pr) {
        reviewThreads(first:100, after:$after) {
          pageInfo { hasNextPage endCursor }
          nodes {
            id isResolved path line
            comments(first:100) {
              nodes { id databaseId author{login} body }
            }
          }
        } } } }' \
    -F owner="$OWNER" -F repo="$NAME" -F pr="$PR" -F after="$CURSOR")
  echo "$RESP" | jq -c '.data.repository.pullRequest.reviewThreads.nodes[]'
  [ "$(jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage' <<<"$RESP")" = "true" ] || break
  CURSOR=$(jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.endCursor' <<<"$RESP")
done
```

Build a mapping of unresolved threads:
`thread_id → {path, line, comment_id, body, author}`. Step 6 drives resolution off
it, so a thread you can't map is a thread you must not resolve.

### 2. Categorize each comment

Change request → make the change. Question → reply. Suggestion → apply if valid,
else explain. Nit → apply. Blocker → must fix. Out of scope → acknowledge, create
a follow-up with `tkt create` if warranted.

Record the outcome per thread — this decides what gets resolved:

- `addressed` — change fully implemented (**will** resolve)
- `question` — answered (**will** resolve after reply)
- `partial` — change only partly implemented (will **not** resolve)
- `skipped` — out of scope, won't-fix, or ambiguous (will **not** resolve)

### 3. Address change requests

For each `addressed` thread: make the code change and verify it builds/tests
(`tkt cfg build.*`). **Do not reply yet** — fix-summary replies cite the commit
SHA, which doesn't exist until step 5.

### 4. Reply to questions

```shell
gh pr comment "$PR" --repo "$REPO" --body "Re: <question> — <answer>"
```

### 5. Commit and push

```shell
git add -A && git commit -m "fix(<scope>): address review feedback" && git push
SHA=$(git rev-parse --short HEAD)
```

### 6. Resolve addressed threads + post fix summaries

For each thread categorized `addressed` or `question`:

1. **Verify the fix is actually in the diff.** A thread whose file was never
   touched must not be marked resolved:

   ```shell
   git diff --name-only HEAD~1 HEAD | grep -Fxq "<path>"
   ```

   Not in the diff → re-categorize as `partial` and skip.

2. **Post a fix-summary reply** on the thread:

   ```shell
   gh api "repos/$OWNER/$NAME/pulls/$PR/comments/<comment-id>/replies" \
     -f body="Fixed in \`$SHA\`: updated \`<file>\` — <one-line change summary>"
   ```

   Questions were already answered in step 4; go straight to resolving.

3. **Resolve the thread:**

   ```shell
   gh api graphql -f query='
     mutation($threadId: ID!) {
       resolveReviewThread(input: {threadId: $threadId}) {
         thread { id isResolved }
       }
     }' -F threadId="$THREAD_ID"
   ```

4. **Accumulate a resolution log entry** for step 7:

   ```
   • [`<file>:<line>`](https://github.com/$OWNER/$NAME/blob/$SHA/<file>#L<line>): <summary>
   ```

**Never resolve when:** the fix is partial; the comment is ambiguous and needs
reviewer confirmation; the thread bundles several requests and only some are done;
or the path isn't in the commit diff. Leave those threads open **without** a
fix-summary reply — a summary on an unresolved thread misleads the reviewer.

### 7. Post the consolidated resolution log

One PR comment as the audit trail:

```shell
BODY="## Review thread resolution

**Commit:** \`$SHA\`
**Resolved:** $RESOLVED_COUNT thread(s)
**Left open:** $OPEN_COUNT thread(s)

### Resolved
$(printf '%s\n' "${RESOLVED_LOG[@]}")

### Left open (needs reviewer)
$(printf '%s\n' "${OPEN_LOG[@]}")"

gh pr comment "$PR" --repo "$REPO" --body "$BODY"
```

Each resolved entry links its file path to the line in the pushed commit.

### 8. Re-request review + move back to review lane

```shell
for R in $(tkt cfg vcs.reviewers --json | jq -r '.[]'); do
  gh api -X POST "repos/$OWNER/$NAME/pulls/$PR/requested_reviewers" -f "reviewers[]=$R" 2>/dev/null || true
done

WL=$(tkt worklog "$KEY" --from-role in_progress --note "Revise cycle — fixes pushed, re-review requested" --json)
tkt transition "$KEY" review
tkt edit "$KEY" --agent-status waiting
tkt comment "$KEY" "Pushed fixes, re-requested review. Time in In Progress (revise cycle): \
$(echo "$WL" | jq -r .human) (worklog $(echo "$WL" | jq -r .worklog_id)). \
Resolved $RESOLVED_COUNT review thread(s); $OPEN_COUNT left open for the reviewer."
```

### 9. Loop until clean

Re-fetch after each push. Exit only when **all** hold:

- No automated-reviewer comments on unresolved threads
- `reviewDecision == APPROVED` (a pending/`REVIEW_REQUIRED` state is NOT enough)
- No new comments in the last poll

Cap at **5** cycles. If a bot repeats the same nit after 2 attempts, reply with a
one-line "won't fix" justification and resolve the thread.

### 10. If stuck after 5 cycles

```shell
gh pr comment "$PR" --repo "$REPO" --body "5 review cycles complete. Remaining items need human judgment — requesting sync review."
WL=$(tkt worklog "$KEY" --from-role review --note "Review cycling unresolved after 5 attempts" --json)
tkt comment "$KEY" "Review cycling unresolved after 5 attempts — needs human sync. \
Time in review so far: $(echo "$WL" | jq -r .human) (worklog $(echo "$WL" | jq -r .worklog_id))."
```

(`$KEY` is the ticket; `$PR` is the PR number — unrelated. Always pass `$PR`
explicitly to `gh pr comment`.)

### 11. On loop exit, annotate review lane time

```shell
WL=$(tkt worklog "$KEY" --from-role review --note "Review loop complete. Cycles: <N>" --json)
tkt comment "$KEY" "Review loop complete. Cycles: <N>. Time in review (this round): \
$(echo "$WL" | jq -r .human) (worklog $(echo "$WL" | jq -r .worklog_id))."
```

## Output

- Review status: approved / changes requested / stuck
- Comments addressed (count)
- Threads resolved vs. left open (count)
- Outstanding items
