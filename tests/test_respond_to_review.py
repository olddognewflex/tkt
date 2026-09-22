"""Regression tests for TKT-40: respond-to-review must reply in-thread.

Step 4 answered a reviewer's question with `gh pr comment`, which posts a
top-level PR comment. Step 6 then resolved the thread, so the reviewer saw a
resolved thread with no visible answer on it. Every answer, fix summary and
"won't fix" justification belongs on the thread, via
`pulls/comments/<id>/replies`.

The skill ships in several harness surfaces. The full copies must stay
byte-identical to the canonical one; the condensed mirrors must each name the
in-thread call, or a harness reading only its mirror repeats the bug. Fully
offline. Run from the repo root:

    python3 -m unittest tests.test_respond_to_review
"""
import os
import re
import unittest
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CANONICAL = ROOT / "skills/respond-to-review/SKILL.md"

FULL_COPIES = [
    ".agents/skills/respond-to-review/SKILL.md",
    ".kiro/skills/respond-to-review/SKILL.md",
]

CONDENSED_MIRRORS = [
    ".agents/workflows/respond-to-review.md",
    ".augment/commands/respond-to-review.md",
    ".clinerules/workflows/respond-to-review.md",
    ".continue/prompts/respond-to-review.prompt",
    ".cursor/commands/respond-to-review.md",
    ".cursor/skills/respond-to-review/SKILL.md",
    ".gemini/commands/respond-to-review.toml",
    ".github/prompts/respond-to-review.prompt.md",
    ".opencode/commands/respond-to-review.md",
    ".windsurf/workflows/respond-to-review.md",
]

REPLIES = "/replies"


def _section(text: str, heading: str) -> str:
    """Body of the `### <heading>` section, up to the next `##`/`###`."""
    m = re.search(rf"^### {re.escape(heading)}\n(.*?)(?=^##)", text,
                  re.MULTILINE | re.DOTALL)
    if not m:
        raise AssertionError(f"no '### {heading}' section")
    return m.group(1)


class CanonicalSkill(unittest.TestCase):
    def setUp(self):
        self.text = CANONICAL.read_text(encoding="utf-8")

    def test_question_answers_go_on_the_thread(self):
        step = _section(self.text, "4. Reply to questions")
        self.assertIn(REPLIES, step)

    def test_question_answers_are_not_top_level_by_default(self):
        # A top-level fallback is allowed only for a question that has no
        # review thread; the thread path must come first.
        step = _section(self.text, "4. Reply to questions")
        self.assertIn(REPLIES, step)
        top_level = step.find("gh pr comment")
        if top_level != -1:
            self.assertLess(step.index(REPLIES), top_level)

    def test_wont_fix_justification_goes_on_the_thread(self):
        step = _section(self.text, "9. Loop until clean")
        self.assertIn(REPLIES, step)

    def test_reply_targets_the_thread_root_comment(self):
        # GitHub rejects a reply to a reply, so the id must be the root's.
        step = _section(self.text, "1. Fetch review comments (bot + humans)")
        self.assertRegex(step, r"root|first comment")

    def test_root_id_uses_the_64_bit_field(self):
        # GraphQL `databaseId` is 32-bit and deprecated; current comment ids
        # overflow it, which would address the reply to the wrong comment.
        step = _section(self.text, "1. Fetch review comments (bot + humans)")
        self.assertIn("fullDatabaseId", step)
        self.assertNotRegex(step, r"nodes \{[^}]*\bdatabaseId\b")


class Mirrors(unittest.TestCase):
    def test_full_copies_match_canonical(self):
        want = CANONICAL.read_bytes()
        for rel in FULL_COPIES:
            with self.subTest(path=rel):
                self.assertEqual((ROOT / rel).read_bytes(), want)

    def test_condensed_mirrors_name_the_in_thread_call(self):
        for rel in CONDENSED_MIRRORS:
            with self.subTest(path=rel):
                text = (ROOT / rel).read_text(encoding="utf-8")
                self.assertIn(REPLIES, text)
                # The canonical skill keeps a top-level reply for a question
                # with no thread; a mirror forbidding it leaves that unanswerable.
                self.assertIn("no thread", text)


if __name__ == "__main__":
    unittest.main()
