"""A tiny JQL-subset evaluator that runs client-side over normalized Tickets.

Used by backends that have no native query-string language (markdown, linear,
openkanban) so the same `[queries]` strings work everywhere. Supports:

    field = "value"            field != "value"
    assignee = currentUser()    assignee is EMPTY
    clauses joined by AND
    optional: ORDER BY field [ASC|DESC][, field [ASC|DESC]]
    (trailing, or the whole query: `ORDER BY key` lists every ticket)

Field names map to Ticket attributes: status / priority / assignee / type / key.
Unknown clauses are ignored (don't exclude) so a provider can pre-filter natively
and let this layer handle the rest.

`ORDER BY` is stricter, because an ignored sort key looks like a working one:
field names ignore case, and a field that is not a text field of `Ticket`, or
that the backend never fills (`created`, `reporter`, ... on every client-side
backend; the adapter passes its own gaps as `unsupplied`), is a `ConfigError`
rather than a silent no-op. So is a direction other than ASC or DESC. An
`ORDER BY` inside a quoted value is part of the value, not a clause.

`ORDER BY priority` ranks by the backend's priority order (highest-first, as the
adapter's `priorities()` reports it), not by the names as text: text order put
Medium ahead of High. `DESC` is highest first. Matching ignores case, and a
priority the order doesn't list, or an empty one, sorts last either way. (A
backend label such as Linear's "No priority" is listed, so it ranks lowest.)
`ORDER BY key` sorts like Jira, by prefix and then number, so TKT-9 precedes
TKT-10. Every other field sorts as text (ISO dates sort correctly that way),
and an empty or absent value sorts last in both directions.
"""
import dataclasses
import re
from collections.abc import Iterable

from core.config import DEFAULT_PRIORITIES
from core.errors import ConfigError
from core.schema import Ticket

# `ORDER BY` at the start of the query or after whitespace, as whole words.
_ORDER_BY = re.compile(r"(?:^|\s)ORDER\s+BY(?:\s|$)", re.IGNORECASE)

# Ticket fields ORDER BY can sort on: the text ones. Lists, `closed` and
# `rollup` have no meaningful order.
_SORTABLE = frozenset(f.name for f in dataclasses.fields(Ticket)
                      if f.type in (str, "str", str | None, "str | None"))

# Text fields no client-side backend (markdown, linear, openkanban) fills --
# Jira alone does -- so sorting on them would silently do nothing. Drop a name
# from here once every one of those backends populates it; a backend missing
# only some fields passes them to JqlSubset as `unsupplied`.
_NOT_SUPPLIED = frozenset({"created", "updated", "project", "project_name",
                           "assignee_id", "reporter", "reporter_id"})

_KEY = re.compile(r"(.*)-(\d+)")


def _in_quotes(s: str, pos: int) -> bool:
    """True when `pos` sits inside a '...' or "..." literal (backslash escapes
    the next character), so `summary = "sort order by phone"` is one value.
    A quote opens a literal only where a value can start (after whitespace,
    `=`, `!`, `(` or `,`), so the apostrophe in an unquoted O'Brien does not."""
    quote, i = "", 0
    while i < pos:
        c = s[i]
        if quote and c == "\\":
            i += 2
            continue
        if quote and c == quote:
            quote = ""
        elif not quote and c in "'\"" and (i == 0 or s[i - 1].isspace()
                                             or s[i - 1] in "=!(,"):
            quote = c
        i += 1
    return bool(quote)


class JqlSubset:
    def __init__(self, jql: str, me: str, priorities: list[str] | None = None,
                 unsupplied: Iterable[str] = ()):
        self.me = me
        self._unsupplied = _NOT_SUPPLIED | {str(f).lower() for f in unsupplied}
        order = priorities if priorities else DEFAULT_PRIORITIES
        # name -> rank, 0 = highest. First occurrence wins on duplicates.
        self._rank: dict[str, int] = {}
        for i, name in enumerate(order):
            key = str(name).strip().lower()
            if key:      # a blank entry must not make empty priorities rank highest
                self._rank.setdefault(key, i)
        self.order: list[tuple[str, bool]] = []
        body = jql
        found = list(_ORDER_BY.finditer(jql))
        m = next((m for m in found if not _in_quotes(jql, m.start())), None)
        if found and m is None and _in_quotes(jql, len(jql)):
            # every ORDER BY sits inside a literal that never closes: the
            # sort would silently become part of a filter value
            raise ConfigError(f"query {jql!r}: unbalanced quote before ORDER BY")
        if m:
            body, order_str = jql[:m.start()], jql[m.end():]
            for part in order_str.split(","):
                toks = part.split()
                if not toks:
                    continue
                self.order.append(self._sort_term(jql, toks))
            if not self.order:
                raise ConfigError(f"query {jql!r}: ORDER BY names no field")
        self.clauses = [c.strip() for c in self._split_and(body) if c.strip()]

    def _sort_term(self, jql: str, toks: list[str]) -> tuple[str, bool]:
        """One `field [ASC|DESC]` term, validated: (lower-cased field, desc)."""
        field = toks[0].lower()
        where = f"query {jql!r}: ORDER BY {toks[0]}"
        if field in self._unsupplied and field in _SORTABLE:
            raise ConfigError(f"{where}: this backend does not record {field}, "
                              "so the sort would do nothing")
        if field not in _SORTABLE:
            valid = ", ".join(sorted(_SORTABLE - self._unsupplied))
            raise ConfigError(f"{where}: not a sortable field (one of: {valid})")
        if len(toks) > 2 or (len(toks) == 2 and toks[1].upper() not in ("ASC", "DESC")):
            raise ConfigError(f"{where}: expected ASC or DESC after the field, "
                              f"got {' '.join(toks[1:])!r}")
        return field, len(toks) == 2 and toks[1].upper() == "DESC"

    @staticmethod
    def _split_and(s: str) -> list[str]:
        out, buf = [], []
        for tok in s.split(" "):
            if tok.upper() == "AND":
                out.append(" ".join(buf))
                buf = []
            else:
                buf.append(tok)
        out.append(" ".join(buf))
        return out

    def _match(self, t: Ticket, clause: str) -> bool:
        if " IS EMPTY" in clause.upper():
            field = clause.upper().split(" IS EMPTY")[0].strip().lower()
            return not getattr(t, field, "")
        for op in ("!=", "="):
            if op in clause:
                field, _, val = clause.partition(op)
                field = field.strip().lower()
                val = val.strip().strip("'\"")
                if val == "currentUser()":
                    val = self.me
                actual = str(getattr(t, field, ""))
                return actual != val if op == "!=" else actual == val
        return True  # unknown clause -> don't exclude

    def _priority_key(self, t: Ticket, desc: bool) -> tuple[int, int]:
        """Sort key for a priority: known ranks by order, unknown/empty last in
        both directions (so the key, not `reverse`, carries the direction)."""
        rank = self._rank.get(str(getattr(t, "priority", "") or "").strip().lower())
        if rank is None:
            return (1, 0)
        return (0, rank if desc else -rank)

    @staticmethod
    def _text(t: Ticket, field: str) -> str:
        value = getattr(t, field, None)
        return "" if value is None else str(value)

    @staticmethod
    def _key_key(key: str) -> tuple[str, int, str]:
        """TKT-9 before TKT-10: prefix, then number. A key with no `-<n>`
        suffix (an openkanban id, say) sorts by its whole text."""
        m = _KEY.fullmatch(key)
        return (m.group(1), int(m.group(2)), "") if m else (key, -1, key)

    def run(self, tickets: list[Ticket]) -> list[Ticket]:
        result = [t for t in tickets if all(self._match(t, c) for c in self.clauses)]
        # Stable sorts applied last-key-first give a multi-key ORDER BY.
        for field, desc in reversed(self.order):
            if field == "priority":
                result.sort(key=lambda t, d=desc: self._priority_key(t, d))
                continue
            if field == "key":
                result.sort(key=lambda t: self._key_key(self._text(t, "key")), reverse=desc)
            else:
                result.sort(key=lambda t, f=field: self._text(t, f), reverse=desc)
            # A second stable pass moves empty values last in both directions
            # without disturbing the order among the rest.
            result.sort(key=lambda t, f=field: not self._text(t, f).strip())
        return result
