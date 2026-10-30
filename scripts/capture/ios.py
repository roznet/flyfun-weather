"""Capture any flight's screens from the real iOS app, against the devserver.

Runs `FlightCaptureUITests` (the live app, `-serverEnvironment dev`, signed in
as the dev user with a short-lived token) on each simulator and appearance, with
the status bar pinned to 9:41, and exports

    build/captures/<flight>/ios/<device>/<light|dark>/NN-<screen>.png

Needs `/devserver --https` up and the flight in the dev DB
(`scripts/ops/import_flight.py <id>`); see `common.py` and
`designs/flight-captures.md`. One `xcodebuild test` at a time: `--wait`
queues behind another one instead of exiting 3.

Usage:
    python3 scripts/capture/ios.py --flight egtf_lfat-2026-10-30-6c36
    python3 scripts/capture/ios.py --flight ID --screens cross-section,skewt --device "iPad Pro 13-inch (M5)"
    python3 scripts/capture/ios.py --flight ID --appearance light --appearance dark
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
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common  # noqa: E402
import ios_screenshots  # noqa: E402
import ios_test  # noqa: E402

TEST = "flyfun-weatherUITests/FlightCaptureUITests/testCaptureFlight"
DEFAULT_DEVICES = ["iPhone 17 Pro Max"]


def export(bundle: Path, dest: Path, screens: list[str]) -> list[Path]:
    """The `capture-<screen>` attachments of a result bundle, as dest/NN-<screen>.png."""
    common.clear_pngs(dest, screens)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["xcrun", "xcresulttool", "export", "attachments",
                        "--path", str(bundle), "--output-path", tmp], check=True, capture_output=True)
        manifest = json.loads((Path(tmp) / "manifest.json").read_text())
        written = []
        for test in manifest:
            for a in test["attachments"]:
                name = a["suggestedHumanReadableName"].split("_")[0]
                screen = name.removeprefix("capture-")
                if name.startswith("capture-") and screen in common.SCREENS:
                    out = dest / f"{common.file_name(screen)}.png"
                    shutil.copy(Path(tmp) / a["exportedFileName"], out)
                    written.append(out)
        return sorted(written)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common.add_common_args(ap)
    ap.add_argument("--device", action="append", help=f"simulator name (repeatable; default {DEFAULT_DEVICES})")
    ap.add_argument("--wait", action="store_true",
                    help="queue behind another running `xcodebuild test` instead of exiting 3")
    args = ap.parse_args()
    devices = args.device or DEFAULT_DEVICES
    appearances = args.appearance or ["light"]
    out = common.out_dir(args, "ios")

    while (other := ios_test.other_xcodebuild_test()):
        if not args.wait:
            print(f"another `xcodebuild test` is running — one at a time:\n{other}", file=sys.stderr)
            return 3
        time.sleep(15)

    # Fresh per device: a full run of several devices can outlast one token.
    sets: dict[str, dict[str, list[Path]]] = {}
    failed = False
    for device in devices:
        udid = ios_test.resolve_device(device)
        for appearance in appearances:
            os.environ.update({
                "TEST_RUNNER_FLYFUN_CAPTURE": "1",
                "TEST_RUNNER_FLYFUN_CAPTURE_FLIGHT": args.flight,
                "TEST_RUNNER_FLYFUN_CAPTURE_TOKEN": common.preflight(args.flight),
                "TEST_RUNNER_FLYFUN_CAPTURE_SCREENS": ",".join(args.screens),
            })
            ios_screenshots.prepare(udid, appearance)
            bundle = Path(tempfile.mkdtemp(prefix="flight-capture-")) / "result.xcresult"
            run_args = argparse.Namespace(only=[TEST], workers=1, stall_minutes=5, test_allowance=900)
            try:
                code = ios_test.run_device(device, udid, run_args, bundle)
            finally:
                subprocess.run(["xcrun", "simctl", "status_bar", udid, "clear"], capture_output=True)
            dest = out / ios_test.slug(device) / appearance
            shots = export(bundle, dest, args.screens) if bundle.exists() else []
            sets.setdefault(ios_test.slug(device), {})[appearance] = shots
            # A failed step is reported as a missing screen below; a run that
            # produced nothing at all is a build or launch failure.
            failed |= code != 0 and not shots
    os.environ.pop("TEST_RUNNER_FLYFUN_CAPTURE_TOKEN", None)
    missing = common.write_manifest(out, "ios", args.flight, sets, args.screens)
    return common.report(out, sets, missing) or int(failed)


if __name__ == "__main__":
    sys.exit(main())
