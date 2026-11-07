"""TKT-72: `tkt view` (human output) prints a `parent:` line when a ticket
has a parent and nothing when it has none; `--json` always carries the key.

Run from the repo root:

    python3 -m unittest tests.test_parent_cli
"""
import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.cli import _print_ticket_human  # noqa: E402
from core.schema import Ticket  # noqa: E402


def render(t: Ticket) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _print_ticket_human(t)
    return buf.getvalue()


class HumanParentLine(unittest.TestCase):

    def test_parent_line_when_set(self):
        out = render(Ticket(key="TKT-2", summary="Child", status="To Do",
                            parent="TKT-1"))
        lines = [ln.strip() for ln in out.splitlines()]
        self.assertIn("parent:   TKT-1", lines)

    def test_no_parent_line_when_empty(self):
        out = render(Ticket(key="TKT-1", summary="Root", status="To Do"))
        self.assertNotIn("parent", out)
        # Control: the same ticket with a parent does print the line.
        self.assertIn("parent:", render(Ticket(key="TKT-1", summary="Root",
                                               status="To Do", parent="TKT-0")))

    def test_parent_line_before_children_line(self):
        out = render(Ticket(key="TKT-2", summary="Mid", status="To Do",
                            parent="TKT-1", children=["TKT-3"]))
        self.assertLess(out.index("parent:"), out.index("children:"))

    def test_json_shape_always_has_parent(self):
        self.assertEqual(Ticket(key="TKT-1").to_dict()["parent"], "")
        self.assertEqual(Ticket(key="TKT-2", parent="TKT-1").to_dict()["parent"],
                         "TKT-1")


if __name__ == "__main__":
    unittest.main()
