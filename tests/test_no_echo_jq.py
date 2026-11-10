"""TKT-102: shipped shell snippets pipe JSON into jq with printf, never echo.

zsh's and dash's `echo` expand the `\\n` escapes inside JSON strings, so
`echo "$X" | jq` hands jq invalid JSON whenever a ticket field holds a line
break. Inside a loop guarded by `|| continue` that failure is silent: it made
select-ticket skip a whole tier.

Scans every tracked text file outside tests/. Offline.

    python3 -m unittest tests.test_no_echo_jq
"""
import re
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# `echo ...` whose output reaches jq through a pipeline, possibly via other
# filters. A pipe inside a `$(...)` in the echo's own arguments belongs to
# that substitution (`echo "x: $(printf ... | jq ...)"` is fine), so the
# echo's own segment may not contain `$(`.
ECHO_JQ = re.compile(r"\becho\b(?:(?!\$\()[^|\n])*\|(?:[^|\n]*\|)*\s*jq\b")


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, check=True,
                             capture_output=True, text=True).stdout
        rels = out.splitlines()
    except (OSError, subprocess.CalledProcessError):
        rels = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*")
                if p.is_file() and ".git" not in p.parts]
    # No suffix filter: mirrors use .md, .toml, .prompt and more. Binary
    # files are skipped where they fail to decode.
    return [ROOT / r for r in rels if not r.startswith("tests/")]


def logical_lines(text: str) -> list[tuple[int, str]]:
    """(first line number, line), with backslash continuations joined so an
    `echo "$X"` line ending in a backslash and a following `| jq` line are
    checked as one."""
    out, buf, start = [], "", 0
    for n, line in enumerate(text.splitlines(), 1):
        if not buf:
            start = n
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        out.append((start, buf + line))
        buf = ""
    if buf:
        out.append((start, buf))
    return out


class NoEchoIntoJq(unittest.TestCase):
    def test_pattern_catches_the_bad_forms(self):
        for bad in ('$(echo "$WL" | jq -r .human)',
                    "echo $OUT | jq 'length'",
                    'echo "$X"|jq .',
                    'echo -n "$X" | jq .',
                    'echo "Body: $X" | jq .',
                    'echo "$X" | tr -d x | jq .'):
            with self.subTest(bad=bad):
                self.assertTrue(ECHO_JQ.search(bad))
        [(_, joined)] = logical_lines('echo "$X" \\\n  | jq .')
        self.assertTrue(ECHO_JQ.search(joined))

    def test_pattern_allows_the_good_forms(self):
        for good in ("""$(printf '%s\\n' "$WL" | jq -r .human)""",
                     """echo "DEFERRED: $(printf '%s\\n' "$D" | jq -r .k)" """,
                     'echo "STOP: $1" >&2',
                     "jq -r .human <<<\"$WL\""):
            with self.subTest(good=good):
                self.assertFalse(ECHO_JQ.search(good))

    def test_no_tracked_file_pipes_echo_into_jq(self):
        hits = []
        for f in tracked_files():
            try:
                text = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for n, line in logical_lines(text):
                if ECHO_JQ.search(line):
                    hits.append(f"{f.relative_to(ROOT)}:{n}: {line.strip()}")
        self.assertEqual(hits, [], "use printf '%s\\n' \"$VAR\" | jq instead:\n"
                         + "\n".join(hits))


class TomlPromptsKeepPrintfIntact(unittest.TestCase):
    def test_printf_format_survives_toml_parsing(self):
        # In a TOML basic string `'%s\n'` becomes a real newline and splits
        # the command, so such prompts must use `'%s'`. .gemini/commands holds
        # the pack's only TOML prompts; widen this glob if that changes.
        files = sorted(ROOT.glob(".gemini/commands/*.toml"))
        self.assertTrue(files)
        for f in files:
            with self.subTest(f=f.name):
                prompt = tomllib.loads(f.read_text(encoding="utf-8")).get("prompt", "")
                for line in prompt.splitlines():
                    self.assertFalse(line.rstrip().endswith("printf '%s"),
                                     f"{f.name}: broken printf format: {line.strip()}")


if __name__ == "__main__":
    unittest.main()
