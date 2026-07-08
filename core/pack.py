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

Runs BEFORE config exists (a consumer may sync before `tkt init`), so it never
calls Config.load(). Stdlib only.
"""
import hashlib
import json
import subprocess
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

# Curated Tier-B / extra harness dirs, copied path-verbatim only with
# --all-harnesses. Only files present in THIS repo are copied.
_EXTRA_HARNESS_DIRS = [
    ".gemini/commands",
    ".agents/skills",
    ".cursor/skills",
    ".cursor/commands",
    ".windsurf/workflows",
    ".clinerules/workflows",
    ".continue/prompts",
    ".augment/commands",
    ".opencode/commands",
]


# ---- hashing / small helpers ----------------------------------------------

def _sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


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


def _default_plan() -> list:
    plan: list = []
    _add_tree(plan, PACK_ROOT / "skills", ".claude/skills")
    agent = PACK_ROOT / "agents" / "ticket-researcher.md"
    if agent.is_file():
        plan.append((agent, ".claude/agents/ticket-researcher.md"))
    _add_tree(plan, PACK_ROOT / ".github" / "prompts", ".github/prompts")
    _add_tree(plan, PACK_ROOT / ".kiro" / "skills", ".kiro/skills")
    return plan


def _extra_plan() -> list:
    plan: list = []
    for d in _EXTRA_HARNESS_DIRS:
        _add_tree(plan, PACK_ROOT / d, d)
    return plan


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

def _report_check(missing: list, locally_mod: list, outdated: list) -> None:
    if not (missing or locally_mod or outdated):
        print("sync-pack --check: pack is in sync")
        return
    for title, items in (
        ("missing", missing),
        ("locally-modified", locally_mod),
        ("out-of-date-vs-pack", outdated),
    ):
        if items:
            print(f"{title} ({len(items)}):")
            for r in items:
                print(f"  {r}")


# ---- entry point -----------------------------------------------------------

def sync_pack(target_dir: str, all_harnesses: bool, check: bool) -> int:
    target = Path(target_dir).expanduser().resolve()

    # Guardrail: never install into the pack checkout itself.
    if target == PACK_ROOT or PACK_ROOT in target.parents:
        raise UsageError(
            f"--dir {target} is inside the pack checkout ({PACK_ROOT}); "
            "sync-pack installs INTO a separate consumer repo, not over the pack")

    plan = _default_plan()
    harness_set = "default"
    if all_harnesses:
        plan += _extra_plan()
        harness_set = "all"

    manifest_path = target / MANIFEST_REL
    old = _load_manifest(manifest_path)
    old_files = old.get("files", {}) if isinstance(old.get("files"), dict) else {}

    new_files: dict[str, str] = {}
    missing: list[str] = []
    locally_mod: list[str] = []
    outdated: list[str] = []
    warnings: list[str] = []
    writes = 0

    for src, rel in plan:
        content = src.read_bytes()
        new_sha = _sha_bytes(content)
        new_files[rel] = new_sha
        dst = target / rel

        if check:
            if not dst.exists():
                missing.append(rel)
                continue
            cur_sha = _sha_bytes(dst.read_bytes())
            man_sha = old_files.get(rel)
            if man_sha is not None and cur_sha != man_sha:
                locally_mod.append(rel)
            if cur_sha != new_sha:
                outdated.append(rel)
            continue

        if dst.exists():
            cur_sha = _sha_bytes(dst.read_bytes())
            man_sha = old_files.get(rel)
            if man_sha is not None and cur_sha != man_sha:
                warnings.append(rel)
            if cur_sha == new_sha:
                continue  # idempotent no-touch
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(content)
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
        _report_check(missing, locally_mod, outdated)
        return 1 if (missing or locally_mod or outdated) else 0

    # write mode for AGENTS.md
    if cur_agents is not None:
        region = _extract_block(cur_agents)
        if region is not None and man_block_sha is not None \
                and _sha_bytes(region.encode("utf-8")) != man_block_sha:
            warnings.append(f"{AGENTS_REL} (tkt-pack block)")
    new_agents = _splice_block(cur_agents, block)
    if new_agents != cur_agents:
        agents_path.parent.mkdir(parents=True, exist_ok=True)
        agents_path.write_text(new_agents, encoding="utf-8")
        writes += 1

    for rel in warnings:
        print(f"warning: {rel} was locally modified since last sync — overwriting")

    # Rewrite the manifest only when the material (non-timestamp) content
    # changed; otherwise leave it byte-for-byte identical so re-runs stay clean.
    material = {
        "pack_commit": _git_head(PACK_ROOT),
        "pack_root": str(PACK_ROOT),
        "harness_set": harness_set,
        "files": new_files,
    }
    old_material = {k: old.get(k) for k in ("pack_commit", "pack_root", "harness_set", "files")}
    wrote_manifest = False
    if material != old_material or not manifest_path.exists():
        manifest = dict(material)
        manifest["synced_at"] = _now_iso()
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        wrote_manifest = True

    if writes == 0 and not warnings and not wrote_manifest:
        print(f"sync-pack: up to date ({len(new_files)} entries) at {target}")
    else:
        summary = f"sync-pack: {writes} file(s) written to {target}"
        if warnings:
            summary += f", {len(warnings)} locally-modified overwritten"
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
