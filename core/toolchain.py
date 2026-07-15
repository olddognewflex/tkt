"""Best-effort detection of a project's build/test/typecheck/lint commands.

Used by `tkt init` to seed `[build]` in the scaffolded config from whatever the
project already declares (package.json scripts, Cargo.toml, Makefile targets, ...)
instead of the example's placeholder `make build` / `true`.

Detection is advisory: a key is only reported when the project actually declares
something for it, so the example's value survives for everything else. Runs before
any config exists, so it never calls Config.load().
"""
import json
import re
import tomllib
from pathlib import Path

KEYS = ("build", "test", "typecheck", "lint")

# script name -> config key. First match (in this order) per key wins.
_NODE_SCRIPTS: dict[str, tuple[str, ...]] = {
    "build": ("build",),
    "test": ("test",),
    "typecheck": ("typecheck", "type-check", "types", "tsc", "check-types"),
    "lint": ("lint",),
}

_MAKE_TARGETS: dict[str, tuple[str, ...]] = {
    "build": ("build",),
    "test": ("test", "check"),
    "typecheck": ("typecheck", "type-check", "types"),
    "lint": ("lint",),
}


def detect(root: Path) -> tuple[dict[str, str], str]:
    """Return ({key: command}, ecosystem-label). Empty dict when nothing detected."""
    for probe in (_detect_node, _detect_rust, _detect_go, _detect_python, _detect_make):
        found, label = probe(root)
        if found:
            return found, label
    return {}, ""


# --- node ------------------------------------------------------------------

def _node_runner(root: Path, pkg: dict) -> str:
    declared = pkg.get("packageManager")
    if isinstance(declared, str) and declared:
        name = declared.split("@", 1)[0].strip()
        if name in ("pnpm", "yarn", "bun", "npm"):
            return name
    for lockfile, runner in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"),
                             ("bun.lockb", "bun"), ("bun.lock", "bun"),
                             ("package-lock.json", "npm")):
        if (root / lockfile).exists():
            return runner
    return "npm"


def _detect_node(root: Path) -> tuple[dict[str, str], str]:
    manifest = root / "package.json"
    if not manifest.exists():
        return {}, ""
    try:
        pkg = json.loads(manifest.read_text())
    except (OSError, json.JSONDecodeError):
        return {}, ""
    scripts = pkg.get("scripts")
    if not isinstance(scripts, dict):
        return {}, ""

    runner = _node_runner(root, pkg)
    found = {}
    for key, candidates in _NODE_SCRIPTS.items():
        for name in candidates:
            if isinstance(scripts.get(name), str):
                found[key] = f"{runner} run {name}"
                break
    return found, f"node ({runner})" if found else ""


# --- rust / go -------------------------------------------------------------

def _detect_rust(root: Path) -> tuple[dict[str, str], str]:
    if not (root / "Cargo.toml").exists():
        return {}, ""
    found = {"build": "cargo build", "test": "cargo test", "typecheck": "cargo check"}
    if (root / "clippy.toml").exists() or (root / ".clippy.toml").exists():
        found["lint"] = "cargo clippy -- -D warnings"
    return found, "rust (cargo)"


def _detect_go(root: Path) -> tuple[dict[str, str], str]:
    if not (root / "go.mod").exists():
        return {}, ""
    return {"build": "go build ./...", "test": "go test ./...",
            "typecheck": "go vet ./..."}, "go"


# --- python ----------------------------------------------------------------

def _python_runner(root: Path) -> str:
    if (root / "uv.lock").exists():
        return "uv run "
    if (root / "poetry.lock").exists():
        return "poetry run "
    return ""


def _detect_python(root: Path) -> tuple[dict[str, str], str]:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return {}, ""
    try:
        data = tomllib.loads(pyproject.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {}, ""

    # pyproject has no standard script table, so infer from the tools it configures
    # plus whatever the dependency lists name.
    tools = set(data.get("tool", {}))
    blob = json.dumps(data.get("project", {})) + json.dumps(data.get("dependency-groups", {}))
    names = tools | set(re.findall(r"[A-Za-z0-9_-]+", blob.lower()))

    run = _python_runner(root)
    found = {}
    if "pytest" in names:
        found["test"] = f"{run}pytest"
    if "mypy" in names:
        found["typecheck"] = f"{run}mypy ."
    elif "pyright" in names:
        found["typecheck"] = f"{run}pyright"
    if "ruff" in names:
        found["lint"] = f"{run}ruff check ."
    elif "flake8" in names:
        found["lint"] = f"{run}flake8"
    return found, "python" if found else ""


# --- make ------------------------------------------------------------------

def _make_targets(root: Path) -> set[str]:
    for name in ("Makefile", "makefile", "GNUmakefile"):
        path = root / name
        if not path.exists():
            continue
        try:
            text = path.read_text()
        except OSError:
            return set()
        return set(re.findall(r"^([A-Za-z0-9_.-]+)\s*:(?!=)", text, re.MULTILINE))
    return set()


def _detect_make(root: Path) -> tuple[dict[str, str], str]:
    targets = _make_targets(root)
    if not targets:
        return {}, ""
    found = {}
    for key, candidates in _MAKE_TARGETS.items():
        for name in candidates:
            if name in targets:
                found[key] = f"make {name}"
                break
    return found, "make" if found else ""


# --- config rewrite --------------------------------------------------------

_KEY_RE = re.compile(r"^(?P<key>[A-Za-z0-9_]+)(?P<pad>\s*)=")


def apply_to_config(text: str, found: dict[str, str]) -> str:
    """Rewrite `key = "..."` lines inside the config's [build] table.

    Only keys present in `found` are touched; alignment padding and every other
    table are left byte-identical. A [build] table with no line for a detected
    key gains one at the end of the table.
    """
    if not found:
        return text

    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines)
                  if line.strip() == "[build]"), None)
    if start is None:
        block = "\n[build]\n" + "".join(
            f'{k:<9} = "{v}"\n' for k, v in found.items() if k in KEYS)
        return text if not block.strip() else text.rstrip("\n") + "\n" + block

    end = next((i for i in range(start + 1, len(lines))
                if lines[i].lstrip().startswith("[")), len(lines))

    seen = set()
    for i in range(start + 1, end):
        m = _KEY_RE.match(lines[i].strip())
        if not m or m.group("key") not in found:
            continue
        key = m.group("key")
        seen.add(key)
        pad = _KEY_RE.match(lines[i].lstrip()).group("pad")
        lines[i] = f'{key}{pad}= "{found[key]}"\n'

    extra = [f'{k:<9} = "{v}"\n' for k, v in found.items()
             if k in KEYS and k not in seen]
    if extra:
        tail = end
        while tail > start + 1 and not lines[tail - 1].strip():
            tail -= 1
        lines[tail:tail] = extra
    return "".join(lines)
