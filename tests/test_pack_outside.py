"""TKT-108: `tkt sync-pack` never writes a pack file through a symlink that
resolves outside the consumer tree, at the leaf or at any parent.

Covered: a dangling leaf symlink (its target is not created); a symlinked
dest dir (`.kiro/steering` pointing out); a `tkt init --link-skills`-style
symlinked skill dir whose recorded file was modified (not overwritten, its
manifest record kept so it is not retired); a symlink loop (skipped, no
traceback); `--check` notes a skipped path and does not report it missing;
an unrecorded skipped path stays out of the manifest and is treated as
pre-existing once the link is gone. Still working: a dest dir symlinked to
another dir *inside* the repo, and a skill dir linked to the very pack
source it would copy (a silent no-op).

Every case points `core.pack.PACK_ROOT` at a temp pack. Offline, POSIX
symlinks.

    python3 -m unittest tests.test_pack_outside
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

SKILL = ".claude/skills/demo/SKILL.md"
STEER = ".kiro/steering/rules.md"
NOTE = "skipped {}: resolves outside the repo"


def skill_md(name: str) -> str:
    return f"---\nname: {name}\ndescription: '{name} skill.'\n---\n\n# {name}\n"


@unittest.skipUnless(hasattr(os, "symlink") and os.name == "posix",
                     "needs POSIX symlinks")
class Outside(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.pack_root = self.tmp / "pack"
        for name in ("demo", "extra"):
            d = self.pack_root / "skills" / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(skill_md(name))
        steering = self.pack_root / ".kiro" / "steering"
        steering.mkdir(parents=True)
        (steering / "rules.md").write_text("# steering rules\n")
        self.consumer = self.tmp / "consumer"
        self.consumer.mkdir()
        self.out_dir = self.tmp / "outside"
        self.out_dir.mkdir()
        patcher = mock.patch.object(pack, "PACK_ROOT", self.pack_root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sync(self, check=False, prune=False) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = pack.sync_pack(str(self.consumer), all_harnesses=False,
                                check=check, harnesses=None, prune=prune)
        return rc, out.getvalue(), err.getvalue()

    def manifest(self) -> dict:
        return json.loads((self.consumer / pack.MANIFEST_REL).read_text())

    def link(self, rel: str, to: Path, is_dir: bool = False) -> Path:
        p = self.consumer / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.symlink_to(to, target_is_directory=is_dir)
        return p

    def outside_files(self) -> list[str]:
        return sorted(str(p.relative_to(self.out_dir))
                      for p in self.out_dir.rglob("*"))

    # ---- refused ----------------------------------------------------------

    def test_dangling_leaf_symlink_target_is_not_created(self):
        leaked = self.out_dir / "leaked.md"
        dst = self.link(SKILL, leaked)
        rc, out, err = self.sync()
        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.lexists(leaked))
        self.assertEqual(self.outside_files(), [])
        self.assertTrue(dst.is_symlink())
        self.assertIn(NOTE.format(SKILL), out)
        self.assertIn("1 skipped (outside the repo)", out)
        self.assertNotIn(SKILL, self.manifest()["files"])
        self.assertFalse(os.path.lexists(self.consumer / (SKILL + ".pre-sdlc.bak")))
        # Everything else still installed.
        self.assertTrue((self.consumer / ".claude/skills/extra/SKILL.md").is_file())
        self.assertTrue((self.consumer / STEER).is_file())

    def test_symlinked_dest_dir_is_never_written_into(self):
        self.link(".kiro/steering", self.out_dir, is_dir=True)
        rc, out, err = self.sync()
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.outside_files(), [])
        self.assertIn(NOTE.format(STEER), out)
        self.assertNotIn(STEER, self.manifest()["files"])

    def test_dangling_dest_dir_symlink_is_not_created(self):
        gone = self.out_dir / "not-yet"
        self.link(".kiro/steering", gone, is_dir=True)
        rc, out, err = self.sync()
        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.lexists(gone))
        self.assertIn(NOTE.format(STEER), out)

    def test_link_skills_dir_with_recorded_modified_file(self):
        # Synced normally, then the skill dir is swapped for a symlink into
        # another checkout (the `tkt init --link-skills` shape) whose copy
        # differs from what the manifest recorded.
        self.sync()
        recorded = self.manifest()["files"][SKILL]
        other = self.out_dir / "other-pack" / "skills" / "demo"
        other.mkdir(parents=True)
        (other / "SKILL.md").write_text("OTHER CHECKOUT, EDITED\n")
        shutil.rmtree(self.consumer / ".claude/skills/demo")
        self.link(".claude/skills/demo", other, is_dir=True)

        rc, out, err = self.sync()
        self.assertEqual(rc, 0, err)
        self.assertEqual((other / "SKILL.md").read_text(), "OTHER CHECKOUT, EDITED\n")
        self.assertEqual([p.name for p in other.iterdir()], ["SKILL.md"])
        self.assertNotIn("overwriting", out)
        self.assertIn(NOTE.format(SKILL), out)
        m = self.manifest()
        self.assertEqual(m["files"][SKILL], recorded)        # record kept
        self.assertNotIn(SKILL, m.get("retired", {}))       # not retired
        # --prune cannot reach it either: still planned, never a candidate.
        rc, out, err = self.sync(prune=True)
        self.assertEqual(rc, 0, err)
        self.assertNotIn("pruned", out)
        self.assertEqual((other / "SKILL.md").read_text(), "OTHER CHECKOUT, EDITED\n")
        self.assertEqual(self.manifest()["files"][SKILL], recorded)

    def test_symlink_loop_is_skipped_without_traceback(self):
        a = self.consumer / ".kiro" / "steering"
        a.parent.mkdir(parents=True)
        a.symlink_to(self.consumer / ".kiro" / "loop", target_is_directory=True)
        (self.consumer / ".kiro" / "loop").symlink_to(a, target_is_directory=True)
        rc, out, err = self.sync()
        self.assertEqual(rc, 0, err)
        self.assertIn(NOTE.format(STEER), out)

    def test_check_notes_skipped_path_and_does_not_call_it_missing(self):
        self.sync()
        (self.consumer / STEER).unlink()
        self.link(STEER, self.out_dir / "leaked.md")
        rc, out, _ = self.sync(check=True)
        self.assertEqual(rc, 0, out)
        self.assertIn(NOTE.format(STEER), out)
        self.assertNotIn("missing", out)
        self.assertEqual(self.outside_files(), [])

    def test_unrecorded_skip_is_pre_existing_once_the_link_is_gone(self):
        dst = self.link(SKILL, self.out_dir / "leaked.md")
        self.sync()
        dst.unlink()
        dst.write_text("CONSUMER-OWNED\n")
        rc, out, _ = self.sync()
        self.assertEqual(rc, 0)
        self.assertIn(f"pre-existing file overwritten: {SKILL}", out)
        self.assertEqual((self.consumer / (SKILL + ".pre-sdlc.bak")).read_text(),
                         "CONSUMER-OWNED\n")

    # ---- still allowed ----------------------------------------------------

    def test_dest_dir_symlinked_inside_the_repo_keeps_working(self):
        shared = self.consumer / "shared" / "steering"
        shared.mkdir(parents=True)
        self.link(".kiro/steering", Path("../shared/steering"), is_dir=True)
        rc, out, err = self.sync()
        self.assertEqual(rc, 0, err)
        self.assertEqual((shared / "rules.md").read_text(), "# steering rules\n")
        self.assertNotIn("resolves outside", out)
        self.assertIn(STEER, self.manifest()["files"])
        rc, out, _ = self.sync(check=True)
        self.assertEqual(rc, 0, out)
        rc, out, _ = self.sync()
        self.assertIn("up to date", out)

    def test_link_to_the_pack_source_is_a_silent_no_op(self):
        src = self.pack_root / "skills" / "demo"
        self.link(".claude/skills/demo", src, is_dir=True)
        before = (src / "SKILL.md").read_bytes()
        rc, out, err = self.sync()
        self.assertEqual(rc, 0, err)
        self.assertNotIn("resolves outside", out)
        self.assertEqual((src / "SKILL.md").read_bytes(), before)
        self.assertEqual(sorted(p.name for p in src.iterdir()), ["SKILL.md"])
        self.assertIn(SKILL, self.manifest()["files"])
        rc, out, _ = self.sync(check=True)
        self.assertEqual(rc, 0, out)


if __name__ == "__main__":
    unittest.main()
