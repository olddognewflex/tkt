"""TKT-104: `tkt init` inherits a parent dir's shared markdown board, derives
the ticket key from the repo dir, and `tkt doctor` warns on example
placeholders. TKT-26: it fills `[vcs].repo` from the git origin remote.
Temp dirs only; stdin is never read. Offline.

    python3 -m unittest tests.test_scaffold_init
"""
import builtins
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.markdown import MarkdownAdapter  # noqa: E402
from core import cli, scaffold  # noqa: E402
from core.config import Config  # noqa: E402
from core.errors import ConfigError, UsageError  # noqa: E402

PARENT = """\
# Root VIEW config, shared by every repo below.
[ticketing]
provider = "markdown"
project  = "INBOX"            # prefix for stray tickets

[markdown]
board_dir = "{board}"   # SHARED
state_dir = "{state}"   # SHARED history
me        = "raymond"

[board.roles]
backlog     = "Backlog"
todo        = "To Do"
in_progress = "In Progress"
review      = "In Review"
done        = "Done"
blocked     = "Blocked"
archived    = "Archived"

[issue_types]
full_sdlc   = ["Story", "Bug", "Sub-task"]
deliverable = ["Task", "Chore", "Spike", "Epic"]

[queries]
all   = 'ORDER BY key ASC'
tier1 = 'status = "To Do" ORDER BY priority DESC'

[timetracking]
provider = "local"

[vcs]
provider = "github"
repo     = ""

[deploy]
staging_workflow = "deploy.yml"

[build]
build = "true"
"""

EXAMPLE = tomllib.loads((scaffold.PACK_ROOT / "examples"
                         / "config.markdown.toml").read_text())


def no_input(*_a, **_k):
    raise AssertionError("init read stdin")


class Base(unittest.TestCase):
    real_git = False        # True: init may run git against the temp dir

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.child = self.root / "ai-map-reference-platform"
        self.child.mkdir()
        self.err = io.StringIO()
        patchers = [mock.patch.object(builtins, "input", no_input)]
        if not self.real_git:
            patchers.append(mock.patch.object(scaffold, "_origin_url",
                                              return_value=(None, "not a git repo")))
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def parent(self, board=None, state=None, text=None):
        sdlc = self.root / ".sdlc"
        (sdlc / "board").mkdir(parents=True, exist_ok=True)
        if text is None:
            text = PARENT.format(board=board or sdlc / "board",
                                 state=state or sdlc / "state")
        (sdlc / "config.toml").write_text(text)
        return sdlc

    def init(self, **kw):
        args = dict(provider="markdown", target_dir=str(self.child), force=False,
                    link_skills=False, sample=False, interactive=False,
                    detect_build=False, stop=self.root)
        args.update(kw)
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(self.err):
            rc = scaffold.init(**args)
        self.assertEqual(rc, 0)
        self.out = out.getvalue()
        return self.cfg(Path(args["target_dir"]))

    def init_raises(self, exc, **kw):
        """Run init expecting `exc`; keeps stdout in self.out."""
        args = dict(provider="markdown", target_dir=str(self.child), force=False,
                    link_skills=False, sample=False, interactive=False,
                    detect_build=False, stop=self.root)
        args.update(kw)
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(self.err), self.assertRaises(exc) as cm:
            scaffold.init(**args)
        self.out = out.getvalue()
        return cm.exception

    def symlink(self, src, dst):
        try:
            os.symlink(src, dst, target_is_directory=True)
        except (OSError, NotImplementedError) as e:
            self.skipTest(f"symlinks unsupported: {e}")

    def cfg(self, target=None):
        target = target or self.child
        return tomllib.loads((target / ".sdlc" / "config.toml").read_text())

    def adapter(self):
        return MarkdownAdapter(Config.load(str(self.child / ".sdlc" / "config.toml")))


class DeriveKey(unittest.TestCase):
    def test_table(self):
        for name, key in [("tkt", "TKT"), ("ai-map-reference-platform", "AMRP"),
                          ("tktban", "TKTBAN"), ("OldDogNewFlex.com", "ODNFC"),
                          ("ai-lab", "AILAB"), ("3d-printer", "DPRINTER"),
                          ("supercalifragilistic", "SUPERCALIF"), ("---", "PROJ"),
                          ("123", "PROJ")]:
            self.assertEqual(scaffold.derive_key(name), key, name)

    def test_validate(self):
        for bad in ("a-b", "1X", "", "A B"):
            with self.assertRaises(UsageError):
                scaffold._validate_key(bad)
        self.assertEqual(scaffold._validate_key("Ab_1"), "Ab_1")


class Inherit(Base):
    def test_absolute_parent_shape(self):
        sdlc = self.parent()
        d = self.init()
        self.assertEqual(d["markdown"]["board_dir"], str(sdlc / "board"))
        self.assertEqual(d["markdown"]["state_dir"], str(sdlc / "state"))
        self.assertEqual(d["ticketing"]["project"], "AMRP")
        self.assertIn("archived", d["board"]["roles"])
        pdata = tomllib.loads((sdlc / "config.toml").read_text())
        self.assertEqual(d["issue_types"], pdata["issue_types"])
        self.assertEqual(d["queries"], pdata["queries"])
        self.assertEqual(d["vcs"]["repo"], EXAMPLE["vcs"]["repo"])
        self.assertEqual(d["build"], EXAMPLE["build"])
        self.assertNotIn("deploy", d)
        self.assertNotIn("models", d)
        text = (self.child / ".sdlc" / "config.toml").read_text()
        self.assertIn("# SHARED history", text)            # line copied verbatim
        self.assertNotIn("stray tickets", text)            # parent's key comment
        self.assertNotIn("Root VIEW", text)
        self.assertIn("inheriting", self.out)
        self.assertEqual(self.adapter().board_dir, sdlc / "board")

    def test_relative_parent_paths_resolve_to_same_board(self):
        sdlc = self.parent(board=".sdlc/board", state=".sdlc/state")
        d = self.init()
        self.assertEqual(d["markdown"]["board_dir"], os.path.join("..", ".sdlc", "board"))
        self.assertEqual(self.adapter().board_dir.resolve(), (sdlc / "board").resolve())
        self.assertEqual(self.adapter().state_dir.resolve(), (sdlc / "state").resolve())

    def test_relative_run_state_dir_rebased(self):
        sdlc = self.parent(text=PARENT.format(board="b", state="s")
                           + '\n[run]\nstate_dir = ".sdlc/state"\n')
        d = self.init()
        self.assertEqual((self.child / d["run"]["state_dir"]).resolve(),
                         (sdlc / "state").resolve())

    def test_omitted_parent_paths_resolve_to_same_board(self):
        text = PARENT.replace('board_dir = "{board}"   # SHARED\n', "") \
                     .replace('state_dir = "{state}"   # SHARED history\n', "")
        sdlc = self.parent(text=text)
        self.init()
        self.assertEqual(self.adapter().board_dir.resolve(), (sdlc / "board").resolve())
        self.assertEqual(self.adapter().state_dir.resolve(), (sdlc / "state").resolve())

    def test_tilde_parent_path_copied_verbatim(self):
        self.parent(board="~/shared/board", state="~/shared/state")
        d = self.init()
        self.assertEqual(d["markdown"]["board_dir"], "~/shared/board")

    def test_non_markdown_parent_ignored(self):
        self.parent(text='[ticketing]\nprovider = "jira"\nproject = "X"\n')
        d = self.init()
        self.assertEqual(d["markdown"]["board_dir"], ".sdlc/board")
        self.assertEqual(d["ticketing"]["project"], "AMRP")

    def test_invalid_parent_toml_warns(self):
        self.parent(text="[ticketing\nprovider = \n")
        d = self.init()
        self.assertEqual(d["markdown"]["board_dir"], ".sdlc/board")
        self.assertIn("not inheriting", self.err.getvalue())

    def test_project_and_board_dir_override(self):
        self.parent()
        d = self.init(project="XYZ", board_dir="/abs/board")
        self.assertEqual(d["ticketing"]["project"], "XYZ")
        self.assertEqual(d["markdown"]["board_dir"], "/abs/board")
        self.assertEqual(d["markdown"]["state_dir"], str(Path("/abs/state")))
        self.assertIn("archived", d["board"]["roles"])     # still inherits the model

    def test_bad_project_rejected_before_writing(self):
        for bad in ("a-b", "1X"):
            with self.assertRaises(UsageError):
                self.init(project=bad)
        self.assertFalse((self.child / ".sdlc").exists())

    def test_no_inherit_uses_example(self):
        self.parent()
        d = self.init(inherit=False)
        self.assertEqual(d["markdown"]["board_dir"], ".sdlc/board")
        self.assertEqual(d["ticketing"]["project"], "AMRP")
        self.assertEqual(d["board"]["roles"], EXAMPLE["board"]["roles"])

    def test_no_parent_uses_example(self):
        d = self.init()
        self.assertEqual(d["markdown"], {**EXAMPLE["markdown"]})
        self.assertEqual(d["ticketing"]["project"], "AMRP")

    def test_stop_bounds_the_walk(self):
        self.parent()
        inner = self.child / "pkg"
        inner.mkdir()
        cfg = self.init(target_dir=str(inner), stop=self.child)
        self.assertEqual(cfg["markdown"]["board_dir"], ".sdlc/board")

    def test_board_dir_rejected_for_other_providers(self):
        with self.assertRaises(UsageError):
            self.init(provider="jira", board_dir="/b")

    def test_explicit_project_for_other_provider(self):
        d = self.init(provider="jira", project="ABC")
        self.assertEqual(d["ticketing"]["project"], "ABC")

    def test_existing_config_needs_force(self):
        self.init()
        with self.assertRaises(ConfigError):
            self.init()


class Collision(Base):
    def test_warns_when_key_taken_on_shared_board(self):
        sdlc = self.parent()
        (sdlc / "board" / "AMRP-1.md").write_text("x")
        (sdlc / "board" / "AMRPX-2.md").write_text("x")
        d = self.init()
        self.assertEqual(d["ticketing"]["project"], "AMRP")      # still written
        self.assertIn("1 ticket(s) keyed AMRP", self.err.getvalue())

    def test_no_warning_on_private_board(self):
        board = self.child / ".sdlc" / "board"
        board.mkdir(parents=True)
        (board / "AMRP-1.md").write_text("x")
        self.init()
        self.assertNotIn("warning", self.err.getvalue())

    def test_no_warning_on_force_reinit_same_key(self):
        sdlc = self.parent()
        self.init()
        (sdlc / "board" / "AMRP-1.md").write_text("x")
        self.init(force=True)
        self.assertNotIn("keyed", self.err.getvalue())


class Sample(Base):
    def test_private_board_gets_key_named_starter(self):
        self.init(sample=True, project="ABC")
        self.assertTrue((self.child / ".sdlc" / "board" / "ABC-1.md").is_file())
        self.assertFalse((self.child / ".sdlc" / "board" / "TKT-1.md").exists())

    def test_sample_follows_board_dir(self):
        self.init(sample=True, board_dir="tickets")
        self.assertTrue((self.child / "tickets" / "AMRP-1.md").is_file())

    def test_shared_board_skips_sample(self):
        sdlc = self.parent()
        self.init(sample=True)
        self.assertEqual(list((sdlc / "board").iterdir()), [])
        self.assertIn("skipped --sample", self.out)


class Prompts(Base):
    def test_tty_defaults_and_decline(self):
        self.parent()
        answers = iter(["", "n"])
        with mock.patch.object(sys.stdin, "isatty", return_value=True), \
                mock.patch.object(builtins, "input", lambda _p="": next(answers)):
            d = self.init(interactive=True)
        self.assertEqual(d["ticketing"]["project"], "AMRP")
        self.assertEqual(d["markdown"]["board_dir"], ".sdlc/board")

    def test_tty_eof_takes_defaults(self):
        sdlc = self.parent()

        def eof(_p=""):
            raise EOFError
        with mock.patch.object(sys.stdin, "isatty", return_value=True), \
                mock.patch.object(builtins, "input", eof):
            d = self.init(interactive=True)
        self.assertEqual(d["markdown"]["board_dir"], str(sdlc / "board"))

    def test_cli_non_tty_never_reads_stdin(self):
        self.parent()
        with mock.patch.object(sys.stdin, "isatty", return_value=False), \
                mock.patch.object(scaffold, "_find_parent_config",
                                  lambda t, s=None: None), \
                contextlib.redirect_stdout(io.StringIO()):
            rc = cli.main(["init", "--provider", "markdown", "--dir", str(self.child),
                           "--project", "CLI", "--board-dir", "b", "--no-detect-build"])
        self.assertEqual(rc, 0)
        d = self.cfg()
        self.assertEqual((d["ticketing"]["project"], d["markdown"]["board_dir"],
                          d["markdown"]["state_dir"]), ("CLI", "b", "state"))


class Doctor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / ".sdlc" / "board").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, project="TKT", repo="your-org/your-repo"):
        p = self.root / ".sdlc" / "config.toml"
        p.write_text(f'[ticketing]\nprovider = "markdown"\nproject = "{project}"\n'
                     '[markdown]\nme = "me"\n[board.roles]\ndone = "Done"\n'
                     f'[vcs]\nrepo = "{repo}"\n')
        return Config.load(str(p))

    def test_placeholder_key_and_repo_warn(self):
        c = scaffold.doctor_check(self.config())
        self.assertTrue(c.ok)
        self.assertIn("TKT", c.detail)
        self.assertIn("your-org/your-repo", c.detail)

    def test_owner_repo_placeholder(self):
        c = scaffold.doctor_check(self.config(project="ABC", repo="owner/repo"))
        self.assertIn("owner/repo", c.detail)
        self.assertNotIn("TKT", c.detail)

    def test_clean_and_empty_repo(self):
        self.assertIsNone(scaffold.doctor_check(self.config("ABC", "")))
        self.assertIsNone(scaffold.doctor_check(self.config("ABC", "me/real")))

    def test_skipped_in_tkt_checkout(self):
        (self.root / "core").mkdir()
        (self.root / "core" / "scaffold.py").write_text("")
        (self.root / "examples").mkdir()
        (self.root / "examples" / "config.markdown.toml").write_text("")
        self.assertIsNone(scaffold.doctor_check(self.config()))

    def test_cli_doctor_exits_zero_with_row(self):
        cfg = self.config()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.main(["--config", str(cfg.path), "doctor"])
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("[ok ] config placeholders", out.getvalue())


# --- QA Author — adversarial tests ------------------------------------------
# Break-It dimensions covered: partial failure (failed write/replace, failed
# render under --force), file modes/umask, input validation per provider,
# encoding (CRLF, missing trailing newline), symlinked/aliased paths, nested
# dirs, non-string config values, CLI flag mapping.

# Parents whose text-level surgery lands on a header-looking line inside a
# ''' string (tomledit does not track multi-line strings), so the re-parse
# guard is what stops a wrong config being written.
PROJECT_TRAP = """\
note = '''
[ticketing]
'''

[ticketing]
provider = "markdown"
project  = "INBOX"

[markdown]
board_dir = "{board}"
state_dir = "{state}"
"""

STATE_TRAP = """\
[ticketing]
provider = "markdown"
project  = "INBOX"

[queries]
odd = '''
[markdown]
'''

[markdown]
board_dir = "{board}"
state_dir = "state"
"""


class WriteAtomic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "config.toml"
        self.addCleanup(self.tmp.cleanup)

    def with_umask(self, mask):
        old = os.umask(mask)
        self.addCleanup(os.umask, old)

    def test_failed_replace_leaves_no_temp_and_no_target(self):
        with mock.patch.object(scaffold.os, "replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                scaffold._write_atomic(self.path, "x = 1\n")
        self.assertEqual(os.listdir(self.dir), [])

    def test_failed_replace_keeps_existing_bytes(self):
        self.path.write_bytes(b"old\r\n")
        with mock.patch.object(scaffold.os, "replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                scaffold._write_atomic(self.path, "new\n")
        self.assertEqual(os.listdir(self.dir), ["config.toml"])
        self.assertEqual(self.path.read_bytes(), b"old\r\n")

    def test_new_file_gets_umask_mode_and_umask_is_restored(self):
        for mask in (0o022, 0o027, 0o077):
            with self.subTest(umask=oct(mask)):
                self.with_umask(mask)
                self.path.unlink(missing_ok=True)
                scaffold._write_atomic(self.path, "x = 1\n")
                self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o666 & ~mask)
                self.assertEqual(os.umask(mask), mask)      # still ours afterwards
                self.assertEqual(self.path.read_text(), "x = 1\n")

    def test_existing_mode_preserved(self):
        self.with_umask(0o022)
        for mode in (0o600, 0o640, 0o664):
            with self.subTest(mode=oct(mode)):
                self.path.write_text("old\n")
                os.chmod(self.path, mode)
                scaffold._write_atomic(self.path, "new\n")
                self.assertEqual(os.stat(self.path).st_mode & 0o777, mode)
                self.assertEqual(self.path.read_text(), "new\n")


class ProjectPerProvider(Base):
    def line(self, prefix):
        text = (self.child / ".sdlc" / "config.toml").read_text()
        return next(ln for ln in text.splitlines() if ln.startswith(prefix))

    def test_accepted_values_land_in_provider_field_and_keep_comment(self):
        for provider, value, table, key, comment in [
                ("linear", "OPS", "linear", "team_key", "# the team whose board"),
                ("openkanban", "My Board 2", "openkanban", "project", "# project name or id"),
                ("jira", "ABC_1", "ticketing", "project", None),
                ("markdown", "XYZ", "ticketing", "project", None)]:
            with self.subTest(provider=provider):
                d = self.init(provider=provider, project=value, force=True)
                self.assertEqual(d[table][key], value)
                if provider == "linear":
                    self.assertNotIn("project", d["ticketing"])
                if comment:
                    self.assertIn(comment, self.line(f"{key} "))

    def test_rejected_values_write_nothing(self):
        for provider, value in [("linear", "a-b"), ("linear", "1X"),
                                ("openkanban", "   "), ("openkanban", ""),
                                ("github", "ABC"), ("github", "a-b")]:
            with self.subTest(provider=provider, value=value):
                self.init_raises(UsageError, provider=provider, project=value)
                self.assertFalse((self.child / ".sdlc").exists())


class RealpathShared(Base):
    def test_aliased_path_into_repo_is_private(self):
        alias_home = tempfile.TemporaryDirectory()
        self.addCleanup(alias_home.cleanup)
        alias = Path(alias_home.name) / "alias"
        self.symlink(self.root, alias)
        board = alias / self.child.name / "tickets"      # == child/tickets, via alias
        self.init(sample=True, board_dir=str(board))
        self.assertTrue((self.child / "tickets" / "AMRP-1.md").is_file())
        self.assertNotIn("skipped --sample", self.out)

    def test_symlink_in_repo_to_outside_board_is_shared(self):
        outside = self.root / "elsewhere"
        outside.mkdir()
        (outside / "AMRP-7.md").write_text("x")
        (self.child / ".sdlc").mkdir()
        self.symlink(outside, self.child / ".sdlc" / "board")
        self.init(sample=True)
        self.assertIn("skipped --sample", self.out)
        self.assertEqual(sorted(os.listdir(outside)), ["AMRP-7.md"])
        self.assertIn("1 ticket(s) keyed AMRP", self.err.getvalue())


class ForceCollision(Base):
    def test_force_warns_unless_same_key_on_same_board(self):
        for name, first, warns in [
                ("same key, private -> shared", dict(inherit=False), True),
                ("other key, same shared board", dict(project="OLD"), True),
                ("same key, same shared board", {}, False)]:
            with self.subTest(name):
                self.err = io.StringIO()
                sdlc = self.parent()
                (sdlc / "board" / "AMRP-1.md").write_text("x")
                self.init(force=True, **first)
                self.err = io.StringIO()
                self.init(force=True)
                self.assertEqual("keyed AMRP" in self.err.getvalue(), warns,
                                 self.err.getvalue())


class RenderFailure(Base):
    def test_mismatch_raises_names_no_inherit_and_writes_nothing(self):
        for name, trap, what in [("project", PROJECT_TRAP, "INBOX"),
                                 ("state_dir", STATE_TRAP, "state")]:
            with self.subTest(name):
                sdlc = self.parent(text=trap.format(board=self.root / ".sdlc" / "board",
                                                    state=self.root / ".sdlc" / "state"))
                e = self.init_raises(ConfigError)
                self.assertIn("expected", str(e))
                self.assertIn(what, str(e))
                self.assertIn("--no-inherit", str(e))
                self.assertIn("left untouched", str(e))
                self.assertFalse((self.child / ".sdlc" / "config.toml").exists())
                self.assertNotIn("wrote", self.out)
                (sdlc / "config.toml").unlink()

    def test_existing_config_byte_identical_under_force(self):
        self.parent(text=STATE_TRAP.format(board=self.root / ".sdlc" / "board"))
        sdlc = self.child / ".sdlc"
        sdlc.mkdir()
        old = b'# hand-tuned\r\n[ticketing]\r\nprovider = "markdown"\r\nproject = "MINE"\r\n'
        (sdlc / "config.toml").write_bytes(old)
        os.chmod(sdlc / "config.toml", 0o600)
        self.init_raises(ConfigError, force=True, sample=True)
        self.assertEqual((sdlc / "config.toml").read_bytes(), old)
        self.assertEqual(os.stat(sdlc / "config.toml").st_mode & 0o777, 0o600)
        self.assertEqual(os.listdir(sdlc), ["config.toml"])   # no temp, no board

    def test_no_inherit_is_a_working_retry(self):
        self.parent(text=STATE_TRAP.format(board=self.root / ".sdlc" / "board"))
        self.init_raises(ConfigError)
        d = self.init(inherit=False)
        self.assertEqual(d["markdown"]["state_dir"], ".sdlc/state")


class CliNoInherit(Base):
    def run_cli(self, target, *extra):
        real = scaffold._find_parent_config
        with mock.patch.object(sys.stdin, "isatty", return_value=False), \
                mock.patch.object(scaffold, "_find_parent_config",
                                  lambda t, s=None: real(t, self.root)), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            rc = cli.main(["init", "--provider", "markdown", "--dir", str(target),
                           "--no-detect-build", *extra])
        self.assertEqual(rc, 0)
        return self.cfg(target)

    def test_flag_gives_private_board_despite_parent(self):
        sdlc = self.parent()
        other = self.root / "other"
        other.mkdir()
        self.assertEqual(self.run_cli(other)["markdown"]["board_dir"],
                         str(sdlc / "board"))                 # control: parent seen
        d = self.run_cli(self.child, "--no-inherit")
        self.assertEqual(d["markdown"]["board_dir"], ".sdlc/board")
        self.assertEqual(d["board"]["roles"], EXAMPLE["board"]["roles"])


class SeedBuild(Base):
    def go_repo(self):
        (self.child / "go.mod").write_text("module x\n")
        (self.child / "Makefile").write_text("lint:\n\tgofmt -l .\n")

    def test_detected_commands_written(self):
        want = {"build": "go build ./...", "test": "go test ./...",
                "typecheck": "go vet ./...", "lint": "make lint"}
        self.go_repo()
        for inherit in (True, False):
            with self.subTest(inherit=inherit):
                self.parent()
                d = self.init(detect_build=True, inherit=inherit, force=True)
                self.assertEqual(d["build"], want)
                self.assertIn("go + make", self.out)

    def test_no_build_notes_when_write_fails(self):
        self.go_repo()
        with mock.patch.object(scaffold, "_write_atomic", side_effect=OSError("disk full")):
            self.init_raises(OSError, detect_build=True)
        self.assertNotIn("[build]", self.out)
        self.assertNotIn("wrote", self.out)

    def test_no_build_notes_when_validation_fails(self):
        self.go_repo()
        self.parent(text=STATE_TRAP.format(board=self.root / ".sdlc" / "board"))
        self.init_raises(ConfigError, detect_build=True)
        self.assertNotIn("[build]", self.out)


class Rebase(Base):
    def test_nested_child_resolves_to_parent_board(self):
        sdlc = self.parent(board=".sdlc/board", state=".sdlc/state")
        deep = self.root / "a" / "b" / "repo"
        deep.mkdir(parents=True)
        d = self.init(target_dir=str(deep))
        self.assertEqual(d["markdown"]["board_dir"],
                         os.path.join("..", "..", "..", ".sdlc", "board"))
        a = MarkdownAdapter(Config.load(str(deep / ".sdlc" / "config.toml")))
        self.assertEqual(a.board_dir.resolve(), (sdlc / "board").resolve())
        self.assertEqual(a.state_dir.resolve(), (sdlc / "state").resolve())

    def test_crlf_and_unterminated_parents(self):
        rel = PARENT.format(board=".sdlc/board", state=".sdlc/state")
        md_last = ('[ticketing]\nprovider = "markdown"\nproject = "INBOX"\n\n'
                   '[board.roles]\ndone = "Done"\n\n[markdown]\nme = "r"')
        for name, text in [("crlf", rel.replace("\n", "\r\n")),
                           ("no trailing newline", rel.rstrip("\n")),
                           ("crlf, no trailing newline", rel.rstrip("\n").replace("\n", "\r\n")),
                           ("[markdown] last, unterminated", md_last)]:
            with self.subTest(name):
                sdlc = self.parent(text=text)
                d = self.init(force=True)
                self.assertEqual(d["ticketing"]["project"], "AMRP")
                self.assertEqual(d["vcs"]["repo"], EXAMPLE["vcs"]["repo"])
                self.assertEqual(self.adapter().board_dir.resolve(),
                                 (sdlc / "board").resolve())
                self.assertEqual(self.adapter().state_dir.resolve(),
                                 (sdlc / "state").resolve())

    def test_absolute_run_state_dir_line_copied_verbatim(self):
        line = 'state_dir = "/srv/tkt-runs"   # shared run state\n'
        self.parent(text=PARENT.format(board="/abs/board", state="/abs/state")
                    + "\n[run]\n" + line)
        d = self.init()
        self.assertEqual(d["run"]["state_dir"], "/srv/tkt-runs")
        self.assertIn(line, (self.child / ".sdlc" / "config.toml").read_text())


class DoctorEdges(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / ".sdlc").mkdir()

    def check(self, body, provider="markdown", top=""):
        p = self.root / ".sdlc" / "config.toml"
        p.write_text(top + f'[ticketing]\nprovider = "{provider}"\n' + body)
        return scaffold.doctor_check(Config.load(str(p)))

    def test_odd_shapes_do_not_crash_or_false_flag(self):
        for name, provider, body, top in [
                ("repo is a list", "markdown", 'project = "ABC"\n[vcs]\nrepo = ["your-org/your-repo"]\n', ""),
                ("repo is an int", "markdown", 'project = "ABC"\n[vcs]\nrepo = 3\n', ""),
                ("vcs is a string", "markdown", 'project = "ABC"\n', 'vcs = "your-org/your-repo"\n'),
                ("no [vcs]", "markdown", 'project = "ABC"\n', ""),
                ("unknown provider", "nosuch", 'project = "TKT"\n', ""),
                ("github: empty project matches example's empty", "github",
                 'project = ""\n[vcs]\nrepo = ""\n', "")]:
            with self.subTest(name):
                self.assertIsNone(self.check(body, provider, top))

    def test_no_vcs_still_flags_key(self):
        c = self.check('project = "TKT"\n')
        self.assertTrue(c.ok)
        self.assertIn('"TKT"', c.detail)
        self.assertNotIn("[vcs]", c.detail)

    def test_provider_project_key_checked_where_it_lives(self):
        # linear/openkanban keep their project identity outside [ticketing]
        for provider, table, key in [("linear", "linear", "team_key"),
                                     ("openkanban", "openkanban", "project")]:
            ex = tomllib.loads((scaffold.PACK_ROOT / "examples" /
                                f"config.{provider}.toml").read_text())
            placeholder = ex[table][key]
            with self.subTest(provider, value="placeholder"):
                c = self.check(f'[{table}]\n{key} = "{placeholder}"\n', provider)
                self.assertIsNotNone(c)
                self.assertTrue(c.ok)
                self.assertIn(f'[{table}].{key} = "{placeholder}"', c.detail)
            with self.subTest(provider, value="changed"):
                self.assertIsNone(self.check(f'[{table}]\n{key} = "REAL"\n', provider))

    def test_empty_repo_not_flagged_alongside_key(self):
        c = self.check('project = "TKT"\n[vcs]\nrepo = ""\n')
        self.assertIn('"TKT"', c.detail)
        self.assertNotIn("repo", c.detail)


PLACEHOLDER = EXAMPLE["vcs"]["repo"]
TOKEN_URL = "https://bot:ghp_SECRET@gith\uff03ub.com/o/r"   # NFKC-invalid netloc


class ParseRemote(unittest.TestCase):
    def test_accepted_forms(self):
        for url, want in [
                ("git@github.com:owner/repo.git", "owner/repo"),
                ("git@github.com:owner/repo", "owner/repo"),
                ("git@github-work:owner/repo.git", "owner/repo"),
                ("github-work:owner/repo.git", "owner/repo"),
                ("git@github.com:/owner/repo.git", "owner/repo"),
                ("ssh://git@github.com/owner/repo.git", "owner/repo"),
                ("ssh://git@github.com:22/owner/repo.git", "owner/repo"),
                ("ssh://git@github-work/owner/repo", "owner/repo"),
                ("https://github.com/owner/repo.git", "owner/repo"),
                ("https://github.com/owner/repo", "owner/repo"),
                ("https://github.com/owner/repo/", "owner/repo"),
                ("https://github.com/owner/repo.GIT", "owner/repo"),
                ("http://github.com/owner/repo.git", "owner/repo"),
                ("HTTPS://github.com/owner/repo.git", "owner/repo"),
                ("https://user:tok@ghe.example.com:8443/owner/repo.git", "owner/repo"),
                ("git://github.com/owner/repo.git", "owner/repo"),
                ("  https://github.com/Owner-1/my.repo_x.git\n", "Owner-1/my.repo_x"),
                ("git@github.com:owner/repo.git.git", "owner/repo.git"),
                ("git@gitlab-mirror:owner/repo.git", "owner/repo")]:
            with self.subTest(url=url):
                self.assertEqual(scaffold.parse_remote(url), want)

    def test_rejected_forms(self):
        for url in ["", "   ", "/srv/git/owner/repo.git", "../repo",
                    "file:///srv/owner/repo.git", "C:/work/owner/repo",
                    "C:\\work\\owner\\repo", "git@gitlab.com:group/sub/repo.git",
                    "https://github.com/owner", "https://github.com/",
                    "https://github.com/owner/repo/tree/main",
                    "git@github.com:owner/.git", "git@github.com:owner/re po.git",
                    "https://github.com/../repo", "https:///owner/repo",
                    "ftp://github.com/owner/repo.git", "git@github.com:owner",
                    "owner/repo", "https://[::1/owner/repo.git",
                    "ssh://git@[github.com/owner/repo", TOKEN_URL,
                    "git@github.com:-owner/repo.git"]:
            with self.subTest(url=url):
                self.assertIsNone(scaffold.parse_remote(url))

    def test_non_github_hosts_rejected(self):
        for url, host in [
                ("git@gitlab.com:group/repo.git", "gitlab.com"),
                ("https://gitlab.com/group/repo.git", "gitlab.com"),
                ("ssh://git@altssh.gitlab.com:443/group/repo.git", "altssh.gitlab.com"),
                ("git@bitbucket.org:group/repo.git", "bitbucket.org"),
                ("https://codeberg.org/group/repo.git", "codeberg.org"),
                ("https://GitLab.com/group/repo", "gitlab.com")]:
            with self.subTest(url=url):
                self.assertIsNone(scaffold.parse_remote(url))
                self.assertEqual(scaffold._split_remote(url), (host, "group/repo"))


class DetectRepo(Base):
    def origin(self, url, why=""):
        p = mock.patch.object(scaffold, "_origin_url",
                              return_value=(url, why or ("" if url else
                                                         "no git origin remote")))
        p.start()
        self.addCleanup(p.stop)

    def test_ssh_https_and_alias_fill_repo(self):
        for url in ("git@github.com:acme/widgets.git",
                    "https://github.com/acme/widgets.git",
                    "git@github-work:acme/widgets.git"):
            with self.subTest(url=url):
                self.origin(url)
                d = self.init(force=True)
                self.assertEqual(d["vcs"]["repo"], "acme/widgets")
                self.assertIn("[vcs].repo = acme/widgets", self.out)

    def test_every_provider_and_inherited_config(self):
        self.origin("git@github.com:acme/widgets.git")
        for provider in scaffold.available_providers():
            with self.subTest(provider=provider):
                d = self.init(provider=provider, force=True)
                self.assertEqual(d["vcs"]["repo"], "acme/widgets")
        self.parent()
        d = self.init(force=True)
        self.assertIn("generated by `tkt init`",
                      (self.child / ".sdlc" / "config.toml").read_text().lower())
        self.assertEqual(d["vcs"]["repo"], "acme/widgets")

    def test_keeps_the_line_shape(self):
        self.origin("git@github.com:acme/widgets.git")
        self.init()
        text = (self.child / ".sdlc" / "config.toml").read_text()
        self.assertIn('repo           = "acme/widgets"\n', text)
        self.assertNotIn(PLACEHOLDER, text)

    def test_no_remote_keeps_placeholder_with_note(self):
        for why in ("no git origin remote", "not a git repo",
                    "not the top of its own git repo"):
            with self.subTest(why=why):
                self.origin(None, why)
                d = self.init(force=True)
                self.assertEqual(d["vcs"]["repo"], PLACEHOLDER)
                self.assertIn(f'{why}; [vcs].repo left as "{PLACEHOLDER}"', self.out)

    def test_unparseable_keeps_placeholder_and_hides_url(self):
        for url in ("https://bot:s3cret@example.com/g/sub/repo.git", TOKEN_URL):
            with self.subTest(url=url):
                self.origin(url)
                d = self.init(force=True)
                self.assertEqual(d["vcs"]["repo"], PLACEHOLDER)
                self.assertIn("not an owner/repo URL", self.out)
                for secret in ("s3cret", "SECRET"):
                    self.assertNotIn(secret, self.out + self.err.getvalue())

    def test_non_github_host_named_in_note(self):
        self.origin("https://bot:SECRET@gitlab.com/group/repo.git")
        d = self.init()
        self.assertEqual(d["vcs"]["repo"], PLACEHOLDER)
        self.assertIn("git origin remote is on gitlab.com, not GitHub", self.out)
        self.assertNotIn("SECRET", self.out + self.err.getvalue())

    def test_opt_out_never_asks_git(self):
        with mock.patch.object(scaffold, "_origin_url") as m:
            d = self.init(detect_repo=False)
        m.assert_not_called()
        self.assertEqual(d["vcs"]["repo"], PLACEHOLDER)
        self.assertNotIn("[vcs].repo", self.out)

    def test_cli_no_detect_repo_flag(self):
        self.origin("git@github.com:acme/widgets.git")
        for extra, want in (([], "acme/widgets"), (["--no-detect-repo"], PLACEHOLDER)):
            with self.subTest(extra=extra), \
                    mock.patch.object(sys.stdin, "isatty", return_value=False), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                rc = cli.main(["init", "--provider", "markdown", "--dir",
                               str(self.child), "--no-detect-build", "--no-inherit",
                               "--force", *extra])
            self.assertEqual(rc, 0)
            self.assertEqual(self.cfg()["vcs"]["repo"], want)

    def test_only_github_vcs_and_only_placeholders(self):
        with mock.patch.object(scaffold, "_origin_url",
                               return_value=("git@github.com:acme/widgets.git", "")) as m:
            for text in ('[vcs]\nprovider = "gitlab"\nrepo = "owner/repo"\n',
                         '[vcs]\nprovider = "github"\nrepo = "real/one"\n',
                         '[ticketing]\nprovider = "markdown"\n',
                         '[vcs]\nprovider = "github"\nrepo = ["owner/repo"]\n',
                         'not toml ['):
                with self.subTest(text=text):
                    out, notes, repo = scaffold._seed_repo(text, self.child)
                    self.assertEqual((out, notes, repo), (text, [], None))
            m.assert_not_called()
            out, notes, repo = scaffold._seed_repo(
                '[vcs]\nprovider = "github"\nrepo = "owner/repo"\n', self.child)
            self.assertEqual(repo, "acme/widgets")
            self.assertEqual(tomllib.loads(out)["vcs"]["repo"], "acme/widgets")

    def test_no_repo_note_when_write_fails(self):
        self.origin("git@github.com:acme/widgets.git")
        with mock.patch.object(scaffold, "_write_atomic", side_effect=OSError("disk full")):
            self.init_raises(OSError)
        self.assertNotIn("[vcs].repo", self.out)


class OriginUrl(unittest.TestCase):
    """`_git` / `_origin_url` with subprocess stubbed."""
    target = Path("/proj")

    def test_git_failures_are_none(self):
        for exc in (FileNotFoundError("git"), subprocess.TimeoutExpired("git", 10),
                    PermissionError("denied")):
            with self.subTest(exc=type(exc).__name__), \
                    mock.patch.object(scaffold.subprocess, "run", side_effect=exc):
                self.assertIsNone(scaffold._git(self.target, "status"))
        for rc, out in ((2, "x"), (0, "  \n")):
            done = subprocess.CompletedProcess([], rc, stdout=out, stderr="")
            with mock.patch.object(scaffold.subprocess, "run", return_value=done):
                self.assertIsNone(scaffold._git(self.target, "status"))

    def test_undecodable_git_output_does_not_crash(self):
        def run(*a, **kw):
            raw = b"https://github.com/acme/w\xffidgets.git\n"
            return subprocess.CompletedProcess(
                [], 0, stdout=raw.decode(kw["encoding"], kw["errors"]), stderr="")
        with mock.patch.object(scaffold.subprocess, "run", side_effect=run):
            url = scaffold._git(self.target, "remote", "get-url", "origin")
        self.assertIsNone(scaffold.parse_remote(url))

    def test_hook_git_env_does_not_leak_in(self):
        done = subprocess.CompletedProcess([], 0, stdout="git@h:a/b.git\n", stderr="")
        with mock.patch.dict(os.environ, {"GIT_DIR": "/elsewhere/.git",
                                          "GIT_WORK_TREE": "/elsewhere",
                                          "GIT_COMMON_DIR": "/elsewhere/.git"}), \
                mock.patch.object(scaffold.subprocess, "run", return_value=done) as run:
            self.assertEqual(scaffold._git(self.target, "x"), "git@h:a/b.git")
        env = run.call_args.kwargs["env"]
        for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
            self.assertNotIn(var, env)
        self.assertEqual(run.call_args.args[0][:3], ["git", "-C", str(self.target)])

    def test_reasons(self):
        def fake(answers):
            return lambda target, *args: answers[args[0]]
        for answers, same, want in [
                ({"rev-parse": None}, True, (None, "not a git repo")),
                ({"rev-parse": "/outer"}, False, (None, "not the top of its own git repo")),
                ({"rev-parse": "/proj", "remote": None}, True,
                 (None, "no git origin remote")),
                ({"rev-parse": "/proj", "remote": "git@h:a/b.git"}, True,
                 ("git@h:a/b.git", ""))]:
            with self.subTest(answers=answers), \
                    mock.patch.object(scaffold, "_git", fake(answers)), \
                    mock.patch.object(scaffold.os.path, "samefile", return_value=same):
                self.assertEqual(scaffold._origin_url(self.target), want)
        with mock.patch.object(scaffold, "_git", fake({"rev-parse": "/gone"})), \
                mock.patch.object(scaffold.os.path, "samefile",
                                  side_effect=FileNotFoundError):
            self.assertEqual(scaffold._origin_url(self.target)[1],
                             "not the top of its own git repo")


@unittest.skipUnless(shutil.which("git"), "git not on PATH")
class DetectRepoRealGit(Base):
    real_git = True

    def git(self, where, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        subprocess.run(["git", "-C", str(where), *args], check=True,
                       capture_output=True, env=env)

    def test_origin_of_a_real_repo(self):
        self.git(self.child, "init", "-q")
        self.git(self.child, "remote", "add", "origin", "git@github-work:acme/widgets.git")
        self.assertEqual(self.init()["vcs"]["repo"], "acme/widgets")

    def test_repo_without_origin(self):
        self.git(self.child, "init", "-q")
        self.git(self.child, "remote", "add", "upstream", "git@github.com:acme/widgets.git")
        self.assertEqual(self.init()["vcs"]["repo"], PLACEHOLDER)
        self.assertIn("no git origin remote", self.out)

    def test_enclosing_repo_origin_not_used(self):
        self.git(self.root, "init", "-q")
        self.git(self.root, "remote", "add", "origin", "git@github.com:acme/outer.git")
        self.assertEqual(self.init()["vcs"]["repo"], PLACEHOLDER)
        self.assertIn("not the top of its own git repo", self.out)

    def test_plain_dir_keeps_placeholder(self):
        # "not a git repo", or "not the top ..." if $TMPDIR sits in a repo
        self.assertEqual(self.init()["vcs"]["repo"], PLACEHOLDER)
        self.assertIn(f'[vcs].repo left as "{PLACEHOLDER}"', self.out)


if __name__ == "__main__":
    unittest.main()
