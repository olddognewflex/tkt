"""TKT-66: the markdown backend reads acceptance items whole and finds
sections only where markdown has them.

A wrapped acceptance item used to lose every line after its first. An
indented continuation line now joins the item above with one space; a
blank line, a new list item or a heading ends the item. A `## ` line in a
fenced or indented code block is content, not a heading, and the scan
stops at the `## Comments` log, whose stamps are inserted verbatim. The
openkanban reader had the same truncation and is covered at the end.
Fully offline: the adapter is pointed at a temp board dir. Run from the
repo root:

    python3 -m unittest tests.test_markdown_acceptance
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.markdown import MarkdownAdapter  # noqa: E402
from adapters.openkanban import _extract_acceptance  # noqa: E402
from core import ticketdoc  # noqa: E402
from core.config import Config  # noqa: E402

ROLES = {"backlog": "Backlog", "todo": "To Do", "in_progress": "In Progress",
         "review": "In Review", "done": "Done", "blocked": "Blocked"}

# The shape TKT-73's acceptance section had on the live board: the first
# item wraps at 72 columns, the rest fit on one line.
TKT73 = """\
Intro.

## Acceptance

- [ ] Regression test first: link with `blocks`, assert the target's
      `blocked_by` contains the source (fails on current code).
- [ ] Same for `is blocked by` -> target's `blocks`.
- [ ] Re-running the same link is a no-op on both files.
- [ ] Linking to a missing ticket fails without writing either file.
- [ ] `tkt blockers B` returns A after `tkt link A --to B --type blocks`.
"""


def adapter(tmp):
    d = Path(tmp) / "board"
    cfg = Config({"ticketing": {"provider": "markdown", "project": "TKT"},
                  "markdown": {"board_dir": str(d), "state_dir": str(Path(tmp) / "state"),
                               "me": "raymond"},
                  "board": {"roles": dict(ROLES)}},
                 Path(tmp) / ".sdlc" / "config.toml")
    return MarkdownAdapter(cfg)


class _Board(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.a = adapter(self.tmp)

    def key(self, body):
        return self.a.create("Story", "S", body=body).key

    def view(self, body):
        return self.a.view(self.key(body))

    def acc(self, body):
        return self.view(body).acceptance


class WrappedItems(_Board):

    def test_two_line_item_comes_back_whole(self):
        self.assertEqual(
            self.acc("## Acceptance\n\n- [ ] First line of an item that\n"
                     "      wraps onto a second line.\n"),
            ["[ ] First line of an item that wraps onto a second line."])

    def test_tkt73_five_criteria_read_back_in_full(self):
        self.assertEqual(self.acc(TKT73), [
            "[ ] Regression test first: link with `blocks`, assert the "
            "target's `blocked_by` contains the source (fails on current "
            "code).",
            "[ ] Same for `is blocked by` -> target's `blocks`.",
            "[ ] Re-running the same link is a no-op on both files.",
            "[ ] Linking to a missing ticket fails without writing either file.",
            "[ ] `tkt blockers B` returns A after "
            "`tkt link A --to B --type blocks`.",
        ])

    def test_three_lines_joined_with_single_spaces(self):
        self.assertEqual(self.acc("## Acceptance\n- one\n  two\n\t three  \n"),
                         ["one two three"])

    def test_continuation_starting_with_dashes_or_emphasis(self):
        # A wrapped line may begin `--flag` or `*word*`; only a real list
        # marker (`- `, `* `) starts a new item.
        self.assertEqual(
            self.acc("## Acceptance\n- [ ] pass\n  --json to tkt\n"
                     "- [x] it is\n  *not* optional\n"),
            ["[ ] pass --json to tkt", "[x] it is *not* optional"])

    def test_star_items_wrap_too(self):
        self.assertEqual(self.acc("## Acceptance\n* a\n  b\n* c\n"),
                         ["a b", "c"])

    def test_blank_line_ends_the_item(self):
        self.assertEqual(self.acc("## Acceptance\n- one\n\n  not folded\n- two\n"),
                         ["one", "two"])

    def test_unindented_prose_is_not_folded(self):
        self.assertEqual(self.acc("## Acceptance\n- one\nProse line.\n- two\n"),
                         ["one", "two"])

    def test_crlf_line_endings(self):
        self.assertEqual(self.acc("## Acceptance\r\n- a\r\n  b\r\n- c\r\n"),
                         ["a b", "c"])

    def test_heading_ends_the_item(self):
        t = self.view("## Acceptance\n- one\n## Notes\n  indented note\n")
        self.assertEqual(t.acceptance, ["one"])


class UnchangedItems(_Board):

    def test_single_line_items_and_checkboxes(self):
        self.assertEqual(
            self.acc("## Acceptance criteria\n\n- [ ] open\n- [x] done\n"
                     "- plain\n* star\n"),
            ["[ ] open", "[x] done", "plain", "star"])

    def test_nested_items_stay_separate(self):
        self.assertEqual(self.acc("## Acceptance\n- parent\n  - child\n"),
                         ["parent", "child"])

    def test_no_acceptance_section(self):
        self.assertEqual(self.acc("Just prose.\n\n- a list item\n"), [])


class CodeIsNotStructure(_Board):

    def test_fenced_acceptance_heading_is_content(self):
        body = ("## Repro\n\n```markdown\n## Acceptance\n- sample item\n```\n\n"
                "## Acceptance\n- real item\n")
        self.assertEqual(self.acc(body), ["real item"])

    def test_tilde_fence_too(self):
        body = "Intro.\n\n~~~\n## Acceptance\n- sample\n~~~\n"
        self.assertEqual(self.acc(body), [])

    def test_longer_fence_needs_as_long_a_close(self):
        body = ("Intro.\n\n````\n```\n## Acceptance\n- sample\n````\n\n"
                "## Acceptance\n- real\n")
        self.assertEqual(self.acc(body), ["real"])

    def test_fence_with_info_string_does_not_close(self):
        body = "Intro.\n\n```text\n```python\n## Acceptance\n- sample\n```\n"
        self.assertEqual(self.acc(body), [])

    def test_indented_acceptance_heading_is_content(self):
        body = "## Repro\n\n    ## Acceptance\n    - sample item\n"
        self.assertEqual(self.acc(body), [])

    def test_tab_indented_heading_is_content(self):
        self.assertEqual(self.acc("## Repro\n\n\t## Acceptance\n\t- sample\n"), [])

    def test_fenced_heading_does_not_end_acceptance(self):
        body = "## Acceptance\n- one\n```\n## Other\n```\n- two\n"
        self.assertEqual(self.acc(body), ["one", "two"])

    def test_items_inside_a_fence_are_not_criteria(self):
        body = "## Acceptance\n- one\n```\n- not an item\n```\n"
        self.assertEqual(self.acc(body), ["one"])

    def test_fenced_heading_does_not_end_the_preamble(self):
        t = self.view("Lead.\n```\n## Not a section\n```\nMore lead.\n")
        self.assertEqual(t.description,
                         "Lead.\n```\n## Not a section\n```\nMore lead.")

    def test_acceptance_heading_in_a_comment_is_not_a_section(self):
        key = self.key("Intro.\n\n## Acceptance\n- real\n")
        self.a.comment(key, "plan\n## Acceptance\n- forged")
        self.assertEqual(self.a.view(key).acceptance, ["real"])

    def test_sections_after_the_comments_log_are_ignored(self):
        # Everything from `## Comments` on is the log, as the format gates
        # read it; every tkt writer keeps the log last.
        key = self.key("## Problem\nP\n")
        self.a.comment(key, "a note")
        fm, doc = self.a._read_raw(key)
        self.a._write_raw(key, fm, doc + "\n## Acceptance\n- after the log\n")
        t = self.a.view(key)
        self.assertEqual(t.acceptance, [])
        self.assertEqual(t.description, "## Problem\nP")

    def test_a_comments_prefixed_heading_is_not_the_log(self):
        key = self.key("## Comments from review\n- x\n\n## Problem\nP\n")
        self.a.comment(key, "stamp-here")
        t = self.a.view(key)
        self.assertEqual(t.description,
                         "## Comments from review\n- x\n## Problem\nP")
        self.assertTrue(self.a._read_raw(key)[1].rstrip().endswith("stamp-here"))


class TicketdocSections(unittest.TestCase):
    """`section_bounds` (used by `comment`, `edit` and `apply` to find the
    Comments log) reads fences and indentation the same way."""

    def test_indented_heading_is_not_a_section(self):
        self.assertIsNone(ticketdoc.section_bounds("    ## Comments\nx", "comments"))

    def test_indented_backticks_do_not_open_a_fence(self):
        body = "    ```\n## Comments\n- log"
        self.assertEqual(ticketdoc.section_bounds(body, "comments"), (1, 3))

    def test_info_string_does_not_close_a_fence(self):
        body = "```\n```python\n## Comments\n```\n## Comments\n- log"
        self.assertEqual(ticketdoc.section_bounds(body, "comments"), (4, 6))

    def test_short_close_does_not_close_a_long_fence(self):
        body = "````\n```\n## Comments\n````\n## Comments\n- log"
        self.assertEqual(ticketdoc.section_bounds(body, "comments"), (4, 6))

    def test_name_must_match_exactly(self):
        body = "## Comments from review\nx\n## comments:\n- log"
        self.assertEqual(ticketdoc.section_bounds(body, "comments"), (2, 4))

    def test_closing_hashes_are_not_part_of_the_name(self):
        self.assertEqual(ticketdoc.section_bounds("## Comments ##\n- a", "comments"),
                         (0, 2))

    def test_mixed_fence_chars_do_not_close(self):
        body = "```\n~~~\n## Comments\n```\n## Comments"
        self.assertEqual(ticketdoc.section_bounds(body, "comments"), (4, 5))


class OpenKanbanAcceptance(unittest.TestCase):

    def test_wrapped_item_comes_back_whole(self):
        self.assertEqual(
            _extract_acceptance("## Acceptance\n- [ ] first line\n"
                                "      second line\n- [ ] next\n"),
            ["[ ] first line second line", "[ ] next"])

    def test_blank_line_ends_the_item(self):
        self.assertEqual(
            _extract_acceptance("## Acceptance\n- one\n\n  stray\n- two\n"),
            ["one"])

    def test_fenced_heading_is_content(self):
        self.assertEqual(
            _extract_acceptance("Repro:\n```\n## Acceptance\n- sample\n```\n\n"
                                "## Acceptance\n- real\n"),
            ["real"])

    def test_fence_inside_the_section_is_skipped(self):
        self.assertEqual(
            _extract_acceptance("## Acceptance\n- one\n```\n- code\n```\n- two\n"),
            ["one", "two"])

    def test_wrapped_line_reading_like_a_heading_continues(self):
        self.assertEqual(
            _extract_acceptance("## Acceptance\n- [ ] The docs list every\n"
                                "      acceptance criteria field.\n- [ ] next\n"),
            ["[ ] The docs list every acceptance criteria field.", "[ ] next"])

    def test_single_line_items_unchanged(self):
        self.assertEqual(
            _extract_acceptance("Intro\n## Acceptance\n- [ ] a\n* b\n• c\n"
                                "\n## Next\n- not acceptance\n"),
            ["[ ] a", "b", "c"])


if __name__ == "__main__":
    unittest.main()
