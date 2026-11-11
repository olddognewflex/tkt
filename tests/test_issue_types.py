"""TKT-103: `[issue_types].container` and the `container` type class.

An Epic groups other tickets and is never worked directly. `container`
defaults to ["Epic"] when absent, `container = []` turns it off, and a type
in several lists resolves container > full_sdlc > deliverable, so an old
config with Epic under `deliverable` becomes `container`. Every adapter
reports it through `type_class`. Offline.

    python3 -m unittest tests.test_issue_types
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.github import GithubAdapter  # noqa: E402
from adapters.jira import JiraAdapter  # noqa: E402
from adapters.linear import LinearAdapter  # noqa: E402
from adapters.markdown import MarkdownAdapter  # noqa: E402
from adapters.openkanban import OpenKanbanAdapter  # noqa: E402
from core import cli  # noqa: E402
from core.config import Config  # noqa: E402
from core.errors import ConfigError  # noqa: E402

ROLES = {"todo": "To Do", "done": "Done", "cancelled": "Cancelled",
         "shipped": "Shipped"}


def config(issue_types=None, provider="markdown", close_on=None, **extra):
    d = {"ticketing": {"provider": provider, "project": "TKT"},
         "board": {"roles": dict(ROLES)}, "queries": {"tier1": 'status = "To Do"'}}
    if issue_types is not None:
        d["issue_types"] = issue_types
    if close_on is not None:
        d["board"]["close_on"] = close_on
    d.update(extra)
    return Config(d, Path("/nonexistent/.sdlc/config.toml"))


class ContainerConfig(unittest.TestCase):
    def test_default_container_when_key_absent(self):
        c = config({"full_sdlc": ["Story"], "deliverable": ["Task"]})
        self.assertEqual(c.container, {"Epic"})
        self.assertTrue(c.container_defaulted)
        self.assertEqual(c.type_class("Epic"), "container")

    def test_no_issue_types_table_still_defaults(self):
        self.assertEqual(config().type_class("Epic"), "container")

    def test_epic_listed_as_deliverable_becomes_container(self):
        c = config({"full_sdlc": ["Story"], "deliverable": ["Task", "Epic"]})
        self.assertEqual(c.type_class("Epic"), "container")
        self.assertEqual(c.type_class("Task"), "deliverable")

    def test_empty_container_opts_out(self):
        c = config({"deliverable": ["Task", "Epic"], "container": []})
        self.assertEqual(c.container, set())
        self.assertFalse(c.container_defaulted)
        self.assertEqual(c.type_class("Epic"), "deliverable")

    def test_explicit_container_list(self):
        c = config({"container": ["Initiative", "Epic"]})
        self.assertEqual(c.type_class("Initiative"), "container")
        self.assertEqual(c.type_class("Story"), "unknown")

    def test_non_array_container_is_a_config_error(self):
        for bad in ("Epic", 1, {"Epic": True}):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                config({"container": bad})

    def test_precedence_across_lists(self):
        c = config({"full_sdlc": ["Story", "Sub-task"],
                    "deliverable": ["Sub-task", "Epic"], "container": ["Epic"]})
        self.assertEqual(c.type_class("Epic"), "container")
        self.assertEqual(c.type_class("Sub-task"), "full_sdlc")

    def test_known_types_is_the_union(self):
        c = config({"full_sdlc": ["Story"], "deliverable": ["Task"]})
        self.assertEqual(c.known_types(), {"Story", "Task", "Epic"})

    def test_closed_roles_include_close_on(self):
        self.assertEqual(config().closed_roles(), {"done", "cancelled"})
        self.assertEqual(config(close_on=["shipped"]).closed_roles(),
                         {"done", "cancelled", "shipped"})


class AdaptersReportContainer(unittest.TestCase):
    """Every adapter resolves an Epic to `container` through the config."""

    def test_jira_issuetype(self):
        a = JiraAdapter(config({"full_sdlc": ["Story"], "deliverable": ["Task", "Epic"]},
                               provider="jira"))
        a.site = "example.atlassian.net"
        t = a._to_ticket({"key": "PROJ-1", "fields": {
            "issuetype": {"name": "Epic"}, "status": {"name": "To Do"}}})
        self.assertEqual(t.type_class, "container")

    def test_github_label(self):
        a = GithubAdapter(config({"full_sdlc": ["Story"]}, provider="github",
                                 github={"board": "labels", "repo": "owner/repo"}))
        t = a._issue_to_ticket({"number": 1, "title": "e", "body": "",
                                "url": "", "labels": [{"name": "Epic"}],
                                "assignees": []})
        self.assertEqual((t.type, t.type_class), ("Epic", "container"))

    def test_linear_label(self):
        a = LinearAdapter(config({"full_sdlc": ["Story"]}, provider="linear",
                                 linear={"team_key": "ENG"}))
        self.assertEqual(a._type_from_labels(["Epic"]), "Epic")
        self.assertEqual(a.config.type_class("Epic"), "container")

    def test_openkanban_label(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "projects.json").write_text(json.dumps(
            {"projects": {"p1": {"id": "p1", "name": "demo"}}}))
        a = OpenKanbanAdapter(config({"full_sdlc": ["Story"]}, provider="openkanban",
                                     openkanban={"project": "demo",
                                                 "config_dir": str(tmp)}))
        self.assertEqual(a._type_from_labels(["Epic"]), "Epic")
        t = a._to_ticket({"id": "E-1", "status": "To Do", "labels": ["Epic"],
                          "meta": {}})
        self.assertEqual(t.type_class, "container")

    def test_markdown_frontmatter(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "board").mkdir()
        (tmp / "board" / "TKT-1.md").write_text(
            "---\ntype: Epic\nstatus: To Do\n---\n# epic\n")
        a = MarkdownAdapter(config({"full_sdlc": ["Story"]},
                                   markdown={"board_dir": str(tmp / "board"),
                                             "state_dir": str(tmp / "state"),
                                             "me": "tester"}))
        self.assertEqual(a.view("TKT-1").type_class, "container")


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


class DoctorRow(unittest.TestCase):
    def row(self, extra):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        board = Path(tmp.name) / "board"
        board.mkdir()
        cfg = Path(tmp.name) / "config.toml"
        cfg.write_text(MARKDOWN_TOML.format(board=board, extra=extra))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["--config", str(cfg), "--json", "doctor"])
        rows = {c["name"]: c for c in json.loads(out.getvalue())}
        return rows["issue types"]

    def test_default_is_reported(self):
        r = self.row("")
        self.assertTrue(r["ok"])
        self.assertEqual(r["detail"], "container: Epic (default)")

    def test_overlap_is_reported(self):
        r = self.row('[issue_types]\ndeliverable = ["Task", "Epic"]\n')
        self.assertTrue(r["ok"])
        self.assertIn("Epic also in deliverable - container wins", r["detail"])

    def test_opt_out_is_reported(self):
        r = self.row('[issue_types]\ncontainer = []\n')
        self.assertEqual(r["detail"], "container: none")


if __name__ == "__main__":
    unittest.main()
