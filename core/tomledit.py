"""Comment-preserving, text-level TOML edits for configs `tkt init` writes.

Works on lines, not a parsed tree, so comments and alignment survive. Only
plain `key = value` lines and `[table]` headers are understood; callers
re-parse the result with tomllib to catch anything this cannot represent.
Multi-line basic/literal strings (`\"\"\"`, `'''`) are not tracked.
"""
import json
import re

HEADER_RE = re.compile(r"^\s*\[\[?\s*([^\[\]#]+?)\s*\]\]?\s*(#.*)?$")
_KV_RE = re.compile(r"^(?P<lead>\s*(?P<key>[A-Za-z0-9_-]+)\s*=\s*)(?P<val>.*)$")
_STR_RE = re.compile(r'^(?:"(?:[^"\\]|\\.)*"|\'[^\']*\')')


def toml_str(v: str) -> str:
    # JSON string escapes are a subset of TOML basic-string escapes.
    return json.dumps(v, ensure_ascii=False)


def _code(line: str) -> str:
    """The line with strings and any trailing comment removed."""
    out, q, i = [], None, 0
    while i < len(line):
        c = line[i]
        if q:
            if q == '"' and c == "\\":
                i += 2
                continue
            if c == q:
                q = None
        elif c in "\"'":
            q = c
        elif c == "#":
            break
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _headers(lines: list[str]) -> list[tuple[int, str]]:
    """(index, name) of each table header outside a multi-line array."""
    found, depth = [], 0
    for i, line in enumerate(lines):
        m = HEADER_RE.match(line)
        if depth == 0 and m:
            found.append((i, re.sub(r"\s*\.\s*", ".", m.group(1))))
            continue
        code = _code(line)
        depth = max(0, depth + code.count("[") - code.count("]"))
    return found


def table_span(lines: list[str], name: str) -> tuple[int, int] | None:
    """[start, end) of table `name`: its header through its last line.

    Trailing blank lines are excluded, as is a trailing comment group set
    off by a blank line (it introduces whatever follows, e.g. a
    commented-out table), so a replace leaves it in place."""
    heads = _headers(lines)
    for n, (start, hname) in enumerate(heads):
        if hname != name:
            continue
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        while True:
            while end > start + 1 and not lines[end - 1].strip():
                end -= 1
            k = end
            while k > start + 1 and lines[k - 1].lstrip().startswith("#"):
                k -= 1
            if k < end and k > start + 1 and not lines[k - 1].strip():
                end = k
                continue
            return start, end
    return None


def table_block(text: str, name: str) -> str | None:
    lines = text.splitlines(keepends=True)
    span = table_span(lines, name)
    if span is None:
        return None
    block = "".join(lines[span[0]:span[1]])
    return block if block.endswith("\n") else block + "\n"


def replace_table(text: str, name: str, block: str | None) -> str:
    """Swap table `name` for `block`; None removes it, a missing one is appended."""
    lines = text.splitlines(keepends=True)
    span = table_span(lines, name)
    if span is None:
        if block is None:
            return text
        return text.rstrip("\n") + "\n\n" + block
    start, end = span
    if lines[end - 1:end] and not lines[end - 1].endswith("\n"):
        lines[end - 1] += "\n"
    new = [] if block is None else block.splitlines(keepends=True)
    if block is None and start > 0 and not lines[start - 1].strip() \
            and (end >= len(lines) or not lines[end].strip()):
        start -= 1          # don't leave a double blank line behind
    return "".join(lines[:start] + new + lines[end:])


def set_keys(text: str, table: str, values: dict[str, str],
             keep_comment: bool = True) -> str:
    """Set string `values` in `[table]`, keeping alignment and trailing comments.

    An existing simple string value has just its token swapped; any other
    value is replaced up to end of line. Missing keys go at the end of the
    table; a missing table is appended. `keep_comment=False` drops a
    trailing comment that described the old value."""
    if not values:
        return text
    lines = text.splitlines(keepends=True)
    span = table_span(lines, table)
    if span is None:
        body = "".join(f"{k:<9} = {toml_str(v)}\n" for k, v in values.items())
        return text.rstrip("\n") + f"\n\n[{table}]\n" + body
    start, end = span
    seen = set()
    for i in range(start + 1, end):
        m = _KV_RE.match(lines[i].rstrip("\r\n"))
        if not m or m.group("key") not in values:
            continue
        key = m.group("key")
        seen.add(key)
        val = m.group("val")
        tok = _STR_RE.match(val)
        rest = val[tok.end():] if tok and keep_comment else ""
        lines[i] = f"{m.group('lead')}{toml_str(values[key])}{rest}\n"
    extra = [f"{k:<9} = {toml_str(v)}\n" for k, v in values.items() if k not in seen]
    if extra:
        if not lines[end - 1].endswith("\n"):
            lines[end - 1] += "\n"
        lines[end:end] = extra
    return "".join(lines)
