---
name: respond-to-review
description: 'Read PR review comments, address each with code changes or replies, push fixes, loop until approved. VCS via gh; ticketing (revise/review transitions + lane time) via tkt.'
---

# Respond to Review

Read PR review comments (bot + human), address each, push fixes, loop until
approved. Git host via `gh`; all ticketing via `tkt`. Repo/reviewers from
`.sdlc/config.toml`.

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

Resolved-thread state (paginate via `pageInfo.hasNextPage`):

```shell
CURSOR=null
while : ; do
  RESP=$(gh api graphql -f query='
    query($owner:String!, $repo:String!, $pr:Int!, $after:String) {
      repository(owner:$owner, name:$repo) { pullRequest(number:$pr) {
        reviewThreads(first:100, after:$after) {
          pageInfo { hasNextPage endCursor }
          nodes { id isResolved comments(first:1){ nodes { author{login} body } } }
        } } } }' \
    -F owner="$OWNER" -F repo="$NAME" -F pr="$PR" -F after="$CURSOR")
  echo "$RESP" | jq -c '.data.repository.pullRequest.reviewThreads.nodes[]'
  [ "$(jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage' <<"$RESP")" = "true" ] || break
  CURSOR=$(jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.endCursor' <<"$RESP")
done
```

### 2. Categorize each comment

Change request → make the change. Question → reply. Suggestion → apply if valid,
else explain. Nit → apply. Blocker → must fix. Out of scope → acknowledge, create
a follow-up with `tkt create` if warranted.

### 3. Address change requests

For each: make the change, verify it builds/tests (`tkt cfg build.*`), reply on the
thread:

```shell
gh api "repos/$OWNER/$NAME/pulls/$PR/comments/<comment-id>/replies" \
  -f body="Fixed in <sha>. <brief explanation>"
```

### 4. Reply to questions

```shell
gh pr comment "$PR" --repo "$REPO" --body "Re: <question> — <answer>"
```

### 5. Commit and push

```shell
git add -A && git commit -m "fix(<scope>): address review feedback" && git push
```

### 6. Re-request review + move back to review lane

```shell
for R in $(tkt cfg vcs.reviewers --json | jq -r '.[]'); do
  gh api -X POST "repos/$OWNER/$NAME/pulls/$PR/requested_reviewers" -f "reviewers[]=$R" 2>/dev/null || true
done

WL=$(tkt worklog "$KEY" --from-role in_progress --note "Revise cycle — fixes pushed, re-review requested" --json)
tkt transition "$KEY" review
tkt comment "$KEY" "Pushed fixes, re-requested review. Time in In Progress (revise cycle): \
$(echo "$WL" | jq -r .human) (worklog $(echo "$WL" | jq -r .worklog_id))."
```

### 7. Loop until clean

Re-fetch after each push. Exit only when **all** hold:

- No automated-reviewer comments on unresolved threads
- `reviewDecision == APPROVED` (a pending/`REVIEW_REQUIRED` state is NOT enough)
- No new comments in the last poll

Cap at **5** cycles. If a bot repeats the same nit after 2 attempts, reply with a
one-line "won't fix" justification and resolve the thread.

### 8. If stuck after 5 cycles

```shell
gh pr comment "$PR" --repo "$REPO" --body "5 review cycles complete. Remaining items need human judgment — requesting sync review."
WL=$(tkt worklog "$KEY" --from-role review --note "Review cycling unresolved after 5 attempts" --json)
tkt comment "$KEY" "Review cycling unresolved after 5 attempts — needs human sync. \
Time in review so far: $(echo "$WL" | jq -r .human) (worklog $(echo "$WL" | jq -r .worklog_id))."
```

(`$KEY` is the ticket; `$PR` is the PR number — unrelated. Always pass `$PR`
explicitly to `gh pr comment`.)

### 9. On loop exit, annotate review lane time

```shell
WL=$(tkt worklog "$KEY" --from-role review --note "Review loop complete. Cycles: <N>" --json)
tkt comment "$KEY" "Review loop complete. Cycles: <N>. Time in review (this round): \
$(echo "$WL" | jq -r .human) (worklog $(echo "$WL" | jq -r .worklog_id))."
```

## Output

- Review status: approved / changes requested / stuck
- Comments addressed (count)
- Outstanding items
