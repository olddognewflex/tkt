"""TKT-103: the skill guards that keep a container (and a ticket with open
children) away from the agent.

Runs complete-deliverable's step 0 guard, automated-sdlc's Phase 1.5
block and check-blockers' step 7, extracted from the canonical SKILL.md files, under every available
shell against a stub `tkt`. The stub answers only the verbs a spec names,
so a guard that tried to transition or comment would fail.
Needs `jq`.

    python3 -m unittest tests.test_container_guards
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHELLS = [s for s in ("bash", "zsh", "dash") if shutil.which(s)]

STUB = f"""#!{sys.executable}
import json, os, sys
spec = json.load(open(os.environ["STUB_SPEC"]))
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
verb = sys.argv[1]
if verb not in spec:
    sys.stderr.write(f"stub: unexpected verb {{verb}}\\n")
    sys.exit(64)
r = spec[verb]
sys.stdout.write(r.get("out", ""))
sys.stderr.write(r.get("err", ""))
sys.exit(r.get("rc", 0))
"""


def block(skill: str, start: str, end: str) -> str:
    text = (ROOT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    section = text[text.index(start):text.index(end)]
    m = re.search(r"```shell\n(.*?)\n```", section, re.DOTALL)
    assert m, f"no shell block in {skill} {start}"
    return m.group(1)


GUARD = block("complete-deliverable", "### 0. Guard", "### 1.")
PHASE_15 = block("automated-sdlc", "### Phase 1.5", "### Phase 2")
STEP_7 = block("check-blockers", "### 7.", "## Output")


def ticket(type_class="deliverable", rollup=None):
    # Escapes in the body are normal ticket content (see test_no_echo_jq).
    return json.dumps({"key": "T-1", "type_class": type_class, "rollup": rollup,
                       "description": "line one\nline two \\c", "transitions": []})


@unittest.skipUnless(SHELLS and shutil.which("jq"), "needs a POSIX shell and jq")
class Guards(unittest.TestCase):
    def run_block(self, code, spec):
        """{shell: (exit code, stdout, stderr, calls)}."""
        results = {}
        for shell in SHELLS:
            with tempfile.TemporaryDirectory() as tmp:
                t = Path(tmp)
                (t / "bin").mkdir()
                stub = t / "bin/tkt"
                stub.write_text(STUB)
                stub.chmod(0o755)
                (t / "spec.json").write_text(json.dumps(spec))
                env = dict(os.environ, PATH=f"{t / 'bin'}:{os.environ['PATH']}",
                           STUB_SPEC=str(t / "spec.json"),
                           STUB_LOG=str(t / "calls.log"), KEY="T-1")
                p = subprocess.run([shell, "-c", code], env=env, text=True,
                                   capture_output=True, timeout=60)
                log = (t / "calls.log").read_text() if (t / "calls.log").exists() else ""
                results[shell] = (p.returncode, p.stdout, p.stderr, log)
        return results

    def assert_only_reads(self, log):
        for line in log.splitlines():
            self.assertIn(line.split()[0], ("view", "children", "cfg", "list"), line)

    # ---- complete-deliverable step 0 ---------------------------------------

    def test_guard_stops_a_container_without_checking_children(self):
        spec = {"view": {"out": ticket("container")}}
        for shell, (rc, out, err, log) in self.run_block(GUARD, spec).items():
            with self.subTest(shell=shell):
                self.assertIn("STOP: T-1 is a container", out)
                self.assertNotIn("children", log)
                self.assert_only_reads(log)

    def test_guard_stops_on_an_open_child(self):
        spec = {"view": {"out": ticket()},
                "children": {"out": json.dumps([{"key": "T-2", "closed": True},
                                                {"key": "T-3", "closed": False}])}}
        for shell, (rc, out, err, log) in self.run_block(GUARD, spec).items():
            with self.subTest(shell=shell):
                self.assertIn("STOP: T-1 has open children", out)
                self.assert_only_reads(log)

    def test_guard_stops_when_children_cannot_be_checked(self):
        spec = {"view": {"out": ticket()},
                "children": {"rc": 3, "err": "tkt: rate limited\n"}}
        for shell, (rc, out, err, log) in self.run_block(GUARD, spec).items():
            with self.subTest(shell=shell):
                self.assertIn("STOP: could not check the children of T-1", out)

    def test_guard_stops_when_the_ticket_cannot_be_read(self):
        # Copilot on PR #105: a failed view plus an empty child list used
        # to pass, letting a container through to the Done transition.
        spec = {"view": {"rc": 3, "err": "tkt: network error\n"},
                "children": {"out": "[]"}}
        for shell, (rc, out, err, log) in self.run_block(GUARD, spec).items():
            with self.subTest(shell=shell):
                self.assertIn("STOP: could not read T-1", out)
                self.assertNotIn("children", log)

    def test_guard_stops_when_the_type_cannot_be_read(self):
        for body in ("not json", json.dumps({"key": "T-1"})):
            spec = {"view": {"out": body}, "children": {"out": "[]"}}
            for shell, (rc, out, err, log) in self.run_block(GUARD, spec).items():
                with self.subTest(shell=shell, body=body):
                    self.assertIn("STOP: could not read the type of T-1", out)
                    self.assertNotIn("children", log)

    def test_guard_passes_when_every_child_is_closed(self):
        for children in ([], [{"key": "T-2", "closed": True}]):
            spec = {"view": {"out": ticket()}, "children": {"out": json.dumps(children)}}
            for shell, (rc, out, err, log) in self.run_block(GUARD, spec).items():
                with self.subTest(shell=shell, children=children):
                    self.assertEqual(rc, 0, err)
                    self.assertNotIn("STOP", out)

    # ---- automated-sdlc Phase 1.5 -------------------------------------------

    def test_phase_15_stops_a_container_and_reports_progress(self):
        rollup = {"total": 3, "closed": 1, "open": ["T-3", "T-4"], "ready_to_close": False}
        spec = {"view": {"out": ticket("container", rollup)}}
        for shell, (rc, out, err, log) in self.run_block(PHASE_15, spec).items():
            with self.subTest(shell=shell):
                self.assertEqual(rc, 0, err)
                self.assertIn("STOP: T-1 is a container: 1/3 children closed.", out)
                self.assertNotIn("propose closing", out)
                self.assertNotIn("complete-deliverable", out)
                self.assert_only_reads(log)

    def test_phase_15_proposes_closing_a_finished_container(self):
        rollup = {"total": 2, "closed": 2, "open": [], "ready_to_close": True}
        spec = {"view": {"out": ticket("container", rollup)}}
        for shell, (rc, out, err, log) in self.run_block(PHASE_15, spec).items():
            with self.subTest(shell=shell):
                self.assertIn("2/2 children closed", out)
                self.assertIn("propose closing T-1 to a human", out)

    def test_phase_15_says_progress_unavailable_without_a_rollup(self):
        spec = {"view": {"out": ticket("container", None)}}
        for shell, (rc, out, err, log) in self.run_block(PHASE_15, spec).items():
            with self.subTest(shell=shell):
                self.assertIn("is a container: progress unavailable.", out)
                self.assertNotIn("0/0", out)

    def test_phase_15_routes_a_deliverable_onward(self):
        spec = {"view": {"out": ticket("deliverable")}}
        for shell, (rc, out, err, log) in self.run_block(PHASE_15, spec).items():
            with self.subTest(shell=shell):
                self.assertIn("complete-deliverable", out)
                self.assertNotIn("STOP", out)

    def test_phase_15_stops_when_the_ticket_cannot_be_read(self):
        # Copilot on PR #105: a failed view used to fall into the unknown
        # branch, whose second read could route a container onward.
        spec = {"view": {"rc": 3, "err": "tkt: network error\n"}}
        for shell, (rc, out, err, log) in self.run_block(PHASE_15, spec).items():
            with self.subTest(shell=shell):
                self.assertIn("STOP: could not read T-1", out)
                self.assertNotIn("complete-deliverable", out)
                self.assertEqual(len(log.splitlines()), 1, log)

    def test_phase_15_stops_when_the_type_cannot_be_read(self):
        for body in ("not json", json.dumps({"key": "T-1", "transitions": []})):
            spec = {"view": {"out": body}}
            for shell, (rc, out, err, log) in self.run_block(PHASE_15, spec).items():
                with self.subTest(shell=shell, body=body):
                    self.assertIn("STOP: could not read the type of T-1", out)
                    self.assertEqual(len(log.splitlines()), 1, log)

    def test_phase_15_unknown_type_uses_the_first_read(self):
        body = json.dumps({"key": "T-1", "type_class": "unknown",
                           "transitions": ["In Review"]})
        code = PHASE_15 + '\necho "HAS_REVIEW=$HAS_REVIEW"'
        spec = {"view": {"out": body}}
        for shell, (rc, out, err, log) in self.run_block(
                f'REVIEW_LANE="In Review"; export REVIEW_LANE\n{code}', spec).items():
            with self.subTest(shell=shell):
                self.assertEqual(rc, 0, err)
                self.assertIn("HAS_REVIEW=true", out)
                self.assertNotIn("STOP", out)
                self.assertEqual(len(log.splitlines()), 1, log)

    # ---- check-blockers step 7 ----------------------------------------------

    def test_step_7_skips_an_already_closed_container(self):
        listed = json.dumps([
            {"key": "E-1", "type_class": "container", "closed": True},
            {"key": "E-2", "type_class": "container", "closed": False},
            {"key": "S-3", "type_class": "full_sdlc", "closed": False}])
        spec = {"cfg": {"out": "type = Epic\n"}, "list": {"out": listed},
                "children": {"out": json.dumps([{"key": "C-1", "closed": True}])}}
        for shell, (rc, out, err, log) in self.run_block(STEP_7, spec).items():
            with self.subTest(shell=shell):
                self.assertEqual(rc, 0, err)
                self.assertIn("E-2 — every child is closed", out)
                self.assertNotIn("E-1", out)
                self.assertNotIn("children E-1", log)
                self.assertNotIn("children S-3", log)
                self.assert_only_reads(log)

    def test_step_7_warns_instead_of_skipping_silently(self):
        spec = {"cfg": {"out": "q\n"}, "list": {"rc": 3, "err": "tkt: down\n"}}
        for shell, (rc, out, err, log) in self.run_block(STEP_7, spec).items():
            with self.subTest(shell=shell):
                self.assertIn("WARNING: could not list", err)
        listed = json.dumps([{"key": "E-2", "type_class": "container", "closed": False}])
        spec = {"cfg": {"out": "q\n"}, "list": {"out": listed},
                "children": {"rc": 3}}
        for shell, (rc, out, err, log) in self.run_block(STEP_7, spec).items():
            with self.subTest(shell=shell):
                self.assertIn("WARNING: could not check the children of E-2", err)
                self.assertNotIn("every child is closed", out)

    def test_step_7_is_quiet_without_a_containers_query(self):
        spec = {"cfg": {"rc": 4}}
        for shell, (rc, out, err, log) in self.run_block(STEP_7, spec).items():
            with self.subTest(shell=shell):
                self.assertEqual((rc, out, err), (0, "", ""))


if __name__ == "__main__":
    unittest.main()
