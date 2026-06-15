"""Markdown board adapter — zero external deps, the portable default and the
proof that the abstraction holds.

Layout (paths relative to the config file's directory unless absolute):

    [markdown]
    board_dir   = ".sdlc/board"        # one <KEY>.md per ticket
    state_dir   = ".sdlc/state"        # history + worklog sidecars (machine-local)
    me          = "raymond"            # value for currentUser()

Each ticket file:

    ---
    type: Story
    status: To Do
    priority: High
    assignee: raymond
    labels: [api, auth]
    blocked_by: [TKT-2]
    blocks: []
    ---
    # One-line summary

    Free-form description...

    ## Acceptance
    - criterion one
    - criterion two

    ## Comments
    - 2026-06-02T10:00:00Z raymond: started work

Frontmatter is a deliberately tiny YAML subset: `key: value`, with `[a, b]`
list literals. No nested structures. Status transitions and time tracking are
recorded in JSONL sidecars under state_dir, never the markdown (markdown stays
the human-canonical source; sidecars are derived/machine-local).
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from core.errors import NotFoundError, ProviderError
from core.query import JqlSubset
from core.schema import Check, Ticket, Worklog, human_duration

from .base import Adapter


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _parse_scalar(raw: str):
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [x.strip().strip("'\"") for x in inner.split(",") if x.strip()]
    return raw.strip("'\"")


class MarkdownAdapter(Adapter):
    def __init__(self, config):
        super().__init__(config)
        mc = config.provider_cfg
        # Relative paths resolve from the project root (the dir that CONTAINS
        # .sdlc), so ".sdlc/board" doesn't double up when config lives at
        # .sdlc/config.toml.
        cfg_dir = config.path.parent
        base = cfg_dir.parent if cfg_dir.name == ".sdlc" else cfg_dir
        self.board_dir = self._resolve(base, mc.get("board_dir", ".sdlc/board"))
        self.state_dir = self._resolve(base, mc.get("state_dir", ".sdlc/state"))
        self.me = mc.get("me", "")

    @staticmethod
    def _resolve(base: Path, p: str) -> Path:
        path = Path(p).expanduser()
        return path if path.is_absolute() else (base / path)

    # ---- file helpers ------------------------------------------------------

    def _ticket_path(self, key: str) -> Path:
        return self.board_dir / f"{key}.md"

    def _read_raw(self, key: str) -> tuple[dict, str]:
        path = self._ticket_path(key)
        if not path.is_file():
            raise NotFoundError(f"ticket {key} not found at {path}")
        text = path.read_text()
        fm: dict[str, object] = {}
        body = text
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                fm_block = text[3:end].strip("\n")
                body = text[end + 4:].lstrip("\n")
                for line in fm_block.splitlines():
                    if ":" not in line:
                        continue
                    k, _, v = line.partition(":")
                    fm[k.strip()] = _parse_scalar(v)
        return fm, body

    def _write_raw(self, key: str, fm: dict, body: str) -> None:
        lines = ["---"]
        for k, v in fm.items():
            if isinstance(v, list):
                lines.append(f"{k}: [{', '.join(str(x) for x in v)}]")
            else:
                lines.append(f"{k}: {v}")
        lines.append("---")
        self._ticket_path(key).write_text("\n".join(lines) + "\n\n" + body.lstrip("\n"))

    def _history_path(self, key: str) -> Path:
        return self.state_dir / f"{key}.history.jsonl"

    def _append_history(self, key: str, frm: str, to: str) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with open(self._history_path(key), "a") as fh:
            fh.write(json.dumps({"ts": _iso(_now()), "from": frm, "to": to}) + "\n")

    def _read_history(self, key: str) -> list[dict]:
        path = self._history_path(key)
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def _append_worklog(self, key: str, role: str, lane: str, secs: int, note: str) -> str:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        wl_path = self.state_dir / "worklog.jsonl"
        existing = wl_path.read_text().splitlines() if wl_path.is_file() else []
        wl_id = str(len([x for x in existing if x.strip()]) + 1)
        with open(wl_path, "a") as fh:
            fh.write(json.dumps({
                "id": wl_id, "key": key, "role": role, "lane": lane,
                "seconds": secs, "started": _iso(_now()), "note": note,
            }) + "\n")
        return wl_id

    # ---- normalization -----------------------------------------------------

    def _to_ticket(self, key: str, fm: dict, body: str) -> Ticket:
        summary = ""
        description_lines: list[str] = []
        acceptance: list[str] = []
        section = None
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("# ") and not summary:
                summary = stripped[2:].strip()
                continue
            if stripped.lower().startswith("## acceptance"):
                section = "acceptance"
                continue
            if stripped.startswith("## "):
                section = None
                continue
            if section == "acceptance" and stripped.startswith(("-", "*")):
                acceptance.append(stripped[1:].strip())
            elif section is None and stripped:
                description_lines.append(stripped)

        status = str(fm.get("status", ""))
        issue_type = str(fm.get("type", ""))
        blocked_raw = fm.get("blocked_by", []) or []
        if isinstance(blocked_raw, str):
            blocked_raw = [blocked_raw]
        blocked_by = [{"key": k, "resolved": self._is_resolved(k)} for k in blocked_raw]
        blocks = fm.get("blocks", []) or []
        if isinstance(blocks, str):
            blocks = [blocks]

        return Ticket(
            key=key,
            type=issue_type,
            summary=summary,
            description="\n".join(description_lines),
            status=status,
            status_role=self.config.lane_to_role(status),
            type_class=self.config.type_class(issue_type),
            assignee=str(fm.get("assignee", "")),
            priority=str(fm.get("priority", "")),
            url=str(self._ticket_path(key)),
            acceptance=acceptance,
            labels=list(fm.get("labels", []) or []),
            components=list(fm.get("components", []) or []),
            blocked_by=blocked_by,
            blocks=list(blocks),
            transitions=sorted(self.config.roles.values()),
        )

    def _is_resolved(self, key: str) -> bool:
        try:
            fm, _ = self._read_raw(key)
        except NotFoundError:
            return False
        done_lane = self.config.roles.get("done")
        return str(fm.get("status", "")) == done_lane

    # ---- verbs -------------------------------------------------------------

    def whoami(self) -> str:
        return self.me or "(unset; set [markdown].me)"

    def _all_tickets(self) -> list[Ticket]:
        if not self.board_dir.is_dir():
            raise ProviderError(f"board_dir does not exist: {self.board_dir}")
        out = []
        for path in sorted(self.board_dir.glob("*.md")):
            key = path.stem
            fm, body = self._read_raw(key)
            out.append(self._to_ticket(key, fm, body))
        return out

    def list(self, tier=None, query=None):
        q = self.config.query(tier=tier, name=query)
        return JqlSubset(q, self.me).run(self._all_tickets())

    def view(self, key):
        fm, body = self._read_raw(key)
        return self._to_ticket(key, fm, body)

    def transition(self, key, role):
        lane = self.config.role_to_lane(role)
        fm, body = self._read_raw(key)
        frm = str(fm.get("status", ""))
        if frm == lane:
            return
        fm["status"] = lane
        self._write_raw(key, fm, body)
        self._append_history(key, frm, lane)

    def comment(self, key, body):
        fm, doc = self._read_raw(key)
        stamp = f"- {_iso(_now())} {self.me or 'agent'}: {body}"
        if "## Comments" in doc:
            doc = doc.rstrip() + "\n" + stamp + "\n"
        else:
            doc = doc.rstrip() + "\n\n## Comments\n" + stamp + "\n"
        self._write_raw(key, fm, doc)

    def blockers(self, key):
        return self.view(key).unresolved_blockers()

    def _next_key(self, project: str) -> str:
        prefix = project or self.config.project or "TKT"
        n = 0
        if self.board_dir.is_dir():
            for path in self.board_dir.glob(f"{prefix}-*.md"):
                suffix = path.stem[len(prefix) + 1:]
                if suffix.isdigit():
                    n = max(n, int(suffix))
        return f"{prefix}-{n + 1}"

    def create(self, issue_type, summary, priority="", assignee="", body="", project=""):
        self.board_dir.mkdir(parents=True, exist_ok=True)
        key = self._next_key(project)
        todo_lane = self.config.roles.get("todo") or self.config.roles.get("backlog", "To Do")
        fm = {
            "type": issue_type,
            "status": todo_lane,
            "priority": priority,
            "assignee": assignee or self.me,
            "blocked_by": [],
            "blocks": [],
        }
        doc = f"# {summary}\n"
        if body:
            doc += f"\n{body}\n"
        self._write_raw(key, fm, doc)
        self._append_history(key, "", todo_lane)
        return self.view(key)

    def link(self, key, to, link_type):
        fm, body = self._read_raw(key)
        lt = link_type.strip().lower()
        if lt == "is blocked by":
            cur = fm.get("blocked_by", []) or []
            if isinstance(cur, str):
                cur = [cur]
            if to not in cur:
                cur.append(to)
            fm["blocked_by"] = cur
        elif lt == "blocks":
            cur = fm.get("blocks", []) or []
            if isinstance(cur, str):
                cur = [cur]
            if to not in cur:
                cur.append(to)
            fm["blocks"] = cur
        else:
            cur = fm.get("links", []) or []
            if isinstance(cur, str):
                cur = [cur]
            cur.append(f"{link_type}:{to}")
            fm["links"] = cur
        self._write_raw(key, fm, body)

    @staticmethod
    def _split_body(doc: str) -> tuple[str, str, str]:
        """Split a ticket body into (summary, description, rest).

        summary = the first `# ` heading; description = the prose between it and
        the first `## ` section; rest = that first `## ` section onward
        (Acceptance, Comments, ...), preserved verbatim so an edit doesn't drop
        acceptance criteria or the comment log."""
        summary = ""
        desc_lines: list[str] = []
        rest_lines: list[str] = []
        seen_summary = False
        in_rest = False
        for line in doc.splitlines():
            s = line.strip()
            if in_rest:
                rest_lines.append(line)
            elif not seen_summary and s.startswith("# "):
                summary = s[2:].strip()
                seen_summary = True
            elif s.startswith("## "):
                in_rest = True
                rest_lines.append(line)
            else:
                desc_lines.append(line)
        return summary, "\n".join(desc_lines).strip(), "\n".join(rest_lines).strip()

    def edit(self, key, summary=None, body=None, priority=None, assignee=None,
             add_labels=None, remove_labels=None):
        fm, doc = self._read_raw(key)

        if priority is not None:
            fm["priority"] = priority
        if assignee is not None:
            fm["assignee"] = assignee

        if add_labels or remove_labels:
            cur = fm.get("labels", []) or []
            if isinstance(cur, str):
                cur = [cur]
            cur = list(cur)
            for lbl in (add_labels or []):
                if lbl not in cur:
                    cur.append(lbl)
            for lbl in (remove_labels or []):
                if lbl in cur:
                    cur.remove(lbl)
            fm["labels"] = cur

        # Only rebuild the body when summary/description actually change, so a
        # pure frontmatter edit leaves the markdown (and its Comments) untouched.
        if summary is not None or body is not None:
            cur_summary, cur_desc, rest = self._split_body(doc)
            new_summary = cur_summary if summary is None else summary
            new_desc = cur_desc if body is None else body
            doc = f"# {new_summary}\n"
            if new_desc:
                doc += f"\n{new_desc}\n"
            if rest:
                doc += f"\n{rest}\n"

        self._write_raw(key, fm, doc)
        return self.view(key)

    def worklog(self, key, from_role, note="", billable=False):
        lane = self.config.role_to_lane(from_role)
        if self.config.timetracking.get("provider", "none") == "none":
            return Worklog(key=key, role=from_role, lane=lane, note=note)
        history = self._read_history(key)
        entered = None
        for h in history:
            if h["to"] == lane:
                entered = _parse_iso(h["ts"])
        if entered is None:
            raise ProviderError(f"no transition into '{lane}' in history for {key}")
        secs = max(int((_now() - entered).total_seconds()), 60)
        wl_id = self._append_worklog(key, from_role, lane, secs, note)
        return Worklog(key=key, role=from_role, lane=lane, seconds=secs,
                       human=human_duration(secs), worklog_id=wl_id, note=note)

    def lane_time(self, key, role):
        lane = self.config.role_to_lane(role)
        if self.config.timetracking.get("provider", "none") == "none":
            return Worklog(key=key, role=role, lane=lane)
        history = sorted(self._read_history(key), key=lambda h: h["ts"])
        last_in = None
        for h in history:
            if h["to"] == lane:
                last_in = _parse_iso(h["ts"])
        if last_in is None:
            raise ProviderError(f"no entry into '{lane}' in history for {key}")
        exit_dt = _now()
        for h in history:
            ts = _parse_iso(h["ts"])
            if h["from"] == lane and ts > last_in:
                exit_dt = ts
                break
        secs = max(int((exit_dt - last_in).total_seconds()), 60)
        wl_id = self._append_worklog(key, role, lane, secs, "retroactive")
        return Worklog(key=key, role=role, lane=lane, seconds=secs,
                       human=human_duration(secs), worklog_id=wl_id, note="retroactive")

    def doctor(self):
        checks = [
            Check("board_dir exists", self.board_dir.is_dir(), str(self.board_dir)),
            Check("roles configured", bool(self.config.roles),
                  f"{len(self.config.roles)} roles"),
            Check("done role mapped", "done" in self.config.roles,
                  self.config.roles.get("done", "MISSING")),
            Check("me set", bool(self.me), self.me or "set [markdown].me for currentUser()"),
        ]
        return checks
