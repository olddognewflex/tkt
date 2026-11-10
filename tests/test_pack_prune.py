"""TKT-22: `tkt sync-pack --prune` removes files a previous sync wrote that
the current selection no longer ships, behind safety guards.

Covered: only old-manifest paths are touched; the base layer, AGENTS.md and
the manifest are never pruned; `_safe_rel` refuses paths that could leave
the consumer tree; a locally modified file is kept and reported; empty
parent dirs are removed, never the consumer root; a plain sync carries
retired files forward under `retired`; `--check --prune` writes nothing.
Aliases of a live path (`./x`, `a//b`, a case-only difference, a symlinked
dir inside the tree) are never pruned; an unreadable entry or a failed
unlink never crashes a sync.

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
        # Dirs too (as None), so a stray mkdir is caught.
        return {p.relative_to(self.consumer).as_posix():
                (p.read_bytes() if p.is_file() else None)
                for p in self.consumer.rglob("*")}

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


def case_insensitive(d: Path) -> bool:
    probe = d / "CaseProbe"
    probe.write_text("x")
    try:
        return (d / "cASEpROBE").exists()
    finally:
        probe.unlink()


def is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


class Aliases(TempPack):
    PLANNED = ".claude/skills/demo/SKILL.md"

    def plant_retired(self, *aliases: str) -> Path:
        self.sync()
        planned = self.consumer / self.PLANNED
        m = self.manifest()
        m["retired"] = {a: sha(planned) for a in aliases}
        self.write_manifest(m)
        return planned

    def test_non_canonical_aliases_of_a_planned_file_survive(self):
        aliases = ("./" + self.PLANNED, ".claude//skills/demo/SKILL.md",
                   self.PLANNED + "/", ".claude/./skills/demo/SKILL.md")
        planned = self.plant_retired(*aliases)
        _, out = self.sync(prune=True)
        self.assertTrue(planned.is_file())
        self.assertNotIn("pruned:", out)
        for a in aliases:
            self.assertIn(f"skipped unsafe manifest path: {a}", out)
        self.assertNotIn("retired", self.manifest())

    def test_case_alias_of_a_planned_file_survives(self):
        if not case_insensitive(self.consumer):
            self.skipTest("needs a case-insensitive filesystem")
        # Pack dirs match case-exactly, so a case alias is refused before
        # the identity check; the case-only rename test covers that check.
        alias = ".CLAUDE/skills/demo/SKILL.md"
        planned = self.plant_retired(alias)
        _, out = self.sync(prune=True)
        self.assertTrue(planned.is_file())
        self.assertIn(f"skipped (not a pack path): {alias}", out)
        self.assertNotIn("retired", self.manifest())

    def test_case_only_rename_in_the_pack_keeps_the_live_file(self):
        if not case_insensitive(self.consumer):
            self.skipTest("needs a case-insensitive filesystem")
        self.sync()
        (self.pack_root / "skills" / "extra").rename(self.pack_root / "skills" / "Extra")
        _, out = self.sync(prune=True)
        self.assertNotIn("pruned:", out)
        self.assertTrue((self.consumer / ".claude/skills/Extra/SKILL.md").is_file())
        self.assertTrue((self.consumer / ".kiro/skills/Extra/SKILL.md").is_file())
        rc, _ = self.sync(check=True)
        self.assertEqual(rc, 0)

    def test_plain_sync_does_not_retire_a_case_alias(self):
        if not case_insensitive(self.consumer):
            self.skipTest("needs a case-insensitive filesystem")
        self.sync()
        (self.pack_root / "skills" / "extra").rename(self.pack_root / "skills" / "Extra")
        self.sync()
        self.assertNotIn("retired", self.manifest())


@unittest.skipUnless(hasattr(os, "symlink"), "needs os.symlink")
class SymlinkedDirsInsideTheTree(TempPack):
    def link(self, at: str, to: str) -> None:
        path = self.consumer / at
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(to, path)
        except OSError as e:
            self.skipTest(f"cannot create symlinks: {e}")

    def test_link_into_the_base_layer_never_prunes_base_files(self):
        # A common layout: Claude's skills dir is a link to the base mirror.
        self.link(".claude/skills", "../.agents/skills")
        self.sync()
        base = self.consumer / ".agents/skills/extra/SKILL.md"
        self.assertTrue(base.is_file())
        shutil.rmtree(self.pack_root / "skills" / "extra")
        _, out = self.sync(prune=True)
        self.assertTrue(base.is_file())
        self.assertNotIn("pruned: .claude/skills/extra/SKILL.md", out)
        self.assertIn("skipped unsafe manifest path: .claude/skills/extra/SKILL.md", out)

    def test_reverse_link_from_the_base_layer_never_prunes_base_files(self):
        # `.agents/skills` links to Claude's dir: the base copy and the
        # Claude copy are one file. Drop the manifest's record of the base
        # path (as an older manifest would), so only the base-layer walk
        # can see it.
        (self.consumer / ".claude/skills").mkdir(parents=True)
        self.link(".agents/skills", "../.claude/skills")
        self.sync()
        base = self.consumer / ".agents/skills/extra/SKILL.md"
        m = self.manifest()
        del m["files"][".agents/skills/extra/SKILL.md"]
        self.write_manifest(m)
        shutil.rmtree(self.pack_root / "skills" / "extra")
        _, out = self.sync(prune=True)
        self.assertTrue(base.is_file())
        self.assertIn("skipped (same file as a live path): "
                      ".claude/skills/extra/SKILL.md", out)

    def test_per_skill_link_from_the_base_layer_never_prunes_base_files(self):
        # Only one skill dir is a link, and its base path is recorded.
        (self.consumer / ".claude/skills/extra").mkdir(parents=True)
        self.link(".agents/skills/extra", "../../.claude/skills/extra")
        self.sync()
        base = self.consumer / ".agents/skills/extra/SKILL.md"
        shutil.rmtree(self.pack_root / "skills" / "extra")
        _, out = self.sync(prune=True)
        self.assertTrue(base.is_file())
        self.assertIn("skipped (same file as a live path): "
                      ".claude/skills/extra/SKILL.md", out)

    def test_unrecorded_link_in_the_base_layer_protects_its_target(self):
        # A link the consumer adds under a base dir, which no manifest
        # records: only following it in the walk can protect the file.
        self.sync()
        self.link(".agents/skills/local-extra", "../../.claude/skills/extra")
        shutil.rmtree(self.pack_root / "skills" / "extra")
        _, out = self.sync(prune=True)
        self.assertTrue(self.extra.is_file())
        self.assertTrue((self.consumer / ".agents/skills/local-extra/SKILL.md").is_file())
        self.assertIn("skipped (same file as a live path): "
                      ".claude/skills/extra/SKILL.md", out)

    def test_link_loop_in_the_base_layer_ends(self):
        self.sync()
        self.link(".agents/skills/loop", ".")
        shutil.rmtree(self.pack_root / "skills" / "extra")
        _, out = self.sync(prune=True)
        self.assertIn("pruned: .claude/skills/extra/SKILL.md", out)

    def test_link_leading_outside_the_tree_is_not_walked(self):
        self.sync()
        outside = self.tmp / "outside"
        outside.mkdir()
        self.link(".agents/skills/out", str(outside))
        walked = []
        real_scandir = os.scandir

        def spy(path, *a, **kw):
            walked.append(Path(path))
            return real_scandir(path, *a, **kw)

        shutil.rmtree(self.pack_root / "skills" / "extra")
        with mock.patch.object(pack.os, "scandir", spy):
            _, out = self.sync(prune=True)
        self.assertIn("pruned: .claude/skills/extra/SKILL.md", out)
        self.assertTrue(walked)
        self.assertNotIn(outside.resolve(), [w.resolve() for w in walked])

    def test_link_to_a_planned_tree_never_prunes_planned_files(self):
        self.sync()
        planned = self.consumer / ".claude/skills/demo/SKILL.md"
        self.link("alias", ".claude/skills")
        m = self.manifest()
        m["retired"] = {"alias/demo/SKILL.md": sha(planned)}
        self.write_manifest(m)
        _, out = self.sync(prune=True)
        self.assertTrue(planned.is_file())
        self.assertIn("skipped unsafe manifest path: alias/demo/SKILL.md", out)


class Robustness(TempPack):
    def lock(self, d: Path, mode: int) -> None:
        if is_root() or not hasattr(os, "geteuid"):
            self.skipTest("permission bits are not enforced here")
        d.chmod(mode)
        self.addCleanup(d.chmod, 0o755)

    def test_unreadable_entry_never_crashes_a_sync(self):
        self.retire_extra()
        self.lock(self.extra.parent, 0o000)
        rc, _ = self.sync()
        self.assertEqual(rc, 0)
        self.assertIn(".claude/skills/extra/SKILL.md", self.manifest()["retired"])
        rc, out = self.sync(prune=True)
        self.assertEqual(rc, 0)
        self.assertIn("kept (unreadable): .claude/skills/extra/SKILL.md", out)

    def test_unreadable_base_dir_keeps_every_candidate(self):
        # Prune cannot tell whether a file in an unreadable base dir links
        # to a candidate, so it keeps them all rather than guess.
        self.retire_extra()
        mine = self.consumer / ".agents/skills/mine"
        mine.mkdir()
        self.lock(mine, 0o000)
        rc, out = self.sync(prune=True)
        self.assertEqual(rc, 0)
        self.assertTrue(self.extra.is_file())
        self.assertIn("kept (could not verify): .claude/skills/extra/SKILL.md", out)
        self.assertIn(".claude/skills/extra/SKILL.md", self.manifest()["retired"])
        rc, out = self.sync(check=True, prune=True)
        self.assertIn("kept, could not verify (", out)

    def test_failed_unlink_is_reported_and_stays_retired(self):
        self.retire_extra()
        self.lock(self.extra.parent, 0o555)
        rc, out = self.sync(prune=True)
        self.assertEqual(rc, 0)
        self.assertTrue(self.extra.is_file())
        self.assertIn("1 could NOT be pruned", out)
        self.assertIn(".claude/skills/extra/SKILL.md", self.manifest()["retired"])

    def test_dir_where_a_file_was_is_not_a_file(self):
        self.retire_extra()
        self.extra.unlink()
        self.extra.mkdir()
        _, out = self.sync(prune=True)
        self.assertTrue(self.extra.is_dir())
        self.assertIn("skipped (not a file): .claude/skills/extra/SKILL.md", out)
        self.assertNotIn(".claude/skills/extra/SKILL.md",
                         self.manifest().get("retired", {}))

    def test_changed_between_classify_and_unlink_is_kept(self):
        self.retire_extra()
        real = pack._classify_retired

        def classify_then_edit(*a, **kw):
            res = real(*a, **kw)
            self.extra.write_text("edited at the last moment\n")
            return res

        with mock.patch.object(pack, "_classify_retired", classify_then_edit):
            _, out = self.sync(prune=True)
        self.assertTrue(self.extra.is_file())
        self.assertIn("kept (locally modified): .claude/skills/extra/SKILL.md", out)

    def after_classify(self, change):
        real = pack._classify_retired

        def wrapped(*a, **kw):
            res = real(*a, **kw)
            change()
            return res
        return mock.patch.object(pack, "_classify_retired", wrapped)

    def test_vanished_before_unlink_is_not_a_failure(self):
        self.retire_extra()
        with self.after_classify(self.extra.unlink):
            rc, out = self.sync(prune=True)
        self.assertEqual(rc, 0)
        self.assertNotIn("could NOT", out)
        self.assertNotIn(".claude/skills/extra/SKILL.md",
                         self.manifest().get("retired", {}))

    def test_turned_into_a_dir_before_unlink_is_labelled_not_a_file(self):
        self.retire_extra()

        def to_dir():
            self.extra.unlink()
            self.extra.mkdir()
        with self.after_classify(to_dir):
            _, out = self.sync(prune=True)
        self.assertTrue(self.extra.is_dir())
        self.assertIn("skipped (not a file): .claude/skills/extra/SKILL.md", out)
        self.assertNotIn("locally modified", out)

    def test_turned_into_a_symlink_before_unlink_is_kept(self):
        self.retire_extra()
        other = self.tmp / "elsewhere.md"
        other.write_text(skill_md("extra"))

        def to_link():
            self.extra.unlink()
            try:
                os.symlink(other, self.extra)
            except OSError as e:
                self.skipTest(f"cannot create symlinks: {e}")
        with self.after_classify(to_link):
            _, out = self.sync(prune=True)
        self.assertTrue(other.is_file())
        self.assertIn("kept (symlink): .claude/skills/extra/SKILL.md", out)

    def test_lstat_permission_error_is_unreadable_on_every_python(self):
        # Newer Pythons hide EACCES in Path.exists(); lstat must surface it.
        self.retire_extra()
        real = os.lstat
        target = os.fspath(self.extra.resolve())

        def deny(path, *a, **kw):
            if os.fspath(path) == target:
                raise PermissionError(13, "Permission denied", target)
            return real(path, *a, **kw)

        with mock.patch.object(pack.os, "lstat", deny):
            rc, out = self.sync(prune=True)
        self.assertEqual(rc, 0)
        self.assertTrue(self.extra.is_file())
        self.assertIn("kept (unreadable): .claude/skills/extra/SKILL.md", out)
        self.assertIn(".claude/skills/extra/SKILL.md", self.manifest()["retired"])


class PackOwnedOnly(TempPack):
    def test_tampered_entries_outside_pack_dirs_are_never_deleted(self):
        self.sync()
        src = self.consumer / "src/auth.py"
        src.parent.mkdir()
        src.write_text("secret = 1\n")
        head = self.consumer / ".git/HEAD"
        head.parent.mkdir()
        head.write_text("ref: refs/heads/main\n")
        inner_git = self.consumer / ".claude/skills/.git/config"
        inner_git.parent.mkdir(parents=True)
        inner_git.write_text("[core]\n")
        m = self.manifest()
        m["retired"] = {r: sha(self.consumer / r) for r in
                        ("src/auth.py", ".git/HEAD", ".claude/skills/.git/config")}
        self.write_manifest(m)
        _, out = self.sync(prune=True)
        for f in (src, head, inner_git):
            self.assertTrue(f.is_file(), f)
        for r in ("src/auth.py", ".git/HEAD", ".claude/skills/.git/config"):
            self.assertIn(f"skipped (not a pack path): {r}", out)
        self.assertNotIn("retired", self.manifest())

    def test_tampered_base_layer_never_walks_outside_the_target(self):
        self.retire_extra()
        m = self.manifest()
        m["base_layer"] = m["base_layer"] + ["/", "/usr/share", "../../x", "src"]
        self.write_manifest(m)
        self.assertEqual(pack._base_prefixes(m), pack._BASE_DESTS)
        roots = []
        real_walk = pack._walk_ids

        def spy(root, *a, **kw):
            roots.append(Path(root))
            return real_walk(root, *a, **kw)

        with mock.patch.object(pack, "_walk_ids", spy):
            self.sync(check=True, prune=True)
            self.sync()
        self.assertTrue(roots)
        consumer = self.consumer.resolve()
        for r in roots:
            self.assertIn(consumer, r.resolve().parents, r)


class ManifestEdges(TempPack):
    def test_empty_retired_key_causes_no_rewrite(self):
        self.sync()
        m = self.manifest()
        m["retired"] = {}
        self.write_manifest(m)
        before = self.manifest_path.read_bytes()
        _, out = self.sync()
        self.assertEqual(self.manifest_path.read_bytes(), before)
        self.assertIn("up to date", out)

    def test_reshipped_retired_file_is_not_reported_as_preexisting(self):
        self.retire_extra()
        d = self.pack_root / "skills" / "extra"
        d.mkdir()
        (d / "SKILL.md").write_text(skill_md("extra") + "\nnew line\n")
        _, out = self.sync()
        self.assertNotIn("pre-existing file overwritten", out)
        self.assertNotIn("base_retired", self.manifest())

    def test_stale_base_files_are_tracked_but_never_pruned(self):
        self.retire_extra()
        base = ".agents/skills/extra/SKILL.md"
        self.assertIn(base, self.manifest()["base_retired"])
        self.assertNotIn(base, self.manifest().get("retired", {}))
        self.sync(prune=True)
        self.assertTrue((self.consumer / base).is_file())
        (self.consumer / base).unlink()
        self.sync()
        self.assertNotIn("base_retired", self.manifest())

    def test_non_string_sha_is_kept_never_pruned(self):
        self.retire_extra()
        m = self.manifest()
        m["retired"][".claude/skills/extra/SKILL.md"] = 5
        self.write_manifest(m)
        _, out = self.sync(prune=True)
        self.assertTrue(self.extra.is_file())
        self.assertIn("kept (locally modified): .claude/skills/extra/SKILL.md", out)

    def test_hint_counts_only_files_prune_would_remove(self):
        self.retire_extra()
        self.extra.write_text("edited\n")
        (self.consumer / ".kiro/skills/extra/SKILL.md").write_text("edited\n")
        _, out = self.sync()
        self.assertNotIn("re-run with --prune", out)


class Cli(TempPack):
    def test_prune_flag_reaches_sync_pack(self):
        self.retire_extra()
        with contextlib.redirect_stdout(io.StringIO()):
            rc = cli.main(["sync-pack", "--prune", "--dir", str(self.consumer)])
        self.assertEqual(rc, 0)
        self.assertFalse(self.extra.exists())


if __name__ == "__main__":
    unittest.main()
