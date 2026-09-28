"""Tests for TKT-16: `tkt activity` on the Jira adapter.

Normalizes comments and changelog items into ActivityEvents in the half-open
window [since, until), with offset-aware bounds enforced per event, and pages
each source from the end nearest the window: comments newest-first with an
early stop, the changelog backward from its tail. Fully offline: REST is
stubbed at the `_jira` boundary, which serves an in-memory board and records
every path. Run from the repo root:

    python3 -m unittest tests.test_jira_activity
"""
import os
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.jira import JiraAdapter  # noqa: E402
from core.config import Config  # noqa: E402
from core.errors import ProviderError  # noqa: E402

SITE = "example.atlassian.net"
UTC = timezone.utc
SINCE = datetime(2026, 9, 20, tzinfo=UTC)
UNTIL = datetime(2026, 9, 21, tzinfo=UTC)
QUERY = 'status = "In Progress" ORDER BY priority DESC'


def make_config(query=QUERY):
    return Config({"ticketing": {"provider": "jira", "project": "PROJ"},
                   "board": {"roles": {"todo": "To Do", "done": "Done"}},
                   "queries": {"active": query}},
                  Path("/nonexistent/.sdlc/config.toml"))


def comment(cid, created, name="Ann", acct="acc-ann", text="hello"):
    return {"id": str(cid), "created": created,
            "author": {"displayName": name, "accountId": acct},
            "body": {"type": "doc", "version": 1, "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": text}]}]}}


def history(hid, created, items=(("status", "To Do", "In Progress"),),
            name="Bob", acct="acc-bob"):
    return {"id": str(hid), "created": created,
            "author": {"displayName": name, "accountId": acct},
            "items": [{"field": f, "fromString": a, "toString": b}
                      for f, a, b in items]}


def ts(dt):
    """Render a UTC datetime the way Jira does: millis and a +0000 offset."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}+0000"


class StubJira(JiraAdapter):
    """JiraAdapter whose REST calls hit an in-memory board.

    `comments` / `changelog` map key -> list, oldest first (as a ticket's
    history accrues). Comments are served newest-first, matching
    `orderBy=-created`; the changelog oldest-first with `total`, as Jira
    pages it. `page` caps maxResults the way Jira silently does.
    """

    def __init__(self, keys=("PROJ-1",), comments=None, changelog=None,
                 page=100, search_page=100, query=QUERY, drop_total=False,
                 oldest_first=False, on_comment_fetch=None):
        super().__init__(make_config(query))
        self.have_rest = True
        self.site = SITE
        self.keys = list(keys)
        self.comments = comments or {}
        self.changelog = changelog or {}
        self.page = page
        self.search_page = search_page
        self.drop_total = drop_total          # Jira omitting `total`
        self.oldest_first = oldest_first      # Jira ignoring orderBy=-created
        self.on_comment_fetch = on_comment_fetch  # mutate the board mid-walk
        self.calls: list[str] = []
        self.jqls: list[str] = []

    def _jira(self, method, path, body=None):
        assert method == "GET", "activity must be read-only"
        self.calls.append(path)
        url = urlsplit(path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/rest/api/3/search/jql":
            self.jqls.append(q["jql"])
            start = int(q.get("nextPageToken", "0"))
            size = min(int(q.get("maxResults", 50)), self.search_page)
            out = {"issues": [{"key": k} for k in self.keys[start:start + size]]}
            if start + size < len(self.keys):
                out.update(nextPageToken=str(start + size), isLast=False)
            else:
                out["isLast"] = True
            return out
        key, what = re.fullmatch(r"/rest/api/3/issue/([^/]+)/(\w+)", url.path).groups()
        start = int(q.get("startAt", 0))
        size = min(int(q.get("maxResults", 50)), self.page)
        if what == "comment":
            assert q.get("orderBy") == "-created", path
            rows = list(self.comments.get(key, []))
            if not self.oldest_first:
                rows.reverse()
            out = {"comments": rows[start:start + size], "startAt": start,
                   "maxResults": size, "total": len(rows)}
            if self.on_comment_fetch:
                self.on_comment_fetch(self, key)
            return self.strip(out)
        assert what == "changelog", path
        rows = self.changelog.get(key, [])
        return self.strip({"values": rows[start:start + size], "startAt": start,
                           "maxResults": size, "total": len(rows),
                           "isLast": start + size >= len(rows)})

    def strip(self, page):
        if self.drop_total:
            page.pop("total")
        return page

    def paths(self, what):
        return [p for p in self.calls if f"/{what}?" in p]

    def starts(self, what):
        return [int(parse_qs(urlsplit(p).query)["startAt"][0])
                for p in self.paths(what)]


def run(stub, since=SINCE, until=UNTIL):
    return stub.activity("active", since, until)


class Normalization(unittest.TestCase):
    def test_comment_event_shape(self):
        stub = StubJira(comments={"PROJ-1": [
            comment(10001, "2026-09-20T12:00:00.000+0200", text="ship it")]})
        report = run(stub)
        self.assertEqual([e.to_dict() for e in report.events], [{
            "evidence_id": "jira:comment:10001", "key": "PROJ-1",
            "kind": "comment", "timestamp": "2026-09-20T10:00:00.000Z",
            "actor": "Ann", "actor_id": "acc-ann",
            "field": "", "from": None, "to": None, "body": "ship it",
            "url": f"https://{SITE}/browse/PROJ-1?focusedCommentId=10001",
        }])

    def test_change_items_get_indexed_evidence_ids(self):
        stub = StubJira(changelog={"PROJ-1": [history(
            500, "2026-09-20T08:00:00.000+0000",
            items=[("status", "To Do", "In Progress"),
                   ("assignee", None, "Bob")])]})
        events = [e.to_dict() for e in run(stub).events]
        self.assertEqual([e["evidence_id"] for e in events],
                         ["jira:change:500:0", "jira:change:500:1"])
        self.assertEqual(events[0], {
            "evidence_id": "jira:change:500:0", "key": "PROJ-1",
            "kind": "change", "timestamp": "2026-09-20T08:00:00.000Z",
            "actor": "Bob", "actor_id": "acc-bob",
            "field": "status", "from": "To Do", "to": "In Progress",
            "body": "", "url": f"https://{SITE}/browse/PROJ-1",
        })
        self.assertIsNone(events[1]["from"])
        self.assertEqual(events[1]["to"], "Bob")

    def test_missing_author_is_empty_not_an_error(self):
        h = history(7, "2026-09-20T08:00:00.000+0000")
        del h["author"]           # automation / anonymous changes carry none
        events = run(StubJira(changelog={"PROJ-1": [h]})).events
        self.assertEqual((events[0].actor, events[0].actor_id), ("", ""))

    def test_report_carries_window_query_and_tickets(self):
        stub = StubJira(keys=["PROJ-1", "PROJ-2"])
        report = run(stub, since=datetime(2026, 9, 20, 2, tzinfo=timezone(
            timedelta(hours=2))))
        d = report.to_dict()
        self.assertEqual(d["query"], "active")
        self.assertEqual(d["since"], "2026-09-20T00:00:00.000Z")
        self.assertEqual(d["until"], "2026-09-21T00:00:00.000Z")
        self.assertEqual(d["tickets"], ["PROJ-1", "PROJ-2"])
        self.assertEqual(d["events"], [])

    def test_unknown_query_beats_missing_rest(self):
        """A mistyped query name is the more useful error: report it first."""
        from core.errors import NotFoundError
        stub = StubJira()
        stub.have_rest = False
        with self.assertRaises(NotFoundError):
            stub.activity("nope", SINCE, UNTIL)

    def test_needs_rest(self):
        stub = StubJira()
        stub.have_rest = False
        with self.assertRaises(ProviderError) as cm:
            run(stub)
        self.assertIn("REST", str(cm.exception))
        self.assertEqual(stub.calls, [])


class Jql(unittest.TestCase):
    def test_broad_updated_floor_scoped_and_order_kept(self):
        stub = StubJira()
        run(stub)
        self.assertEqual(stub.jqls, [
            '(project = "PROJ") AND ((status = "In Progress") AND '
            'updated >= "2026-09-19") ORDER BY priority DESC'])

    def test_floor_uses_the_utc_date_of_since_minus_a_day(self):
        stub = StubJira()
        # 01:00 at +05:00 is 2026-09-19T20:00Z; a day earlier is the 18th.
        run(stub, since=datetime(2026, 9, 20, 1, tzinfo=timezone(timedelta(hours=5))))
        self.assertIn('updated >= "2026-09-18"', stub.jqls[0])

    def test_since_at_datetime_min_does_not_overflow(self):
        stub = StubJira()
        run(stub, since=datetime(1, 1, 1, tzinfo=UTC))
        self.assertIn('updated >= "0001-01-01"', stub.jqls[0])

    def test_order_by_only_query(self):
        stub = StubJira(query="ORDER BY created")
        run(stub)
        self.assertEqual(stub.jqls, [
            '(project = "PROJ") AND (updated >= "2026-09-19") ORDER BY created'])

    def test_search_is_paginated(self):
        keys = [f"PROJ-{i}" for i in range(1, 6)]
        stub = StubJira(keys=keys, search_page=2)
        self.assertEqual(run(stub).tickets, keys)
        self.assertEqual(len(stub.jqls), 3)


class WindowBounds(unittest.TestCase):
    def kept(self, stamps, **kw):
        stub = StubJira(comments={"PROJ-1": [
            comment(i, s) for i, s in enumerate(stamps)]}, **kw)
        return [e.evidence_id for e in run(stub).events]

    def test_half_open_with_millisecond_neighbours(self):
        stamps = [
            "2026-09-19T23:59:59.999+0000",   # 0: 1ms before since -> out
            "2026-09-20T00:00:00.000+0000",   # 1: exactly since -> in
            "2026-09-20T23:59:59.999+0000",   # 2: 1ms before until -> in
            "2026-09-21T00:00:00.000+0000",   # 3: exactly until -> out
        ]
        self.assertEqual(sorted(self.kept(stamps)),
                         ["jira:comment:1", "jira:comment:2"])

    def test_offsets_are_compared_as_instants(self):
        stamps = [
            "2026-09-20T01:30:00.000+0200",   # 0: 23:30Z on the 19th -> out
            "2026-09-19T20:30:00.000-0400",   # 1: 00:30Z on the 20th -> in
            "2026-09-21T01:00:00.000+0200",   # 2: 23:00Z on the 20th -> in
            "2026-09-20T20:00:00.000-0400",   # 3: 00:00Z on the 21st -> out
        ]
        self.assertEqual(sorted(self.kept(stamps)),
                         ["jira:comment:1", "jira:comment:2"])

    def test_changelog_bounds_match_comment_bounds(self):
        stub = StubJira(changelog={"PROJ-1": [
            history(1, "2026-09-19T23:59:59.999+0000"),
            history(2, "2026-09-20T00:00:00.000+0000"),
            history(3, "2026-09-20T23:59:59.999+0000"),
            history(4, "2026-09-21T00:00:00.000+0000"),
        ]})
        self.assertEqual(sorted(e.evidence_id for e in run(stub).events),
                         ["jira:change:2:0", "jira:change:3:0"])

    def test_timestamp_without_fraction_and_with_z(self):
        self.assertEqual(sorted(self.kept(["2026-09-20T06:00:00+0000",
                                           "2026-09-20T07:00:00Z"])),
                         ["jira:comment:0", "jira:comment:1"])

    def test_canonical_timestamp_is_utc_z_millis(self):
        stub = StubJira(comments={"PROJ-1": [
            comment(1, "2026-09-20T09:28:36.123456+0300")]})
        self.assertEqual(run(stub).events[0].timestamp, "2026-09-20T06:28:36.123Z")


class BadTimestamps(unittest.TestCase):
    def assert_raises_for(self, **kw):
        with self.assertRaises(ProviderError) as cm:
            run(StubJira(**kw))
        self.assertIn("timestamp", str(cm.exception))
        return str(cm.exception)

    def test_unparseable_comment_timestamp(self):
        msg = self.assert_raises_for(comments={"PROJ-1": [comment(9, "yesterday")]})
        self.assertIn("PROJ-1", msg)

    def test_unparseable_changelog_timestamp(self):
        self.assert_raises_for(changelog={"PROJ-1": [history(9, "2026-13-40T00:00")]})

    def test_naive_timestamp_is_rejected(self):
        """No offset means no instant: guessing a zone could shift it out of
        (or into) the window, so it is an error, not a UTC assumption."""
        self.assert_raises_for(comments={"PROJ-1": [
            comment(9, "2026-09-20T10:00:00.000")]})

    def test_missing_timestamp(self):
        c = comment(9, "x")
        del c["created"]
        self.assert_raises_for(comments={"PROJ-1": [c]})


class CommentPagination(unittest.TestCase):
    def thread(self, n):
        """n comments, one per hour, the newest at 2026-09-20T23:00Z."""
        newest = datetime(2026, 9, 20, 23, tzinfo=UTC)
        return [comment(i, ts(newest - timedelta(hours=n - 1 - i)))
                for i in range(n)]

    def test_stops_on_first_page_when_window_is_recent(self):
        stub = StubJira(comments={"PROJ-1": self.thread(250)})
        events = run(stub, since=datetime(2026, 9, 20, 21, tzinfo=UTC)).events
        self.assertEqual(len(events), 3)             # 21:00, 22:00, 23:00
        self.assertEqual(stub.starts("comment"), [0])

    def test_follows_pages_until_past_the_window(self):
        stub = StubJira(comments={"PROJ-1": self.thread(250)}, page=100)
        # 150 comments in the window: needs page 2, never page 3.
        since = datetime(2026, 9, 20, 23, tzinfo=UTC) - timedelta(hours=149)
        events = run(stub, since=since).events
        self.assertEqual(len(events), 150)
        self.assertEqual(stub.starts("comment"), [0, 100])

    def test_reads_every_page_when_whole_thread_is_in_window(self):
        stub = StubJira(comments={"PROJ-1": self.thread(120)}, page=50)
        events = run(stub, since=datetime(2026, 9, 1, tzinfo=UTC)).events
        self.assertEqual(len(events), 120)
        self.assertEqual(stub.starts("comment"), [0, 50, 100])


    def test_missing_total_still_reads_every_page(self):
        stub = StubJira(comments={"PROJ-1": self.thread(120)}, page=50,
                        drop_total=True)
        events = run(stub, since=datetime(2026, 9, 1, tzinfo=UTC)).events
        self.assertEqual(len(events), 120)
        self.assertEqual(stub.starts("comment"), [0, 50, 100])

    def test_comment_added_mid_walk_is_not_duplicated(self):
        """A new comment shifts every offset by one, so page 2 repeats the
        last row of page 1; the evidence_id must still be unique."""
        def add_once(stub, key):
            if len(stub.paths("comment")) == 1:
                stub.comments[key].append(
                    comment(999, "2026-09-20T23:30:00.000+0000"))
        stub = StubJira(comments={"PROJ-1": self.thread(150)},
                        on_comment_fetch=add_once)
        ids = [e.evidence_id for e in
               run(stub, since=datetime(2026, 9, 1, tzinfo=UTC)).events]
        self.assertEqual(len(ids), len(set(ids)), ids)
        self.assertEqual(len(ids), 150)

    def test_oldest_first_page_is_an_error_not_empty(self):
        stub = StubJira(comments={"PROJ-1": self.thread(30)}, oldest_first=True)
        with self.assertRaises(ProviderError) as cm:
            run(stub, since=datetime(2026, 9, 20, 21, tzinfo=UTC))
        self.assertIn("order", str(cm.exception))

    def test_order_is_checked_across_pages(self):
        """Each page descending on its own, but page 2 newer than page 1."""
        rows = self.thread(30)
        served = rows[:10][::-1] + rows[10:20][::-1] + rows[20:][::-1]
        stub = StubJira(comments={"PROJ-1": served}, page=10, oldest_first=True)
        with self.assertRaises(ProviderError) as cm:
            run(stub, since=datetime(2026, 9, 1, tzinfo=UTC))
        self.assertIn("order", str(cm.exception))


class ChangelogPagination(unittest.TestCase):
    def log(self, n):
        """n histories, one per hour, the newest at 2026-09-20T23:00Z."""
        newest = datetime(2026, 9, 20, 23, tzinfo=UTC)
        return [history(i, ts(newest - timedelta(hours=n - 1 - i)))
                for i in range(n)]

    def test_short_changelog_is_one_call(self):
        stub = StubJira(changelog={"PROJ-1": self.log(40)})
        self.assertEqual(len(run(stub).events), 24)
        self.assertEqual(stub.starts("changelog"), [0])

    def test_jumps_to_the_tail_and_stops_there(self):
        stub = StubJira(changelog={"PROJ-1": self.log(250)})
        events = run(stub, since=datetime(2026, 9, 20, 21, tzinfo=UTC)).events
        self.assertEqual(sorted(e.evidence_id for e in events),
                         ["jira:change:247:0", "jira:change:248:0",
                          "jira:change:249:0"])
        # Page 0 is the probe for `total`; page 100 is never read.
        self.assertEqual(stub.starts("changelog"), [0, 200])

    def test_walks_backward_until_older_than_since(self):
        stub = StubJira(changelog={"PROJ-1": self.log(250)})
        since = datetime(2026, 9, 20, 23, tzinfo=UTC) - timedelta(hours=119)
        self.assertEqual(len(run(stub, since=since).events), 120)
        self.assertEqual(stub.starts("changelog"), [0, 200, 100])

    def test_whole_history_reuses_the_probe_page(self):
        stub = StubJira(changelog={"PROJ-1": self.log(250)})
        events = run(stub, since=datetime(2026, 9, 1, tzinfo=UTC)).events
        self.assertEqual(len(events), 250)
        self.assertEqual(stub.starts("changelog"), [0, 200, 100])

    def test_server_capped_page_size_is_honoured(self):
        """Jira may return fewer than maxResults per page; the tail offset
        must come from the size it actually used."""
        stub = StubJira(changelog={"PROJ-1": self.log(250)}, page=40)
        events = run(stub, since=datetime(2026, 9, 20, 21, tzinfo=UTC)).events
        self.assertEqual(len(events), 3)
        self.assertEqual(stub.starts("changelog"), [0, 240])

    def test_missing_total_walks_forward_not_just_page_zero(self):
        stub = StubJira(changelog={"PROJ-1": self.log(250)}, drop_total=True)
        events = run(stub, since=datetime(2026, 9, 20, 21, tzinfo=UTC)).events
        self.assertEqual(len(events), 3)
        self.assertEqual(stub.starts("changelog"), [0, 100, 200])

    def test_empty_changelog(self):
        stub = StubJira(changelog={"PROJ-1": []})
        self.assertEqual(run(stub).events, [])
        self.assertEqual(stub.starts("changelog"), [0])


if __name__ == "__main__":
    unittest.main()
