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
from __future__ import annotations

import json
import re
import subprocess
import sys
from urllib.parse import urlsplit

from core import ticketdoc
from core.errors import ConfigError, NotFoundError, ProviderError, UsageError
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
        self._repo_name = _name_with_owner(self.repo)
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
        return self.config.pick_type(labels)

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

    def _parse_links(self, body: str, check_resolved: bool, native=None):
        """`blocked_by` / `blocks` from the body text, after the native
        dependency edges `native` = (blocked_by, blocks) when given. A key
        the native edges already carry is not looked up again."""
        body = body or ""
        native_blocked, native_blocks = native or ([], [])
        seen = {b["key"] for b in native_blocked}
        blocked = list(native_blocked) + [
            {"key": n, "resolved": self._is_closed(n) if check_resolved else False}
            for n in dict.fromkeys(_numbers(_BLOCKED_RE, body)) if n not in seen]
        blocks = list(dict.fromkeys([*native_blocks, *_numbers(_BLOCKS_RE, body)]))
        return blocked, blocks

    def _is_closed(self, number: str) -> bool:
        try:
            data = self._gh_json("issue", "view", number, "--repo", self.repo, "--json", "closed")
            return bool(data.get("closed"))
        except ProviderError:
            return False

    # ---- normalization -----------------------------------------------------

    def _issue_to_ticket(self, issue: dict, status: str | None = None,
                         check_resolved: bool = False, transitions=None,
                         children: list[str] | None = None,
                         parent: str = "", native_links=None) -> Ticket:
        labels = self._label_names(issue.get("labels"))
        body = issue.get("body", "") or ""
        if status is None:
            status = self._status_from_labels(labels) if self.board == "labels" else ""
        issue_type = self._type_from_labels(labels)
        blocked_by, blocks = self._parse_links(body, check_resolved, native_links)
        number = str(issue.get("number", ""))
        return Ticket(
            key=number,
            type=issue_type,
            summary=issue.get("title", ""),
            description=body,
            status=status,
            status_role=self.config.lane_to_role(status) if status else "",
            # `gh issue --json state` says CLOSED, the REST API says closed.
            closed=self._closed(self.config.lane_to_role(status) if status else "",
                                str(issue.get("state") or "").lower() == "closed"),
            type_class=self.config.type_class(issue_type),
            assignee=self._assignee_login(issue.get("assignees")),
            priority=self._priority_from_labels(labels),
            url=issue.get("url", ""),
            acceptance=_extract_acceptance(body),
            labels=labels,
            components=[],
            blocked_by=blocked_by,
            blocks=blocks,
            children=[c for c in dict.fromkeys(children or []) if c != number],
            parent=parent if parent != number else "",
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

    _ITEM_PAGE = 100

    def _project_items(self, query: str | None = None) -> list[dict]:
        """Every item, not only the first page: a child past it would lose
        its lane. `--limit` is a cap gh pages up to, so a full first page
        asks the project for its item count and fetches that many."""
        self._require_project()
        items = self._item_list(self._ITEM_PAGE, query)
        if len(items) >= self._ITEM_PAGE:
            view = self._gh_json("project", "view", self.project_number,
                                 "--owner", self.project_owner, "--format", "json")
            total = int(((view.get("items") or {}).get("totalCount")) or 0)
            if total > len(items):
                items = self._item_list(total, query)
        return items

    def _item_list(self, limit: int, query: str | None) -> list[dict]:
        args = ["project", "item-list", self.project_number, "--owner", self.project_owner,
                "--format", "json", "--limit", str(limit)]
        if query:
            args += ["--query", query]
        return self._gh_json(*args).get("items", [])

    def _issue_states(self, numbers: list) -> dict[str, str]:
        """{number: "OPEN" | "CLOSED"} for this repo's issues, in one GraphQL
        call per 100 (`item-list` carries no issue state)."""
        owner, _, name = self._repo_name.partition("/")
        host = _repo_host(self.repo)
        nums = [str(n) for n in dict.fromkeys(numbers) if str(n).isdigit()]
        out: dict[str, str] = {}
        for i in range(0, len(nums), 100):
            chunk = nums[i:i + 100]
            fields = " ".join(f"i{n}: issue(number: {n}) {{ state }}" for n in chunk)
            q = f"query {{ repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ {fields} }} }}"
            args = ["api", "graphql", "-f", f"query={q}"]
            if host:
                args += ["--hostname", host]
            repo = ((self._gh_json(*args).get("data") or {}).get("repository") or {})
            for n in chunk:
                state = (repo.get(f"i{n}") or {}).get("state")
                if state:
                    out[n] = state
        return out

    def _repo_items(self, query: str | None = None) -> list[dict]:
        """This repo's items only. A Project can span repos, and issue numbers
        repeat across them, so every number lookup must go through here."""
        return [i for i in self._project_items(query) if self._in_repo(i)]

    def _in_repo(self, item: dict) -> bool:
        content = item.get("content", {}) or {}
        repo = content.get("repository") or ""
        if not repo:
            # Fall back to the URL: https://github.com/<owner>/<repo>/issues/<n>
            parts = (content.get("url") or "").split("/")
            repo = "/".join(parts[3:5]) if len(parts) > 4 else ""
        return _name_with_owner(repo) == self._repo_name

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
            "state": content.get("state"),
        }
        status = self._item_status(item, self.status_field)
        return self._issue_to_ticket(issue, status=status, check_resolved=check_resolved,
                                     transitions=transitions)

    def _find_item_id(self, number: str) -> str:
        for item in self._repo_items():
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

    def list(self, tier=None, query=None, all_projects=False):
        # `all_projects` is a no-op: scoped by `--repo` / the Project itself.
        q = self.config.query(tier=tier, name=query)
        if self.board == "projectv2":
            items = [i for i in self._repo_items(query=q)
                     if (i.get("content", {}) or {}).get("type") == "Issue"]
            opt_names = self._project()["option_names"]
            try:
                states = self._issue_states(
                    [(i.get("content") or {}).get("number") for i in items])
            except ProviderError as e:
                # `closed` then falls back to the lane alone.
                print(f"tkt: warn: issue state unavailable: {e}", file=sys.stderr)
                states = {}
            for i in items:
                content = i.get("content") or {}
                state = states.get(str(content.get("number")))
                if state:
                    i["content"] = {**content, "state": state}
            return [self._item_to_ticket(i, transitions=opt_names) for i in items]
        # labels mode: GitHub issue search
        issues = self._gh_json("issue", "list", "--repo", self.repo, "--search", q,
                               "--json", self._ISSUE_FIELDS, "--limit", "25")
        lanes = sorted(self.config.roles.values())
        return [self._issue_to_ticket(i, transitions=lanes) for i in issues]

    def view(self, key, *, hierarchy: bool = True):
        # `hierarchy=False` is for internal callers that read neither the
        # hierarchy nor the links (`transition`, `create`, the format gates,
        # `tkt agents --enrich`): it skips every extra call (sub-issues,
        # parent and native dependencies). `blockers` asks for links only.
        return self._view(key, hierarchy=hierarchy, links=hierarchy)

    def _view(self, key, *, hierarchy: bool, links: bool):
        issue = self._gh_json("issue", "view", key, "--repo", self.repo,
                              "--json", self._ISSUE_FIELDS)
        children, parent, native = None, "", None
        if hierarchy or links:
            host, nwo = _issue_url_repo(issue.get("url"))
            if not nwo:
                # No usable issue URL: fall back to what the config names.
                host, nwo = _repo_host(self.repo), self._repo_name
            number = str(issue.get("number") or key)
            if hierarchy:
                children = self._children(number, host, nwo)
                parent = self._parent(number, host, nwo)
            if links:
                # `blockers` (links without hierarchy) reads blocked_by only.
                native = self._native_links(number, host, nwo, blocking=hierarchy)
        if self.board == "projectv2":
            status = ""
            for item in self._repo_items():
                if str((item.get("content", {}) or {}).get("number")) == str(key):
                    status = self._item_status(item, self.status_field)
                    break
            return self._issue_to_ticket(issue, status=status, check_resolved=True,
                                         transitions=self._project()["option_names"],
                                         children=children, parent=parent,
                                         native_links=native)
        return self._issue_to_ticket(issue, check_resolved=True,
                                     transitions=sorted(self.config.roles.values()),
                                     children=children, parent=parent,
                                     native_links=native)

    def _dependencies(self, number: str, host: str, nwo: str,
                      which: str) -> list[dict] | None:
        """The raw issue objects of issue `number`'s native dependencies:
        `which` = "blocked_by" (its blockers) or "blocking" (what it
        blocks). None on a 404: the issue exists, so the host has no
        dependencies API (older GHES). Any other failure raises, so
        `blockers` never reports "none" when it could not look."""
        args = ["api", f"repos/{nwo}/issues/{number}/dependencies/{which}?per_page=100",
                "--paginate"]
        if host:
            args += ["--hostname", host]
        try:
            out = self._gh(*args)
        except ProviderError as e:
            if "HTTP 404" in str(e):
                return None
            raise
        return [i for i in _json_pages(out, " ".join(args)) if isinstance(i, dict)]

    def _native_links(self, number: str, host: str, nwo: str, blocking=True):
        """(blocked_by, blocks) from the native dependencies, keyed like
        children; `resolved` = the blocker is closed, which the response
        already carries. ([], []) where the API is missing; `blocks` stays
        [] without `blocking` (one call fewer)."""
        blocked_by, blocks = [], []
        for issue in self._dependencies(number, host, nwo, "blocked_by") or []:
            k = _related_key(issue, nwo)
            if k and k != number and k not in {b["key"] for b in blocked_by}:
                blocked_by.append({"key": k,
                                   "resolved": str(issue.get("state")).lower() == "closed"})
        for issue in (self._dependencies(number, host, nwo, "blocking")
                      if blocking else None) or []:
            k = _related_key(issue, nwo)
            if k and k != number:
                blocks.append(k)
        return blocked_by, list(dict.fromkeys(blocks))

    def _children(self, number: str, host: str, nwo: str) -> list[str]:
        """Keys of the issue's sub-issues: "12" for one in this repo,
        "owner/repo#12" for one elsewhere (a bare number would name the
        wrong issue). One extra call, so `view` only. A host without the
        sub-issues API (older GHES) answers 404, which means "none here".

        `host` and `nwo` (lowercased `owner/name`) come from the issue's own
        URL, which is canonical whatever `--repo` spelling the config uses.
        `host` is passed whenever known, so a GHES issue is never looked up
        on gh's default host (and vice versa)."""
        keys = [_related_key(sub, nwo) for sub in self._sub_issues(number, host, nwo)]
        return [k for k in dict.fromkeys(keys) if k and k != number]

    def _sub_issues(self, number: str, host: str, nwo: str,
                    strict: bool = False) -> list[dict]:
        """The raw sub-issue objects of issue `number`; [] on a 404. With
        `strict`, a 404 for an issue that does not exist raises NotFound
        (GitHub also answers 404 where the sub-issues API is missing)."""
        args = ["api", f"repos/{nwo}/issues/{number}/sub_issues?per_page=100",
                "--paginate"]
        if host:
            args += ["--hostname", host]
        try:
            out = self._gh(*args)
        except ProviderError as e:
            if "HTTP 404" not in str(e):
                raise
            if strict and not self._issue_exists(number):
                raise NotFoundError(f"no issue #{number} in {self.repo}") from e
            return []
        return [s for s in _json_pages(out, " ".join(args)) if isinstance(s, dict)]

    def _issue_exists(self, number: str) -> bool:
        try:
            self._gh_json("issue", "view", number, "--repo", self.repo, "--json", "number")
        except ProviderError:
            return False
        return True

    def child_states(self, key, children=None):
        """One sub-issues call carrying each child's state, not a view per
        child (which would fail on a cross-repo `owner/repo#N` key). Closed =
        the issue is closed, or its lane is a closed role. Lane comes from
        labels, or in projectv2 mode from one item-list call (in-repo
        children only; GitHub numbers repeat across repos)."""
        host, nwo = _repo_host(self.repo), self._repo_name
        number = str(key)
        number = number[1:] if number.startswith("#") else number
        if not number.isdigit():
            raise UsageError(f"children takes an issue number in {self.repo}, got {key!r}")
        subs = self._sub_issues(number, host, nwo, strict=True)
        item_status: dict[str, str] = {}
        if self.board == "projectv2" and subs:
            for item in self._repo_items():
                n = (item.get("content", {}) or {}).get("number")
                if n is not None:
                    item_status[str(n)] = self._item_status(item, self.status_field)
        closed_roles = self.config.closed_roles()
        out, seen = [], set()
        for sub in subs:
            k = _related_key(sub, nwo)
            if not k or k == number or k in seen:
                continue
            seen.add(k)
            if self.board == "labels":
                status = self._status_from_labels(self._label_names(sub.get("labels")))
            else:
                status = item_status.get(k, "") if "#" not in k else ""
            role = self.config.lane_to_role(status) if status else ""
            out.append({"key": k, "status": status, "status_role": role,
                        "closed": sub.get("state") == "closed" or role in closed_roles})
        return out

    def _parent(self, number: str, host: str, nwo: str) -> str:
        """Key of the issue's parent (the issue it is a sub-issue of), keyed
        like `_children`; "" when it has none. One extra call, so `view`
        only. GitHub answers 404 both for "no parent" and on a host without
        the sub-issues API (older GHES); either way, "none here"."""
        args = ["api", f"repos/{nwo}/issues/{number}/parent"]
        if host:
            args += ["--hostname", host]
        try:
            out = self._gh(*args)
        except ProviderError as e:
            if "HTTP 404" in str(e):
                return ""
            raise
        try:
            issue = json.loads(out)
        except json.JSONDecodeError as e:
            raise ProviderError(f"gh returned non-JSON for {' '.join(args)}: {e}") from e
        key = _related_key(issue, nwo)
        return key if key != number else ""

    has_artifact_body = True

    def artifact_body(self, ticket):
        return ticket.description

    def transition(self, key, role):
        lane = self.config.role_to_lane(role)
        role = self.config.lane_to_role(lane)  # caller may have passed the lane name
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
            self._sync_issue_state(key, role)
            return
        # labels mode: swap the Status: label
        cur = self.view(key, hierarchy=False)
        args = ["issue", "edit", key, "--repo", self.repo, "--add-label", f"{self.status_label_prefix}{lane}"]
        existing = self._status_from_labels(cur.labels)
        if existing and existing != lane:
            args += ["--remove-label", f"{self.status_label_prefix}{existing}"]
        self._gh(*args)
        self._sync_issue_state(key, role)

    def _sync_issue_state(self, key, role):
        """Close or reopen the issue so its open/closed state agrees with the board.

        Driven by `[board].close_on`. A board status is only a label or a project
        field; GitHub's own issue list shows open/closed, and that is the surface
        people read. Runs after the board update, so a failed close leaves the
        board moved and the issue still open; a retry is idempotent (same label,
        then close) and heals it. Skipped entirely when `close_on` is unset.
        """
        close_on = self.config.close_on
        if not close_on:
            return
        state = self._gh_json("issue", "view", key, "--repo", self.repo,
                              "--json", "state").get("state", "")
        if role in close_on:
            if state != "CLOSED":
                # gh spells it with a space: {completed|not planned|duplicate}
                reason = "not planned" if role == "cancelled" else "completed"
                self._gh("issue", "close", key, "--repo", self.repo, "--reason", reason)
        elif state == "CLOSED":
            self._gh("issue", "reopen", key, "--repo", self.repo)

    def comment(self, key, body):
        self._gh("issue", "comment", key, "--repo", self.repo, "--body", body)

    def blockers(self, key):
        return self._view(key, hierarchy=False, links=True).unresolved_blockers()

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
        return self.view(number, hierarchy=False)  # brand new: it has none

    def link(self, key, to, link_type):
        lt = link_type.strip().lower()
        if lt in ("is blocked by", "blocks", "depends on"):
            a, b = _issue_number(key), _issue_number(to)
            if a == b:
                raise UsageError(f"issue #{a} cannot block itself")
            blocker, blocked = (a, b) if lt == "blocks" else (b, a)
            self._link_blocker(blocker, blocked)
        else:
            ref = to if to.startswith("#") else f"#{to}"
            self.comment(key, f"{link_type} {ref}")

    def _link_blocker(self, blocker: str, blocked: str):
        """Record "`blocker` blocks `blocked`" so both issues read it back.

        Native issue dependencies first (the web UI shows them too): one
        POST on the blocked issue, skipped when the link is already there.
        Where that API 404s, append `Blocked by #A` to the blocked issue's
        body and `Blocks #B` to the blocker's, each only if missing. Both
        issues are fetched before anything is written, so a missing one
        fails with nothing changed."""
        host, nwo = _repo_host(self.repo), self._repo_name
        issues = {n: self._rest_issue(n, host, nwo) for n in (blocker, blocked)}
        deps = self._dependencies(blocked, host, nwo, "blocked_by")
        if deps is not None:
            # By database id, not key: after a rename or transfer the API
            # names the new repo, which a configured name would miss.
            if any(d.get("id") == issues[blocker]["id"] for d in deps):
                return
            args = ["api", "-X", "POST",
                    f"repos/{nwo}/issues/{blocked}/dependencies/blocked_by",
                    "-F", f"issue_id={issues[blocker]['id']}"]
            if host:
                args += ["--hostname", host]
            self._gh(*args)
            return
        self._append_link(blocked, issues[blocked], "Blocked by", blocker, _BLOCKED_RE)
        self._append_link(blocker, issues[blocker], "Blocks", blocked, _BLOCKS_RE)

    def _append_link(self, number: str, issue: dict, verb: str, other: str, pattern):
        body = (issue.get("body") or "").rstrip()
        if other in _numbers(pattern, body):
            return
        self._gh("issue", "edit", number, "--repo", self.repo,
                 "--body", f"{body}\n\n{verb} #{other}".strip())

    def _rest_issue(self, number: str, host: str, nwo: str) -> dict:
        """Issue `number` as the REST API returns it (carries the database
        `id` the dependencies API takes); NotFound on a 404."""
        args = ["api", f"repos/{nwo}/issues/{number}"]
        if host:
            args += ["--hostname", host]
        try:
            out = self._gh(*args)
        except ProviderError as e:
            if "HTTP 404" in str(e):
                raise NotFoundError(f"no issue #{number} in {self.repo}") from e
            raise
        try:
            issue = json.loads(out)
        except json.JSONDecodeError as e:
            raise ProviderError(f"gh returned non-JSON for {' '.join(args)}: {e}") from e
        if not isinstance(issue, dict) or "id" not in issue:
            raise ProviderError(f"gh returned no issue id for {' '.join(args)}")
        return issue

    def worklog(self, key, from_role, note="", billable=False):
        # GitHub has no time tracking.
        return Worklog(key=key, role=from_role,
                       lane=self.config.role_to_lane(from_role), note=note)

    def lane_time(self, key, role, read_only=False):
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
                # A repo that matches none of the items would list as an idle
                # board, which is indistinguishable from a mistyped repo.
                items = self._project_items()
                mine = sum(1 for i in items if self._in_repo(i))
                checks.append(Check("project items in repo", mine > 0 or not items,
                                    f"{mine} of {len(items)} items are in {self.repo}"))
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


def _name_with_owner(repo: str) -> str:
    """`owner/name`, lowercased, from any `--repo` spelling gh accepts
    (`owner/name`, `HOST/owner/name`, a URL, `git@HOST:owner/name`),
    tolerating `.git` and slashes."""
    parts = [p for p in re.split(r"[/:]", repo.strip()) if p]
    name = "/".join(parts[-2:]).lower()
    return name[:-4] if name.endswith(".git") else name


def _repo_host(repo: str) -> str:
    """Best-effort host from a `--repo` spelling (`HOST/owner/name`, a URL,
    `git@HOST:owner/name`), userinfo and port dropped; "" for `owner/name`
    (gh then uses its default host). Only a fallback for when the issue's
    own URL is unusable; see `_issue_url_repo`."""
    s = repo.strip()
    has_scheme = "://" in s
    s = s.split("://", 1)[-1]
    head = s.split("/", 1)[0]
    if not has_scheme and ":" in head:
        # scp-like `[user@]host:owner/name`
        return head.split(":", 1)[0].rpartition("@")[2].lower()
    parts = [p for p in s.split("/") if p]
    if len(parts) < 3:
        return ""
    return parts[-3].rpartition("@")[2].split(":", 1)[0].lower()


def _issue_url_repo(url) -> tuple[str, str]:
    """(host, lowercased `owner/name`) from an issue's `url`
    (`https://HOST/owner/name/issues/N`), or ("", "") if it isn't one."""
    if not isinstance(url, str) or not url.strip():
        return "", ""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()   # userinfo and port dropped
    segs = [p for p in parts.path.split("/") if p]
    if not host or len(segs) < 4 or segs[-2] != "issues":
        return "", ""
    return host, f"{segs[-4]}/{segs[-3]}".lower()


def _json_pages(out: str, what: str) -> list:
    """Every item from `gh api --paginate` output on an array endpoint.

    gh writes each page as its own JSON array, back to back (`[...][...]`),
    which `json.loads` rejects past page one. `--slurp` would wrap them, but
    it only exists from gh 2.48, so decode the pages one at a time instead.
    """
    dec, items, i, n = json.JSONDecoder(), [], 0, len(out)
    while True:
        while i < n and out[i].isspace():
            i += 1
        if i >= n:
            return items
        try:
            page, i = dec.raw_decode(out, i)
        except json.JSONDecodeError as e:
            raise ProviderError(f"gh returned non-JSON for {what}: {e}") from e
        if isinstance(page, list):
            items += page
        else:
            raise ProviderError(f"gh returned a non-array page for {what}")


def _numbers(pattern, body: str) -> list[str]:
    """Issue numbers `pattern` captures in `body`, canonical (`#05` -> "5")."""
    return [str(int(n)) for n in pattern.findall(body)]


def _issue_number(key) -> str:
    """An issue number in `[github].repo` from `12` or `#12`; anything else
    (a cross-repo `owner/repo#12`, a typo) is a UsageError."""
    s = str(key).strip()
    s = s[1:] if s.startswith("#") else s
    if not (s.isascii() and s.isdigit()):
        raise UsageError(f"blocker links take issue numbers in this repo, got {key!r}")
    return str(int(s))


def _related_key(issue, nwo: str) -> str:
    """How this adapter keys a sub-issue or parent from the REST API: its
    bare number when it lives in `nwo` (the viewed issue's lowercased
    `owner/name`), "owner/repo#N" when elsewhere, so a number from another
    repo is never read as one of ours; "" if it carries no number."""
    if not isinstance(issue, dict) or issue.get("number") in (None, ""):
        return ""
    repo = _sub_issue_repo(issue)
    if not repo or _name_with_owner(repo) == nwo:
        return str(issue["number"])
    return f"{repo}#{issue['number']}"


def _sub_issue_repo(sub: dict) -> str:
    """`owner/name` a sub-issue lives in, as GitHub spelled it."""
    repo = sub.get("repository")
    if isinstance(repo, dict) and repo.get("full_name"):
        return repo["full_name"]
    # https://api.github.com/repos/<owner>/<name>
    url = sub.get("repository_url") or ""
    if "/repos/" in url:
        return "/".join(url.split("/repos/", 1)[1].split("/")[:2])
    # https://github.com/<owner>/<name>/issues/<n>
    parts = (sub.get("html_url") or "").split("/")
    return "/".join(parts[3:5]) if len(parts) > 4 else ""


# One parser for every backend that stores acceptance in a free-form
# description (github, linear, openkanban).
_extract_acceptance = ticketdoc.extract_acceptance
