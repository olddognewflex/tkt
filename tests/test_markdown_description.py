"""The markdown backend's description when a body has no preamble.

The description is the preamble between the `# ` summary line and the first
`## ` heading. A body that goes straight into `## ` sections has no
preamble, so viewers showed `(none)` and skills read an empty ticket. When
the preamble is empty, the description is the body text outside the
Acceptance sections and the Comments log, headings kept (TKT-66; it
generalises the `## Summary` fallback of #111). Fully offline: the adapter
is pointed at a temp board dir. Run from the repo root:

    python3 -m unittest tests.test_markdown_description
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


def adapter(tmp):
    d = Path(tmp) / "board"
    cfg = Config({"ticketing": {"provider": "markdown", "project": "TKT"},
                  "markdown": {"board_dir": str(d), "state_dir": str(Path(tmp) / "state"),
                               "me": "raymond"},
                  "board": {"roles": dict(ROLES)}},
                 Path(tmp) / ".sdlc" / "config.toml")
    return MarkdownAdapter(cfg)


class DescriptionFallback(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.a = adapter(self.tmp)

    def view(self, body):
        return self.a.view(self.a.create("Story", "S", body=body).key)

    def test_preamble_only(self):
        self.assertEqual(self.view("Plain prose.").description, "Plain prose.")

    def test_preamble_wins_over_sections(self):
        t = self.view("Lead prose.\n\n## Summary\n\nSection text.")
        self.assertEqual(t.description, "Lead prose.")

    def test_sections_used_when_no_preamble(self):
        t = self.view("## Summary\n\nWhat it does.\nSecond line.\n\n"
                      "## Acceptance\n\n- one\n- two")
        self.assertEqual(t.description, "## Summary\nWhat it does.\nSecond line.")
        self.assertEqual(t.acceptance, ["one", "two"])

    def test_any_opening_section_counts(self):
        self.assertEqual(self.view("## Problem\n\nBroken.").description,
                         "## Problem\nBroken.")

    def test_every_section_kept_in_order(self):
        t = self.view("## Context\n\nBackground.\n\n## Summary\n\nThe point.")
        self.assertEqual(t.description,
                         "## Context\nBackground.\n## Summary\nThe point.")

    def test_acceptance_between_sections_is_left_out(self):
        t = self.view("## Problem\nBroken.\n\n## Acceptance criteria\n"
                      "- [ ] fixed\n  for real\n\n## Notes\nLater.")
        self.assertEqual(t.description, "## Problem\nBroken.\n## Notes\nLater.")
        self.assertEqual(t.acceptance, ["[ ] fixed for real"])

    def test_acceptance_only_body_stays_empty(self):
        self.assertEqual(self.view("## Acceptance\n- one").description, "")

    def test_empty_body_stays_empty(self):
        self.assertEqual(self.view("").description, "")

    def test_fenced_lines_are_text(self):
        t = self.view("## Repro\n```\n## Acceptance\n- sample\n```")
        self.assertEqual(t.description,
                         "## Repro\n```\n## Acceptance\n- sample\n```")
        self.assertEqual(t.acceptance, [])

    def test_comments_not_folded_in(self):
        key = self.a.create("Story", "S", body="## Summary\n\nThe point.").key
        self.a.comment(key, "a note")
        self.assertEqual(self.a.view(key).description, "## Summary\nThe point.")

    def test_heading_inside_comment_ignored(self):
        key = self.a.create("Story", "S", body="## Problem\n\nBroken.").key
        self.a.comment(key, "note\n## Summary\nnot the ticket's")
        self.assertEqual(self.a.view(key).description, "## Problem\nBroken.")

    def test_comments_only_body_stays_empty(self):
        key = self.a.create("Story", "S").key
        self.a.comment(key, "a note")
        self.assertEqual(self.a.view(key).description, "")


class DescriptionAfterEdit(unittest.TestCase):
    """`edit` splits the stored doc with `_split_body`; the description it
    leaves behind must read the same as a freshly created ticket's."""

    SECTIONED = "## Problem\nBroken.\n\n## Acceptance\n- [ ] fixed\n  whole"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.a = adapter(self.tmp)

    def test_summary_edit_keeps_description(self):
        key = self.a.create("Story", "S", body=self.SECTIONED).key
        self.a.comment(key, "a note")
        before = self.a.view(key)
        after = self.a.edit(key, summary="New summary")
        self.assertEqual(after.summary, "New summary")
        self.assertEqual(after.description, before.description)
        self.assertEqual(after.acceptance, before.acceptance)

    def test_text_above_the_summary_is_not_description(self):
        # `_split_body` keeps only what follows the summary, so a summary
        # edit must not change the description.
        key = self.a.create("Story", "S").key
        fm, _ = self.a._read_raw(key)
        self.a._write_raw(key, fm, "## Context\nC\n\n## Acceptance\n- above\n"
                          "\n# S\nLead\n")
        self.assertEqual(self.a.view(key).description, "Lead")
        self.assertEqual(self.a.view(key).acceptance, [])
        self.assertEqual(self.a.edit(key, summary="New").description, "Lead")

    def test_body_edit_matches_create(self):
        key = self.a.create("Story", "S", body="Old preamble.").key
        self.a.comment(key, "a note")
        edited = self.a.edit(key, body=self.SECTIONED)
        fresh = self.a.view(self.a.create("Story", "S", body=self.SECTIONED).key)
        self.assertEqual(edited.description, "## Problem\nBroken.")
        self.assertEqual(edited.description, fresh.description)
        self.assertEqual(edited.acceptance, ["[ ] fixed whole"])


if __name__ == "__main__":
    unittest.main()
