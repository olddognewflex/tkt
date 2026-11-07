"""TKT-72: the openkanban adapter keeps a ticket's parent in `meta.parent`
(one key) and `tkt link --type child|parent` writes both hierarchy edges
(`meta.parent` on the child, `meta.children` on the parent) in one atomic
store write.

Offline: a temp config dir holds projects.json + tickets/<project_id>.json in
openkanban's own layout. Run from the repo root:

    python3 -m unittest tests.test_parent_openkanban
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.openkanban import OpenKanbanAdapter  # noqa: E402
from core.config import Config  # noqa: E402
from core.errors import NotFoundError, UsageError  # noqa: E402

PID = "proj-1"


def raw_ticket(tid, meta=None):
    return {"id": tid, "project_id": PID, "title": tid, "description": "",
            "status": "backlog", "agent_status": "none", "labels": [],
            "priority": 3, "meta": meta if meta is not None else {}}


class OpenKanbanParent(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "projects.json").write_text(json.dumps(
            {"projects": {PID: {"id": PID, "name": "demo"}}}))
        (self.tmp / "tickets").mkdir()
        cfg = Config({"ticketing": {"provider": "openkanban"},
                      "openkanban": {"project": "demo", "config_dir": str(self.tmp)},
                      "board": {"roles": {"todo": "backlog", "done": "done"}},
                      "queries": {"all": 'status = "backlog"'}},
                     self.tmp / ".sdlc" / "config.toml")
        self.a = OpenKanbanAdapter(cfg)

    @property
    def store_path(self):
        return self.tmp / "tickets" / f"{PID}.json"

    def seed(self, *tickets):
        self.store_path.write_text(json.dumps(
            {"project_id": PID, "tickets": {t["id"]: t for t in tickets},
             "updated_at": "2026-01-01T00:00:00Z"}))

    def meta_of(self, tid):
        return json.loads(self.store_path.read_text())["tickets"][tid]["meta"]

    def assert_control(self):
        # Paired with each "reads as none" case so the test also fails if
        # parent parsing were removed (which would read "" everywhere).
        self.seed(raw_ticket("y", {"parent": "z"}))
        self.assertEqual(self.a.view("y").parent, "z")

    # ---- read ---------------------------------------------------------------

    def test_meta_parent_parsed(self):
        self.seed(raw_ticket("c", {"parent": "p"}))
        self.assertEqual(self.a.view("c").parent, "p")
        self.assertEqual(self.a.view("c").to_dict()["parent"], "p")

    def test_parent_whitespace_stripped(self):
        self.seed(raw_ticket("c", {"parent": " p "}))
        self.assertEqual(self.a.view("c").parent, "p")

    def test_no_parent_is_empty(self):
        self.seed(raw_ticket("a"), raw_ticket("b") | {"meta": None},
                  raw_ticket("c", {"parent": ""}), raw_ticket("d", {"parent": None}))
        for tid in "abcd":
            self.assertEqual(self.a.view(tid).parent, "", tid)
        self.assert_control()

    def test_own_key_is_empty(self):
        self.seed(raw_ticket("c", {"parent": "c"}))
        self.assertEqual(self.a.view("c").parent, "")
        self.assert_control()

    def test_list_carries_parent(self):
        self.seed(raw_ticket("p"), raw_ticket("c", {"parent": "p"}))
        by_key = {t.key: t.parent for t in self.a.list(query="all")}
        self.assertEqual(by_key, {"p": "", "c": "p"})

    def test_view_accepts_hierarchy_flag_and_ignores_it(self):
        self.seed(raw_ticket("c", {"parent": "p"}))
        self.assertEqual(self.a.view("c", hierarchy=False).parent, "p")

    # ---- link ---------------------------------------------------------------

    def test_link_child_writes_both_edges(self):
        self.seed(raw_ticket("p"), raw_ticket("c"))
        self.a.link("p", "c", "child")
        self.assertEqual(self.meta_of("p")["children"], "c")
        self.assertEqual(self.meta_of("c")["parent"], "p")
        self.assertEqual(self.a.view("c").parent, "p")
        self.assertEqual(self.a.view("p").children, ["c"])

    def test_link_parent_is_the_mirror(self):
        for lt in ("parent", "  Child Of ", "IS CHILD OF"):
            with self.subTest(link_type=lt):
                self.seed(raw_ticket("p"), raw_ticket("c"))
                self.a.link("c", "p", lt)
                self.assertEqual(self.meta_of("p"), {"children": "c"})
                self.assertEqual(self.meta_of("c"), {"parent": "p"})

    def test_link_into_null_meta(self):
        self.seed(raw_ticket("p") | {"meta": None}, raw_ticket("c") | {"meta": None})
        self.a.link("p", "c", "has child")
        self.assertEqual(self.meta_of("p"), {"children": "c"})
        self.assertEqual(self.meta_of("c"), {"parent": "p"})

    def test_reparent_removes_from_old_parent(self):
        self.seed(raw_ticket("p0", {"children": "c,x"}), raw_ticket("p1"),
                  raw_ticket("c", {"parent": "p0"}))
        self.a.link("c", "p1", "parent")
        self.assertEqual(self.meta_of("c")["parent"], "p1")
        self.assertEqual(self.meta_of("p1")["children"], "c")
        self.assertEqual(self.meta_of("p0")["children"], "x")

    def test_reparent_last_child_drops_bucket(self):
        self.seed(raw_ticket("p0", {"children": "c"}), raw_ticket("p1"),
                  raw_ticket("c", {"parent": "p0"}))
        self.a.link("p1", "c", "child")
        self.assertEqual(self.a.view("p0").children, [])
        self.assertNotIn("children", self.meta_of("p0"))

    def test_reparent_with_missing_old_parent(self):
        self.seed(raw_ticket("p1"), raw_ticket("c", {"parent": "gone"}))
        self.a.link("p1", "c", "child")
        self.assertEqual(self.meta_of("c")["parent"], "p1")
        self.assertEqual(self.meta_of("p1")["children"], "c")
        self.assertNotIn("gone", json.loads(self.store_path.read_text())["tickets"])

    def test_missing_child_or_parent_writes_nothing(self):
        self.seed(raw_ticket("p"), raw_ticket("c", {"parent": "q"}),
                  raw_ticket("q", {"children": "c"}))
        before = self.store_path.read_bytes()
        for args in (("p", "nope", "child"), ("nope", "c", "child"),
                     ("c", "nope", "parent"), ("nope", "p", "parent")):
            with self.subTest(args=args), self.assertRaises(NotFoundError):
                self.a.link(*args)
        self.assertEqual(self.store_path.read_bytes(), before)

    def test_self_link_rejected_and_nothing_written(self):
        self.seed(raw_ticket("p"))
        before = self.store_path.read_bytes()
        for lt in ("child", "parent"):
            with self.subTest(link_type=lt), self.assertRaises(UsageError):
                self.a.link("p", "p", lt)
        self.assertEqual(self.store_path.read_bytes(), before)

    def test_two_cycle_rejected_and_nothing_written(self):
        self.seed(raw_ticket("p", {"children": "c"}), raw_ticket("c", {"parent": "p"}))
        before = self.store_path.read_bytes()
        with self.assertRaises(UsageError):
            self.a.link("c", "p", "child")
        with self.assertRaises(UsageError):
            self.a.link("p", "c", "parent")
        self.assertEqual(self.store_path.read_bytes(), before)

    def test_relink_is_idempotent_and_does_not_rewrite(self):
        self.seed(raw_ticket("p"), raw_ticket("c"))
        self.a.link("p", "c", "child")
        after_first = self.store_path.read_bytes()
        saves = []
        real_save = self.a._save_store
        self.a._save_store = lambda store: (saves.append(1), real_save(store))
        self.a.link("p", "c", "child")
        self.a.link("c", "p", "parent")
        self.assertEqual(saves, [])  # nothing changed, so no store write
        self.assertEqual(self.store_path.read_bytes(), after_first)
        self.assertEqual(self.meta_of("p")["children"], "c")

    def test_keeps_existing_children(self):
        self.seed(raw_ticket("p", {"children": "x"}), raw_ticket("c"))
        self.a.link("p", "c", "child")
        self.assertEqual(self.meta_of("p")["children"], "x,c")
        self.assertEqual(self.meta_of("c")["parent"], "p")

    # ---- board scan, cycles, case, null meta -------------------------------

    def ticket_of(self, tid):
        return json.loads(self.store_path.read_text())["tickets"][tid]

    def count_saves(self):
        saves = []
        real_save = self.a._save_store
        self.a._save_store = lambda store: (saves.append(1), real_save(store))
        return saves

    def test_one_sided_old_parent_is_cleaned(self):
        # p0 lists c but c names no parent (a link from before the inverse
        # edge existed, or a hand edit): c must not end up under two parents.
        p0 = raw_ticket("p0", {"children": "x,c", "blocks": "z"}) | {
            "updated_at": "2026-01-01T00:00:00Z"}
        self.seed(p0, raw_ticket("p1"), raw_ticket("c"),
                  raw_ticket("u", {"children": "x"}))
        before = json.loads(self.store_path.read_text())["tickets"]
        saves = self.count_saves()
        self.a.link("p1", "c", "child")
        self.assertEqual(saves, [1])  # still one atomic store write
        self.assertEqual(self.a.view("p0").children, ["x"])
        self.assertEqual(self.a.view("p1").children, ["c"])
        self.assertEqual(self.a.view("c").parent, "p1")
        after = self.ticket_of("p0")
        self.assertNotEqual(after.pop("updated_at"), "2026-01-01T00:00:00Z")
        want = dict(before["p0"])
        want.pop("updated_at")
        want["meta"] = {"children": "x", "blocks": "z"}
        self.assertEqual(after, want)
        self.assertEqual(self.ticket_of("u"), before["u"])  # untouched

    def test_every_stale_parent_is_cleaned(self):
        self.seed(raw_ticket("p0", {"children": "c"}),
                  raw_ticket("q", {"children": "c,y"}), raw_ticket("p1"),
                  raw_ticket("c", {"parent": "p0"}))
        self.a.link("c", "p1", "parent")
        self.assertNotIn("children", self.meta_of("p0"))
        self.assertEqual(self.meta_of("q")["children"], "y")
        self.assertEqual(self.meta_of("p1")["children"], "c")

    def test_rerun_repairs_a_stale_old_parent(self):
        # c and p1 already agree; p0 still lists c (as a single-sided edit
        # would leave it). Re-running the link cleans p0.
        self.seed(raw_ticket("p0", {"children": "c"}),
                  raw_ticket("p1", {"children": "c"}),
                  raw_ticket("c", {"parent": "p1"}))
        self.a.link("p1", "c", "child")
        self.assertNotIn("children", self.meta_of("p0"))
        self.assertEqual(self.meta_of("p1")["children"], "c")
        self.assertEqual(self.meta_of("c")["parent"], "p1")

    def test_two_cycle_via_childs_children_rejected(self):
        # c lists p as a child one-sidedly (p names no parent).
        self.seed(raw_ticket("p"), raw_ticket("c", {"children": "p"}))
        before = self.store_path.read_bytes()
        with self.assertRaises(UsageError):
            self.a.link("p", "c", "child")
        with self.assertRaises(UsageError):
            self.a.link("c", "p", "parent")
        self.assertEqual(self.store_path.read_bytes(), before)

    def test_case_folded_self_link_rejected(self):
        self.seed(raw_ticket("p"))
        before = self.store_path.read_bytes()
        for args in (("P", "p", "child"), ("p", "P", "child"), ("P", "p", "parent")):
            with self.subTest(args=args), self.assertRaises(UsageError):
                self.a.link(*args)
        self.assertEqual(self.store_path.read_bytes(), before)

    def test_non_hierarchy_link_into_null_meta(self):
        for lt, want in (("relates to", {"relates to": "q"}),
                         ("blocks", {"blocks": "q"}),
                         ("is blocked by", {"blocked_by": "q"})):
            with self.subTest(link_type=lt):
                self.seed(raw_ticket("p") | {"meta": None}, raw_ticket("q"))
                self.a.link("p", "q", lt)
                self.assertEqual(self.meta_of("p"), want)

    def test_non_hierarchy_link_into_null_bucket(self):
        self.seed(raw_ticket("p", {"blocks": None}), raw_ticket("q"))
        self.a.link("p", "q", "blocks")
        self.assertEqual(self.meta_of("p"), {"blocks": "q"})


if __name__ == "__main__":
    unittest.main()
