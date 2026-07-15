# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Read `AGENTS.md` first.** It is the harness-facing contract: the verb reference,
role vocabulary, exit codes, portability rules, and the skill pack inventory. This
file covers only what `AGENTS.md` deliberately leaves out — the internals of this
repo and how to change them.

## What this is

`tkt` is a provider-agnostic ticketing/board CLI plus a portable SDLC skill pack.
It decouples **process logic** (what step comes next, in the skills) from
**provider calls** (how to talk to Jira/GitHub/etc., in the adapters). `README.md`
has the full verb contract and per-provider notes.

Pure stdlib, Python 3.11+ (uses `tomllib`). No third-party deps, no build step.

## Commands

```sh
./tkt <verb> ...              # run the CLI from the repo (or symlink ./tkt onto PATH)
./tkt doctor                  # validate a project's auth + reachability + board model
./tkt view KEY --json         # normalized ticket shape skills parse
./tkt init --provider markdown --link-skills --sample   # scaffold a project's .sdlc/
./tkt sync-pack --check --dir ../consumer   # is a consumer repo's pack copy current?
```

There is **no test suite, linter config, or Makefile** in this repo. "Validation"
of an adapter means running `tkt doctor` / the read verbs live against a real backend
(see each provider's "Validation status" in `README.md`). When adding code, exercise
it by running the actual verbs against a configured backend rather than expecting CI.
`scripts/smoke-sync-pack.sh` is the one exception — it smoke-tests `sync-pack`.

`tkt` locates its own `core/`/`adapters/` relative to the script (`tkt:12`), so it
runs from any cwd and a PATH symlink is fine.

## Architecture

Two layers, connected only by the verb contract and the normalized schema:

- **`core/`** — provider-independent plumbing.
  - `cli.py` — argparse verb parsing, dispatch, and output formatting. `--json` works
    on either side of the verb. `init`/`sync-pack`/`lane`/`cfg` are handled **before**
    loading an adapter (`init` and `sync-pack` run before any config exists; `lane`/`cfg`
    are pure config reads). Exception: `cfg priorities` is backend-aware, so it does
    load an adapter. Input validation for dates and `--agent-status` lives here so a
    typo fails before it reaches a backend.
  - `config.py` — loads `.sdlc/config.toml`. Discovery order: `--config` → `$TKT_CONFIG`
    → nearest `.sdlc/config.toml` walking up from cwd. Owns role↔lane mapping, issue-type
    routing (`full_sdlc` vs `deliverable`), named queries, the backend-agnostic priority
    ordering, and dotted-path `get()`.
  - `registry.py` — `provider name → (module, class)`, **lazy-imported** so one adapter's
    missing optional dep can't break the others.
  - `schema.py` — `Ticket` / `Worklog` / `Check` dataclasses + `to_dict()` (the JSON shape).
  - `query.py` — shared tiny **JQL-subset** evaluator, used by adapters with no native
    query language (markdown, linear, openkanban). (Note: `README.md`'s architecture
    block lists this under `adapters/`; it actually lives in `core/`.)
  - `ticketdoc.py` — the canonical full-ticket markdown document (frontmatter + body)
    that `tkt apply` ingests and the `$EDITOR` flow round-trips. Backend-agnostic:
    adapters map the parsed result onto their own storage.
  - `pack.py` — implements `tkt sync-pack` and its `doctor` check. Copies pack files
    into a consumer tree, tracks them in `.sdlc/pack-manifest.json`, and maintains a
    marker-delimited managed block in the consumer's `AGENTS.md`. Never deletes, never
    writes outside recorded paths, and is idempotent.
  - `errors.py` — typed errors → exit codes (see `AGENTS.md` for the table). Errors
    always go to stderr with a non-zero exit so skills branch on codes.
  - `toolchain.py` — best-effort detection of a project's build/test/typecheck/lint
    commands (package.json scripts, Cargo.toml, go.mod, pyproject.toml, Makefile)
    plus the `[build]`-table rewrite `init` applies to the copied example config.
    Advisory only: a key the project doesn't declare keeps the example's value.
  - `scaffold.py` — implements `tkt init`.
- **`adapters/`** — one file per backend, each subclassing `adapters/base.Adapter`.
  - `base.py` is the contract: required `@abstractmethod` verbs (whoami, list, view,
    transition, comment, blockers, worklog, lane_time, doctor) plus **optional** verbs
    (`create`, `apply`, `edit`, `link`) that default to a clear "not supported"
    `ProviderError` so a backend opts in without breaking instantiation.
  - Implementations: `jira.py` (acli + Jira REST + Tempo), `github.py` (Issues + Projects v2
    or `Status:` labels, all via `gh`), `linear.py` (GraphQL), `openkanban.py` (local JSON
    store), `markdown.py` (one `<KEY>.md` per ticket + JSONL state sidecars).

### Roles, not lane names

Skills reference canonical **roles**; `[board.roles]` in config maps each role to the
provider's literal lane string. This is the core abstraction that lets one skill drive
an 8-lane Jira board and a 3-column markdown board unchanged. Role vocabulary and usage
are in `AGENTS.md`.

### Ticket schema notes

Beyond the obvious fields, `Ticket` carries `status` (provider lane, verbatim) alongside
`status_role` (canonical role), `type_class` (`full_sdlc` | `deliverable` | `unknown`),
the date fields `due` / `scheduled` / `completed`, and `agent_status` (board agent state:
`idle` | `processing` | `waiting` | `done` | `blocked`, or empty). Adapters that can't
store a field leave it at its default rather than faking it.

## Adding a provider

1. Implement `adapters/base.Adapter` in `adapters/<name>.py` (all abstract verbs;
   `create`/`apply`/`edit`/`link` only if the backend supports them).
2. Register it in `core/registry.py`'s `_PROVIDERS`.
3. Add `examples/config.<name>.toml`.

No skill changes are ever required — that's the point of the split.

## Editing the skill pack

The rules live in `AGENTS.md` ("Editing skills"). The short version: no backend
specifics in skill text, read every toolchain/VCS/deploy value through `tkt cfg`, and
remember that a skill's frontmatter `description` feeds the generated `AGENTS.md` block
that `sync-pack` writes into consumer repos.
