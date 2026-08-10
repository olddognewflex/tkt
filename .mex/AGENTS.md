---
name: agents
description: Always-loaded project anchor. Read this first. Contains project identity, non-negotiables, commands, and pointer to ROUTER.md for full context.
last_updated: 2026-08-10
---

# tkt

## What This Is

A provider-agnostic ticketing/board CLI (pure-stdlib Python 3.11+) plus a portable SDLC skill pack, letting one set of skills drive Jira, GitHub, Linear, openkanban, or local markdown unchanged.

## Non-Negotiables

- **No third-party dependencies, ever.** Stdlib only; there is no install step, no `pyproject.toml`, and adding an import breaks the project's core promise.
- **`core/` never imports an adapter**, and no backend name appears in `core/` or `skills/`. The only crossing point is the lazy import in `core/registry.py`.
- **Skills speak canonical roles and `tkt cfg` lookups** — never lane strings, repo names, or build commands.
- **Every failure raises a typed `TktError`** so the exit code is right (2 config / 3 provider / 4 not-found / 64 usage). Never print an error and return 0.
- **Adapters return schema dataclasses**, leaving unsupported fields at their defaults rather than faking values.

## Commands

- Test: `python3 -m unittest tests.test_run tests.test_jira_adf` (58 tests; `discover` does not work — no `tests/__init__.py`)
- Smoke: `bash scripts/smoke-sync-pack.sh`
- Validate a project: `./tkt doctor`
- Inspect the normalized shape: `./tkt view KEY --json`
- Build/lint: none exist. There is no build step, no linter config, and no CI.

## Code Graph
The repo is indexed into `.mex/graph.db`. Use it to avoid re-reading code you already have — it is one tool alongside Grep/Glob, not a replacement for them.
- If you know the symbol name, go straight to it: `mex graph query <who-calls|what-calls|where-defined> <symbol>` and `mex graph get <id>` are exact and cheap. This is the strongest part of the graph. Give it exact names — an approximate name can return a confident wrong match.
- Exploring an unfamiliar task? `mex graph scope "<task>"` returns a compact JSONL manifest (`meta`, `fact`s, `summary`). Scope matches on words, not meaning: if your phrasing does not share vocabulary with the code, results will be weak. Treat it as a starting point, never as a complete answer.
- If the manifest does not clearly contain what you need, use Grep/Glob instead. Do not expand node ids that look irrelevant, and do not re-run `scope` with reworded phrasing more than once — that costs more than searching directly.
- Treat any source the graph DOES return as ALREADY READ; do not re-open those files.
- Pick 1-3 relevant node ids from the manifest and expand only those with `mex graph get <id> --detail source`.
- Before editing a symbol, run `mex impact <symbol|file>` to see affected callers and scaffold memory.
- If a result is `truncated`, do NOT repeat the broad query — narrow the task or use the summary's `suggestedNextCommands`. Scale through a few focused calls, never one giant response.
- During `mex sync`, adjudicate any AMBIGUOUS grounding; after repairs, ensure the refreshed grounding is re-emitted.

## Scaffold Growth
After meaningful work, run GROW:
- Ground: what changed in reality?
- Record: update `ROUTER.md` and relevant `context/` files
- Orient: create or update a `patterns/` runbook if this can recur
- Write: bump `last_updated` on changed scaffold files and run `mex log` when rationale matters

The scaffold grows from real work, not just setup. See the GROW step in `ROUTER.md` for details.

## Navigation
At the start of every session, read `ROUTER.md` before doing anything else.
For full project context, patterns, and task guidance — everything is there.
