"""TKT-23: before `tkt sync-pack` overwrites a consumer file the pack has
never written, it copies it to `<name>.pre-sdlc.bak` and prints the path.

Covered: the backup holds the original bytes and is reported; an identical
pre-existing file and a locally modified pack file are not backed up; an
existing backup is never overwritten (identical one reused, otherwise the
next numbered name); a symlink planted at the backup path is not followed;
a failed backup, an unreadable file or one under a symlinked dir resolving
outside the consumer tree leaves the file untouched, out of the manifest,
and exits 1, and a failure is retried by the next sync; backups are never in
the manifest, never shipped from a pack tree, and survive `--prune`;
`--check` writes none.

Every case points `core.pack.PACK_ROOT` at a temp pack. Offline.

    python3 -m unittest tests.test_pack_backup
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

REL = ".claude/skills/demo/SKILL.md"
BAK = REL + ".pre-sdlc.bak"


def skill_md(name: str) -> str:
    return f"---\nname: {name}\ndescription: '{name} skill.'\n---\n\n# {name}\n"


class TempPack(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.pack_root = self.tmp / "pack"
        for name in ("demo", "extra"):
            d = self.pack_root / "skills" / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(skill_md(name))
        self.consumer = self.tmp / "consumer"
        self.consumer.mkdir()
        patcher = mock.patch.object(pack, "PACK_ROOT", self.pack_root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sync(self, check=False, prune=False) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = pack.sync_pack(str(self.consumer), all_harnesses=False,
                                check=check, harnesses=None, prune=prune)
        return rc, out.getvalue(), err.getvalue()

    def plant(self, rel: str = REL, text: str = "CONSUMER-OWNED\n") -> Path:
        p = self.consumer / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def manifest(self) -> dict:
        return json.loads((self.consumer / pack.MANIFEST_REL).read_text())

    @property
    def shipped(self) -> str:
        return (self.pack_root / "skills/demo/SKILL.md").read_text()


class Backup(TempPack):
    def test_preexisting_file_is_backed_up_and_reported(self):
        dst = self.plant()
        rc, out, _ = self.sync()
        self.assertEqual(rc, 0)
        self.assertEqual((self.consumer / BAK).read_text(), "CONSUMER-OWNED\n")
        self.assertEqual(dst.read_text(), self.shipped)
        self.assertIn(f"pre-existing file overwritten: {REL} (backup: {BAK})", out)
        self.assertIn("1 backed up", out)

    def test_backup_is_never_tracked(self):
        self.plant()
        self.sync()
        m = self.manifest()
        for key in ("files", "retired", "base_retired"):
            self.assertFalse(any("pre-sdlc" in k for k in m.get(key, {})), key)

    def test_second_sync_is_a_no_op(self):
        self.plant()
        self.sync()
        rc, out, _ = self.sync()
        self.assertEqual(rc, 0)
        self.assertIn("up to date", out)
        self.assertNotIn("backup", out)

    def test_identical_preexisting_file_is_not_backed_up(self):
        self.plant(text=self.shipped)
        _, out, _ = self.sync()
        self.assertFalse((self.consumer / BAK).exists())
        self.assertNotIn("backup", out)

    def test_locally_modified_pack_file_is_not_backed_up(self):
        self.sync()
        (self.consumer / REL).write_text("edited after sync\n")
        _, out, _ = self.sync()
        self.assertFalse((self.consumer / BAK).exists())
        self.assertIn("locally modified", out)

    def test_existing_identical_backup_is_reused(self):
        self.plant()
        bak = self.plant(BAK, "CONSUMER-OWNED\n")
        _, out, _ = self.sync()
        self.assertEqual(bak.read_text(), "CONSUMER-OWNED\n")
        self.assertFalse((self.consumer / (REL + ".pre-sdlc.1.bak")).exists())
        self.assertIn(f"(backup: {BAK})", out)

    def test_existing_different_backup_is_never_overwritten(self):
        self.plant()
        bak = self.plant(BAK, "OLDER BACKUP\n")
        _, out, _ = self.sync()
        self.assertEqual(bak.read_text(), "OLDER BACKUP\n")
        bak1 = REL + ".pre-sdlc.1.bak"
        self.assertEqual((self.consumer / bak1).read_text(), "CONSUMER-OWNED\n")
        self.assertIn(f"(backup: {bak1})", out)

    def test_symlink_at_backup_path_is_not_followed(self):
        self.plant()
        outside = self.tmp / "outside.txt"
        outside.write_text("not yours\n")
        link = self.consumer / BAK
        link.symlink_to(outside)
        self.sync()
        self.assertEqual(outside.read_text(), "not yours\n")
        self.assertTrue(link.is_symlink())
        self.assertEqual((self.consumer / (REL + ".pre-sdlc.1.bak")).read_text(),
                         "CONSUMER-OWNED\n")

    def test_failed_backup_leaves_file_untouched_and_retries(self):
        dst = self.plant()
        with mock.patch.object(pack, "_backup", side_effect=OSError("disk full")):
            rc, out, err = self.sync()
        self.assertEqual(rc, 1)
        self.assertEqual(dst.read_text(), "CONSUMER-OWNED\n")
        self.assertIn(f"could not back up pre-existing {REL} (disk full)", err)
        self.assertIn("1 NOT written (backup failed)", out)
        self.assertNotIn(REL, self.manifest()["files"])
        # Other files still installed.
        self.assertTrue((self.consumer / ".claude/skills/extra/SKILL.md").is_file())
        # Fixed: the next sync still sees it as pre-existing and backs it up.
        rc, out, _ = self.sync()
        self.assertEqual(rc, 0)
        self.assertEqual((self.consumer / BAK).read_text(), "CONSUMER-OWNED\n")
        self.assertEqual(dst.read_text(), self.shipped)
        self.assertIn(REL, self.manifest()["files"])

    def test_unwritable_dir_fails_backup_without_overwrite(self):
        if os.name != "posix" or os.geteuid() == 0:
            self.skipTest("needs POSIX permissions and a non-root user")
        dst = self.plant()
        d = dst.parent
        d.chmod(0o555)
        self.addCleanup(d.chmod, 0o755)
        rc, _, err = self.sync()
        self.assertEqual(rc, 1)
        self.assertEqual(dst.read_text(), "CONSUMER-OWNED\n")
        self.assertIn("could not back up", err)

    def test_unreadable_preexisting_file_is_left_alone(self):
        if os.name != "posix" or os.geteuid() == 0:
            self.skipTest("needs POSIX permissions and a non-root user")
        dst = self.plant()
        dst.chmod(0o000)
        self.addCleanup(dst.chmod, 0o644)
        rc, _, err = self.sync()
        self.assertEqual(rc, 1)
        self.assertIn(f"could not back up pre-existing {REL} (unreadable:", err)
        dst.chmod(0o644)
        self.assertEqual(dst.read_text(), "CONSUMER-OWNED\n")
        self.assertNotIn(REL, self.manifest()["files"])

    def test_symlinked_dir_outside_tree_is_left_alone(self):
        # `tkt init --link-skills` symlinks whole skill dirs, possibly into
        # another pack checkout: neither back up into it nor overwrite it.
        other = self.tmp / "other-pack" / "skills" / "demo"
        other.mkdir(parents=True)
        (other / "SKILL.md").write_text("OTHER CHECKOUT\n")
        link_dir = self.consumer / ".claude/skills/demo"
        link_dir.parent.mkdir(parents=True)
        link_dir.symlink_to(other, target_is_directory=True)
        rc, _, err = self.sync()
        self.assertEqual(rc, 1)
        self.assertIn("resolves outside the consumer tree", err)
        self.assertEqual((other / "SKILL.md").read_text(), "OTHER CHECKOUT\n")
        self.assertEqual([p.name for p in other.iterdir()], ["SKILL.md"])

    def test_backup_inside_a_pack_tree_is_never_shipped(self):
        stray = self.pack_root / "skills/demo/SKILL.md.pre-sdlc.bak"
        stray.write_text("stray\n")
        (self.pack_root / "skills/demo/x.pre-sdlc.2.bak").write_text("stray\n")
        self.sync()
        self.assertFalse(any("pre-sdlc" in k for k in self.manifest()["files"]))
        self.assertFalse((self.consumer / BAK).exists())

    def test_prune_never_removes_a_backup(self):
        extra = ".claude/skills/extra/SKILL.md"
        self.plant(extra)
        self.sync()
        bak = self.consumer / (extra + ".pre-sdlc.bak")
        self.assertTrue(bak.is_file())
        shutil.rmtree(self.pack_root / "skills" / "extra")
        rc, out, _ = self.sync(prune=True)
        self.assertEqual(rc, 0)
        self.assertIn(f"pruned: {extra}", out)
        self.assertEqual(bak.read_text(), "CONSUMER-OWNED\n")

    def test_check_writes_no_backup(self):
        self.plant()
        rc, _, _ = self.sync(check=True)
        self.assertEqual(rc, 1)
        self.assertFalse((self.consumer / BAK).exists())


if __name__ == "__main__":
    unittest.main()
