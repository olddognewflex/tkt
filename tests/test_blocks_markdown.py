"""TKT-73: on the markdown board, `tkt link --type blocks|"is blocked by"`
writes both edges of a blocker link -- the blocker's `blocks:` and the
blocked ticket's `blocked_by:` -- so `tkt blockers` (which reads
`blocked_by:`) cannot miss a link written from the blocker's side.

Offline: the adapter is pointed at a temp board dir. Run from the repo root:

    python3 -m unittest tests.test_blocks_markdown
"""
import json
import os
import shutil
import subprocess
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

    def write(self, key, fm_lines=(), status="To Do"):
        text = f"---\ntype: Story\nstatus: {status}\n" + "".join(
            f"{line}\n" for line in fm_lines) + f"---\n# {key}\n"
        (self.a.board_dir / f"{key}.md").write_text(text)

    def fm(self, key):
        return self.a._read_raw(key)[0]

    def edge(self, key, name):
        """A frontmatter list key, normalized: absent or empty -> []."""
        value = self.fm(key).get(name) or []
        return [value] if isinstance(value, str) else list(value)

    def snapshot(self):
        """Every file under the board dir, by name -> content."""
        return {p.name: p.read_text() for p in sorted(self.a.board_dir.iterdir())}


class MarkdownBlocksLink(_Base):

    # ---- both edges ---------------------------------------------------------

    def test_blocks_writes_source_blocks(self):
        # Control: the side the adapter already writes. Keeps the target-side
        # tests below honest -- they fail on the missing edge, not on setup.
        self.write("TKT-1")
        self.write("TKT-2")
        self.a.link("TKT-1", "TKT-2", "blocks")
        self.assertEqual(self.edge("TKT-1", "blocks"), ["TKT-2"])

    def test_blocks_writes_target_blocked_by(self):
        self.write("TKT-1")
        self.write("TKT-2")
        self.a.link("TKT-1", "TKT-2", "blocks")
        self.assertEqual(self.edge("TKT-2", "blocked_by"), ["TKT-1"])

    def test_is_blocked_by_writes_source_blocked_by(self):
        self.write("TKT-1")
        self.write("TKT-2")
        self.a.link("TKT-1", "TKT-2", "is blocked by")
        self.assertEqual(self.edge("TKT-1", "blocked_by"), ["TKT-2"])

    def test_is_blocked_by_writes_target_blocks(self):
        self.write("TKT-1")
        self.write("TKT-2")
        self.a.link("TKT-1", "TKT-2", "is blocked by")
        self.assertEqual(self.edge("TKT-2", "blocks"), ["TKT-1"])

    def test_link_type_case_and_whitespace_ignored(self):
        self.write("TKT-1")
        self.write("TKT-2")
        self.a.link("TKT-1", "TKT-2", "  Is Blocked By ")
        self.assertEqual(self.edge("TKT-1", "blocked_by"), ["TKT-2"])
        self.assertEqual(self.edge("TKT-2", "blocks"), ["TKT-1"])

    def test_depends_on_is_blocked_by(self):
        # `depends on` already means "is blocked by" on openkanban, github and
        # linear; markdown filed it under a generic `links:` key instead.
        self.write("TKT-1")
        self.write("TKT-2")
        self.a.link("TKT-1", "TKT-2", "depends on")
        self.assertEqual(self.edge("TKT-1", "blocked_by"), ["TKT-2"])
        self.assertEqual(self.edge("TKT-2", "blocks"), ["TKT-1"])
        self.assertEqual(self.edge("TKT-1", "links"), [])

    def test_appends_to_existing_edges(self):
        self.write("TKT-1", ["blocks: [TKT-9]"])
        self.write("TKT-2", ["blocked_by: [TKT-8]"])
        self.a.link("TKT-1", "TKT-2", "blocks")
        self.assertEqual(self.edge("TKT-1", "blocks"), ["TKT-9", "TKT-2"])
        self.assertEqual(self.edge("TKT-2", "blocked_by"), ["TKT-8", "TKT-1"])

    def test_scalar_edge_tolerated(self):
        # A hand-edited `blocked_by: X` (no brackets) parses as a string.
        self.write("TKT-1")
        self.write("TKT-2", ["blocked_by: TKT-8"])
        self.a.link("TKT-1", "TKT-2", "blocks")
        self.assertEqual(self.edge("TKT-2", "blocked_by"), ["TKT-8", "TKT-1"])

    def test_completes_one_sided_link(self):
        # A link written before this fix (blocker side only): re-running it
        # adds the missing edge without duplicating the existing one.
        self.write("TKT-1", ["blocks: [TKT-2]"])
        self.write("TKT-2")
        self.a.link("TKT-1", "TKT-2", "blocks")
        self.assertEqual(self.edge("TKT-1", "blocks"), ["TKT-2"])
        self.assertEqual(self.edge("TKT-2", "blocked_by"), ["TKT-1"])

    # ---- idempotence --------------------------------------------------------

    def test_relink_is_noop_on_both_files(self):
        self.write("TKT-1")
        self.write("TKT-2")
        self.a.link("TKT-1", "TKT-2", "blocks")
        before = self.snapshot()
        self.a.link("TKT-1", "TKT-2", "blocks")
        self.assertEqual(self.snapshot(), before)

    def test_inverse_relink_is_noop(self):
        # `B is blocked by A` is the same edge as `A blocks B`.
        self.write("TKT-1")
        self.write("TKT-2")
        self.a.link("TKT-1", "TKT-2", "blocks")
        before = self.snapshot()
        self.a.link("TKT-2", "TKT-1", "is blocked by")
        self.assertEqual(self.snapshot(), before)

    # ---- nothing half-written -----------------------------------------------

    def test_blocks_missing_target_writes_nothing(self):
        self.write("TKT-1")
        before = self.snapshot()
        with self.assertRaises(NotFoundError):
            self.a.link("TKT-1", "TKT-404", "blocks")
        self.assertEqual(self.snapshot(), before)

    def test_is_blocked_by_missing_target_writes_nothing(self):
        self.write("TKT-1")
        before = self.snapshot()
        with self.assertRaises(NotFoundError):
            self.a.link("TKT-1", "TKT-404", "is blocked by")
        self.assertEqual(self.snapshot(), before)

    def test_missing_source_writes_nothing(self):
        self.write("TKT-2")
        before = self.snapshot()
        with self.assertRaises(NotFoundError):
            self.a.link("TKT-404", "TKT-2", "blocks")
        self.assertEqual(self.snapshot(), before)

    def test_self_link_rejected(self):
        self.write("TKT-1")
        before = self.snapshot()
        with self.assertRaises(UsageError):
            self.a.link("TKT-1", "TKT-1", "blocks")
        self.assertEqual(self.snapshot(), before)

    def test_self_link_rejected_case_variant(self):
        # Same rule parent links apply: compared case-insensitively, before
        # any read, so the error does not depend on the filesystem.
        self.write("TKT-1")
        before = self.snapshot()
        with self.assertRaises(UsageError):
            self.a.link("TKT-1", "tkt-1", "is blocked by")
        self.assertEqual(self.snapshot(), before)

    # ---- what readers see ---------------------------------------------------

    def test_blockers_reports_source_after_blocks(self):
        self.write("TKT-1")
        self.write("TKT-2")
        self.a.link("TKT-1", "TKT-2", "blocks")
        self.assertEqual([b["key"] for b in self.a.blockers("TKT-2")], ["TKT-1"])

    def test_blockers_drops_resolved_source(self):
        self.write("TKT-1", status="Done")
        self.write("TKT-2")
        self.a.link("TKT-1", "TKT-2", "blocks")
        self.assertEqual(self.a.blockers("TKT-2"), [])

    def test_view_shows_both_sides(self):
        self.write("TKT-1")
        self.write("TKT-2")
        self.a.link("TKT-2", "TKT-1", "is blocked by")
        self.assertEqual(self.a.view("TKT-1").blocks, ["TKT-2"])
        self.assertEqual([b["key"] for b in self.a.view("TKT-2").blocked_by],
                         ["TKT-1"])

    # ---- other link types untouched -----------------------------------------

    def test_other_link_type_still_one_sided(self):
        self.write("TKT-1")
        self.write("TKT-2")
        self.a.link("TKT-1", "TKT-2", "relates to")
        self.assertEqual(self.edge("TKT-1", "links"), ["relates to:TKT-2"])
        self.assertEqual(self.edge("TKT-2", "links"), [])


# QA Author -- adversarial tests
# Break-It dimensions covered: idempotency (an edge stored under another
# name for the same file), encoding/input (case-variant keys, hard-link
# aliases), error propagation (exit codes and output through the CLI).

class MarkdownBlocksIdentity(_Base):
    """Two names for one ticket file. TKT-9.md is a hard link to TKT-2.md and
    TKT-8.md one to TKT-1.md -- runs on every filesystem, and takes the same
    `_TicketIdentity` path a case variant takes on a case-insensitive one."""

    def setUp(self):
        super().setUp()
        self.write("TKT-1")
        self.write("TKT-2")
        os.link(self.a.board_dir / "TKT-2.md", self.a.board_dir / "TKT-9.md")
        os.link(self.a.board_dir / "TKT-1.md", self.a.board_dir / "TKT-8.md")

    def test_identity_blocks_edge_under_alias_not_duplicated(self):
        # Red if the dedup compares strings: TKT-2 would be appended next to
        # TKT-9, listing one blocker twice.
        self.write("TKT-1", ["blocks: [TKT-9]"])
        self.write("TKT-2", ["blocked_by: [TKT-1]"])
        before = self.snapshot()
        self.a.link("TKT-1", "TKT-2", "blocks")
        self.assertEqual(self.snapshot(), before)

    def test_identity_blocked_by_edge_under_alias_not_duplicated(self):
        self.write("TKT-1", ["blocks: [TKT-2]"])
        self.write("TKT-2", ["blocked_by: [TKT-8]"])
        before = self.snapshot()
        self.a.link("TKT-2", "TKT-1", "is blocked by")
        self.assertEqual(self.snapshot(), before)

    def test_identity_completes_missing_side_only(self):
        # One side present under an alias, the other missing: only the
        # missing side is written.
        self.write("TKT-1", ["blocks: [TKT-9]"])
        self.a.link("TKT-1", "TKT-2", "blocks")
        self.assertEqual(self.edge("TKT-1", "blocks"), ["TKT-9"])
        self.assertEqual(self.edge("TKT-2", "blocked_by"), ["TKT-1"])

    def test_identity_self_link_via_hard_link_rejected(self):
        # Red without the file-identity self check: the strings differ, so
        # only the identity comparison catches it.
        before = self.snapshot()
        with self.assertRaises(UsageError):
            self.a.link("TKT-2", "TKT-9", "blocks")
        self.assertEqual(self.snapshot(), before)

    def test_identity_self_link_via_hard_link_rejected_inverse(self):
        before = self.snapshot()
        with self.assertRaises(UsageError):
            self.a.link("TKT-8", "TKT-1", "is blocked by")
        self.assertEqual(self.snapshot(), before)


def _folds_case(directory):
    """True when `directory` is on a case-insensitive filesystem."""
    probe = Path(directory) / "CaseProbe.tmp"
    probe.write_text("")
    try:
        return (Path(directory) / "caseprobe.tmp").exists()
    finally:
        probe.unlink()


class MarkdownBlocksCaseInsensitive(_Base):
    """Case-variant keys on a case-insensitive filesystem (macOS APFS by
    default). Skipped on a case-sensitive one."""

    def setUp(self):
        super().setUp()
        if not _folds_case(self.a.board_dir):
            self.skipTest("filesystem is case-sensitive")
        self.write("TKT-1")
        self.write("TKT-2")

    def test_case_lowercase_keys_written_in_disk_spelling(self):
        # Red if the raw keys are written: `blocks: [tkt-2]`.
        self.a.link("tkt-1", "tkt-2", "blocks")
        self.assertEqual(self.edge("TKT-1", "blocks"), ["TKT-2"])
        self.assertEqual(self.edge("TKT-2", "blocked_by"), ["TKT-1"])
        self.assertEqual(sorted(p.name for p in self.a.board_dir.iterdir()),
                         ["TKT-1.md", "TKT-2.md"])

    def test_case_lowercase_inverse_written_in_disk_spelling(self):
        self.a.link("tkt-2", "tkt-1", "is blocked by")
        self.assertEqual(self.edge("TKT-2", "blocked_by"), ["TKT-1"])
        self.assertEqual(self.edge("TKT-1", "blocks"), ["TKT-2"])

    def test_case_relink_in_other_case_no_duplicate(self):
        self.a.link("tkt-1", "tkt-2", "blocks")
        before = self.snapshot()
        self.a.link("TKT-1", "TKT-2", "blocks")
        self.assertEqual(self.snapshot(), before)

    def test_case_hand_written_lowercase_edge_not_duplicated(self):
        # Red if the dedup compares strings: TKT-2 next to tkt-2.
        self.write("TKT-1", ["blocks: [tkt-2]"])
        self.write("TKT-2", ["blocked_by: [tkt-1]"])
        before = self.snapshot()
        self.a.link("TKT-1", "TKT-2", "blocks")
        self.assertEqual(self.snapshot(), before)

    def test_case_blockers_through_lowercase_link(self):
        self.a.link("tkt-1", "tkt-2", "blocks")
        self.assertEqual([b["key"] for b in self.a.blockers("TKT-2")], ["TKT-1"])


REPO = Path(__file__).resolve().parent.parent


class MarkdownBlocksCli(unittest.TestCase):
    """`tkt link` / `tkt blockers` end to end through `core/cli.py`, run as a
    subprocess against a temp board, from a cwd outside the repo."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / ".sdlc" / "board").mkdir(parents=True)
        (self.tmp / ".sdlc" / "config.toml").write_text(
            '[ticketing]\nprovider = "markdown"\nproject = "TKT"\n'
            '[markdown]\nboard_dir = ".sdlc/board"\nstate_dir = ".sdlc/state"\n'
            'me = "raymond"\n'
            '[board.roles]\nbacklog = "Backlog"\ntodo = "To Do"\n'
            'in_progress = "In Progress"\ndone = "Done"\n')
        for key in ("TKT-1", "TKT-2"):
            (self.tmp / ".sdlc" / "board" / f"{key}.md").write_text(
                f"---\ntype: Story\nstatus: To Do\n---\n# {key}\n")

    def tkt(self, *args):
        env = {k: v for k, v in os.environ.items() if k != "TKT_CONFIG"}
        return subprocess.run([sys.executable, str(REPO / "tkt"), *args],
                              cwd=self.tmp, env=env, capture_output=True,
                              text=True, timeout=60)

    def test_blockers_json_after_link(self):
        r = self.tkt("link", "TKT-1", "--to", "TKT-2", "--type", "blocks")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.tkt("blockers", "TKT-2", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([b["key"] for b in json.loads(r.stdout)], ["TKT-1"])

    def test_self_link_exits_64_and_writes_nothing(self):
        before = (self.tmp / ".sdlc" / "board" / "TKT-1.md").read_text()
        r = self.tkt("link", "TKT-1", "--to", "TKT-1", "--type", "blocks")
        self.assertEqual(r.returncode, 64, r.stderr)
        self.assertIn("cannot block itself", r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertEqual((self.tmp / ".sdlc" / "board" / "TKT-1.md").read_text(),
                         before)

    def test_missing_target_exits_4_and_writes_nothing(self):
        before = (self.tmp / ".sdlc" / "board" / "TKT-1.md").read_text()
        r = self.tkt("link", "TKT-1", "--to", "TKT-404", "--type", "blocks")
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIn("TKT-404", r.stderr)
        self.assertEqual((self.tmp / ".sdlc" / "board" / "TKT-1.md").read_text(),
                         before)


if __name__ == "__main__":
    unittest.main()
