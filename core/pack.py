"""`tkt sync-pack` — install the SDLC skill pack into a consumer repo as
COMMITTED COPIES rather than symlinks.

Rationale: cloud harnesses (GitHub Copilot, CI) and anything that reads the repo
over git only see *tracked files*. Symlinks pointing at a home-dir clone of this
pack (what `tkt init --link-skills` writes) are invisible to them. sync-pack
copies the pack's files into the consumer tree so they can be committed.

Guarantees:
  * Only ever writes paths it records in .sdlc/pack-manifest.json — never deletes.
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

Runs BEFORE config exists (a consumer may sync before `tkt init`), so it never
calls Config.load(). Stdlib only.
"""
import hashlib
import json
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
# pairs; every Tier-B dir is copied path-verbatim, so source == dest for all but
# Claude Code (whose sources are the pack's top-level skills/ and agents/).
# Only files present in THIS repo are copied — an absent source is a no-op.
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
        (".kiro/skills", ".kiro/skills"),
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
    "agents": ("Antigravity / Junie / Codex", [
        (".agents/skills", ".agents/skills"),
        (".agents/workflows", ".agents/workflows"),
    ]),
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

def _add_tree(plan: list, base: Path, dest_prefix: str) -> None:
    """Map every file under `base` to `<dest_prefix>/<relative path>`, skipping
    any pack-internal excluded skill dir."""
    for f in _walk(base):
        rel_in = f.relative_to(base)
        if rel_in.parts and rel_in.parts[0] in _EXCLUDE_SKILLS:
            continue
        plan.append((f, f"{dest_prefix}/{rel_in.as_posix()}"))


def _plan_for(harnesses: list[str]) -> list:
    """Source→dest plan for the selected harnesses, de-duplicated on dest so two
    harnesses sharing a directory can't queue the same write twice."""
    plan: list = []
    seen: set[str] = set()
    for name in harnesses:
        for src_rel, dest_rel in _HARNESSES[name][1]:
            for src, rel in _sub_plan(src_rel, dest_rel):
                if rel in seen:
                    continue
                seen.add(rel)
                plan.append((src, rel))
    return plan


def _sub_plan(src_rel: str, dest_rel: str) -> list:
    plan: list = []
    _add_tree(plan, PACK_ROOT / src_rel, dest_rel)
    return plan


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
    for name, (label, dirs) in _HARNESSES.items():
        mark = "*" if name in installed else " "
        default = " (default)" if name in _DEFAULT_HARNESSES else ""
        print(f" {mark} {name:<10} {label}{default}")
        print(f"     {', '.join(d for _, d in dirs)}")
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
              harnesses: list[str] | None = None) -> int:
    target = Path(target_dir).expanduser().resolve()

    # Guardrail: never install into the pack checkout itself.
    if target == PACK_ROOT or PACK_ROOT in target.parents:
        raise UsageError(
            f"--dir {target} is inside the pack checkout ({PACK_ROOT}); "
            "sync-pack installs INTO a separate consumer repo, not over the pack")

    manifest_path = target / MANIFEST_REL
    old = _load_manifest(manifest_path)
    old_files = old.get("files", {}) if isinstance(old.get("files"), dict) else {}

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
            man_sha = old_files.get(rel)
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
            man_sha = old_files.get(rel)
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
        _report_check(missing, locally_mod, outdated, not_exec)
        return 1 if (missing or locally_mod or outdated or not_exec) else 0

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
        "files": new_files,
    }
    old_material = {k: old.get(k) for k in ("pack_commit", "pack_root", "harnesses", "files")}
    wrote_manifest = False
    if material != old_material or not manifest_path.exists():
        manifest = dict(material)
        manifest["synced_at"] = _now_iso()
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        wrote_manifest = True

    if added:
        print(f"harnesses added: {', '.join(added)}")
    # A failed repair is not "up to date": `--check` would report the file
    # as not-executable straight after.
    if (writes == 0 and modes == 0 and not warnings and not wrote_manifest
            and not chmod_failed):
        print(f"sync-pack: up to date ({len(new_files)} entries) at {target}")
    else:
        summary = f"sync-pack: {writes} file(s) written to {target}"
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
