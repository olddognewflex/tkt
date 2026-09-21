"""Regression tests for TKT-15: project scoping must survive the word
"project" and a trailing ORDER BY.

`_full_jql` prepended `project = X AND` only when the substring "project"
was absent from the query, so `summary ~ "project"` silently searched every
project on the site; and prepending in front of a query that is only an
`ORDER BY` clause produces invalid JQL. The project key was interpolated
unquoted. Fully offline -- `_full_jql` and `_split_order_by` are pure string
functions, so nothing touches acli or REST. Run from the repo root:

    python3 -m unittest tests.test_jira_jql
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.jira import JiraAdapter, _split_order_by  # noqa: E402
from core.config import Config  # noqa: E402

ROLES = {"todo": "To Do", "in_progress": "In Progress", "done": "Done"}


class StubJira(JiraAdapter):
    """A constructed adapter with no credentials -- a construction helper.

    `__init__` only reads env vars, so it is safe offline, and `project`
    comes from the config it is handed rather than being pinned here: an
    assignment afterwards would keep these tests green even if the
    config -> adapter wiring broke. Only the pure string methods are
    exercised; calling a verb on this would still reach the network.
    """

    def __init__(self, project="TKT"):
        super().__init__(Config(
            {"ticketing": {"provider": "jira", "project": project},
             "board": {"roles": dict(ROLES)}},
            Path("/nonexistent/.sdlc/config.toml")))


class SplitOrderBy(unittest.TestCase):
    def test_no_order_by(self):
        self.assertEqual(_split_order_by('status = "To Do"'),
                         ('status = "To Do"', ""))

    def test_trailing_order_by(self):
        body, order = _split_order_by('status = "To Do" ORDER BY priority DESC')
        self.assertEqual(body.strip(), 'status = "To Do"')
        self.assertEqual(order, "ORDER BY priority DESC")

    def test_order_by_only(self):
        self.assertEqual(_split_order_by("ORDER BY priority"),
                         ("", "ORDER BY priority"))

    def test_lowercase_and_extra_whitespace(self):
        body, order = _split_order_by("a = 1 order   by b")
        self.assertEqual(body.strip(), "a = 1")
        self.assertEqual(order, "order   by b")

    def test_quoted_literal_is_not_a_clause(self):
        """`summary ~ "order by"` must not be split -- it is a search term."""
        self.assertEqual(_split_order_by('summary ~ "order by"'),
                         ('summary ~ "order by"', ""))

    def test_single_quoted_literal_is_not_a_clause(self):
        self.assertEqual(_split_order_by("summary ~ 'order by'"),
                         ("summary ~ 'order by'", ""))

    def test_escaped_quote_inside_a_literal(self):
        jql = 'summary ~ "a \\" order by b"'
        self.assertEqual(_split_order_by(jql), (jql, ""))

    def test_parenthesised_order_by_is_not_top_level(self):
        jql = "(a = 1 order by b) AND c = 2"
        self.assertEqual(_split_order_by(jql), (jql, ""))

    def test_repeated_order_by_is_deterministic(self):
        """Invalid JQL either way; pins that the split is not arbitrary."""
        body, order = _split_order_by("a = 1 ORDER BY b ORDER BY c")
        self.assertEqual(body.strip(), "a = 1 ORDER BY b")
        self.assertEqual(order, "ORDER BY c")

    def test_word_boundary_rejects_keyword_suffixes(self):
        """The \\b guard, reached unquoted and with real whitespace.

        The earlier version of this test used a quoted literal and an
        unspaced "orderby", so it exercised the quote branch and the `\\s+`
        requirement instead -- both stayed green with `\\b` deleted.
        """
        for jql in ("a = reorder by b", "a = 1 AND xorder by b"):
            with self.subTest(jql=jql):
                self.assertEqual(_split_order_by(jql), (jql, ""))

    def test_unspaced_orderby_is_not_the_keyword(self):
        self.assertEqual(_split_order_by("a = orderby"), ("a = orderby", ""))

    def test_empty(self):
        self.assertEqual(_split_order_by(""), ("", ""))


class FullJql(unittest.TestCase):
    """The four acceptance cases from TKT-15, plus the scope-escape paths."""

    def setUp(self):
        self.a = StubJira()

    def test_plain_query_is_scoped(self):
        self.assertEqual(self.a._full_jql('status = "To Do"'),
                         '(project = "TKT") AND (status = "To Do")')

    def test_query_containing_the_word_project_is_still_scoped(self):
        """The bug: this used to lose scoping and search every project."""
        out = self.a._full_jql('summary ~ "project"')
        self.assertEqual(out, '(project = "TKT") AND (summary ~ "project")')

    def test_order_by_is_re_appended_not_buried(self):
        self.assertEqual(
            self.a._full_jql('status = "To Do" ORDER BY priority DESC'),
            '(project = "TKT") AND (status = "To Do") ORDER BY priority DESC')

    def test_order_by_only_query_stays_valid(self):
        """No empty `AND ()` -- the condition carries the clause alone."""
        self.assertEqual(self.a._full_jql("ORDER BY priority DESC"),
                         'project = "TKT" ORDER BY priority DESC')

    def test_empty_query_is_just_the_condition(self):
        self.assertEqual(self.a._full_jql(""), 'project = "TKT"')

    def test_whitespace_only_query_is_just_the_condition(self):
        self.assertEqual(self.a._full_jql("   "), 'project = "TKT"')

    def test_top_level_or_cannot_escape_the_scope(self):
        """Without parentheses `A AND B OR C` would return other projects."""
        self.assertEqual(self.a._full_jql("a = 1 OR b = 2"),
                         '(project = "TKT") AND (a = 1 OR b = 2)')

    def test_project_key_is_quoted(self):
        self.assertIn('project = "TKT"', self.a._full_jql("a = 1"))

    def test_project_key_needing_escaping_is_quoted_safely(self):
        a = StubJira(project='we"ird')
        self.assertEqual(a._full_jql("a = 1"),
                         '(project = "we\\"ird") AND (a = 1)')

    def test_unscoped_when_no_project_configured(self):
        a = StubJira(project="")
        for jql in ('status = "To Do"', "ORDER BY priority", ""):
            with self.subTest(jql=jql):
                self.assertEqual(a._full_jql(jql), jql)

    def test_multi_key_sort_is_preserved(self):
        """Every [queries] entry in examples/config.jira.toml ends this way."""
        self.assertEqual(
            self.a._full_jql('status = "To Do" ORDER BY priority DESC, created ASC'),
            '(project = "TKT") AND (status = "To Do") '
            'ORDER BY priority DESC, created ASC')

    def test_parenthesised_body_is_wrapped_not_flattened(self):
        self.assertEqual(self.a._full_jql("(a = 1 OR b = 2) ORDER BY c"),
                         '(project = "TKT") AND ((a = 1 OR b = 2)) ORDER BY c')

    def test_cross_project_query_is_narrowed_to_configured_project(self):
        """Deliberate behavior change, recorded so it is not incidental.

        A query written to span projects used to escape scoping through the
        substring loophole this ticket removes; it is now ANDed with the
        configured project and returns only that one. There is no opt-out
        until [query_scopes] (TKT-18) lands. Documented in README's jira
        section and in examples/config.jira.toml.
        """
        self.assertEqual(
            self.a._full_jql('project IN (TKT, OPS) ORDER BY priority DESC'),
            '(project = "TKT") AND (project IN (TKT, OPS)) '
            'ORDER BY priority DESC')

    def test_already_scoped_query_is_scoped_again_not_dropped(self):
        """Double-scoping is redundant but correct; dropping it was the bug."""
        self.assertEqual(self.a._full_jql('project = "TKT" AND a = 1'),
                         '(project = "TKT") AND (project = "TKT" AND a = 1)')


if __name__ == "__main__":
    unittest.main()
