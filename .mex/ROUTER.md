---
name: router
description: Session bootstrap and navigation hub. Read at the start of every session before any task. Contains project state, routing table, and behavioural contract.
edges:
  - target: context/architecture.md
    condition: when working on system design, integrations, or understanding how components connect
  - target: context/stack.md
    condition: when working with specific technologies, libraries, or making tech decisions
  - target: context/conventions.md
    condition: when writing new code, reviewing code, or unsure about project patterns
  - target: context/decisions.md
    condition: when making architectural choices or understanding why something is built a certain way
  - target: context/setup.md
    condition: when setting up the dev environment or running the project for the first time
  - target: context/adapter-contract.md
    condition: when the work touches adapters/ or you need the exact verb signatures and normalized shapes
  - target: context/run-driver.md
    condition: when the work touches `tkt run`, phase state, gates, or headless pipeline execution
  - target: context/skill-pack.md
    condition: when the work touches skills/, agents/, or how the pack is distributed to consumer repos
  - target: patterns/INDEX.md
    condition: when starting a task — check the pattern index for a matching pattern file
last_updated: 2026-08-10
---

# Session Bootstrap

If you haven't already read `AGENTS.md`, read it now — it contains the project identity, non-negotiables, and commands.

Then read this file fully before doing anything else in this session.

## Current Project State

**Working:**
- The full verb contract across five adapters — `jira`, `github`, `linear`, `markdown`,
  `openkanban` — all normalizing into one `Ticket`/`Worklog`/`Check` JSON shape.
- The SDLC skill pack: 20 skills in `skills/`, 6 trust-scoped subagents in `agents/`,
  and translations for eleven harnesses.
- `tkt sync-pack` — committed-copy distribution into consumer repos, with a tracked
  manifest, a managed `AGENTS.md` block, `--check` staleness reporting, and additive
  sticky harness selection.
- `tkt init` — scaffolds `.sdlc/` from `examples/config.<provider>.toml` and seeds the
  `[build]` table from whatever the target project already declares.
- `tkt run` — the external loop driver: one pipeline phase per harness invocation, with
  resume markers, a STOP file, iteration and attempt caps, and three enforced gates.
- 58 offline unit tests (`tests/test_run.py`, `tests/test_jira_adf.py`) plus
  `scripts/smoke-sync-pack.sh`, all runnable with a bare Python 3.11+ interpreter.

**Not yet built:**
- No CI. `.github/` contains only Copilot prompt translations — there are no workflows,
  so nothing runs the tests or the smoke script automatically.
- No packaging or distribution. There is no packaging manifest, no build script, and
  no Makefile in the tree; installation is clone-and-symlink.
- No adapter tests. The suite covers the run driver's state machine and the Jira
  Markdown→ADF converter; every code path under `adapters/` is validated by hand
  against a live backend.
- `agent_status` is persisted only by the `markdown` adapter; the other four leave it
  at its default.
- The verb contract has no "list comments" verb, which is why run-state resume needs a
  local mirror on every backend except `markdown`.

**Known issues:**
- **The root `CLAUDE.md` is out of date.** It states there is no test suite (there are
  58 tests), does not mention `core/run.py` or the `tkt run` verb at all, and claims
  `README.md` misfiles `query.py` under `adapters/` — `README.md` has since been fixed
  and now correctly shows it under `core/`. Treat `AGENTS.md` and this scaffold as
  authoritative where they disagree.
- `README.md`'s architecture block omits `core/pack.py` and `core/run.py`.
- `python3 -m unittest discover` does not work — `tests/` has no `__init__.py`. Name
  the modules explicitly.
- This repo's own `.sdlc/config.toml` is gitignored local dogfooding state, and its
  `[build].test` is a `true` placeholder rather than the real unittest command.
- `.mex/` is currently untracked and not listed in `.gitignore`; decide which it
  should be before committing.

## Routing Table

Load the relevant file based on the current task. Always load `context/architecture.md` first if not already in context this session.

| Task type | Load |
|-----------|------|
| Understanding how the system works | `context/architecture.md` |
| Working with a specific technology | `context/stack.md` |
| Writing or reviewing code | `context/conventions.md` |
| Making a design decision | `context/decisions.md` |
| Setting up or running the project | `context/setup.md` |
| Anything inside `adapters/`, or the verb/schema contract | `context/adapter-contract.md` |
| `tkt run`, phases, gates, resume markers | `context/run-driver.md` |
| `skills/`, `agents/`, `sync-pack`, harness translations | `context/skill-pack.md` |
| Any specific task | Check `patterns/INDEX.md` for a matching pattern |

## Behavioural Contract

For every task, follow this loop:

1. **CONTEXT** — Load the relevant context file(s) from the routing table above. Check `patterns/INDEX.md` for a matching pattern. If one exists, follow it. Narrate what you load: "Loading architecture context..."
2. **BUILD** — Do the work. If a pattern exists, follow its Steps. If you are about to deviate from an established pattern, say so before writing any code — state the deviation and why.
3. **VERIFY** — Load `context/conventions.md` and run the Verify Checklist item by item. State each item and whether the output passes. Do not summarise — enumerate explicitly.
4. **DEBUG** — If verification fails or something breaks, check `patterns/INDEX.md` for a debug pattern. Follow it. Fix the issue and re-run VERIFY.
5. **GROW** — After meaningful work, run this binary checklist:
   - **Ground:** What changed in reality? Name the changed behavior, system, command, dependency, or workflow.
   - **Record:** If project state changed, update the "Current Project State" section above. If documented facts changed, update the relevant `context/` file surgically.
   - **Orient:** If this task can recur and no pattern exists, create one in `patterns/` using `patterns/README.md`, then add it to `patterns/INDEX.md`.  If a pattern exists but you learned a gotcha, update it.
   - **Write:** Bump `last_updated` in every scaffold file you changed. If the why matters, run `mex log --type decision "<what changed and why>"` or `mex log "<note>"`.
