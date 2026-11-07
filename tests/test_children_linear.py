"""TKT-19: the Linear adapter fills `Ticket.children` from the issue's native
`children` (sub-issue) connection, on both `list` and `view`.

Offline: `_gql` is stubbed, so no API key or network is needed. Run from the
repo root:

    python3 -m unittest tests.test_children_linear
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


def issue(key, children=..., **extra):
    raw = {"id": f"id-{key}", "identifier": key, "title": key,
           "state": {"name": "Todo", "type": "unstarted"}}
    if children is not ...:
        raw["children"] = children
    raw.update(extra)
    return raw


def nodes(*keys):
    return {"nodes": [{"identifier": k} for k in keys]}


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


class LinearChildren(unittest.TestCase):

    def view(self, raw):
        return StubLinear(make_config(), [raw]).view(raw["identifier"])

    def test_query_requests_children_connection(self):
        a = StubLinear(make_config(), [issue("ENG-1", nodes())])
        a.view("ENG-1")
        a.list(tier=1)
        issue_queries = [q for q in a.queries if "issues(" in q]
        self.assertEqual(len(issue_queries), 2)
        for q in issue_queries:
            self.assertIn("children { nodes { identifier } }", q)

    def test_view_accepts_children_flag_and_ignores_it(self):
        a = StubLinear(make_config(), [issue("ENG-1", nodes("ENG-2"))])
        self.assertEqual(a.view("ENG-1", hierarchy=False).children, ["ENG-2"])

    def test_view_maps_children(self):
        t = self.view(issue("ENG-1", nodes("ENG-2", "ENG-3")))
        self.assertEqual(t.children, ["ENG-2", "ENG-3"])
        self.assertEqual(t.to_dict()["children"], ["ENG-2", "ENG-3"])

    def test_list_maps_children(self):
        a = StubLinear(make_config(), [issue("ENG-1", nodes("ENG-2")),
                                       issue("ENG-2", nodes())])
        by_key = {t.key: t.children for t in a.list(tier=1)}
        self.assertEqual(by_key, {"ENG-1": ["ENG-2"], "ENG-2": []})

    def test_duplicates_collapse_in_order(self):
        t = self.view(issue("ENG-1", nodes("ENG-3", "ENG-2", "ENG-3")))
        self.assertEqual(t.children, ["ENG-3", "ENG-2"])

    def test_own_key_dropped(self):
        t = self.view(issue("ENG-1", nodes("ENG-1", "ENG-2")))
        self.assertEqual(t.children, ["ENG-2"])

    def test_null_connection_is_empty(self):
        self.assertEqual(self.view(issue("ENG-1", None)).children, [])

    def test_missing_connection_is_empty(self):
        self.assertEqual(self.view(issue("ENG-1")).children, [])

    def test_null_nodes_and_null_entries_tolerated(self):
        self.assertEqual(self.view(issue("ENG-1", {"nodes": None})).children, [])
        t = self.view(issue("ENG-1", {"nodes": [None, {"identifier": None},
                                                {"identifier": "ENG-4"}]}))
        self.assertEqual(t.children, ["ENG-4"])


if __name__ == "__main__":
    unittest.main()
