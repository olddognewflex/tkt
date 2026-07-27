# Model routing

Three-tier routing policy for the SDLC pipeline. Each phase runs on the cheapest
tier that preserves quality for that phase's workload.

Routing is **advisory everywhere**. No skill requires a model switch to produce
correct output — a harness with no model control runs the whole pipeline on its
session default and must behave identically.

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

The orchestrator resolves a tier at invocation time:

```shell
tkt cfg models.deep        # -> claude-opus-5
```

This is the same rule the pack applies to every other project-specific value
(`build.*`, `vcs.*`): the skill names *what it needs*, config names *what it is*.
Model IDs change far faster than the pipeline does, so pinning them in skill text
would mean editing 20 files per model generation across every consumer repo.

`[models]` is optional. If the table is absent, `tkt cfg models.<tier>` exits 4
(`NotFoundError`) — treat that as "no routing configured" and proceed on the
session default.

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

Escalation is per-invocation, not sticky. After a successful retry, subsequent
phases resume at their assigned tier.

## Per-harness support

| Harness | Mechanism | Enforcement |
|---------|-----------|-------------|
| **Claude Code** | `.claude/agents/*.md` frontmatter `model:` | Per subagent |
| **Kiro CLI** | `.kiro/agents/*.json` `"model"` field | Per agent |
| **Kiro IDE** | `.kiro/agents/*.md` frontmatter `model:` | Per agent |
| Codex, Cursor, Gemini CLI, Windsurf, Cline, Continue, Augment, OpenCode, Antigravity, GitHub Copilot | — | Advisory only; session default |

`sync-pack` ships the pack's canonical `model_tier` frontmatter verbatim; it does
**not** rewrite it into a harness-native `model:` field, because the resolved ID
depends on the consumer's `[models]` table. On the enforcing harnesses above, add
the `model:` key to your synced `.claude/agents/*.md` (or `.kiro/agents/*`) copies
with whatever your `[models]` table resolves to, then re-run `sync-pack --check`
to confirm nothing else drifted.

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
