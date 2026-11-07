"""TKT-72: the markdown adapter reads `Ticket.parent` from a `parent:`
frontmatter scalar, round-trips it through `tkt apply`, and `tkt link --type
child|parent` writes both hierarchy edges (the child's `parent:` and the
parent's `children:`) so they cannot drift.

Offline: the adapter is pointed at a temp board dir. Run from the repo root:

    python3 -m unittest tests.test_parent_markdown
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
from core.errors import NotFoundError, UsageError  # noqa: E402

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


class _Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.a = adapter(self.tmp)
        self.a.board_dir.mkdir(parents=True)

    def write(self, key, fm_lines=()):
        text = "---\ntype: Story\nstatus: To Do\n" + "".join(
            f"{line}\n" for line in fm_lines) + f"---\n# {key}\n"
        (self.a.board_dir / f"{key}.md").write_text(text)

    def fm(self, key):
        return self.a._read_raw(key)[0]

    def assert_control(self):
        # Paired with each "reads as none" case so the test also fails if
        # parent parsing were removed (which would read "" everywhere).
        self.write("TKT-99", ["parent: TKT-98"])
        self.assertEqual(self.a.view("TKT-99").parent, "TKT-98")

    def snapshot(self):
        """Every file under the board dir, by name -> content."""
        return {p.name: p.read_text() for p in sorted(self.a.board_dir.iterdir())}

    def doc(self, *fm_lines, summary="Child"):
        return ("---\ntype: Story\n" + "".join(f"{x}\n" for x in fm_lines)
                + f"---\n# {summary}\n\nBody.\n")


class MarkdownParentRead(_Base):

    def test_scalar(self):
        self.write("TKT-2", ["parent: TKT-1"])
        self.assertEqual(self.a.view("TKT-2").parent, "TKT-1")

    def test_quoted_scalar(self):
        self.write("TKT-2", ['parent: "TKT-1"'])
        self.assertEqual(self.a.view("TKT-2").parent, "TKT-1")

    def test_one_element_list_is_that_element(self):
        self.write("TKT-2", ["parent: [TKT-1]"])
        self.assertEqual(self.a.view("TKT-2").parent, "TKT-1")

    def test_multi_element_list_is_empty(self):
        self.write("TKT-2", ["parent: [TKT-1, TKT-3]"])
        self.assertEqual(self.a.view("TKT-2").parent, "")
        self.assert_control()

    def test_empty_list_and_empty_value_are_empty(self):
        self.write("TKT-2", ["parent: []"])
        self.write("TKT-3", ["parent:"])
        self.assertEqual(self.a.view("TKT-2").parent, "")
        self.assertEqual(self.a.view("TKT-3").parent, "")
        self.assert_control()

    def test_own_key_is_empty(self):
        self.write("TKT-2", ["parent: TKT-2"])
        self.write("TKT-3", ["parent: [TKT-3]"])
        self.assertEqual(self.a.view("TKT-2").parent, "")
        self.assertEqual(self.a.view("TKT-3").parent, "")
        self.assert_control()

    def test_absent_key_is_empty(self):
        self.write("TKT-2")
        self.assertEqual(self.a.view("TKT-2").parent, "")
        self.assert_control()

    def test_view_json_and_list_carry_parent(self):
        self.write("TKT-1")
        self.write("TKT-2", ["parent: TKT-1"])
        self.assertEqual(self.a.view("TKT-2").to_dict()["parent"], "TKT-1")
        by_key = {t.key: t.parent for t in self.a.list(query="all")}
        self.assertEqual(by_key, {"TKT-1": "", "TKT-2": "TKT-1"})

    def test_view_accepts_hierarchy_flag_and_ignores_it(self):
        self.write("TKT-2", ["parent: TKT-1"])
        self.assertEqual(self.a.view("TKT-2", hierarchy=False).parent, "TKT-1")


class MarkdownParentApply(_Base):

    def test_apply_create_carries_parent(self):
        self.write("TKT-1")
        t = self.a.apply(self.doc("parent: TKT-1"))
        self.assertEqual(t.parent, "TKT-1")
        self.assertEqual(self.fm(t.key)["parent"], "TKT-1")

    def test_apply_create_coerces_one_element_list(self):
        self.write("TKT-1")
        t = self.a.apply(self.doc("parent: [TKT-1]"))
        self.assertEqual(self.fm(t.key)["parent"], "TKT-1")

    def test_apply_create_drops_parent_naming_the_new_key(self):
        # The board is empty, so the new ticket is minted as TKT-1.
        t = self.a.apply(self.doc("parent: TKT-1"))
        self.assertEqual(t.key, "TKT-1")
        self.assertEqual(t.parent, "")
        self.assertNotIn("parent", self.fm(t.key))
        # Control: the next ticket (TKT-2) may name TKT-1.
        t = self.a.apply(self.doc("parent: TKT-1"))
        self.assertEqual((t.key, t.parent), ("TKT-2", "TKT-1"))

    def test_apply_create_omits_empty_parent(self):
        t = self.a.apply(self.doc("parent:"))
        self.assertEqual(t.parent, "")
        self.assertNotIn("parent", self.fm(t.key))
        # Control: a set parent on the same path is written.
        t = self.a.apply(self.doc("parent: TKT-1"))
        self.assertEqual(self.fm(t.key)["parent"], "TKT-1")

    def test_apply_is_single_sided(self):
        # apply writes only the ticket it is given; the parent's file is
        # untouched (use `tkt link` for both edges).
        self.write("TKT-1")
        before = (self.a.board_dir / "TKT-1.md").read_text()
        t = self.a.apply(self.doc("parent: TKT-1"))
        self.assertEqual(t.parent, "TKT-1")
        self.assertEqual((self.a.board_dir / "TKT-1.md").read_text(), before)

    def test_apply_update_round_trips_parent(self):
        self.write("TKT-2")
        t = self.a.apply(self.doc("parent: TKT-1"), key="TKT-2")
        self.assertEqual(t.parent, "TKT-1")
        self.assertEqual(self.fm("TKT-2")["parent"], "TKT-1")
        # A later apply that does not mention parent keeps it.
        t = self.a.apply(self.doc(), key="TKT-2")
        self.assertEqual(t.parent, "TKT-1")

    def test_apply_update_changes_and_clears_parent(self):
        self.write("TKT-2", ["parent: TKT-1"])
        self.assertEqual(self.a.apply(self.doc("parent: TKT-5"), key="TKT-2").parent,
                         "TKT-5")
        t = self.a.apply(self.doc("parent:"), key="TKT-2")
        self.assertEqual(t.parent, "")
        self.assertNotIn("parent", self.fm("TKT-2"))

    def test_apply_update_own_key_clears(self):
        self.write("TKT-2", ["parent: TKT-1"])
        t = self.a.apply(self.doc("parent: TKT-2"), key="TKT-2")
        self.assertEqual(t.parent, "")
        self.assertNotIn("parent", self.fm("TKT-2"))


class MarkdownParentLink(_Base):

    def test_link_child_writes_both_files(self):
        self.write("TKT-1")
        self.write("TKT-2")
        self.a.link("TKT-1", "TKT-2", "child")
        self.assertEqual(self.fm("TKT-1")["children"], ["TKT-2"])
        self.assertEqual(self.fm("TKT-2")["parent"], "TKT-1")
        self.assertEqual(self.a.view("TKT-2").parent, "TKT-1")
        self.assertEqual(self.a.view("TKT-1").children, ["TKT-2"])

    def test_link_parent_is_the_mirror(self):
        for lt in ("parent", "  Child Of ", "IS CHILD OF"):
            with self.subTest(link_type=lt):
                for f in self.a.board_dir.iterdir():
                    f.unlink()
                self.write("TKT-1")
                self.write("TKT-2")
                self.a.link("TKT-2", "TKT-1", lt)
                self.assertEqual(self.fm("TKT-1")["children"], ["TKT-2"])
                self.assertEqual(self.fm("TKT-2")["parent"], "TKT-1")
                self.assertNotIn("links", self.fm("TKT-1"))
                self.assertNotIn("links", self.fm("TKT-2"))

    def test_both_spellings_produce_identical_files(self):
        self.write("TKT-1")
        self.write("TKT-2")
        self.a.link("TKT-1", "TKT-2", "has child")
        via_child = self.snapshot()
        for f in self.a.board_dir.iterdir():
            f.unlink()
        self.write("TKT-1")
        self.write("TKT-2")
        self.a.link("TKT-2", "TKT-1", "parent")
        self.assertEqual(self.snapshot(), via_child)

    def test_reparent_removes_from_old_parent(self):
        self.write("TKT-1", ["children: [TKT-3, TKT-9]"])
        self.write("TKT-2")
        self.write("TKT-3", ["parent: TKT-1"])
        self.a.link("TKT-3", "TKT-2", "parent")
        self.assertEqual(self.fm("TKT-3")["parent"], "TKT-2")
        self.assertEqual(self.fm("TKT-2")["children"], ["TKT-3"])
        self.assertEqual(self.fm("TKT-1")["children"], ["TKT-9"])

    def test_reparent_with_missing_old_parent(self):
        self.write("TKT-2")
        self.write("TKT-3", ["parent: TKT-1"])  # TKT-1.md does not exist
        self.a.link("TKT-2", "TKT-3", "child")
        self.assertEqual(self.fm("TKT-3")["parent"], "TKT-2")
        self.assertEqual(self.fm("TKT-2")["children"], ["TKT-3"])
        self.assertFalse((self.a.board_dir / "TKT-1.md").exists())

    def test_reparent_from_list_literal_parent(self):
        self.write("TKT-1", ["children: [TKT-3]"])
        self.write("TKT-2")
        self.write("TKT-3", ["parent: [TKT-1]"])
        self.a.link("TKT-2", "TKT-3", "child")
        self.assertEqual(self.fm("TKT-3")["parent"], "TKT-2")
        self.assertEqual(self.fm("TKT-1")["children"], [])

    def test_missing_child_writes_nothing(self):
        self.write("TKT-1")
        before = self.snapshot()
        with self.assertRaises(NotFoundError):
            self.a.link("TKT-1", "TKT-2", "child")
        self.assertEqual(self.snapshot(), before)

    def test_missing_parent_writes_nothing(self):
        self.write("TKT-2", ["parent: TKT-5"])
        self.write("TKT-5", ["children: [TKT-2]"])
        before = self.snapshot()
        with self.assertRaises(NotFoundError):
            self.a.link("TKT-2", "TKT-1", "parent")
        with self.assertRaises(NotFoundError):
            self.a.link("TKT-1", "TKT-2", "child")
        self.assertEqual(self.snapshot(), before)

    def test_self_link_rejected_and_nothing_written(self):
        self.write("TKT-1")
        before = self.snapshot()
        for lt in ("child", "parent"):
            with self.subTest(link_type=lt), self.assertRaises(UsageError):
                self.a.link("TKT-1", "TKT-1", lt)
        self.assertEqual(self.snapshot(), before)

    def test_two_cycle_rejected_and_nothing_written(self):
        self.write("TKT-1", ["children: [TKT-2]"])
        self.write("TKT-2", ["parent: TKT-1"])
        before = self.snapshot()
        with self.assertRaises(UsageError):
            self.a.link("TKT-2", "TKT-1", "child")
        with self.assertRaises(UsageError):
            self.a.link("TKT-1", "TKT-2", "parent")
        self.assertEqual(self.snapshot(), before)

    def test_relink_is_idempotent_and_does_not_rewrite(self):
        self.write("TKT-1")
        self.write("TKT-2")
        self.a.link("TKT-1", "TKT-2", "child")
        after_first = self.snapshot()
        mtimes = {p.name: p.stat().st_mtime_ns for p in self.a.board_dir.iterdir()}
        self.a.link("TKT-1", "TKT-2", "child")
        self.a.link("TKT-2", "TKT-1", "parent")
        self.assertEqual(self.snapshot(), after_first)
        self.assertEqual(
            {p.name: p.stat().st_mtime_ns for p in self.a.board_dir.iterdir()}, mtimes)
        self.assertEqual(self.fm("TKT-1")["children"], ["TKT-2"])

    def test_relink_repairs_a_one_sided_edge(self):
        # As left by a crash after the child write: child names the parent,
        # the parent does not list the child yet.
        self.write("TKT-1")
        self.write("TKT-2", ["parent: TKT-1"])
        self.a.link("TKT-1", "TKT-2", "child")
        self.assertEqual(self.fm("TKT-1")["children"], ["TKT-2"])
        self.assertEqual(self.fm("TKT-2")["parent"], "TKT-1")
        # And the other half (a hand-edited parent that lists a child which
        # does not name it back).
        self.write("TKT-3", ["children: [TKT-4]"])
        self.write("TKT-4")
        self.a.link("TKT-4", "TKT-3", "parent")
        self.assertEqual(self.fm("TKT-3")["children"], ["TKT-4"])
        self.assertEqual(self.fm("TKT-4")["parent"], "TKT-3")

    def test_keeps_existing_children_and_body(self):
        self.write("TKT-1", ["children: TKT-9"])
        self.write("TKT-2")
        self.a.link("TKT-1", "TKT-2", "child")
        self.assertEqual(self.fm("TKT-1")["children"], ["TKT-9", "TKT-2"])
        self.assertEqual(self.a.view("TKT-2").parent, "TKT-1")
        self.assertEqual(self.a.view("TKT-2").summary, "TKT-2")
        self.assertEqual(self.a.view("TKT-1").summary, "TKT-1")


class MarkdownParentBoardScan(_Base):
    """`_set_parent` drops the child from every other ticket's `children`,
    found by scanning the board, not only the parent C's `parent:` names."""

    def record_writes(self):
        writes = []
        real = self.a._write_raw

        def spy(key, fm, body):
            writes.append(key)
            real(key, fm, body)
        self.a._write_raw = spy
        return writes

    def test_one_sided_old_parent_is_cleaned(self):
        # TKT-1 lists TKT-3 but TKT-3 names no parent (a one-sided link from
        # before the inverse edge existed, or a hand edit).
        self.write("TKT-1", ["priority: High", "children: [TKT-9, TKT-3]",
                             "labels: [api]"])
        self.write("TKT-2")
        self.write("TKT-3")
        self.write("TKT-4", ["children: [TKT-9]"])  # unrelated, untouched
        before = self.snapshot()
        old_fm, old_body = self.a._read_raw("TKT-1")
        writes = self.record_writes()
        self.a.link("TKT-2", "TKT-3", "child")
        self.assertEqual(self.a.view("TKT-1").children, ["TKT-9"])
        self.assertEqual(self.a.view("TKT-2").children, ["TKT-3"])
        self.assertEqual(self.a.view("TKT-3").parent, "TKT-2")
        # TKT-1 changed only in its children (key order kept).
        fm, body = self.a._read_raw("TKT-1")
        self.assertEqual(list(fm.items()),
                         list((old_fm | {"children": ["TKT-9"]}).items()))
        self.assertEqual(body, old_body)
        # Child, new parent, stale parent; nothing else rewritten.
        self.assertEqual(writes, ["TKT-3", "TKT-2", "TKT-1"])
        self.assertEqual(self.snapshot()["TKT-4.md"], before["TKT-4.md"])

    def test_every_stale_parent_is_cleaned_across_projects(self):
        # A shared board: the scan is not limited to this project's prefix.
        self.write("TKT-1", ["children: [TKT-3]"])
        self.write("OTHER-1", ["children: [TKT-3]"])
        self.write("TKT-2")
        self.write("TKT-3", ["parent: TKT-1"])
        writes = self.record_writes()
        self.a.link("TKT-3", "TKT-2", "parent")
        self.assertEqual(self.fm("TKT-1")["children"], [])
        self.assertEqual(self.fm("OTHER-1")["children"], [])
        self.assertEqual(self.fm("TKT-2")["children"], ["TKT-3"])
        self.assertEqual(writes, ["TKT-3", "TKT-2", "OTHER-1", "TKT-1"])

    def test_rerun_after_crash_repairs_old_parent(self):
        self.write("TKT-1", ["children: [TKT-3]"])
        self.write("TKT-2")
        self.write("TKT-3", ["parent: TKT-1"])
        real = self.a._write_raw
        writes = []

        def crash_on_third(key, fm, body):
            if len(writes) == 2:
                raise OSError("simulated crash")
            writes.append(key)
            real(key, fm, body)
        self.a._write_raw = crash_on_third
        with self.assertRaises(OSError):
            self.a.link("TKT-2", "TKT-3", "child")
        # Child and new parent landed; the old parent still lists the child,
        # and the child no longer names it.
        self.assertEqual(writes, ["TKT-3", "TKT-2"])
        self.assertEqual(self.fm("TKT-3")["parent"], "TKT-2")
        self.assertEqual(self.fm("TKT-1")["children"], ["TKT-3"])
        self.a._write_raw = real
        writes = self.record_writes()
        self.a.link("TKT-2", "TKT-3", "child")
        self.assertEqual(writes, ["TKT-1"])
        self.assertEqual(self.fm("TKT-1")["children"], [])
        self.assertEqual(self.fm("TKT-2")["children"], ["TKT-3"])
        self.assertEqual(self.fm("TKT-3")["parent"], "TKT-2")

    def test_two_cycle_via_childs_children_rejected(self):
        # TKT-2 lists TKT-1 as a child one-sidedly (TKT-1 names no parent),
        # so making TKT-1 the parent of TKT-2 would be a cycle.
        self.write("TKT-1")
        self.write("TKT-2", ["children: [TKT-1]"])
        before = self.snapshot()
        with self.assertRaises(UsageError):
            self.a.link("TKT-1", "TKT-2", "child")
        with self.assertRaises(UsageError):
            self.a.link("TKT-2", "TKT-1", "parent")
        self.assertEqual(self.snapshot(), before)

    def test_case_folded_self_link_rejected(self):
        self.write("TKT-1")
        before = self.snapshot()
        for args in (("tkt-1", "TKT-1", "child"), ("TKT-1", "tkt-1", "child"),
                     ("tkt-1", "TKT-1", "parent")):
            with self.subTest(args=args), self.assertRaises(UsageError):
                self.a.link(*args)
        self.assertEqual(self.snapshot(), before)


class MarkdownParentIdentity(_Base):
    """Keys are compared by file identity, not by string: two names that
    open one file are one ticket. Hard links give two names for one file on
    every filesystem, so these run everywhere and exercise the same path a
    case-variant spelling takes on a case-insensitive one."""

    def alias(self, key, name):
        os.link(self.a.board_dir / f"{key}.md", self.a.board_dir / f"{name}.md")

    def test_two_cycle_via_parent_edge_under_another_name(self):
        # TKT-2 names TKT-1 as its parent (one-sided). ALIAS-1 is TKT-1.
        self.write("TKT-1")
        self.write("TKT-2", ["parent: TKT-1"])
        self.alias("TKT-1", "ALIAS-1")
        before = self.snapshot()
        with self.assertRaises(UsageError):
            self.a.link("TKT-2", "ALIAS-1", "child")
        self.assertEqual(self.snapshot(), before)

    def test_two_cycle_via_children_edge_under_another_name(self):
        # TKT-1 lists TKT-2 as a child (one-sided). ALIAS-2 is TKT-2.
        self.write("TKT-1", ["children: [TKT-2]"])
        self.write("TKT-2")
        self.alias("TKT-2", "ALIAS-2")
        before = self.snapshot()
        with self.assertRaises(UsageError):
            self.a.link("ALIAS-2", "TKT-1", "child")
        self.assertEqual(self.snapshot(), before)

    def test_self_link_under_another_name_rejected(self):
        self.write("TKT-1")
        self.alias("TKT-1", "ALIAS-1")
        before = self.snapshot()
        with self.assertRaises(UsageError):
            self.a.link("TKT-1", "ALIAS-1", "child")
        self.assertEqual(self.snapshot(), before)

    def test_reparent_cleans_old_parent_listing_another_name(self):
        # The old parent lists the child as TKT-3; the command names it
        # ALIAS-3. The stale scan must still find and clean TKT-1.
        self.write("TKT-1", ["children: [TKT-9, TKT-3]"])
        self.write("TKT-2")
        self.write("TKT-3", ["parent: TKT-1"])
        self.alias("TKT-3", "ALIAS-3")
        self.a.link("TKT-2", "ALIAS-3", "child")
        self.assertEqual(self.fm("TKT-1")["children"], ["TKT-9"])
        self.assertEqual(self.fm("TKT-2")["children"], ["ALIAS-3"])
        self.assertEqual(self.fm("TKT-3")["parent"], "TKT-2")

    def test_new_parent_listing_another_name_is_not_appended_twice(self):
        # TKT-2 already lists the child twice, under both names: it ends
        # up listed once, in the command's spelling, where it first was.
        self.write("TKT-2", ["children: [TKT-8, TKT-3, ALIAS-3]"])
        self.write("TKT-3", ["parent: TKT-2"])
        self.alias("TKT-3", "ALIAS-3")
        self.a.link("TKT-2", "ALIAS-3", "child")
        self.assertEqual(self.fm("TKT-2")["children"], ["TKT-8", "ALIAS-3"])

    def test_missing_entries_compare_by_string(self):
        from adapters.markdown import _TicketIdentity
        self.write("TKT-1")
        self.alias("TKT-1", "ALIAS-1")
        ids = _TicketIdentity(self.a.board_dir)
        self.assertTrue(ids.same("TKT-1", "ALIAS-1"))
        self.assertTrue(ids.same("GONE-1", "GONE-1"))
        self.assertFalse(ids.same("GONE-1", "GONE-2"))
        self.assertFalse(ids.same("GONE-1", "TKT-1"))
        self.assertFalse(ids.same("TKT-1", "GONE-1"))
        # A listed name is its own canonical spelling; a missing one too.
        self.assertEqual(ids.canonical("ALIAS-1"), "ALIAS-1")
        self.assertEqual(ids.canonical("GONE-1"), "GONE-1")


class MarkdownSelfReferenceByIdentity(_Base):
    """A ticket never names itself as parent or child, under any name that
    opens its own file. TKT-9.md is a hard link to TKT-1.md (runs on every
    filesystem, the same path a case variant takes on a case-insensitive
    one)."""

    def setUp(self):
        super().setUp()
        self.write("TKT-1")
        self.write("TKT-2")
        os.link(self.a.board_dir / "TKT-1.md", self.a.board_dir / "TKT-9.md")

    def test_view_drops_parent_naming_own_file(self):
        self.write("TKT-1", ["parent: TKT-9"])
        self.assertEqual(self.a.view("TKT-1").parent, "")
        self.assert_control()

    def test_view_drops_child_naming_own_file(self):
        self.write("TKT-1", ["children: [TKT-9, TKT-2]"])
        self.assertEqual(self.a.view("TKT-1").children, ["TKT-2"])

    def test_list_normalizes_the_same_way(self):
        self.write("TKT-1", ["parent: TKT-9", "children: [TKT-9, TKT-2]"])
        by_key = {t.key: (t.parent, t.children) for t in self.a.list(query="all")}
        self.assertEqual(by_key["TKT-1"], ("", ["TKT-2"]))
        self.assertEqual(by_key["TKT-9"], ("", ["TKT-2"]))
        self.assertEqual(by_key["TKT-2"], ("", []))

    def test_apply_update_clears_parent_naming_own_file(self):
        self.write("TKT-1", ["parent: TKT-2"])
        t = self.a.apply(self.doc("parent: TKT-9"), key="TKT-1")
        self.assertEqual(t.parent, "")
        self.assertNotIn("parent", self.fm("TKT-1"))

    def test_apply_update_drops_child_naming_own_file(self):
        t = self.a.apply(self.doc("children: [TKT-9, TKT-2]"), key="TKT-1")
        self.assertEqual(t.children, ["TKT-2"])
        self.assertEqual(self.fm("TKT-1")["children"], ["TKT-2"])
        # Control: names that open other files are kept as written.
        t = self.a.apply(self.doc("parent: TKT-2", "children: [TKT-2, TKT-5]"),
                         key="TKT-1")
        self.assertEqual((t.parent, t.children), ("TKT-2", ["TKT-2", "TKT-5"]))
        self.assertEqual(self.fm("TKT-1")["children"], ["TKT-2", "TKT-5"])

    def test_list_does_not_stat_tickets_outside_a_hierarchy(self):
        from adapters import markdown
        calls = []
        real = markdown._TicketIdentity.of

        def spy(ids, key):
            calls.append(key)
            return real(ids, key)
        markdown._TicketIdentity.of = spy
        self.addCleanup(setattr, markdown._TicketIdentity, "of", real)
        self.a.list(query="all")
        self.assertEqual(calls, [])


class MarkdownApplyCreateSelfChild(_Base):

    def test_apply_create_drops_child_naming_the_new_key(self):
        # The board is empty, so the new ticket is minted as TKT-1.
        t = self.a.apply(self.doc("children: [TKT-1, TKT-5]"))
        self.assertEqual(t.key, "TKT-1")
        self.assertEqual(self.fm("TKT-1")["children"], ["TKT-5"])
        t = self.a.apply(self.doc("children: [TKT-2]"))
        self.assertEqual(t.key, "TKT-2")
        self.assertNotIn("children", self.fm("TKT-2"))


def _folds_case(directory):
    """True when `directory` is on a case-insensitive filesystem."""
    probe = Path(directory) / "CaseProbe.tmp"
    probe.write_text("")
    try:
        return (Path(directory) / "caseprobe.tmp").exists()
    finally:
        probe.unlink()


class MarkdownParentCaseInsensitive(_Base):
    """Case-variant spellings on a case-insensitive filesystem (macOS APFS
    by default). Skipped on a case-sensitive one."""

    def setUp(self):
        super().setUp()
        if not _folds_case(self.a.board_dir):
            self.skipTest("filesystem is case-sensitive")

    def test_reverse_link_in_other_case_is_a_two_cycle(self):
        self.write("TKT-1", ["children: [TKT-2]"])
        self.write("TKT-2", ["parent: TKT-1"])
        before = self.snapshot()
        with self.assertRaises(UsageError):
            self.a.link("tkt-2", "tkt-1", "child")
        with self.assertRaises(UsageError):
            self.a.link("tkt-1", "TKT-2", "parent")
        self.assertEqual(self.snapshot(), before)

    def test_reparent_in_other_case_cleans_old_parent(self):
        self.write("TKT-1", ["children: [TKT-3]"])
        self.write("TKT-2")
        self.write("TKT-3", ["parent: TKT-1"])
        self.a.link("TKT-2", "tkt-3", "child")
        self.assertEqual(self.fm("TKT-1")["children"], [])
        self.assertEqual(self.fm("TKT-3")["parent"], "TKT-2")
        self.assertEqual(self.fm("TKT-2")["children"], ["TKT-3"])
        # Retrying in either spelling changes nothing.
        after = self.snapshot()
        self.a.link("TKT-2", "tkt-3", "child")
        self.a.link("tkt-2", "TKT-3", "child")
        self.assertEqual(self.snapshot(), after)

    def test_lowercase_command_writes_on_disk_spelling(self):
        self.write("TKT-1")
        self.write("TKT-2")
        self.a.link("tkt-1", "tkt-2", "child")
        self.assertEqual(self.fm("TKT-1")["children"], ["TKT-2"])
        self.assertEqual(self.fm("TKT-2")["parent"], "TKT-1")
        self.assertEqual(sorted(self.snapshot()), ["TKT-1.md", "TKT-2.md"])

    def test_miscased_existing_entry_is_normalized_not_duplicated(self):
        self.write("TKT-2", ["children: [tkt-3]"])
        self.write("TKT-3", ["parent: TKT-2"])
        self.a.link("TKT-2", "TKT-3", "child")
        self.assertEqual(self.fm("TKT-2")["children"], ["TKT-3"])

    def test_view_drops_case_variant_self_reference(self):
        self.write("TKT-1", ["parent: tkt-1", "children: [tkt-1, TKT-2]"])
        t = self.a.view("TKT-1")
        self.assertEqual((t.parent, t.children), ("", ["TKT-2"]))
        self.assert_control()

    def test_apply_update_clears_case_variant_self_parent(self):
        self.write("TKT-1", ["parent: TKT-2"])
        t = self.a.apply(self.doc("parent: tkt-1", "children: [tkt-1, TKT-2]"),
                         key="TKT-1")
        self.assertEqual((t.parent, t.children), ("", ["TKT-2"]))
        self.assertNotIn("parent", self.fm("TKT-1"))
        self.assertEqual(self.fm("TKT-1")["children"], ["TKT-2"])

    def test_apply_create_drops_case_variant_of_the_new_key(self):
        # The board is empty, so the new ticket is minted as TKT-1.
        t = self.a.apply(self.doc("parent: tkt-1", "children: [tkt-1, TKT-5]"))
        self.assertEqual((t.key, t.parent, t.children), ("TKT-1", "", ["TKT-5"]))
        self.assertNotIn("parent", self.fm("TKT-1"))
        self.assertEqual(self.fm("TKT-1")["children"], ["TKT-5"])


class MarkdownParentCaseSensitive(_Base):
    """On a case-sensitive filesystem `TKT-3.md` and `tkt-3.md` are two
    tickets; identity comparison must not conflate them. Skipped on a
    case-insensitive one."""

    def setUp(self):
        super().setUp()
        if _folds_case(self.a.board_dir):
            self.skipTest("filesystem is case-insensitive")

    def test_case_variant_files_stay_distinct(self):
        self.write("TKT-1", ["children: [tkt-3]"])
        self.write("TKT-2")
        self.write("TKT-3")
        self.write("tkt-3", ["parent: TKT-1"])
        before = self.snapshot()
        self.a.link("TKT-2", "TKT-3", "child")
        self.assertEqual(self.fm("TKT-3")["parent"], "TKT-2")
        self.assertEqual(self.fm("TKT-2")["children"], ["TKT-3"])
        # tkt-3's edges, and TKT-1 listing it, are untouched.
        after = self.snapshot()
        self.assertEqual(after["TKT-1.md"], before["TKT-1.md"])
        self.assertEqual(after["tkt-3.md"], before["tkt-3.md"])

    def test_case_variant_parent_is_another_ticket(self):
        self.write("TKT-1", ["parent: tkt-1", "children: [tkt-1]"])
        self.write("tkt-1")
        t = self.a.view("TKT-1")
        self.assertEqual((t.parent, t.children), ("tkt-1", ["tkt-1"]))
        t = self.a.apply(self.doc("parent: tkt-1", "children: [tkt-1]"),
                         key="TKT-1")
        self.assertEqual((t.parent, t.children), ("tkt-1", ["tkt-1"]))
        self.assertEqual(self.fm("TKT-1")["parent"], "tkt-1")


if __name__ == "__main__":
    unittest.main()
