"""Tests for TKT-70: `tkt list --all-projects` drops project scoping for one
call, so a shared markdown board can be viewed whole.

`list` is scoped to the configured `[ticketing] project` (TKT-59), which is
right for `select-ticket` but leaves no way to see every repo's tickets on a
shared board. `--all-projects` lifts the key-prefix filter on markdown and the
project condition on Jira; every other adapter accepts it and ignores it.
Fully offline: the markdown adapter is pointed at a temp board dir and the
remote backends are stubbed. Run from the repo root:

    python3 -m unittest tests.test_list_all_projects
"""
import contextlib
import importlib
import inspect
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
from core import cli, registry  # noqa: E402
from core.config import Config  # noqa: E402
from core.errors import ProviderError  # noqa: E402

ROLES = {"backlog": "Backlog", "todo": "To Do", "in_progress": "In Progress",
         "review": "In Review", "done": "Done", "blocked": "Blocked"}

TICKET = """---
type: Story
status: {status}
priority: High
assignee: {assignee}
---

# {key}

Body for {key}.
"""

# One shared board, four projects. TKB is "this" repo.
SHARED = {"TKT-1": "To Do", "TKB-1": "To Do", "TEACH-2": "In Progress",
          "OPS-3": "To Do"}
ALL_KEYS = ["OPS-3", "TEACH-2", "TKB-1", "TKT-1"]   # key order


def board(tmp, tickets, assignee="raymond"):
    d = Path(tmp) / "board"
    d.mkdir(parents=True, exist_ok=True)
    for k, status in tickets.items():
        (d / f"{k}.md").write_text(TICKET.format(key=k, status=status,
                                                 assignee=assignee))
    return d


def md_config(d, tmp, project="TKB"):
    ticketing = {"provider": "markdown"}
    if project is not None:
        ticketing["project"] = project
    return Config({"ticketing": ticketing,
                   "markdown": {"board_dir": str(d),
                                "state_dir": str(Path(tmp) / "state"),
                                "me": "raymond"},
                   "board": {"roles": dict(ROLES)},
                   "queries": {"all": "ORDER BY key",
                               "tier1": 'status = "To Do"',
                               "mine": "assignee = currentUser()"}},
                  Path(tmp) / ".sdlc" / "config.toml")


def adapter(tmp, tickets=SHARED, project="TKB", **kw):
    return MarkdownAdapter(md_config(board(tmp, tickets, **kw), tmp, project))


def keys(tickets):
    return [t.key for t in tickets]


class MarkdownAllProjects(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_without_the_flag_only_this_project(self):
        self.assertEqual(keys(adapter(self.tmp).list(query="all")), ["TKB-1"])

    def test_flag_lists_every_prefix(self):
        a = adapter(self.tmp)
        self.assertEqual(keys(a.list(query="all", all_projects=True)), ALL_KEYS)

    def test_flag_still_applies_the_tier_query_across_prefixes(self):
        # TEACH-2 is In Progress, so tier1 (To Do) drops it.
        got = adapter(self.tmp).list(tier=1, all_projects=True)
        self.assertEqual(sorted(keys(got)), ["OPS-3", "TKB-1", "TKT-1"])

    def test_current_user_still_filters(self):
        d = board(self.tmp, SHARED)
        (d / "OPS-9.md").write_text(TICKET.format(key="OPS-9", status="To Do",
                                                  assignee="someone"))
        a = MarkdownAdapter(md_config(d, self.tmp))
        got = keys(a.list(query="mine", all_projects=True))
        self.assertNotIn("OPS-9", got)
        self.assertEqual(sorted(got), ALL_KEYS)

    def test_unset_project_flag_matches_no_flag(self):
        a = adapter(self.tmp, project=None)
        self.assertEqual(keys(a.list(query="all", all_projects=True)),
                         keys(a.list(query="all")))
        self.assertEqual(sorted(keys(a.list(query="all"))), ALL_KEYS)

    def test_flag_tolerates_junk_on_a_shared_board(self):
        a = adapter(self.tmp)
        d = a.board_dir
        (d / "TEACH-dir.md").mkdir()               # a directory named *.md
        (d / ".hidden.md").write_text("not a ticket")
        self.assertEqual(sorted(keys(a.list(query="all", all_projects=True))),
                         ALL_KEYS)

    def test_flag_lists_human_named_tickets(self):
        # Same rule as an unset project (UnprefixedKeys): a human-named file
        # is a ticket, so the whole-board view shows it.
        a = adapter(self.tmp)
        (a.board_dir / "fix-login.md").write_text(TICKET.format(
            key="fix-login", status="To Do", assignee="raymond"))
        self.assertIn("fix-login",
                      keys(a.list(query="all", all_projects=True)))
        self.assertNotIn("fix-login", keys(a.list(query="all")))

    def test_missing_board_dir_is_still_a_provider_error(self):
        a = MarkdownAdapter(md_config(Path(self.tmp) / "nope", self.tmp))
        with self.assertRaises(ProviderError):
            a.list(query="all", all_projects=True)


MARKDOWN_TOML = """\
[ticketing]
provider = "markdown"
project = "TKB"

[markdown]
board_dir = "{board}"
me = "raymond"

[board.roles]
todo = "To Do"
in_progress = "In Progress"
done = "Done"

[queries]
all = "ORDER BY key"
tier1 = 'status = "To Do"'
"""


class Cli(unittest.TestCase):

    def run_cli(self, *argv):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = board(tmp.name, SHARED)
        cfg = self.cfg = Path(tmp.name) / "config.toml"
        cfg.write_text(MARKDOWN_TOML.format(board=d), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(["--config", str(cfg), *argv])
            except SystemExit as e:   # argparse usage errors
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def json_keys(self, *argv):
        code, out, err = self.run_cli(*argv)
        self.assertEqual(code, 0, err)
        return [t["key"] for t in json.loads(out)]

    def test_query_all_with_flag_lists_every_prefix(self):
        got = self.json_keys("list", "--query", "all", "--all-projects", "--json")
        self.assertEqual(got, ALL_KEYS)

    def test_without_the_flag_output_is_scoped(self):
        self.assertEqual(self.json_keys("list", "--query", "all", "--json"),
                         ["TKB-1"])

    def test_without_the_flag_output_is_unchanged(self):
        # Byte-identical to the adapter called with no `all_projects`
        # argument at all, i.e. the call the CLI makes without the flag.
        code, out, err = self.run_cli("list", "--query", "all", "--json")
        self.assertEqual(code, 0, err)
        a = MarkdownAdapter(Config.load(str(self.cfg)))
        baseline = io.StringIO()
        with contextlib.redirect_stdout(baseline):
            cli._print_ticket_list(a.list(query="all"), True)
        self.assertEqual(out, baseline.getvalue())

    def test_tier_with_flag(self):
        got = self.json_keys("list", "--tier", "1", "--all-projects", "--json")
        self.assertEqual(sorted(got), ["OPS-3", "TKB-1", "TKT-1"])

    def test_flag_alone_is_a_usage_error(self):
        code, _, err = self.run_cli("list", "--all-projects")
        self.assertEqual(code, 2)
        self.assertIn("--tier", err)


def jira_config(scopes=None):
    d = {"ticketing": {"provider": "jira", "project": "TKT"},
         "board": {"roles": {"todo": "To Do", "done": "Done"}},
         "queries": {"tier1": 'status = "To Do"',
                     "mine": "assignee = currentUser() ORDER BY priority DESC"}}
    if scopes is not None:
        d["query_scopes"] = scopes
    return Config(d, Path("/nonexistent/.sdlc/config.toml"))


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


class JiraAllProjects(unittest.TestCase):

    def test_flag_leaves_the_jql_unscoped(self):
        a = StubJira(jira_config())
        a.list(query="mine", all_projects=True)
        self.assertEqual(a.jql, ["assignee = currentUser() ORDER BY priority DESC"])

    def test_without_the_flag_stays_scoped(self):
        a = StubJira(jira_config())
        a.list(tier=1)
        self.assertEqual(a.jql, ['(project = "TKT") AND (status = "To Do")'])

    def test_flag_overrides_a_project_scoped_query_scope(self):
        a = StubJira(jira_config({"tier1": "project"}))
        a.list(tier=1, all_projects=True)
        self.assertEqual(a.jql, ['status = "To Do"'])


class OldSignatureAdapter(unittest.TestCase):
    """An adapter predating `all_projects` still lists without the flag."""

    def test_cli_omits_the_kwarg_when_the_flag_is_off(self):
        seen = []

        class Legacy(MarkdownAdapter):
            def list(self, tier=None, query=None):
                seen.append((tier, query))
                return []

        orig = cli.get_adapter
        cli.get_adapter = lambda config: Legacy(config)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                board(tmp, SHARED)
                cfg = Path(tmp) / "c.toml"
                cfg.write_text(MARKDOWN_TOML.format(board=Path(tmp) / "board",
                                                    state=Path(tmp) / "state"))
                with contextlib.redirect_stdout(io.StringIO()):
                    code = cli.main(["--config", str(cfg), "list",
                                     "--query", "all", "--json"])
        finally:
            cli.get_adapter = orig
        self.assertEqual(code, 0)
        self.assertEqual(seen, [(None, "all")])


class OtherAdaptersAcceptTheFlag(unittest.TestCase):
    """No-op elsewhere: same result with and without the flag, no error."""

    def test_github_labels_mode(self):
        cfg = Config({"ticketing": {"provider": "github"},
                      "board": {"roles": {"todo": "Todo", "done": "Done"}},
                      "github": {"board": "labels", "repo": "owner/repo"},
                      "queries": {"tier1": "is:open"}},
                     Path("/nonexistent/.sdlc/config.toml"))

        class Stub(GithubAdapter):
            calls = []

            def _gh_json(self, *args):
                self.calls.append(args)
                return []

        a = Stub(cfg)
        self.assertEqual(a.list(tier=1, all_projects=True), [])
        self.assertIn("owner/repo", a.calls[0])   # still scoped by --repo

    def test_github_projectv2_mode(self):
        from tests.test_github_scope import StubGithub, make_config
        gh = StubGithub(make_config())
        self.assertEqual(sorted(keys(gh.list(tier=1, all_projects=True))),
                         sorted(keys(gh.list(tier=1))))

    def test_linear(self):
        cfg = Config({"ticketing": {"provider": "linear"},
                      "linear": {"team_key": "ENG"},
                      "board": {"roles": {"todo": "Todo"}},
                      "queries": {"tier1": 'status = "Todo"'}},
                     Path("/nonexistent/.sdlc/config.toml"))

        class Stub(LinearAdapter):
            def _gql(self, query, variables=None):
                return {"issues": {"nodes": []}}

            def whoami(self):
                return "me"

        self.assertEqual(Stub(cfg).list(tier=1, all_projects=True), [])

    def test_openkanban(self):
        cfg = Config({"ticketing": {"provider": "openkanban"},
                      "openkanban": {"project": "p"},
                      "board": {"roles": {"todo": "backlog", "done": "done"}},
                      "queries": {"tier1": 'status = "backlog"'}},
                     Path("/nonexistent/.sdlc/config.toml"))

        class Stub(OpenKanbanAdapter):
            def _load_store(self):
                return {"tickets": {i: {"id": i, "title": i, "status": "backlog",
                                        "priority": 3} for i in ("a", "b")}}

            def _store_path(self):
                return Path("/nonexistent/store.json")

        a = Stub(cfg)
        self.assertEqual(keys(a.list(tier=1, all_projects=True)),
                         keys(a.list(tier=1)))


class Contract(unittest.TestCase):

    def test_every_registered_adapter_accepts_all_projects(self):
        for provider, (mod, name) in registry._PROVIDERS.items():
            with self.subTest(provider=provider):
                cls = getattr(importlib.import_module(mod), name)
                self.assertIn("all_projects",
                              inspect.signature(cls.list).parameters)


if __name__ == "__main__":
    unittest.main()
