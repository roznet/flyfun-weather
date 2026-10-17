"""Capture the App Store screenshots from the showcase fixture.

Runs ScreenshotUITests (the mock app on `Showcase/showcase.json`, see
scripts/export_showcase_fixture_ios.py) on each device and appearance through
ios_test.py, with the status bar pinned to Apple's marketing state (9:41, full
battery and signal), and exports the captures as PNGs named by screen:

    build/appstore-screenshots/<device>/<light|dark>/NN-screen.png

The defaults are the two sizes App Store Connect requires: iPhone 17
(6.3", 1206x2622) and iPad Pro 13" (2064x2752). Anything else is scaled down
from those by the store.

Usage:
    python3 scripts/ios_screenshots.py
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
DEFAULT_DEVICES = ["iPhone 17", "iPad Pro 13-inch (M5)"]


def simctl(*args: str) -> None:
    subprocess.run(["xcrun", "simctl", *args], check=True, capture_output=True)


def prepare(udid: str, appearance: str) -> None:
    """Boot, pin the status bar and set the appearance (both persist until reboot)."""
    subprocess.run(["xcrun", "simctl", "boot", udid], capture_output=True)  # already booted is fine
    simctl("bootstatus", udid)
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
    args = ap.parse_args()

    while (other := ios_test.other_xcodebuild_test()):
        if not args.wait:
            print(f"another `xcodebuild test` is running — one at a time:\n{other}", file=sys.stderr)
            return 3
        time.sleep(15)
    os.environ["TEST_RUNNER_FLYFUN_SCREENSHOTS"] = "1"
    run_args = argparse.Namespace(only=[TEST], workers=1, stall_minutes=5, test_allowance=600)

    failed = False
    for device in args.device or DEFAULT_DEVICES:
        udid = ios_test.resolve_device(device)
        for appearance in args.appearance or ["light"]:
            prepare(udid, appearance)
            bundle = Path(tempfile.mkdtemp(prefix="ios-screenshots-")) / "result.xcresult"
            code = ios_test.run_device(device, udid, run_args, bundle)
            shots = export(bundle, args.out / ios_test.slug(device) / appearance) if bundle.exists() else []
            print(f"{device} / {appearance}: {len(shots)} screenshots → {args.out / ios_test.slug(device) / appearance}")
            failed |= code != 0 or not shots
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
