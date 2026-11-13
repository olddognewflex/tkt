"""The markdown backend's description falls back to a `## Summary` section.

The description is the preamble between the `# ` summary line and the first
`## ` heading. A body that goes straight into `## Summary` has no preamble,
so viewers showed `(none)` for a ticket that does describe itself. The first
Summary section now stands in when the preamble is empty. Fully offline: the
adapter is pointed at a temp board dir. Run from the repo root:

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

    def test_summary_section_used_when_no_preamble(self):
        t = self.view("## Summary\n\nWhat it does.\nSecond line.\n\n"
                      "## Acceptance\n\n- one\n- two")
        self.assertEqual(t.description, "What it does.\nSecond line.")
        self.assertEqual(t.acceptance, ["one", "two"])

    def test_preamble_wins_over_summary(self):
        t = self.view("Lead prose.\n\n## Summary\n\nSection text.")
        self.assertEqual(t.description, "Lead prose.")

    def test_summary_after_other_section(self):
        t = self.view("## Context\n\nBackground.\n\n## Summary\n\nThe point.")
        self.assertEqual(t.description, "The point.")

    def test_heading_case_insensitive(self):
        self.assertEqual(self.view("## summary\n\nLower.").description, "Lower.")

    def test_first_summary_only(self):
        t = self.view("## Summary\n\nFirst.\n\n## Summary\n\nSecond.")
        self.assertEqual(t.description, "First.")

    def test_no_preamble_no_summary_stays_empty(self):
        self.assertEqual(self.view("## Problem\n\nBroken.").description, "")

    def test_similar_heading_not_matched(self):
        self.assertEqual(self.view("## Summary of changes\n\nX.").description, "")

    def test_comments_not_folded_in(self):
        key = self.a.create("Story", "S", body="## Summary\n\nThe point.").key
        self.a.comment(key, "a note")
        self.assertEqual(self.a.view(key).description, "The point.")

    def test_empty_first_summary_does_not_fall_through(self):
        t = self.view("## Summary\n\n## Summary\n\nSecond section.")
        self.assertEqual(t.description, "")

    def test_summary_heading_inside_comment_ignored(self):
        key = self.a.create("Story", "S", body="## Problem\n\nBroken.").key
        self.a.comment(key, "note\n## Summary\nnot the ticket's")
        self.assertEqual(self.a.view(key).description, "")


if __name__ == "__main__":
    unittest.main()
