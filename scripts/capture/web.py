"""Capture any flight's briefing page from the web app, against the devserver.

Drives headless Chromium (`web/scripts/capture-flight.mjs`, Playwright from
`web/node_modules`) on `https://localhost.ro-z.me:8443`, signed in as the dev
user with a short-lived token in the `flyfun_auth` cookie, and writes

    build/captures/<flight>/web/<width>x<height>/<light|dark>/NN-<screen>.png

`flights` and `briefing` are the viewport; the other screens are their own
section of the briefing page, shot alone (cross-section and map are the viz
section in each layout). Needs `/devserver --https` up and the flight in the
dev DB (`scripts/ops/import_flight.py <id>`); see `common.py` and
`designs/flight-captures.md`.

Usage:
    python3 scripts/capture/web.py --flight egtf_lfat-2026-10-30-6c36
    python3 scripts/capture/web.py --flight ID --size 390x844 --scale 3     # a phone-width page
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

WEB = common.ROOT / "web"
DRIVER = WEB / "scripts/capture-flight.mjs"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common.add_common_args(ap)
    ap.add_argument("--size", action="append", help="viewport WxH (repeatable; default 1440x900)")
    ap.add_argument("--scale", type=float, default=2, help="device scale factor (default 2)")
    args = ap.parse_args()
    sizes = args.size or ["1440x900"]
    appearances = args.appearance or ["light"]
    out = common.out_dir(args, "web")

    sets: dict[str, dict[str, list[Path]]] = {}
    failed = False
    for size in sizes:
        width, _, height = size.partition("x")
        for appearance in appearances:
            env = {**os.environ, "FLYFUN_CAPTURE_TOKEN": common.preflight(args.flight)}
            dest = out / size / appearance
            common.clear_pngs(dest, args.screens)
            with tempfile.TemporaryDirectory() as tmp:
                r = subprocess.run(["node", str(DRIVER), "--base", common.BASE_URL, "--flight", args.flight,
                                    "--out", tmp, "--screens", ",".join(args.screens), "--width", width,
                                    "--height", height, "--scale", str(args.scale), "--appearance", appearance],
                                   cwd=WEB, env=env, text=True, capture_output=True, timeout=600)
                if r.returncode != 0:
                    print((r.stderr or r.stdout).strip()[-2000:], file=sys.stderr)
                shots = []
                for png in sorted(Path(tmp).glob("*.png")):
                    if png.stem in common.SCREENS:
                        target = dest / f"{common.file_name(png.stem)}.png"
                        shutil.copy(png, target)
                        shots.append(target)
            sets.setdefault(size, {})[appearance] = sorted(shots)
            failed |= r.returncode != 0 and not shots
    missing = common.write_manifest(out, "web", args.flight, sets, args.screens)
    return common.report(out, sets, missing) or int(failed)


if __name__ == "__main__":
    sys.exit(main())
