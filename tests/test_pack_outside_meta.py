"""TKT-109: `tkt sync-pack` never follows a symlink out of the consumer tree
for its two writes outside the pack-file loop: the `AGENTS.md` managed block
and `.sdlc/pack-manifest.json`.

AGENTS.md resolving outside (an outside symlink, a dangling one) is skipped
with the same note pack files get: not read for splicing, not written, its
manifest record kept, and `--check` does not call it missing. An in-repo
symlink (`AGENTS.md -> CLAUDE.md`) still gets the block.

A manifest resolving outside (`.sdlc` linked to a shared dir, or the
manifest file itself a dangling outside link) refuses the whole sync up
front with a UsageError (exit 64), before any write, in write and `--check`
mode alike.

Every case points `core.pack.PACK_ROOT` at a temp pack. Offline, POSIX
symlinks.

    python3 -m unittest tests.test_pack_outside_meta
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
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import pack  # noqa: E402
from core.errors import UsageError  # noqa: E402

NOTE = "skipped AGENTS.md: resolves outside the repo"
KEY = pack.AGENTS_MANIFEST_KEY


def skill_md(name: str) -> str:
    return f"---\nname: {name}\ndescription: '{name} skill.'\n---\n\n# {name}\n"


@unittest.skipUnless(hasattr(os, "symlink") and os.name == "posix",
                     "needs POSIX symlinks")
class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.pack_root = self.tmp / "pack"
        d = self.pack_root / "skills" / "demo"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(skill_md("demo"))
        enf = self.pack_root / "enforcement"
        enf.mkdir()
        (enf / "check.mjs").write_text("// checker\n")
        self.consumer = self.tmp / "consumer"
        self.consumer.mkdir()
        self.out_dir = self.tmp / "outside"
        self.out_dir.mkdir()
        patcher = mock.patch.object(pack, "PACK_ROOT", self.pack_root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sync(self, check=False) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = pack.sync_pack(str(self.consumer), all_harnesses=False,
                                check=check, harnesses=None, prune=False)
        return rc, out.getvalue(), err.getvalue()

    def manifest(self) -> dict:
        return json.loads((self.consumer / pack.MANIFEST_REL).read_text())

    def snapshot(self, root: Path) -> dict:
        """{relpath: bytes | '<link>' | '<dir>'} for everything under root."""
        out = {}
        for p in sorted(root.rglob("*")):
            rel = str(p.relative_to(root))
            if p.is_symlink():
                out[rel] = "<link> " + os.readlink(p)
            elif p.is_dir():
                out[rel] = "<dir>"
            else:
                out[rel] = p.read_bytes()
        return out


class AgentsOutside(Base):
    def test_outside_symlink_is_neither_read_nor_written(self):
        target = self.out_dir / "AGENTS.md"
        target.write_text("# dotfiles agents\n")
        (self.consumer / "AGENTS.md").symlink_to(target)
        before = self.snapshot(self.out_dir)
        rc, out, err = self.sync()
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.snapshot(self.out_dir), before)
        self.assertTrue((self.consumer / "AGENTS.md").is_symlink())
        self.assertIn(NOTE, out)
        self.assertIn("1 skipped (outside the repo)", out)
        self.assertNotIn(KEY, self.manifest()["files"])
        # The rest of the pack still installed.
        self.assertTrue((self.consumer / ".claude/skills/demo/SKILL.md").is_file())

    def test_dangling_outside_symlink_target_is_not_created(self):
        gone = self.out_dir / "new.md"
        (self.consumer / "AGENTS.md").symlink_to(gone)
        rc, out, err = self.sync()
        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.lexists(gone))
        self.assertEqual(self.snapshot(self.out_dir), {})
        self.assertIn(NOTE, out)

    def test_existing_record_is_kept_and_check_does_not_call_it_missing(self):
        self.sync()
        recorded = self.manifest()["files"][KEY]
        agents = self.consumer / "AGENTS.md"
        target = self.out_dir / "AGENTS.md"
        target.write_text("# no markers here\n")
        agents.unlink()
        agents.symlink_to(target)
        before = self.snapshot(self.out_dir)

        rc, out, err = self.sync()
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.snapshot(self.out_dir), before)
        self.assertEqual(self.manifest()["files"][KEY], recorded)
        self.assertIn(NOTE, out)

        rc, out, _ = self.sync(check=True)
        self.assertEqual(rc, 0, out)
        self.assertIn(NOTE, out)
        self.assertNotIn("missing", out)
        self.assertEqual(self.snapshot(self.out_dir), before)

    def test_in_repo_link_to_a_file_linked_outside_is_skipped(self):
        # AGENTS.md -> CLAUDE.md, but CLAUDE.md lives in a dotfiles checkout:
        # the whole chain is resolved, not just the first link.
        target = self.out_dir / "CLAUDE.md"
        target.write_text("# dotfiles claude\n")
        (self.consumer / "CLAUDE.md").symlink_to(target)
        (self.consumer / "AGENTS.md").symlink_to("CLAUDE.md")
        before = self.snapshot(self.out_dir)
        rc, out, err = self.sync()
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.snapshot(self.out_dir), before)
        self.assertIn(NOTE, out)

    def test_self_loop_is_skipped_without_traceback(self):
        (self.consumer / "AGENTS.md").symlink_to("AGENTS.md")
        rc, out, err = self.sync()
        self.assertEqual(rc, 0, err)
        self.assertIn(NOTE, out)
        rc, out, _ = self.sync(check=True)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("missing", out)

    def test_in_repo_symlink_is_still_updated(self):
        claude = self.consumer / "CLAUDE.md"
        claude.write_text("# project notes\n")
        (self.consumer / "AGENTS.md").symlink_to("CLAUDE.md")
        rc, out, err = self.sync()
        self.assertEqual(rc, 0, err)
        self.assertNotIn("resolves outside", out)
        text = claude.read_text()
        self.assertTrue(text.startswith("# project notes\n"))
        self.assertIn(pack.BEGIN, text)
        self.assertTrue((self.consumer / "AGENTS.md").is_symlink())
        self.assertIn(KEY, self.manifest()["files"])
        rc, out, _ = self.sync(check=True)
        self.assertEqual(rc, 0, out)
        rc, out, _ = self.sync()
        self.assertIn("up to date", out)


class ManifestOutside(Base):
    def assert_refused(self, check: bool) -> str:
        before_out = self.snapshot(self.out_dir)
        before_in = self.snapshot(self.consumer)
        with self.assertRaises(UsageError) as cm:
            self.sync(check=check)
        self.assertEqual(cm.exception.exit_code, 64)
        self.assertEqual(self.snapshot(self.out_dir), before_out)
        self.assertEqual(self.snapshot(self.consumer), before_in)
        msg = str(cm.exception)
        self.assertIn(pack.MANIFEST_REL, msg)
        self.assertIn("outside", msg)
        return msg

    def test_sdlc_dir_linked_outside_refuses_before_any_write(self):
        (self.consumer / ".sdlc").symlink_to(self.out_dir, target_is_directory=True)
        self.assert_refused(check=False)

    def test_sdlc_dir_linked_outside_refuses_check_too(self):
        (self.consumer / ".sdlc").symlink_to(self.out_dir, target_is_directory=True)
        self.assert_refused(check=True)

    def test_existing_outside_manifest_is_not_read_or_rewritten(self):
        # A shared .sdlc another repo already synced into.
        (self.out_dir / "pack-manifest.json").write_text('{"files": {}}\n')
        (self.consumer / ".sdlc").symlink_to(self.out_dir, target_is_directory=True)
        self.assert_refused(check=False)

    def test_dangling_outside_manifest_leaf_is_not_created(self):
        (self.consumer / ".sdlc").mkdir()
        gone = self.out_dir / "manifest.json"
        (self.consumer / pack.MANIFEST_REL).symlink_to(gone)
        self.assert_refused(check=False)
        self.assertFalse(os.path.lexists(gone))

    def test_in_repo_sdlc_symlink_keeps_working(self):
        shared = self.consumer / "shared-sdlc"
        shared.mkdir()
        (self.consumer / ".sdlc").symlink_to("shared-sdlc", target_is_directory=True)
        rc, out, err = self.sync()
        self.assertEqual(rc, 0, err)
        self.assertTrue((shared / "pack-manifest.json").is_file())
        rc, out, _ = self.sync()
        self.assertIn("up to date", out)


if __name__ == "__main__":
    unittest.main()
