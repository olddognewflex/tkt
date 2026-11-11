"""TKT-103: `tkt view` adds a child-progress `rollup` for containers only,
and `tkt children KEY` lists each child's state.

The rollup costs extra calls, so a non-container view never makes them and
its JSON carries `"rollup": null`. A failed child lookup warns and leaves the
rollup null; it never fails the view. `ready_to_close` is true only when
every child is closed and the container itself is not. Offline: a temp
markdown board driven through `cli.main`.

    python3 -m unittest tests.test_rollup_cli
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.markdown import MarkdownAdapter  # noqa: E402
from core import cli  # noqa: E402
from core.errors import ProviderError  # noqa: E402

TOML = """\
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
done = "Done"

[queries]
tier1 = 'status = "To Do"'
"""


class Board(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.board = self.root / "board"
        self.board.mkdir()
        self.cfg = self.root / "config.toml"
        self.cfg.write_text(TOML.format(board=self.board, state=self.root / "state"))

    def write(self, key, type_, status, children=()):
        fm = f"type: {type_}\nstatus: {status}\n"
        if children:
            fm += f"children: [{', '.join(children)}]\n"
        (self.board / f"{key}.md").write_text(f"---\n{fm}---\n# {key}\n")

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--config", str(self.cfg), *argv])
        return code, out.getvalue(), err.getvalue()

    def view_json(self, key):
        code, out, err = self.run_cli("view", key, "--json")
        self.assertEqual(code, 0, err)
        return json.loads(out)


class Rollup(Board):
    def test_container_rollup_counts(self):
        self.write("TKT-1", "Epic", "To Do", ["TKT-2", "TKT-3", "TKT-4"])
        self.write("TKT-2", "Story", "Done")
        self.write("TKT-3", "Story", "In Progress")
        self.write("TKT-4", "Story", "To Do")
        d = self.view_json("TKT-1")
        self.assertEqual(d["type_class"], "container")
        self.assertEqual(d["rollup"], {"total": 3, "closed": 1,
                                       "open": ["TKT-3", "TKT-4"],
                                       "ready_to_close": False})

    def test_ready_to_close_only_when_all_closed_and_container_open(self):
        self.write("TKT-1", "Epic", "To Do", ["TKT-2"])
        self.write("TKT-2", "Story", "Done")
        self.assertTrue(self.view_json("TKT-1")["rollup"]["ready_to_close"])
        self.write("TKT-1", "Epic", "Done", ["TKT-2"])
        self.assertFalse(self.view_json("TKT-1")["rollup"]["ready_to_close"])

    def test_container_without_children_needs_no_lookup(self):
        self.write("TKT-1", "Epic", "To Do")
        with mock.patch.object(MarkdownAdapter, "child_states",
                               side_effect=AssertionError("no lookup")):
            d = self.view_json("TKT-1")
        self.assertEqual(d["rollup"], {"total": 0, "closed": 0, "open": [],
                                       "ready_to_close": False})

    def test_non_container_has_null_rollup_and_no_lookup(self):
        self.write("TKT-1", "Story", "To Do", ["TKT-2"])
        self.write("TKT-2", "Sub-task", "To Do")
        with mock.patch.object(MarkdownAdapter, "child_states",
                               side_effect=AssertionError("no lookup")):
            d = self.view_json("TKT-1")
        self.assertIsNone(d["rollup"])

    def test_lookup_failure_warns_and_leaves_rollup_null(self):
        self.write("TKT-1", "Epic", "To Do", ["TKT-2"])
        with mock.patch.object(MarkdownAdapter, "child_states",
                               side_effect=ProviderError("backend down")):
            code, out, err = self.run_cli("view", "TKT-1", "--json")
        self.assertEqual(code, 0)
        self.assertIsNone(json.loads(out)["rollup"])
        self.assertIn("rollup unavailable: backend down", err)

    def test_human_progress_lines(self):
        self.write("TKT-1", "Epic", "To Do", ["TKT-2", "TKT-3"])
        self.write("TKT-2", "Story", "Done")
        self.write("TKT-3", "Story", "To Do")
        _, out, _ = self.run_cli("view", "TKT-1")
        self.assertIn("progress: 1/2 children closed (open: TKT-3)", out)
        self.assertNotIn("ready to close", out)
        self.write("TKT-3", "Story", "Done")
        _, out, _ = self.run_cli("view", "TKT-1")
        self.assertIn("progress: 2/2 children closed - ready to close", out)

    def test_no_progress_line_for_non_containers(self):
        self.write("TKT-1", "Story", "To Do")
        _, out, _ = self.run_cli("view", "TKT-1")
        self.assertNotIn("progress:", out)


class RollupLine(unittest.TestCase):
    def test_no_children_only_when_the_ticket_lists_none(self):
        empty = {"total": 0, "closed": 0, "open": [], "ready_to_close": False}
        self.assertEqual(cli._rollup_line(empty, []), "no children")
        # The lookup found nothing, yet the ticket lists children.
        self.assertEqual(cli._rollup_line(empty, ["E-2"]), "progress unavailable")

    def test_counts_and_ready(self):
        r = {"total": 2, "closed": 2, "open": [], "ready_to_close": True}
        self.assertEqual(cli._rollup_line(r, ["A", "B"]),
                         "2/2 children closed - ready to close (a human closes containers)")


class ChildrenVerb(Board):
    def test_json_shape(self):
        self.write("TKT-1", "Story", "To Do", ["TKT-2", "TKT-3"])
        self.write("TKT-2", "Sub-task", "Done")
        self.write("TKT-3", "Sub-task", "In Progress")
        code, out, err = self.run_cli("children", "TKT-1", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), [
            {"key": "TKT-2", "status": "Done", "status_role": "done", "closed": True},
            {"key": "TKT-3", "status": "In Progress", "status_role": "in_progress",
             "closed": False}])

    def test_human_output(self):
        self.write("TKT-1", "Story", "To Do", ["TKT-2"])
        self.write("TKT-2", "Sub-task", "Done")
        _, out, _ = self.run_cli("children", "TKT-1")
        self.assertEqual(out.strip(), "TKT-2  Done  (closed)")

    def test_no_children(self):
        self.write("TKT-1", "Story", "To Do")
        _, out, _ = self.run_cli("children", "TKT-1")
        self.assertEqual(out.strip(), "(no children)")
        _, out, _ = self.run_cli("children", "TKT-1", "--json")
        self.assertEqual(json.loads(out), [])

    def test_unknown_parent_fails(self):
        code, _, err = self.run_cli("children", "TKT-404", "--json")
        self.assertNotEqual(code, 0)
        self.assertIn("TKT-404", err)


if __name__ == "__main__":
    unittest.main()
