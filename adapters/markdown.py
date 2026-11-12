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
    parent: TKT-1
    children: [TKT-4, TKT-5]
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
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from core import ticketdoc
from core.gates import cut_managed_tail
from core.errors import NotFoundError, ProviderError, UsageError
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


# `tkt link` types that set the hierarchy. `KEY child TO` and `TO parent KEY`
# are the same edge, so both spellings route to `_set_parent`.
_CHILD_LINKS = ("child", "has child")
_PARENT_LINKS = ("parent", "child of", "is child of")
# `tkt link` types that record a blocker. `KEY blocks TO` and `TO is blocked
# by KEY` are the same edge, so both route to `_set_blocks`.
_BLOCKS_LINKS = ("blocks",)
_BLOCKED_BY_LINKS = ("is blocked by", "depends on")


def _coerce_parent(value, key: str = "") -> str:
    """The `parent:` frontmatter value as one key, "" when none.

    `parent` is a scalar, but the frontmatter grammar turns `[X]` into a
    list, so a one-element list is read as that element. An empty or
    multi-element list names no single parent and reads as none, as does a
    self-reference (a ticket is never its own parent)."""
    if isinstance(value, list):
        value = value[0] if len(value) == 1 else ""
    out = str(value or "").strip()
    return "" if out == key else out


def _as_list(value) -> list:
    """A frontmatter list key, tolerating a bare scalar (`children: X`)."""
    value = value or []
    return [value] if isinstance(value, str) else list(value)


class _TicketIdentity:
    """Which board keys name the same ticket file, for one `_set_parent`,
    one `_set_blocks`, or one hierarchy self-reference check
    (`_without_self`).

    On a case-insensitive filesystem `tkt-3` and `TKT-3` open one file, so
    key strings alone miss existing edges. Keys are compared by file
    identity (st_dev, st_ino; what `os.path.samefile` compares), never by
    case-folding: on a case-sensitive filesystem `TKT-3.md` and `tkt-3.md`
    are two tickets. A key whose file does not exist compares by string.
    Each key is stat'd at most once per instance."""

    def __init__(self, board_dir: Path):
        self.board_dir = board_dir
        self._ids: dict[str, tuple | None] = {}

    def of(self, key: str):
        if key not in self._ids:
            try:
                st = (self.board_dir / f"{key}.md").stat()
                self._ids[key] = (st.st_dev, st.st_ino)
            except (OSError, ValueError):
                self._ids[key] = None
        return self._ids[key]

    def same(self, a: str, b: str) -> bool:
        if a == b:
            return True
        ia = self.of(a)
        return ia is not None and ia == self.of(b)

    def canonical(self, key: str) -> str:
        """`key` as spelled by the board file it opens (`tkt-3` ->
        `TKT-3` when the file is `TKT-3.md`), so links are never written
        in a miscased spelling. `key` itself when that exact name is a
        directory entry, or when no entry matches."""
        names = sorted(n for n in os.listdir(self.board_dir)
                       if n.endswith(".md") and not n.startswith("."))
        if f"{key}.md" in names:
            return key
        for name in names:
            if self.same(name[:-3], key):
                return name[:-3]
        return key


class MarkdownAdapter(Adapter):
    def __init__(self, config):
        super().__init__(config)
        mc = config.provider_cfg
        # Relative paths resolve from the project root, so ".sdlc/board"
        # doesn't double up when config lives at .sdlc/config.toml.
        base = config.root
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

    def _without_self(self, key: str, parent: str, children: list) -> tuple:
        """`(parent, children)` with every reference to `key`'s own file
        removed: `parent` becomes "" and such `children` entries are dropped.

        A reference is the key itself or any other name that opens the same
        file (a hard link, or a case variant on a case-insensitive
        filesystem); see `_TicketIdentity`, which never case-folds, so on a
        case-sensitive filesystem `tkt-1` stays a distinct ticket from
        `TKT-1`. Nothing is stat'd when there is no parent and no child, so
        `list` pays nothing for tickets outside a hierarchy."""
        if not parent and not children:
            return parent, children
        ids = _TicketIdentity(self.board_dir)
        if parent and ids.same(parent, key):
            parent = ""
        return parent, [k for k in children if not ids.same(str(k), key)]

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
                # Any other section (Comments, etc.) — mark it distinctly, not
                # None, so its body isn't folded into the description. None is
                # reserved for the preamble between the summary and the first
                # `## ` heading, which is the actual description.
                section = "other"
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
        # Hierarchy, from frontmatter: `children:` on the parent, `parent:`
        # on the child. `tkt link --type child|parent` writes both edges;
        # a hand edit or `apply` writes only the file it touches. A
        # reference to the ticket's own file, under any name, is dropped.
        parent, children = self._without_self(
            key, _coerce_parent(fm.get("parent", ""), key),
            [k for k in dict.fromkeys(str(c) for c in
                                      _as_list(fm.get("children"))) if k])

        def _date(name: str) -> str | None:
            v = fm.get(name)
            return str(v) if v not in (None, "", []) else None

        return Ticket(
            key=key,
            type=issue_type,
            summary=summary,
            description="\n".join(description_lines),
            status=status,
            status_role=self.config.lane_to_role(status),
            closed=self._closed(self.config.lane_to_role(status)),
            type_class=self.config.type_class(issue_type),
            assignee=str(fm.get("assignee", "")),
            priority=str(fm.get("priority", "")),
            agent_status=str(fm.get("agent_status", "")),
            agent_status_at=_date("agent_status_at"),
            due=_date("due"),
            scheduled=_date("scheduled"),
            completed=_date("completed"),
            url=str(self._ticket_path(key)),
            acceptance=acceptance,
            labels=list(fm.get("labels", []) or []),
            components=list(fm.get("components", []) or []),
            blocked_by=blocked_by,
            blocks=list(blocks),
            children=children,
            parent=parent,
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

    def _all_tickets(self, all_projects: bool = False) -> list[Ticket]:
        """Every ticket on this board that belongs to this project.

        One board dir is often shared by several repos, each with its own
        config (`[markdown].board_dir` pointing at the same place), so
        enumerating the whole directory hands a repo another project's
        tickets -- and `select-ticket` would auto-select one. Scope by the
        key prefix, the same convention `_next_key` already mints against.
        An unset `[ticketing].project` means "no scoping" and enumerates
        everything, which is the single-project case. `all_projects=True`
        does the same on demand (`tkt list --all-projects`), whatever the
        configured project.

        `view` stays deliberately unscoped: a blocker or link may point at
        another project's ticket and must still resolve by key.
        """
        if not self.board_dir.is_dir():
            raise ProviderError(f"board_dir does not exist: {self.board_dir}")
        prefix = "" if all_projects else self.config.project
        out = []
        for path in sorted(self.board_dir.glob("*.md")):
            key = path.stem
            # A shared board collects strays the prefix filter used to hide
            # (a `*.md` directory, editor dotfiles). Skipped only on the
            # all-projects path, so the scoped path is unchanged.
            if all_projects and (not path.is_file() or key.startswith(".")):
                continue
            # `TKT-` rather than `TKT`, so TKTX-1 is not read as a TKT ticket.
            if prefix and not key.startswith(f"{prefix}-"):
                continue
            fm, body = self._read_raw(key)
            out.append(self._to_ticket(key, fm, body))
        return out

    def list(self, tier=None, query=None, all_projects=False):
        q = self.config.query(tier=tier, name=query)
        return JqlSubset(q, self.me, self.priorities()).run(
            self._all_tickets(all_projects=all_projects))

    def view(self, key, *, hierarchy: bool = True):
        # `hierarchy` is ignored: parent and children are frontmatter, read
        # with the ticket.
        fm, body = self._read_raw(key)
        return self._to_ticket(key, fm, body)

    has_artifact_body = True

    def artifact_body(self, ticket):
        # The body after frontmatter, up to `## Comments`. Ticket.description
        # is only the preamble before the first `##`, so section rules can't
        # use it. Comments are cut, not left for the checker to ignore: a
        # comment is inserted verbatim, so a `## Findings` line in one would
        # end the section and be judged as the ticket's. Every tkt writer
        # keeps Comments last (`comment` appends it, `apply`/`edit --body`
        # move it to the end), so only a hand-placed section after it is lost.
        _, body = self._read_raw(ticket.key)
        return cut_managed_tail(body, "comments")

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
        # Append inside the Comments section, which need not be the last one:
        # `edit --body` restores only that section, so a stamp written past
        # it would be lost.
        bounds = ticketdoc.section_bounds(doc, "comments")
        if bounds is None:
            doc = doc.rstrip() + "\n\n## Comments\n" + stamp + "\n"
        else:
            lines = doc.splitlines()
            end = bounds[1]
            while end > bounds[0] + 1 and not lines[end - 1].strip():
                end -= 1
            lines.insert(end, stamp)
            doc = "\n".join(lines).rstrip() + "\n"
        self._write_raw(key, fm, doc)

    def blockers(self, key):
        return self.view(key).unresolved_blockers()

    def _next_key(self, project: str) -> str:
        prefix = project or self.config.project or "TKT"
        n = 0
        if self.board_dir.is_dir():
            # Literal prefix match, the same rule `_all_tickets` applies. A
            # glob would treat metacharacters in the project as a pattern:
            # with project `A[BC]`, `glob("A[BC]-*.md")` matches `AB-7.md`
            # but NOT the literal `A[BC]-1.md`, so the highest existing
            # number is missed and `create` overwrites a live ticket.
            for path in self.board_dir.glob("*.md"):
                stem = path.stem
                if not stem.startswith(f"{prefix}-"):
                    continue
                suffix = stem[len(prefix) + 1:]
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

    def apply_template(self):
        return ticketdoc.template()

    def apply(self, doc, key=None):
        fm_in, body_in = ticketdoc.split_frontmatter(doc)
        summary = ticketdoc.summary_of(body_in)
        if not summary:
            raise ProviderError("apply: document needs a '# summary' heading")

        if key is None:
            # create
            issue_type = str(fm_in.get("type", "")).strip()
            if not issue_type:
                raise ProviderError("apply --new: frontmatter must set 'type'")
            self.board_dir.mkdir(parents=True, exist_ok=True)
            new_key = self._next_key(self.config.project)
            todo_lane = (self.config.roles.get("todo")
                         or self.config.roles.get("backlog", "To Do"))
            fm = {
                "type": issue_type,
                "status": todo_lane,
                "priority": str(fm_in.get("priority", "")),
                "assignee": str(fm_in.get("assignee", "")) or self.me,
                "blocked_by": list(fm_in.get("blocked_by", []) or []),
                "blocks": list(fm_in.get("blocks", []) or []),
            }
            # Optional lists are written only when the doc sets them, so a
            # new ticket carries no empty `labels: []` / `children: []` noise.
            for name in ("labels", "children"):
                val = fm_in.get(name, []) or []
                if isinstance(val, str):
                    val = [val]
                if name == "children":  # a ticket is never its own child
                    val = [k for k in val if str(k) != new_key]
                if val:
                    fm[name] = list(val)
            # Likewise `parent`, only when set. apply is single-sided: it
            # does not add the new ticket to the parent's `children`; use
            # `tkt link --type parent` for both edges.
            parent = _coerce_parent(fm_in.get("parent", ""), new_key)
            if parent:
                fm["parent"] = parent
            for name in ("due", "scheduled", "completed"):
                if name in fm_in and fm_in[name] not in (None, ""):
                    fm[name] = fm_in[name]
            self._write_raw(new_key, fm, body_in)
            # Only now does the new file exist, so only now can another
            # name (a case variant on a case-insensitive filesystem) be seen
            # to open it. Rewritten only when such a self-reference is found.
            kids = fm.get("children", [])
            parent, kept = self._without_self(new_key, parent, kids)
            if parent != fm.get("parent", "") or kept != kids:
                if not parent:
                    fm.pop("parent", None)
                if kept:
                    fm["children"] = kept
                else:
                    fm.pop("children", None)
                self._write_raw(new_key, fm, body_in)
            self._append_history(new_key, "", todo_lane)
            return self.view(new_key)

        # update — NotFoundError here means no partial write happened.
        cur_fm, cur_body = self._read_raw(key)
        fm = dict(cur_fm)
        # `status` stays under transition's control; everything else the doc
        # carries is owned by apply.
        for field in ("type", "priority", "assignee", "labels",
                      "blocked_by", "blocks", "children", "components",
                      "due", "scheduled", "completed"):
            if field in fm_in:
                fm[field] = fm_in[field]
        # `parent` is stored as one scalar key; an empty one drops the key.
        # Single-sided, like `children` above: neither the old nor the new
        # parent's file is touched (that is `tkt link`'s job). A reference
        # to this ticket's own file, under any name, is dropped from both
        # (a self-parent clears the key, as an empty one does).
        kids = _as_list(fm_in.get("children"))
        parent, kept = self._without_self(
            key, _coerce_parent(fm_in.get("parent", ""), key), kids)
        if len(kept) != len(kids):
            fm["children"] = kept
        if "parent" in fm_in:
            if parent:
                fm["parent"] = parent
            else:
                fm.pop("parent", None)
        # The buffer body wins, but backend-managed sections (Comments) are
        # restored verbatim from the stored ticket so the editor can't corrupt
        # the timestamped log.
        body = body_in
        for name in ticketdoc.MANAGED_SECTIONS:
            canon = ticketdoc.extract_section(cur_body, name)
            body = ticketdoc.strip_section(body, name)
            if canon:
                body = body.rstrip() + "\n\n" + canon + "\n"
        self._write_raw(key, fm, body)
        return self.view(key)

    def link(self, key, to, link_type):
        lt = link_type.strip().lower()
        if lt in _CHILD_LINKS:
            return self._set_parent(parent=key, child=to)
        if lt in _PARENT_LINKS:
            return self._set_parent(parent=to, child=key)
        if lt in _BLOCKS_LINKS:
            return self._set_blocks(blocker=key, blocked=to)
        if lt in _BLOCKED_BY_LINKS:
            return self._set_blocks(blocker=to, blocked=key)
        fm, body = self._read_raw(key)
        fm["links"] = _as_list(fm.get("links")) + [f"{link_type}:{to}"]
        self._write_raw(key, fm, body)

    def _set_blocks(self, blocker: str, blocked: str) -> None:
        """Record that `blocker` blocks `blocked`, writing both edges: the
        blocker's `blocks:` and the blocked ticket's `blocked_by:`.
        `tkt blockers` reads only `blocked_by:`, so a link written from the
        blocker's side alone would leave the blocked ticket selectable.

        Keys are compared by file identity (see `_TicketIdentity`), as
        `_set_parent` does, and written in their on-disk spelling. The one
        exception is the up-front self-link check, which case-folds like
        `_set_parent`'s: on a case-sensitive filesystem `TKT-1 blocks
        tkt-1` is rejected even if both files exist."""
        # Before any read, so the error does not depend on whether the
        # filesystem folds case.
        if blocker.casefold() == blocked.casefold():
            raise UsageError(f"link: {blocker} cannot block itself")
        # Read both before writing either, so a missing ticket leaves the
        # board untouched.
        sides = {blocker: self._read_raw(blocker), blocked: self._read_raw(blocked)}
        ids = _TicketIdentity(self.board_dir)
        if ids.same(blocker, blocked):  # two names for one file (a hard link)
            raise UsageError(f"link: {blocker} cannot block itself")
        canon = {k: ids.canonical(k) for k in sides}
        # Two independent writes. A crash between them leaves a one-sided
        # link that re-running the same link repairs: an edge already
        # present (under any spelling) is skipped, and an unchanged file is
        # not rewritten.
        for key, field, other in ((blocker, "blocks", blocked),
                                  (blocked, "blocked_by", blocker)):
            fm, body = sides[key]
            cur = _as_list(fm.get(field))
            if any(ids.same(k, other) for k in cur):
                continue
            fm[field] = cur + [canon[other]]
            self._write_raw(canon[key], fm, body)

    def _set_parent(self, parent: str, child: str) -> None:
        """Make `parent` the parent of `child`, writing both edges: the
        child's `parent:` and the parent's `children:`. `child` is also
        dropped from the `children:` of every other ticket on the board --
        its previous parent, and any ticket that lists it one-sidedly (a
        hand edit, a single-sided `apply`, or a link written before the
        inverse edge existed) -- so it never ends up under two parents.

        Keys are compared by file identity (see `_TicketIdentity`), so a
        case-variant spelling on a case-insensitive filesystem is the same
        ticket, and both keys are written in their on-disk spelling.

        Only a direct 2-cycle (`parent` is already `child`'s child, by
        either edge) is rejected; longer cycles are not looked for."""
        # Before any read, so the error is the same whether or not the
        # filesystem folds case (on a case-sensitive one `tkt-1.md` would
        # otherwise surface as NotFoundError).
        if parent.casefold() == child.casefold():
            raise UsageError(f"link: {child} cannot be its own parent")
        # Read everything before writing anything, so a missing ticket or a
        # rejected cycle leaves the board untouched.
        child_fm, child_body = self._read_raw(child)
        parent_fm, parent_body = self._read_raw(parent)
        ids = _TicketIdentity(self.board_dir)
        if ids.same(parent, child):  # two names for one file (a hard link)
            raise UsageError(f"link: {child} cannot be its own parent")
        child, parent = ids.canonical(child), ids.canonical(parent)
        if (ids.same(_coerce_parent(parent_fm.get("parent", ""), parent), child)
                or any(ids.same(k, parent)
                       for k in _as_list(child_fm.get("children")))):
            raise UsageError(f"link: {child} is the parent of {parent}; making "
                             f"{parent} its parent too would be a cycle")
        stale = self._stale_parents(parent, child, ids)

        # Each file write is independent: child, then new parent, then each
        # stale parent. A crash between them leaves a partial state that
        # re-running the same link repairs: every step is idempotent, the
        # board scan finds any stale parent still listing the child, and an
        # unchanged file is not rewritten.
        if child_fm.get("parent") != parent:
            child_fm["parent"] = parent
            self._write_raw(child, child_fm, child_body)
        # The child appears once, canonically spelled, where it first was
        # (appended if absent); any other spelling of it is dropped.
        kids = _as_list(parent_fm.get("children"))
        new_kids, placed = [], False
        for k in kids:
            if not ids.same(k, child):
                new_kids.append(k)
            elif not placed:
                new_kids.append(child)
                placed = True
        if not placed:
            new_kids.append(child)
        if new_kids != kids:
            parent_fm["children"] = new_kids
            self._write_raw(parent, parent_fm, parent_body)
        for key, fm, body in stale:
            fm["children"] = [k for k in _as_list(fm.get("children"))
                              if not ids.same(k, child)]
            self._write_raw(key, fm, body)

    def _stale_parents(self, parent: str, child: str, ids: _TicketIdentity) -> list:
        """`(key, fm, body)` for every board file other than `parent` and
        `child` whose `children:` lists `child` under any spelling that
        opens the child's file.

        Scans the whole board dir, not just this project's prefix: on a
        shared board a parent may belong to another project. Files are
        matched by identity rather than by name, so a case-folded spelling
        of `parent` on a case-insensitive filesystem is still skipped (and
        keeps the edge just written to it)."""
        skip = {ids.of(parent), ids.of(child)}
        out = []
        for path in sorted(self.board_dir.glob("*.md")):
            if not path.is_file() or path.name.startswith("."):
                continue
            if ids.of(path.stem) in skip:
                continue
            fm, body = self._read_raw(path.stem)
            if any(ids.same(k, child) for k in _as_list(fm.get("children"))):
                out.append((path.stem, fm, body))
        return out

    @staticmethod
    def _split_body(doc: str) -> tuple[str, str]:
        """Split a ticket body into (summary, body).

        summary = the first `# ` heading; body = everything after it (prose,
        Acceptance, Comments, ...), so a summary-only edit keeps it verbatim."""
        lines = doc.splitlines()
        for i, line in enumerate(lines):
            s = line.strip()
            if s.startswith("# "):
                return s[2:].strip(), "\n".join(lines[i + 1:]).strip()
        return "", doc.strip()

    def edit(self, key, summary=None, body=None, priority=None, assignee=None,
             add_labels=None, remove_labels=None,
             due=None, scheduled=None, completed=None, agent_status=None):
        fm, doc = self._read_raw(key)

        if priority is not None:
            fm["priority"] = priority
        if assignee is not None:
            fm["assignee"] = assignee

        # Agent state: None = leave as-is, "" = clear (drop the key), else set.
        # The stamp is derived, never passed in: it marks when the state last
        # actually changed, so re-asserting the same state does not reset it.
        if agent_status is not None:
            if agent_status == "":
                fm.pop("agent_status", None)
                fm.pop("agent_status_at", None)
            else:
                if (fm.get("agent_status") != agent_status
                        or "agent_status_at" not in fm):
                    fm["agent_status_at"] = _iso(_now())
                fm["agent_status"] = agent_status

        # Dates: None = leave as-is, "" = clear (drop the key), else set.
        for name, val in (("due", due), ("scheduled", scheduled),
                          ("completed", completed)):
            if val is None:
                continue
            if val == "":
                fm.pop(name, None)
            else:
                fm[name] = val

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
            cur_summary, cur_body = self._split_body(doc)
            new_summary = cur_summary if summary is None else summary
            new_body = cur_body
            if body is not None:
                # `--body` replaces the whole body, as `create` writes it.
                # Backend-managed sections (Comments) are restored from the
                # stored ticket, as `apply` does, so the log survives.
                new_body = body
                for name in ticketdoc.MANAGED_SECTIONS:
                    canon = ticketdoc.extract_section(cur_body, name)
                    new_body = ticketdoc.strip_section(new_body, name)
                    if canon:
                        new_body = (f"{new_body.rstrip()}\n\n{canon}"
                                    if new_body.strip() else canon)
            doc = f"# {new_summary}\n"
            if new_body:
                doc += f"\n{new_body}\n"

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

    def lane_time(self, key, role, read_only=False):
        lane = self.config.role_to_lane(role)
        # read_only computes for display regardless of time tracking; the
        # recording path stays a no-op when no provider is configured.
        if not read_only and self.config.timetracking.get("provider", "none") == "none":
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
        if read_only:
            return Worklog(key=key, role=role, lane=lane, seconds=secs,
                           human=human_duration(secs))
        wl_id = self._append_worklog(key, role, lane, secs, "retroactive")
        return Worklog(key=key, role=role, lane=lane, seconds=secs,
                       human=human_duration(secs), worklog_id=wl_id, note="retroactive")

    def lane_time_batch(self, keys_roles, read_only=False):
        out = []
        for key, role in keys_roles:
            lane = self.config.role_to_lane(role)
            if not read_only and self.config.timetracking.get("provider", "none") == "none":
                out.append(Worklog(key=key, role=role, lane=lane))
                continue
            history = sorted(self._read_history(key), key=lambda h: h["ts"])
            last_in = None
            for h in history:
                if h["to"] == lane:
                    last_in = _parse_iso(h["ts"])
            if last_in is None:
                out.append(Worklog(key=key, role=role, lane=lane))
                continue
            exit_dt = _now()
            for h in history:
                ts = _parse_iso(h["ts"])
                if h["from"] == lane and ts > last_in:
                    exit_dt = ts
                    break
            secs = max(int((exit_dt - last_in).total_seconds()), 60)
            if read_only:
                out.append(Worklog(key=key, role=role, lane=lane, seconds=secs,
                                   human=human_duration(secs)))
            else:
                wl_id = self._append_worklog(key, role, lane, secs, "retroactive")
                out.append(Worklog(key=key, role=role, lane=lane, seconds=secs,
                                   human=human_duration(secs), worklog_id=wl_id,
                                   note="retroactive"))
        return out

    def doctor(self):
        checks = [
            Check("board_dir exists", self.board_dir.is_dir(), str(self.board_dir)),
            Check("roles configured", bool(self.config.roles),
                  f"{len(self.config.roles)} roles"),
            Check("done role mapped", "done" in self.config.roles,
                  self.config.roles.get("done", "MISSING")),
            Check("me set", bool(self.me), self.me or "set [markdown].me for currentUser()"),
        ]
        # A `project` that matches no key on the board makes every query come
        # back empty, which reads as an idle board rather than a
        # misconfiguration. Name it here: `doctor` is the validation surface.
        prefix = self.config.project
        if prefix and self.board_dir.is_dir():
            files = [p.stem for p in self.board_dir.glob("*.md")]
            matched = [k for k in files if k.startswith(f"{prefix}-")]
            checks.append(Check(
                "keys match project prefix",
                bool(matched) or not files,
                f"{len(matched)} of {len(files)} *.md start with '{prefix}-'"
                + ("" if matched or not files else
                   f" — every ticket is invisible to `list`; found e.g. {files[0]}")))
        return checks
