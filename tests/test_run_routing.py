"""`tkt run` honouring `[routes]` per phase, and the `TKT-RESULT:` stdout
hand-back for harnesses that cannot write result.json.

Fully offline: markdown adapter in a temp dir, and a stub harness that
records its argv (minus the prompt) and replays a scripted sequence of
result.json writes and stdout/stderr text.
"""
import io
import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config  # noqa: E402
from core.errors import ConfigError  # noqa: E402
from core.run import (  # noqa: E402
    PHASES, PHASE_NAMES, PHASE_ROUTES, RunConfig, RunDriver,
    build_phase_prompt, invoke_harness, phase_command, read_result_line,
)

STUB = """\
#!{python}
# Records argv[1:-1] (the prompt is last), then plays the next scripted step.
import json, re, sys
calls, seq = {calls!r}, {seq!r}
with open(calls, "a") as fh:
    fh.write(json.dumps(sys.argv[1:-1]) + "\\n")
steps = json.load(open(seq))
if not steps:
    sys.exit(1)
step, rest = steps[0], steps[1:]
json.dump(rest, open(seq, "w"))
if step.get("file") is not None:
    path = re.search(r"\\S*result\\.json", sys.argv[-1]).group(0)
    open(path, "w").write(json.dumps(step["file"]))
sys.stdout.write(step.get("stdout", ""))
sys.stderr.write(step.get("stderr", ""))
"""

ROUTED = """\
[harnesses.codex]
cmd = "{stub} codex --model {model} {prompt}"

[routes]
self-review = { harness = "codex", model = "gpt-5.5" }
"""


def res(phase, outcome, nxt="", reason="r"):
    return {"phase": phase, "outcome": outcome, "next": nxt or phase,
            "reason": reason}


class RunRoutingBase(unittest.TestCase):
    harness_cmd = "{stub} default {prompt}"

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.stub = self.tmp / "stub.py"
        self.calls = self.tmp / "calls.jsonl"
        self.seq = self.tmp / "seq.json"

    def tearDown(self):
        self._td.cleanup()

    def env(self, steps, extra="", harness_cmd=None):
        # A bare result dict is shorthand for a step that writes result.json.
        steps = [s if {"file", "stdout", "stderr"} & s.keys() else {"file": s}
                 for s in steps]
        self.seq.write_text(json.dumps(steps))
        self.stub.write_text(STUB.format(python=sys.executable,
                                         calls=str(self.calls),
                                         seq=str(self.seq)))
        self.stub.chmod(self.stub.stat().st_mode | stat.S_IEXEC)
        sdlc = self.tmp / ".sdlc"
        board = sdlc / "board"
        board.mkdir(parents=True)
        (sdlc / "state").mkdir()
        cmd = (harness_cmd or self.harness_cmd).replace("{stub}", str(self.stub))
        (sdlc / "config.toml").write_text(textwrap.dedent(f"""\
            [ticketing]
            provider = "markdown"
            project  = "TKT"

            [markdown]
            board_dir = "{board}"
            state_dir = "{sdlc / 'state'}"
            me        = "testbot"

            [board.roles]
            todo        = "To Do"
            in_progress = "In Progress"
            review      = "In Review"
            qa_ready    = "QA Ready"
            done        = "Done"
            blocked     = "Blocked"

            [timetracking]
            provider = "none"

            [run]
            harness_cmd = "{cmd}"
            max_phase_attempts = 3
            invocation_timeout = 20
            heartbeat_interval = 0
        """) + "\n" + extra.replace("{stub}", str(self.stub)))
        (board / "TKT-1.md").write_text(textwrap.dedent("""\
            ---
            type: Story
            status: In Progress
            priority: High
            assignee: testbot
            blocked_by: []
            blocks: []
            ---
            # Test ticket TKT-1
        """))
        self.config = Config.load(str(sdlc / "config.toml"))
        return self.config

    def driver(self, phase=None, dry_run=False):
        d = RunDriver(self.config, RunConfig(self.config), key="TKT-1",
                      dry_run=dry_run)
        if phase:
            d.phase, d.attempt = phase, 1
            d._init_state()
        return d

    def executed(self):
        if not self.calls.exists():
            return []
        return [json.loads(l) for l in self.calls.read_text().splitlines()]

    def log(self, d):
        return [json.loads(l)
                for l in (d.state_dir / "run.log").read_text().splitlines()]


class TestPhaseRouteMap(unittest.TestCase):
    def test_every_phase_has_a_route_key(self):
        self.assertEqual(list(PHASE_ROUTES), PHASES)

    def test_phase_names_unchanged(self):
        # Marker text; a resume parses it, so the route map must not leak in.
        self.assertEqual(PHASE_NAMES["P1"], "triage")
        self.assertEqual(PHASE_NAMES["P3"], "implement/test")


class TestRoutedInvocation(RunRoutingBase):
    def test_routed_phase_uses_routed_cmd_others_default(self):
        self.env([res("P1", "advance", "P4"), res("P4", "advance", "P5"),
                  res("P5", "gate")], ROUTED)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(self.driver().run(), 0)
        self.assertEqual(self.executed(), [
            ["default"], ["codex", "--model", "gpt-5.5"], ["default"]])

    def test_tier_route_fills_model_from_models(self):
        extra = ROUTED.replace('model = "gpt-5.5"', 'tier = "deep"') + \
            '\n[models]\ndeep = "o-deep"\n'
        self.env([], extra)
        cmd, harness = phase_command(self.config, RunConfig(self.config), "P4")
        self.assertEqual(harness, "codex")
        self.assertEqual(cmd, f"{self.stub} codex --model o-deep {{prompt}}")

    def test_log_entry_carries_harness(self):
        self.env([res("P4", "advance", "P5"), res("P5", "gate")], ROUTED)
        d = self.driver("P4")
        with redirect_stderr(io.StringIO()):
            self.assertEqual(d._loop(), 0)
        self.assertEqual([e["harness"] for e in self.log(d)], ["codex", ""])

    def test_dry_run_shows_routed_cmd(self):
        self.env([], ROUTED)
        d = self.driver("P4", dry_run=True)
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(d._loop(), 0)
        self.assertIn(f"{self.stub} codex --model gpt-5.5 '", err.getvalue())
        self.assertEqual(self.executed(), [])


class TestUnroutedUnchanged(RunRoutingBase):
    # A literal {model} in harness_cmd must survive: unrouted phases are
    # byte-for-byte what they were before routes existed.
    harness_cmd = "{stub} default {model} {prompt}"

    def test_every_phase_gets_harness_cmd_verbatim(self):
        self.env([], '[models]\ndeep = "o-deep"\nstandard = "o-std"\n')
        rc = RunConfig(self.config)
        for phase in PHASES:
            self.assertEqual(phase_command(self.config, rc, phase),
                             (rc.harness_cmd, ""), phase)

    def test_driver_invokes_identical_cmd(self):
        self.env([res("P1", "advance", "P2"), res("P2", "advance", "P4"),
                  res("P4", "gate")], '[models]\ndeep = "o-deep"\n')
        with redirect_stderr(io.StringIO()):
            self.assertEqual(self.driver().run(), 0)
        self.assertEqual(self.executed(), [["default", "{model}"]] * 3)

    def test_non_phase_route_key_ignored(self):
        # qa-author is interactive-only; even a tier with no [models] entry
        # is doctor's business, not the driver's.
        self.env([res("P1", "gate")],
                 '[routes]\nqa-author = { tier = "deep" }\n')
        with redirect_stderr(io.StringIO()):
            self.assertEqual(self.driver().run(), 0)
        self.assertEqual(self.executed(), [["default", "{model}"]])


class TestFailFast(RunRoutingBase):
    def assert_fails_before_invoking(self, extra, dry_run=False):
        self.env([res("P1", "gate")], extra)
        with self.assertRaises(ConfigError):
            self.driver(dry_run=dry_run).run()
        self.assertEqual(self.executed(), [])

    def test_unknown_harness(self):
        self.assert_fails_before_invoking(
            '[routes]\nself-review = { harness = "nope" }\n')

    def test_tier_without_models_entry(self):
        # P4 is not the first phase run: resolution happens up front.
        self.assert_fails_before_invoking(
            '[routes]\nself-review = { tier = "deep" }\n')

    def test_bad_harness_table(self):
        self.assert_fails_before_invoking(
            '[harnesses.codex]\ncmd = "codex exec"\n')

    def test_dry_run_also_fails(self):
        self.assert_fails_before_invoking(
            '[routes]\nci-fix = { harness = "nope" }\n', dry_run=True)


class TestStdoutResult(RunRoutingBase):
    def run_p4(self, steps):
        self.env(steps)
        d = self.driver("P4")
        with redirect_stderr(io.StringIO()):
            rc = d._loop()
        return d, rc, [e["outcome"] for e in self.log(d)]

    def line(self, data):
        return "TKT-RESULT: " + json.dumps(data) + "\n"

    def test_stdout_line_advances(self):
        d, rc, outcomes = self.run_p4([
            {"stdout": "working...\n" + self.line(res("P4", "advance", "P5"))},
            {"file": res("P5", "gate")}])
        self.assertEqual((rc, outcomes), (0, ["advance", "gate"]))
        self.assertEqual(d.phase, "P5")

    def test_bad_lines_retry(self):
        bad = [
            'TKT-RESULT: {"phase": "P4", "outcome": \n',     # malformed
            "TKT-RESULT:" + json.dumps(res("P4", "advance")) + "\n",  # no space
            "tkt-result: " + json.dumps(res("P4", "advance")) + "\n",
            "RESULT: " + json.dumps(res("P4", "advance")) + "\n",
            self.line(res("P4", "done")),                       # bad outcome
            self.line({"outcome": "advance"}),                  # no phase
            "TKT-RESULT: [1, 2]\n",
        ]
        for text in bad:
            with self.subTest(text=text):
                self.tearDown()
                self.setUp()
                _, rc, outcomes = self.run_p4([{"stdout": text},
                                               {"file": res("P4", "gate")}])
                self.assertEqual((rc, outcomes), (0, ["retry", "gate"]))

    def test_stderr_line_does_not_count(self):
        _, _, outcomes = self.run_p4([
            {"stderr": self.line(res("P4", "advance", "P5"))},
            {"file": res("P4", "gate")}])
        self.assertEqual(outcomes, ["retry", "gate"])

    def test_result_file_wins(self):
        d, rc, outcomes = self.run_p4([
            {"file": res("P4", "gate"),
             "stdout": self.line(res("P4", "advance", "P5"))}])
        self.assertEqual((rc, outcomes), (0, ["gate"]))
        self.assertEqual(d.phase, "P4")

    def test_invalid_file_falls_back_to_stdout(self):
        _, _, outcomes = self.run_p4([
            {"file": {"phase": "P4", "outcome": "bogus"},
             "stdout": self.line(res("P4", "gate"))}])
        self.assertEqual(outcomes, ["gate"])

    def test_last_line_wins(self):
        _, _, outcomes = self.run_p4([
            {"stdout": self.line(res("P4", "retry")) + "more\n"
             + "  " + self.line(res("P4", "gate")).rstrip() + "  \ntrailer\n"}])
        self.assertEqual(outcomes, ["gate"])

    def test_line_for_another_phase_is_not_a_result(self):
        line = self.line(res("P2", "advance"))
        self.assertIsNone(read_result_line(line, "P4"))
        self.assertIsNotNone(read_result_line(line, "P2"))

    def test_driver_ignores_a_line_for_another_phase(self):
        _, _, outcomes = self.run_p4([{"stdout": self.line(res("P2", "advance"))}])
        self.assertTrue(outcomes)
        self.assertEqual(set(outcomes), {"retry"})

    def test_unicode_line_separator_inside_json(self):
        line = "TKT-RESULT: " + json.dumps(
            {"phase": "P4", "outcome": "advance", "reason": "a\u2028b"},
            ensure_ascii=False) + "\n"
        self.assertEqual(read_result_line(line, "P4")["reason"], "a\u2028b")

    def test_deeply_nested_json_is_rejected_not_raised(self):
        self.assertIsNone(read_result_line("TKT-RESULT: " + "[" * 100000))

    def test_invalid_last_line_does_not_fall_back(self):
        self.assertIsNone(read_result_line(
            self.line(res("P4", "advance")) + "TKT-RESULT: {oops}\n"))


class TestPromptAndInvoke(unittest.TestCase):
    def test_prompt_mentions_stdout_fallback(self):
        prompt = build_phase_prompt("TKT-5", "P4", 1, Path("/x"))
        self.assertIn("If you cannot write files", prompt)
        self.assertIn("prefixed `TKT-RESULT: `", prompt)
        self.assertIn("After writing result.json, exit.", prompt)

    def test_invoke_keeps_stdout_apart(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = invoke_harness("echo out; echo err >&2", "p", 10,
                                     Path(tmp))
        self.assertEqual(rc, 0)
        self.assertIn("out", out)
        self.assertIn("err", out)
        self.assertEqual(out.stdout, "out\n")


if __name__ == "__main__":
    unittest.main()
