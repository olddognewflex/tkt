"""TKT-105: route a pipeline phase to a harness command and model.

`[harnesses]` names harness commands, `[routes]` maps a phase (skill name)
to a harness and a pinned model or a tier, and `tkt cfg route <phase>`
resolves it: route, then the skill's `model_tier` through `[models]`, then
`[run].harness_cmd`. Both tables are validated on use. Offline.

    python3 -m unittest tests.test_routing
"""
import contextlib
import io
import json
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import cli, routing  # noqa: E402
from core.config import Config  # noqa: E402
from core.errors import ConfigError, NotFoundError  # noqa: E402

CLAUDE = "claude -p {prompt} --model {model} --permission-mode acceptEdits"
CODEX = "codex exec --model {model} --sandbox read-only {prompt}"
HARNESSES = {"claude": {"cmd": CLAUDE}, "codex": {"cmd": CODEX}}
MODELS = {"cheap": "m-cheap", "standard": "m-standard", "deep": "m-deep"}
DEFAULT_CMD = "claude -p {prompt} --permission-mode acceptEdits"


def config(harnesses=None, routes=None, models=None, harness_cmd=None):
    d = {"ticketing": {"provider": "markdown", "project": "TKT"},
         "board": {"roles": {"todo": "To Do", "done": "Done"}}}
    if harnesses is not None:
        d["harnesses"] = harnesses
    if routes is not None:
        d["routes"] = routes
    if models is not None:
        d["models"] = models
    if harness_cmd is not None:
        d["run"] = {"harness_cmd": harness_cmd}
    return Config(d, Path("/nonexistent/.sdlc/config.toml"))


class Resolve(unittest.TestCase):

    def test_route_with_pinned_model(self):
        c = config(HARNESSES, {"self-review": {"harness": "codex", "model": "gpt-5.5"}},
                   MODELS, DEFAULT_CMD)
        r = routing.resolve(c, "self-review")
        self.assertEqual(r, {
            "phase": "self-review", "harness": "codex", "model": "gpt-5.5",
            "tier": "", "source": "route",
            "cmd": "codex exec --model gpt-5.5 --sandbox read-only {prompt}"})

    def test_route_with_tier_reads_models(self):
        c = config(HARNESSES, {"plan-ticket": {"harness": "claude", "tier": "deep"}}, MODELS)
        r = routing.resolve(c, "plan-ticket")
        self.assertEqual((r["source"], r["harness"], r["tier"], r["model"]),
                         ("route", "claude", "deep", "m-deep"))
        self.assertIn("--model m-deep", r["cmd"])
        self.assertIn("{prompt}", r["cmd"])

    def test_route_tier_without_a_models_entry_is_a_config_error(self):
        c = config(HARNESSES, {"plan-ticket": {"harness": "claude", "tier": "deep"}},
                   {"cheap": "m-cheap"})
        with self.assertRaises(ConfigError) as cm:
            routing.resolve(c, "plan-ticket")
        self.assertIn("[models].deep", str(cm.exception))

    def test_harness_only_route_uses_the_skills_tier(self):
        c = config(HARNESSES, {"plan-ticket": {"harness": "claude"},
                               "self-review": {"harness": "codex"}}, MODELS)
        r = routing.resolve(c, "plan-ticket")
        self.assertEqual((r["source"], r["tier"], r["model"]), ("route", "deep", "m-deep"))
        r = routing.resolve(c, "self-review")
        self.assertEqual((r["tier"], r["model"]), ("standard", "m-standard"))
        self.assertTrue(r["cmd"].startswith("codex exec --model m-standard "))

    def test_route_without_harness_uses_run_harness_cmd(self):
        c = config(HARNESSES, {"self-review": {"model": "x"}}, None, DEFAULT_CMD)
        r = routing.resolve(c, "self-review")
        self.assertEqual((r["harness"], r["model"], r["cmd"]), ("", "x", DEFAULT_CMD))

    def test_unrouted_phase_with_skill_tier_and_models(self):
        c = config(HARNESSES, {}, MODELS, DEFAULT_CMD)
        r = routing.resolve(c, "plan-ticket")
        self.assertEqual(r, {"phase": "plan-ticket", "harness": "", "model": "m-deep",
                             "tier": "deep", "cmd": DEFAULT_CMD, "source": "tier"})

    def test_unrouted_tier_source_without_harness_cmd(self):
        r = routing.resolve(config(models=MODELS), "select-ticket")
        self.assertEqual((r["source"], r["model"], r["cmd"]), ("tier", "m-cheap", ""))

    def test_unrouted_without_models_falls_back_to_default(self):
        r = routing.resolve(config(harness_cmd=DEFAULT_CMD), "plan-ticket")
        self.assertEqual((r["source"], r["model"], r["tier"], r["harness"], r["cmd"]),
                         ("default", "", "deep", "", DEFAULT_CMD))

    def test_unknown_phase_with_harness_cmd_is_default(self):
        r = routing.resolve(config(models=MODELS, harness_cmd=DEFAULT_CMD), "no-such-skill")
        self.assertEqual((r["source"], r["tier"], r["model"]), ("default", "", ""))

    def test_nothing_configured_is_not_found(self):
        for c in (config(), config(models={"cheap": "m"})):
            with self.assertRaises(NotFoundError) as cm:
                routing.resolve(c, "plan-ticket")
            self.assertIn("no route for phase 'plan-ticket'", str(cm.exception))

    def test_backwards_compatible_with_run_and_models_only(self):
        c = config(models=MODELS, harness_cmd=DEFAULT_CMD)
        self.assertEqual(c.get("models.deep"), "m-deep")
        self.assertEqual(routing.resolve(c, "plan-ticket")["source"], "tier")
        self.assertIsNone(routing.doctor_check(c))

    def test_model_placeholder_without_a_model_is_a_config_error(self):
        c = config(harness_cmd="claude -p {prompt} --model {model}")
        with self.assertRaises(ConfigError) as cm:
            routing.resolve(c, "no-such-skill")
        self.assertIn("{model}", str(cm.exception))
        c = config(HARNESSES, {"no-such-skill": {"harness": "claude"}})
        with self.assertRaises(ConfigError):
            routing.resolve(c, "no-such-skill")

    def test_model_is_shell_quoted(self):
        c = config(HARNESSES, {"self-review": {"harness": "codex", "model": "a b;rm -rf x"}})
        cmd = routing.resolve(c, "self-review")["cmd"]
        self.assertIn("--model 'a b;rm -rf x' ", cmd)
        self.assertEqual(shlex.split(cmd)[3], "a b;rm -rf x")

    def test_every_model_placeholder_is_filled(self):
        h = {"x": {"cmd": "x {model} --again {model} {prompt}"}}
        r = routing.resolve(config(h, {"p": {"harness": "x", "model": "m"}}), "p")
        self.assertEqual(r["cmd"], "x m --again m {prompt}")


class Validation(unittest.TestCase):

    def assertConfigError(self, c, needle, phase="self-review"):
        with self.assertRaises(ConfigError) as cm:
            routing.resolve(c, phase)
        self.assertIn(needle, str(cm.exception))

    def test_model_and_tier(self):
        self.assertConfigError(config(HARNESSES, {"self-review": {
            "harness": "codex", "model": "m", "tier": "deep"}}), "not both")

    def test_unknown_harness(self):
        self.assertConfigError(config(HARNESSES, {"self-review": {"harness": "gemini"}}),
                               "Defined: claude, codex")

    def test_cmd_missing_prompt(self):
        self.assertConfigError(config({"codex": {"cmd": "codex exec"}}),
                               "[harnesses.codex].cmd must contain {prompt}")

    def test_cmd_unbalanced_quotes(self):
        self.assertConfigError(config({"codex": {"cmd": "codex 'exec {prompt}"}}),
                               "[harnesses.codex].cmd does not parse")

    def test_cmd_empty_or_not_a_string(self):
        for bad in ("", "  ", 3, ["codex", "{prompt}"]):
            with self.assertRaises(ConfigError, msg=repr(bad)):
                routing.resolve(config({"codex": {"cmd": bad}}), "x")
        self.assertConfigError(config({"codex": {}}), "non-empty string")

    def test_unknown_route_key(self):
        self.assertConfigError(config(HARNESSES, {"self-review": {"harnes": "codex"}}),
                               "[routes].self-review.harnes: unknown key")

    def test_unknown_harness_key(self):
        self.assertConfigError(config({"codex": {"cmd": CODEX, "args": "x"}}),
                               "[harnesses.codex].args: unknown key. Known: cmd")

    def test_harness_not_a_table(self):
        self.assertConfigError(config({"codex": CODEX}), "[harnesses.codex] must be a table")
        self.assertConfigError(config("codex"), "[harnesses] must be a table")

    def test_empty_route_table(self):
        self.assertConfigError(config(HARNESSES, {"self-review": {}}), "is empty")

    def test_bad_tier_value(self):
        for bad in ("Deep", "huge", 1, ""):
            with self.assertRaises(ConfigError, msg=repr(bad)):
                routing.resolve(config(HARNESSES, {"self-review": {"tier": bad}},
                                       MODELS), "self-review")

    def test_bad_model_value(self):
        for bad in ("", 5):
            with self.assertRaises(ConfigError, msg=repr(bad)):
                routing.resolve(config(routes={"self-review": {"model": bad}}),
                                "self-review")

    def test_routes_not_a_table(self):
        self.assertConfigError(config(routes="codex"), "[routes] must be a table")
        self.assertConfigError(config(routes={"self-review": "codex"}),
                               "[routes].self-review must be a table")

    def test_bad_route_elsewhere_breaks_a_valid_phase(self):
        c = config(HARNESSES, {"self-review": {"harness": "codex", "model": "m"},
                               "plan-ticket": {"harness": "gemini"}})
        self.assertConfigError(c, "[routes].plan-ticket.harness")

    def test_bad_harness_elsewhere_breaks_a_valid_phase(self):
        h = dict(HARNESSES, broken={"cmd": "nope"})
        c = config(h, {"self-review": {"harness": "codex", "model": "m"}})
        self.assertConfigError(c, "[harnesses.broken]")

    def test_bad_table_does_not_break_config_load(self):
        c = config({"codex": "not a table"}, harness_cmd=DEFAULT_CMD)
        self.assertEqual(c.get("run.harness_cmd"), DEFAULT_CMD)


class ModelValues(unittest.TestCase):

    def test_braces_in_a_route_model_are_rejected(self):
        c = config(HARNESSES, {"self-review": {"harness": "codex", "model": "{prompt}"}})
        with self.assertRaises(ConfigError) as cm:
            routing.resolve(c, "self-review")
        self.assertIn("braces", str(cm.exception))

    def test_braces_in_a_models_value_are_rejected(self):
        c = config(HARNESSES, {"plan-ticket": {"harness": "claude", "tier": "deep"}},
                   models={"deep": "x}{prompt"})
        with self.assertRaises(ConfigError):
            routing.resolve(c, "plan-ticket")

    def test_non_string_models_value_names_the_type_problem(self):
        c = config(HARNESSES, {"plan-ticket": {"harness": "claude", "tier": "deep"}},
                   models={"deep": 3})
        with self.assertRaises(ConfigError) as cm:
            routing.resolve(c, "plan-ticket")
        self.assertIn("[models].deep must be a non-empty string", str(cm.exception))


    def test_empty_models_value_is_unset(self):
        c = config(models={"cheap": ""}, harness_cmd=DEFAULT_CMD)
        self.assertEqual(routing.resolve(c, "select-ticket")["source"], "default")


class SkillTier(unittest.TestCase):

    def test_shipped_skills(self):
        self.assertEqual(routing.skill_tier("plan-ticket"), "deep")
        self.assertEqual(routing.skill_tier("self-review"), "standard")
        self.assertEqual(routing.skill_tier("select-ticket"), "cheap")

    def test_missing_skill(self):
        self.assertEqual(routing.skill_tier("no-such-skill"), "")

    def test_unsafe_names_are_rejected(self):
        for bad in ("", ".", "..", "../x", "../skills/plan-ticket", "a/b",
                    "a\\b", ".hidden", "/etc", "D:plan-ticket", "C:", "-x"):
            self.assertEqual(routing.skill_tier(bad), "", bad)

    def test_frontmatter_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = {
                "ok": "---\nname: ok\nmodel_tier: 'deep'\n---\nbody\n",
                "none": "---\nname: none\n---\nmodel_tier: deep\n",
                "nofm": "model_tier: deep\n",
                "bad": "---\nmodel_tier: gigantic\n---\n",
            }
            for name, text in cases.items():
                (root / name).mkdir()
                (root / name / "SKILL.md").write_text(text)
            orig = routing.SKILLS_DIR
            routing.SKILLS_DIR = root
            try:
                got = {n: routing.skill_tier(n) for n in cases}
            finally:
                routing.SKILLS_DIR = orig
        self.assertEqual(got, {"ok": "deep", "none": "", "nofm": "", "bad": ""})


HEAD = '[ticketing]\nprovider = "markdown"\nproject = "TKT"\n[board.roles]\ntodo = "To Do"\n'
ROUTED = HEAD + f"""
[models]
deep = "m-deep"

[harnesses.codex]
cmd = "{CODEX}"

[routes]
self-review = {{ harness = "codex", model = "gpt-5.5" }}
"""


class Cli(unittest.TestCase):

    def run_cli(self, toml, *argv):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(toml)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["--config", str(path), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_route_json(self):
        code, out, _ = self.run_cli(ROUTED, "cfg", "route", "self-review", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {
            "phase": "self-review", "harness": "codex", "model": "gpt-5.5",
            "tier": "", "source": "route",
            "cmd": "codex exec --model gpt-5.5 --sandbox read-only {prompt}"})

    def test_route_human(self):
        code, out, _ = self.run_cli(ROUTED, "cfg", "route", "self-review")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual([ln.split(":")[0] for ln in lines],
                         ["phase", "source", "harness", "model", "tier", "cmd"])
        self.assertIn("source:  route", lines)
        self.assertIn("tier:", lines)

    def test_nothing_configured_exits_4(self):
        code, out, err = self.run_cli(HEAD, "cfg", "route", "plan-ticket")
        self.assertEqual((code, out), (4, ""))
        self.assertIn("no route for phase", err)

    def test_invalid_exits_2(self):
        code, _, err = self.run_cli(
            ROUTED + 'plan-ticket = { harness = "gemini" }\n',
            "cfg", "route", "self-review")
        self.assertEqual(code, 2)
        self.assertIn("gemini", err)

    def test_route_without_phase_exits_64(self):
        code, _, err = self.run_cli(ROUTED, "cfg", "route")
        self.assertEqual(code, 64)
        self.assertIn("requires a PHASE", err)

    def test_phase_with_another_key_exits_64(self):
        code, _, _ = self.run_cli(HEAD + '[vcs]\nrepo = "o/r"\n', "cfg", "vcs.repo", "extra")
        self.assertEqual(code, 64)

    def test_plain_cfg_still_works(self):
        code, out, _ = self.run_cli(ROUTED, "cfg", "models.deep")
        self.assertEqual((code, out), (0, "m-deep\n"))


MARKDOWN_TOML = """\
[ticketing]
provider = "markdown"
project = "TKT"

[markdown]
board_dir = "{board}"
me = "tester"

[board.roles]
todo = "To Do"
done = "Done"

[queries]
tier1 = 'status = "To Do"'
{extra}
"""

MISSING = "tkt-test-no-such-harness-binary"


class Doctor(unittest.TestCase):

    def test_absent_tables(self):
        self.assertIsNone(routing.doctor_check(config(models=MODELS, harness_cmd=DEFAULT_CMD)))

    def test_valid_with_missing_executable_is_ok_with_warning(self):
        h = dict(HARNESSES, ghost={"cmd": f"{MISSING} {{prompt}}"},
                 unused={"cmd": f"{MISSING}-2 {{prompt}}"})
        c = config(h, {"self-review": {"harness": "ghost", "model": "m"},
                       "plan-ticket": {"harness": "ghost", "model": "m"}})
        row = routing.doctor_check(c)
        self.assertEqual(row.name, "routing")
        self.assertTrue(row.ok)
        self.assertIn("2 routes, 4 harnesses", row.detail)
        self.assertIn("warning: ghost not on PATH (routes: self-review, plan-ticket)",
                      row.detail)
        self.assertNotIn("unused", row.detail)

    def test_invalid_fails_the_row(self):
        row = routing.doctor_check(config(HARNESSES, {"self-review": {"harness": "nope"}}))
        self.assertFalse(row.ok)
        self.assertIn("nope", row.detail)

    def test_routes_only(self):
        row = routing.doctor_check(config(routes={"self-review": {"model": "m"}}))
        self.assertEqual((row.ok, row.detail), (True, "1 route, 0 harnesses"))

    def test_shell_forms_resolve_to_the_real_program(self):
        # Harness cmds run under a shell: env prefixes, ~ and $VAR are valid.
        os.environ["TKT_TEST_BIN"] = "/opt/bin"
        self.addCleanup(os.environ.pop, "TKT_TEST_BIN", None)
        cmds = {"envy": "FOO=1 BAR=x codex {prompt}",
                "builtin": "exec command codex {prompt}",
                "home": "~/bin/codex {prompt}",
                "var": '"$TKT_TEST_BIN/codex" {prompt}'}
        seen = []
        def which(exe):
            seen.append(exe)
            return exe
        c = config({n: {"cmd": v} for n, v in cmds.items()},
                   {f"p-{n}": {"harness": n, "model": "m"} for n in cmds})
        row = routing.doctor_check(c, which=which)
        self.assertEqual((row.ok, "warning" in row.detail), (True, False), row.detail)
        self.assertEqual(sorted(seen), sorted(
            ["codex", "codex", os.path.expanduser("~/bin/codex"), "/opt/bin/codex"]))

    def test_unexpandable_executable_is_not_reported_missing(self):
        c = config({"x": {"cmd": '"$TKT_UNSET_VAR_FOR_TEST/codex" {prompt}'}},
                   {"self-review": {"harness": "x", "model": "m"}})
        row = routing.doctor_check(c, which=lambda exe: None)
        self.assertTrue(row.ok)
        self.assertIn("could not check x's executable", row.detail)
        self.assertNotIn("not on PATH", row.detail)

    def test_route_that_fails_every_lookup_fails_the_row(self):
        row = routing.doctor_check(config(
            HARNESSES, {"ci-fix": {"harness": "codex", "tier": "cheap"}}))
        self.assertFalse(row.ok)
        self.assertIn("[models].cheap", row.detail)

    def test_end_to_end_missing_harness_does_not_fail_doctor(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        board = Path(tmp.name) / "board"
        board.mkdir()
        cfg = Path(tmp.name) / "config.toml"
        cfg.write_text(MARKDOWN_TOML.format(board=board, extra=f"""
[harnesses.ghost]
cmd = "{MISSING} {{prompt}}"

[routes]
self-review = {{ harness = "ghost", model = "m" }}
"""))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["--config", str(cfg), "--json", "doctor"])
        rows = {c["name"]: c for c in json.loads(out.getvalue())}
        self.assertTrue(rows["routing"]["ok"])
        self.assertIn("ghost not on PATH", rows["routing"]["detail"])
        self.assertEqual(code, 0, [r for r in rows.values() if not r["ok"]])


if __name__ == "__main__":
    unittest.main()
