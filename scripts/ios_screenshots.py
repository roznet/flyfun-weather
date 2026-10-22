"""Capture the App Store screenshots from the showcase fixture.

Runs ScreenshotUITests (the mock app on `Showcase/showcase.json`, see
scripts/export_showcase_fixture_ios.py) on each device and appearance through
ios_test.py, with the status bar pinned to Apple's marketing state (9:41, full
battery and signal), and exports the captures as PNGs named by screen:

    build/appstore-screenshots/<device>/<light|dark>/NN-screen.png

The defaults are the sizes App Store Connect requires: iPhone 17 (6.3",
1206x2622), iPhone 17 Pro Max (6.9", 1320x2868 — without it the store wants a
6.5" set instead) and iPad Pro 13" (2064x2752). Anything else is scaled down
from those by the store.

A successful run writes `manifest.json` next to the captures with the commit it
captured from. `--if-changed` skips the run when nothing that can show up in a
screenshot (CAPTURE_INPUTS) changed since then, so a release that didn't touch
the screens keeps its screenshots; `scripts/asc.py screenshots` then finds the
store already matches and uploads nothing.

Usage:
    python3 scripts/ios_screenshots.py
    python3 scripts/ios_screenshots.py --if-changed
    python3 scripts/ios_screenshots.py --device "iPhone 17 Pro Max" --appearance light --appearance dark
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ios_test  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TEST = "flyfun-weatherUITests/ScreenshotUITests/testCaptureStoreScreenshots"
DEFAULT_DEVICES = ["iPhone 17", "iPhone 17 Pro Max", "iPad Pro 13-inch (M5)"]
BUNDLE_ID = "net.ro-z.flyfun-weather"
# What ScreenshotUITests captures: a run missing any of them fails.
EXPECTED = ["01-briefing", "02-advisories", "03-cross-section", "04-why-red",
            "05-skewt", "06-map", "07-discussion", "08-flights"]
# The iPad's summary already shows the advisory list, so 02 would repeat 01.
IPAD_SKIPS = {"02-advisories"}
# What can change a screenshot: the drawing code, its assets, the fixture and the
# journey. Models/Services/AppIntents are left out on purpose — almost every
# release touches them, and a decoding change that alters a screen also fails
# FixtureShowcaseTests. Force a capture by running without --if-changed.
APP = "app/flyfun-weather"
CAPTURE_INPUTS = [
    f"{APP}/flyfun-weather/Views",
    f"{APP}/flyfun-weather/ViewModels",
    f"{APP}/flyfun-weather/Assets.xcassets",
    f"{APP}/flyfun-weather/Resources",
    f"{APP}/flyfun-weather/Services/FixtureShowcaseData.swift",
    f"{APP}/flyfun-weather/Services/FixtureBriefingRepository.swift",
    f"{APP}/flyfun-weatherUITests/ScreenshotUITests.swift",
    f"{APP}/flyfun-weatherUITests/Showcase",
    "scripts/ios_screenshots.py",
]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True,
                          text=True).stdout.strip()


def changed_since_capture(out: Path, devices: list[str], appearances: list[str]) -> str | None:
    """Why a capture is needed, or None when the one in `out` is still current."""
    try:
        manifest = json.loads((out / "manifest.json").read_text())
    except (OSError, ValueError):
        return "no previous capture manifest"
    captured = manifest.get("captures", {})
    for device in devices:
        for appearance in appearances:
            if appearance not in captured.get(ios_test.slug(device), []):
                return f"{device} / {appearance} was not in the last capture"
    if manifest.get("dirty"):
        return "the last capture ran on uncommitted screen changes"
    try:
        diff = git("diff", "--name-only", manifest["commit"], "HEAD", "--", *CAPTURE_INPUTS)
    except (KeyError, subprocess.CalledProcessError):
        return "the last capture's commit is unknown here"
    dirty = git("status", "--porcelain", "--", *CAPTURE_INPUTS)
    if diff or dirty:
        files = (diff + "\n" + dirty).split()
        return f"{len(files)} screen input(s) changed since {manifest['commit'][:8]}, e.g. {files[-1]}"
    return None


def write_manifest(out: Path, devices: list[str], appearances: list[str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps({
        "commit": git("rev-parse", "HEAD"),
        "dirty": bool(git("status", "--porcelain", "--", *CAPTURE_INPUTS)),
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "captures": {ios_test.slug(d): appearances for d in devices},
    }, indent=2) + "\n")


def simctl(*args: str) -> None:
    subprocess.run(["xcrun", "simctl", *args], check=True, capture_output=True)


def prepare(udid: str, appearance: str) -> None:
    """Boot, pin the status bar and set the appearance (both persist until reboot).

    Also uninstalls the app, which xcodebuild then reinstalls: the screenshots
    show a fresh install's defaults (cross-section layers, emulation, theme),
    not whatever the UI journeys last left in the simulator's UserDefaults.
    """
    subprocess.run(["xcrun", "simctl", "boot", udid], capture_output=True)  # already booted is fine
    simctl("bootstatus", udid)
    subprocess.run(["xcrun", "simctl", "uninstall", udid, BUNDLE_ID], capture_output=True)
    simctl("status_bar", udid, "override", "--time", "9:41", "--dataNetwork", "wifi",
           "--wifiMode", "active", "--wifiBars", "3", "--cellularMode", "active",
           "--cellularBars", "4", "--batteryState", "charged", "--batteryLevel", "100")
    simctl("ui", udid, "appearance", appearance)


def export(bundle: Path, dest: Path) -> list[Path]:
    """The named captures (`NN-screen`) from a result bundle, as dest/NN-screen.png."""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["xcrun", "xcresulttool", "export", "attachments",
                        "--path", str(bundle), "--output-path", tmp], check=True, capture_output=True)
        manifest = json.loads((Path(tmp) / "manifest.json").read_text())
        dest.mkdir(parents=True, exist_ok=True)
        written = []
        for test in manifest:
            for a in test["attachments"]:
                name = a["suggestedHumanReadableName"].split("_")[0]
                if name[:2].isdigit() and a["exportedFileName"].endswith(".png"):
                    out = dest / f"{name}.png"
                    shutil.copy(Path(tmp) / a["exportedFileName"], out)
                    written.append(out)
        return sorted(written)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", action="append", help=f"simulator name (repeatable; default {DEFAULT_DEVICES})")
    ap.add_argument("--appearance", action="append", choices=["light", "dark"],
                    help="repeatable; default light")
    ap.add_argument("--out", type=Path, default=ROOT / "build/appstore-screenshots")
    ap.add_argument("--wait", action="store_true",
                    help="queue behind another running `xcodebuild test` instead of exiting 3")
    ap.add_argument("--if-changed", action="store_true",
                    help="skip (exit 0) when no screen input changed since the last capture in --out")
    args = ap.parse_args()
    devices = args.device or DEFAULT_DEVICES
    appearances = args.appearance or ["light"]

    if args.if_changed:
        reason = changed_since_capture(args.out, devices, appearances)
        if reason is None:
            print(f"screenshots in {args.out} are current — nothing on screen changed; skipping")
            return 0
        print(f"capturing: {reason}")

    while (other := ios_test.other_xcodebuild_test()):
        if not args.wait:
            print(f"another `xcodebuild test` is running — one at a time:\n{other}", file=sys.stderr)
            return 3
        time.sleep(15)
    os.environ["TEST_RUNNER_FLYFUN_SCREENSHOTS"] = "1"
    run_args = argparse.Namespace(only=[TEST], workers=1, stall_minutes=5, test_allowance=600)

    failed = False
    for device in devices:
        udid = ios_test.resolve_device(device)
        for appearance in appearances:
            prepare(udid, appearance)
            bundle = Path(tempfile.mkdtemp(prefix="ios-screenshots-")) / "result.xcresult"
            try:
                code = ios_test.run_device(device, udid, run_args, bundle)
            finally:
                simctl("status_bar", udid, "clear")
            dest = args.out / ios_test.slug(device) / appearance
            shots = export(bundle, dest) if bundle.exists() else []
            expected = set(EXPECTED) - (IPAD_SKIPS if device.startswith("iPad") else set())
            missing = sorted(expected - {s.stem for s in shots})
            print(f"{device} / {appearance}: {len(shots)} screenshots → {dest}"
                  + (f"  MISSING {missing}" if missing else ""))
            failed |= code != 0 or bool(missing)
    if failed:
        return 1
    write_manifest(args.out, devices, appearances)
    return 0


if __name__ == "__main__":
    sys.exit(main())
