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
