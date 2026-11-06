"""TKT-19: the markdown adapter reads `Ticket.children` from a `children:`
frontmatter list and round-trips it through `tkt apply`.

Offline: the adapter is pointed at a temp board dir. Run from the repo root:

    python3 -m unittest tests.test_children_markdown
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.markdown import MarkdownAdapter  # noqa: E402
from core.config import Config  # noqa: E402

ROLES = {"backlog": "Backlog", "todo": "To Do", "in_progress": "In Progress",
         "done": "Done"}


def adapter(tmp):
    cfg = Config({"ticketing": {"provider": "markdown", "project": "TKT"},
                  "markdown": {"board_dir": str(Path(tmp) / "board"),
                               "state_dir": str(Path(tmp) / "state"),
                               "me": "raymond"},
                  "board": {"roles": dict(ROLES)},
                  "queries": {"all": 'status = "To Do"'}},
                 Path(tmp) / ".sdlc" / "config.toml")
    return MarkdownAdapter(cfg)


class MarkdownChildren(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.a = adapter(self.tmp)
        self.a.board_dir.mkdir(parents=True)

    def write(self, key, fm_lines):
        text = "---\ntype: Story\nstatus: To Do\n" + "".join(
            f"{line}\n" for line in fm_lines) + f"---\n# {key}\n"
        (self.a.board_dir / f"{key}.md").write_text(text)

    def doc(self, *fm_lines, summary="Parent"):
        return ("---\ntype: Story\n" + "".join(f"{x}\n" for x in fm_lines)
                + f"---\n# {summary}\n\nBody.\n")

    # ---- read ---------------------------------------------------------------

    def test_list_literal(self):
        self.write("TKT-1", ["children: [TKT-2, TKT-3]"])
        self.assertEqual(self.a.view("TKT-1").children, ["TKT-2", "TKT-3"])

    def test_bare_string_coerced_to_one_element_list(self):
        self.write("TKT-1", ["children: TKT-2"])
        self.assertEqual(self.a.view("TKT-1").children, ["TKT-2"])

    def test_duplicates_collapse_in_order(self):
        self.write("TKT-1", ["children: [TKT-3, TKT-2, TKT-3]"])
        self.assertEqual(self.a.view("TKT-1").children, ["TKT-3", "TKT-2"])

    def test_own_key_dropped(self):
        self.write("TKT-1", ["children: [TKT-1, TKT-2]"])
        self.assertEqual(self.a.view("TKT-1").children, ["TKT-2"])

    def test_absent_key_is_empty(self):
        self.write("TKT-1", [])
        self.assertEqual(self.a.view("TKT-1").children, [])

    def test_empty_list_and_empty_value_are_empty(self):
        self.write("TKT-1", ["children: []"])
        self.write("TKT-2", ["children:"])
        self.assertEqual(self.a.view("TKT-1").children, [])
        self.assertEqual(self.a.view("TKT-2").children, [])

    def test_view_json_has_children(self):
        self.write("TKT-1", ["children: [TKT-2]"])
        self.assertEqual(self.a.view("TKT-1").to_dict()["children"], ["TKT-2"])

    def test_list_carries_children(self):
        self.write("TKT-1", ["children: [TKT-2]"])
        self.write("TKT-2", [])
        by_key = {t.key: t.children for t in self.a.list(query="all")}
        self.assertEqual(by_key, {"TKT-1": ["TKT-2"], "TKT-2": []})

    # ---- apply ----------------------------------------------------------------

    def test_apply_update_round_trips_children(self):
        self.write("TKT-1", [])
        t = self.a.apply(self.doc("children: [TKT-4, TKT-5]"), key="TKT-1")
        self.assertEqual(t.children, ["TKT-4", "TKT-5"])
        fm, _ = self.a._read_raw("TKT-1")
        self.assertEqual(fm["children"], ["TKT-4", "TKT-5"])
        # And the stored file reads back the same through a second apply
        # that does not mention children (untouched keys are kept).
        t = self.a.apply(self.doc(), key="TKT-1")
        self.assertEqual(t.children, ["TKT-4", "TKT-5"])

    def test_apply_update_can_clear_children(self):
        self.write("TKT-1", ["children: [TKT-4]"])
        t = self.a.apply(self.doc("children: []"), key="TKT-1")
        self.assertEqual(t.children, [])
        fm, _ = self.a._read_raw("TKT-1")
        self.assertEqual(fm["children"], [])

    def test_apply_create_carries_children(self):
        t = self.a.apply(self.doc("children: [TKT-7, TKT-8]"))
        self.assertEqual(t.children, ["TKT-7", "TKT-8"])
        fm, _ = self.a._read_raw(t.key)
        self.assertEqual(fm["children"], ["TKT-7", "TKT-8"])

    def test_apply_create_bare_string_children(self):
        t = self.a.apply(self.doc("children: TKT-7"))
        self.assertEqual(t.children, ["TKT-7"])

    def test_apply_create_omits_empty_children(self):
        t = self.a.apply(self.doc("children: []"))
        self.assertEqual(t.children, [])
        fm, _ = self.a._read_raw(t.key)
        self.assertNotIn("children", fm)

    def test_create_does_not_add_children_key(self):
        t = self.a.create("Story", "S")
        fm, _ = self.a._read_raw(t.key)
        self.assertNotIn("children", fm)
        self.assertEqual(t.children, [])

    # ---- link -----------------------------------------------------------------

    def test_link_child_appends_without_duplicates(self):
        self.write("TKT-1", [])
        self.a.link("TKT-1", "TKT-2", "child")
        self.a.link("TKT-1", "TKT-3", "  Has Child ")
        self.a.link("TKT-1", "TKT-2", "CHILD")
        self.assertEqual(self.a.view("TKT-1").children, ["TKT-2", "TKT-3"])
        fm, _ = self.a._read_raw("TKT-1")
        self.assertEqual(fm["children"], ["TKT-2", "TKT-3"])
        self.assertNotIn("links", fm)

    def test_link_child_extends_a_bare_string(self):
        self.write("TKT-1", ["children: TKT-2"])
        self.a.link("TKT-1", "TKT-3", "child")
        self.assertEqual(self.a.view("TKT-1").children, ["TKT-2", "TKT-3"])

    def test_link_child_leaves_the_child_and_block_buckets_alone(self):
        self.write("TKT-1", ["blocks: [TKT-9]"])
        self.write("TKT-2", [])
        self.a.link("TKT-1", "TKT-2", "child")
        fm, _ = self.a._read_raw("TKT-1")
        self.assertEqual(fm["blocks"], ["TKT-9"])
        self.assertFalse(fm.get("blocked_by"))
        child_fm, _ = self.a._read_raw("TKT-2")
        self.assertNotIn("parent", child_fm)
        self.assertNotIn("children", child_fm)

    def test_other_link_types_do_not_touch_children(self):
        self.write("TKT-1", [])
        self.a.link("TKT-1", "TKT-2", "relates to")
        fm, _ = self.a._read_raw("TKT-1")
        self.assertNotIn("children", fm)
        self.assertEqual(fm["links"], ["relates to:TKT-2"])

    def test_view_accepts_children_flag_and_ignores_it(self):
        self.write("TKT-1", ["children: [TKT-2]"])
        self.assertEqual(self.a.view("TKT-1", children=False).children, ["TKT-2"])


if __name__ == "__main__":
    unittest.main()
