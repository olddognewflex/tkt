"""TKT-114: a ticket's git branches and pull requests.

The markdown adapter reads optional `branches:` / `prs:` frontmatter lists
into `Ticket.branches` / `Ticket.prs`, round-trips them through `tkt apply`,
and `tkt edit --add-branch/--remove-branch/--add-pr/--remove-pr` maintains
them. The CLI validates added values before they reach a backend and prints
both lists in human `view`. Every adapter's JSON carries both keys.

Offline: the adapter is pointed at a temp board dir. Run from the repo root:

    python3 -m unittest tests.test_vcs_links_markdown
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.markdown import MarkdownAdapter  # noqa: E402
from core import cli  # noqa: E402
from core.config import Config  # noqa: E402
from core.errors import NotFoundError  # noqa: E402
from core.schema import Ticket  # noqa: E402

ROLES = {"backlog": "Backlog", "todo": "To Do", "in_progress": "In Progress",
         "done": "Done"}
PR1 = "https://github.com/olddognewflex/tkt/pull/96"
PR2 = "https://github.com/olddognewflex/tktban/pull/12"


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

    def write(self, key, fm_lines=(), body=""):
        text = "---\ntype: Story\nstatus: To Do\n" + "".join(
            f"{line}\n" for line in fm_lines) + f"---\n# {key}\n{body}"
        (self.a.board_dir / f"{key}.md").write_text(text)

    def fm(self, key):
        return self.a._read_raw(key)[0]

    def text(self, key):
        return (self.a.board_dir / f"{key}.md").read_text()


class Read(_Base):

    def test_missing_keys_read_as_empty(self):
        self.write("TKT-1")
        t = self.a.view("TKT-1")
        self.assertEqual((t.branches, t.prs), ([], []))

    def test_multiple_entries_in_order(self):
        self.write("TKT-1", ["branches: [feature/tkt-1-a, "
                             "olddognewflex/tktban@feature/tkb-12-x]",
                             f"prs: [{PR2}, {PR1}]"])
        t = self.a.view("TKT-1")
        self.assertEqual(t.branches, ["feature/tkt-1-a",
                                      "olddognewflex/tktban@feature/tkb-12-x"])
        self.assertEqual(t.prs, [PR2, PR1])

    def test_duplicates_and_blanks_dropped(self):
        self.write("TKT-1", ["branches: [b1, b1, '', b2]", f"prs: [{PR1}, {PR1}]"])
        t = self.a.view("TKT-1")
        self.assertEqual(t.branches, ["b1", "b2"])
        self.assertEqual(t.prs, [PR1])

    def test_bare_scalar_is_one_entry(self):
        self.write("TKT-1", ["branches: feature/tkt-1-a", f"prs: {PR1}"])
        t = self.a.view("TKT-1")
        self.assertEqual((t.branches, t.prs), (["feature/tkt-1-a"], [PR1]))

    def test_list_carries_them(self):
        self.write("TKT-1", ["branches: [b1]", f"prs: [{PR1}]"])
        t, = self.a.list(query="all")
        self.assertEqual((t.branches, t.prs), (["b1"], [PR1]))


class Edit(_Base):

    def test_add_to_a_ticket_without_the_keys(self):
        self.write("TKT-1")
        t = self.a.edit("TKT-1", add_branches=["b1", "o/r@b2"], add_prs=[PR1])
        self.assertEqual(t.branches, ["b1", "o/r@b2"])
        self.assertEqual(t.prs, [PR1])
        self.assertIn("branches: [b1, o/r@b2]", self.text("TKT-1"))

    def test_add_is_idempotent_and_appends(self):
        self.write("TKT-1", ["branches: [b1]"])
        self.a.edit("TKT-1", add_branches=["b1"])
        self.assertEqual(self.fm("TKT-1")["branches"], ["b1"])
        t = self.a.edit("TKT-1", add_branches=["b2", "b1"])
        self.assertEqual(t.branches, ["b1", "b2"])

    def test_remove_and_absent_remove(self):
        self.write("TKT-1", ["branches: [b1, b2]", f"prs: [{PR1}, {PR2}]"])
        t = self.a.edit("TKT-1", remove_branches=["b1", "nope"], remove_prs=[PR2])
        self.assertEqual((t.branches, t.prs), (["b2"], [PR1]))

    def test_emptied_list_drops_its_key(self):
        self.write("TKT-1", ["branches: [b1]", f"prs: [{PR1}]"])
        self.a.edit("TKT-1", remove_branches=["b1"], remove_prs=[PR1])
        fm = self.fm("TKT-1")
        self.assertNotIn("branches", fm)
        self.assertNotIn("prs", fm)

    def test_add_and_remove_same_value_removes(self):
        self.write("TKT-1")
        t = self.a.edit("TKT-1", add_branches=["b1"], remove_branches=["b1"])
        self.assertEqual(t.branches, [])

    def test_other_edits_leave_lists_alone(self):
        self.write("TKT-1", ["branches: [b1]"])
        before = self.text("TKT-1")
        self.a.edit("TKT-1", priority="High")
        self.assertIn("branches: [b1]", self.text("TKT-1"))
        self.assertNotEqual(before, self.text("TKT-1"))  # priority did land

    def test_missing_ticket(self):
        with self.assertRaises(NotFoundError):
            self.a.edit("TKT-404", add_branches=["b1"])


class Apply(_Base):

    DOC = ("---\ntype: Story\nbranches: [b1, o/r@b2]\nprs: [%s]\n---\n"
           "# New ticket\n" % PR1)

    def test_apply_new_writes_them(self):
        t = self.a.apply(self.DOC)
        self.assertEqual((t.branches, t.prs), (["b1", "o/r@b2"], [PR1]))

    def test_apply_new_omits_empty_lists(self):
        t = self.a.apply("---\ntype: Story\nbranches: []\n---\n# New\n")
        self.assertNotIn("branches", self.fm(t.key))
        self.assertNotIn("prs", self.fm(t.key))

    def test_apply_update_replaces_and_empties(self):
        self.write("TKT-1", ["branches: [old]", f"prs: [{PR1}]"])
        self.a.apply("---\ntype: Story\nbranches: [new]\nprs: []\n---\n# TKT-1\n",
                     key="TKT-1")
        fm = self.fm("TKT-1")
        self.assertEqual(fm["branches"], ["new"])
        self.assertNotIn("prs", fm)

    def test_apply_update_without_keys_keeps_them(self):
        self.write("TKT-1", ["branches: [b1]"])
        self.a.apply("---\ntype: Story\n---\n# TKT-1\n", key="TKT-1")
        self.assertEqual(self.fm("TKT-1")["branches"], ["b1"])


class Cli(_Base):

    def setUp(self):
        super().setUp()
        self.cfg = Path(self.tmp) / ".sdlc" / "config.toml"
        self.cfg.parent.mkdir(parents=True)
        self.cfg.write_text(
            '[ticketing]\nprovider = "markdown"\nproject = "TKT"\n'
            f'[markdown]\nboard_dir = "{self.a.board_dir}"\n'
            f'state_dir = "{Path(self.tmp) / "state"}"\nme = "raymond"\n'
            '[board.roles]\n' + "".join(f'{k} = "{v}"\n' for k, v in ROLES.items()),
            encoding="utf-8")
        self.write("TKT-1")

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--config", str(self.cfg), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_flags_add_and_remove(self):
        code, out, _ = self.run_cli(
            "edit", "TKT-1", "--add-branch", "b1", "--add-branch",
            "olddognewflex/tktban@feature/x", "--add-pr", PR1, "--json")
        self.assertEqual(code, 0)
        d = json.loads(out)
        self.assertEqual(d["branches"], ["b1", "olddognewflex/tktban@feature/x"])
        self.assertEqual(d["prs"], [PR1])
        code, out, _ = self.run_cli("edit", "TKT-1", "--remove-branch", "b1",
                                    "--remove-pr", PR1, "--json")
        self.assertEqual(code, 0)
        d = json.loads(out)
        self.assertEqual((d["branches"], d["prs"]),
                         (["olddognewflex/tktban@feature/x"], []))

    def test_bad_values_exit_64_and_write_nothing(self):
        before = self.text("TKT-1")
        for flag, val in (("--add-branch", ""), ("--add-branch", "a b"),
                          ("--add-branch", "a,b"), ("--add-branch", "[a]"),
                          ("--add-branch", "main@{1}"),
                          ("--add-branch", "repo@branch"),
                          ("--add-branch", "o/r@"), ("--add-branch", "/r@b"),
                          ("--add-pr", "github.com/o/r/pull/1"),
                          ("--add-pr", "https://"),
                          ("--add-pr", "https://x/pull/1,2"),
                          ("--add-pr", "https://x/pull 1")):
            with self.subTest(flag=flag, val=val):
                code, _, err = self.run_cli("edit", "TKT-1", flag, val)
                self.assertEqual(code, 64, err)
                self.assertIn(flag, err)
        self.assertEqual(self.text("TKT-1"), before)

    def test_remove_is_not_validated(self):
        self.write("TKT-1", ["branches: [odd@{entry}]"])
        code, _, err = self.run_cli("edit", "TKT-1", "--remove-branch", "odd@{entry}")
        self.assertEqual(code, 0, err)
        self.assertNotIn("branches", self.fm("TKT-1"))

    def test_human_view_lines(self):
        self.run_cli("edit", "TKT-1", "--add-branch", "b1", "--add-branch", "b2",
                     "--add-pr", PR1)
        code, out, _ = self.run_cli("view", "TKT-1")
        self.assertEqual(code, 0)
        lines = [ln.strip() for ln in out.splitlines()]
        self.assertIn("branches: b1, b2", lines)
        self.assertIn(f"prs:      {PR1}", lines)

    def test_human_view_silent_without_them(self):
        _, out, _ = self.run_cli("view", "TKT-1")
        self.assertNotIn("branches:", out)
        self.assertNotIn("prs:", out)


class Schema(unittest.TestCase):

    def test_json_shape_always_has_both(self):
        d = Ticket(key="TKT-1").to_dict()
        self.assertEqual((d["branches"], d["prs"]), ([], []))


if __name__ == "__main__":
    unittest.main()
