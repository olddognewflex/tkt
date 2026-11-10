"""TKT-20: the normalized ticket carries identity fields -- `project`,
`project_name`, `assignee_id`, `reporter`, `reporter_id`, `created`,
`updated` -- populated by the Jira adapter and left at their defaults by
backends that cannot supply them.

Normalization must be lenient: a missing, null or odd value yields the
field's default, never an exception, because one malformed field must not
make a whole ticket unreadable. Timestamps are canonical UTC (`iso_utc`).

Fully offline: the REST path is stubbed at `_jira`, the acli path at `_acli`.
Run from the repo root:

    python3 -m unittest tests.test_jira_identity
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.jira import JiraAdapter  # noqa: E402
from adapters.markdown import MarkdownAdapter  # noqa: E402
from core.cli import _print_ticket_human  # noqa: E402
from core.config import Config  # noqa: E402
from core.schema import Ticket  # noqa: E402

ROLES = {"todo": "To Do", "in_progress": "In Progress", "done": "Done"}

IDENTITY = ("project", "project_name", "assignee_id", "reporter",
            "reporter_id", "created", "updated")

FULL_FIELDS = {
    "summary": "identity", "issuetype": {"name": "Story"},
    "status": {"name": "To Do"}, "issuelinks": [],
    "project": {"id": "10000", "key": "PROJ", "name": "Project One"},
    "assignee": {"accountId": "acc-ann", "displayName": "Ann",
                 "emailAddress": "ann@example.com"},
    "reporter": {"accountId": "acc-bob", "displayName": "Bob",
                 "emailAddress": "bob@example.com"},
    "created": "2026-09-27T06:28:36.123+0000",
    "updated": "2026-09-28T12:00:00.500+0530",
}


def issue(key="PROJ-1", **fields):
    return {"id": key.split("-")[-1], "key": key, "fields": fields}


class StubJira(JiraAdapter):
    """Constructed through the real `__init__` (it only reads env vars), with
    the site pinned so the browse URL doesn't depend on the environment."""

    def __init__(self, have_rest, payload=None):
        super().__init__(Config(
            {"ticketing": {"provider": "jira", "project": "PROJ"},
             "board": {"roles": dict(ROLES)},
             "queries": {"tier1": "status = \"To Do\""}},
            Path("/nonexistent/.sdlc/config.toml")))
        self.site = "example.atlassian.net"
        self.have_rest = have_rest
        self.payload = payload if payload is not None else issue(**FULL_FIELDS)
        self.searches: list[dict] = []
        self.acli_calls: list[tuple] = []


class StubRestJira(StubJira):
    def __init__(self, payload=None):
        super().__init__(True, payload)

    def _jira(self, method, path, body=None):
        if path.startswith("/rest/api/3/search/jql?"):
            params = {k: v[0] for k, v in parse_qs(urlsplit(path).query).items()}
            self.searches.append(params)
            if params["jql"].startswith("parent"):
                return {"issues": [], "isLast": True}
            return {"issues": [self.payload], "isLast": True}
        if path.endswith("/transitions"):
            return {"transitions": []}
        if path.startswith("/rest/api/3/issue/"):
            return self.payload
        raise AssertionError(f"unexpected REST call {method} {path}")

    def _acli(self, *args):
        raise AssertionError(f"REST mode must not shell out to acli: {args}")


class StubAcliJira(StubJira):
    def __init__(self, payload=None):
        super().__init__(False, payload)

    def _acli(self, *args):
        self.acli_calls.append(args)
        if args[:3] == ("jira", "workitem", "view"):
            return json.dumps(self.payload)
        if args[:3] == ("jira", "workitem", "search"):
            if "parent" in args[args.index("--jql") + 1]:
                return "[]"
            return json.dumps([self.payload])
        raise AssertionError(f"unexpected acli call {args}")

    def _jira(self, method, path, body=None):
        raise AssertionError(f"acli mode must not call REST: {method} {path}")


def ticket(**fields) -> Ticket:
    """Normalize one issue built from `fields` without any I/O."""
    return StubRestJira()._to_ticket(issue(**fields))


class FullPayload(unittest.TestCase):

    def assert_identity(self, t: Ticket):
        self.assertEqual(t.project, "PROJ")
        self.assertEqual(t.project_name, "Project One")
        self.assertEqual(t.assignee, "Ann")
        self.assertEqual(t.assignee_id, "acc-ann")
        self.assertEqual(t.reporter, "Bob")
        self.assertEqual(t.reporter_id, "acc-bob")
        self.assertEqual(t.created, "2026-09-27T06:28:36.123Z")
        self.assertEqual(t.updated, "2026-09-28T06:30:00.500Z")

    def test_rest_view_populates_all_seven(self):
        self.assert_identity(StubRestJira().view("PROJ-1"))

    def test_acli_view_populates_all_seven(self):
        # acli payloads share REST's `fields` shape.
        self.assert_identity(StubAcliJira().view("PROJ-1"))

    def test_rest_list_populates_all_seven(self):
        [t] = StubRestJira().list(tier=1)
        self.assert_identity(t)

    def test_acli_list_populates_all_seven(self):
        [t] = StubAcliJira().list(tier=1)
        self.assert_identity(t)

    def test_to_dict_carries_the_values(self):
        d = StubRestJira().view("PROJ-1").to_dict()
        self.assertEqual({k: d[k] for k in IDENTITY}, {
            "project": "PROJ", "project_name": "Project One",
            "assignee_id": "acc-ann", "reporter": "Bob",
            "reporter_id": "acc-bob",
            "created": "2026-09-27T06:28:36.123Z",
            "updated": "2026-09-28T06:30:00.500Z"})
        json.dumps(d)  # still serializable

    def test_reporter_falls_back_to_email_like_assignee(self):
        t = ticket(reporter={"accountId": "acc-bob",
                             "emailAddress": "bob@example.com"},
                   assignee={"accountId": "acc-ann",
                             "emailAddress": "ann@example.com"})
        self.assertEqual((t.reporter, t.reporter_id),
                         ("bob@example.com", "acc-bob"))
        self.assertEqual((t.assignee, t.assignee_id),
                         ("ann@example.com", "acc-ann"))

    def test_account_id_without_a_name(self):
        t = ticket(reporter={"accountId": "acc-bob"})
        self.assertEqual((t.reporter, t.reporter_id), ("", "acc-bob"))


class Timestamps(unittest.TestCase):

    def test_utc_offset_normalized_to_z(self):
        self.assertEqual(ticket(created="2026-09-27T06:28:36.123+0000").created,
                         "2026-09-27T06:28:36.123Z")

    def test_positive_offset_converted_to_utc(self):
        self.assertEqual(ticket(updated="2026-09-27T06:28:36.123+0530").updated,
                         "2026-09-27T00:58:36.123Z")

    def test_negative_offset_crosses_the_day(self):
        self.assertEqual(ticket(created="2026-09-27T22:00:00.000-0400").created,
                         "2026-09-28T02:00:00.000Z")

    def test_no_milliseconds_gains_them(self):
        self.assertEqual(ticket(created="2026-09-27T06:28:36+00:00").created,
                         "2026-09-27T06:28:36.000Z")

    def test_offsetless_timestamp_is_none(self):
        # Guessing a zone could shift the time by hours; report nothing.
        t = ticket(created="2026-09-27T06:28:36.123",
                   updated="2026-09-27")
        self.assertIsNone(t.created)
        self.assertIsNone(t.updated)

    def test_garbage_is_none_not_an_error(self):
        for bad in ("yesterday", "", "2026-13-45T00:00:00+0000", 1727418516,
                    {"iso": "2026-09-27"}, [],
                    # in range locally, out of range once shifted to UTC
                    "0001-01-01T00:00:00+0100", "9999-12-31T23:59:59-0100"):
            with self.subTest(bad=bad):
                t = ticket(created=bad, updated=bad)
                self.assertIsNone(t.created)
                self.assertIsNone(t.updated)


class MissingFields(unittest.TestCase):

    def assert_defaults(self, t: Ticket):
        self.assertEqual(t.project, "")
        self.assertEqual(t.project_name, "")
        self.assertEqual(t.assignee, "")
        self.assertEqual(t.assignee_id, "")
        self.assertEqual(t.reporter, "")
        self.assertEqual(t.reporter_id, "")
        self.assertIsNone(t.created)
        self.assertIsNone(t.updated)

    def test_absent_fields_are_defaults(self):
        self.assert_defaults(ticket(summary="bare"))

    def test_no_fields_object_at_all(self):
        self.assert_defaults(StubRestJira()._to_ticket({"key": "PROJ-1"}))

    def test_explicit_nulls_are_defaults(self):
        # An unassigned issue arrives as `"assignee": null`.
        self.assert_defaults(ticket(project=None, assignee=None, reporter=None,
                                    created=None, updated=None))

    def test_non_object_people_and_project_are_defaults(self):
        self.assert_defaults(ticket(project="PROJ", assignee="Ann",
                                    reporter=["Bob"]))

    def test_project_without_name(self):
        t = ticket(project={"key": "PROJ"})
        self.assertEqual((t.project, t.project_name), ("PROJ", ""))


class ListFieldStrings(unittest.TestCase):
    """`list` asks for named fields, so the new ones must be requested or
    `list` output would silently lack them. (`view` asks for `*all`.)"""

    WANTED = {"project", "reporter", "created", "updated"}

    def test_rest_search_requests_identity_fields(self):
        a = StubRestJira()
        a.list(tier=1)
        [search] = a.searches
        self.assertLessEqual(self.WANTED, set(search["fields"].split(",")))

    def test_acli_search_requests_identity_fields(self):
        a = StubAcliJira()
        a.list(tier=1)
        [call] = [c for c in a.acli_calls if c[:3] == ("jira", "workitem", "search")]
        fields = call[call.index("--fields") + 1]
        self.assertLessEqual(self.WANTED, set(fields.split(",")))

    def test_existing_fields_still_requested(self):
        a = StubRestJira()
        a.list(tier=1)
        got = set(a.searches[0]["fields"].split(","))
        self.assertLessEqual({"priority", "assignee", "summary", "issuelinks",
                              "labels", "components", "issuetype", "status",
                              "parent"}, got)


class SchemaDefaults(unittest.TestCase):

    def test_bare_ticket_to_dict_has_all_keys_at_defaults(self):
        d = Ticket(key="X-1").to_dict()
        self.assertEqual({k: d[k] for k in IDENTITY}, {
            "project": "", "project_name": "", "assignee_id": "",
            "reporter": "", "reporter_id": "", "created": None,
            "updated": None})

    def test_markdown_ticket_carries_keys_at_defaults(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        cfg = Config({"ticketing": {"provider": "markdown", "project": "TKT"},
                      "markdown": {"board_dir": str(Path(tmp) / "board"),
                                   "state_dir": str(Path(tmp) / "state"),
                                   "me": "raymond"},
                      "board": {"roles": {"todo": "To Do", "done": "Done"}},
                      "queries": {"all": 'status = "To Do"'}},
                     Path(tmp) / ".sdlc" / "config.toml")
        a = MarkdownAdapter(cfg)
        a.board_dir.mkdir(parents=True)
        (a.board_dir / "TKT-1.md").write_text(
            "---\ntype: Story\nstatus: To Do\nassignee: raymond\n---\n# One\n")
        d = a.view("TKT-1").to_dict()
        for k in IDENTITY:
            self.assertIn(k, d)
        self.assertEqual(d["project"], "")
        self.assertEqual(d["reporter"], "")
        self.assertIsNone(d["created"])
        self.assertIsNone(d["updated"])


def render(t: Ticket) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _print_ticket_human(t)
    return buf.getvalue()


class HumanView(unittest.TestCase):

    def test_lines_when_set(self):
        out = render(Ticket(key="PROJ-1", summary="s", status="To Do",
                            project="PROJ", project_name="Project One",
                            assignee="Ann", reporter="Bob",
                            created="2026-09-27T06:28:36.123Z",
                            updated="2026-09-28T06:30:00.500Z"))
        lines = [ln.strip() for ln in out.splitlines()]
        self.assertIn("project:  PROJ (Project One)", lines)
        self.assertIn("reporter: Bob", lines)
        self.assertIn("dates:    created=2026-09-27T06:28:36.123Z, "
                      "updated=2026-09-28T06:30:00.500Z", lines)

    def test_project_key_alone(self):
        lines = [ln.strip() for ln in
                 render(Ticket(key="PROJ-1", project="PROJ")).splitlines()]
        self.assertIn("project:  PROJ", lines)

    def test_absent_when_empty(self):
        out = render(Ticket(key="TKT-1", summary="s", status="To Do"))
        for word in ("project", "reporter", "created", "updated", "dates"):
            self.assertNotIn(word, out)

    def test_ids_are_never_printed(self):
        # Account ids are for machines; the human view shows names only.
        out = render(Ticket(key="PROJ-1", assignee="Ann", assignee_id="acc-ann",
                            reporter="Bob", reporter_id="acc-bob"))
        self.assertNotIn("acc-", out)


if __name__ == "__main__":
    unittest.main()
