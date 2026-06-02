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
"""
from core.schema import Ticket


class JqlSubset:
    def __init__(self, jql: str, me: str):
        self.me = me
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

    def run(self, tickets: list[Ticket]) -> list[Ticket]:
        result = [t for t in tickets if all(self._match(t, c) for c in self.clauses)]
        for field, desc in reversed(self.order):
            result.sort(key=lambda t: str(getattr(t, field, "")), reverse=desc)
        return result
