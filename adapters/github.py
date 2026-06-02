"""GitHub adapter — Issues as tickets, board status from either Projects v2
(default) or a `Status:` label convention.

All access goes through the `gh` CLI (uses the user's existing gh auth):
  - issues:   gh issue view/list/create/edit/comment   (needs `repo` scope)
  - projects: gh project item-list/field-list/item-*    (needs `project` scope)

Config ([github] block):
  board                 "projectv2" (default) | "labels"
  repo                  "owner/name" (falls back to [vcs].repo)
  # projectv2 mode:
  project_owner         login or "@me"
  project_number        the Projects v2 number (int)
  status_field          single-select field name (default "Status")
  # labels mode:
  status_label_prefix   default "Status: "
  # both modes:
  priority_label_prefix default "Priority: "
  type_label_prefix     default ""  (empty = match labels against [issue_types] names)

Time tracking: GitHub has none — worklog/lane-time are always no-ops here. Set
[timetracking].provider = "none" (doctor warns otherwise).
"""
import json
import re
import subprocess

from core.errors import ConfigError, NotFoundError, ProviderError
from core.schema import Check, Ticket, Worklog

from .base import Adapter

_BLOCKED_RE = re.compile(r"(?i)\b(?:blocked by|depends on)\s+#(\d+)")
_BLOCKS_RE = re.compile(r"(?i)\bblocks\s+#(\d+)")


class GithubAdapter(Adapter):
    def __init__(self, config):
        super().__init__(config)
        gh = config.provider_cfg
        self.board = gh.get("board", "projectv2")
        if self.board not in ("projectv2", "labels"):
            raise ConfigError(f"[github].board must be 'projectv2' or 'labels', got '{self.board}'")
        self.repo = gh.get("repo") or (config.vcs.get("repo") if config.vcs else "")
        if not self.repo:
            raise ConfigError("github needs a repo ([github].repo or [vcs].repo)")
        self.project_owner = gh.get("project_owner", "@me")
        self.project_number = str(gh.get("project_number", "")) if gh.get("project_number") else ""
        self.status_field = gh.get("status_field", "Status")
        self.status_label_prefix = gh.get("status_label_prefix", "Status: ")
        self.priority_label_prefix = gh.get("priority_label_prefix", "Priority: ")
        self.type_label_prefix = gh.get("type_label_prefix", "")
        self._me = None
        self._project_meta = None  # cached {id, status_field_id, options:{name:id}}

    # ---- gh helpers --------------------------------------------------------

    def _gh(self, *args: str) -> str:
        try:
            return subprocess.run(["gh", *args], capture_output=True, text=True,
                                  check=True).stdout
        except FileNotFoundError as e:
            raise ProviderError("gh not found on PATH") from e
        except subprocess.CalledProcessError as e:
            raise ProviderError(f"gh {' '.join(args)} failed: {e.stderr.strip()}") from e

    def _gh_json(self, *args: str):
        out = self._gh(*args)
        try:
            return json.loads(out)
        except json.JSONDecodeError as e:
            raise ProviderError(f"gh returned non-JSON for {' '.join(args)}: {e}") from e

    # ---- label / field helpers --------------------------------------------

    def _label_names(self, raw_labels) -> list[str]:
        out = []
        for lab in raw_labels or []:
            out.append(lab["name"] if isinstance(lab, dict) else str(lab))
        return out

    def _assignee_login(self, raw_assignees) -> str:
        for a in raw_assignees or []:
            return a["login"] if isinstance(a, dict) else str(a)
        return ""

    def _type_from_labels(self, labels: list[str]) -> str:
        if self.type_label_prefix:
            for lab in labels:
                if lab.startswith(self.type_label_prefix):
                    return lab[len(self.type_label_prefix):].strip()
            return ""
        known = self.config.full_sdlc | self.config.deliverable
        for lab in labels:
            if lab in known:
                return lab
        return ""

    def _priority_from_labels(self, labels: list[str]) -> str:
        for lab in labels:
            if lab.startswith(self.priority_label_prefix):
                return lab[len(self.priority_label_prefix):].strip()
        return ""

    def _status_from_labels(self, labels: list[str]) -> str:
        for lab in labels:
            if lab.startswith(self.status_label_prefix):
                return lab[len(self.status_label_prefix):].strip()
        return ""

    def _parse_links(self, body: str, check_resolved: bool):
        body = body or ""
        blocked = [{"key": n, "resolved": self._is_closed(n) if check_resolved else False}
                   for n in dict.fromkeys(_BLOCKED_RE.findall(body))]
        blocks = list(dict.fromkeys(_BLOCKS_RE.findall(body)))
        return blocked, blocks

    def _is_closed(self, number: str) -> bool:
        try:
            data = self._gh_json("issue", "view", number, "--repo", self.repo, "--json", "closed")
            return bool(data.get("closed"))
        except ProviderError:
            return False

    # ---- normalization -----------------------------------------------------

    def _issue_to_ticket(self, issue: dict, status: str | None = None,
                         check_resolved: bool = False, transitions=None) -> Ticket:
        labels = self._label_names(issue.get("labels"))
        body = issue.get("body", "") or ""
        if status is None:
            status = self._status_from_labels(labels) if self.board == "labels" else ""
        issue_type = self._type_from_labels(labels)
        blocked_by, blocks = self._parse_links(body, check_resolved)
        number = str(issue.get("number", ""))
        return Ticket(
            key=number,
            type=issue_type,
            summary=issue.get("title", ""),
            description=body,
            status=status,
            status_role=self.config.lane_to_role(status) if status else "",
            type_class=self.config.type_class(issue_type),
            assignee=self._assignee_login(issue.get("assignees")),
            priority=self._priority_from_labels(labels),
            url=issue.get("url", ""),
            acceptance=_extract_acceptance(body),
            labels=labels,
            components=[],
            blocked_by=blocked_by,
            blocks=blocks,
            transitions=transitions or [],
        )

    # ---- projectv2 helpers -------------------------------------------------

    def _require_project(self):
        if not self.project_number:
            raise ConfigError("projectv2 board needs [github].project_number")

    def _project(self) -> dict:
        """Cache {id, status_field_id, options:{name:id}, option_names:[...]}."""
        if self._project_meta is not None:
            return self._project_meta
        self._require_project()
        view = self._gh_json("project", "view", self.project_number,
                             "--owner", self.project_owner, "--format", "json")
        fields = self._gh_json("project", "field-list", self.project_number,
                               "--owner", self.project_owner, "--format", "json")
        sf = None
        for f in fields.get("fields", []):
            if f.get("name") == self.status_field:
                sf = f
                break
        if not sf:
            raise ProviderError(f"project has no '{self.status_field}' field")
        options = {o["name"]: o["id"] for o in sf.get("options", [])}
        self._project_meta = {
            "id": view.get("id"),
            "status_field_id": sf.get("id"),
            "options": options,
            "option_names": list(options),
        }
        return self._project_meta

    def _project_items(self, query: str | None = None) -> list[dict]:
        self._require_project()
        args = ["project", "item-list", self.project_number, "--owner", self.project_owner,
                "--format", "json", "--limit", "100"]
        if query:
            args += ["--query", query]
        data = self._gh_json(*args)
        return data.get("items", [])

    @staticmethod
    def _item_status(item: dict, status_field: str) -> str:
        for k in (status_field, status_field.lower(), "status"):
            if k in item and isinstance(item[k], str):
                return item[k]
        return ""

    def _item_to_ticket(self, item: dict, check_resolved=False, transitions=None) -> Ticket:
        content = item.get("content", {}) or {}
        # item-list flattens issue basics into content + field values at top level.
        issue = {
            "number": content.get("number"),
            "title": content.get("title", ""),
            "body": content.get("body", ""),
            "url": content.get("url", ""),
            "labels": item.get("labels") or content.get("labels"),
            "assignees": item.get("assignees") or content.get("assignees"),
        }
        status = self._item_status(item, self.status_field)
        return self._issue_to_ticket(issue, status=status, check_resolved=check_resolved,
                                     transitions=transitions)

    def _find_item_id(self, number: str) -> str:
        for item in self._project_items():
            content = item.get("content", {}) or {}
            if str(content.get("number")) == str(number):
                return item.get("id")
        raise NotFoundError(f"issue #{number} is not an item in project {self.project_number}")

    # ---- verbs -------------------------------------------------------------

    def whoami(self) -> str:
        if self._me is None:
            self._me = self._gh("api", "user", "--jq", ".login").strip()
        return self._me

    _ISSUE_FIELDS = "number,title,body,state,url,labels,assignees"

    def list(self, tier=None, query=None):
        q = self.config.query(tier=tier, name=query)
        if self.board == "projectv2":
            items = self._project_items(query=q)
            opt_names = self._project()["option_names"]
            return [self._item_to_ticket(i, transitions=opt_names)
                    for i in items if (i.get("content", {}) or {}).get("type") == "Issue"]
        # labels mode: GitHub issue search
        issues = self._gh_json("issue", "list", "--repo", self.repo, "--search", q,
                               "--json", self._ISSUE_FIELDS, "--limit", "25")
        lanes = sorted(self.config.roles.values())
        return [self._issue_to_ticket(i, transitions=lanes) for i in issues]

    def view(self, key):
        issue = self._gh_json("issue", "view", key, "--repo", self.repo,
                              "--json", self._ISSUE_FIELDS)
        if self.board == "projectv2":
            status = ""
            for item in self._project_items():
                if str((item.get("content", {}) or {}).get("number")) == str(key):
                    status = self._item_status(item, self.status_field)
                    break
            return self._issue_to_ticket(issue, status=status, check_resolved=True,
                                         transitions=self._project()["option_names"])
        return self._issue_to_ticket(issue, check_resolved=True,
                                     transitions=sorted(self.config.roles.values()))

    def transition(self, key, role):
        lane = self.config.role_to_lane(role)
        if self.board == "projectv2":
            proj = self._project()
            oid = proj["options"].get(lane)
            if not oid:
                raise ProviderError(f"project '{self.status_field}' has no option '{lane}' "
                                    f"(have: {', '.join(proj['option_names'])})")
            item_id = self._find_item_id(key)
            self._gh("project", "item-edit", "--id", item_id,
                     "--project-id", proj["id"], "--field-id", proj["status_field_id"],
                     "--single-select-option-id", oid)
            return
        # labels mode: swap the Status: label
        cur = self.view(key)
        args = ["issue", "edit", key, "--repo", self.repo, "--add-label", f"{self.status_label_prefix}{lane}"]
        existing = self._status_from_labels(cur.labels)
        if existing and existing != lane:
            args += ["--remove-label", f"{self.status_label_prefix}{existing}"]
        self._gh(*args)

    def comment(self, key, body):
        self._gh("issue", "comment", key, "--repo", self.repo, "--body", body)

    def blockers(self, key):
        return self.view(key).unresolved_blockers()

    def create(self, issue_type, summary, priority="", assignee="", body="", project=""):
        args = ["issue", "create", "--repo", self.repo, "--title", summary,
                "--body", body or summary]
        if assignee:
            args += ["--assignee", assignee]
        if issue_type:
            label = f"{self.type_label_prefix}{issue_type}" if self.type_label_prefix else issue_type
            args += ["--label", label]
        if priority:
            args += ["--label", f"{self.priority_label_prefix}{priority}"]
        todo_lane = self.config.roles.get("todo") or self.config.roles.get("backlog", "")
        if self.board == "labels" and todo_lane:
            args += ["--label", f"{self.status_label_prefix}{todo_lane}"]
        url = self._gh(*args).strip().splitlines()[-1]
        number = url.rstrip("/").split("/")[-1]
        if self.board == "projectv2":
            self._require_project()
            self._gh("project", "item-add", self.project_number,
                     "--owner", self.project_owner, "--url", url)
            self._project_meta = None  # item set changed
            if todo_lane:
                self.transition(number, "todo")
        return self.view(number)

    def link(self, key, to, link_type):
        lt = link_type.strip().lower()
        ref = to if to.startswith("#") else f"#{to}"
        if lt in ("is blocked by", "blocks", "depends on"):
            verb = "Blocks" if lt == "blocks" else "Blocked by"
            issue = self._gh_json("issue", "view", key, "--repo", self.repo, "--json", "body")
            body = (issue.get("body", "") or "").rstrip()
            self._gh("issue", "edit", key, "--repo", self.repo,
                     "--body", f"{body}\n\n{verb} {ref}".strip())
        else:
            self.comment(key, f"{link_type} {ref}")

    def worklog(self, key, from_role, note="", billable=False):
        # GitHub has no time tracking.
        return Worklog(key=key, role=from_role,
                       lane=self.config.role_to_lane(from_role), note=note)

    def lane_time(self, key, role):
        return Worklog(key=key, role=role, lane=self.config.role_to_lane(role))

    def doctor(self):
        checks = []
        try:
            self._gh("auth", "status")
            checks.append(Check("gh authenticated", True, self.whoami()))
        except ProviderError as e:
            checks.append(Check("gh authenticated", False, str(e)))
            return checks
        checks.append(Check("repo configured", bool(self.repo), self.repo))
        checks.append(Check("board mode", True, self.board))
        if self.board == "projectv2":
            try:
                proj = self._project()
                missing = [lane for role, lane in self.config.roles.items()
                           if role not in ("blocked", "cancelled", "revise")
                           and lane not in proj["options"]]
                checks.append(Check("project + status field reachable", True,
                                    f"#{self.project_number}, {len(proj['options'])} options"))
                checks.append(Check("status options cover board roles", not missing,
                                    "missing: " + ", ".join(missing) if missing else "all mapped"))
            except (ProviderError, ConfigError) as e:
                checks.append(Check("project reachable", False, str(e)))
        else:
            checks.append(Check("roles configured", bool(self.config.roles),
                                f"{len(self.config.roles)} roles"))
        tt = self.config.timetracking.get("provider", "none")
        if tt != "none":
            checks.append(Check("timetracking is none", False,
                                f"set [timetracking].provider='none' — GitHub has no time tracking (got '{tt}')"))
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
