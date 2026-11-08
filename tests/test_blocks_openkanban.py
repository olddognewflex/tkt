"""TKT-73: on openkanban, `tkt link --type blocks|"is blocked by"|"depends
on"` writes both edges of a blocker link -- the blocker's `meta.blocks` and
the blocked ticket's `meta.blocked_by` -- in one atomic store write, so
`tkt blockers` (which reads `meta.blocked_by`) cannot miss a link written
from the blocker's side.

Offline: a temp config dir holds projects.json + tickets/<project_id>.json in
openkanban's own layout. Run from the repo root:

    python3 -m unittest tests.test_blocks_openkanban
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.openkanban import OpenKanbanAdapter  # noqa: E402
from core.config import Config  # noqa: E402
from core.errors import NotFoundError, UsageError  # noqa: E402

PID = "proj-1"


def raw_ticket(tid, meta=None, status="backlog"):
    return {"id": tid, "project_id": PID, "title": tid, "description": "",
            "status": status, "agent_status": "none", "labels": [],
            "priority": 3, "meta": meta if meta is not None else {}}


class OpenKanbanBlocksLink(unittest.TestCase):

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

    def edge(self, tid, name):
        """`meta.<name>` as a key list (stored comma-joined)."""
        meta = json.loads(self.store_path.read_text())["tickets"][tid]["meta"] or {}
        return [x for x in (meta.get(name) or "").split(",") if x]

    def tickets(self):
        """The store's tickets, minus the timestamps every save bumps."""
        data = json.loads(self.store_path.read_text())["tickets"]
        return {k: {f: v for f, v in t.items() if f != "updated_at"}
                for k, t in data.items()}

    # ---- both edges ---------------------------------------------------------

    def test_blocks_writes_source_blocks(self):
        # Control: the side the adapter already writes.
        self.seed(raw_ticket("a"), raw_ticket("b"))
        self.a.link("a", "b", "blocks")
        self.assertEqual(self.edge("a", "blocks"), ["b"])

    def test_blocks_writes_target_blocked_by(self):
        self.seed(raw_ticket("a"), raw_ticket("b"))
        self.a.link("a", "b", "blocks")
        self.assertEqual(self.edge("b", "blocked_by"), ["a"])

    def test_is_blocked_by_writes_source_blocked_by(self):
        self.seed(raw_ticket("a"), raw_ticket("b"))
        self.a.link("a", "b", "is blocked by")
        self.assertEqual(self.edge("a", "blocked_by"), ["b"])

    def test_is_blocked_by_writes_target_blocks(self):
        self.seed(raw_ticket("a"), raw_ticket("b"))
        self.a.link("a", "b", "is blocked by")
        self.assertEqual(self.edge("b", "blocks"), ["a"])

    def test_depends_on_writes_target_blocks(self):
        self.seed(raw_ticket("a"), raw_ticket("b"))
        self.a.link("a", "b", "depends on")
        self.assertEqual(self.edge("a", "blocked_by"), ["b"])
        self.assertEqual(self.edge("b", "blocks"), ["a"])

    def test_null_meta_on_target_tolerated(self):
        t = raw_ticket("b")
        t["meta"] = None
        self.seed(raw_ticket("a"), t)
        self.a.link("a", "b", "blocks")
        self.assertEqual(self.edge("b", "blocked_by"), ["a"])

    def test_appends_to_existing_edges(self):
        self.seed(raw_ticket("a", {"blocks": "z"}),
                  raw_ticket("b", {"blocked_by": "y"}))
        self.a.link("a", "b", "blocks")
        self.assertEqual(self.edge("a", "blocks"), ["z", "b"])
        self.assertEqual(self.edge("b", "blocked_by"), ["y", "a"])

    def test_completes_one_sided_link(self):
        self.seed(raw_ticket("a", {"blocks": "b"}), raw_ticket("b"))
        self.a.link("a", "b", "blocks")
        self.assertEqual(self.edge("a", "blocks"), ["b"])
        self.assertEqual(self.edge("b", "blocked_by"), ["a"])

    # ---- idempotence --------------------------------------------------------

    def test_relink_is_noop(self):
        self.seed(raw_ticket("a"), raw_ticket("b"))
        self.a.link("a", "b", "blocks")
        before = self.tickets()
        self.a.link("a", "b", "blocks")
        self.assertEqual(self.tickets(), before)

    def test_inverse_relink_is_noop(self):
        self.seed(raw_ticket("a"), raw_ticket("b"))
        self.a.link("a", "b", "blocks")
        before = self.tickets()
        self.a.link("b", "a", "is blocked by")
        self.assertEqual(self.tickets(), before)

    # ---- nothing half-written -----------------------------------------------

    def test_missing_target_writes_nothing(self):
        self.seed(raw_ticket("a"))
        before = self.store_path.read_text()
        with self.assertRaises(NotFoundError):
            self.a.link("a", "nope", "blocks")
        self.assertEqual(self.store_path.read_text(), before)

    def test_missing_target_is_blocked_by_writes_nothing(self):
        self.seed(raw_ticket("a"))
        before = self.store_path.read_text()
        with self.assertRaises(NotFoundError):
            self.a.link("a", "nope", "is blocked by")
        self.assertEqual(self.store_path.read_text(), before)

    def test_self_link_rejected(self):
        self.seed(raw_ticket("a"))
        before = self.store_path.read_text()
        with self.assertRaises(UsageError):
            self.a.link("a", "a", "blocks")
        self.assertEqual(self.store_path.read_text(), before)

    # ---- what readers see ---------------------------------------------------

    def test_blockers_reports_source_after_blocks(self):
        self.seed(raw_ticket("a"), raw_ticket("b"))
        self.a.link("a", "b", "blocks")
        self.assertEqual([b["key"] for b in self.a.blockers("b")], ["a"])

    def test_blockers_drops_done_source(self):
        self.seed(raw_ticket("a", status="done"), raw_ticket("b"))
        self.a.link("a", "b", "blocks")
        self.assertEqual(self.a.blockers("b"), [])

    # QA Author -- adversarial tests
    # Break-It dimensions covered: idempotency (re-link must not touch the
    # store at all). Timestamps have one-second resolution, so comparing the
    # file text could pass by luck; count the saves instead.

    def test_relink_does_not_save_store(self):
        self.seed(raw_ticket("a"), raw_ticket("b"))
        self.a.link("a", "b", "blocks")
        with mock.patch.object(self.a, "_save_store",
                               wraps=self.a._save_store) as save:
            self.a.link("a", "b", "blocks")
        save.assert_not_called()

    def test_inverse_relink_does_not_save_store(self):
        self.seed(raw_ticket("a"), raw_ticket("b"))
        self.a.link("a", "b", "blocks")
        with mock.patch.object(self.a, "_save_store",
                               wraps=self.a._save_store) as save:
            self.a.link("b", "a", "is blocked by")
        save.assert_not_called()

    def test_first_link_saves_once(self):
        # Control for the two above: a real change is saved, exactly once
        # (both edges in one atomic write).
        self.seed(raw_ticket("a"), raw_ticket("b"))
        with mock.patch.object(self.a, "_save_store",
                               wraps=self.a._save_store) as save:
            self.a.link("a", "b", "blocks")
        self.assertEqual(save.call_count, 1)
        self.assertEqual(self.edge("b", "blocked_by"), ["a"])

    def test_completing_one_side_bumps_only_that_ticket(self):
        # Red if updated_at is bumped on a ticket whose edge already existed.
        self.seed(raw_ticket("a", {"blocks": "b"}), raw_ticket("b"))
        for t in ("a", "b"):
            data = json.loads(self.store_path.read_text())
            data["tickets"][t]["updated_at"] = "2000-01-01T00:00:00Z"
            self.store_path.write_text(json.dumps(data))
        self.a.link("a", "b", "blocks")
        data = json.loads(self.store_path.read_text())["tickets"]
        self.assertEqual(data["a"]["updated_at"], "2000-01-01T00:00:00Z")
        self.assertNotEqual(data["b"]["updated_at"], "2000-01-01T00:00:00Z")

    # ---- other link types untouched -----------------------------------------

    def test_other_link_type_recorded_on_source_only(self):
        self.seed(raw_ticket("a"), raw_ticket("b"))
        self.a.link("a", "b", "relates to")
        meta = json.loads(self.store_path.read_text())["tickets"]
        self.assertEqual(meta["a"]["meta"].get("relates to"), "b")
        self.assertNotIn("relates to", meta["b"]["meta"])


if __name__ == "__main__":
    unittest.main()
