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
"""
from __future__ import annotations

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


def _section_bounds(body: str, name_lower: str) -> tuple[int, int] | None:
    """(start, end) line indices for the `## <name>` section — end exclusive,
    bounded by the next `## ` heading or EOF — or None if absent."""
    lines = body.splitlines()
    start = None
    for i, line in enumerate(lines):
        s = line.strip()
        if start is None:
            if s.startswith("## ") and s[3:].strip().lower().startswith(name_lower):
                start = i
        elif s.startswith("## "):
            return (start, i)
    if start is None:
        return None
    return (start, len(lines))


def extract_section(body: str, name_lower: str) -> str | None:
    """The full `## <name>` section text (heading included), or None."""
    bounds = _section_bounds(body, name_lower)
    if bounds is None:
        return None
    lines = body.splitlines()
    return "\n".join(lines[bounds[0]:bounds[1]]).strip()


def strip_section(body: str, name_lower: str) -> str:
    """`body` with the `## <name>` section removed (no-op if absent)."""
    bounds = _section_bounds(body, name_lower)
    if bounds is None:
        return body
    lines = body.splitlines()
    return "\n".join(lines[:bounds[0]] + lines[bounds[1]:]).strip()


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
