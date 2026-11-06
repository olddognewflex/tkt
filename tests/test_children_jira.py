"""TKT-19: `tkt view` on Jira reports a ticket's direct children.

Children come from one extra search, `parent = "KEY"`, made by `view` only:
`list` would pay it once per ticket, and `blockers` / `create` don't need it.
The search is deliberately NOT project-scoped, because an Epic's children may
live in other projects.

Fully offline: the REST path is stubbed at `_jira`, the acli path at `_acli`.
Run from the repo root:

    python3 -m unittest tests.test_children_jira
"""
import json
import os
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.jira import JiraAdapter  # noqa: E402
from core.config import Config  # noqa: E402
from core.errors import ProviderError  # noqa: E402

ROLES = {"todo": "To Do", "in_progress": "In Progress", "done": "Done"}

ISSUE = {"key": "PROJ-1",
         "fields": {"summary": "epic", "issuetype": {"name": "Epic"},
                    "status": {"name": "To Do"}, "issuelinks": []}}


class StubJira(JiraAdapter):
    """Constructed through the real `__init__` (it only reads env vars), with
    the site pinned so the browse URL doesn't depend on the environment."""

    def __init__(self, have_rest):
        super().__init__(Config(
            {"ticketing": {"provider": "jira", "project": "PROJ"},
             "board": {"roles": dict(ROLES)},
             "queries": {"tier1": "status = \"To Do\""}},
            Path("/nonexistent/.sdlc/config.toml")))
        self.site = "example.atlassian.net"
        self.have_rest = have_rest


class StubRestJira(StubJira):
    """REST mode: serves ISSUE, its transitions, and a canned search."""

    def __init__(self, search_issues=(), search_error=None):
        super().__init__(have_rest=True)
        self.search_issues = list(search_issues)
        self.search_error = search_error
        self.calls: list[tuple] = []
        self.searches: list[dict] = []

    def _jira(self, method, path, body=None):
        self.calls.append((method, path))
        if path.startswith("/rest/api/3/search/jql?"):
            params = {k: v[0] for k, v in parse_qs(urlsplit(path).query).items()}
            self.searches.append(params)
            if self.search_error:
                raise self.search_error
            if params["jql"].startswith("parent"):
                return {"issues": self.search_issues, "isLast": True}
            return {"issues": [ISSUE], "isLast": True}
        if path.endswith("/transitions"):
            return {"transitions": [{"id": "1", "name": "Done",
                                     "to": {"name": "Done"}}]}
        if path.startswith("/rest/api/3/issue/"):
            return ISSUE
        raise AssertionError(f"unexpected REST call {method} {path}")

    def _acli(self, *args):
        raise AssertionError(f"REST mode must not shell out to acli: {args}")


class StubAcliJira(StubJira):
    """acli-only mode: `_acli` returns the JSON text `_acli_json` parses."""

    def __init__(self, search_result=()):
        super().__init__(have_rest=False)
        self.search_result = search_result
        self.calls: list[tuple] = []

    def _acli(self, *args):
        self.calls.append(args)
        if args[:3] == ("jira", "workitem", "view"):
            return json.dumps(ISSUE)
        if args[:3] == ("jira", "workitem", "search"):
            return json.dumps(self.search_result)
        raise AssertionError(f"unexpected acli call {args}")

    def _jira(self, method, path, body=None):
        raise AssertionError(f"acli mode must not call REST: {method} {path}")


def child(key, id_=None):
    return {"id": id_ or key.split("-")[-1], "key": key}


class RestView(unittest.TestCase):
    def test_children_come_from_the_parent_search(self):
        a = StubRestJira([child("PROJ-2"), child("OTHER-7")])
        self.assertEqual(a.view("PROJ-1").children, ["PROJ-2", "OTHER-7"])

    def test_jql_is_exactly_parent_with_no_project_scope(self):
        # The adapter has project = "PROJ"; an Epic's children in OTHER must
        # still be found, so the query must not be ANDed with a project clause.
        a = StubRestJira([child("PROJ-2")])
        a.view("PROJ-1")
        self.assertEqual(len(a.searches), 1)
        self.assertEqual(a.searches[0]["jql"], 'parent = "PROJ-1"')
        self.assertEqual(a.searches[0]["fields"], "key")

    def test_duplicates_collapse_and_own_key_is_dropped(self):
        # Distinct ids so `_search_issues`' own id-dedupe doesn't hide the
        # adapter's: the key-level dedupe must still collapse them.
        a = StubRestJira([child("PROJ-2", "10"), child("PROJ-1", "1"),
                          child("PROJ-2", "11"), child("PROJ-3", "12")])
        self.assertEqual(a.view("PROJ-1").children, ["PROJ-2", "PROJ-3"])

    def test_entries_without_a_key_are_skipped(self):
        a = StubRestJira([{"id": "9"}, child("PROJ-2")])
        self.assertEqual(a.view("PROJ-1").children, ["PROJ-2"])

    def test_no_children_is_an_empty_list(self):
        self.assertEqual(StubRestJira([]).view("PROJ-1").children, [])

    def test_search_error_propagates(self):
        a = StubRestJira(search_error=ProviderError("Jira GET ... -> 500: boom"))
        with self.assertRaises(ProviderError):
            a.view("PROJ-1")

    def test_to_dict_carries_children(self):
        t = StubRestJira([child("PROJ-2")]).view("PROJ-1")
        self.assertEqual(t.to_dict()["children"], ["PROJ-2"])

    def test_list_makes_no_children_query(self):
        a = StubRestJira([child("PROJ-2")])
        tickets = a.list(tier=1)
        self.assertEqual([t.key for t in tickets], ["PROJ-1"])
        self.assertEqual(tickets[0].children, [])
        self.assertFalse(any(s["jql"].startswith("parent") for s in a.searches),
                         a.searches)

    def test_blockers_makes_no_children_query(self):
        a = StubRestJira([child("PROJ-2")])
        a.blockers("PROJ-1")
        self.assertEqual(a.searches, [])


class AcliView(unittest.TestCase):
    def test_children_come_from_the_parent_search(self):
        a = StubAcliJira([child("PROJ-2"), child("OTHER-7")])
        self.assertEqual(a.view("PROJ-1").children, ["PROJ-2", "OTHER-7"])

    def test_search_args_have_limit_and_no_fields(self):
        a = StubAcliJira([child("PROJ-2")])
        a.view("PROJ-1")
        searches = [c for c in a.calls if c[:3] == ("jira", "workitem", "search")]
        self.assertEqual(len(searches), 1)
        args = searches[0]
        # acli answers `--fields key` with null entries, so no --fields at all.
        self.assertNotIn("--fields", args)
        self.assertEqual(args[args.index("--jql") + 1], 'parent = "PROJ-1"')
        self.assertEqual(args[args.index("--limit") + 1], "100")

    def test_null_and_keyless_entries_are_skipped(self):
        a = StubAcliJira([None, child("PROJ-2"), {"fields": {}}, "junk",
                          child("PROJ-2"), child("PROJ-1"), child("PROJ-3")])
        self.assertEqual(a.view("PROJ-1").children, ["PROJ-2", "PROJ-3"])

    def test_wrapped_result_is_tolerated(self):
        a = StubAcliJira({"issues": [child("PROJ-2")]})
        self.assertEqual(a.view("PROJ-1").children, ["PROJ-2"])

    def test_null_result_is_no_children(self):
        self.assertEqual(StubAcliJira(None).view("PROJ-1").children, [])

    def test_blockers_makes_no_children_query(self):
        a = StubAcliJira([child("PROJ-2")])
        a.blockers("PROJ-1")
        self.assertFalse(any(c[:3] == ("jira", "workitem", "search")
                             for c in a.calls), a.calls)


class CreateMakesNoChildrenQuery(unittest.TestCase):
    """A brand-new ticket has no children, so `create` (and `apply --new`,
    which re-reads after setting labels) must not pay for the search."""

    @staticmethod
    def rest():
        a = StubRestJira([child("PROJ-2")])
        acli = []

        def fake_acli(*args):
            acli.append(args)
            if args[:3] == ("jira", "workitem", "create"):
                return json.dumps({"key": "PROJ-1"})
            raise AssertionError(f"unexpected acli call {args}")
        a._acli = fake_acli
        return a

    def test_rest_create(self):
        a = self.rest()
        t = a.create("Story", "new", priority="High", body="text")
        self.assertEqual(t.key, "PROJ-1")
        self.assertEqual(t.children, [])
        self.assertEqual(a.searches, [])

    def test_rest_apply_new_with_labels(self):
        a = self.rest()
        t = a.apply("---\ntype: Story\nlabels: [api]\n---\n# new\n\nbody\n")
        self.assertEqual(t.key, "PROJ-1")
        self.assertIn(("PUT", "/rest/api/3/issue/PROJ-1"), a.calls)
        self.assertEqual(a.searches, [])

    def test_rest_apply_update_still_reports_children(self):
        a = self.rest()
        t = a.apply("# epic\n\nbody\n", key="PROJ-1")
        self.assertEqual(t.children, ["PROJ-2"])

    def test_acli_create(self):
        a = StubAcliJira([child("PROJ-2")])
        real = a._acli

        def fake_acli(*args):
            if args[:3] == ("jira", "workitem", "create"):
                a.calls.append(args)
                return json.dumps({"key": "PROJ-1"})
            return real(*args)
        a._acli = fake_acli
        self.assertEqual(a.create("Story", "new").children, [])
        self.assertFalse(any(c[:3] == ("jira", "workitem", "search")
                             for c in a.calls), a.calls)


if __name__ == "__main__":
    unittest.main()
