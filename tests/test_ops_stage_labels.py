"""scripts/ops/stage_labels.py: the transition rules, with gh stubbed out."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts.ops import stage_labels as sl


T0 = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeGh(sl.Gh):
    """Answers the few endpoints the script reads; records label writes."""

    def __init__(self, labels=None, comments=None, files=None, head_date=T0, issues=None):
        super().__init__("o/r")
        self.labels = {int(k): set(v) for k, v in (labels or {}).items()}
        self.comments = comments or []
        self.files = files or []
        self.head_date = head_date
        self.issues = issues or {}
        self.writes = []

    def api(self, path, method="GET", body=None, paginate=False):
        if path.startswith("commits/"):
            return {"commit": {"committer": {"date": iso(self.head_date)}}}
        if path.endswith("/comments?per_page=100"):
            return self.comments
        if "/files?" in path:
            return [{"filename": f} for f in self.files]
        if path.startswith("issues/") and path.endswith("/labels?per_page=100"):
            n = int(path.split("/")[1])
            return [{"name": l} for l in self.labels.get(n, set())]
        if path.startswith("issues/") and method == "PUT":
            n = int(path.split("/")[1])
            self.labels[n] = set(body["labels"])
            self.writes.append((n, sorted(body["labels"])))
            return None
        if path.startswith("issues/"):
            n = int(path.split("/")[1])
            return self.issues.get(n, {"state": "open"})
        raise AssertionError(f"unexpected gh call {method} {path}")


def pr(number=701, body="", branch="x", draft=False, created=T0 - timedelta(hours=1), labels=()):
    return {"number": number, "body": body, "head": {"ref": branch, "sha": "abc"}, "draft": draft,
            "created_at": iso(created), "labels": [{"name": l} for l in labels]}


# --- referenced issues ----------------------------------------------------------

@pytest.mark.parametrize("body,branch,expect", [
    ("Closes #690", "x", [690]),
    ("closes #690\n\nAddresses #12", "x", [690, 12]),
    ("Fixes: #5", "x", [5]),
    ("", "issue-690-web-observed", [690]),
    ("", "697-live-highlight", [697]),
    ("Closes #697", "697-live-highlight", [697]),
    ("see #640 for context", "feature-x", []),  # a bare mention is not a close keyword
])
def test_referenced_issues(body, branch, expect):
    assert sl.referenced_issues(pr(body=body, branch=branch)) == expect


# --- one stage at a time --------------------------------------------------------

def test_replace_stage_keeps_flags_and_other_family():
    cur = {"to-plan", "mac", "enhancement"}
    assert sl.replace_stage(cur, sl.ISSUE_STAGES, "implementing") == {"implementing", "mac", "enhancement"}
    assert sl.replace_stage(cur, sl.ISSUE_STAGES, None) == {"mac", "enhancement"}


def test_issue_if_unstaged_does_not_override():
    gh = FakeGh(labels={5: {"to-start"}})
    sl.set_issue_stage(gh, 5, "to-plan", if_unstaged=True)
    assert gh.writes == []
    sl.set_issue_stage(gh, 5, "implementing")
    assert gh.labels[5] == {"implementing"}


# --- PR stage derivation --------------------------------------------------------

def review(at: datetime, login="claude[bot]", first_line="## Code Review"):
    return {"user": {"login": login}, "body": f"{first_line}\n\n### Minor\n- x", "created_at": iso(at)}


def test_draft_is_working():
    assert sl.derive_pr_stage(FakeGh(), pr(draft=True), set()) == "working"


def test_fresh_pr_without_review_is_to_review():
    # created an hour ago, head commit at creation, no review yet: the brief is readable
    assert sl.derive_pr_stage(FakeGh(head_date=T0 - timedelta(hours=1)), pr(), set()) == "to-review"


def test_push_after_creation_without_review_is_working():
    assert sl.derive_pr_stage(FakeGh(head_date=T0), pr(), set()) == "working"


def test_review_on_head_is_to_review_and_keeps_to_land():
    gh = FakeGh(head_date=T0, comments=[review(T0 + timedelta(minutes=5))])
    assert sl.derive_pr_stage(gh, pr(), set()) == "to-review"
    assert sl.derive_pr_stage(gh, pr(), {"to-land"}) == "to-land"


def test_stale_review_before_head_does_not_count():
    gh = FakeGh(head_date=T0, comments=[review(T0 - timedelta(minutes=5))])
    assert sl.derive_pr_stage(gh, pr(), {"to-land"}) == "working"


def test_review_matcher_is_tolerant_of_decoration_but_not_of_author():
    gh = FakeGh(head_date=T0, comments=[review(T0 + timedelta(minutes=1), first_line="**Code Review**")])
    assert sl.has_review_on_head(gh, pr(), T0)
    gh = FakeGh(head_date=T0, comments=[review(T0 + timedelta(minutes=1), login="roznet")])
    assert not sl.has_review_on_head(gh, pr(), T0)


def test_touches_mac_only_for_app_paths():
    assert sl.touches_mac(FakeGh(files=["app/flyfun-weather/X.swift", "web/a.ts"]), 1)
    assert not sl.touches_mac(FakeGh(files=["web/a.ts", "weatherbrief/x.py"]), 1)


# --- Actions entry --------------------------------------------------------------

def run_event(monkeypatch, tmp_path, gh, name, payload):
    path = tmp_path / "event.json"
    path.write_text(json.dumps(payload))
    monkeypatch.setenv("GITHUB_EVENT_NAME", name)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    sl.handle_event(gh)


def test_issue_opened_gets_to_plan_unless_born_staged(monkeypatch, tmp_path):
    gh = FakeGh(labels={10: set(), 11: {"to-start"}})
    run_event(monkeypatch, tmp_path, gh, "issues", {"action": "opened", "issue": {"number": 10}})
    run_event(monkeypatch, tmp_path, gh, "issues", {"action": "opened", "issue": {"number": 11}})
    assert gh.labels[10] == {"to-plan"}
    assert gh.labels[11] == {"to-start"}


def test_owner_ready_comment_flips_to_start(monkeypatch, tmp_path):
    gh = FakeGh(labels={10: {"to-plan"}})
    ev = {"action": "created", "issue": {"number": 10}, "repository": {"owner": {"login": "roznet"}},
          "comment": {"user": {"login": "roznet"}, "body": " Ready \n"}}
    run_event(monkeypatch, tmp_path, gh, "issue_comment", ev)
    assert gh.labels[10] == {"to-start"}
    # someone else, or a longer comment, does nothing
    gh = FakeGh(labels={10: {"to-plan"}})
    ev["comment"] = {"user": {"login": "someone"}, "body": "ready"}
    run_event(monkeypatch, tmp_path, gh, "issue_comment", ev)
    ev["comment"] = {"user": {"login": "roznet"}, "body": "ready when you are"}
    run_event(monkeypatch, tmp_path, gh, "issue_comment", ev)
    assert gh.labels[10] == {"to-plan"}


def test_pr_opened_marks_issue_implementing_and_mac(monkeypatch, tmp_path):
    gh = FakeGh(labels={690: {"to-start"}, 699: set()}, files=["app/X.swift"])
    ev = {"action": "opened", "pull_request": pr(699, body="Closes #690", branch="issue-690-x")}
    run_event(monkeypatch, tmp_path, gh, "pull_request", ev)
    assert gh.labels[699] == {"to-review", "mac"}
    assert gh.labels[690] == {"implementing"}


def test_pr_push_and_draft_are_working_ready_is_to_review(monkeypatch, tmp_path):
    gh = FakeGh(labels={699: {"to-review"}})
    base = pr(699)
    run_event(monkeypatch, tmp_path, gh, "pull_request", {"action": "synchronize", "pull_request": base})
    assert gh.labels[699] == {"working"}
    run_event(monkeypatch, tmp_path, gh, "pull_request", {"action": "ready_for_review", "pull_request": base})
    assert gh.labels[699] == {"to-review"}
    run_event(monkeypatch, tmp_path, gh, "pull_request", {"action": "opened", "pull_request": pr(700, draft=True)})
    assert gh.labels[700] == {"working"}


def test_pr_closed_unmerged_returns_issue_to_start(monkeypatch, tmp_path):
    gh = FakeGh(labels={690: {"implementing", "mac"}})
    ev = {"action": "closed", "pull_request": {**pr(699, body="Closes #690"), "merged": False}}
    run_event(monkeypatch, tmp_path, gh, "pull_request", ev)
    assert gh.labels[690] == {"to-start", "mac"}


def test_pr_merged_strips_implementing_only_from_issues_left_open(monkeypatch, tmp_path):
    gh = FakeGh(labels={1: {"implementing"}, 2: {"implementing"}},
                issues={1: {"state": "closed"}, 2: {"state": "open"}})
    ev = {"action": "closed", "pull_request": {**pr(9, body="Closes #1\nAddresses #2"), "merged": True}}
    run_event(monkeypatch, tmp_path, gh, "pull_request", ev)
    assert gh.labels[1] == {"implementing"}  # closed by the merge; untouched
    assert gh.labels[2] == set()
