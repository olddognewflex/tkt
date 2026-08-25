"""Read-only view of every agent run's state.

`tkt run` writes one state dir per ticket under the run root; this module reads
them all back at once so a dashboard or TUI can poll a single verb instead of
one call per ticket.

Deliberately filesystem-only: no adapter is constructed and no backend call is
made unless the caller asks for `--enrich`, so polling this at 1Hz costs
nothing and works on a project whose backend is unreachable.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .errors import TktError, UsageError
from .run import (PHASE_NAMES, check_stop_file, read_heartbeat,
                  read_ticket_marker, run_root)

# Three missed beats at the default interval. Long enough that a loaded machine
# does not flap a live run into "stalled".
DEFAULT_STALE_AFTER = 45

# Reported run states, most-active first: rows sort by this index so the
# interesting ones land at the top of a board.
STATES = ("running", "stalled", "dead", "blocked", "halted", "idle")

_ISO = "%Y-%m-%dT%H:%M:%SZ"


# ---- Local state readers -----------------------------------------------------

def read_marker(state_dir: Path) -> dict[str, Any] | None:
    """Read the locally mirrored run marker.

    Deliberately does not fall back to the ticket comment the way
    `run.read_ticket_marker` does — that costs a backend round-trip, and this
    module's contract is that it never makes one.
    """
    path = state_dir / "marker.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


# ---- Time + liveness helpers -------------------------------------------------

def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), _ISO).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _age(value: Any, now: datetime) -> int | None:
    """Seconds since an ISO stamp, or None if it is missing/unparseable."""
    dt = _parse_iso(value)
    return None if dt is None else max(0, int((now - dt).total_seconds()))


def _pid_alive(pid: Any, host: Any) -> bool | None:
    """True/False when we can tell, None when we cannot.

    None covers a run on another machine (its pid means nothing here) and a
    heartbeat too old to carry one.
    """
    if host and str(host) != socket.gethostname():
        return None
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # alive, just owned by another user
    except OSError:
        return None
    return True


# ---- State resolution --------------------------------------------------------

def resolve_state(marker: dict[str, Any] | None,
                  heartbeat: dict[str, Any] | None,
                  now: datetime,
                  stale_after: int = DEFAULT_STALE_AFTER) -> str:
    """Classify a run from its beat file and its last marker."""
    if heartbeat:
        age = _age(heartbeat.get("beat"), now)
        if age is not None and age <= stale_after:
            return "running"
        if _pid_alive(heartbeat.get("pid"), heartbeat.get("host")) is False:
            return "dead"
        # Beat is stale but the process is alive, or it lives on a host we
        # cannot check. Say "stalled" rather than guess either way.
        return "stalled"

    if not marker:
        return "idle"

    outcome = str(marker.get("outcome", ""))
    if outcome == "blocked":
        return "blocked"
    if outcome in ("halted", "gate"):
        return "halted"
    # advance/retry are mid-loop markers — written, then the loop continues.
    # Finding one with no heartbeat means the driver went away without
    # halting. (Runs started before heartbeats existed also land here, which
    # is the honest answer: nothing is driving them now.)
    return "dead"


def read_run(state_dir: Path, now: datetime,
             stale_after: int = DEFAULT_STALE_AFTER) -> dict[str, Any]:
    """Merge one run's marker, heartbeat and STOP flag into a single row."""
    return build_row(state_dir.name, read_marker(state_dir),
                     read_heartbeat(state_dir), state_dir, now, stale_after)


def build_row(key: str, marker: dict[str, Any] | None,
              heartbeat: dict[str, Any] | None, state_dir: Path,
              now: datetime,
              stale_after: int = DEFAULT_STALE_AFTER) -> dict[str, Any]:
    """Shape one run's state into the row a caller renders or serializes."""
    m = marker or {}
    h = heartbeat or {}

    # The heartbeat is fresher than the marker while a phase is in flight, so
    # it wins for the live fields; the marker is the only source once the
    # driver is gone.
    phase = str(h.get("phase") or m.get("phase") or "")

    return {
        "key": key,
        "state": resolve_state(marker, heartbeat, now, stale_after),
        "phase": phase,
        "phase_name": PHASE_NAMES.get(phase, ""),
        "attempt": h.get("attempt", m.get("attempt")),
        "iteration": h.get("iteration"),
        "outcome": m.get("outcome", ""),
        "reason": m.get("reason", ""),
        "next": m.get("next", ""),
        "pid": h.get("pid"),
        "host": h.get("host", ""),
        "beat": h.get("beat", ""),
        "beat_age": _age(h.get("beat"), now),
        "run_started": h.get("run_started", m.get("run_started", "")),
        "run_age": _age(h.get("run_started", m.get("run_started")), now),
        "phase_started": h.get("phase_started", m.get("phase_started", "")),
        "phase_age": _age(h.get("phase_started", m.get("phase_started")), now),
        "updated": m.get("updated", ""),
        "stop_requested": state_dir.is_dir() and check_stop_file(state_dir),
        "dir": str(state_dir),
    }


# ---- Collection --------------------------------------------------------------

def roots_for(config: Config, dirs: list[str] | None) -> list[Path]:
    """Run roots to scan: the loaded config's, or one per --dir project root."""
    if not dirs:
        return [run_root(config)]
    out: list[Path] = []
    for d in dirs:
        path = Path(d).expanduser()
        cand = path / ".sdlc" / "config.toml"
        if not cand.is_file():
            raise UsageError(f"agents --dir: no .sdlc/config.toml under {path}")
        root = run_root(Config.load(str(cand)))
        if root not in out:
            out.append(root)
    return out


def collect(roots: list[Path], stale_after: int = DEFAULT_STALE_AFTER,
            include_all: bool = False,
            now: datetime | None = None) -> list[dict[str, Any]]:
    """One row per run dir across every root, most-active first."""
    now = now or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue          # nothing has ever run here; not an error
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            # `_select` is the placeholder dir a keyless run uses for P0, not
            # a ticket. Hidden unless the caller asks for everything.
            if d.name.startswith("_") and not include_all:
                continue
            row = read_run(d, now, stale_after)
            if row["state"] == "idle" and not include_all:
                continue
            if row["key"] in seen:
                continue      # same ticket via two overlapping roots
            seen.add(row["key"])
            rows.append(row)
    rows.sort(key=lambda r: (STATES.index(r["state"]), r["key"]))
    return rows


def enrich(config: Config, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add board fields to each row. The only path here that hits a backend."""
    from .registry import get_adapter
    adapter = get_adapter(config)
    for row in rows:
        try:
            t = adapter.view(row["key"])
        except TktError:
            continue          # a run dir can outlive its ticket
        row.update(
            summary=t.summary,
            status=t.status,
            status_role=t.status_role,
            agent_status=t.agent_status,
            agent_status_at=t.agent_status_at,
            assignee=t.assignee,
            url=t.url,
        )
    return rows


# ---- Rendering ---------------------------------------------------------------

def human_age(secs: int | None) -> str:
    if secs is None:
        return "-"
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"
    return f"{secs // 86400}d"


def _print_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        # stdout stays empty so `tkt agents | wc -l` is honest.
        print("no agent runs found", file=sys.stderr)
        return
    cols = [
        ("KEY", lambda r: r["key"]),
        ("STATE", lambda r: r["state"] + ("*" if r["stop_requested"] else "")),
        ("PHASE", lambda r: f"{r['phase']} {r['phase_name']}".strip()),
        ("TRY", lambda r: str(r["attempt"] or "-")),
        ("FOR", lambda r: human_age(r["phase_age"])),
        ("DETAIL", lambda r: r.get("summary") or r["reason"] or ""),
    ]
    table = [[name for name, _ in cols]]
    table += [[str(fn(r)) for _, fn in cols] for r in rows]
    widths = [max(len(row[i]) for row in table) for i in range(len(cols))]
    for row in table:
        print("  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip())


# ---- CLI entry points --------------------------------------------------------

def cmd_agents(config: Config, dirs: list[str] | None, stale_after: int,
               include_all: bool, do_enrich: bool, as_json: bool) -> int:
    """Entry point for `tkt agents`."""
    rows = collect(roots_for(config, dirs), stale_after, include_all)
    if do_enrich:
        enrich(config, rows)
    if as_json:
        # An empty board is exit 0 with an empty list, never an error: it is a
        # TUI's steady state.
        print(json.dumps({
            "generated": datetime.now(timezone.utc).strftime(_ISO),
            "stale_after": stale_after,
            "agents": rows,
        }, indent=2))
    else:
        _print_rows(rows)
    return 0


def status_for_key(config: Config, key: str,
                   stale_after: int = DEFAULT_STALE_AFTER,
                   allow_backend: bool = True) -> dict[str, Any]:
    """One run's row, for `tkt run --status KEY`.

    Local state answers first — it is free and carries the heartbeat. Only when
    there is nothing local does this fall back to the ticket marker comment,
    which is the cross-machine record and the whole point of writing it: a run
    resumed in a fresh worktree can still be asked where it got to. `tkt agents`
    never takes that fallback, so polling it stays backend-free.
    """
    now = datetime.now(timezone.utc)
    state_dir = run_root(config) / key
    if state_dir.is_dir():
        row = build_row(key, read_marker(state_dir), read_heartbeat(state_dir),
                        state_dir, now, stale_after)
        if row["state"] != "idle":
            return row

    if allow_backend:
        try:
            from .registry import get_adapter
            marker = read_ticket_marker(get_adapter(config), key, None)
        except TktError:
            marker = None
        if marker:
            return build_row(key, marker, None, state_dir, now, stale_after)

    return {"key": key, "state": "idle", "phase": None,
            "status": "no run state found"}
