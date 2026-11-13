"""Phase routing: `[harnesses]` + `[routes]` and `tkt cfg route <phase>`.

A phase (a skill name such as `self-review`) resolves to a harness command
and a model: an explicit `[routes]` entry first, then the skill's own
`model_tier` through `[models]`, then the plain `[run].harness_cmd`. Both
tables are validated on use, like `Config.enforcement()`, so a bad table
breaks only the verbs that read it.
"""
import json
import os
import re
import shlex
import shutil
from pathlib import Path

from .errors import ConfigError, NotFoundError
from .schema import Check

TIERS = ("cheap", "standard", "deep")
HARNESS_KEYS = ("cmd",)
ROUTE_KEYS = ("harness", "model", "tier")
# A phase is a skill dir name. Allow-listed, so no form of path (`..`, `a/b`,
# a Windows drive-relative `D:x`) can steer the frontmatter read.
_PHASE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_ENV_ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_PREFIX_BUILTINS = ("exec", "command", "builtin")

# tkt's own checkout, not the consumer's: the shipped skills carry the tiers.
SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def harnesses(config) -> dict[str, dict]:
    table = config._d.get("harnesses", {})
    if not isinstance(table, dict):
        raise ConfigError("[harnesses] must be a table of [harnesses.<name>] tables")
    for name, h in table.items():
        where = f"[harnesses.{name}]"
        if not isinstance(h, dict):
            raise ConfigError(f"{where} must be a table with a cmd key")
        for key in h:
            if key not in HARNESS_KEYS:
                raise ConfigError(f"{where}.{key}: unknown key. Known: "
                                  f"{', '.join(HARNESS_KEYS)}")
        cmd = h.get("cmd")
        if not isinstance(cmd, str) or not cmd.strip():
            raise ConfigError(f"{where}.cmd must be a non-empty string")
        if "{prompt}" not in cmd:
            raise ConfigError(f"{where}.cmd must contain {{prompt}}")
        try:
            shlex.split(cmd)
        except ValueError as e:
            raise ConfigError(f"{where}.cmd does not parse: {e}") from e
    return table


def routes(config) -> dict[str, dict]:
    table = config._d.get("routes", {})
    if not isinstance(table, dict):
        raise ConfigError("[routes] must be a table of phase = { harness, model | tier }")
    defined = harnesses(config)
    for phase, r in table.items():
        where = f"[routes].{phase}"
        if not isinstance(r, dict):
            raise ConfigError(f"{where} must be a table, e.g. "
                              f'{{ harness = "claude", tier = "deep" }}')
        if not r:
            raise ConfigError(f"{where} is empty: set harness, model or tier")
        for key in r:
            if key not in ROUTE_KEYS:
                raise ConfigError(f"{where}.{key}: unknown key. Known: "
                                  f"{', '.join(ROUTE_KEYS)}")
        if "model" in r and "tier" in r:
            raise ConfigError(f"{where}: set model or tier, not both")
        if "tier" in r and r["tier"] not in TIERS:
            raise ConfigError(f"{where}.tier = {r['tier']!r}: must be one of "
                              f"{', '.join(repr(t) for t in TIERS)}")
        if "model" in r:
            _check_model(f"{where}.model", r["model"])
        if "harness" in r and (not isinstance(r["harness"], str)
                               or r["harness"] not in defined):
            raise ConfigError(f"{where}.harness = {r['harness']!r}: no such harness. "
                              f"Defined: {', '.join(sorted(defined)) or '(none)'}")
    return table


def _check_model(where: str, model) -> None:
    if not isinstance(model, str) or not model.strip():
        raise ConfigError(f"{where} must be a non-empty string")
    # A brace would put a placeholder back into cmd for the caller to fill.
    if "{" in model or "}" in model:
        raise ConfigError(f"{where} = {model!r}: must not contain braces")


def skill_tier(phase: str) -> str:
    """The `model_tier` a shipped skill declares, or "" when there is none."""
    if not isinstance(phase, str) or not _PHASE_RE.fullmatch(phase):
        return ""
    try:
        text = (SKILLS_DIR / phase / "SKILL.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, val = line.partition(":")
        if sep and key.strip() == "model_tier":
            val = val.strip().strip("'\"")
            return val if val in TIERS else ""
    return ""


def _model_for(config, tier: str) -> str:
    # An empty value is unset, as it was before routes existed.
    if not tier or config.models.get(tier) in (None, ""):
        return ""
    _check_model(f"[models].{tier}", config.models[tier])
    return config.models[tier]


def resolve(config, phase: str) -> dict:
    defined = harnesses(config)
    table = routes(config)
    default_cmd = config.run.get("harness_cmd", "") if isinstance(config.run, dict) else ""
    default_cmd = default_cmd if isinstance(default_cmd, str) else ""

    if phase in table:
        r = table[phase]
        source, harness = "route", r.get("harness", "")
        if "model" in r:
            model, tier = r["model"], ""
        elif "tier" in r:
            tier = r["tier"]
            model = _model_for(config, tier)
            if not model:
                raise ConfigError(f"[routes].{phase}: tier '{tier}' has no [models].{tier}")
        else:
            tier = skill_tier(phase)
            model = _model_for(config, tier)
        cmd = defined[harness]["cmd"] if harness else default_cmd
    else:
        harness, tier = "", skill_tier(phase)
        model = _model_for(config, tier)
        cmd = default_cmd
        if model:
            source = "tier"
        elif default_cmd:
            source = "default"
        else:
            raise NotFoundError(
                f"no route for phase '{phase}': no [routes].{phase}, no [models] "
                f"entry for its tier, no [run].harness_cmd")

    if "{model}" in cmd:
        if not model:
            raise ConfigError(f"cmd for phase '{phase}' uses {{model}} but no model resolves")
        cmd = cmd.replace("{model}", shlex.quote(model))
    # {prompt} stays verbatim: the caller substitutes its own prompt.
    return {"phase": phase, "harness": harness, "model": model, "tier": tier,
            "cmd": cmd, "source": source}


def cmd_route(config, phase: str, as_json: bool) -> int:
    r = resolve(config, phase)
    if as_json:
        print(json.dumps(r, indent=2))
    else:
        for k in ("phase", "source", "harness", "model", "tier", "cmd"):
            print(f"{k + ':':<9}{r[k]}".rstrip())
    return 0


def _executable(cmd: str) -> str | None:
    """The program a shell would run for `cmd`, or None when it cannot be told
    without running a shell (a variable or placeholder left after expansion).
    Harness cmds run under `shell=True`, so `FOO=1 x`, `~/bin/x` and
    `"$HOME/x"` are all valid and must not read as a missing program."""
    for tok in shlex.split(cmd):
        if _ENV_ASSIGN.match(tok) or tok in _PREFIX_BUILTINS:
            continue
        tok = os.path.expandvars(os.path.expanduser(tok))
        return None if ("$" in tok or "{" in tok) else tok
    return None


def doctor_check(config, *, which=shutil.which) -> Check | None:
    if "harnesses" not in config._d and "routes" not in config._d:
        return None
    try:
        defined = harnesses(config)
        table = routes(config)
        # A route can be valid on its own and still fail every lookup (a
        # tier with no [models] entry), so resolve each one.
        for phase in table:
            resolve(config, phase)
    except ConfigError as e:
        return Check("routing", False, str(e))
    n = len(table)
    detail = (f"{n} route{'' if n == 1 else 's'}, "
              f"{len(defined)} harness{'' if len(defined) == 1 else 'es'}")
    used: dict[str, list[str]] = {}
    for phase, r in table.items():
        if r.get("harness"):
            used.setdefault(r["harness"], []).append(phase)
    # Warning only: doctor may run on a machine that never drives the harness.
    warnings = []
    for name, phases in sorted(used.items()):
        exe = _executable(defined[name]["cmd"])
        where = f"(routes: {', '.join(phases)})"
        if exe is None:
            warnings.append(f"could not check {name}'s executable {where}")
        elif not which(exe):
            warnings.append(f"{name} not on PATH {where}")
    if warnings:
        detail += "; warning: " + "; ".join(warnings)
    return Check("routing", True, detail)
