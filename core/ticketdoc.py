"""Canonical full-ticket markdown document — the editor-buffer format that
`tkt apply` ingests and that TKB-10's $EDITOR flow round-trips. Backend-agnostic:
adapters map the parsed result onto their own storage (markdown writes it almost
verbatim; jira splits summary/description and maps frontmatter to fields).

Shape (frontmatter optional; body is GitHub-flavored markdown):

    ---
    type: Story
    priority: Medium
    assignee: raymond
    labels: [a, b]
    blocked_by: []
    blocks: []
    ---
    # One-line summary

    Free-form description.

    ## Acceptance
    - criterion one

    ## Comments          # backend-managed; `apply` never rewrites it
    - 2026-06-02T10:00:00Z raymond: started work

`apply` owns the frontmatter (except `status`, which only `transition` changes so
lane history stays correct) and the whole body EXCEPT backend-managed sections
(`MANAGED_SECTIONS`), which are preserved verbatim from the stored ticket.

A backend maps only the keys it can store and ignores the rest. `children:
[KEY, ...]` and `parent: KEY` are such keys: markdown stores them (one side
only; `tkt link --type child|parent` keeps both sides in step), while
backends that derive hierarchy from their own links (jira, github, linear)
ignore them, so they are deliberately not in the shared `TEMPLATE`.
"""
from __future__ import annotations

import re

# Body section headings `apply` must not overwrite — the backend manages them
# (e.g. the timestamped comment log appended by `tkt comment`). Compared
# case-insensitively against each `## ` heading.
MANAGED_SECTIONS = ("comments",)


def parse_scalar(raw: str):
    """The tiny frontmatter value grammar: `[a, b]` list literals, else a bare
    (optionally quoted) scalar. Mirrors the markdown adapter's reader."""
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [x.strip().strip("'\"") for x in inner.split(",") if x.strip()]
    return raw.strip("'\"")


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter, body). Body is everything after the closing `---`,
    or the whole text when there is no frontmatter block."""
    fm: dict[str, object] = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            block = text[3:end].strip("\n")
            body = text[end + 4:].lstrip("\n")
            for line in block.splitlines():
                if ":" not in line:
                    continue
                k, _, v = line.partition(":")
                fm[k.strip()] = parse_scalar(v)
    return fm, body


def render_frontmatter(fm: dict) -> str:
    """The `---` block, in the same shape the markdown adapter writes."""
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(str(x) for x in v)}]")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)


def summary_of(body: str) -> str:
    """The first `# ` heading's text (the ticket summary), or ""."""
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return ""


def body_without_summary(body: str) -> str:
    """Body with the first `# summary` heading line removed, for backends that
    store the summary as a separate field (e.g. jira)."""
    out, dropped = [], False
    for line in body.splitlines():
        if not dropped and line.strip().startswith("# "):
            dropped = True
            continue
        out.append(line)
    return "\n".join(out).strip()


# Fences as CommonMark (and enforcement/check-artifact-format.mjs) read them:
# up to three spaces of indent, three or more backticks or tildes; a backtick
# fence's info string holds no backtick; the close uses the same character,
# is at least as long, and has no info string. An unclosed fence runs to EOF.
_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_FENCE_CLOSE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")


def code_mask(lines: list[str]) -> list[bool]:
    """For each line, True when it is a fence line or inside a fenced code
    block, so it is content and never structure."""
    out: list[bool] = []
    fence = ""
    for line in lines:
        if fence:
            m = _FENCE_CLOSE.match(line)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = ""
            out.append(True)
            continue
        m = _FENCE_OPEN.match(line)
        if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
            fence = m.group(1)
            out.append(True)
            continue
        out.append(False)
    return out


def indent_of(line: str) -> str:
    """The line's leading spaces and tabs."""
    return line[:len(line) - len(line.lstrip(" \t"))]


def h2_text(line: str) -> str | None:
    """The text of a `## ` heading line, or None. Four or more columns of
    indent (a tab counts as four) make the line indented code, not a
    heading. Callers skip fenced lines first (`code_mask`)."""
    lead = indent_of(line)
    if "\t" in lead or len(lead) > 3:
        return None
    s = line.strip()
    return s[3:].strip() if s.startswith("## ") else None


def is_list_marker(s: str, bullets: str = "-*") -> bool:
    """`s` (a stripped line) opens a list item: a bullet followed by
    whitespace or nothing. `-flag` or `*word*` is text, not an item."""
    return bool(s) and s[0] in bullets and (len(s) == 1 or s[1] in " \t")


_ACCEPTANCE_HEADINGS = ("## acceptance", "acceptance criteria", "### acceptance")
_ORDERED_MARKER = re.compile(r"^\d{1,9}[.)](?:[ \t]|$)")
_ATX_HEADING = re.compile(r"^#{1,6}(?:[ \t]|$)")


def _structural(line: str) -> bool:
    """At most three spaces of indent and no tab: deeper is indented code
    (or text inside a list item), so not a heading."""
    lead = indent_of(line)
    return "\t" not in lead and len(lead) <= 3


def _opens_item(s: str) -> bool:
    """`s` (stripped) opens a list item: a `-`, `*`, `•` or `+` bullet, or
    an ordered `1.` / `1)` marker, followed by whitespace or nothing."""
    return is_list_marker(s, "-*•+") or bool(_ORDERED_MARKER.match(s))


def _item_text(s: str) -> str | None:
    """The text of the list item `s` (stripped) opens, or None. Any line
    starting `-`, `*` or `•` is an item, as it always was here; `+` and
    ordered markers need the whitespace after them."""
    if s.startswith(("-", "*", "•")):
        return s.lstrip("-*• ").strip()
    if is_list_marker(s, "+"):
        return s[1:].strip()
    m = _ORDERED_MARKER.match(s)
    return s[m.end():].strip() if m else None


def extract_acceptance(body: str | None) -> list[str]:
    """Acceptance items of a free-form issue description, as the github,
    linear and openkanban backends store one. (The markdown board reads
    its own layout in `adapters/markdown.py`'s `_read_body`.)

    Every list item under an acceptance heading is one criterion, nested
    ones included, bulleted (`-`, `*`, `•`, `+`) or ordered (`1.`,
    `1)`); the marker is dropped. The section ends at the next heading
    or at unindented prose. An indented line right after an item
    continues it, joined with one space, unless it opens a list item or
    is a heading. A blank line or a fence ends the item; an indented
    line that continues no item (an item's second paragraph) is skipped.
    Fenced lines are content, never headings or items, and a line
    indented four or more spaces is never a heading."""
    out: list[str] = []
    capture = open_item = False
    lines = (body or "").splitlines()
    for line, code in zip(lines, code_mask(lines)):
        s = line.strip()
        if code:
            open_item = False
            continue
        heading = _structural(line) and bool(_ATX_HEADING.match(s))
        if (capture and open_item and s and indent_of(line)
                and not heading and not _opens_item(s)):
            out[-1] = f"{out[-1]} {s}".strip()
            continue
        if _structural(line) and s.lower().startswith(_ACCEPTANCE_HEADINGS):
            capture = True
            open_item = False
            continue
        if capture:
            item = _item_text(s)
            if item is not None:
                out.append(item)
                open_item = True
            elif heading:
                break
            elif not s or indent_of(line):
                open_item = False
            else:
                break
    return out


def _norm_name(text: str) -> str:
    """A heading's name as the format gates compare it: closing `#`s
    dropped, whitespace collapsed, lowercased, a trailing colon dropped."""
    text = re.sub(r"(^|[ \t]+)#+$", "", text)
    return " ".join(text.split()).lower().rstrip(":").rstrip()


def section_bounds(body: str, name_lower: str) -> tuple[int, int] | None:
    """(start, end) line indices for the `## <name>` section — end exclusive,
    bounded by the next `## ` heading or EOF — or None if absent. The name
    must match exactly (case and spacing aside), as `gates.cut_managed_tail`
    matches it, so `## Comments from review` is not the log. A `## ` line
    inside a ``` or ~~~ fence, or indented as code, is content, not a
    heading."""
    lines = body.splitlines()
    start = None
    for i, (line, code) in enumerate(zip(lines, code_mask(lines))):
        heading = None if code else h2_text(line)
        if heading is None:
            continue
        if start is None:
            if _norm_name(heading) == name_lower:
                start = i
        else:
            return (start, i)
    if start is None:
        return None
    return (start, len(lines))


def extract_section(body: str, name_lower: str) -> str | None:
    """The full `## <name>` section text (heading included), or None."""
    bounds = section_bounds(body, name_lower)
    if bounds is None:
        return None
    lines = body.splitlines()
    return "\n".join(lines[bounds[0]:bounds[1]]).strip()


def strip_section(body: str, name_lower: str) -> str:
    """`body` with every `## <name>` section removed (no-op if absent)."""
    while (bounds := section_bounds(body, name_lower)) is not None:
        lines = body.splitlines()
        body = "\n".join(lines[:bounds[0]] + lines[bounds[1]:]).strip()
    return body


TEMPLATE = """\
---
type: Story
priority: Medium
assignee:
labels: []
blocked_by: []
blocks: []
---
# One-line summary

Describe the work here.

## Acceptance
- first acceptance criterion
"""


def template() -> str:
    """The create-document the editor should open with for `apply --new`."""
    return TEMPLATE
