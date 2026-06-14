# Markdown Ticketing

How the `markdown` provider works, end to end — the file format, where state
lives, what each verb does to disk, and how to put a Kanban board on top (custom
TUI or Obsidian).

This is the dependency-free default adapter. It is also the proof that the `tkt`
abstraction holds: the same SDLC skills that drive Jira drive a folder of `.md`
files unchanged. Source of truth for everything below: `adapters/markdown.py`.

---

## 1. Mental model

A board is **a directory of markdown files, one file per ticket**.

- The markdown file is the **human-canonical source**. You can read, edit, and
  diff it by hand. Git tracks it. Nothing is hidden in a database.
- **Derived / machine-local state** (status-change history, time tracking) lives
  in JSONL **sidecars** in a separate directory. These are append-only logs that
  `tkt` writes; you rarely touch them by hand.

That split is deliberate: the prose stays clean and reviewable, while the noisy
machine bookkeeping (timestamps, lane durations) stays out of the document.

```
project-root/
├── .sdlc/
│   ├── config.toml              # provider = "markdown" + role map + queries
│   ├── board/                   # board_dir — one <KEY>.md per ticket
│   │   ├── TKT-1.md
│   │   ├── TKT-2.md
│   │   └── TKT-3.md
│   └── state/                   # state_dir — derived, append-only sidecars
│       ├── TKT-1.history.jsonl  # per-ticket lane transition log
│       ├── TKT-2.history.jsonl
│       └── worklog.jsonl        # one shared time-tracking log for the board
```

`board_dir` and `state_dir` are configurable and may be absolute (see
§7 Shared board). Relative paths resolve from the **project root** — the
directory that *contains* `.sdlc`, not `.sdlc` itself — so `".sdlc/board"`
doesn't double up to `.sdlc/.sdlc/board`.

---

## 2. The ticket file format

```markdown
---
type: Story
status: To Do
priority: High
assignee: raymond
labels: [api, auth]
blocked_by: [TKT-2]
blocks: []
---
# One-line summary

Free-form description goes here. Any prose before the first `##` heading
becomes the ticket description.

## Acceptance
- criterion one
- criterion two

## Comments
- 2026-06-02T10:00:00Z raymond: started work
```

### 2.1 Frontmatter (the YAML block between `---` fences)

It is a **deliberately tiny YAML subset**, parsed by hand in the adapter — *not*
a real YAML engine. Rules:

- One `key: value` per line. Nothing nested.
- List literals only as `[a, b, c]` on a single line. Quotes around items are
  stripped. An empty list is `[]`.
- Everything else is a scalar string. Quotes (`'` or `"`) around a scalar are
  stripped.

Recognised keys and how the adapter maps them into the normalized ticket:

| Frontmatter key | Normalized field | Notes |
|---|---|---|
| `type`        | `type` + `type_class` | `type_class` resolved via `[issue_types]` → `full_sdlc` / `deliverable` / `unknown`. |
| `status`      | `status` + `status_role` | `status` is the literal lane string; `status_role` is the canonical role resolved through `[board.roles]`. |
| `priority`    | `priority` | Free string (`Highest`/`High`/…); only meaningful relative to your queries. |
| `assignee`    | `assignee` | Compared against `currentUser()` in queries. |
| `labels`      | `labels` | List. |
| `components`  | `components` | List. |
| `blocked_by`  | `blocked_by` | List of keys. Each is expanded to `{key, resolved}`, where `resolved` is true iff that ticket's `status` equals the `done` lane. |
| `blocks`      | `blocks` | List of keys. Informational (the inverse edge). |
| `links`       | — | List of `"<type>:<KEY>"` strings, written by `tkt link` for non-block link types. |

Any other key you add is preserved on write but ignored by normalization. A
single string where a list is expected (e.g. `blocked_by: TKT-2`) is coerced to
a one-element list, so hand-edits are forgiving.

### 2.2 Body sections

The body is parsed structurally:

- The **first `# ` heading** → `summary` (the one-liner).
- Prose lines *before* any `##` section → `description`.
- A `## Acceptance` section → `acceptance[]`, one entry per `-`/`*` bullet.
- A `## Comments` section → where `tkt comment` appends. Each comment is a
  bullet: `- <ISO-8601 ts> <author>: <text>`.
- Any other `## ` heading closes the current section (its content is not
  currently normalized into a field, but it stays in the file).

> **Gotcha:** the summary is taken from the *first* `# ` line only. Keep exactly
> one top-level `#` heading per ticket.

---

## 3. State sidecars (the JSONL files)

These are append-only. Never edited by hand in normal flow; safe to delete if
you want to reset derived state (you lose history/time, not the ticket).

### 3.1 `<KEY>.history.jsonl` — lane transition log

One line per status change, written by `transition` (and `create`):

```json
{"ts": "2026-06-02T10:00:00Z", "from": "To Do", "to": "In Progress"}
```

`create` writes an initial `{"from": "", "to": "<todo lane>"}`. This log is the
sole source for time-in-lane math.

### 3.2 `worklog.jsonl` — one shared time log for the whole board

One line per logged interval, written by `worklog` / `lane_time`:

```json
{"id": "1", "key": "TKT-1", "role": "in_progress", "lane": "In Progress",
 "seconds": 5400, "started": "2026-06-02T11:30:00Z", "note": "..."}
```

`id` is just the 1-based line count at append time.

---

## 4. Roles, not lane names

This is the whole abstraction, and it matters most for the markdown board
because *you* invent the lane names.

Skills speak **canonical roles** — `backlog`, `todo`, `in_progress`, `review`,
`qa_ready`, `done`, `blocked`, … `[board.roles]` in config maps each role to the
literal string you put in a ticket's `status:` field.

```toml
[board.roles]
backlog     = "Backlog"
todo        = "To Do"
in_progress = "In Progress"
review      = "In Review"
done        = "Done"
blocked     = "Blocked"
```

Consequences:

- A ticket whose `status: In Progress` has `status_role: in_progress`.
- A skill calls `tkt transition TKT-1 review`; the adapter writes
  `status: In Review` and logs the transition. The skill never knows the literal.
- `$(tkt lane review)` prints `In Review` when a skill genuinely needs the literal.
- A blocker counts as **resolved** only when its file's `status` equals the
  `done` lane — so the `done` role *must* be mapped, or blocker resolution and
  `doctor` break.
- Your column set is entirely up to you. A 3-column personal board and an
  8-lane team board are the same code; only `[board.roles]` differs.

---

## 5. What each verb does to disk

| Verb | Reads | Writes | Notes |
|---|---|---|---|
| `whoami` | `[markdown].me` | — | Returns `me`, or a hint if unset. |
| `list [--tier N] [--query name]` | every `*.md` in `board_dir` | — | Loads all tickets, then runs the JQL-subset query (`core/query.py`). |
| `view KEY` | `board/KEY.md` | — | Normalized ticket. `--json` for the machine shape skills parse. |
| `transition KEY role` | `board/KEY.md` | rewrites frontmatter `status`; appends history | No-op if already in that lane. |
| `comment KEY "text"` | `board/KEY.md` | appends a bullet under `## Comments` | Creates the section if missing. Author = `me` (or `agent`). |
| `blockers KEY` | `board/KEY.md` + each blocker's file | — | Returns only *unresolved* blockers. |
| `create --type T --summary S [...]` | board_dir | new `board/KEY.md` + initial history | Auto-keys `PREFIX-N` (next integer). Starts in the `todo` (else `backlog`) lane. |
| `link KEY --to KEY2 --type "is blocked by"` | `board/KEY.md` | updates `blocked_by`/`blocks`/`links` | Only the source file is edited; the inverse edge is not auto-written. |
| `worklog KEY from_role` | history | appends to `worklog.jsonl` | Duration = now − last entry into that lane (min 60s). No-op shape if timetracking `none`. |
| `lane_time KEY role` | history | appends to `worklog.jsonl` | Retroactive: time between entering the lane and the next exit. |
| `doctor` | config + board_dir | — | Checks board_dir exists, roles configured, `done` mapped, `me` set. |

`create` keys auto-increment: it scans `board_dir` for `PREFIX-<n>.md`, takes the
max `n`, returns `PREFIX-(n+1)`. Prefix comes from `--project` then
`[ticketing].project` then `TKT`. **Flags, not positionals:**
`tkt create --type Story --summary "Add auth" --priority High`.

---

## 6. Configuration reference

Minimal working config (`.sdlc/config.toml`):

```toml
[ticketing]
provider = "markdown"
project  = "TKT"               # key prefix for create

[markdown]
board_dir = ".sdlc/board"      # one <KEY>.md per ticket (human-canonical)
state_dir = ".sdlc/state"      # history + worklog sidecars (machine-local)
me        = "raymond"          # currentUser() in queries; comment author

[board.roles]
backlog     = "Backlog"
todo        = "To Do"
in_progress = "In Progress"
review      = "In Review"
done        = "Done"
blocked     = "Blocked"

[issue_types]
full_sdlc   = ["Story", "Bug"]        # go through the full PR pipeline
deliverable = ["Task", "Chore", "Spike"]  # short-circuit (complete-deliverable)

[queries]   # tiny JQL subset: field = "v" [AND ...] [ORDER BY field DIR]
tier1 = 'status = "To Do" AND priority = Highest AND assignee = currentUser() ORDER BY priority DESC'
tier2 = 'status = "To Do" AND assignee = currentUser() ORDER BY priority DESC'

[timetracking]
provider = "local"             # "none" disables worklog; anything else → local JSONL
```

`vcs`/`build` blocks are only relevant if you run the SDLC skills (PRs, CI); the
board itself doesn't need them.

Scaffold a fresh one: `./tkt init --provider markdown --link-skills --sample`.

---

## 7. The ODNF shared-board setup (how *your* repos do it)

All repos under `/Users/raymonddoran/Development/odnf` use a **hybrid**: one
shared board, per-repo VCS. The trick is absolute paths in `[markdown]`:

- One shared board at `/Users/raymonddoran/Development/odnf/.sdlc/board` + `.../state`.
- Each repo has its *own* `.sdlc/config.toml` with repo-specific `project`
  prefix and `[vcs]`/`[build]`, but `board_dir`/`state_dir` point at the shared
  absolute paths.
- Walk-up discovery (cwd → parents for `.sdlc/config.toml`) means running `tkt`
  inside a repo finds that repo's config (→ correct VCS/prefix) while all configs
  write to the one board.
- Root view config at `odnf/.sdlc/config.toml` (`project="INBOX"`) so `tkt list`
  from the dev root shows every repo's tickets.
- Regenerate / add repos via `/Users/raymonddoran/Development/odnf/.sdlc/_gen_configs.py`
  (edit the REPOS table, rerun). It rewrites configs; never touches board contents.

Key prefixes in use: AILAB, AIMRP, AIMW, AIMAP, CFX, EMAIL, ERS, GQLD, GQLAT,
GQLPK, ODNF, TKT, TUI, QI.

**Implication for any Kanban viewer:** point it at the one shared `board/`
directory and it shows every repo's tickets in one board. The `PREFIX-` in each
key tells you which repo a ticket belongs to — that's your natural "project"
swimlane / filter axis.

---

## 8. Putting a Kanban board on top

The board is already Kanban-shaped: **columns = the `status` values**,
**cards = the `.md` files**, **card order within a column = your call** (priority
field, filename, or an explicit order key). Two viable front ends:

### Option A — Obsidian plugin (lowest effort, recommended to start)

Obsidian already renders the vault, the frontmatter, and the prose. You almost
certainly don't need a *custom* plugin — existing community plugins read exactly
this shape:

- **Kanban** (mrjackphil / obsidian-kanban) — board view; but note it stores its
  own board state inside a special markdown file, so it's a slightly different
  model than "one file per card." Better as a *reader* than the writer here.
- **Projects** (marcusolsson/obsidian-projects) — this is the strong fit. It
  treats *a folder of notes* as a dataset and gives you Board / Table / Calendar
  views driven by **frontmatter fields**. Point it at `board/`, set the board's
  "status" field to `status`, and you get drag-between-columns that writes the
  `status:` frontmatter back — which is exactly the field `tkt` reads.
- **Dataview** — for read-only dashboards/queries (e.g. "all my `In Progress`
  across every prefix") without a board UI.

To make the markdown board Obsidian-friendly, the only thing worth adding is a
couple of optional frontmatter fields the adapter already preserves on write:

| Field | Purpose in a board UI |
|---|---|
| `status`   | **Column.** Already there. Must match a `[board.roles]` literal. |
| `priority` | Sort within a column. |
| `assignee` | Filter / swimlane. |
| `labels`   | Tag chips / filter. |
| `order`    | (Optional, new) explicit card position within a column, if you want manual ordering instead of priority sort. `tkt` ignores it; Obsidian Projects can own it. |

**Caveat — the drag-write contract:** if a plugin edits frontmatter, it must keep
the tiny-subset format `tkt` parses (`key: value`, single-line `[a, b]` lists, no
nested YAML). Obsidian Projects writes plain scalar frontmatter, which is
compatible. The thing to *avoid* is a plugin rewriting lists into multi-line YAML
block style:

```yaml
# tkt CANNOT parse this — it only reads single-line [a, b] lists
labels:
  - api
  - auth
```

If you adopt Obsidian as the writer, add one CI/pre-commit check that re-reads
each touched ticket through `tkt view KEY --json` and fails on a parse error —
cheap guard against format drift.

What you lose with Obsidian: the **history/worklog sidecars** are invisible to it
(they live in `state/`, not the vault, and aren't markdown notes). Time-in-lane
and transition history stay a `tkt` concern. That's fine — Obsidian moves cards;
`tkt`/the skills do the bookkeeping. If you want the move *timestamped in
history*, you'd transition via `tkt transition` rather than dragging — see §9.

### Option B — Custom TUI (most control, fits the ODNF tooling style)

A read-mostly TUI is small because all the parsing already exists in the adapter.
Architecture:

1. **Read path:** shell out to `tkt list --json` (and `tkt view KEY --json`), or
   import `MarkdownAdapter` directly. Either way you get the normalized `Ticket`
   shape — never re-parse the markdown yourself. Group by `status_role` (stable
   across boards) into columns; sort each column by `priority` then `key`.
2. **Render:** one column per role in `[board.roles]` order, cards show
   `key`, `summary`, `assignee`, `priority`, blocker count. Textual or
   Rich (Python, stdlib-adjacent, matches the repo's no-deps-if-possible ethos)
   is the natural pick; you already have a `TUI` key prefix, so this may be a
   planned repo.
3. **Write path (the important rule):** never write the markdown directly from
   the TUI. Call the verbs:
   - move a card → `tkt transition KEY <role>` (rewrites status **and** logs
     history + lets you fire `lane_time`).
   - add a note → `tkt comment KEY "..."`.
   - create → `tkt create --type … --summary …`.

   Going through the verbs is what keeps history/worklog correct and keeps the
   TUI backend-agnostic — point the same TUI at a Jira config and it still works,
   because it only ever speaks the verb contract.

Minimum viable TUI = a read-only `tkt list --json` grouped into columns. Add the
three write actions above and you have a full board. Everything else (filters by
prefix/assignee, time-in-lane from the history sidecar) is incremental.

### Which to pick

- **Want it today, mostly to *see* the board and occasionally drag a card?**
  → Obsidian + the Projects plugin. Zero code. Just mind the frontmatter format
  contract and that history isn't captured on drag.
- **Want timestamped transitions, time tracking, multi-repo prefix swimlanes,
  and a tool that also works against Jira/Linear later?**
  → Custom TUI over `tkt … --json`. More work, but it's the backend-agnostic,
  history-correct path and matches the rest of your tooling.

A sensible hybrid: **Obsidian for browsing/editing prose, the TUI (or plain
`tkt`) for moves** so history and worklog stay honest.

---

## 9. Invariants a viewer must respect

1. **One `# ` heading per ticket** — it's the summary.
2. **Frontmatter stays in the tiny subset** — `key: value`, single-line
   `[a, b]` lists, no nesting. (See §8 caveat.)
3. **`status` must always be a literal from `[board.roles]`** — an unmapped
   status has `status_role: ""` and falls out of role-based queries/columns.
4. **Don't hand-write the sidecars** — let `tkt` own `state/`. Deleting them
   resets history/time only, not tickets.
5. **Prefer the verbs over direct file writes** for transitions, so history and
   worklog stay accurate.

---

## 10. Quick recipes

```sh
# See the board as the skills see it
./tkt list --json | jq -r '.[] | "\(.status_role)\t\(.key)\t\(.summary)"' | sort

# Columns + counts (cheap text Kanban in the shell)
./tkt list --json | jq -r 'group_by(.status_role)[] | "\(.[0].status_role) (\(length))"'

# Make a ticket
./tkt create --type Story --summary "Add login" --priority High

# Move it (writes status + logs history)
./tkt transition TKT-1 in_progress

# What's blocking it, unresolved only
./tkt blockers TKT-1

# Validate the board model
./tkt doctor
```
