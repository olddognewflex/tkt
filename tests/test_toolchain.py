"""TKT-104: `toolchain.detect` merges Makefile targets into a language probe's
result, and `apply_to_config` / `core.tomledit` rewrite config text without
disturbing comments, padding or other tables. Offline.

    python3 -m unittest tests.test_toolchain
"""
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import tomledit, toolchain  # noqa: E402

EXAMPLE = (Path(__file__).resolve().parent.parent / "examples"
           / "config.markdown.toml").read_text()


class ApplyToConfig(unittest.TestCase):
    def test_rewrites_only_build_and_keeps_padding(self):
        out = toolchain.apply_to_config(EXAMPLE, {"build": "go build ./..."})
        self.assertIn('build     = "go build ./..."\n', out)
        self.assertEqual(out.replace('"go build ./..."', '"make build"'), EXAMPLE)

    def test_appends_missing_key_inside_table(self):
        text = '[build]\nbuild = "x"\n\n[run]\na = "b"\n'
        out = toolchain.apply_to_config(text, {"lint": "make lint"})
        self.assertEqual(out, '[build]\nbuild = "x"\nlint      = "make lint"\n\n[run]\na = "b"\n')

    def test_adds_build_table_when_absent(self):
        out = toolchain.apply_to_config('[ticketing]\nprovider = "markdown"\n',
                                        {"test": "go test ./..."})
        self.assertEqual(tomllib.loads(out)["build"], {"test": "go test ./..."})

    def test_escapes_quotes(self):
        cmd = 'test -z "$(gofmt -l .)"'
        out = toolchain.apply_to_config(EXAMPLE, {"lint": cmd})
        self.assertEqual(tomllib.loads(out)["build"]["lint"], cmd)

    def test_keeps_trailing_comment(self):
        out = toolchain.apply_to_config('[build]\ntest = "true"   # run it\n',
                                        {"test": "make test"})
        self.assertEqual(out, '[build]\ntest = "make test"   # run it\n')

    def test_ignores_non_build_keys(self):
        self.assertEqual(toolchain.apply_to_config(EXAMPLE, {"deploy": "x"}), EXAMPLE)


class TomlEdit(unittest.TestCase):
    def test_replace_table_none_removes(self):
        text = '[a]\nx = "1"\n\n[b]\ny = "2"\n\n[c]\nz = "3"\n'
        out = tomledit.replace_table(text, "b", None)
        self.assertEqual(out, '[a]\nx = "1"\n\n[c]\nz = "3"\n')

    def test_replace_table_appends_missing(self):
        out = tomledit.replace_table('[a]\nx = "1"\n', "b", '[b]\ny = "2"\n')
        self.assertEqual(tomllib.loads(out), {"a": {"x": "1"}, "b": {"y": "2"}})

    def test_bracket_line_inside_array_is_not_a_header(self):
        text = '[a]\nxs = [\n  "p",\n[b]\n]\n\n[c]\nz = "3"\n'
        lines = text.splitlines(keepends=True)
        self.assertIsNone(tomledit.table_span(lines, "b"))
        self.assertEqual(tomledit.table_span(lines, "a"), (0, 5))

    def test_trailing_comment_group_stays_put(self):
        block = tomledit.table_block(EXAMPLE, "vcs")
        self.assertTrue(block.rstrip().endswith('merge          = "squash"'))
        self.assertNotIn("[schedule]", block)
        self.assertIn("# bdd", tomledit.table_block(EXAMPLE, "build"))

    def test_set_keys_non_string_value_replaced(self):
        out = tomledit.set_keys('[t]\nk = 3  # n\n', "t", {"k": "v"})
        self.assertEqual(tomllib.loads(out), {"t": {"k": "v"}})


class TomlEditEdges(unittest.TestCase):
    """QA Author — adversarial: header comments, [[array]] headers, EOF
    without newline, CRLF input."""

    def test_non_string_value_keeps_trailing_comment(self):
        for old, want in [('build = true  # run it\n', 'build = "x"  # run it\n'),
                          ('build = ["a#b"] # c\n', 'build = "x" # c\n'),
                          ('build = 3\n', 'build = "x"\n')]:
            with self.subTest(old):
                out = tomledit.set_keys("[build]\n" + old, "build", {"build": "x"})
                self.assertEqual(out, "[build]\n" + want)
                self.assertEqual(tomllib.loads(out), {"build": {"build": "x"}})

    def test_replaced_line_keeps_its_line_ending(self):
        text = '[build]\r\nbuild = true # c\r\nlint = "y"\r\n'
        out = tomledit.set_keys(text, "build", {"build": "x"})
        self.assertEqual(out, '[build]\r\nbuild = "x" # c\r\nlint = "y"\r\n')

    def test_header_with_trailing_comment(self):
        text = '[build]   # commands\nbuild = "x"\n\n[run]\na = "b"\n'
        out = tomledit.set_keys(text, "build", {"build": "y", "lint": "z"})
        self.assertEqual(tomllib.loads(out),
                         {"build": {"build": "y", "lint": "z"}, "run": {"a": "b"}})
        self.assertTrue(out.startswith("[build]   # commands\n"))

    def test_array_of_tables_header_ends_the_previous_table(self):
        text = '[build]\nbuild = "x"\n\n[[plugins]]\nname = "p"\n'
        self.assertEqual(tomledit.table_span(text.splitlines(True), "build"), (0, 2))
        out = tomledit.set_keys(text, "build", {"lint": "make lint"})
        self.assertEqual(tomllib.loads(out),
                         {"build": {"build": "x", "lint": "make lint"},
                          "plugins": [{"name": "p"}]})

    def test_array_of_tables_is_not_table_of_same_prefix(self):
        text = '[[build.steps]]\nrun = "a"\n'
        out = tomledit.set_keys(text, "build", {"lint": "make lint"})
        self.assertEqual(tomllib.loads(out)["build"]["lint"], "make lint")
        self.assertEqual(tomllib.loads(out)["build"]["steps"], [{"run": "a"}])

    def test_set_keys_appends_to_table_at_eof_without_newline(self):
        for text in ('[build]\nbuild = "x"', '[build]\nbuild = "x"   # c'):
            with self.subTest(text=text):
                out = tomledit.set_keys(text, "build", {"lint": "make lint"})
                self.assertEqual(tomllib.loads(out)["build"],
                                 {"build": "x", "lint": "make lint"})
                self.assertTrue(out.endswith('lint      = "make lint"\n'))

    def test_replace_table_removes_last_table(self):
        for text in ('[a]\nx = "1"\n\n[b]\ny = "2"\n', '[a]\nx = "1"\n\n[b]\ny = "2"'):
            with self.subTest(text=text):
                self.assertEqual(tomledit.replace_table(text, "b", None), '[a]\nx = "1"\n')

    def test_replace_last_table_without_newline(self):
        out = tomledit.replace_table('[a]\nx = "1"\n\n[b]\ny = "2"', "b", '[b]\ny = "3"\n')
        self.assertEqual(tomllib.loads(out), {"a": {"x": "1"}, "b": {"y": "3"}})

    def test_crlf_round_trips(self):
        text = ('# head\r\n[ticketing]   # t\r\nproject = "TKT"   # key\r\n\r\n'
                '[vcs]\r\nrepo = "x"\r\n\r\n[build]\r\nbuild = "make build"\r\n')
        out = tomledit.set_keys(text, "ticketing", {"project": "ABC"})
        out = tomledit.set_keys(out, "build", {"build": "go build", "lint": "make lint"})
        out = tomledit.replace_table(out, "vcs", '[vcs]\nrepo = "y"\n')
        self.assertEqual(tomllib.loads(out), {
            "ticketing": {"project": "ABC"}, "vcs": {"repo": "y"},
            "build": {"build": "go build", "lint": "make lint"}})
        self.assertIn('project = "ABC"   # key', out)
        self.assertEqual(tomledit.table_block(text, "vcs"), '[vcs]\r\nrepo = "x"\r\n')


class Detect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text=""):
        (self.root / name).write_text(text)

    def test_go_with_makefile_lint(self):
        self.write("go.mod", "module x\n")
        self.write("Makefile", "lint:\n\tgofmt -l .\n")
        found, label = toolchain.detect(self.root)
        self.assertEqual(found["lint"], "make lint")
        self.assertEqual(found["typecheck"], "go vet ./...")
        self.assertEqual(found["build"], "go build ./...")
        self.assertIn("make", label)
        self.assertEqual(list(found), list(toolchain.KEYS))

    def test_go_without_makefile_has_no_lint(self):
        self.write("go.mod", "module x\n")
        found, label = toolchain.detect(self.root)
        self.assertNotIn("lint", found)
        self.assertEqual(label, "go")

    def test_node_with_makefile_lint(self):
        self.write("package.json", '{"scripts": {"build": "tsc"}}')
        self.write("Makefile", "lint:\n\teslint .\nbuild:\n\tmake-it\n")
        found, label = toolchain.detect(self.root)
        self.assertEqual(found["build"], "npm run build")   # language wins
        self.assertEqual(found["lint"], "make lint")
        self.assertEqual(label, "node (npm) + make")

    def test_rust_without_clippy_gets_make_lint(self):
        self.write("Cargo.toml", "[package]\n")
        self.write("Makefile", "lint:\n\tcargo fmt --check\n")
        found, _ = toolchain.detect(self.root)
        self.assertEqual(found["lint"], "make lint")
        self.assertEqual(found["test"], "cargo test")

    def test_makefile_only_unchanged(self):
        self.write("Makefile", "build:\n\tx\ncheck:\n\ty\n")
        self.assertEqual(toolchain.detect(self.root),
                         ({"build": "make build", "test": "make check"}, "make"))

    def test_makefile_without_matching_targets(self):
        self.write("go.mod", "module x\n")
        self.write("Makefile", "release:\n\tx\n")
        self.assertEqual(toolchain.detect(self.root)[1], "go")

    # QA Author — adversarial: undecodable manifests must not crash init.
    def test_non_utf8_makefile_still_merged(self):
        self.write("package.json", '{"scripts": {"build": "tsc"}}')
        (self.root / "Makefile").write_bytes(b"# caf\xe9 \xff\nlint:\n\teslint .\n")
        found, label = toolchain.detect(self.root)
        self.assertEqual(found, {"build": "npm run build", "lint": "make lint"})
        self.assertEqual(label, "node (npm) + make")

    def test_non_utf8_manifests_do_not_crash(self):
        for name, raw in [("package.json", b'{"description": "caf\xe9\xff\xfe", "scripts": {}}'),
                          ("pyproject.toml", b'[project]\nname = "caf\xe9\xff\xfe"\n')]:
            with self.subTest(name), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                (root / name).write_bytes(raw)
                (root / "Makefile").write_bytes(b"test:\n\tx\n")
                found, _ = toolchain.detect(root)
                self.assertEqual(found, {"test": "make test"})

    def test_nothing(self):
        self.assertEqual(toolchain.detect(self.root), ({}, ""))


if __name__ == "__main__":
    unittest.main()
