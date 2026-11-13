"""TKT-113: the github and linear backends read acceptance items whole.

Same defect as TKT-66 (markdown, openkanban): an acceptance item that
wrapped onto an indented second line came back with only its first line.
An indented continuation line now joins the item above with one space,
unless it opens a list item of its own; a blank line, a fence or a heading
ends the item, and fenced lines are content, never headings or items.

All three non-markdown readers share one parser
(`ticketdoc.extract_acceptance`); each is driven here through the adapter's
own normalizer, so a backend that stops using it is caught. Fully offline:
no `gh`, no API key. Run from the repo root:

    python3 -m unittest tests.test_acceptance_github_linear
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.github import GithubAdapter  # noqa: E402
from adapters.linear import LinearAdapter  # noqa: E402
from core import ticketdoc  # noqa: E402
from core.config import Config  # noqa: E402

ROLES = {"todo": "Todo", "done": "Done"}


def github_acceptance(body: str) -> list[str]:
    config = Config({"ticketing": {"provider": "github"},
                     "board": {"roles": dict(ROLES)},
                     "github": {"board": "labels", "repo": "owner/repo"}},
                    Path("/nonexistent/.sdlc/config.toml"))
    issue = {"number": 7, "title": "t", "body": body, "state": "OPEN",
             "url": "", "labels": [], "assignees": []}
    return GithubAdapter(config)._issue_to_ticket(issue).acceptance


def linear_acceptance(body: str) -> list[str]:
    config = Config({"ticketing": {"provider": "linear"},
                     "linear": {"team_key": "ENG"},
                     "board": {"roles": dict(ROLES)}},
                    Path("/nonexistent/.sdlc/config.toml"))
    issue = {"id": "id-1", "identifier": "ENG-1", "title": "t",
             "description": body,
             "state": {"name": "Todo", "type": "unstarted"}}
    return LinearAdapter(config)._to_ticket(issue).acceptance


# TKT-113's own acceptance section, wrapped at 72 columns the way an
# editor leaves it: the first item spills onto a second line.
WRAPPED = """\
Same defect as TKT-66.

## Acceptance

- [ ] A regression test with a two-line item fails on current code,
      for github and for linear.
- [ ] Wrapped items come back whole and the items after them are kept.
"""


class _Cases:
    """Mixed into one TestCase per backend; `read` is the backend reader."""

    read = staticmethod(lambda body: [])

    def check(self, body, want):
        self.assertEqual(self.read(body), want)

    def test_wrapped_item_comes_back_whole(self):
        self.check(WRAPPED, [
            "[ ] A regression test with a two-line item fails on current "
            "code, for github and for linear.",
            "[ ] Wrapped items come back whole and the items after them "
            "are kept."])

    def test_items_after_a_wrapped_item_are_kept(self):
        self.check("## Acceptance\n- [ ] first line\n"
                   "      second line\n- [ ] next\n* [x] last\n",
                   ["[ ] first line second line", "[ ] next", "[x] last"])

    def test_item_wrapping_over_several_lines(self):
        self.check("## Acceptance\n- one\n  two\n\tthree\n- four\n",
                   ["one two three", "four"])

    def test_crlf_body_wraps_too(self):
        # GitHub's web editor saves issue bodies with CRLF line endings.
        self.check("## Acceptance\r\n- [ ] first\r\n  second\r\n- [ ] next\r\n",
                   ["[ ] first second", "[ ] next"])

    def test_wrapped_line_reading_like_a_heading_continues(self):
        self.check("## Acceptance\n- [ ] The docs list every\n"
                   "      acceptance criteria field.\n- [ ] next\n",
                   ["[ ] The docs list every acceptance criteria field.",
                    "[ ] next"])

    def test_indented_list_item_is_its_own_item(self):
        self.check("## Acceptance\n- parent\n  - nested\n  * starred\n",
                   ["parent", "nested", "starred"])

    def test_indented_bullet_char_text_continues(self):
        # `-flag` and `*word*` are text, not list markers.
        self.check("## Acceptance\n- pass the\n  -v flag\n- emphasise\n"
                   "  *this* word\n",
                   ["pass the -v flag", "emphasise *this* word"])

    def test_blank_line_ends_the_item(self):
        self.check("## Acceptance\n- one\n\n  stray\n- two\n", ["one"])

    def test_heading_ends_the_section(self):
        self.check("## Acceptance\n- one\n  wrapped\n## Notes\n- not acceptance\n",
                   ["one wrapped"])

    def test_unindented_prose_ends_the_section(self):
        self.check("## Acceptance\n- one\nProse after.\n- not acceptance\n",
                   ["one"])

    def test_fenced_heading_is_content(self):
        self.check("Repro:\n```\n## Acceptance\n- sample\n```\n\n"
                   "## Acceptance\n- real\n", ["real"])

    def test_fence_inside_the_section_is_skipped(self):
        self.check("## Acceptance\n- one\n```\n- code\n```\n- two\n",
                   ["one", "two"])

    def test_fence_ends_an_open_item(self):
        # The indented line after the fence continues nothing, so it reads
        # as prose and ends the section.
        self.check("## Acceptance\n- one\n```\ncode\n```\n  stray\n- two\n",
                   ["one"])

    def test_single_line_items_and_checkboxes_unchanged(self):
        self.check("Intro\n## Acceptance\n- [ ] a\n* [x] b\n• c\n"
                   "\n## Next\n- not acceptance\n",
                   ["[ ] a", "[x] b", "c"])

    def test_heading_spellings_still_open_the_section(self):
        for heading in ("## Acceptance criteria", "### Acceptance",
                        "Acceptance criteria:"):
            with self.subTest(heading=heading):
                self.check(f"{heading}\n- a\n  b\n", ["a b"])

    def test_no_section_no_items(self):
        self.check("Just prose.\n- a list, but not acceptance\n", [])
        self.check("", [])


class GithubAcceptance(_Cases, unittest.TestCase):
    read = staticmethod(github_acceptance)


class LinearAcceptance(_Cases, unittest.TestCase):
    read = staticmethod(linear_acceptance)


class SharedParser(unittest.TestCase):
    """The three copies of the parser are now one."""

    def test_backends_share_the_ticketdoc_parser(self):
        import adapters.github as github
        import adapters.linear as linear
        import adapters.openkanban as openkanban
        for module in (github, linear, openkanban):
            with self.subTest(module=module.__name__):
                self.assertIs(module._extract_acceptance,
                              ticketdoc.extract_acceptance)

    def test_none_body_is_empty(self):
        self.assertEqual(ticketdoc.extract_acceptance(None), [])


if __name__ == "__main__":
    unittest.main()
