"""scripts/ops/notify_deploy_issues.py: which issues a deploy notifies or closes, with gh stubbed out."""

from __future__ import annotations

import pytest

from scripts.ops import notify_deploy_issues as nd


class FakeGh(nd.Gh):
    """Answers the endpoints the script reads; records writes."""

    def __init__(self, bodies=None, issues=None, comments=None, commit_prs=None, dry_run=False):
        super().__init__("o/r", dry_run=dry_run)
        self.bodies = bodies or {}
        self.issues = issues or {}
        self.comments = comments or {}
        self.commit_prs = commit_prs or {}
        self.writes = []

    def api(self, path, method="GET", body=None, paginate=False):
        if method != "GET":
            self.writes.append((method, path, body))
            return None
        if path.startswith("commits/"):
            return [{"number": n} for n in self.commit_prs.get(path.split("/")[1], [])]
        if path.startswith("pulls/"):
            return {"body": self.bodies.get(int(path.split("/")[1]), "")}
        if path.endswith("/comments?per_page=100"):
            return [{"body": b} for b in self.comments.get(int(path.split("/")[1]), [])]
        if path.startswith("issues/"):
            return {"state": self.issues[int(path.split("/")[1])]}
        raise AssertionError(f"unexpected gh call {method} {path}")


@pytest.mark.parametrize("body,expect", [
    ("Closes #12", [12]),
    ("Addresses #3, refs #4 and related to #5", [3, 4, 5]),
    ("Fixes issue #9", [9]),
    ("Fixed #1. Resolved #2. Resolves #2 again", [1, 2]),
    ("see #50 for context", []),
    ("prefixes #7", []),
    ("feat(web): Observed nutshell (#690)", []),
    (None, []),
])
def test_only_keyword_references_count(body, expect):
    assert nd.referenced_issues(body) == expect


def test_open_issue_is_commented_and_closed():
    gh = FakeGh(bodies={10: "Addresses #3"}, issues={3: "open"})
    assert nd.notify(gh, [10]) == ["close #3 (from PR #10)"]
    assert gh.writes == [
        ("POST", "issues/3/comments", {"body": nd.DEPLOYED_MSG}),
        ("PATCH", "issues/3", {"state": "closed", "state_reason": "completed"}),
    ]


def test_closed_issue_is_commented_once():
    gh = FakeGh(bodies={10: "Closes #4\nCloses #5"}, issues={4: "closed", 5: "closed"},
                comments={5: ["Deployed to https://weather.flyfun.aero — earlier deploy"]})
    assert nd.notify(gh, [10]) == ["notify #4 (already closed, from PR #10)",
                                   "already notified #5 (from PR #10)"]
    assert gh.writes == [("POST", "issues/4/comments", {"body": nd.DEPLOYED_MSG})]


def test_issue_shared_by_two_prs_is_handled_once():
    gh = FakeGh(bodies={10: "Closes #4", 11: "Refs #4"}, issues={4: "closed"})
    assert nd.notify(gh, [10, 11]) == ["notify #4 (already closed, from PR #10)"]
    assert len(gh.writes) == 1


def test_dry_run_writes_nothing():
    gh = FakeGh(bodies={10: "Addresses #3\nCloses #4"}, issues={3: "open", 4: "closed"}, dry_run=True)
    assert nd.notify(gh, [10]) == ["would close #3 (from PR #10)",
                                   "would notify #4 (already closed, from PR #10)"]
    assert gh.writes == []


def test_prs_are_collected_across_commits_without_duplicates():
    gh = FakeGh(commit_prs={"a": [701], "b": [699, 701], "c": []})
    assert nd.prs_for_commits(gh, ["a", "b", "c"]) == [699, 701]
