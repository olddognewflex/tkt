"""`tkt init` — scaffold a project: write .sdlc/config.toml from an example,
optionally link the skill pack into .claude/, and print next steps.

Runs BEFORE config exists, so it never calls Config.load().
"""
import shutil
import sys
from pathlib import Path

from . import toolchain
from .errors import ConfigError, UsageError

PACK_ROOT = Path(__file__).resolve().parent.parent


def available_providers() -> list[str]:
    ex = PACK_ROOT / "examples"
    return sorted(p.name[len("config."):-len(".toml")]
                  for p in ex.glob("config.*.toml"))


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
         link_skills: bool, sample: bool, interactive: bool,
         detect_build: bool = True) -> int:
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

    if detect_build:
        _seed_build(cfg, target)

    if sample and provider == "markdown":
        board = sdlc / "board"
        board.mkdir(parents=True, exist_ok=True)
        starter = board / "TKT-1.md"
        if not starter.exists() or force:
            import os
            starter.write_text(_STARTER_TICKET.format(me=os.environ.get("USER", "me")))
            print(f"wrote {starter}  (starter ticket; --sample)")

    if link_skills:
        _link_pack(target)

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
    if not link_skills:
        print("  4. Activate skills: `tkt sync-pack` (committed copies — best for "
              "cloud/CI harnesses like Copilot) or `tkt init --link-skills` (symlinks)")
    return 0


def _seed_build(cfg: Path, target: Path) -> None:
    """Replace the example's placeholder [build] commands with detected ones."""
    found, label = toolchain.detect(target)
    if not found:
        print("  no build/test commands detected — [build] left at example "
              "defaults (edit by hand)")
        return
    cfg.write_text(toolchain.apply_to_config(cfg.read_text(), found))
    print(f"  detected {label} toolchain; [build] set to:")
    for key in toolchain.KEYS:
        if key in found:
            print(f"    {key:<9} = {found[key]}")
    missing = [k for k in toolchain.KEYS if k not in found]
    if missing:
        print(f"  not declared by the project (left as-is): {', '.join(missing)}")


def _link_pack(target: Path) -> None:
    claude = target / ".claude"
    for kind in ("skills", "agents"):
        srcdir = PACK_ROOT / kind
        if not srcdir.exists():
            continue
        dstdir = claude / kind
        dstdir.mkdir(parents=True, exist_ok=True)
        for item in sorted(srcdir.iterdir()):
            link = dstdir / item.name
            if link.exists() or link.is_symlink():
                print(f"  skip (exists): {link}")
                continue
            link.symlink_to(item.resolve())
            print(f"  linked {link} -> {item.resolve()}")


def _tkt_invocation() -> str:
    exe = PACK_ROOT / "tkt"
    return str(exe) if exe.exists() else "tkt"
