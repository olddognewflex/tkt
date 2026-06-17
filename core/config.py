"""Load and expose .sdlc/config.toml — the single source of truth for which
provider to talk to and how this project's board is shaped.
"""
import os
import tomllib
from pathlib import Path
from typing import Any

from .errors import ConfigError, NotFoundError

# Backend-agnostic priority ordering, highest-first. Used when a project does
# not define its own `priorities = [...]` in .sdlc/config.toml.
DEFAULT_PRIORITIES = ["Highest", "High", "Medium", "Low", "Lowest"]


class Config:
    def __init__(self, data: dict[str, Any], path: Path):
        self.path = path
        self._d = data

        t = data.get("ticketing", {})
        self.provider = t.get("provider")
        if not self.provider:
            raise ConfigError(f"{path}: [ticketing].provider is required")
        self.project = t.get("project", "")
        self.board_id = str(t.get("board_id", ""))
        self.auth_env = list(t.get("auth_env", []))

        board = data.get("board", {})
        self.roles: dict[str, str] = dict(board.get("roles", {}))
        self._lane_to_role = {lane: role for role, lane in self.roles.items()}
        self.ownership: dict[str, str] = dict(board.get("ownership", {}))

        it = data.get("issue_types", {})
        self.full_sdlc = set(it.get("full_sdlc", []))
        self.deliverable = set(it.get("deliverable", []))

        self.queries: dict[str, str] = dict(data.get("queries", {}))
        self.vcs = data.get("vcs", {})
        self.build = data.get("build", {})
        self.timetracking = data.get("timetracking", {})
        self.docs = data.get("docs", {})
        # provider-specific blocks (e.g. [markdown], [github])
        self.provider_cfg = data.get(self.provider, {})

    # ---- lane / role resolution -------------------------------------------

    def role_to_lane(self, role: str) -> str:
        """Map a canonical role (in_progress, review, ...) to the provider's
        actual lane name. Pass-through if the caller already gave a lane name
        that matches a configured value."""
        if role in self.roles:
            return self.roles[role]
        if role in self._lane_to_role:  # already a lane name
            return role
        raise NotFoundError(
            f"unknown lane role '{role}'. Configured roles: "
            f"{', '.join(sorted(self.roles)) or '(none)'}"
        )

    def lane_to_role(self, lane: str) -> str:
        return self._lane_to_role.get(lane, lane)

    # ---- issue type routing ------------------------------------------------

    def type_class(self, issue_type: str) -> str:
        if issue_type in self.full_sdlc:
            return "full_sdlc"
        if issue_type in self.deliverable:
            return "deliverable"
        return "unknown"

    # ---- named queries -----------------------------------------------------

    def query(self, tier: int | None = None, name: str | None = None) -> str:
        if tier is not None:
            name = f"tier{tier}"
        if not name:
            raise ConfigError("query requires --tier N or --query <name>")
        if name not in self.queries:
            raise NotFoundError(
                f"no [queries].{name} in config. Defined: "
                f"{', '.join(sorted(self.queries)) or '(none)'}"
            )
        return self.queries[name]

    # ---- priorities --------------------------------------------------------

    def priorities(self) -> list[str]:
        """The configured ordered priority list (highest-first), or the default
        when `priorities` is absent or not a non-empty list. Adapters may map
        this to a backend's own scheme; see Adapter.priorities()."""
        val = self._d.get("priorities")
        if isinstance(val, list) and val:
            return [str(p) for p in val]
        return list(DEFAULT_PRIORITIES)

    # ---- arbitrary lookups (for skills reading build/vcs settings) ---------

    def get(self, dotted: str) -> Any:
        """Fetch a value by dotted path, e.g. 'build.test' or 'vcs.repo'."""
        cur: Any = self._d
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                raise NotFoundError(f"config key not found: {dotted}")
            cur = cur[part]
        return cur

    # ---- discovery ---------------------------------------------------------

    @classmethod
    def load(cls, explicit: str | None = None) -> "Config":
        path = cls._find(explicit)
        try:
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
        except OSError as e:
            raise ConfigError(f"cannot read config {path}: {e}") from e
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"invalid TOML in {path}: {e}") from e
        return cls(data, path)

    @staticmethod
    def _find(explicit: str | None) -> Path:
        if explicit:
            p = Path(explicit).expanduser()
            if not p.is_file():
                raise ConfigError(f"config not found: {p}")
            return p
        env = os.environ.get("TKT_CONFIG")
        if env:
            p = Path(env).expanduser()
            if not p.is_file():
                raise ConfigError(f"TKT_CONFIG points at missing file: {p}")
            return p
        # Walk up from cwd looking for .sdlc/config.toml
        here = Path.cwd()
        for d in [here, *here.parents]:
            cand = d / ".sdlc" / "config.toml"
            if cand.is_file():
                return cand
        raise ConfigError(
            "no .sdlc/config.toml found (searched cwd and parents). "
            "Set TKT_CONFIG or run `sdlc init`."
        )
