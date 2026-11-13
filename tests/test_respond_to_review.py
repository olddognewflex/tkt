"""Regression tests for respond-to-review's thread handling.

TKT-40: respond-to-review must reply in-thread.

Step 4 answered a reviewer's question with `gh pr comment`, which posts a
top-level PR comment. Step 6 then resolved the thread, so the reviewer saw a
resolved thread with no visible answer on it. Every answer, fix summary and
"won't fix" justification belongs on the thread, via
`pulls/comments/<id>/replies`.

The skill ships in several harness surfaces. The full copies
(`.agents/skills`, `.kiro/skills`) are derived from the canonical one at sync
time (see test_pack_base.py); the condensed mirrors must each name the
in-thread call, or a harness reading only its mirror repeats the bug. Fully
offline. Run from the repo root:

    python3 -m unittest tests.test_respond_to_review

TKT-68: step 2 said a won't-fix thread is never resolved while step 9
resolved a repeated bot nit after a won't-fix reply, so an agent picked one
rule at random. The rule now splits by reviewer: a won't-fix on an automated
reviewer's repeated nit is justified on the thread and resolved (a bot cannot
reply, and its open thread would block step 9's exit forever); a won't-fix on
a human reviewer's comment is justified on the thread and left open for them.

TKT-67: step 6 ran the path-in-diff check on `question` threads too, so an
answer, which changes no file, was never resolved; the never-resolve list
still blocks any thread whose path is missing from the diff, and step 5
fails outright on a round that only answered questions (nothing to commit).
The condensed mirrors never said to resolve an answered thread at all, while
their loop exit allows no open human thread except one left open on purpose,
so an answered question held the loop until the cycle cap.

TKT-69: the condensed mirrors use `$PR` without saying where it comes from;
each now declares the PR number and ticket key as inputs.
"""
import os
import re
import unittest
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CANONICAL = ROOT / "skills/respond-to-review/SKILL.md"

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

WONT_FIX = re.compile(r"won'?t[- ]fix", re.IGNORECASE)
BOT = re.compile(r"\bbot\b|automated", re.IGNORECASE)
HUMAN = re.compile(r"\bhuman\b", re.IGNORECASE)


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


def _outcomes(text: str) -> dict:
    """Step 2's per-thread outcomes: name -> (resolves?, bullet text)."""
    step = _section(text, "2. Categorize each comment")
    out = {}
    for m in re.finditer(r"^- `([\w-]+)` [—-] (.*(?:\n  .*)*)", step,
                         re.MULTILINE):
        body = " ".join(m.group(2).split())
        if "will **not** resolve" in body:
            resolves = False
        elif "**will** resolve" in body:
            resolves = True
        else:
            raise AssertionError(f"outcome {m.group(1)!r} says neither")
        out[m.group(1)] = (resolves, body)
    bullets = len(re.findall(r"^- `", step, re.MULTILINE))
    if not out or len(out) != bullets:
        raise AssertionError(f"parsed {len(out)} of {bullets} step 2 outcomes")
    return out


class WontFixResolution(unittest.TestCase):
    """TKT-68: steps 2, 6 and 9 agree on when a won't-fix is resolved."""

    def setUp(self):
        self.text = CANONICAL.read_text(encoding="utf-8")
        self.outcomes = _outcomes(self.text)

    def _wont_fix(self, resolves: bool) -> dict:
        return {name: body for name, (res, body) in self.outcomes.items()
                if res is resolves and WONT_FIX.search(body)}

    def _only(self, resolves: bool) -> str:
        found = self._wont_fix(resolves)
        self.assertEqual(len(found), 1, found)
        return next(iter(found))

    def test_resolved_wont_fix_is_for_bots_only(self):
        resolved = self._wont_fix(True)
        self.assertEqual(len(resolved), 1, resolved)
        (body,) = resolved.values()
        self.assertRegex(body, BOT)
        self.assertNotRegex(body, HUMAN)

    def test_open_wont_fix_is_for_humans(self):
        kept_open = self._wont_fix(False)
        self.assertTrue(kept_open, "no kept-open won't-fix outcome")
        for name, body in kept_open.items():
            with self.subTest(outcome=name):
                self.assertRegex(body, HUMAN)

    def test_step_9_resolves_through_the_bot_outcome(self):
        name = self._only(True)
        step = _section(self.text, "9. Loop until clean")
        self.assertIn(f"`{name}`", step)
        self.assertRegex(step, r"\bresolve")

    def test_step_9_keeps_human_wont_fix_open(self):
        step = " ".join(_section(self.text, "9. Loop until clean").split())
        self.assertRegex(step, HUMAN)
        kept = self._only(False)
        self.assertIn(f"`{kept}`", step)

    def test_step_6_never_resolves_a_human_wont_fix(self):
        step = " ".join(
            _section(self.text,
                     "6. Resolve addressed threads + post fix summaries").split())
        rule = step[step.index("**Never resolve when:**"):]
        self.assertRegex(rule, WONT_FIX)
        self.assertRegex(rule, HUMAN)


class Mirrors(unittest.TestCase):
    def test_condensed_mirrors_name_the_in_thread_call(self):
        for rel in CONDENSED_MIRRORS:
            with self.subTest(path=rel):
                text = (ROOT / rel).read_text(encoding="utf-8")
                self.assertIn(REPLIES, text)
                # The canonical skill keeps a top-level reply for a question
                # with no thread; a mirror forbidding it leaves that unanswerable.
                self.assertIn("no thread", text)

    def test_condensed_mirrors_split_wont_fix_by_reviewer(self):
        # TKT-68: a mirror that says "justify won't fix and resolve" with no
        # reviewer qualifier resolves a human's thread over their head.
        for rel in CONDENSED_MIRRORS:
            with self.subTest(path=rel):
                text = (ROOT / rel).read_text(encoding="utf-8")
                lines = [ln for ln in text.splitlines() if WONT_FIX.search(ln)]
                resolved = [ln for ln in lines if re.search(r"\bresolve", ln)
                            and not re.search(r"\bopen\b", ln)]
                kept_open = [ln for ln in lines if re.search(r"\bopen\b", ln)]
                self.assertTrue(resolved, "no resolved won't-fix rule")
                for ln in resolved:
                    self.assertRegex(ln, BOT)
                    self.assertNotRegex(ln, HUMAN)
                self.assertTrue(kept_open, "no kept-open won't-fix rule")
                for ln in kept_open:
                    self.assertRegex(ln, HUMAN)

    def test_condensed_mirrors_exit_with_human_threads_open(self):
        # A human's won't-fix thread stays open, so a loop that waits for no
        # unresolved comments at all never exits before the cycle cap.
        for rel in CONDENSED_MIRRORS:
            with self.subTest(path=rel):
                text = (ROOT / rel).read_text(encoding="utf-8")
                self.assertNotIn("no unresolved comments", text)
                self.assertIn("no unresolved bot comments", text)

    def test_condensed_mirrors_block_exit_on_ordinary_human_threads(self):
        # PR #114 review: "no unresolved bot comments" alone lets the loop
        # exit with a human's ordinary change request still open (approval
        # can stay in place after a later inline comment). Only a human
        # thread deliberately left open is exempt.
        for rel in CONDENSED_MIRRORS:
            with self.subTest(path=rel):
                line = _exit_line((ROOT / rel).read_text(encoding="utf-8"))
                self.assertRegex(
                    line,
                    r"no unresolved human thread except one left open on purpose")
                self.assertIn("partial", line)
                self.assertRegex(line, WONT_FIX)
                # A reviewer reply on an exempt thread is new feedback.
                self.assertRegex(line, r"hasn't replied to since")

    def test_condensed_mirrors_define_a_partial_fix(self):
        # The exit line exempts "a partial fix"; a mirror-only agent needs to
        # know that means stating what remains, not a way out of the change.
        for rel in CONDENSED_MIRRORS:
            with self.subTest(path=rel):
                text = (ROOT / rel).read_text(encoding="utf-8")
                self.assertRegex(text, r"partly made: say on the thread what remains "
                                       r"\(a note, not a fix summary\), leave it open")


def _exit_line(text: str) -> str:
    """The condensed mirror's loop-exit step."""
    lines = [ln for ln in text.splitlines() if "Loop until" in ln]
    if len(lines) != 1:
        raise AssertionError(f"expected one 'Loop until' line, got {lines}")
    return lines[0]


class LoopExit(unittest.TestCase):
    """PR #114 review: step 9 exempts only human threads left open on purpose."""

    def setUp(self):
        step = _section(CANONICAL.read_text(encoding="utf-8"),
                        "9. Loop until clean")
        self.criteria = step[:step.index("Cap at")]

        self.text = " ".join(self.criteria.split())

    def test_ordinary_human_thread_blocks_exit(self):
        self.assertRegex(self.text,
                         r"Every unresolved human thread is one you recorded")
        self.assertRegex(self.text,
                         r"[Aa]ny other unresolved human thread.*?step 2")

    def test_only_partial_and_skipped_human_threads_are_exempt(self):
        self.assertIn("`partial`", self.text)
        self.assertIn("`skipped`", self.text)

    def test_reviewer_reply_ends_the_exemption(self):
        # Otherwise a reviewer's "no, please fix this" on a skipped thread
        # holds the loop for one poll, then it exits with the thread open.
        self.assertRegex(self.text, r"hasn't replied on it since")
        self.assertRegex(self.text, r"reviewer reply on a thread you left "
                                    r"open.*?step 2")


class MissingFix(unittest.TestCase):
    """A fix that never landed is redone, not exempted as `partial`."""

    def test_step_6_redoes_a_fix_missing_from_the_diff(self):
        step = " ".join(_section(
            CANONICAL.read_text(encoding="utf-8"),
            "6. Resolve addressed threads + post fix summaries").split())
        rule = step[step.index("Not in the diff"):]
        rule = rule[:rule.index("2. **")]
        self.assertNotIn("re-categorize as `partial`", rule)
        self.assertIn("redo it next cycle", rule)

    def test_step_6_diff_check_skips_questions(self):
        # An answered question touches no file; running the diff check on it
        # would "redo" it every cycle and block exit until the cap.
        step = " ".join(_section(
            CANONICAL.read_text(encoding="utf-8"),
            "6. Resolve addressed threads + post fix summaries").split())
        check = step[step.index("1. **"):step.index("Not in the diff")]
        self.assertRegex(check, r"For an `addressed` thread")
        self.assertRegex(check, r"[Qq]uestions skip this check")


STEP_6 = "6. Resolve addressed threads + post fix summaries"


def _flat(text: str) -> str:
    return " ".join(text.split())


class AnsweredQuestions(unittest.TestCase):
    """TKT-67: an answered question is resolved without a diff check."""

    def setUp(self):
        self.text = CANONICAL.read_text(encoding="utf-8")
        self.step6 = _flat(_section(self.text, STEP_6))

    def test_step_6_routes_questions_past_the_diff_check(self):
        # The intro scopes items 1 (diff check) and 2 (fix summary) to
        # `addressed` threads and sends a `question` straight to resolve.
        intro = self.step6[:self.step6.index("1. **")]
        self.assertRegex(intro, r"[Ii]tems 1 and 2 apply to `addressed` "
                                r"threads only")
        self.assertRegex(intro, r"`question` thread.*?straight to item 3")

    def test_fix_summary_is_for_addressed_threads_only(self):
        item = self.step6[self.step6.index("2. **"):self.step6.index("3. **")]
        self.assertIn("**For an `addressed` thread, post a fix-summary reply", item)

    def test_never_resolve_rule_scopes_the_path_check(self):
        # An unqualified "the path isn't in the commit diff" still matches
        # every answered question and keeps it open.
        rule = self.step6[self.step6.index("**Never resolve when:**"):]
        self.assertNotRegex(rule, r"or the path isn't in the commit diff")
        self.assertIn("an `addressed` thread's path isn't in the commit diff",
                      rule)

    def test_resolution_log_includes_answered_questions(self):
        item = self.step6[self.step6.index("4. **"):
                          self.step6.index("**Never resolve when:**")]
        self.assertRegex(item, r"`question`")
        self.assertIn("the summary is the answer", item)

    def test_diff_check_covers_only_this_round(self):
        # `HEAD~1 HEAD` is the previous round's commit when this round
        # committed nothing: an unlanded fix would pass on a stale file.
        step3 = _section(self.text, "3. Address change requests")
        self.assertRegex(step3, r"BASE=\$\(git rev-parse HEAD\)")
        check = self.step6[self.step6.index("1. **"):
                           self.step6.index("Not in the diff")]
        self.assertIn('git diff --name-only "$BASE" HEAD', check)
        self.assertNotIn("HEAD~1", self.step6)

    def test_step_5_survives_a_round_with_nothing_to_commit(self):
        # A round that only answered questions changes no file: a bare
        # `git commit` exits non-zero and the `&& git push` chain stops.
        step = _section(self.text, "5. Commit and push")
        self.assertRegex(step, r'if \[ -n "\$\(git status --porcelain\)" \]; '
                               r'then\n\s+git add -A && git commit [^\n]*\nfi\n')
        # The push still runs: an earlier round's commit may be unpushed.
        self.assertRegex(step, r"(?m)^git push$")
        self.assertRegex(step, r"SHA=\$\(git rev-parse --short HEAD\)")


    def test_step_8_does_not_claim_pushed_fixes(self):
        step = _section(self.text, "8. Re-request review + move back to review lane")
        self.assertNotRegex(step, r"[Ff]ixes pushed|Pushed fixes")


class MirrorAnsweredQuestions(unittest.TestCase):
    """TKT-67: every condensed mirror resolves an answered thread."""

    def test_condensed_mirrors_resolve_answered_questions(self):
        for rel in CONDENSED_MIRRORS:
            with self.subTest(path=rel):
                text = (ROOT / rel).read_text(encoding="utf-8")
                lines = [ln for ln in text.splitlines()
                         if re.search(r"\bresolve", ln, re.IGNORECASE)
                         and "answered" in ln]
                self.assertEqual(len(lines), 1, lines)
                line = lines[0]
                # The fix needs the diff check; the answer must not get it.
                self.assertIn("pushed diff", line)
                self.assertIn("no diff check", line)
                # A partial fix's file is in the diff too.
                self.assertIn("whole fix", line)
                self.assertRegex(line, r"never a partial")
                # gh has no resolve command; name the GraphQL mutation.
                self.assertIn("resolveReviewThread", line)

    def test_condensed_mirrors_skip_an_empty_commit(self):
        for rel in CONDENSED_MIRRORS:
            with self.subTest(path=rel):
                text = (ROOT / rel).read_text(encoding="utf-8")
                self.assertIn("skip the commit when nothing changed", text)
                # `git commit ... && git push` never pushes on an empty round.
                self.assertNotRegex(text, r"git commit [^\n`]*&& git push")


class MirrorInputs(unittest.TestCase):
    """TKT-69: a mirror that uses `$PR` says where it comes from."""

    INPUT = re.compile(r"^\W*Input:.*`\$PR`.*`\$KEY`", re.MULTILINE)

    def test_condensed_mirrors_declare_pr_and_key(self):
        for rel in CONDENSED_MIRRORS:
            with self.subTest(path=rel):
                text = (ROOT / rel).read_text(encoding="utf-8")
                if "$PR" not in text:
                    continue
                m = self.INPUT.search(text)
                self.assertIsNotNone(m, "no Input line declaring $PR and $KEY")
                # Before the first command that uses it.
                self.assertGreaterEqual(text.index("$PR"), m.start())
                self.assertIn("gh pr view --json number", m.group(0))

    def test_every_condensed_mirror_uses_pr(self):
        # Guards the `continue` above: today every mirror uses `$PR`.
        for rel in CONDENSED_MIRRORS:
            with self.subTest(path=rel):
                self.assertIn("$PR", (ROOT / rel).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
