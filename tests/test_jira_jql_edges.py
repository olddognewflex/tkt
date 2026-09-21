"""Adversarial tests for TKT-15: the hand-rolled JQL scanner in
`adapters/jira.py` (`_split_order_by`) and the query composition built on it
(`_full_jql`, `list`).

`_full_jql` prepended `project = X AND` only when the substring "project" was
absent from the lowercased query, so `summary ~ "project"` silently searched
every project on the site; a query that is only an `ORDER BY` clause produced
invalid JQL; and the project key was interpolated unquoted. The fix scans for
the last *top-level* `ORDER BY` -- which means a hand-written scanner that has
to track quote state, escapes and paren depth. These tests attack that scanner
(literals holding quotes, parens, escapes and the words "order by"; unbalanced
and nested parens; near-miss keywords) and then pin the JQL string that
actually reaches each transport, which is where the acceptance criterion lives.

`tests/test_jira_jql.py` covers the four headline acceptance cases; this file
is the edge layer and deliberately does not repeat them.

Fully offline: `_split_order_by` and `_full_jql` are pure string functions, and
`list()` is driven through a StubJira whose `_jira` / `_acli_json` record their
arguments instead of reaching REST or acli. Run from the repo root:

    python3 -m unittest tests.test_jira_jql_edges
"""
import json
import os
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.jira import JiraAdapter, _split_order_by  # noqa: E402
from core.config import Config  # noqa: E402

ROLES = {"todo": "To Do", "in_progress": "In Progress", "done": "Done"}

# A named query whose text contains the word that used to disable scoping, and
# a tier query that is *only* an ORDER BY clause: both TKT-15 failure modes,
# reached through `core.config.Config.query()` the way `list()` reaches them.
QUERIES = {
    "tier1": "ORDER BY priority DESC",
    "mine": 'summary ~ "project" AND assignee = currentUser()',
}

SCOPED_MINE = ('(project = "TKT") AND '
               '(summary ~ "project" AND assignee = currentUser())')
SCOPED_TIER1 = 'project = "TKT" ORDER BY priority DESC'


def make_config(project="TKT"):
    return Config(
        {"ticketing": {"provider": "jira", "project": project},
         "board": {"roles": dict(ROLES)},
         "queries": dict(QUERIES)},
        Path("/nonexistent/.sdlc/config.toml"))


class StubJira(JiraAdapter):
    """A constructed adapter with a fixed project and no credentials.

    `__init__` only reads env vars, so it is safe offline. Both transports are
    replaced by recorders: `rest=True` selects the REST branch of `list()`,
    `rest=False` the acli branch.
    """

    def __init__(self, project="TKT", rest=True):
        super().__init__(make_config(project))
        self.project = project
        self.have_rest = rest
        self.site = "example.atlassian.net"
        self.rest_calls: list[tuple] = []
        self.acli_calls: list[tuple] = []

    def _jira(self, method, path, body=None):
        self.rest_calls.append((method, path, body))
        return {"issues": []}

    def _acli_json(self, *args):
        self.acli_calls.append(args)
        return []

    # -- helpers ---------------------------------------------------------
    def rest_jql(self):
        """The `jql` parameter as it left `urlencode`, decoded back."""
        return parse_qs(urlparse(self.rest_calls[-1][1]).query)["jql"][0]

    def acli_jql(self):
        """The single argv element following `--jql`."""
        args = self.acli_calls[-1]
        return args[args.index("--jql") + 1]


class SplitOrderByQuotes(unittest.TestCase):
    """Quote-state tracking: a literal must be opaque to the scanner."""

    def assert_split(self, jql, body, order):
        got_body, got_order = _split_order_by(jql)
        self.assertEqual((got_body, got_order), (body, order))
        # The function is a split, never a rewrite: nothing may be lost.
        self.assertEqual(got_body + got_order, jql)

    def test_mixed_case_order_by_inside_a_literal_is_not_a_clause(self):
        """`"Order By"` is search text; the real clause follows it."""
        self.assert_split('summary ~ "Order By" ORDER BY rank',
                          'summary ~ "Order By" ', "ORDER BY rank")

    def test_parens_inside_a_literal_do_not_shift_depth(self):
        """A `(` or `)` in a literal must not move paren depth -- if it did,
        the trailing top-level ORDER BY would stop looking top-level."""
        for lit in ("a ( b", "a ) b", "a ) ) b", "a ( ( b"):
            with self.subTest(lit=lit):
                jql = 'summary ~ "%s" ORDER BY rank' % lit
                self.assert_split(jql, 'summary ~ "%s" ' % lit, "ORDER BY rank")

    def test_apostrophe_inside_a_double_quoted_literal(self):
        """A `'` inside `"..."` does not open a second literal."""
        self.assert_split("""summary ~ "it's order by time" ORDER BY rank""",
                          """summary ~ "it's order by time" """,
                          "ORDER BY rank")

    def test_double_quote_inside_a_single_quoted_literal(self):
        self.assert_split("""summary ~ 'say "order by" now' ORDER BY rank""",
                          """summary ~ 'say "order by" now' """,
                          "ORDER BY rank")

    def test_escaped_quote_does_not_end_the_literal(self):
        """`\\"` keeps the literal open, so the `ORDER BY` inside it stays
        search text and only the trailing clause is peeled off."""
        self.assert_split('summary ~ "a \\" order by b" ORDER BY rank',
                          'summary ~ "a \\" order by b" ', "ORDER BY rank")

    def test_literal_ending_in_an_escaped_backslash_still_closes(self):
        """`"...\\\\"` ends at that final quote -- the backslash is escaped,
        not escaping it -- so the clause after the literal is top-level."""
        self.assert_split('summary ~ "ends in a backslash \\\\" ORDER BY rank',
                          'summary ~ "ends in a backslash \\\\" ',
                          "ORDER BY rank")

    def test_unclosed_quote_swallows_the_rest_without_crashing(self):
        """Malformed input (Jira rejects it either way): the literal never
        closes, so nothing is treated as a top-level clause. The contract
        under test is only that the scanner terminates and loses no text."""
        self.assert_split('summary ~ "unclosed ORDER BY rank',
                          'summary ~ "unclosed ORDER BY rank', "")

    def test_trailing_backslash_at_end_of_string_does_not_index_past_it(self):
        """The escape skip advances two characters; on a final backslash that
        lands past the end of the string, which must end the scan, not raise."""
        self.assert_split('summary ~ "a ORDER BY b \\',
                          'summary ~ "a ORDER BY b \\', "")

    def test_doubled_quotes_do_not_desync_the_scanner(self):
        """`""` is close-then-open, so the text between doubled pairs is
        outside a literal -- but the trailing clause is still found."""
        self.assert_split('summary ~ "a ""order by"" b" ORDER BY rank',
                          'summary ~ "a ""order by"" b" ', "ORDER BY rank")


class SplitOrderByParens(unittest.TestCase):
    """Paren-depth tracking: only a depth-0 `ORDER BY` terminates the query."""

    def test_deeply_nested_parens_close_back_to_top_level(self):
        jql = "((((a = 1) AND (b = 2)) OR c = 3)) ORDER BY rank"
        body, order = _split_order_by(jql)
        self.assertEqual(order, "ORDER BY rank")
        self.assertEqual(body, "((((a = 1) AND (b = 2)) OR c = 3)) ")

    def test_nested_order_by_then_a_real_one(self):
        """The bracketed `order by` is search text inside a subclause; only
        the depth-0 clause is peeled off."""
        jql = "(a ~ 'x' AND (b = order by)) ORDER BY rank"
        body, order = _split_order_by(jql)
        self.assertEqual(order, "ORDER BY rank")
        self.assertEqual(body, "(a ~ 'x' AND (b = order by)) ")

    def test_unbalanced_open_paren_suppresses_the_split(self):
        """Malformed input: depth never returns to 0, so no clause is found.
        Pinned to show the scanner terminates and drops nothing."""
        jql = "(a = 1 AND b = 2 ORDER BY rank"
        self.assertEqual(_split_order_by(jql), (jql, ""))

    def test_unbalanced_close_paren_does_not_suppress_later_detection(self):
        """Depth is clamped at 0, so a stray `)` does not poison the rest.

        Without the clamp depth went to -1 and never recovered, so every
        later `ORDER BY` stopped looking top-level and the query came back
        wrapped as `(...) AND (a = 1) AND b = 2 ORDER BY rank)` -- turning
        Jira's "unbalanced parentheses" error into a misleading "ORDER BY
        inside parentheses" one. Jira rejects the input either way; this
        pins the better diagnostic.
        """
        body, order = _split_order_by("a = 1) AND b = 2 ORDER BY rank")
        self.assertEqual(body, "a = 1) AND b = 2 ")
        self.assertEqual(order, "ORDER BY rank")

    def test_close_then_open_leaves_an_unclosed_group(self):
        """`) (` does not cancel out once depth is clamped at 0.

        The `)` clamps to 0 rather than going to -1, so the following `(`
        opens a group that is never closed and the trailing `ORDER BY` is
        correctly not top-level. Characterization of malformed input, not a
        supported shape.
        """
        jql = "a = 1) AND (b = 2 ORDER BY rank"
        self.assertEqual(_split_order_by(jql), (jql, ""))


class SplitOrderByDetection(unittest.TestCase):
    """Where the keyword itself starts and stops being the keyword."""

    def test_whitespace_between_the_two_words(self):
        for gap in (" ", "  ", "\t", "\n", " \n\t "):
            with self.subTest(gap=repr(gap)):
                jql = "a = 1 ORDER%sBY rank" % gap
                body, order = _split_order_by(jql)
                self.assertEqual(body, "a = 1 ")
                self.assertEqual(order, "ORDER%sBY rank" % gap)

    def test_no_whitespace_between_the_two_words_is_not_the_keyword(self):
        """`ORDERBY` is not JQL; `\\s+` requires at least one separator."""
        self.assertEqual(_split_order_by("a = 1 ORDERBY rank"),
                         ("a = 1 ORDERBY rank", ""))

    def test_near_miss_words_are_not_the_keyword(self):
        """The `\\b` guards on both ends: a keyword glued to other letters."""
        for jql in ("a = 1 reorder by hand", "a = 1 AND b = disorder by x",
                    "a ~ preordering by x"):
            with self.subTest(jql=jql):
                self.assertEqual(_split_order_by(jql), (jql, ""))

    def test_bare_keyword_with_no_sort_field(self):
        self.assertEqual(_split_order_by("ORDER BY"), ("", "ORDER BY"))

    def test_surrounding_whitespace_is_preserved_by_the_split(self):
        body, order = _split_order_by("   a = 1   ORDER BY rank   ")
        self.assertEqual(body, "   a = 1   ")
        self.assertEqual(order, "ORDER BY rank   ")

    def test_text_after_the_clause_belongs_to_the_clause(self):
        """"Last top-level wins" means everything from the final keyword on is
        the clause -- there is no valid JQL with conditions after ORDER BY, so
        a malformed query keeps its own tail rather than having it re-scoped."""
        body, order = _split_order_by("a = 1 ORDER BY rank AND b = 2")
        self.assertEqual(body, "a = 1 ")
        self.assertEqual(order, "ORDER BY rank AND b = 2")

    def test_lowercase_clause_after_a_quoted_uppercase_decoy(self):
        """Case-insensitive matching must not make the decoy in the literal
        win over the real, lowercase clause that follows it."""
        body, order = _split_order_by('summary ~ "ORDER BY" order by rank')
        self.assertEqual(body, 'summary ~ "ORDER BY" ')
        self.assertEqual(order, "order by rank")


class FullJqlEdges(unittest.TestCase):
    """Composition: scoping must survive every shape the scanner returns."""

    def setUp(self):
        self.a = StubJira()

    def test_order_by_only_query_with_padding(self):
        """No stray `AND ()` and no leading/trailing space in the output."""
        self.assertEqual(self.a._full_jql("   ORDER BY rank   "),
                         'project = "TKT" ORDER BY rank')

    def test_whitespace_only_query_of_tabs_and_newlines(self):
        self.assertEqual(self.a._full_jql("\t\n  "), 'project = "TKT"')

    def test_or_and_order_by_together_stay_scoped(self):
        """The parens must wrap the whole disjunction, not just its first arm,
        or the `OR` returns issues from every project."""
        self.assertEqual(
            self.a._full_jql('a = 1 OR b = 2 ORDER BY rank DESC'),
            '(project = "TKT") AND (a = 1 OR b = 2) ORDER BY rank DESC')

    def test_word_project_inside_a_sort_field_is_still_scoped(self):
        """`ORDER BY project` and a `projects` literal both contain the
        substring the old guard tested for."""
        self.assertEqual(
            self.a._full_jql("text ~ 'projects' ORDER BY project"),
            '(project = "TKT") AND (text ~ \'projects\') ORDER BY project')

    def test_quoted_literal_holding_or_project_cannot_escape_scope(self):
        self.assertEqual(
            self.a._full_jql('summary ~ "x) OR project = FOO AND (y"'),
            '(project = "TKT") AND (summary ~ "x) OR project = FOO AND (y")')

    def test_project_key_with_a_quote_cannot_inject_a_second_clause(self):
        """An injected `" OR project = "OTHER` must land inside the literal:
        the emitted condition parses back to the key verbatim."""
        a = StubJira(project='X" OR project = "Y')
        out = a._full_jql("a = 1")
        self.assertEqual(
            out, '(project = "X\\" OR project = \\"Y") AND (a = 1)')
        literal = out[len('(project = '):out.index(") AND")]
        self.assertEqual(json.loads(literal), 'X" OR project = "Y')

    def test_project_key_with_a_backslash_is_escaped(self):
        """A trailing backslash would otherwise escape the closing quote."""
        a = StubJira(project="back\\slash")
        out = a._full_jql("a = 1")
        self.assertEqual(out, '(project = "back\\\\slash") AND (a = 1)')
        literal = out[len('(project = '):out.index(") AND")]
        self.assertEqual(json.loads(literal), "back\\slash")

    def test_unclosed_quote_query_is_still_scoped(self):
        """Malformed input stays malformed -- Jira rejects it -- but it must
        still never be sent unscoped."""
        out = self.a._full_jql('summary ~ "unclosed ORDER BY rank')
        self.assertTrue(out.startswith('(project = "TKT") AND ('), out)

    def test_unbalanced_paren_query_is_still_scoped(self):
        out = self.a._full_jql("status = Open) ORDER BY rank")
        self.assertTrue(out.startswith('(project = "TKT") AND ('), out)


class ListTransportSeam(unittest.TestCase):
    """What actually reaches REST and acli -- the acceptance level.

    Both named-query and tier lookups go through `core.config.Config.query()`,
    so both are exercised here.
    """

    def test_rest_named_query_is_scoped(self):
        a = StubJira(rest=True)
        self.assertEqual(a.list(query="mine"), [])
        self.assertEqual(a.rest_jql(), SCOPED_MINE)

    def test_rest_tier_query_that_is_only_order_by_is_scoped(self):
        a = StubJira(rest=True)
        a.list(tier=1)
        self.assertEqual(a.rest_jql(), SCOPED_TIER1)

    def test_rest_request_is_a_get_on_the_search_endpoint(self):
        a = StubJira(rest=True)
        a.list(tier=1)
        method, path, body = a.rest_calls[-1]
        self.assertEqual((method, body), ("GET", None))
        self.assertEqual(urlparse(path).path, "/rest/api/3/search/jql")

    def test_rest_percent_encodes_the_quoted_key(self):
        """The raw query string must carry no bare `"` -- if it did, the fix's
        quoting would break the URL rather than the JQL."""
        a = StubJira(rest=True)
        a.list(query="mine")
        raw = urlparse(a.rest_calls[-1][1]).query
        self.assertNotIn('"', raw)
        self.assertIn("project", parse_qs(raw)["jql"][0])

    def test_acli_named_query_is_scoped(self):
        a = StubJira(rest=False)
        self.assertEqual(a.list(query="mine"), [])
        self.assertEqual(a.acli_jql(), SCOPED_MINE)

    def test_acli_tier_query_that_is_only_order_by_is_scoped(self):
        a = StubJira(rest=False)
        a.list(tier=1)
        self.assertEqual(a.acli_jql(), SCOPED_TIER1)

    def test_acli_gets_the_jql_as_one_argv_element(self):
        """`_acli` execs a list, so the JQL is never shell-parsed; a key
        containing a quote or a space stays a single argument."""
        a = StubJira(project='O DD"', rest=False)
        a.list(query="mine")
        args = a.acli_calls[-1]
        self.assertEqual(args[:4], ("jira", "workitem", "search", "--jql"))
        self.assertEqual(args[4], '(project = "O DD\\"") AND (%s)'
                         % QUERIES["mine"])

    def test_unscoped_when_no_project_is_configured(self):
        """No project means no condition -- and no stray parens either."""
        a = StubJira(project="", rest=True)
        a.list(query="mine")
        self.assertEqual(a.rest_jql(), QUERIES["mine"])


if __name__ == "__main__":
    unittest.main()
