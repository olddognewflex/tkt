"""A tiny JQL-subset evaluator that runs client-side over normalized Tickets.

Used by backends that have no native query-string language (markdown, linear) so
the same `[queries]` strings work everywhere. Supports:

    field = "value"            field != "value"
    assignee = currentUser()    assignee is EMPTY
    clauses joined by AND
    optional trailing: ORDER BY field [ASC|DESC][, field [ASC|DESC]]

Field names map to Ticket attributes: status / priority / assignee / type / key.
Unknown clauses are ignored (don't exclude) so a provider can pre-filter natively
and let this layer handle the rest.

`ORDER BY priority` ranks by the backend's priority order (highest-first, as the
adapter's `priorities()` reports it), not by the names as text: text order put
Medium ahead of High. `DESC` is highest first. Matching ignores case, and a
priority the order doesn't list, or an empty one, sorts last either way. (A
backend label such as Linear's "No priority" is listed, so it ranks lowest.)
Every other field sorts as text.
"""
from core.config import DEFAULT_PRIORITIES
from core.schema import Ticket


class JqlSubset:
    def __init__(self, jql: str, me: str, priorities: list[str] | None = None):
        self.me = me
        order = priorities if priorities else DEFAULT_PRIORITIES
        # name -> rank, 0 = highest. First occurrence wins on duplicates.
        self._rank: dict[str, int] = {}
        for i, name in enumerate(order):
            key = str(name).strip().lower()
            if key:      # a blank entry must not make empty priorities rank highest
                self._rank.setdefault(key, i)
        self.order: list[tuple[str, bool]] = []
        body = jql
        upper = jql.upper()
        if " ORDER BY " in upper:
            idx = upper.index(" ORDER BY ")
            body, order_str = jql[:idx], jql[idx + len(" ORDER BY "):]
            for part in order_str.split(","):
                toks = part.split()
                if not toks:
                    continue
                field = toks[0]
                desc = len(toks) > 1 and toks[1].upper() == "DESC"
                self.order.append((field, desc))
        self.clauses = [c.strip() for c in self._split_and(body) if c.strip()]

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

    def run(self, tickets: list[Ticket]) -> list[Ticket]:
        result = [t for t in tickets if all(self._match(t, c) for c in self.clauses)]
        # Stable sorts applied last-key-first give a multi-key ORDER BY.
        for field, desc in reversed(self.order):
            if field.lower() == "priority":
                result.sort(key=lambda t, d=desc: self._priority_key(t, d))
            else:
                result.sort(key=lambda t, f=field: str(getattr(t, f, "")), reverse=desc)
        return result
