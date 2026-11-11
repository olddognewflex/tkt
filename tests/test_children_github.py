"""TKT-19: `tkt view` on GitHub reports an issue's sub-issues as children.

One extra `gh api .../sub_issues --paginate` call, made by `view` only. Keys
follow how this adapter keys tickets: a same-repo sub-issue is its bare
number ("12"); one in another repo is "owner/repo#12", so it can't be read
as a same-repo number. A host without the sub-issues API answers 404, which
means no children rather than a failed view.

Fully offline: `gh` is stubbed at the `_gh` / `_gh_json` boundary. Run from
the repo root:

    python3 -m unittest tests.test_children_github
"""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.github import GithubAdapter  # noqa: E402
from core.config import Config  # noqa: E402
from core.errors import ProviderError  # noqa: E402

ROLES = {"todo": "Todo", "in_progress": "In Progress", "done": "Done"}


def make_config(board="labels", repo="owner/repo"):
    gh = {"board": board, "repo": repo}
    if board == "projectv2":
        gh.update(project_number="1", project_owner="owner")
    return Config({
        "ticketing": {"provider": "github"},
        "board": {"roles": dict(ROLES)},
        "github": gh,
        "queries": {"tier1": "is:open"},
    }, Path("/nonexistent/.sdlc/config.toml"))


def sub(number, repo="owner/repo", shape="full_name"):
    """A sub-issue as the REST API returns it, naming its repo one way."""
    s = {"number": number, "title": f"sub {number}"}
    if shape == "full_name":
        s["repository"] = {"full_name": repo}
    elif shape == "repository_url":
        s["repository_url"] = f"https://api.github.com/repos/{repo}"
    elif shape == "html_url":
        s["html_url"] = f"https://github.com/{repo}/issues/{number}"
    return s


GITHUB_URL = "https://github.com/owner/repo/issues/{n}"


def issue_payload(number, url=GITHUB_URL):
    return {"number": number, "title": "parent", "body": "", "state": "OPEN",
            "url": url.format(n=number) if url else "", "labels": [],
            "assignees": []}


class StubGithub(GithubAdapter):
    """`pages` is what `gh api --paginate` prints: a list of JSON arrays,
    written back to back the way gh does. `api_error` makes that call fail."""

    def __init__(self, config, pages=((),), api_error=None, issue_url=GITHUB_URL):
        super().__init__(config)
        self.pages = pages
        self.issue_url = issue_url
        self.api_error = api_error
        self.calls: list[tuple[str, ...]] = []

    def _gh_json(self, *args):
        self.calls.append(args)
        if args[:2] == ("issue", "view"):
            return issue_payload(int(args[2]), self.issue_url)
        if args[:2] == ("issue", "list"):
            return [issue_payload(7)]
        if args[:2] == ("project", "item-list"):
            return {"items": [{"id": "I7", "status": "Todo",
                               "content": {"type": "Issue", "number": 7,
                                           "repository": "owner/repo"}}]}
        if args[:2] == ("api", "graphql"):
            return {"data": {"repository": {}}}   # no issue states
        raise AssertionError(f"unexpected gh json call: {args}")

    def _gh(self, *args):
        self.calls.append(args)
        if args[0] == "api":
            if self.api_error:
                raise self.api_error
            if args[1].endswith("/parent"):
                # TKT-72: `view` also asks for the parent; none here
                # (tests/test_parent_github.py covers that call).
                raise ProviderError(f"gh {' '.join(args)} failed: "
                                    "gh: Not Found (HTTP 404)")
            return "".join(json.dumps(list(p)) for p in self.pages) + "\n"
        return ""

    def _project(self):
        return {"id": "P", "status_field_id": "F",
                "options": {"Todo": "o1"}, "option_names": ["Todo"]}

    def api_calls(self):
        """The sub-issues calls (the parent call is tested on its own)."""
        return [c for c in self.calls
                if c[0] == "api" and "/sub_issues" in c[1]]


def not_found():
    return ProviderError("gh api repos/owner/repo/issues/7/sub_issues --paginate "
                         "failed: gh: Not Found (HTTP 404)")


class SubIssueKeys(unittest.TestCase):
    def view(self, pages, repo="owner/repo", board="labels"):
        return StubGithub(make_config(board, repo), pages=pages).view("7")

    def test_same_repo_is_a_bare_number(self):
        self.assertEqual(self.view([[sub(12), sub(13)]]).children, ["12", "13"])

    def test_other_repo_is_qualified(self):
        t = self.view([[sub(12), sub(12, "other/place")]])
        self.assertEqual(t.children, ["12", "other/place#12"])

    def test_repo_match_is_case_insensitive(self):
        t = self.view([[sub(12, "Owner/Repo"), sub(13, "OWNER/REPO",
                                                   shape="repository_url")]])
        self.assertEqual(t.children, ["12", "13"])

    def test_configured_repo_spelling_does_not_matter(self):
        t = self.view([[sub(12), sub(14, "other/place")]],
                      repo="https://github.com/Owner/Repo.git")
        self.assertEqual(t.children, ["12", "other/place#14"])

    def test_repo_from_repository_url_and_html_url(self):
        t = self.view([[sub(12, shape="repository_url"),
                        sub(13, "other/place", shape="repository_url"),
                        sub(14, shape="html_url"),
                        sub(15, "other/place", shape="html_url")]])
        self.assertEqual(t.children,
                         ["12", "other/place#13", "14", "other/place#15"])

    def test_dedupe_and_own_key_dropped(self):
        t = self.view([[sub(12), sub(7), sub(12), sub(13)]])
        self.assertEqual(t.children, ["12", "13"])

    def test_other_repos_issue_with_own_number_is_kept(self):
        # other/place#7 is a different issue from this repo's #7.
        t = self.view([[sub(7, "other/place")]])
        self.assertEqual(t.children, ["other/place#7"])

    def test_every_paginated_page_is_read(self):
        # `gh api --paginate` prints `[...][...]`, which json.loads rejects.
        t = self.view([[sub(12), sub(13)], [sub(14)], []])
        self.assertEqual(t.children, ["12", "13", "14"])

    def test_entries_without_a_number_are_skipped(self):
        t = self.view([[{"title": "no number"}, None, sub(12)]])
        self.assertEqual(t.children, ["12"])

    def test_no_sub_issues(self):
        self.assertEqual(self.view([[]]).children, [])

    def test_to_dict_carries_children(self):
        self.assertEqual(self.view([[sub(12)]]).to_dict()["children"], ["12"])


class SubIssueCall(unittest.TestCase):
    def test_call_shape(self):
        gh = StubGithub(make_config(), pages=[[sub(12)]])
        gh.view("7")
        (call,) = gh.api_calls()
        self.assertEqual(call[1].split("?")[0], "repos/owner/repo/issues/7/sub_issues")
        self.assertIn("--paginate", call)
        self.assertEqual(call[call.index("--hostname") + 1], "github.com")

    def test_without_an_issue_url_bare_repo_passes_no_host(self):
        gh = StubGithub(make_config(), pages=[[sub(12)]], issue_url=None)
        self.assertEqual(gh.view("7").children, ["12"])
        (call,) = gh.api_calls()
        self.assertEqual(call[1].split("?")[0], "repos/owner/repo/issues/7/sub_issues")
        self.assertNotIn("--hostname", call)

    def test_without_an_issue_url_host_in_repo_is_passed_to_gh_api(self):
        gh = StubGithub(make_config(repo="ghe.example.com/owner/repo"),
                        pages=[[sub(12)]], issue_url=None)
        self.assertEqual(gh.view("7").children, ["12"])
        (call,) = gh.api_calls()
        self.assertEqual(call[1].split("?")[0], "repos/owner/repo/issues/7/sub_issues")
        self.assertEqual(call[call.index("--hostname") + 1], "ghe.example.com")

    def test_without_an_issue_url_fallback_tolerates_gh_spellings(self):
        for repo in ("ssh://git@ghe.corp/owner/repo.git",
                     "git@ghe.corp:owner/repo.git",
                     "https://user@ghe.corp:8443/owner/repo"):
            with self.subTest(repo=repo):
                gh = StubGithub(make_config(repo=repo), pages=[[sub(12)]],
                                issue_url=None)
                self.assertEqual(gh.view("7").children, ["12"])
                (call,) = gh.api_calls()
                self.assertEqual(call[1].split("?")[0],
                                 "repos/owner/repo/issues/7/sub_issues")
                self.assertEqual(call[call.index("--hostname") + 1], "ghe.corp")

    def test_404_means_no_children(self):
        gh = StubGithub(make_config(), api_error=not_found())
        t = gh.view("7")
        self.assertEqual(t.children, [])
        self.assertEqual(t.key, "7")

    def test_other_errors_propagate(self):
        err = ProviderError("gh api repos/owner/repo/issues/7/sub_issues "
                            "--paginate failed: gh: Server Error (HTTP 500)")
        with self.assertRaises(ProviderError):
            StubGithub(make_config(), api_error=err).view("7")

    def test_non_json_output_raises(self):
        gh = StubGithub(make_config())
        gh._gh = lambda *a: "[{\"number\": 1}]garbage"
        with self.assertRaises(ProviderError):
            gh.view("7")

    def test_projectv2_view_gets_children(self):
        gh = StubGithub(make_config("projectv2"), pages=[[sub(12)]])
        t = gh.view("7")
        self.assertEqual(t.children, ["12"])
        self.assertEqual(t.status, "Todo")

    def test_list_does_not_call_sub_issues(self):
        for board in ("labels", "projectv2"):
            with self.subTest(board=board):
                gh = StubGithub(make_config(board), pages=[[sub(12)]])
                tickets = gh.list(tier=1)
                self.assertEqual([t.key for t in tickets], ["7"])
                self.assertEqual(tickets[0].children, [])
                self.assertEqual(gh.api_calls(), [])

    def test_create_does_not_call_sub_issues(self):
        for board in ("labels", "projectv2"):
            with self.subTest(board=board):
                gh = StubGithub(make_config(board), pages=[[sub(12)]])
                real = gh._gh

                def fake_gh(*args, gh=gh, real=real):
                    if args[:2] == ("issue", "create"):
                        gh.calls.append(args)
                        return "https://github.com/owner/repo/issues/7\n"
                    return real(*args)
                gh._gh = fake_gh
                gh.transition = lambda *a: None   # projectv2 todo move
                t = gh.create("Story", "new")
                self.assertEqual(t.key, "7")
                self.assertEqual(t.children, [])
                self.assertEqual(gh.api_calls(), [])

    def test_blockers_and_transition_do_not_call_sub_issues(self):
        gh = StubGithub(make_config(), pages=[[sub(12)]])
        gh.blockers("7")
        gh.transition("7", "done")
        self.assertEqual(gh.api_calls(), [])


class HostAndRepoFromIssueUrl(unittest.TestCase):
    """The sub-issues path, `--hostname` and the same-repo test come from the
    issue's own `url`, not from parsing `[github].repo`: gh accepts spellings
    (ssh URLs, scp-like remotes, userinfo) that don't split into HOST/owner/name.
    """

    def check(self, repo, issue_url, host, nwo="owner/repo"):
        gh = StubGithub(make_config(repo=repo),
                        pages=[[sub(12, nwo), sub(13, "other/place")]],
                        issue_url=issue_url)
        t = gh.view("7")
        (call,) = gh.api_calls()
        self.assertEqual(call[1].split("?")[0], f"repos/{nwo}/issues/7/sub_issues")
        self.assertEqual(call[call.index("--hostname") + 1], host)
        self.assertEqual(t.children, ["12", "other/place#13"])

    def test_ssh_url_repo(self):
        self.check("ssh://git@github.com/owner/repo.git", GITHUB_URL, "github.com")

    def test_scp_like_repo(self):
        self.check("git@github.com:owner/repo", GITHUB_URL, "github.com")

    def test_userinfo_in_repo_url(self):
        self.check("https://user@ghe.corp/owner/repo",
                   "https://ghe.corp/owner/repo/issues/{n}", "ghe.corp")

    def test_ghes_issue_url(self):
        # The config names no host at all; only the URL says GHES.
        self.check("owner/repo",
                   "https://ghe.corp/owner/repo/issues/{n}", "ghe.corp")

    def test_issue_url_beats_a_misleading_repo_spelling(self):
        # A transferred/renamed repo: gh follows the redirect, and the URL it
        # reports is the canonical owner/name to query and compare against.
        self.check("owner/old-name",
                   "https://ghe.corp/Real/Repo/issues/{n}", "ghe.corp",
                   nwo="real/repo")

    def test_same_repo_match_uses_issue_url_case_insensitively(self):
        gh = StubGithub(make_config(repo="git@github.com:owner/repo"),
                        pages=[[sub(12, "OWNER/Repo"), sub(13, "other/place")]],
                        issue_url="https://github.com/Owner/REPO/issues/{n}")
        self.assertEqual(gh.view("7").children, ["12", "other/place#13"])

    def test_unparseable_issue_url_falls_back_to_config(self):
        gh = StubGithub(make_config(repo="ghe.corp/owner/repo"),
                        pages=[[sub(12)]], issue_url="not a url {n}")
        self.assertEqual(gh.view("7").children, ["12"])
        (call,) = gh.api_calls()
        self.assertEqual(call[1].split("?")[0], "repos/owner/repo/issues/7/sub_issues")
        self.assertEqual(call[call.index("--hostname") + 1], "ghe.corp")


if __name__ == "__main__":
    unittest.main()
