---
name: automated-sdlc
description: 'Automated SDLC pipeline: ticket selection, intake, plan, implement, self-review, PR, review, CI, preview, human QA gate, deploy. Provider-agnostic via tkt — configure ticketing + board in .sdlc/config.toml. Orchestrates sub-skills with explicit human gates and lane-time annotation.'
compatibility:
  - claude
  - opencode
---

# Automated SDLC

End-to-end pipeline from "what should I work on?" to "shipped." Orchestrates
sub-skills with explicit human gates and per-lane time annotation. **Every
ticketing/board operation goes through `tkt`**, so the same pipeline runs on Jira,
GitHub Issues+Projects, Linear, qi, or a markdown board — the backend and board
shape are declared in `.sdlc/config.toml`.

## Prerequisites

- `tkt doctor` passes (ticketing auth + board model reachable).
- `gh` authenticated (for the PR/CI/merge phases).
- Toolchain for local build/test (commands resolved via `tkt cfg build.*`).
- Docker running if your tests need it.
- On the default branch, up to date.

## Board lanes (roles)

Skills speak in canonical **roles**; `.sdlc/config.toml` `[board.roles]` maps each
to the provider's actual lane name. Default flow:

```
backlog → todo → in_progress → review → qa_ready → qa → deploy_ready → done
```

Side roles: `revise`, `blocked`, `cancelled`. Print a literal lane name with
`tkt lane <role>`. A board that lacks a lane (e.g. a markdown board with no
`qa_ready`) simply doesn't define that role — type routing (below) keeps such
tickets on the short flow.

## Ownership of transitions

Driven by `[board.ownership]` in config. Default:

| Transition (role→role) | Owner |
| --- | --- |
| backlog → todo | Human (refinement) |
| todo → in_progress | **Agent** (after select + triage) |
| in_progress → review | **Agent** (on PR open) |
| review → revise | Reviewer — or `respond-to-review` if changes requested mid-loop |
| revise → in_progress → review | **Agent** (`resume-from-revise`) |
| review → qa_ready | **Agent** (QA promotion gate; PR stays open) |
| qa_ready → qa | **Human (QA)** |
| qa → revise / qa → deploy_ready | **Human (QA)** |
| deploy_ready → done | **Human** (until the deploy workflow truly deploys) |

## Lane-time annotation

Every agent-driven transition out of a lane records lane time via **one command**:

```shell
tkt worklog "$KEY" --from-role <role> --note "<context>" --json
```

`tkt worklog` finds the most recent entry into that lane (paging the provider's
full changelog/history), computes elapsed time, writes the canonical time entry
(e.g. a Jira/Tempo worklog, or a local JSONL row), and returns
`{human, worklog_id, seconds}`. It is a **no-op** (empty `worklog_id`) when
`[timetracking].provider = "none"`. Then post the human-readable comment embedding
both values:

```shell
WL=$(tkt worklog "$KEY" --from-role in_progress --note "PR opened" --json)
tkt comment "$KEY" "PR opened. Time in In Progress: \
$(echo "$WL" | jq -r .human) (worklog $(echo "$WL" | jq -r .worklog_id))."
```

Annotate on: in_progress → review, review → qa_ready, deploy_ready → done, any
revise → in_progress, and any agent-driven transition to `blocked`. (This replaces
the old hand-rolled `annotate_lane_time` shell/Python helper entirely — the
changelog pagination + billable logic now live inside the provider adapter.)

Use `--billable` only for client/billable work (provider-dependent; ignored where
the backend has no billable concept).

## Pipeline

### Phase 0: Select ticket

**Invoke `select-ticket`.** Auto-selects Tier 1/2 (assigned); recommends Tier 3–5.
Drops candidates with unresolved blockers. If nothing across all tiers → stop with
a "nothing to work on" report.

### Phase 1: Triage

**Invoke `triage-ticket`** with the key. Transitions `todo → in_progress`. Returns
the ticket `type` and `type_class`.

### Phase 1.5: Route by type

```shell
CLASS=$(tkt view "$KEY" --json | jq -r .type_class)
case "$CLASS" in
  full_sdlc)   : ;;                               # continue to Phase 2
  deliverable) echo "→ complete-deliverable"; ;;  # invoke complete-deliverable, then stop
  *)           # unknown: fall back to lane availability
    HAS_REVIEW=$(tkt view "$KEY" --json | jq -r '[.transitions[]? == (env.REVIEW_LANE)] | any')
    ;;
esac
```

`full_sdlc` (Story/Bug) → full pipeline. `deliverable` (Task/Sub-task/Chore/Epic/
Spike) → **invoke `complete-deliverable`, then stop**. The `full_sdlc` /
`deliverable` sets are defined in `[issue_types]`.

### Phase 2: Plan

**Invoke `plan-ticket`.** Output: files, test strategy, risks. External dep missing
→ see "External blocker handling".

### Phase 3: Implement + test

1. Branch: `git checkout -b "$(tkt cfg vcs.branch_fmt --ticket "$KEY" --slug "<slug>")"`
2. Implement per plan.
3. After each logical unit, run the configured toolchain:

   ```shell
   eval "$(tkt cfg build.build --pkg "<pkg>")"
   eval "$(tkt cfg build.typecheck)"
   eval "$(tkt cfg build.test --pkg "<pkg>")"
   ```

4. If tests fail → invoke `debug` → loop back.

### Phase 4: Self-review (adversarial)

**Invoke `self-review`** in a loop until clean.

### Phase 5: Open PR

**Invoke `open-pr`** — pushes, opens the PR, requests reviewers, and (for
`full_sdlc`) transitions `in_progress → review` with lane-time annotation.

### Phase 6: CI loop

**Invoke `ci-fix`** — wait → diagnose → fix → push → repeat until green.

### Phase 7: Review loop

**Invoke `respond-to-review`** — handle all reviewer feedback. Loop until no
unaddressed comments and the PR is approved. If a reviewer moves the ticket to
`revise` mid-loop, `respond-to-review` flips it back to `in_progress` while pushing
fixes, then back to `review`.

### Phase 8: Preview env confirmation

After CI is green, confirm the preview URL is live. **Invoke `deploy-preview`** to
comment the URL on the ticket (it does not transition).

### Phase 9: Promote to qa_ready (human QA gate)

PR stays **open**. Transition + annotate:

```shell
WL=$(tkt worklog "$KEY" --from-role review --note "Approved — promoting to QA" --json)
tkt transition "$KEY" qa_ready
tkt comment "$KEY" "Preview: <url>. PR open and approved, awaiting QA. \
Time in review (incl. loops): $(echo "$WL" | jq -r .human) (worklog $(echo "$WL" | jq -r .worklog_id))."
```

**Stop here.** QA owns the next move: `qa_ready → qa` (testing), then `→ deploy_ready`
(pass, pipeline resumes at Phase 10) or `→ revise` (fail, resume via
`resume-from-revise`).

### Phase 10: Deploy

**Invoke `deploy-ready`** — picks up `deploy_ready` tickets, merges the open PR,
watches the deploy workflow, gates manual prod deploy, then handles the final
`done` transition per your project's deploy contract.

## Side flows

### External blocker handling

```shell
WL=$(tkt worklog "$KEY" --from-role in_progress --note "Blocked — <desc>" --json)
tkt transition "$KEY" blocked
tkt comment "$KEY" "BLOCKED: <desc>. Waiting on: <who>. Need: <what unblocks>. \
Time in In Progress before blocking: $(echo "$WL" | jq -r .human) (worklog $(echo "$WL" | jq -r .worklog_id))."
```

Then stop. Run `check-blockers` periodically to recommend unblock actions.

### Revise (QA failure)

When QA moves a ticket to `revise`, a human dev fixes it, then runs
`/resume-from-revise <KEY>` — annotates revise time and re-enters Phases 6–9.

### Production regression / revert

Invoke `hotfix-revert` — fast-track: highest-priority hotfix ticket, revert the
offending commit, single human signoff, skip review/QA loops.

## Decision points

| Condition | Action |
| --- | --- |
| Tests fail 3× after debug | Stop, comment, `tkt transition "$KEY" blocked`, request human help |
| CI flake (infra, not code) | `gh run rerun <id> --failed`; don't count against budget |
| Reviewer "request changes" | Loop via `respond-to-review` until approved |
| Merge conflicts | `git fetch && git rebase origin/main` → resolve → re-push |
| Ticket unclear / spec gap | `tkt transition "$KEY" blocked` with a clarifying comment |
| Scope > 400 LOC | Stop, recommend splitting via `plan-ticket` |
| Production regression | Invoke `hotfix-revert` |

## Abort conditions

Stop and notify (comment on the ticket) on: 3+ non-converging review cycles;
infrastructure CI failure; scope > ~400 lines (split needed); external blocker
unresolved > 24h.
