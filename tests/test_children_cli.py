"""TKT-19: `tkt view` (human output) prints a `children:` line when a ticket
has children and nothing when it has none; `--json` always carries the key.

Run from the repo root:

    python3 -m unittest tests.test_children_cli
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


class HumanChildrenLine(unittest.TestCase):

    def test_children_line_when_set(self):
        out = render(Ticket(key="TKT-1", summary="Parent", status="To Do",
                            children=["TKT-2", "TKT-3"]))
        lines = [ln.strip() for ln in out.splitlines()]
        self.assertIn("children: TKT-2, TKT-3", lines)

    def test_no_children_line_when_empty(self):
        out = render(Ticket(key="TKT-1", summary="Leaf", status="To Do"))
        self.assertNotIn("children", out)

    def test_json_shape_always_has_children(self):
        self.assertEqual(Ticket(key="TKT-1").to_dict()["children"], [])
        self.assertEqual(Ticket(key="TKT-1", children=["TKT-2"]).to_dict()["children"],
                         ["TKT-2"])


if __name__ == "__main__":
    unittest.main()
