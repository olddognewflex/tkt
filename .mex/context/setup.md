---
name: setup
description: Dev environment setup and commands. Load when setting up the project for the first time or when environment issues arise.
triggers:
  - "setup"
  - "install"
  - "environment"
  - "getting started"
  - "how do I run"
  - "local development"
edges:
  - target: context/stack.md
    condition: when specific technology versions or library details are needed
  - target: context/architecture.md
    condition: when understanding how components connect during setup
  - target: patterns/debug-config-resolution.md
    condition: when a command fails with a config error or the wrong config is being picked up
  - target: context/skill-pack.md
    condition: when installing the pack into a consumer repo rather than working on tkt itself
grounds_to:
  - node: "method:63d4e566959373a8ea1e944f5088a4a3"
    fingerprint: "mh:64:7b226d696e68617368223a5b31373636313031392c3132343131373137322c33303336373738352c333534353038352c35383136393639312c33303133353032342c37313138313138322c37383837343238352c37353636373238362c35313039363337352c31373931303839362c31313236353438362c34323234323235342c31383537323237312c373633323832392c39303137383935342c3131383537303232312c3138343035353933362c37373637313334382c313535343731302c31393031393432382c38343331303739302c33313633363030342c34373939313830302c37333232373136312c34393338373230332c3135373538393738342c34383530303431302c35383735303930382c34333636393037392c33313736313835342c3233313335323637312c343431393134332c33373037373835342c3132383039343331342c35323934383637372c393238303630302c31333730383630332c32303431373732342c36343531303332342c38333238373839342c37373033373232302c39303031373634392c32323637323731362c31383434303833372c3132323333393634382c36383337343337332c3132333334313832322c34393236353938322c39323931333838322c31343333363634382c37333535363435332c31393131333238372c3133343933333132312c32313437343538332c37303230383532362c35343139343937332c313830313638392c38333630383837312c37333335383338332c323035363330362c32393939383334352c39313837303739332c31313732363932355d2c226e65696768626f7273223a5b226d6574686f643a3037643536643433393632653934366336343365333131313839313938373431222c226d6574686f643a6332363665363464623731613835353566353932373539356364656363306636225d2c22746f6b656e436f756e74223a3134307d"
  - node: "function:0d3291c64a6f1ef200a8dcc084cd5fa1"
    fingerprint: "mh:64:7b226d696e68617368223a5b383939303735392c38353030383030312c32313532313238312c36343236323035362c3132313330333834372c37363839323333362c3131303031383138372c31333530393139342c32353331383030302c31353935333038372c31333034313534362c31313236353438362c37323039333639302c31383537323237312c32383331363038322c32373732313037342c3131383537303232312c373535353739342c34353839353537392c333237303332372c33383137363739372c37333436353130332c313830393030342c393832393038372c3137303139353032342c34393338373230332c3134323037333138362c35313532323032362c35383735303930382c313634363838322c3131353037313430372c3134363731353938312c343431393134332c31313431373433332c35333435323433352c3131383139353636382c36303635363539302c35373534303936322c32303431373732342c3133323233333533372c36353530383733302c39373439363630332c3832363536312c3130313136333735342c353232303232332c343434363039342c3136373437313035302c34363739363633322c3132323930393536302c32323934333537332c3131343131343137342c37333535363435332c31383737353335302c36303737383137322c32353330383530382c37383932363934362c35343139343937332c313830313638392c38333630383837312c35373536313138302c31323330333835362c37373637393735362c32333032383630312c31313732363932355d2c226e65696768626f7273223a5b2266756e6374696f6e3a3835366537666135323366383732303132373135623038336332353335353562222c2266756e6374696f6e3a6564323862663262666631636130313238613839666361316139663934343134222c2266756e6374696f6e3a6630323663643730306438313463353834333334653431303237353962383039225d2c22746f6b656e436f756e74223a3133327d"
last_updated: 2026-08-10
---

# Setup

## Prerequisites

- **Python 3.11+** on `PATH` — `tomllib` is the floor. Nothing else to install: no
  virtualenv, no `pip install`, no lockfile.
- **`git`** — only needed for `sync-pack`'s `pack_commit` stamp and `tkt doctor`'s
  staleness hint.
- **A backend CLI, only for the provider you actually use:** `acli` for `jira`,
  `gh` for `github` (with `-s project,read:project` if the board is Projects v2).
  `linear` needs no CLI, just `LINEAR_API_KEY`. `markdown` and `openkanban` need
  nothing at all — they are pure local files.

## First-time Setup

Working on `tkt` itself:

1. `git clone <repo> && cd tkt` — there is nothing to build or install.
2. `./tkt --help` — confirms the interpreter is new enough. `tkt` locates its own
   `core/` and `adapters/` relative to the script, so it runs from any cwd and a
   `PATH` symlink (`ln -s "$PWD/tkt" /usr/local/bin/tkt`) works fine.
3. `python3 -m unittest tests.test_run tests.test_jira_adf` — 58 tests, fully offline.
4. This repo dogfoods itself against a **markdown** board. `.sdlc/` is gitignored, so
   a fresh clone has no config; create one with `./tkt init --provider markdown --sample`
   and then `./tkt doctor`.

Pointing another project at `tkt`:

1. `cd my-project && tkt init --provider <jira|markdown|github|linear|openkanban>`
   — writes `.sdlc/config.toml` from `examples/config.<provider>.toml`, and rewrites its
   `[build]` table from whatever the project already declares
   ([`_seed_build()`](mex://function:0d3291c64a6f1ef200a8dcc084cd5fa1); pass
   `--no-detect-build` to keep the example's values).
2. Edit `.sdlc/config.toml` — at minimum `[board.roles]`, `[queries]`, and `[vcs]`.
3. Export the provider's auth (see below), then `tkt doctor`.
4. Activate the skills: `tkt sync-pack` for committed copies (required for cloud
   harnesses and CI) or `tkt init --link-skills` for symlinks (local Claude Code only).

## Environment Variables

`tkt` itself reads exactly one variable; the rest belong to a specific provider and are
declared per project in `[ticketing].auth_env`.

- `TKT_CONFIG` (optional) — absolute path to a `config.toml`, overriding directory
  discovery. Second in precedence, after an explicit `--config`. If it points at a
  missing file that is a hard `ConfigError`, not a fallback.
- `CONFLUENCE_SITE`, `CONFLUENCE_EMAIL`, `CONFLUENCE_API_TOKEN` (required for `jira`)
  — the Atlassian site and API credentials; also used for the Tempo worklog calls.
- `LINEAR_API_KEY` (required for `linear`) — sent to `api.linear.app/graphql`.
- `github` (required: none) — authentication is whatever `gh auth status` reports.
- `markdown`, `openkanban` (required: none) — local files only.

Never put credential values in `.sdlc/config.toml`; it names the variables, it does not
hold them.

## Common Commands

- `./tkt doctor` — the first thing to run against any project. Validates auth,
  reachability, the board model, and folds in the `sync-pack` staleness check.
- `./tkt view KEY --json` — the normalized shape every skill parses. The fastest way
  to see what an adapter actually returns.
- `./tkt list --tier 1` / `--query <name>` — runs a named query from `[queries]`.
  One of the two is required.
- `python3 -m unittest tests.test_run tests.test_jira_adf` — the full suite (58 tests).
  Add `-v` for per-test output. Note that `unittest discover` does **not** work here
  (`tests/` has no `__init__.py`); name the modules explicitly.
- `bash scripts/smoke-sync-pack.sh` — the only end-to-end check in the repo. Exercises
  fresh install, idempotency, local-modification restore, `--check` exit status,
  `--all-harnesses`, and `AGENTS.md` content preservation, in throwaway git repos.
  Prints PASS/FAIL per case.
- `./tkt sync-pack --check --dir ../consumer` — is a consumer repo's pack copy current?
  Exits 1 when anything is missing, locally modified, or out of date.
- `./tkt cfg <dotted.key>` — read a config value the way a skill does, e.g.
  `./tkt cfg build.test --pkg web` or `./tkt cfg vcs.branch_fmt --ticket TKT-1 --slug fix`.

## Common Issues

**"no .sdlc/config.toml found (searched cwd and parents)":** discovery walks up from
cwd ([`Config._find()`](mex://method:63d4e566959373a8ea1e944f5088a4a3)) and this repo
gitignores `.sdlc/`, so a fresh clone genuinely has none. Run `tkt init`, or point at
one with `--config` / `TKT_CONFIG`. Exit code 2.

**The wrong config is being used:** precedence is `--config` → `$TKT_CONFIG` → nearest
`.sdlc/config.toml` walking up. An exported `TKT_CONFIG` from another project beats
the one in the directory you are standing in. `./tkt cfg ticketing.provider` tells you
which one won.

**`unittest discover` fails with "Start directory is not importable":** there is no
`tests/__init__.py`. Run `python3 -m unittest tests.test_run tests.test_jira_adf`
from the repo root instead.

**"unknown lane role 'X'":** the role is missing from `[board.roles]`. Exit code 4.
`./tkt lane <role>` resolves a role to its literal lane and fails the same way, which
makes it a quick probe.

**A verb exits 3 with "not supported by provider":** that adapter has not opted into
the optional verb (`create`, `apply`, `edit`, `link`). This is expected, not a bug —
branch on the exit code.

**`sync-pack` refuses with "--dir is inside the pack checkout":** `sync-pack` installs
*into a separate consumer repo*. Running it with no `--dir` from inside this repo is
caught by a guardrail rather than overwriting the pack with itself.
