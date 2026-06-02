"""Jira adapter — ports the existing acli + REST + Tempo logic from the edge
SDLC skills behind the verb contract. Behavior is intended to match the
original skills exactly so the live DTB board is the regression baseline.

Auth (env names declared in config [ticketing].auth_env, defaults shown):
    CONFLUENCE_SITE       e.g. auctionedge.atlassian.net
    CONFLUENCE_EMAIL
    CONFLUENCE_API_TOKEN
    TEMPO_API_TOKEN       optional; required to mark worklogs non-billable
"""
import base64
import json
import os
import subprocess
import time
import urllib.request
from datetime import datetime, timezone

from core.errors import ProviderError
from core.schema import Check, Ticket, Worklog, human_duration

from .base import Adapter


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _adf_to_text(node) -> str:
    """Flatten Atlassian Document Format to plaintext."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    out = []
    if isinstance(node, dict):
        if node.get("type") == "text":
            out.append(node.get("text", ""))
        for child in node.get("content", []) or []:
            out.append(_adf_to_text(child))
        if node.get("type") in ("paragraph", "heading"):
            out.append("\n")
    elif isinstance(node, list):
        for child in node:
            out.append(_adf_to_text(child))
    return "".join(out)


class JiraAdapter(Adapter):
    def __init__(self, config):
        super().__init__(config)
        self.site = os.environ.get("CONFLUENCE_SITE", "")
        self.email = os.environ.get("CONFLUENCE_EMAIL", "")
        self.token = os.environ.get("CONFLUENCE_API_TOKEN", "")
        self.tempo_token = os.environ.get("TEMPO_API_TOKEN", "")
        self.project = config.project
        # REST (basic auth + API token) gives full fidelity incl. available
        # transitions and changelog. When the token is absent we fall back to
        # acli over its own (e.g. OAuth) session for reads — transitions then
        # come back empty (acli can't list them); skill routing relies on
        # type_class from config rather than live transitions, so that's fine.
        self.have_rest = bool(self.site and self.email and self.token)
        self._acli_site_cache: str | None = None

    # ---- low-level REST ----------------------------------------------------

    def _auth_header(self) -> str:
        return base64.b64encode(f"{self.email}:{self.token}".encode()).decode()

    def _jira(self, method: str, path: str, body=None):
        if not (self.site and self.email and self.token):
            raise ProviderError(
                "Jira auth missing — set CONFLUENCE_SITE, CONFLUENCE_EMAIL, "
                "CONFLUENCE_API_TOKEN"
            )
        req = urllib.request.Request(
            f"https://{self.site}{path}",
            method=method,
            headers={
                "Authorization": f"Basic {self._auth_header()}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            data=json.dumps(body).encode() if body else None,
        )
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise ProviderError(f"Jira {method} {path} -> {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise ProviderError(f"Jira request failed: {e}") from e

    def _tempo(self, method: str, path: str, body=None):
        req = urllib.request.Request(
            f"https://api.tempo.io/4{path}",
            method=method,
            headers={
                "Authorization": f"Bearer {self.tempo_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            data=json.dumps(body).encode() if body else None,
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def _acli(self, *args: str) -> str:
        try:
            return subprocess.run(
                ["acli", *args], capture_output=True, text=True, check=True
            ).stdout
        except FileNotFoundError as e:
            raise ProviderError("acli not found on PATH") from e
        except subprocess.CalledProcessError as e:
            raise ProviderError(f"acli {' '.join(args)} failed: {e.stderr.strip()}") from e

    def _acli_json(self, *args: str):
        out = self._acli(*args, "--json")
        try:
            return json.loads(out)
        except json.JSONDecodeError as e:
            raise ProviderError(f"acli returned non-JSON for {' '.join(args)}: {e}") from e

    def _site_for_url(self) -> str:
        """Site host for browse URLs. From env when REST-configured, else parsed
        once from the acli session."""
        if self.site:
            return self.site
        if self._acli_site_cache is None:
            self._acli_site_cache = ""
            for line in self._acli("jira", "auth", "status").splitlines():
                if "Site:" in line:
                    self._acli_site_cache = line.split("Site:", 1)[1].strip()
                    break
        return self._acli_site_cache

    # ---- normalization -----------------------------------------------------

    def _to_ticket(self, issue: dict, transitions: list[str] | None = None) -> Ticket:
        f = issue.get("fields", {})
        key = issue.get("key", "")
        issue_type = (f.get("issuetype") or {}).get("name", "")
        desc = _adf_to_text(f.get("description")).strip()
        blocked_by, blocks = [], []
        for link in f.get("issuelinks", []) or []:
            ltype = link.get("type", {})
            if "inwardIssue" in link and ltype.get("inward") == "is blocked by":
                inner = link["inwardIssue"]
                cat = (((inner.get("fields") or {}).get("status") or {})
                       .get("statusCategory") or {}).get("key", "")
                blocked_by.append({"key": inner.get("key"), "resolved": cat == "done"})
            if "outwardIssue" in link and ltype.get("outward") == "blocks":
                blocks.append(link["outwardIssue"].get("key"))
        status = (f.get("status") or {}).get("name", "")
        return Ticket(
            key=key,
            type=issue_type,
            summary=f.get("summary", ""),
            description=desc,
            status=status,
            status_role=self.config.lane_to_role(status),
            type_class=self.config.type_class(issue_type),
            assignee=((f.get("assignee") or {}).get("displayName")
                      or (f.get("assignee") or {}).get("emailAddress") or ""),
            priority=(f.get("priority") or {}).get("name", ""),
            url=f"https://{self._site_for_url()}/browse/{key}",
            acceptance=self._extract_acceptance(desc),
            labels=list(f.get("labels", []) or []),
            components=[c.get("name") for c in (f.get("components") or [])],
            blocked_by=blocked_by,
            blocks=blocks,
            transitions=transitions or [],
        )

    @staticmethod
    def _extract_acceptance(desc: str) -> list[str]:
        out, capture = [], False
        for line in desc.splitlines():
            low = line.strip().lower()
            if low.startswith("acceptance"):
                capture = True
                continue
            if capture:
                s = line.strip()
                if s.startswith(("-", "*", "•")):
                    out.append(s[1:].strip())
                elif not s:
                    continue
                else:
                    break
        return out

    # ---- verbs -------------------------------------------------------------

    def whoami(self) -> str:
        if self.have_rest:
            me = self._jira("GET", "/rest/api/3/myself")
            return me.get("displayName") or me.get("emailAddress") or me.get("accountId", "")
        # acli session: parse `acli jira auth status` text.
        for line in self._acli("jira", "auth", "status").splitlines():
            if "Email:" in line:
                return line.split("Email:", 1)[1].strip()
        return "(acli authenticated; identity unknown)"

    def _full_jql(self, jql: str) -> str:
        if self.project and "project" not in jql.lower():
            return f"project = {self.project} AND {jql}"
        return jql

    def list(self, tier=None, query=None):
        jql = self._full_jql(self.config.query(tier=tier, name=query))
        if self.have_rest:
            from urllib.parse import urlencode

            fields = "priority,assignee,summary,issuelinks,labels,components,issuetype,status"
            qs = urlencode({"jql": jql, "fields": fields, "maxResults": 25})
            data = self._jira("GET", f"/rest/api/3/search/jql?{qs}")
            return [self._to_ticket(i) for i in data.get("issues", [])]
        # acli search only allows navigable fields — it rejects issuelinks /
        # components, and never returns issuelinks regardless. So list results
        # in acli mode carry no blocked_by; callers needing blocker state must
        # confirm per-ticket via `tkt blockers` (which uses acli `view`, where
        # issuelinks ARE available). REST mode (above) returns the full set.
        fields = "priority,assignee,summary,labels,issuetype,status"
        issues = self._acli_json("jira", "workitem", "search", "--jql", jql,
                                 "--fields", fields, "--limit", "25")
        return [self._to_ticket(i) for i in (issues or [])]

    def view(self, key):
        if self.have_rest:
            issue = self._jira("GET", f"/rest/api/3/issue/{key}?fields=*all")
            trans = self._jira("GET", f"/rest/api/3/issue/{key}/transitions")
            names = [t["to"]["name"] for t in trans.get("transitions", [])]
            return self._to_ticket(issue, transitions=names)
        # acli read: full fields (default view omits priority/links), but no
        # available-transitions list (acli can't enumerate them).
        issue = self._acli_json("jira", "workitem", "view", key, "--fields", "*all")
        return self._to_ticket(issue, transitions=[])

    def transition(self, key, role):
        lane = self.config.role_to_lane(role)
        self._acli("jira", "workitem", "transition", "--key", key,
                   "--status", lane, "--yes")

    def comment(self, key, body):
        self._acli("jira", "workitem", "comment", "create", "--key", key, "--body", body)

    def blockers(self, key):
        return self.view(key).unresolved_blockers()

    # ---- time tracking (ported from annotate_lane_time) --------------------

    def _changelog_entries(self, key: str) -> list[tuple[str, str, str]]:
        """(ts, fromStatus, toStatus) for every status change, full pagination."""
        entries, start = [], 0
        while True:
            page = self._jira(
                "GET", f"/rest/api/3/issue/{key}/changelog?startAt={start}&maxResults=100"
            )
            for h in page.get("values", []):
                for it in h.get("items", []):
                    if it.get("field") == "status":
                        entries.append((h["created"], it.get("fromString"),
                                        it.get("toString")))
            if page.get("isLast", True):
                break
            start += page.get("maxResults", 100)
        return entries

    def _post_worklog(self, key: str, secs: int, started: str, note: str) -> str:
        wl = self._jira("POST", f"/rest/api/3/issue/{key}/worklog", {
            "timeSpentSeconds": secs,
            "started": started,
            "comment": {"type": "doc", "version": 1, "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": note}]}]},
        })
        return str(wl["id"])

    def _mark_non_billable(self, wl_id: str, secs: int, note: str) -> None:
        if not self.tempo_token:
            print(f"tkt: TEMPO_API_TOKEN not set; worklog {wl_id} left at Tempo "
                  f"default (billable).", flush=True)
            return
        for attempt in range(5):
            try:
                results = self._tempo("GET", f"/worklogs/jira/{wl_id}").get("results", [])
                if not results:
                    raise RuntimeError("Tempo has not synced the worklog yet")
                row = results[0]
                self._tempo("PUT", f"/worklogs/{row['tempoWorklogId']}", {
                    "issueId": row["issue"]["id"],
                    "timeSpentSeconds": secs,
                    "billableSeconds": 0,
                    "startDate": row["startDate"],
                    "startTime": row["startTime"],
                    "authorAccountId": row["author"]["accountId"],
                    "description": note,
                    "attributes": row.get("attributes", {"values": []}),
                })
                return
            except Exception as e:  # noqa: BLE001 — best-effort, retried
                if attempt == 4:
                    print(f"tkt: failed to set worklog {wl_id} non-billable: {e}",
                          flush=True)
                else:
                    time.sleep(2 ** attempt)

    def _provider_tracks_time(self) -> bool:
        return self.config.timetracking.get("provider", "none") != "none"

    def worklog(self, key, from_role, note="", billable=False):
        lane = self.config.role_to_lane(from_role)
        if not self._provider_tracks_time():
            return Worklog(key=key, role=from_role, lane=lane, note=note)
        times = [ts for ts, _, to in self._changelog_entries(key) if to == lane]
        if not times:
            raise ProviderError(f"no transition into '{lane}' in changelog for {key}")
        entered_str = max(times)
        secs = max(int((_now() - _parse_iso(entered_str)).total_seconds()), 60)
        body = f"Lane: {lane}. {note or 'Recorded by tkt.'}"
        wl_id = self._post_worklog(key, secs, entered_str, body)
        if not billable:
            self._mark_non_billable(wl_id, secs, body)
        return Worklog(key=key, role=from_role, lane=lane, seconds=secs,
                       human=human_duration(secs), worklog_id=wl_id, note=note)

    def lane_time(self, key, role):
        lane = self.config.role_to_lane(role)
        if not self._provider_tracks_time():
            return Worklog(key=key, role=role, lane=lane)
        entries = sorted(self._changelog_entries(key), key=lambda e: e[0])
        last_in = None
        for ts, _, to in entries:
            if to == lane:
                last_in = ts
        if not last_in:
            raise ProviderError(f"no entry into '{lane}' in changelog for {key}")
        exit_dt = _now()
        for ts, frm, _ in entries:
            if frm == lane and ts > last_in:
                exit_dt = _parse_iso(ts)
                break
        secs = max(int((exit_dt - _parse_iso(last_in)).total_seconds()), 60)
        body = f"Lane: {lane}. Recorded retroactively by tkt."
        wl_id = self._post_worklog(key, secs, last_in, body)
        self._mark_non_billable(wl_id, secs, body)
        return Worklog(key=key, role=role, lane=lane, seconds=secs,
                       human=human_duration(secs), worklog_id=wl_id, note="retroactive")

    def doctor(self):
        checks = []
        if self.have_rest:
            checks.append(Check("auth: REST token", True, "CONFLUENCE_SITE/EMAIL/API_TOKEN"))
            try:
                checks.append(Check("jira reachable (/myself)", True, self.whoami()))
            except ProviderError as e:
                checks.append(Check("jira reachable (/myself)", False, str(e)))
        else:
            try:
                who = self.whoami()
                acli_ok = "unknown" not in who
                checks.append(Check("auth: acli session", acli_ok,
                                    f"{who} (no REST token — reads via acli, "
                                    f"transitions list + worklog unavailable)"))
            except ProviderError as e:
                checks.append(Check("auth: acli session", False, str(e)))
        checks.append(Check("roles configured", bool(self.config.roles),
                            f"{len(self.config.roles)} roles"))
        tt = self.config.timetracking.get("provider", "none")
        if tt == "tempo":
            checks.append(Check("tempo token (for non-billable)",
                                bool(self.tempo_token),
                                "TEMPO_API_TOKEN" if self.tempo_token else "unset"))
        return checks
