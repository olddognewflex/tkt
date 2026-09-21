"""Regression tests for TKT-59: a shared markdown board must only hand a repo
its own project's tickets.

`_all_tickets` globbed every `*.md` in `[markdown].board_dir`. That directory
is routinely shared by several repos, each with its own config, so `tkt list`
returned other projects' tickets and `select-ticket` -- which takes the first
candidate -- could auto-select one. Scoping is by key prefix, the convention
`_next_key` already mints against. Fully offline: the adapter is pointed at a
temp board dir, so nothing touches a real board. Run from the repo root:

    python3 -m unittest tests.test_markdown_scope
"""
import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.markdown import MarkdownAdapter  # noqa: E402
from core.config import Config  # noqa: E402

ROLES = {"backlog": "Backlog", "todo": "To Do", "in_progress": "In Progress",
         "review": "In Review", "done": "Done", "blocked": "Blocked"}

TICKET = """---
type: Story
status: To Do
priority: High
assignee: raymond
---

# {key}

Body for {key}.
"""


def board(tmp, keys):
    d = Path(tmp) / "board"
    d.mkdir(parents=True, exist_ok=True)
    for k in keys:
        (d / f"{k}.md").write_text(TICKET.format(key=k))
    return d


def adapter(tmp, keys, project="TKT"):
    d = board(tmp, keys)
    cfg = Config({"ticketing": {"provider": "markdown", "project": project},
                  "markdown": {"board_dir": str(d), "state_dir": str(Path(tmp) / "state"),
                               "me": "raymond"},
                  "board": {"roles": dict(ROLES)},
                  "queries": {"tier1": 'status = "To Do"',
                              "tier2": 'status = "To Do" ORDER BY priority DESC'}},
                 Path(tmp) / ".sdlc" / "config.toml")
    return MarkdownAdapter(cfg)


class SharedBoardScoping(unittest.TestCase):
    """The shape that bit in practice: one board, several repos."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def keys(self, **kw):
        a = adapter(self.tmp, kw.pop("keys"), **kw)
        return [t.key for t in a.list(tier="1")]

    def test_only_this_projects_tickets_are_listed(self):
        self.assertEqual(
            self.keys(keys=["TKT-1", "TEACH-10", "TKT-2", "OPS-3"]),
            ["TKT-1", "TKT-2"])

    def test_unset_project_lists_everything(self):
        """Single-project boards must keep working unchanged."""
        self.assertEqual(
            sorted(self.keys(keys=["TKT-1", "TEACH-10"], project="")),
            ["TEACH-10", "TKT-1"])

    def test_prefix_match_requires_the_hyphen(self):
        """TKTX is a different project, not a TKT ticket."""
        self.assertEqual(
            self.keys(keys=["TKT-1", "TKTX-1", "TKTX-22"]), ["TKT-1"])

    def test_prefix_is_not_matched_mid_key(self):
        self.assertEqual(self.keys(keys=["SUBTKT-1", "TKT-4"]), ["TKT-4"])

    def test_board_of_only_other_projects_yields_nothing(self):
        """A tier of only out-of-project tickets must fall through empty,
        so select-ticket moves to the next tier instead of picking one."""
        self.assertEqual(self.keys(keys=["TEACH-10", "OPS-3"]), [])

    def test_empty_board_is_not_an_error(self):
        self.assertEqual(self.keys(keys=[]), [])

    def test_view_stays_unscoped(self):
        """A blocker or link may point at another project; it must resolve."""
        a = adapter(self.tmp, ["TKT-1", "TEACH-10"])
        self.assertEqual(a.view("TEACH-10").key, "TEACH-10")
        self.assertEqual([t.key for t in a.list(tier="1")], ["TKT-1"])

    def test_out_of_project_files_are_never_read(self):
        """Skipped on the filename, before the file is opened.

        Asserted by recording every `_read_raw` key rather than by feeding
        in a malformed file: the parser tolerates arbitrary content and
        returns an empty ticket, so a "corrupt file" test would pass with
        or without the scoping and prove nothing.
        """
        a = adapter(self.tmp, ["TKT-1", "TEACH-10", "OPS-3"])
        read = []
        original = a._read_raw
        a._read_raw = lambda key: (read.append(key), original(key))[1]
        a.list(tier="1")
        self.assertEqual(read, ["TKT-1"])


class KeyMintingStaysConsistent(unittest.TestCase):
    """`_next_key` must find the same keys `_all_tickets` lists.

    They disagreed: `_next_key` globbed `f"{prefix}-*.md"` while the listing
    compares the stem literally. With a project containing a glob
    metacharacter the two diverge and `create` overwrites a live ticket.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_project_with_glob_metacharacters_does_not_overwrite(self):
        a = adapter(self.tmp, ["A[BC]-1", "AB-7"], project="A[BC]")
        # Globbing would match AB-7.md via the character class and miss the
        # literal A[BC]-1.md, minting A[BC]-1 over an existing ticket.
        self.assertEqual(a._next_key(""), "A[BC]-2")
        a.create("Story", "brand new")
        board_dir = Path(self.tmp) / "board"
        self.assertIn("# A[BC]-1", (board_dir / "A[BC]-1.md").read_text())
        self.assertTrue((board_dir / "A[BC]-2.md").is_file())

    def test_minting_ignores_other_projects(self):
        a = adapter(self.tmp, ["TKT-1", "TEACH-99"])
        self.assertEqual(a._next_key(""), "TKT-2")


class DoctorNamesAPrefixMismatch(unittest.TestCase):
    """An empty board and a misconfigured project look identical to `list`.

    Both return `[]`, so `select-ticket` reports "nothing to work on" for
    what is really a config error. `doctor` is the validation surface, so
    it has to be the thing that tells them apart.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def names(self, checks):
        return {c.name: c for c in checks}

    def test_fails_when_no_key_matches(self):
        a = adapter(self.tmp, ["TKT-1", "TKT-2"], project="ACME")
        check = self.names(a.doctor())["keys match project prefix"]
        self.assertFalse(check.ok)
        self.assertIn("0 of 2", check.detail)
        self.assertIn("ACME-", check.detail)

    def test_passes_when_some_key_matches(self):
        a = adapter(self.tmp, ["TKT-1", "TEACH-9"])
        check = self.names(a.doctor())["keys match project prefix"]
        self.assertTrue(check.ok)
        self.assertIn("1 of 2", check.detail)

    def test_empty_board_is_not_a_mismatch(self):
        """A genuinely empty board must not raise a false alarm."""
        check = self.names(adapter(self.tmp, []).doctor())["keys match project prefix"]
        self.assertTrue(check.ok)

    def test_absent_when_no_project_configured(self):
        """Nothing to check: unset project means no scoping."""
        self.assertNotIn("keys match project prefix",
                         self.names(adapter(self.tmp, ["TKT-1"], project="").doctor()))


class UnprefixedKeys(unittest.TestCase):
    """Human-named ticket files become invisible once a project is set.

    `README.md` invites hand-authored files ("the markdown stays
    human-canonical"), so this is a real constraint, not a curiosity. Pinned
    so it is a documented rule rather than a surprise; `doctor` above is what
    makes it diagnosable.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_human_named_files_are_invisible_when_scoped(self):
        a = adapter(self.tmp, ["fix-login", "2026-notes", "TKT-1"])
        self.assertEqual([t.key for t in a.list(tier="1")], ["TKT-1"])

    def test_they_are_listed_when_project_is_omitted_entirely(self):
        """Acceptance 2 is about *unset*, not about an empty string."""
        d = board(self.tmp, ["fix-login", "TKT-1"])
        cfg = Config({"ticketing": {"provider": "markdown"},   # no `project` key
                      "markdown": {"board_dir": str(d),
                                   "state_dir": str(Path(self.tmp) / "state"),
                                   "me": "raymond"},
                      "board": {"roles": dict(ROLES)},
                      "queries": {"tier1": 'status = "To Do"'}},
                     Path(self.tmp) / ".sdlc" / "config.toml")
        self.assertEqual(sorted(t.key for t in MarkdownAdapter(cfg).list(tier="1")),
                         ["TKT-1", "fix-login"])


if __name__ == "__main__":
    unittest.main()
