"""Capture any flight's screens from the real Android app, against the devserver.

Builds and installs the debug app and its test APK, then runs
`FlightCaptureJourney` (the live app: a debug build on an emulator talks to
`https://localhost.ro-z.me:8443`, signed in as the dev user with a short-lived
token) with the status bar in System UI demo mode (12:00 → 09:41, full
battery and signal), and pulls

    build/captures/<flight>/android/<avd>/<light|dark>/NN-<screen>.png

Uses the running emulator, else boots `--avd` headless (and shuts it down
after). Needs `/devserver --https` up and the flight in the dev DB
(`scripts/ops/import_flight.py <id>`); see `common.py` and
`designs/flight-captures.md`.

Usage:
    python3 scripts/capture/android.py --flight egtf_lfat-2026-10-30-6c36
    python3 scripts/capture/android.py --flight ID --screens map,cross-section --appearance dark
    python3 scripts/capture/android.py --flight ID --no-build      # APKs already installed
"""

from __future__ import annotations

import argparse
import functools
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

ANDROID = common.ROOT / "app/android"
PACKAGE = "aero.flyfun.weather"
RUNNER = f"{PACKAGE}.test/androidx.test.runner.AndroidJUnitRunner"
JOURNEY = f"{PACKAGE}.journeys.FlightCaptureJourney"
DEFAULT_AVD = "flyfun_pixel10_api37"


@functools.cache
def sdk() -> Path:
    """Resolved on first use, not at import: a missing SDK fails the run, not an import."""
    for candidate in (os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
                      str(Path.home() / "Library/Android/sdk")):
        if candidate and (Path(candidate) / "platform-tools/adb").exists():
            return Path(candidate)
    sys.exit("error: Android SDK not found (set ANDROID_HOME)")


def adb_path() -> str:
    return str(sdk() / "platform-tools/adb")


def adb(*args: str, check: bool = True, timeout: float = 120) -> str:
    r = subprocess.run([adb_path(), *args], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args[:3])}… failed: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def running_emulator() -> str | None:
    for line in adb("devices").splitlines()[1:]:
        serial, _, state = line.partition("\t")
        if serial.startswith("emulator-") and state == "device":
            return serial
    return None


def boot(avd: str) -> subprocess.Popen:
    print(f"booting {avd} headless…", flush=True)
    proc = subprocess.Popen([str(sdk() / "emulator/emulator"), "-avd", avd, "-no-window", "-no-audio",
                             "-no-snapshot-save"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    adb("wait-for-device", timeout=300)
    deadline = time.time() + 300
    while adb("shell", "getprop", "sys.boot_completed", check=False).strip() != "1":
        if time.time() > deadline:
            sys.exit("error: the emulator did not finish booting in 5 min")
        time.sleep(3)
    return proc


def build_and_install() -> None:
    """Debug app + test APK. A Play- or release-signed copy blocks the install: remove it once."""
    env = {**os.environ, "ANDROID_HOME": str(sdk())}
    cmd = ["./gradlew", "-q", ":app:installDebug", ":app:installDebugAndroidTest"]
    for attempt in range(2):
        r = subprocess.run(cmd, cwd=ANDROID, env=env, capture_output=True, text=True)
        if r.returncode == 0:
            return
        if attempt == 0 and "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in r.stdout + r.stderr:
            print(f"uninstalling a differently signed {PACKAGE}…", flush=True)
            adb("uninstall", PACKAGE, check=False)
            continue
        sys.exit(f"error: gradle install failed:\n{(r.stdout + r.stderr)[-3000:]}")


def installed() -> bool:
    packages = adb("shell", "pm", "list", "packages", PACKAGE, check=False).split()
    return f"package:{PACKAGE}" in packages and f"package:{PACKAGE}.test" in packages


def demo_status_bar(on: bool) -> None:
    """System UI demo mode: a fixed 09:41, full battery and signal, no notification icons."""
    def demo(command: str, *extras: str) -> None:
        adb("shell", "am", "broadcast", "-a", "com.android.systemui.demo", "-e", "command", command,
            *extras, check=False)
    if not on:
        demo("exit")
        return
    adb("shell", "settings", "put", "global", "sysui_demo_allowed", "1", check=False)
    demo("enter")
    demo("clock", "-e", "hhmm", "0941")
    demo("battery", "-e", "level", "100", "-e", "plugged", "false")
    demo("network", "-e", "wifi", "show", "-e", "level", "4")
    demo("network", "-e", "mobile", "show", "-e", "datatype", "none", "-e", "level", "4")
    demo("notifications", "-e", "visible", "false")


def run_journey(flight: str, token: str, screens: list[str]) -> tuple[int, str]:
    # The token rides in an instrumentation argument: visible to `ps` on the
    # host and device while the run lasts, which is why it is short-lived.
    r = subprocess.run([adb_path(), "shell", "am", "instrument", "-w", "-r", "-e", "class", JOURNEY,
                        "-e", "flyfunCaptureFlight", flight, "-e", "flyfunCaptureToken", token,
                        "-e", "flyfunCaptureScreens", ",".join(screens), RUNNER],
                       capture_output=True, text=True, timeout=900)
    out = r.stdout + r.stderr
    ok = "OK (1 test)" in out
    return (0 if ok else 1), out


def pull(dest: Path, screens: list[str]) -> list[Path]:
    """The journey's `files/captures/<screen>.png`, as dest/NN-<screen>.png (via run-as: a debug build)."""
    common.clear_pngs(dest, screens)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "captures.tar"
        with open(archive, "wb") as f:
            subprocess.run([adb_path(), "exec-out", "run-as", PACKAGE, "tar", "cf", "-", "-C", "files", "captures"],
                           stdout=f, check=True)
        try:
            with tarfile.open(archive) as tar:
                tar.extractall(tmp, filter="data")
        except tarfile.ReadError:
            print("no captures to pull: the journey wrote none (see the error above)", file=sys.stderr)
            return []
        written = []
        for png in sorted((Path(tmp) / "captures").glob("*.png")):
            if png.stem in common.SCREENS:
                out = dest / f"{common.file_name(png.stem)}.png"
                shutil.copy(png, out)
                written.append(out)
        return sorted(written)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common.add_common_args(ap)
    ap.add_argument("--avd", default=DEFAULT_AVD, help=f"AVD to boot when no emulator is running (default {DEFAULT_AVD})")
    ap.add_argument("--no-build", action="store_true", help="skip the gradle build + install")
    args = ap.parse_args()
    appearances = args.appearance or ["light"]
    out = common.out_dir(args, "android")
    common.preflight(args.flight)

    booted = None
    if not running_emulator():
        booted = boot(args.avd)
    device = adb("emu", "avd", "name", check=False).splitlines()[0].strip() or "emulator"
    # The app's DNS sends localhost.ro-z.me to 127.0.0.1 first: forward it to the host's devserver.
    adb("reverse", "tcp:8443", "tcp:8443", check=False)
    sets: dict[str, dict[str, list[Path]]] = {}
    failed = False
    try:
        if not args.no_build:
            print("building and installing the debug app + journeys…", flush=True)
            build_and_install()
        elif not installed():
            # connectedDebugAndroidTest uninstalls both APKs when it finishes.
            sys.exit(f"error: {PACKAGE} or its test APK is not installed; run without --no-build")
        # A fresh install asks for notifications right after sign-in: a system
        # dialog over the app, which hides the flight list from the journey.
        adb("shell", "pm", "grant", PACKAGE, "android.permission.POST_NOTIFICATIONS", check=False)
        demo_status_bar(True)
        for appearance in appearances:
            adb("shell", "cmd", "uimode", "night", "yes" if appearance == "dark" else "no", check=False)
            code, log = run_journey(args.flight, common.preflight(args.flight), args.screens)
            shots = pull(out / device / appearance, args.screens)
            sets.setdefault(device, {})[appearance] = shots
            if code != 0:
                failures = [ln.strip() for ln in log.splitlines() if "screens not captured" in ln or "Error" in ln]
                print("\n".join(failures[:5]) or log[-2000:], file=sys.stderr)
                failed |= not shots
    finally:
        demo_status_bar(False)
        adb("shell", "cmd", "uimode", "night", "no", check=False)
        if booted:
            adb("emu", "kill", check=False)
            booted.wait(timeout=60)
    missing = common.write_manifest(out, "android", args.flight, sets, args.screens)
    return common.report(out, sets, missing) or int(failed)


if __name__ == "__main__":
    sys.exit(main())
