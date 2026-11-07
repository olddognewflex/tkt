"""TKT-72: Jira tickets report their direct parent.

The parent (an Epic, or the Story a sub-task sits under) arrives on the issue
itself as `fields.parent`, so unlike `children` it costs no extra call. `view`
already fetches `*all`; `list` must ask for `parent` explicitly in both its
REST `fields` and its acli `--fields`, so list results carry it too. And
`view(hierarchy=False)` still carries it: only the children search is skipped.

Fully offline: the REST path is stubbed at `_jira`, the acli path at `_acli`.
Run from the repo root:

    python3 -m unittest tests.test_parent_jira
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

ROLES = {"todo": "To Do", "in_progress": "In Progress", "done": "Done"}


def issue(key="PROJ-2", parent="PROJ-1", parent_shape="object"):
    """An issue as Jira returns it, with `fields.parent` spelled one way."""
    fields = {"summary": "story", "issuetype": {"name": "Story"},
              "status": {"name": "To Do"}, "issuelinks": []}
    if parent_shape == "object":
        fields["parent"] = {"id": "10001", "key": parent,
                            "fields": {"summary": "epic"}}
    elif parent_shape == "null":
        fields["parent"] = None
    elif parent_shape == "keyless":
        fields["parent"] = {"id": "10001"}
    # "missing": no `parent` key at all
    return {"id": "10002", "key": key, "fields": fields}


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
    """REST mode: serves `self.issue` to `view` and to the list search; the
    children search (`parent = ...`) finds nothing."""

    def __init__(self, issue_=None):
        super().__init__(have_rest=True)
        self.issue = issue_ or issue()
        self.searches: list[dict] = []

    def _jira(self, method, path, body=None):
        if path.startswith("/rest/api/3/search/jql?"):
            params = {k: v[0] for k, v in parse_qs(urlsplit(path).query).items()}
            self.searches.append(params)
            if params["jql"].startswith("parent"):
                return {"issues": [], "isLast": True}
            return {"issues": [self.issue], "isLast": True}
        if path.endswith("/transitions"):
            return {"transitions": []}
        if path.startswith("/rest/api/3/issue/"):
            return self.issue
        raise AssertionError(f"unexpected REST call {method} {path}")

    def _acli(self, *args):
        raise AssertionError(f"REST mode must not shell out to acli: {args}")


class StubAcliJira(StubJira):
    """acli-only mode: `_acli` returns the JSON text `_acli_json` parses."""

    def __init__(self, issue_=None):
        super().__init__(have_rest=False)
        self.issue = issue_ or issue()
        self.calls: list[tuple] = []

    def _acli(self, *args):
        self.calls.append(args)
        if args[:3] == ("jira", "workitem", "view"):
            return json.dumps(self.issue)
        if args[:3] == ("jira", "workitem", "search"):
            jql = args[args.index("--jql") + 1]
            return json.dumps([] if jql.startswith("parent") else [self.issue])
        raise AssertionError(f"unexpected acli call {args}")

    def _jira(self, method, path, body=None):
        raise AssertionError(f"acli mode must not call REST: {method} {path}")

    def searches(self):
        return [c for c in self.calls if c[:3] == ("jira", "workitem", "search")]


class ToTicket(unittest.TestCase):
    def to_ticket(self, **kw):
        return StubJira(have_rest=True)._to_ticket(issue(**kw))

    def test_maps_fields_parent_key(self):
        self.assertEqual(self.to_ticket(parent="PROJ-1").parent, "PROJ-1")

    def test_parent_in_another_project_is_kept(self):
        self.assertEqual(self.to_ticket(parent="EPIC-9").parent, "EPIC-9")

    def test_missing_null_or_keyless_parent_is_empty(self):
        for shape in ("missing", "null", "keyless"):
            with self.subTest(shape=shape):
                self.assertEqual(self.to_ticket(parent_shape=shape).parent, "")

    def test_own_key_is_never_the_parent(self):
        self.assertEqual(self.to_ticket(key="PROJ-2", parent="PROJ-2").parent, "")

    def test_to_dict_carries_parent(self):
        self.assertEqual(self.to_ticket().to_dict()["parent"], "PROJ-1")
        self.assertEqual(self.to_ticket(parent_shape="missing")
                         .to_dict()["parent"], "")


class RestList(unittest.TestCase):
    def test_search_requests_parent_and_maps_it(self):
        a = StubRestJira()
        (t,) = a.list(tier=1)
        self.assertEqual(t.parent, "PROJ-1")
        (search,) = a.searches
        self.assertIn("parent", search["fields"].split(","))


class AcliList(unittest.TestCase):
    def test_search_requests_parent_and_maps_it(self):
        a = StubAcliJira()
        (t,) = a.list(tier=1)
        self.assertEqual(t.parent, "PROJ-1")
        (search,) = a.searches()
        self.assertIn("parent", search[search.index("--fields") + 1].split(","))


class View(unittest.TestCase):
    def test_rest_view_carries_parent(self):
        self.assertEqual(StubRestJira().view("PROJ-2").parent, "PROJ-1")

    def test_acli_view_carries_parent(self):
        self.assertEqual(StubAcliJira().view("PROJ-2").parent, "PROJ-1")

    def test_rest_hierarchy_false_still_has_parent_but_no_children_search(self):
        a = StubRestJira()
        t = a.view("PROJ-2", hierarchy=False)
        self.assertEqual(t.parent, "PROJ-1")
        self.assertEqual(a.searches, [])

    def test_acli_hierarchy_false_still_has_parent_but_no_children_search(self):
        a = StubAcliJira()
        t = a.view("PROJ-2", hierarchy=False)
        self.assertEqual(t.parent, "PROJ-1")
        self.assertEqual(a.searches(), [])


if __name__ == "__main__":
    unittest.main()
