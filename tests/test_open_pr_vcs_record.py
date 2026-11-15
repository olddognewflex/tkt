"""TKT-114: open-pr records the branch and PR on the ticket.

QA Author — adversarial tests
Break-It dimensions covered: drift across copies (every open-pr surface,
discovered, with a floor so discovery cannot pass vacuously), error
propagation (exit 3 = "not stored", anything else = a warning, never fatal),
missing/empty input (detached HEAD, no repo, empty PR URL), shell
portability (bash, zsh, dash; zsh keeps `${X:+a "$X"}` as one word).

After `gh pr create`, open-pr runs `tkt edit KEY --add-branch B --add-pr URL`.
The skill ships in several harness surfaces; a harness reading only its own
copy skips the record step if that copy lacks it. Copies are found by
scanning the tree for files that run `gh pr create`, so a surface added later
is checked without editing this file. The full `.agents/skills` /
`.kiro/skills` copies are derived from the canonical skill at sync time (see
test_pack_base.py), so the canonical checks cover them.

The canonical step-6 shell block is run under every available shell against
stub `tkt` and `gh` binaries in a scratch git repo. Repos are never
committed to (commit signing can hang): `git init -b NAME` leaves an unborn
branch that `git symbolic-ref` resolves, and a detached HEAD is a raw object
id written into `.git/HEAD`, which git accepts without the object existing.

    python3 -m unittest tests.test_open_pr_vcs_record
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CANONICAL_REL = "skills/open-pr/SKILL.md"

# The floor: these exist today. Discovery may find more, never fewer.
KNOWN_COPIES = {
    CANONICAL_REL,
    ".agents/workflows/open-pr.md",
    ".augment/commands/open-pr.md",
    ".clinerules/workflows/open-pr.md",
    ".continue/prompts/open-pr.prompt",
    ".cursor/commands/open-pr.md",
    ".cursor/skills/open-pr/SKILL.md",
    ".gemini/commands/open-pr.toml",
    ".github/prompts/open-pr.prompt.md",
    ".opencode/commands/open-pr.md",
    ".windsurf/workflows/open-pr.md",
}

# Files that run `gh pr create` but are not open-pr, each a decision.
NOT_OPEN_PR = {
    "skills/hotfix-revert/SKILL.md",  # revert PR: no ticket branch to record
}

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}

# `tkt edit <key> ... --add-pr <var>`, on one line / in one code span.
RECORD = re.compile(
    r'tkt edit\s+"?(?:\$\{?KEY\}?|\{\{args\}\})"?[^\n`]*?--add-pr\s+"?\$\{?(\w+)')

SHELLS = [s for s in ("bash", "zsh", "dash") if shutil.which(s)]
HAVE_GIT = shutil.which("git") is not None


def gh_pr_create_files(root: Path) -> set:
    """Repo-relative paths of every text file that mentions `gh pr create`."""
    found = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS
                       and not (Path(dirpath) == root and d == "tests")]
        for name in filenames:
            path = Path(dirpath) / name
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if "gh pr create" in text:
                found.add(path.relative_to(root).as_posix())
    return found


def open_pr_copies(root: Path) -> set:
    return {p for p in gh_pr_create_files(root) if "open-pr" in p}


class OpenPrCopies(unittest.TestCase):
    """Every open-pr surface carries the record step."""

    def setUp(self):
        self.files = gh_pr_create_files(ROOT)
        self.copies = {p for p in self.files if "open-pr" in p}

    def test_discovery_finds_at_least_the_known_set(self):
        self.assertLessEqual(len(KNOWN_COPIES), len(self.copies))
        self.assertEqual(KNOWN_COPIES - self.copies, set(),
                         "known open-pr copies missing or no longer running "
                         "`gh pr create`")

    def test_every_gh_pr_create_file_is_classified(self):
        # A new surface named differently (say `openpr.md`) would slip past
        # the `open-pr` path filter; make it an explicit decision instead.
        unclassified = self.files - self.copies - NOT_OPEN_PR
        self.assertEqual(unclassified, set(),
                         "runs `gh pr create` but is neither an open-pr copy "
                         "nor listed in NOT_OPEN_PR")

    def test_every_copy_records_the_pr_after_creating_it(self):
        for rel in sorted(self.copies):
            with self.subTest(path=rel):
                text = (ROOT / rel).read_text(encoding="utf-8")
                m = RECORD.search(text)
                self.assertIsNotNone(m, "no `tkt edit $KEY ... --add-pr` step")
                self.assertIn("--add-branch", text)
                self.assertLess(text.index("gh pr create"), m.start(),
                                "record step comes before the PR exists")
                review = re.search(r'tkt transition "?\$KEY"? review', text)
                if review:
                    self.assertLess(m.start(), review.start(),
                                    "record step after the review transition")

    def test_every_copy_assigns_the_pr_variable_from_gh(self):
        # The variable `--add-pr` reads must be assigned, before the record
        # step, from `gh pr create` (which prints the new PR's URL) or
        # `gh pr view`. Merely naming `$PR_URL` passes an empty value when
        # the instructions are followed literally, and `tkt edit` rejects it.
        for rel in sorted(self.copies):
            with self.subTest(path=rel):
                text = (ROOT / rel).read_text(encoding="utf-8")
                m = RECORD.search(text)
                self.assertIsNotNone(m, "no `tkt edit $KEY ... --add-pr` step")
                var = m.group(1)
                self.assertRegex(
                    text[:m.start()],
                    rf"\b{re.escape(var)}=\$\(gh pr (?:create|view)\b",
                    f"`${var}` is never assigned from `gh pr create`/`gh pr view` "
                    "before the record step")

    def test_no_copy_takes_the_branch_from_rev_parse(self):
        # `git rev-parse --abbrev-ref HEAD` prints `HEAD` on a detached
        # checkout, which `--add-branch` rejects (exit 64), so the PR would
        # not be recorded either. Every copy must use `symbolic-ref` and
        # leave the flag out when it prints nothing.
        for rel in sorted(self.copies):
            with self.subTest(path=rel):
                text = (ROOT / rel).read_text(encoding="utf-8")
                self.assertNotIn("rev-parse --abbrev-ref HEAD", text)
                self.assertIn("git symbolic-ref -q --short HEAD", text)

    def test_every_copy_says_exit_3_is_not_a_failure(self):
        for rel in sorted(self.copies):
            with self.subTest(path=rel):
                text = (ROOT / rel).read_text(encoding="utf-8")
                m = RECORD.search(text)
                self.assertIsNotNone(m, "no `tkt edit $KEY ... --add-pr` step")
                tail = text[m.start():]
                self.assertRegex(tail[:800], r"(?:exit|rc\"?\s+-eq)\s+\"?3")


# ---- the canonical shell block, executed ----------------------------------

STUB_TKT = f"""#!{sys.executable}
import json, os, sys
spec = json.load(open(os.environ["STUB_SPEC"]))
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(json.dumps(["tkt", *sys.argv[1:]]) + "\\n")
verb = sys.argv[1]
if verb == "view":
    print(json.dumps({{"key": sys.argv[2], "type_class": spec["class"]}}))
elif verb == "cfg":
    print("olddognewflex/tkt")
elif verb == "edit" and "--add-pr" in sys.argv:
    rc = spec["edit_rc"]
    if rc:
        sys.stderr.write(f"tkt: scripted failure {{rc}}\\n")
    sys.exit(rc)
elif verb == "worklog":
    print(json.dumps({{"human": "1h", "worklog_id": "w1"}}))
"""

STUB_GH = f"""#!{sys.executable}
import json, os, sys
spec = json.load(open(os.environ["STUB_SPEC"]))
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(json.dumps(["gh", *sys.argv[1:]]) + "\\n")
if sys.argv[1:3] == ["pr", "view"]:
    print(spec["pr_url"])
"""

PR_URL = "https://github.com/olddognewflex/tkt/pull/96"


def step6_block(root: Path = None) -> str:
    text = ((root or ROOT) / CANONICAL_REL).read_text(encoding="utf-8")
    section = text[text.index("### 6."):text.index("## Output")]
    m = re.search(r"```shell\n(.*?)\n```", section, re.DOTALL)
    assert m, "no shell block in step 6"
    return m.group(1)


def run_step6(shell, *, head="branch", branch="feature/tkt-114-x",
              edit_rc=0, pr_url=PR_URL, cls="deliverable", prefix="",
              root=None):
    """Run step 6 once. Returns (exit code, stdout, stderr, calls)."""
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "bin").mkdir()
        for name, body in (("tkt", STUB_TKT), ("gh", STUB_GH)):
            (t / "bin" / name).write_text(body)
            (t / "bin" / name).chmod(0o755)
        (t / "spec.json").write_text(json.dumps(
            {"class": cls, "edit_rc": edit_rc, "pr_url": pr_url}))
        work = t / "work"
        work.mkdir()
        env = dict(os.environ, PATH=f"{t / 'bin'}:{os.environ['PATH']}",
                   STUB_SPEC=str(t / "spec.json"), STUB_LOG=str(t / "calls.log"),
                   GIT_CEILING_DIRECTORIES=str(t))
        # BASH_ENV / ENV would source user rc files into bash / dash.
        for k in ("GIT_DIR", "GIT_WORK_TREE", "TKT_CONFIG", "BASH_ENV", "ENV"):
            env.pop(k, None)
        if head != "none":
            # An empty template: a user template that ships its own HEAD
            # makes `git init` a re-init that ignores `-b`.
            (t / "tpl").mkdir()
            subprocess.run(["git", "init", "-q", f"--template={t / 'tpl'}",
                            "-b", branch, str(work)],
                           env=env, check=True, capture_output=True, timeout=30)
        if head == "detached":
            (work / ".git" / "HEAD").write_text("1" * 40 + "\n")
        script = prefix + 'KEY="TKT-9"\nPR=96\n' + step6_block(root)
        # zsh -f: skip zshenv, which can put e.g. /opt/homebrew/bin ahead
        # of the stubs, so the real `gh` would answer.
        argv = [shell, "-f", "-c", script] if shell == "zsh" else \
            [shell, "-c", script]
        p = subprocess.run(argv, cwd=work, env=env,
                           text=True, capture_output=True, timeout=60)
        log = t / "calls.log"
        calls = [json.loads(ln) for ln in log.read_text().splitlines()] \
            if log.exists() else []
        if not any(c[:3] == ["gh", "pr", "view"] for c in calls):
            raise AssertionError(f"{shell}: the stub gh was bypassed: {calls}")
        return p.returncode, p.stdout, p.stderr, calls


def record_calls(calls):
    return [c for c in calls if c[:2] == ["tkt", "edit"] and "--add-pr" in c]


@unittest.skipUnless(SHELLS and HAVE_GIT, "needs a POSIX shell and git")
class CanonicalRecordStep(unittest.TestCase):

    def each_shell(self, **kw):
        """(shell, result) per shell; callers wrap asserts in subTest."""
        return [(shell, run_step6(shell, **kw)) for shell in SHELLS]

    def test_on_a_branch_records_branch_and_pr_as_separate_args(self):
        for shell, (rc, out, err, calls) in self.each_shell():
            with self.subTest(shell=shell):
                self.assertEqual(rc, 0, err)
                self.assertEqual(record_calls(calls), [[
                    "tkt", "edit", "TKT-9", "--add-branch", "feature/tkt-114-x",
                    "--add-pr", PR_URL]])
                self.assertNotIn("Warning", out)
                self.assertNotIn("does not store", out)

    def test_detached_head_records_the_pr_without_a_branch(self):
        for shell, (rc, out, err, calls) in self.each_shell(head="detached"):
            with self.subTest(shell=shell):
                self.assertEqual(rc, 0, err)
                # Exactly these words: no `--add-branch`, no "" and no "HEAD"
                # (a zsh-joined `${BRANCH:+...}` would show up as one odd word).
                self.assertEqual(record_calls(calls),
                                 [["tkt", "edit", "TKT-9", "--add-pr", PR_URL]])
                self.assertNotIn("Warning", out)

    def test_outside_a_git_repo_still_records_the_pr(self):
        for shell, (rc, out, err, calls) in self.each_shell(head="none"):
            with self.subTest(shell=shell):
                self.assertEqual(rc, 0, err)
                self.assertEqual(record_calls(calls),
                                 [["tkt", "edit", "TKT-9", "--add-pr", PR_URL]])

    def test_exit_3_is_a_note_and_the_skill_carries_on(self):
        for shell, (rc, out, err, calls) in self.each_shell(edit_rc=3):
            with self.subTest(shell=shell):
                self.assertEqual(rc, 0, err)
                self.assertIn("does not store branches/PRs", out)
                self.assertNotIn("Warning", out)
                self.assertTrue(any(c[:2] == ["tkt", "comment"] for c in calls),
                                "skill stopped after exit 3")

    def test_any_other_failure_is_a_warning_naming_the_exit_code(self):
        for code in (1, 2, 4, 5, 64):
            for shell, (rc, out, err, calls) in self.each_shell(edit_rc=code):
                with self.subTest(shell=shell, edit_rc=code):
                    self.assertEqual(rc, 0, err)
                    self.assertIn(
                        f"Warning: could not record branch/PR on TKT-9 "
                        f"(tkt edit exit {code})", out)
                    self.assertNotIn("does not store", out)
                    self.assertTrue(any(c[:2] == ["tkt", "comment"]
                                        for c in calls))

    def test_empty_pr_url_warns_and_never_calls_edit(self):
        for shell, (rc, out, err, calls) in self.each_shell(pr_url=""):
            with self.subTest(shell=shell):
                self.assertEqual(rc, 0, err)
                self.assertEqual(record_calls(calls), [])
                self.assertIn("Warning: no PR URL", out)
                self.assertNotIn("does not store", out)

    def test_exit_3_under_set_e_does_not_abort(self):
        for shell, (rc, out, err, calls) in self.each_shell(
                edit_rc=3, prefix="set -e\n"):
            with self.subTest(shell=shell):
                self.assertEqual(rc, 0, err)
                self.assertIn("does not store", out)
                self.assertTrue(any(c[:2] == ["tkt", "comment"] for c in calls))

    def test_no_branch_under_set_e_does_not_abort(self):
        # A bare `BRANCH=$(git symbolic-ref ...)` exits non-zero when there
        # is no branch, which `set -e` turns into an abort before the record.
        for head in ("detached", "none"):
            for shell, (rc, out, err, calls) in self.each_shell(
                    head=head, prefix="set -e\n"):
                with self.subTest(shell=shell, head=head):
                    self.assertEqual(rc, 0, err)
                    self.assertEqual(
                        record_calls(calls),
                        [["tkt", "edit", "TKT-9", "--add-pr", PR_URL]])

    def test_full_sdlc_records_before_the_review_transition(self):
        if not shutil.which("jq"):
            self.skipTest("needs jq")
        for shell, (rc, out, err, calls) in self.each_shell(cls="full_sdlc"):
            with self.subTest(shell=shell):
                self.assertEqual(rc, 0, err)
                verbs = [c[1] for c in calls if c[0] == "tkt"]
                rec = calls.index(record_calls(calls)[0])
                trans = calls.index(["tkt", "transition", "TKT-9", "review"])
                self.assertLess(rec, trans, verbs)


if __name__ == "__main__":
    unittest.main()
