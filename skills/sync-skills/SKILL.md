---
name: sync-skills
description: 'When a skill is created or updated in skills/, generate the translated versions for all other agent/IDE platforms.'
---

# Sync Skills

When a new skill is created (or an existing skill is significantly updated) in
`skills/<name>/SKILL.md`, generate equivalent files for all supported agent
platforms.

## Source of Truth

`skills/<name>/SKILL.md` is always the canonical version. All other locations are
derived from it.

## Target Locations

### Tier A — Per-skill invocable workflows (sync every skill)

These tools support discrete, invocable workflows. Every skill (except meta/internal
ones — see "When NOT to Sync") gets a copy here.

| # | Location | Format | Used By |
|---|----------|--------|---------|
| 1 | `.agents/skills/<name>/SKILL.md` | Agent Skills (YAML + MD) | Antigravity, JetBrains Junie |
| 2 | `.kiro/skills/<name>/SKILL.md` | Agent Skills (YAML + MD) | AWS Kiro |
| 3 | `.github/prompts/<name>.prompt.md` | Copilot Prompt (YAML + MD) | GitHub Copilot |
| 4 | `.gemini/commands/<name>.toml` | TOML | Gemini CLI |

### Tier B — Selective per-skill workflows (sync the high-value ~7 only)

These tools support invocable workflows but the spec favors a curated set. Sync the
core SDLC pipeline skills: `select-ticket`, `triage-ticket`, `plan-ticket`,
`open-pr`, `self-review`, `respond-to-review`, `deploy-ready`.

| Location | Format | Used By |
|----------|--------|---------|
| `.cursor/skills/<name>/SKILL.md` | Skill MD + YAML | Cursor |
| `.cursor/commands/<name>.md` | Plain command MD | Cursor |
| `.windsurf/workflows/<name>.md` | Workflow MD | Windsurf |
| `.clinerules/workflows/<name>.md` | Workflow MD | Cline |
| `.continue/prompts/<name>.prompt` | Prompt MD + YAML | Continue |
| `.augment/commands/<name>.md` | Command MD | Augment |
| `.agents/workflows/<name>.md` | Workflow MD (`// turbo` markers) | Antigravity |
| `.opencode/commands/<name>.md` | Command MD (`description` frontmatter; `$ARGUMENTS`; `!` backtick shell injection) | OpenCode |

### Tier C — Rules-style targets (NOT per-skill; only when global conventions change)

These tools consume project-wide rule files. **When a skill changes, do NOT sync to
these locations.** Only update them if tkt conventions or project-wide guardrail
policy change.

| Location | Format | Used By |
|----------|--------|---------|
| `AGENTS.md` (root) | MD | Codex, Codex Cloud, Devin, T3Code, Zed, Amp, v0/Vercel, Cursor, Windsurf, Aider, Gemini CLI, Jules, Factory, Warp, Junie, Kilo, Augment, Ona, Phoenix, UiPath, Semgrep, Lovable, OpenCode |
| `.cursor/rules/*.mdc` | Cursor MDC | Cursor |
| `.windsurf/rules/*.md` | YAML + MD (`trigger:`) | Windsurf |
| `.clinerules/*.md` | YAML + MD (`paths:`) | Cline |
| `.continue/rules/*.md` | YAML + MD (`globs:`) | Continue.dev |
| `.augment/rules/*.md` | YAML + MD (`type:`) | Augment Code |
| `.agents/rules/*.md` | YAML + MD (`trigger:`) | Antigravity |

Root `AGENTS.md` is the universal fallback. Sub-`AGENTS.md` cascading is honored by
Codex, Kilo, Amp, Factory, and Windsurf; other tools fall back gracefully to root.
OpenCode reads root `AGENTS.md` natively.

### Tier D — Programmatic surfaces (NOT per-skill; configure once)

These are per-tool programmatic config, not skill translations. Update only when
guardrail policy or MCP servers change.

| Location | Purpose |
|----------|---------|
| `.claude/settings.json` | Claude Code hooks, permissions, statusLine |
| `.cursor/hooks.json` + `.cursor/scripts/` | Cursor guardrail hooks |
| `.cursor/mcp.json` | Cursor MCP |
| `.windsurf/hooks.json` + `.windsurf/scripts/` | Windsurf guardrail hooks |
| `.windsurf/mcp_config.json` | Windsurf MCP |
| `.continue/agents/*.yaml` | Continue agent personas |
| `.augment/settings.json` | Augment MCP |
| `.codex/config.toml` | Codex CLI approval policy |
| `.openhands/microagents/repo.md` | OpenHands runtime bootstrap |

## Format Templates

### 1. Agent Skills Standard (`.agents/skills/` and `.kiro/skills/`)

Identical to the source format:

```markdown
---
name: <skill-name>
description: <one-line description from source>
---

# <Title>

<body — same markdown content as source skill>
```

### 2. GitHub Copilot Prompt (`.github/prompts/<name>.prompt.md`)

Condensed version with Copilot-specific frontmatter:

```markdown
---
mode: agent
description: '<one-line description>'
tools: ['search/codebase', 'terminal', 'file']
---

# <Title>

<condensed instructions — keep under ~60 lines>
```

Preserve the provider-agnostic rule: all ticketing goes through `tkt`, all
repo/toolchain values come from `.sdlc/config.toml` via `tkt cfg`.

### 3. Gemini CLI Command (`.gemini/commands/<name>.toml`)

```toml
description = "<one-line description>"

prompt = """
<instruction text>

## Steps

1. <step 1>
2. <step 2>
...
"""
```

Use `{{args}}` for user-provided input. Use `!{<cmd>}` for shell expansions
providing context (e.g. `!{git status --short}`). Use `@{path}` for file
inclusions. No YAML, no markdown frontmatter — pure TOML.

### 4. Cursor Skill (`.cursor/skills/<name>/SKILL.md`)

```markdown
---
name: <skill-name>
description: <one-line description>
disable-model-invocation: true  # invoke as slash command, not auto
---

# <Title>

<condensed instructions, ~40-60 lines>
```

### 5. Windsurf / Cline / Augment Workflow

Plain markdown, ≤12KB. Slash-invokable as `/<name>`. No frontmatter required for
Cline/Augment. Windsurf accepts optional frontmatter.

### 6. Continue Prompt (`.continue/prompts/<name>.prompt`)

```markdown
---
name: <skill-name>
description: <one-line description>
invokable: true
---

# <Title>

<condensed instructions>
```

### 7. Antigravity Workflow (`.agents/workflows/<name>.md`)

```markdown
---
name: <skill-name>
description: <one-line description>
trigger: manual
---

# <Title>

<condensed instructions. Mark safe-to-auto commands with `// turbo` comment lines.>
```

### 8. OpenCode Command (`.opencode/commands/<name>.md`)

```markdown
---
description: <one-line description>
---

<condensed instructions. Use `$ARGUMENTS` (all args) or `$1`, `$2` for positional
input, and a bang-prefixed backtick command (`!<cmd>`) to inject live shell output,
e.g. `!git status --short`.>
```

Markdown body is the prompt template. Optional frontmatter keys: `agent`, `model`,
`subtask`. Guardrails are enforced separately by project config — not per-command.

## Steps to Sync

### 1. Identify the source skill

Read `skills/<name>/SKILL.md` — extract `name`, `description`, and body.

### 2. Tier A: byte-identical copies (always)

Copy to:
- `.agents/skills/<name>/SKILL.md`
- `.kiro/skills/<name>/SKILL.md`

### 3. Tier A: condensed translations (always)

Translate to:
- `.github/prompts/<name>.prompt.md` (~40-60 lines, `mode: agent` + `tools` array)
- `.gemini/commands/<name>.toml` (TOML format, `{{args}}` for input)

### 4. Tier B: selective workflows (only for high-value skills)

If the skill is one of `select-ticket`, `triage-ticket`, `plan-ticket`, `open-pr`,
`self-review`, `respond-to-review`, `deploy-ready`, also create:
- `.cursor/skills/<name>/SKILL.md` + `.cursor/commands/<name>.md`
- `.windsurf/workflows/<name>.md`
- `.clinerules/workflows/<name>.md`
- `.continue/prompts/<name>.prompt`
- `.augment/commands/<name>.md`
- `.agents/workflows/<name>.md` (with `// turbo` markers)
- `.opencode/commands/<name>.md`

### 5. Verify

```shell
NAME=<skill-name>
for p in skills/$NAME/SKILL.md .agents/skills/$NAME/SKILL.md .kiro/skills/$NAME/SKILL.md .github/prompts/$NAME.prompt.md .gemini/commands/$NAME.toml; do
  test -f "$p" && echo "✓ $p" || echo "✗ $p MISSING"
done
```

For high-value skills additionally verify:
```shell
for p in .cursor/skills/$NAME/SKILL.md .cursor/commands/$NAME.md .windsurf/workflows/$NAME.md .clinerules/workflows/$NAME.md .continue/prompts/$NAME.prompt .augment/commands/$NAME.md .agents/workflows/$NAME.md .opencode/commands/$NAME.md; do
  test -f "$p" && echo "✓ $p" || echo "✗ $p MISSING"
done
```

## Current Skill Inventory

`tkt` skills are the canonical source. Tier A targets get every non-meta skill.

| Skill | Source | Agents | Kiro | Copilot | Gemini | Tier B? |
|-------|--------|--------|------|---------|--------|---------|
| automated-sdlc | ✓ | ✓ | ✓ | ✓ | ✓ | |
| check-blockers | ✓ | ✓ | ✓ | ✓ | ✓ | |
| ci-fix | ✓ | ✓ | ✓ | ✓ | ✓ | |
| complete-deliverable | ✓ | ✓ | ✓ | ✓ | ✓ | |
| deploy-preview | ✓ | ✓ | ✓ | ✓ | ✓ | |
| deploy-ready | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| hotfix-revert | ✓ | ✓ | ✓ | ✓ | ✓ | |
| open-pr | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| plan-ticket | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| respond-to-review | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| resume-from-revise | ✓ | ✓ | ✓ | ✓ | ✓ | |
| select-ticket | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| self-review | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| triage-ticket | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

`sync-skills` itself is meta — only in `skills/`.

**Tier B coverage**: the seven ✓ skills above should also exist in `.cursor/skills/`,
`.cursor/commands/`, `.windsurf/workflows/`, `.clinerules/workflows/`,
`.continue/prompts/`, `.augment/commands/`, `.agents/workflows/`, and
`.opencode/commands/`.

## When NOT to Sync

- **Meta/internal skills** (like this one) — only in `skills/`.
- **Skills that use harness-specific features** — adapt or skip for other platforms.
- **Trivial updates** (typo fixes) — sync only if the fix is in commands or steps that
  other platforms also show.
- **Per-skill changes** — never propagate to Tier C (rules-style targets) or Tier D
  (programmatic surfaces). Those only change when global conventions or guardrail
  policy change.

## Portability Rules for All Translations

1. **No backend specifics.** Skills must call `tkt view`, `tkt transition`, etc.,
   never `acli`, `gh issue`, Jira REST, or Linear GraphQL directly.
2. **No repo/toolchain hardcoding.** Read values through `tkt cfg`
   (e.g. `tkt cfg vcs.repo`, `tkt cfg build.test --pkg X`).
3. **Speak in roles, not lane names.** Use `in_progress`, `review`, `done`, etc.;
   resolve literal lane names with `tkt lane <role>` when needed.
4. **Keep `type_class` branching.** `full_sdlc` vs `deliverable` drives whether a
   ticket stops at `complete-deliverable` or runs the full PR/review/deploy loop.
5. **Respect `tkt worklog` / `tkt lane-time` no-ops.** Backends with
   `[timetracking].provider = "none"` return empty worklogs; comments should still
   read naturally.

## Platforms Explicitly Skipped (and Why)

| Platform | Why skipped |
|----------|-------------|
| **Roo Code** | Service sunset 2026-05-15. Successor: **Kilo Code** (covered via root `AGENTS.md`). |
| **T3Code** | Wraps OpenAI Codex CLI — covered via `AGENTS.md` + `.codex/`. |
| **Sweep** | Adoption declining; uses `sweep.yaml` if revisited. |
| **Replit Agent / v0 / bolt.new** | SaaS-only; honor `AGENTS.md` manually if needed. |
| **Qodo** | PR-review only; would use `.pr_agent.toml` if adopted. |
| **Aide / Codestory** | No documented project-rules file as of June 2026. |
| **Sourcegraph Cody** | Maintenance mode; strategic agent is Amp (covered by `AGENTS.md`). |
| **Tabnine / Tabby** | No team adoption; project-rules surfaces exist but server-side only. |
| **Lovable / GitHub Spark** | Covered by `AGENTS.md` (Lovable) or Copilot config (Spark). |
