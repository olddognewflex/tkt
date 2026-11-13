"""TKT-80: `tkt link` on GitHub writes a blocker link both issues see.

Before the fix, `link` appended `Blocks #N` / `Blocked by #N` to the source
issue's body only, and `view` builds `blocked_by` from each issue's own body,
so after `tkt link 1 --to 2 --type blocks` issue 2 reported no blockers.

Now `link` uses GitHub's native issue dependencies (REST
`repos/{nwo}/issues/{n}/dependencies/blocked_by`), so the web UI shows the
same link; where that API 404s (older GHES) it writes both bodies instead.
`view` and `blockers` read the native edges alongside the body text.

Fully offline: `gh` is stubbed at the `_gh` / `_gh_json` boundary by a tiny
in-memory GitHub. Run from the repo root:

    python3 -m unittest tests.test_blocks_github
"""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.github import GithubAdapter  # noqa: E402
from core.config import Config  # noqa: E402
from core.errors import NotFoundError, ProviderError, UsageError  # noqa: E402

ROLES = {"todo": "Todo", "in_progress": "In Progress", "done": "Done"}
NWO = "owner/repo"


def make_config(repo=NWO):
    return Config({
        "ticketing": {"provider": "github"},
        "board": {"roles": dict(ROLES)},
        "github": {"board": "labels", "repo": repo},
        "queries": {"tier1": "is:open"},
    }, Path("/nonexistent/.sdlc/config.toml"))


def http_error(args, status):
    return ProviderError(f"gh {' '.join(args)} failed: gh: Error (HTTP {status})")


class FakeGithub(GithubAdapter):
    """An in-memory repo. `issues` maps number -> {body, state}; each issue's
    database id is 1000 + number. `deps` maps a blocked number -> list of
    blocker numbers (native dependencies). `native=False` makes every
    dependencies endpoint answer 404, as a host without the API does.
    `foreign` maps a blocked number -> extra blocker objects from other
    repos. Every call is recorded; `writes()` lists the mutating ones."""

    def __init__(self, issues, deps=None, native=True, foreign=None,
                 dep_status=None, repo=NWO, post_status=None,
                 api_repo=NWO, issue_url=None):
        super().__init__(make_config(repo))
        self.issues = {str(n): {"body": "", "state": "open", **v}
                       for n, v in issues.items()}
        self.deps = {str(k): [str(b) for b in v] for k, v in (deps or {}).items()}
        self.native = native
        self.foreign = foreign or {}
        self.dep_status = dep_status
        self.post_status = post_status
        self.api_repo = api_repo      # how GitHub names the repo today
        self.issue_url = issue_url or f"https://github.com/{NWO}/issues/{{n}}"
        self.calls: list[tuple[str, ...]] = []

    # ---- REST objects --------------------------------------------------------

    def rest_issue(self, n):
        i = self.issues[str(n)]
        return {"id": 1000 + int(n), "number": int(n), "body": i["body"],
                "state": i["state"],
                "repository_url": f"https://api.github.com/repos/{self.api_repo}",
                "html_url": f"https://github.com/{self.api_repo}/issues/{n}"}

    def blocking(self, n):
        return [b for b, blockers in self.deps.items() if str(n) in blockers]

    # ---- gh boundary ----------------------------------------------------------

    def _gh_json(self, *args):
        self.calls.append(args)
        if args[:2] == ("issue", "view"):
            n = args[2]
            if n not in self.issues:
                raise ProviderError(f"gh issue view {n} failed: not found")
            i = self.issues[n]
            return {"number": int(n), "title": f"issue {n}", "body": i["body"],
                    "state": i["state"].upper(), "closed": i["state"] == "closed",
                    "url": self.issue_url.format(n=n),
                    "labels": [], "assignees": []}
        raise AssertionError(f"unexpected gh json call: {args}")

    def _gh(self, *args):
        self.calls.append(args)
        if args[:2] == ("issue", "edit"):
            self.issues[args[2]]["body"] = args[args.index("--body") + 1]
            return ""
        if args[:2] == ("issue", "comment"):
            return ""
        if args[0] != "api":
            raise AssertionError(f"unexpected gh call: {args}")
        if "-X" in args:
            return self._post(args)
        path = args[1].split("?", 1)[0]
        parts = path.split("/")
        if "/sub_issues" in path:
            return "[]\n"
        if path.endswith("/parent"):
            raise http_error(args, 404)
        if "/dependencies/" in path:
            if self.dep_status:
                raise http_error(args, self.dep_status)
            n, which = parts[4], parts[6]
            if not self.native or n not in self.issues:
                raise http_error(args, 404)
            nums = self.deps.get(n, []) if which == "blocked_by" else self.blocking(n)
            objs = [self.rest_issue(b) for b in nums]
            if which == "blocked_by":
                objs += self.foreign.get(n, [])
            return json.dumps(objs) + "\n"
        if len(parts) == 5 and parts[3] == "issues":
            n = parts[4]
            if n not in self.issues:
                raise http_error(args, 404)
            return json.dumps(self.rest_issue(n)) + "\n"
        raise AssertionError(f"unexpected gh api call: {args}")

    def _post(self, args):
        path = args[args.index("-X") + 2]
        parts = path.split("/")
        assert parts[5:] == ["dependencies", "blocked_by"], args
        blocked = parts[4]
        field = args[args.index("-F") + 1]
        assert field.startswith("issue_id="), args
        blocker = str(int(field.split("=", 1)[1]) - 1000)
        assert blocker in self.issues, args
        if self.post_status:
            raise http_error(args, self.post_status)
        if blocker in self.deps.get(blocked, []):
            raise http_error(args, 422)     # GitHub rejects a duplicate
        self.deps.setdefault(blocked, []).append(blocker)
        return "{}\n"

    # ---- helpers ----------------------------------------------------------

    def writes(self):
        return [c for c in self.calls
                if c[:2] == ("issue", "edit") or "-X" in c]

    def posts(self):
        return [c for c in self.calls if "-X" in c]

    def dep_calls(self):
        return [c for c in self.calls
                if c[0] == "api" and "/dependencies/" in c[1] and "-X" not in c]


def keys(blockers):
    return [b["key"] for b in blockers]


class Regression(unittest.TestCase):
    def test_blocked_issue_reports_its_blocker(self):
        # The reported bug: after `tkt link 1 --to 2 --type blocks`,
        # `tkt blockers 2` came back empty.
        gh = FakeGithub({1: {}, 2: {}})
        gh.link("1", "2", "blocks")
        self.assertEqual(keys(gh.blockers("2")), ["1"])

    def test_blocked_issue_reports_its_blocker_without_native_api(self):
        gh = FakeGithub({1: {}, 2: {}}, native=False)
        gh.link("1", "2", "blocks")
        self.assertEqual(keys(gh.blockers("2")), ["1"])


class BothDirections(unittest.TestCase):
    """One `link`, read back from either issue, for every blocker spelling
    and both write paths."""

    CASES = [
        # (link type, source, target) -> blocker, blocked
        ("blocks", "1", "2", "1", "2"),
        ("is blocked by", "2", "1", "1", "2"),
        ("depends on", "2", "1", "1", "2"),
        ("  Is Blocked By ", "2", "1", "1", "2"),
    ]

    def test_both_sides_read_back(self):
        for native in (True, False):
            for lt, src, dst, blocker, blocked in self.CASES:
                with self.subTest(native=native, link_type=lt):
                    gh = FakeGithub({1: {}, 2: {}}, native=native)
                    gh.link(src, dst, lt)
                    self.assertEqual(keys(gh.view(blocked).blocked_by), [blocker])
                    self.assertEqual(gh.view(blocker).blocks, [blocked])
                    self.assertEqual(gh.view(blocker).blocked_by, [])
                    self.assertEqual(gh.view(blocked).blocks, [])
                    self.assertEqual(keys(gh.blockers(blocked)), [blocker])
                    self.assertEqual(gh.blockers(blocker), [])

    def test_hash_prefixed_keys(self):
        gh = FakeGithub({1: {}, 2: {}})
        gh.link("#1", "#2", "blocks")
        self.assertEqual(keys(gh.blockers("2")), ["1"])


class NativeWrite(unittest.TestCase):
    def test_post_shape(self):
        gh = FakeGithub({1: {}, 2: {}})
        gh.link("1", "2", "blocks")
        (post,) = gh.posts()
        self.assertEqual(post[post.index("-X") + 1], "POST")
        self.assertEqual(post[post.index("-X") + 2],
                         "repos/owner/repo/issues/2/dependencies/blocked_by")
        # The database id of the blocker, typed (-F), not the number.
        self.assertEqual(post[post.index("-F") + 1], "issue_id=1001")

    def test_no_body_edit(self):
        gh = FakeGithub({1: {}, 2: {}})
        gh.link("1", "2", "blocks")
        self.assertFalse([c for c in gh.calls if c[:2] == ("issue", "edit")])
        self.assertEqual(gh.issues["1"]["body"], "")
        self.assertEqual(gh.issues["2"]["body"], "")

    def test_relink_is_a_noop(self):
        gh = FakeGithub({1: {}, 2: {}})
        gh.link("1", "2", "blocks")
        gh.link("1", "2", "blocks")
        gh.link("2", "1", "is blocked by")
        self.assertEqual(len(gh.posts()), 1)
        self.assertEqual(gh.deps, {"2": ["1"]})

    def test_relink_after_repo_rename_is_a_noop(self):
        # The config still names old/repo; GitHub answers with the new name.
        gh = FakeGithub({1: {}, 2: {}}, repo="old/repo", api_repo="new/repo")
        gh.link("1", "2", "blocks")
        gh.link("1", "2", "blocks")
        self.assertEqual(len(gh.posts()), 1)

    def test_failed_post_raises_and_does_not_fall_back(self):
        for status in (404, 422):
            with self.subTest(status=status):
                gh = FakeGithub({1: {}, 2: {}}, post_status=status)
                with self.assertRaises(ProviderError):
                    gh.link("1", "2", "blocks")
                self.assertFalse([c for c in gh.calls if c[:2] == ("issue", "edit")])

    def test_hostname_passed_for_ghes_repo(self):
        gh = FakeGithub({1: {}, 2: {}}, repo="ghe.corp/owner/repo")
        gh.link("1", "2", "blocks")
        api = [c for c in gh.calls if c[0] == "api"]
        self.assertTrue(api)
        for c in api:
            self.assertEqual(c[c.index("--hostname") + 1], "ghe.corp")

    def test_github_com_repo_passes_no_hostname(self):
        gh = FakeGithub({1: {}, 2: {}})
        gh.link("1", "2", "blocks")
        for c in gh.calls:
            self.assertNotIn("--hostname", c)


class FallbackWrite(unittest.TestCase):
    def test_writes_both_bodies(self):
        gh = FakeGithub({1: {"body": "first"}, 2: {"body": "second\n"}},
                        native=False)
        gh.link("1", "2", "blocks")
        self.assertEqual(gh.issues["1"]["body"], "first\n\nBlocks #2")
        self.assertEqual(gh.issues["2"]["body"], "second\n\nBlocked by #1")
        self.assertFalse(gh.posts())

    def test_empty_bodies(self):
        gh = FakeGithub({1: {}, 2: {}}, native=False)
        gh.link("2", "1", "depends on")
        self.assertEqual(gh.issues["1"]["body"], "Blocks #2")
        self.assertEqual(gh.issues["2"]["body"], "Blocked by #1")

    def test_relink_is_a_noop(self):
        gh = FakeGithub({1: {}, 2: {}}, native=False)
        gh.link("1", "2", "blocks")
        n = len(gh.writes())
        gh.link("1", "2", "blocks")
        gh.link("2", "1", "is blocked by")
        self.assertEqual(len(gh.writes()), n)
        self.assertEqual(n, 2)

    def test_completes_a_one_sided_link(self):
        # A link written before this fix: blocker side only. Re-running it
        # adds the missing side without duplicating the existing one.
        gh = FakeGithub({1: {"body": "Blocks #2"}, 2: {}}, native=False)
        gh.link("1", "2", "blocks")
        self.assertEqual(gh.issues["1"]["body"], "Blocks #2")
        self.assertEqual(gh.issues["2"]["body"], "Blocked by #1")
        self.assertEqual(len(gh.writes()), 1)

    def test_zero_padded_ref_counts_as_present(self):
        gh = FakeGithub({1: {"body": "Blocks #02"}, 2: {}}, native=False)
        gh.link("1", "2", "blocks")
        self.assertEqual(gh.issues["1"]["body"], "Blocks #02")
        self.assertEqual(gh.issues["2"]["body"], "Blocked by #1")

    def test_depends_on_spelling_counts_as_present(self):
        gh = FakeGithub({1: {}, 2: {"body": "Depends on #1"}}, native=False)
        gh.link("1", "2", "blocks")
        self.assertEqual(gh.issues["2"]["body"], "Depends on #1")
        self.assertEqual(gh.issues["1"]["body"], "Blocks #2")


class MissingIssue(unittest.TestCase):
    def test_missing_issue_fails_before_any_write(self):
        for native in (True, False):
            for src, dst in (("1", "9"), ("9", "1")):
                for lt in ("blocks", "is blocked by"):
                    with self.subTest(native=native, src=src, dst=dst, lt=lt):
                        gh = FakeGithub({1: {}}, native=native)
                        with self.assertRaises(NotFoundError):
                            gh.link(src, dst, lt)
                        self.assertEqual(gh.writes(), [])
                        self.assertEqual(gh.issues["1"]["body"], "")

    def test_other_api_error_propagates(self):
        gh = FakeGithub({1: {}, 2: {}}, dep_status=500)
        with self.assertRaises(ProviderError):
            gh.link("1", "2", "blocks")
        self.assertEqual(gh.writes(), [])


class BadKeys(unittest.TestCase):
    def test_non_numeric_key_is_a_usage_error(self):
        for src, dst in (("1", "abc"), ("x/y#2", "1"), ("", "1"), ("1", "\u00b2")):
            with self.subTest(src=src, dst=dst):
                gh = FakeGithub({1: {}, 2: {}})
                with self.assertRaises(UsageError):
                    gh.link(src, dst, "blocks")
                self.assertEqual(gh.calls, [])

    def test_self_link_is_a_usage_error(self):
        gh = FakeGithub({1: {}})
        with self.assertRaises(UsageError):
            gh.link("1", "#1", "blocks")
        self.assertEqual(gh.calls, [])

    def test_other_link_types_still_comment(self):
        gh = FakeGithub({1: {}, 2: {}})
        gh.link("1", "2", "Fixes")
        self.assertEqual(gh.calls,
                         [("issue", "comment", "1", "--repo", NWO, "--body", "Fixes #2")])


class Reading(unittest.TestCase):
    def test_body_text_links_still_parse(self):
        gh = FakeGithub({1: {"body": "Blocked by #5\nDepends on #6\nBlocks #7"},
                         5: {"state": "closed"}, 6: {}, 7: {}})
        t = gh.view("1")
        self.assertEqual(t.blocked_by, [{"key": "5", "resolved": True},
                                        {"key": "6", "resolved": False}])
        self.assertEqual(t.blocks, ["7"])
        self.assertEqual(keys(gh.blockers("1")), ["6"])

    def test_body_and_native_merge_without_duplicates(self):
        gh = FakeGithub({1: {"body": "Blocked by #5\nBlocks #7"},
                         5: {}, 6: {}, 7: {}},
                        deps={1: [5, 6], 7: [1]})
        t = gh.view("1")
        self.assertEqual(keys(t.blocked_by), ["5", "6"])
        self.assertEqual(t.blocks, ["7"])

    def test_native_resolved_comes_from_state(self):
        gh = FakeGithub({1: {}, 5: {"state": "closed"}, 6: {}}, deps={1: [5, 6]})
        self.assertEqual(gh.view("1").blocked_by,
                         [{"key": "5", "resolved": True},
                          {"key": "6", "resolved": False}])
        self.assertEqual(keys(gh.blockers("1")), ["6"])
        # No per-blocker `issue view` for natively known blockers.
        views = [c for c in gh.calls if c[:2] == ("issue", "view") and c[2] != "1"]
        self.assertEqual(views, [])

    def test_cross_repo_native_blocker_is_qualified(self):
        other = {"id": 77, "number": 3, "state": "open",
                 "repository_url": "https://api.github.com/repos/other/place"}
        gh = FakeGithub({1: {}}, foreign={"1": [other]})
        self.assertEqual(keys(gh.view("1").blocked_by), ["other/place#3"])
        self.assertEqual(keys(gh.blockers("1")), ["other/place#3"])

    def test_404_reads_body_only(self):
        gh = FakeGithub({1: {"body": "Blocked by #2"}, 2: {}}, native=False)
        self.assertEqual(keys(gh.view("1").blocked_by), ["2"])
        self.assertEqual(keys(gh.blockers("1")), ["2"])

    def test_other_dependency_error_propagates(self):
        # `tkt blockers` must not report "no blockers" when it could not
        # look; select-ticket excludes a candidate it cannot check.
        gh = FakeGithub({1: {}}, dep_status=502)
        with self.assertRaises(ProviderError):
            gh.blockers("1")

    def test_blockers_skips_hierarchy_calls(self):
        gh = FakeGithub({1: {}, 2: {}}, deps={2: [1]})
        gh.blockers("2")
        self.assertFalse([c for c in gh.calls if c[0] == "api" and
                          ("/sub_issues" in c[1] or c[1].endswith("/parent"))])
        # Its blockers only: no `/blocking` call.
        self.assertEqual([c[1] for c in gh.dep_calls()],
                         ["repos/owner/repo/issues/2/dependencies/blocked_by?per_page=100"])

    def test_view_without_hierarchy_makes_no_dependency_calls(self):
        # transition, create, the format gates and `tkt agents --enrich` read
        # no links; they keep their call count.
        gh = FakeGithub({1: {}, 2: {}}, deps={2: [1]})
        gh.view("2", hierarchy=False)
        self.assertEqual(gh.calls, [("issue", "view", "2", "--repo", NWO, "--json",
                                     GithubAdapter._ISSUE_FIELDS)])

    def test_zero_padded_body_ref_dedupes_with_native(self):
        gh = FakeGithub({1: {"body": "Blocked by #05"}, 5: {}}, deps={1: [5]})
        self.assertEqual(keys(gh.view("1").blocked_by), ["5"])

    def test_dependency_calls_follow_the_issue_url(self):
        # Like children and parent: host and owner/name come from the
        # issue's own URL, whatever spelling the config uses.
        gh = FakeGithub({1: {}}, issue_url="https://ghe.corp/Real/Repo/issues/{n}")
        gh.view("1")
        gh.blockers("1")
        self.assertEqual(len(gh.dep_calls()), 3)
        for c in gh.dep_calls():
            self.assertTrue(c[1].startswith("repos/real/repo/issues/1/"), c)
            self.assertEqual(c[c.index("--hostname") + 1], "ghe.corp")

    def test_dependency_call_shape(self):
        gh = FakeGithub({1: {}})
        gh.view("1")
        paths = sorted(c[1] for c in gh.dep_calls())
        self.assertEqual(paths, [
            "repos/owner/repo/issues/1/dependencies/blocked_by?per_page=100",
            "repos/owner/repo/issues/1/dependencies/blocking?per_page=100",
        ])
        for c in gh.dep_calls():
            self.assertIn("--paginate", c)


if __name__ == "__main__":
    unittest.main()
