"""Tests for TKT-16: the `tkt activity` verb's CLI layer and schema.

Covers the window validation that runs before any adapter is built (ISO date
or datetime, naive = UTC, `--until` defaults to now, `since < until`), the
event ordering and both output modes, the ActivityEvent JSON shape, and the
base Adapter's "not supported" default. Offline: the adapter factory is
patched. Run from the repo root:

    python3 -m unittest tests.test_activity_cli
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.base import Adapter  # noqa: E402
from core import cli  # noqa: E402
from core.schema import ActivityEvent, ActivityReport, iso_utc  # noqa: E402

UTC = timezone.utc

CONFIG_TOML = """\
[ticketing]
provider = "jira"
project = "PROJ"

[board.roles]
todo = "To Do"

[queries]
active = 'status = "In Progress"'
"""


class RecordingAdapter:
    """Stands in for an adapter: records the window, returns canned events."""

    def __init__(self, events=()):
        self.events = list(events)
        self.windows = []

    def activity(self, query, since, until):
        self.windows.append((query, since, until))
        return ActivityReport(query=query, since=iso_utc(since),
                              until=iso_utc(until), tickets=["PROJ-1"],
                              events=list(self.events))


class NoActivity(Adapter):
    """A backend that implements only the required verbs."""

    def whoami(self): return ""
    def list(self, tier=None, query=None): return []
    def view(self, key): raise NotImplementedError
    def transition(self, key, role): pass
    def comment(self, key, body): pass
    def blockers(self, key): return []
    def worklog(self, key, from_role, note="", billable=False): raise NotImplementedError
    def lane_time(self, key, role, read_only=False): raise NotImplementedError
    def doctor(self): return []


class CliBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config_path = Path(tmp.name) / "config.toml"
        self.config_path.write_text(CONFIG_TOML, encoding="utf-8")

    def run_cli(self, adapter, *argv):
        self.built = 0

        def factory(config):
            self.built += 1
            return adapter

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "get_adapter", side_effect=factory), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--config", str(self.config_path), *argv])
        return code, out.getvalue(), err.getvalue()


class WindowValidation(CliBase):
    def window(self, *argv):
        stub = RecordingAdapter()
        code, _, err = self.run_cli(stub, "activity", "--query", "active", *argv)
        self.assertEqual(code, 0, err)
        return stub.windows[0]

    def test_naive_datetime_is_utc(self):
        _, since, until = self.window("--since", "2026-09-20T06:00",
                                      "--until", "2026-09-20T18:30:15")
        self.assertEqual(since, datetime(2026, 9, 20, 6, tzinfo=UTC))
        self.assertEqual(until, datetime(2026, 9, 20, 18, 30, 15, tzinfo=UTC))

    def test_bare_date_is_utc_midnight(self):
        _, since, _ = self.window("--since", "2026-09-20", "--until", "2026-09-21")
        self.assertEqual(since, datetime(2026, 9, 20, tzinfo=UTC))

    def test_offset_is_normalised_to_utc(self):
        _, since, _ = self.window("--since", "2026-09-20T02:00:00+02:00",
                                  "--until", "2026-09-21T00:00:00Z")
        self.assertEqual(since, datetime(2026, 9, 20, tzinfo=UTC))
        self.assertEqual(since.utcoffset(), timedelta(0))

    def test_until_defaults_to_now(self):
        before = datetime.now(UTC)
        _, _, until = self.window("--since", "2026-01-01")
        self.assertIsNotNone(until.tzinfo)
        self.assertTrue(before <= until <= datetime.now(UTC))

    def assert_usage_error(self, *argv):
        stub = RecordingAdapter()
        code, out, err = self.run_cli(stub, "activity", "--query", "active", *argv)
        self.assertEqual(code, 64, err)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("tkt: "), err)
        self.assertEqual(self.built, 0, "validation must precede the adapter")
        return err

    def test_garbage_since(self):
        self.assertIn("--since", self.assert_usage_error("--since", "last week"))

    def test_garbage_until(self):
        self.assertIn("--until", self.assert_usage_error(
            "--since", "2026-09-20", "--until", "2026-09-40"))

    def test_since_equal_until(self):
        self.assert_usage_error("--since", "2026-09-20T00:00:00Z",
                                "--until", "2026-09-20T02:00:00+02:00")

    def test_since_after_until(self):
        self.assert_usage_error("--since", "2026-09-21", "--until", "2026-09-20")

    def test_since_in_the_future_with_default_until(self):
        self.assert_usage_error("--since", "2999-01-01")

    def test_out_of_range_offset_is_a_usage_error(self):
        self.assert_usage_error("--since", "0001-01-01T00:00:00+01:00")

    def test_out_of_range_until_is_a_usage_error(self):
        self.assert_usage_error("--since", "2026-09-20",
                                "--until", "9999-12-31T23:59:59-01:00")

    def test_unknown_query_fails_before_the_adapter(self):
        stub = RecordingAdapter()
        code, out, err = self.run_cli(stub, "activity", "--query", "nope",
                                      "--since", "2026-09-20")
        self.assertEqual(code, 4, err)
        self.assertIn("nope", err)
        self.assertEqual(self.built, 0)

    def test_since_and_query_required(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                cli.main(["--config", str(self.config_path), "activity",
                          "--query", "active"])
            with self.assertRaises(SystemExit):
                cli.main(["--config", str(self.config_path), "activity",
                          "--since", "2026-09-20"])


def ev(eid, stamp, kind="comment", **kw):
    return ActivityEvent(evidence_id=eid, key="PROJ-1", kind=kind,
                         timestamp=stamp, **kw)


class Output(CliBase):
    EVENTS = [
        ev("jira:comment:2", "2026-09-20T10:00:00.000Z", actor="Ann",
           body="second line\nmore"),
        ev("jira:change:9:1", "2026-09-20T08:00:00.000Z", kind="change",
           actor="Bob", field="assignee", to="Bob"),
        ev("jira:change:9:0", "2026-09-20T08:00:00.000Z", kind="change",
           actor="Bob", field="status", from_="To Do", to="In Progress"),
    ]
    ARGS = ("activity", "--query", "active", "--since", "2026-09-20",
            "--until", "2026-09-21")

    def test_json_is_the_report_sorted_by_time_then_id(self):
        code, out, _ = self.run_cli(RecordingAdapter(self.EVENTS), *self.ARGS, "--json")
        self.assertEqual(code, 0)
        d = json.loads(out)
        self.assertEqual(set(d), {"query", "since", "until", "tickets", "events"})
        self.assertEqual((d["since"], d["until"]),
                         ("2026-09-20T00:00:00.000Z", "2026-09-21T00:00:00.000Z"))
        self.assertEqual([e["evidence_id"] for e in d["events"]],
                         ["jira:change:9:0", "jira:change:9:1", "jira:comment:2"])

    def test_tie_break_is_numeric_aware(self):
        at = "2026-09-20T08:00:00.000Z"
        events = [ev(f"jira:change:9:{i}", at, kind="change") for i in (10, 2, 11, 0, 1)]
        code, out, _ = self.run_cli(RecordingAdapter(events), *self.ARGS, "--json")
        self.assertEqual(code, 0)
        self.assertEqual([e["evidence_id"].rsplit(":", 1)[1]
                          for e in json.loads(out)["events"]],
                         ["0", "1", "2", "10", "11"])

    def test_json_flag_before_the_verb(self):
        code, out, _ = self.run_cli(RecordingAdapter(), "--json", *self.ARGS)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["events"], [])

    def test_human_is_one_line_per_event(self):
        code, out, _ = self.run_cli(RecordingAdapter(self.EVENTS), *self.ARGS)
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("status: To Do -> In Progress", lines[0])
        self.assertIn("assignee: - -> Bob", lines[1])
        self.assertTrue(lines[2].startswith("2026-09-20T10:00:00.000Z  PROJ-1"))
        self.assertIn("second line", lines[2])
        self.assertNotIn("more", lines[2])

    def test_human_empty(self):
        code, out, _ = self.run_cli(RecordingAdapter(), *self.ARGS)
        self.assertEqual((code, out), (0, "(no activity)\n"))

    def test_unsupported_backend_exits_3(self):
        code, out, err = self.run_cli(NoActivity(cli.Config.load(
            str(self.config_path))), *self.ARGS)
        self.assertEqual(code, 3)
        self.assertEqual(out, "")
        self.assertIn("activity not supported", err)


class Schema(unittest.TestCase):
    def test_event_dict_uses_from_not_from_(self):
        d = ev("x", "2026-09-20T00:00:00.000Z", kind="change",
               field="status", from_="A", to="B").to_dict()
        self.assertEqual(list(d), ["evidence_id", "key", "kind", "timestamp",
                                   "actor", "actor_id", "field", "from", "to",
                                   "body", "url"])
        self.assertEqual((d["from"], d["to"]), ("A", "B"))

    def test_iso_utc(self):
        self.assertEqual(
            iso_utc(datetime(2026, 9, 27, 8, 28, 36, 123456,
                             tzinfo=timezone(timedelta(hours=2)))),
            "2026-09-27T06:28:36.123Z")
        self.assertEqual(iso_utc(datetime(2026, 9, 27, tzinfo=UTC)),
                         "2026-09-27T00:00:00.000Z")


if __name__ == "__main__":
    unittest.main()
