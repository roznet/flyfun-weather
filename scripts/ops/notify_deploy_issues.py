#!/usr/bin/env python3
"""After a deploy: tell the issues behind the deployed PRs that they are live.

    notify_deploy_issues.py SERVER_SHA LOCAL_SHA [--dry-run]

Walks the commits in SERVER_SHA..LOCAL_SHA, collects the PRs they belong to, and
reads each PR body for an explicit keyword reference (`Closes #N`, `Addresses #N`,
`Refs #N`, ...). Per referenced issue:

    OPEN    comment "Deployed to ..." and close it  (a deferred outside-reporter issue)
    CLOSED  comment "Deployed to ..." once          (the common case: closed at merge)

A bare `#N` never counts: it may be a passing mention. Why the whitelist, and why
not to loosen it: designs/references/deploy-notes.md §D6.

This replaces a shell loop in the deploy skill that word-split `$PRS` the bash way;
under zsh (the agent's shell) it passed every PR number as one argument and failed.

Needs `gh` logged in (locally: run with the sandbox disabled, else gh returns empty
with exit 0). Run it only after the health check passed — the fix must be live.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stage_labels import Gh, repo_from_env  # noqa: E402

DEPLOYED_MSG = "Deployed to https://weather.flyfun.aero — give it a try and let us know how it works."

# §D6 whitelist. The optional `issue` token catches "Fixes issue #N", which
# GitHub's own auto-close misses.
REF_RE = re.compile(
    r"(?i)\b(?:addresses|refs?|references|related to|closes?|closed|fix(?:es|ed)?|resolves?|resolved)"
    r"\s+(?:issue\s+)?#(\d+)"
)


def referenced_issues(body: str | None) -> list[int]:
    found: list[int] = []
    for m in REF_RE.finditer(body or ""):
        n = int(m.group(1))
        if n not in found:
            found.append(n)
    return found


def commits_in_range(server_sha: str, local_sha: str) -> list[str]:
    out = subprocess.run(["git", "log", "--format=%H", f"{server_sha}..{local_sha}"],
                         text=True, capture_output=True)
    if out.returncode != 0:
        sys.exit(f"git log {server_sha}..{local_sha} failed: {out.stderr.strip()}")
    return out.stdout.split()


def prs_for_commits(gh: Gh, shas: list[str]) -> list[int]:
    prs: set[int] = set()
    for sha in shas:
        prs.update(p["number"] for p in gh.api(f"commits/{sha}/pulls") or [])
    return sorted(prs)


def already_notified(gh: Gh, issue: int) -> bool:
    comments = gh.api(f"issues/{issue}/comments?per_page=100", paginate=True) or []
    return any("Deployed to https://weather.flyfun.aero" in (c.get("body") or "") for c in comments)


def notify(gh: Gh, prs: list[int]) -> list[str]:
    """Act on every referenced issue; return one report line per action."""
    report: list[str] = []
    seen: set[int] = set()
    verb = "would " if gh.dry_run else ""
    for pr in prs:
        body = (gh.api(f"pulls/{pr}") or {}).get("body")
        for issue in referenced_issues(body):
            if issue in seen:
                continue
            seen.add(issue)
            state = (gh.api(f"issues/{issue}") or {}).get("state")
            if state == "open":
                if not gh.dry_run:
                    gh.api(f"issues/{issue}/comments", "POST", {"body": DEPLOYED_MSG})
                    gh.api(f"issues/{issue}", "PATCH", {"state": "closed", "state_reason": "completed"})
                report.append(f"{verb}close #{issue} (from PR #{pr})")
            elif state == "closed":
                if already_notified(gh, issue):
                    report.append(f"already notified #{issue} (from PR #{pr})")
                    continue
                if not gh.dry_run:
                    gh.api(f"issues/{issue}/comments", "POST", {"body": DEPLOYED_MSG})
                report.append(f"{verb}notify #{issue} (already closed, from PR #{pr})")
    return report


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("server_sha", help="what ran before the deploy")
    p.add_argument("local_sha", help="what was just deployed")
    p.add_argument("--repo", help="owner/name (default: $GH_REPO, $GITHUB_REPOSITORY, or the checkout)")
    p.add_argument("--dry-run", action="store_true", help="report what would happen, write nothing")
    a = p.parse_args(argv)

    gh = Gh(repo_from_env(a.repo), dry_run=a.dry_run)
    prs = prs_for_commits(gh, commits_in_range(a.server_sha, a.local_sha))
    if not prs:
        print("no PRs in this deploy")
        return
    print(f"PRs: {' '.join(f'#{n}' for n in prs)}")
    report = notify(gh, prs)
    print("\n".join(f"  {line}" for line in report) if report else "no linked issues in this deploy")


if __name__ == "__main__":
    main()
