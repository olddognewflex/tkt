"""TKT-59 follow-on: the adversarial edges of markdown board scoping.

TKT-59's bug was a false *positive*: `_all_tickets` globbed every `*.md` in
`[markdown].board_dir`, a directory routinely shared by several repos, so
`tkt list` handed a repo other projects' tickets and `select-ticket` -- which
takes the first candidate -- could auto-select one. Live, a tier query returned
63 tickets spanning TKT/TEACH/OPS. The fix skips any board file whose key does
not start with `f"{[ticketing].project}-"`.

`tests/test_markdown_scope.py` covers the happy path of that fix. This file
covers the ways prefix matching can still go wrong, and the surfaces the fix
deliberately did *not* touch:

  * prefix *semantics* -- a key that is exactly the project with no suffix
    (`TKT.md`), extra hyphens (`TKT-1-2`) and non-numeric suffixes (`TKT-abc`),
    case, a project that is itself hyphenated (`MY-PROJ`), a project holding
    glob metacharacters (`[`, `*`, `?`) -- which must be matched literally,
    since `_all_tickets` filters on a string while `_next_key` globs, and a
    whitespace-only project, which must fail closed rather than leak;
  * the `create`/`_next_key` pairing -- what a repo mints must be what it can
    list, and the unset-project case where it is not;
  * cross-project *writes*: `transition` / `comment` / `edit` read by key
    through `_read_raw`, so a ticket this repo cannot list it can still mutate;
  * cross-project *blockers*, which is the case `view` staying unscoped exists
    for -- a TKT ticket blocked by a TEACH ticket must resolve, with the right
    resolved flag for Done vs not;
  * `JqlSubset` running after the scoping, so `ORDER BY priority` and
    `assignee = currentUser()` rank and filter only in-project tickets;
  * board hygiene -- a shared board collects non-`.md` files, subdirectories,
    dotfiles, stray `*.md` directories and dangling symlinks, and out-of-project
    junk must not be able to break this project's listing; plus a missing
    board_dir, which must stay a `ProviderError`.

Fully offline: every adapter is pointed at a fresh temp board dir, so no real
board (least of all a shared one) is read or written. Run from the repo root:

    python3 -m unittest tests.test_markdown_scope_edges
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
from core.errors import ProviderError  # noqa: E402

ROLES = {"backlog": "Backlog", "todo": "To Do", "in_progress": "In Progress",
         "review": "In Review", "done": "Done", "blocked": "Blocked"}

TICKET = """---
type: Story
status: {status}
priority: {priority}
assignee: {assignee}
---

# {key}

Body for {key}.
"""

BLOCKED_TICKET = """---
type: Story
status: To Do
priority: High
assignee: raymond
blocked_by: [{blocker}]
---

# {key}

Body for {key}.
"""


class ScopeEdges(unittest.TestCase):
    """One temp board per test; nothing outside the temp dir is touched."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.board_dir = Path(self.tmp) / "board"

    # ---- fixtures ---------------------------------------------------------

    def write(self, key, status="To Do", priority="High", assignee="raymond"):
        self.board_dir.mkdir(parents=True, exist_ok=True)
        path = self.board_dir / f"{key}.md"
        path.write_text(TICKET.format(key=key, status=status,
                                      priority=priority, assignee=assignee))
        return path

    def adapter(self, keys=(), project="TKT"):
        for k in keys:
            self.write(*k) if isinstance(k, tuple) else self.write(k)
        self.board_dir.mkdir(parents=True, exist_ok=True)
        cfg = Config({"ticketing": {"provider": "markdown", "project": project},
                      "markdown": {"board_dir": str(self.board_dir),
                                   "state_dir": str(Path(self.tmp) / "state"),
                                   "me": "raymond"},
                      "board": {"roles": dict(ROLES)},
                      "queries": {
                          "tier1": 'status = "To Do"',
                          "tier2": 'status = "To Do" ORDER BY priority DESC',
                          "mine": 'assignee = currentUser()',
                      }},
                     Path(self.tmp) / ".sdlc" / "config.toml")
        return MarkdownAdapter(cfg)

    def listed(self, keys=(), project="TKT", tier="1", query=None):
        a = self.adapter(keys, project=project)
        return [t.key for t in a.list(tier=None if query else tier, query=query)]

    # ---- 1. prefix semantics ----------------------------------------------

    def test_key_equal_to_the_project_is_not_a_ticket(self):
        """`TKT.md` has no `-N` suffix, so it is not a TKT ticket. The guard
        matches `TKT-`, not `TKT`, which is exactly what keeps it out."""
        self.assertEqual(self.listed(["TKT", "TKT-1"]), ["TKT-1"])

    def test_extra_hyphens_and_non_numeric_suffixes_stay_in_project(self):
        """Scoping keys on the prefix must not quietly become "keys must be
        PROJ-<int>": `TKT-1-2` and `TKT-abc` are this project's files, while
        `TKTX-1-2` and `SUBTKT-abc` are not."""
        self.assertEqual(
            self.listed(["TKT-1-2", "TKT-abc", "TKTX-1-2", "SUBTKT-abc"]),
            ["TKT-1-2", "TKT-abc"])

    def test_key_matching_is_case_sensitive(self):
        """`tkt-9` is a different project from `TKT-9`, not the same ticket
        in another case -- so an all-lowercase board with an uppercase
        `[ticketing].project` lists nothing rather than everything."""
        self.assertEqual(self.listed(["tkt-9", "Tkt-8", "TKT-1"]), ["TKT-1"])

    def test_hyphenated_project_matches_only_its_own_keys(self):
        """A project may contain a hyphen itself; the boundary is still the
        hyphen *after* the whole project string."""
        self.assertEqual(
            self.listed(["MY-PROJ-1", "MY-PROJX-1", "MY-1", "PROJ-1"],
                        project="MY-PROJ"),
            ["MY-PROJ-1"])

    def test_glob_metacharacters_in_a_project_are_matched_literally(self):
        """`_all_tickets` filters keys as strings while `_next_key` globs.
        A project holding `[`, `*` or `?` must not turn the listing into a
        pattern match that pulls in other projects."""
        for project, keys, expected in (
            ("A[BC]", ["A[BC]-1", "AB-1", "AC-1"], ["A[BC]-1"]),
            ("A*", ["A*-1", "AB-1"], ["A*-1"]),
            ("A?", ["A?-1", "AB-1"], ["A?-1"]),
        ):
            with self.subTest(project=project):
                shutil.rmtree(self.board_dir, ignore_errors=True)
                self.assertEqual(self.listed(keys, project=project), expected)

    def test_whitespace_only_project_fails_closed(self):
        """A typo'd `project = " "` is truthy, so it scopes to the prefix
        `" -"` and matches nothing. Empty is the documented "no scoping"
        value (see tests/test_markdown_scope.py); whitespace must not quietly
        become that and hand back the whole shared board."""
        self.assertEqual(self.listed(["TKT-1", "TEACH-2"], project=" "), [])

    # ---- 2. create / _next_key coherence ----------------------------------

    def test_create_mints_a_key_this_project_can_list(self):
        """The pairing that matters: on a shared board, what `create` mints
        must come back from `list`, and nothing else must."""
        a = self.adapter(["TKT-1", "TEACH-9"])
        made = a.create("Story", "new work")
        self.assertEqual(made.key, "TKT-2")
        self.assertEqual([t.key for t in a.list(tier="1")], ["TKT-1", "TKT-2"])

    def test_unset_project_mints_the_tkt_default_but_lists_everything(self):
        """Pins today's behaviour, which is incoherent and flagged in the
        TKT-59 QA report: with `[ticketing].project` unset, `_next_key` falls
        back to the hard-coded `"TKT"` while `_all_tickets` scopes to nothing
        at all, so the adapter writes into one namespace and reads from the
        whole shared board. It is not destructive -- `_next_key` globs
        `TKT-*.md` and so still takes max+1 -- but create and list disagree
        about which project this repo is."""
        a = self.adapter(["TKT-1", "TEACH-9"], project="")
        self.assertEqual(a.create("Story", "new work").key, "TKT-2")
        self.assertEqual(sorted(t.key for t in a.list(tier="1")),
                         ["TEACH-9", "TKT-1", "TKT-2"])

    # ---- 3. cross-project writes -------------------------------------------

    def test_another_projects_ticket_is_unlistable_but_still_writable(self):
        """Pins today's behaviour. `transition` / `comment` / `edit` read by
        key through `_read_raw`, which the fix left unscoped along with
        `view`, so a repo can still mutate a ticket it cannot list. That is
        load-bearing for `view` (blockers, links) but not obviously wanted for
        writes; flagged in the TKT-59 QA report."""
        a = self.adapter(["TKT-1", "TEACH-9"])
        a.transition("TEACH-9", "done")
        a.comment("TEACH-9", "touched from the TKT repo")
        a.edit("TEACH-9", priority="Low")

        foreign = a.view("TEACH-9")
        self.assertEqual(foreign.status, "Done")
        self.assertEqual(foreign.priority, "Low")
        self.assertIn("touched from the TKT repo", Path(foreign.url).read_text())
        self.assertEqual([t.key for t in a.list(tier="1")], ["TKT-1"])

    # ---- 4. cross-project blockers -----------------------------------------

    def test_cross_project_blocker_resolves_and_flags_correctly(self):
        """The case `view` staying unscoped exists for. A TKT ticket blocked
        by a TEACH ticket must resolve the blocker by key, and report it
        unresolved until the TEACH ticket is Done."""
        a = self.adapter(["TEACH-9"])
        (self.board_dir / "TKT-1.md").write_text(
            BLOCKED_TICKET.format(key="TKT-1", blocker="TEACH-9"))

        self.assertEqual(a.view("TKT-1").blocked_by,
                         [{"key": "TEACH-9", "resolved": False}])
        self.assertEqual(a.blockers("TKT-1"),
                         [{"key": "TEACH-9", "resolved": False}])

        a.transition("TEACH-9", "done")
        self.assertEqual(a.view("TKT-1").blocked_by,
                         [{"key": "TEACH-9", "resolved": True}])
        self.assertEqual(a.blockers("TKT-1"), [])

    def test_blocker_naming_a_ticket_not_on_the_board_stays_unresolved(self):
        """A dangling cross-project reference must block, not silently clear:
        `_is_resolved` cannot see a Done status it cannot read."""
        a = self.adapter(["TKT-2", "TEACH-5"])
        (self.board_dir / "TKT-1.md").write_text(
            BLOCKED_TICKET.format(key="TKT-1", blocker="TEACH-404"))
        self.assertEqual(a.blockers("TKT-1"),
                         [{"key": "TEACH-404", "resolved": False}])
        self.assertEqual([t.key for t in a.list(tier="1")], ["TKT-1", "TKT-2"])

    # ---- 5. query interaction ----------------------------------------------

    def test_order_by_priority_ranks_only_in_project_tickets(self):
        """Scoping runs before `JqlSubset`, so a higher-priority ticket from
        another project must not be ranked -- let alone ranked first, which is
        precisely what `select-ticket` would then pick."""
        self.assertEqual(
            self.listed([("TKT-1", "To Do", "Low", "raymond"),
                         ("TEACH-9", "To Do", "Highest", "raymond"),
                         ("TKT-2", "To Do", "Highest", "raymond")],
                        tier="2"),
            ["TKT-2", "TKT-1"])

    def test_current_user_filter_applies_within_the_project_only(self):
        """`assignee = currentUser()` must not reach across projects just
        because the same person is assigned there."""
        self.assertEqual(
            self.listed([("TKT-1", "To Do", "High", "raymond"),
                         ("TEACH-9", "To Do", "High", "raymond"),
                         ("TKT-2", "To Do", "High", "someone-else")],
                        query="mine"),
            ["TKT-1"])

    # ---- 6. board hygiene ---------------------------------------------------

    def test_out_of_project_junk_cannot_break_the_listing(self):
        """A shared board accumulates other repos' debris. None of it is this
        project's business, and a `*.md` *directory* belonging to another
        project would otherwise reach `_read_raw` and abort the whole listing
        with NotFoundError."""
        a = self.adapter(["TKT-1"])
        (self.board_dir / "README.txt").write_text("not a ticket")
        (self.board_dir / "notes").mkdir()
        (self.board_dir / "notes" / "TKT-99.md").write_text(
            TICKET.format(key="TKT-99", status="To Do",
                          priority="High", assignee="raymond"))
        (self.board_dir / ".TEACH-hidden.md").write_text("dotfile")
        (self.board_dir / "TEACH-dir.md").mkdir()

        self.assertEqual([t.key for t in a.list(tier="1")], ["TKT-1"])

    def test_symlinked_tickets_follow_the_same_scoping(self):
        """Some shared-board setups symlink tickets in. An in-project symlink
        to a real file is a ticket; an out-of-project one -- even a dangling
        one, which would raise NotFoundError if it were read -- is skipped on
        its name, before anything is opened."""
        a = self.adapter(["TKT-1"])
        elsewhere = Path(self.tmp) / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "ticket.md").write_text(
            TICKET.format(key="TKT-7", status="To Do",
                          priority="High", assignee="raymond"))
        os.symlink(elsewhere / "ticket.md", self.board_dir / "TKT-7.md")
        os.symlink(Path(self.tmp) / "does-not-exist.md",
                   self.board_dir / "TEACH-dangling.md")

        self.assertEqual([t.key for t in a.list(tier="1")], ["TKT-1", "TKT-7"])

    def test_missing_board_dir_is_a_provider_error(self):
        """Distinct from an empty board dir, which is simply no tickets: a
        board_dir that is not there is a misconfiguration and must surface as
        ProviderError (exit 4), not as an empty candidate list that
        `select-ticket` would read as "nothing to do"."""
        a = self.adapter(["TKT-1"])
        shutil.rmtree(self.board_dir)
        with self.assertRaises(ProviderError):
            a.list(tier="1")


if __name__ == "__main__":
    unittest.main()
