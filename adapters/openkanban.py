"""OpenKanban adapter — https://github.com/TechDufus/openkanban

OpenKanban is a local TUI kanban for orchestrating AI agents. It stores state as
JSON under its config dir; its CLI only manages *projects* (new/list/delete) — there
is no ticket CLI — so this adapter reads/writes the ticket JSON directly (the format
is stable and documented in the repo's DATA_MODEL.md, verified against the store
source). Fully local, no network.

Layout (config dir resolved exactly like openkanban itself):
    <config>/projects.json              {"projects": {id: {Project}}}
    <config>/tickets/<project_id>.json  {"project_id","tickets":{id:Ticket},"updated_at"}
config dir = [openkanban].config_dir | $OPENKANBAN_CONFIG_DIR
           | $XDG_CONFIG_HOME/openkanban | ~/.config/openkanban

Model notes:
  - Statuses are a fixed enum: backlog | in_progress | done | archived. So this is a
    3-lane board (+ archived). Map roles accordingly; review/qa_ready/etc. have no
    column — openkanban suits the short todo->in_progress->done flow.
  - No assignee field (single-user local tool) -> assignee is always "". Don't filter
    queries by assignee.
  - No comments field -> `comment` appends to the description under "## Activity".
  - No relations -> blocked_by/blocks stored in the ticket `meta` map.
  - No time tracking -> worklog/lane-time are no-ops.
  - priority is an int 1..5 (1=highest); normalized to a label here.

Config [openkanban]:
  project       required — project name or id (resolved against projects.json)
  config_dir    optional — overrides the resolved config dir
  me            optional — for whoami (openkanban has no users)
"""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.errors import ConfigError, NotFoundError, ProviderError
from core.query import JqlSubset
from core.schema import Check, Ticket, Worklog

from .base import Adapter

_PRI_LABEL = {1: "Highest", 2: "High", 3: "Medium", 4: "Low", 5: "Lowest"}
_PRI_INT = {v.lower(): k for k, v in _PRI_LABEL.items()}
_DONE_STATUSES = {"done", "archived"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class OpenKanbanAdapter(Adapter):
    def __init__(self, config):
        super().__init__(config)
        oc = config.provider_cfg
        self.project_ref = oc.get("project", "")
        self.me = oc.get("me", "")
        self._config_dir = oc.get("config_dir", "")
        self._pid = None

    # ---- paths -------------------------------------------------------------

    def config_dir(self) -> Path:
        if self._config_dir:
            return Path(self._config_dir).expanduser()
        if os.environ.get("OPENKANBAN_CONFIG_DIR"):
            return Path(os.environ["OPENKANBAN_CONFIG_DIR"])
        if os.environ.get("XDG_CONFIG_HOME"):
            return Path(os.environ["XDG_CONFIG_HOME"]) / "openkanban"
        return Path.home() / ".config" / "openkanban"

    def _registry(self) -> dict:
        path = self.config_dir() / "projects.json"
        if not path.is_file():
            raise ProviderError(f"openkanban registry not found: {path} "
                                "(run `openkanban new <name>` first)")
        return json.loads(path.read_text()).get("projects", {})

    def _project_id(self) -> str:
        if self._pid:
            return self._pid
        if not self.project_ref:
            raise ConfigError("openkanban needs [openkanban].project (name or id)")
        projects = self._registry()
        if self.project_ref in projects:
            self._pid = self.project_ref
        else:
            matches = [pid for pid, p in projects.items()
                       if p.get("name") == self.project_ref]
            if not matches:
                names = ", ".join(p.get("name", "?") for p in projects.values())
                raise NotFoundError(f"no openkanban project '{self.project_ref}' "
                                    f"(have: {names or 'none'})")
            self._pid = matches[0]
        return self._pid

    def _store_path(self) -> Path:
        return self.config_dir() / "tickets" / f"{self._project_id()}.json"

    def _load_store(self) -> dict:
        path = self._store_path()
        if not path.is_file():
            return {"project_id": self._project_id(), "tickets": {}, "updated_at": _now()}
        data = json.loads(path.read_text())
        data.setdefault("tickets", {})
        return data

    def _save_store(self, store: dict) -> None:
        store["updated_at"] = _now()
        path = self._store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(store, indent=2))
        tmp.rename(path)

    # ---- normalization -----------------------------------------------------

    def _to_ticket(self, raw: dict) -> Ticket:
        status = raw.get("status", "")
        meta = raw.get("meta", {}) or {}
        blocked_ids = [x for x in (meta.get("blocked_by", "").split(",")) if x]
        blocks_ids = [x for x in (meta.get("blocks", "").split(",")) if x]
        return Ticket(
            key=raw.get("id", ""),
            type=self._type_from_labels(raw.get("labels", []) or []),
            summary=raw.get("title", ""),
            description=raw.get("description", "") or "",
            status=status,
            status_role=self.config.lane_to_role(status) if status else "",
            type_class=self.config.type_class(self._type_from_labels(raw.get("labels", []) or [])),
            assignee="",  # openkanban has no assignee
            priority=_PRI_LABEL.get(raw.get("priority", 0), ""),
            url=str(self._store_path()) + f"#{raw.get('id','')}",
            acceptance=_extract_acceptance(raw.get("description", "") or ""),
            labels=list(raw.get("labels", []) or []),
            components=[],
            blocked_by=[{"key": b, "resolved": self._is_done(b)} for b in blocked_ids],
            blocks=blocks_ids,
            transitions=sorted(set(self.config.roles.values())),
        )

    def _type_from_labels(self, labels: list[str]) -> str:
        known = self.config.full_sdlc | self.config.deliverable
        for lab in labels:
            if lab in known:
                return lab
        return ""

    def _is_done(self, ticket_id: str) -> bool:
        store = self._load_store()
        raw = store["tickets"].get(ticket_id)
        return bool(raw and raw.get("status") in _DONE_STATUSES)

    def _get_raw(self, key: str) -> tuple[dict, dict]:
        store = self._load_store()
        raw = store["tickets"].get(key)
        if not raw:
            raise NotFoundError(f"no openkanban ticket {key}")
        return store, raw

    # ---- verbs -------------------------------------------------------------

    def whoami(self) -> str:
        return self.me or "(openkanban is single-user; set [openkanban].me to label)"

    def list(self, tier=None, query=None):
        q = self.config.query(tier=tier, name=query)
        store = self._load_store()
        tickets = [self._to_ticket(r) for r in store["tickets"].values()]
        return JqlSubset(q, self.me).run(tickets)

    def view(self, key):
        _, raw = self._get_raw(key)
        return self._to_ticket(raw)

    def transition(self, key, role):
        lane = self.config.role_to_lane(role)
        store, raw = self._get_raw(key)
        raw["status"] = lane
        raw["updated_at"] = _now()
        if lane == self.config.roles.get("in_progress") and not raw.get("started_at"):
            raw["started_at"] = _now()
        if lane in (self.config.roles.get("done"), "done") and not raw.get("completed_at"):
            raw["completed_at"] = _now()
        self._save_store(store)

    def comment(self, key, body):
        store, raw = self._get_raw(key)
        desc = (raw.get("description", "") or "").rstrip()
        stamp = f"- {_now()} {self.me or 'agent'}: {body}"
        if "## Activity" in desc:
            desc += "\n" + stamp
        else:
            desc += ("\n\n" if desc else "") + "## Activity\n" + stamp
        raw["description"] = desc
        raw["updated_at"] = _now()
        self._save_store(store)

    def blockers(self, key):
        return self.view(key).unresolved_blockers()

    def create(self, issue_type, summary, priority="", assignee="", body="", project=""):
        store = self._load_store()
        tid = str(uuid.uuid4())
        labels = [issue_type] if issue_type else []
        raw = {
            "id": tid,
            "project_id": self._project_id(),
            "title": summary,
            "description": body or "",
            "status": self.config.roles.get("todo") or self.config.roles.get("backlog", "backlog"),
            "agent_status": "none",
            "created_at": _now(),
            "updated_at": _now(),
            "labels": labels,
            "priority": _PRI_INT.get(priority.strip().lower(), 0) if priority else 0,
            "meta": {},
        }
        store["tickets"][tid] = raw
        self._save_store(store)
        return self._to_ticket(raw)

    def link(self, key, to, link_type):
        store, raw = self._get_raw(key)
        meta = raw.setdefault("meta", {})
        lt = link_type.strip().lower()
        bucket = "blocked_by" if lt in ("is blocked by", "depends on") else \
                 "blocks" if lt == "blocks" else None
        if bucket is None:
            # record other relation types in meta verbatim
            meta[link_type] = to
        else:
            cur = [x for x in meta.get(bucket, "").split(",") if x]
            if to not in cur:
                cur.append(to)
            meta[bucket] = ",".join(cur)
        raw["updated_at"] = _now()
        self._save_store(store)

    def worklog(self, key, from_role, note="", billable=False):
        return Worklog(key=key, role=from_role,
                       lane=self.config.role_to_lane(from_role), note=note)

    def lane_time(self, key, role):
        return Worklog(key=key, role=role, lane=self.config.role_to_lane(role))

    def doctor(self):
        checks = []
        cd = self.config_dir()
        checks.append(Check("config dir exists", cd.is_dir(), str(cd)))
        reg = cd / "projects.json"
        checks.append(Check("projects.json exists", reg.is_file(), str(reg)))
        if reg.is_file():
            try:
                pid = self._project_id()
                checks.append(Check("project resolves", True, f"{self.project_ref} -> {pid}"))
                checks.append(Check("tickets file", self._store_path().is_file(),
                                    str(self._store_path()) + " (created on first write)"))
            except (ProviderError, ConfigError, NotFoundError) as e:
                checks.append(Check("project resolves", False, str(e)))
        checks.append(Check("roles configured", bool(self.config.roles),
                            f"{len(self.config.roles)} roles"))
        tt = self.config.timetracking.get("provider", "none")
        if tt != "none":
            checks.append(Check("timetracking is none", False,
                                f"openkanban has no time tracking (got '{tt}')"))
        return checks


def _extract_acceptance(body: str) -> list[str]:
    out, capture = [], False
    for line in (body or "").splitlines():
        low = line.strip().lower()
        if low.startswith(("## acceptance", "acceptance criteria", "### acceptance")):
            capture = True
            continue
        if capture:
            s = line.strip()
            if s.startswith(("-", "*", "•")):
                out.append(s.lstrip("-*• ").strip())
            elif s.startswith("#"):
                break
            elif not s:
                continue
            else:
                break
    return out
