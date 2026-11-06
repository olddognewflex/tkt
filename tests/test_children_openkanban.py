"""TKT-19: the openkanban adapter keeps children in the ticket `meta` map
(`meta.children`, comma-separated like `blocks`) and `tkt link --type child`
appends to it.

Offline: a temp config dir holds projects.json + tickets/<project_id>.json in
openkanban's own layout. Run from the repo root:

    python3 -m unittest tests.test_children_openkanban
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

PID = "proj-1"


def raw_ticket(tid, meta=None):
    return {"id": tid, "project_id": PID, "title": tid, "description": "",
            "status": "backlog", "agent_status": "none", "labels": [],
            "priority": 3, "meta": meta if meta is not None else {}}


class OpenKanbanChildren(unittest.TestCase):

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

    def seed(self, *tickets):
        (self.tmp / "tickets" / f"{PID}.json").write_text(json.dumps(
            {"project_id": PID, "tickets": {t["id"]: t for t in tickets},
             "updated_at": "2026-01-01T00:00:00Z"}))

    def meta_of(self, tid):
        store = json.loads((self.tmp / "tickets" / f"{PID}.json").read_text())
        return store["tickets"][tid]["meta"]

    # ---- read ---------------------------------------------------------------

    def test_meta_children_parsed(self):
        self.seed(raw_ticket("p", {"children": "c1,c2"}))
        self.assertEqual(self.a.view("p").children, ["c1", "c2"])
        self.assertEqual(self.a.view("p").to_dict()["children"], ["c1", "c2"])

    def test_view_accepts_children_flag_and_ignores_it(self):
        self.seed(raw_ticket("p", {"children": "c1"}))
        self.assertEqual(self.a.view("p", children=False).children, ["c1"])

    def test_dedupe_and_own_key_dropped(self):
        self.seed(raw_ticket("p", {"children": "c2,p,c1,c2,"}))
        self.assertEqual(self.a.view("p").children, ["c2", "c1"])

    def test_no_children_bucket_is_empty(self):
        self.seed(raw_ticket("p"), raw_ticket("q") | {"meta": None})
        self.assertEqual(self.a.view("p").children, [])
        self.assertEqual(self.a.view("q").children, [])

    def test_list_carries_children(self):
        self.seed(raw_ticket("p", {"children": "c"}), raw_ticket("c"))
        by_key = {t.key: t.children for t in self.a.list(query="all")}
        self.assertEqual(by_key, {"p": ["c"], "c": []})

    # ---- link ---------------------------------------------------------------

    def test_link_child_appends_without_duplicates(self):
        self.seed(raw_ticket("p"), raw_ticket("c1"), raw_ticket("c2"))
        self.a.link("p", "c1", "child")
        self.a.link("p", "c2", "  Has Child ")
        self.a.link("p", "c1", "CHILD")
        self.assertEqual(self.meta_of("p")["children"], "c1,c2")
        self.assertEqual(self.a.view("p").children, ["c1", "c2"])

    def test_other_link_types_leave_children_untouched(self):
        self.seed(raw_ticket("p", {"children": "c1"}), raw_ticket("x"))
        self.a.link("p", "x", "blocks")
        self.a.link("p", "x", "is blocked by")
        self.a.link("p", "x", "relates to")
        meta = self.meta_of("p")
        self.assertEqual(meta["children"], "c1")
        self.assertEqual(meta["blocks"], "x")
        self.assertEqual(meta["blocked_by"], "x")
        self.assertEqual(meta["relates to"], "x")
        self.assertEqual(self.a.view("p").children, ["c1"])

    def test_child_link_does_not_touch_block_buckets(self):
        self.seed(raw_ticket("p"), raw_ticket("c"))
        self.a.link("p", "c", "child")
        meta = self.meta_of("p")
        self.assertNotIn("blocks", meta)
        self.assertNotIn("blocked_by", meta)
        self.assertNotIn("child", meta)


if __name__ == "__main__":
    unittest.main()
