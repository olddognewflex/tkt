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

# Markdown reading mirrors check-artifact-format.mjs: fences close on the
# same character, at least as long, with no info string; HTML comments are
# skipped as blocks (a line opening with "<!--") or inline on one line; a
# leading YAML frontmatter block is skipped when every line in it looks like
# YAML, else its opening "---" is a thematic break.
_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_FENCE_CLOSE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
_COMMENT_BLOCK = re.compile(r"^ {0,3}<!--")
_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_YAML_KEY = re.compile(r"^[ \t]*[\w.-]+[ \t]*:(?:[ \t].*)?$", re.ASCII)
_YAML_ITEM = re.compile(r"^[ \t]*-(?:[ \t].*)?$")
_YAML_COMMENT = re.compile(r"^[ \t]*#")


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

def _norm_name(text: str) -> str:
    return " ".join(text.split()).lower().rstrip(":").rstrip()


def _heading(line: str) -> tuple[int, str] | None:
    """(level, normalized name) for an ATX heading, else None."""
    m = _HEADING.match(line)
    if not m:
        return None
    text = re.sub(r"(^|[ \t]+)#+$", "", m.group(2) or "")
    return len(m.group(1)), _norm_name(text)


def _code_spans(line: str) -> list[tuple[int, int]]:
    """[start, end) of inline code spans: a run of N backticks closed by the
    next run of exactly N. A run with no closer is literal text."""
    runs = [(m.start(), len(m.group())) for m in re.finditer(r"`+", line)]
    nxt, last = [-1] * len(runs), {}
    for k in range(len(runs) - 1, -1, -1):
        nxt[k] = last.get(runs[k][1], -1)
        last[runs[k][1]] = k
    spans, a = [], 0
    while a < len(runs):
        b = nxt[a]
        if b < 0:
            a += 1
            continue
        spans.append((runs[a][0], runs[b][0] + runs[b][1]))
        a = b + 1
    return spans


def _strip_inline_comments(line: str) -> str:
    """`line` without the `<!-- ... -->` comments that close on it. One in a
    code span, or with no `-->` after it, is text."""
    spans = _code_spans(line)
    # Spans are sorted and asked about increasing offsets, so one cursor
    # walks them once (as the checker's stripInlineComments does).
    si = 0
    out, frm = [], 0
    start = line.find("<!--")
    while start >= 0:
        while si < len(spans) and spans[si][1] <= start:
            si += 1
        if si < len(spans) and start >= spans[si][0]:
            start = line.find("<!--", start + 1)
            continue
        end = line.find("-->", start + 4)
        if end < 0:
            break
        out.append(line[frm:start] + " ")
        frm = end + 3
        start = line.find("<!--", frm)
    return "".join(out) + line[frm:]


def _frontmatter_end(lines: list[str]) -> int:
    """Index of the first line after a leading YAML frontmatter block, or 0
    when there is none: no closing "---"/"...", or a line between the fences
    that doesn't look like YAML (then the opening "---" is a thematic
    break). Same test as the checker's frontmatterEnd."""
    if not lines or lines[0].lstrip("\ufeff").rstrip() != "---":
        return 0
    saw_key = False
    for j in range(1, len(lines)):
        line = lines[j].rstrip()
        if line in ("---", "..."):
            return j + 1
        if not line.strip() or _YAML_COMMENT.match(line):
            continue
        if _YAML_KEY.match(line):
            saw_key = True
        elif not (saw_key and (_YAML_ITEM.match(line) or line[:1] in " \t")):
            return 0
    return 0


def _scan(body: str):
    """(index, text, heading) for each line outside frontmatter and fenced
    code. Indices count every line of `body`, frontmatter included, so a cut
    lands on the right line. `text` has HTML comments removed; lines of a
    comment block (an HTML block) are never headings and cannot open a
    fence."""
    fence = ""
    in_comment = False
    lines = body.splitlines()
    start = _frontmatter_end(lines)
    for i, raw in enumerate(lines[start:], start):
        if fence:
            m = _FENCE_CLOSE.match(raw)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = ""
            continue
        if in_comment or _COMMENT_BLOCK.match(raw):
            rest = raw if in_comment else raw[raw.index("<!--") + 4:]
            end = rest.find("-->")
            in_comment = end < 0
            text = "" if in_comment else _strip_inline_comments(rest[end + 3:])
            yield i, text, None
            continue
        m = _FENCE_OPEN.match(raw)
        if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
            fence = m.group(1)
            continue
        text = _strip_inline_comments(raw)
        yield i, text, _heading(text)


def managed_tail_start(body: str, name: str) -> int | None:
    """Line index of the first `## <name>` heading (exact normalized name,
    outside fences and HTML comments), or None. Adapters that keep a log
    section at the end of a body (markdown Comments, openkanban Activity)
    cut there so the log is never judged as the ticket."""
    target = _norm_name(name)
    for i, _, h in _scan(body):
        if h and h[0] == 2 and h[1] == target:
            return i
    return None


def cut_managed_tail(body: str, name: str) -> str:
    """`body` up to its `## <name>` log section, or unchanged without one."""
    start = managed_tail_start(body, name)
    if start is None:
        return body
    return "\n".join(body.splitlines()[:start]).rstrip() + "\n"


def _prose_lines(body: str):
    """(text, heading) for each line outside fenced code, HTML comments and
    a `## Comments` section."""
    in_comments = False
    for _, text, h in _scan(body):
        if h and h[1] and h[0] <= 2:
            in_comments = h[0] == 2 and h[1] == "comments"
        if in_comments:
            continue
        yield text, h


def has_test_plan(body: str) -> bool:
    return any(h and h[0] >= 2 and h[1] in TEST_PLAN_HEADINGS
               for _, h in _prose_lines(body))


def word_count(body: str) -> int:
    """Whitespace tokens with a letter or digit, outside fences and the
    `## Comments` section."""
    return sum(1 for line, _ in _prose_lines(body) for tok in line.split()
               if any(c.isalnum() for c in tok))


def _norm_type(t: str) -> str:
    """Case-folded letters and digits only, any script: "Sub-task" and
    "subtask" match, "子任务" survives, "Tâche" stays apart from "Tche"."""
    return "".join(c for c in str(t).casefold() if c.isalnum())


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
    if script.is_file() and not rules.is_file():
        # Every check would exit 2 and silently pass: a broken install.
        return Check(name, False,
                     f"rules file missing: {rules}; format gate cannot run")
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
