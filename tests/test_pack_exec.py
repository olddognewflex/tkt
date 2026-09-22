"""Regression tests for TKT-24: `sync-pack` must carry the executable bit.

It compared content hashes, which cannot see the mode bit, and wrote files
with `write_bytes` — so an executable pack file landed in the consumer as
0644. Git silently skips a hook that is not executable, so a consumer would
believe it was protected when it was not.

The subtle half: every consumer synced before this fix already has the right
*bytes* and the wrong *mode*, and the idempotent path skipped any file whose
content matched. A fix that only chmods on write would never repair them.

The real pack ships no executable today (0 of 171 planned files), so these
tests plant one in a temp pack by pointing `core.pack.PACK_ROOT` at it; an
assertion over the real pack would pass vacuously over zero files. Fully
offline. Run from the repo root:

    python3 -m unittest tests.test_pack_exec
"""
import contextlib
import io
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

SKILL_MD = """---
name: demo
description: 'A demo skill for exec-bit tests.'
---

# Demo
"""


def is_exec(p: Path) -> bool:
    return bool(p.stat().st_mode & stat.S_IXUSR)


class ExecBit(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.pack_root = self.tmp / "pack"
        skill = self.pack_root / "skills" / "demo"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(SKILL_MD)
        self.hook_src = skill / "hook.sh"
        self.hook_src.write_text("#!/bin/sh\necho hi\n")
        self.hook_src.chmod(0o755)
        self.consumer = self.tmp / "consumer"
        self.consumer.mkdir()
        patcher = mock.patch.object(pack, "PACK_ROOT", self.pack_root)
        patcher.start()
        self.addCleanup(patcher.stop)

    @property
    def hook_dst(self) -> Path:
        return self.consumer / ".claude" / "skills" / "demo" / "hook.sh"

    def sync(self, check=False):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = pack.sync_pack(str(self.consumer), all_harnesses=False,
                                check=check, harnesses=["claude"])
        return rc, out.getvalue()

    def test_fresh_install_is_executable(self):
        self.sync()
        self.assertTrue(self.hook_dst.is_file())
        self.assertTrue(is_exec(self.hook_dst))

    def test_resync_repairs_a_lost_exec_bit_with_identical_content(self):
        """The case a write-only fix misses: right bytes, wrong mode."""
        self.sync()
        self.hook_dst.chmod(0o644)
        self.assertFalse(is_exec(self.hook_dst))
        self.sync()
        self.assertTrue(is_exec(self.hook_dst))

    def test_check_flags_a_lost_exec_bit_and_fails(self):
        self.sync()
        self.hook_dst.chmod(0o644)
        rc, out = self.sync(check=True)
        self.assertEqual(rc, 1, out)
        self.assertIn("not-executable", out)
        self.assertIn("hook.sh", out)

    def test_check_passes_once_repaired(self):
        self.sync()
        self.hook_dst.chmod(0o644)
        self.sync()
        rc, out = self.sync(check=True)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("not-executable", out)

    def test_a_lost_exec_bit_is_not_reported_as_out_of_date(self):
        """Content is identical; calling it out of date would mislead."""
        self.sync()
        self.hook_dst.chmod(0o644)
        _, out = self.sync(check=True)
        self.assertNotIn("out-of-date-vs-pack", out)

    def test_non_executable_source_stays_non_executable(self):
        self.sync()
        md = self.consumer / ".claude" / "skills" / "demo" / "SKILL.md"
        self.assertFalse(is_exec(md))

    def test_non_executable_source_leaves_a_consumer_chmod_alone(self):
        """A consumer's own chmod +x on a non-exec pack file survives."""
        self.sync()
        md = self.consumer / ".claude" / "skills" / "demo" / "SKILL.md"
        md.chmod(0o755)
        self.sync()
        self.assertTrue(is_exec(md))

    def test_repair_ignores_the_sources_group_and_other_bits(self):
        """A user-only-executable source (0o744) repairs the same way a 0o755
        one does: only the owner bit decides "executable", and the grant
        follows the destination's own r bits. So a consumer copy at 0o655
        goes to 0o755 — keeping its group/other x rather than being cut back
        to the source's 0o744."""
        self.hook_src.chmod(0o744)
        self.sync()
        self.hook_dst.chmod(0o655)
        self.sync()
        self.assertEqual(stat.S_IMODE(self.hook_dst.stat().st_mode), 0o755)

    def test_follows_git_checkout_semantics_not_the_source_triplet(self):
        """Git stores only 100755/100644, so group/other bits come from the
        umask each checkout ran under. The destination gets x wherever it
        already has r — what a checkout would give — rather than a copy of
        the source's exact triplet."""
        self.hook_src.chmod(0o744)
        self.sync()
        self.assertEqual(stat.S_IMODE(self.hook_dst.stat().st_mode), 0o755)

    def test_repair_adds_bits_and_never_strips_others(self):
        """The repair ORs bits in; it never overwrites the mode. A consumer
        copy at 0o655 (group/other x, but not owner x) goes to 0o755, not to
        something that drops the bits the consumer had."""
        self.sync()
        self.hook_dst.chmod(0o655)
        self.sync()
        self.assertEqual(stat.S_IMODE(self.hook_dst.stat().st_mode), 0o755)

    def test_owner_executable_is_executable_whatever_the_other_bits(self):
        """0o750 is executable to git (owner x). It must be neither flagged
        by --check nor rewritten — the old whole-triplet comparison flagged it
        and then 'repaired' it to the nonsense mode 0o751."""
        self.sync()
        self.hook_dst.chmod(0o750)
        rc, out = self.sync(check=True)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("not-executable", out)
        self.sync()
        self.assertEqual(stat.S_IMODE(self.hook_dst.stat().st_mode), 0o750)

    def test_respects_a_restrictive_umask(self):
        """Under umask 077 the file is written 0o600; adding x where r is set
        gives 0o700, not the 0o711 that ORing in 0o111 would produce."""
        old = os.umask(0o077)
        try:
            self.sync()
        finally:
            os.umask(old)
        self.assertEqual(stat.S_IMODE(self.hook_dst.stat().st_mode), 0o700)

    def test_a_mode_only_repair_is_not_reported_as_a_write(self):
        self.sync()
        self.hook_dst.chmod(0o644)
        _, out = self.sync()
        self.assertIn("0 file(s) written", out)
        self.assertIn("1 exec bit(s) repaired", out)

    def test_an_unchmoddable_file_warns_instead_of_aborting(self):
        """A regular file owned by another user (e.g. after a container ran
        sync-pack) cannot be chmod'ed. That must not abort the sync half-way
        with a traceback — and --check must still report the file."""
        self.sync()
        self.hook_dst.chmod(0o644)
        err = io.StringIO()
        with mock.patch.object(Path, "chmod",
                               side_effect=PermissionError(1, "Operation not permitted")), \
                contextlib.redirect_stderr(err):
            rc, out = self.sync()
        self.assertEqual(rc, 0, out)
        self.assertIn("could not make", err.getvalue())
        self.assertIn("hook.sh", err.getvalue())
        rc, out = self.sync(check=True)
        self.assertEqual(rc, 1, out)
        self.assertIn("not-executable", out)

    def test_symlinked_destination_mode_is_left_alone(self):
        """A symlinked destination resolves outside the consumer tree (e.g.
        `tkt init --link-skills`); sync-pack only writes paths it records,
        so it must not chmod the link target, nor flag what it won't fix."""
        outside = self.tmp / "outside.sh"
        outside.write_bytes(self.hook_src.read_bytes())
        outside.chmod(0o644)
        self.sync()
        self.hook_dst.unlink()
        self.hook_dst.symlink_to(outside)
        self.sync()
        self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o644)
        _, out = self.sync(check=True)
        self.assertNotIn("not-executable", out)

    def test_symlinked_parent_directory_is_not_followed(self):
        """The `tkt init --link-skills` shape: the skill *directory* is a
        symlink, so `hook.sh` is a plain file whose parent resolves outside
        the consumer tree. A leaf-only `is_symlink()` check misses it and
        chmod changes the outside file; it must be left alone."""
        shared = self.tmp / "shared" / "demo"
        shared.mkdir(parents=True)
        for f in ("SKILL.md", "hook.sh"):
            (shared / f).write_bytes((self.hook_src.parent / f).read_bytes())
        (shared / "hook.sh").chmod(0o644)
        (self.consumer / ".claude" / "skills").mkdir(parents=True)
        (self.consumer / ".claude" / "skills" / "demo").symlink_to(shared)
        self.sync()
        self.assertEqual(stat.S_IMODE((shared / "hook.sh").stat().st_mode), 0o644)
        _, out = self.sync(check=True)
        self.assertNotIn("not-executable", out)

    def test_a_failed_repair_is_not_reported_as_up_to_date(self):
        """If the only change needed is a repair that fails, saying "up to
        date" contradicts the `--check` run that follows it."""
        self.sync()
        self.hook_dst.chmod(0o644)
        with mock.patch.object(Path, "chmod",
                               side_effect=PermissionError(1, "Operation not permitted")), \
                contextlib.redirect_stderr(io.StringIO()):
            _, out = self.sync()
        self.assertNotIn("up to date", out)
        self.assertIn("could NOT be set", out)

    def test_out_of_date_and_non_executable_reports_once_as_out_of_date(self):
        """Deliberate: the re-sync that fixes the content also sets the bit,
        so the file is reported once, in the category that describes it."""
        self.sync()
        self.hook_dst.write_text("#!/bin/sh\necho stale\n")
        self.hook_dst.chmod(0o644)
        _, out = self.sync(check=True)
        self.assertIn("out-of-date-vs-pack", out)
        self.assertNotIn("not-executable", out)
        self.sync()
        self.assertTrue(is_exec(self.hook_dst))

if __name__ == "__main__":
    unittest.main()
