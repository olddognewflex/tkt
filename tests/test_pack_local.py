"""TKT-25: `sync-pack` mirrors a consumer's own `.claude/skills/<name>` dirs
into every verbatim skill surface the selection installs.

A local skill is a `.claude/skills/<name>` dir holding a `SKILL.md` under a
name the pack does not own. It is copied byte-for-byte into the surfaces
`_skill_surfaces` derives from the harness registry (`.agents/skills`
always, `.kiro/skills`, `.cursor/skills`, ...) and never into translated
ones such as Copilot's `.github/prompts`.

Every case points `core.pack.PACK_ROOT` at a temp pack. Offline.

    python3 -m unittest tests.test_pack_local
"""
import contextlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import pack  # noqa: E402

PACK_SKILL = "---\nname: demo\ndescription: 'Pack demo skill.'\n---\n\n# Demo\n"
LOCAL_SKILL = "---\nname: mine\ndescription: 'A consumer skill.'\n---\n\n# Mine\n"


def is_exec(p: Path) -> bool:
    return bool(p.stat().st_mode & stat.S_IXUSR)


class LocalSkillCase(unittest.TestCase):
    """A temp pack with one skill (`demo`), the pack-internal `sync-skills`,
    a Copilot prompt and a stored Cursor skill; a consumer with one local
    skill, `mine`, carrying a reference file and an executable script."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = self.pack_root = self.tmp / "pack"
        (p / "skills" / "demo").mkdir(parents=True)
        (p / "skills" / "demo" / "SKILL.md").write_text(PACK_SKILL)
        (p / "skills" / "sync-skills").mkdir()
        (p / "skills" / "sync-skills" / "SKILL.md").write_text("---\nname: sync-skills\n---\n")
        (p / ".github" / "prompts").mkdir(parents=True)
        (p / ".github" / "prompts" / "demo.prompt.md").write_text("prompt\n")
        (p / ".cursor" / "skills" / "demo").mkdir(parents=True)
        (p / ".cursor" / "skills" / "demo" / "SKILL.md").write_text("cursor demo\n")
        self.consumer = self.tmp / "consumer"
        self.local = self.consumer / ".claude" / "skills" / "mine"
        (self.local / "ref").mkdir(parents=True)
        (self.local / "SKILL.md").write_text(LOCAL_SKILL)
        (self.local / "ref" / "notes.md").write_text("notes\n")
        (self.local / "run.sh").write_text("#!/bin/sh\necho hi\n")
        (self.local / "run.sh").chmod(0o755)
        (self.local / ".DS_Store").write_bytes(b"junk")
        (self.local / "SKILL.md.pre-sdlc.bak").write_text("old backup\n")
        patcher = mock.patch.object(pack, "PACK_ROOT", self.pack_root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sync(self, harnesses=None, check=False, prune=False) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = pack.sync_pack(str(self.consumer), all_harnesses=False,
                                check=check, harnesses=harnesses, prune=prune)
        return rc, out.getvalue()

    def manifest(self) -> dict:
        return json.loads((self.consumer / pack.MANIFEST_REL).read_text())


class Surfaces(unittest.TestCase):
    def test_derived_from_registry(self):
        self.assertEqual(pack._skill_surfaces(list(pack._HARNESSES)),
                         [".agents/skills", ".kiro/skills", ".cursor/skills"])

    def test_default_selection(self):
        self.assertEqual(pack._skill_surfaces(list(pack._DEFAULT_HARNESSES)),
                         [".agents/skills", ".kiro/skills"])

    def test_source_and_translated_surfaces_excluded(self):
        surfaces = pack._skill_surfaces(list(pack._HARNESSES))
        self.assertNotIn(".claude/skills", surfaces)
        self.assertNotIn(".github/prompts", surfaces)
        self.assertNotIn(".cursor/commands", surfaces)

    def test_new_harness_skill_surface_is_picked_up(self):
        reg = dict(pack._HARNESSES)
        reg["future"] = ("Future", [(".future/skills", ".future/skills"),
                                    (".future/prompts", ".future/prompts")])
        with mock.patch.object(pack, "_HARNESSES", reg):
            self.assertEqual(pack._skill_surfaces(["future"]),
                             [".agents/skills", ".future/skills"])


class Mirror(LocalSkillCase):
    def test_default_sync_mirrors_into_verbatim_surfaces_only(self):
        rc, out = self.sync()
        self.assertEqual(rc, 0, out)
        c = self.consumer
        for surface in (".agents/skills", ".kiro/skills"):
            for rel in ("SKILL.md", "ref/notes.md", "run.sh"):
                self.assertEqual((c / surface / "mine" / rel).read_bytes(),
                                 (self.local / rel).read_bytes(), f"{surface}/{rel}")
            self.assertFalse((c / surface / "mine" / ".DS_Store").exists())
            self.assertFalse((c / surface / "mine" / "SKILL.md.pre-sdlc.bak").exists())
        self.assertFalse(any("mine" in p.name for p in (c / ".github/prompts").iterdir()))
        self.assertFalse((c / ".cursor").exists())
        self.assertIn("local skills mirrored: mine", out)

    def test_cursor_selection_adds_cursor_surface(self):
        self.sync(harnesses=["cursor"])
        self.assertEqual((self.consumer / ".cursor/skills/mine/SKILL.md").read_text(),
                         LOCAL_SKILL)
        # The pack's own stored Cursor skill is still the pack's.
        self.assertEqual((self.consumer / ".cursor/skills/demo/SKILL.md").read_text(),
                         "cursor demo\n")

    def test_exec_bit_carried(self):
        self.sync()
        self.assertTrue(is_exec(self.consumer / ".kiro/skills/mine/run.sh"))
        self.assertFalse(is_exec(self.consumer / ".kiro/skills/mine/SKILL.md"))

    def test_manifest_records_copies_not_source(self):
        self.sync()
        files = self.manifest()["files"]
        self.assertIn(".agents/skills/mine/SKILL.md", files)
        self.assertIn(".kiro/skills/mine/run.sh", files)
        self.assertFalse(any(k.startswith(".claude/skills/mine") for k in files))

    def test_source_untouched(self):
        before = {p: p.read_bytes() for p in self.local.rglob("*") if p.is_file()}
        self.sync()
        after = {p: p.read_bytes() for p in self.local.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_second_run_up_to_date_and_manifest_unchanged(self):
        self.sync()
        before = (self.consumer / pack.MANIFEST_REL).read_bytes()
        rc, out = self.sync()
        self.assertEqual(rc, 0)
        self.assertIn("up to date", out)
        self.assertNotIn("local skills mirrored", out)
        self.assertEqual((self.consumer / pack.MANIFEST_REL).read_bytes(), before)

    def test_check_flags_a_stale_copy_and_resync_fixes_it(self):
        self.sync()
        rc, out = self.sync(check=True)
        self.assertEqual(rc, 0, out)
        self.assertIn("local skills: mine -> .agents/skills, .kiro/skills", out)
        (self.local / "SKILL.md").write_text(LOCAL_SKILL + "\nmore\n")
        rc, out = self.sync(check=True)
        self.assertEqual(rc, 1)
        self.assertIn(".kiro/skills/mine/SKILL.md", out)
        self.assertIn(".agents/skills/mine/SKILL.md", out)
        self.sync()
        self.assertEqual((self.consumer / ".kiro/skills/mine/SKILL.md").read_text(),
                         LOCAL_SKILL + "\nmore\n")
        self.assertEqual(self.sync(check=True)[0], 0)


class Ownership(LocalSkillCase):
    def test_pack_owned_name_is_not_mirrored(self):
        own = self.consumer / ".claude/skills/demo"
        own.mkdir()
        (own / "SKILL.md").write_text("consumer's demo\n")
        self.sync(harnesses=["cursor"])     # .claude/skills stays the consumer's
        for surface in (".agents/skills", ".cursor/skills"):
            self.assertNotEqual(
                (self.consumer / surface / "demo/SKILL.md").read_text(),
                "consumer's demo\n", surface)
        self.assertEqual((self.consumer / ".agents/skills/demo/SKILL.md").read_text(),
                         PACK_SKILL)

    def test_pack_internal_skill_is_not_mirrored(self):
        meta = self.consumer / ".claude/skills/sync-skills"
        meta.mkdir()
        (meta / "SKILL.md").write_text("linked meta skill\n")
        self.sync()
        self.assertFalse((self.consumer / ".agents/skills/sync-skills").exists())

    def test_retired_pack_skill_under_claude_is_not_local(self):
        # A previous sync wrote `.claude/skills/old`; the pack no longer ships it.
        self.sync()
        old = self.consumer / ".claude/skills/old"
        old.mkdir()
        (old / "SKILL.md").write_text("old pack skill\n")
        m = self.manifest()
        m["files"][".claude/skills/old/SKILL.md"] = pack._sha_bytes(b"old pack skill\n")
        (self.consumer / pack.MANIFEST_REL).write_text(json.dumps(m))
        self.sync()
        self.assertFalse((self.consumer / ".agents/skills/old").exists())
        # It is now retired, and still not local on the next run.
        self.assertIn(".claude/skills/old/SKILL.md", self.manifest()["retired"])
        self.sync()
        self.assertFalse((self.consumer / ".agents/skills/old").exists())

    def test_dirs_without_skill_md_and_hidden_dirs_ignored(self):
        (self.consumer / ".claude/skills/notes").mkdir()
        (self.consumer / ".claude/skills/notes/README.md").write_text("x\n")
        (self.consumer / ".claude/skills/.hidden").mkdir()
        (self.consumer / ".claude/skills/.hidden/SKILL.md").write_text("x\n")
        (self.consumer / ".claude/skills/loose.md").write_text("x\n")
        self.sync()
        names = {p.name for p in (self.consumer / ".agents/skills").iterdir()}
        self.assertEqual(names, {"demo", "mine"})

    def test_symlink_outside_tree_skipped(self):
        ext = self.tmp / "elsewhere" / "linked"
        ext.mkdir(parents=True)
        (ext / "SKILL.md").write_text("outside\n")
        (self.consumer / ".claude/skills/linked").symlink_to(ext)
        rc, out = self.sync()
        self.assertEqual(rc, 0)
        self.assertFalse((self.consumer / ".agents/skills/linked").exists())
        self.assertIn("skipped local skill linked", out)

    def test_symlinked_file_outside_tree_skipped(self):
        secret = self.tmp / "secret.txt"
        secret.write_text("secret\n")
        (self.local / "leak.txt").symlink_to(secret)
        self.sync()
        self.assertFalse((self.consumer / ".agents/skills/mine/leak.txt").exists())
        self.assertTrue((self.consumer / ".agents/skills/mine/SKILL.md").is_file())


class Collisions(LocalSkillCase):
    def test_foreign_copy_dir_is_kept_whole(self):
        mine = self.consumer / ".kiro/skills/mine"
        mine.mkdir(parents=True)
        (mine / "SKILL.md").write_text("hand-written kiro variant\n")
        rc, out = self.sync()
        self.assertEqual(rc, 0)
        self.assertEqual((mine / "SKILL.md").read_text(), "hand-written kiro variant\n")
        # Decided per skill dir: none of the source's other files land in it.
        self.assertFalse((mine / "run.sh").exists())
        self.assertIn("kept .kiro/skills/mine: holds files sync-pack did not write", out)
        self.assertFalse(any(k.startswith(".kiro/skills/mine/")
                             for k in self.manifest()["files"]))
        # Other surfaces still get the skill, and --check stays clean.
        self.assertTrue((self.consumer / ".agents/skills/mine/run.sh").is_file())
        rc, out = self.sync(check=True)
        self.assertEqual(rc, 0, out)

    def test_extra_foreign_file_keeps_the_dir(self):
        mine = self.consumer / ".kiro/skills/mine"
        mine.mkdir(parents=True)
        (mine / "kiro-only.md").write_text("mine\n")
        _, out = self.sync()
        self.assertIn("kept .kiro/skills/mine", out)
        self.assertFalse((mine / "SKILL.md").exists())

    def test_identical_preexisting_copy_is_adopted(self):
        mine = self.consumer / ".kiro/skills/mine"
        mine.mkdir(parents=True)
        (mine / "SKILL.md").write_text(LOCAL_SKILL)
        _, out = self.sync()
        self.assertNotIn("kept .kiro", out)
        self.assertIn(".kiro/skills/mine/SKILL.md", self.manifest()["files"])
        self.assertTrue((mine / "run.sh").is_file())

    def test_locally_modified_copy_is_overwritten_with_warning(self):
        self.sync()
        (self.consumer / ".kiro/skills/mine/SKILL.md").write_text("edited copy\n")
        _, out = self.sync()
        self.assertIn(".kiro/skills/mine/SKILL.md was locally modified", out)
        self.assertEqual((self.consumer / ".kiro/skills/mine/SKILL.md").read_text(),
                         LOCAL_SKILL)

    def test_pack_now_shipping_a_local_name_warns(self):
        self.sync()
        self.assertIn("mine", self.manifest()["local_skills"])
        (self.pack_root / "skills" / "mine").mkdir()
        (self.pack_root / "skills" / "mine" / "SKILL.md").write_text("pack mine\n")
        _, out = self.sync()
        self.assertIn("warning: the pack now ships a skill named mine", out)
        self.assertEqual((self.consumer / ".agents/skills/mine/SKILL.md").read_text(),
                         "pack mine\n")
        self.assertNotIn("local_skills", self.manifest())

    def test_consumer_dir_shadowed_by_pack_name_is_reported(self):
        own = self.consumer / ".claude/skills/demo"
        own.mkdir()
        (own / "SKILL.md").write_text("consumer's demo\n")
        _, out = self.sync(harnesses=["cursor"])    # .claude/skills not the pack's
        self.assertIn("skipped local skill demo: the pack owns", out)

    def test_pack_written_claude_copies_are_not_reported(self):
        _, out = self.sync()                         # claude writes .claude/skills/demo
        _, out = self.sync()
        self.assertNotIn("skipped local skill", out)


    def test_extra_file_in_owned_copy_dir_does_not_stop_mirroring(self):
        self.sync()
        mine = self.consumer / ".kiro/skills/mine"
        (mine / "kiro-addendum.md").write_text("extra\n")
        (self.local / "SKILL.md").write_text(LOCAL_SKILL + "v2\n")
        _, out = self.sync()
        self.assertNotIn("kept .kiro", out)
        self.assertEqual((mine / "SKILL.md").read_text(), LOCAL_SKILL + "v2\n")
        self.assertEqual((mine / "kiro-addendum.md").read_text(), "extra\n")
        self.assertNotIn("retired", self.manifest())
        self.sync(prune=True)
        self.assertTrue((mine / "SKILL.md").is_file())

    def test_retired_pack_name_is_skipped_quietly(self):
        self.sync(harnesses=["cursor"])
        m = self.manifest()
        m["base_retired"] = {".agents/skills/gone/SKILL.md": "0" * 64}
        (self.consumer / pack.MANIFEST_REL).write_text(json.dumps(m))
        (self.consumer / ".claude/skills/gone").mkdir()
        (self.consumer / ".claude/skills/gone/SKILL.md").write_text("x\n")
        _, out = self.sync()
        self.assertNotIn("skipped local skill gone", out)
        self.assertFalse((self.consumer / ".cursor/skills/gone").exists())


class Layouts(LocalSkillCase):
    def test_symlinked_copy_dir_to_source_is_left_alone(self):
        kiro = self.consumer / ".kiro/skills"
        kiro.mkdir(parents=True)
        (kiro / "mine").symlink_to(Path("..") / ".." / ".claude/skills/mine")
        _, out = self.sync()
        self.assertTrue((kiro / "mine").is_symlink())
        self.assertNotIn("kept .kiro", out)
        (self.local / "SKILL.md").write_text(LOCAL_SKILL + "edit\n")
        rc, out = self.sync(check=True)
        self.assertNotIn(".kiro/skills/mine", out)
        _, out = self.sync()
        self.assertNotIn("locally modified", out)

    def test_copy_dir_resolving_outside_is_never_written_through(self):
        ext = self.tmp / "external-mine"
        ext.mkdir()
        kiro = self.consumer / ".kiro/skills"
        kiro.mkdir(parents=True)
        (kiro / "mine").symlink_to(ext)
        _, out = self.sync()
        self.assertEqual(list(ext.iterdir()), [])
        self.assertIn("skipped .kiro/skills/mine: resolves outside the repo", out)

    def test_vcs_metadata_and_caches_not_mirrored(self):
        (self.local / ".git").mkdir()
        (self.local / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        (self.local / "__pycache__").mkdir()
        (self.local / "__pycache__" / "x.pyc").write_bytes(b"\0")
        self.sync()
        mirror = self.consumer / ".agents/skills/mine"
        self.assertFalse((mirror / ".git").exists())
        self.assertFalse((mirror / "__pycache__").exists())
        self.assertTrue((mirror / "SKILL.md").is_file())

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_gitignored_files_not_mirrored(self):
        import subprocess
        subprocess.run(["git", "init", "-q", str(self.consumer)], check=True)
        (self.consumer / ".gitignore").write_text(".claude/skills/mine/.env\n")
        (self.local / ".env").write_text("TOKEN=secret\n")
        self.sync()
        self.assertFalse((self.consumer / ".agents/skills/mine/.env").exists())
        self.assertTrue((self.consumer / ".agents/skills/mine/SKILL.md").is_file())


    def git_init(self):
        import subprocess
        subprocess.run(["git", "init", "-q", str(self.consumer)], check=True)

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_gitignored_skill_is_not_local_and_its_copies_retire(self):
        self.git_init()
        self.sync()
        self.assertIn("mine", self.manifest()["local_skills"])
        (self.consumer / ".gitignore").write_text(".claude/skills/mine/\n")
        _, out = self.sync()
        self.assertNotIn("local skills mirrored", out)
        self.assertIn(".agents/skills/mine/SKILL.md", self.manifest()["retired"])
        self.sync(prune=True)
        self.assertFalse((self.consumer / ".agents/skills/mine").exists())

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_gitignore_honoured_through_symlinked_skill_dir(self):
        self.git_init()
        shared = self.consumer / "shared" / "mine"
        shared.parent.mkdir()
        shutil.move(str(self.local), str(shared))
        self.local.symlink_to(Path("..") / ".." / "shared" / "mine")
        (self.consumer / ".gitignore").write_text("shared/mine/.env\n")
        (shared / ".env").write_text("TOKEN=secret\n")
        self.sync()
        self.assertTrue((self.consumer / ".agents/skills/mine/SKILL.md").is_file())
        self.assertFalse((self.consumer / ".agents/skills/mine/.env").exists())

    def test_git_failure_in_a_work_tree_fails_closed(self):
        def fake(target, *args):
            if args[0] == "rev-parse":
                return mock.Mock(returncode=0, stdout=b"true\n")
            return mock.Mock(returncode=128, stdout=b"")
        with mock.patch.object(pack, "_git", fake):
            _, out = self.sync()
        self.assertIn("local skills not mirrored this run", out)
        self.assertFalse((self.consumer / ".agents/skills/mine").exists())


class Removal(LocalSkillCase):
    def test_removed_skill_retires_and_prunes_everywhere(self):
        self.sync()
        shutil.rmtree(self.local)
        _, out = self.sync()
        m = self.manifest()
        self.assertIn(".kiro/skills/mine/SKILL.md", m["retired"])
        self.assertIn(".agents/skills/mine/SKILL.md", m["retired"])
        self.assertNotIn(".agents/skills/mine/SKILL.md", m.get("base_retired", {}))
        self.assertEqual(m["local_skills"], ["mine"])   # kept while copies remain
        rc, out = self.sync(check=True, prune=True)
        self.assertEqual(rc, 1)
        self.assertIn(".agents/skills/mine/SKILL.md", out)
        self.sync(prune=True)
        self.assertFalse((self.consumer / ".kiro/skills/mine").exists())
        self.assertFalse((self.consumer / ".agents/skills/mine").exists())
        # The pack's own base-layer skill is untouched.
        self.assertTrue((self.consumer / ".agents/skills/demo/SKILL.md").is_file())
        m = self.manifest()
        self.assertNotIn("local_skills", m)
        self.assertNotIn("retired", m)

    def test_edited_base_copy_of_removed_skill_is_kept(self):
        self.sync()
        (self.consumer / ".agents/skills/mine/SKILL.md").write_text("edited\n")
        shutil.rmtree(self.local)
        _, out = self.sync(prune=True)
        self.assertIn("kept (locally modified): .agents/skills/mine/SKILL.md", out)
        self.assertTrue((self.consumer / ".agents/skills/mine/SKILL.md").is_file())

    def test_removed_then_readded(self):
        self.sync()
        saved = self.tmp / "saved"
        shutil.copytree(self.local, saved)
        shutil.rmtree(self.local)
        self.sync()
        shutil.copytree(saved, self.local)
        _, out = self.sync()
        m = self.manifest()
        self.assertEqual(m["local_skills"], ["mine"])
        self.assertIn(".agents/skills/mine/SKILL.md", m["files"])
        self.assertNotIn("retired", m)

    def test_retired_pack_skill_in_base_layer_is_not_resurrected(self):
        # The pack shipped `old` (its .agents copy survives in base_retired);
        # a `.claude/skills/old` with no manifest record under it is still not
        # the consumer's, whatever happened to its `.claude` entries.
        self.sync()
        old = self.consumer / ".claude/skills/old"
        old.mkdir()
        (old / "SKILL.md").write_text("old pack skill\n")
        base = self.consumer / ".agents/skills/old"
        base.mkdir()
        (base / "SKILL.md").write_text("old pack skill\n")
        m = self.manifest()
        m["base_retired"] = {".agents/skills/old/SKILL.md":
                             pack._sha_bytes(b"old pack skill\n")}
        (self.consumer / pack.MANIFEST_REL).write_text(json.dumps(m))
        self.sync(harnesses=["cursor"])
        self.assertFalse((self.consumer / ".cursor/skills/old").exists())
        self.assertFalse((self.consumer / ".kiro/skills/old").exists())


if __name__ == "__main__":
    unittest.main()
