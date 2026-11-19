---
name: automated-sdlc
description: 'Automated SDLC pipeline: ticket selection, intake, plan, implement, self-review, PR, review, CI, preview, human QA gate, deploy. Provider-agnostic via tkt — configure ticketing + board in .sdlc/config.toml. Orchestrates sub-skills with explicit human gates and lane-time annotation.'
model_tier: standard
---

# Automated SDLC

End-to-end pipeline from "what should I work on?" to "shipped." Orchestrates
sub-skills with explicit human gates and per-lane time annotation. **Every
ticketing/board operation goes through `tkt`**, so the same pipeline runs on Jira,
GitHub Issues+Projects, Linear, qi, or a markdown board — the backend and board
shape are declared in `.sdlc/config.toml`.

## Model routing

Before invoking a phase, ask the resolver where it runs. The phase key is the
skill name (`plan-ticket`, `self-review`, ...):

```shell
R=$(tkt cfg route "<phase>" --json); rc=$?
```

| Exit | Meaning | Do |
|------|---------|----|
| 0 | resolved | read `source`, `harness`, `model`, `cmd` from `$R` |
| 4 | nothing configured for the phase | run it on the session default |
| other | a routing table is invalid (2) or `tkt` failed | stop and report the error; never guess a route |

Every phase runs **in this session**: delegate as usual (subagent, else
inline), passing the resolved `model` when this harness can select one per
delegation. A phase's output (a plan, code, ticket transitions) has to land
here, so the orchestrator never shells a whole phase out to another harness.
When a phase's route names a different harness (`source` is `route`,
`harness` is set, and it is not the harness you are running in, judged by the
program `cmd` runs rather than the `harness` key), that route is for `tkt run`,
which runs whole phases through it. Say so and run the phase here
on the session default: its `model` belongs to the other harness.

The one exception is the **`self-review` review pass**. It is read-only and
reports findings, so `self-review` step 2 resolves its own route and, under that
same condition, shells out to the routed `cmd` and reads the findings from the
**last** stdout line starting `TKT-RESULT: ` (the JSON contract `tkt run`
reads). Invoke `self-review` in-session as usual and let it do the routing. If
the routed run cannot happen (non-zero exit, no valid result line, a prompt too
large to pass), step 2 reviews in-session and says the routed review did not run.
That fallback is never read as a PASS.

A routed invocation gets no more trust than the agent it stands in for: the
routed review pass stands in for the read-only `sdlc-reviewer` agent, so its
harness `cmd` should be read-only. The resolver cannot check this; the
project's `[harnesses]` table owns it. `tkt run` reads the same route for its
whole self-review phase, fixes included, so a read-only `cmd` there blocks the
ticket on any finding; see [docs/model-routing.md](../../docs/model-routing.md)
("Trust") for choosing between the two.

Each sub-skill declares a `model_tier` (`cheap` | `standard` | `deep`) in its
frontmatter, which the resolver falls back to through `[models]`:

| Tier | Phases |
|------|--------|
| cheap | `select-ticket`, `check-blockers`, `triage-ticket` |
| deep | `plan-ticket` |
| standard | everything else |

**Escalate on failure, never start high.** A cheap-tier phase that returns a wrong
or ambiguous result gets retried once at standard. If `self-review` cycles 3+ times
without converging, re-plan at deep and resume the review at standard. Look up the
escalated model with `tkt cfg models.<tier>` (exit 4: stay on the session default).
Escalation is per-invocation — subsequent phases resume at their assigned tier.

Escalation applies only to a phase run in this session on its resolved model
(not one on the session default because its route names another harness) whose
resolved `tier` is set: a `tier` route, a harness-only route (its tier comes from the skill), or
`source` `tier`. **A route with a pinned `model` never escalates** (its resolved
`tier` is empty): every retry and every review pass stays on that model, and
when the phase's own limit is spent (for `self-review`, 3 passes) it stops for a
human as usual. A routed review pass on another harness keeps its resolved
`cmd` and never escalates either. Pinning one phase does not change the others:
a pinned review that cycles 3+ times still triggers the deep re-plan when
`plan-ticket` is tier-routed.

**Never block on a missing route.** If the harness cannot select a model, or
nothing is configured (exit 4), proceed on the session default. No phase requires
a specific model to produce correct output — routing is a cost and
independence choice. Full policy and the per-harness support matrix:
[docs/model-routing.md](../../docs/model-routing.md).

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
(e.g. your ticketing backend's worklog, or a local JSONL row), and returns
`{human, worklog_id, seconds}`. It is a **no-op** (empty `worklog_id`) when
`[timetracking].provider = "none"`. Then post the human-readable comment embedding
both values:

```shell
WL=$(tkt worklog "$KEY" --from-role in_progress --note "PR opened" --json)
tkt comment "$KEY" "PR opened. Time in In Progress: \
$(printf '%s\n' "$WL" | jq -r .human) (worklog $(printf '%s\n' "$WL" | jq -r .worklog_id))."
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
Drops candidates with unresolved blockers, containers (Epics by default) and any
ticket with open children. Tickets carrying the after-hours label
(optional `[schedule]` table, evaluated by `tkt schedule`) are deferred
during business hours when other workable candidates exist. If nothing across all
tiers → stop with a "nothing to work on" report.

### Phase 1: Triage

**Invoke `triage-ticket`** with the key. Transitions `todo → in_progress`. Returns
the ticket `type` and `type_class`. A container is returned untouched (no
transition, no branch) and stops at Phase 1.5.

### Phase 1.5: Route by type

```shell
if ! T=$(tkt view "$KEY" --json); then
  echo "STOP: could not read $KEY (see the error above)."; CLASS=unread
elif ! CLASS=$(printf '%s\n' "$T" | jq -er .type_class); then
  echo "STOP: could not read the type of $KEY."; CLASS=unread
fi
case "$CLASS" in
  unread)      : ;;                               # stopped above: never route an unread ticket
  full_sdlc)   : ;;                               # continue to Phase 2
  deliverable) echo "→ complete-deliverable"; ;;  # invoke complete-deliverable, then stop
  container)   # never worked or closed by the agent: stop, no transition,
               # no comment of its own (`tkt run` may still write its phase marker)
    MSG=$(printf '%s\n' "$T" | jq -r 'if .rollup then "\(.rollup.closed)/\(.rollup.total) children closed" else "progress unavailable" end')
    READY=$(printf '%s\n' "$T" | jq -r '.rollup.ready_to_close // false')
    echo "STOP: $KEY is a container: $MSG. Containers are never worked or closed by the agent; a human closes it."
    if [ "$READY" = "true" ]; then
      echo "All children are closed: propose closing $KEY to a human."
    fi
    ;;
  *)           # unknown: fall back to lane availability
    HAS_REVIEW=$(printf '%s\n' "$T" | jq -r '[.transitions[]? == (env.REVIEW_LANE)] | any')
    ;;
esac
```

`full_sdlc` → full pipeline. `deliverable` → **invoke `complete-deliverable`,
then stop**. `container` → **stop** and report child progress; under `tkt run`
the outcome is `gate` (reason: container). A ticket that can't be read, or
whose `type_class` can't be read, **stops** too: an unread type is never
treated as `unknown`, so it can't route a container onward. `[issue_types]` decides which types
fall in each set, so route on `type_class`, never on the type name. The example
configs put Story and Bug in `full_sdlc`, Task, Sub-task, Chore and Spike in
`deliverable`, and Epic in `container` (also the default when `container` is
absent; `container = []` turns it off). A project whose sub-tasks ship code
moves Sub-task to `full_sdlc`.

### Phase 2: Plan (→ `sdlc-planner`)

**Delegate to `sdlc-planner`** with the ticket JSON (`tkt view "$KEY" --json`) as
context. It returns files to touch, ordered steps, risks, test strategy, and
parallelism hints. External dep missing → see "External blocker handling".

**Fallback:** invoke `plan-ticket` inline. Same output, same context — acceptable
here because planning is not adversarial.

### Phase 3: Implement + test (→ `sdlc-executor`)

1. Branch: already created by `triage-ticket` (Phase 1). Entering this phase
   directly, without triage, create it first —
   `git checkout -b "$(tkt cfg vcs.branch_fmt --ticket "$KEY" --slug "<slug>")"`
2. Implement per plan. **Delegate to `sdlc-executor`**, which is scoped to the repo
   worktree and cannot transition tickets or push. Fallback: implement inline.
3. After each logical unit, run the configured toolchain:

   ```shell
   eval "$(tkt cfg build.build --pkg "<pkg>")"
   eval "$(tkt cfg build.typecheck)"
   eval "$(tkt cfg build.test --pkg "<pkg>")"
   ```

4. If tests fail → invoke `debug` → loop back.

### Phase 4: Self-review (adversarial)

**Invoke `self-review`** in a loop until clean. Its review pass delegates to
`sdlc-reviewer` (read-only, findings only) and its check pass to `sdlc-verifier`
(PASS/FAIL with command output as evidence) — both fresh contexts that did not
write the code.

### Phase 4.5: Adversarial QA (optional, → `qa-engineer`)

Run when the change carries real failure risk — new endpoints, auth, money, data
migrations, concurrency — or when Phase 4 flagged a test gap. Skip for docs-only
and trivial changes; say which and why.

**Delegate to `qa-engineer`**, which routes to the QA skills: `qa-strategy` (test
plan from acceptance criteria), `qa-author` (adversarial tests, red-before-green),
`qa-critique` (existing-suite assessment). See
[docs/qa-suite-design.md](../../docs/qa-suite-design.md).

A P0 finding sends the ticket back to Phase 3. **Fallback:** invoke `qa-author`
inline against the modules Phase 4 flagged.

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
tkt edit "$KEY" --agent-status waiting
tkt comment "$KEY" "Preview: <url>. PR open and approved, awaiting QA. \
Time in review (incl. loops): $(printf '%s\n' "$WL" | jq -r .human) (worklog $(printf '%s\n' "$WL" | jq -r .worklog_id))."
```

**Stop here.** QA owns the next move: `qa_ready → qa` (testing), then `→ deploy_ready`
(pass, pipeline resumes at Phase 10) or `→ revise` (fail, resume via
`resume-from-revise`).

### Phase 10: Deploy

**Invoke `deploy-ready`** — picks up `deploy_ready` tickets, merges the open PR,
watches the deploy workflow, gates manual prod deploy, then handles the final
`done` transition per your project's deploy contract. During business hours a
ticket carrying the after-hours label is held before merge while other
`deploy_ready` tickets proceed, and the prod gate warns if the release would ship
one. If nothing else can proceed, the outcome is `gate`.

## Side flows

### External blocker handling

```shell
WL=$(tkt worklog "$KEY" --from-role in_progress --note "Blocked — <desc>" --json)
tkt transition "$KEY" blocked
tkt edit "$KEY" --agent-status blocked
tkt comment "$KEY" "BLOCKED: <desc>. Waiting on: <who>. Need: <what unblocks>. \
Time in In Progress before blocking: $(printf '%s\n' "$WL" | jq -r .human) (worklog $(printf '%s\n' "$WL" | jq -r .worklog_id))."
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
