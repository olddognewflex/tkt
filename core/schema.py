"""Normalized data shapes every adapter must return.

The whole point of tkt: a skill reads this shape and never knows whether the
backend was Jira, GitHub, Linear, qi, or a markdown file.
"""
from dataclasses import dataclass, field, asdict
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


def human_duration(seconds: int) -> str:
    seconds = max(int(seconds), 0)
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    return f"{h}h {m}m"
