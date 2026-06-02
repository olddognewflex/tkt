"""The verb contract. Every provider adapter subclasses this and implements
each verb. Skills depend ONLY on these signatures and the schema shapes they
return — never on a concrete provider.
"""
from abc import ABC, abstractmethod

from core.config import Config
from core.schema import Check, Ticket, Worklog


class Adapter(ABC):
    def __init__(self, config: Config):
        self.config = config

    @abstractmethod
    def whoami(self) -> str:
        """Return the current user's canonical id for this provider."""

    @abstractmethod
    def list(self, tier: int | None = None, query: str | None = None) -> list[Ticket]:
        """Return tickets matching a named query (config [queries].tierN / name)."""

    @abstractmethod
    def view(self, key: str) -> Ticket:
        """Return one normalized ticket."""

    @abstractmethod
    def transition(self, key: str, role: str) -> None:
        """Move a ticket to the lane mapped from `role`."""

    @abstractmethod
    def comment(self, key: str, body: str) -> None:
        """Post a comment to the activity stream."""

    @abstractmethod
    def blockers(self, key: str) -> list[dict]:
        """Return UNRESOLVED blockers only: [{"key", "resolved": false}, ...]."""

    @abstractmethod
    def worklog(
        self,
        key: str,
        from_role: str,
        note: str = "",
        billable: bool = False,
    ) -> Worklog:
        """Record time spent in the lane mapped from `from_role`, measured from
        the most recent entry into that lane until now. No-op (returns a Worklog
        with worklog_id="") when timetracking provider is 'none'."""

    @abstractmethod
    def lane_time(self, key: str, role: str) -> Worklog:
        """Record time for an ALREADY-EXITED lane (entry -> next exit), for
        retroactive accounting (e.g. deploy-ready over QA lanes)."""

    @abstractmethod
    def doctor(self) -> list[Check]:
        """Validate auth + reachability + board model. Read-only."""
