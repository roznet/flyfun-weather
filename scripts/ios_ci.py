"""CI glue for the iOS tests: pick a simulator, summarize a run, gate a release.

    pick-device NAME [NAME ...]
        Print an xcodebuild destination for the first available simulator
        matching the names in order (exact name first, then prefix), on the
        newest runtime that has it. The nightly's iPad leg uses this so a
        runner-image change that drops one model degrades to the next instead
        of failing as "unavailable destination".

    summarize BUNDLE [--label TEXT]
        Markdown for the run page: totals, tests that only passed on a retry
        ("flaky"), and tests whose last attempt failed, with their messages.
        A retried test is a `Test Case` marked Failed with `Repetition`
        children (First Run Failed, Retry 1 Passed) — xcresult's own counts
        call it failed even though xcodebuild exits 0, which is why the old
        summary read "1 failed" on a green night. Exits 1 if no test ran (an
        -only-testing filter that matches nothing is a silent vacuous pass).

    gate [--repo OWNER/NAME]
        For /archive: can the local UI passes be skipped? Finds the newest
        nightly on main whose iPhone *and* iPad UI jobs both ran and passed,
        then checks that app/flyfun-weather has not changed since that commit
        apart from the version-bump lines. Exit 0 = covered (prints the run),
        1 = not covered (prints why). Needs `gh` (unsandboxed).

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = "app/flyfun-weather"
WORKFLOW = "ios-ui-nightly.yml"
# Job names the nightly's matrix produces — `gate` requires both to have run.
LEGS = ("UI (iPhone)", "UI (iPad)")
# Lines a version bump touches; a diff made only of these is not a code change.
VERSION_LINE = re.compile(r"^[+-]\s*(MARKETING_VERSION|CURRENT_PROJECT_VERSION) = [^;]+;\s*$")


def run(*cmd: str) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True, cwd=ROOT).stdout


# --- pick-device -------------------------------------------------------------

def pick_device(names: list[str]) -> str:
    devices = json.loads(run("xcrun", "simctl", "list", "devices", "available", "-j"))["devices"]

    def runtime_version(key: str) -> tuple[int, ...]:
        m = re.search(r"iOS-([\d-]+)$", key)
        return tuple(int(x) for x in m.group(1).split("-")) if m else ()

    ios = sorted(
        ((runtime_version(k), d) for k, ds in devices.items() if ".iOS-" in k for d in ds),
        key=lambda pair: pair[0], reverse=True,
    )
    for name in names:
        for match in (lambda n: n == name, lambda n: n.startswith(name)):
            for _version, d in ios:
                if match(d["name"]):
                    print(f"picked {d['name']} (iOS {'.'.join(map(str, _version))})", file=sys.stderr)
                    return f"platform=iOS Simulator,id={d['udid']}"
    sys.exit(f"no available simulator matches any of {names}")


# --- summarize ---------------------------------------------------------------

def xcresult(bundle: str, what: str) -> dict:
    return json.loads(run("xcrun", "xcresulttool", "get", "test-results", what, "--path", bundle))


def classify(tests: dict) -> tuple[list[str], list[tuple[str, str]]]:
    """(flaky test names, [(failed test name, message)])."""
    flaky: list[str] = []
    failed: list[tuple[str, str]] = []

    def messages(node: dict) -> list[str]:
        own = [node["name"]] if node.get("nodeType") == "Failure Message" else []
        return own + [m for c in node.get("children", []) for m in messages(c)]

    def judge(unit: dict) -> tuple[bool, bool]:
        """(last attempt failed, some attempt failed) for one test or one
        configuration of it. Its own `result` says Failed after a passing
        retry too, so the last Repetition decides when there is one."""
        reps = [c for c in unit.get("children", []) if c.get("nodeType") == "Repetition"]
        if reps:
            return reps[-1].get("result") == "Failed", any(r.get("result") == "Failed" for r in reps)
        bad = unit.get("result") == "Failed"
        return bad, bad

    def walk(node: dict, suite: str) -> None:
        if node.get("nodeType") == "Test Case":
            name = f"{suite}/{node['name']}"
            # Per-configuration tests (Light/Dark, orientations) hang their
            # repetitions under one `Arguments` node per configuration.
            units = [c for c in node.get("children", []) if c.get("nodeType") == "Arguments"] or [node]
            verdicts = [judge(u) for u in units]
            if any(last for last, _ in verdicts):
                msgs = [m for u, (last, _) in zip(units, verdicts) if last for m in messages(u)]
                failed.append((name, "; ".join(dict.fromkeys(msgs)) or "(no message)"))
            elif any(some for _, some in verdicts):
                flaky.append(name)
            return
        if node.get("nodeType") == "Test Suite":
            suite = node["name"]
        for c in node.get("children", []):
            walk(c, suite)

    for n in tests.get("testNodes", []):
        walk(n, "")
    return flaky, failed


def summarize(bundle: str, label: str) -> int:
    s = xcresult(bundle, "summary")
    flaky, failed = classify(xcresult(bundle, "tests"))
    total, skipped = s["totalTestCount"], s["skippedTests"]
    passed = total - skipped - len(failed)
    print(f"### {label}: {passed} passed, {len(failed)} failed, {skipped} skipped (of {total})")
    if flaky:
        print(f"\n**Passed only on retry ({len(flaky)})** — infrastructure noise once; "
              "a journey here two nights running is a bug:")
        print("\n".join(f"- `{t}`" for t in flaky))
    if failed:
        print(f"\n**Failed ({len(failed)}):**")
        print("\n".join(f"- `{t}` — {m}" for t, m in failed))
    if total == 0:
        print("\n**0 tests ran** — the -only-testing filter matched nothing.")
        return 1
    return 0


# --- gate ----------------------------------------------------------------------

def code_changes_since(sha: str) -> list[str]:
    """Changed lines under app/ since `sha`, ignoring version-bump lines."""
    diff = run("git", "diff", "-U0", sha, "HEAD", "--", APP_DIR)
    return [
        line for line in diff.splitlines()
        if line[:1] in "+-" and not line.startswith(("+++", "---")) and not VERSION_LINE.match(line)
    ]


def gate(repo: str | None) -> int:
    repo_args = ["--repo", repo] if repo else []
    runs = json.loads(run(
        "gh", "run", "list", *repo_args, "--workflow", WORKFLOW, "--branch", "main",
        "--status", "success", "--limit", "15", "--json", "databaseId,headSha,createdAt,url",
    ))
    for r in runs:
        jobs = json.loads(run("gh", "run", "view", str(r["databaseId"]), *repo_args, "--json", "jobs"))["jobs"]
        ran = {j["name"]: j["conclusion"] for j in jobs}
        # A run whose `changed` guard skipped the simulator is "success" too —
        # only a run where both legs executed and passed is evidence.
        if all(ran.get(leg) == "success" for leg in LEGS):
            break
    else:
        print(f"NOT COVERED: no nightly on main in the last {len(runs)} green runs ran both {' and '.join(LEGS)}.")
        return 1

    sha = r["headSha"]
    try:
        changes = code_changes_since(sha)
    except subprocess.CalledProcessError:
        print(f"NOT COVERED: nightly commit {sha[:8]} is not in this checkout (git fetch?).")
        return 1
    print(f"nightly {sha[:8]} ({r['createdAt']}) — both legs green: {r['url']}")
    if changes:
        files = run("git", "diff", "--name-only", sha, "HEAD", "--", APP_DIR).split()
        print(f"NOT COVERED: {len(changes)} changed line(s) under {APP_DIR} since then:")
        print("\n".join(f"  {f}" for f in files))
        return 1
    print(f"COVERED: no change under {APP_DIR} since that run except version-bump lines.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pick-device")
    p.add_argument("names", nargs="+")
    s = sub.add_parser("summarize")
    s.add_argument("bundle")
    s.add_argument("--label", default="UI journeys")
    g = sub.add_parser("gate")
    g.add_argument("--repo")
    args = ap.parse_args()

    if args.cmd == "pick-device":
        print(pick_device(args.names))
        return 0
    if args.cmd == "summarize":
        return summarize(args.bundle, args.label)
    return gate(args.repo)


if __name__ == "__main__":
    sys.exit(main())
