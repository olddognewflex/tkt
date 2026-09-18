"""What `tkt --version` prints.

The release number comes from `core.__version__`. Installs are usually a
symlink into a git checkout, where that number alone says nothing about which
build is running, so the checkout's `git describe --long` output is appended
when — and only when — the directory holding this package is itself the repo
root. `--long` keeps a commit in the output even on a tagged release.

A copy vendored into a subdirectory of someone else's repo prints the bare
number rather than the enclosing repo's commit. A repo whose root *is* tkt (a
fork, or a company copy made by scripts/company-import.sh) reports its own
commit; a company copy also carries a PACK_VERSION stamp, and the upstream
commit recorded there is appended too, since that is the one an upstream bug
report needs.

Best-effort by design: no git, a timeout, or any git error yields the bare
number, never an error. Only ever called when the flag is used, so ordinary
verbs (and a TUI polling `tkt agents`) never pay for a subprocess.
"""
import os
import re
import subprocess
from pathlib import Path

from . import __version__

ROOT = Path(__file__).resolve().parent.parent

# A stamp is written by our own script, but it is still a file on disk: accept
# only a hex commit, so a hand-edited or hostile stamp cannot print junk or
# terminal escape sequences.
_SHA = re.compile(r"^[0-9a-f]{7,64}$")   # SHA-1 or SHA-256 repos


def _git(root: Path, *args: str) -> str:
    # Drop inherited GIT_* variables. A git hook, `rebase -x` or
    # `submodule foreach` exports GIT_DIR/GIT_WORK_TREE for *their* repo, and
    # git honours those over `-C`, so tkt would report that repo's commit as
    # its own build.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, errors="replace", check=True, timeout=2,
                          env=env).stdout.strip()


def build_id(root: Path = ROOT) -> str:
    """`git describe` for this checkout (e.g. `8ef2cee-dirty`), or "" if none."""
    try:
        top = _git(root, "rev-parse", "--show-toplevel")
        # samefile, not a resolved-path compare: APFS is case-insensitive, so
        # the same directory can arrive spelled two ways.
        if not top or not os.path.samefile(top, root):
            return ""
        return _git(root, "describe", "--always", "--long", "--dirty", "--abbrev=7")
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""


def upstream_commit(root: Path = ROOT) -> str:
    """Upstream commit from a company copy's PACK_VERSION stamp, or ""."""
    try:
        text = (root / "PACK_VERSION").read_text(errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "commit" and _SHA.match(value.strip()):
            return value.strip()[:7]
    return ""


def version_string(root: Path = ROOT) -> str:
    """e.g. `X.Y.Z`, `X.Y.Z (8ef2cee-dirty)`, `X.Y.Z (a1b2c3d, upstream 16c6e23)`."""
    parts = []
    if bid := build_id(root):
        parts.append(bid)
    if up := upstream_commit(root):
        parts.append(f"upstream {up}")
    return f"{__version__} ({', '.join(parts)})" if parts else __version__
