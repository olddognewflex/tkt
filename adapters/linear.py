"""Linear adapter — GraphQL API (https://api.linear.app/graphql).

Issues are tickets (key = identifier like ENG-123). Board lanes = Linear workflow
states (transition updates the issue's state). Blockers use Linear's native issue
relations. No CLI — talks GraphQL over urllib.

Auth: a Linear API key in env (named in [ticketing].auth_env, default LINEAR_API_KEY).
Personal API keys go in the Authorization header verbatim (no "Bearer").

Config ([linear] block):
  team_key              required, e.g. "ENG" (the team whose board this is)
  type_label_prefix     default "" (empty = match labels against [issue_types] names)
  list_limit            default 100 (working set fetched before client-side query)

Board roles map to Linear workflow-state NAMES:
  [board.roles]
  in_progress = "In Progress"   # must match a state name on the team

Time tracking: Linear has none — worklog/lane-time are no-ops; set
[timetracking].provider = "none".
"""
import json
import os
import re
import urllib.request

from core.errors import ConfigError, ProviderError
from core.query import JqlSubset
from core.schema import Check, Ticket, Worklog

from .base import Adapter

_ENDPOINT = "https://api.linear.app/graphql"
# Linear numeric priority -> label (matches priorityLabel).
_PRIORITY_LABEL = {0: "No priority", 1: "Urgent", 2: "High", 3: "Medium", 4: "Low"}
_PRIORITY_INT = {v.lower(): k for k, v in _PRIORITY_LABEL.items()}
# Workflow-state types Linear considers "done" — used for blocker resolution.
_DONE_STATE_TYPES = {"completed", "canceled"}

_ISSUE_FIELDS = """
  id identifier title description url priority priorityLabel
  state { name type }
  assignee { displayName name email }
  labels { nodes { name } }
  relations { nodes { type relatedIssue { identifier state { type } } } }
  inverseRelations { nodes { type issue { identifier state { type } } } }
"""


class LinearAdapter(Adapter):
    def __init__(self, config):
        super().__init__(config)
        lc = config.provider_cfg
        self.team_key = lc.get("team_key", "")
        self.type_label_prefix = lc.get("type_label_prefix", "")
        self.list_limit = int(lc.get("list_limit", 100))
        env_names = config.auth_env or ["LINEAR_API_KEY"]
        self.api_key = ""
        for name in env_names:
            if os.environ.get(name):
                self.api_key = os.environ[name]
                break
        self._me = None
        self._states = None   # {name: id}
        self._team_id = None
        self._labels = None   # {name: id}

    # ---- GraphQL -----------------------------------------------------------

    def _gql(self, query: str, variables: dict | None = None):
        if not self.api_key:
            raise ProviderError(
                "Linear API key missing — set "
                + " or ".join(self.config.auth_env or ["LINEAR_API_KEY"])
            )
        req = urllib.request.Request(
            _ENDPOINT, method="POST",
            headers={"Authorization": self.api_key,
                     "Content-Type": "application/json"},
            data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        )
        try:
            with urllib.request.urlopen(req) as resp:
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise ProviderError(f"Linear HTTP {e.code}: {e.read().decode(errors='replace')[:400]}") from e
        except urllib.error.URLError as e:
            raise ProviderError(f"Linear request failed: {e}") from e
        if payload.get("errors"):
            raise ProviderError(f"Linear GraphQL error: {payload['errors']}")
        return payload["data"]

    # ---- caches ------------------------------------------------------------

    def _require_team(self):
        if not self.team_key:
            raise ConfigError("linear needs [linear].team_key (e.g. \"ENG\")")

    def _team(self) -> str:
        if self._team_id is None:
            self._require_team()
            data = self._gql(
                "query($k:String!){ teams(filter:{key:{eq:$k}}){ nodes { id } } }",
                {"k": self.team_key})
            nodes = data["teams"]["nodes"]
            if not nodes:
                raise ProviderError(f"no Linear team with key '{self.team_key}'")
            self._team_id = nodes[0]["id"]
        return self._team_id

    def _state_map(self) -> dict:
        if self._states is None:
            data = self._gql(
                "query($k:String!){ workflowStates(filter:{team:{key:{eq:$k}}}){ nodes { id name } } }",
                {"k": self.team_key})
            self._states = {s["name"]: s["id"] for s in data["workflowStates"]["nodes"]}
        return self._states

    def _label_map(self) -> dict:
        if self._labels is None:
            data = self._gql(
                "query($k:String!){ issueLabels(filter:{team:{key:{eq:$k}}}){ nodes { id name } } }",
                {"k": self.team_key})
            self._labels = {l["name"]: l["id"] for l in data["issueLabels"]["nodes"]}
        return self._labels

    # ---- normalization -----------------------------------------------------

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

    def _to_ticket(self, issue: dict, transitions=None) -> Ticket:
        labels = [n["name"] for n in (issue.get("labels", {}) or {}).get("nodes", [])]
        issue_type = self._type_from_labels(labels)
        state = issue.get("state") or {}
        status = state.get("name", "")
        blocked_by = []
        for r in (issue.get("inverseRelations", {}) or {}).get("nodes", []):
            if r.get("type") == "blocks" and r.get("issue"):
                other = r["issue"]
                resolved = (other.get("state") or {}).get("type") in _DONE_STATE_TYPES
                blocked_by.append({"key": other.get("identifier"), "resolved": resolved})
        blocks = [r["relatedIssue"]["identifier"]
                  for r in (issue.get("relations", {}) or {}).get("nodes", [])
                  if r.get("type") == "blocks" and r.get("relatedIssue")]
        assignee = issue.get("assignee") or {}
        return Ticket(
            key=issue.get("identifier", ""),
            type=issue_type,
            summary=issue.get("title", ""),
            description=issue.get("description", "") or "",
            status=status,
            status_role=self.config.lane_to_role(status) if status else "",
            type_class=self.config.type_class(issue_type),
            assignee=assignee.get("displayName") or assignee.get("name") or assignee.get("email", ""),
            priority=issue.get("priorityLabel", "") or _PRIORITY_LABEL.get(issue.get("priority", 0), ""),
            url=issue.get("url", ""),
            acceptance=_extract_acceptance(issue.get("description", "") or ""),
            labels=labels,
            components=[],
            blocked_by=blocked_by,
            blocks=blocks,
            transitions=transitions or list(self._state_map_safe()),
        )

    def _state_map_safe(self) -> dict:
        try:
            return self._state_map()
        except ProviderError:
            return {}

    def _fetch_issue(self, key: str) -> dict:
        # identifier ENG-123 -> team key + number
        m = re.match(r"^([A-Za-z]+)-(\d+)$", key)
        if not m:
            raise ProviderError(f"not a Linear identifier: {key}")
        team_key, number = m.group(1), int(m.group(2))
        data = self._gql(
            "query($k:String!,$n:Float!){ issues(filter:{team:{key:{eq:$k}},number:{eq:$n}}, first:1){ nodes { "
            + _ISSUE_FIELDS + " } } }",
            {"k": team_key, "n": number})
        nodes = data["issues"]["nodes"]
        if not nodes:
            raise ProviderError(f"no Linear issue {key}")
        return nodes[0]

    # ---- verbs -------------------------------------------------------------

    def whoami(self) -> str:
        if self._me is None:
            data = self._gql("{ viewer { displayName name email } }")
            v = data["viewer"]
            self._me = v.get("displayName") or v.get("name") or v.get("email", "")
        return self._me

    def list(self, tier=None, query=None):
        q = self.config.query(tier=tier, name=query)
        self._require_team()
        data = self._gql(
            "query($k:String!,$n:Int!){ issues(filter:{team:{key:{eq:$k}}}, first:$n){ nodes { "
            + _ISSUE_FIELDS + " } } }",
            {"k": self.team_key, "n": self.list_limit})
        tickets = [self._to_ticket(i) for i in data["issues"]["nodes"]]
        return JqlSubset(q, self.whoami()).run(tickets)

    def view(self, key):
        return self._to_ticket(self._fetch_issue(key))

    def transition(self, key, role):
        lane = self.config.role_to_lane(role)
        state_id = self._state_map().get(lane)
        if not state_id:
            raise ProviderError(f"no Linear workflow state '{lane}' on team {self.team_key} "
                                f"(have: {', '.join(self._state_map())})")
        issue_id = self._fetch_issue(key)["id"]
        self._gql(
            "mutation($id:String!,$s:String!){ issueUpdate(id:$id, input:{stateId:$s}){ success } }",
            {"id": issue_id, "s": state_id})

    def comment(self, key, body):
        issue_id = self._fetch_issue(key)["id"]
        self._gql(
            "mutation($id:String!,$b:String!){ commentCreate(input:{issueId:$id, body:$b}){ success } }",
            {"id": issue_id, "b": body})

    def blockers(self, key):
        return self.view(key).unresolved_blockers()

    def create(self, issue_type, summary, priority="", assignee="", body="", project=""):
        team_id = self._team()
        inp = {"teamId": team_id, "title": summary, "description": body or ""}
        todo_lane = self.config.roles.get("todo") or self.config.roles.get("backlog")
        if todo_lane and todo_lane in self._state_map():
            inp["stateId"] = self._state_map()[todo_lane]
        if priority:
            pint = _PRIORITY_INT.get(priority.strip().lower())
            if pint is not None:
                inp["priority"] = pint
        label_ids = []
        if issue_type:
            name = f"{self.type_label_prefix}{issue_type}" if self.type_label_prefix else issue_type
            lid = self._label_map().get(name)
            if lid:
                label_ids.append(lid)
        if label_ids:
            inp["labelIds"] = label_ids
        if assignee:
            uid = self._resolve_user(assignee)
            if uid:
                inp["assigneeId"] = uid
        data = self._gql(
            "mutation($i:IssueCreateInput!){ issueCreate(input:$i){ success issue { identifier } } }",
            {"i": inp})
        res = data["issueCreate"]
        if not res.get("success"):
            raise ProviderError(f"Linear issueCreate failed: {data}")
        return self.view(res["issue"]["identifier"])

    def _resolve_user(self, who: str) -> str:
        data = self._gql(
            "query($q:String!){ users(filter:{or:[{email:{eq:$q}},{displayName:{eq:$q}},{name:{eq:$q}}]}){ nodes { id } } }",
            {"q": who})
        nodes = data["users"]["nodes"]
        return nodes[0]["id"] if nodes else ""

    def link(self, key, to, link_type):
        lt = link_type.strip().lower()
        # Map common types to Linear's native relation directions.
        if lt in ("blocks", "is blocked by", "depends on"):
            src, dst = (key, to) if lt == "blocks" else (to, key)  # src blocks dst
            rel = "blocks"
        elif lt in ("relates to", "related"):
            src, dst, rel = key, to, "related"
        elif lt in ("duplicates", "duplicate"):
            src, dst, rel = key, to, "duplicate"
        else:
            # Unsupported relation type — record as a comment instead.
            self.comment(key, f"{link_type} {to}")
            return
        src_id = self._fetch_issue(src)["id"]
        dst_id = self._fetch_issue(dst)["id"]
        self._gql(
            "mutation($i:String!,$r:String!,$t:IssueRelationType!){ "
            "issueRelationCreate(input:{issueId:$i, relatedIssueId:$r, type:$t}){ success } }",
            {"i": src_id, "r": dst_id, "t": rel})

    def worklog(self, key, from_role, note="", billable=False):
        return Worklog(key=key, role=from_role,
                       lane=self.config.role_to_lane(from_role), note=note)

    def lane_time(self, key, role):
        return Worklog(key=key, role=role, lane=self.config.role_to_lane(role))

    def doctor(self):
        checks = []
        if not self.api_key:
            checks.append(Check("api key set", False,
                                " or ".join(self.config.auth_env or ["LINEAR_API_KEY"])))
            return checks
        checks.append(Check("api key set", True, "configured"))
        try:
            checks.append(Check("linear reachable (viewer)", True, self.whoami()))
        except ProviderError as e:
            checks.append(Check("linear reachable (viewer)", False, str(e)))
            return checks
        try:
            self._require_team()
            states = self._state_map()
            missing = [lane for role, lane in self.config.roles.items()
                       if role not in ("blocked", "cancelled", "revise")
                       and lane not in states]
            checks.append(Check("team + workflow states reachable", True,
                                f"{self.team_key}: {len(states)} states"))
            checks.append(Check("states cover board roles", not missing,
                                "missing: " + ", ".join(missing) if missing else "all mapped"))
        except (ProviderError, ConfigError) as e:
            checks.append(Check("team reachable", False, str(e)))
        tt = self.config.timetracking.get("provider", "none")
        if tt != "none":
            checks.append(Check("timetracking is none", False,
                                f"Linear has no time tracking (got '{tt}')"))
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
