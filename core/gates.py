"""Format gates on `tkt transition` and `tkt comment`.

Before a ticket moves into a late lane (review onwards), its body is run
through `enforcement/check-artifact-format.mjs`; before a comment is posted,
the comment is. `[enforcement].artifact_format` picks the mode: "warn" (the
default) prints findings to stderr and carries on, "strict" refuses with
GateError (exit 5), "off" never runs the checker.

A gate that cannot judge never blocks. No node, no checker script, an empty
or unavailable body, a checker crash, timeout or usage error, or a backend
error while fetching the ticket: each one means "cannot judge" and the verb
proceeds as if the gate were not there.

`check_hierarchy` adds advisory, pure-Python findings about test plans and
sub-tasks. They are printed as warnings and never block, even in strict mode.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .errors import ConfigError, GateError
from .schema import Check

# Process hooks, looked up at call time so tests can patch the gate's view of
# node without touching other users of subprocess/shutil.
_run_process = subprocess.run
_which = shutil.which

# Roles whose entry is gated. Earlier lanes are work in progress.
GATED_ROLES = frozenset({"review", "qa_ready", "qa", "deploy_ready", "done"})

# Resolved from tkt's own checkout: consumer repos have no enforcement/.
CHECKER = (Path(__file__).resolve().parent.parent / "enforcement"
           / "check-artifact-format.mjs")
RULES_NAME = "artifact-format.rules.json"
CHECKER_TIMEOUT = 15        # seconds

TEST_PLAN_HEADINGS = frozenset({"test plan", "testing", "test strategy"})
SUBTASK_MAX_WORDS = 150

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")


# ---- role resolution -------------------------------------------------------

def gated_role(config, target: str) -> str | None:
    """The gated role a transition to `target` enters, or None. `target`
    resolves as `role_to_lane` does (exact role, then exact lane), so a
    literal lane ("In Review") can't slip past. A lane shared by several
    roles is gated when any of them is."""
    if target in config.roles:
        lane = config.roles[target]
    elif target in config.roles.values():
        lane = target
    else:
        return None             # the adapter raises its own error
    if target in config.roles and target in GATED_ROLES:
        return target
    for role, mapped in config.roles.items():
        if mapped == lane and role in GATED_ROLES:
            return role
    return None


def is_gated(config, target: str) -> bool:
    return gated_role(config, target) is not None


def preset_for(settings: dict, issue_type: str) -> str:
    """The preset for a ticket body: `presets_by_type` (exact, then
    case-insensitive) else `preset`."""
    by_type = settings["presets_by_type"]
    if issue_type in by_type:
        return by_type[issue_type]
    folded = issue_type.casefold()
    for t, name in by_type.items():
        if t.casefold() == folded:
            return name
    return settings["preset"]


# ---- the checker -----------------------------------------------------------

def run_checker(kind: str, preset: str, body: str | None, *, runner=None,
                which=None, script=None) -> list[str] | None:
    """Finding lines (`<kind>: <rule-id>: <message>`) for `body`, or None
    when it cannot be judged. Runs without --strict and reads stderr, not the
    exit status; exit codes other than 0/1 mean the checker could not judge."""
    if not isinstance(body, str) or not body.strip():
        return None
    script = CHECKER if script is None else Path(script)
    if not script.is_file():
        return None
    node = (which or _which)("node")
    if not node:
        return None
    try:
        proc = (runner or _run_process)(
            [node, str(script), "--kind", kind, "--preset", preset],
            input=body, capture_output=True, text=True, encoding="utf-8",
            timeout=CHECKER_TIMEOUT)
    except Exception:       # OSError, TimeoutExpired, anything else
        return None
    if proc.returncode not in (0, 1):
        return None
    prefix = f"{kind}: "
    return [ln for ln in (proc.stderr or "").splitlines()
            if ln.startswith(prefix)]


# ---- hierarchy (advisory) --------------------------------------------------

def _heading(line: str) -> tuple[int, str] | None:
    """(level, normalized name) for an ATX heading, else None."""
    m = _HEADING.match(line)
    if not m:
        return None
    text = re.sub(r"(^|[ \t]+)#+$", "", m.group(2) or "")
    name = " ".join(text.split()).lower().rstrip(":").rstrip()
    return len(m.group(1)), name


def _prose_lines(body: str):
    """(line, heading) for each line outside fenced code and outside a
    `## Comments` section; heading is `_heading(line)`."""
    fence = ""
    in_comments = False
    for line in body.splitlines():
        if fence:
            m = _FENCE.match(line)
            if (m and m.group(1)[0] == fence[0]
                    and len(m.group(1)) >= len(fence) and not m.group(2).strip()):
                fence = ""
            continue
        m = _FENCE.match(line)
        if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
            fence = m.group(1)
            continue
        h = _heading(line)
        if h and h[1] and h[0] <= 2:
            in_comments = h[0] == 2 and h[1] == "comments"
        if in_comments:
            continue
        yield line, h


def has_test_plan(body: str) -> bool:
    return any(h and h[0] >= 2 and h[1] in TEST_PLAN_HEADINGS
               for _, h in _prose_lines(body))


def word_count(body: str) -> int:
    """Whitespace tokens with a letter or digit, outside fences and the
    `## Comments` section."""
    return sum(1 for line, _ in _prose_lines(body) for tok in line.split()
               if any(c.isalnum() for c in tok))


def _norm_type(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(t).lower())


def is_subtask(issue_type: str, subtask_types) -> bool:
    norm = _norm_type(issue_type)
    return bool(norm) and norm in {_norm_type(s) for s in subtask_types}


def _children(adapter, ticket) -> list[str]:
    """`ticket.children`, else best effort from a full view: the gate's own
    view skips hierarchy so a failed children lookup can't cost the format
    check. Local boards fill children either way."""
    if ticket.children:
        return list(ticket.children)
    try:
        return list(adapter.view(ticket.key).children or [])
    except Exception:
        return []


def check_hierarchy(config, adapter, ticket, body) -> list[str]:
    """Advisory findings about test plans and sub-task size, formatted
    `hierarchy: <rule-id>: <message>`. [] when the ticket can't be judged."""
    try:
        if not isinstance(body, str) or not body.strip():
            return []
        subtypes = config.enforcement()["subtask_types"]
        plan = has_test_plan(body)
        out = []
        subtask = is_subtask(ticket.type, subtypes)
        if subtask:
            words = word_count(body)
            if words > SUBTASK_MAX_WORDS:
                out.append(f"hierarchy: subtask-too-long: sub-task body is "
                           f"{words} words (max {SUBTASK_MAX_WORDS}); move "
                           f"detail to the parent")
            if plan:
                out.append("hierarchy: subtask-test-plan: sub-task has a "
                           "test plan heading; the test plan belongs on the "
                           "parent")
        # A sub-task has no sub-tasks of its own: skip the lookup.
        if not plan and not subtask:
            for child in _children(adapter, ticket):
                try:
                    c = adapter.view(child, hierarchy=False)
                except Exception:
                    continue
                if is_subtask(c.type, subtypes):
                    out.append(f"hierarchy: parent-test-plan: has sub-task "
                               f"{child} but no test plan heading (## Test "
                               f"plan, ## Testing or ## Test strategy)")
                    break
        return out
    except Exception:
        return []


# ---- the gates -------------------------------------------------------------

def _report(mode: str, what: str, findings: list[str],
            advisory: list[str]) -> None:
    strict = mode == "strict"
    for f in findings:
        print(f"tkt: {'error' if strict else 'warn'}: {f}", file=sys.stderr)
    for f in advisory:
        print(f"tkt: warn: {f}", file=sys.stderr)
    if strict and findings:
        n = len(findings)
        raise GateError(
            f"{what} blocked: {n} format finding{'s' if n != 1 else ''} "
            f"(strict mode). Fix and retry, or set "
            f'[enforcement].artifact_format = "warn" to report without blocking')


def gate_transition(config, adapter, key: str, target: str, **checker) -> None:
    """Judge `key`'s body before it moves to `target`. Returns to let the
    transition proceed; raises GateError in strict mode with findings.
    `checker` passes runner/which/script through to `run_checker`."""
    settings = config.enforcement()     # a bad table is exit 2, not ignored
    mode = settings["artifact_format"]
    if mode == "off":
        return
    role = gated_role(config, target)
    if role is None:
        return
    if not getattr(adapter, "has_artifact_body", False):
        return                          # nothing to judge; skip the view call
    try:
        ticket = adapter.view(key, hierarchy=False)
        body = adapter.artifact_body(ticket)
    except Exception:
        return
    if not isinstance(body, str) or not body.strip():
        return
    findings = run_checker("ticket", preset_for(settings, ticket.type), body,
                           **checker) or []
    advisory = check_hierarchy(config, adapter, ticket, body)
    _report(mode, f"{key}: transition to {role}", findings, advisory)


def gate_comment(config, key: str, body: str, **checker) -> None:
    """Judge a comment before it is posted. Comments always use `preset`,
    so this never costs a backend call."""
    settings = config.enforcement()
    mode = settings["artifact_format"]
    if mode == "off":
        return
    findings = run_checker("comment", settings["preset"], body, **checker) or []
    _report(mode, f"{key}: comment", findings, [])


# ---- doctor ----------------------------------------------------------------

def doctor_check(config, *, which=None, script=None) -> Check:
    """The `enforcement` doctor row. FAIL on an invalid table or a preset the
    rules file doesn't define; a missing node or checker is ok but inactive."""
    name = "enforcement"
    try:
        s = config.enforcement()
    except ConfigError as e:
        return Check(name, False, str(e))
    script = CHECKER if script is None else Path(script)
    rules = script.with_name(RULES_NAME)
    if rules.is_file():
        try:
            presets = json.loads(rules.read_text(encoding="utf-8"))["presets"]
            if not isinstance(presets, dict):
                raise TypeError("presets is not an object")
        except (OSError, ValueError, KeyError, TypeError) as e:
            return Check(name, False, f"cannot read {rules}: {e}")
        wanted = [s["preset"], *s["presets_by_type"].values()]
        unknown = sorted({p for p in wanted if p not in presets})
        if unknown:
            return Check(name, False,
                         f"unknown preset {', '.join(unknown)}. Defined in "
                         f"{RULES_NAME}: {', '.join(sorted(presets))}")
    mode = s["artifact_format"]
    if mode == "off":
        return Check(name, True, "off")
    detail = f"{mode}, preset {s['preset']}"
    if s["presets_by_type"]:
        detail += f", {len(s['presets_by_type'])} by type"
    if not script.is_file():
        detail += "; format check inactive: checker missing"
    elif not (which or _which)("node"):
        detail += "; format check inactive: node not found"
    return Check(name, True, detail)
