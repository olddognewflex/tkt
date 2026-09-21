"""Regression tests for TKT-13: blocker direction comes from which side Jira
returned on the link entry, never from the site's link-type wording.

`inward`/`outward` descriptions are editable per Jira site, so the old literal
match on "is blocked by" / "blocks" reported no blockers at all on a renamed
site and `select-ticket` then picked blocked work. Fully offline: `_to_ticket`
is called directly on synthetic issue payloads, so no acli or REST call is
made. Run from the repo root:

    python3 -m unittest tests.test_jira_blockers
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.jira import JiraAdapter, _is_blocks_link  # noqa: E402
from core.config import Config  # noqa: E402

ROLES = {"todo": "To Do", "in_progress": "In Progress", "done": "Done"}

# The stock Jira link type, and the same relationship on a site that renamed
# the descriptions. `name` is what stays put across both.
DEFAULT_TYPE = {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"}
CUSTOM_TYPE = {"name": "Blocks", "inward": "is blocking-on", "outward": "is blocking"}
RENAMED_TYPE = {"name": "Dependency", "inward": "is blocked up by",
                "outward": "blocks delivery of"}
UNRELATED_TYPE = {"name": "Relates", "inward": "relates to", "outward": "relates to"}
CLONE_TYPE = {"name": "Cloners", "inward": "is cloned by", "outward": "clones"}


class StubJira(JiraAdapter):
    """A fully constructed adapter with a fixed site and no credentials.

    `__init__` only reads env vars, so it is safe to run offline; going
    through it (rather than `__new__`) keeps `project` / `have_rest` /
    `_acli_site_cache` set, so `_to_ticket` stays valid if it ever reaches
    for them. `site` is pinned afterwards because the browse URL is the one
    thing `_to_ticket` reads that would otherwise depend on the environment.
    """

    def __init__(self):
        super().__init__(Config(
            {"ticketing": {"provider": "jira", "project": "PROJ"},
             "board": {"roles": dict(ROLES)}},
            Path("/nonexistent/.sdlc/config.toml")))
        self.site = "example.atlassian.net"


def adapter():
    return StubJira()


def issue(links, key="PROJ-1"):
    return {"key": key,
            "fields": {"summary": "s", "issuetype": {"name": "Bug"},
                       "status": {"name": "To Do"}, "issuelinks": links}}


def inward_link(ltype, key, category="new"):
    """A link entry where `key` blocks the issue being parsed."""
    return {"type": dict(ltype),
            "inwardIssue": {"key": key,
                            "fields": {"status": {"statusCategory":
                                                  {"key": category}}}}}


def outward_link(ltype, key):
    """A link entry where the issue being parsed blocks `key`."""
    return {"type": dict(ltype), "outwardIssue": {"key": key}}


class LinkTypeMatching(unittest.TestCase):
    def test_matches_stock_name(self):
        self.assertTrue(_is_blocks_link(DEFAULT_TYPE))

    def test_matches_name_despite_custom_wording(self):
        self.assertTrue(_is_blocks_link(CUSTOM_TYPE))

    def test_name_match_is_case_insensitive(self):
        self.assertTrue(_is_blocks_link({"name": "blocks"}))
        self.assertTrue(_is_blocks_link({"name": " BLOCKS "}))

    def test_falls_back_to_both_descriptions(self):
        self.assertTrue(_is_blocks_link(RENAMED_TYPE))

    def test_rejects_unrelated_types(self):
        self.assertFalse(_is_blocks_link(UNRELATED_TYPE))
        self.assertFalse(_is_blocks_link(CLONE_TYPE))

    def test_rejects_one_sided_block_wording(self):
        # "blocks" on one side only is not the blocks relationship.
        self.assertFalse(_is_blocks_link({"name": "Causes",
                                          "inward": "is caused by",
                                          "outward": "blocks release of"}))

    def test_tolerates_missing_fields(self):
        self.assertFalse(_is_blocks_link({}))
        self.assertFalse(_is_blocks_link({"name": None, "inward": None,
                                          "outward": None}))


class BlockerDirection(unittest.TestCase):
    def setUp(self):
        self.a = adapter()

    def test_default_wording_both_directions(self):
        t = self.a._to_ticket(issue([inward_link(DEFAULT_TYPE, "PROJ-2"),
                                     outward_link(DEFAULT_TYPE, "PROJ-3")]))
        self.assertEqual([b["key"] for b in t.blocked_by], ["PROJ-2"])
        self.assertEqual(t.blocks, ["PROJ-3"])

    def test_custom_wording_gives_identical_result(self):
        """The whole point of TKT-13: same payload, renamed descriptions."""
        default = self.a._to_ticket(issue([inward_link(DEFAULT_TYPE, "PROJ-2"),
                                           outward_link(DEFAULT_TYPE, "PROJ-3")]))
        custom = self.a._to_ticket(issue([inward_link(CUSTOM_TYPE, "PROJ-2"),
                                          outward_link(CUSTOM_TYPE, "PROJ-3")]))
        self.assertEqual(custom.blocked_by, default.blocked_by)
        self.assertEqual(custom.blocks, default.blocks)

    def test_renamed_type_still_resolves(self):
        t = self.a._to_ticket(issue([inward_link(RENAMED_TYPE, "PROJ-2")]))
        self.assertEqual([b["key"] for b in t.blocked_by], ["PROJ-2"])

    def test_unrelated_links_are_ignored(self):
        t = self.a._to_ticket(issue([inward_link(UNRELATED_TYPE, "PROJ-9"),
                                     outward_link(CLONE_TYPE, "PROJ-8")]))
        self.assertEqual(t.blocked_by, [])
        self.assertEqual(t.blocks, [])

    def test_resolved_blocker_is_marked(self):
        t = self.a._to_ticket(issue([inward_link(DEFAULT_TYPE, "PROJ-2",
                                                 category="done"),
                                     inward_link(CUSTOM_TYPE, "PROJ-4",
                                                 category="indeterminate")]))
        self.assertEqual(t.blocked_by,
                         [{"key": "PROJ-2", "resolved": True},
                          {"key": "PROJ-4", "resolved": False}])
        self.assertEqual([b["key"] for b in t.unresolved_blockers()], ["PROJ-4"])

    def test_missing_status_category_is_unresolved(self):
        link = {"type": dict(DEFAULT_TYPE), "inwardIssue": {"key": "PROJ-2"}}
        t = self.a._to_ticket(issue([link]))
        self.assertEqual(t.blocked_by, [{"key": "PROJ-2", "resolved": False}])

    def test_no_links_at_all(self):
        for links in ([], None):
            with self.subTest(links=links):
                t = self.a._to_ticket(issue(links))
                self.assertEqual(t.blocked_by, [])
                self.assertEqual(t.blocks, [])


if __name__ == "__main__":
    unittest.main()
