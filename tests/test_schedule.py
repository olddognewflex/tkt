"""Tests for the `[schedule]` table and `tkt schedule` (TKT-55).

Offline and clock-independent: every check passes an explicit time. Run from
the repo root:

    python3 -m unittest tests.test_schedule
"""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import schedule  # noqa: E402
from core.cli import main  # noqa: E402
from core.config import Config  # noqa: E402
from core.errors import ConfigError  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NY = ZoneInfo("America/New_York")


def cfg(table=None) -> Config:
    data = {"ticketing": {"provider": "markdown"}, "board": {"roles": {"todo": "To Do"}}}
    if table is not None:
        data["schedule"] = table
    return Config(data, Path("/nonexistent/.sdlc/config.toml"))


def at(day: str, hm: str, tz=NY) -> datetime:
    """A time on a fixed week: 2026-09-14 is a Monday."""
    offset = schedule.WEEKDAYS.index(day)
    h, m = map(int, hm.split(":"))
    return datetime(2026, 9, 14 + offset, h, m, tzinfo=tz)


def win(table, day, hm, tz=NY) -> bool:
    return schedule.in_window(schedule.load(cfg(table)), at(day, hm, tz))


BASE = {"after_hours_label": "after-hours", "timezone": "America/New_York"}


class TestOff(unittest.TestCase):
    def test_no_table(self):
        self.assertIsNone(schedule.load(cfg()))
        self.assertEqual(schedule.report(cfg(), at("wed", "12:00")),
                         {"enabled": False, "in_window": False, "label": None})

    def test_table_without_label(self):
        self.assertIsNone(schedule.load(cfg({"business_hours": "09:00-18:00"})))


class TestDayWindow(unittest.TestCase):
    CASES = [("wed", "12:00", True), ("wed", "08:59", False), ("wed", "09:00", True),
             ("wed", "17:59", True), ("wed", "18:00", False), ("sat", "12:00", False),
             ("sun", "12:00", False), ("mon", "09:00", True), ("fri", "17:59", True)]

    def test_defaults_are_nine_to_six_weekdays(self):
        for day, hm, want in self.CASES:
            with self.subTest(day=day, hm=hm):
                self.assertEqual(win(BASE, day, hm), want)

    def test_all_day(self):
        t = dict(BASE, business_hours="00:00-24:00")
        self.assertTrue(win(t, "wed", "00:00"))
        self.assertTrue(win(t, "wed", "23:59"))
        self.assertFalse(win(t, "sat", "12:00"))


class TestOvernight(unittest.TestCase):
    T = dict(BASE, business_hours="18:00-06:00")

    def test_window_belongs_to_the_day_it_starts(self):
        cases = [("wed", "23:00", True), ("thu", "05:59", True), ("thu", "06:00", False),
                 ("wed", "12:00", False), ("wed", "18:00", True),
                 ("fri", "23:00", True),
                 ("sat", "01:00", True),     # Friday night's window
                 ("sat", "23:00", False),    # Saturday is not a working day
                 ("mon", "01:00", False),    # Sunday night's window
                 ("mon", "18:00", True)]
        for day, hm, want in cases:
            with self.subTest(day=day, hm=hm):
                self.assertEqual(win(self.T, day, hm), want)


class TestTimezone(unittest.TestCase):
    def test_utc_input_is_judged_in_the_configured_zone(self):
        # 16:30 UTC on a September Wednesday is 12:30 in New York (EDT).
        self.assertTrue(win(BASE, "wed", "16:30", tz=timezone.utc))
        # 23:30 UTC Wednesday is 19:30 New York: after hours.
        self.assertFalse(win(BASE, "wed", "23:30", tz=timezone.utc))

    def test_day_is_the_local_day(self):
        # 02:00 UTC Saturday is still Friday 22:00 in New York.
        t = dict(BASE, business_hours="18:00-06:00", days=["fri"])
        self.assertTrue(win(t, "sat", "02:00", tz=timezone.utc))

    def test_unknown_zone_is_an_error_not_utc(self):
        with self.assertRaises(ConfigError):
            schedule.load(cfg(dict(BASE, timezone="America/New_Yrok")))


class TestParsing(unittest.TestCase):
    def test_hours_accepted_forms(self):
        for raw, want in [("09:00-18:00", (540, 1080)), ("9:00-18:00", (540, 1080)),
                          ("09:00 - 18:00", (540, 1080)), ("18:00-06:00", (1080, 360)),
                          ("00:00-24:00", (0, 1440))]:
            with self.subTest(raw=raw):
                self.assertEqual(schedule.parse_hours(raw), want)

    def test_hours_rejected_forms(self):
        for raw in ["0900", "09:00", "09:00-09:00", "25:00-18:00", "09:60-18:00",
                    "24:00-06:00", "09:00-24:30", "", None, 900]:
            with self.subTest(raw=raw), self.assertRaises(ConfigError):
                schedule.parse_hours(raw)

    def test_days_accepted_forms(self):
        want = ("mon", "wed", "fri")
        for raw in [["mon", "wed", "fri"], "mon wed fri", "Mon, Wed, Fri",
                    ["Monday", "WEDNESDAY", "fri"], "fri mon wed"]:
            with self.subTest(raw=raw):
                self.assertEqual(schedule.parse_days(raw), want)

    def test_days_rejected_forms(self):
        for raw in ["mon funday", [], "", ["mon", 3], 5]:
            with self.subTest(raw=raw), self.assertRaises(ConfigError):
                schedule.parse_days(raw)

    def test_non_string_label_is_an_error(self):
        with self.assertRaises(ConfigError):
            schedule.load(cfg(dict(BASE, after_hours_label=["after-hours"])))

    def test_non_table_schedule_is_an_error(self):
        with self.assertRaises(ConfigError):
            schedule.load(cfg("after-hours"))


class TestCli(unittest.TestCase):
    def _run(self, toml: str, *argv: str) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(toml)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(["--config", str(path), "schedule", *argv])
        return code, out.getvalue(), err.getvalue()

    HEAD = '[ticketing]\nprovider = "markdown"\n[board.roles]\ntodo = "To Do"\n'

    def test_json_in_window(self):
        code, out, _ = self._run(
            self.HEAD + '[schedule]\nafter_hours_label = "after-hours"\n'
                        'timezone = "America/New_York"\ndays = ["mon", "tue", "wed"]\n',
            "--json", "--at", "2026-09-16T16:30:00Z")
        self.assertEqual(code, 0)
        r = json.loads(out)
        self.assertEqual((r["enabled"], r["in_window"], r["label"]), (True, True, "after-hours"))
        self.assertEqual((r["local_day"], r["local_time"]), ("wed", "12:30"))
        self.assertEqual(r["days"], ["mon", "tue", "wed"])

    def test_naive_at_is_read_in_the_schedules_zone(self):
        code, out, _ = self._run(
            self.HEAD + '[schedule]\nafter_hours_label = "x"\ntimezone = "America/New_York"\n',
            "--json", "--at", "2026-09-16T09:30")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["local_time"], "09:30")

    def test_json_off_without_table(self):
        code, out, _ = self._run(self.HEAD, "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["enabled"], False)

    def test_malformed_config_exits_2(self):
        code, _, err = self._run(
            self.HEAD + '[schedule]\nafter_hours_label = "x"\nbusiness_hours = "0900"\n', "--json")
        self.assertEqual(code, 2)
        self.assertIn("business_hours", err)

    def test_bad_at_is_usage_error(self):
        code, _, _ = self._run(self.HEAD, "--at", "noon")
        self.assertEqual(code, 64)


MIRRORS = (".agents", ".augment", ".clinerules", ".continue", ".cursor", ".gemini",
           ".github/prompts", ".kiro", ".opencode", ".windsurf", "skills")


def _fenced_lines():
    """(path:line, text) for every line inside a ``` fence in the skill pack and
    its harness mirrors. Prose outside fences (tables with escaped `\\<`) is
    not shell."""
    for top in MIRRORS:
        for p in sorted((ROOT / top).rglob("*")):
            if p.suffix not in (".md", ".toml", ".prompt") or not p.is_file():
                continue
            fenced = False
            for n, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
                if line.lstrip().startswith("```"):
                    fenced = not fenced
                    continue
                if fenced:
                    yield f"{p.relative_to(ROOT)}:{n}", line


class TestSkillsStayPortable(unittest.TestCase):
    def test_no_string_comparison_in_test_brackets(self):
        """zsh's `[` rejects `\\<` and `\\>`; a window check written that way
        fails open. Clock maths belongs in `tkt schedule`."""
        bad = re.compile(r"\[\s[^\]]*\\[<>]")
        self.assertEqual([loc for loc, line in _fenced_lines() if bad.search(line)], [])

    def test_no_for_loop_over_an_unquoted_variable(self):
        """zsh does not word-split `for x in $VAR`: the loop runs once with the
        whole list as one value. Iterate `... | while read -r x` instead."""
        bad = re.compile(r"\bfor\s+\w+\s+in\s+\$[{A-Za-z_]")
        self.assertEqual([loc for loc, line in _fenced_lines() if bad.search(line)], [])

    def test_the_scan_sees_the_skills(self):
        self.assertTrue(any(loc.startswith("skills/deploy-ready/") for loc, _ in _fenced_lines()))


if __name__ == "__main__":
    unittest.main()
