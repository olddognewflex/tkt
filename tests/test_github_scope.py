"""Regression tests for TKT-62: in projectv2 mode the GitHub adapter must scope
to its own repo. A Project can span repos, and issue numbers repeat across
them, so `list` must drop other repos' items and every number lookup (`view`'s
status, `transition`'s item) must match on repo as well as number.

Fully offline: `gh` is stubbed at the `_gh_json` boundary. Run from the repo
root:

    python3 -m unittest tests.test_github_scope
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.github import GithubAdapter  # noqa: E402
from core.config import Config  # noqa: E402
from core.errors import NotFoundError  # noqa: E402

ROLES = {"todo": "Todo", "in_progress": "In Progress", "done": "Done"}


def item(item_id, repo, number, status, title="t", with_repo_field=True):
    content = {"type": "Issue", "number": number, "title": title, "body": "",
               "url": f"https://github.com/{repo}/issues/{number}"}
    if with_repo_field:
        content["repository"] = repo
    return {"id": item_id, "status": status, "content": content}


# Both repos have an issue #7, in different lanes.
ITEMS = [
    item("MINE-7", "owner/repo", 7, "Todo", title="mine"),
    item("THEIRS-7", "other/elsewhere", 7, "In Progress", title="theirs"),
    item("THEIRS-9", "other/elsewhere", 9, "Todo"),
    item("MINE-8", "Owner/Repo", 8, "Todo"),                    # case differs
    item("MINE-10", "owner/repo", 10, "Todo", with_repo_field=False),  # URL only
    {"id": "DRAFT", "status": "Todo",
     "content": {"type": "DraftIssue", "title": "draft"}},
]

# The other repo's #7 first: what a number-only lookup would trip on.
REVERSED = [ITEMS[1], ITEMS[0]] + ITEMS[2:]


def make_config(repo="owner/repo"):
    return Config({
        "ticketing": {"provider": "github"},
        "board": {"roles": dict(ROLES)},
        "github": {"board": "projectv2", "repo": repo,
                   "project_number": "1", "project_owner": "owner"},
        "queries": {"tier1": "status:Todo"},
    }, Path("/nonexistent/.sdlc/config.toml"))


class StubGithub(GithubAdapter):
    """GithubAdapter over a fixed cross-repo Project."""

    def __init__(self, config, items=ITEMS):
        super().__init__(config)
        self.calls: list[tuple[str, ...]] = []
        self.items = items

    def _gh_json(self, *args):
        self.calls.append(args)
        if args[:2] == ("project", "item-list"):
            return {"items": self.items}
        if args[:2] == ("issue", "view"):
            return {"number": int(args[2]), "title": "mine", "body": "",
                    "state": "OPEN", "url": "", "labels": [], "assignees": []}
        raise AssertionError(f"unexpected gh call: {args}")

    def _gh(self, *args):
        self.calls.append(args)
        return ""

    def _project(self):
        return {"id": "P", "status_field_id": "F",
                "options": {lane: f"opt-{lane}" for lane in ROLES.values()},
                "option_names": list(ROLES.values())}

    def _is_closed(self, number):
        return False


class ProjectV2Scope(unittest.TestCase):
    def setUp(self):
        self.gh = StubGithub(make_config())

    def test_list_returns_only_this_repos_issues(self):
        keys = [t.key for t in self.gh.list(tier=1)]
        self.assertEqual(sorted(keys), ["10", "7", "8"])
        titles = [t.summary for t in self.gh.list(tier=1) if t.key == "7"]
        self.assertEqual(titles, ["mine"])

    def test_view_reads_status_from_this_repos_item(self):
        # Put the other repo's #7 (In Progress) first, so a number-only
        # match would pick it.
        self.gh._project_items = lambda query=None: REVERSED
        self.assertEqual(self.gh.view("7").status, "Todo")

    def test_view_of_number_only_in_another_repo_has_no_status(self):
        self.assertEqual(self.gh.view("9").status, "")

    def test_transition_moves_this_repos_item(self):
        self.gh._project_items = lambda query=None: REVERSED
        self.gh.transition("7", "in_progress")
        edits = [c for c in self.gh.calls if c[:2] == ("project", "item-edit")]
        self.assertEqual(len(edits), 1)
        self.assertIn("MINE-7", edits[0])
        self.assertNotIn("THEIRS-7", edits[0])

    def test_transition_of_another_repos_number_edits_nothing(self):
        with self.assertRaises(NotFoundError):
            self.gh.transition("9", "in_progress")
        self.assertFalse([c for c in self.gh.calls
                          if c[:2] == ("project", "item-edit")])

    def test_host_prefixed_repo_spellings_still_match(self):
        for repo in ("github.com/Owner/repo", "owner/repo.git",
                     "https://github.com/owner/repo/", " owner/repo "):
            with self.subTest(repo=repo):
                gh = StubGithub(make_config(repo))
                self.assertEqual(sorted(t.key for t in gh.list(tier=1)),
                                 ["10", "7", "8"])


class DoctorScope(unittest.TestCase):
    def check(self, gh):
        gh._gh = lambda *a: ""
        gh.whoami = lambda: "me"
        return next(c for c in gh.doctor() if c.name == "project items in repo")

    def test_passes_when_repo_has_items(self):
        c = self.check(StubGithub(make_config()))
        self.assertTrue(c.ok)
        self.assertIn("3 of 6", c.detail)

    def test_fails_when_no_item_is_in_repo(self):
        # The silent case: a mistyped repo lists as an idle board.
        c = self.check(StubGithub(make_config("owner/typo")))
        self.assertFalse(c.ok)

    def test_empty_project_is_not_a_failure(self):
        self.assertTrue(self.check(StubGithub(make_config(), items=[])).ok)


if __name__ == "__main__":
    unittest.main()
