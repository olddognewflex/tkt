# Installing the SDLC skill pack in your repo

This guide is for an engineer adopting the provider-agnostic SDLC skill pack in a
repository. The pack is a CLI (`tkt`) plus a set of SDLC skills that install into
your repo as **committed files**, so every AI harness — and CI, and anything that
reads the repo over git — sees them.

## 1. Install the CLI

The CLI ships as a single entry script that locates its own `core/` and
`adapters/` relative to itself. Clone the pack once and put the script on your
`PATH`:

```sh
git clone <pack-repo-url> ~/.local/share/sdlc-pack
mkdir -p ~/.local/bin
ln -s ~/.local/share/sdlc-pack/tkt ~/.local/bin/tkt   # ensure ~/.local/bin is on PATH
tkt --help                                            # verify
```

A symlink target is fine — the script resolves back through it to the clone.
Requires Python 3.11+ (stdlib only, no third-party dependencies).

## 2. Adopt the pack in a repo

From the root of the repo you want to add the pipeline to:

```sh
cd my-repo

# a) Scaffold config for your ticketing backend.
tkt init --provider <jira|github|linear|markdown>

# b) Edit the generated config to match your board, queries, VCS, and build.
$EDITOR .sdlc/config.toml

# c) Validate auth + board model.
tkt doctor

# d) Install the pack files into the repo (committed copies).
tkt sync-pack

# e) Commit everything sync-pack and init produced.
git add .sdlc/config.toml .sdlc/pack-manifest.json .sdlc/enforcement .claude .github .kiro .agents AGENTS.md
git commit -m "chore: install SDLC skill pack"
```

**Commit `.sdlc/config.toml` and `.sdlc/pack-manifest.json`.** They are the
activation record for the pack — `sync-pack` reads the manifest to stay idempotent
and to detect drift. **Do not blanket-gitignore `.sdlc/`.** If you use the
`markdown` backend, ignore only its machine-local state directory
(`.sdlc/state/`), never the whole `.sdlc/` tree.

`tkt sync-pack` does not require config and can even run before `tkt init` — but
the order above (config first) is the sane path so `tkt doctor` can validate
before you install.

## 3. What gets installed

Every sync installs the **base layer**, then a **default harness set**. Name any
other harness to add it, or use `--all-harnesses` for the whole curated long
tail, path-verbatim.

### Base layer (always)

| Pack source | Destination | Notes |
|---|---|---|
| `skills/` | `.agents/skills/` | Agent Skills mirror, derived from `skills/` at sync time |
| `.agents/workflows/` | `.agents/workflows/` | condensed workflows (Antigravity, Junie) |
| `standards/` | `docs/sdlc/` | shipped once the pack has a `standards/` tree |
| `enforcement/` | `.sdlc/enforcement/` | checker scripts for consumer CI and hooks; `*.test.mjs` left out |

Codex reads the base layer plus `AGENTS.md`, so it needs no harness of its own.
Harness selection only adds surfaces on top: no selection, however narrow, drops
the base layer. The manifest records these dirs under `base_layer`, and
`--prune` never removes them. A file of your own at one of these paths is
overwritten, with a warning, on the first sync that ships it, after being
copied to `<name>.pre-sdlc.bak` beside it (the warning names the backup; an
existing backup is never overwritten, the next is `<name>.pre-sdlc.1.bak`).
Backups are never tracked in the manifest and never pruned. If the copy
fails, the file is left alone and the sync exits 1. Keep your own files
outside these dirs.

### Harnesses

| Name | Destination | Harness | Default |
|---|---|---|:---:|
| `claude` | `.claude/skills/` (19 skills) + `.claude/agents/` (6 subagents) | Claude Code | ✓ |
| `copilot` | `.github/prompts/` | GitHub Copilot | ✓ |
| `kiro` | `.kiro/skills/` (derived from `skills/`) + `.kiro/steering/` + `.kiro/agents/` | AWS Kiro (skills, project conventions, sub-agents) | ✓ |
| — | `AGENTS.md` managed block (`<!-- tkt-pack:begin/end -->`) | Universal fallback (Codex, Amp, Devin, …) — always written | ✓ |
| `cursor` | `.cursor/skills/` + `.cursor/commands/` | Cursor | |
| `gemini` | `.gemini/commands/` | Gemini CLI | |
| `agents` | — (served by the base layer; alone, installs only the base layer) | Antigravity, Junie, Codex | |
| `windsurf` | `.windsurf/workflows/` | Windsurf | |
| `cline` | `.clinerules/workflows/` | Cline | |
| `continue` | `.continue/prompts/` | Continue | |
| `augment` | `.augment/commands/` | Augment | |
| `opencode` | `.opencode/commands/` | OpenCode | |

### Adding a harness later

A project that was set up with the default set can pick up another harness at any
time — no re-init, no `--force`:

```sh
tkt sync-pack cursor              # add Cursor
tkt sync-pack cursor windsurf     # several at once
tkt sync-pack --list-harnesses    # names, dirs, and what this repo installs
```

The selection is **additive and sticky**: it is recorded under `harnesses` in
`.sdlc/pack-manifest.json`, so subsequent bare `tkt sync-pack` runs (and
`--check`) cover every added harness instead of reverting to the default three.
Because selection is sticky, removing a harness means dropping its name from
that manifest list and then running `tkt sync-pack --prune` (see §4).

The default set applies only when you name nothing **and** the manifest records
nothing. A repo set up with `tkt init --link-skills` has symlinks in `.claude/`
and no manifest, so `tkt sync-pack cursor` there installs the base layer,
`.cursor/` and the `AGENTS.md` block — it will not lay committed copies over
the `.claude/` symlinks.

### Your own skills

Skills you write yourself under `.claude/skills/<name>/SKILL.md` are mirrored
on every sync, whole dir and byte for byte, into each verbatim skill surface
the repo installs: `.agents/skills/` always, `.kiro/skills/` and
`.cursor/skills/` when those harnesses are selected. Copilot's
`.github/prompts/` and the other command dirs get no copy, because they take
a rewrite, not a copy. Write the skill once, in `.claude/skills/`, and re-run
`tkt sync-pack`; `--check` reports a copy that is older than its source.

- A skill named like one the pack ships is not mirrored: the pack's copy wins,
  and sync tells you. Rename yours to mirror it.
- A dir with no `SKILL.md` and a hidden dir are ignored, and so is a skill
  whose `SKILL.md` git ignores (a personal skill). Inside a skill, `.git`,
  `__pycache__`, gitignored files and anything that resolves outside the
  repo (a symlink into a home-dir clone) are left out.
- A copy dir sync-pack has never written into that already holds other files
  (your own Kiro or Cursor variant) is kept whole, never overwritten, and
  reported. A copy dir
  that is a symlink back to `.claude/skills/<name>` is left alone.
- Remove the skill and every copy is retired, `.agents/skills/` included;
  `tkt sync-pack --prune` deletes the unchanged ones.
- Upgrading tkt in a repo that already has its own skills: the first
  `tkt sync-pack --check` after the upgrade reports their copies as missing
  until you run `tkt sync-pack` once.

Notes:
- The `sync-skills` meta-skill is pack-internal and never shipped to a consumer.
- The `AGENTS.md` block is spliced in between markers; content outside the markers
  is preserved verbatim. Don't hand-edit inside the markers — it is regenerated.
- Only directories that exist in the pack are copied; empty ones are skipped.
  That is why `docs/sdlc/` does not appear until the pack ships `standards/`.

## 4. Updating

The pack is the single source of truth. To pull a newer version into your repos:

```sh
cd ~/.local/share/sdlc-pack && git pull      # update the pack clone once
cd my-repo && tkt sync-pack                  # re-install in each consumer repo
git add -A && git commit -m "chore: update SDLC skill pack"
```

Check for drift without writing anything:

```sh
tkt sync-pack --check    # lists missing / locally-modified / out-of-date / not-executable; exit 1 if any
```

After a pack update that adds files (such as a new base-layer dir), `--check`
lists them as **missing** until you re-run `tkt sync-pack` once.

`--check` reports four buckets: **missing** (pack file not installed here),
**locally-modified** (installed file changed since the last sync),
**out-of-date-vs-pack** (installed file differs from the current pack), and
**not-executable** (content matches, but the pack file is executable and the
installed copy is not — a re-sync repairs it).

**Executable bits are carried.** An executable pack file is installed executable,
and a lost bit is repaired even when the content already matches. "Executable"
follows git's rule — the owner can run it — and group/other bits follow your
umask, as a checkout would. Bits are only ever added: if the pack makes a file
non-executable later, installed copies keep their bit. A file sync-pack cannot
chmod (owned by another user) produces a warning rather than aborting, and
`--check` keeps reporting it. Note that a consumer repo with
`core.fileMode=false` will not record the bit in git, however sync-pack sets it.

**Overwrite-and-warn.** A normal `tkt sync-pack` restores installed pack files to
the pack's version. If a file was locally modified since the last sync, it is
overwritten and a warning is printed
(`warning: <path> was locally modified since last sync — overwriting`). This is
intentional: **customizations belong upstream in the pack, not in a consumer
repo.** If you need a skill to behave differently, change it in the pack and let
`sync-pack` propagate it everywhere.

A file at a pack path that sync-pack **never wrote** (no manifest entry) and
whose content differs is backed up before it is replaced:
`warning: pre-existing file overwritten: <path> (backup: <path>.pre-sdlc.bak)`.
This holds in every pack dir, harness dirs included. A file it cannot back
up (unreadable, or a failed copy) is left alone, reported on stderr, and the
sync exits 1.

**Symlinks out of the repo are never written through.** A pack path that
resolves outside the consumer tree, at the file itself or at any dir above
it, is skipped: a dangling symlink (its target is not created), a pack dir
such as `.kiro/steering` linked to a dir elsewhere, or a skill dir
`tkt init --link-skills` pointed at another checkout. Each prints
`skipped <path>: resolves outside the repo`, the sync still exits 0, and
`--check` does not report the path as missing. If the manifest already
records the path, the record is kept, so the file is not retired or pruned.
Symlinks that stay inside the repo keep working: linking `.kiro/steering`
to `docs/steering` in the same repo installs the files there. A path that
is a link to the very pack file it would copy is already current and is
left alone without a note.

`sync-pack` only ever writes the paths recorded in `.sdlc/pack-manifest.json`,
and deletes nothing unless you pass `--prune`. A re-run against an unchanged pack
makes zero changes on disk (the manifest included), so `git status` stays clean.

**Pruning retired files.** When the current selection stops shipping a file a
previous sync wrote (you dropped a harness from the manifest list, or the pack
removed a skill), that file is *retired*. A plain sync keeps it, records it under
`retired` in the manifest and prints a note, so a later prune can still find it.

```sh
tkt sync-pack --check --prune   # list what would be removed; writes nothing, exit 1 if any
tkt sync-pack --prune           # remove it
```

`--prune` only considers paths the previous manifest recorded, and only inside
the directories the pack writes into (never anything under `.git`). It never
touches the base layer, `AGENTS.md` or the manifest. It skips any recorded path
that is absolute, not in the form the pack writes, or goes through a symlinked
directory, and any path that turns out to be the same file as one still shipped
(for example a case-only rename on macOS, or a hard link). On Windows a path
whose on-disk case differs from the manifest is refused as unsafe instead: it
is left in place and dropped from `retired`, never deleted. A retired file you edited since the
last sync is kept and reported as `kept (locally modified)`; delete it yourself
once you have saved what you need. Empty directories left behind are removed.

## 5. Committed copies vs. symlinks

There are two ways to activate the pack; use committed copies for any shared repo.

- **`tkt sync-pack` — committed copies (recommended).** Writes real files into the
  repo tree so they are tracked by git. Cloud harnesses (GitHub Copilot, CI
  runners, review bots) and teammates only ever see **tracked files**, so this is
  the only option that works for a shared/CI repo.
- **`tkt init --link-skills` — symlinks.** Points `.claude/` at your home-dir pack
  clone. Convenient for solo local work, but the symlinks resolve to a path only
  on your machine — they are invisible to git, CI, and cloud harnesses. Not for
  shared repos.

## 6. Policy framing

Shipping resident config for a harness means the pack **technically supports** that
harness — it is **not** an endorsement to use it. Company policy governs which
harnesses, subscriptions, and models are approved for work. Today the approved
harnesses are **GitHub Copilot (Business)** and **AWS Kiro (Enterprise Pro)**.

The **default** `tkt sync-pack` set is deliberately narrow: the two company-approved
harnesses (Copilot, Kiro), the canonical Claude source the skills are authored in,
and the universal `AGENTS.md` fallback. The broader harness matrix is opt-in via
`--all-harnesses` — installing those files is a technical convenience for anyone
piloting a harness, and does not change what is approved for day-to-day work.

## 7. Scope note (v1)

**Cloud execution of skills is out of scope for v1.** A cloud coding agent (e.g.
GitHub Copilot's cloud agent) can *read* the committed prompts under
`.github/prompts/`, but the `tkt` CLI itself is not installed on cloud runners, so
skill steps that shell out to `tkt` will not execute there. Run the pipeline from a
local/IDE session where the CLI is on `PATH`.
