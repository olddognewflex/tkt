"""Regression tests for TKT-12: the Jira adapter must verify that a transition
actually happened instead of trusting acli's exit code.

`acli jira workitem transition` reports an unavailable transition on stdout and
still exits 0, so `check=True` never fires. Fully offline: acli and REST are
stubbed at the `_acli` / `_jira` boundary and every call is recorded. Run from
the repo root:

    python3 -m unittest tests.test_jira_transition

QA Author additions (CliTransitionExitCode, AcliOnlyVerification): drive the
`tkt transition` CLI end to end with the adapter factory patched to a StubJira,
and pin acli-only (no REST) verification when the status read fails.
Break-It dimensions covered: error propagation (exit code, stderr vs stdout),
partial failure (write reported ok / read broken), missing data (unreadable
status).
"""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.jira import JiraAdapter  # noqa: E402
from core import cli  # noqa: E402
from core.config import Config  # noqa: E402
from core.errors import ProviderError  # noqa: E402

ROLES = {"todo": "To Do", "in_progress": "In Progress",
         "review": "PR Needs Review", "done": "Done"}

ACLI_FAILURE = ("✗ Failure: PROJ-1 can't be transitioned: No allowed "
                "transitions found for status 'PR Needs Review'\n")
ACLI_SUCCESS = "✓ Success: PROJ-1 transitioned to 'In Progress'\n"


def make_config():
    data = {"ticketing": {"provider": "jira", "project": "PROJ"},
            "board": {"roles": dict(ROLES)}}
    return Config(data, Path("/nonexistent/.sdlc/config.toml"))


class StubJira(JiraAdapter):
    """JiraAdapter with acli and REST replaced by an in-memory issue.

    `moves` controls whether acli actually changes the status. `acli_out` is
    what acli prints on stdout (it always "exits 0", i.e. never raises).
    """

    def __init__(self, status="To Do", moves=True, acli_out=ACLI_SUCCESS,
                 rest=True, reachable=("In Progress", "Done"),
                 status_read_fails=False):
        super().__init__(make_config())
        self.have_rest = rest
        self.site = "example.atlassian.net"
        self.status = status
        self.moves = moves
        self.acli_out = acli_out
        self.reachable = list(reachable)
        self.status_read_fails = status_read_fails
        self.calls: list[tuple] = []

    def _acli(self, *args):
        self.calls.append(("acli", *args))
        if args[:3] == ("jira", "workitem", "transition"):
            if self.moves:
                self.status = args[args.index("--status") + 1]
            return self.acli_out
        if args[:3] == ("jira", "workitem", "view"):
            if self.status_read_fails:
                raise ProviderError("acli view failed")
            return '{"key": "PROJ-1", "fields": {"status": {"name": "%s"}}}' % self.status
        return ""

    def _jira(self, method, path, body=None):
        self.calls.append(("rest", method, path))
        if path.endswith("/transitions"):
            return {"transitions": [{"id": str(i), "name": n, "to": {"name": n}}
                                    for i, n in enumerate(self.reachable)]}
        if path.startswith("/rest/api/3/issue/"):
            if self.status_read_fails:
                raise ProviderError("Jira GET -> 503")
            return {"key": "PROJ-1", "fields": {"status": {"name": self.status}}}
        raise AssertionError(f"unexpected REST call {method} {path}")

    def transition_calls(self):
        return [c for c in self.calls if c[:4] == ("acli", "jira", "workitem", "transition")]


class TransitionVerified(unittest.TestCase):
    def test_success_when_status_moves(self):
        j = StubJira(status="To Do")
        j.transition("PROJ-1", "in_progress")
        self.assertEqual(j.status, "In Progress")
        self.assertEqual(len(j.transition_calls()), 1)

    def test_acli_exit_zero_failure_raises(self):
        # The bug: acli prints a failure, exits 0, and the board never moves.
        j = StubJira(status="In Progress", moves=False, acli_out=ACLI_FAILURE)
        with self.assertRaises(ProviderError) as cm:
            j.transition("PROJ-1", "review")
        msg = str(cm.exception)
        self.assertIn("PROJ-1", msg)
        self.assertIn("PR Needs Review", msg)

    def test_failure_lists_reachable_transitions(self):
        j = StubJira(status="In Progress", moves=False, acli_out=ACLI_FAILURE,
                     reachable=("To Do", "Done"))
        with self.assertRaises(ProviderError) as cm:
            j.transition("PROJ-1", "review")
        msg = str(cm.exception)
        self.assertIn("In Progress", msg)  # the current status
        self.assertIn("To Do", msg)
        self.assertIn("Done", msg)

    def test_silent_non_move_raises(self):
        # No failure marker at all, but the status did not change: still a failure.
        j = StubJira(status="In Progress", moves=False, acli_out="")
        with self.assertRaises(ProviderError):
            j.transition("PROJ-1", "review")

    def test_failure_marker_alone_raises_even_if_status_unreadable(self):
        j = StubJira(status="In Progress", moves=False, acli_out=ACLI_FAILURE,
                     status_read_fails=True)
        with self.assertRaises(ProviderError):
            j.transition("PROJ-1", "review")

    def test_failure_without_rest_still_raises(self):
        # acli-only mode can't list transitions, but must not report success.
        j = StubJira(status="In Progress", moves=False, acli_out=ACLI_FAILURE,
                     rest=False)
        with self.assertRaises(ProviderError) as cm:
            j.transition("PROJ-1", "review")
        self.assertIn("PR Needs Review", str(cm.exception))
        self.assertFalse(any(c[0] == "rest" for c in j.calls))


class AlreadyInLane(unittest.TestCase):
    def test_already_in_target_lane_is_success(self):
        # Jira rarely defines a self-transition, so acli fails; retries from
        # resume-from-revise / respond-to-review must not become hard errors.
        j = StubJira(status="PR Needs Review", moves=False, acli_out=ACLI_FAILURE)
        j.transition("PROJ-1", "review")  # no raise
        self.assertEqual(j.status, "PR Needs Review")

    def test_already_in_lane_compares_case_insensitively(self):
        j = StubJira(status="pr needs review", moves=False, acli_out=ACLI_FAILURE)
        j.transition("PROJ-1", "review")  # no raise

    def test_already_in_lane_acli_only(self):
        j = StubJira(status="PR Needs Review", moves=False, acli_out=ACLI_FAILURE,
                     rest=False)
        j.transition("PROJ-1", "review")  # no raise


class VerificationReadFailure(unittest.TestCase):
    def test_status_read_failure_does_not_mask_a_clean_transition(self):
        # acli reported success; a broken status read must not turn that into
        # an error.
        j = StubJira(status="To Do", status_read_fails=True)
        j.transition("PROJ-1", "in_progress")  # no raise
        self.assertEqual(j.status, "In Progress")

    def test_current_status_returns_none_on_read_failure(self):
        j = StubJira(status_read_fails=True)
        self.assertIsNone(j._current_status("PROJ-1"))
        j = StubJira(status_read_fails=True, rest=False)
        self.assertIsNone(j._current_status("PROJ-1"))

    def test_current_status_reads_both_modes(self):
        self.assertEqual(StubJira(status="Done")._current_status("PROJ-1"), "Done")
        self.assertEqual(StubJira(status="Done", rest=False)._current_status("PROJ-1"),
                         "Done")


ACLI_STATUS_READ = ("acli", "jira", "workitem", "view", "PROJ-1",
                    "--fields", "status", "--json")


class AcliOnlyVerification(unittest.TestCase):
    """rest=False: status is re-read via `acli jira workitem view`."""

    def test_acli_success_with_unreadable_status_succeeds(self):
        j = StubJira(status="To Do", rest=False, status_read_fails=True)
        j.transition("PROJ-1", "in_progress")  # no raise
        self.assertEqual(j.status, "In Progress")
        self.assertEqual(len(j.transition_calls()), 1)
        # The verification read went through acli, never REST.
        self.assertIn(ACLI_STATUS_READ, j.calls)
        self.assertFalse(any(c[0] == "rest" for c in j.calls))

    def test_acli_failure_marker_with_unreadable_status_raises(self):
        j = StubJira(status="In Progress", moves=False, acli_out=ACLI_FAILURE,
                     rest=False, status_read_fails=True)
        with self.assertRaises(ProviderError) as cm:
            j.transition("PROJ-1", "review")
        msg = str(cm.exception)
        self.assertIn("PROJ-1", msg)
        self.assertIn("PR Needs Review", msg)
        self.assertEqual(cm.exception.exit_code, 3)
        # The verification read was attempted via acli (and failed).
        self.assertIn(ACLI_STATUS_READ, j.calls)
        self.assertFalse(any(c[0] == "rest" for c in j.calls))


CONFIG_TOML = """\
[ticketing]
provider = "jira"
project = "PROJ"

[board.roles]
todo = "To Do"
in_progress = "In Progress"
review = "PR Needs Review"
done = "Done"
"""


class CliTransitionExitCode(unittest.TestCase):
    """`tkt transition` end to end: real argparse, real Config.load, real
    error handling in cli.main; only the adapter factory is swapped for a
    StubJira so nothing touches acli or the network."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config_path = Path(tmp.name) / "config.toml"
        self.config_path.write_text(CONFIG_TOML, encoding="utf-8")

    def run_cli(self, stub, *argv):
        seen = []

        def factory(config):
            seen.append(config.provider)
            return stub

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "get_adapter", side_effect=factory), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--config", str(self.config_path), *argv])
        self.assertEqual(seen, ["jira"])
        return code, out.getvalue(), err.getvalue()

    def test_board_did_not_move_exits_3_with_error_on_stderr(self):
        stub = StubJira(status="In Progress", moves=False, acli_out=ACLI_FAILURE)
        code, out, err = self.run_cli(stub, "transition", "PROJ-1", "review")
        self.assertEqual(code, 3)
        self.assertNotIn("PROJ-1 -> PR Needs Review", out)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("tkt: "), err)
        self.assertIn("PROJ-1", err)
        self.assertIn("PR Needs Review", err)
        self.assertEqual(stub.status, "In Progress")

    def test_silent_non_move_exits_3(self):
        # acli printed nothing and exited 0, but the board did not move.
        stub = StubJira(status="In Progress", moves=False, acli_out="")
        code, out, err = self.run_cli(stub, "transition", "PROJ-1", "review")
        self.assertEqual(code, 3)
        self.assertEqual(out, "")
        self.assertIn("PR Needs Review", err)

    def test_successful_move_exits_0_and_prints_lane(self):
        stub = StubJira(status="In Progress")
        code, out, err = self.run_cli(stub, "transition", "PROJ-1", "review")
        self.assertEqual(code, 0)
        self.assertEqual(out, "PROJ-1 -> PR Needs Review\n")
        self.assertEqual(err, "")
        self.assertEqual(stub.status, "PR Needs Review")


if __name__ == "__main__":
    unittest.main()
