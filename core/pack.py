"""`tkt sync-pack` — install the SDLC skill pack into a consumer repo as
COMMITTED COPIES rather than symlinks.

Rationale: cloud harnesses (GitHub Copilot, CI) and anything that reads the repo
over git only see *tracked files*. Symlinks pointing at a home-dir clone of this
pack (what `tkt init --link-skills` writes) are invisible to them. sync-pack
copies the pack's files into the consumer tree so they can be committed.

Guarantees:
  * Only ever writes paths it records in .sdlc/pack-manifest.json. It deletes
    nothing unless asked: `--prune` removes files a previous sync wrote that
    the current selection no longer ships (see `_prune_candidates`).
  * Idempotent: a second run with an unchanged pack makes zero changes on disk
    (including the manifest), so `git status` stays clean.
  * Never touches content outside the AGENTS.md marker region it manages.
  * Carries the executable bit. Content hashes cannot see it and git runs a
    non-executable hook silently, so an executable pack file is chmod'ed on
    write, a lost bit is repaired even when the content already matches, and
    `--check` reports it as `not-executable`. "Executable" is git's rule
    (owner x); group/other follow the umask, as a checkout would. Bits are
    only ever added, a destination that resolves outside the consumer tree
    (a symlinked file or parent directory) is left alone, and a file that
    cannot be chmod'ed is a warning rather than an aborted sync.

Base layer: some dirs ship on every sync, whatever harnesses are selected
(`_BASE_LAYER`): the `.agents/skills` mirror, `.agents/workflows`,
`standards/` -> `docs/sdlc/` and `enforcement/` -> `.sdlc/enforcement/`. The
manifest records their dest prefixes as `base_layer` so `--prune` never
removes them. The `.agents/skills` and `.kiro/skills` mirrors are derived
from the canonical `skills/` at sync time; they are not stored in the pack.

Pruning: a file the last sync wrote that the current plan no longer ships is
"retired". A plain sync leaves it in place and records it under the
manifest's `retired` key, so a later `--prune` can still reach it. `--prune`
deletes it only when it is still byte-identical to what the pack wrote, and
only through a canonical manifest path inside a pack dest dir (`_PACK_DESTS`,
never under `.git`). The base layer, AGENTS.md and the manifest are never
pruned; a base-layer file the pack stops shipping is tracked under
`base_retired` so a later re-ship is not reported as pre-existing.

Runs BEFORE config exists (a consumer may sync before `tkt init`), so it never
calls Config.load(). Stdlib only.
"""
import fnmatch
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .errors import UsageError
from .schema import Check

PACK_ROOT = Path(__file__).resolve().parent.parent

MANIFEST_REL = ".sdlc/pack-manifest.json"
AGENTS_REL = "AGENTS.md"
# Distinct manifest key for the AGENTS.md marker-block hash (it is not a
# whole-file copy, so it can't share the file's own relpath).
AGENTS_MANIFEST_KEY = "AGENTS.md#tkt-pack"
BEGIN = "<!-- tkt-pack:begin -->"
END = "<!-- tkt-pack:end -->"

# Pack-internal meta skill; never shipped to a consumer.
_EXCLUDE_SKILLS = {"sync-skills"}

# Named harnesses. Each maps to (pack-relative source, consumer-relative dest)
# pairs. Most dirs are copied path-verbatim; the skill mirrors (Claude Code,
# Kiro, and the base layer's `.agents/skills`) all come from the canonical
# top-level skills/. Only files present in THIS repo are copied — an absent
# source is a no-op.
_HARNESSES: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "claude": ("Claude Code", [
        ("skills", ".claude/skills"),
        ("agents", ".claude/agents"),
    ]),
    "copilot": ("GitHub Copilot", [
        (".github/prompts", ".github/prompts"),
    ]),
    # Kiro does not read AGENTS.md — steering is its only project-convention
    # surface, so it ships by default rather than opt-in.
    "kiro": ("Kiro", [
        ("skills", ".kiro/skills"),          # derived, not stored
        (".kiro/steering", ".kiro/steering"),
        (".kiro/agents", ".kiro/agents"),
    ]),
    "cursor": ("Cursor", [
        (".cursor/skills", ".cursor/skills"),
        (".cursor/commands", ".cursor/commands"),
    ]),
    "gemini": ("Gemini CLI", [
        (".gemini/commands", ".gemini/commands"),
    ]),
    # Its dirs moved to the base layer. Kept so old manifests and
    # `tkt sync-pack agents` stay valid; alone it means "base layer only".
    "agents": ("Antigravity / Junie / Codex", []),
    "windsurf": ("Windsurf", [
        (".windsurf/workflows", ".windsurf/workflows"),
    ]),
    "cline": ("Cline", [
        (".clinerules/workflows", ".clinerules/workflows"),
    ]),
    "continue": ("Continue", [
        (".continue/prompts", ".continue/prompts"),
    ]),
    "augment": ("Augment", [
        (".augment/commands", ".augment/commands"),
    ]),
    "opencode": ("OpenCode", [
        (".opencode/commands", ".opencode/commands"),
    ]),
}

# Always installed, whatever the harness selection; never pruned. Codex
# needs no surface of its own: it reads these plus AGENTS.md.
_BASE_LAYER: list[tuple[str, str]] = [
    ("skills", ".agents/skills"),            # derived, not stored
    (".agents/workflows", ".agents/workflows"),
    ("standards", "docs/sdlc"),              # no-op until standards/ exists
    ("enforcement", ".sdlc/enforcement"),
]
# Recorded in the manifest as `base_layer`. Always the full list, even when
# a source is absent, so the manifest does not change when one appears.
_BASE_DESTS = [d for _, d in _BASE_LAYER]

# File-name globs left out of a tree, keyed by pack-relative source.
_EXCLUDE_GLOBS: dict[str, tuple[str, ...]] = {
    "enforcement": ("*.test.mjs", ".DS_Store"),  # self-tests, OS junk
}

# Installed unless the caller narrows the set.
_DEFAULT_HARNESSES = ("claude", "copilot", "kiro")


def harness_names() -> list[str]:
    return list(_HARNESSES)


# ---- hashing / small helpers ----------------------------------------------

def _sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _is_exec(mode: int) -> bool:
    """Git's rule: a file is executable (100755) iff its owner can run it.

    Not a comparison of the whole rwx triplet: the group/other bits come from
    whichever umask the pack and the consumer were each checked out under, so
    they legitimately differ, and a consumer hook at 0o750 is executable to
    git and would be wrongly flagged.
    """
    return bool(mode & stat.S_IXUSR)


def _grant_exec(dst: Path) -> bool:
    """Make `dst` executable the way a git checkout does; True if it changed.

    Content hashes cannot see the mode bit, and git runs a non-executable
    hook silently — so a consumer would believe it is protected when it is
    not. Adds x wherever r is already set, so the result respects the umask
    `dst` was written under (0o644 -> 0o755, 0o600 -> 0o700) instead of
    producing a mode like 0o711 or 0o751. Only ever adds bits.
    """
    mode = dst.stat().st_mode
    if _is_exec(mode):
        return False
    dst.chmod(mode | ((mode & 0o444) >> 2) | stat.S_IXUSR)
    return True


def _within(dst: Path, target: Path) -> bool:
    """True when `dst`, with every symlink in its path resolved, is inside
    `target` (already resolved). Works for a not-yet-created leaf: resolve
    is non-strict, so its existing parents are still followed."""
    return dst.resolve().is_relative_to(target)


def _try_grant_exec(dst: Path, rel: str, failed: list) -> int:
    """`_grant_exec`, but a file we cannot chmod (owned by another user, say
    after a container ran sync-pack) is recorded rather than aborting the
    whole sync half-way with a traceback. Returns 1 if the mode changed."""
    try:
        return int(_grant_exec(dst))
    except OSError as e:
        failed.append((rel, e.strerror or str(e)))
        return 0


def _walk(base: Path) -> list[Path]:
    if not base.exists():
        return []
    return sorted(p for p in base.rglob("*") if p.is_file())


def _git_head(root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(root),
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_manifest(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# ---- source → destination plan --------------------------------------------

def _add_tree(plan: list, base: Path, dest_prefix: str,
              exclude: tuple[str, ...] = ()) -> None:
    """Map every file under `base` to `<dest_prefix>/<relative path>`, skipping
    any pack-internal excluded skill dir and any file matching `exclude`."""
    for f in _walk(base):
        rel_in = f.relative_to(base)
        if rel_in.parts and rel_in.parts[0] in _EXCLUDE_SKILLS:
            continue
        if any(fnmatch.fnmatch(f.name, pat) for pat in exclude):
            continue
        plan.append((f, f"{dest_prefix}/{rel_in.as_posix()}"))


def _plan_for(harnesses: list[str]) -> list:
    """Source→dest plan for the base layer plus the selected harnesses,
    de-duplicated on dest so two entries sharing a directory can't queue the
    same write twice."""
    plan: list = []
    seen: set[str] = set()
    pairs = list(_BASE_LAYER)
    for name in harnesses:
        pairs += _HARNESSES[name][1]
    for src_rel, dest_rel in pairs:
        for src, rel in _sub_plan(src_rel, dest_rel):
            if rel in seen:
                continue
            seen.add(rel)
            plan.append((src, rel))
    return plan


def _sub_plan(src_rel: str, dest_rel: str) -> list:
    plan: list = []
    _add_tree(plan, PACK_ROOT / src_rel, dest_rel, _EXCLUDE_GLOBS.get(src_rel, ()))
    return plan


def _is_base(rel: str, prefixes: list[str] = _BASE_DESTS) -> bool:
    """True when consumer path `rel` sits in a base-layer dir. Matches whole
    path segments, so `.agents/skillsX/a` is not under `.agents/skills`."""
    return any(rel == p or rel.startswith(p + "/") for p in prefixes)


# ---- pruning ----------------------------------------------------------------

_DRIVE = re.compile(r"^[A-Za-z]:")

# Dest dirs the pack used in earlier versions but no longer writes. Files a
# previous sync left there are still the pack's to prune. Empty today: every
# dest dir the pack has ever written is still in use.
_RETIRED_DESTS: tuple[str, ...] = ()

# Every dir the pack writes into, now or before. Prune only acts inside these.
_PACK_DESTS = sorted({d for _, dirs in _HARNESSES.values() for _, d in dirs}
                     | set(_BASE_DESTS) | set(_RETIRED_DESTS))


def _canonical(rel) -> bool:
    """`rel` is in the form the pack writes: `a/b/c`, relative, with no
    empty, `.` or `..` parts, no backslash and no NUL."""
    if not isinstance(rel, str) or not rel or "\0" in rel or "\\" in rel:
        return False
    if rel.startswith("/") or _DRIVE.match(rel):
        return False
    return not any(p in ("", ".", "..") for p in rel.split("/"))


def _pack_owned(rel: str) -> bool:
    """`rel` sits in a dir the pack writes into, and never under `.git`."""
    if any(p.casefold() == ".git" for p in rel.split("/")):
        return False
    return _is_base(rel, _PACK_DESTS)


def _safe_rel(target: Path, rel) -> Path | None:
    """The consumer path for manifest entry `rel`, or None when prune must
    not act on it: a corrupted or tampered manifest must never make prune
    delete an arbitrary file.

    `rel` must be in the canonical form the pack writes (`a/b/c`: no empty,
    `.` or `..` parts, no backslash), so an alias of a live path such as
    `./a/b` or `a//b` is refused rather than compared as a different
    string. Every parent dir must be a real dir: a symlink anywhere in the
    middle, pointing inside or outside the tree, is refused. The leaf is not
    followed here; callers check `is_symlink()` themselves."""
    if not _canonical(rel):
        return None
    parts = rel.split("/")
    dst = target.joinpath(*parts)
    parent = dst.parent
    if not parent.exists():
        return dst                     # nothing can be there to remove
    if parent.resolve() != target.joinpath(*parts[:-1]):
        return None
    return dst


def _protected(rel: str, base_prefixes: list[str]) -> bool:
    """Paths prune never touches: the base layer, AGENTS.md (and its block
    key) and the manifest. Compared case-folded on a normalized path, so
    `./agents.md` on a case-insensitive disk is still AGENTS.md."""
    if not isinstance(rel, str):
        return False
    norm = "/".join(p for p in rel.replace("\\", "/").split("/")
                    if p not in ("", ".")).casefold()
    if norm in {AGENTS_REL.casefold(), AGENTS_MANIFEST_KEY.casefold(),
                MANIFEST_REL.casefold()}:
        return True
    return _is_base(norm, [b.casefold() for b in base_prefixes])


def _base_prefixes(old: dict) -> list[str]:
    """Current base-layer dests plus any an old manifest recorded. An old
    entry counts only when it is canonical and inside a pack dest dir, so a
    tampered manifest cannot point the identity walk outside the target."""
    old_base = old.get("base_layer")
    prefixes = list(_BASE_DESTS)
    if isinstance(old_base, list):
        prefixes += [b for b in old_base
                     if _canonical(b) and _is_base(b, _PACK_DESTS)
                     and b not in prefixes]
    return prefixes


def _str_dict(v) -> dict:
    """A manifest map of {rel: sha} with non-string values dropped."""
    if not isinstance(v, dict):
        return {}
    return {k: x for k, x in v.items() if isinstance(k, str) and isinstance(x, str)}


def _base_retired(target: Path, old: dict, old_files: dict, planned: set[str],
                  base_prefixes: list[str]) -> dict:
    """{rel: sha} of base-layer files a previous sync wrote that the pack no
    longer ships and that are still on disk. Never pruned; kept so a later
    re-ship knows the pack wrote them and does not warn they pre-existed."""
    out: dict = {}
    for rel, sha in {**_str_dict(old.get("base_retired")), **_str_dict(old_files)}.items():
        if rel in planned or not _canonical(rel) or not _is_base(rel, base_prefixes):
            continue
        try:
            os.lstat(target.joinpath(*rel.split("/")))
        except FileNotFoundError:
            continue
        except OSError:
            pass                       # can't tell: keep tracking it
        out[rel] = sha
    return out


def _prune_candidates(old: dict, old_files: dict, planned: set[str]) -> dict:
    """{rel: sha last written} for files a previous sync wrote (old `files`,
    plus anything already `retired`) that the current plan no longer ships,
    minus protected paths. A non-canonical `rel` stays in, so `_safe_rel`
    reports it as unsafe and it drops out of `retired`."""
    base_prefixes = _base_prefixes(old)
    old_retired = old.get("retired") if isinstance(old.get("retired"), dict) else {}
    out: dict = {}
    # `files` wins over `retired`: it holds the most recent write.
    for rel, sha in list(old_retired.items()) + list(old_files.items()):
        if rel in planned or _protected(rel, base_prefixes):
            continue
        out[rel] = sha if isinstance(sha, str) else None
    return out


def _file_id(path: Path) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def _walk_ids(root: Path, target: Path, ids: set) -> bool:
    """Add the (st_dev, st_ino) of every file under `root` to `ids`. Dir
    symlinks are followed when they stay inside `target` (a link a consumer
    adds under a base-layer dir can point at a pack file); one leading
    outside cannot reach a prune candidate, so it is skipped. Each real dir
    is entered once, so a link loop ends. False when some dir or entry
    could not be read: the set may then be incomplete."""
    ok = True
    seen: set = set()
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            if d != target and d.is_symlink():
                d.resolve().relative_to(target)
            st = os.stat(d)
            key = (st.st_dev, st.st_ino)
            if key in seen:
                continue
            seen.add(key)
            entries = list(os.scandir(d))
        except (FileNotFoundError, NotADirectoryError, ValueError):
            continue
        except OSError:
            ok = False
            continue
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=True):
                    stack.append(Path(e.path))
                else:
                    fid = _file_id(Path(e.path))
                    if fid:
                        ids.add(fid)
            except OSError:
                ok = False
    return ok


def _live_ids(target: Path, planned: set[str], base_prefixes: list[str],
              recorded: list[str] = (), problems: list | None = None) -> set:
    """(st_dev, st_ino) of every file prune must never remove: each planned
    dest, AGENTS.md, the manifest, every recorded base-layer path, and every
    file under a base-layer dir. Comparing identities, not strings, catches
    the aliases a string check misses: a case-only difference on a
    case-insensitive disk, a hard link, or a symlinked dir. `os.stat`
    follows links, and the walk follows dir links that stay in the tree, so
    a base path behind a symlink is covered whether recorded or not. A base
    dir that could not be fully read is appended to `problems`."""
    ids = set()
    base_recorded = [r for r in recorded
                     if _canonical(r) and _is_base(r, base_prefixes)]
    for rel in list(planned) + [AGENTS_REL, MANIFEST_REL] + base_recorded:
        fid = _file_id(target / rel)
        if fid:
            ids.add(fid)
    for prefix in base_prefixes:
        if not _walk_ids(target / prefix, target, ids) and problems is not None:
            problems.append(prefix)
    return ids


_BUCKETS = ("prune", "modified", "symlink", "unreadable", "unverified",
            "unsafe", "foreign", "notfile", "alias")


def _locate(target: Path, rel) -> tuple[str | None, Path | None]:
    """(refusal bucket or None, path) for one candidate. Neither set means
    it has vanished."""
    dst = _safe_rel(target, rel)
    if dst is None:
        return "unsafe", None
    if not _pack_owned(rel):
        return "foreign", None
    # lstat, not Path.exists()/is_symlink(): those swallow EACCES on newer
    # Pythons, which would make an unreadable file look vanished.
    try:
        st = os.lstat(dst)
    except (FileNotFoundError, NotADirectoryError):
        return None, None
    if stat.S_ISLNK(st.st_mode):
        return "symlink", dst
    if not stat.S_ISREG(st.st_mode):
        return "notfile", dst          # the pack only ever writes files
    return None, dst


def _classify_one(target: Path, rel: str, sha, live: set) -> str | None:
    """The bucket for one candidate, or None when it has vanished."""
    bucket, dst = _locate(target, rel)
    if bucket or dst is None:
        return bucket
    if _file_id(dst) in live:
        return "alias"
    cur = _sha_bytes(dst.read_bytes())
    # A missing or non-string sha never matches: keep and report it.
    return "prune" if isinstance(sha, str) and cur == sha else "modified"


def _classify_retired(target: Path, candidates: dict, live: set | None = None,
                      verified: bool = True) -> dict[str, list[str]]:
    """Sort candidates into prune (unchanged since the pack wrote it), kept
    (locally modified, symlink, unreadable) and skipped (unsafe path, not a
    file, same file as a live path). Vanished files are dropped: there is
    nothing left to remove."""
    res: dict[str, list[str]] = {k: [] for k in _BUCKETS}
    live = live or set()
    for rel in sorted(candidates):
        try:
            bucket = _classify_one(target, rel, candidates[rel], live)
        except OSError:
            # e.g. a parent dir with no permissions: keep it, carry it on.
            bucket = "unreadable"
        if bucket == "prune" and not verified:
            # Part of the base layer could not be read, so this file may
            # still be in use there: keep it rather than guess.
            bucket = "unverified"
        if bucket:
            res[bucket].append(rel)
    return res


def _prune_one(target: Path, rel: str, sha) -> str:
    """Re-check a file just before deleting it and delete it if nothing
    changed since it was classified. Returns "pruned", "gone" (already
    removed), a refusal bucket ("unsafe", "foreign", "symlink", "notfile"),
    "modified" (its content changed), or raises OSError."""
    try:
        bucket, dst = _locate(target, rel)
        if bucket:
            return bucket
        if dst is None:
            return "gone"
        if _sha_bytes(dst.read_bytes()) != sha:
            return "modified"
        dst.unlink()
    except FileNotFoundError:
        return "gone"
    _remove_empty_parents(dst, target)
    return "pruned"


def _remove_empty_parents(path: Path, target: Path) -> None:
    """rmdir `path`'s parents while they are empty, stopping at the first
    non-empty one, any error, or the consumer root (never removed)."""
    d = path.parent
    while d != target and target in d.parents:
        try:
            d.rmdir()
        except OSError:
            return
        d = d.parent


def _resolve_harnesses(requested: list[str], all_harnesses: bool,
                       manifest: dict) -> list[str]:
    """Selection is ADDITIVE and STICKY: the harnesses recorded by a previous
    sync stay installed, and `--harness X` adds X to that set permanently. This
    is what makes a later bare `tkt sync-pack` keep every harness up to date
    instead of silently reverting to the default three."""
    selected = set(_manifest_harnesses(manifest))
    if all_harnesses:
        selected |= set(_HARNESSES)
    for name in requested:
        if name not in _HARNESSES:
            raise UsageError(
                f"unknown harness '{name}'. Available: "
                f"{', '.join(_HARNESSES)} (or --list-harnesses)")
        selected.add(name)
    if not selected:
        selected = set(_DEFAULT_HARNESSES)
    # Stable order: registry order, which puts the defaults first.
    return [n for n in _HARNESSES if n in selected]


def _manifest_harnesses(manifest: dict) -> list[str]:
    """Harnesses a previous sync installed. Understands the pre-registry
    manifests that recorded only `harness_set: default|all`."""
    recorded = manifest.get("harnesses")
    if isinstance(recorded, list):
        return [n for n in recorded if n in _HARNESSES]
    if manifest.get("harness_set") == "all":
        return list(_HARNESSES)
    if manifest.get("harness_set") == "default":
        return list(_DEFAULT_HARNESSES)
    return []


def list_harnesses(target_dir: str) -> int:
    installed = set(_manifest_harnesses(
        _load_manifest(Path(target_dir).expanduser().resolve() / MANIFEST_REL)))
    print(f"  base layer (always installed): {', '.join(_BASE_DESTS)}")
    print()
    for name, (label, dirs) in _HARNESSES.items():
        mark = "*" if name in installed else " "
        default = " (default)" if name in _DEFAULT_HARNESSES else ""
        print(f" {mark} {name:<10} {label}{default}")
        print(f"     {', '.join(d for _, d in dirs) or '(base layer only)'}")
    print()
    print("  * = recorded in this project's pack manifest")
    print("  add one with: tkt sync-pack <name>")
    return 0


# ---- AGENTS.md managed block ----------------------------------------------

def _skill_names() -> list[str]:
    base = PACK_ROOT / "skills"
    if not base.exists():
        return []
    return sorted(
        d.name for d in base.iterdir()
        if d.is_dir() and d.name not in _EXCLUDE_SKILLS and (d / "SKILL.md").is_file()
    )


def _skill_oneliner(name: str) -> str:
    """First sentence of the skill's frontmatter `description`, best-effort."""
    try:
        text = (PACK_ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("description:"):
            v = s[len("description:"):].strip().strip("'\"")
            first = v.split(". ")[0].strip()
            if first and not first.endswith("."):
                first += "."
            return first
    return ""


def _agents_block() -> str:
    """Deterministic managed-block content. Stable across runs so its hash (and
    thus idempotency) holds as long as the installed skill set is unchanged."""
    lines = [
        BEGIN,
        "## SDLC skill pack (managed by `tkt sync-pack`)",
        "",
        "Ticketing is provider-agnostic via **`tkt`** — configure your backend in",
        "`.sdlc/config.toml`. These skills speak semantic verbs (`tkt view`,",
        "`tkt transition`, `tkt comment`, ...) and never call a backend API directly.",
        "",
        "Installed skills:",
        "",
    ]
    for name in _skill_names():
        one = _skill_oneliner(name)
        lines.append(f"- `{name}`" + (f" — {one}" if one else ""))
    lines += [
        "",
        "Re-run `tkt sync-pack` to refresh these committed copies. Do not edit this",
        "block by hand — everything between the markers is regenerated.",
        END,
    ]
    return "\n".join(lines)


def _extract_block(text: str) -> str | None:
    if BEGIN in text and END in text:
        return text[text.index(BEGIN):text.index(END) + len(END)]
    return None


def _splice_block(cur: str | None, block: str) -> str:
    """Return AGENTS.md text with the managed block set to `block`. Content
    outside the markers is preserved verbatim; when markers are absent the block
    is appended; when the file is absent it becomes just the block."""
    if cur is None:
        return block + "\n"
    if BEGIN in cur and END in cur:
        pre = cur[:cur.index(BEGIN)]
        post = cur[cur.index(END) + len(END):]
        return pre + block + post
    sep = "" if cur.endswith("\n") else "\n"
    return cur + sep + "\n" + block + "\n"


# ---- check-mode reporting --------------------------------------------------

def _report_check(missing: list, locally_mod: list, outdated: list,
                  not_exec: list) -> None:
    if not (missing or locally_mod or outdated or not_exec):
        print("sync-pack --check: pack is in sync")
        return
    for title, items in (
        ("missing", missing),
        ("locally-modified", locally_mod),
        ("out-of-date-vs-pack", outdated),
        # Content matches but the exec bit was lost — a re-sync repairs it.
        ("not-executable", not_exec),
    ):
        if items:
            print(f"{title} ({len(items)}):")
            for r in items:
                print(f"  {r}")


# ---- entry point -----------------------------------------------------------

def sync_pack(target_dir: str, all_harnesses: bool, check: bool,
              harnesses: list[str] | None = None, prune: bool = False) -> int:
    target = Path(target_dir).expanduser().resolve()

    # Guardrail: never install into the pack checkout itself.
    if target == PACK_ROOT or PACK_ROOT in target.parents:
        raise UsageError(
            f"--dir {target} is inside the pack checkout ({PACK_ROOT}); "
            "sync-pack installs INTO a separate consumer repo, not over the pack")

    manifest_path = target / MANIFEST_REL
    old = _load_manifest(manifest_path)
    old_files = old.get("files", {}) if isinstance(old.get("files"), dict) else {}
    old_retired = _str_dict(old.get("retired"))
    old_base_retired = _str_dict(old.get("base_retired"))

    def last_sha(rel: str) -> str | None:
        """What the pack last wrote at `rel`, from any manifest map."""
        for m in (old_files, old_retired, old_base_retired):
            v = m.get(rel)
            if isinstance(v, str):
                return v
        return None

    selected = _resolve_harnesses(harnesses or [], all_harnesses, old)
    added = [n for n in selected if n not in _manifest_harnesses(old)]
    plan = _plan_for(selected)

    new_files: dict[str, str] = {}
    missing: list[str] = []
    locally_mod: list[str] = []
    outdated: list[str] = []
    not_exec: list[str] = []
    # (relpath, error) for exec bits we could not set. Non-fatal: the rest of
    # the sync still completes, and `--check` keeps reporting the file.
    chmod_failed: list[tuple[str, str]] = []
    modes = 0
    # (relpath, reason) where reason is "modified" (drifted from last sync) or
    # "preexisting" (already on disk but never synced by us).
    warnings: list[tuple[str, str]] = []
    writes = 0

    for src, rel in plan:
        content = src.read_bytes()
        new_sha = _sha_bytes(content)
        new_files[rel] = new_sha
        dst = target / rel
        # Only change modes on files that really live in the consumer tree.
        # Checking `dst.is_symlink()` is not enough: `tkt init --link-skills`
        # symlinks whole skill *directories*, so a leaf that is a plain file
        # can still resolve through a symlinked parent to a file outside the
        # tree, and chmod would follow it there.
        want_exec = _is_exec(src.stat().st_mode) and _within(dst, target)

        if check:
            if not dst.exists():
                missing.append(rel)
                continue
            cur_sha = _sha_bytes(dst.read_bytes())
            # A retired file the pack ships again was still written by us.
            man_sha = last_sha(rel)
            if man_sha is not None and cur_sha != man_sha:
                locally_mod.append(rel)
            # A file both out of date and non-executable reports once, as
            # out-of-date: the re-sync that fixes the content also sets the bit.
            if cur_sha != new_sha:
                outdated.append(rel)
            elif want_exec and not _is_exec(dst.stat().st_mode):
                not_exec.append(rel)
            continue

        if dst.exists():
            cur_sha = _sha_bytes(dst.read_bytes())
            man_sha = last_sha(rel)
            if man_sha is None:
                # Present on disk but never synced by us (e.g. a consumer that
                # already had .github/prompts/select-ticket.prompt.md with its
                # own content). Don't clobber it silently.
                if cur_sha != new_sha:
                    warnings.append((rel, "preexisting"))
            elif cur_sha != man_sha:
                warnings.append((rel, "modified"))
            if cur_sha == new_sha:
                # Content already matches, but still repair a lost exec bit:
                # every consumer synced before modes were carried has the
                # right bytes and the wrong mode, and a content-only
                # comparison would skip it forever.
                if want_exec:
                    modes += _try_grant_exec(dst, rel, chmod_failed)
                continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(content)
        if want_exec:
            _try_grant_exec(dst, rel, chmod_failed)
        writes += 1

    # Files a previous sync wrote that this plan no longer ships. Live ids
    # are taken after the writes, so a file this run just wrote is covered.
    planned = {r for _, r in plan}
    base_prefixes = _base_prefixes(old)
    candidates = _prune_candidates(old, old_files, planned)
    live_problems: list[str] = []
    live = (_live_ids(target, planned, base_prefixes,
                      list(old_files) + list(old_base_retired), live_problems)
            if candidates else set())
    retired = _classify_retired(target, candidates, live,
                                verified=not live_problems)
    new_base_retired = _base_retired(target, old, old_files, planned, base_prefixes)

    # AGENTS.md managed block.
    block = _agents_block()
    block_sha = _sha_bytes(block.encode("utf-8"))
    new_files[AGENTS_MANIFEST_KEY] = block_sha
    agents_path = target / AGENTS_REL
    cur_agents = agents_path.read_text(encoding="utf-8") if agents_path.exists() else None
    man_block_sha = old_files.get(AGENTS_MANIFEST_KEY)

    if check:
        if cur_agents is None:
            missing.append(f"{AGENTS_REL} (tkt-pack block)")
        else:
            region = _extract_block(cur_agents)
            if region is None:
                missing.append(f"{AGENTS_REL} (tkt-pack block)")
            else:
                cur_region_sha = _sha_bytes(region.encode("utf-8"))
                if man_block_sha is not None and cur_region_sha != man_block_sha:
                    locally_mod.append(f"{AGENTS_REL} (tkt-pack block)")
                if cur_region_sha != block_sha:
                    outdated.append(f"{AGENTS_REL} (tkt-pack block)")
        print(f"harnesses: {', '.join(selected)}")
        print(f"base layer: {', '.join(_BASE_DESTS)}")
        _report_check(missing, locally_mod, outdated, not_exec)
        if prune:
            for title, key in (("would prune", "prune"),
                               ("kept, locally modified", "modified"),
                               ("kept, symlink", "symlink"),
                               ("kept, unreadable", "unreadable"),
                               ("kept, could not verify", "unverified"),
                               ("skipped, unsafe manifest path", "unsafe"),
                               ("skipped, not a pack path", "foreign"),
                               ("skipped, not a file", "notfile"),
                               ("skipped, same file as a live path", "alias")):
                if retired[key]:
                    print(f"{title} ({len(retired[key])}):")
                    for r in retired[key]:
                        print(f"  {r}")
        return 1 if (missing or locally_mod or outdated or not_exec
                     or (prune and retired["prune"])) else 0

    # write mode for AGENTS.md
    if cur_agents is not None:
        region = _extract_block(cur_agents)
        if region is not None and man_block_sha is not None \
                and _sha_bytes(region.encode("utf-8")) != man_block_sha:
            warnings.append((f"{AGENTS_REL} (tkt-pack block)", "modified"))
    new_agents = _splice_block(cur_agents, block)
    if new_agents != cur_agents:
        agents_path.parent.mkdir(parents=True, exist_ok=True)
        agents_path.write_text(new_agents, encoding="utf-8")
        writes += 1

    # Retired files: delete (with --prune) or carry forward under `retired`.
    pruned = 0
    prune_failed = 0
    keep = (retired["modified"] + retired["symlink"] + retired["unreadable"]
            + retired["unverified"])
    if prune:
        for rel in retired["prune"]:
            try:
                outcome = _prune_one(target, rel, candidates[rel])
            except OSError as e:
                print(f"warning: could not prune {rel} ({e})", file=sys.stderr)
                keep.append(rel)
                prune_failed += 1
                continue
            if outcome == "gone":
                continue
            if outcome != "pruned":
                # Changed since it was classified: report it in its new bucket.
                retired[outcome].append(rel)
                if outcome in ("modified", "symlink"):
                    keep.append(rel)
                continue
            print(f"pruned: {rel}")
            pruned += 1
        for label, key in (("locally modified", "modified"), ("symlink", "symlink"),
                           ("unreadable", "unreadable"),
                           ("could not verify", "unverified")):
            for rel in retired[key]:
                print(f"kept ({label}): {rel}")
        for prefix in live_problems:
            print(f"warning: could not fully read {prefix}; retired files kept "
                  f"until it is readable", file=sys.stderr)
    else:
        keep += retired["prune"]
    for rel in retired["unsafe"]:
        print(f"skipped unsafe manifest path: {rel}")
    for rel in retired["foreign"]:
        print(f"skipped (not a pack path): {rel}")
    for rel in retired["notfile"]:
        print(f"skipped (not a file): {rel}")
    for rel in retired["alias"]:
        print(f"skipped (same file as a live path): {rel}")
    new_retired = {rel: candidates[rel] for rel in sorted(keep)}

    for rel, reason in warnings:
        if reason == "preexisting":
            print(f"warning: pre-existing file overwritten: {rel}")
        else:
            print(f"warning: {rel} was locally modified since last sync — overwriting")
    for rel, err in chmod_failed:
        print(f"warning: could not make {rel} executable ({err}) — git will not "
              f"run it; fix its ownership and re-run sync-pack", file=sys.stderr)

    # Rewrite the manifest only when the material (non-timestamp) content
    # changed; otherwise leave it byte-for-byte identical so re-runs stay clean.
    material = {
        "pack_commit": _git_head(PACK_ROOT),
        "pack_root": str(PACK_ROOT),
        "harnesses": selected,
        "base_layer": _BASE_DESTS,
        "files": new_files,
        # Omitted when empty, so a consumer with nothing retired keeps a
        # byte-identical manifest.
        "retired": new_retired or None,
        # Never pruned; lets a re-shipped base file skip the pre-existing warning.
        "base_retired": new_base_retired or None,
    }
    old_material = {k: old.get(k) for k in
                    ("pack_commit", "pack_root", "harnesses", "base_layer", "files")}
    old_material["retired"] = old.get("retired") or None
    old_material["base_retired"] = old.get("base_retired") or None
    wrote_manifest = False
    if material != old_material or not manifest_path.exists():
        manifest = {k: v for k, v in material.items() if v is not None}
        manifest["synced_at"] = _now_iso()
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        wrote_manifest = True

    if added:
        print(f"harnesses added: {', '.join(added)}")
    # A failed repair is not "up to date": `--check` would report the file
    # as not-executable straight after.
    if retired["prune"] and not prune:
        print(f"note: {len(retired['prune'])} retired file(s) left in place; "
              f"re-run with --prune to remove them")
    if (writes == 0 and modes == 0 and not warnings and not wrote_manifest
            and not chmod_failed and not pruned and not prune_failed):
        print(f"sync-pack: up to date ({len(new_files)} entries) at {target}")
    else:
        summary = f"sync-pack: {writes} file(s) written to {target}"
        if pruned:
            summary += f", {pruned} pruned"
        if prune_failed:
            summary += f", {prune_failed} could NOT be pruned"
        if modes:
            summary += f", {modes} exec bit(s) repaired"
        if chmod_failed:
            summary += f", {len(chmod_failed)} exec bit(s) could NOT be set"
        if warnings:
            summary += f", {len(warnings)} pre-existing/locally-modified overwritten"
        print(summary)
        if wrote_manifest:
            print(f"  manifest: {manifest_path}")
    return 0


# ---- doctor integration ----------------------------------------------------

def doctor_check(config) -> Check | None:
    """Non-fatal staleness hint. Returns None when the consumer has no pack
    manifest (nothing to report), else an always-ok Check noting pack_commit and
    whether the pack checkout HEAD has moved since the last sync."""
    manifest_path = config.path.parent / "pack-manifest.json"
    if not manifest_path.exists():
        return None
    m = _load_manifest(manifest_path)
    commit = m.get("pack_commit", "unknown")
    head = _git_head(PACK_ROOT)
    if commit == "unknown" or head == "unknown":
        detail = f"pack_commit={commit} (pack HEAD unknown)"
    elif commit != head:
        detail = f"pack_commit={commit} differs from pack HEAD {head} — run `tkt sync-pack`"
    else:
        detail = f"pack_commit={commit} (in sync)"
    return Check(name="pack-manifest", ok=True, detail=detail)
