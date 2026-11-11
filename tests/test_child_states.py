"""TKT-103: `Adapter.child_states` reports each direct child and whether it
is closed, for `tkt children` and the container rollup.

The base default views each child: closed = a role in `closed_roles()`
(done, cancelled, `close_on`); a missing child warns and counts as open.
Jira and GitHub override it with one call; openkanban adds its native
done/archived statuses. Offline.

    python3 -m unittest tests.test_child_states
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

from adapters.markdown import MarkdownAdapter  # noqa: E402
from adapters.openkanban import OpenKanbanAdapter  # noqa: E402
from core.config import Config  # noqa: E402
from adapters.linear import LinearAdapter  # noqa: E402
from core import cli  # noqa: E402
from core.errors import NotFoundError, ProviderError, UsageError  # noqa: E402
from core.schema import Ticket  # noqa: E402

from tests.test_children_github import StubGithub, make_config as gh_config, sub  # noqa: E402
from tests.test_children_jira import StubAcliJira, StubRestJira  # noqa: E402

ROLES = {"todo": "To Do", "in_progress": "In Progress", "done": "Done",
         "cancelled": "Cancelled", "shipped": "Shipped"}


class MarkdownDefault(unittest.TestCase):
    """The base implementation, on a real markdown board."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "board").mkdir()
        cfg = Config({"ticketing": {"provider": "markdown", "project": "TKT"},
                      "markdown": {"board_dir": str(self.tmp / "board"),
                                   "state_dir": str(self.tmp / "state"),
                                   "me": "tester"},
                      "board": {"roles": dict(ROLES), "close_on": ["shipped"]},
                      "queries": {"all": 'status = "To Do"'}},
                     self.tmp / ".sdlc" / "config.toml")
        self.a = MarkdownAdapter(cfg)

    def write(self, key, status, children=None):
        fm = f"type: Story\nstatus: {status}\n"
        if children:
            fm += f"children: [{', '.join(children)}]\n"
        (self.tmp / "board" / f"{key}.md").write_text(f"---\n{fm}---\n# {key}\n")

    def test_closed_roles_and_open_ones(self):
        self.write("TKT-1", "To Do", ["TKT-2", "TKT-3", "TKT-4", "TKT-5"])
        self.write("TKT-2", "Done")
        self.write("TKT-3", "Cancelled")
        self.write("TKT-4", "Shipped")
        self.write("TKT-5", "In Progress")
        states = {s["key"]: s for s in self.a.child_states("TKT-1")}
        self.assertEqual({k: s["closed"] for k, s in states.items()},
                         {"TKT-2": True, "TKT-3": True, "TKT-4": True, "TKT-5": False})
        self.assertEqual(states["TKT-5"]["status_role"], "in_progress")
        self.assertEqual(states["TKT-2"]["status"], "Done")

    def test_children_argument_skips_the_parent_view(self):
        self.write("TKT-2", "Done")
        # TKT-1 does not exist: the passed keys are used as they are.
        self.assertEqual([s["key"] for s in self.a.child_states("TKT-1", ["TKT-2"])],
                         ["TKT-2"])

    def test_missing_child_warns_and_counts_as_open(self):
        self.write("TKT-1", "To Do", ["TKT-9"])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            states = self.a.child_states("TKT-1")
        self.assertEqual(states, [{"key": "TKT-9", "status": "", "status_role": "",
                                   "closed": False}])
        self.assertIn("child TKT-9 of TKT-1 not found", err.getvalue())

    def test_no_children(self):
        self.write("TKT-1", "To Do")
        self.assertEqual(self.a.child_states("TKT-1"), [])


def jira_child(key, status, category):
    return {"key": key, "fields": {"status": {
        "name": status, "statusCategory": {"key": category}}}}


class JiraOverride(unittest.TestCase):
    ROWS = [jira_child("PROJ-2", "Done", "done"),
            jira_child("PROJ-3", "In Progress", "indeterminate"),
            jira_child("PROJ-4", "Won't Do", "done"),     # unmapped lane, done category
            jira_child("PROJ-1", "To Do", "new")]          # the parent itself: dropped

    def expect(self, states):
        self.assertEqual([(s["key"], s["closed"]) for s in states],
                         [("PROJ-2", True), ("PROJ-3", False), ("PROJ-4", True)])
        self.assertEqual(states[1]["status_role"], "in_progress")

    def test_rest_one_search_with_status(self):
        a = StubRestJira(search_issues=self.ROWS)
        self.expect(a.child_states("PROJ-1"))
        (search,) = a.searches
        self.assertEqual(search["jql"], 'parent = "PROJ-1"')
        self.assertEqual(search["fields"], "status")
        self.assertFalse([c for c in a.calls if c[1].startswith("/rest/api/3/issue/")])

    def test_acli_one_search(self):
        a = StubAcliJira(search_result=self.ROWS)
        self.expect(a.child_states("PROJ-1"))
        searches = [c for c in a.calls if c[:3] == ("jira", "workitem", "search")]
        self.assertEqual(len(searches), 1)
        self.assertNotIn("--fields", searches[0])
        # Every page: a capped list could make a parent look finished.
        self.assertIn("--paginate", searches[0])
        self.assertNotIn("--limit", searches[0])

    def test_children_view_wire_calls_unchanged(self):
        a = StubRestJira(search_issues=[{"key": "PROJ-2"}])
        a.view("PROJ-1")
        self.assertEqual([s["fields"] for s in a.searches], ["key"])


def gh_sub(number, state="open", labels=(), repo="owner/repo"):
    s = sub(number, repo=repo)
    s["state"] = state
    s["labels"] = [{"name": n} for n in labels]
    return s


class GithubOverride(unittest.TestCase):
    def test_labels_mode_state_and_lane(self):
        a = StubGithub(gh_config(), pages=([
            gh_sub(2, "closed"),
            gh_sub(3, labels=["Status: Done"]),
            gh_sub(4, labels=["Status: In Progress"]),
            gh_sub(5, repo="other/repo"),
        ],))
        states = {s["key"]: s for s in a.child_states("1")}
        self.assertEqual({k: s["closed"] for k, s in states.items()},
                         {"2": True, "3": True, "4": False, "other/repo#5": False})
        self.assertEqual(states["4"]["status_role"], "in_progress")
        api = [c for c in a.calls if c[0] == "api"]
        self.assertEqual(len(api), 1)
        self.assertIn("repos/owner/repo/issues/1/sub_issues?per_page=100", api[0][1])
        self.assertFalse([c for c in a.calls if c[:2] == ("issue", "view")])

    def test_projectv2_reads_lanes_from_one_item_list(self):
        a = StubGithub(gh_config("projectv2"), pages=([gh_sub(7), gh_sub(8)],))
        states = {s["key"]: s for s in a.child_states("1")}
        self.assertEqual(states["7"]["status"], "Todo")
        self.assertEqual(states["8"]["status"], "")
        self.assertEqual(len([c for c in a.calls if c[:2] == ("project", "item-list")]), 1)

    def test_404_on_an_existing_issue_means_no_children(self):
        # e.g. a host without the sub-issues API: the issue itself is there.
        a = StubGithub(gh_config(), api_error=ProviderError(
            "gh api failed: gh: Not Found (HTTP 404)"))
        self.assertEqual(a.child_states("1"), [])

    def test_404_on_a_missing_issue_is_not_found(self):
        class Missing(StubGithub):
            def _gh_json(self, *args):
                if args[:2] == ("issue", "view"):
                    raise ProviderError("gh issue view failed: no issue")
                return super()._gh_json(*args)

        a = Missing(gh_config(), api_error=ProviderError(
            "gh api failed: gh: Not Found (HTTP 404)"))
        with self.assertRaises(NotFoundError):
            a.child_states("99999")

    def test_keys_that_are_not_issue_numbers_are_a_usage_error(self):
        a = StubGithub(gh_config())
        for bad in ("other/repo#5", "#5#6", "abc", ""):
            with self.subTest(bad=bad), self.assertRaises(UsageError):
                a.child_states(bad)
        self.assertFalse(a.calls)
        a.child_states("#1")          # one leading `#` is fine


class LinearNativeStates(unittest.TestCase):
    """A canceled-type state (Linear's "Duplicate") is closed whatever the
    role map says, for children exactly as for blockers."""

    def test_canceled_type_child_counts_as_closed(self):
        a = LinearAdapter(Config(
            {"ticketing": {"provider": "linear"}, "linear": {"team_key": "ENG"},
             "board": {"roles": dict(ROLES)}, "queries": {"all": ""}},
            Path("/nonexistent/.sdlc/config.toml")))
        issues = {
            "ENG-2": {"identifier": "ENG-2", "state": {"name": "Duplicate", "type": "canceled"}},
            "ENG-3": {"identifier": "ENG-3", "state": {"name": "In Progress", "type": "started"}},
        }
        a._gql = lambda q, v=None: {"issues": {"nodes": [{
            "identifier": "ENG-1",
            "children": {"nodes": [issues["ENG-2"], issues["ENG-3"], issues["ENG-2"],
                                   {"identifier": "ENG-1", "state": {}}],
                         "pageInfo": {"hasNextPage": False}}}]}}
        states = a.child_states("ENG-1", ["ENG-2"])
        self.assertEqual([(s["key"], s["closed"]) for s in states],
                         [("ENG-2", True), ("ENG-3", False)])
        self.assertEqual(states[0]["status_role"], "Duplicate")


def linear_adapter():
    return LinearAdapter(Config(
        {"ticketing": {"provider": "linear"}, "linear": {"team_key": "ENG"},
         "board": {"roles": dict(ROLES)}, "queries": {"all": ""}},
        Path("/nonexistent/.sdlc/config.toml")))


def linear_page(nodes, cursor=None):
    return {"issues": {"nodes": [{
        "identifier": "ENG-1",
        "children": {"nodes": nodes,
                     "pageInfo": {"hasNextPage": cursor is not None,
                                  "endCursor": cursor}}}]}}


class LinearReadsEveryChild(unittest.TestCase):
    """`view` holds only Linear's first page of children (50); child_states
    must page past it, or an open 51st child makes the parent look done."""

    def test_pages_past_the_first_and_ignores_the_truncated_list(self):
        a = linear_adapter()
        done = [{"identifier": f"ENG-{n}", "state": {"name": "Done", "type": "completed"}}
                for n in range(2, 52)]
        open_ = [{"identifier": "ENG-99", "state": {"name": "In Progress", "type": "started"}}]
        calls = []

        def gql(q, v=None):
            calls.append(dict(v))
            return linear_page(done, "c1") if v["c"] is None else linear_page(open_)

        a._gql = gql
        states = a.child_states("ENG-1", [d["identifier"] for d in done])
        self.assertEqual(len(states), 51)
        self.assertEqual([s["key"] for s in states if not s["closed"]], ["ENG-99"])
        self.assertEqual([c["c"] for c in calls], [None, "c1"])
        t = Ticket(key="ENG-1", type="Epic", type_class="container",
                   status_role="in_progress", children=[d["identifier"] for d in done])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(cli._container_rollup(a, t)["ready_to_close"])

    def test_a_cursor_that_does_not_advance_is_an_error_not_a_partial_list(self):
        a = linear_adapter()
        a._gql = lambda q, v=None: linear_page([], "same")
        with self.assertRaises(ProviderError):
            a.child_states("ENG-1")

    def test_unknown_and_malformed_keys(self):
        a = linear_adapter()
        a._gql = lambda q, v=None: {"issues": {"nodes": []}}
        with self.assertRaises(NotFoundError):
            a.child_states("ENG-404")
        with self.assertRaises(UsageError):
            a.child_states("not-a-key!")


def project_item(n, status, repo="owner/repo"):
    return {"id": f"I{n}", "status": status,
            "content": {"type": "Issue", "number": n, "repository": repo}}


class GithubProjectPaging(StubGithub):
    """A project with more items than one item-list page."""

    def __init__(self, items, **kw):
        super().__init__(gh_config("projectv2"), **kw)
        self.items = items

    def _gh_json(self, *args):
        self.calls.append(args)
        if args[:2] == ("project", "item-list"):
            limit = int(args[args.index("--limit") + 1])
            return {"items": self.items[:limit]}
        if args[:2] == ("project", "view"):
            return {"items": {"totalCount": len(self.items)}}
        return super()._gh_json(*args)


class GithubReadsEveryProjectItem(unittest.TestCase):
    def test_child_past_the_first_100_items_keeps_its_lane(self):
        items = [project_item(n, "Todo") for n in range(1, 120)] + [project_item(120, "Done")]
        a = GithubProjectPaging(items, pages=([gh_sub(120)],))
        [state] = a.child_states("1")
        self.assertEqual((state["status"], state["closed"]), ("Done", True))
        limits = [c[c.index("--limit") + 1] for c in a.calls if c[:2] == ("project", "item-list")]
        self.assertEqual(limits, ["100", "120"])

    def test_a_short_project_makes_one_item_list_call(self):
        a = GithubProjectPaging([project_item(7, "Done")], pages=([gh_sub(7)],))
        a.child_states("1")
        self.assertEqual([c[:2] for c in a.calls if c[0] == "project"],
                         [("project", "item-list")])


class GithubListCarriesIssueState(unittest.TestCase):
    """`gh project item-list` exports no issue state, so a closed issue in a
    stale lane would list as open; list asks GraphQL for the states."""

    def stub(self, graphql):
        class S(GithubProjectPaging):
            def _gh_json(self, *args):
                if args[:2] == ("api", "graphql"):
                    self.calls.append(args)
                    return graphql(args)
                return super()._gh_json(*args)
        return S([project_item(7, "In Progress"), project_item(8, "In Progress"),
                  project_item(9, "Todo", repo="other/repo")])

    def test_closed_issue_in_a_stale_lane_lists_as_closed(self):
        a = self.stub(lambda args: {"data": {"repository": {
            "i7": {"state": "CLOSED"}, "i8": {"state": "OPEN"}}}})
        closed = {t.key: t.closed for t in a.list(tier=1)}
        self.assertEqual(closed, {"7": True, "8": False})
        [q] = [c for c in a.calls if c[:2] == ("api", "graphql")]
        self.assertIn("i7: issue(number: 7)", q[3])
        self.assertNotIn("number: 9", q[3])     # other repo's item

    def test_a_failed_state_lookup_warns_and_falls_back_to_the_lane(self):
        def boom(args):
            raise ProviderError("gh api graphql failed")
        a = self.stub(boom)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            closed = {t.key: t.closed for t in a.list(tier=1)}
        self.assertEqual(closed, {"7": False, "8": False})
        self.assertIn("issue state unavailable", err.getvalue())


class ContainerClosedNatively(unittest.TestCase):
    """The container is judged like its children: a native terminal state
    counts even when its lane or label is stale or unmapped."""

    def rollup(self, adapter, t):
        adapter.child_states = lambda key, children=None: [
            {"key": "C-1", "status": "", "status_role": "", "closed": True}]
        with contextlib.redirect_stderr(io.StringIO()):
            return cli._container_rollup(adapter, t)

    def test_closed_github_epic_with_stale_label_is_not_ready(self):
        a = StubGithub(gh_config())
        t = a._issue_to_ticket({"number": 1, "title": "e", "body": "", "url": "",
                                "state": "CLOSED", "assignees": [],
                                "labels": [{"name": "Epic"},
                                           {"name": "Status: In Progress"}]},
                               children=["2"])
        self.assertEqual((t.type_class, t.status_role, t.closed),
                         ("container", "in_progress", True))
        self.assertFalse(self.rollup(a, t)["ready_to_close"])

    def test_open_github_epic_with_closed_children_is_ready(self):
        a = StubGithub(gh_config())
        t = a._issue_to_ticket({"number": 1, "title": "e", "body": "", "url": "",
                                "state": "OPEN", "assignees": [],
                                "labels": [{"name": "Epic"},
                                           {"name": "Status: In Progress"}]},
                               children=["2"])
        self.assertFalse(t.closed)
        self.assertTrue(self.rollup(a, t)["ready_to_close"])

    def test_jira_epic_in_unmapped_done_category_is_not_ready(self):
        a = StubRestJira(search_issues=[])
        t = a._to_ticket({"key": "PROJ-1", "fields": {
            "issuetype": {"name": "Epic"},
            "status": {"name": "Won't Do", "statusCategory": {"key": "done"}}}},
            children=["PROJ-2"])
        self.assertEqual((t.type_class, t.status_role, t.closed),
                         ("container", "Won't Do", True))
        self.assertFalse(self.rollup(a, t)["ready_to_close"])

    def test_ticket_built_outside_an_adapter_falls_back_to_roles(self):
        a = StubRestJira(search_issues=[])
        t = Ticket(key="PROJ-1", type_class="container", status_role="todo",
                   children=["PROJ-2"])
        self.assertIsNone(t.closed)
        self.assertTrue(self.rollup(a, t)["ready_to_close"])


class OpenKanbanOverride(unittest.TestCase):
    def test_archived_counts_as_closed(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "projects.json").write_text(json.dumps(
            {"projects": {"p1": {"id": "p1", "name": "demo"}}}))
        (tmp / "tickets").mkdir()

        def raw(tid, status, meta=None):
            return {"id": tid, "project_id": "p1", "title": tid, "description": "",
                    "status": status, "agent_status": "none", "labels": [],
                    "priority": 3, "meta": meta or {}}

        (tmp / "tickets" / "p1.json").write_text(json.dumps(
            {"project_id": "p1", "updated_at": "2026-01-01T00:00:00Z", "tickets": {
                "E-1": raw("E-1", "backlog", {"children": "T-2,T-3"}),
                "T-2": raw("T-2", "archived"),
                "T-3": raw("T-3", "in_progress")}}))
        a = OpenKanbanAdapter(Config(
            {"ticketing": {"provider": "openkanban"},
             "openkanban": {"project": "demo", "config_dir": str(tmp)},
             "board": {"roles": {"todo": "backlog", "in_progress": "in_progress",
                                 "done": "done"}},
             "queries": {"all": 'status = "backlog"'}},
            tmp / ".sdlc" / "config.toml"))
        self.assertEqual([(s["key"], s["closed"]) for s in a.child_states("E-1")],
                         [("T-2", True), ("T-3", False)])


if __name__ == "__main__":
    unittest.main()
