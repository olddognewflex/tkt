"""Regression tests for TKT-17: non-billable Jira worklogs are marked
natively, in the create request, and Tempo is only called under
`[timetracking].provider = "tempo"`.

`worklog` and `lane-time` used to post the worklog, then always make a
second round-trip to Tempo to mark it non-billable. That demanded
`TEMPO_API_TOKEN` from consumers who don't use Tempo, and could fail after
the worklog existed, leaving billable time recorded. Now a `billing` worklog
entity property rides in the same POST, and the Tempo PUT runs only for the
Tempo provider (Tempo reports from its own `billableSeconds`, not the Jira
property). Fully offline: REST and Tempo transports are stubbed. Run from
the repo root:

    python3 -m unittest tests.test_jira_worklog
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters import jira  # noqa: E402
from adapters.jira import JiraAdapter  # noqa: E402
from core import cli  # noqa: E402
from core.config import Config  # noqa: E402

ROLES = {"todo": "To Do", "in_progress": "In Progress",
         "review": "In Review", "done": "Done"}
PROPERTY = {"key": "billing", "value": {"billable": False, "type": "non-billable"}}


class StubJira(JiraAdapter):
    """REST-mode adapter: worklog POSTs and Tempo calls are recorded."""

    def __init__(self, provider, tempo_token="tempo-token", rest=True,
                 tempo_fails=False, **timetracking):
        super().__init__(Config(
            {"ticketing": {"provider": "jira", "project": "TKT"},
             "board": {"roles": dict(ROLES)},
             "timetracking": {"provider": provider, **timetracking}},
            Path("/nonexistent/.sdlc/config.toml")))
        self.tempo_fails = tempo_fails
        self.site = "example.atlassian.net"
        self.have_rest = rest
        self.tempo_token = tempo_token
        self.posts: list[dict] = []
        self.tempo_calls: list[tuple[str, str]] = []

    def _changelog_entries(self, key):
        return [("2026-09-27T08:00:00.000+0000", "To Do", "In Progress"),
                ("2026-09-27T09:00:00.000+0000", "In Progress", "In Review")]

    def _jira(self, method, path, body=None):
        assert (method, path) == ("POST", "/rest/api/3/issue/TKT-1/worklog"), path
        self.posts.append(body)
        return {"id": "501"}

    def _tempo(self, method, path, body=None):
        self.tempo_calls.append((method, path))
        if self.tempo_fails:
            raise RuntimeError("tempo down")
        if method == "GET":
            return {"results": [{
                "tempoWorklogId": 9, "issue": {"id": 1},
                "startDate": "2026-09-27", "startTime": "08:00:00",
                "author": {"accountId": "acc"}}]}
        return {}


class NativeProperty(unittest.TestCase):

    def test_non_billable_worklog_carries_the_property(self):
        a = StubJira("jira-worklog")
        wl = a.worklog("TKT-1", "in_progress")
        self.assertEqual(wl.worklog_id, "501")
        self.assertEqual(a.posts[0]["properties"], [PROPERTY])

    def test_billable_worklog_has_no_property(self):
        a = StubJira("jira-worklog")
        a.worklog("TKT-1", "in_progress", billable=True)
        self.assertNotIn("properties", a.posts[0])

    def test_property_is_in_the_single_create_request(self):
        a = StubJira("jira-worklog")
        a.worklog("TKT-1", "in_progress")
        self.assertEqual(len(a.posts), 1)

    def test_retroactive_lane_time_is_always_non_billable(self):
        a = StubJira("jira-worklog")
        wl = a.lane_time("TKT-1", "in_progress")
        self.assertEqual(wl.seconds, 3600)
        self.assertEqual(a.posts[0]["properties"], [PROPERTY])

    def test_property_is_not_shared_mutable_state(self):
        a = StubJira("jira-worklog")
        a.worklog("TKT-1", "in_progress")
        a.posts[0]["properties"][0]["value"]["billable"] = True
        self.assertFalse(JiraAdapter.NON_BILLABLE_PROPERTY["value"]["billable"])


class TempoOnlyUnderTempo(unittest.TestCase):

    def test_tempo_provider_also_zeroes_tempo_billable_seconds(self):
        a = StubJira("tempo")
        a.worklog("TKT-1", "in_progress")
        self.assertEqual([m for m, _ in a.tempo_calls], ["GET", "PUT"])
        self.assertEqual(a.posts[0]["properties"], [PROPERTY])

    def test_tempo_provider_lane_time_calls_tempo(self):
        a = StubJira("tempo")
        a.lane_time("TKT-1", "in_progress")
        self.assertIn("PUT", [m for m, _ in a.tempo_calls])

    def test_jira_worklog_provider_never_calls_tempo(self):
        a = StubJira("jira-worklog")
        a.worklog("TKT-1", "in_progress")
        a.lane_time("TKT-1", "in_progress")
        self.assertEqual(a.tempo_calls, [])

    def test_no_tempo_token_needed_outside_tempo(self):
        a = StubJira("jira-worklog", tempo_token="")
        wl = a.worklog("TKT-1", "in_progress")
        self.assertEqual(wl.worklog_id, "501")
        self.assertEqual(a.tempo_calls, [])

    def test_billable_tempo_worklog_skips_tempo(self):
        a = StubJira("tempo")
        a.worklog("TKT-1", "in_progress", billable=True)
        self.assertEqual(a.tempo_calls, [])

    def test_provider_none_posts_nothing(self):
        a = StubJira("none")
        wl = a.worklog("TKT-1", "in_progress")
        self.assertEqual(wl.worklog_id, "")
        self.assertEqual(a.posts, [])
        self.assertEqual(a.tempo_calls, [])


class TempoFailuresStayOffStdout(unittest.TestCase):
    """Skills parse `tkt worklog --json` stdout; warnings belong on stderr."""

    def run_worklog(self, a, *flags):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Path(tmp.name) / "config.toml"
        cfg.write_text('[ticketing]\nprovider = "jira"\n', encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "get_adapter", return_value=a), \
                mock.patch.object(jira.time, "sleep"), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--config", str(cfg), "worklog", "TKT-1",
                             "--from-role", "in_progress", "--json", *flags])
        return code, out.getvalue(), err.getvalue()

    def test_missing_tempo_token_warns_on_stderr(self):
        a = StubJira("tempo", tempo_token="")
        code, out, err = self.run_worklog(a)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["worklog_id"], "501")
        self.assertIn("TEMPO_API_TOKEN not set", err)
        self.assertEqual(a.tempo_calls, [])

    def test_tempo_failure_keeps_the_worklog_and_warns_on_stderr(self):
        a = StubJira("tempo", tempo_fails=True)
        code, out, err = self.run_worklog(a)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["worklog_id"], "501")
        self.assertIn("failed to set worklog 501 non-billable", err)
        self.assertEqual(len(a.tempo_calls), 5)
        self.assertEqual(a.posts[0]["properties"], [PROPERTY])


class BillableDefault(TempoFailuresStayOffStdout):
    """`[timetracking].billable` is the default; the flags override it."""

    def billable_sent(self, cfg_billable, *flags):
        kw = {} if cfg_billable is None else {"billable": cfg_billable}
        a = StubJira("jira-worklog", **kw)
        code, _, err = self.run_worklog(a, *flags)
        self.assertEqual(code, 0, err)
        return "properties" not in a.posts[0]

    def test_unset_config_defaults_non_billable(self):
        self.assertFalse(self.billable_sent(None))

    def test_config_true_makes_worklogs_billable(self):
        self.assertTrue(self.billable_sent(True))

    def test_no_billable_flag_overrides_config(self):
        self.assertFalse(self.billable_sent(True, "--no-billable"))

    def test_billable_flag_overrides_config(self):
        self.assertTrue(self.billable_sent(False, "--billable"))


class Doctor(unittest.TestCase):

    def checks(self, a):
        # Skip the live reachability probe; only the config-derived rows matter.
        a.whoami = lambda: "tester"
        return {c.name: c for c in a.doctor()}

    def test_worklog_red_without_rest(self):
        c = self.checks(StubJira("jira-worklog", rest=False))
        self.assertFalse(c["worklog (REST)"].ok)

    def test_worklog_green_with_rest(self):
        c = self.checks(StubJira("jira-worklog"))
        self.assertTrue(c["worklog (REST)"].ok)
        self.assertNotIn("tempo token (for non-billable)", c)

    def test_no_worklog_row_when_not_tracking(self):
        self.assertNotIn("worklog (REST)", self.checks(StubJira("none")))

    def test_unknown_provider_is_red(self):
        for bad in ("Tempo", "local"):
            c = self.checks(StubJira(bad))
            self.assertFalse(c["timetracking provider"].ok, bad)

    def test_known_providers_have_no_provider_row(self):
        for good in ("none", "jira-worklog", "tempo"):
            self.assertNotIn("timetracking provider", self.checks(StubJira(good)))

    def test_tempo_provider_still_checks_the_tempo_token(self):
        c = self.checks(StubJira("tempo", tempo_token=""))
        self.assertFalse(c["tempo token (for non-billable)"].ok)


if __name__ == "__main__":
    unittest.main()
