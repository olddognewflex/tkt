"""TKT-72: the Linear adapter fills `Ticket.parent` from the issue's native
`parent` field, on both `list` and `view`.

Offline: `_gql` is stubbed, so no API key or network is needed. Run from the
repo root:

    python3 -m unittest tests.test_parent_linear
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.linear import LinearAdapter  # noqa: E402
from core.config import Config  # noqa: E402


def make_config():
    return Config({"ticketing": {"provider": "linear"},
                   "linear": {"team_key": "ENG"},
                   "board": {"roles": {"todo": "Todo", "done": "Done"}},
                   "queries": {"tier1": 'status = "Todo"'}},
                  Path("/nonexistent/.sdlc/config.toml"))


def issue(key, parent=..., **extra):
    raw = {"id": f"id-{key}", "identifier": key, "title": key,
           "state": {"name": "Todo", "type": "unstarted"}}
    if parent is not ...:
        raw["parent"] = parent
    raw.update(extra)
    return raw


class StubLinear(LinearAdapter):
    def __init__(self, config, issues):
        super().__init__(config)
        self.issues = issues
        self.queries = []

    def _gql(self, query, variables=None):
        self.queries.append(query)
        if "workflowStates" in query:
            return {"workflowStates": {"nodes": [{"id": "s1", "name": "Todo"}]}}
        if "issues(" in query:
            if variables and "n" in variables and "k" in variables \
                    and isinstance(variables["n"], int) and "first:1" in query:
                want = f"{variables['k']}-{variables['n']}"
                return {"issues": {"nodes": [i for i in self.issues
                                             if i["identifier"] == want]}}
            return {"issues": {"nodes": list(self.issues)}}
        raise AssertionError(f"unexpected query: {query}")

    def whoami(self):
        return "me"


class LinearParent(unittest.TestCase):

    def view(self, raw):
        return StubLinear(make_config(), [raw]).view(raw["identifier"])

    def assert_control(self):
        # Paired with each "reads as none" case so the test also fails if
        # parent mapping were removed (which would read "" everywhere).
        self.assertEqual(self.view(issue("ENG-9", parent={"identifier": "ENG-8"})).parent,
                         "ENG-8")

    def test_query_requests_parent(self):
        a = StubLinear(make_config(), [issue("ENG-1")])
        a.view("ENG-1")
        a.list(tier=1)
        issue_queries = [q for q in a.queries if "issues(" in q]
        self.assertEqual(len(issue_queries), 2)
        for q in issue_queries:
            self.assertIn("parent { identifier }", q)

    def test_view_maps_parent(self):
        t = self.view(issue("ENG-2", parent={"identifier": "ENG-1"}))
        self.assertEqual(t.parent, "ENG-1")
        self.assertEqual(t.to_dict()["parent"], "ENG-1")

    def test_list_maps_parent(self):
        a = StubLinear(make_config(), [issue("ENG-1", parent=None),
                                       issue("ENG-2", parent={"identifier": "ENG-1"})])
        by_key = {t.key: t.parent for t in a.list(tier=1)}
        self.assertEqual(by_key, {"ENG-1": "", "ENG-2": "ENG-1"})

    def test_null_parent_is_empty(self):
        self.assertEqual(self.view(issue("ENG-1", parent=None)).parent, "")
        self.assert_control()

    def test_missing_parent_is_empty(self):
        self.assertEqual(self.view(issue("ENG-1")).parent, "")
        self.assert_control()

    def test_parent_without_identifier_is_empty(self):
        self.assertEqual(self.view(issue("ENG-1", parent={})).parent, "")
        self.assertEqual(self.view(issue("ENG-1", parent={"identifier": None})).parent, "")
        self.assert_control()

    def test_own_key_is_empty(self):
        self.assertEqual(self.view(issue("ENG-1", parent={"identifier": "ENG-1"})).parent, "")
        self.assert_control()


if __name__ == "__main__":
    unittest.main()
