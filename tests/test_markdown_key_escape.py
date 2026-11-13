"""TKT-83: the markdown board refuses a ticket key that would name a file
outside the board folder (`../secret`, `a/b`, an absolute path, a NUL
byte, ...). Every verb builds its path through one helper, so every verb
rejects such a key with UsageError (exit 64) before any read or write.

Offline: the adapter is pointed at a temp board dir, with a planted
`secret.md` next to the board so an escaping key has a real file to hit.
Run from the repo root:

    python3 -m unittest tests.test_markdown_key_escape
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.markdown import MarkdownAdapter, _check_key, _key_ok  # noqa: E402
from core.config import Config  # noqa: E402
from core.errors import UsageError  # noqa: E402

ROLES = {"backlog": "Backlog", "todo": "To Do", "in_progress": "In Progress",
         "done": "Done"}

SECRET = "---\ntype: Story\nstatus: To Do\n---\n# not a board ticket\n"

# Each of these would resolve outside the board dir, or is not a key.
BAD_KEYS = [
    "../secret",            # the ticket's example: <board>/../secret.md
    "../../secret",
    "..\\secret",           # Windows separator
    "sub/TKT-1",            # a separator alone, even pointing inward
    "/tmp/secret",          # absolute
    "C:secret",             # Windows drive-relative
    "TKT-1:secret",         # NTFS alternate data stream on TKT-1.md
    "a:b",
    "TKT-1\0",              # NUL byte
    "..",
    "",
    "CON",                  # Windows device names: CON.md opens the console
    "nul",
    "COM1",
    "LPT9.v2",
]


def adapter(tmp, project="TKT"):
    cfg = Config({"ticketing": {"provider": "markdown", "project": project},
                  "markdown": {"board_dir": str(Path(tmp) / "board"),
                               "state_dir": str(Path(tmp) / "state"),
                               "me": "raymond"},
                  "board": {"roles": dict(ROLES)},
                  "queries": {"all": 'status = "To Do"'}},
                 Path(tmp) / ".sdlc" / "config.toml")
    return MarkdownAdapter(cfg)


def tree(root):
    """Every file under `root`, relative path -> bytes, and every directory,
    relative path -> None (so a stray mkdir shows up too)."""
    out = {}
    for dirpath, dirs, names in os.walk(root):
        for d in dirs:
            out[str((Path(dirpath) / d).relative_to(root)) + "/"] = None
        for name in names:
            p = Path(dirpath) / name
            out[str(p.relative_to(root))] = p.read_bytes()
    return out


class _Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.a = adapter(self.tmp)
        self.a.board_dir.mkdir(parents=True)
        (Path(self.tmp) / "secret.md").write_text(SECRET)
        for key in ("TKT-1", "TKT-2"):
            self.write(key)

    def write(self, key, fm_lines=()):
        text = "---\ntype: Story\nstatus: To Do\n" + "".join(
            f"{line}\n" for line in fm_lines) + f"---\n# {key}\n"
        (self.a.board_dir / f"{key}.md").write_text(text)

    def assertRejected(self, fn, *args, **kwargs):
        """`fn(*args)` raises UsageError and leaves every file under the temp
        dir -- board, state sidecars and the planted secret -- untouched."""
        before = tree(self.tmp)
        with self.assertRaises(UsageError):
            fn(*args, **kwargs)
        self.assertEqual(tree(self.tmp), before)


class Validator(unittest.TestCase):

    def test_colon_anywhere_is_refused(self):
        # `<board>/TKT-1:secret.md` is a hidden NTFS stream on TKT-1, not a
        # ticket file; `PureWindowsPath(...).drive` alone misses it.
        for key in ("TKT-1:secret", "a:b", "TKT-1:", ":x", "C:x"):
            with self.subTest(key=key):
                self.assertFalse(_key_ok(key))
                with self.assertRaises(UsageError) as cm:
                    _check_key(key)
                self.assertIn("':'", str(cm.exception))

    def test_plain_keys_pass(self):
        for key in ("TKT-1", "ODNF-3", "A.B-1", "my_proj-2", "CON-1"):
            with self.subTest(key=key):
                self.assertTrue(_key_ok(key))
                self.assertEqual(_check_key(key), key)


class LinkEscape(_Base):

    def test_link_blocks_to_parent_dir_writes_nothing(self):
        # The ticket's reproduction: this used to write
        # `blocked_by: [TKT-1]` into <board>/../secret.md.
        self.assertRejected(self.a.link, "TKT-1", "../secret", "blocks")
        self.assertEqual((Path(self.tmp) / "secret.md").read_text(), SECRET)

    def test_link_every_type_both_directions(self):
        for lt in ("blocks", "is blocked by", "child", "parent", "relates to"):
            for key, to in (("TKT-1", "../secret"), ("../secret", "TKT-1")):
                with self.subTest(link_type=lt, key=key, to=to):
                    self.assertRejected(self.a.link, key, to, lt)


class EveryVerbRejects(_Base):

    def test_bad_keys(self):
        verbs = {
            "view": lambda k: self.a.view(k),
            "edit": lambda k: self.a.edit(k, priority="High"),
            "comment": lambda k: self.a.comment(k, "hello"),
            "transition": lambda k: self.a.transition(k, "in_progress"),
            "blockers": lambda k: self.a.blockers(k),
            "apply": lambda k: self.a.apply("---\ntype: Story\n---\n# x\n", key=k),
            "worklog": lambda k: self.a.worklog(k, "todo"),
            "lane_time": lambda k: self.a.lane_time(k, "todo"),
            # What `tkt lane-time` actually calls, even for one key.
            "lane_time_batch": lambda k: self.a.lane_time_batch([(k, "todo")]),
            "artifact_body": lambda k: self.a.artifact_body(
                SimpleNamespace(key=k)),
        }
        for name, fn in verbs.items():
            for key in BAD_KEYS:
                with self.subTest(verb=name, key=key):
                    self.assertRejected(fn, key)

    def test_lane_time_batch_checks_every_key_before_writing(self):
        # A good key first: with time tracking on it would be logged
        # (a worklog.jsonl row) before the bad one was reached.
        self.a.config.timetracking = {"provider": "local"}
        self.a.transition("TKT-1", "in_progress")
        self.assertRejected(self.a.lane_time_batch,
                            [("TKT-1", "in_progress"), ("../secret", "todo")])

    def test_message_names_the_key(self):
        with self.assertRaises(UsageError) as cm:
            self.a.view("../secret")
        self.assertIn("../secret", str(cm.exception))

    def test_create_with_escaping_project(self):
        # `create --project` mints `<project>-<n>` as the key.
        self.assertRejected(self.a.create, "Story", "x", project="../evil")
        self.assertFalse((Path(self.tmp) / "evil-1.md").exists())

    def test_create_error_names_the_project_as_typed(self):
        with self.assertRaises(UsageError) as cm:
            self.a.create("Story", "x", project="../evil")
        self.assertIn("'../evil'", str(cm.exception))

    def test_create_makes_no_board_dir_when_rejected(self):
        # A board that does not exist yet: the rejected create must not
        # leave an empty board dir behind.
        shutil.rmtree(self.a.board_dir)
        self.assertRejected(self.a.create, "Story", "x", project="../evil")
        self.assertFalse(self.a.board_dir.exists())

    def test_apply_new_makes_no_board_dir_when_rejected(self):
        shutil.rmtree(self.a.board_dir)
        a = adapter(self.tmp, project="../evil")
        self.assertRejected(a.apply, "---\ntype: Story\n---\n# x\n")
        self.assertFalse(a.board_dir.exists())

    def test_device_like_names_inside_a_key_still_work(self):
        # Only the whole name before the first dot is reserved.
        for key in ("CON-1", "NULL-2", "COM10-3", "AUXX-4"):
            with self.subTest(key=key):
                self.write(key)
                self.assertEqual(self.a.view(key).key, key)

    def test_apply_new_with_escaping_configured_project(self):
        a = adapter(self.tmp, project="../evil")
        self.assertRejected(a.apply, "---\ntype: Story\n---\n# x\n")


class ValidKeysStillWork(_Base):

    def test_other_project_prefix_on_shared_board(self):
        self.write("ODNF-3")
        self.assertEqual(self.a.view("ODNF-3").key, "ODNF-3")
        self.a.link("TKT-1", "ODNF-3", "blocks")
        self.assertEqual(self.a.view("ODNF-3").blocked_by[0]["key"], "TKT-1")

    def test_dots_and_underscores_inside_a_key(self):
        for key in ("A.B-1", "my_proj-2", "TKT-1.v2"):
            with self.subTest(key=key):
                self.write(key)
                self.a.comment(key, "ok")
                self.assertEqual(self.a.view(key).key, key)

    def test_list_skips_a_board_file_whose_name_is_not_a_key(self):
        # `notes...md` has stem `notes..`, which no verb could open now;
        # listing the board must skip it rather than fail on it.
        (self.a.board_dir / "notes...md").write_text("# stray\n")
        keys = [t.key for t in self.a._all_tickets(all_projects=True)]
        self.assertEqual(keys, ["TKT-1", "TKT-2"])

    def test_create_and_verbs_on_a_normal_key(self):
        t = self.a.create("Story", "fresh")
        self.assertEqual(t.key, "TKT-3")
        self.a.transition(t.key, "in_progress")
        self.a.edit(t.key, priority="High")
        self.assertEqual(self.a.view(t.key).status, "In Progress")


class EscapingKeyInFrontmatter(_Base):
    """A key a hand edit left in another ticket's frontmatter is data, not a
    command: viewing that ticket must not fail, and must not read outside
    the board to judge it."""

    def test_blocked_by_escape_is_unresolved_not_an_error(self):
        # The planted secret is Done: if it were read, the blocker would
        # wrongly count as resolved.
        (Path(self.tmp) / "secret.md").write_text(
            "---\ntype: Story\nstatus: Done\n---\n# s\n")
        self.write("TKT-5", ["blocked_by: [../secret]"])
        t = self.a.view("TKT-5")
        self.assertEqual(t.blocked_by, [{"key": "../secret", "resolved": False}])
        self.assertEqual([b["key"] for b in self.a.blockers("TKT-5")],
                         ["../secret"])

    def test_hierarchy_escape_still_views(self):
        self.write("TKT-5", ["parent: ../secret", "children: [../secret, TKT-1]"])
        t = self.a.view("TKT-5")
        self.assertEqual(t.parent, "../secret")
        self.assertEqual(t.children, ["../secret", "TKT-1"])

    def test_list_with_escape_in_frontmatter(self):
        self.write("TKT-5", ["blocked_by: [../secret]", "parent: ../x"])
        self.assertIn("TKT-5", [t.key for t in self.a._all_tickets()])


REPO = Path(__file__).resolve().parent.parent


class KeyEscapeCli(unittest.TestCase):
    """Through `core/cli.py` as a subprocess: exit 64, error on stderr, no
    file written anywhere."""

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
        (self.tmp / ".sdlc" / "board" / "TKT-1.md").write_text(
            "---\ntype: Story\nstatus: To Do\n---\n# TKT-1\n")
        (self.tmp / ".sdlc" / "secret.md").write_text(SECRET)

    def tkt(self, *args):
        env = {k: v for k, v in os.environ.items() if k != "TKT_CONFIG"}
        return subprocess.run([sys.executable, str(REPO / "tkt"), *args],
                              cwd=self.tmp, env=env, capture_output=True,
                              text=True, timeout=60)

    def test_cli_exits_64_and_writes_nothing(self):
        cases = [
            ("link", "TKT-1", "--to", "../secret", "--type", "blocks"),
            ("view", "../secret"),
            ("edit", "../secret", "--priority", "High"),
            ("comment", "../secret", "hi"),
            ("transition", "../secret", "in_progress"),
            ("lane-time", "../secret", "--role", "todo"),
            ("comment", "TKT-1:secret", "hi"),
            ("link", "TKT-1", "--to", "TKT-1:secret", "--type", "blocks"),
        ]
        for args in cases:
            with self.subTest(args=args):
                before = tree(self.tmp)
                r = self.tkt(*args)
                self.assertEqual(r.returncode, 64, r.stderr)
                self.assertIn(args[1] if args[0] != "link" else args[3],
                              r.stderr)
                self.assertEqual(tree(self.tmp), before)


if __name__ == "__main__":
    unittest.main()
