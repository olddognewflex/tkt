# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`tkt` is a provider-agnostic ticketing/board CLI plus a portable SDLC skill pack.
The pack's skills speak **semantic verbs** (`tkt view`, `tkt transition`, ...) and
never touch a concrete backend; `tkt` reads `.sdlc/config.toml`, dispatches to the
configured provider adapter, and normalizes every backend into one JSON ticket shape.
This decouples **process logic** (what step comes next, in the skills) from
**provider calls** (how to talk to Jira/GitHub/etc., in the adapters). See `PLAN.md`
for the coupling inventory that motivated the split, and `README.md` for the full
verb contract and per-provider notes.

Pure stdlib, Python 3.11+ (uses `tomllib`). No third-party deps, no build step.

## Commands

```sh
./tkt <verb> ...              # run the CLI from the repo (or symlink ./tkt onto PATH)
./tkt doctor                  # validate a project's auth + reachability + board model
./tkt view KEY --json         # normalized ticket shape skills parse
./tkt init --provider markdown --link-harness claude --sample   # scaffold a project's .sdlc/
```

Use `--link-harness opencode` for OpenCode, `--link-harness all` for every known
harness, or `--global` to install into user harness config instead of the project.
`--link-skills` is deprecated and now means `--link-harness claude`.

There is **no test suite, linter config, or Makefile** in this repo. "Validation"
of an adapter means running `tkt doctor` / the read verbs live against a real backend
(see each provider's "Validation status" in `README.md`). When adding code, exercise
it by running the actual verbs against a configured backend rather than expecting CI.

`tkt` locates its own `core/`/`adapters/` relative to the script (`tkt:12`), so it
runs from any cwd and a PATH symlink is fine.

## Architecture

Two layers, connected only by the verb contract and the normalized schema:

- **`core/`** — provider-independent plumbing.
  - `cli.py` — argparse verb parsing, dispatch, and output formatting. `--json` works
    on either side of the verb. `init`/`lane`/`cfg` are handled **before** loading an
    adapter (`init` runs before any config exists; `lane`/`cfg` are pure config reads).
  - `config.py` — loads `.sdlc/config.toml`. Discovery order: `--config` → `$TKT_CONFIG`
    → nearest `.sdlc/config.toml` walking up from cwd. Owns role↔lane mapping, issue-type
    routing (`full_sdlc` vs `deliverable`), named queries, and dotted-path `get()`.
  - `registry.py` — `provider name → (module, class)`, **lazy-imported** so one adapter's
    missing optional dep can't break the others.
  - `schema.py` — `Ticket` / `Worklog` / `Check` dataclasses + `to_dict()` (the JSON shape).
  - `query.py` — shared tiny **JQL-subset** evaluator, used by adapters with no native
    query language (markdown, linear, openkanban). (Note: `README.md`'s architecture
    block lists this under `adapters/`; it actually lives in `core/`.)
  - `errors.py` — typed errors → exit codes: **2** config, **3** provider, **4** not-found,
    **64** usage. Errors always go to stderr with a non-zero exit so skills branch on codes.
  - `scaffold.py` — implements `tkt init`.
- **`adapters/`** — one file per backend, each subclassing `adapters/base.Adapter`.
  - `base.py` is the contract: required `@abstractmethod` verbs (whoami, list, view,
    transition, comment, blockers, worklog, lane_time, doctor) plus **optional** verbs
    (`create`, `link`) that default to a clear "not supported" `ProviderError` so a
    backend opts in without breaking instantiation.
  - Implementations: `jira.py` (acli + Jira REST + Tempo), `github.py` (Issues + Projects v2
    or `Status:` labels, all via `gh`), `linear.py` (GraphQL), `openkanban.py` (local JSON
    store), `markdown.py` (one `<KEY>.md` per ticket + JSONL state sidecars).

### Roles, not lane names

Skills reference canonical **roles** (`in_progress`, `review`, `qa_ready`, `done`,
`blocked`, ...). `[board.roles]` in config maps each role to the provider's literal lane
string. This is the core abstraction that lets one skill drive an 8-lane Jira board and a
3-column markdown board unchanged. In skills, use `tkt transition KEY review` to move and
`$(tkt lane review)` when you need the literal lane name.

## Adding a provider

1. Implement `adapters/base.Adapter` in `adapters/<name>.py` (all abstract verbs;
   `create`/`link` only if the backend supports them).
2. Register it in `core/registry.py`'s `_PROVIDERS`.
3. Add `examples/config.<name>.toml`.

No skill changes are ever required — that's the point of the split.

## Skill pack (`skills/`, `agents/`, `commands/`)

The `skills/*/SKILL.md` files are the provider-agnostic SDLC pipeline (select → triage →
plan → open-pr → ci-fix → self-review → respond-to-review → deploy, orchestrated by
`automated-sdlc`). They depend only on `tkt` + `.sdlc/config.toml`. Two rules when editing
them:

- **No backend specifics in skill text.** Speak in roles, and read all toolchain/VCS/deploy
  settings through `tkt cfg` (e.g. `tkt cfg build.test --pkg X`,
  `tkt cfg vcs.branch_fmt --ticket K --slug s`). Never hardcode Jira/pnpm/GitHub/repo names.
- VCS (PR/CI/merge via `gh`) and infra (preview/deploy) are still GitHub/cloud-shaped, but
  repo/branch/reviewer/workflow values come from config; genuinely infra-specific steps are
  marked `PROJECT-SPECIFIC` in the skill text.
