"""Tests for TKT-27: format gates on `tkt transition` and `tkt comment`.

`core/gates.py` runs `enforcement/check-artifact-format.mjs` on a ticket body
before it moves into a late lane, and on a comment before it is posted. Warn
mode prints findings, strict mode blocks (exit 5), off skips the check, and a
gate that cannot judge never blocks. Fully offline: the checker subprocess
and the node lookup are stubbed, except one end-to-end test that needs node.
Run from the repo root:

    python3 -m unittest tests.test_gates
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.base import Adapter  # noqa: E402
from adapters.jira import JiraAdapter  # noqa: E402
from adapters.markdown import MarkdownAdapter  # noqa: E402
from core import cli, gates  # noqa: E402
from core.config import Config  # noqa: E402
from core.errors import ConfigError, GateError  # noqa: E402
from core.schema import Ticket  # noqa: E402

ROLES = {"backlog": "Backlog", "todo": "To Do", "in_progress": "In Progress",
         "review": "In Review", "qa": "QA", "done": "Done"}

GOOD_BODY = "# Title\n\nThesis line.\n\n## Summary\nok\n\n## Behavior\nok\n"
TWO_FINDINGS = ("ticket: missing-section: required section \"## Summary\" not found\n"
                "ticket: thesis: no thesis line\n")


def config(enforcement=None, provider="markdown"):
    d = {"ticketing": {"provider": provider, "project": "TKT"},
         "board": {"roles": dict(ROLES)}}
    if enforcement is not None:
        d["enforcement"] = enforcement
    return Config(d, Path("/nonexistent/.sdlc/config.toml"))


class FakeAdapter:
    """Records verb calls; `bodies` is what artifact_body hands the gate."""

    has_artifact_body = True

    def __init__(self, tickets=None, bodies=None, view_raises=False):
        self.tickets = tickets or {}
        self.bodies = bodies or {}
        self.view_raises = view_raises
        self.views, self.transitions, self.comments = [], [], []

    def view(self, key, *, hierarchy=True):
        self.views.append((key, hierarchy))
        if self.view_raises:
            raise RuntimeError("backend down")
        return self.tickets.get(key) or Ticket(key=key, type="Story")

    def artifact_body(self, ticket):
        return self.bodies.get(ticket.key)

    def transition(self, key, role):
        self.transitions.append((key, role))

    def comment(self, key, body):
        self.comments.append((key, body))


class FakeRunner:
    def __init__(self, stderr="", returncode=0, raises=None):
        self.stderr, self.returncode, self.raises = stderr, returncode, raises
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        if self.raises is not None:
            raise self.raises
        return SimpleNamespace(returncode=self.returncode, stderr=self.stderr,
                               stdout="")

    def preset(self):
        argv = self.calls[-1][0]
        return argv[argv.index("--preset") + 1]

    def kind(self):
        argv = self.calls[-1][0]
        return argv[argv.index("--kind") + 1]


def has_node(_name):
    return "/usr/bin/node"


def no_node(_name):
    return None


class GateCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.script = self.tmp / "check-artifact-format.mjs"
        self.script.write_text("// stub\n")

    def kw(self, runner, which=has_node, script=None):
        return {"runner": runner, "which": which,
                "script": self.script if script is None else script}

    def transition(self, cfg, adapter, target, runner, key="TKT-1", **over):
        """Gate then transition, like cli does. Returns (stderr, error)."""
        err = io.StringIO()
        exc = None
        with contextlib.redirect_stderr(err):
            try:
                gates.gate_transition(cfg, adapter, key, target,
                                      **self.kw(runner, **over))
                adapter.transition(key, target)
            except GateError as e:
                exc = e
        return err.getvalue(), exc

    def comment(self, cfg, adapter, body, runner, key="TKT-1", **over):
        err = io.StringIO()
        exc = None
        with contextlib.redirect_stderr(err):
            try:
                gates.gate_comment(cfg, key, body, **self.kw(runner, **over))
                adapter.comment(key, body)
            except GateError as e:
                exc = e
        return err.getvalue(), exc


class Gating(GateCase):

    def test_ungated_lane_never_runs_the_checker(self):
        a = FakeAdapter(bodies={"TKT-1": "x"})
        r = FakeRunner(TWO_FINDINGS)
        err, exc = self.transition(config(), a, "in_progress", r)
        self.assertEqual(r.calls, [])
        self.assertEqual(a.views, [])
        self.assertEqual(a.transitions, [("TKT-1", "in_progress")])
        self.assertEqual(err, "")

    def test_mode_off_never_runs_the_checker(self):
        a = FakeAdapter(bodies={"TKT-1": "x"})
        r = FakeRunner(TWO_FINDINGS)
        cfg = config({"artifact_format": "off"})
        self.transition(cfg, a, "review", r)
        self.comment(cfg, a, "a comment", r)
        self.assertEqual(r.calls, [])
        self.assertEqual(a.views, [])
        self.assertEqual(len(a.transitions), 1)
        self.assertEqual(len(a.comments), 1)

    def test_warn_prints_findings_and_transitions(self):
        a = FakeAdapter(bodies={"TKT-1": "body text"})
        r = FakeRunner(TWO_FINDINGS)
        err, exc = self.transition(config(), a, "review", r)
        self.assertIsNone(exc)
        self.assertEqual(a.transitions, [("TKT-1", "review")])
        self.assertEqual(err.splitlines(), [
            "tkt: warn: ticket: missing-section: required section \"## Summary\" not found",
            "tkt: warn: ticket: thesis: no thesis line"])
        self.assertEqual(r.kind(), "ticket")
        self.assertNotIn("--strict", r.calls[0][0])
        self.assertEqual(r.calls[0][1]["input"], "body text")

    def test_strict_with_findings_blocks_before_the_adapter(self):
        a = FakeAdapter(bodies={"TKT-1": "body text"})
        r = FakeRunner(TWO_FINDINGS, returncode=0)
        err, exc = self.transition(config({"artifact_format": "strict"}),
                                   a, "done", r)
        self.assertIsInstance(exc, GateError)
        self.assertEqual(exc.exit_code, 5)
        self.assertEqual(a.transitions, [])
        msg = str(exc)
        self.assertIn("TKT-1", msg)
        self.assertIn("2 format findings", msg)
        self.assertIn('artifact_format = "warn"', msg)
        self.assertIn("ticket: missing-section", err)

    def test_strict_with_no_findings_proceeds(self):
        a = FakeAdapter(bodies={"TKT-1": GOOD_BODY})
        r = FakeRunner("")
        err, exc = self.transition(config({"artifact_format": "strict"}),
                                   a, "review", r)
        self.assertIsNone(exc)
        self.assertEqual(a.transitions, [("TKT-1", "review")])
        self.assertEqual(err, "")
        self.assertEqual(len(r.calls), 1)

    def test_comment_warn(self):
        a = FakeAdapter()
        r = FakeRunner("comment: word-budget: 52 words, over the 40-word budget\n")
        err, exc = self.comment(config(), a, "long comment", r)
        self.assertIsNone(exc)
        self.assertEqual(a.comments, [("TKT-1", "long comment")])
        self.assertEqual(err, "tkt: warn: comment: word-budget: 52 words, "
                              "over the 40-word budget\n")
        self.assertEqual(r.kind(), "comment")
        self.assertEqual(a.views, [])       # comments never cost a backend call

    def test_comment_strict_blocks(self):
        a = FakeAdapter()
        r = FakeRunner("comment: word-budget: too long\n", returncode=0)
        err, exc = self.comment(config({"artifact_format": "strict"}), a,
                                "long comment", r)
        self.assertIsInstance(exc, GateError)
        self.assertIn("1 format finding ", str(exc))
        self.assertEqual(a.comments, [])

    def test_comment_strict_clean_proceeds(self):
        a = FakeAdapter()
        err, exc = self.comment(config({"artifact_format": "strict"}), a,
                                "short", FakeRunner(""))
        self.assertIsNone(exc)
        self.assertEqual(a.comments, [("TKT-1", "short")])

    def test_stderr_parsing_ignores_non_finding_lines(self):
        r = FakeRunner("note: something else\nwarning: deprecated\n"
                       "ticket: title: no title\n  ticket: indented\n"
                       "ticketx: nope\n", returncode=1)
        out = gates.run_checker("ticket", "standard", "body", **self.kw(r))
        self.assertEqual(out, ["ticket: title: no title"])

    def test_presets_by_type_selects_the_ticket_preset(self):
        cfg = config({"preset": "standard",
                      "presets_by_type": {"Story": "problem-proposal"}})
        story = FakeAdapter(tickets={"TKT-1": Ticket(key="TKT-1", type="Story")},
                            bodies={"TKT-1": "x"})
        r = FakeRunner("")
        self.transition(cfg, story, "review", r)
        self.assertEqual(r.preset(), "problem-proposal")
        task = FakeAdapter(tickets={"TKT-1": Ticket(key="TKT-1", type="Task")},
                           bodies={"TKT-1": "x"})
        self.transition(cfg, task, "review", r)
        self.assertEqual(r.preset(), "standard")

    def test_presets_by_type_matches_case_insensitively(self):
        cfg = config({"presets_by_type": {"story": "problem-proposal"}})
        a = FakeAdapter(tickets={"TKT-1": Ticket(key="TKT-1", type="Story")},
                        bodies={"TKT-1": "x"})
        r = FakeRunner("")
        self.transition(cfg, a, "review", r)
        self.assertEqual(r.preset(), "problem-proposal")

    def test_comments_use_preset_not_presets_by_type(self):
        cfg = config({"preset": "problem-proposal",
                      "presets_by_type": {"Story": "standard"}})
        r = FakeRunner("")
        self.comment(cfg, FakeAdapter(), "hi", r)
        self.assertEqual(r.preset(), "problem-proposal")


class LaneResolution(GateCase):

    def gated_run(self, target):
        a = FakeAdapter(bodies={"TKT-1": "x"})
        r = FakeRunner("ticket: title: no title\n")
        self.transition(config(), a, target, r)
        return r

    def test_exact_lane_name_is_gated(self):
        self.assertEqual(len(self.gated_run("In Review").calls), 1)

    def test_case_mismatched_target_is_not_gated(self):
        # role_to_lane is exact, so the adapter rejects these (exit 4);
        # gating them first would only turn that into a misleading exit 5.
        self.assertEqual(self.gated_run("in review").calls, [])
        self.assertEqual(self.gated_run("DONE").calls, [])
        self.assertEqual(self.gated_run("Review").calls, [])

    def test_role_name_is_gated(self):
        self.assertEqual(len(self.gated_run("qa").calls), 1)

    def test_unknown_target_is_not_gated(self):
        self.assertEqual(self.gated_run("Shipped").calls, [])

    def test_ungated_lane_name(self):
        self.assertEqual(self.gated_run("In Progress").calls, [])

    def test_gated_role(self):
        c = config()
        self.assertEqual(gates.gated_role(c, "review"), "review")
        self.assertEqual(gates.gated_role(c, "In Review"), "review")
        self.assertIsNone(gates.gated_role(c, "in REVIEW"))
        self.assertIsNone(gates.gated_role(c, "in_progress"))
        self.assertIsNone(gates.gated_role(c, "nope"))

    def shared_lane_config(self):
        d = {"ticketing": {"provider": "markdown"},
             "board": {"roles": {"todo": "To Do", "in_progress": "Doing",
                                 "review": "Doing", "done": "Done"}}}
        return Config(d, Path("/nonexistent/.sdlc/config.toml"))

    def test_lane_shared_with_a_gated_role_is_gated(self):
        c = self.shared_lane_config()
        self.assertEqual(gates.gated_role(c, "Doing"), "review")
        # The role name lands in the same lane, so it is gated too.
        self.assertEqual(gates.gated_role(c, "in_progress"), "review")
        a = FakeAdapter(bodies={"TKT-1": "x"})
        r = FakeRunner("ticket: title: no title\n")
        err, exc = self.transition(
            Config({**c._d, "enforcement": {"artifact_format": "strict"}},
                   c.path), a, "Doing", r)
        self.assertIsInstance(exc, GateError)
        self.assertIn("transition to review", str(exc))
        self.assertEqual(a.transitions, [])


class CannotJudge(GateCase):
    """Every failure to judge is swallowed: no findings, the verb proceeds,
    even in strict mode."""

    STRICT = {"artifact_format": "strict"}

    def assert_proceeds(self, adapter, runner, **over):
        err, exc = self.transition(config(self.STRICT), adapter, "review",
                                   runner, **over)
        self.assertIsNone(exc)
        self.assertEqual(err, "")
        self.assertEqual(adapter.transitions, [("TKT-1", "review")])

    def test_none_body(self):
        r = FakeRunner(TWO_FINDINGS)
        self.assert_proceeds(FakeAdapter(bodies={}), r)
        self.assertEqual(r.calls, [])

    def test_empty_body(self):
        r = FakeRunner(TWO_FINDINGS)
        self.assert_proceeds(FakeAdapter(bodies={"TKT-1": "  \n\t"}), r)
        self.assertEqual(r.calls, [])

    def test_node_missing(self):
        r = FakeRunner(TWO_FINDINGS)
        self.assert_proceeds(FakeAdapter(bodies={"TKT-1": "x"}), r,
                             which=no_node)
        self.assertEqual(r.calls, [])

    def test_script_missing(self):
        r = FakeRunner(TWO_FINDINGS)
        self.assert_proceeds(FakeAdapter(bodies={"TKT-1": "x"}), r,
                             script=self.tmp / "absent.mjs")
        self.assertEqual(r.calls, [])

    def test_runner_raises(self):
        self.assert_proceeds(FakeAdapter(bodies={"TKT-1": "x"}),
                             FakeRunner(raises=OSError("exec failed")))

    def test_timeout(self):
        self.assert_proceeds(
            FakeAdapter(bodies={"TKT-1": "x"}),
            FakeRunner(raises=subprocess.TimeoutExpired(["node"], 15)))

    def test_checker_exit_2(self):
        self.assert_proceeds(
            FakeAdapter(bodies={"TKT-1": "x"}),
            FakeRunner("error: unknown preset\nticket: fake: line\n",
                       returncode=2))

    def test_view_raises(self):
        r = FakeRunner(TWO_FINDINGS)
        self.assert_proceeds(FakeAdapter(view_raises=True), r)
        self.assertEqual(r.calls, [])

    def test_gate_view_skips_hierarchy(self):
        a = FakeAdapter(bodies={"TKT-1": "x"})
        self.transition(config(), a, "review", FakeRunner(""))
        self.assertEqual(a.views[0], ("TKT-1", False))

    def test_hierarchy_view_failure_keeps_the_format_check(self):
        class GithubLike(FakeAdapter):
            # Children cost an extra call that is down; the body is fine.
            def view(self, key, *, hierarchy=True):
                self.views.append((key, hierarchy))
                if hierarchy:
                    raise RuntimeError("children lookup failed")
                return Ticket(key=key, type="Story")
        a = GithubLike(bodies={"TKT-1": "body"})
        r = FakeRunner("ticket: title: no title\n")
        err, exc = self.transition(config(self.STRICT), a, "review", r)
        self.assertIsInstance(exc, GateError)
        self.assertEqual(len(r.calls), 1)
        self.assertEqual(err.splitlines()[0], "tkt: error: ticket: title: no title")
        self.assertIn(("TKT-1", True), a.views)    # the best-effort lookup
        self.assertEqual(a.transitions, [])

    def test_artifact_body_raises(self):
        class Boom(FakeAdapter):
            def artifact_body(self, ticket):
                raise RuntimeError("no")
        self.assert_proceeds(Boom(), FakeRunner(TWO_FINDINGS))

    def test_adapter_without_bodies_skips_the_view(self):
        a = FakeAdapter(bodies={"TKT-1": "x"})
        a.has_artifact_body = False
        r = FakeRunner(TWO_FINDINGS)
        self.assert_proceeds(a, r)
        self.assertEqual(a.views, [])
        self.assertEqual(r.calls, [])

    def test_comment_empty_body(self):
        r = FakeRunner("comment: x: y\n")
        a = FakeAdapter()
        err, exc = self.comment(config(self.STRICT), a, "   ", r)
        self.assertIsNone(exc)
        self.assertEqual(r.calls, [])
        self.assertEqual(a.comments, [("TKT-1", "   ")])

    def test_comment_node_missing(self):
        r = FakeRunner("comment: x: y\n")
        a = FakeAdapter()
        err, exc = self.comment(config(self.STRICT), a, "hi", r, which=no_node)
        self.assertIsNone(exc)
        self.assertEqual(a.comments, [("TKT-1", "hi")])


class EnforcementConfig(unittest.TestCase):

    def test_absent_table_defaults(self):
        s = config().enforcement()
        self.assertEqual(s["artifact_format"], "warn")
        self.assertEqual(s["preset"], "standard")
        self.assertEqual(s["presets_by_type"], {})
        self.assertEqual(s["subtask_types"], ["Sub-task", "Subtask"])

    def test_values_read(self):
        s = config({"artifact_format": "strict", "preset": "problem-proposal",
                    "presets_by_type": {"Epic": "standard"},
                    "subtask_types": ["Sub-bug"]}).enforcement()
        self.assertEqual(s["artifact_format"], "strict")
        self.assertEqual(s["preset"], "problem-proposal")
        self.assertEqual(s["presets_by_type"], {"Epic": "standard"})
        self.assertEqual(s["subtask_types"], ["Sub-bug"])

    def test_defaults_are_not_shared(self):
        config().enforcement()["subtask_types"].append("X")
        self.assertEqual(config().enforcement()["subtask_types"],
                         ["Sub-task", "Subtask"])

    def bad(self, table, needle):
        with self.assertRaises(ConfigError) as cm:
            config(table).enforcement()
        self.assertIn(needle, str(cm.exception))
        self.assertEqual(cm.exception.exit_code, 2)

    def test_bad_mode(self):
        self.bad({"artifact_format": "block"}, "artifact_format")

    def test_unknown_key(self):
        self.bad({"artifact_fromat": "strict"}, "unknown key")

    def test_wrong_types(self):
        self.bad({"artifact_format": True}, "artifact_format")
        self.bad({"preset": 3}, "preset")
        self.bad({"preset": ""}, "preset")
        self.bad({"presets_by_type": ["Story"]}, "presets_by_type")
        self.bad({"presets_by_type": {"Story": 1}}, "presets_by_type.Story")
        self.bad({"subtask_types": "Sub-task"}, "subtask_types")
        self.bad({"subtask_types": [1]}, "subtask_types")

    def test_table_not_a_table(self):
        d = {"ticketing": {"provider": "markdown"}, "enforcement": "strict"}
        with self.assertRaises(ConfigError):
            Config(d, Path("/x/config.toml")).enforcement()

    def test_bad_table_fails_the_gate_loudly(self):
        with self.assertRaises(ConfigError):
            gates.gate_transition(config({"artifact_format": "nope"}),
                                  FakeAdapter(), "TKT-1", "review",
                                  runner=FakeRunner(), which=no_node)

    def test_bad_table_does_not_break_config_load(self):
        cfg = config({"artifact_format": "nope"})
        self.assertEqual(cfg.role_to_lane("review"), "In Review")


def tk(key, type_, children=()):
    return Ticket(key=key, type=type_, children=list(children))


class Hierarchy(unittest.TestCase):

    def run_h(self, ticket, body, others=(), cfg=None):
        a = FakeAdapter(tickets={t.key: t for t in (ticket, *others)})
        return gates.check_hierarchy(cfg or config(), a, ticket, body), a

    def rules(self, findings):
        return [f.split(": ")[1] for f in findings]

    def test_parent_without_test_plan_flagged(self):
        out, a = self.run_h(tk("TKT-1", "Story", ["TKT-2"]), "# T\n\n## Summary\nx\n",
                            [tk("TKT-2", "Sub-task")])
        self.assertEqual(self.rules(out), ["parent-test-plan"])
        self.assertTrue(out[0].startswith("hierarchy: parent-test-plan: "))
        self.assertIn(("TKT-2", False), a.views)

    def test_parent_with_test_plan_allowed(self):
        for heading in ("## Test plan", "### Testing:", "## TEST   Strategy",
                        "## Test plan ##"):
            out, a = self.run_h(tk("TKT-1", "Story", ["TKT-2"]),
                                f"# T\n\n{heading}\n- x\n",
                                [tk("TKT-2", "Sub-task")])
            self.assertEqual(out, [], heading)
            self.assertEqual(a.views, [], heading)   # no child lookups needed

    def test_children_fetched_when_view_skipped_them(self):
        full = tk("TKT-1", "Story", ["TKT-2"])
        a = FakeAdapter(tickets={"TKT-1": full, "TKT-2": tk("TKT-2", "Sub-task")})
        out = gates.check_hierarchy(config(), a, tk("TKT-1", "Story"), "# T\n")
        self.assertEqual(self.rules(out), ["parent-test-plan"])
        self.assertEqual(a.views[0], ("TKT-1", True))

    def test_children_lookup_failure_is_no_finding(self):
        a = FakeAdapter(view_raises=True)
        out = gates.check_hierarchy(config(), a, tk("TKT-1", "Story"), "# T\n")
        self.assertEqual(out, [])

    def test_subtask_skips_the_children_lookup(self):
        a = FakeAdapter()
        gates.check_hierarchy(config(), a, tk("TKT-2", "Sub-task"), "# S\n")
        self.assertEqual(a.views, [])

    def test_fenced_test_plan_does_not_count(self):
        body = "# T\n\n```md\n## Test plan\n```\n\n~~~\n## Testing\n~~~\n"
        out, _ = self.run_h(tk("TKT-1", "Story", ["TKT-2"]), body,
                            [tk("TKT-2", "Subtask")])
        self.assertEqual(self.rules(out), ["parent-test-plan"])

    def test_level_one_heading_does_not_count(self):
        out, _ = self.run_h(tk("TKT-1", "Story", ["TKT-2"]), "# Test plan\n",
                            [tk("TKT-2", "Sub-task")])
        self.assertEqual(self.rules(out), ["parent-test-plan"])

    def test_test_plan_in_comments_does_not_count(self):
        body = "# T\n\n## Comments\n### Test plan\n- later\n"
        out, _ = self.run_h(tk("TKT-1", "Story", ["TKT-2"]), body,
                            [tk("TKT-2", "Sub-task")])
        self.assertEqual(self.rules(out), ["parent-test-plan"])

    def test_epic_with_story_children_no_finding(self):
        out, _ = self.run_h(tk("TKT-1", "Epic", ["TKT-2", "TKT-3"]), "# E\n",
                            [tk("TKT-2", "Story"), tk("TKT-3", "Bug")])
        self.assertEqual(out, [])

    def test_failed_child_lookup_is_skipped(self):
        class Partial(FakeAdapter):
            def view(self, key, *, hierarchy=True):
                if key == "TKT-2":
                    raise RuntimeError("gone")
                return super().view(key, hierarchy=hierarchy)
        parent = tk("TKT-1", "Story", ["TKT-2", "TKT-3"])
        a = Partial(tickets={"TKT-3": tk("TKT-3", "sub task")})
        out = gates.check_hierarchy(config(), a, parent, "# T\n")
        self.assertEqual(self.rules(out), ["parent-test-plan"])
        self.assertIn("TKT-3", out[0])

    def test_test_plan_hidden_in_a_comment_block_does_not_count(self):
        for body in ("# T\n\n<!--\n## Test plan\n-->\n",
                     "# T\n\n<!-- template:\n## Testing\n- fill in -->\n",
                     "# T\n\n   <!--\n## Test plan -->\n"):
            self.assertFalse(gates.has_test_plan(body), body)
            out, _ = self.run_h(tk("TKT-1", "Story", ["TKT-2"]), body,
                                [tk("TKT-2", "Sub-task")])
            self.assertEqual(self.rules(out), ["parent-test-plan"], body)
            out, _ = self.run_h(tk("TKT-2", "Sub-task"), body)
            self.assertEqual(out, [], body)

    def test_frontmatter_is_not_ticket_content(self):
        # Frontmatter is skipped as the checker skips it: a YAML comment
        # that reads like a heading is not a plan, and keys are not words.
        body = "---\ntitle: many words in the frontmatter\n## Test plan\n---\n# T\n"
        self.assertFalse(gates.has_test_plan(body))
        self.assertEqual(gates.word_count(body), 1)       # T
        self.assertTrue(gates.has_test_plan(
            "---\nk: v\n...\n## Test plan\n- x\n"))

    def test_leading_break_before_prose_is_not_frontmatter(self):
        # Not YAML, or never closed: the "---" is a thematic break and what
        # follows is read.
        for body in ("---\nJust prose.\n---\n## Test plan\n",
                     "---\n## Test plan\n- x\n"):
            self.assertTrue(gates.has_test_plan(body), body)

    def test_heading_after_an_inline_comment_still_counts(self):
        # An inline comment that closes on its line hides only itself.
        self.assertTrue(gates.has_test_plan("## Test plan <!-- todo -->\n"))
        self.assertFalse(gates.has_test_plan("<!-- x --> ## Test plan\n"))

    def test_hidden_words_do_not_count_toward_the_limit(self):
        body = ("# S\n\nDo it.\n<!--\n" + "hidden " * 200 + "\n-->\n"
                "Inline <!-- " + "gone " * 50 + "--> kept\n")
        self.assertEqual(gates.word_count(body), 5)  # S, Do, it., Inline, kept
        out, _ = self.run_h(tk("TKT-2", "Sub-task"), body)
        self.assertEqual(out, [])

    def test_comment_marker_in_a_code_span_is_text(self):
        # Were the span's "<!--" a comment opener, it would hide up to "-->".
        body = "Write `<!--` then some words then `-->` done\n"
        self.assertEqual(gates.word_count(body), 6)
        # An unclosed mid-line marker is text too, and hides nothing.
        self.assertEqual(gates.word_count("a <!-- b\nc\n"), 3)

    def test_fence_closes_only_on_a_long_enough_run(self):
        body = "````\n```\n## Test plan\n```\n````\n"
        self.assertFalse(gates.has_test_plan(body))
        self.assertTrue(gates.has_test_plan(body + "## Testing\n"))

    def test_subtask_too_long_flagged(self):
        body = "# S\n\n" + "word " * 151
        out, _ = self.run_h(tk("TKT-2", "Sub-task"), body)
        self.assertEqual(self.rules(out), ["subtask-too-long"])

    def test_subtask_at_limit_allowed(self):
        body = "word " * 148 + "\n# S\n"         # 150 words with the title
        out, _ = self.run_h(tk("TKT-2", "Sub-task"), body)
        self.assertEqual(out, [])

    def test_subtask_word_count_skips_fences_comments_and_symbols(self):
        body = ("# S\n\n- " + "w " * 100 + "\n| --- | --- |\n"
                "```\n" + "code " * 200 + "\n```\n"
                "## Comments\n" + "chat " * 200 + "\n")
        self.assertEqual(gates.word_count(body), 101)
        out, _ = self.run_h(tk("TKT-2", "Subtask"), body)
        self.assertEqual(out, [])

    def test_section_after_comments_counts_again(self):
        body = "## Comments\nchat chat\n## Notes\none two\n"
        self.assertEqual(gates.word_count(body), 3)   # Notes, one, two

    def test_subtask_with_test_plan_flagged(self):
        out, _ = self.run_h(tk("TKT-2", "Sub-task"), "# S\n\n## Testing\n- x\n")
        self.assertEqual(self.rules(out), ["subtask-test-plan"])

    def test_subtask_short_without_plan_allowed(self):
        out, _ = self.run_h(tk("TKT-2", "Sub-task"), "# S\n\nDo the thing.\n")
        self.assertEqual(out, [])

    def test_custom_subtask_types(self):
        cfg = config({"subtask_types": ["Chunk"]})
        out, _ = self.run_h(tk("TKT-2", "chunk"), "## Test plan\n", cfg=cfg)
        self.assertEqual(self.rules(out), ["subtask-test-plan"])
        out, _ = self.run_h(tk("TKT-2", "Sub-task"), "## Test plan\n", cfg=cfg)
        self.assertEqual(out, [])

    def test_unjudgeable_body_returns_empty(self):
        t = tk("TKT-1", "Story", ["TKT-2"])
        for body in (None, "", "  \n"):
            out, a = self.run_h(t, body, [tk("TKT-2", "Sub-task")])
            self.assertEqual(out, [])
            self.assertEqual(a.views, [])

    def test_hierarchy_findings_never_block_in_strict(self):
        parent = tk("TKT-1", "Story", ["TKT-2"])
        a = FakeAdapter(tickets={"TKT-1": parent, "TKT-2": tk("TKT-2", "Sub-task")},
                        bodies={"TKT-1": "# T\n"})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            gates.gate_transition(config({"artifact_format": "strict"}), a,
                                  "TKT-1", "review", runner=FakeRunner(""),
                                  which=has_node, script=Path(__file__))
        lines = err.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith(
            "tkt: warn: hierarchy: parent-test-plan: "), lines)

    def test_hierarchy_runs_without_node(self):
        a = FakeAdapter(tickets={"TKT-2": tk("TKT-2", "Sub-task")},
                        bodies={"TKT-2": "## Test plan\n"})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            gates.gate_transition(config(), a, "TKT-2", "done",
                                  runner=FakeRunner(), which=no_node)
        self.assertIn("tkt: warn: hierarchy: subtask-test-plan", err.getvalue())


class ArtifactBody(unittest.TestCase):

    def test_base_default_is_none(self):
        class Bare(Adapter):
            whoami = list = view = transition = comment = None
            blockers = worklog = lane_time = doctor = None
        Bare.__abstractmethods__ = frozenset()
        a = Bare(config())
        self.assertIsNone(a.artifact_body(Ticket(key="TKT-1",
                                                 description="text")))
        self.assertFalse(a.has_artifact_body)

    def test_jira_is_none(self):
        j = JiraAdapter.__new__(JiraAdapter)
        self.assertIsNone(j.artifact_body(Ticket(key="P-1", description="## A")))
        self.assertFalse(JiraAdapter.has_artifact_body)

    def test_description_backends(self):
        from adapters.github import GithubAdapter
        from adapters.linear import LinearAdapter
        from adapters.openkanban import OpenKanbanAdapter
        t = Ticket(key="X-1", description="## Summary\nbody")
        for cls in (GithubAdapter, LinearAdapter, OpenKanbanAdapter):
            self.assertTrue(cls.has_artifact_body, cls)
            self.assertEqual(cls.artifact_body(cls.__new__(cls), t),
                             "## Summary\nbody", cls)

    def test_markdown_returns_the_full_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            board = Path(tmp) / "board"
            board.mkdir()
            (board / "TKT-1.md").write_text(
                "---\ntype: Story\nstatus: To Do\n---\n# Title\n\nThesis.\n\n"
                "## Summary\nsum\n\n## Test plan\n- run it\n\n"
                "## Comments\n- c\n")
            cfg = Config({"ticketing": {"provider": "markdown", "project": "TKT"},
                          "board": {"roles": dict(ROLES)},
                          "markdown": {"board_dir": str(board)}},
                         Path(tmp) / "config.toml")
            a = MarkdownAdapter(cfg)
            t = a.view("TKT-1")
            self.assertTrue(a.has_artifact_body)
            body = a.artifact_body(t)
            self.assertTrue(body.startswith("# Title\n"))
            self.assertIn("## Summary\nsum", body)
            self.assertIn("## Test plan\n- run it", body)
            self.assertNotIn("status:", body)
            self.assertNotIn("## Comments", body)
            self.assertNotIn("- c", body)
            self.assertNotIn("Test plan", t.description)

    def markdown(self, tmp, text):
        board = Path(tmp) / "board"
        board.mkdir()
        (board / "TKT-1.md").write_text(text)
        cfg = Config({"ticketing": {"provider": "markdown", "project": "TKT"},
                      "board": {"roles": dict(ROLES)},
                      "markdown": {"board_dir": str(board)}},
                     Path(tmp) / "config.toml")
        a = MarkdownAdapter(cfg)
        return a, a.view("TKT-1")

    def test_markdown_fenced_comments_heading_does_not_truncate(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, t = self.markdown(tmp, "---\ntype: Story\n---\n# T\n\n"
                                 "## Summary\n```md\n## Comments\n```\n"
                                 "## Behavior\nb\n\n## Comments\n- c\n")
            body = a.artifact_body(t)
            self.assertIn("```md\n## Comments\n```", body)
            self.assertIn("## Behavior\nb", body)
            self.assertNotIn("- c", body)

    def test_markdown_lookalike_comments_headings_do_not_truncate(self):
        lookalikes = {
            "html block": "<!--\n## Comments\n-->\n",
            "long fence": "````md\n```\n## Comments\n```\n````\n",
            "longer name": "## Comments on design\nnotes\n",
            "inline html": "<!-- ## Comments -->\n",
        }
        for label, chunk in lookalikes.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp:
                a, t = self.markdown(tmp, "---\ntype: Story\n---\n# T\n\n"
                                     "## Summary\ns\n\n" + chunk +
                                     "\n## Behavior\nb\n\n## Comments\n- c\n")
                body = a.artifact_body(t)
                self.assertIn("## Behavior\nb", body)
                self.assertIn(chunk.strip(), body)
                self.assertNotIn("- c", body)     # the real log is still cut

    def test_managed_tail_start(self):
        self.assertEqual(gates.managed_tail_start("a\n## Comments\nx", "comments"), 1)
        self.assertEqual(gates.managed_tail_start("## comments:\n", "Comments"), 0)
        self.assertIsNone(gates.managed_tail_start("### Comments\n", "comments"))
        self.assertIsNone(gates.managed_tail_start("## Commentary\n", "comments"))
        self.assertIsNone(gates.managed_tail_start("", "comments"))

    def test_markdown_comment_headings_are_not_judged(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, t = self.markdown(tmp, "---\ntype: Sub-task\n---\n# T\n\n"
                                 "Short.\n")
            a.comment("TKT-1", "Self-review done.\n\n## Testing\n"
                      + "word " * 200)
            body = a.artifact_body(t)
            self.assertNotIn("## Testing", body)
            self.assertFalse(gates.has_test_plan(body))
            out = gates.check_hierarchy(config(), a, a.view("TKT-1"), body)
            self.assertEqual(out, [])       # neither too long nor a test plan


class OpenKanbanActivity(unittest.TestCase):
    """openkanban keeps comments as an `## Activity` log in the description;
    the gate must judge the description without it."""

    PID = "proj-1"
    DESC = ("The thesis.\n\n## Summary\nWhat.\n\n## Behavior\nHow.\n\n"
            "## References\n- none")

    def setUp(self):
        from adapters.openkanban import OpenKanbanAdapter
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "projects.json").write_text(json.dumps(
            {"projects": {self.PID: {"id": self.PID, "name": "demo"}}}))
        (root / "tickets").mkdir()
        (root / "tickets" / f"{self.PID}.json").write_text(json.dumps(
            {"project_id": self.PID, "updated_at": "2026-01-01T00:00:00Z",
             "tickets": {"t1": {"id": "t1", "project_id": self.PID,
                                "title": "T", "description": self.DESC,
                                "status": "in_progress", "labels": ["Story"],
                                "priority": 3, "meta": {}}}}))
        self.cfg = Config({"ticketing": {"provider": "openkanban"},
                           "openkanban": {"project": "demo",
                                          "config_dir": str(root)},
                           "issue_types": {"full_sdlc": ["Story"]},
                           "board": {"roles": {"todo": "backlog",
                                               "in_progress": "in_progress",
                                               "done": "done"}},
                           "enforcement": {"artifact_format": "strict"}},
                          root / ".sdlc" / "config.toml")
        self.a = OpenKanbanAdapter(self.cfg)

    def test_activity_log_is_cut(self):
        self.a.comment("t1", "Self-review done.\n\n## Findings\nnone")
        self.a.comment("t1", "second")
        t = self.a.view("t1")
        self.assertIn("## Activity", t.description)
        body = self.a.artifact_body(t)
        self.assertEqual(body, self.DESC + "\n")

    def set_desc(self, desc):
        path = Path(self.cfg.get("openkanban.config_dir")) / "tickets" / f"{self.PID}.json"
        store = json.loads(path.read_text())
        store["tickets"]["t1"]["description"] = desc
        path.write_text(json.dumps(store))

    def test_lookalike_activity_headings_still_start_a_log(self):
        # A fenced `## Activity` example or an `### Activity` subheading is
        # ticket content: `comment` must open a real log after it, which
        # `artifact_body` then cuts.
        for extra in ("\n\n```\n## Activity\n```", "\n\n### Activity\nnotes",
                      "\n\n## Activity log\nnotes",
                      "\n\n<!--\n## Activity\n-->"):
            desc = self.DESC + extra
            self.set_desc(desc)
            self.a.comment("t1", "Self-review done.\n\n## Findings\nnone")
            t = self.a.view("t1")
            self.assertIn("\n\n## Activity\n- ", t.description, extra)
            self.assertEqual(self.a.artifact_body(t), desc + "\n", extra)

    def test_frontmatter_activity_comment_is_not_the_log(self):
        # A YAML comment reading `## Activity` is frontmatter, not the log:
        # nothing is cut, and `comment` opens a real log at the end.
        desc = "---\n## Activity\n---\n" + self.DESC
        self.set_desc(desc)
        self.assertEqual(self.a.artifact_body(self.a.view("t1")), desc)
        self.a.comment("t1", "first")
        t = self.a.view("t1")
        self.assertIn(self.DESC + "\n\n## Activity\n- ", t.description)
        self.assertEqual(self.a.artifact_body(t), desc + "\n")

    def test_no_activity_returns_the_description(self):
        self.assertEqual(self.a.artifact_body(self.a.view("t1")), self.DESC)

    @unittest.skipUnless(shutil.which("node"), "node not on PATH")
    def test_comment_then_strict_transition_passes(self):
        self.a.comment("t1", "Self-review done.\n\n## Findings\nnone")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            gates.gate_transition(self.cfg, self.a, "t1", "done")
            self.a.transition("t1", "done")
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(self.a.view("t1").status, "done")


MARKDOWN_TOML = """
[ticketing]
provider = "markdown"
project = "TKT"

[markdown]
board_dir = "{board}"
state_dir = "{state}"
me = "tester"

[board.roles]
todo = "To Do"
in_progress = "In Progress"
review = "In Review"
done = "Done"

[queries]
tier1 = 'status = "To Do"'
{extra}
"""

TICKET = "---\ntype: Story\nstatus: In Progress\n---\n# Title\n\nNo sections here.\n"


class Cli(unittest.TestCase):
    """Real argparse, Config.load and markdown adapter through cli.main;
    the checker subprocess and node lookup are stubbed."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.board = self.root / "board"
        self.board.mkdir()
        (self.board / "TKT-1.md").write_text(TICKET)
        self.script = self.root / "check.mjs"
        self.script.write_text("// stub\n")
        (self.root / "artifact-format.rules.json").write_text(json.dumps(
            {"version": 1, "presets": {"standard": {}, "problem-proposal": {}}}))

    def run_cli(self, extra, *argv, runner=None, which=has_node):
        cfg = self.root / "config.toml"
        cfg.write_text(MARKDOWN_TOML.format(board=self.board,
                                            state=self.root / "state",
                                            extra=extra), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(gates, "CHECKER", self.script), \
                mock.patch.object(gates, "_run_process",
                                  runner or FakeRunner("")), \
                mock.patch.object(gates, "_which", which), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--config", str(cfg), *argv])
        return code, out.getvalue(), err.getvalue()

    def status(self):
        text = (self.board / "TKT-1.md").read_text()
        return text.split("status: ")[1].split("\n")[0]

    def test_warn_transition_json_stdout_unaffected(self):
        r = FakeRunner("ticket: missing-section: no Summary\n")
        code, out, err = self.run_cli("", "--json", "transition", "TKT-1",
                                      "In Review", runner=r)
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "TKT-1 -> In Review\n")
        self.assertEqual(err, "tkt: warn: ticket: missing-section: no Summary\n")
        self.assertEqual(self.status(), "In Review")
        self.assertIn("No sections here.", r.calls[0][1]["input"])

    def test_strict_transition_exits_5_and_does_not_move(self):
        r = FakeRunner("ticket: missing-section: no Summary\n")
        code, out, err = self.run_cli('[enforcement]\nartifact_format = "strict"',
                                      "transition", "TKT-1", "review", runner=r)
        self.assertEqual(code, 5)
        self.assertEqual(out, "")
        self.assertIn("tkt: TKT-1: transition to review blocked", err)
        self.assertEqual(self.status(), "In Progress")

    def test_strict_comment_exits_5_and_does_not_post(self):
        r = FakeRunner("comment: word-budget: too long\n")
        code, out, err = self.run_cli('[enforcement]\nartifact_format = "strict"',
                                      "comment", "TKT-1", "words", runner=r)
        self.assertEqual(code, 5)
        self.assertNotIn("## Comments", (self.board / "TKT-1.md").read_text())

    def test_bad_table_is_exit_2(self):
        code, _, err = self.run_cli('[enforcement]\nartifact_format = "loud"',
                                    "comment", "TKT-1", "hi")
        self.assertEqual(code, 2, err)
        self.assertIn("artifact_format", err)

    def doctor_row(self, extra, which=has_node):
        code, out, _ = self.run_cli(extra, "--json", "doctor", which=which)
        rows = {c["name"]: c for c in json.loads(out)}
        return code, rows["enforcement"]

    def test_doctor_default_row(self):
        code, row = self.doctor_row("")
        self.assertTrue(row["ok"])
        self.assertEqual(row["detail"], "warn, preset standard")

    def test_doctor_by_type_and_strict(self):
        _, row = self.doctor_row('[enforcement]\nartifact_format = "strict"\n'
                                 '[enforcement.presets_by_type]\n'
                                 'Story = "problem-proposal"')
        self.assertTrue(row["ok"])
        self.assertEqual(row["detail"], "strict, preset standard, 1 by type")

    def test_doctor_off(self):
        _, row = self.doctor_row('[enforcement]\nartifact_format = "off"')
        self.assertEqual((row["ok"], row["detail"]), (True, "off"))

    def test_doctor_node_missing_is_ok_but_inactive(self):
        code, row = self.doctor_row("", which=no_node)
        self.assertTrue(row["ok"])
        self.assertIn("inactive: node not found", row["detail"])

    def test_doctor_checker_missing_is_ok_but_inactive(self):
        self.script.unlink()
        _, row = self.doctor_row("")
        self.assertTrue(row["ok"])
        self.assertIn("inactive: checker missing", row["detail"])

    def test_doctor_fails_when_the_rules_file_is_missing(self):
        (self.root / "artifact-format.rules.json").unlink()
        code, row = self.doctor_row('[enforcement]\nartifact_format = "strict"')
        self.assertEqual(code, 1)
        self.assertFalse(row["ok"])
        self.assertIn("rules file missing", row["detail"])
        self.assertIn("format gate cannot run", row["detail"])

    def test_doctor_fails_an_invalid_table(self):
        code, row = self.doctor_row('[enforcement]\nmode = "strict"')
        self.assertEqual(code, 1)
        self.assertFalse(row["ok"])
        self.assertIn("unknown key", row["detail"])

    def test_doctor_fails_an_unknown_preset(self):
        code, row = self.doctor_row('[enforcement]\npreset = "nope"')
        self.assertEqual(code, 1)
        self.assertFalse(row["ok"])
        self.assertIn("nope", row["detail"])

    def test_doctor_fails_an_unknown_by_type_preset(self):
        _, row = self.doctor_row('[enforcement.presets_by_type]\nEpic = "epic"')
        self.assertFalse(row["ok"])
        self.assertIn("epic", row["detail"])

    def test_real_rules_file_defines_the_default_preset(self):
        presets = json.loads(gates.CHECKER.with_name(gates.RULES_NAME)
                             .read_text())["presets"]
        self.assertIn("standard", presets)


@unittest.skipUnless(shutil.which("node"), "node not on PATH")
class EndToEnd(unittest.TestCase):
    """The real checker, through cli.main on a markdown board."""

    def test_real_checker_warns_then_strict_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            board = root / "board"
            board.mkdir()
            (board / "TKT-1.md").write_text(TICKET)
            cfg = root / "config.toml"

            def run(extra, *argv):
                cfg.write_text(MARKDOWN_TOML.format(board=board, state=root / "s",
                                                    extra=extra))
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = cli.main(["--config", str(cfg), *argv])
                return code, err.getvalue()

            code, err = run('[enforcement]\nartifact_format = "strict"',
                            "transition", "TKT-1", "review")
            self.assertEqual(code, 5, err)
            self.assertIn("tkt: error: ticket: missing-section:", err)
            self.assertIn("status: In Progress", (board / "TKT-1.md").read_text())

            code, err = run("", "transition", "TKT-1", "review")
            self.assertEqual(code, 0, err)
            self.assertIn("tkt: warn: ticket: missing-section:", err)
            self.assertIn("status: In Review", (board / "TKT-1.md").read_text())

            code, err = run("", "comment", "TKT-1", "short and fine")
            self.assertEqual((code, err), (0, ""))

    def test_comment_with_a_heading_does_not_block_strict(self):
        # Reproduced in review: a well-formed ticket, then a multi-line
        # comment holding "## Findings", used to fail strict with
        # last-section ("## Findings" after "## References").
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            board = root / "board"
            board.mkdir()
            (board / "TKT-1.md").write_text(
                "---\ntype: Story\nstatus: In Progress\n---\n# Title\n\n"
                "The thesis.\n\n## Summary\nWhat.\n\n## Behavior\nHow.\n\n"
                "## References\n- none\n")
            cfg = root / "config.toml"
            cfg.write_text(MARKDOWN_TOML.format(
                board=board, state=root / "s",
                extra='[enforcement]\nartifact_format = "strict"'))

            def run(*argv):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = cli.main(["--config", str(cfg), *argv])
                return code, err.getvalue()

            self.assertEqual(run("comment", "TKT-1",
                                 "Self-review done.\n\n## Findings\nnone"),
                             (0, ""))
            self.assertIn("## Findings", (board / "TKT-1.md").read_text())
            self.assertEqual(run("transition", "TKT-1", "review"), (0, ""))
            self.assertIn("status: In Review", (board / "TKT-1.md").read_text())


if __name__ == "__main__":
    unittest.main()
