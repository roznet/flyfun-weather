#!/usr/bin/env python3
"""Stage labels: the label sits where the owner looks, and names what it asks of them.

An open issue carries exactly one ISSUE stage; an open PR exactly one PR stage;
either may carry flags. Once an issue has a PR it reads `implementing` and the
PR carries the stage from there on. Merging closes both, so nothing needs
clearing afterwards. Rationale and the full table: designs/stage-labels.md.

    stage_labels.py labels                    create / recolour every label
    stage_labels.py issue N STAGE [--if-unstaged]   to-plan | to-start | implementing | none
    stage_labels.py pr N STAGE                working | to-review | to-land
    stage_labels.py flag N FLAG               mac | question | blocked   (issue or PR)
    stage_labels.py unflag N FLAG
    stage_labels.py pr-issues N               issues this PR closes / addresses
    stage_labels.py event                     GitHub Actions entry: reads GITHUB_EVENT_PATH
    stage_labels.py sync [--dry-run]          reconcile every open issue and PR from state
    stage_labels.py status                    the in-flight view + inconsistencies

Needs `gh` logged in (locally: run with the sandbox disabled, else gh returns
empty with exit 0). Repo from --repo, else $GH_REPO / $GITHUB_REPOSITORY, else
the checkout's `gh repo view` (or its `origin` remote when gh cannot run).

**Plan mode, when gh cannot write.** A cloud session has no working `gh` (its
GitHub writes go through the agent's GitHub connector, which a script cannot
call). When `gh auth status` fails, or with `--plan` / STAGE_LABELS_PLAN=1, the
script still decides: it reads the current labels without a token (public repo),
applies the same one-stage rules, changes nothing, prints the label sets to
apply and exits 3. The agent applies them with the connector (replace each
item's labels with the printed set). Exit 3 is never success: if the reads fail
too, the message says so and names the change to make by hand. Only this script and the workflows that call it
touch stage labels; skills call it rather than `gh issue edit` so one-stage-
at-a-time holds everywhere.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

ISSUE_STAGES = ("to-plan", "to-start", "implementing")
PR_STAGES = ("working", "to-review", "to-land")
FLAGS = ("mac", "question", "blocked")

# label -> (colour, description). Descriptions are what the owner sees on hover.
LABELS = {
    "to-plan": ("c5def5", "Issue: an idea, needs a plan in the thread"),
    "to-start": ("0e8a16", "Issue: plan agreed, run /implement-issue"),
    "implementing": ("bfdadc", "Issue: a PR exists, look at the PR list"),
    "working": ("ededed", "PR: agent on it, or bot review pending on the head"),
    "to-review": ("fbca04", "PR: bot review (or a fresh PR's brief) waiting for you"),
    "to-land": ("0e8a16", "PR: review loop clean, run /land-pr"),
    "mac": ("5319e7", "Needs Mac-only verification (see the PR body checklist)"),
    "question": ("d93f0b", "An agent is waiting on your answer in the thread"),
    "blocked": ("b60205", "Waiting on something external"),
}

CLOSE_RE = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|address(?:es|ed)?)\s*:?\s+#(\d+)"
)
BRANCH_RE = re.compile(r"^(?:issue-)?(\d+)-")
REVIEW_BOTS = {"claude", "claude[bot]"}
MAC_PATHS = ("app/",)
# A PR whose head moved more than this after creation has had a push; a bot review
# is then pending, so it reads `working` until the review lands.
PUSH_GRACE = timedelta(minutes=2)


# --------------------------------------------------------------------------- gh

# Commands that never change labels: plan mode has nothing to report for them.
READ_ONLY_CMDS = ("pr-issues", "status")

# Exit code for "decided, not applied: apply the printed plan with the connector".
EXIT_PLAN = 3


class PlanReadError(Exception):
    """Plan mode could not read GitHub without a token either."""


def gh_can_write() -> bool:
    """True when `gh` is installed and authenticated (Actions, a logged-in Mac)."""
    try:
        out = subprocess.run(["gh", "auth", "status"], text=True, capture_output=True)
    except FileNotFoundError:
        return False
    return out.returncode == 0


def anonymous_get(path: str, paginate: bool = False):
    """GET a GitHub REST path without a token (enough for a public repo)."""
    import urllib.error
    import urllib.request

    url: str | None = f"https://api.github.com/{path.lstrip('/')}"
    pages = []
    while url:
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json", "User-Agent": "stage_labels.py",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                pages.append(json.loads(resp.read() or b"null"))
                link = resp.headers.get("Link") or ""
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise PlanReadError(f"GET {path}: {exc}") from exc
        nxt = re.search(r'<([^>]+)>;\s*rel="next"', link)
        url = nxt.group(1) if (paginate and nxt) else None
    if paginate:
        return [item for page in pages for item in page]
    return pages[0]


class Gh:
    def __init__(self, repo: str, dry_run: bool = False, quiet: bool = False, plan: bool = False):
        self.repo = repo
        self.dry_run = dry_run
        self.quiet = quiet
        # Plan mode: read anonymously, record writes in ``pending`` instead.
        self.plan = plan
        self.pending: list[dict] = []

    def api(self, path: str, method: str = "GET", body: dict | None = None, paginate: bool = False):
        if self.plan:
            if method != "GET":
                raise AssertionError(f"plan mode never writes ({method} {path})")
            return anonymous_get(path if path.startswith("/") else f"repos/{self.repo}/{path}", paginate)
        cmd = ["gh", "api", "-X", method, path if path.startswith("/") else f"repos/{self.repo}/{path}"]
        if body is not None:
            cmd += ["--input", "-"]
        if paginate:
            cmd += ["--paginate", "--slurp"]
        out = subprocess.run(cmd, input=json.dumps(body) if body is not None else None,
                             text=True, capture_output=True)
        if out.returncode != 0:
            sys.exit(f"gh api {method} {path} failed: {out.stderr.strip() or out.stdout.strip()}")
        if not out.stdout.strip():
            return None
        data = json.loads(out.stdout)
        if paginate:  # --slurp gives one array per page
            data = [item for page in data for item in page]
        return data

    def labels_of(self, number: int) -> set[str]:
        return {l["name"] for l in self.api(f"issues/{number}/labels?per_page=100")}

    def set_labels(self, number: int, labels: set[str], before: set[str]) -> None:
        if labels == before:
            return
        added, removed = sorted(labels - before), sorted(before - labels)
        if self.plan:
            self.pending.append({"number": number, "add": added, "remove": removed,
                                 "labels": sorted(labels)})
            return
        if not self.quiet:
            print(f"#{number}: +{added} -{removed}" if not self.dry_run else f"#{number}: would +{added} -{removed}")
        if not self.dry_run:
            self.api(f"issues/{number}/labels", "PUT", {"labels": sorted(labels)})


REMOTE_RE = re.compile(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$|/git/([^/]+)/([^/]+?)(?:\.git)?/?$")


def repo_from_remote(url: str) -> str | None:
    """owner/name from an origin URL (github.com, ssh form, or a cloud git proxy)."""
    m = REMOTE_RE.search(url.strip())
    if not m:
        return None
    owner, name = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    return f"{owner}/{name}"


def repo_from_env(explicit: str | None, use_gh: bool = True) -> str:
    if explicit:
        return explicit
    for key in ("GH_REPO", "GITHUB_REPOSITORY"):
        if os.environ.get(key):
            return os.environ[key]
    if use_gh:
        out = subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
                             text=True, capture_output=True)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    out = subprocess.run(["git", "remote", "get-url", "origin"], text=True, capture_output=True)
    repo = repo_from_remote(out.stdout) if out.returncode == 0 else None
    if not repo:
        sys.exit("cannot determine the repo: pass --repo owner/name")
    return repo


# ------------------------------------------------------------------ mutations

def replace_stage(current: set[str], family: tuple[str, ...], stage: str | None) -> set[str]:
    new = {l for l in current if l not in family}
    if stage:
        new.add(stage)
    return new


def set_issue_stage(gh: Gh, number: int, stage: str, if_unstaged: bool = False) -> None:
    before = gh.labels_of(number)
    if if_unstaged and before & set(ISSUE_STAGES):
        return
    gh.set_labels(number, replace_stage(before, ISSUE_STAGES, None if stage == "none" else stage), before)


def set_pr_stage(gh: Gh, number: int, stage: str) -> None:
    before = gh.labels_of(number)
    gh.set_labels(number, replace_stage(before, PR_STAGES, stage), before)


def set_flag(gh: Gh, number: int, flag: str, on: bool) -> None:
    before = gh.labels_of(number)
    gh.set_labels(number, (before | {flag}) if on else (before - {flag}), before)


def ensure_labels(gh: Gh) -> None:
    existing = {l["name"]: l for l in gh.api("labels?per_page=100", paginate=True)}
    for name, (colour, desc) in LABELS.items():
        if gh.plan:
            cur = existing.get(name)
            if cur is None or cur.get("color") != colour or cur.get("description") != desc:
                gh.pending.append({"label": name, "color": colour, "description": desc,
                                   "action": "update" if cur else "create"})
            continue
        if name in existing:
            cur = existing[name]
            if cur.get("color") == colour and cur.get("description") == desc:
                continue
            print(f"update label {name}")
            if not gh.dry_run:
                gh.api(f"labels/{name}", "PATCH", {"color": colour, "description": desc})
        else:
            print(f"create label {name}")
            if not gh.dry_run:
                gh.api("labels", "POST", {"name": name, "color": colour, "description": desc})


# ------------------------------------------------------------------- derivation

def referenced_issues(pr: dict) -> list[int]:
    """Issues a PR closes or addresses: close keywords in the body, plus the branch name."""
    found: list[int] = []
    for m in CLOSE_RE.finditer(pr.get("body") or ""):
        n = int(m.group(1))
        if n not in found:
            found.append(n)
    m = BRANCH_RE.match((pr.get("head") or {}).get("ref") or "")
    if m and int(m.group(1)) not in found:
        found.append(int(m.group(1)))
    return found


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def head_commit_date(gh: Gh, pr: dict) -> datetime:
    commit = gh.api(f"commits/{pr['head']['sha']}")
    return parse_ts(commit["commit"]["committer"]["date"])


def has_review_on_head(gh: Gh, pr: dict, head_date: datetime) -> bool:
    for c in gh.api(f"issues/{pr['number']}/comments?per_page=100", paginate=True):
        if (c.get("user") or {}).get("login") not in REVIEW_BOTS:
            continue
        first = (c.get("body") or "").strip().splitlines()[:1]
        if first and "code review" in first[0].lower() and parse_ts(c["created_at"]) > head_date:
            return True
    return False


def touches_mac(gh: Gh, number: int) -> bool:
    files = gh.api(f"pulls/{number}/files?per_page=100", paginate=True)
    return any(f["filename"].startswith(MAC_PATHS) for f in files)


def derive_pr_stage(gh: Gh, pr: dict, current: set[str]) -> str:
    if pr.get("draft"):
        return "working"
    head_date = head_commit_date(gh, pr)
    if has_review_on_head(gh, pr, head_date):
        return "to-land" if "to-land" in current else "to-review"
    # No review on the head: a push since creation means a review is pending.
    if head_date > parse_ts(pr["created_at"]) + PUSH_GRACE:
        return "working"
    return "to-review"


def open_prs(gh: Gh) -> list[dict]:
    return gh.api("pulls?state=open&per_page=100", paginate=True)


def open_issues(gh: Gh) -> list[dict]:
    return [i for i in gh.api("issues?state=open&per_page=100", paginate=True) if "pull_request" not in i]


def sync(gh: Gh) -> list[str]:
    """Reconcile labels from state. Returns the inconsistencies found (also applied unless dry-run)."""
    notes: list[str] = []
    prs = open_prs(gh)
    implementing: dict[int, list[int]] = {}
    for pr in prs:
        for n in referenced_issues(pr):
            implementing.setdefault(n, []).append(pr["number"])
        before = {l["name"] for l in pr["labels"]}
        want = replace_stage(before, PR_STAGES, derive_pr_stage(gh, pr, before))
        if touches_mac(gh, pr["number"]):
            want.add("mac")
        if want != before:
            notes.append(f"PR #{pr['number']}: {sorted(before & set(PR_STAGES)) or 'no stage'} -> {sorted(want & set(PR_STAGES))}")
        gh.set_labels(pr["number"], want, before)

    for issue in open_issues(gh):
        n = issue["number"]
        before = {l["name"] for l in issue["labels"]}
        stage = next((s for s in ISSUE_STAGES if s in before), None)
        if n in implementing:
            want_stage = "implementing"
        elif stage == "implementing":
            want_stage = "to-start"
            notes.append(f"#{n}: labelled implementing but no open PR references it -> to-start")
        elif stage is None:
            want_stage = "to-plan"
            notes.append(f"#{n}: no stage label -> to-plan")
        else:
            want_stage = stage
        gh.set_labels(n, replace_stage(before, ISSUE_STAGES, want_stage), before)
    return notes


# ------------------------------------------------------------------- the view

def status(gh: Gh) -> None:
    prs = open_prs(gh)
    issues = open_issues(gh)
    by_pr: dict[int, list[int]] = {}
    for pr in prs:
        for n in referenced_issues(pr):
            by_pr.setdefault(n, []).append(pr["number"])

    print("Issues")
    for stage in ISSUE_STAGES:
        rows = [i for i in issues if stage in {l["name"] for l in i["labels"]}]
        print(f"  {stage} ({len(rows)})")
        for i in rows:
            flags = [f for f in FLAGS if f in {l["name"] for l in i["labels"]}]
            via = f"  -> PR {', '.join(f'#{p}' for p in by_pr[i['number']])}" if i["number"] in by_pr else ""
            print(f"    #{i['number']} {i['title']}{' [' + ','.join(flags) + ']' if flags else ''}{via}")
    unstaged = [i for i in issues if not {l["name"] for l in i["labels"]} & set(ISSUE_STAGES)]

    print("PRs")
    for stage in PR_STAGES:
        rows = [p for p in prs if stage in {l["name"] for l in p["labels"]}]
        print(f"  {stage} ({len(rows)})")
        for p in rows:
            flags = [f for f in FLAGS if f in {l["name"] for l in p["labels"]}]
            refs = referenced_issues(p)
            print(f"    #{p['number']} {p['title']}{' [' + ','.join(flags) + ']' if flags else ''}"
                  f"{'  (closes ' + ', '.join(f'#{n}' for n in refs) + ')' if refs else ''}")
    pr_unstaged = [p for p in prs if not {l["name"] for l in p["labels"]} & set(PR_STAGES)]

    print("Inconsistencies")
    problems = [f"#{i['number']} open issue with no stage label" for i in unstaged]
    problems += [f"PR #{p['number']} open PR with no stage label" for p in pr_unstaged]
    for i in issues:
        if "implementing" in {l["name"] for l in i["labels"]} and i["number"] not in by_pr:
            problems.append(f"#{i['number']} labelled implementing but no open PR references it")
    for p in prs:
        if not referenced_issues(p):
            problems.append(f"PR #{p['number']} references no issue (no close keyword, branch not issue-N-*)")
    dry = Gh(gh.repo, dry_run=True, quiet=True, plan=gh.plan)
    problems += [n for n in sync(dry) if n not in problems]
    print("  none" if not problems else "\n".join(f"  - {p}" for p in problems))


# ------------------------------------------------------------- Actions entry

def handle_event(gh: Gh) -> None:
    """Driven by .github/workflows/stage-labels.yml. One place for the transition rules."""
    name = os.environ.get("GITHUB_EVENT_NAME", "")
    with open(os.environ["GITHUB_EVENT_PATH"]) as f:
        ev = json.load(f)
    action = ev.get("action")

    if name == "issues" and action == "opened":
        set_issue_stage(gh, ev["issue"]["number"], "to-plan", if_unstaged=True)

    elif name == "issue_comment" and action == "created":
        issue = ev["issue"]
        if "pull_request" in issue:
            return
        owner = ev["repository"]["owner"]["login"]
        body = re.sub(r"\s+", "", ev["comment"]["body"] or "").lower()
        if ev["comment"]["user"]["login"] == owner and body == "ready":
            set_issue_stage(gh, issue["number"], "to-start")

    elif name == "pull_request":
        pr = ev["pull_request"]
        n = pr["number"]
        refs = referenced_issues(pr)
        if action == "closed":
            merged = bool(pr.get("merged"))
            for issue_n in refs:
                issue = gh.api(f"issues/{issue_n}")
                if issue["state"] != "open":
                    continue  # closed by the merge, nothing to do
                # Merged with `Addresses #n` (outside reporter): the issue stays open and
                # is no longer being implemented. Unmerged: the plan is still there.
                set_issue_stage(gh, issue_n, "none" if merged else "to-start")
            return
        if action in ("opened", "reopened", "ready_for_review") and not pr.get("draft"):
            set_pr_stage(gh, n, "to-review")
        elif action in ("opened", "reopened", "synchronize", "converted_to_draft"):
            set_pr_stage(gh, n, "working")
        if touches_mac(gh, n):
            set_flag(gh, n, "mac", True)
        for issue_n in refs:
            set_issue_stage(gh, issue_n, "implementing")


# ------------------------------------------------------------------------ main

def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", help="owner/name (default: $GH_REPO, $GITHUB_REPOSITORY, or the checkout)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("labels")
    s = sub.add_parser("issue"); s.add_argument("number", type=int); s.add_argument("stage", choices=ISSUE_STAGES + ("none",)); s.add_argument("--if-unstaged", action="store_true")
    s = sub.add_parser("pr"); s.add_argument("number", type=int); s.add_argument("stage", choices=PR_STAGES)
    s = sub.add_parser("flag"); s.add_argument("number", type=int); s.add_argument("flag", choices=FLAGS)
    s = sub.add_parser("unflag"); s.add_argument("number", type=int); s.add_argument("flag", choices=FLAGS)
    s = sub.add_parser("pr-issues"); s.add_argument("number", type=int)
    sub.add_parser("event")
    s = sub.add_parser("sync"); s.add_argument("--dry-run", action="store_true")
    sub.add_parser("status")
    p.add_argument("--plan", action="store_true",
                   help="decide but change nothing: print the labels to apply and exit 3")
    a = p.parse_args(argv)

    plan = a.plan or os.environ.get("STAGE_LABELS_PLAN") == "1" or not gh_can_write()
    if plan and a.cmd == "event":
        sys.exit("event runs inside GitHub Actions, where gh is authenticated")
    gh = Gh(repo_from_env(a.repo, use_gh=not plan), dry_run=getattr(a, "dry_run", False), plan=plan)
    try:
        run(gh, a)
    except PlanReadError as exc:
        print(f"STAGE_LABELS_MANUAL: gh cannot write here and the anonymous read failed ({exc}).\n"
              f"Nothing was changed. Make this change by hand, one stage per family "
              f"(designs/stage-labels.md): {' '.join(sys.argv[1:] if argv is None else argv)}")
        sys.exit(EXIT_PLAN)
    if gh.plan and a.cmd not in READ_ONLY_CMDS:
        report_plan(gh)


def report_plan(gh: Gh) -> None:
    """Print what plan mode decided; exit 3 if anything is left to apply."""
    if not gh.pending:
        print("STAGE_LABELS_PLAN: labels already correct, nothing to apply")
        return
    print("STAGE_LABELS_MANUAL: gh cannot write here; nothing was changed.")
    print("Apply with the GitHub connector (set each item's labels to the full list):")
    for item in gh.pending:
        if "number" in item:
            print(f"  #{item['number']}: labels {item['labels']}  (add {item['add']}, remove {item['remove']})")
        else:
            print(f"  {item['action']} label {item['label']} (color {item['color']}): {item['description']}")
    print("STAGE_LABELS_PLAN_JSON: " + json.dumps(gh.pending))
    sys.exit(EXIT_PLAN)


def run(gh: Gh, a: argparse.Namespace) -> None:
    if a.cmd == "labels":
        ensure_labels(gh)
    elif a.cmd == "issue":
        set_issue_stage(gh, a.number, a.stage, a.if_unstaged)
    elif a.cmd == "pr":
        set_pr_stage(gh, a.number, a.stage)
    elif a.cmd == "flag":
        set_flag(gh, a.number, a.flag, True)
    elif a.cmd == "unflag":
        set_flag(gh, a.number, a.flag, False)
    elif a.cmd == "pr-issues":
        print(" ".join(str(n) for n in referenced_issues(gh.api(f"pulls/{a.number}"))))
    elif a.cmd == "event":
        handle_event(gh)
    elif a.cmd == "sync":
        ensure_labels(gh)
        notes = sync(gh)
        print("in sync" if not notes else "\n".join(notes))
    elif a.cmd == "status":
        status(gh)


if __name__ == "__main__":
    main()
