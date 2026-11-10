"""TKT-21: `sync-pack` installs an always-on base layer, and derives the
`.agents/skills` and `.kiro/skills` mirrors from canonical `skills/`.

The base layer (`.agents/skills`, `.agents/workflows`, `standards/` ->
`docs/sdlc/`, `enforcement/` -> `.sdlc/enforcement/`) ships on every sync,
whatever harnesses are selected, and the manifest records its dest prefixes
as `base_layer` so `--prune` skips them.

Most cases point `core.pack.PACK_ROOT` at a temp pack; the last class checks
the real pack so a stored mirror can never come back unnoticed. Offline.

    python3 -m unittest tests.test_pack_base
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
from core.errors import UsageError  # noqa: E402

SKILL_MD = """---
name: demo
description: 'A demo skill for base-layer tests.'
---

# Demo
"""


def is_exec(p: Path) -> bool:
    return bool(p.stat().st_mode & stat.S_IXUSR)


class TempPack(unittest.TestCase):
    """A temp pack with one skill, one workflow and an enforcement dir."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.pack_root = self.tmp / "pack"
        skill = self.pack_root / "skills" / "demo"
        (skill / "ref").mkdir(parents=True)
        (skill / "SKILL.md").write_text(SKILL_MD)
        (skill / "ref" / "x.md").write_text("reference\n")
        meta = self.pack_root / "skills" / "sync-skills"
        meta.mkdir()
        (meta / "SKILL.md").write_text("---\nname: sync-skills\n---\n")
        wf = self.pack_root / ".agents" / "workflows"
        wf.mkdir(parents=True)
        (wf / "demo.md").write_text("workflow\n")
        enf = self.pack_root / "enforcement"
        enf.mkdir()
        (enf / "chk.mjs").write_text("#!/usr/bin/env node\n")
        (enf / "chk.mjs").chmod(0o755)
        (enf / "rules.json").write_text("{}\n")
        (enf / "chk.test.mjs").write_text("// self-test\n")
        self.consumer = self.tmp / "consumer"
        self.consumer.mkdir()
        patcher = mock.patch.object(pack, "PACK_ROOT", self.pack_root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sync(self, harnesses=None, check=False) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = pack.sync_pack(str(self.consumer), all_harnesses=False,
                                check=check, harnesses=harnesses)
        return rc, out.getvalue()

    def manifest(self) -> dict:
        return json.loads((self.consumer / pack.MANIFEST_REL).read_text())

    def assert_base_layer(self):
        c = self.consumer
        for rel in (".agents/skills/demo/SKILL.md", ".agents/skills/demo/ref/x.md",
                    ".agents/workflows/demo.md", ".sdlc/enforcement/chk.mjs",
                    ".sdlc/enforcement/rules.json"):
            self.assertTrue((c / rel).is_file(), rel)
        self.assertFalse((c / ".sdlc/enforcement/chk.test.mjs").exists())
        self.assertFalse((c / ".agents/skills/sync-skills").exists())


class BareSync(TempPack):
    def test_fresh_consumer_gets_base_layer_and_defaults(self):
        rc, _ = self.sync()
        self.assertEqual(rc, 0)
        self.assert_base_layer()
        c = self.consumer
        self.assertTrue((c / ".kiro/skills/demo/SKILL.md").is_file())
        self.assertTrue((c / ".claude/skills/demo/SKILL.md").is_file())
        self.assertFalse((c / ".kiro/skills/sync-skills").exists())
        # No standards/ in the pack: nothing written, no empty dir.
        self.assertFalse((c / "docs/sdlc").exists())
        self.assertFalse(any(k.startswith("docs/sdlc")
                             for k in self.manifest()["files"]))

    def test_standards_ship_when_present(self):
        std = self.pack_root / "standards"
        std.mkdir()
        (std / "a.md").write_text("standard\n")
        self.sync()
        self.assertEqual((self.consumer / "docs/sdlc/a.md").read_text(), "standard\n")
        self.assertIn("docs/sdlc/a.md", self.manifest()["files"])

    def test_mirrors_are_byte_copies_of_canonical_skills(self):
        self.sync()
        for mirror in (".agents/skills", ".kiro/skills"):
            for rel in ("demo/SKILL.md", "demo/ref/x.md"):
                self.assertEqual(
                    (self.consumer / mirror / rel).read_bytes(),
                    (self.pack_root / "skills" / rel).read_bytes(), f"{mirror}/{rel}")

    def test_checker_ships_executable_and_check_is_clean(self):
        self.sync()
        self.assertTrue(is_exec(self.consumer / ".sdlc/enforcement/chk.mjs"))
        rc, out = self.sync(check=True)
        self.assertEqual(rc, 0, out)
        self.assertIn("base layer: .agents/skills", out)

    def test_check_reports_missing_base_file(self):
        self.sync()
        (self.consumer / ".sdlc/enforcement/chk.mjs").unlink()
        rc, out = self.sync(check=True)
        self.assertEqual(rc, 1)
        self.assertIn("missing (1):", out)
        self.assertIn(".sdlc/enforcement/chk.mjs", out)

    def test_second_run_is_up_to_date_and_manifest_unchanged(self):
        self.sync()
        before = (self.consumer / pack.MANIFEST_REL).read_bytes()
        _, out = self.sync()
        self.assertIn("up to date", out)
        self.assertEqual((self.consumer / pack.MANIFEST_REL).read_bytes(), before)


class NarrowSelection(TempPack):
    def test_cursor_only_still_gets_base_layer(self):
        self.sync(harnesses=["cursor"])
        self.assert_base_layer()
        self.assertFalse((self.consumer / ".claude").exists())
        self.assertEqual(self.manifest()["harnesses"], ["cursor"])

    def test_agents_alone_means_base_layer_only(self):
        rc, _ = self.sync(harnesses=["agents"])
        self.assertEqual(rc, 0)
        self.assert_base_layer()
        for d in (".claude", ".github", ".kiro"):
            self.assertFalse((self.consumer / d).exists(), d)
        self.assertTrue((self.consumer / "AGENTS.md").is_file())
        self.assertEqual(self.manifest()["harnesses"], ["agents"])

    def test_agents_alone_stays_alone_on_a_bare_resync(self):
        # The recorded selection is non-empty, so defaults must not return.
        self.sync(harnesses=["agents"])
        self.sync()
        for d in (".claude", ".github", ".kiro"):
            self.assertFalse((self.consumer / d).exists(), d)
        self.assertEqual(self.manifest()["harnesses"], ["agents"])

    def test_unknown_harness_still_rejected(self):
        with self.assertRaises(UsageError):
            self.sync(harnesses=["nope"])


class BaseLayerManifest(TempPack):
    def test_manifest_records_base_layer(self):
        self.sync()
        self.assertEqual(self.manifest()["base_layer"], pack._BASE_DESTS)

    def test_is_base_matches_whole_segments(self):
        for rel in (".agents/skills/x/SKILL.md", "docs/sdlc/a.md",
                    ".sdlc/enforcement/chk.mjs", ".agents/workflows/demo.md"):
            self.assertTrue(pack._is_base(rel), rel)
        for rel in (".agents/skillsX/a", ".claude/skills/x", "docs/sdlc-old/a",
                    ".sdlc/pack-manifest.json", ".kiro/skills/x/SKILL.md"):
            self.assertFalse(pack._is_base(rel), rel)


class Upgrade(TempPack):
    def test_old_agents_manifest_upgrades_without_warnings(self):
        # A consumer synced when `.agents/skills` was a stored mirror that
        # had fallen behind: on-disk bytes match what the manifest recorded.
        stale = b"---\nname: demo\n---\nold\n"
        dst = self.consumer / ".agents/skills/demo/SKILL.md"
        dst.parent.mkdir(parents=True)
        dst.write_bytes(stale)
        man = self.consumer / pack.MANIFEST_REL
        man.parent.mkdir(parents=True)
        man.write_text(json.dumps({
            "pack_commit": "old", "pack_root": str(self.pack_root),
            "harnesses": ["claude", "copilot", "kiro", "agents"],
            "files": {".agents/skills/demo/SKILL.md": pack._sha_bytes(stale)},
        }))
        _, out = self.sync()
        self.assertNotIn("warning", out)
        self.assertEqual(dst.read_text(), SKILL_MD)
        m = self.manifest()
        self.assertEqual(m["base_layer"], pack._BASE_DESTS)
        self.assertIn("agents", m["harnesses"])
        _, out = self.sync()
        self.assertIn("up to date", out)


class ListHarnesses(TempPack):
    def test_lists_base_layer_and_agents_entry(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            pack.list_harnesses(str(self.consumer))
        text = out.getvalue()
        self.assertIn("base layer (always installed): .agents/skills", text)
        self.assertIn("(base layer only)", text)


class RealPackAntiRot(unittest.TestCase):
    """The real pack: skill mirrors must come from canonical skills/."""

    def test_no_source_is_a_stored_mirror(self):
        sources = [s for s, _ in pack._BASE_LAYER]
        for _, dirs in pack._HARNESSES.values():
            sources += [s for s, _ in dirs]
        for stored in (".agents/skills", ".kiro/skills"):
            self.assertNotIn(stored, sources)

    def test_stored_mirror_dirs_are_absent(self):
        # sync-pack never reads these, so a copy that comes back (say, from
        # a branch cut before they were deleted) would drift unnoticed.
        for stored in (".agents/skills", ".kiro/skills"):
            self.assertFalse((pack.PACK_ROOT / stored).exists(),
                             f"{stored} is derived from skills/; do not store it")

    def test_mirror_dests_map_to_canonical_files(self):
        canonical = pack.PACK_ROOT / "skills"
        plan = dict((rel, src) for src, rel in pack._plan_for(["kiro"]))
        mirrored = [r for r in plan
                    if r.startswith((".agents/skills/", ".kiro/skills/"))]
        self.assertTrue(mirrored)
        for rel in mirrored:
            self.assertTrue(plan[rel].is_relative_to(canonical), rel)
        self.assertEqual(plan[".kiro/skills/respond-to-review/SKILL.md"],
                         canonical / "respond-to-review" / "SKILL.md")
        self.assertEqual(plan[".agents/skills/select-ticket/SKILL.md"],
                         canonical / "select-ticket" / "SKILL.md")

    def test_enforcement_self_tests_not_shipped(self):
        dests = [rel for _, rel in pack._plan_for([])]
        self.assertTrue(any(r.startswith(".sdlc/enforcement/") for r in dests))
        self.assertFalse(any(r.endswith(".test.mjs") for r in dests))


if __name__ == "__main__":
    unittest.main()
