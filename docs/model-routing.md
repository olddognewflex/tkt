# Model routing

Three-tier routing policy for the SDLC pipeline. Each phase runs on the cheapest
tier that preserves quality for that phase's workload.

Tier routing is **advisory everywhere**. No skill requires a model switch to
produce correct output — a harness with no model control runs the whole pipeline
on its session default and must behave identically.

A project can also pin a phase to a harness and model with `[harnesses]` and
`[routes]` (see [Routing a phase to another harness](#routing-a-phase-to-another-harness)),
for example a review pass on a second vendor's model so the reviewer is not the
model that wrote the code.

## Tiers, not model IDs

Skills and agents declare a **tier** in frontmatter:

```yaml
model_tier: cheap | standard | deep
```

The tier is the portable, backend-agnostic part. The concrete model is a project
decision, mapped in `.sdlc/config.toml`:

```toml
[models]
cheap    = "claude-haiku-4-5"
standard = "claude-sonnet-5"
deep     = "claude-opus-5"
```

The orchestrator resolves each phase at invocation time with the phase
resolver, which reads `[routes]` first and falls back to the skill's tier
through `[models]`:

```shell
tkt cfg route plan-ticket --json   # -> {"model": "claude-opus-5", "tier": "deep", "source": "tier", ...}
tkt cfg models.deep                # escalation lookup only -> claude-opus-5
```

This is the same rule the pack applies to every other project-specific value
(`build.*`, `vcs.*`): the skill names *what it needs*, config names *what it is*.
Model IDs change far faster than the pipeline does, so pinning them in skill text
would mean editing 20 files per model generation across every consumer repo.

`[models]` is optional. When `tkt cfg route <phase>` exits 4 (`NotFoundError`),
nothing is configured for that phase: no route, no `[models]` entry for its
tier, no `[run].harness_cmd`. Proceed on the session default. A project with
`[routes]` and no `[models]` still has routing configured, so check with
`tkt cfg route`, not `tkt cfg models.<tier>`.

> `model_tier` is unrelated to `[queries].tierN` / `tkt list --tier N`, which
> select *tickets* by priority band.

## Tier assignment

| Tier | Workload | Skills / agents |
|------|----------|-----------------|
| **cheap** | Board queries, filtering, mechanical transitions, pure lookup | `select-ticket`, `check-blockers`, `triage-ticket`, `ticket-researcher` |
| **deep** | Decomposition, architecture, risk analysis — where reasoning depth pays for itself | `plan-ticket`, `sdlc-planner` |
| **standard** | Everything else | `automated-sdlc`, `self-review`, `open-pr`, `ci-fix`, `respond-to-review`, `resume-from-revise`, `complete-deliverable`, `deploy-preview`, `deploy-ready`, `hotfix-revert`, `sdlc-executor`, `sdlc-reviewer`, `sdlc-verifier` |

Route by **phase workload, not ticket importance**. A Story's triage is still
mechanical; a Chore's plan may still warrant deep reasoning if its scope does.

## Escalation

Escalate on failure; never start high.

| Condition | Action |
|-----------|--------|
| Cheap-tier phase returns a wrong or ambiguous result | Retry that step once at standard |
| Standard-tier review cycles 3+ times without converging | Re-plan at deep, then resume the review at standard |
| Deep-tier plan is still unclear | Stop, comment on the ticket, request human input |
| Any escalation succeeds | Do **not** persist the escalated tier |
| Phase routed with a pinned `model` fails | Retry on the same model; once the phase's own limit is spent (`self-review`: 3 passes), stop for a human. Other phases still escalate by their own routes |

Escalation is per-invocation, not sticky. After a successful retry, subsequent
phases resume at their assigned tier.

These rules apply to **tier** routing only: a phase run in-session on a
resolved model (not one falling back to the session default because its route
names another harness) whose resolved `tier` is set (`source: "tier"`, a route that names a `tier`, or a
harness-only route, whose tier comes from the skill). A route that pins a
`model` resolves an empty `tier` and never escalates, and neither does a review
pass routed to another harness, which keeps its resolved `cmd`. The project chose that model on purpose, often
to keep a reviewer independent of the author, and silently swapping it for a
"bigger" one would defeat the choice. `tkt run` behaves the same way: it never
changes a phase's command mid-run.

## Routing a phase to another harness

`[harnesses]` names harness command lines and `[routes]` sends a phase (a skill
name) to one of them, with a pinned `model` or a `tier`. The tables, their
validation and the resolution order are in the README's
[Phase routing](../README.md#phase-routing-harnesses--routes) section. Both
consumers read them through one resolver:

```shell
tkt cfg route self-review --json
# {"phase": "self-review", "harness": "...", "model": "...", "tier": "",
#  "cmd": "... {prompt}", "source": "route"}
```

- **`tkt run`** (the external driver) runs each routed phase's whole prompt
  through the resolved `cmd`; unrouted phases run `[run].harness_cmd`. Under
  `tkt run` a route covers the whole phase; see [Trust](#trust) for what that
  means for a read-only `self-review` route.
- **Interactive skills** (`automated-sdlc` and the phases it invokes) run every
  phase in the session, because a phase's output (a plan, code, ticket
  transitions) has to land there. They call the resolver before delegating:

| Resolver answer | Interactive path |
|-----------------|------------------|
| exit 4 (nothing configured) | Delegate on the session default, as without routing |
| any other non-zero exit (2 = invalid table) | Stop and report it; never guess a route |
| `source` `tier` or `default`, or an empty `harness` (a model-only route), or a `harness` whose `cmd` runs the harness already running (judged by the program, not the key name) | Delegate in-session (subagent, else inline) with the resolved `model` where the harness can select one |
| `source` `route` with a different `harness`, for the `self-review` review pass | `self-review` step 2 shells out to `cmd` and reads the findings back |
| `source` `route` with a different `harness`, any other phase | Say the route is for `tkt run` and run the phase in-session on the session default |

The review pass is the one cross-harness hop because it is read-only and its
whole output is a verdict and findings, which fit the result line. Fixes stay
with the author, and the verifier's checks stay in-session because their
evidence is command output, not judgment.

Skill text names only the phase and the resolver, never a harness or model, so
the same skill runs whichever harnesses a project configures.

### Invocation and result contract

`self-review` writes the review prompt and its inputs (the diff, the plan or
ticket summary) to a file and substitutes `{prompt}` with
`"$(cat "$PROMPT_FILE")"`, so the prompt never passes through shell parsing.
That needs `{prompt}` as a **bare** shell word in `cmd`: `tkt` rejects a quoted
one (`"{prompt}"`, `'{prompt}'`, `--flag="{prompt}"`), an escaped one, one
inside `$(...)` or backticks, and one glued to other text, any of which would
hand the prompt back to the shell. `--flag={prompt}` is fine. The prompt travels as one argument, so one over 100000 bytes
is not routed (Linux caps a single argument at 128 KiB). The call runs as one
shell invocation that resolves the route itself, with stdin from `/dev/null`,
and blocks for the whole review; a call the shell tool kills on timeout is a
failed run. `self-review` step 2 carries the worked, shell-portable snippet.

The routed harness reports back on stdout with the contract `tkt run` uses: the
**last** line starting `TKT-RESULT: ` is the result, a JSON object with `phase`
and `outcome` plus `reason`. The review pass allows `advance` (PASS) or `retry`
(findings to fix) and adds `findings`; an `advance` that lists a blocker or
warning is not a valid result. Because the result travels on stdout, a
read-only harness that cannot write `result.json` still reports.

A non-zero exit, no result line, a line that is not exactly one JSON object, or
a result for another phase is a **failed run**, not a finding. `self-review`
says in its output that the routed review did not run and reviews in-session; a
failed run is never read as a PASS. That fallback is the one place the pinned
reviewer model is not used, and it is always announced.

### Trust

A routed invocation stands in for the subagent the phase would otherwise
delegate to, and gets no more trust than that agent. The trust levels are the
agent trust table in [AGENTS.md](../AGENTS.md) (`## Meta`); this doc does not
keep a second copy. The routed review pass stands in for `sdlc-reviewer`, so
its harness `cmd` should be read-only. `tkt` cannot inspect a harness's
permission flags, so this is the project's responsibility when it writes
`[harnesses]`.

One `[routes].self-review` entry serves both consumers, and they want different
things from it:

- The interactive review pass only reads, so a read-only `cmd` is the right
  fit, and the sandbox enforces what the prompt asks.
- `tkt run` runs the **whole** self-review phase through that `cmd`, fixes
  included. With a read-only `cmd`, `tkt run` blocks the ticket on any
  finding: each pass reports `retry`, nothing can be fixed, and after
  `[run].max_phase_attempts` the driver moves the ticket to `blocked`.

Pick by how the project drives the pipeline. Interactive only: use a read-only
`cmd`. `tkt run` (alone or alongside interactive use): give the route a
write-capable `cmd`, or leave `self-review` unrouted for `tkt run`. With a
write-capable `cmd`, the interactive review pass relies on its prompt, which
tells the reviewer to modify no file, rather than on a sandbox.

## Per-harness support

| Harness | In-session tier mechanism | Enforcement | Route target (`[harnesses]`) |
|---------|---------------------------|-------------|------------------------------|
| **Claude Code** | `.claude/agents/*.md` frontmatter `model:` | Per subagent | Yes: `claude -p {prompt} --model {model}` |
| **Kiro CLI** | `.kiro/agents/*.json` `"model"` field | Per agent | Yes: `kiro-cli chat --no-interactive {prompt}` |
| **Kiro IDE** | `.kiro/agents/*.md` frontmatter `model:` | Per agent | No CLI; reached through Kiro CLI |
| **Codex** | — | Advisory only; session default | Yes: `codex exec --model {model} --sandbox read-only {prompt}` |
| Cursor, Gemini CLI, Windsurf, Cline, Continue, Augment, OpenCode, Antigravity, GitHub Copilot | — | Advisory only; session default | Any of them with a non-interactive CLI that takes the prompt as an argument |

The first three columns cover choosing a model *inside* the running harness.
The last covers sending a phase *out* to another harness: any command line that
runs one prompt non-interactively, exits, and prints to stdout can be a
`[harnesses]` entry, whichever harness is driving. The examples show the
invocation shape only (the Codex one with its read-only sandbox); a route for
the review pass needs the harness's own read-only or sandbox flags, and the
flags are the harness vendor's to define, so check its CLI help.

**Skills** carry only `model_tier` — the orchestrator resolves it through
`tkt cfg route <phase>` (which falls back to `[models]`), so nothing needs
rewriting at sync time.

**Agents** carry both, because the enforcing harnesses read the frontmatter
themselves and a tier name means nothing to them:

```yaml
model_tier: deep      # portable statement of intent
model: opus           # harness-native default, safe to override
```

The shipped `model:` values are generation-stable aliases (`haiku` / `sonnet` /
`opus`) rather than pinned IDs, so they don't rot with each model release. A
consumer who wants a specific build edits the synced `.claude/agents/*.md` copy —
`sync-pack --check` will then report that file as drifted, which is the intended
signal, not an error.

On advisory harnesses, `model_tier` is documentation: the operator can select a
model manually before invoking a skill. The pipeline never blocks or fails because
a model is unavailable.

## ID spelling

Model identifiers are spelled differently per harness, so keep the mapping in
config rather than in skill text:

| Context | Format | Example |
|---------|--------|---------|
| `[models]` in `.sdlc/config.toml` | whatever your harness accepts | `claude-opus-5` |
| Kiro agents (JSON + MD) | dotted | `claude-opus-4.8` |
| Claude Code agents | alias or full ID | `opus`, `claude-opus-5` |

The example configs ship dashed Anthropic IDs as a starting point. Replace them
with the identifiers your harness and provider actually accept.
