"""Regression tests for TKT-10: the GitHub adapter must close the issue on a
terminal transition and reopen it on the way back, driven by `[board].close_on`.

Fully offline: `gh` is stubbed at the `_gh` boundary and every call is recorded,
so the tests assert on the exact command list. Run from the repo root:

    python3 -m unittest tests.test_github_close
"""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.github import GithubAdapter  # noqa: E402
from core.config import Config  # noqa: E402
from core.errors import ConfigError  # noqa: E402

ROLES = {"todo": "Todo", "in_progress": "In Progress", "done": "Done",
         "cancelled": "Cancelled"}


def make_config(close_on=None, board="labels"):
    data = {
        "ticketing": {"provider": "github"},
        "board": {"roles": dict(ROLES)},
        "github": {"board": board, "repo": "owner/repo"},
    }
    if close_on is not None:
        data["board"]["close_on"] = close_on
    return Config(data, Path("/nonexistent/.sdlc/config.toml"))


class StubGithub(GithubAdapter):
    """GithubAdapter with `gh` replaced by an in-memory issue."""

    def __init__(self, config, state="OPEN", labels=()):
        super().__init__(config)
        self.calls: list[tuple[str, ...]] = []
        self.state = state
        self.labels = list(labels)

    def _gh(self, *args):
        self.calls.append(args)
        if args[:2] == ("issue", "view"):
            return json.dumps({
                "number": 24, "title": "t", "body": "", "state": self.state,
                "url": "", "labels": [{"name": n} for n in self.labels],
                "assignees": [],
            })
        if args[:2] == ("issue", "close"):
            self.state = "CLOSED"
        if args[:2] == ("issue", "reopen"):
            self.state = "OPEN"
        return ""

    # projectv2 plumbing, stubbed so transition() can run in that mode too.
    def _project(self):
        return {"id": "P", "status_field_id": "F",
                "options": {lane: f"opt-{lane}" for lane in ROLES.values()},
                "option_names": list(ROLES.values())}

    def _find_item_id(self, number):
        return "ITEM"

    # helpers
    def verbs(self):
        return [c[:2] for c in self.calls]

    def call(self, verb):
        return next(c for c in self.calls if c[:2] == ("issue", verb))


class ConfigParsing(unittest.TestCase):
    def test_absent_defaults_to_empty(self):
        self.assertEqual(make_config().close_on, [])

    def test_roles_are_kept(self):
        self.assertEqual(make_config(["done", "cancelled"]).close_on, ["done", "cancelled"])

    def test_unknown_role_is_a_config_error(self):
        with self.assertRaises(ConfigError):
            make_config(["shipped"])

    def test_bare_string_is_a_config_error(self):
        """`close_on = "done"` must not be iterated as characters."""
        with self.assertRaises(ConfigError) as ctx:
            make_config("done")
        self.assertIn("array", str(ctx.exception))

    def test_other_providers_accept_the_key(self):
        """Acceptance 4: a shared [board] block may carry close_on for a backend
        that has no terminal state; construction must succeed and nothing else
        reads the key."""
        data = {"ticketing": {"provider": "markdown"},
                "board": {"roles": dict(ROLES), "close_on": ["done"]},
                "markdown": {"board_dir": "/nonexistent", "state_dir": "/nonexistent"}}
        cfg = Config(data, Path("/nonexistent/.sdlc/config.toml"))
        self.assertEqual(cfg.close_on, ["done"])


class LabelsMode(unittest.TestCase):
    def test_done_closes_issue(self):
        """The reported bug: a done transition only swapped the label."""
        gh = StubGithub(make_config(["done"]), labels=["Status: In Progress"])
        gh.transition("24", "done")
        self.assertIn(("issue", "close"), gh.verbs())
        self.assertEqual(gh.call("close")[2], "24")
        self.assertIn("completed", gh.call("close"))
        # Still labelled Done.
        self.assertIn("Status: Done", gh.call("edit"))
        self.assertEqual(gh.state, "CLOSED")

    def test_unset_is_byte_identical_to_today(self):
        gh = StubGithub(make_config(), labels=["Status: In Progress"])
        gh.transition("24", "done")
        self.assertEqual(gh.calls, [
            ("issue", "view", "24", "--repo", "owner/repo", "--json", gh._ISSUE_FIELDS),
            ("issue", "edit", "24", "--repo", "owner/repo",
             "--add-label", "Status: Done", "--remove-label", "Status: In Progress"),
        ])

    def test_active_role_does_not_close(self):
        gh = StubGithub(make_config(["done"]), labels=["Status: Todo"])
        gh.transition("24", "in_progress")
        self.assertNotIn(("issue", "close"), gh.verbs())
        self.assertNotIn(("issue", "reopen"), gh.verbs())

    def test_already_closed_is_not_closed_again(self):
        gh = StubGithub(make_config(["done"]), state="CLOSED", labels=["Status: Done"])
        gh.transition("24", "done")
        self.assertNotIn(("issue", "close"), gh.verbs())

    def test_moving_closed_issue_back_reopens_it(self):
        gh = StubGithub(make_config(["done"]), state="CLOSED", labels=["Status: Done"])
        gh.transition("24", "in_progress")
        self.assertIn(("issue", "reopen"), gh.verbs())
        self.assertEqual(gh.state, "OPEN")

    def test_cancelled_closes_as_not_planned(self):
        gh = StubGithub(make_config(["done", "cancelled"]), labels=["Status: Todo"])
        gh.transition("24", "cancelled")
        # gh's accepted spelling has a space; "not_planned" is rejected at flag parsing.
        self.assertEqual(gh.call("close"),
                         ("issue", "close", "24", "--repo", "owner/repo", "--reason", "not planned"))

    def test_literal_lane_name_still_closes(self):
        """`tkt transition 24 Done` passes the lane, not the role."""
        gh = StubGithub(make_config(["done"]), labels=["Status: In Progress"])
        gh.transition("24", "Done")
        self.assertIn(("issue", "close"), gh.verbs())

    def test_literal_lane_name_on_closed_issue_does_not_reopen(self):
        gh = StubGithub(make_config(["done"]), state="CLOSED", labels=["Status: Done"])
        gh.transition("24", "Done")
        self.assertNotIn(("issue", "reopen"), gh.verbs())
        self.assertNotIn(("issue", "close"), gh.verbs())

    def test_literal_cancelled_lane_uses_not_planned(self):
        gh = StubGithub(make_config(["done", "cancelled"]), labels=["Status: Todo"])
        gh.transition("24", "Cancelled")
        self.assertIn("not planned", gh.call("close"))

    def test_board_update_precedes_close(self):
        gh = StubGithub(make_config(["done"]), labels=["Status: In Progress"])
        gh.transition("24", "done")
        verbs = gh.verbs()
        self.assertLess(verbs.index(("issue", "edit")), verbs.index(("issue", "close")))


class ProjectV2Mode(unittest.TestCase):
    def test_done_closes_issue(self):
        gh = StubGithub(make_config(["done"], board="projectv2"))
        gh.transition("24", "done")
        self.assertIn(("project", "item-edit"), gh.verbs())
        self.assertIn(("issue", "close"), gh.verbs())

    def test_unset_leaves_projectv2_unchanged(self):
        gh = StubGithub(make_config(board="projectv2"))
        gh.transition("24", "done")
        self.assertEqual(gh.verbs(), [("project", "item-edit")])


if __name__ == "__main__":
    unittest.main()
