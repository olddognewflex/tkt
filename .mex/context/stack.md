---
name: stack
description: Technology stack, library choices, and the reasoning behind them. Load when working with specific technologies or making decisions about libraries and tools.
triggers:
  - "library"
  - "package"
  - "dependency"
  - "which tool"
  - "technology"
edges:
  - target: context/decisions.md
    condition: when the reasoning behind a tech choice is needed
  - target: context/conventions.md
    condition: when understanding how to use a technology in this codebase
  - target: context/setup.md
    condition: when you need the actual commands to run or validate anything
  - target: context/architecture.md
    condition: when you need to know which module a technology is confined to
grounds_to: []
last_updated: 2026-08-04
---

# Stack

## Core Technologies

- **Python 3.11+** — the floor is set by `tomllib`, which is stdlib from 3.11. There
  is no version pin file; the interpreter on `PATH` is what runs.
- **The Python standard library, and nothing else** — `argparse`, `tomllib`, `json`,
  `pathlib`, `dataclasses`, `subprocess`, `urllib`, `hashlib`, `re`, `unittest`.
  Adding a third-party import is a breaking change to this project's promise.
- **Bash** — `scripts/smoke-sync-pack.sh` (the `sync-pack` smoke test) and
  `scripts/company-import.sh` (bulk import).
- **Markdown + YAML frontmatter** — the format of the skill pack (`skills/<name>/SKILL.md`),
  the `markdown` provider's on-disk tickets, and the canonical ticket document.
- **TOML** — `.sdlc/config.toml` is the only configuration surface. Read-only at
  runtime except for the `[build]` table, which `tkt init` rewrites.

## Key Libraries

There are no libraries. What matters instead is which stdlib module owns which job,
because picking the "obvious" alternative breaks a project constraint:

- **`tomllib`** (not `toml`, not `tomlkit`) — config parsing. It is read-only by
  design, which is why the `[build]` rewrite in `core/toolchain.py` is a careful
  line-level regex edit rather than a parse-and-redump.
- **`unittest`** (not pytest) — the suite must run with a bare interpreter and no
  install step: `python3 -m unittest tests.test_run tests.test_jira_adf`.
- **`argparse`** (not click/typer) — the whole CLI, including the shared-flag trick
  that lets `--config`/`--json` sit on either side of the verb.
- **`subprocess`** (not an SDK) — every backend CLI (`acli`, `gh`, `git`) is shelled
  out to. There is no Jira, GitHub, or Linear client library anywhere in the tree.
- **`urllib.request`** (not `requests`/`httpx`) — the raw HTTP calls in the `jira`
  (REST + Tempo) and `linear` (GraphQL) adapters.
- **`dataclasses` + `asdict`** (not pydantic) — `Ticket`/`Worklog`/`Check` and their
  `to_dict()`. The JSON shape skills parse is literally `asdict()` output, so adding a
  field to a dataclass changes the public contract.

## What We Deliberately Do NOT Use

- **No third-party packages, ever.** The project's selling point is "clone it once and
  point any project at it" — a dependency reintroduces an install step and a
  virtualenv. If a task seems to need a library, it needs a smaller design instead.
- **No pytest, no CI runner, no linter/formatter config.** No `pyproject.toml`,
  `setup.py`, `Makefile`, or `.github/workflows/`. Do not add tool config expecting
  something to pick it up — nothing will.
- **No ORM, database, or cache.** Local providers persist to plain files: one `.md`
  per ticket plus JSONL sidecars (`markdown`), or a single JSON store (`openkanban`).
- **No async.** Every verb is one synchronous round trip; `lane_time_batch` loops
  rather than fanning out.
- **No backend SDK inside `core/`.** `core/` must stay provider-blind; if you find
  yourself importing an adapter there, the design is wrong.

## Version Constraints

Python **3.11** is the hard floor (`tomllib`). Code is written to run unchanged on
3.11 through current releases — the suite passes on 3.14. Windows is a supported
target, so paths go through `pathlib` and the `tkt` shebang is expected to be
bypassed by a batch/PowerShell wrapper (see `README.md`).
