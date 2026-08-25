"""Unit tests for core/agents.py — the read-only view of every run's state.

Fully offline: builds run state dirs by hand and asserts on what `tkt agents`
would report. Run from repo root:

    python3 -m unittest tests.test_agents
"""
import json
import os
import socket
import sys
import tempfile
import textwrap
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agents import (
    DEFAULT_STALE_AFTER, build_row, collect, human_age, read_marker, read_run,
    resolve_state, roots_for, status_for_key,
)
from core.config import Config
from core.errors import UsageError
from core.run import Heartbeat, read_heartbeat, run_root

NOW = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)
HERE = socket.gethostname()


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_config(tmp: Path, extra_run: str = "") -> Path:
    """Minimal markdown config; extra_run is appended to the [run] table."""
    sdlc = tmp / ".sdlc"
    sdlc.mkdir(parents=True, exist_ok=True)
    board = sdlc / "board"
    board.mkdir(exist_ok=True)
    path = sdlc / "config.toml"
    path.write_text(textwrap.dedent(f"""\
        [ticketing]
        provider = "markdown"
        project  = "TKT"

        [markdown]
        board_dir = "{board}"
        state_dir = "{sdlc / 'state'}"
        me        = "testbot"

        [board.roles]
        todo        = "To Do"
        in_progress = "In Progress"
        done        = "Done"
        blocked     = "Blocked"

        [issue_types]
        full_sdlc   = ["Story"]
        deliverable = ["Task"]

        [run]
        """) + extra_run + "\n")
    return path


def _mkrun(root: Path, key: str, marker=None, heartbeat=None, stop=False) -> Path:
    d = root / key
    d.mkdir(parents=True, exist_ok=True)
    if marker is not None:
        (d / "marker.json").write_text(json.dumps(marker))
    if heartbeat is not None:
        (d / "heartbeat.json").write_text(json.dumps(heartbeat))
    if stop:
        (d / "STOP").touch()
    return d


class TestResolveState(unittest.TestCase):
    """The classification table a TUI colours its rows from."""

    def _hb(self, beat_delta_s: int, pid: int, host: str = HERE) -> dict:
        return {"beat": _iso(NOW - timedelta(seconds=beat_delta_s)),
                "pid": pid, "host": host}

    def test_fresh_beat_is_running(self):
        self.assertEqual(
            resolve_state(None, self._hb(1, os.getpid()), NOW), "running")

    def test_beat_exactly_at_threshold_is_running(self):
        self.assertEqual(
            resolve_state(None, self._hb(DEFAULT_STALE_AFTER, os.getpid()), NOW),
            "running")

    def test_stale_beat_live_process_is_stalled(self):
        # Do not call this dead: the process is demonstrably still there.
        self.assertEqual(
            resolve_state(None, self._hb(600, os.getpid()), NOW), "stalled")

    def test_stale_beat_gone_process_is_dead(self):
        self.assertEqual(
            resolve_state(None, self._hb(600, 999_999), NOW), "dead")

    def test_stale_beat_other_host_is_stalled(self):
        # Another machine's pid means nothing here, so refuse to guess "dead".
        self.assertEqual(
            resolve_state(None, self._hb(600, 1, host="elsewhere"), NOW),
            "stalled")

    def test_marker_outcomes(self):
        for outcome, want in (("blocked", "blocked"), ("halted", "halted"),
                              ("gate", "halted"), ("advance", "dead"),
                              ("retry", "dead")):
            with self.subTest(outcome=outcome):
                self.assertEqual(
                    resolve_state({"outcome": outcome}, None, NOW), want)

    def test_nothing_is_idle(self):
        self.assertEqual(resolve_state(None, None, NOW), "idle")

    def test_heartbeat_wins_over_marker(self):
        # A live driver that has already written an advance marker is running,
        # not dead.
        self.assertEqual(
            resolve_state({"outcome": "advance"}, self._hb(1, os.getpid()), NOW),
            "running")


class TestRowShape(unittest.TestCase):
    def test_heartbeat_fields_win_over_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "run"
            d = _mkrun(
                root, "TKT-1",
                marker={"phase": "P2", "attempt": 1, "outcome": "advance",
                        "next": "P3", "updated": _iso(NOW - timedelta(minutes=2)),
                        "run_started": _iso(NOW - timedelta(minutes=10)),
                        "phase_started": _iso(NOW - timedelta(minutes=2))},
                heartbeat={"phase": "P3", "attempt": 2, "iteration": 5,
                           "pid": os.getpid(), "host": HERE,
                           "beat": _iso(NOW),
                           "run_started": _iso(NOW - timedelta(minutes=10)),
                           "phase_started": _iso(NOW - timedelta(seconds=30))})
            row = read_run(d, NOW)
            self.assertEqual(row["state"], "running")
            self.assertEqual(row["phase"], "P3")          # heartbeat is fresher
            self.assertEqual(row["phase_name"], "implement/test")
            self.assertEqual(row["attempt"], 2)
            self.assertEqual(row["iteration"], 5)
            self.assertEqual(row["outcome"], "advance")   # marker-only field
            self.assertEqual(row["phase_age"], 30)
            self.assertEqual(row["run_age"], 600)
            self.assertFalse(row["stop_requested"])

    def test_marker_only_row_still_carries_ages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "run"
            d = _mkrun(root, "TKT-2", marker={
                "phase": "P1", "attempt": 1, "outcome": "halted",
                "reason": "stopped by operator",
                "run_started": _iso(NOW - timedelta(hours=1)),
                "phase_started": _iso(NOW - timedelta(minutes=5)),
                "updated": _iso(NOW - timedelta(minutes=5))}, stop=True)
            row = read_run(d, NOW)
            self.assertEqual(row["state"], "halted")
            self.assertEqual(row["phase_age"], 300)
            self.assertEqual(row["run_age"], 3600)
            self.assertTrue(row["stop_requested"])
            self.assertIsNone(row["beat_age"])

    def test_pre_heartbeat_marker_has_no_ages(self):
        # Markers written before timestamps existed must not crash a reader.
        with tempfile.TemporaryDirectory() as tmp:
            d = _mkrun(Path(tmp) / "run", "TKT-3",
                       marker={"phase": "P5", "attempt": 1, "outcome": "halted"})
            row = read_run(d, NOW)
            self.assertIsNone(row["phase_age"])
            self.assertIsNone(row["run_age"])
            self.assertEqual(row["state"], "halted")

    def test_unparseable_marker_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = _mkrun(Path(tmp) / "run", "TKT-4")
            (d / "marker.json").write_text("{not json")
            self.assertIsNone(read_marker(d))
            self.assertEqual(read_run(d, NOW)["state"], "idle")

    def test_build_row_without_a_state_dir(self):
        # status_for_key's comment fallback builds a row for a dir that does
        # not exist; that must not raise.
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "run" / "TKT-9"
            row = build_row("TKT-9", {"phase": "P5", "outcome": "advance"},
                            None, missing, NOW)
            self.assertEqual(row["phase"], "P5")
            self.assertFalse(row["stop_requested"])


class TestCollect(unittest.TestCase):
    def test_filters_and_ordering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "run"
            _mkrun(root, "TKT-halt", marker={"outcome": "halted", "phase": "P1"})
            _mkrun(root, "TKT-live", heartbeat={
                "phase": "P3", "pid": os.getpid(), "host": HERE,
                "beat": _iso(NOW)})
            _mkrun(root, "TKT-idle")                       # no state at all
            _mkrun(root, "_select", marker={"outcome": "halted", "phase": "P0"})
            (root / "stray.txt").write_text("not a run dir")

            rows = collect([root], now=NOW)
            self.assertEqual([r["key"] for r in rows], ["TKT-live", "TKT-halt"])

            allrows = collect([root], include_all=True, now=NOW)
            self.assertEqual(
                sorted(r["key"] for r in allrows),
                ["TKT-halt", "TKT-idle", "TKT-live", "_select"])

    def test_missing_root_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(collect([Path(tmp) / "never-ran"], now=NOW), [])

    def test_same_key_across_roots_appears_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a" / "run"
            b = Path(tmp) / "b" / "run"
            _mkrun(a, "TKT-1", marker={"outcome": "halted", "phase": "P1"})
            _mkrun(b, "TKT-1", marker={"outcome": "blocked", "phase": "P2"})
            rows = collect([a, b], now=NOW)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["state"], "halted")   # first root wins


class TestRunRoot(unittest.TestCase):
    def test_default_is_beside_the_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            config = Config.load(str(_write_config(tmp)))
            self.assertEqual(run_root(config), tmp / ".sdlc" / "state" / "run")

    def test_absolute_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            shared = Path(tmp) / "shared"
            config = Config.load(str(_write_config(
                tmp, f'state_dir = "{shared}"')))
            self.assertEqual(run_root(config), shared / "run")

    def test_relative_override_resolves_against_the_project_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            config = Config.load(str(_write_config(
                tmp, 'state_dir = "elsewhere/state"')))
            self.assertEqual(run_root(config),
                             tmp / "elsewhere" / "state" / "run")

    def test_roots_for_rejects_a_dir_with_no_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            config = Config.load(str(_write_config(tmp)))
            with self.assertRaises(UsageError):
                roots_for(config, [str(tmp / "nope")])

    def test_roots_for_reads_each_dirs_own_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            other = tmp / "other"
            other.mkdir()
            shared = tmp / "shared-run-state"
            _write_config(other, f'state_dir = "{shared}"')
            config = Config.load(str(_write_config(tmp)))
            self.assertEqual(roots_for(config, [str(other)]), [shared / "run"])


class TestHeartbeatLifecycle(unittest.TestCase):
    def test_write_update_and_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            hb = Heartbeat(d, "TKT-1", interval=0, run_started=_iso(NOW))
            hb.write()
            data = read_heartbeat(d)
            self.assertEqual(data["key"], "TKT-1")
            self.assertEqual(data["pid"], os.getpid())
            self.assertIn("beat", data)

            hb.update(phase="P4", attempt=3)
            self.assertEqual(read_heartbeat(d)["phase"], "P4")

            # Absence of the file is what marks a run as no longer live.
            hb.stop()
            self.assertIsNone(read_heartbeat(d))

    def test_interval_zero_starts_no_thread(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            hb = Heartbeat(d, "TKT-1", interval=0, run_started=_iso(NOW))
            hb.start()
            self.assertIsNone(read_heartbeat(d))    # opted out entirely
            hb.stop()

    def test_thread_beats_and_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            hb = Heartbeat(d, "TKT-1", interval=1, run_started=_iso(NOW))
            hb.start()
            first = read_heartbeat(d)["beat"]
            time.sleep(2.2)
            self.assertNotEqual(read_heartbeat(d)["beat"], first)
            hb.stop()
            self.assertIsNone(read_heartbeat(d))

    def test_rebind_moves_the_beat_file(self):
        # A keyless run starts in _select and moves once P0 picks a ticket.
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "_select"
            second = Path(tmp) / "TKT-7"
            first.mkdir()
            second.mkdir()
            hb = Heartbeat(first, "_select", interval=0, run_started=_iso(NOW))
            hb.write()
            hb.rebind(second, "TKT-7")
            self.assertIsNone(read_heartbeat(first))
            self.assertEqual(read_heartbeat(second)["key"], "TKT-7")


class TestStatusForKey(unittest.TestCase):
    def test_local_state_answers_without_the_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            config = Config.load(str(_write_config(tmp)))
            _mkrun(run_root(config), "TKT-1",
                   marker={"phase": "P6", "attempt": 1, "outcome": "retry"})
            row = status_for_key(config, "TKT-1", allow_backend=False)
            self.assertEqual(row["phase"], "P6")

    def test_no_state_reports_idle(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            config = Config.load(str(_write_config(tmp)))
            row = status_for_key(config, "TKT-1", allow_backend=False)
            self.assertEqual(row["state"], "idle")
            self.assertIsNone(row["phase"])


class TestHumanAge(unittest.TestCase):
    def test_formats(self):
        for secs, want in ((None, "-"), (0, "0s"), (59, "59s"), (60, "1m"),
                           (3599, "59m"), (3600, "1h00m"), (7860, "2h11m"),
                           (86400, "1d")):
            with self.subTest(secs=secs):
                self.assertEqual(human_age(secs), want)


if __name__ == "__main__":
    unittest.main()
