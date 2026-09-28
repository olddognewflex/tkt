"""Regression tests for TKT-14: Jira `list` must return every matching issue.

`list` asked for `maxResults=25` in REST mode and `--limit 25` in acli mode
and read one page, so a tier query with more than 25 candidates silently
dropped the rest and skewed `select-ticket` and `check-blockers`.
`_search_issues` now pages until exhausted under both response contracts:
token-based (`nextPageToken`) and legacy offset-based
(`startAt`/`maxResults`/`total`/`isLast`). Fully offline -- the REST
transport is stubbed. Run from the repo root:

    python3 -m unittest tests.test_jira_search
"""
import os
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.jira import JiraAdapter  # noqa: E402
from core.errors import ProviderError  # noqa: E402
from core.config import Config  # noqa: E402

ROLES = {"todo": "To Do", "in_progress": "In Progress", "done": "Done"}


def issue(n):
    return {"key": f"TKT-{n}", "fields": {"summary": f"s{n}"}}


class StubJira(JiraAdapter):
    """REST-mode adapter whose search calls are answered by `pages`, a
    function from the request's query params to the response dict."""

    def __init__(self, pages):
        super().__init__(Config(
            {"ticketing": {"provider": "jira", "project": "TKT"},
             "board": {"roles": dict(ROLES)},
             "queries": {"tier1": 'status = "To Do"'}},
            Path("/nonexistent/.sdlc/config.toml")))
        # Real REST credentials are never read; setting `site` also keeps
        # `_site_for_url` from shelling out to acli for browse links.
        self.site = "example.atlassian.net"
        self.have_rest = True
        self.pages = pages
        self.calls: list[dict] = []

    def _jira(self, method, path, body=None):
        assert path.startswith("/rest/api/3/search/jql?"), path
        params = {k: v[0] for k, v in parse_qs(urlsplit(path).query).items()}
        self.calls.append(params)
        if len(self.calls) > 50:
            raise AssertionError("search did not terminate")
        return self.pages(params)


def token_pages(total, size=100):
    """Token contract: the enhanced /search/jql endpoint."""
    def pages(p):
        start = int(p.get("nextPageToken", "0"))
        rows = [issue(n) for n in range(start, min(start + size, total))]
        end = start + len(rows)
        out = {"issues": rows, "isLast": end >= total}
        if end < total:
            out["nextPageToken"] = str(end)
        return out
    return pages


def offset_pages(total, size=100, with_total=True, with_is_last=True):
    """Legacy contract: startAt / maxResults / total / isLast."""
    def pages(p):
        start = int(p.get("startAt", "0"))
        rows = [issue(n) for n in range(start, min(start + size, total))]
        out = {"issues": rows, "startAt": start, "maxResults": size}
        if with_total:
            out["total"] = total
        if with_is_last:
            out["isLast"] = start + len(rows) >= total
        return out
    return pages


class TokenContract(unittest.TestCase):

    def test_two_pages_yield_every_issue(self):
        a = StubJira(token_pages(130))
        keys = [t.key for t in a.list(tier="1")]
        self.assertEqual(keys, [f"TKT-{n}" for n in range(130)])
        self.assertEqual(len(a.calls), 2)
        self.assertNotIn("nextPageToken", a.calls[0])
        self.assertEqual(a.calls[1]["nextPageToken"], "100")

    def test_more_than_25_is_not_capped(self):
        a = StubJira(token_pages(40, size=25))
        self.assertEqual(len(a.list(tier="1")), 40)

    def test_single_page(self):
        a = StubJira(token_pages(3))
        self.assertEqual(len(a.list(tier="1")), 3)
        self.assertEqual(len(a.calls), 1)

    def test_repeated_token_raises(self):
        a = StubJira(lambda p: {"issues": [issue(1)], "nextPageToken": "same"})
        with self.assertRaises(ProviderError):
            a.list(tier="1")
        self.assertEqual(len(a.calls), 2)

    def test_token_cycle_raises_instead_of_duplicating(self):
        """A -> B -> A: the third page repeats the first."""
        seq = {None: ("A", [1, 2]), "A": ("B", [3]), "B": ("A", [1, 2])}
        def pages(p):
            nxt, ns = seq[p.get("nextPageToken")]
            return {"issues": [issue(n) for n in ns], "nextPageToken": nxt}
        a = StubJira(pages)
        with self.assertRaises(ProviderError):
            a.list(tier="1")
        self.assertEqual(len(a.calls), 3)

    def test_fresh_token_but_same_rows_raises(self):
        n = iter(range(100))
        a = StubJira(lambda p: {"issues": [issue(1)], "nextPageToken": str(next(n))})
        with self.assertRaises(ProviderError):
            a.list(tier="1")

    def test_overlapping_pages_are_deduped(self):
        seq = {None: ("A", [1, 2]), "A": (None, [2, 3])}
        def pages(p):
            nxt, ns = seq[p.get("nextPageToken")]
            out = {"issues": [issue(n) for n in ns], "isLast": nxt is None}
            if nxt:
                out["nextPageToken"] = nxt
            return out
        a = StubJira(pages)
        self.assertEqual([t.key for t in a.list(tier="1")],
                         ["TKT-1", "TKT-2", "TKT-3"])

    def test_requested_fields_and_page_size(self):
        a = StubJira(token_pages(1))
        a.list(tier="1")
        self.assertIn("issuelinks", a.calls[0]["fields"])
        self.assertEqual(a.calls[0]["maxResults"], "100")


class OffsetContract(unittest.TestCase):

    def test_two_pages_yield_every_issue(self):
        a = StubJira(offset_pages(130))
        keys = [t.key for t in a.list(tier="1")]
        self.assertEqual(keys, [f"TKT-{n}" for n in range(130)])
        self.assertEqual([c.get("startAt") for c in a.calls], [None, "100"])

    def test_server_capped_page_size(self):
        a = StubJira(offset_pages(130, size=50))
        self.assertEqual(len(a.list(tier="1")), 130)
        self.assertEqual([c.get("startAt") for c in a.calls], [None, "50", "100"])

    def test_total_only(self):
        a = StubJira(offset_pages(130, with_is_last=False))
        self.assertEqual(len(a.list(tier="1")), 130)

    def test_is_last_only(self):
        a = StubJira(offset_pages(130, with_total=False))
        self.assertEqual(len(a.list(tier="1")), 130)

    def test_neither_total_nor_is_last_stops_on_short_page(self):
        a = StubJira(offset_pages(130, with_total=False, with_is_last=False))
        self.assertEqual(len(a.list(tier="1")), 130)
        self.assertEqual(len(a.calls), 2)

    def test_server_ignoring_startat_with_fixed_echo_raises(self):
        a = StubJira(lambda p: {"issues": [issue(n) for n in range(100)],
                                "startAt": 5, "maxResults": 100, "total": 1000})
        with self.assertRaises(ProviderError):
            a.list(tier="1")
        self.assertEqual(len(a.calls), 2)
        self.assertEqual(a.calls[1]["startAt"], "100")

    def test_server_ignoring_startat_without_total_raises(self):
        a = StubJira(lambda p: {"issues": [issue(n) for n in range(100)],
                                "startAt": 0, "maxResults": 100, "isLast": False})
        with self.assertRaises(ProviderError):
            a.list(tier="1")

    def test_malformed_total_is_a_provider_error(self):
        a = StubJira(lambda p: {"issues": [issue(1)], "startAt": 0, "total": "abc"})
        with self.assertRaises(ProviderError):
            a.list(tier="1")

    def test_exact_multiple_ends_on_total(self):
        a = StubJira(offset_pages(200))
        self.assertEqual(len(a.list(tier="1")), 200)
        self.assertEqual(len(a.calls), 2)


class NeitherContract(unittest.TestCase):

    def test_bare_page_stops_after_one_call(self):
        a = StubJira(lambda p: {"issues": [issue(1), issue(2)]})
        self.assertEqual(len(a.list(tier="1")), 2)
        self.assertEqual(len(a.calls), 1)

    def test_empty_page(self):
        a = StubJira(lambda p: {"issues": []})
        self.assertEqual(a.list(tier="1"), [])

    def test_is_last_false_without_continuation_raises(self):
        a = StubJira(lambda p: {"issues": [issue(1)], "isLast": False})
        with self.assertRaises(ProviderError):
            a.list(tier="1")

    def test_page_cap_raises(self):
        n = iter(range(10**6))
        def pages(p):
            k = next(n)
            return {"issues": [issue(k)], "nextPageToken": str(k)}
        a = StubJira(pages)
        a._SEARCH_MAX_PAGES = 5
        with self.assertRaises(ProviderError):
            a.list(tier="1")
        self.assertEqual(len(a.calls), 5)

    def test_empty_page_with_token_stops(self):
        a = StubJira(lambda p: {"issues": [], "nextPageToken": "x"})
        self.assertEqual(a.list(tier="1"), [])
        self.assertEqual(len(a.calls), 1)


class AcliSearch(unittest.TestCase):

    def test_acli_search_fetches_every_page(self):
        a = StubJira(None)
        a.have_rest = False
        seen = []
        a._acli_json = lambda *args: seen.append(args) or []
        a.list(tier="1")
        self.assertIn("--paginate", seen[0])
        self.assertNotIn("--limit", seen[0])


if __name__ == "__main__":
    unittest.main()
