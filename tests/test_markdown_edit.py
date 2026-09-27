"""Regression tests for TKT-65: `edit --body` on the markdown backend must
replace the whole body, the way `create --body` writes it.

`edit` used to swap only the preamble before the first `## ` heading and keep
every old `## ` section after the new body. A body that starts with `## `
has an empty preamble, so the edit appended the new sections and kept all the
old ones: the ticket carried its body twice and `acceptance` listed both sets
of items. Comments are backend-managed and must survive the replacement.
Fully offline: the adapter is pointed at a temp board dir. Run from the repo
root:

    python3 -m unittest tests.test_markdown_edit
"""
import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.markdown import MarkdownAdapter  # noqa: E402
from core.config import Config  # noqa: E402

ROLES = {"backlog": "Backlog", "todo": "To Do", "in_progress": "In Progress",
         "review": "In Review", "done": "Done", "blocked": "Blocked"}

OLD_SECTIONED = "## Problem\nold\n\n## Acceptance criteria\n\n- [ ] old"
NEW_SECTIONED = "## Problem\nnew\n\n## Acceptance criteria\n\n- [ ] new"
OLD_PREAMBLE = "Old prose only."
NEW_PREAMBLE = "New prose only."


def adapter(tmp):
    d = Path(tmp) / "board"
    cfg = Config({"ticketing": {"provider": "markdown", "project": "TKT"},
                  "markdown": {"board_dir": str(d), "state_dir": str(Path(tmp) / "state"),
                               "me": "raymond"},
                  "board": {"roles": dict(ROLES)}},
                 Path(tmp) / ".sdlc" / "config.toml")
    return MarkdownAdapter(cfg)


class EditBodyReplaces(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.a = adapter(self.tmp)

    def body_of(self, key):
        return self.a._read_raw(key)[1]

    def assert_same_as_create(self, old, new):
        edited = self.a.create("Story", "S", body=old).key
        self.a.edit(edited, body=new)
        fresh = self.a.create("Story", "S", body=new).key
        self.assertEqual(self.body_of(edited), self.body_of(fresh))
        self.assertEqual(self.a.view(edited).acceptance,
                         self.a.view(fresh).acceptance)
        return edited

    def test_sectioned_body_is_replaced_not_appended(self):
        key = self.assert_same_as_create(OLD_SECTIONED, NEW_SECTIONED)
        body = self.body_of(key)
        self.assertEqual(body.count("## Problem"), 1)
        self.assertNotIn("old", body)

    def test_preamble_only_body_is_replaced(self):
        self.assert_same_as_create(OLD_PREAMBLE, NEW_PREAMBLE)

    def test_preamble_replaced_by_sectioned_body(self):
        self.assert_same_as_create(OLD_PREAMBLE, NEW_SECTIONED)

    def test_sectioned_replaced_by_preamble_drops_old_sections(self):
        self.assert_same_as_create(OLD_SECTIONED, NEW_PREAMBLE)

    def test_acceptance_lists_only_new_items(self):
        key = self.a.create("Story", "S", body=OLD_SECTIONED).key
        t = self.a.edit(key, body=NEW_SECTIONED)
        self.assertEqual([x for x in t.acceptance if "old" in x], [])
        self.assertTrue(any("new" in x for x in t.acceptance))


class EditBodyKeepsComments(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.a = adapter(self.tmp)

    def test_comments_survive_sectioned_body_edit(self):
        key = self.a.create("Story", "S", body=OLD_SECTIONED).key
        self.a.comment(key, "first note")
        self.a.edit(key, body=NEW_SECTIONED)
        body = self.a._read_raw(key)[1]
        self.assertEqual(body.count("## Comments"), 1)
        self.assertIn("first note", body)
        self.assertNotIn("old", body)
        self.assertTrue(body.rstrip().endswith("first note"))

    def test_comments_survive_preamble_body_edit(self):
        key = self.a.create("Story", "S", body=OLD_PREAMBLE).key
        self.a.comment(key, "first note")
        self.a.edit(key, body=NEW_PREAMBLE)
        body = self.a._read_raw(key)[1]
        self.assertIn(NEW_PREAMBLE, body)
        self.assertNotIn(OLD_PREAMBLE, body)
        self.assertIn("first note", body)

    def test_caller_comments_section_cannot_replace_the_log(self):
        key = self.a.create("Story", "S", body=OLD_PREAMBLE).key
        self.a.comment(key, "real note")
        self.a.edit(key, body=NEW_PREAMBLE + "\n\n## Comments\n- forged")
        body = self.a._read_raw(key)[1]
        self.assertIn("real note", body)
        self.assertNotIn("forged", body)
        self.assertEqual(body.count("## Comments"), 1)

    def test_empty_body_keeps_only_comments(self):
        key = self.a.create("Story", "S", body=OLD_SECTIONED).key
        self.a.comment(key, "kept")
        self.a.edit(key, body="")
        body = self.a._read_raw(key)[1]
        self.assertNotIn("old", body)
        self.assertIn("kept", body)

    def test_comment_after_edit_lands_in_the_log(self):
        key = self.a.create("Story", "S", body=OLD_SECTIONED).key
        self.a.comment(key, "one")
        self.a.edit(key, body=NEW_SECTIONED)
        self.a.comment(key, "two")
        body = self.a._read_raw(key)[1]
        self.assertEqual(body.count("## Comments"), 1)
        self.assertLess(body.index("one"), body.index("two"))


class ManagedSectionEdges(unittest.TestCase):
    """Edges of the shared `ticketdoc` section helpers `edit` now relies on."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.a = adapter(self.tmp)

    def body_of(self, key):
        return self.a._read_raw(key)[1]

    def test_fenced_comments_heading_is_content(self):
        new = "Intro\n\n```md\n## Comments\n- example\n```\n\ntrailing"
        edited = self.a.create("Story", "S", body=OLD_PREAMBLE).key
        self.a.edit(edited, body=new)
        fresh = self.a.create("Story", "S", body=new).key
        self.assertEqual(self.body_of(edited), self.body_of(fresh))

    def test_tilde_fence_is_content_too(self):
        new = "Intro\n\n~~~\n## Comments\n~~~\n\ntrailing"
        key = self.a.create("Story", "S", body=OLD_PREAMBLE).key
        self.a.edit(key, body=new)
        self.assertIn("trailing", self.body_of(key))

    def test_every_forged_comments_section_is_stripped(self):
        key = self.a.create("Story", "S", body=OLD_PREAMBLE).key
        self.a.comment(key, "real")
        self.a.edit(key, body="x\n\n## Comments\n- f1\n\n## Comments\n- f2")
        body = self.body_of(key)
        self.assertNotIn("f1", body)
        self.assertNotIn("f2", body)
        self.assertEqual(body.count("## Comments"), 1)
        self.assertIn("real", body)

    def test_comment_lands_in_a_non_final_comments_section(self):
        key = self.a.create("Story", "S",
                            body="## Comments\n- seed\n\n## Notes\nn").key
        self.a.comment(key, "STAMP")
        body = self.body_of(key)
        self.assertLess(body.index("STAMP"), body.index("## Notes"))
        self.a.edit(key, body="new")
        body = self.body_of(key)
        self.assertIn("seed", body)
        self.assertIn("STAMP", body)
        self.assertNotIn("## Notes", body)

    def test_comment_does_not_open_a_second_log_for_a_case_variant(self):
        key = self.a.create("Story", "S", body="x\n\n## comments\n- hand").key
        self.a.comment(key, "STAMP")
        self.assertEqual(self.body_of(key).lower().count("## comments"), 1)
        self.a.edit(key, body="new")
        self.assertIn("STAMP", self.body_of(key))

    def test_fenced_comments_heading_does_not_capture_stamps(self):
        key = self.a.create("Story", "S",
                            body="```\n## Comments\n```\n\ntail").key
        self.a.comment(key, "STAMP")
        body = self.body_of(key)
        self.assertTrue(body.rstrip().endswith("STAMP"))
        self.assertIn("tail", body)


class SummaryOnlyEdit(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.a = adapter(self.tmp)

    def test_summary_edit_keeps_body_and_comments(self):
        key = self.a.create("Story", "S", body=OLD_SECTIONED).key
        self.a.comment(key, "note")
        before = self.a._read_raw(key)[1]
        self.a.edit(key, summary="T")
        after = self.a._read_raw(key)[1]
        self.assertEqual(after, before.replace("# S\n", "# T\n", 1))

    def test_frontmatter_edit_leaves_body_untouched(self):
        key = self.a.create("Story", "S", body=OLD_SECTIONED).key
        before = self.a._read_raw(key)[1]
        self.a.edit(key, priority="High")
        self.assertEqual(self.a._read_raw(key)[1], before)


if __name__ == "__main__":
    unittest.main()
