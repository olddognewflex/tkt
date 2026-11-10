"""TKT-22: `tkt sync-pack --prune` removes files a previous sync wrote that
the current selection no longer ships, behind safety guards.

Covered: only old-manifest paths are touched; the base layer, AGENTS.md and
the manifest are never pruned; `_safe_rel` refuses paths that could leave
the consumer tree; a locally modified file is kept and reported; empty
parent dirs are removed, never the consumer root; a plain sync carries
retired files forward under `retired`; `--check --prune` writes nothing.

Every case points `core.pack.PACK_ROOT` at a temp pack. Offline.

    python3 -m unittest tests.test_pack_prune
"""
import contextlib
import hashlib
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

from core import cli, pack  # noqa: E402


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def skill_md(name: str) -> str:
    return f"---\nname: {name}\ndescription: '{name} skill.'\n---\n\n# {name}\n"


class TempPack(unittest.TestCase):
    """A temp pack with two skills (`extra` is the one tests retire), a
    workflow, an enforcement dir and a Gemini command."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.pack_root = self.tmp / "pack"
        for name in ("demo", "extra"):
            d = self.pack_root / "skills" / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(skill_md(name))
        wf = self.pack_root / ".agents" / "workflows"
        wf.mkdir(parents=True)
        (wf / "demo.md").write_text("workflow\n")
        enf = self.pack_root / "enforcement"
        enf.mkdir()
        (enf / "rules.json").write_text("{}\n")
        gem = self.pack_root / ".gemini" / "commands"
        gem.mkdir(parents=True)
        (gem / "x.md").write_text("gemini command\n")
        self.consumer = self.tmp / "consumer"
        self.consumer.mkdir()
        patcher = mock.patch.object(pack, "PACK_ROOT", self.pack_root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sync(self, harnesses=None, check=False, prune=False) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            rc = pack.sync_pack(str(self.consumer), all_harnesses=False,
                                check=check, harnesses=harnesses, prune=prune)
        return rc, out.getvalue()

    @property
    def manifest_path(self) -> Path:
        return self.consumer / pack.MANIFEST_REL

    def manifest(self) -> dict:
        return json.loads(self.manifest_path.read_text())

    def write_manifest(self, m: dict) -> None:
        self.manifest_path.write_text(json.dumps(m, indent=2, sort_keys=True) + "\n")

    def retire_extra(self):
        """Sync, then drop `extra` from the pack and sync again without
        --prune: its copies become retired."""
        self.sync()
        shutil.rmtree(self.pack_root / "skills" / "extra")
        return self.sync()

    @property
    def extra(self) -> Path:
        return self.consumer / ".claude/skills/extra/SKILL.md"


class Retirement(TempPack):
    def test_plain_sync_keeps_retired_files_and_records_them(self):
        _, out = self.retire_extra()
        self.assertTrue(self.extra.is_file())
        retired = self.manifest()["retired"]
        self.assertIn(".claude/skills/extra/SKILL.md", retired)
        self.assertIn(".kiro/skills/extra/SKILL.md", retired)
        self.assertEqual(retired[".claude/skills/extra/SKILL.md"], sha(self.extra))
        self.assertIn("re-run with --prune", out)

    def test_retired_files_carry_forward_to_a_later_prune(self):
        self.retire_extra()
        self.sync()                       # another plain sync: still recorded
        self.assertIn(".claude/skills/extra/SKILL.md", self.manifest()["retired"])
        rc, out = self.sync(prune=True)
        self.assertEqual(rc, 0)
        self.assertFalse(self.extra.exists())
        self.assertFalse((self.consumer / ".kiro/skills/extra").exists())
        self.assertIn("pruned: .claude/skills/extra/SKILL.md", out)
        self.assertNotIn("retired", self.manifest())

    def test_dropping_a_harness_retires_its_files(self):
        self.sync(harnesses=["gemini"])
        cmd = self.consumer / ".gemini/commands/x.md"
        self.assertTrue(cmd.is_file())
        m = self.manifest()
        m["harnesses"] = [h for h in m["harnesses"] if h != "gemini"]
        self.write_manifest(m)
        _, out = self.sync(prune=True)
        self.assertIn("pruned: .gemini/commands/x.md", out)
        # Empty parents climbed and removed, the consumer root kept.
        self.assertFalse((self.consumer / ".gemini").exists())
        self.assertTrue(self.consumer.is_dir())

    def test_replanned_file_leaves_retired(self):
        self.retire_extra()
        d = self.pack_root / "skills" / "extra"
        d.mkdir()
        (d / "SKILL.md").write_text(skill_md("extra"))
        self.sync()
        m = self.manifest()
        self.assertNotIn("retired", m)
        self.assertIn(".claude/skills/extra/SKILL.md", m["files"])

    def test_vanished_retired_file_is_dropped(self):
        self.retire_extra()
        shutil.rmtree(self.consumer / ".claude/skills/extra")
        self.sync()
        self.assertNotIn(".claude/skills/extra/SKILL.md",
                         self.manifest().get("retired", {}))


class Guards(TempPack):
    def test_unrecorded_consumer_file_is_never_touched(self):
        self.retire_extra()
        mine = self.consumer / ".claude/skills/extra/notes.md"
        mine.write_text("my own notes\n")
        self.sync(prune=True)
        self.assertTrue(mine.is_file())
        self.assertFalse(self.extra.exists())
        # Its dir is not empty, so it stays.
        self.assertTrue(mine.parent.is_dir())

    def test_base_layer_is_never_pruned(self):
        _, _ = self.retire_extra()
        base = self.consumer / ".agents/skills/extra/SKILL.md"
        self.assertTrue(base.is_file())
        self.assertNotIn(".agents/skills/extra/SKILL.md",
                         self.manifest().get("retired", {}))
        self.sync(prune=True)
        self.assertTrue(base.is_file())

    def test_base_layer_recorded_by_an_old_manifest_is_protected(self):
        self.sync()
        old_base = self.consumer / "docs/old-base/a.md"
        old_base.parent.mkdir(parents=True)
        old_base.write_text("old base\n")
        m = self.manifest()
        m["base_layer"] = m["base_layer"] + ["docs/old-base"]
        m["files"]["docs/old-base/a.md"] = sha(old_base)
        self.write_manifest(m)
        self.sync(prune=True)
        self.assertTrue(old_base.is_file())

    def test_agents_md_and_manifest_are_never_pruned(self):
        self.sync()
        agents = self.consumer / "AGENTS.md"
        m = self.manifest()
        tampered = {
            "AGENTS.md": sha(agents),
            "./agents.md": sha(agents),
            pack.AGENTS_MANIFEST_KEY: sha(agents),
            pack.MANIFEST_REL: sha(self.manifest_path),
        }
        m["files"].update(tampered)
        m["retired"] = dict(tampered)
        self.write_manifest(m)
        rc, out = self.sync(prune=True)
        self.assertEqual(rc, 0)
        self.assertTrue(agents.is_file())
        self.assertTrue(self.manifest_path.is_file())
        self.assertNotIn("pruned:", out)
        self.assertNotIn("retired", self.manifest())

    def test_locally_modified_file_is_kept_and_reported(self):
        self.retire_extra()
        self.extra.write_text("edited by hand\n")
        rc, out = self.sync(prune=True)
        self.assertEqual(rc, 0)
        self.assertTrue(self.extra.is_file())
        self.assertIn("kept (locally modified): .claude/skills/extra/SKILL.md", out)
        # Still retired, so it keeps being reported until resolved.
        self.assertIn(".claude/skills/extra/SKILL.md", self.manifest()["retired"])

    def test_second_prune_is_up_to_date(self):
        self.retire_extra()
        self.sync(prune=True)
        _, out = self.sync(prune=True)
        self.assertIn("up to date", out)


@unittest.skipUnless(hasattr(os, "symlink"), "needs os.symlink")
class Symlinks(TempPack):
    def setUp(self):
        super().setUp()
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        self.victim = self.outside / "f.md"
        self.victim.write_text("not yours\n")
        try:
            os.symlink(self.outside, self.consumer / "link")
        except OSError as e:  # e.g. Windows without the privilege
            self.skipTest(f"cannot create symlinks: {e}")

    def test_safe_rel_refuses_a_symlinked_parent_outside_the_tree(self):
        self.assertIsNone(pack._safe_rel(self.consumer.resolve(), "link/f.md"))

    def test_prune_never_follows_a_symlinked_parent(self):
        self.sync()
        m = self.manifest()
        m["retired"] = {"link/f.md": sha(self.victim)}
        self.write_manifest(m)
        _, out = self.sync(prune=True)
        self.assertTrue(self.victim.is_file())
        self.assertIn("skipped unsafe manifest path: link/f.md", out)
        self.assertNotIn("retired", self.manifest())

    def test_leaf_symlink_is_kept(self):
        self.retire_extra()
        self.extra.unlink()
        os.symlink(self.victim, self.extra)
        _, out = self.sync(prune=True)
        self.assertTrue(self.extra.is_symlink())
        self.assertTrue(self.victim.is_file())
        self.assertIn("kept (symlink): .claude/skills/extra/SKILL.md", out)


class SafeRel(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_rejects_paths_that_leave_the_tree(self):
        for bad in ("", None, 7, "/etc/passwd", "..", "../x", "a/../../x",
                    "a/..", "\\x", "\\\\server\\share\\x", "C:/x", "c:x",
                    "a\\..\\..\\b", "a\0b", "."):
            with self.subTest(bad=bad):
                self.assertIsNone(pack._safe_rel(self.tmp, bad))

    def test_accepts_a_plain_relative_path(self):
        self.assertEqual(pack._safe_rel(self.tmp, ".claude/skills/a/SKILL.md"),
                         self.tmp / ".claude/skills/a/SKILL.md")

    def test_tampered_absolute_path_is_never_deleted(self):
        victim = self.tmp / "victim.md"
        victim.write_text("x\n")
        consumer = self.tmp / "c"
        consumer.mkdir()
        res = pack._classify_retired(consumer, {str(victim): sha(victim),
                                                "../victim.md": sha(victim)})
        self.assertEqual(res["prune"], [])
        self.assertEqual(len(res["unsafe"]), 2)


class RemoveEmptyParents(unittest.TestCase):
    def test_stops_at_the_root_and_at_non_empty_dirs(self):
        root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, root, True)
        f = root / "a" / "b" / "f.md"
        f.parent.mkdir(parents=True)
        f.write_text("x\n")
        (root / "a" / "keep.md").write_text("x\n")
        f.unlink()
        pack._remove_empty_parents(f, root)
        self.assertFalse((root / "a" / "b").exists())
        self.assertTrue((root / "a").is_dir())
        (root / "a" / "keep.md").unlink()
        pack._remove_empty_parents(root / "a" / "keep.md", root)
        self.assertFalse((root / "a").exists())
        self.assertTrue(root.is_dir())


class CheckPrune(TempPack):
    def snapshot(self) -> dict:
        return {p.relative_to(self.consumer).as_posix(): p.read_bytes()
                for p in self.consumer.rglob("*") if p.is_file()}

    def test_lists_and_writes_nothing(self):
        self.retire_extra()
        before = self.snapshot()
        rc, out = self.sync(check=True, prune=True)
        self.assertEqual(rc, 1)
        self.assertIn("would prune (", out)
        self.assertIn(".claude/skills/extra/SKILL.md", out)
        self.assertEqual(self.snapshot(), before)

    def test_reports_kept_files(self):
        self.retire_extra()
        self.extra.write_text("edited\n")
        rc, out = self.sync(check=True, prune=True)
        self.assertIn("kept, locally modified (", out)

    def test_exit_zero_when_nothing_to_prune(self):
        self.sync()
        rc, out = self.sync(check=True, prune=True)
        self.assertEqual(rc, 0)
        self.assertNotIn("would prune", out)

    def test_check_without_prune_is_unchanged(self):
        self.retire_extra()
        rc, out = self.sync(check=True)
        self.assertEqual(rc, 0)
        self.assertNotIn("would prune", out)


class NoChurn(TempPack):
    def test_manifest_without_retired_stays_byte_identical(self):
        self.sync()
        before = self.manifest_path.read_bytes()
        _, out = self.sync()
        self.assertEqual(self.manifest_path.read_bytes(), before)
        self.assertNotIn("retired", self.manifest())
        self.assertIn("up to date", out)
        _, out = self.sync(prune=True)
        self.assertEqual(self.manifest_path.read_bytes(), before)
        self.assertIn("up to date", out)


class Cli(TempPack):
    def test_prune_flag_reaches_sync_pack(self):
        self.retire_extra()
        with contextlib.redirect_stdout(io.StringIO()):
            rc = cli.main(["sync-pack", "--prune", "--dir", str(self.consumer)])
        self.assertEqual(rc, 0)
        self.assertFalse(self.extra.exists())


if __name__ == "__main__":
    unittest.main()
