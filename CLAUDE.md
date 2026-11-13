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
The one exception is `enforcement/`: stdlib-only Node checker scripts (Node 20+,
CI runs 24; no npm packages) that CI self-tests. `tkt` itself never requires
Node: the format gates (`core/gates.py`) shell out to it when it is on `PATH`
and stay inactive when it is not.

## Commands

```sh
./tkt <verb> ...              # run the CLI from the repo (or symlink ./tkt onto PATH)
./tkt doctor                  # validate a project's auth + reachability + board model
./tkt view KEY --json         # normalized ticket shape skills parse
./tkt init --provider markdown --link-skills --sample   # scaffold a project's .sdlc/
./tkt sync-pack --check --dir ../consumer   # is a consumer repo's pack copy current?
./tkt agents --json           # state of every agent run (local read, safe to poll)

python3 -m unittest discover -s tests   # whole suite, fully offline
node --test enforcement/*.test.mjs  # enforcement checker self-tests (Node 20+)
bash scripts/smoke-agents.sh      # live run -> dead/halted, end to end
bash scripts/smoke-sync-pack.sh
```

There is **no linter config and no Makefile** in this repo. CI is
`.github/workflows/ci.yml`, and it needs no credentials — every test stubs its
transport. On every PR and every push to `main` it runs the suite on Python
3.11/3.12/3.13 (Linux) and 3.11 (macOS), byte-compiles `core`/`adapters`/`tkt`,
asserts every *tracked file with a shebang* is still mode `100755`, scaffolds a
throwaway markdown project and reads a ticket back through a PATH symlink from
a cwd outside the repo, runs `scripts/smoke-sync-pack.sh`, and runs the
`enforcement/` self-tests under Node 24 (`node --test enforcement/*.test.mjs`,
with bash `failglob` so a glob matching nothing fails rather than passing
vacuously).

Three deliberate gaps, each documented at its place in the YAML: **Windows is
not in the matrix** (decided in TKT-61: the CLI supports Windows, the suite
does not — `tests/` wants an IANA tz database and `#!/bin/sh`). The one
Windows-only code path, `core/agents.py`'s liveness probe, avoids
`os.kill(pid, 0)` there (it would *terminate* the target) and asks kernel32
via ctypes instead; `TestWindowsLivenessProbe` covers it on every OS by
stubbing `_win_query_process`;
**`scripts/smoke-agents.sh` stays local** because it SIGKILLs a live background
driver; and the exec-bit check covers *this repo's index only*. The mode bits
`sync-pack` writes into a *consumer* tree are covered separately, by
`smoke-sync-pack.sh` case 10 and `tests/test_pack_exec.py` — both plant their own
executable, so they don't depend on what the real pack ships (today only
the base layer's enforcement checker). `tests/` is a stdlib
`unittest` suite covering the run
driver's state machine (`test_run.py`) and its per-phase routing and
`TKT-RESULT:` stdout hand-back (`test_run_routing.py`), the `tkt agents` readers and their edge
cases (`test_agents.py`, `test_agents_edges.py`), the GitHub close/reopen sync
(`test_github_close.py`), GitHub projectv2 repo scoping
(`test_github_scope.py`), `select-ticket` surfacing `tkt` errors instead of
skipping a tier and never selecting a container or a ticket with open
children, run against a stub `tkt` under every available shell
(`test_select_ticket.py`), the after-hours window (`test_schedule.py`),
phase routing (`[harnesses]`/`[routes]`, `tkt cfg route`, its validation
and doctor row; `test_routing.py`), the Jira Markdown→ADF converter (`test_jira_adf.py`), Jira transition verification
(`test_jira_transition.py`), `tkt activity` on Jira (window bounds,
timestamp parsing, comment/changelog pagination; `test_jira_activity.py`)
and its CLI window validation, ordering and output (`test_activity_cli.py`),
Jira non-billable worklogs (native property, Tempo only under
`provider = "tempo"`, the `worklog (REST)` doctor row; `test_jira_worklog.py`),
Jira `list` paging every search result under both the token and
offset contracts (`test_jira_search.py`),
the identity fields (`project`, `reporter`, account ids, `created` /
`updated`) Jira fills on `list` and `view`, the acli search allow-list,
their lenient timestamp
normalization and their `view` rendering (`test_jira_identity.py`),
Jira blocker-link direction (`test_jira_blockers.py`,
`test_jira_blockers_edges.py`), Jira JQL project scoping (`test_jira_jql.py`,
`test_jira_jql_edges.py`), the shared JQL-subset evaluator (`test_query.py`),
`[query_scopes]` per-query project/global scope and Jira honouring it
(`test_query_scopes.py`),
markdown board scoping (`test_markdown_scope.py`, `test_markdown_scope_edges.py`),
`tkt list --all-projects` dropping project scoping on markdown and Jira
and being accepted by every adapter (`test_list_all_projects.py`),
markdown `edit --body` replacing the whole body (`test_markdown_edit.py`),
markdown refusing a ticket key that would escape the board folder
(`test_markdown_key_escape.py`),
markdown description and wrapped acceptance items, with fenced or
indented headings ignored (`test_markdown_description.py`,
`test_markdown_acceptance.py`),
`sync-pack`'s exec-bit handling (`test_pack_exec.py`), its
`.pre-sdlc.bak` backup of a pre-existing consumer file
(`test_pack_backup.py`), never writing through a symlink that resolves
outside the consumer tree (`test_pack_outside.py`; the AGENTS.md block
and manifest: `test_pack_outside_meta.py`), its always-on base
layer and derived skill mirrors (`test_pack_base.py`), `--prune` and its
safety guards (`test_pack_prune.py`), mirroring a consumer's own
`.claude/skills` into the verbatim skill surfaces (`test_pack_local.py`), `respond-to-review`
replying in-thread and resolving only a bot's won't-fix, across the
skill and every mirror
(`test_respond_to_review.py`), `Ticket.children` normalization in every
adapter and its `view` rendering (`test_children_jira.py`,
`test_children_github.py`, `test_children_linear.py`,
`test_children_markdown.py`, `test_children_openkanban.py`,
`test_children_cli.py`), `Ticket.parent` likewise, plus the two-sided
`link --type child|parent` on the local boards (`test_parent_jira.py`,
`test_parent_github.py`, `test_parent_linear.py`,
`test_parent_markdown.py`, `test_parent_openkanban.py`,
`test_parent_cli.py`), the two-sided `link --type blocks|"is blocked by"`
on the local boards (`test_blocks_markdown.py`,
`test_blocks_openkanban.py`) and on GitHub via native issue
dependencies or both bodies (`test_blocks_github.py`), `tkt --version` (`test_version.py`), shipped shell snippets
piping JSON into `jq` with `printf`, never `echo`
(`test_no_echo_jq.py`), the `container` type class and its precedence
(`test_issue_types.py`), `child_states` per adapter (`test_child_states.py`),
the container rollup and `tkt children` (`test_rollup_cli.py`), and the
transition/comment format gates: warn/strict/off, lane-name resolution,
every cannot-judge path, `[enforcement]` validation, the hierarchy checks,
`artifact_body` per adapter and the doctor row, with one end-to-end run of
the real checker skipped when node is absent (`test_gates.py`).
Outside `tests/`, `enforcement/check-artifact-format.test.mjs` (`node:test`,
run by `node --test enforcement/*.test.mjs`, not by `unittest`) drives
`check-artifact-format.mjs` as a subprocess: every rule once allowed and once
flagged, the parser's edge cases (fences, HTML comments, frontmatter vs a
leading thematic break), both shipped presets, the stderr/`--json` output and
exit codes 0/1/2, and rules-file validation.
`tkt init` is covered by `test_scaffold_init.py` (shared-board inheritance,
key derivation, `--project`/`--board-dir`/`--no-inherit`, the collision
warning, `--sample`, prompts, the placeholder doctor row) and
`test_toolchain.py` (Makefile merge, `apply_to_config`, `tomledit`).
Use `discover -s tests`; a bare `discover` from the root finds nothing (no
`tests/__init__.py`). Adapter coverage is limited to those stubbed paths:
"validation" of an adapter still means running `tkt doctor` / the read
verbs live against a real backend (see each provider's "Validation status" in
`README.md`). The two `scripts/smoke-*.sh` scripts cover `sync-pack` and `agents`
end to end.

`tkt` locates its own `core/`/`adapters/` relative to the script (`tkt:12`), so it
runs from any cwd and a PATH symlink is fine.

## Architecture

Two layers, connected only by the verb contract and the normalized schema:

- **`core/`** — provider-independent plumbing.
  - `cli.py` — argparse verb parsing, dispatch, and output formatting. `--json` works
    on either side of the verb. `init`/`sync-pack`/`lane`/`cfg` are handled **before**
    loading an adapter (`init` and `sync-pack` run before any config exists; `lane`/`cfg`
    are pure config reads). Exception: `cfg priorities` is backend-aware, so it does
    load an adapter. Input validation for dates, `--agent-status` and the
    `activity` window lives here so a typo fails before it reaches a backend.
  - `config.py` — loads `.sdlc/config.toml`. Discovery order: `--config` → `$TKT_CONFIG`
    → nearest `.sdlc/config.toml` walking up from cwd. Owns role↔lane mapping, the
    `close_on` terminal roles and `closed_roles()`, issue-type routing (`full_sdlc` /
    `deliverable` / `container`, container defaulting to `["Epic"]`), named
    queries and their `[query_scopes]` (project | global), the
    `[enforcement]` table (validated on use, not at load), the
    backend-agnostic priority ordering, and dotted-path `get()`.
  - `registry.py` — `provider name → (module, class)`, **lazy-imported** so one adapter's
    missing optional dep can't break the others.
  - `schema.py` — `Ticket` / `Worklog` / `Check` dataclasses + `to_dict()` (the JSON shape).
  - `query.py` — shared tiny **JQL-subset** evaluator, used by adapters with no native
    query language (markdown, linear, openkanban). `ORDER BY priority` ranks by the
    adapter's `priorities()` order, which callers pass in. (Note: `README.md`'s architecture
    block lists this under `adapters/`; it actually lives in `core/`.)
  - `ticketdoc.py` — the canonical full-ticket markdown document (frontmatter + body)
    that `tkt apply` ingests and the `$EDITOR` flow round-trips. Backend-agnostic:
    adapters map the parsed result onto their own storage.
  - `pack.py` — implements `tkt sync-pack` and its `doctor` check. Copies pack files
    into a consumer tree, tracks them in `.sdlc/pack-manifest.json`, and maintains a
    marker-delimited managed block in the consumer's `AGENTS.md`. Deletes only on
    `--prune`, never writes outside recorded paths except the untracked
    `<name>.pre-sdlc.bak` copy `_backup` makes before overwriting a file the pack
    never wrote, never writes a pack file whose path resolves outside the
    consumer tree (`_within`, checked before every plan write; skipped with a
    note, record kept; an outside AGENTS.md is skipped the same way, and an
    outside manifest refuses the whole sync up front with a `UsageError`),
    and is idempotent. `_HARNESSES` is the registry of
    named harnesses → (pack source, consumer dest) dirs; the installed set is
    additive and persisted in the manifest's `harnesses` list, so a bare re-run
    refreshes every harness the project ever added. `_BASE_LAYER` ships on
    every sync regardless (`.agents/skills`, `.agents/workflows`,
    `standards/` → `docs/sdlc/`, `enforcement/` → `.sdlc/enforcement/`) and
    is recorded as `base_layer` so `--prune` skips it. Files the plan no
    longer ships are carried under the manifest's `retired` key until
    `--prune` deletes them (`_prune_candidates`, `_safe_rel`); prune only acts
    inside `_PACK_DESTS` and never under `.git`. Stale base-layer files are
    tracked under `base_retired`, never pruned. The `.agents/skills`
    and `.kiro/skills` mirrors are derived from `skills/` at sync time, not
    stored in this repo. A consumer's own `.claude/skills/<name>` dirs
    (names the pack doesn't own) are mirrored into `_skill_surfaces` —
    every registered dest named `<dir>/skills` except `.claude/skills` —
    tracked like pack files, names recorded as `local_skills`. A copy dir
    holding files sync-pack never wrote is kept whole; a removed skill's
    copies retire and prune, `.agents/skills` included (`unprotect`).
  - `__init__.py` / `version.py` — `__version__` is the single place the release
    number is written; bump it when releasing. `version.py` builds what
    `tkt --version` prints (number + own-checkout `git describe`), lazily.
  - `errors.py` — typed errors → exit codes (see `AGENTS.md` for the table). Errors
    always go to stderr with a non-zero exit so skills branch on codes.
  - `toolchain.py` — best-effort detection of a project's build/test/typecheck/lint
    commands (package.json scripts, Cargo.toml, go.mod, pyproject.toml, Makefile)
    plus the `[build]`-table rewrite `init` applies to the copied example config.
    The first language probe that matches is the base; Makefile targets fill
    the keys it lacks. Advisory only: a key the project doesn't declare keeps
    the example's value.
  - `tomledit.py` — line-level TOML edits (`set_keys`, `replace_table`) that
    keep comments and alignment; callers re-parse the result with `tomllib`.
  - `scaffold.py` — implements `tkt init`: derives the project key from the
    dir name, and for markdown joins the nearest parent `.sdlc/config.toml`
    that is a markdown board (parent text kept, repo's own `project`,
    `[vcs]`, `[build]`; relative paths rebased via `config.project_root`).
    Also holds the `config placeholders` doctor row.
  - `gates.py` — the format gates `cli.py` runs before `transition` (into
    `review` and later lanes, a literal lane name resolved to its role first)
    and `comment`. Shells out to `enforcement/check-artifact-format.mjs` in
    tkt's own checkout and reads its stderr finding lines; `[enforcement]`
    picks warn (default) / strict (`GateError`, exit 5) / off. Anything it
    cannot judge — no node, no checker, no body (`Adapter.artifact_body`
    returns None; jira always does), a backend or checker failure — lets the
    verb through. Its markdown reading mirrors the checker's (CommonMark
    fences, HTML comments, leading YAML frontmatter); `cut_managed_tail` is
    what the markdown and openkanban adapters use to drop their comment
    logs (`## Comments`, `## Activity`) from the judged body. Also holds the advisory hierarchy
    checks (test plans, sub-task size) and the `enforcement` doctor row.
  - `schedule.py` — the `[schedule]` table and `tkt schedule`: parses and
    validates it (bad hours, days or timezone are a `ConfigError`, never a
    silently disabled hold) and answers "is it business hours now". Skills ask
    it instead of doing clock maths in shell, which behaved differently under
    zsh, bash and dash.
  - `routing.py` — `[harnesses]` / `[routes]` and `tkt cfg route <phase>`:
    resolves a phase (skill name) to harness cmd + model, route first, then
    the skill's `model_tier` (read from tkt's own `skills/`) through
    `[models]`, then `[run].harness_cmd`. Validated on use, like
    `[enforcement]`; also the `routing` doctor row (missing harness = warning).
  - `run.py` — the `tkt run` external loop driver, and the only writer of run
    state: phase markers (ticket comment + `marker.json` mirror), the `run.log`
    JSONL, the STOP file, and `heartbeat.json`. The heartbeat runs on a daemon
    thread because a harness invocation blocks for up to `invocation_timeout`;
    a beat written only at loop boundaries could be an hour stale and could not
    distinguish "working" from "crashed". `run_root()` resolves where all of it
    lives, honouring `[run].state_dir` so repos sharing a board can share a
    run-state root. Each phase's command comes from `phase_command`: its
    `PHASE_ROUTES` key resolved through `core/routing.py` when routed (all
    routed phases are resolved up front by `check_routes`), else
    `[run].harness_cmd` verbatim. A missing or invalid `result.json` falls
    back to the last `TKT-RESULT: {...}` line of the harness's stdout
    (`read_result_line`, same validation as the file).
  - `agents.py` — the read side: `tkt agents`, plus the row `tkt run --status`
    prints. Imports from `run.py`, never the reverse. Contract: for `tkt agents`,
    **no adapter is constructed and no backend call is made** unless the caller
    passes `--enrich`, so a TUI can poll it once a second. (`status_for_key` has
    one lazy, non-fatal fallback to the ticket marker comment, used by
    `run --status` only when there is no local state.) `resolve_state()` holds the
    running/stalled/dead/blocked/halted/idle table.
- **`adapters/`** — one file per backend, each subclassing `adapters/base.Adapter`.
  - `base.py` is the contract: required `@abstractmethod` verbs (whoami, list, view,
    transition, comment, blockers, worklog, lane_time, doctor) plus **optional** verbs
    (`create`, `apply`, `edit`, `link`, `activity`) that default to a clear "not supported"
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
`status_role` (canonical role) and `closed` (finished: a closed role, or the
backend's own terminal state, filled at normalization through
`Adapter._closed` so parents and children share one rule), `type_class` (`full_sdlc` | `deliverable` | `container` |
`unknown`),
the date fields `due` / `scheduled` / `completed`, and `agent_status` (board agent state:
`idle` | `processing` | `waiting` | `done` | `blocked`, or empty) alongside
`agent_status_at` (when that state last actually changed, ISO-8601 UTC; stamped by
the adapter, never passed in, so re-asserting the same state doesn't reset it),
and the hierarchy edges `children` (direct child keys) and `parent` (one key or
`""`). Edges that cost an extra backend call — jira and github children, github
parent, github native blocker links — are filled on `view` only
(`blockers` asks github for the links alone). Every adapter's `view` takes
`hierarchy=False` to skip those calls (the rest ignore it); internal callers that
read none of them — `create`, `tkt agents --enrich` — pass it. On
the local boards (markdown, openkanban) `tkt link --type child|parent` writes
both edges so they cannot drift. `rollup` (children closed / total, open keys,
`ready_to_close`) is filled by `cli.py` on `view` of a container only, through
`Adapter.child_states` (base default: a view per child; jira and github one
call); `tkt children` prints the same rows. It is `None` everywhere else.
The identity fields `project` / `project_name` (key and display name),
`assignee_id`, `reporter` / `reporter_id` (name and account id) and `created` /
`updated` (canonical UTC from `schema.iso_utc`, `YYYY-MM-DDTHH:MM:SS.mmmZ`) are
filled by jira only, on `view` and REST-mode `list` (acli search rejects
`project`, `created`, `updated` and `parent`, so acli-mode `list` leaves
those at their defaults); jira normalizes them leniently
(a missing, null or offset-less value is the default, never an error). Every
other adapter leaves them at `""` / `None`.
Adapters that can't store a field leave it at its default rather than faking it.

## Adding a provider

1. Implement `adapters/base.Adapter` in `adapters/<name>.py` (all abstract verbs;
   `create`/`apply`/`edit`/`link`/`activity` only if the backend supports them).
2. Register it in `core/registry.py`'s `_PROVIDERS`.
3. Add `examples/config.<name>.toml`.

No skill changes are ever required — that's the point of the split.

## Editing the skill pack

The rules live in `AGENTS.md` ("Editing skills"). The short version: no backend
specifics in skill text, read every toolchain/VCS/deploy value through `tkt cfg`, and
remember that a skill's frontmatter `description` feeds the generated `AGENTS.md` block
that `sync-pack` writes into consumer repos.
