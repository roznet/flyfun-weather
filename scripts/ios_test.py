"""Run the iOS test suite on one simulator, without the silent hangs.

A bare ``xcodebuild test -quiet | tail`` has three ways to waste a quarter of
an hour without saying so:

* On any failure xcodebuild collects a sysdiagnose-style dump of the simulator
  (``-collect-test-diagnostics on-failure``, the default). On this machine that
  step stalls until a 600 s internal timeout, so every red run sat 10 minutes
  idle *after* the last test finished. Passed as ``never`` here.
* A test that wedges (a sheet that never appears, an app that never launches)
  has no ceiling. ``-maximum-test-execution-time-allowance`` gives it one.
* Piping through ``tail``/``grep`` shows nothing until exit, so "slow" and
  "hung" look identical. This prints each test as it finishes and runs a
  watchdog: if neither xcodebuild's output nor the result bundle (written to
  several times a minute while tests run) changes for ``--stall-minutes``,
  xcodebuild is killed and the run reported as STALLED.

The summary at the end comes from the result bundle, with every failure's
assertion message, so a red run is diagnosable without opening Xcode.

Usage:
    python3 scripts/ios_test.py --device "iPhone 17 Pro"
    python3 scripts/ios_test.py --device "iPad Pro 11-inch (M5)" \\
        --only flyfun-weatherUITests/flyfun_weatherUITests/testRouteSigmetsSectionRenders

Exit codes: 0 all passed, 1 test failures, 2 build/setup failure, 3 another
``xcodebuild test`` is already running, 124 stalled (killed by the watchdog).
Stdlib only — no venv needed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECT = ROOT / "app/flyfun-weather/flyfun-weather.xcodeproj"
SCHEME = "flyfun-weather"

# xcodebuild lines worth showing live; everything else still goes to the log.
# Its own per-test lines are left out: under parallel testing it prints them
# all at the very end, so they come from the runner logs instead (RUNNER).
SHOW = re.compile(r"error: |\*\* (TEST|BUILD) [A-Z ]+\*\*|Testing failed")
# The test runner's stdout, which the result bundle stages as the run goes.
# XCTest (the UI journeys, minutes each): every start and finish. Swift
# Testing (~800 unit tests in under a second): failures and the run total only.
RUNNER = re.compile(r"Test Case '.*' (started|passed|failed|skipped)|^\W*Test run with|^\W*✘|: error: ")


def resolve_device(name: str) -> str:
    """Map a simulator name to one UDID — names are duplicated on this machine."""
    if re.fullmatch(r"[0-9A-F-]{36}", name):
        return name
    out = subprocess.run(
        ["xcrun", "simctl", "list", "devices", "available", "-j"],
        capture_output=True, text=True, check=True,
    ).stdout
    matches = [
        d for devs in json.loads(out)["devices"].values() for d in devs if d["name"] == name
    ]
    if not matches:
        sys.exit(f"no available simulator named {name!r} (xcrun simctl list devices available)")
    # Prefer one already booted: it saves a boot.
    matches.sort(key=lambda d: d["state"] != "Booted")
    return matches[0]["udid"]


def other_xcodebuild_test() -> str | None:
    # Anchored: a shell whose command line merely *mentions* xcodebuild is not one.
    out = subprocess.run(["pgrep", "-fl", "^[^ ]*xcodebuild test"], capture_output=True, text=True).stdout
    return out.strip() or None


def newest_mtime(path: Path) -> float:
    newest = 0.0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                newest = max(newest, os.stat(os.path.join(root, f)).st_mtime)
            except OSError:
                pass
    return newest


def tail_runner_logs(bundle: Path, offsets: dict[Path, int]) -> list[str]:
    """New test-progress lines from the runner logs staged inside the bundle."""
    lines: list[str] = []
    for f in sorted((bundle / "Staging").rglob("StandardOutputAndStandardError*.txt")):
        try:
            with open(f, "rb") as fh:
                fh.seek(offsets.get(f, 0))
                chunk = fh.read()
        except OSError:
            continue
        # Hold back a trailing partial line until it is complete.
        cut = chunk.rfind(b"\n") + 1
        offsets[f] = offsets.get(f, 0) + cut
        text = chunk[:cut].decode(errors="replace")
        lines += [l for l in text.splitlines() if RUNNER.search(l)]
    return lines


def summarize(bundle: Path) -> tuple[dict | None, list[tuple[str, str]]]:
    def get(*args: str) -> dict | None:
        r = subprocess.run(
            ["xcrun", "xcresulttool", "get", "test-results", *args, "--path", str(bundle)],
            capture_output=True, text=True,
        )
        return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None

    summary = get("summary")
    failures: list[tuple[str, str]] = []
    tests = get("tests")

    def walk(node: dict) -> None:
        if node.get("nodeType") == "Test Case" and node.get("result") == "Failed":
            msgs = [c["name"] for c in node.get("children", []) if c.get("nodeType") == "Failure Message"]
            failures.append((node["name"], "; ".join(msgs) or "(no message)"))
        for c in node.get("children", []):
            walk(c)

    for n in (tests or {}).get("testNodes", []):
        walk(n)
    return summary, failures


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", required=True, help="simulator name or UDID")
    ap.add_argument("--only", action="append", default=[], help="-only-testing identifier (repeatable)")
    ap.add_argument("--stall-minutes", type=float, default=5,
                    help="kill xcodebuild after this long with no output and no result-bundle writes (default 5)")
    ap.add_argument("--test-allowance", type=int, default=900,
                    help="per-test ceiling in seconds (default 900; the live scenario took 515 on iPad)")
    ap.add_argument("--result-bundle", type=Path, help="default: a fresh temp dir")
    ap.add_argument("--wait", action="store_true",
                    help="queue behind another running `xcodebuild test` instead of exiting 3")
    args = ap.parse_args()

    while (other := other_xcodebuild_test()):
        if not args.wait:
            print(f"another `xcodebuild test` is running — one at a time:\n{other}", file=sys.stderr)
            return 3
        time.sleep(15)

    udid = resolve_device(args.device)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    workdir = Path(tempfile.mkdtemp(prefix=f"ios-test-{stamp}-"))
    bundle = args.result_bundle or workdir / "result.xcresult"
    log_path = workdir / "xcodebuild.log"

    cmd = [
        "xcodebuild", "test",
        "-project", str(PROJECT), "-scheme", SCHEME,
        "-destination", f"platform=iOS Simulator,id={udid}",
        "-resultBundlePath", str(bundle),
        "-collect-test-diagnostics", "never",
        "-test-timeouts-enabled", "YES",
        "-maximum-test-execution-time-allowance", str(args.test_allowance),
        *[f"-only-testing:{t}" for t in args.only],
    ]
    print(f"device {args.device} ({udid})\nlog    {log_path}\nbundle {bundle}", flush=True)

    start = time.time()
    last_output = start
    proc = subprocess.Popen(
        cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        start_new_session=True,  # so the watchdog can kill xcodebuild's children too
    )

    def pump() -> None:
        nonlocal last_output
        with open(log_path, "w") as log:
            for line in proc.stdout:  # type: ignore[union-attr]
                last_output = time.time()
                log.write(line)
                log.flush()
                if SHOW.search(line):
                    print(f"[{int(last_output - start):>4}s] {line.rstrip()}", flush=True)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()

    stalled = False
    offsets: dict[Path, int] = {}
    while proc.poll() is None:
        time.sleep(10)
        for line in tail_runner_logs(bundle, offsets):
            print(f"[{int(time.time() - start):>4}s] {line.strip()}", flush=True)
        activity = max(last_output, newest_mtime(bundle) if bundle.exists() else 0)
        if time.time() - activity > args.stall_minutes * 60:
            stalled = True
            print(f"STALLED: no output and no result-bundle writes for {args.stall_minutes:g} min "
                  f"— killing xcodebuild", flush=True)
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
    reader.join(timeout=5)
    wall = time.time() - start

    summary, failures = summarize(bundle) if bundle.exists() else (None, [])
    print(f"\n=== {args.device}: xcodebuild exit {proc.returncode}, {wall / 60:.1f} min")
    if summary:
        print(f"passed {summary['passedTests']}  failed {summary['failedTests']}  "
              f"skipped {summary['skippedTests']}  (of {summary['totalTestCount']})")
        if summary["totalTestCount"] == 0:
            print("WARNING: 0 tests ran — an -only-testing filter matched nothing")
    for name, msg in failures:
        print(f"FAILED {name}: {msg}")

    if stalled:
        return 124
    if proc.returncode == 0:
        return 0
    if summary is None or summary["totalTestCount"] == 0:
        print(f"no test results — build or setup failed; see {log_path}")
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
