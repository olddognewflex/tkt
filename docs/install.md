# Installing the SDLC skill pack in your repo

This guide is for an engineer adopting the provider-agnostic SDLC skill pack in a
repository. The pack is a CLI (`tkt`) plus a set of SDLC skills that install into
your repo as **committed files**, so every AI harness — and CI, and anything that
reads the repo over git — sees them.

For a worked, backend-specific example, see
[Migrating edge-dev-platform to the pack](migrating-edge-dev-platform.md).

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
git add .sdlc/config.toml .sdlc/pack-manifest.json .claude .github .kiro AGENTS.md
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

`tkt sync-pack` copies a **default harness set**. Add `--all-harnesses` to also
install the curated long tail of other harnesses, path-verbatim.

| Destination | Harness | Default | `--all-harnesses` |
|---|---|:---:|:---:|
| `.claude/skills/` (14 skills) | Claude Code | ✓ | ✓ |
| `.claude/agents/ticket-researcher.md` | Claude Code (lookup subagent) | ✓ | ✓ |
| `.github/prompts/` | GitHub Copilot | ✓ | ✓ |
| `.kiro/skills/` | AWS Kiro | ✓ | ✓ |
| `AGENTS.md` managed block (`<!-- tkt-pack:begin/end -->`) | Universal fallback (Codex, Amp, Devin, …) | ✓ | ✓ |
| `.gemini/commands/` | Gemini CLI | | ✓ |
| `.agents/skills/` | Antigravity, Junie, Codex | | ✓ |
| `.cursor/skills/` + `.cursor/commands/` | Cursor | | ✓ |
| `.windsurf/workflows/` | Windsurf | | ✓ |
| `.clinerules/workflows/` | Cline | | ✓ |
| `.continue/prompts/` | Continue | | ✓ |
| `.augment/commands/` | Augment | | ✓ |
| `.opencode/commands/` | OpenCode | | ✓ |

Notes:
- The `sync-skills` meta-skill is pack-internal and never shipped to a consumer.
- The `AGENTS.md` block is spliced in between markers; content outside the markers
  is preserved verbatim. Don't hand-edit inside the markers — it is regenerated.
- Only directories that exist in the pack are copied; empty ones are skipped.

## 4. Updating

The pack is the single source of truth. To pull a newer version into your repos:

```sh
cd ~/.local/share/sdlc-pack && git pull      # update the pack clone once
cd my-repo && tkt sync-pack                  # re-install in each consumer repo
git add -A && git commit -m "chore: update SDLC skill pack"
```

Check for drift without writing anything:

```sh
tkt sync-pack --check    # lists missing / locally-modified / out-of-date files; exit 1 if any
```

`--check` reports three buckets: **missing** (pack file not installed here),
**locally-modified** (installed file changed since the last sync), and
**out-of-date-vs-pack** (installed file differs from the current pack).

**Overwrite-and-warn.** A normal `tkt sync-pack` restores installed pack files to
the pack's version. If a file was locally modified since the last sync, it is
overwritten and a warning is printed
(`warning: <path> was locally modified since last sync — overwriting`). This is
intentional: **customizations belong upstream in the pack, not in a consumer
repo.** If you need a skill to behave differently, change it in the pack and let
`sync-pack` propagate it everywhere.

`sync-pack` never deletes files and only ever writes the paths recorded in
`.sdlc/pack-manifest.json`. A re-run against an unchanged pack makes zero changes
on disk (the manifest included), so `git status` stays clean.

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

## 6. Policy framing (Auction Edge)

Shipping resident config for a harness means the pack **technically supports** that
harness — it is **not** an endorsement to use it. Company policy governs which
harnesses, subscriptions, and models are approved for work. Today the approved
harnesses are **GitHub Copilot (Business)** and **AWS Kiro (Enterprise Pro)**.
This mirrors the stance in `edge-dev-platform` ADR-0013 (AI-tooling policy).

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
