"""The `[schedule]` table and `tkt schedule`: is it business hours right now?

Tickets carrying the configured after-hours label must only deploy outside
business hours. Skills ask `tkt schedule --json` instead of doing clock maths in
shell. Shell string comparison in `[` differs between bash, zsh and dash (zsh
rejects `\\<`), `date` silently falls back to UTC on a mistyped timezone, and a
TOML array of days prints as a Python repr through `tkt cfg`. Every one of those
failed open: an after-hours ticket was simply not held. Here the answer is
computed once, the same way in every harness, and bad config is an error.

Semantics:
- `business_hours = "HH:MM-HH:MM"`. Start is inclusive, end exclusive. `24:00`
  is allowed as an end. Start after end is an overnight window.
- An overnight window belongs to the day it *starts*: with `18:00-06:00` on
  mon-fri, Friday 23:00 and Saturday 01:00 are in business hours, while Monday
  01:00 (Sunday night's window) is not.
- `days` is an array (`["mon", "tue"]`) or a string (`"mon tue"`, commas
  allowed). Three-letter or full names, any case.
- `timezone` is an IANA name. Absent means the machine's local time.
- No table, or no `after_hours_label`, means the feature is off.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import Config
from .errors import ConfigError, NotFoundError, UsageError

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")    # datetime.weekday() order
_FULL = {"monday": "mon", "tuesday": "tue", "wednesday": "wed", "thursday": "thu",
         "friday": "fri", "saturday": "sat", "sunday": "sun"}
DEFAULT_HOURS = "09:00-18:00"
DEFAULT_DAYS = ("mon", "tue", "wed", "thu", "fri")

_HOURS = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


@dataclass(frozen=True)
class Schedule:
    label: str
    start: int                 # minutes after midnight, inclusive
    end: int                   # minutes after midnight, exclusive; 1440 = 24:00
    days: tuple[str, ...]      # WEEKDAYS order
    tz_name: str               # "" = machine local time
    hours: str                 # normalised "HH:MM-HH:MM", for messages

    def tz(self) -> ZoneInfo | None:
        return ZoneInfo(self.tz_name) if self.tz_name else None


def _minutes(h: str, m: str, *, allow_24: bool, raw: str) -> int:
    hh, mm = int(h), int(m)
    if mm > 59 or hh > 24 or (hh == 24 and (mm or not allow_24)):
        raise ConfigError(f"[schedule].business_hours: bad time in {raw!r}")
    return hh * 60 + mm


def parse_hours(raw: Any) -> tuple[int, int]:
    if not isinstance(raw, str) or not (m := _HOURS.match(raw)):
        raise ConfigError(
            f"[schedule].business_hours must look like \"09:00-18:00\", got {raw!r}")
    start = _minutes(m[1], m[2], allow_24=False, raw=raw)
    end = _minutes(m[3], m[4], allow_24=True, raw=raw)
    if start == end:
        raise ConfigError(
            f"[schedule].business_hours {raw!r} is empty; use \"00:00-24:00\" for all day")
    return start, end


def parse_days(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, str):
        items = [p for p in re.split(r"[\s,]+", raw) if p]
    elif isinstance(raw, list) and all(isinstance(p, str) for p in raw):
        items = raw
    else:
        raise ConfigError(f"[schedule].days must be an array or a string of day names, got {raw!r}")
    out = set()
    for item in items:
        name = item.strip().lower()
        name = _FULL.get(name, name)
        if name not in WEEKDAYS:
            raise ConfigError(f"[schedule].days: unknown day {item!r} (use mon..sun)")
        out.add(name)
    if not out:
        raise ConfigError("[schedule].days is empty; omit it for mon-fri")
    return tuple(d for d in WEEKDAYS if d in out)


def _fmt(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def load(config: Config) -> Schedule | None:
    """The configured schedule, None when the feature is off. Raises ConfigError
    on anything malformed — a silently-off safety hold is the failure mode this
    module exists to prevent."""
    try:
        table = config.get("schedule")
    except NotFoundError:
        return None
    if not isinstance(table, dict):
        raise ConfigError("[schedule] must be a table")
    raw_label = table.get("after_hours_label", "")
    if not isinstance(raw_label, str):
        raise ConfigError(f"[schedule].after_hours_label must be a string, got {raw_label!r}")
    label = raw_label.strip()
    if not label:
        return None
    start, end = parse_hours(table.get("business_hours", DEFAULT_HOURS))
    days = parse_days(table["days"]) if "days" in table else DEFAULT_DAYS
    tz_name = str(table.get("timezone", "") or "").strip()
    if tz_name:
        try:
            ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ConfigError(f"[schedule].timezone: unknown IANA zone {tz_name!r}") from e
    return Schedule(label=label, start=start, end=end, days=days, tz_name=tz_name,
                    hours=f"{_fmt(start)}-{_fmt(end)}")


def in_window(s: Schedule, now: datetime) -> bool:
    """Is `now` (timezone-aware) inside business hours?"""
    local = now.astimezone(s.tz()) if s.tz_name else now.astimezone()
    minute = local.hour * 60 + local.minute
    today = WEEKDAYS[local.weekday()]
    if s.start < s.end:
        return today in s.days and s.start <= minute < s.end
    # Overnight: the evening part belongs to today, the early-morning part to
    # the day the window started, i.e. yesterday.
    if minute >= s.start:
        return today in s.days
    if minute < s.end:
        return WEEKDAYS[(local.weekday() - 1) % 7] in s.days
    return False


def report(config: Config, now: datetime) -> dict[str, Any]:
    """The JSON `tkt schedule --json` prints."""
    s = load(config)
    if s is None:
        return {"enabled": False, "in_window": False, "label": None}
    local = now.astimezone(s.tz()) if s.tz_name else now.astimezone()
    return {
        "enabled": True,
        "in_window": in_window(s, now),
        "label": s.label,
        "business_hours": s.hours,
        "days": list(s.days),
        "timezone": s.tz_name or None,
        "local_time": local.strftime("%H:%M"),
        "local_day": WEEKDAYS[local.weekday()],
    }


def cmd_schedule(config: Config, at: str | None, as_json: bool) -> int:
    import json
    if at:
        try:
            now = datetime.fromisoformat(at.replace("Z", "+00:00"))
        except ValueError as e:
            raise UsageError(f"schedule --at: not an ISO-8601 time: {at!r}") from e
        if now.tzinfo is None:
            # Naive = wall-clock time in the schedule's own zone, which is what
            # someone checking a config means; machine local when none is set.
            s = load(config)
            now = now.replace(tzinfo=s.tz()) if s and s.tz_name else now.astimezone()
    else:
        now = datetime.now().astimezone()
    r = report(config, now)
    if as_json:
        print(json.dumps(r, indent=2))
    elif not r["enabled"]:
        print("after-hours scheduling is off (no [schedule].after_hours_label)")
    else:
        where = r["timezone"] or "local time"
        state = "IN business hours" if r["in_window"] else "outside business hours"
        print(f"{state}: {r['local_day']} {r['local_time']} {where}; "
              f"window {r['business_hours']} on {' '.join(r['days'])}; "
              f"label '{r['label']}'")
    return 0
