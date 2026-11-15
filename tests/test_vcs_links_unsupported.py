"""TKT-114: `tkt edit --add-branch/--add-pr` on a backend that cannot store them.

QA Author — adversarial tests
Break-It dimensions covered: error propagation (exit code, clean stderr, no
traceback), partial failure (nothing written, no partial JSON), input
validation ordering (a bad value is a usage error before any backend call).

Only the markdown adapter implements `edit`; every other backend inherits
`adapters/base.Adapter.edit`, which raises `ProviderError` (exit 3). The
open-pr skill relies on that: it treats exit 3 from its record step as "this
backend does not store branches/PRs" and carries on. So on such a backend the
VCS flags must exit 3 with a one-line `tkt:` message, never a TypeError
traceback, and a malformed added value must still exit 64 first, without the
adapter being asked to edit anything. The markdown behaviour is covered in
test_vcs_links_markdown.py.

Offline: an openkanban store in a temp dir. Run from the repo root:

    python3 -m unittest tests.test_vcs_links_unsupported
"""
import contextlib
import importlib
import inspect
import io
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
from core import cli, registry  # noqa: E402

PID = "proj-1"
PR = "https://github.com/olddognewflex/tkt/pull/96"
VCS_KWARGS = ("add_branches", "remove_branches", "add_prs", "remove_prs")
UNSUPPORTED = "edit not supported by provider 'openkanban'"


class _OpenKanbanCli(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        store = self.tmp / "ok"
        (store / "tickets").mkdir(parents=True)
        (store / "projects.json").write_text(json.dumps(
            {"projects": {PID: {"id": PID, "name": "demo"}}}))
        self.store = store / "tickets" / f"{PID}.json"
        self.store.write_text(json.dumps(
            {"project_id": PID, "updated_at": "2026-01-01T00:00:00Z",
             "tickets": {"t1": {"id": "t1", "project_id": PID, "title": "t1",
                                "description": "", "status": "backlog",
                                "agent_status": "none", "labels": [],
                                "priority": 3, "meta": {}}}}))
        self.cfg = self.tmp / ".sdlc" / "config.toml"
        self.cfg.parent.mkdir()
        self.cfg.write_text(
            '[ticketing]\nprovider = "openkanban"\n'
            f'[openkanban]\nproject = "demo"\nconfig_dir = "{store}"\n'
            '[board.roles]\ntodo = "backlog"\nin_progress = "in_progress"\n'
            'done = "done"\n', encoding="utf-8")

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--config", str(self.cfg), *argv])
        return code, out.getvalue(), err.getvalue()


class UnsupportedBackendExits3(_OpenKanbanCli):

    def assert_clean_3(self, code, out, err):
        self.assertEqual(code, 3, err)
        self.assertEqual(out, "")               # no partial ticket / JSON
        self.assertIn(UNSUPPORTED, err)
        self.assertTrue(err.startswith("tkt: "), err)
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertNotIn("Traceback", err)
        self.assertNotIn("TypeError", err)
        self.assertNotIn("unexpected keyword", err)

    def test_open_pr_record_call_exits_3_cleanly(self):
        # The exact shape open-pr runs.
        before = self.store.read_text()
        self.assert_clean_3(*self.run_cli(
            "edit", "t1", "--add-branch", "feature/tkt-114-x", "--add-pr", PR))
        self.assertEqual(self.store.read_text(), before)

    def test_detached_head_shape_exits_3_cleanly(self):
        # open-pr on a detached HEAD records the PR alone.
        self.assert_clean_3(*self.run_cli("edit", "t1", "--add-pr", PR))

    def test_each_flag_alone_exits_3(self):
        for argv in (("--add-branch", "b1"), ("--remove-branch", "b1"),
                     ("--add-pr", PR), ("--remove-pr", PR)):
            with self.subTest(argv=argv):
                self.assert_clean_3(*self.run_cli("edit", "t1", *argv))

    def test_json_flag_prints_no_partial_output(self):
        for pos in ("before", "after"):
            with self.subTest(json=pos):
                argv = ["edit", "t1", "--add-branch", "b1", "--add-pr", PR]
                argv = ["--json", *argv] if pos == "before" else [*argv, "--json"]
                self.assert_clean_3(*self.run_cli(*argv))

    def test_unremovable_odd_entry_still_exits_3_not_64(self):
        # Removals are not validated (they must match hand-edited entries),
        # so an odd removal reaches the backend and gets its exit 3.
        self.assert_clean_3(*self.run_cli(
            "edit", "t1", "--remove-branch", "odd@{x}", "--remove-pr", "not-a-url"))

    def test_vcs_flags_mixed_with_other_fields_exit_3(self):
        self.assert_clean_3(*self.run_cli(
            "edit", "t1", "--priority", "High", "--add-branch", "b1"))

    def test_missing_ticket_is_still_unsupported_not_a_crash(self):
        self.assert_clean_3(*self.run_cli(
            "edit", "nope", "--add-branch", "b1", "--add-pr", PR))


class BadValueExits64BeforeBackend(_OpenKanbanCli):
    """Validation runs first: 64, not 3, and `edit` is never reached."""

    BAD = (
        ("--add-branch", "", "--add-pr", PR),
        ("--add-branch", "HEAD", "--add-pr", PR),    # `rev-parse --abbrev-ref` when detached
        ("--add-branch", "o/r@HEAD", "--add-pr", PR),
        ("--add-branch", "a b", "--add-pr", PR),
        ("--add-branch", "'quoted'", "--add-pr", PR),
        ("--add-branch", "ok", "--add-branch", "a,b"),   # one bad among good
        ("--add-branch", "feature/x", "--add-pr", ""),
        ("--add-branch", "feature/x", "--add-pr", "github.com/o/r/pull/1"),
        ("--add-branch", "feature/x", "--add-pr", "ftp://host/pull/1"),
        ("--add-pr", PR, "--add-pr", "https://x/pull/1]"),
        ("--add-pr", PR + '"',),
        ("--remove-branch", "b1", "--add-branch", "[b]"),  # removal valid, add bad
    )

    def test_bad_added_value_is_64_and_edit_never_called(self):
        before = self.store.read_text()
        for argv in self.BAD:
            with self.subTest(argv=argv), \
                    mock.patch.object(OpenKanbanAdapter, "edit",
                                      autospec=True) as spy:
                code, out, err = self.run_cli("edit", "t1", *argv)
                self.assertEqual(code, 64, err)
                self.assertEqual(out, "")
                self.assertNotIn(UNSUPPORTED, err)
                self.assertNotIn("Traceback", err)
                self.assertRegex(err, r"^tkt: --add-(branch|pr): invalid ")
                spy.assert_not_called()
        self.assertEqual(self.store.read_text(), before)

    def test_good_values_do_reach_the_adapter(self):
        # Guards the test above: the spy does see a valid call, with the
        # VCS lists passed through and nothing passed for absent flags.
        with mock.patch.object(OpenKanbanAdapter, "edit", autospec=True,
                               side_effect=lambda *a, **k: None) as spy, \
                mock.patch.object(cli, "_print_ticket_human"):
            code, _, err = self.run_cli(
                "edit", "t1", "--add-branch", "b1", "--add-pr", PR)
        self.assertEqual(code, 0, err)
        spy.assert_called_once()
        kw = spy.call_args.kwargs
        self.assertEqual(kw["add_branches"], ["b1"])
        self.assertEqual(kw["add_prs"], [PR])
        self.assertNotIn("remove_branches", kw)
        self.assertNotIn("remove_prs", kw)


class EveryAdapterEditAcceptsTheVcsKwargs(unittest.TestCase):
    """The CLI passes the VCS lists as keywords whenever the flags are given.
    An adapter that overrides `edit` without them would turn the open-pr
    record step into an uncaught TypeError instead of an exit code."""

    def test_signatures(self):
        for name, (mod, cls) in registry._PROVIDERS.items():
            with self.subTest(provider=name):
                edit = getattr(importlib.import_module(mod), cls).edit
                params = inspect.signature(edit).parameters
                if any(p.kind is p.VAR_KEYWORD for p in params.values()):
                    continue
                missing = [k for k in VCS_KWARGS if k not in params]
                self.assertEqual(missing, [], f"{cls}.edit lacks {missing}")


if __name__ == "__main__":
    unittest.main()
