"""Tests for the client-side JQL subset in core/query.py (TKT-56).

`ORDER BY priority` used to sort priority names as text, so on the markdown,
Linear and openkanban backends Medium outranked High and `select-ticket`
auto-selected the wrong ticket. Offline. Run from the repo root:

    python3 -m unittest tests.test_query
"""
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import DEFAULT_PRIORITIES, Config  # noqa: E402
from core.errors import ConfigError  # noqa: E402
from core.query import JqlSubset  # noqa: E402
from core.registry import get_adapter  # noqa: E402
from core.schema import Ticket  # noqa: E402


def t(key, priority, status="To Do", assignee="me"):
    return Ticket(key=key, type="Story", summary=key, description="", status=status,
                  priority=priority, assignee=assignee)


def keys(tickets):
    return [x.key for x in tickets]


MIXED = [t("M", "Medium"), t("H", "High"), t("L", "Low"), t("X", "Highest"), t("W", "Lowest")]


class TestPriorityRank(unittest.TestCase):
    def test_desc_is_highest_first(self):
        """The reported bug: text order put Medium ahead of High."""
        got = JqlSubset("status = \"To Do\" ORDER BY priority DESC", "me").run(MIXED)
        self.assertEqual(keys(got), ["X", "H", "M", "L", "W"])

    def test_asc_is_lowest_first(self):
        got = JqlSubset("status = \"To Do\" ORDER BY priority ASC", "me").run(MIXED)
        self.assertEqual(keys(got), ["W", "L", "M", "H", "X"])

    def test_bare_order_by_is_ascending(self):
        got = JqlSubset("status = \"To Do\" ORDER BY priority", "me").run(MIXED)
        self.assertEqual(keys(got), ["W", "L", "M", "H", "X"])

    def test_case_is_ignored(self):
        # The shared board already carries a lowercase `medium`.
        rows = [t("lo", "low"), t("MED", "MEDIUM"), t("hi", "high")]
        got = JqlSubset("status = \"To Do\" ORDER BY priority DESC", "me").run(rows)
        self.assertEqual(keys(got), ["hi", "MED", "lo"])

    def test_unknown_and_empty_sort_last_both_ways(self):
        rows = [t("U", "Critical"), t("E", ""), t("H", "High"), t("L", "Low")]
        desc = keys(JqlSubset("status = \"To Do\" ORDER BY priority DESC", "me").run(rows))
        asc = keys(JqlSubset("status = \"To Do\" ORDER BY priority ASC", "me").run(rows))
        self.assertEqual(desc[:2], ["H", "L"])
        self.assertEqual(asc[:2], ["L", "H"])
        self.assertEqual(set(desc[2:]), {"U", "E"})
        self.assertEqual(set(asc[2:]), {"U", "E"})

    def test_configured_order_wins(self):
        order = ["Urgent", "High", "Medium", "Low", "No priority"]
        rows = [t("N", "No priority"), t("M", "Medium"), t("U", "Urgent"), t("H", "High")]
        got = JqlSubset("status = \"To Do\" ORDER BY priority DESC", "me", order).run(rows)
        self.assertEqual(keys(got), ["U", "H", "M", "N"])

    def test_blank_entry_in_a_configured_list_is_ignored(self):
        rows = [t("E", ""), t("H", "High"), t("L", "Low")]
        got = JqlSubset("status = \"To Do\" ORDER BY priority DESC", "me", ["", "High", "Low"]).run(rows)
        self.assertEqual(keys(got), ["H", "L", "E"])

    def test_default_is_the_config_default(self):
        got = JqlSubset("status = \"To Do\" ORDER BY priority DESC", "me").run(MIXED)
        want = [p for p in DEFAULT_PRIORITIES]
        self.assertEqual([x.priority for x in got], want)


class TestMultiKeyAndOtherFields(unittest.TestCase):
    def test_secondary_key_breaks_ties(self):
        rows = [t("B-2", "High"), t("A-9", "Medium"), t("A-1", "High")]
        got = JqlSubset("status = \"To Do\" ORDER BY priority DESC, key ASC", "me").run(rows)
        self.assertEqual(keys(got), ["A-1", "B-2", "A-9"])

    def test_priority_as_secondary_key(self):
        rows = [t("B", "Low"), t("A", "Low"), t("C", "High")]
        got = JqlSubset("status = \"To Do\" ORDER BY status ASC, priority DESC", "me").run(rows)
        self.assertEqual(keys(got), ["C", "B", "A"])   # stable within equal priority

    def test_other_fields_keep_text_order(self):
        rows = [t("b", "High"), t("c", "High"), t("a", "High")]
        got = JqlSubset("status = \"To Do\" ORDER BY key DESC", "me").run(rows)
        self.assertEqual(keys(got), ["c", "b", "a"])

    def test_filtering_is_unchanged(self):
        rows = [t("A", "High"), t("B", "High", status="Done"), t("C", "Low", assignee="other")]
        got = JqlSubset("status = \"To Do\" AND assignee = currentUser()", "me").run(rows)
        self.assertEqual(keys(got), ["A"])


def d(key, **dates):
    return Ticket(key=key, status="To Do", **dates)


class TestOrderByFieldShapes(unittest.TestCase):
    """TKT-58: four ways ORDER BY mis-sorted or did nothing."""

    def order(self, jql, rows):
        return keys(JqlSubset(jql, "me").run(rows))

    # 1. a query that is only an ORDER BY clause
    def test_leading_order_by_sorts(self):
        rows = [d("B"), d("C"), d("A")]
        self.assertEqual(self.order("ORDER BY key ASC", rows), ["A", "B", "C"])

    def test_leading_order_by_ignores_case_and_spacing(self):
        rows = [d("B"), d("C"), d("A")]
        self.assertEqual(self.order("  order   by key desc", rows), ["C", "B", "A"])

    def test_leading_order_by_keeps_every_ticket(self):
        rows = [d("B", due="2026-01-02"), d("A")]
        self.assertEqual(sorted(self.order("ORDER BY due", rows)), ["A", "B"])

    def test_order_word_inside_a_field_name_is_not_a_clause(self):
        """`border` ends in ORDER; only a whole-word ORDER BY splits."""
        rows = [d("A"), d("B")]
        self.assertEqual(self.order("border by != x", rows), ["A", "B"])

    # 2. created / updated: no client-side backend fills them
    def test_created_and_updated_are_reported(self):
        for field in ("created", "updated", "CREATED"):
            with self.subTest(field=field):
                with self.assertRaises(ConfigError) as cm:
                    JqlSubset(f"status = \"To Do\" ORDER BY priority DESC, {field} ASC", "me")
                self.assertIn(field.lower(), str(cm.exception))

    def test_jira_only_fields_are_reported(self):
        """Filled by Jira alone, so a client-side sort on them is a no-op."""
        for field in ("project", "project_name", "assignee_id", "reporter", "reporter_id"):
            with self.subTest(field=field):
                with self.assertRaises(ConfigError) as cm:
                    JqlSubset(f"ORDER BY {field} DESC", "me")
                self.assertIn("does not record", str(cm.exception))

    def test_backend_gaps_are_reported(self):
        with self.assertRaises(ConfigError) as cm:
            JqlSubset("ORDER BY Due DESC", "me", unsupplied=("due",))
        self.assertIn("does not record due", str(cm.exception))
        # the same field is fine where the backend fills it
        JqlSubset("ORDER BY due DESC", "me")

    def test_unknown_field_error_lists_only_usable_fields(self):
        with self.assertRaises(ConfigError) as cm:
            JqlSubset("ORDER BY rank", "me", unsupplied=("due",))
        listed = set(str(cm.exception).split("one of: ")[1].rstrip(")").split(", "))
        self.assertFalse(listed & {"created", "reporter", "reporter_id", "due"})
        self.assertIn("key", listed)

    def test_order_by_inside_a_quoted_value_is_not_a_clause(self):
        rows = [Ticket(key="A", summary="sort order by phone"), Ticket(key="B", summary="x")]
        for jql in ('summary = "sort order by phone"', "summary = 'sort order by phone'"):
            with self.subTest(jql=jql):
                self.assertEqual(self.order(jql, rows), ["A"])
        got = self.order('summary != "it\\"s order by" ORDER BY key DESC', rows)
        self.assertEqual(got, ["B", "A"])

    def test_apostrophe_in_an_unquoted_value_keeps_the_order_by(self):
        q = JqlSubset("assignee = O'Brien ORDER BY key DESC", "me")
        self.assertEqual(q.order, [("key", True)])
        self.assertEqual(q.clauses, ["assignee = O'Brien"])

    def test_unclosed_quote_hiding_the_order_by_is_reported(self):
        with self.assertRaises(ConfigError) as cm:
            JqlSubset('summary = "unclosed ORDER BY key', "me")
        self.assertIn("unbalanced quote", str(cm.exception))

    def test_unknown_sort_field_is_reported(self):
        with self.assertRaises(ConfigError) as cm:
            JqlSubset("ORDER BY rank", "me")
        self.assertIn("rank", str(cm.exception))

    def test_list_field_is_not_sortable(self):
        with self.assertRaises(ConfigError):
            JqlSubset("ORDER BY labels", "me")

    def test_bad_direction_is_reported(self):
        for jql in ("ORDER BY key DSC", "ORDER BY key ASC NULLS", "ORDER BY", "ORDER BY ,"):
            with self.subTest(jql=jql):
                with self.assertRaises(ConfigError):
                    JqlSubset(jql, "me")

    # 3. absent values sort last in both directions
    def test_undated_sort_last_both_ways(self):
        for field in ("due", "scheduled", "completed"):
            rows = [d("N"), d("E", **{field: "2026-02-01"}), d("D", **{field: "2026-01-01"})]
            with self.subTest(field=field):
                self.assertEqual(self.order(f"ORDER BY {field} DESC", rows), ["E", "D", "N"])
                self.assertEqual(self.order(f"ORDER BY {field} ASC", rows), ["D", "E", "N"])

    def test_empty_text_sorts_last_both_ways(self):
        rows = [Ticket(key="N", assignee=""), Ticket(key="B", assignee="bo"),
                Ticket(key="A", assignee="al")]
        self.assertEqual(self.order("ORDER BY assignee DESC", rows), ["B", "A", "N"])
        self.assertEqual(self.order("ORDER BY assignee ASC", rows), ["A", "B", "N"])

    def test_empty_last_keeps_secondary_order(self):
        """ASC, where empties would otherwise sort first, with several of
        each so the empty-last pass must keep the secondary key's order."""
        rows = [d("B"), d("Z", due="2026-01-02"), d("A"), d("C"), d("Y", due="2026-01-01")]
        self.assertEqual(self.order("ORDER BY due ASC, key DESC", rows),
                         ["Y", "Z", "C", "B", "A"])
        self.assertEqual(self.order("ORDER BY due DESC, key ASC", rows),
                         ["Z", "Y", "A", "B", "C"])

    # 4. field names ignore case
    def test_field_names_ignore_case(self):
        rows = [d("A"), d("C"), d("B")]
        self.assertEqual(self.order("ORDER BY Key DESC", rows), ["C", "B", "A"])
        self.assertEqual(self.order("ORDER BY KEY desc", rows), ["C", "B", "A"])

    # key order is ticket order, as Jira sorts it
    def test_keys_sort_by_number(self):
        rows = [d("TKT-10"), d("TKT-9"), d("ABC-2"), d("TKT-100"), d("TKT-1")]
        self.assertEqual(self.order("ORDER BY key", rows),
                         ["ABC-2", "TKT-1", "TKT-9", "TKT-10", "TKT-100"])
        self.assertEqual(self.order("ORDER BY key DESC", rows),
                         ["TKT-100", "TKT-10", "TKT-9", "TKT-1", "ABC-2"])

    def test_keys_without_a_number_sort_as_text(self):
        rows = [d("b"), d("TKT-2"), d("a")]
        self.assertEqual(self.order("ORDER BY key", rows), ["TKT-2", "a", "b"])


class TestAdapters(unittest.TestCase):
    def _markdown(self, tmp, extra=""):
        sdlc = Path(tmp) / ".sdlc"
        (sdlc / "board").mkdir(parents=True)
        path = sdlc / "config.toml"
        path.write_text(textwrap.dedent(f"""\
            {extra}
            [ticketing]
            provider = "markdown"
            project  = "TKT"

            [markdown]
            board_dir = "{sdlc / 'board'}"
            state_dir = "{sdlc / 'state'}"
            me        = "me"

            [board.roles]
            todo = "To Do"
            done = "Done"

            [queries]
            tier2 = 'status = "To Do" AND assignee = currentUser() ORDER BY priority DESC'
            all   = 'ORDER BY key ASC'
            """))
        return get_adapter(Config.load(str(path)))

    def test_markdown_tier_query_lists_high_before_medium(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self._markdown(tmp)
            for summary, prio in (("med", "Medium"), ("high", "High"), ("top", "Highest")):
                a.create("Story", summary, priority=prio, assignee="me")
            self.assertEqual([x.priority for x in a.list(tier=2)], ["Highest", "High", "Medium"])

    def test_markdown_honours_a_configured_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self._markdown(tmp, extra='priorities = ["P0", "P1", "P2"]')
            for summary, prio in (("two", "P2"), ("zero", "P0"), ("one", "P1")):
                a.create("Story", summary, priority=prio, assignee="me")
            self.assertEqual([x.priority for x in a.list(tier=2)], ["P0", "P1", "P2"])

    def test_markdown_all_query_lists_in_key_order(self):
        """TKT-58 acceptance: the board's `all = 'ORDER BY key ASC'`."""
        with tempfile.TemporaryDirectory() as tmp:
            a = self._markdown(tmp)
            for n in range(1, 12):
                a.create("Story", f"s{n}", assignee="me")
            self.assertEqual([x.key for x in a.list(query="all")],
                             [f"TKT-{n}" for n in range(1, 12)])

    def test_openkanban_ranks_by_its_fixed_labels_even_if_config_differs(self):
        from adapters.openkanban import OpenKanbanAdapter
        data = {"ticketing": {"provider": "openkanban"}, "priorities": ["P0", "P1"],
                "openkanban": {"project": "p"},
                "board": {"roles": {"todo": "backlog", "done": "done"}},
                "queries": {"tier2": 'status = "backlog" ORDER BY priority DESC'}}

        class Stub(OpenKanbanAdapter):
            def _load_store(self):
                return {"tickets": {i: {"id": i, "title": i, "status": "backlog", "priority": p}
                                    for i, p in (("a", 4), ("b", 1), ("c", 3))}}

            def _store_path(self):
                return Path("/nonexistent/store.json")

        a = Stub(Config(data, Path("/nonexistent/.sdlc/config.toml")))
        self.assertEqual(a.priorities(), ["Highest", "High", "Medium", "Low", "Lowest"])
        self.assertEqual([x.priority for x in a.list(tier=2)], ["Highest", "Medium", "Low"])

    def test_openkanban_reports_a_sort_on_a_field_it_never_fills(self):
        from adapters.openkanban import OpenKanbanAdapter

        class Stub(OpenKanbanAdapter):
            def _load_store(self):
                return {"tickets": {}}

        for field in ("due", "assignee", "agent_status"):
            data = {"ticketing": {"provider": "openkanban"}, "openkanban": {"project": "p"},
                    "board": {"roles": {"todo": "backlog"}},
                    "queries": {"q": f"ORDER BY {field}"}}
            a = Stub(Config(data, Path("/nonexistent/.sdlc/config.toml")))
            with self.subTest(field=field), self.assertRaises(ConfigError):
                a.list(query="q")

    def test_linear_reports_a_sort_on_a_field_it_never_fills(self):
        data = {"ticketing": {"provider": "linear", "auth_env": ["LINEAR_API_KEY"]},
                "linear": {"team_key": "ENG"}, "board": {"roles": {"todo": "Todo"}},
                "queries": {"q": "ORDER BY scheduled DESC", "ok": "ORDER BY key"}}
        a = get_adapter(Config(data, Path("/nonexistent/.sdlc/config.toml")))
        a._gql = lambda *args, **kw: {"issues": {"nodes": []}}
        a._me = "me"
        with self.assertRaises(ConfigError):
            a.list(query="q")
        self.assertEqual(a.list(query="ok"), [])

    def _linear(self, extra=""):
        data = {"ticketing": {"provider": "linear", "auth_env": ["LINEAR_API_KEY"]},
                "linear": {"team_key": "ENG"}, "board": {"roles": {"todo": "Todo"}}}
        if extra:
            data["priorities"] = extra
        return get_adapter(Config(data, Path("/nonexistent/.sdlc/config.toml")))

    def test_linear_defaults_to_its_native_order(self):
        """Without a configured list, Urgent must not rank as unknown."""
        self.assertEqual(self._linear().priorities(),
                         ["Urgent", "High", "Medium", "Low", "No priority"])

    def test_linear_uses_a_configured_list(self):
        self.assertEqual(self._linear(["A", "B"]).priorities(), ["A", "B"])


if __name__ == "__main__":
    unittest.main()
