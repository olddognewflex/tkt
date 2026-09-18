"""Edge cases for the run-state readers and the verbs built on them.

Covers what the first pass of tests/test_agents.py left open: the heartbeat
writer under two threads, unreadable state files, the read-only CLI paths when
the backend cannot be built, `agents --dir` from a directory with no config, and
the `agent_status_at` stamp round-tripping through markdown frontmatter.

Fully offline. Run from repo root:

    python3 -m unittest tests.test_agents_edges
"""
import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import textwrap
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import agents  # noqa: E402
from core.agents import DEFAULT_STALE_AFTER, read_marker  # noqa: E402
from core.cli import main  # noqa: E402
from core.config import Config  # noqa: E402
from core.registry import get_adapter  # noqa: E402
from core.run import Heartbeat, heartbeat_path, read_heartbeat  # noqa: E402

NOW_ISO = "2026-08-25T12:00:00Z"


def _write_config(tmp: Path, provider_block: str | None = None, extra_run: str = "") -> Path:
    sdlc = tmp / ".sdlc"
    (sdlc / "board").mkdir(parents=True, exist_ok=True)
    if provider_block is None:
        provider_block = textwrap.dedent(f"""\
            [ticketing]
            provider = "markdown"
            project  = "TKT"

            [markdown]
            board_dir = "{sdlc / 'board'}"
            state_dir = "{sdlc / 'state'}"
            me        = "testbot"
            """)
    path = sdlc / "config.toml"
    path.write_text(provider_block + textwrap.dedent("""\

        [board.roles]
        todo        = "To Do"
        in_progress = "In Progress"
        done        = "Done"

        [issue_types]
        full_sdlc   = ["Story"]
        deliverable = ["Task"]

        [run]
        """) + extra_run + "\n")
    return path


@contextlib.contextmanager
def _cwd(path: Path):
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _run_cli(argv: list[str], cwd: Path) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    env_cfg = os.environ.pop("TKT_CONFIG", None)
    try:
        with _cwd(cwd), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
    finally:
        if env_cfg is not None:
            os.environ["TKT_CONFIG"] = env_cfg
    return code, out.getvalue(), err.getvalue()


class TestHeartbeatConcurrency(unittest.TestCase):
    def test_reader_never_sees_a_torn_beat(self):
        """The beat thread and update() both write; a 1Hz reader must never
        catch the file empty or half-written."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            hb = Heartbeat(d, "TKT-1", interval=0.001, run_started=NOW_ISO)
            hb.start()
            torn = []
            done = threading.Event()

            def reader():
                while not done.is_set():
                    if heartbeat_path(d).is_file() and read_heartbeat(d) is None:
                        # is_file() then a failed parse: only a torn write or a
                        # file vanishing between the two calls. Re-check.
                        if heartbeat_path(d).is_file():
                            torn.append(1)

            t = threading.Thread(target=reader)
            t.start()
            for i in range(400):
                hb.update(iteration=i)
            done.set()
            t.join()
            hb.stop()
            self.assertEqual(torn, [])
            self.assertEqual(list(d.glob(".heartbeat.*.tmp")), [])

    def test_late_beat_cannot_resurrect_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            hb = Heartbeat(d, "TKT-1", interval=0, run_started=NOW_ISO)
            hb.write()
            hb.stop()
            hb.write()          # a beat thread that lost the race with stop()
            self.assertFalse(heartbeat_path(d).exists())

    def test_rebind_under_a_running_thread_leaves_nothing_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / "_select", Path(tmp) / "TKT-7"
            first.mkdir()
            second.mkdir()
            hb = Heartbeat(first, "_select", interval=0.001, run_started=NOW_ISO)
            hb.start()
            for _ in range(50):
                hb.update(iteration=1)
            hb.rebind(second, "TKT-7")
            for _ in range(50):
                hb.update(iteration=2)
            hb.stop()
            self.assertFalse(heartbeat_path(first).exists())
            self.assertFalse(heartbeat_path(second).exists())


class TestUnreadableState(unittest.TestCase):
    def test_non_utf8_heartbeat_is_none_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            heartbeat_path(d).write_bytes(b"\xff\xfe\x00 not utf-8")
            self.assertIsNone(read_heartbeat(d))

    def test_non_utf8_marker_is_none_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "marker.json").write_bytes(b"\xff\xfe\x00 not utf-8")
            self.assertIsNone(read_marker(d))


class TestDefaultStaleAfter(unittest.TestCase):
    def test_no_config_uses_the_constant(self):
        self.assertEqual(agents.default_stale_after(None), DEFAULT_STALE_AFTER)

    def test_fast_beat_keeps_the_floor(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config.load(str(_write_config(Path(tmp), extra_run="heartbeat_interval = 5")))
            self.assertEqual(agents.default_stale_after(cfg), DEFAULT_STALE_AFTER)

    def test_slow_beat_widens_to_three_missed_beats(self):
        """heartbeat_interval = 60 with a fixed 45s window showed every healthy
        run as stalled."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config.load(str(_write_config(Path(tmp), extra_run="heartbeat_interval = 60")))
            self.assertEqual(agents.default_stale_after(cfg), 180)


class TestReadOnlyVerbsNeedNoAdapter(unittest.TestCase):
    # [github] with no repo: get_adapter raises ConfigError. Status and stop are
    # local reads/writes of run state and must not care.
    BROKEN = textwrap.dedent("""\
        [ticketing]
        provider = "github"
        project  = "TKT"

        [github]
        board = "labels"
        """)

    def test_status_works_when_the_adapter_cannot_be_built(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_config(Path(tmp), provider_block=self.BROKEN)
            code, out, err = _run_cli(["run", "--status", "TKT-1"], Path(tmp))
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out)["state"], "idle")

    def test_stop_works_when_the_adapter_cannot_be_built(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_config(Path(tmp), provider_block=self.BROKEN)
            code, out, err = _run_cli(["run", "--stop", "TKT-1"], Path(tmp))
            self.assertEqual(code, 0, err)
            self.assertTrue((Path(tmp) / ".sdlc" / "state" / "run" / "TKT-1" / "STOP").is_file())

    def test_status_still_requires_a_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_config(Path(tmp))
            code, _, err = _run_cli(["run", "--status"], Path(tmp))
            self.assertEqual(code, 64)
            self.assertIn("requires a ticket KEY", err)


class TestAgentsDirEscapeHatch(unittest.TestCase):
    def test_dir_works_from_a_directory_with_no_config(self):
        """A TUI is launched from anywhere; --dir names the projects to scan."""
        with tempfile.TemporaryDirectory() as proj, tempfile.TemporaryDirectory() as empty:
            _write_config(Path(proj))
            run = Path(proj) / ".sdlc" / "state" / "run" / "TKT-9"
            run.mkdir(parents=True)
            (run / "marker.json").write_text(json.dumps(
                {"phase": "P3", "outcome": "blocked", "attempt": 1}))
            code, out, err = _run_cli(["agents", "--dir", proj, "--json"], Path(empty))
            self.assertEqual(code, 0, err)
            rows = json.loads(out)["agents"]
            self.assertEqual([(r["key"], r["state"]) for r in rows], [("TKT-9", "blocked")])

    def test_dir_uses_the_scanned_projects_own_beat_rate(self):
        """A project beating every 60s, scanned via --dir, must not read its
        healthy runs as stalled under the 45s floor."""
        with tempfile.TemporaryDirectory() as proj, tempfile.TemporaryDirectory() as empty:
            _write_config(Path(proj), extra_run="heartbeat_interval = 60")
            run = Path(proj) / ".sdlc" / "state" / "run" / "TKT-9"
            run.mkdir(parents=True)
            beat = (datetime.now(timezone.utc) - timedelta(seconds=100)).strftime("%Y-%m-%dT%H:%M:%SZ")
            heartbeat_path(run).write_text(json.dumps(
                {"key": "TKT-9", "pid": os.getpid(), "host": socket.gethostname(), "beat": beat}))
            code, out, err = _run_cli(["agents", "--dir", proj, "--json"], Path(empty))
            self.assertEqual(code, 0, err)
            payload = json.loads(out)
            self.assertEqual(payload["agents"][0]["state"], "running")
            self.assertEqual(payload["stale_after"], 180)
            self.assertEqual(payload["agents"][0]["stale_after"], 180)
            # An explicit window still wins.
            code, out, _ = _run_cli(["agents", "--dir", proj, "--json", "--stale-after", "45"],
                                    Path(empty))
            self.assertEqual(json.loads(out)["agents"][0]["state"], "stalled")

    def test_invalid_found_config_is_not_swallowed(self):
        with tempfile.TemporaryDirectory() as proj, tempfile.TemporaryDirectory() as bad:
            _write_config(Path(proj))
            (Path(bad) / ".sdlc").mkdir()
            (Path(bad) / ".sdlc" / "config.toml").write_text("this is = not [valid toml")
            code, _, _ = _run_cli(["agents", "--dir", proj, "--json"], Path(bad))
            self.assertEqual(code, 2)

    def test_no_dir_and_no_config_is_still_a_config_error(self):
        with tempfile.TemporaryDirectory() as empty:
            code, _, _ = _run_cli(["agents", "--json"], Path(empty))
            self.assertEqual(code, 2)

    def test_enrich_without_a_config_is_a_config_error(self):
        with tempfile.TemporaryDirectory() as proj, tempfile.TemporaryDirectory() as empty:
            _write_config(Path(proj))
            code, _, _ = _run_cli(["agents", "--dir", proj, "--enrich"], Path(empty))
            self.assertEqual(code, 2)


class TestAgentStatusAtRoundTrip(unittest.TestCase):
    def _adapter(self, tmp):
        return get_adapter(Config.load(str(_write_config(Path(tmp)))))

    def test_stamp_round_trips_through_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self._adapter(tmp)
            key = a.create("Story", "stamp me").key
            self.assertIsNone(a.view(key).agent_status_at)   # absent = None, like the dates

            a.edit(key, agent_status="processing")
            stamp = a.view(key).agent_status_at
            parsed = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            self.assertLess(abs((datetime.now(timezone.utc) - parsed).total_seconds()), 60)

            # A fresh adapter reads the same value back off disk, and it is in
            # the JSON shape skills parse.
            again = self._adapter(tmp).view(key)
            self.assertEqual(again.agent_status_at, stamp)
            self.assertEqual(again.to_dict()["agent_status_at"], stamp)
            raw = (Path(tmp) / ".sdlc" / "board" / f"{key}.md").read_text()
            self.assertIn(f"agent_status_at: {stamp}", raw)

    def test_reasserting_the_same_state_keeps_the_stamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self._adapter(tmp)
            key = a.create("Story", "stamp me").key
            a.edit(key, agent_status="processing")
            fm_path = Path(tmp) / ".sdlc" / "board" / f"{key}.md"
            # Age the stamp by hand so "unchanged" is provable within one second.
            fm_path.write_text(fm_path.read_text().replace(
                f"agent_status_at: {a.view(key).agent_status_at}",
                "agent_status_at: 2026-01-01T00:00:00Z"))
            a.edit(key, agent_status="processing")
            self.assertEqual(a.view(key).agent_status_at, "2026-01-01T00:00:00Z")
            a.edit(key, agent_status="waiting")
            self.assertNotEqual(a.view(key).agent_status_at, "2026-01-01T00:00:00Z")

    def test_clearing_the_state_clears_the_stamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self._adapter(tmp)
            key = a.create("Story", "stamp me").key
            a.edit(key, agent_status="processing")
            a.edit(key, agent_status="")
            t = a.view(key)
            self.assertEqual((t.agent_status, t.agent_status_at), ("", None))


if __name__ == "__main__":
    unittest.main()
