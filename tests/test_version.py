"""Tests for `tkt --version` / `tkt -V` (TKT-54).

Offline. The git-derived suffix is exercised against this checkout when it is a
git repo, and with `subprocess.run` stubbed for the no-git and vendored-copy
cases. Run from the repo root:

    python3 -m unittest tests.test_version
"""
import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core  # noqa: E402
from core import version  # noqa: E402
from core.cli import build_parser, main  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        with _catch_exit() as box:
            main(argv)
    return box["code"], out.getvalue(), err.getvalue()


@contextlib.contextmanager
def _catch_exit():
    box = {"code": None}
    try:
        yield box
    except SystemExit as e:
        box["code"] = e.code


def _is_git_checkout() -> bool:
    try:
        top = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return False
    return Path(top).resolve() == ROOT


class TestFlag(unittest.TestCase):
    def test_long_flag_prints_version_and_exits_zero(self):
        code, out, _ = _run(["--version"])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith(f"tkt {core.__version__}"), out)

    def test_short_alias(self):
        code, out, _ = _run(["-V"])
        self.assertEqual(code, 0)
        self.assertEqual(out, _run(["--version"])[1])

    def test_no_verb_needed(self):
        """The verb subparser is required; --version must still win."""
        code, _, err = _run(["--version"])
        self.assertEqual(code, 0)
        self.assertNotIn("required", err)

    def test_listed_in_help(self):
        help_text = build_parser().format_help()
        self.assertIn("-V, --version", help_text)
        self.assertIn(f"version {core.__version__}", help_text)

    def test_single_canonical_source(self):
        self.assertRegex(core.__version__, r"^\d+\.\d+\.\d+$")
        self.assertTrue(version.version_string().startswith(core.__version__))
        # The literal lives in exactly one of tkt's own source files. Scan only
        # those (not a .venv or a consumer's tree) and judge paths relative to
        # the checkout, so a parent directory named `tests` changes nothing.
        sources = [ROOT / "tkt", *(ROOT / "core").glob("*.py"), *(ROOT / "adapters").glob("*.py")]
        hits = [p.relative_to(ROOT).as_posix() for p in sources
                if f'"{core.__version__}"' in p.read_text(errors="ignore")]
        self.assertEqual(hits, ["core/__init__.py"])


class TestLaziness(unittest.TestCase):
    def test_parser_and_normal_verbs_never_shell_out(self):
        """`tkt agents` is polled at 1Hz; building the parser must not run git."""
        with mock.patch("subprocess.run") as run, mock.patch("subprocess.Popen") as popen:
            parser = build_parser()
            parser.parse_args(["lane", "todo"])
            parser.format_help()
        run.assert_not_called()
        popen.assert_not_called()


class TestGitSuffix(unittest.TestCase):
    """build_id in isolation. A company copy ships a PACK_VERSION stamp, which
    would add an upstream suffix to every string here; pin it off so these
    tests pass in that copy too. TestCompanyCopy covers the stamp."""

    def setUp(self):
        patcher = mock.patch("core.version.upstream_commit", return_value="")
        patcher.start()
        self.addCleanup(patcher.stop)

    @unittest.skipUnless(_is_git_checkout(), "not running from a git checkout")
    def test_checkout_appends_its_commit(self):
        sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short=7", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        s = version.version_string()
        self.assertRegex(s, rf"^{re.escape(core.__version__)} \(.*{sha[:7]}.*\)$")

    def test_describe_is_long_so_a_tag_still_carries_the_sha(self):
        calls = []

        def fake(cmd, **kw):
            calls.append(cmd)
            if "--show-toplevel" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=f"{ROOT}\n")
            return subprocess.CompletedProcess(cmd, 0, stdout="v0.1.0-0-g5a825d0\n")
        with mock.patch("core.version.subprocess.run", side_effect=fake):
            self.assertEqual(version.build_id(), "v0.1.0-0-g5a825d0")
        self.assertIn("--long", calls[-1])

    def test_inherited_git_env_is_dropped(self):
        """A hook's GIT_DIR must not make tkt report that repo's commit."""
        seen = {}

        def fake(cmd, **kw):
            seen.update(kw.get("env") or {})
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{ROOT}\n")
        with mock.patch.dict(os.environ, {"GIT_DIR": "/elsewhere/.git",
                                          "GIT_WORK_TREE": "/elsewhere"}), \
                mock.patch("core.version.subprocess.run", side_effect=fake):
            version.build_id()
        self.assertTrue(seen, "git was not called with an explicit env")
        self.assertFalse([k for k in seen if k.startswith("GIT_")])
        self.assertIn("PATH", seen)

    def test_no_git_binary_gives_plain_version(self):
        with mock.patch("core.version.subprocess.run", side_effect=FileNotFoundError):
            self.assertEqual(version.version_string(), core.__version__)

    def test_git_failure_gives_plain_version(self):
        err = subprocess.CalledProcessError(128, ["git"], stderr="not a git repository")
        with mock.patch("core.version.subprocess.run", side_effect=err):
            self.assertEqual(version.version_string(), core.__version__)

    def test_timeout_gives_plain_version(self):
        with mock.patch("core.version.subprocess.run",
                        side_effect=subprocess.TimeoutExpired(["git"], 2)):
            self.assertEqual(version.version_string(), core.__version__)

    def test_vendored_copy_ignores_the_enclosing_repo(self):
        """A copy of tkt inside another repo must not report that repo's commit."""
        def fake(cmd, **kw):
            if "--show-toplevel" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="/some/other/repo\n")
            return subprocess.CompletedProcess(cmd, 0, stdout="deadbee\n")
        with mock.patch("core.version.subprocess.run", side_effect=fake):
            self.assertEqual(version.version_string(), core.__version__)

    def test_own_checkout_reports_describe_output(self):
        def fake(cmd, **kw):
            if "--show-toplevel" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=f"{ROOT}\n")
            return subprocess.CompletedProcess(cmd, 0, stdout="abc1234-dirty\n")
        with mock.patch("core.version.subprocess.run", side_effect=fake):
            self.assertEqual(version.version_string(), f"{core.__version__} (abc1234-dirty)")


class TestCompanyCopy(unittest.TestCase):
    STAMP = ("source: upstream tkt SDLC pack\n"
             "commit: 16c6e2393cb1a318d3746f0bf9695b2ee3815370\n"
             "imported: 2026-07-08T07:18:28Z\n"
             "cli: acme-sdlc (renamed from tkt)\n")

    def test_upstream_commit_is_read_from_pack_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "PACK_VERSION").write_text(self.STAMP)
            self.assertEqual(version.upstream_commit(Path(tmp)), "16c6e23")

    def test_sha256_stamp_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "PACK_VERSION").write_text("commit: " + "ab" * 32 + "\n")
            self.assertEqual(version.upstream_commit(Path(tmp)), "abababa")

    def test_malformed_or_hostile_stamp_is_ignored(self):
        for bad in ("commit: not a sha at all\n", "commit:\n",
                    "commit = 16c6e23\n", "commit: \x1b[31m16c6e23\n",
                    "commit: 16C6E2393CB1\n"):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "PACK_VERSION").write_text(bad)
                self.assertEqual(version.upstream_commit(Path(tmp)), "")

    def test_no_stamp_means_no_upstream(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(version.upstream_commit(Path(tmp)), "")

    def test_copy_reports_its_own_and_the_upstream_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "PACK_VERSION").write_text(self.STAMP)
            with mock.patch("core.version.build_id", return_value="a1b2c3d"):
                self.assertEqual(version.version_string(Path(tmp)),
                                 f"{core.__version__} (a1b2c3d, upstream 16c6e23)")
            with mock.patch("core.version.build_id", return_value=""):
                self.assertEqual(version.version_string(Path(tmp)),
                                 f"{core.__version__} (upstream 16c6e23)")


class TestProgName(unittest.TestCase):
    def test_renamed_copy_prints_its_own_name(self):
        parser = build_parser()
        parser.prog = "acme-sdlc"
        out = io.StringIO()
        with contextlib.redirect_stdout(out), _catch_exit():
            parser.parse_args(["--version"])
        self.assertTrue(out.getvalue().startswith("acme-sdlc "), out.getvalue())


if __name__ == "__main__":
    unittest.main()
