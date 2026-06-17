"""The verb contract. Every provider adapter subclasses this and implements
each verb. Skills depend ONLY on these signatures and the schema shapes they
return — never on a concrete provider.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from core.config import Config
from core.errors import ProviderError
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
    def lane_time(self, key: str, role: str, read_only: bool = False) -> Worklog:
        """Time in the lane mapped from `role`, measured from the most recent
        entry into that lane until its next exit (or until now if still there).

        By default this RECORDS a retroactive worklog (e.g. deploy-ready over QA
        lanes). With read_only=True it computes and returns the same duration but
        records nothing (worklog_id=""), for display surfaces such as a board UI;
        in that mode the timetracking provider is ignored so the duration is
        available even when no time is being tracked."""

    def lane_time_batch(
        self,
        keys_roles: list[tuple[str, str]],
        read_only: bool = False,
    ) -> list[Worklog]:
        """Batch form of `lane_time`. Default implementation loops over single
        calls; adapters for local/remote backends should override for efficiency.

        Per-key failures (e.g. no history in the requested lane) degrade to a
        Worklog with seconds=0 and human="" rather than aborting the whole batch.
        Other failures propagate."""
        out = []
        for key, role in keys_roles:
            try:
                out.append(self.lane_time(key, role, read_only=read_only))
            except ProviderError as e:
                msg = str(e).lower()
                if "no entry" in msg or "no transition" in msg:
                    out.append(Worklog(key=key, role=role,
                                     lane=self.config.role_to_lane(role)))
                else:
                    raise
        return out

    @abstractmethod
    def doctor(self) -> list[Check]:
        """Validate auth + reachability + board model. Read-only."""

    # ---- config-backed, optionally backend-mapped --------------------------

    def priorities(self) -> list[str]:
        """The ordered priority list (highest-first) this backend understands.

        Defaults to the project's configured list (Config.priorities()); a
        backend with its own priority scheme (e.g. Jira) overrides this to
        reconcile the configured names with what the backend actually accepts."""
        return self.config.priorities()

    # ---- optional verbs ----------------------------------------------------
    # Not every backend supports these; default to a clear error so adapters
    # can opt in without breaking instantiation (unlike the @abstractmethods).

    def create(
        self,
        issue_type: str,
        summary: str,
        priority: str = "",
        assignee: str = "",
        body: str = "",
        project: str = "",
    ) -> Ticket:
        """Create a new ticket; return it normalized (with its new key)."""
        raise ProviderError(f"create not supported by provider '{self.config.provider}'")

    def link(self, key: str, to: str, link_type: str) -> None:
        """Link `key` to `to` with a provider link type (e.g. 'is blocked by',
        'blocks', 'relates to', 'Fixes', 'duplicates')."""
        raise ProviderError(f"link not supported by provider '{self.config.provider}'")

    def edit(
        self,
        key: str,
        summary: str | None = None,
        body: str | None = None,
        priority: str | None = None,
        assignee: str | None = None,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> Ticket:
        """Edit a ticket's content/fields in place; return it normalized.

        Only the arguments that are not None are changed (an empty string is a
        valid value, e.g. clearing the assignee). Status is intentionally NOT
        editable here — lane moves go through `transition` so history/worklog
        stay correct."""
        raise ProviderError(f"edit not supported by provider '{self.config.provider}'")
