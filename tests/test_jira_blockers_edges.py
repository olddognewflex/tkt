"""TKT-13 follow-on: the adversarial edges of Jira blocker detection.

TKT-13's bug was a false *negative* — matching the literal strings "is blocked
by" / "blocks" meant a site that renamed its link-type descriptions reported no
blockers at all, and `select-ticket` then picked blocked work. The fix matches
the link type `name` first and falls back to a negation-aware "block" stem in
both descriptions.

`tests/test_jira_blockers.py` covers the happy path of that fix. This file
covers the ways it can still go wrong:

  * false *positives* — `Unblocks` / `Non-blocking dependency` are real custom
    link types that mean the opposite, and treating them as blockers makes
    `select-ticket` skip work that is ready (the inverse of TKT-13);
  * the `name == "blocks"` branch in isolation, via a fixture whose
    descriptions cannot reach the stem fallback at all;
  * link entries whose absent side arrives as an explicit null, or whose issue
    carries no key — an `AttributeError` here would escape `core/errors.py`'s
    exit-code mapping and surface as a traceback, not exit 3;
  * duplicate entries when two link types on one site both match, which would
    change `skills/select-ticket/SKILL.md`'s `tkt blockers KEY --json |
    jq 'length'` count;
  * the acli `view` payload shape (see `AcliPayloadShape` — its assumption is
    NOT confirmed against a live site);
  * the ticket's literal acceptance, at the `tkt blockers KEY --json` CLI level.

Fully offline. `_to_ticket` is called directly on synthetic payloads, and the
`view()` / `blockers()` / CLI tests stub at the `_jira` / `_acli` boundary the
way `tests/test_jira_transition.py` does, so no subprocess and no socket. Run
from the repo root:

    python3 -m unittest tests.test_jira_blockers_edges
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.jira import JiraAdapter, _is_blocks_link  # noqa: E402
from core import cli  # noqa: E402
from core.config import Config  # noqa: E402

ROLES = {"todo": "To Do", "in_progress": "In Progress", "done": "Done"}

# --- link types -----------------------------------------------------------
# Stock Jira, and the same relationship on sites that renamed things.
BLOCKS = {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"}
# A marketplace plugin's own blocking type: the `name` branch cannot match it,
# so it only resolves through the description fallback.
BLOCKER_PLUGIN = {"name": "Blocker", "inward": "is blocked by",
                  "outward": "blocks"}
# Descriptions renamed AND localised: no "block" stem on either side, so the
# only thing that can match this is the stable link type `name`.
OPAQUE_NAMED = {"name": "Blocks", "inward": "dépend de", "outward": "requis pour"}

# Real custom link types that mean the OPPOSITE of blocking. A false positive
# on any of these makes select-ticket skip a ticket that is ready to work.
UNBLOCKS = {"name": "Unblocks", "inward": "is unblocked by", "outward": "unblocks"}
NON_BLOCKING = {"name": "Non-blocking dependency",
                "inward": "is non-blocking dependency of",
                "outward": "has non-blocking dependency"}
NONBLOCKING_PLAIN = {"name": "Nonblocking dependency",
                     "inward": "is nonblocking dependency of",
                     "outward": "has nonblocking dependency"}

NEGATED_TYPES = [UNBLOCKS, NON_BLOCKING, NONBLOCKING_PLAIN]


def make_config():
    return Config(
        {"ticketing": {"provider": "jira", "project": "PROJ"},
         "board": {"roles": dict(ROLES)}},
        Path("/nonexistent/.sdlc/config.toml"))


class StubJira(JiraAdapter):
    """Constructed adapter with a pinned site and no credentials.

    `__init__` only reads env vars, so it is offline-safe; going through it
    keeps `project` / `_acli_site_cache` set. `site` and `have_rest` are pinned
    afterwards so the developer's own CONFLUENCE_* env cannot change a result.
    """

    def __init__(self):
        super().__init__(make_config())
        self.site = "example.atlassian.net"
        self.have_rest = True


def issue(links, key="PROJ-1", status="To Do"):
    return {"key": key,
            "fields": {"summary": "s", "issuetype": {"name": "Bug"},
                       "status": {"name": status}, "issuelinks": links}}


def rest_side(key, category="new"):
    """An issue stub as `fields.issuelinks[].inwardIssue` carries it over REST:
    the status category key is nested under fields.status.statusCategory."""
    return {"key": key,
            "fields": {"status": {"name": "Done" if category == "done" else "To Do",
                                  "statusCategory": {"key": category}}}}


def inward(ltype, key, category="new"):
    """`key` blocks the issue being parsed."""
    return {"type": dict(ltype), "inwardIssue": rest_side(key, category)}


def outward(ltype, key):
    """The issue being parsed blocks `key`."""
    return {"type": dict(ltype), "outwardIssue": {"key": key}}


# --------------------------------------------------------------------------


class NegationFalsePositives(unittest.TestCase):
    """`Unblocks` and `Non-blocking dependency` must never count as blockers.

    Asserted through `_to_ticket`, not only `_is_blocks_link`: the helper is
    private and a future caller could stop consulting it, but the Ticket shape
    is what skills read.
    """

    def setUp(self):
        self.a = StubJira()

    def test_helper_rejects_negated_types(self):
        for ltype in NEGATED_TYPES:
            with self.subTest(name=ltype["name"]):
                self.assertFalse(_is_blocks_link(ltype))

    def test_to_ticket_records_nothing_for_negated_types(self):
        for ltype in NEGATED_TYPES:
            with self.subTest(name=ltype["name"]):
                t = self.a._to_ticket(issue([inward(ltype, "PROJ-2"),
                                             outward(ltype, "PROJ-3")]))
                self.assertEqual(t.blocked_by, [])
                self.assertEqual(t.blocks, [])
                self.assertEqual(t.unresolved_blockers(), [])

    def test_negated_link_does_not_contaminate_a_real_one(self):
        # One genuine blocker alongside three inverse links: exactly one entry.
        links = [inward(UNBLOCKS, "PROJ-90"),
                 inward(NON_BLOCKING, "PROJ-91"),
                 inward(BLOCKS, "PROJ-2"),
                 outward(NONBLOCKING_PLAIN, "PROJ-92")]
        t = self.a._to_ticket(issue(links))
        self.assertEqual(t.blocked_by, [{"key": "PROJ-2", "resolved": False}])
        self.assertEqual(t.blocks, [])

    def test_negated_wording_alone_never_selects_the_ticket_out(self):
        # A ticket linked ONLY by inverse types is workable: no unresolved
        # blockers, so select-ticket must not skip it.
        t = self.a._to_ticket(issue([inward(UNBLOCKS, "PROJ-2"),
                                     inward(NON_BLOCKING, "PROJ-3")]))
        self.assertEqual(t.unresolved_blockers(), [])

    def test_negation_is_case_insensitive(self):
        shouty = {"name": "UNBLOCKS", "inward": "Is UNBLOCKED By",
                  "outward": "UNBLOCKS"}
        self.assertFalse(_is_blocks_link(shouty))
        t = self.a._to_ticket(issue([inward(shouty, "PROJ-2")]))
        self.assertEqual(t.blocked_by, [])

    def test_separator_spelling_does_not_change_the_answer(self):
        """"non-blocking", "non blocking" and "nonblocking" are one type.

        Separators are stripped before the stem is matched, so a site's
        choice of hyphen, space or neither can't decide whether a link that
        means "not a blocker" gets filed as one.
        """
        for spelling in ("non-blocking", "non blocking", "nonblocking"):
            with self.subTest(spelling=spelling):
                ltype = {"name": f"{spelling} dependency",
                         "inward": f"is {spelling} dependency of",
                         "outward": f"has {spelling} dependency"}
                self.assertFalse(_is_blocks_link(ltype))
                t = self.a._to_ticket(issue([inward(ltype, "PROJ-2")]))
                self.assertEqual(t.blocked_by, [])

    def test_spaced_un_and_not_negations_are_rejected(self):
        for ltype in ({"name": "Un blocks", "inward": "is un blocked by",
                       "outward": "un blocks"},
                      {"name": "Not blocking", "inward": "is not blocking",
                       "outward": "not blocking"}):
            with self.subTest(name=ltype["name"]):
                self.assertFalse(_is_blocks_link(ltype))

    def test_self_referential_link_never_blocks_the_ticket_itself(self):
        """A both-sides payload names this issue on one side.

        `fields.issuelinks` never does, but `GET /issueLink/{id}` and webhook
        bodies do. Without the guard `unresolved_blockers()` never empties and
        select-ticket skips the ticket forever.
        """
        link = {"type": dict(BLOCKS), "inwardIssue": {"key": "PROJ-1"},
                "outwardIssue": {"key": "PROJ-1"}}
        t = self.a._to_ticket(issue([link], key="PROJ-1"))
        self.assertEqual(t.blocked_by, [])
        self.assertEqual(t.blocks, [])
        self.assertEqual(t.unresolved_blockers(), [])


class NameBranchIsolation(unittest.TestCase):
    """The stable-`name` branch, with the description fallback made unreachable.

    Every fixture in `tests/test_jira_blockers.py` carries "block" in both
    descriptions, so all of them also satisfy the fallback: deleting the name
    branch leaves them green. These use descriptions with no "block" stem at
    all, so only the `name` branch can match.
    """

    def setUp(self):
        self.a = StubJira()

    def test_fixture_cannot_reach_the_description_fallback(self):
        # Same descriptions, non-matching name: proves the descriptions alone
        # carry no signal, so the assertions below can only be the name branch.
        self.assertFalse(_is_blocks_link({"name": "Relates",
                                          "inward": OPAQUE_NAMED["inward"],
                                          "outward": OPAQUE_NAMED["outward"]}))

    def test_blocked_by_found_by_name_alone(self):
        t = self.a._to_ticket(issue([inward(OPAQUE_NAMED, "PROJ-2")]))
        self.assertEqual(t.blocked_by, [{"key": "PROJ-2", "resolved": False}])
        self.assertEqual([b["key"] for b in t.unresolved_blockers()], ["PROJ-2"])

    def test_blocks_found_by_name_alone(self):
        t = self.a._to_ticket(issue([outward(OPAQUE_NAMED, "PROJ-3")]))
        self.assertEqual(t.blocks, ["PROJ-3"])

    def test_resolved_flag_still_read_when_matched_by_name(self):
        t = self.a._to_ticket(issue([inward(OPAQUE_NAMED, "PROJ-2",
                                            category="done")]))
        self.assertEqual(t.blocked_by, [{"key": "PROJ-2", "resolved": True}])
        self.assertEqual(t.unresolved_blockers(), [])

    def test_plugin_type_found_by_description_alone(self):
        # The mirror image: a name the `name` branch cannot match, resolved
        # purely by the stem fallback.
        self.assertNotEqual(BLOCKER_PLUGIN["name"].lower(), "blocks")
        t = self.a._to_ticket(issue([inward(BLOCKER_PLUGIN, "PROJ-2")]))
        self.assertEqual(t.blocked_by, [{"key": "PROJ-2", "resolved": False}])


class NullAndMissingSides(unittest.TestCase):
    """An absent side can arrive as an explicit null, and both sides can be
    present on one entry (the shape `GET /rest/api/3/issueLink/{id}` and
    webhooks return). Neither may raise: an `AttributeError` escapes
    `core/errors.py`'s typed-error mapping and prints a traceback instead of
    exiting 3.
    """

    def setUp(self):
        self.a = StubJira()

    def test_explicit_null_inward_with_real_outward(self):
        link = {"type": dict(BLOCKS), "inwardIssue": None,
                "outwardIssue": {"key": "PROJ-3"}}
        t = self.a._to_ticket(issue([link]))
        self.assertEqual(t.blocked_by, [])
        self.assertEqual(t.blocks, ["PROJ-3"])

    def test_explicit_null_outward_with_real_inward(self):
        link = {"type": dict(BLOCKS), "inwardIssue": rest_side("PROJ-2"),
                "outwardIssue": None}
        t = self.a._to_ticket(issue([link]))
        self.assertEqual(t.blocked_by, [{"key": "PROJ-2", "resolved": False}])
        self.assertEqual(t.blocks, [])

    def test_both_sides_null(self):
        link = {"type": dict(BLOCKS), "inwardIssue": None, "outwardIssue": None}
        t = self.a._to_ticket(issue([link]))
        self.assertEqual(t.blocked_by, [])
        self.assertEqual(t.blocks, [])

    def test_neither_side_present(self):
        t = self.a._to_ticket(issue([{"type": dict(BLOCKS)}]))
        self.assertEqual(t.blocked_by, [])
        self.assertEqual(t.blocks, [])

    def test_both_sides_present_on_one_entry_are_both_recorded(self):
        link = {"type": dict(BLOCKS),
                "inwardIssue": rest_side("PROJ-2", category="done"),
                "outwardIssue": {"key": "PROJ-3"}}
        t = self.a._to_ticket(issue([link]))
        self.assertEqual(t.blocked_by, [{"key": "PROJ-2", "resolved": True}])
        self.assertEqual(t.blocks, ["PROJ-3"])

    def test_inward_issue_without_a_key_field(self):
        link = {"type": dict(BLOCKS),
                "inwardIssue": {"fields": {"status": {"statusCategory":
                                                      {"key": "new"}}}}}
        t = self.a._to_ticket(issue([link]))
        self.assertEqual(t.blocked_by, [])

    def test_null_and_empty_keys_are_dropped_not_recorded(self):
        for bad in (None, ""):
            with self.subTest(key=bad):
                links = [{"type": dict(BLOCKS), "inwardIssue": {"key": bad}},
                         {"type": dict(BLOCKS), "outwardIssue": {"key": bad}}]
                t = self.a._to_ticket(issue(links))
                self.assertEqual(t.blocked_by, [])
                self.assertEqual(t.blocks, [])

    def test_a_dropped_side_does_not_drop_the_rest_of_the_list(self):
        links = [{"type": dict(BLOCKS), "inwardIssue": None},
                 {"type": dict(BLOCKS), "inwardIssue": {"key": None}},
                 inward(BLOCKS, "PROJ-2"),
                 {"type": dict(BLOCKS), "outwardIssue": {}},
                 outward(BLOCKS, "PROJ-3")]
        t = self.a._to_ticket(issue(links))
        self.assertEqual(t.blocked_by, [{"key": "PROJ-2", "resolved": False}])
        self.assertEqual(t.blocks, ["PROJ-3"])


class Deduplication(unittest.TestCase):
    """Two link types that both match must not double-count the same pair.

    `skills/select-ticket/SKILL.md` reads `tkt blockers KEY --json |
    jq 'length'`, so a duplicate is not cosmetic — it changes what the skill
    reports and can make a one-blocker ticket look like a two-blocker ticket.
    """

    def setUp(self):
        self.a = StubJira()

    def test_blocked_by_is_deduplicated_across_link_types(self):
        # Stock "Blocks" matches by name; the plugin "Blocker" matches by
        # description. Same pair, two entries in the payload, one blocker.
        t = self.a._to_ticket(issue([inward(BLOCKS, "PROJ-2"),
                                     inward(BLOCKER_PLUGIN, "PROJ-2")]))
        self.assertEqual(t.blocked_by, [{"key": "PROJ-2", "resolved": False}])
        self.assertEqual(len(t.unresolved_blockers()), 1)

    def test_blocks_is_deduplicated_across_link_types(self):
        t = self.a._to_ticket(issue([outward(BLOCKS, "PROJ-3"),
                                     outward(BLOCKER_PLUGIN, "PROJ-3")]))
        self.assertEqual(t.blocks, ["PROJ-3"])

    def test_dedup_keeps_distinct_keys(self):
        t = self.a._to_ticket(issue([inward(BLOCKS, "PROJ-2"),
                                     inward(BLOCKER_PLUGIN, "PROJ-2"),
                                     inward(BLOCKS, "PROJ-4"),
                                     outward(BLOCKS, "PROJ-3"),
                                     outward(BLOCKER_PLUGIN, "PROJ-5")]))
        self.assertEqual([b["key"] for b in t.blocked_by], ["PROJ-2", "PROJ-4"])
        self.assertEqual(t.blocks, ["PROJ-3", "PROJ-5"])

    def test_first_occurrence_wins_when_resolved_flags_disagree(self):
        # Same key twice with different status categories. Pinning "first
        # wins" so a change of precedence is visible: the second reading
        # would flip a blocked ticket to workable, or the reverse.
        t = self.a._to_ticket(issue([inward(BLOCKS, "PROJ-2", category="done"),
                                     inward(BLOCKER_PLUGIN, "PROJ-2",
                                            category="new")]))
        self.assertEqual(t.blocked_by, [{"key": "PROJ-2", "resolved": True}])

    def test_blocked_by_and_blocks_are_deduplicated_independently(self):
        # A mutual link: the same key legitimately appears on both lists.
        t = self.a._to_ticket(issue([inward(BLOCKS, "PROJ-2"),
                                     outward(BLOCKS, "PROJ-2"),
                                     inward(BLOCKER_PLUGIN, "PROJ-2"),
                                     outward(BLOCKER_PLUGIN, "PROJ-2")]))
        self.assertEqual(t.blocked_by, [{"key": "PROJ-2", "resolved": False}])
        self.assertEqual(t.blocks, ["PROJ-2"])


# --- view() / blockers() over each transport ------------------------------


class StubRestJira(StubJira):
    """REST mode: `_jira` serves one canned issue and its transitions."""

    def __init__(self, issue_payload, transitions=("In Progress", "Done")):
        super().__init__()
        self.have_rest = True
        self.issue_payload = issue_payload
        self.transitions_available = list(transitions)
        self.calls: list[tuple] = []

    def _jira(self, method, path, body=None):
        self.calls.append((method, path))
        if path.endswith("/transitions"):
            return {"transitions": [{"id": str(i), "name": n, "to": {"name": n}}
                                    for i, n in enumerate(self.transitions_available)]}
        if path.startswith("/rest/api/3/issue/"):
            return self.issue_payload
        raise AssertionError(f"unexpected REST call {method} {path}")

    def _acli(self, *args):
        raise AssertionError(f"REST mode must not shell out to acli: {args}")


class StubAcliJira(StubJira):
    """acli-only mode: `_acli` returns the canned JSON `_acli_json` parses."""

    def __init__(self, issue_payload):
        super().__init__()
        self.have_rest = False
        self.issue_payload = issue_payload
        self.calls: list[tuple] = []

    def _acli(self, *args):
        self.calls.append(args)
        if args[:3] == ("jira", "workitem", "view"):
            return json.dumps(self.issue_payload)
        raise AssertionError(f"unexpected acli call {args}")

    def _jira(self, method, path, body=None):
        raise AssertionError(f"acli mode must not call REST: {method} {path}")


def acli_side_with_category(key, category="done"):
    """The acli `inwardIssue` shape this adapter ASSUMES: same nesting as REST."""
    return rest_side(key, category)


def acli_side_without_category(key, status_name="Done"):
    """The acli `inwardIssue` shape if acli flattens the status: a name, but
    no `statusCategory`."""
    return {"key": key, "fields": {"status": {"name": status_name}}}


class AcliPayloadShape(unittest.TestCase):
    """What `blockers()` does with each possible acli `issuelinks` shape.

    `blockers()` delegates to `view()`, which has a REST branch and an acli
    branch (`acli jira workitem view --fields '*all'`). Every other test in
    this repo feeds REST-shaped payloads, so the load-bearing unknown is
    whether acli nests `fields.status.statusCategory` inside
    `issuelinks[].inwardIssue` the way REST does.

    ASSUMPTION, UNCONFIRMED AGAINST A LIVE SITE: it does. That is what
    `test_acli_nested_status_category_resolves_a_done_blocker` pins.

    `test_acli_without_status_category_never_resolves` pins the consequence if
    the assumption is wrong: `cat` is "", every blocker is permanently
    `resolved: False`, and select-ticket skips the ticket forever even once
    its blockers are Done. That test asserts TODAY'S behavior, not desired
    behavior — if a live acli payload turns out to omit `statusCategory`, the
    adapter needs a fallback on the status name and this test is the one to
    change.
    """

    def test_acli_view_uses_the_all_fields_invocation(self):
        a = StubAcliJira(issue([inward(BLOCKS, "PROJ-2")]))
        a.view("PROJ-1")
        self.assertEqual(a.calls,
                         [("jira", "workitem", "view", "PROJ-1",
                           "--fields", "*all", "--json")])

    def test_acli_nested_status_category_resolves_a_done_blocker(self):
        link = {"type": dict(BLOCKS),
                "inwardIssue": acli_side_with_category("PROJ-2", "done")}
        a = StubAcliJira(issue([link]))
        t = a.view("PROJ-1")
        self.assertEqual(t.blocked_by, [{"key": "PROJ-2", "resolved": True}])
        self.assertEqual(a.blockers("PROJ-1"), [])

    def test_acli_without_status_category_never_resolves(self):
        # The failure mode, pinned: the blocker IS Done on the board, but with
        # no statusCategory the adapter cannot see it, so select-ticket keeps
        # skipping this ticket. See the class docstring.
        link = {"type": dict(BLOCKS),
                "inwardIssue": acli_side_without_category("PROJ-2", "Done")}
        a = StubAcliJira(issue([link]))
        t = a.view("PROJ-1")
        self.assertEqual(t.blocked_by, [{"key": "PROJ-2", "resolved": False}])
        self.assertEqual(a.blockers("PROJ-1"),
                         [{"key": "PROJ-2", "resolved": False}])

    def test_acli_custom_wording_matches_default_wording(self):
        # TKT-13 over the acli transport, which `view()` uses whenever REST
        # credentials are absent.
        default = StubAcliJira(issue([inward(BLOCKS, "PROJ-2"),
                                      outward(BLOCKS, "PROJ-3")])).view("PROJ-1")
        custom = StubAcliJira(issue([inward(OPAQUE_NAMED, "PROJ-2"),
                                     outward(OPAQUE_NAMED, "PROJ-3")])).view("PROJ-1")
        self.assertEqual(custom.blocked_by, default.blocked_by)
        self.assertEqual(custom.blocks, default.blocks)
        self.assertEqual([b["key"] for b in custom.blocked_by], ["PROJ-2"])

    def test_acli_transports_no_transitions_but_full_blockers(self):
        a = StubAcliJira(issue([inward(BLOCKS, "PROJ-2")]))
        t = a.view("PROJ-1")
        self.assertEqual(t.transitions, [])
        self.assertEqual([b["key"] for b in t.blocked_by], ["PROJ-2"])

    def test_rest_view_resolves_a_done_blocker(self):
        a = StubRestJira(issue([inward(BLOCKS, "PROJ-2", category="done"),
                                inward(BLOCKS, "PROJ-4")]))
        self.assertEqual(a.blockers("PROJ-1"),
                         [{"key": "PROJ-4", "resolved": False}])

    def test_rest_and_acli_agree_on_the_same_payload(self):
        payload = issue([inward(BLOCKS, "PROJ-2", category="done"),
                         inward(OPAQUE_NAMED, "PROJ-4"),
                         inward(UNBLOCKS, "PROJ-9"),
                         outward(BLOCKER_PLUGIN, "PROJ-3")])
        rest = StubRestJira(payload).view("PROJ-1")
        acli = StubAcliJira(payload).view("PROJ-1")
        self.assertEqual(rest.blocked_by, acli.blocked_by)
        self.assertEqual(rest.blocks, acli.blocks)
        self.assertEqual(acli.blocked_by,
                         [{"key": "PROJ-2", "resolved": True},
                          {"key": "PROJ-4", "resolved": False}])


# --- CLI ------------------------------------------------------------------

CONFIG_TOML = """\
[ticketing]
provider = "jira"
project = "PROJ"

[board.roles]
todo = "To Do"
in_progress = "In Progress"
done = "Done"
"""


class CliBlockersJson(unittest.TestCase):
    """TKT-13's literal acceptance, at the level the skills actually call:
    `tkt blockers KEY --json` returns the same result regardless of the site's
    link-type descriptions.

    Real argparse, real Config.load, real error handling in `cli.main`; only
    the adapter factory is swapped, and the stub's transport is stubbed too,
    so nothing touches acli or the network.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config_path = Path(tmp.name) / "config.toml"
        self.config_path.write_text(CONFIG_TOML, encoding="utf-8")

    def run_cli(self, stub, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "get_adapter", return_value=stub), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--config", str(self.config_path), *argv])
        return code, out.getvalue(), err.getvalue()

    def blockers_json(self, links):
        stub = StubRestJira(issue(links))
        code, out, err = self.run_cli(stub, "blockers", "PROJ-1", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")
        return out

    def test_custom_wording_gives_byte_identical_json(self):
        default = self.blockers_json([inward(BLOCKS, "PROJ-2"),
                                      inward(BLOCKS, "PROJ-4", category="done")])
        custom = self.blockers_json([inward(OPAQUE_NAMED, "PROJ-2"),
                                     inward(OPAQUE_NAMED, "PROJ-4",
                                            category="done")])
        self.assertEqual(custom, default)
        self.assertEqual(json.loads(custom),
                         [{"key": "PROJ-2", "resolved": False}])

    def test_plugin_type_gives_byte_identical_json(self):
        default = self.blockers_json([inward(BLOCKS, "PROJ-2")])
        plugin = self.blockers_json([inward(BLOCKER_PLUGIN, "PROJ-2")])
        self.assertEqual(plugin, default)

    def test_jq_length_is_one_when_two_link_types_match_the_same_pair(self):
        # skills/select-ticket reads `| jq 'length'`; a duplicate would make
        # this 2 and misreport the ticket.
        out = self.blockers_json([inward(BLOCKS, "PROJ-2"),
                                  inward(BLOCKER_PLUGIN, "PROJ-2")])
        self.assertEqual(len(json.loads(out)), 1)

    def test_inverse_link_types_report_no_blockers(self):
        out = self.blockers_json([inward(UNBLOCKS, "PROJ-2"),
                                  inward(NON_BLOCKING, "PROJ-3"),
                                  inward(NONBLOCKING_PLAIN, "PROJ-4")])
        self.assertEqual(json.loads(out), [])
        self.assertEqual(out, self.blockers_json([]))

    def test_null_side_exits_0_instead_of_raising(self):
        # An AttributeError here would bypass core/errors.py and print a
        # traceback; the CLI must stay on the typed exit-code contract.
        out = self.blockers_json([{"type": dict(BLOCKS), "inwardIssue": None,
                                   "outwardIssue": {"key": "PROJ-3"}}])
        self.assertEqual(json.loads(out), [])

    def test_human_output_matches_across_wordings(self):
        stub_default = StubRestJira(issue([inward(BLOCKS, "PROJ-2")]))
        stub_custom = StubRestJira(issue([inward(OPAQUE_NAMED, "PROJ-2")]))
        _, out_default, _ = self.run_cli(stub_default, "blockers", "PROJ-1")
        _, out_custom, _ = self.run_cli(stub_custom, "blockers", "PROJ-1")
        self.assertEqual(out_default, "PROJ-2  (unresolved)\n")
        self.assertEqual(out_custom, out_default)

    def test_no_blockers_human_output(self):
        stub = StubRestJira(issue([]))
        code, out, err = self.run_cli(stub, "blockers", "PROJ-1")
        self.assertEqual(code, 0)
        self.assertEqual(out, "(no unresolved blockers)\n")


if __name__ == "__main__":
    unittest.main()
