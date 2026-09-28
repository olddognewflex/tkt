"""Normalized data shapes every adapter must return.

The whole point of tkt: a skill reads this shape and never knows whether the
backend was Jira, GitHub, Linear, qi, or a markdown file.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class Ticket:
    key: str
    type: str = ""
    summary: str = ""
    description: str = ""
    status: str = ""                 # provider lane name, verbatim
    status_role: str = ""            # canonical role resolved from config
    type_class: str = ""             # "full_sdlc" | "deliverable" | "unknown"
    assignee: str = ""
    priority: str = ""
    # Agent execution state, for boards that surface "is an agent working this,
    # and what is it doing": "" (none) | idle | processing | waiting | done |
    # blocked. Set by `tkt edit --agent-status`; only the markdown backend
    # persists it today. "" when unset.
    agent_status: str = ""
    # When agent_status last changed, ISO-8601 UTC. Stamped by the adapter on
    # write, never set by hand, so a board can render "processing for 12m"
    # without a second clock source. None when agent_status is unset.
    agent_status_at: str | None = None
    # Optional dates, ISO YYYY-MM-DD; None when unset (rendered as null in JSON).
    due: str | None = None
    scheduled: str | None = None
    completed: str | None = None
    url: str = ""
    acceptance: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    # blocked_by entries: {"key": str, "resolved": bool}
    blocked_by: list[dict[str, Any]] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    transitions: list[str] = field(default_factory=list)  # available next lanes

    def unresolved_blockers(self) -> list[dict[str, Any]]:
        return [b for b in self.blocked_by if not b.get("resolved")]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Worklog:
    key: str
    role: str = ""
    lane: str = ""
    seconds: int = 0
    human: str = ""                  # e.g. "1h 23m"
    worklog_id: str = ""             # provider id, or local id, or "" if none
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ActivityEvent:
    """One thing that happened on a ticket: a comment or a field change."""
    evidence_id: str                 # stable, provider-scoped: "jira:comment:<id>"
    key: str
    kind: str                        # "comment" | "change"
    timestamp: str                   # canonical UTC, see iso_utc()
    actor: str = ""                  # display name; "" when the provider has none
    actor_id: str = ""               # provider account id
    field: str = ""                  # changes only: which field moved
    from_: str | None = None         # changes only; "from" in JSON
    to: str | None = None            # changes only
    body: str = ""                   # comments only, plain text
    url: str = ""                    # deep link to the event, else the ticket

    def to_dict(self) -> dict[str, Any]:
        # `from` is a keyword, so the attribute carries a trailing underscore
        # that the JSON shape does not.
        return {k.rstrip("_"): v for k, v in asdict(self).items()}


@dataclass
class ActivityReport:
    """`tkt activity`: events on a named query's tickets in [since, until)."""
    query: str
    since: str                       # canonical UTC, inclusive
    until: str                       # canonical UTC, exclusive
    tickets: list[str] = field(default_factory=list)  # every key scanned
    events: list[ActivityEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"query": self.query, "since": self.since, "until": self.until,
                "tickets": list(self.tickets),
                "events": [e.to_dict() for e in self.events]}


def iso_utc(dt: datetime) -> str:
    """An aware datetime as `YYYY-MM-DDTHH:MM:SS.mmmZ`. Fixed width, so the
    strings sort in time order; milliseconds because that is Jira's grain."""
    return (dt.astimezone(timezone.utc).isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"))


def human_duration(seconds: int) -> str:
    seconds = max(int(seconds), 0)
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    return f"{h}h {m}m"
