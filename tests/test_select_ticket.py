"""Regression tests for TKT-63: select-ticket must not swallow `tkt` errors.

Step 2 ran `tkt list ... 2>/dev/null || continue`, so a backend or config
failure looked like an undefined tier and selection fell through to a lower
tier, or reported "nothing to work on". A failed `tkt blockers` produced no
count, which read as unblocked.

These tests run step 2's shell block, extracted from the canonical SKILL.md,
under every available shell against a stub `tkt` that plays back a scripted
exit code and output per call. The mirrors are checked for the same rules.
Needs `jq`. Run from the repo root:

    python3 -m unittest tests.test_select_ticket
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CANONICAL = ROOT / "skills/select-ticket/SKILL.md"

FULL_COPIES = [".agents/skills/select-ticket/SKILL.md",
               ".kiro/skills/select-ticket/SKILL.md"]
CONDENSED_MIRRORS = [
    ".agents/workflows/select-ticket.md",
    ".augment/commands/select-ticket.md",
    ".clinerules/workflows/select-ticket.md",
    ".continue/prompts/select-ticket.prompt",
    ".cursor/commands/select-ticket.md",
    ".cursor/skills/select-ticket/SKILL.md",
    ".gemini/commands/select-ticket.toml",
    ".github/prompts/select-ticket.prompt.md",
    ".opencode/commands/select-ticket.md",
    ".windsurf/workflows/select-ticket.md",
]

SHELLS = [s for s in ("bash", "zsh", "dash") if shutil.which(s)]

# Plays back SPEC (a JSON file named by $STUB_SPEC) and logs each call.
STUB = f"""#!{sys.executable}
import json, os, sys
spec = json.load(open(os.environ["STUB_SPEC"]))
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
verb, arg = sys.argv[1], (sys.argv[2:] or [""])[0]
if verb == "cfg":
    tier = arg.removeprefix("queries.tier")
    rc = spec["cfg"].get(tier, 4)
    if rc == 4:
        sys.stderr.write(f"tkt: config key not found: {{arg}}\\n")
    elif rc:
        sys.stderr.write("tkt: config is broken\\n")
    sys.exit(rc)
if verb == "list":
    r = spec["list"][sys.argv[3]]
elif verb == "schedule":
    r = {{"out": json.dumps({{"enabled": False, "in_window": False, "label": None}})}}
elif verb == "blockers":
    r = spec.get("blockers", {{}}).get(arg, {{"out": "[]"}})
else:
    sys.exit(64)
sys.stdout.write(r.get("out", ""))
sys.stderr.write(r.get("err", ""))
sys.exit(r.get("rc", 0))
"""


def step2_block() -> str:
    text = CANONICAL.read_text(encoding="utf-8")
    section = text[text.index("### 2. Run tiers"):text.index("### 3.")]
    m = re.search(r"```shell\n(.*?)\n```", section, re.DOTALL)
    assert m, "no shell block in step 2"
    return m.group(1)


def step3_block() -> str:
    text = CANONICAL.read_text(encoding="utf-8")
    section = text[text.index("### 3."):text.index("### Recommendation")]
    m = re.search(r"```shell\n(.*?)\n\s*```", section, re.DOTALL)
    assert m, "no shell block in step 3"
    return m.group(1)


def tickets(*keys):
    # Multi-line bodies and backslashes are normal ticket content; zsh's and
    # dash's echo would expand the escapes and break the JSON.
    return json.dumps([{"key": k, "summary": f"{k} C:\\temp\\new",
                        "description": "line one\nline two\ttabbed \\c"}
                       for k in keys])


@unittest.skipUnless(SHELLS and shutil.which("jq"), "needs a POSIX shell and jq")
class Step2(unittest.TestCase):
    def run_step2(self, spec, prefix="", then_step3=False):
        """{shell: (exit code, stdout, stderr, selected tier, candidate keys)}."""
        results = {}
        for shell in SHELLS:
            with tempfile.TemporaryDirectory() as tmp:
                t = Path(tmp)
                (t / "bin").mkdir()
                stub = t / "bin/tkt"
                stub.write_text(STUB)
                stub.chmod(0o755)
                (t / "spec.json").write_text(json.dumps(spec))
                block = prefix + step2_block()
                if then_step3:
                    block += "\n" + step3_block()
                block = block.replace("/tmp/", f"{tmp}/")
                env = dict(os.environ, PATH=f"{t / 'bin'}:{os.environ['PATH']}",
                           STUB_SPEC=str(t / "spec.json"),
                           STUB_LOG=str(t / "calls.log"))
                env.pop("TKT", None)
                p = subprocess.run([shell, "-c", block], env=env, text=True,
                                   capture_output=True, timeout=60)
                tier = (t / "tkt_tier").read_text().strip() \
                    if (t / "tkt_tier").exists() else None
                keys = [c["key"] for c in json.loads(
                    (t / "tkt_candidates.json").read_text())] \
                    if (t / "tkt_candidates.json").exists() else None
                results[shell] = (p.returncode, p.stdout, p.stderr, tier, keys)
        return results

    def test_list_failure_stops_with_tkts_error(self):
        spec = {"cfg": {"1": 0, "2": 0},
                "list": {"1": {"rc": 3, "err": "tkt: jira: 503 Service Unavailable\n"},
                         "2": {"out": tickets("T-2")}}}
        for shell, (rc, out, err, tier, keys) in self.run_step2(spec).items():
            with self.subTest(shell=shell):
                self.assertNotEqual(rc, 0)
                self.assertIn("503 Service Unavailable", err)
                self.assertIn("STOP:", err)
                self.assertNotIn("TIER=", out)       # did not fall to tier 2
                self.assertIsNone(tier)

    def test_undefined_tier_is_skipped_quietly(self):
        spec = {"cfg": {"2": 0}, "list": {"2": {"out": tickets("T-2")}}}
        for shell, (rc, out, err, tier, keys) in self.run_step2(spec).items():
            with self.subTest(shell=shell):
                self.assertEqual(rc, 0, err)
                self.assertEqual(tier, "2")
                self.assertEqual(keys, ["T-2"])
                self.assertNotIn("config key not found", err)

    def test_config_error_stops(self):
        spec = {"cfg": {"1": 2}, "list": {}}
        for shell, (rc, out, err, tier, keys) in self.run_step2(spec).items():
            with self.subTest(shell=shell):
                self.assertNotEqual(rc, 0)
                self.assertIn("config is broken", err)
                self.assertIn("STOP:", err)
                self.assertIsNone(tier)

    def test_non_json_list_output_stops(self):
        spec = {"cfg": {"1": 0}, "list": {"1": {"out": "<html>login</html>"}}}
        for shell, (rc, out, err, tier, keys) in self.run_step2(spec).items():
            with self.subTest(shell=shell):
                self.assertNotEqual(rc, 0)
                self.assertIn("STOP:", err)

    def test_empty_tier_falls_through(self):
        spec = {"cfg": {"1": 0, "2": 0},
                "list": {"1": {"out": "[]"}, "2": {"out": tickets("T-2")}}}
        for shell, (rc, out, err, tier, keys) in self.run_step2(spec).items():
            with self.subTest(shell=shell):
                self.assertEqual(rc, 0, err)
                self.assertEqual(tier, "2")

    def test_blocker_check_failure_excludes_with_warning(self):
        spec = {"cfg": {"1": 0},
                "list": {"1": {"out": tickets("T-1", "T-2", "T-3", "T-4")}},
                "blockers": {"T-1": {"rc": 3, "err": "tkt: rate limited\n"},
                             "T-2": {"out": "not json"},
                             "T-3": {"out": json.dumps([{"key": "X-1"}])},
                             "T-4": {"out": "[]"}}}
        for shell, (rc, out, err, tier, keys) in self.run_step2(spec).items():
            with self.subTest(shell=shell):
                self.assertEqual(rc, 0, err)
                self.assertEqual(keys, ["T-4"])      # T-3 blocked, T-1/T-2 unknown
                self.assertIn("rate limited", err)
                self.assertIn("could not check blockers for T-1", err)
                self.assertIn("could not check blockers for T-2", err)
                self.assertNotIn("T-3", err)          # plainly blocked: no warning

    def test_all_candidates_unknown_is_not_selected(self):
        spec = {"cfg": {"1": 0},
                "list": {"1": {"out": tickets("T-1")}},
                "blockers": {"T-1": {"rc": 3}}}
        for shell, (rc, out, err, tier, keys) in self.run_step2(spec).items():
            with self.subTest(shell=shell):
                self.assertNotEqual(rc, 0)
                self.assertIsNone(tier)
                self.assertIn("could not check blockers for T-1", err)
                self.assertIn("STOP:", err)

    def test_unknown_blockers_do_not_fall_through_to_a_lower_tier(self):
        spec = {"cfg": {"1": 0, "2": 0},
                "list": {"1": {"out": tickets("T-1")}, "2": {"out": tickets("T-9")}},
                "blockers": {"T-1": {"rc": 3, "err": "tkt: rate limited\n"}}}
        for shell, (rc, out, err, tier, keys) in self.run_step2(spec).items():
            with self.subTest(shell=shell):
                self.assertNotEqual(rc, 0)
                self.assertIsNone(tier)
                self.assertNotIn("TIER=2", out)

    def test_undefined_tier_is_skipped_under_errexit(self):
        spec = {"cfg": {"2": 0}, "list": {"2": {"out": tickets("T-2")}}}
        for shell, (rc, out, err, tier, keys) in self.run_step2(
                spec, prefix="set -e\n").items():
            with self.subTest(shell=shell):
                self.assertEqual(rc, 0, err)
                self.assertEqual(tier, "2")

    def test_non_array_list_output_stops(self):
        # Also under errexit, where an unguarded `LEN=$(jq ...)` would kill
        # the block before it could print STOP.
        for body in ("null", "{}", '"x"', "<html>login</html>"):
            for prefix in ("", "set -e\n"):
                spec = {"cfg": {"1": 0}, "list": {"1": {"out": body}}}
                for shell, (rc, out, err, tier, keys) in self.run_step2(
                        spec, prefix=prefix).items():
                    with self.subTest(shell=shell, body=body, errexit=bool(prefix)):
                        self.assertNotEqual(rc, 0)
                        self.assertIn("STOP:", err)

    def test_escaped_ticket_content_survives_both_steps(self):
        spec = {"cfg": {"1": 0}, "list": {"1": {"out": tickets("T-1", "T-2")}}}
        for shell, (rc, out, err, tier, keys) in self.run_step2(
                spec, then_step3=True).items():
            with self.subTest(shell=shell):
                self.assertEqual(rc, 0, err)
                self.assertEqual(keys, ["T-1", "T-2"])
                self.assertIn("SELECTED: T-1", out)
                self.assertNotIn("parse error", err)


class Mirrors(unittest.TestCase):
    def test_full_copies_match_canonical(self):
        canonical = CANONICAL.read_bytes()
        for rel in FULL_COPIES:
            with self.subTest(copy=rel):
                self.assertEqual((ROOT / rel).read_bytes(), canonical)

    def test_condensed_mirrors_state_both_rules(self):
        for rel in CONDENSED_MIRRORS:
            with self.subTest(mirror=rel):
                text = (ROOT / rel).read_text(encoding="utf-8")
                self.assertIn("`tkt cfg queries.tierN` exits 4", text)
                self.assertIn("stops selection", text)
                self.assertIn("never treated as unblocked", text)

    def test_gemini_mirror_is_still_valid_toml(self):
        tomllib.loads((ROOT / ".gemini/commands/select-ticket.toml").read_text())

    def test_canonical_block_no_longer_discards_list_errors(self):
        self.assertNotIn("2>/dev/null) || continue", step2_block())


if __name__ == "__main__":
    unittest.main()
