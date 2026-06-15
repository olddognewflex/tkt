"""`tkt init` — scaffold a project: write .sdlc/config.toml from an example,
optionally install the skill pack into one or more agent harnesses, and print
next steps.

Runs BEFORE config exists, so it never calls Config.load().
"""
import shutil
import sys
import tomllib
from pathlib import Path

from .errors import ConfigError, UsageError

PACK_ROOT = Path(__file__).resolve().parent.parent


def available_providers() -> list[str]:
    ex = PACK_ROOT / "examples"
    return sorted(p.name[len("config."):-len(".toml")]
                  for p in ex.glob("config.*.toml"))


def available_harnesses() -> list[str]:
    root = PACK_ROOT / "harnesses"
    if not root.exists():
        return []
    names = []
    for manifest in root.glob("*/manifest.toml"):
        try:
            with manifest.open("rb") as f:
                data = tomllib.load(f)
            if "harness" in data and "name" in data["harness"]:
                names.append(data["harness"]["name"])
        except Exception:
            continue
    return sorted(names)


_STARTER_TICKET = """\
---
type: Story
status: To Do
priority: Highest
assignee: {me}
blocked_by: []
blocks: []
---
# First ticket — replace me

Created by `tkt init --sample`. Edit or delete this file.

## Acceptance
- the board lists this ticket
"""


def init(provider: str | None, target_dir: str, force: bool,
         link_skills: bool, link_harness: str, global_: bool,
         sample: bool, interactive: bool) -> int:
    providers = available_providers()

    if not provider:
        if interactive and sys.stdin.isatty():
            print("Pick a ticketing backend:")
            for i, p in enumerate(providers, 1):
                print(f"  {i}) {p}")
            choice = input("> ").strip()
            provider = (providers[int(choice) - 1] if choice.isdigit()
                        and 1 <= int(choice) <= len(providers) else choice)
        else:
            raise UsageError(
                "init needs --provider <name> (or run interactively). "
                f"Available: {', '.join(providers)}")
    if provider not in providers:
        raise ConfigError(f"no example config for provider '{provider}'. "
                          f"Available: {', '.join(providers)}")

    target = Path(target_dir).expanduser().resolve()
    sdlc = target / ".sdlc"
    cfg = sdlc / "config.toml"
    src = PACK_ROOT / "examples" / f"config.{provider}.toml"

    if cfg.exists() and not force:
        raise ConfigError(f"{cfg} already exists (use --force to overwrite)")

    sdlc.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, cfg)
    print(f"wrote {cfg}  (from examples/config.{provider}.toml)")

    if sample and provider == "markdown":
        board = sdlc / "board"
        board.mkdir(parents=True, exist_ok=True)
        starter = board / "TKT-1.md"
        if not starter.exists() or force:
            import os
            starter.write_text(_STARTER_TICKET.format(me=os.environ.get("USER", "me")))
            print(f"wrote {starter}  (starter ticket; --sample)")

    if link_skills:
        print(
            "warning: --link-skills is deprecated; use --link-harness claude instead",
            file=sys.stderr,
        )
        _link_harness(target, "claude", global_)

    if link_harness:
        harnesses = available_harnesses()
        requested = harnesses if link_harness == "all" else [link_harness]
        for name in requested:
            if name not in harnesses:
                raise ConfigError(
                    f"unknown harness '{name}'. Available: {', '.join(harnesses)}"
                )
        for name in requested:
            _link_harness(target, name, global_)

    print()
    print("Next steps:")
    print(f"  1. Edit {cfg}")
    if provider == "jira":
        print("  2. Export CONFLUENCE_SITE / CONFLUENCE_EMAIL / CONFLUENCE_API_TOKEN")
    elif provider == "linear":
        print("  2. Export LINEAR_API_KEY")
    elif provider == "github":
        print("  2. gh auth login (and `-s project,read:project` for projectv2 boards)")
    else:
        print("  2. (markdown: no auth needed)")
    print(f"  3. {_tkt_invocation()} doctor")
    if not link_harness and not link_skills:
        print("  4. Activate skills: tkt init --link-harness <claude|opencode|all>")
    return 0


def _load_manifest(name: str) -> dict:
    manifest = PACK_ROOT / "harnesses" / name / "manifest.toml"
    if not manifest.exists():
        raise ConfigError(f"missing harness manifest: {manifest}")
    with manifest.open("rb") as f:
        return tomllib.load(f)


def _link_harness(target: Path, name: str, global_: bool) -> None:
    data = _load_manifest(name)
    harness = data.get("harness", {})
    mappings = harness.get("mappings", [])
    if not mappings:
        print(f"  harness '{name}' has no mappings; skipping")
        return

    print(f"  linking harness: {name}")
    for mapping in mappings:
        kind = mapping.get("kind")
        source = mapping.get("source")
        target_key = "global_target" if global_ else "target"
        dest_template = mapping.get(target_key)
        if not kind or not source or not dest_template:
            continue

        srcdir = PACK_ROOT / source
        if not srcdir.exists():
            continue

        dest = Path(dest_template.replace("~", str(Path.home())))
        if not dest.is_absolute():
            dest = target / dest
        dest = dest.expanduser().resolve()
        dest.mkdir(parents=True, exist_ok=True)

        for item in sorted(srcdir.iterdir()):
            link = dest / item.name
            if link.exists() or link.is_symlink():
                print(f"    skip (exists): {link}")
                continue
            link.symlink_to(item.resolve())
            print(f"    linked {link} -> {item.resolve()}")


def _tkt_invocation() -> str:
    exe = PACK_ROOT / "tkt"
    return str(exe) if exe.exists() else "tkt"
