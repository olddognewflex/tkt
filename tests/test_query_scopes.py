"""Tests for TKT-18: `[query_scopes]` marks named queries project-scoped
(the default) or global.

Every named query used to be forced into the configured project, so a query
that legitimately spans projects ("everything assigned to me across the
org") could not be expressed. `Config.query_scope` reads the optional table,
and the Jira adapter skips the project condition for a "global" query. Fully
offline: Jira search is stubbed. Run from the repo root:

    python3 -m unittest tests.test_query_scopes
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.jira import JiraAdapter  # noqa: E402
from core import cli  # noqa: E402
from core.config import Config  # noqa: E402
from core.errors import ConfigError  # noqa: E402

ROLES = {"todo": "To Do", "done": "Done"}
QUERIES = {"tier1": 'status = "To Do"',
           "mine_all": "assignee = currentUser() ORDER BY priority DESC"}


def config(scopes=None, project="TKT"):
    d = {"ticketing": {"provider": "jira", "project": project},
         "board": {"roles": dict(ROLES)}, "queries": dict(QUERIES)}
    if scopes is not None:
        d["query_scopes"] = scopes
    return Config(d, Path("/nonexistent/.sdlc/config.toml"))


class QueryScope(unittest.TestCase):

    def test_default_is_project_without_a_table(self):
        self.assertEqual(config().query_scope(name="mine_all"), "project")

    def test_unlisted_query_defaults_to_project(self):
        c = config({"mine_all": "global"})
        self.assertEqual(c.query_scope(name="tier1"), "project")

    def test_explicit_global(self):
        c = config({"mine_all": "global"})
        self.assertEqual(c.query_scope(name="mine_all"), "global")

    def test_explicit_project(self):
        c = config({"mine_all": "project"})
        self.assertEqual(c.query_scope(name="mine_all"), "project")

    def test_tier_key(self):
        c = config({"tier1": "global"})
        self.assertEqual(c.query_scope(tier=1), "global")
        self.assertEqual(c.query_scope(tier="1"), "global")

    def test_invalid_value_is_a_config_error(self):
        for bad in ("Global", "all", "", 1, True, ["global"]):
            with self.assertRaises(ConfigError, msg=repr(bad)):
                config({"mine_all": bad}).query_scope(name="mine_all")

    def test_invalid_value_elsewhere_in_the_table_still_fails(self):
        c = config({"mine_all": "global", "tier1": "everywhere"})
        with self.assertRaises(ConfigError) as cm:
            c.query_scope(name="mine_all")
        self.assertIn("tier1", str(cm.exception))

    def test_misspelled_key_is_a_config_error(self):
        for bad in ("mine_al", "tier01", "tier9"):
            with self.assertRaises(ConfigError, msg=bad) as cm:
                config({bad: "global"}).query_scope(name="mine_all")
            self.assertIn("no such query", str(cm.exception))

    def test_query_scopes_returns_the_validated_table(self):
        self.assertEqual(config().query_scopes(), {})
        self.assertEqual(config({"tier1": "global"}).query_scopes(),
                         {"tier1": "global"})

    def test_non_table_is_a_config_error(self):
        with self.assertRaises(ConfigError):
            config("global").query_scope(name="mine_all")

    def test_config_error_exits_2(self):
        with self.assertRaises(ConfigError) as cm:
            config({"mine_all": "bad"}).query_scope(name="mine_all")
        self.assertEqual(cm.exception.exit_code, 2)


class StubJira(JiraAdapter):
    """REST-mode adapter recording the JQL each search is given."""

    def __init__(self, cfg):
        super().__init__(cfg)
        self.site = "example.atlassian.net"
        self.have_rest = True
        self.jql: list[str] = []

    def _search_issues(self, jql, fields):
        self.jql.append(jql)
        return []


class JiraScoping(unittest.TestCase):

    def test_project_scope_adds_the_project_condition(self):
        a = StubJira(config({"mine_all": "project"}))
        a.list(query="mine_all")
        self.assertEqual(a.jql, ['(project = "TKT") AND (assignee = currentUser()) '
                                 'ORDER BY priority DESC'])

    def test_global_scope_runs_the_jql_as_written(self):
        a = StubJira(config({"mine_all": "global"}))
        a.list(query="mine_all")
        self.assertEqual(a.jql, [QUERIES["mine_all"]])

    def test_global_tier(self):
        a = StubJira(config({"tier1": "global"}))
        a.list(tier="1")
        self.assertEqual(a.jql, [QUERIES["tier1"]])

    def test_other_queries_stay_scoped(self):
        a = StubJira(config({"mine_all": "global"}))
        a.list(tier="1")
        self.assertEqual(a.jql, ['(project = "TKT") AND (status = "To Do")'])

    def test_invalid_scope_fails_before_searching(self):
        a = StubJira(config({"mine_all": "nope"}))
        with self.assertRaises(ConfigError):
            a.list(query="mine_all")
        self.assertEqual(a.jql, [])

    def test_activity_honours_global_scope(self):
        a = StubJira(config({"mine_all": "global"}))
        since = datetime(2026, 9, 20, tzinfo=timezone.utc)
        a.activity("mine_all", since, datetime(2026, 9, 21, tzinfo=timezone.utc))
        self.assertEqual(a.jql, ['(assignee = currentUser()) AND '
                                 'updated >= "2026-09-19" ORDER BY priority DESC'])

    def test_activity_project_scope(self):
        a = StubJira(config())
        since = datetime(2026, 9, 20, tzinfo=timezone.utc)
        a.activity("mine_all", since, datetime(2026, 9, 21, tzinfo=timezone.utc))
        self.assertTrue(a.jql[0].startswith('(project = "TKT") AND '))


MARKDOWN_TOML = """\
[ticketing]
provider = "markdown"
project = "TKT"

[markdown]
board_dir = "{board}"
me = "tester"

[board.roles]
todo = "To Do"
done = "Done"

[queries]
tier1 = 'status = "To Do"'
{extra}
"""


class Cli(unittest.TestCase):
    """Backend-agnostic surfaces: doctor, and activity's up-front checks."""

    def run_cli(self, extra, *argv):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        board = Path(tmp.name) / "board"
        board.mkdir()
        cfg = Path(tmp.name) / "config.toml"
        cfg.write_text(MARKDOWN_TOML.format(board=board, extra=extra),
                       encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--config", str(cfg), *argv])
        return code, out.getvalue(), err.getvalue()

    def doctor_row(self, extra):
        code, out, _ = self.run_cli(extra, "--json", "doctor")
        rows = {c["name"]: c for c in json.loads(out)}
        return code, rows.get("query scopes")

    def test_doctor_flags_a_bad_value_on_a_non_jira_backend(self):
        code, row = self.doctor_row('[query_scopes]\ntier1 = "bogus"')
        self.assertEqual(code, 1)
        self.assertFalse(row["ok"])
        self.assertIn("bogus", row["detail"])

    def test_doctor_flags_a_misspelled_key(self):
        code, row = self.doctor_row('[query_scopes]\ntier01 = "global"')
        self.assertFalse(row["ok"])
        self.assertIn("no such query", row["detail"])

    def test_doctor_passes_a_good_table(self):
        _, row = self.doctor_row('[query_scopes]\ntier1 = "global"')
        self.assertTrue(row["ok"])

    def test_doctor_has_no_row_without_a_table(self):
        _, row = self.doctor_row("")
        self.assertIsNone(row)

    def test_activity_bad_scope_exits_2_before_the_backend(self):
        code, _, err = self.run_cli('[query_scopes]\ntier1 = "bogus"',
                                    "activity", "--query", "tier1",
                                    "--since", "2026-09-20")
        self.assertEqual(code, 2, err)
        self.assertNotIn("not supported", err)


if __name__ == "__main__":
    unittest.main()
