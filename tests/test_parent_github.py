"""TKT-72: `tkt view` on GitHub reports the issue a sub-issue sits under.

One extra `gh api repos/{owner}/{repo}/issues/{n}/parent` call, made by
`view` only (never `list`, nor `view(hierarchy=False)` as `blockers`,
`create` and `transition` use). The response is one issue object, keyed the
way children are: a same-repo parent is its bare number ("12"); one in
another repo is "owner/repo#12". GitHub answers 404 for "no parent", and a
host without the sub-issues API does too, so 404 means "" rather than a
failed view.

Fully offline: `gh` is stubbed at the `_gh` / `_gh_json` boundary. Run from
the repo root:

    python3 -m unittest tests.test_parent_github
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


def parent(number, repo="owner/repo", shape="repository_url"):
    """The parent issue as the REST API returns it, naming its repo one way."""
    p = {"number": number, "title": f"parent {number}", "state": "open"}
    if shape == "repository_url":
        p["repository_url"] = f"https://api.github.com/repos/{repo}"
    elif shape == "full_name":
        p["repository"] = {"full_name": repo}
    elif shape == "html_url":
        p["html_url"] = f"https://github.com/{repo}/issues/{number}"
    return p


GITHUB_URL = "https://github.com/owner/repo/issues/{n}"


def issue_payload(number, url=GITHUB_URL):
    return {"number": number, "title": "child", "body": "", "state": "OPEN",
            "url": url.format(n=number) if url else "", "labels": [],
            "assignees": []}


def http_error(args, status):
    return ProviderError(f"gh {' '.join(args)} failed: gh: Error (HTTP {status})")


class StubGithub(GithubAdapter):
    """`parent` is the object `.../parent` answers with (None: 404, no
    parent); `parent_raw` overrides the printed text; `parent_status` makes
    the call fail with that HTTP status. Sub-issues: always none."""

    def __init__(self, config, parent=None, parent_raw=None,
                 parent_status=None, issue_url=GITHUB_URL):
        super().__init__(config)
        self.parent = parent
        self.parent_raw = parent_raw
        self.parent_status = parent_status
        self.issue_url = issue_url
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
        if args[0] == "api" and "/sub_issues" in args[1]:
            return "[]\n"
        if args[0] == "api" and args[1].endswith("/parent"):
            if self.parent_status:
                raise http_error(args, self.parent_status)
            if self.parent_raw is not None:
                return self.parent_raw
            if self.parent is None:
                raise http_error(args, 404)
            return json.dumps(self.parent) + "\n"
        if args[0] == "api":
            raise AssertionError(f"unexpected gh api call: {args}")
        return ""

    def _project(self):
        return {"id": "P", "status_field_id": "F",
                "options": {"Todo": "o1"}, "option_names": ["Todo"]}

    def parent_calls(self):
        return [c for c in self.calls
                if c[0] == "api" and c[1].endswith("/parent")]


class ParentKey(unittest.TestCase):
    def view(self, parent_, repo="owner/repo", board="labels"):
        return StubGithub(make_config(board, repo), parent=parent_).view("7")

    def test_same_repo_is_a_bare_number(self):
        self.assertEqual(self.view(parent(12)).parent, "12")

    def test_other_repo_is_qualified(self):
        self.assertEqual(self.view(parent(12, "other/place")).parent,
                         "other/place#12")

    def test_repo_match_is_case_insensitive(self):
        self.assertEqual(self.view(parent(12, "Owner/REPO")).parent, "12")
        self.assertEqual(self.view(parent(12, "OWNER/repo", "full_name")).parent,
                         "12")

    def test_configured_repo_spelling_does_not_matter(self):
        t = self.view(parent(12), repo="https://github.com/Owner/Repo.git")
        self.assertEqual(t.parent, "12")

    def test_repo_from_each_shape(self):
        for shape in ("repository_url", "full_name", "html_url"):
            with self.subTest(shape=shape):
                self.assertEqual(self.view(parent(12, shape=shape)).parent, "12")
                self.assertEqual(
                    self.view(parent(12, "other/place", shape)).parent,
                    "other/place#12")

    def test_own_number_is_never_the_parent(self):
        self.assertEqual(self.view(parent(7)).parent, "")

    def test_other_repos_issue_with_own_number_is_kept(self):
        # other/place#7 is a different issue from this repo's #7.
        self.assertEqual(self.view(parent(7, "other/place")).parent,
                         "other/place#7")

    def test_object_without_a_number_is_no_parent(self):
        self.assertEqual(self.view({"title": "no number"}).parent, "")

    def test_to_dict_carries_parent(self):
        self.assertEqual(self.view(parent(12)).to_dict()["parent"], "12")

    def test_projectv2_view_gets_parent(self):
        t = self.view(parent(12), board="projectv2")
        self.assertEqual(t.parent, "12")
        self.assertEqual(t.status, "Todo")


class ParentCall(unittest.TestCase):
    def test_call_shape(self):
        gh = StubGithub(make_config(), parent=parent(12))
        gh.view("7")
        (call,) = gh.parent_calls()
        self.assertEqual(call[1], "repos/owner/repo/issues/7/parent")
        self.assertNotIn("--paginate", call)   # one object, not an array
        self.assertEqual(call[call.index("--hostname") + 1], "github.com")

    def test_ghes_issue_url_sets_hostname_and_path(self):
        gh = StubGithub(make_config(), parent=parent(12, "real/repo"),
                        issue_url="https://ghe.corp/Real/Repo/issues/{n}")
        self.assertEqual(gh.view("7").parent, "12")
        (call,) = gh.parent_calls()
        self.assertEqual(call[1], "repos/real/repo/issues/7/parent")
        self.assertEqual(call[call.index("--hostname") + 1], "ghe.corp")

    def test_without_an_issue_url_falls_back_to_config(self):
        gh = StubGithub(make_config(repo="ghe.corp/owner/repo"),
                        parent=parent(12), issue_url=None)
        self.assertEqual(gh.view("7").parent, "12")
        (call,) = gh.parent_calls()
        self.assertEqual(call[1], "repos/owner/repo/issues/7/parent")
        self.assertEqual(call[call.index("--hostname") + 1], "ghe.corp")

    def test_without_an_issue_url_bare_repo_passes_no_host(self):
        gh = StubGithub(make_config(), parent=parent(12), issue_url=None)
        self.assertEqual(gh.view("7").parent, "12")
        (call,) = gh.parent_calls()
        self.assertNotIn("--hostname", call)

    def test_404_means_no_parent(self):
        gh = StubGithub(make_config(), parent=None)
        t = gh.view("7")
        self.assertEqual(t.parent, "")
        self.assertEqual(t.key, "7")
        self.assertEqual(len(gh.parent_calls()), 1)

    def test_other_errors_propagate(self):
        with self.assertRaises(ProviderError):
            StubGithub(make_config(), parent_status=500).view("7")

    def test_non_json_output_raises(self):
        for raw in ("garbage", "", "{\"number\": 12"):
            with self.subTest(raw=raw):
                with self.assertRaises(ProviderError):
                    StubGithub(make_config(), parent_raw=raw).view("7")

    def test_list_makes_no_parent_call(self):
        for board in ("labels", "projectv2"):
            with self.subTest(board=board):
                gh = StubGithub(make_config(board), parent=parent(12))
                tickets = gh.list(tier=1)
                self.assertEqual([t.key for t in tickets], ["7"])
                self.assertEqual(tickets[0].parent, "")
                self.assertEqual(gh.parent_calls(), [])

    def test_hierarchy_false_makes_no_parent_call(self):
        for board in ("labels", "projectv2"):
            with self.subTest(board=board):
                gh = StubGithub(make_config(board), parent=parent(12))
                t = gh.view("7", hierarchy=False)
                self.assertEqual(t.parent, "")
                self.assertEqual(gh.parent_calls(), [])

    def test_blockers_and_transition_make_no_parent_call(self):
        gh = StubGithub(make_config(), parent=parent(12))
        gh.blockers("7")
        gh.transition("7", "done")
        self.assertEqual(gh.parent_calls(), [])

    def test_create_makes_no_parent_call(self):
        for board in ("labels", "projectv2"):
            with self.subTest(board=board):
                gh = StubGithub(make_config(board), parent=parent(12))
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
                self.assertEqual(t.parent, "")
                self.assertEqual(gh.parent_calls(), [])


if __name__ == "__main__":
    unittest.main()
